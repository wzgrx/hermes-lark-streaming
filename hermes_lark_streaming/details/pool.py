"""Which key of a provider's credential pool served a turn, from the pool state Hermes persists.

Hermes rotates between the keys of one provider (``credential_pool`` in ``auth.json``) without telling
hooks which key a request used, so the footer could only say "Go". The persisted state is enough: a
``fill_first`` pool serves from the first entry that is not cooling down, and an entry benched during
the turn was tried first. Read-only; secrets are never read into memory beyond the JSON parse.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_EXHAUSTED_TTL_SEC = 3600.0  # Hermes' default bench when a 429 names no reset time
_CACHE: dict[Path, tuple[float, dict[str, Any]]] = {}


def _pool(home: Path) -> dict[str, Any]:
    path = home / "auth.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pool = data.get("credential_pool") if isinstance(data, dict) else None
    pool = pool if isinstance(pool, dict) else {}
    _CACHE[path] = (mtime, pool)
    return pool


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _label(entry: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """The configured account label for an entry: env-sourced entries are named after their variable."""
    source = str(entry.get("source") or "")
    if source.startswith("env:"):
        env = source[4:]
        for row in rows:
            if row.get("key_env") == env and row.get("label"):
                return str(row["label"])
    label = str(entry.get("label") or "").strip()
    return "" if not label or label.isupper() else label[:24]  # a bare env name says nothing


def _benched(entry: dict[str, Any], now: float) -> bool:
    if entry.get("last_status") != "exhausted":
        return False
    until = _number(entry.get("last_error_reset_at"))
    if until is None:
        at = _number(entry.get("last_status_at"))
        until = at + _EXHAUSTED_TTL_SEC if at is not None else None
    return until is None or until > now


def pool_route(home: Path, provider: str, since: float, rows: list[dict[str, Any]]) -> list[str]:
    """Labels of the keys this turn went through, oldest first: those benched since ``since``, then the
    one serving now. Empty when the provider has no multi-key pool or the labels are unknown."""
    entries = [e for e in _pool(home).get(provider) or [] if isinstance(e, dict)]
    if len(entries) < 2:
        return []
    entries.sort(key=lambda e: _number(e.get("priority")) or 0.0)
    now = time.time()
    route = [e for e in entries if _benched(e, now) and (_number(e.get("last_status_at")) or 0.0) >= since]
    active = next((e for e in entries if not _benched(e, now)), None)
    if active is not None:
        route.append(active)
    labels = [_label(e, rows) for e in route]
    return labels if labels and all(labels) else []
