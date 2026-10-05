"""Card handoffs retain missing-result evidence without inventing active work."""

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card
from hermes_lark_streaming.cardkit.reference import build_reference_footer, build_tools
from hermes_lark_streaming.controller import StreamCardController
from hermes_lark_streaming.feishu import FeishuClient
from hermes_lark_streaming.streaming.session import CardSession, SessionState


def running_step():
    return {
        "name": "terminal",
        "title": "Run command",
        "status": "running",
        "detail": "fixture command",
        "elapsed_ms": None,
        "error": "",
        "output": "",
        "error_block": None,
        "result_block": None,
    }


def reference(steps=None):
    return {
        "steps": steps or [],
        "tools_prior": 3,
        "done_prior": 2,
        "failed_prior": 1,
        "failed_total": 1,
        "succeeded_total": 1,
        "resources_enabled": False,
        "show_tools": True,
    }


@pytest.mark.parametrize("flags", [{}, {"is_error": True}, {"is_aborted": True}])
@pytest.mark.parametrize("active", [False, True])
def test_final_prefix_and_footer_agree_on_missing_results_across_cards(flags, active):
    ref = reference([running_step()] if active else [])
    data = {"presentation": "reference", "reference": ref}
    before = deepcopy(data)
    card = build_complete_card(
        segments=[], all_tool_steps=ref["steps"], footer_data=data, footer_mode="enhanced", **flags
    )
    panels = {e["element_id"]: e for e in card["body"]["elements"] if e.get("element_id")}
    expected = 2 if active else 1
    for name in ("reference_tools", "footer_details"):
        text = json.dumps(panels[name], ensure_ascii=False)
        assert f"{expected} 结果未确认" in text
        assert "运行中" not in text and "running" not in text.lower()
    assert data == before and inspect_card(card).safe


def test_live_prefix_separates_prior_unconfirmed_from_current_running():
    panel = build_tools({}, reference([running_step()]))
    title = panel["header"]["title"]
    assert "1 unconfirmed" in title["content"] and "1 running" in title["content"]
    assert "1 结果未确认" in title["i18n_content"]["zh_cn"] and "1 运行中" in title["i18n_content"]["zh_cn"]
    assert "2/4" in title["content"]
    text = json.dumps(panel, ensure_ascii=False)
    assert "Turn ended" not in text and "本轮已结束" not in text
    assert "前卡 3 步" in text


@pytest.mark.parametrize("prior,done", [(0, 0), (3, 3), (2, 3)])
def test_no_prior_gap_does_not_invent_an_unconfirmed_step(prior, done):
    ref = {**reference(), "tools_prior": prior, "done_prior": done}
    for obj in (build_tools({}, ref, terminal=True), build_reference_footer({"reference": ref})):
        assert "结果未确认" not in json.dumps(obj, ensure_ascii=False)


@pytest.mark.parametrize("terminal", [False, True])
def test_handoff_counts_share_existing_native_panel_budget(terminal):
    steps = [dict(running_step(), detail="<>&" * 200) for _ in range(24)]
    steps.extend(dict(running_step(), status="error", error="错误" * 300) for _ in range(24))
    ref = {**reference(steps), "tools_prior": 9999, "done_prior": 9000}
    before = deepcopy(ref)
    panel = build_tools({}, ref, terminal=terminal)
    assert len(json.dumps(panel, ensure_ascii=False, separators=(",", ":")).encode()) <= 13000
    assert ref == before
    assert panel["element_id"] == "reference_tools" and panel["expanded"] is False


@pytest.mark.asyncio
async def test_new_turn_does_not_inherit_another_sessions_missing_results(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"layout": "reference", "footer": {"mode": "enhanced"}}}
    first = CardSession("first", "chat", asyncio.get_running_loop())
    first.tool_calls_prior, first.tools_done_prior = 3, 1
    first.state = SessionState.COMPLETED
    second = CardSession("second", "chat", asyncio.get_running_loop())
    second.state = SessionState.COMPLETED
    previous = build_reference_footer(ctrl._reference_snapshot(first, {}))
    current = build_reference_footer(ctrl._reference_snapshot(second, {}))
    assert "2 结果未确认" in json.dumps(previous, ensure_ascii=False)
    assert "结果未确认" not in json.dumps(current, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED])
async def test_repeated_real_controller_handoffs_keep_counts_in_final_card(tmp_path, state):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {
        "streaming": {
            "enabled": True,
            "layout": "reference",
            "resources": {"enabled": False},
            "footer": {"mode": "enhanced", "history": {"enabled": False}},
        }
    }
    ctrl._initialized = True
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("handoff-fixture", "fixture-chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id="first-card", card_msg_id="first-message")
    session.flush.set_card_message_ready(True)
    # Each handoff archives a missing result plus a confirmed success. No host
    # process is started or stopped: only the display tracker receives events.
    with (
        patch.object(ctrl, "_do_flush", new_callable=AsyncMock),
        patch.object(
            ctrl,
            "_create_streaming_card",
            new_callable=AsyncMock,
            side_effect=[("second-card", "second-message"), ("third-card", "third-message")],
        ),
    ):
        for index in range(2):
            session.tool_use.record_start("terminal", f"fixture pending {index}")
            session.tool_use.record_start("clarify")
            session.tool_use.record_end("clarify", output="confirmed")
            assert await ctrl._do_clarify_split(session)
    assert (session.tool_calls_prior, session.tools_done_prior, session.tools_failed_prior) == (4, 2, 0)
    assert session.tool_use.build_display_steps() == []
    session.tool_use.record_start("terminal", "current pending")
    session.state = state
    assert await ctrl._do_complete_card_inner(session)
    card = ctrl._client.cardkit_update.await_args.args[1]
    panels = {e["element_id"]: e for e in card["body"]["elements"] if e.get("element_id")}
    for name in ("reference_tools", "footer_details"):
        assert "3 结果未确认" in json.dumps(panels[name], ensure_ascii=False)
    assert session.footer["tool_calls"] == 5
    assert session.footer["reference"]["succeeded_total"] == 2
    assert session.footer["reference"]["failed_total"] == 0
    assert session.tool_use.build_display_steps()[0]["status"] == "running"
    assert inspect_card(card).safe
