"""Running footer: truthful phases, shared writer, bounded ticks, and classic parity."""

from __future__ import annotations

import asyncio
import json
import time
from contextvars import ContextVar
from unittest.mock import AsyncMock, patch

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_streaming_card_v2
from hermes_lark_streaming.controller import StreamCardController
from hermes_lark_streaming.feishu import FeishuAPIError, FeishuClient
from hermes_lark_streaming.footer.hooks import observe
from hermes_lark_streaming.footer.layout import DETAIL_ELEMENT_RESERVE, SUMMARY_ELEMENT_RESERVE
from hermes_lark_streaming.footer.runtime import RuntimeStatus, build_runtime_footer, runtime_actions
from hermes_lark_streaming.footer.state import TurnFooter
from hermes_lark_streaming.streaming.session import CardSession, SessionState


def event(**extra):
    return dict(platform="feishu", session_id="s", turn_id="t", api_request_id="r",
                started_at=time.time(), provider="first", model="fixture", **extra)


def test_runtime_main_events_and_duplicates_do_not_fake_completed_or_quota():
    state = RuntimeStatus()
    first = event()
    assert state.observe("pre_api_request", first)
    state.signal("answer")
    assert not state.observe("pre_api_request", first)
    assert state.snapshot()["runtime_phase"] == "answer"
    assert state.observe("api_request_error", first)
    assert state.snapshot()["runtime_phase"] == "request_error"
    second = {**event(), "provider": "second"}
    assert state.observe("pre_api_request", second)
    assert state.snapshot() == {"runtime_phase": "provider_switch", "runtime_route": ("first", "second"),
                                "compression_observed": False}
    assert not state.observe("api_request_error", first)
    state.observe("post_api_request", second)
    rendered = json.dumps(build_runtime_footer(state.snapshot()))
    assert "quota" not in rendered and "Completed" not in rendered


def test_runtime_aux_summary_is_not_compression_commit_or_context_size():
    state = RuntimeStatus()
    pre = event(aux_task="compression", approx_input_tokens=852000, request_messages=["PRIVATE"])
    assert state.observe("pre_auxiliary_call", pre)
    assert state.snapshot()["runtime_phase"] == "compression"
    assert not state.observe("pre_auxiliary_call", pre)
    state.observe("post_auxiliary_call", pre)
    assert state.snapshot()["runtime_phase"] == "summary_returned"
    rendered = json.dumps(build_runtime_footer(state.snapshot()))
    assert "852" not in rendered and "PRIVATE" not in repr(vars(state))
    assert not state.observe("post_auxiliary_call", pre)
    state.observe("pre_api_request", event())
    assert state.snapshot()["runtime_phase"] == "processing"


def test_late_auxiliary_events_do_not_replace_resumed_main_phase():
    state = RuntimeStatus()
    pre = event(aux_task="compression")
    state.observe("pre_auxiliary_call", pre)
    later = event()
    state.observe("pre_api_request", later)
    state.signal("answer")
    state.observe("post_auxiliary_call", {**pre, "error": "private failure"})
    assert state.snapshot()["runtime_phase"] == "answer"
    assert not state.observe("pre_auxiliary_call", {**pre, "api_request_id": "late"})


def test_native_main_stream_resumes_phase_without_claiming_compression_commit():
    state = RuntimeStatus()
    pre = event(aux_task="compression")
    state.observe("pre_auxiliary_call", pre)
    state.signal("answer")
    state.observe("post_auxiliary_call", pre)
    assert state.snapshot()["runtime_phase"] == "answer"
    assert state.snapshot()["compression_observed"]
    text = json.dumps(build_runtime_footer(state.snapshot()), ensure_ascii=False)
    assert "压缩提交待确认" in text and "压缩尚未观测" not in text


@pytest.mark.parametrize("change", [
    {"platform": "telegram"}, {"session_id": "other"}, {"turn_id": "other"},
    {"api_request_id": ""}, {"started_at": 0}, {"aux_task": "title_generation"},
])
def test_auxiliary_identity_rejects_unrelated_events(change):
    state = RuntimeStatus()
    state.observe("pre_api_request", event())
    assert not state.observe("pre_auxiliary_call", {**event(aux_task="compression"), **change})


@pytest.mark.parametrize("phase", [
    "processing", "answer", "thinking", "tool", "waiting", "compression", "summary_returned",
    "summary_failed", "provider_switch", "request_error",
])
@pytest.mark.parametrize("details", [True, False])
def test_all_runtime_phases_fit_budget_and_preserve_collapsed_state(phase, details):
    data = dict(runtime_phase=phase, duration=12, runtime_tool="<at>command</at>",
                runtime_route=("first", "second"), runtime_tools_done=2,
                requested_model="x" * 160, response_model="y" * 160,
                routes=["first", "second"], input_tokens=100, output_tokens=2, last_error_type="RateLimitError")
    elements = build_runtime_footer(data, details=details)
    card = {"schema": "2.0", "body": {"elements": elements}}
    inspection = inspect_card(card)
    assert inspection.safe
    assert inspection.elements <= (DETAIL_ELEMENT_RESERVE if details else SUMMARY_ELEMENT_RESERVE)
    assert elements[0]["element_id"] == "loading_icon" and elements[0]["content"].strip()
    assert "<at>" not in json.dumps(elements)
    for action in runtime_actions(elements):
        assert "expanded" not in action["params"]["partial_element"]
        assert "header" not in action["params"]["partial_element"]
    assert len(runtime_actions(elements)) == (2 if details else 1)


def setup():
    ctrl = StreamCardController()
    ctrl._cfg._raw = {"streaming": {"enabled": True, "footer": {"mode": "enhanced"}},
                      "feishu": {"app_id": "fixture", "app_secret": "fixture"}}
    ctrl._initialized = True
    ctrl._client = AsyncMock(spec=FeishuClient)
    ctrl._client.cardkit_create.return_value = "fixture-card"
    ctrl._client.reply_card_by_id.return_value = "fixture-message"
    session = CardSession("fixture", "fixture-chat", asyncio.get_running_loop())
    ctrl._sessions[session.message_id] = session
    ctrl._session_keys["fixture-key"] = session
    session.session_key = "fixture-key"
    session.state = SessionState.STREAMING
    session.set_card(card_id="card", card_msg_id="message")
    session.flush.set_card_message_ready(True)
    return ctrl, session


@pytest.mark.asyncio
async def test_real_observer_dispatch_binds_aux_and_main_without_counting_summary_usage():
    ctrl, session = setup()
    scope = ContextVar("HERMES_SESSION_KEY")
    token = scope.set("fixture-key")
    try:
        with patch("hermes_lark_streaming.controller.get_controller", return_value=ctrl), patch.object(
            ctrl, "request_runtime_update"
        ) as notify:
            pre = event()
            observe("pre_api_request", pre)
            aux = event(aux_task="compression")
            observe("pre_auxiliary_call", aux)
            assert session.runtime_status.snapshot()["runtime_phase"] == "compression"
            observe("post_auxiliary_call", {**aux, "usage": {"prompt_tokens": 999, "output_tokens": 5}})
            assert session.runtime_status.snapshot()["runtime_phase"] == "summary_returned"
            assert session.footer_state.snapshot()["api_calls"] == 1
            assert "input_tokens" not in session.footer_state.snapshot()
            assert notify.call_count == 3
    finally:
        scope.reset(token)


@pytest.mark.asyncio
async def test_footer_flush_throttles_counters_but_phase_changes_are_immediate():
    ctrl, session = setup()
    await ctrl._do_flush(session)
    assert session.sequence == 2
    ctrl._client.cardkit_batch_update.assert_awaited_once()
    await ctrl._do_flush(session)
    assert session.sequence == 2
    session.runtime_status.signal("answer")
    await ctrl._do_flush(session)
    assert session.sequence == 3
    assert ctrl._client.cardkit_batch_update.await_count == 2


@pytest.mark.asyncio
async def test_paused_card_updates_waiting_footer_without_flushing_body():
    ctrl, session = setup()
    session.state = SessionState.CLARIFY_PAUSED
    session.segment_state.on_answer_delta("pending answer")
    await ctrl._do_flush(session)
    actions = ctrl._client.cardkit_batch_update.await_args.args[1]
    assert "等待确认" in json.dumps(actions, ensure_ascii=False)
    assert "pending answer" not in json.dumps(actions)
    ctrl._client.cardkit_stream_element.assert_not_awaited()
    assert session.segment_state.segments[0].dirty
    assert not session.segment_state.segments[0].created


@pytest.mark.asyncio
async def test_footer_only_update_never_finalizes_body_reasoning():
    ctrl, session = setup()
    session.segment_state.on_reasoning_delta("private reasoning")
    seg = session.segment_state.segments[0]
    seg.created, seg.elapsed_ms = True, 100
    await ctrl._flush_runtime_footer(session)
    assert not seg.reasoning_finalized and seg.dirty


@pytest.mark.asyncio
async def test_footer_failure_backs_off_without_advancing_sequence():
    ctrl, session = setup()
    ctrl._client.cardkit_batch_update.side_effect = FeishuAPIError("limited", code=99991400)
    assert not await ctrl._flush_runtime_footer(session)
    assert session.sequence == 1 and session.runtime_last_signature is None
    session.runtime_status.signal("thinking")  # phase change also respects failure backoff
    await ctrl._flush_runtime_footer(session)
    ctrl._client.cardkit_batch_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_footer_recovery_replays_body_and_keeps_footer():
    ctrl, session = setup()
    session.segment_state.on_answer_delta("retained answer")
    segment = session.segment_state.segments[0]
    segment.created, segment.dirty = True, False
    ctrl._client.cardkit_batch_update.side_effect = FeishuAPIError(
        "not found element_id: footer_details", code=300313,
    )
    # Match the upstream error extractor contract rather than relying on this fixture's message grammar.
    with patch("hermes_lark_streaming.streaming.controller.extract_missing_element_id", return_value="footer_details"):
        assert not await ctrl._flush_runtime_footer(session)
    assert not segment.created and segment.dirty
    card = ctrl._client.cardkit_update.await_args.args[1]
    assert any(e.get("element_id") == "footer_details" for e in card["body"]["elements"])
    assert session.sequence == 2


@pytest.mark.asyncio
async def test_periodic_timer_has_one_handle_and_cancels_on_cleanup():
    ctrl, session = setup()
    loop = session._loop
    with patch.object(loop, "call_later") as schedule, patch.object(ctrl, "_schedule_flush") as flush:
        ctrl._start_runtime_timer(session)
        ctrl._start_runtime_timer(session)
        schedule.assert_called_once()
        assert schedule.call_args.args[0] == 5.0
        tick = schedule.call_args.args[1]
        tick()
        assert flush.call_count == 1 and schedule.call_count == 2
        session.flush.mark_completed()
        tick()
        assert flush.call_count == 1
    ctrl._start_runtime_timer(session)
    ctrl._cleanup_session(session)
    assert session.runtime_timer is None


@pytest.mark.asyncio
async def test_completion_failure_and_stop_do_not_leave_live_footer():
    ctrl, session = setup()
    session.segment_state.on_answer_delta("existing answer")
    session.state = SessionState.FAILED
    ctrl._start_runtime_timer(session)
    assert session.runtime_timer is None
    assert await ctrl._do_complete_card(session)
    card = ctrl._client.cardkit_update.await_args.args[1]
    assert "loading_icon" not in json.dumps(card)
    assert "本轮失败" in json.dumps(card, ensure_ascii=False)
    assert "existing answer" in json.dumps(card)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,enabled", [("classic", True), ("enhanced", False)])
async def test_disabled_or_classic_adds_no_timer_or_footer_updates(mode, enabled):
    ctrl, session = setup()
    ctrl._cfg._raw["streaming"]["footer"].update(mode=mode, enabled=enabled)
    ctrl._start_runtime_timer(session)
    assert session.runtime_timer is None and ctrl._runtime_snapshot(session) is None
    await ctrl._do_flush(session)
    ctrl._client.cardkit_batch_update.assert_not_awaited()
    card = build_streaming_card_v2(show_tool_use=False, show_streaming_element=False)
    assert len(card["body"]["elements"]) == 1


@pytest.mark.asyncio
async def test_initial_and_split_cards_include_runtime_panel_without_double_count():
    ctrl, session = setup()
    session.state = SessionState.IDLE
    try:
        await ctrl._do_create_card(session)
        initial = ctrl._client.cardkit_create.await_args.args[0]
        assert "footer_details" in json.dumps(initial)
        assert session.element_count == 1  # remaining footer is already in the reserve
        assert session.runtime_timer is not None
        await ctrl._create_streaming_card(session)
        replacement = ctrl._client.cardkit_create.await_args.args[0]
        assert "footer_details" in json.dumps(replacement)
    finally:
        ctrl._cleanup_session(session)


@pytest.mark.asyncio
async def test_tools_and_confirmation_display_only_names_and_counts():
    ctrl, session = setup()
    session.tool_use.record_start("terminal", "PRIVATE ARGS")
    data = ctrl._runtime_snapshot(session)
    assert data["runtime_phase"] == "tool" and data["runtime_tool"] == "terminal"
    assert "PRIVATE" not in repr(data)
    with patch.object(ctrl, "request_runtime_update") as notify:
        ctrl.on_approval_enter(message_id=session.message_id)
        notify.assert_called_once_with(session)
    assert ctrl._runtime_snapshot(session)["runtime_phase"] == "waiting"


@pytest.mark.asyncio
async def test_concurrent_footer_requests_use_existing_flush_mutex():
    ctrl, session = setup()
    entered, release = asyncio.Event(), asyncio.Event()
    active, peak = 0, 0

    async def batch(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        await release.wait()
        active -= 1

    ctrl._client.cardkit_batch_update.side_effect = batch
    first = asyncio.create_task(session.flush.flush_now(lambda: ctrl._do_flush(session)))
    await entered.wait()
    await session.flush.flush_now(lambda: ctrl._do_flush(session))
    release.set()
    await first
    await asyncio.sleep(0)
    session.flush.mark_completed()
    assert peak == 1
    assert session.sequence == 2


@pytest.mark.asyncio
async def test_manual_handoff_defers_scheduled_flush_and_preserves_tool_totals():
    ctrl, session = setup()
    session.tool_use.record_start("clarify")
    session.tool_use.record_end("clarify", output="done")
    session.manual_split_in_progress = True
    with patch.object(session.flush, "schedule_update") as schedule:
        ctrl._schedule_flush(session)
        schedule.assert_not_called()
    session.manual_split_in_progress = False
    with patch.object(ctrl, "_do_flush", new_callable=AsyncMock):
        assert await ctrl._do_clarify_split(session)
    assert ctrl._runtime_snapshot(session)["tool_calls"] == 1
    session.tool_use.record_start("terminal")
    assert ctrl._runtime_snapshot(session)["tool_calls"] == 2
    assert ctrl._runtime_snapshot(session)["runtime_tools_done"] == 1
    session.state = SessionState.COMPLETED
    assert await ctrl._do_complete_card(session)
    assert session.footer["tool_calls"] == 2


@pytest.mark.parametrize("nested", [True, False])
@pytest.mark.parametrize("raw,expected", [
    ("RateLimitError", "RateLimitError"), ("httpx.TimeoutException", "httpx.TimeoutException"),
    ("PRIVATE TEXT contains body", ""), ("https://secret.invalid", ""), ("sk_privatevalue", ""), (None, ""),
])
def test_failure_type_only_never_stores_error_message(raw, expected, nested):
    state = TurnFooter()
    p = event()
    state.observe("pre_api_request", p)
    error = {"type": raw, "message": "PRIVATE EXCEPTION BODY"} if nested else "PRIVATE EXCEPTION BODY"
    state.observe("api_request_error", {**p, "error_type": None if nested else raw, "error": error})
    data = state.finish()
    assert data.get("last_error_type", "") == expected
    assert "PRIVATE" not in repr(state._requests) + repr(data)
