"""Bridge functions: forward to the controller, never raise, honour a disabled controller."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes_lark_streaming.hooks import bridge


def test_sync_calls_forward_keyword_arguments(controller: MagicMock) -> None:
    bridge.on_message_started(message_id="m", chat_id="c", anchor_id="a", session_key="s")
    controller.on_message_started.assert_called_once_with(message_id="m", chat_id="c", anchor_id="a", session_key="s")
    controller.on_thinking.return_value = True
    controller.on_reasoning.return_value = False
    controller.on_answer.return_value = True
    assert bridge.on_thinking_delta(message_id="m", text="t") is True
    assert bridge.on_reasoning_delta(message_id="m", text="t") is False
    assert bridge.on_answer_delta(message_id="m", text="t") is True
    bridge.on_message_aborted(message_id="m")
    controller.on_aborted.assert_called_once_with(message_id="m")
    bridge.on_message_interrupted(message_id="old", new_message_id="new", chat_id="c", anchor_id="a", session_key="s")
    controller.on_interrupted.assert_called_once_with(
        old_message_id="old", new_message_id="new", chat_id="c", anchor_id="a", session_key="s"
    )
    bridge.on_approval_enter(message_id="m")
    bridge.on_clarify_enter(message_id="m", chat_id="c", session_key="s")
    bridge.on_clarify_exit(message_id="m", chat_id="c", session_key="s")
    controller.on_approval_enter.assert_called_once_with(message_id="m")
    controller.on_clarify_enter.assert_called_once_with(message_id="m", chat_id="c", session_key="s")
    controller.on_clarify_exit.assert_called_once_with(message_id="m", chat_id="c", session_key="s")


def test_background_review_and_text_fallback(controller: MagicMock) -> None:
    sender = MagicMock()
    controller.defer_background_review.return_value = True
    controller.consume_text_fallback.return_value = True
    assert bridge.on_background_review_message(message_id="m", text="t", sender=sender) is True
    controller.defer_background_review.assert_called_once_with(message_id="m", text="t", sender=sender)
    assert bridge.on_message_needs_text_fallback(message_id="m") is True
    controller.consume_text_fallback.assert_called_once_with("m")


@pytest.mark.asyncio
async def test_async_calls_forward(controller: MagicMock) -> None:
    controller.on_completed_wait = AsyncMock(return_value=True)
    controller.on_session_aborted = AsyncMock(return_value=True)
    controller.on_background_deliver = AsyncMock(return_value=True)
    assert await bridge.on_message_completed_wait(message_id="m", answer="a", tokens={"x": 1}) is True
    assert controller.on_completed_wait.await_args.kwargs == {
        "message_id": "m", "answer": "a", "is_error": False, "duration": 0.0, "model": "",
        "tokens": {"x": 1}, "context": None, "deliver_all_media": False,
    }  # fmt: skip
    assert await bridge.on_session_aborted(session_key="k") is True
    controller.on_session_aborted.assert_awaited_once_with(session_key="k")
    assert await bridge.on_background_deliver(chat_id="c", preview="p", content="x", reply_to_message_id="r") is True
    controller.on_background_deliver.assert_awaited_once_with(
        chat_id="c", preview="p", content="x", reply_to_message_id="r"
    )


def test_disabled_controller_returns_defaults_without_forwarding(controller: MagicMock) -> None:
    controller.enabled = False
    assert bridge.on_answer_delta(message_id="m", text="t") is False
    assert bridge.on_cron_deliver(chat_id="c", content="x", loop=MagicMock()) is False
    assert bridge.on_message_started(message_id="m", chat_id="c") is None
    controller.on_answer.assert_not_called()
    controller.on_cron_deliver.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_controller_async_defaults(controller: MagicMock) -> None:
    controller.enabled = False
    assert await bridge.on_message_completed_wait(message_id="m") is False
    assert await bridge.on_session_aborted(session_key="k") is False
    assert await bridge.on_background_deliver(chat_id="c", preview="p", content="x") is False


def test_missing_controller_is_treated_as_disabled(monkeypatch: pytest.MonkeyPatch, controller: MagicMock) -> None:
    import sys

    monkeypatch.setattr(sys.modules["hermes_lark_streaming.session"], "get_controller", lambda: None, raising=False)
    assert bridge.on_answer_delta(message_id="m", text="t") is False


def test_controller_exceptions_never_reach_hermes(controller: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
    for name in ("on_answer", "on_message_started", "on_cron_deliver", "on_tool_update"):
        getattr(controller, name).side_effect = RuntimeError("boom")
    with caplog.at_level(logging.DEBUG, logger="hermes_lark_streaming"):
        assert bridge.on_answer_delta(message_id="m", text="t") is False
        assert bridge.on_message_started(message_id="m", chat_id="c") is None
        assert bridge.on_cron_deliver(chat_id="c", content="x") is False
        assert bridge.on_tool_updated(message_id="m", tool_name="t", status="started") is False
    assert any("on_cron_deliver error" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_async_controller_exceptions_never_reach_hermes(controller: MagicMock) -> None:
    controller.on_completed_wait = AsyncMock(side_effect=RuntimeError("boom"))
    controller.on_session_aborted = AsyncMock(side_effect=RuntimeError("boom"))
    assert await bridge.on_message_completed_wait(message_id="m") is False
    assert await bridge.on_session_aborted(session_key="k") is False


def test_session_import_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "hermes_lark_streaming.session", None)  # import raises ImportError
    assert bridge.on_answer_delta(message_id="m", text="t") is False


def test_cron_deliver_forwards_all_arguments(controller: MagicMock) -> None:
    loop = MagicMock()
    controller.on_cron_deliver.return_value = {"message_id": "om"}
    result = bridge.on_cron_deliver(chat_id="c1", content="hello", loop=loop, task_name="t", run_time="r", job_id="j")
    assert result == {"message_id": "om"}
    controller.on_cron_deliver.assert_called_once_with(
        chat_id="c1", content="hello", loop=loop, task_name="t", run_time="r", job_id="j", media_files=None,
        execution_id="",
    )


def test_tool_update_normalizes_terminal_failure(controller: MagicMock) -> None:
    controller.on_tool_update.return_value = True
    assert bridge.on_tool_updated(
        message_id="m", tool_name="terminal", status="completed", detail="ls",
        result='{"exit_code": 2, "stderr": "token=abc123 nope"}',
    )  # fmt: skip
    kwargs = controller.on_tool_update.call_args.kwargs
    assert kwargs["status"] == "error" and "Exit code 2" in kwargs["detail"] and "abc123" not in kwargs["detail"]


def test_tool_start_detail_is_untouched(controller: MagicMock) -> None:
    bridge.on_tool_updated(message_id="m", tool_name="terminal", status="started", detail="ls -la")
    assert controller.on_tool_update.call_args.kwargs["status"] == "started"
    assert controller.on_tool_update.call_args.kwargs["detail"] == "ls -la"


class TestFollowupBoundary:
    @pytest.mark.asyncio
    async def test_marks_result_when_card_sent(self, controller: MagicMock) -> None:
        controller.on_completed_wait = AsyncMock(return_value=True)
        result: dict[str, Any] = {"final_response": "ok", "model": "m", "input_tokens": 3}
        assert await bridge.on_queued_followup_boundary(message_id="msg", result=result) is True
        assert (result["response_previewed"], result["already_sent"], result["final_response"]) == (True, True, "")
        kwargs = controller.on_completed_wait.await_args.kwargs
        assert (
            kwargs["deliver_all_media"] is True and kwargs["answer"] == "ok" and kwargs["tokens"]["input_tokens"] == 3
        )

    @pytest.mark.asyncio
    async def test_consumes_text_fallback_when_card_not_sent(self, controller: MagicMock) -> None:
        controller.on_completed_wait = AsyncMock(return_value=False)
        result: dict[str, Any] = {"final_response": "plain"}
        assert await bridge.on_queued_followup_boundary(message_id="msg", result=result) is False
        controller.consume_text_fallback.assert_called_once_with("msg")
        assert "response_previewed" not in result

    @pytest.mark.asyncio
    async def test_interrupted_flag_overrides_result_dict(self, controller: MagicMock) -> None:
        controller.on_completed_wait = AsyncMock(return_value=True)
        assert (
            await bridge.on_queued_followup_boundary(message_id="m", result={"interrupted": False}, interrupted=True)
            is False
        )
        controller.on_completed_wait.assert_not_awaited()

    def test_result_hook_preserves_deepest_completion_id(self, controller: MagicMock) -> None:
        result = {"_hermes_lark_completion_id": "deep"}
        bridge.on_queued_followup_result(message_id="outer", followup_result=result)
        assert result["_hermes_lark_completion_id"] == "deep"


class TestNormalize:
    def run(self, controller: MagicMock, *, thread: Any, reply_to: Any, raw: Any, platform: str = "feishu") -> Any:
        source = SimpleNamespace(platform=SimpleNamespace(value=platform), thread_id=thread)
        event = SimpleNamespace(reply_to_message_id=reply_to, raw_message=raw, source=None)
        bridge.on_feishu_normalize(message_id="m", source=source, event=event, reply_anchor_id="a")
        return source, event

    def test_false_thread_id_on_quoted_message_is_cleared(self, controller: MagicMock) -> None:
        source, event = self.run(controller, thread="t1", reply_to="parent", raw={"event": {"message": {}}})
        assert source.thread_id is None and event.source is source

    def test_real_thread_id_is_kept(self, controller: MagicMock) -> None:
        source, _ = self.run(
            controller, thread="t1", reply_to="parent", raw={"event": {"message": {"thread_id": "t1"}}}
        )
        assert source.thread_id == "t1"

    def test_object_shaped_raw_message(self, controller: MagicMock) -> None:
        raw = SimpleNamespace(event=SimpleNamespace(message=SimpleNamespace(thread_id=None)))
        source, _ = self.run(controller, thread="t1", reply_to="parent", raw=raw)
        assert source.thread_id is None

    def test_not_quoted_or_other_platform_untouched(self, controller: MagicMock) -> None:
        assert self.run(controller, thread="t1", reply_to=None, raw={})[0].thread_id == "t1"
        assert self.run(controller, thread="t1", reply_to="p", raw={}, platform="telegram")[0].thread_id == "t1"
