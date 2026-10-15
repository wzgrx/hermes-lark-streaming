"""Owns the background readers for one gateway: host sampler, history summary, per-provider accounts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..card.model import Section
from .account_monitor import AccountsSummary
from .accounts import current_provider_settings, current_provider_snapshot, provider_products
from .config import DetailsConfig, env_secret, hermes_home
from .history import HistorySummary
from .host import HostSampler
from .pool import pool_status
from .sections import Source, build_sections

_MAX_PROVIDERS = 8


def with_pool_windows(accounts: dict[str, Any], home: Path | None = None) -> dict[str, Any]:
    """OpenDesign answers no quota query with the runtime key, but its 402 names when the 5-hour plan comes
    back, and Hermes keeps that on the pool entry. While it is cooling down for the plan, show the window as
    used up until then; otherwise the panel keeps saying the quota is only visible in OpenDesign's console."""
    rows = accounts.get("accounts")
    if not isinstance(rows, list) or not any(
        isinstance(r, dict) and r.get("reason") == "runtime_key_no_quota" for r in rows
    ):
        return accounts
    bench = next((info for _, info in pool_status(home or hermes_home(), "opendesign", []) if info), None)
    if bench is None or not bench[1] or bench[0] is None:
        return accounts
    reset = datetime.fromtimestamp(bench[0], UTC).isoformat()
    window = {"name": "rolling", "used_percent": 100.0, "reset_at": reset, "source": "pool"}
    patched = [
        {**r, "status": "ok", "windows": [window], "reason": "plan_exhausted"}
        if isinstance(r, dict) and r.get("reason") == "runtime_key_no_quota" else r
        for r in rows
    ]
    return {**accounts, "accounts": patched}


class DetailsCollector:
    """Coalesced, bounded readers; ``request`` never blocks and ``finish`` waits a bounded time."""

    def __init__(self, config: DetailsConfig, resolve: Callable[[str], str] = env_secret) -> None:
        self.config = config
        self._resolve = resolve
        self._host = HostSampler(ttl_s=config.resources_ttl_s)
        self._history = HistorySummary(config.ledger_path(), config.zone, ttl_s=config.history_ttl_s)
        self._accounts: dict[str, AccountsSummary] = {}

    def _accounts_for(self, provider: str) -> AccountsSummary | None:
        if not provider_products(provider):
            return None
        reader = self._accounts.get(provider)
        if reader is None:
            if len(self._accounts) >= _MAX_PROVIDERS:
                idle = next((k for k, v in self._accounts.items() if getattr(v, "_task", None) is None), None)
                if idle is None:
                    return None
                del self._accounts[idle]
            reader = self._accounts[provider] = AccountsSummary(ttl_s=self.config.accounts_ttl_s)
        return reader

    def request(self, *, chat_id: str = "", provider: str = "", terminal: bool = False) -> None:
        """Start whichever reads are due. Terminal cards only validate identity, they never start HTTP."""
        if self.config.history and not terminal:
            self._history.set_timezone(self.config.zone)
            self._history.request()
        if self.config.resources and not terminal:
            self._host.request()
        if self.config.accounts_allowed(chat_id) and (reader := self._accounts_for(provider)):
            reader.request(
                current_provider_settings(self.config.account_settings(), provider),
                self._resolve,
                allow_read=not terminal,
            )

    async def finish(self, chat_id: str = "", provider: str = "") -> None:
        """Bounded final wait so the terminal card carries fresh values; slow readers keep running."""
        waits: list[Any] = []
        if self.config.history:
            self._history.set_timezone(self.config.zone)
            waits.append(self._history.finish())
        if self.config.resources:
            waits.append(self._host.finish())
        reader = self._accounts.get(provider)
        if reader is not None and self.config.accounts_allowed(chat_id):
            waits.append(reader.finish())
        if waits:
            await asyncio.gather(*waits)

    def sections(
        self,
        turn: Source = None,
        *,
        chat_id: str = "",
        provider: str = "",
        terminal: bool = False,
        now: float | None = None,
    ) -> tuple[Section, ...]:
        reader = self._accounts.get(provider) if self.config.accounts_allowed(chat_id) else None
        accounts = (
            current_provider_snapshot(reader.snapshot(), provider, terminal=terminal) if reader is not None else None
        )
        if accounts is not None:
            accounts = with_pool_windows(accounts)
        return build_sections(
            self.config,
            turn=turn,
            history=self._history.snapshot,
            host=self._host.snapshot,
            accounts=accounts,
            chat_id=chat_id,
            terminal=terminal,
            now=now,
        )
