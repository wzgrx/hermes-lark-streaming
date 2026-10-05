"""Opt-in provider account snapshots, separate from model usage and routing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .account_adapters import money as _money
from .account_adapters import parse_extra, parse_subscription
from .account_catalog import ENDPOINTS as _ENDPOINTS
from .account_catalog import PREFIXES as _PREFIXES
from .account_catalog import UNAVAILABLE, catalog
from .state import label
from .usage import mapping


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    provider: str
    key_env: str
    discovered: bool = False
    subscription_expires_at: str | None = None


def configured(settings: Any) -> tuple[Account, ...]:
    rows = mapping(settings).get("accounts")
    if not isinstance(rows, list):
        return ()
    result = []
    seen = set()
    for raw in rows[:4]:
        row = mapping(raw)
        ident, provider = row.get("id"), label(row.get("provider"))
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", ident) or ident in seen:
            continue
        prefix = _PREFIXES.get(provider)
        env = row.get("key_env", prefix or "")
        if prefix and (not isinstance(env, str) or not re.fullmatch(re.escape(prefix) + r"(?:_[A-Z0-9_]+)?", env)):
            continue
        # Unsupported providers never resolve or transmit any credential.
        result.append(
            Account(
                ident,
                label(row.get("label"))[:48] or ident,
                provider,
                env if prefix else "",
                subscription_expires_at=_timestamp(row.get("subscription_expires_at")),
            )
        )
        seen.add(ident)
    return tuple(result)


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(UTC).isoformat() if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def parse_account(provider: str, value: Any) -> dict[str, Any]:
    data = mapping(value)
    result: dict[str, Any] = {"status": "unavailable", "windows": [], "balances": []}
    if provider == "opencode-go":
        usage = mapping(data.get("usage"))
        for name in ("rolling", "weekly", "monthly"):
            row = mapping(usage.get(name))
            percent = row.get("percent")
            if not isinstance(percent, (int, float)) or isinstance(percent, bool):
                continue
            if (
                not 0 <= percent <= 10**6
                or not math.isfinite(percent)
                or row.get("status") not in {"ok", "rate-limited"}
            ):
                continue
            result["windows"].append(
                {
                    "name": name,
                    "used_percent": percent,
                    "remaining_percent": max(0, 100 - percent),
                    "reset_at": _timestamp(row.get("resetsAt")),
                    "limited": row["status"] == "rate-limited",
                }
            )
    elif provider == "deepseek":
        rows = data.get("balance_infos")
        for row in rows[:4] if isinstance(rows, list) else []:
            row = mapping(row)
            currency, amount = row.get("currency"), _money(row.get("total_balance"))
            if currency in {"CNY", "USD"} and amount is not None:
                result["balances"].append({"kind": "account_balance", "currency": currency, "amount": amount})
    elif provider == "openrouter":
        row = mapping(data.get("data"))
        amount = _money(row.get("limit_remaining"))
        if amount is not None:
            result["balances"].append({"kind": "key_credit_remaining", "currency": "USD", "amount": amount})
        elif row.get("limit") is None and "limit" in row:
            result["key_limit_unset"] = True  # not an unlimited account balance
    if provider == "openrouter":
        expires = _timestamp(mapping(data.get("data")).get("expires_at"))
        if expires:
            result["key_expires_at"] = expires
    parse_extra(provider, data, result)
    if result["windows"] or result["balances"] or result.get("key_limit_unset") or result.get("key_expires_at"):
        result["status"] = "ok"
    if provider == "opencode-go":
        result["partial"] = len(result["windows"]) != 3
    return result


def provider_products(provider: Any) -> tuple[str, ...]:
    """Only reviewed billing product aliases; never infer provider from model."""
    if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
        return ()
    return ("openrouter", "openrouter-credits") if provider == "openrouter" else (provider,)


def current_provider_settings(settings: dict[str, Any], provider: str) -> dict[str, Any]:
    products = provider_products(provider)
    rows = settings.get("accounts")
    return {
        **settings,
        "accounts": [row for row in rows if isinstance(row, dict) and row.get("provider") in products]
        if isinstance(rows, list) else [],
        "_provider_products": products,
    }


def current_provider_snapshot(value: dict[str, Any], provider: str, *, terminal: bool = False) -> dict[str, Any]:
    products = provider_products(provider)
    rows = value.get("accounts")
    return {
        **value, "scope": "active_provider", "active_provider": provider, "terminal": terminal,
        "accounts": [row for row in rows if isinstance(row, dict) and row.get("provider") in products]
        if isinstance(rows, list) else [],
    }


def discover(
    settings: Any, resolve: Callable[[str], str], *, env_names: tuple[str, ...] | None = None, limit: int = 4
) -> tuple[Account, ...]:
    """Explicit rows first, opt-in known local names only; never print secrets."""
    explicit = configured(settings)
    result: list[Account] = []
    seen_credentials: set[tuple[str, bytes]] = set()
    seen_refs: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    # Explicit aliases also need deduplication, even with discovery disabled.
    # Resolve each reference once here; keep the first label/source record.
    for account in explicit:
        ref = (account.provider, account.key_env)
        if account.key_env and ref in seen_refs:
            continue
        if account.key_env:
            seen_refs.add(ref)
        key = resolve(account.key_env) if account.key_env else ""
        credential = (account.provider, hashlib.sha256(key.encode()).digest()) if key else None
        if credential is not None and credential in seen_credentials:
            continue
        result.append(account)
        seen_refs.add(ref)
        seen_ids.add(account.id)
        if credential is not None:
            seen_credentials.add(credential)
    if mapping(settings).get("auto_detect") is not True or len(result) >= limit:
        return tuple(result[:limit])
    names = tuple(os.environ) if env_names is None else env_names
    # Bounded local suffix discovery; no scanning browsers/keychains/files.
    names = tuple(sorted(n for n in names if re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", n)))[:256]
    records = catalog()["products"]
    scope = mapping(settings).get("_provider_products")
    if isinstance(scope, tuple):
        records = [row for row in records if row["id"] in scope]
    priority = {name: index for index, name in enumerate(_ENDPOINTS)}
    records.sort(key=lambda r: priority.get(r["id"], len(priority)))
    checked: set[str] = set()
    for row in records:
        provider = row["id"]
        prefixes = [_PREFIXES[provider]] if provider in _PREFIXES else row.get("env", [])[:3]
        for prefix in prefixes:
            if not isinstance(prefix, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", prefix):
                continue
            candidates = [prefix] + [n for n in names if n.startswith(prefix + "_")]
            for name in candidates[:8]:
                if (provider, name) in seen_refs or name in checked:
                    continue
                if len(checked) >= 128:
                    return tuple(result[:limit])
                checked.add(name)
                key = resolve(name)
                if not key:
                    continue
                credential = (provider, hashlib.sha256(key.encode()).digest())
                if credential in seen_credentials:
                    continue
                seen_credentials.add(credential)
                ident = "auto-" + hashlib.sha256((provider + ":" + name).encode()).hexdigest()[:12]
                if ident in seen_ids:
                    continue
                result.append(
                    Account(
                        ident,
                        label(row.get("name"))[:48] or provider,
                        provider,
                        name if provider in _ENDPOINTS else "",
                        discovered=True,
                    )
                )
                seen_ids.add(ident)
                seen_refs.add((provider, name))
                if len(result) >= limit:
                    return tuple(result[:limit])
    return tuple(result[:limit])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:
        return None


def fetch_account(account: Account, key: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": account.id,
        "label": account.name,
        "provider": account.provider,
        "source": "provider_api",
        "checked_at": datetime.now(UTC).isoformat(),
        "discovered": account.discovered,
    }
    if account.subscription_expires_at:
        result.update(subscription_expires_at=account.subscription_expires_at, subscription_source="manual")
    endpoint = _ENDPOINTS.get(account.provider)
    if endpoint is None:
        return {**result, **UNAVAILABLE.get(account.provider, {}), "source": "none", "status": "unsupported"}
    if not key or len(key) > 4096 or any(c in key for c in "\r\n"):
        return {**result, "status": "missing_credentials"}
    if account.provider in {"minimax", "minimax-cn"} and key.startswith("sk-api-"):
        endpoint = endpoint.replace("/v1/token_plan/remains", "/account/query_balance")
    authorization = key if account.provider in {"zai", "bigmodel"} else "Bearer " + key
    request = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": authorization,
            "Accept": "application/json",
            "User-Agent": "hermes-lark-streaming/account-status",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=4) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                return {**result, "status": "unavailable", "error_type": "ResponseTooLarge"}
            result.update(parse_account(account.provider, json.loads(raw)))
        if account.provider in {"zai", "bigmodel"}:
            subscription_url = endpoint.replace("/api/monitor/usage/quota/limit", "/api/biz/subscription/list")
            try:
                query = urllib.request.Request(
                    subscription_url, headers={"Authorization": authorization, "Accept": "application/json"}
                )
                with urllib.request.build_opener(_NoRedirect()).open(query, timeout=4) as response:
                    payload = response.read(65537)
                    if len(payload) <= 65536:
                        result.update(parse_subscription(json.loads(payload)))
            except Exception:
                result["subscription_status"] = "unavailable"
        return result
    except urllib.error.HTTPError as exc:
        return {**result, "status": "unavailable", "http_status": exc.code}
    except Exception as exc:
        # URLs, authorization headers, provider bodies and raw errors stay out
        # of snapshots, cards, logs and CLI output.
        return {**result, "status": "unavailable", "error_type": type(exc).__name__}


def fetch_accounts(accounts: tuple[Account, ...], keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        "status": "snapshot",
        "scope": "configured_accounts",
        "accounts": [fetch_account(account, key) for account, key in zip(accounts, keys, strict=True)],
    }


class AccountsSummary:
    """One owned background task; key/config changes invalidate old results."""

    def __init__(self) -> None:
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
        value["stale"] = value.get("status") == "snapshot" and time.monotonic() - self._at >= 300
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
        if allow_read and self._task is None and time.monotonic() - self._at >= 300:
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
                    result = await asyncio.to_thread(fetch_accounts, specs, keys)
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


def cli(argv: list[str]) -> int:
    import argparse

    from ..config import Config, _get_secret

    parser = argparse.ArgumentParser(description="Configured account status; no routing or credential changes")
    parser.add_argument("--refresh", action="store_true", help="Make bounded read-only official API requests")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--catalog", action="store_true", help="Provider inventory and reviewed capabilities; no network"
    )
    parser.add_argument(
        "--discover", action="store_true", help="Inspect known local credential references; no network unless --refresh"
    )
    args = parser.parse_args(argv)
    if args.catalog:
        print(json.dumps(catalog(), ensure_ascii=False))
        return 0
    from ..__main__ import _load_hermes_environment

    _load_hermes_environment()
    settings = dict(Config().footer_accounts)
    if args.discover:
        settings["auto_detect"] = True
    specs = discover(settings, _get_secret, limit=16 if args.discover and not args.refresh else 4)
    if args.refresh:
        from ..__main__ import _load_hermes_environment

        _load_hermes_environment()
        result = fetch_accounts(specs, tuple(_get_secret(a.key_env) if a.key_env else "" for a in specs))
    else:
        result = {
            "status": "not_refreshed",
            "accounts": [
                {"id": a.id, "label": a.name, "provider": a.provider, "discovered": a.discovered} for a in specs
            ],
        }
    result.update(
        network_requested=args.refresh,
        automatic_account_switch=False,
        request_account_attribution=False,
        enabled=settings.get("enabled") is True,
        auto_detect=settings.get("auto_detect") is True,
        queried_accounts_limit=4,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(a.get("status", "ok") == "ok" for a in result["accounts"]) else 1
