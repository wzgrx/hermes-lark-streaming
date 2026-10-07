"""Drive the real controller through the same bridge functions the injected snippets call."""

from __future__ import annotations

import pytest

from hermes_lark_streaming import session as session_pkg
from hermes_lark_streaming.hooks import bridge

from .conftest import settle, state_tag

pytestmark = pytest.mark.asyncio


@pytest.fixture
def wired(make_controller, monkeypatch):
    ctl = make_controller()
    monkeypatch.setattr(session_pkg, "get_controller", lambda: ctl)
    return ctl


async def test_turn_through_the_bridge(wired, client):
    bridge.on_message_started(message_id="om_1", chat_id="oc_1", anchor_id=None, session_key="sk")
    await wired._wait_creation(wired._sessions["om_1"])
    assert bridge.on_tool_updated(message_id="om_1", tool_name="terminal", status="started", detail="ls")
    assert bridge.on_tool_updated(
        message_id="om_1", tool_name="terminal", status="completed", detail="",
        result='{"exit_code": 2, "output": "no such file"}', is_error=None,
    )
    assert bridge.on_answer_delta(message_id="om_1", text="结果如下")
    await settle(wired)
    sent = await bridge.on_message_completed_wait(
        message_id="om_1", answer="结果如下", duration=2.0, model="m",
        tokens={"input_tokens": 1, "output_tokens": 1}, context={"used_tokens": 5, "max_tokens": 100},
    )
    assert sent is True
    final = client.of("update_card")[-1][2]
    assert "1 步失败" in state_tag(final)
    assert "5/100" in str(final)  # the result payload fills in the context chip when telemetry saw nothing


async def test_every_bridge_target_exists_on_the_controller(wired):
    names = {
        "on_message_started", "on_completed_wait", "consume_text_fallback", "on_tool_update", "on_answer",
        "on_thinking", "on_reasoning", "defer_background_review", "on_aborted", "on_session_aborted",
        "on_interrupted", "on_cron_deliver", "on_background_deliver", "on_approval_enter", "on_clarify_enter",
        "on_clarify_exit",
    }
    assert all(callable(getattr(wired, name, None)) for name in names)


async def test_stop_and_fallback_paths(wired, client):
    bridge.on_message_started(message_id="om_2", chat_id="oc_1", session_key="sk2")
    await wired._wait_creation(wired._sessions["om_2"])
    assert await bridge.on_session_aborted(session_key="sk2") is True
    assert bridge.on_message_needs_text_fallback(message_id="om_2") is False


async def test_telemetry_reaches_the_final_meta_chips(wired, client):
    import time

    bridge.on_message_started(message_id="om_3", chat_id="oc_1", session_key="sk3")
    session = await _ready(wired, "om_3")
    payload = {"platform": "feishu", "session_id": "s", "turn_id": "t", "api_request_id": "r",
               "started_at": time.time() + 1, "provider": "p", "model": "deepseek-v4.1-flash"}
    assert wired.observe("pre_api_request", payload, session_key="sk3") is True
    assert wired.observe("pre_api_request", payload, session_key="unknown") is False
    assert session.state.value == "streaming"
    assert await bridge.on_message_completed_wait(message_id="om_3", answer="ok") is True
    assert "deepseek" in str(client.of("update_card")[-1][2]).lower()


async def _ready(ctl, mid):
    session = ctl._sessions[mid]
    await ctl._wait_creation(session)
    return session


async def test_provider_known_from_telemetry_starts_account_reads(wired, client):
    import time

    bridge.on_message_started(message_id="om_4", chat_id="oc_1", session_key="sk4")
    session = await _ready(wired, "om_4")
    requests: list[dict] = []
    wired.collector.request = lambda **kw: requests.append(kw)  # type: ignore[method-assign]
    payload = {"platform": "feishu", "session_id": "s", "turn_id": "t", "api_request_id": "r",
               "started_at": time.time() + 1, "provider": "opencode-go", "model": "m"}
    import asyncio
    import threading

    def from_worker() -> None:  # Hermes calls observers from agent worker threads, never the loop
        wired.observe("pre_api_request", payload, session_key="sk4")
        wired.observe("pre_api_request", payload, session_key="sk4")  # unchanged provider: no second request

    worker = threading.Thread(target=from_worker)
    worker.start()
    worker.join()
    await asyncio.sleep(0.05)  # the request runs on the loop
    assert requests == [{"chat_id": "oc_1", "provider": "opencode-go"}] and session.provider == "opencode-go"
