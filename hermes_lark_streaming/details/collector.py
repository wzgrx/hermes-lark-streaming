"""Owns the background readers for one gateway: host sampler, history summary, per-provider accounts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..card.model import Section
from .account_monitor import AccountsSummary
from .accounts import current_provider_settings, current_provider_snapshot, provider_products
from .config import DetailsConfig, env_secret
from .history import HistorySummary
from .host import HostSampler
from .sections import Source, build_sections

_MAX_PROVIDERS = 8


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
