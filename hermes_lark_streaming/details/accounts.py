"""Account references: explicit rows, opt-in discovery, current-provider scoping. Never routes or switches."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .account_catalog import ENDPOINTS as _ENDPOINTS
from .account_catalog import PREFIXES as _PREFIXES
from .account_catalog import catalog
from .values import label, mapping


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
                subscription_expires_at=timestamp(row.get("subscription_expires_at")),
            )
        )
        seen.add(ident)
    return tuple(result)


def timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(UTC).isoformat() if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


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


