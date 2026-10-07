"""History summary correctness and reader ownership, using only synthetic local data."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from hermes_lark_streaming.details.history import HistorySummary, read_summary
from hermes_lark_streaming.details.ledger import UsageLedger

from .test_ledger import event


def test_summary_has_one_sqlite_snapshot_during_concurrent_write(tmp_path):
    path = tmp_path / "history.db"
    ledger = UsageLedger(path)
    ledger.record("post_api_request", event())
    connect = sqlite3.connect

    class InjectWriter(sqlite3.Connection):
        inserted = False

        def execute(self, sql, parameters=(), /):
            cursor = super().execute(sql, parameters)
            if sql.lstrip().upper().startswith("SELECT") and "usage_events" in sql and not self.inserted:
                self.inserted = True
                with patch("sqlite3.connect", connect):
                    ledger.record("post_api_request", event("two"))
            return cursor

    def reader_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=InjectWriter)

    with patch("hermes_lark_streaming.details.history.sqlite3.connect", reader_connect):
        summary = read_summary(path, "UTC")
    assert summary["total"]["requests"] == 1 and summary["total"]["tokens"] == 110
    assert [model["model"] for model in summary["models"]] == ["one"]
    assert ledger.report()["totals"]["requests"] == 2


def test_summary_snapshot_does_not_expose_nested_cache(tmp_path):
    reader = HistorySummary(tmp_path / "history.db", "UTC")
    reader._cached = {"status": "ok", "total": {"tokens": 110}, "models": [{"model": "one"}]}
    snapshot = reader.snapshot()
    snapshot["total"]["tokens"] = 999
    snapshot["models"].append({"model": "extra"})
    assert reader.snapshot() == {"status": "ok", "total": {"tokens": 110}, "models": [{"model": "one"}]}


def test_summary_uses_two_queries_and_keeps_empty_period_zero(tmp_path):
    path = tmp_path / "history.db"
    UsageLedger(path).record("post_api_request", event())
    statements = []
    connect = sqlite3.connect

    def reader_connect(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    with patch("hermes_lark_streaming.details.history.sqlite3.connect", reader_connect):
        summary = read_summary(path, "Asia/Shanghai", now=1_900_000_000)
    assert summary["today"] == summary["month"] == {"tokens": 0, "requests": 0, "partial": False}
    assert summary["total"]["tokens"] == 110
    assert statements[0] == "BEGIN"
    assert len([sql for sql in statements if sql.startswith("SELECT")]) == 2


def test_missing_usage_is_not_a_known_zero_in_summary(tmp_path):
    path = tmp_path / "history.db"
    UsageLedger(path).record("post_api_request", {**event(), "usage": {}})
    summary = read_summary(path, "UTC", now=1_700_000_001)
    for period in ("today", "month", "total"):
        assert summary[period] == {"tokens": None, "requests": 1, "partial": True}


def test_day_and_month_follow_the_configured_timezone(tmp_path):
    # Oct 1 in Shanghai, still Sep 30 in UTC: the old event is in neither today nor this month for Shanghai.
    path = tmp_path / "history.db"
    now = datetime(2026, 9, 30, 17, tzinfo=UTC).timestamp()
    UsageLedger(path).record("post_api_request", {**event(), "started_at": now - 7200})
    utc = read_summary(path, "UTC", now=now)
    shanghai = read_summary(path, "Asia/Shanghai", now=now)
    assert utc["today"]["tokens"] == utc["month"]["tokens"] == 110
    assert shanghai["today"]["tokens"] == shanghai["month"]["tokens"] == 0
    assert shanghai["total"]["tokens"] == 110


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_old_read", [False, True])
async def test_timezone_change_during_read_discards_stale_result_without_duplicate_worker(tmp_path, fail_old_read):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_read(path, tz):
        calls.append(tz)
        if tz == "UTC":
            started.set()
            assert release.wait(5)
            if fail_old_read:
                raise OSError("fixture")
        return {"status": "ok", "timezone": tz, "models": []}

    reader = HistorySummary(tmp_path / "fixture.db", "UTC")
    with patch("hermes_lark_streaming.details.history.read_summary", slow_read):
        reader.request()
        task = reader._task
        try:
            assert await asyncio.to_thread(started.wait, 1)
            reader.set_timezone("Asia/Shanghai")
            reader.request(force=True)
            assert reader._task is task and calls == ["UTC"]
            assert reader.snapshot() == {"status": "pending", "timezone": "Asia/Shanghai"}
        finally:
            release.set()
            await task
        assert reader.snapshot() == {"status": "pending", "timezone": "Asia/Shanghai"}
        reader.request()  # no 60-second delay after an obsolete worker finishes
        assert reader._task is not None
        await reader._task
        assert reader.snapshot()["status"] == "ok" and calls == ["UTC", "Asia/Shanghai"]
        reader.set_timezone("Asia/Shanghai")
        reader.request()
        assert reader._task is None  # unchanged config preserves the cache


@pytest.mark.asyncio
async def test_finish_wait_is_bounded_without_detaching_worker(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow_read(*args):
        started.set()
        assert release.wait(5)
        return {"status": "ok", "models": []}

    reader = HistorySummary(tmp_path / "history.db", "UTC")
    reader._cached = {"status": "ok", "total": {"tokens": 999}}
    with patch("hermes_lark_streaming.details.history.read_summary", slow_read):
        reader.request()
        task = reader._task
        try:
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.wait_for(reader.finish(), 2.5)
            assert reader._task is task and not task.done()
            assert reader.snapshot()["status"] == "unavailable"
            reader._at = float("-inf")
            reader.request()
            assert reader._task is task  # still coalesced onto the one owned read
        finally:
            release.set()
            if task is not None:
                await task
    assert reader._task is None


@pytest.mark.asyncio
async def test_cancelled_history_wait_does_not_cancel_shared_read(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow_read(*args):
        started.set()
        assert release.wait(5)
        return {"status": "ok", "models": []}

    reader = HistorySummary(tmp_path / "history.db", "UTC")
    with patch("hermes_lark_streaming.details.history.read_summary", slow_read):
        reader.request()
        task = reader._task
        try:
            assert await asyncio.to_thread(started.wait, 1)
            waiter = asyncio.create_task(reader.finish())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert reader._task is task and not task.cancelled()
        finally:
            release.set()
            if task is not None:
                await task
    assert reader.snapshot()["status"] == "ok"
