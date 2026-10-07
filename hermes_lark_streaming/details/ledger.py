"""Opt-in, profile-local usage ledger. No prompts, responses or credentials stored."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import DetailsConfig
from .usage import normalize_usage
from .values import label, mapping, seconds

_LOG = logging.getLogger("hermes_lark_streaming")
_EVENTS = {"post_api_request", "api_request_error", "post_auxiliary_call"}
_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
GROUPS = ("provider-model", "provider", "model", "month", "day", "subscription")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
 event_key TEXT PRIMARY KEY, occurred_at REAL NOT NULL,
 provider TEXT NOT NULL, subscription TEXT NOT NULL,
 requested_model TEXT NOT NULL, response_model TEXT NOT NULL,
 scope TEXT NOT NULL, platform TEXT NOT NULL,
 completed INTEGER NOT NULL, error_seen INTEGER NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
 cache_write_tokens INTEGER, reasoning_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS usage_time ON usage_events(occurred_at);
"""


class UsageLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, event: str, payload: dict[str, Any], labels: dict[str, Any] | None = None) -> bool:
        """Persist terminal physical attempts, idempotently. Caller owns fail-open handling."""
        if event not in _EVENTS:
            return False
        rid, started = payload.get("api_request_id"), seconds(payload.get("started_at"))
        if not isinstance(rid, str) or not rid or len(rid) > 512 or started is None or not 0 < started < 253402214400:
            return False
        # Auxiliary hooks have their own stream; do not double count aliased main events.
        if payload.get("aux_task") and event != "post_auxiliary_call":
            return False
        scope = "auxiliary" if event == "post_auxiliary_call" else "main"
        identity = [scope, payload.get("session_id"), payload.get("turn_id"), rid, started]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
        provider = label(payload.get("provider")) or "unknown"
        subscription = label((labels or {}).get(provider)) or provider
        usage = normalize_usage(payload.get("usage"), "hermes")
        failed = event == "api_request_error" or bool(payload.get("error_type") or payload.get("error"))
        values = (
            key,
            started,
            provider,
            subscription,
            label(payload.get("model")) or "unknown",
            label(payload.get("response_model")) or "",
            scope,
            label(payload.get("platform")) or "unknown",
            int(not failed),
            int(failed),
            usage.prompt,
            usage.output,
            usage.cache_read,
            usage.cache_write,
            usage.reasoning,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create privately before SQLite opens the file; no process-global umask changes.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=0.2)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            db.execute(
                """INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                 completed=MAX(completed,excluded.completed), error_seen=MAX(error_seen,excluded.error_seen),
                 response_model=CASE WHEN excluded.response_model!=''
                    THEN excluded.response_model ELSE response_model END,
                 input_tokens=COALESCE(excluded.input_tokens,input_tokens),
                 output_tokens=COALESCE(excluded.output_tokens,output_tokens),
                 cache_read_tokens=COALESCE(excluded.cache_read_tokens,cache_read_tokens),
                 cache_write_tokens=COALESCE(excluded.cache_write_tokens,cache_write_tokens),
                 reasoning_tokens=COALESCE(excluded.reasoning_tokens,reasoning_tokens)""",
                values,
            )
        return True

    def report(
        self,
        *,
        start: float | None = None,
        end: float | None = None,
        group_by: str = "provider-model",
        timezone: str = "UTC",
        scope: str = "all",
    ) -> dict[str, Any]:
        tz = ZoneInfo(timezone)
        if group_by not in GROUPS:
            raise ValueError("Unknown grouping")
        if scope not in {"all", "main", "auxiliary"}:
            raise ValueError("Unknown scope")
        if start is not None and end is not None and start >= end:
            raise ValueError("Start must precede end")
        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "no_history",
            "timezone": timezone,
            "start_inclusive": start,
            "end_exclusive": end,
            "scope": scope,
            "group_by": group_by,
            "groups": [],
            "totals": _bucket(),
            "coverage": {"first_event": None, "last_event": None},
            "note": "Observed Hermes events only; not a bill. Missing usage stays unknown. Cache is inside input.",
        }
        if not self.path.is_file():
            return result
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        where: list[str] = []
        params: list[Any] = []
        for op, value in ((">=", start), ("<", end)):
            if value is not None:
                where.append(f"occurred_at {op} ?")
                params.append(value)
        if scope != "all":
            where.append("scope = ?")
            params.append(scope)
        query = "SELECT * FROM usage_events" + (" WHERE " + " AND ".join(where) if where else "")
        # Read-only: querying never creates a ledger, deletes history, or checkpoints live WAL.
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute(query, params):
                model = row["response_model"] or row["requested_model"]
                dimensions = {
                    "provider-model": (row["provider"], row["subscription"], row["requested_model"], model),
                    "provider": (row["provider"],),
                    "model": (model,),
                    "subscription": (row["subscription"],),
                    "month": (datetime.fromtimestamp(row["occurred_at"], tz).strftime("%Y-%m"),),
                    "day": (datetime.fromtimestamp(row["occurred_at"], tz).strftime("%Y-%m-%d"),),
                }
                key = dimensions[group_by]
                bucket = groups.setdefault(key, {"key": list(key), **_bucket()})
                _add(bucket, row)
                _add(result["totals"], row)
                coverage = result["coverage"]
                at = row["occurred_at"]
                coverage["first_event"] = min(at, coverage["first_event"] or at)
                coverage["last_event"] = max(at, coverage["last_event"] or at)
        result["groups"] = [groups[key] for key in sorted(groups)]
        result["status"] = "ok" if groups else "empty_range"
        for item in [result["totals"], *result["groups"]]:
            item["usage_partial"] = item["measured_requests"] != item["requests"]
        return result


def _bucket() -> dict[str, Any]:
    return {
        "requests": 0,
        "completed_requests": 0,
        "error_attempts": 0,
        "measured_requests": 0,
        "main_requests": 0,
        "auxiliary_requests": 0,
        "usage_partial": False,
        "total_tokens": None,
        **dict.fromkeys(_FIELDS),
        "known_field_requests": dict.fromkeys(_FIELDS, 0),
    }


def _add(bucket: dict[str, Any], row: sqlite3.Row) -> None:
    bucket["requests"] += 1
    bucket["completed_requests"] += row["completed"]
    bucket["error_attempts"] += row["error_seen"]
    bucket[row["scope"] + "_requests"] += 1
    if row["input_tokens"] is not None and row["output_tokens"] is not None:
        bucket["measured_requests"] += 1
        bucket["total_tokens"] = (bucket["total_tokens"] or 0) + row["input_tokens"] + row["output_tokens"]
    for field in _FIELDS:
        if row[field] is not None:
            bucket[field] = (bucket[field] or 0) + row[field]
            bucket["known_field_requests"][field] += 1


def observe_history(config: DetailsConfig, event: str, payload: dict[str, Any]) -> bool:
    """Record one event when history is enabled. Never raises: inference must not depend on disk health."""
    if event not in _EVENTS or not config.history:
        return False
    try:
        return UsageLedger(config.ledger_path()).record(event, payload, mapping(dict(config.provider_labels)))
    except Exception:
        # Never log request payloads/SQLite values.
        _LOG.warning("Usage history event was not recorded; check ledger permissions/free space/locks")
        return False
