from __future__ import annotations

import asyncio

import pytest

from hermes_lark_streaming.card.render import ANSWER_ID, FOOTER_ID, PROCESS_ID, STATUS_ID
from hermes_lark_streaming.session.state import State
from hermes_lark_streaming.transport import FeishuAPIError

from .conftest import settle

pytestmark = pytest.mark.asyncio


def element_ids(card):
    return [e.get("element_id") for e in card["body"]["elements"]]


async def start(ctl, mid="om_1", chat="oc_1", **kw):
    ctl.on_message_started(message_id=mid, chat_id=chat, **kw)
    session = ctl._sessions[mid]
    await ctl._wait_creation(session)
    return session


async def test_happy_path_streams_then_finalizes(make_controller, client):
    ctl = make_controller()
    session = await start(ctl)
    assert client.of("create_card")[0]["config"]["streaming_mode"] is True
    assert client.of("reply_card") == [("om_1", "card_1")]
    assert session.state is State.STREAMING

    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="started", detail="git status")
    ctl.on_answer(message_id="om_1", text="先看")
    ctl.on_answer(message_id="om_1", text="状态。")
    await settle(ctl)
    # the process panel is inserted before the answer, then the answer streams
    batch = client.of("batch_update")[-1][2]
    assert any(a["action"] == "add_elements" and a["params"]["target_element_id"] == ANSWER_ID for a in batch)
    assert client.of("stream")[-1][2:] == (ANSWER_ID, "先看状态。")

    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="completed", result="{}")
    sent = await ctl.on_completed_wait(message_id="om_1", answer="先看状态。", duration=3.2, model="m1")
    assert sent is True
    seqs = [c[1][1] for c in client.calls if c[0] in {"batch_update", "stream", "close", "update_card"}]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # strictly increasing
    assert [c[0] for c in client.calls][-2:] == ["close", "update_card"]
    final = client.of("update_card")[-1][2]
    assert "streaming_mode" not in final["config"]
    assert element_ids(final)[:2] == [STATUS_ID, PROCESS_ID] and FOOTER_ID in element_ids(final)
    assert final["body"]["elements"][0]["content"].startswith("<font color='green'>")
    assert ctl._sessions == {} and ctl.consume_text_fallback("om_1") is False


async def test_failed_tool_and_failed_turn_are_distinct(make_controller, client):
    ctl = make_controller()
    await start(ctl)
    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="started", detail="exit 7")
    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="completed",
                       result='{"exit_code": 7, "output": "boom"}')
    assert await ctl.on_completed_wait(message_id="om_1", answer="done") is True
    final = client.of("update_card")[-1][2]
    assert "1 failed" in final["body"]["elements"][0]["content"]
    assert final["body"]["elements"][1]["expanded"] is True

    ctl2 = make_controller()
    await start(ctl2, "om_2")
    assert await ctl2.on_completed_wait(message_id="om_2", answer="", is_error=True) is True
    assert "Failed" in client.of("update_card")[-1][2]["body"]["elements"][0]["content"]


async def test_creation_rejected_yields_to_gateway(make_controller, client):
    ctl = make_controller()
    client.fail["create_card"] = FeishuAPIError("bad", 99991)
    await start(ctl)
    assert await ctl.on_completed_wait(message_id="om_1", answer="hi") is False
    assert ctl.consume_text_fallback("om_1") is True
    assert client.of("update_card") == []


async def test_unknown_attach_keeps_card_and_sends_one_notice(make_controller, client):
    ctl = make_controller()
    client.fail["reply_card"] = ConnectionError("lost response")
    session = await start(ctl)
    assert session.has_card and session.card_msg_id is None
    assert client.of("send_text") and "可能已经送达" in client.of("send_text")[0]
    assert await ctl.on_completed_wait(message_id="om_1", answer="x") is True
    assert len(client.of("send_text")) == 1  # the notice is idempotent per message


async def test_interrupt_stops_old_and_redirects_completion(make_controller, client):
    ctl = make_controller()
    await start(ctl, "om_a")
    ctl.on_interrupted(old_message_id="om_a", new_message_id="om_b", chat_id="oc_1")
    await settle(ctl, 0.3)
    assert "已停止" in client.of("update_card")[0][2]["body"]["elements"][0]["i18n_content"]["zh_cn"]
    assert "om_b" in ctl._sessions
    ctl.on_answer(message_id="om_b", text="新的回答")
    assert await ctl.on_completed_wait(message_id="om_a", answer="新的回答") is True  # redirected
    assert ctl._sessions == {}


async def test_stop_by_session_key(make_controller, client):
    ctl = make_controller()
    await start(ctl, session_key="sk")
    assert await ctl.on_session_aborted(session_key="sk") is True
    assert "已停止" in client.of("update_card")[-1][2]["body"]["elements"][0]["i18n_content"]["zh_cn"]


async def test_time_limit_handoff_seals_old_and_continues(make_controller, client):
    ctl = make_controller({"rollover_sec": 60})
    session = await start(ctl)
    ctl.on_answer(message_id="om_1", text="第一段")
    await settle(ctl)
    session.card_started -= 100  # pretend the card is older than the rollover age
    ctl.on_answer(message_id="om_1", text="第二段")
    await settle(ctl, 0.6)
    assert client.cards == 2
    sealed = next(c for c in client.of("update_card") if c[0] == "card_1")[2]
    assert "已分页" in sealed["body"]["elements"][0]["i18n_content"]["zh_cn"]
    assert "第一段第二段" in str(sealed) or "第一段" in str(sealed)
    ctl.on_answer(message_id="om_1", text="第三段")
    assert await ctl.on_completed_wait(message_id="om_1", answer="") is True
    final = client.of("update_card")[-1]
    assert final[0] == "card_2" and "第三段" in str(final[2])


async def test_background_review_embeds_in_card(make_controller, client):
    ctl = make_controller()
    await start(ctl)
    sent: list[str] = []
    assert ctl.defer_background_review(message_id="om_1", text="已保存记忆", sender=sent.append)
    assert await ctl.on_completed_wait(message_id="om_1", answer="ok") is True
    assert "已保存记忆" in str(client.of("update_card")[-1][2]) and sent == []


async def test_cron_and_background_cards(make_controller, client):
    ctl = make_controller()
    out = await asyncio.to_thread(
        ctl.on_cron_deliver, chat_id="oc_1", content="日报", loop=asyncio.get_running_loop(),
        task_name="晨报", run_time="2026-10-07T08:00:00", job_id="j1")
    assert out["success"] is True and out["message_id"] == "om_static"
    again = await asyncio.to_thread(
        ctl.on_cron_deliver, chat_id="oc_1", content="日报", loop=asyncio.get_running_loop(),
        task_name="晨报", run_time="2026-10-07T08:00:00", job_id="j1")
    assert again["message_id"] == "om_static" and len(client.of("send_card")) == 1  # idempotent per occurrence
    assert await ctl.on_background_deliver(chat_id="oc_1", preview="整理", content="完成") is True


async def test_disabled_controller_is_inert(make_controller, client):
    ctl = make_controller({"enabled": False})
    ctl.on_message_started(message_id="om_1", chat_id="oc_1")
    assert ctl._sessions == {} and client.calls == []
    assert ctl.on_answer(message_id="om_1", text="x") is False
