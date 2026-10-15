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
    assert summary["today"] == summary["month"] == {"tokens": 0, "fresh": 0, "cached": 0, "requests": 0,
                                                    "partial": False}
    assert summary["total"]["tokens"] == 110
    assert statements[0] == "BEGIN"
    assert len([sql for sql in statements if sql.startswith("SELECT")]) == 2


def test_missing_usage_is_not_a_known_zero_in_summary(tmp_path):
    path = tmp_path / "history.db"
    UsageLedger(path).record("post_api_request", {**event(), "usage": {}})
    summary = read_summary(path, "UTC", now=1_700_000_001)
    for period in ("today", "month", "total"):
        assert summary[period] == {"tokens": None, "fresh": None, "cached": None, "requests": 1, "partial": True}


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


def test_summary_counts_auxiliary_requests_too(tmp_path):
    import time

    from hermes_lark_streaming.details.history import read_summary
    from hermes_lark_streaming.details.ledger import UsageLedger

    ledger = UsageLedger(tmp_path / "u.sqlite3")
    now = time.time()
    base = {"session_id": "s", "turn_id": "t", "provider": "p", "model": "m", "started_at": now - 5}
    main = {"input_tokens": 100, "output_tokens": 10}
    ledger.record("post_api_request", {**base, "api_request_id": "a", "usage": main})
    aux = {"input_tokens": 50, "output_tokens": 5}
    ledger.record("post_auxiliary_call", {**base, "api_request_id": "b", "usage": aux})
    summary = read_summary(tmp_path / "u.sqlite3", "UTC", now=now)
    assert summary["today"]["requests"] == 2 and summary["today"]["tokens"] == 165


def test_history_counts_new_tokens_apart_from_cache_and_merges_model_aliases(tmp_path) -> None:
    import sqlite3
    import time

    from hermes_lark_streaming.details.history import read_summary

    path = tmp_path / "usage.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE usage_events (event_key TEXT, occurred_at REAL, provider TEXT, subscription TEXT, "
               "requested_model TEXT, response_model TEXT, scope TEXT, platform TEXT, completed INT, error_seen INT, "
               "input_tokens INT, output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, "
               "reasoning_tokens INT)")
    now = time.time()
    rows = [("a", "deepseek-v4.1-flash", "deepseek-flash", 193_000, 500, 192_900),
            ("b", "deepseek-v4.1-flash", "deepseek-v4.1-flash", 193_100, 100, 193_000)]
    for key, requested, response, inp, out, read in rows:
        db.execute("INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (key, now, "opendesign", "OpenDesign 订阅", requested, response, "main", "feishu", 1, 0,
                    inp, out, read, 0, 0))
    db.commit()
    db.close()
    today = read_summary(path, "Asia/Shanghai", now=now)["today"]
    assert today["tokens"] == 386_700 and today["cached"] == 385_900 and today["fresh"] == 800  # 200 new + 600 out
    models = read_summary(path, "Asia/Shanghai", now=now)["models"]
    assert [(m["model"], m["fresh"]) for m in models] == [("DeepSeek V4.1 Flash", 800)]
