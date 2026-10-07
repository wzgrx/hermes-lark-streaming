"""Switches and settings for the footer data and the details panel. Plain values, no Hermes imports."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .values import mapping


def hermes_home() -> Path:
    """HERMES_HOME, else ~/.hermes; the Hermes helper is used only when importable."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return Path(get_hermes_home())


def default_history_path() -> Path:
    return hermes_home() / "state" / "card-usage.sqlite3"


def env_secret(name: str) -> str:
    """Resolve a credential reference without ever returning or logging it elsewhere."""
    try:
        from agent.secret_scope import get_secret  # type: ignore[import-not-found,unused-ignore]
    except ImportError:
        return os.environ.get(name, "")
    return str(get_secret(name, "") or "")


def valid_timezone(value: Any) -> str:
    """The name if it is a real IANA zone, else UTC; a bad setting must never break rendering."""
    if not isinstance(value, str) or not value:
        return "UTC"
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError, OSError):
        return "UTC"
    return value


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _chats(value: Any) -> tuple[str, ...]:
    return tuple(c for c in value if isinstance(c, str) and c) if isinstance(value, list | tuple) else ()


def _pricing(raw: Any) -> dict[str, dict[str, Any]]:
    """``{model: {input, output, cache_read, currency}}`` per million tokens; anything malformed is dropped."""
    out: dict[str, dict[str, Any]] = {}
    for model, value in mapping(raw).items():
        prices = mapping(value)
        numbers = {k: prices[k] for k in ("input", "output", "cache_read")
                   if isinstance(prices.get(k), int | float) and not isinstance(prices.get(k), bool) and prices[k] >= 0}
        if isinstance(model, str) and model and {"input", "output"} <= set(numbers):
            currency = prices.get("currency")
            numbers["currency"] = currency if isinstance(currency, str) and len(currency) <= 4 else "¥"
            out[model.strip().lower()] = numbers
    return out


@dataclass(frozen=True, slots=True)
class DetailsConfig:
    usage: bool = True  # turn + history section
    resources: bool = False  # GPU / RAM / disk section
    accounts: bool = False  # subscription / quota section
    history: bool = False  # record the local usage ledger (needed for the history rows)
    timezone: str = "UTC"  # day/month boundaries and every account clock
    allowed_chats: tuple[str, ...] = ()  # non-empty: sections are shown in these chats only
    account_chats: tuple[str, ...] = ()  # accounts need explicit membership; empty hides them everywhere
    account_rows: tuple[Mapping[str, Any], ...] = ()  # explicit accounts: id, provider, key_env, label, ...
    auto_detect: bool = False  # opt-in discovery of known local credential names (current provider only)
    history_path: Path | None = None  # None: <hermes home>/state/card-usage.sqlite3
    provider_labels: Mapping[str, str] = field(default_factory=dict)
    show_models: bool = True  # top models in the history rows
    pricing: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # model -> per-million prices; user supplied
    history_ttl_s: float = 60.0
    resources_ttl_s: float = 10.0
    accounts_ttl_s: float = 300.0

    @property
    def zone(self) -> str:
        return valid_timezone(self.timezone)

    def chat_allowed(self, chat_id: str) -> bool:
        return not self.allowed_chats or chat_id in self.allowed_chats

    def accounts_allowed(self, chat_id: str) -> bool:
        return self.accounts and bool(chat_id) and chat_id in self.account_chats and self.chat_allowed(chat_id)

    def account_settings(self) -> dict[str, Any]:
        return {"accounts": list(self.account_rows), "auto_detect": self.auto_detect}

    def ledger_path(self) -> Path:
        return self.history_path or default_history_path()

    @classmethod
    def from_mapping(cls, raw: Any) -> DetailsConfig:
        """Parse the ``details`` config section; wrong types fall back to the safe default."""
        data = mapping(raw)
        history, accounts = mapping(data.get("history")), mapping(data.get("accounts"))
        rows = accounts.get("accounts")
        path = history.get("path")
        labels = mapping(history.get("provider_labels"))
        return cls(
            usage=_flag(data.get("usage"), True),
            resources=_flag(data.get("resources"), False),
            accounts=_flag(accounts.get("enabled"), False),
            history=_flag(history.get("enabled"), False),
            timezone=valid_timezone(data.get("timezone")),
            allowed_chats=_chats(data.get("allowed_chats")),
            account_chats=_chats(accounts.get("allowed_chats")),
            account_rows=tuple(r for r in rows if isinstance(r, dict)) if isinstance(rows, list) else (),
            auto_detect=accounts.get("auto_detect") is True,
            history_path=Path(path).expanduser() if isinstance(path, str) and path else None,
            provider_labels={k: v for k, v in labels.items() if isinstance(k, str) and isinstance(v, str)},
            show_models=_flag(history.get("show_models"), True),
            pricing=_pricing(data.get("pricing")),
        )
