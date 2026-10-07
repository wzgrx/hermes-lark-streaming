"""One owned background task per provider scope; key/config changes invalidate old results."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from typing import Any

from . import account_fetch
from .accounts import Account, configured, discover


class AccountsSummary:
    """One owned background task; key/config changes invalidate old results."""

    def __init__(self, *, ttl_s: float = 300.0) -> None:
        self._ttl = ttl_s
        self._cached: dict[str, Any] = {"status": "pending", "accounts": []}
        self._signature: tuple[Any, ...] = ()
        self._at = float("-inf")
        self._task: asyncio.Task[None] | None = None
        self._specs: tuple[Account, ...] = ()
        self._spec_identity: tuple[Any, ...] = ()
        self._spec_at = float("-inf")
        self._pending_query: tuple[tuple[Account, ...], tuple[str, ...], tuple[Any, ...]] | None = None

    def snapshot(self) -> dict[str, Any]:
        value = deepcopy(self._cached)
        value["stale"] = value.get("status") == "snapshot" and time.monotonic() - self._at >= self._ttl
        return value

    def request(self, settings: dict[str, Any], resolve: Callable[[str], str], *, allow_read: bool = True) -> None:
        identity = (configured(settings), settings.get("auto_detect") is True,
                    settings.get("_provider_products"), tuple(os.environ))
        if identity != self._spec_identity or time.monotonic() - self._spec_at >= 60:
            self._specs = discover(settings, resolve)
            self._spec_identity, self._spec_at = identity, time.monotonic()
        specs = self._specs
        keys = tuple(resolve(a.key_env) if a.key_env else "" for a in specs)
        # Digests remain in process memory only; never persisted or rendered.
        signature = tuple((a, hashlib.sha256(k.encode()).digest()) for a, k in zip(specs, keys, strict=True))
        if signature != self._signature:
            if self._task is not None:
                # Coalesce to the latest identity; do not cancel an in-flight
                # stdlib HTTP worker or require another token/message delta.
                self._pending_query = (specs, keys, signature) if allow_read else None
            self._signature, self._at = signature, float("-inf")
            self._cached = {
                "status": "pending",
                "scope": "configured_accounts",
                "accounts": [
                    {
                        "id": a.id,
                        "label": a.name,
                        "provider": a.provider,
                        "status": "pending",
                        "discovered": a.discovered,
                    }
                    for a in specs
                ],
            }
        if allow_read and self._task is None and time.monotonic() - self._at >= self._ttl:
            self._task = asyncio.create_task(self._read(specs, keys, signature))

    async def finish(self, timeout: float = 0.6) -> None:
        """A bounded final-snapshot wait, never cancel the owned HTTP read."""
        if self._task is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=max(0.0, min(timeout, 0.6)))

    async def _read(self, specs: tuple[Account, ...], keys: tuple[str, ...], signature: tuple[Any, ...]) -> None:
        try:
            while True:
                try:
                    result = await asyncio.to_thread(account_fetch.fetch_accounts, specs, keys)
                    if signature == self._signature:
                        old = {r.get("id"): r for r in self._cached.get("accounts", [])}
                        for index, row in enumerate(result.get("accounts", [])):
                            previous = old.get(row.get("id"), {})
                            if row.get("status") != "ok" and previous.get("status") == "ok":
                                retained = deepcopy(previous)
                                retained.update(
                                    stale=True, last_error_status=row.get("status"),
                                    last_attempt_at=row.get("checked_at"),
                                )
                                code = row.get("http_status")
                                if type(code) is int and 100 <= code <= 599:
                                    retained["last_http_status"] = code
                                else:
                                    retained.pop("last_http_status", None)
                                result["accounts"][index] = retained
                        self._cached = result
                except Exception:
                    if signature == self._signature:
                        self._cached = {"status": "unavailable", "accounts": []}
                if signature == self._signature:
                    self._at = time.monotonic()
                    break
                pending, self._pending_query = self._pending_query, None
                if pending is None:
                    break
                specs, keys, signature = pending
        finally:
            self._pending_query = None
            self._task = None
