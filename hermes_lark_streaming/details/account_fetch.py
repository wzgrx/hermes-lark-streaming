"""Read-only provider account queries: bounded, redirect-free, secrets never leave the request."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from .account_adapters import money as _money
from .account_adapters import parse_extra, parse_subscription
from .account_catalog import ENDPOINTS as _ENDPOINTS
from .account_catalog import UNAVAILABLE
from .accounts import Account, timestamp
from .values import mapping


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
                    "reset_at": timestamp(row.get("resetsAt")),
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
        expires = timestamp(mapping(data.get("data")).get("expires_at"))
        if expires:
            result["key_expires_at"] = expires
    parse_extra(provider, data, result)
    if result["windows"] or result["balances"] or result.get("key_limit_unset") or result.get("key_expires_at"):
        result["status"] = "ok"
    if provider == "opencode-go":
        result["partial"] = len(result["windows"]) != 3
    return result


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
