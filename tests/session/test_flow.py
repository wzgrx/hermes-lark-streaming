from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_lark_streaming.card.render import STATUS_ID
from hermes_lark_streaming.session.state import State
from hermes_lark_streaming.transport import FeishuAPIError

from .conftest import settle, state_tag

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
    # new blocks are inserted above the live line; the answer block then streams as a typewriter
    adds = [a for b in client.of("batch_update") for a in b[2] if a["action"] == "add_elements"]
    assert adds and all(a["params"]["target_element_id"] == STATUS_ID for a in adds)
    assert any("git status" in str(a) for a in adds)
    assert any("先看状态。" in str(a) for a in adds)  # a new answer block arrives with its first text
    ctl.on_answer(message_id="om_1", text="继续。")
    await settle(ctl)
    streamed = client.of("stream")[-1]  # later text streams into the same element
    assert streamed[2].startswith("b") and streamed[3] == "先看状态。继续。"

    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="completed", result="{}")
    sent = await ctl.on_completed_wait(message_id="om_1", answer="先看状态。继续。", duration=3.2, model="m1")
    assert sent is True
    seqs = [c[1][1] for c in client.calls if c[0] in {"batch_update", "stream", "close", "update_card"}]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # strictly increasing
    assert [c[0] for c in client.calls][-2:] == ["close", "update_card"]
    final = client.of("update_card")[-1][2]
    assert "streaming_mode" not in final["config"]
    assert "header" not in final and state_tag(final).startswith("✅ **已完成**")
    assert STATUS_ID not in element_ids(final)
    assert ctl._sessions == {} and ctl.consume_text_fallback("om_1") is False


async def test_failed_tool_and_failed_turn_are_distinct(make_controller, client):
    ctl = make_controller()
    await start(ctl)
    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="started", detail="exit 7")
    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="completed",
                       result='{"exit_code": 7, "output": "boom"}')
    assert await ctl.on_completed_wait(message_id="om_1", answer="done") is True
    final = client.of("update_card")[-1][2]
    assert "1 步失败" in state_tag(final) and "Exit code 7" in str(final)
    failed_panel = next(e for e in final["body"]["elements"] if "1 失败" in str(e.get("header", "")))
    assert failed_panel["expanded"] is True

    ctl2 = make_controller()
    await start(ctl2, "om_2")
    assert await ctl2.on_completed_wait(message_id="om_2", answer="", is_error=True) is True
    assert "❌ **出错**</font>" in state_tag(client.of("update_card")[-1][2])


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
    assert "🛑 **已停止**" in state_tag(client.of("update_card")[0][2])
    assert "om_b" in ctl._sessions
    ctl.on_answer(message_id="om_b", text="新的回答")
    assert await ctl.on_completed_wait(message_id="om_a", answer="新的回答") is True  # redirected
    assert ctl._sessions == {}


async def test_stop_by_session_key(make_controller, client):
    ctl = make_controller()
    await start(ctl, session_key="sk")
    assert await ctl.on_session_aborted(session_key="sk") is True
    assert "🛑 **已停止**" in state_tag(client.of("update_card")[-1][2])


async def test_time_limit_handoff_seals_old_and_continues(make_controller, client):
    ctl = make_controller({"rollover_sec": 60, "rollover": True})  # the old behaviour, now opt-in
    session = await start(ctl)
    ctl.on_answer(message_id="om_1", text="第一段")
    await settle(ctl)
    session.card_started -= 100  # pretend the card is older than the rollover age
    ctl.on_answer(message_id="om_1", text="第二段")
    await settle(ctl, 0.6)
    assert client.cards == 2
    sealed = next(c for c in client.of("update_card") if c[0] == "card_1")[2]
    assert "内容见下一张卡片" in state_tag(sealed)
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


async def test_cron_card_shows_this_runs_slot_and_dedupes_by_execution(make_controller, client):
    import time as _time

    ctl = make_controller()
    loop = asyncio.get_running_loop()
    slot = "2026-10-07T19:00:00+00:00"  # Hermes hands over the run's slot as a UTC instant
    kw = dict(chat_id="oc_1", content="日报", loop=loop, task_name="晨报", run_time=slot, job_id="j1")
    first = await asyncio.to_thread(ctl.on_cron_deliver, execution_id="e1", **kw)
    again = await asyncio.to_thread(ctl.on_cron_deliver, execution_id="e1", **kw)
    assert first["message_id"] == again["message_id"] and len(client.of("send_card")) == 1
    local = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(1791399600))  # 2026-10-07 19:00 UTC
    assert local in str(client.of("send_card")[0])
    await asyncio.to_thread(ctl.on_cron_deliver, execution_id="e2", **kw)  # a manual re-run is a new card
    assert len(client.of("send_card")) == 2


# ---------------------------------------------------------------- long turns, failures, crashed gateways


async def test_a_long_running_turn_is_never_pruned_for_its_age(make_controller, client):
    ctl = make_controller({"card_ttl_sec": 60})
    session = await start(ctl, "om_long")
    session.created_at -= 3600  # an hour-long agent task
    session.touched -= 120  # quiet for two minutes (one slow tool)
    await start(ctl, "om_other", chat="oc_2")  # another chat's message used to prune it
    assert "om_long" in ctl._sessions
    ctl.on_answer(message_id="om_long", text="还在")
    assert await ctl.on_completed_wait(message_id="om_long", answer="还在") is True


async def test_an_abandoned_turn_is_closed_as_stopped(make_controller, client):
    from hermes_lark_streaming.session import controller as controller_module

    ctl = make_controller({"card_ttl_sec": 60})
    session = await start(ctl, "om_lost")
    session.touched -= controller_module._ABANDONED_SEC + 1  # its completion never came
    await start(ctl, "om_next", chat="oc_2")
    await settle(ctl, 0.3)
    assert "om_lost" not in ctl._sessions
    assert "🛑 **已停止**" in state_tag(client.of("update_card")[-1][2])


async def test_a_failed_session_still_finalizes_its_card(make_controller, client):
    from hermes_lark_streaming.session.state import State as S

    ctl = make_controller()
    session = await start(ctl)
    ctl.on_answer(message_id="om_1", text="答案")
    session.state = S.FAILED  # writes kept failing mid-turn
    assert await ctl.on_completed_wait(message_id="om_1", answer="答案") is True  # no second text reply
    assert "答案" in str(client.of("update_card")[-1][2]) and ctl.consume_text_fallback("om_1") is False


async def test_open_cards_are_registered_until_finalized(make_controller, client):
    ctl = make_controller()
    session = await start(ctl)
    assert [c.card_id for c in ctl.open_cards.orphans(set(), min_age=0)] == [session.channel.card_id]
    assert await ctl.on_completed_wait(message_id="om_1", answer="ok") is True
    assert ctl.open_cards.orphans(set(), min_age=0) == []


@pytest.mark.parametrize("uptime", [5.0, 500.0])
async def test_cards_left_live_by_a_dead_gateway_are_marked_interrupted(make_controller, client, monkeypatch, uptime):
    from hermes_lark_streaming.card.render import STATUS_ID as LIVE
    from hermes_lark_streaming.session import controller as controller_module

    # A newly booted CI runner/WSL instance must sweep on its first message too.
    # Patch only this module's clock, not the event loop's global time module.
    monkeypatch.setattr(controller_module, "time", SimpleNamespace(monotonic=lambda: uptime))
    ctl = make_controller()
    fired = []
    fire = ctl._fire

    def record_fire(coro, loop):
        future = fire(coro, loop)
        fired.append(future)
        return future

    monkeypatch.setattr(ctl, "_fire", record_fire)
    ctl.open_cards.add("card_dead", "oc_9", "om_dead")  # the previous process died mid-turn
    ctl.open_cards._write({c.card_id: c.__class__(c.card_id, c.chat_id, c.message_id, c.opened_at - 600)
                           for c in ctl.open_cards.orphans(set(), min_age=0)})
    await start(ctl)
    assert len(fired) == 2  # initial sweep plus card creation
    await asyncio.gather(*(asyncio.wrap_future(future) for future in fired))
    patched = [b for b in client.of("batch_update") if b[0] == "card_dead"]
    assert patched and patched[0][2][0]["params"]["element_id"] == LIVE and "已中断" in str(patched[0][2])
    assert patched[0][1] > 1_000_000_000  # a wall-clock sequence, above anything the dead turn used
    assert all(c.card_id != "card_dead" for c in ctl.open_cards.orphans(set(), min_age=0))
    await start(ctl, mid="om_2")
    assert len(fired) == 3  # only creation; the next sweep stays throttled


async def test_a_failed_card_create_is_not_left_pending(make_controller, client):
    ctl = make_controller()
    client.fail["create_card"] = TimeoutError()
    await start(ctl)
    assert ctl.ledger.summary()["counts"].get("pending", 0) == 0


@pytest.mark.parametrize("message_id", [None, "", 42, []])
async def test_completion_without_message_identity_preserves_active_card(make_controller, client, message_id):
    ctl = make_controller()
    session = await start(ctl)
    calls = list(client.calls)
    assert await ctl.on_completed_wait(message_id=message_id, answer="background result") is False
    assert ctl._sessions["om_1"] is session
    assert session.state is State.STREAMING
    assert client.calls == calls


async def test_a_message_folded_into_the_turn_keeps_the_card_alive(make_controller, client):
    ctl = make_controller()
    await start(ctl, session_key="sk")
    ctl.on_answer(message_id="om_1", text="先回答一部分。")
    # Hermes redirected the running turn onto the new message: every later update carries om_2
    assert ctl.on_turn_folded(message_id="om_1", new_message_id="om_2", anchor_id="om_2", text="再加一个条件",
                              session_key="sk") is True
    assert ctl.on_answer(message_id="om_2", text="已按新条件继续。") is True  # used to be refused: card froze
    assert await ctl.on_completed_wait(message_id="om_2", answer="") is True  # used to fall back to plain text
    body = [str(e) for e in client.of("update_card")[-1][2]["body"]["elements"]]
    needles = ("先回答一部分", "再加一个条件", "已按新条件继续")
    where = {needle: next(i for i, e in enumerate(body) if needle in e) for needle in needles}
    assert where["先回答一部分"] < where["再加一个条件"] < where["已按新条件继续"]  # the note sits where it arrived
    assert "💬 你补充" in body[where["再加一个条件"]]
    assert ctl._sessions == {}  # aliases are dropped with the session


async def test_subagent_results_turn_gets_its_own_card_in_the_chat(make_controller, client):
    from hermes_lark_streaming import session as session_pkg
    from hermes_lark_streaming.hooks import bridge

    ctl = make_controller()
    text = "[ASYNC DELEGATION BATCH COMPLETE — deleg_739765a9] A background fan-out unit you started finished."
    ctl.on_message_started(message_id=None, chat_id="oc_1", session_key="sk", text=text)
    mid = ctl.synthetic_id_for("sk")
    assert mid and mid.startswith("synthetic-")
    await ctl._wait_creation(ctl._sessions[mid])
    assert client.of("reply_card") == [] and client.of("send_card_entity")  # posted into the chat, no reply
    with patch.object(session_pkg, "get_controller", lambda: ctl):
        assert bridge.on_answer_delta(message_id=None, text="两个子代理都完成了。", session_key="sk") is True
        assert await bridge.on_message_completed_wait(message_id=None, answer="", session_key="sk") is True
    final = client.of("update_card")[-1][2]
    body = [str(e) for e in final["body"]["elements"]]
    assert "🔀 后台子代理已完成" in body[0] and "两个子代理都完成了" in str(final)
    assert ctl.synthetic_id_for("sk") is None and ctl._sessions == {}


async def test_unknown_generated_turns_stay_native(make_controller, client):
    ctl = make_controller()
    ctl.on_message_started(message_id=None, chat_id="oc_1", session_key="sk", text="[SYSTEM: something else]")
    assert ctl._sessions == {} and ctl.synthetic_id_for("sk") is None and client.calls == []


def sub(i: int, **extra):
    return {"subagent_id": f"sa-{i}", "task_index": i, "task_count": 2, "goal": f"任务 {i}",
            "delegation_id": "deleg_live", **extra}


async def test_subagents_show_as_a_panel_on_the_live_card(make_controller, client):
    ctl = make_controller()
    await start(ctl)
    assert ctl.on_subagent_event(message_id="om_1", event_type="subagent.start", details=sub(0)) is True
    assert ctl.on_subagent_event(message_id="om_1", event_type="subagent.complete", details=sub(0, status="completed",
                                 summary="完成了")) is True
    await settle(ctl)
    assert any("🔀 子代理" in str(a) for b in client.of("batch_update") for a in b[2])  # inserted while streaming
    assert await ctl.on_completed_wait(message_id="om_1", answer="汇总") is True
    assert "完成了" in str(client.of("update_card")[-1][2]) and ctl._background == {}


async def test_background_subagents_keep_updating_the_finished_card(make_controller, client, monkeypatch):
    from hermes_lark_streaming.session import controller as controller_module

    monkeypatch.setattr(controller_module, "_BACKGROUND_REFRESH_SEC", 0.05)
    ctl = make_controller()
    await start(ctl, session_key="sk")
    ctl.on_subagent_event(message_id="om_1", event_type="subagent.start", details=sub(0))
    assert await ctl.on_completed_wait(message_id="om_1", answer="已派出后台子代理") is True
    assert "1 运行中" in str(client.of("update_card")[-1][2])
    assert "om_1" in ctl._background  # the card keeps tracking its subagents after the turn ended
    finals = len(client.of("update_card"))
    ctl.on_subagent_event(message_id="om_1", event_type="subagent.tool", tool_name="terminal",
                          details=sub(0, tool_count=4))
    ctl.on_subagent_event(message_id="om_1", event_type="subagent.complete",
                          details=sub(0, status="completed", summary="三个入口", duration_seconds=125.0))
    await settle(ctl, 0.8)
    last = str(client.of("update_card")[-1][2])
    assert len(client.of("update_card")) > finals and "三个入口" in last and "全部完成" in last
    assert ctl._background == {}  # all done: tracking released
    # the summary turn Hermes starts afterwards carries the same batch, finished
    text = "[ASYNC DELEGATION BATCH COMPLETE — deleg_other] A background fan-out unit you started finished."
    ctl.on_message_started(message_id=None, chat_id="oc_1", session_key="sk", text=text)
    summary = ctl._sessions[ctl.synthetic_id_for("sk")]
    assert [e.kind.value for e in summary.timeline] == ["note", "agents"]


async def test_a_long_turn_stays_on_one_card_after_the_streaming_window(make_controller, client):
    from hermes_lark_streaming.transport.errors import StreamingClosedError

    ctl = make_controller()
    session = await start(ctl)
    ctl.on_answer(message_id="om_1", text="第一段")
    await settle(ctl)
    session.card_started -= 900  # fifteen minutes in: no time-based handoff any more
    client.fail["update_element_content"] = StreamingClosedError("streaming closed", 300309)
    ctl.on_answer(message_id="om_1", text="第二段")
    await settle(ctl, 1.6)
    assert client.cards == 1 and session.streaming_closed
    patched = [a for b in client.of("batch_update") for a in b[2] if a["action"] == "partial_update_element"]
    assert any("第一段第二段" in str(a) for a in patched)  # the answer keeps growing on the same card
    del client.fail["update_element_content"]
    assert await ctl.on_completed_wait(message_id="om_1", answer="") is True
    assert client.of("update_card")[-1][0] == "card_1"


async def test_task_list_stays_above_the_live_line(make_controller, client):
    import json as _json

    from hermes_lark_streaming import session as session_pkg
    from hermes_lark_streaming.hooks import bridge

    ctl = make_controller()
    await start(ctl)
    todos = {"todos": [{"id": "1", "content": "反编译 APK", "status": "completed"},
                       {"id": "2", "content": "定位支付接口", "status": "in_progress"},
                       {"id": "3", "content": "写报告", "status": "pending"}], "summary": {}}
    with patch.object(session_pkg, "get_controller", lambda: ctl):
        bridge.on_tool_updated(message_id="om_1", tool_name="todo_list", status="started", detail="")
        bridge.on_tool_updated(message_id="om_1", tool_name="todo_list", status="completed", detail="",
                               result=_json.dumps(todos, ensure_ascii=False))
    await settle(ctl)
    ctl.on_answer(message_id="om_1", text="继续分析。")
    await settle(ctl)
    adds = [a["params"] for b in client.of("batch_update") for a in b[2] if a["action"] == "add_elements"]
    todo_add = next(p for p in adds if "任务清单" in str(p))
    assert todo_add["target_element_id"] == STATUS_ID and "1/3 完成" in str(todo_add)
    later = [p for p in adds[adds.index(todo_add) + 1:]]
    assert later and all(p["target_element_id"].startswith("todo_") for p in later)  # inserted above the list
    assert await ctl.on_completed_wait(message_id="om_1", answer="") is True
    final = client.of("update_card")[-1][2]
    panel = next(e for e in final["body"]["elements"] if "任务清单" in str(e))
    assert panel["expanded"] is False and "~~反编译 APK~~" in str(panel) and "**定位支付接口**" in str(panel)


async def test_finished_card_lists_the_files_the_answer_delivers(make_controller, client, tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# r", encoding="utf-8")
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    ctl = make_controller()
    await start(ctl)
    answer = f"报告已生成。\nMEDIA:{report}\nMEDIA:{chart}"
    ctl.on_answer(message_id="om_1", text=answer)
    assert await ctl.on_completed_wait(message_id="om_1", answer=answer, deliver_all_media=True) is True
    final = str(client.of("update_card")[-1][2])
    assert "📎 产出 · report.md · chart.png" in final


async def test_a_new_turn_under_a_finished_turns_id_gets_its_own_card(make_controller, client):
    ctl = make_controller()
    await start(ctl, session_key="sk")
    assert await ctl.on_completed_wait(message_id="om_1", answer="第一轮。") is True
    # Hermes runs a queued notice under the old anchor: it used to get no card and went out as plain text
    text = "[IMPORTANT: Background process proc_1 completed normally (exit code 0)."
    ctl.on_message_started(message_id="om_1", chat_id="oc_1", session_key="sk", text=text)
    session = ctl._sessions["om_1"]
    await ctl._wait_creation(session)
    assert client.cards == 2 and session.reply_target is None  # posted into the chat, not quoting om_1
    assert ctl.on_answer(message_id="om_1", text="进程已经跑完。") is True
    assert await ctl.on_completed_wait(message_id="om_1", answer="") is True
    final = str(client.of("update_card")[-1][2])
    assert "后台进程通知" in final and "进程已经跑完" in final


async def test_a_reply_streaming_under_a_finished_id_is_revived_into_a_card(make_controller, client):
    ctl = make_controller()
    await start(ctl, session_key="sk")
    ctl.on_turn_folded(message_id="om_1", new_message_id="om_2", text="再补充一句", session_key="sk")
    assert await ctl.on_completed_wait(message_id="om_1", answer="第一轮。") is True
    # the folded message came back as its own turn without a start event
    assert ctl.on_tool_update(message_id="om_2", tool_name="terminal", status="started", detail="ls") is True
    session = ctl._sessions["om_2"]
    await ctl._wait_creation(session)
    assert ctl.on_answer(message_id="om_2", text="补充的也处理了。") is True
    assert await ctl.on_completed_wait(message_id="om_2", answer="") is True
    assert client.cards == 2 and client.of("reply_card")[-1][0] == "om_2"
    assert "补充的也处理了" in str(client.of("update_card")[-1][2])


async def test_ids_never_seen_stay_native(make_controller, client):
    ctl = make_controller()
    assert ctl.on_answer(message_id="om_9", text="别处的回复") is False
    assert ctl.on_tool_update(message_id="om_9", tool_name="t", status="started") is False
    assert ctl._sessions == {} and client.calls == []


async def test_late_completion_events_of_a_finished_turn_do_not_open_a_card(make_controller, client):
    ctl = make_controller()
    await start(ctl)
    assert await ctl.on_completed_wait(message_id="om_1", answer="完成。") is True
    assert ctl.on_tool_update(message_id="om_1", tool_name="t", status="completed") is False
    assert client.cards == 1
