"""History correctness and sampler ownership, using only synthetic local data."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from unittest.mock import patch

import pytest

from hermes_lark_streaming.footer.history import UsageLedger
from hermes_lark_streaming.footer.history_summary import HistorySummary, read_summary
from hermes_lark_streaming.footer.host import HostSampler


def event(rid="one", **usage):
    return {
        "api_request_id": rid,
        "started_at": 1_700_000_000,
        "session_id": "fixture-session",
        "turn_id": "fixture-turn",
        "provider": "fixture",
        "model": rid,
        "usage": {"prompt_tokens": 100, "output_tokens": 10, **usage},
    }


@pytest.mark.parametrize("field", ["cache_read_tokens", "cache_write_tokens", "reasoning_tokens"])
def test_known_zero_is_not_missing_history(tmp_path, field):
    ledger = UsageLedger(tmp_path / "history.db")
    ledger.record("post_api_request", event(**{field: 0}))
    totals = ledger.report()["totals"]
    assert totals[field] == 0
    assert totals["known_field_requests"][field] == 1


@pytest.mark.parametrize("field", ["cache_read_tokens", "cache_write_tokens", "reasoning_tokens"])
def test_late_zero_corrects_nonzero_without_erasing_missing_values(tmp_path, field):
    ledger = UsageLedger(tmp_path / "history.db")
    ledger.record("post_api_request", event(**{field: 17}))
    ledger.record("post_api_request", event(**{field: 0}))
    ledger.record("post_api_request", event())
    totals = ledger.report()["totals"]
    assert totals["requests"] == 1 and totals[field] == 0


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
                # The writer uses its own unpatched connection and WAL permits
                # the update while the read-only transaction remains open.
                with patch("sqlite3.connect", connect):
                    ledger.record("post_api_request", event("two"))
            return cursor

    def reader_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=InjectWriter)

    with patch("hermes_lark_streaming.footer.history_summary.sqlite3.connect", reader_connect):
        summary = read_summary(path, "UTC")
    assert summary["total"]["requests"] == 1
    assert summary["total"]["tokens"] == 110
    assert [model["model"] for model in summary["models"]] == ["one"]
    assert ledger.report()["totals"]["requests"] == 2


def test_summary_snapshot_does_not_expose_nested_cache(tmp_path):
    reader = HistorySummary(tmp_path / "history.db", "UTC")
    reader._cached = {"status": "ok", "total": {"tokens": 110}, "models": [{"model": "one"}]}
    snapshot = reader.snapshot()
    snapshot["total"]["tokens"] = 999
    snapshot["models"][0]["model"] = "changed"
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

    with patch("hermes_lark_streaming.footer.history_summary.sqlite3.connect", reader_connect):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["history", "host"])
async def test_slow_thread_stays_owned_and_coalesced(tmp_path, kind):
    started, release = threading.Event(), threading.Event()
    calls = 0

    def slow_read(*args):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(5), "test worker was not released"
        return {"status": "ok", "models": []} if kind == "history" else ({"ram_used_gib": 1}, None)

    reader = HistorySummary(tmp_path / "history.db", "UTC") if kind == "history" else HostSampler()
    target = ("hermes_lark_streaming.footer.history_summary.read_summary" if kind == "history"
              else "hermes_lark_streaming.footer.host.read_proc")
    with patch(target, slow_read), patch("hermes_lark_streaming.footer.host.shutil.which", return_value=None):
        reader.request()
        task = reader._task
        try:
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.sleep(0.65)
            # A waiter timeout is not proof that the blocking thread stopped.
            assert reader._task is task and not task.done()
            reader._at = float("-inf")
            reader.request()
            await asyncio.sleep(0)
            assert reader._task is task and calls == 1
        finally:
            release.set()
            if task is not None:
                await task
    assert reader._task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["history", "host"])
async def test_finish_wait_is_bounded_without_detaching_worker(tmp_path, kind):
    started, release = threading.Event(), threading.Event()

    def slow_read(*args):
        started.set()
        assert release.wait(5)
        return {"status": "ok", "models": []} if kind == "history" else ({"ram_used_gib": 1}, None)

    reader = HistorySummary(tmp_path / "history.db", "UTC") if kind == "history" else HostSampler()
    reader._cached = {"status": "ok", "total": {"tokens": 999}} if kind == "history" else {"unavailable": True}
    target = ("hermes_lark_streaming.footer.history_summary.read_summary" if kind == "history"
              else "hermes_lark_streaming.footer.host.read_proc")
    with patch(target, slow_read), patch("hermes_lark_streaming.footer.host.shutil.which", return_value=None):
        reader.request()
        task = reader._task
        try:
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.wait_for(reader.finish(), 2.5)
            assert reader._task is task and not task.done()
            if kind == "history":
                assert reader.snapshot()["status"] == "unavailable"
            reader._at = float("-inf")
            reader.request()
            assert reader._task is task
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
    with patch("hermes_lark_streaming.footer.history_summary.read_summary", slow_read):
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
