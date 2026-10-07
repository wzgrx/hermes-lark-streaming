"""Bounded read-only SQL history summaries: today / month / total plus the top models."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def read_summary(path: Path, timezone: str, *, now: float | None = None) -> dict[str, Any]:
    tz = ZoneInfo(timezone)
    current = datetime.fromtimestamp(now if now is not None else time.time(), tz)
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    month = today.replace(day=1)
    tomorrow = today + timedelta(days=1)
    end_month = month.replace(year=month.year + (month.month == 12), month=month.month % 12 + 1)
    result: dict[str, Any] = {"timezone": timezone, "status": "no_history", "models": []}
    if not path.is_file():
        return result
    measured = "input_tokens IS NOT NULL AND output_tokens IS NOT NULL"
    periods = (
        ("today", today.timestamp(), tomorrow.timestamp()),
        ("month", month.timestamp(), end_month.timestamp()),
        ("total", 0, 253402214400),
    )
    columns = ["MIN(occurred_at)"]
    params: list[float] = []
    for _key, start, end in periods:
        # One scan for all periods, not MIN plus three independent scans.
        inside = "occurred_at >= ? AND occurred_at < ?"
        columns.extend((
            f"SUM(CASE WHEN {inside} THEN 1 ELSE 0 END)",
            f"SUM(CASE WHEN {inside} AND {measured} THEN 1 ELSE 0 END)",
            f"SUM(CASE WHEN {inside} AND {measured} THEN input_tokens+output_tokens END)",
        ))
        params.extend((start, end) * 3)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
        deadline = time.monotonic() + 0.5
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        # WAL writers keep running, but the totals and model groups must share
        # the same read snapshot. No write transaction/checkpoint is performed.
        db.execute("BEGIN")
        row = db.execute("SELECT " + ", ".join(columns) + " FROM usage_events WHERE scope='main'", params).fetchone()
        first = row[0]
        if first is None:
            return result
        result.update(status="ok", since=datetime.fromtimestamp(first, tz).strftime("%Y-%m-%d"))
        for index, (key, _start, _end) in enumerate(periods):
            requests, known, tokens = row[1 + index * 3:4 + index * 3]
            # A truly empty period is zero once recording exists. Requests with
            # missing usage remain unknown/partial, never an invented zero.
            result[key] = {
                "tokens": tokens if requests else 0,
                "requests": requests,
                "partial": requests != (known or 0),
            }
        for subscription, model, requests, known, tokens in db.execute(
            f"SELECT subscription, CASE WHEN response_model!='' THEN response_model ELSE requested_model END model, "
            f"COUNT(*), SUM(CASE WHEN {measured} THEN 1 ELSE 0 END), "
            f"SUM(CASE WHEN {measured} THEN input_tokens+output_tokens END) FROM usage_events "
            "WHERE scope='main' GROUP BY subscription, model ORDER BY 5 DESC, subscription, model LIMIT 3"
        ):
            result["models"].append(
                {"subscription": subscription, "model": model, "tokens": tokens, "partial": requests != (known or 0)}
            )
    return result


class HistorySummary:
    def __init__(self, path: Path, timezone: str, *, ttl_s: float = 60.0) -> None:
        self._path, self._timezone, self._ttl = path, timezone, ttl_s
        self._cached: dict[str, Any] = {"status": "pending", "timezone": timezone}
        self._at = float("-inf")
        self._task: asyncio.Task[None] | None = None

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self._cached)

    def set_timezone(self, timezone: str) -> None:
        """Apply validated live config on the Gateway loop without detaching a read."""
        if timezone == self._timezone:
            return
        self._timezone = timezone
        self._cached = {"status": "pending", "timezone": timezone}
        self._at = float("-inf")

    def request(self, *, force: bool = False) -> None:
        if self._task is None and (force or time.monotonic() - self._at >= self._ttl):
            self._task = asyncio.create_task(self._read())

    async def finish(self) -> None:
        # Drain an earlier cache read, then query AFTER the terminal usage hook.
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 0.8)
            except TimeoutError:
                # Do not label an older cache as a completed terminal refresh.
                # The worker stays owned and may complete for the next turn.
                self._cached = {"status": "unavailable", "timezone": self._timezone}
                return
        self.request(force=True)
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 0.8)
            except TimeoutError:
                self._cached = {"status": "unavailable", "timezone": self._timezone}

    async def _read(self) -> None:
        timezone = self._timezone
        try:
            # Timing out to_thread() does not stop its underlying OS thread.
            # Keep ownership until it really exits so requests stay coalesced.
            # SQL retains its own deadline; finish() bounds callers' wait time.
            result = await asyncio.to_thread(read_summary, self._path, timezone)
        except (OSError, TimeoutError, ValueError, sqlite3.Error, ZoneInfoNotFoundError):
            result = {"status": "unavailable", "timezone": timezone}
        finally:
            self._at = time.monotonic() if timezone == self._timezone else float("-inf")
            self._task = None
        # A config change can happen while SQLite runs on its worker thread.
        # Old-period totals (or errors) must not be published under a new zone.
        if timezone == self._timezone:
            self._cached = result
