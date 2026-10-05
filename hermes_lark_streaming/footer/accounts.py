"""Opt-in provider account snapshots, separate from model usage and routing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .state import label
from .usage import mapping

_ENDPOINTS = {
    "opencode-go": "https://opencode.ai/zen/go/v1/usage",
    "deepseek": "https://api.deepseek.com/user/balance",
    "openrouter": "https://openrouter.ai/api/v1/key",
}
_PREFIXES = {"opencode-go": "OPENCODE_GO_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
             "openrouter": "OPENROUTER_API_KEY"}


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    provider: str
    key_env: str


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
        result.append(Account(ident, label(row.get("label"))[:48] or ident, provider, env if prefix else ""))
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


def _money(value: Any) -> str | None:
    if type(value) not in (str, int, float) or len(str(value)) > 48:
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= 10**12:
            return None
        text = format(amount, "f")
        return text if len(text) <= 64 else None
    except (InvalidOperation, ValueError):
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
            if (not 0 <= percent <= 10**6 or not math.isfinite(percent)
                    or row.get("status") not in {"ok", "rate-limited"}):
                continue
            result["windows"].append({"name": name, "used_percent": percent,
                "remaining_percent": max(0, 100-percent), "reset_at": _timestamp(row.get("resetsAt")),
                "limited": row["status"] == "rate-limited"})
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
    if result["windows"] or result["balances"] or result.get("key_limit_unset"):
        result["status"] = "ok"
    if provider == "opencode-go":
        result["partial"] = len(result["windows"]) != 3
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:
        return None


def fetch_account(account: Account, key: str) -> dict[str, Any]:
    result: dict[str, Any] = {"id": account.id, "label": account.name, "provider": account.provider,
                             "source": "provider_api", "checked_at": datetime.now(UTC).isoformat()}
    endpoint = _ENDPOINTS.get(account.provider)
    if endpoint is None:
        return {**result, "source": "none", "status": "unsupported"}
    if not key or len(key) > 4096 or any(c in key for c in "\r\n"):
        return {**result, "status": "missing_credentials"}
    request = urllib.request.Request(endpoint, headers={"Authorization": "Bearer " + key,
        "Accept": "application/json", "User-Agent": "hermes-lark-streaming/account-status"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=4) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                return {**result, "status": "unavailable", "error_type": "ResponseTooLarge"}
            return {**result, **parse_account(account.provider, json.loads(raw))}
    except urllib.error.HTTPError as exc:
        return {**result, "status": "unavailable", "http_status": exc.code}
    except Exception as exc:
        # URLs, authorization headers, provider bodies and raw errors stay out
        # of snapshots, cards, logs and CLI output.
        return {**result, "status": "unavailable", "error_type": type(exc).__name__}


def fetch_accounts(accounts: tuple[Account, ...], keys: tuple[str, ...]) -> dict[str, Any]:
    return {"status": "snapshot", "scope": "configured_accounts", "accounts": [
        fetch_account(account, key) for account, key in zip(accounts, keys, strict=True)
    ]}


class AccountsSummary:
    """One owned background task; key/config changes invalidate old results."""

    def __init__(self) -> None:
        self._cached: dict[str, Any] = {"status": "pending", "accounts": []}
        self._signature: tuple[Any, ...] = ()
        self._at = float("-inf")
        self._task: asyncio.Task[None] | None = None

    def snapshot(self) -> dict[str, Any]:
        value = deepcopy(self._cached)
        value["stale"] = value.get("status") == "snapshot" and time.monotonic() - self._at >= 300
        return value

    def request(self, settings: dict[str, Any], resolve: Callable[[str], str]) -> None:
        specs = configured(settings)
        keys = tuple(resolve(a.key_env) if a.key_env else "" for a in specs)
        # Digests remain in process memory only; never persisted or rendered.
        signature = tuple((a, hashlib.sha256(k.encode()).digest()) for a, k in zip(specs, keys, strict=True))
        if signature != self._signature:
            self._signature, self._at = signature, float("-inf")
            self._cached = {"status": "pending", "scope": "configured_accounts", "accounts": [
                {"id": a.id, "label": a.name, "provider": a.provider, "status": "pending"} for a in specs
            ]}
        if self._task is None and time.monotonic() - self._at >= 300:
            self._task = asyncio.create_task(self._read(specs, keys, signature))

    async def _read(self, specs: tuple[Account, ...], keys: tuple[str, ...], signature: tuple[Any, ...]) -> None:
        try:
            result = await asyncio.to_thread(fetch_accounts, specs, keys)
            if signature == self._signature:
                self._cached = result
        except Exception:
            if signature == self._signature:
                self._cached = {"status": "unavailable", "accounts": []}
        finally:
            if signature == self._signature:
                self._at = time.monotonic()
            self._task = None


def cli(argv: list[str]) -> int:
    import argparse

    from ..config import Config, _get_secret

    parser = argparse.ArgumentParser(description="Configured account status; no routing or credential changes")
    parser.add_argument("--refresh", action="store_true", help="Make bounded read-only official API requests")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    settings = Config().footer_accounts
    specs = configured(settings)
    if args.refresh:
        from ..__main__ import _load_hermes_environment

        _load_hermes_environment()
        result = fetch_accounts(specs, tuple(_get_secret(a.key_env) if a.key_env else "" for a in specs))
    else:
        result = {"status": "not_refreshed", "accounts": [
            {"id": a.id, "label": a.name, "provider": a.provider} for a in specs
        ]}
    result.update(network_requested=args.refresh, automatic_account_switch=False,
                  request_account_attribution=False, enabled=settings.get("enabled") is True)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if all(a.get("status", "ok") == "ok" for a in result["accounts"]) else 1
