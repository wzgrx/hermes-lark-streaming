"""Regression coverage for live configuration and sealed turn ownership."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import yaml

from hermes_lark_streaming.controller import StreamCardController
from hermes_lark_streaming.footer.history import UsageLedger
from hermes_lark_streaming.footer.history_summary import HistorySummary, read_summary
from hermes_lark_streaming.footer.state import TurnFooter
from hermes_lark_streaming.streaming.session import CardSession, SessionState


@pytest.mark.parametrize("method", ["finish", "snapshot"])
def test_sealed_routes_are_owned_by_the_turn(method):
    state = TurnFooter()
    event = dict(platform="feishu", session_id="s", turn_id="t", api_request_id="r",
                 started_at=state.created_at + 1, provider="first")
    assert state.observe("pre_api_request", event)
    assert state.observe("post_api_request", {**event, "usage": {"prompt_tokens": 10, "output_tokens": 1}})
    state.finish()
    exposed = getattr(state, method)()
    exposed["routes"].append("changed-by-consumer")
    assert state.snapshot()["routes"] == ["first"]
    assert state.finish()["route_count"] == 1


@pytest.mark.asyncio
async def test_timezone_reload_recomputes_day_and_month_without_restarting_controller(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_lark_streaming.config._CONFIG_RELOAD_TTL_S", 0)
    path = tmp_path / "state/card-usage.sqlite3"
    # It is Oct 1 in Shanghai, still Sep 30 in UTC. The old event belongs
    # to Sep 30 in both zones, so neither today's nor this month's total agrees.
    now = datetime(2026, 9, 30, 17, tzinfo=UTC).timestamp()
    UsageLedger(path).record("post_api_request", {
        "api_request_id": "r", "session_id": "s", "turn_id": "t",
        "started_at": now - 7200, "usage": {"prompt_tokens": 10, "output_tokens": 1},
    })
    history_config = {"enabled": True, "timezone": "UTC"}
    config = {"streaming": {"layout": "reference", "resources": {"enabled": False},
                           "footer": {"mode": "enhanced", "history": history_config}}}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))
    ctrl = StreamCardController(tmp_path)
    session = CardSession("fixture", "fixture-chat", asyncio.get_running_loop())
    session.state = SessionState.COMPLETED

    def read_at_boundary(path, tz):
        return read_summary(path, tz, now=now)

    with patch("hermes_lark_streaming.footer.history_summary.read_summary", read_at_boundary):
        ctrl._reference_snapshot(session, {})
        reader = ctrl._reference_history
        await reader.finish()
        before = ctrl._reference_snapshot(session, {})["reference"]["history"]
        assert before["today"]["tokens"] == before["month"]["tokens"] == 11
        history_config["timezone"] = "Asia/Shanghai"
        config_path.write_text(yaml.safe_dump(config))
        pending = ctrl._reference_snapshot(session, {})["reference"]["history"]
        assert pending["timezone"] == "Asia/Shanghai"
        assert pending["status"] == "pending" and "today" not in pending
        assert ctrl._reference_history is reader  # no detached replacement worker
        await reader.finish()
        after = ctrl._reference_snapshot(session, {})["reference"]["history"]
        assert after["today"]["tokens"] == after["month"]["tokens"] == 0
        assert after["total"]["tokens"] == 11


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
    with patch("hermes_lark_streaming.footer.history_summary.read_summary", slow_read):
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
        # Neither the old value nor its error may overwrite the new timezone.
        assert reader.snapshot() == {"status": "pending", "timezone": "Asia/Shanghai"}
        reader.request()  # no 60-second delay after an obsolete worker finishes
        assert reader._task is not None
        await reader._task
        assert reader.snapshot()["timezone"] == "Asia/Shanghai"
        assert reader.snapshot()["status"] == "ok" and calls == ["UTC", "Asia/Shanghai"]
        reader.set_timezone("Asia/Shanghai")
        reader.request()
        assert reader._task is None  # unchanged config preserves the cache
