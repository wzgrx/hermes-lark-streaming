"""Execute generated snippets in minimal scopes: the guards and carried state must behave as in legacy."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.hooks import snippets

from .conftest import indented

BRIDGE = "hermes_lark_streaming.hooks.bridge"


def build(source: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(compile(source, "<snippet-test>", "exec"), namespace)
    return namespace


def tool_callback(use_ctx: bool) -> Any:
    if use_ctx:
        ns = build(
            "class Callbacks:\n"
            "    def __init__(self, ctx):\n        self._ctx = ctx\n"
            "    def callback(self, event_type, tool_name=None, preview=None, **kwargs):\n"
            f"{indented(snippets.tool, '        ')}        return 'native'\n"
        )
        return lambda ctx: ns["Callbacks"](ctx).callback
    ns = build(
        "def callback(event_type, event_message_id, _run_still_current, tool_name=None, preview=None, **kwargs):\n"
        f"{indented(snippets.tool)}    return 'native'\n"
    )
    return lambda _ctx: ns["callback"]


def ctx_with(**values: Any) -> MagicMock:
    ctx = MagicMock()
    ctx.event_message_id = "modern-message"
    ctx._run_still_current.return_value = True
    ctx.log_queue = None
    for key, value in values.items():
        setattr(ctx, key, value)
    return ctx


class TestAnswer:
    def run(self, ctx_scope: bool) -> Any:
        signature = "text, ctx" if ctx_scope else "text, event_message_id, _run_still_current"
        return build(f"def callback({signature}):\n{indented(snippets.answer)}    return 'native'\n")["callback"]

    def test_ctx_scope_consumes_delta(self) -> None:
        with patch(f"{BRIDGE}.on_answer_delta", return_value=True) as hook:
            assert self.run(True)("delta", ctx_with()) is None
        hook.assert_called_once_with(message_id="modern-message", text="delta")

    def test_flat_scope_consumes_delta(self) -> None:
        with patch(f"{BRIDGE}.on_answer_delta", return_value=True) as hook:
            assert self.run(False)("delta", "flat-message", MagicMock(return_value=True)) is None
        hook.assert_called_once_with(message_id="flat-message", text="delta")

    def test_unconsumed_delta_falls_through(self) -> None:
        with patch(f"{BRIDGE}.on_answer_delta", return_value=False):
            assert self.run(True)("delta", ctx_with()) == "native"

    def test_stale_run_is_not_consumed(self) -> None:
        with patch(f"{BRIDGE}.on_answer_delta", return_value=True) as hook:
            assert self.run(True)("delta", ctx_with(_run_still_current=lambda: False)) == "native"
        hook.assert_not_called()

    def test_consumed_delta_disables_native_deltas_and_feeds_tts(self) -> None:
        ns = build(f"def callback(text, ctx, stts):\n{indented(snippets.answer)}    return 'native'\n")
        native, stts = MagicMock(), MagicMock()
        native.stream_deltas_enabled = True
        with patch(f"{BRIDGE}.on_answer_delta", return_value=True):
            ns["callback"]("delta", ctx_with(stream_consumer_holder=[native]), stts)
        stts.on_delta.assert_called_once_with("delta")
        assert native.stream_deltas_enabled is False

    def test_tts_failure_is_logged_not_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        ns = build(f"def callback(text, ctx, stts):\n{indented(snippets.answer)}    return 'native'\n")
        stts = MagicMock()
        stts.on_delta.side_effect = RuntimeError("tts down")
        with (
            caplog.at_level(logging.ERROR, logger="hermes_lark_streaming"),
            patch(f"{BRIDGE}.on_answer_delta", return_value=True),
        ):
            assert ns["callback"]("delta", ctx_with(), stts) is None
        assert any("streaming_tts" in r.message for r in caplog.records)

    def test_empty_text_is_never_consumed(self) -> None:
        with patch(f"{BRIDGE}.on_answer_delta", return_value=True) as hook:
            assert self.run(True)("", ctx_with()) == "native"
        hook.assert_not_called()


class TestThinking:
    def run(self) -> Any:
        return build(
            f"def callback(text, ctx, already_streamed, stts):\n{indented(snippets.thinking)}    return 'native'\n"
        )["callback"]

    def test_commentary_is_wrapped_in_tts_boundaries(self) -> None:
        stts = MagicMock()
        with patch(f"{BRIDGE}.on_thinking_delta", return_value=True):
            assert self.run()("thought", ctx_with(), False, stts) is None
        assert [c.args[0] for c in stts.on_delta.call_args_list] == [None, "thought", None]

    def test_already_streamed_is_skipped(self) -> None:
        with patch(f"{BRIDGE}.on_thinking_delta", return_value=True) as hook:
            assert self.run()("thought", ctx_with(), True, None) == "native"
        hook.assert_not_called()


class TestTool:
    def test_ctx_scope_consumes_event(self) -> None:
        ctx = ctx_with()
        with patch(f"{BRIDGE}.on_tool_updated", return_value=True) as hook:
            assert tool_callback(True)(ctx)("tool.started", tool_name="search", preview="query") is None
        hook.assert_called_once_with(
            message_id="modern-message",
            tool_name="search",
            status="started",
            detail="query",
            result=None,
            is_error=None,
        )

    def test_consumed_event_preserves_log_mode(self) -> None:
        ctx = ctx_with(log_queue=MagicMock())
        with patch(f"{BRIDGE}.on_tool_updated", return_value=True):
            tool_callback(True)(ctx)("tool.started", tool_name="search", preview="query")
        assert ctx.log_queue.put.call_args.args[0].endswith('  search: "query"')

    def test_flat_scope_consumes_event(self) -> None:
        with patch(f"{BRIDGE}.on_tool_updated", return_value=True) as hook:
            result = tool_callback(False)(None)(
                "tool.completed", "flat", MagicMock(return_value=True), tool_name="search", preview="done"
            )
        assert result is None and hook.call_args.kwargs["status"] == "completed"

    def test_completion_kwargs_are_forwarded(self) -> None:
        with patch(f"{BRIDGE}.on_tool_updated", return_value=True) as hook:
            tool_callback(True)(ctx_with())("tool.completed", tool_name="terminal", result="{}", is_error=True)
        assert hook.call_args.kwargs["result"] == "{}" and hook.call_args.kwargs["is_error"] is True

    def test_exception_is_logged_before_native_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.ERROR, logger="hermes_lark_streaming"),
            patch(f"{BRIDGE}.on_tool_updated", side_effect=RuntimeError("boom")),
        ):
            assert tool_callback(True)(ctx_with())("tool.started", tool_name="search") == "native"
        record = next(r for r in caplog.records if "injected hook failed: tool" in r.message)
        assert record.exc_info is not None


@pytest.mark.parametrize("key_name", ["quick_key", "_quick_key"])
@pytest.mark.asyncio
async def test_stop_hook_uses_available_session_key(key_name: str) -> None:
    ns = build(f"async def stop(source, {key_name}):\n{indented(snippets.stop)}")
    source = SimpleNamespace(platform=SimpleNamespace(value="feishu"))
    with patch(f"{BRIDGE}.on_session_aborted", new_callable=AsyncMock) as hook:
        await ns["stop"](source, "session:chat")
    hook.assert_awaited_once_with(session_key="session:chat")


@pytest.mark.asyncio
async def test_stop_hook_ignores_other_platforms() -> None:
    ns = build(f"async def stop(source, quick_key):\n{indented(snippets.stop)}")
    with patch(f"{BRIDGE}.on_session_aborted", new_callable=AsyncMock) as hook:
        await ns["stop"](SimpleNamespace(platform=SimpleNamespace(value="telegram")), "k")
    hook.assert_not_awaited()


class TestComplete:
    def run(self) -> Any:
        return build(
            "async def complete(agent_result, event, response, _turn_seconds, _footer_line):\n"
            f"{indented(snippets.complete)}    return agent_result, response, _footer_line\n"
        )["complete"]

    @pytest.mark.asyncio
    async def test_footer_stays_in_card_without_native_resend(self) -> None:
        agent_result: dict[str, Any] = {}
        with (
            patch(f"{BRIDGE}.on_message_completed_wait", new_callable=AsyncMock, return_value=True) as done,
            patch(f"{BRIDGE}.on_message_needs_text_fallback", return_value=False),
        ):
            result, response, footer = await self.run()(
                agent_result, SimpleNamespace(message_id="message"), "answer\n\nfooter", 1.0, "footer"
            )
        assert done.await_args.kwargs["answer"] == "answer\n\nfooter"
        assert done.await_args.kwargs["message_id"] == "message"
        assert result["already_sent"] is True and response == "answer\n\nfooter" and footer == ""

    @pytest.mark.asyncio
    async def test_error_card_suppresses_native_error(self) -> None:
        with (
            patch(f"{BRIDGE}.on_message_completed_wait", new_callable=AsyncMock, return_value=True) as done,
            patch(f"{BRIDGE}.on_message_needs_text_fallback", return_value=False),
        ):
            result, response, _ = await self.run()(
                {"failed": True}, SimpleNamespace(message_id="m"), "request failed", 1.0, ""
            )
        assert done.await_args.kwargs["is_error"] is True and result["failed"] is True and response == ""

    @pytest.mark.asyncio
    async def test_completion_id_carried_from_followup_wins(self) -> None:
        with (
            patch(f"{BRIDGE}.on_message_completed_wait", new_callable=AsyncMock, return_value=True) as done,
            patch(f"{BRIDGE}.on_message_needs_text_fallback", return_value=False),
        ):
            await self.run()({"_hermes_lark_completion_id": "deep"}, SimpleNamespace(message_id="outer"), "a", 1.0, "")
        assert done.await_args.kwargs["message_id"] == "deep"

    @pytest.mark.asyncio
    async def test_text_fallback_clears_already_sent(self) -> None:
        with (
            patch(f"{BRIDGE}.on_message_completed_wait", new_callable=AsyncMock, return_value=False),
            patch(f"{BRIDGE}.on_message_needs_text_fallback", return_value=True),
        ):
            result, response, _ = await self.run()(
                {"already_sent": True}, SimpleNamespace(message_id="m"), "a", 1.0, "f"
            )
        assert "already_sent" not in result and response == "a"


class TestFollowup:
    def run(self) -> Any:
        return build(
            "async def complete(turn_ctx, result, response):\n"
            f"{indented(snippets.followup_complete)}    return result, response\n"
        )["complete"]

    @pytest.mark.asyncio
    async def test_distinct_delivery_result_is_cleared_and_carried(self, controller: MagicMock) -> None:
        controller.on_completed_wait = AsyncMock(return_value=True)
        raw, delivery = {"final_response": "raw"}, {"final_response": "normalized"}
        raw, delivery = await self.run()(SimpleNamespace(event_message_id="message"), raw, delivery)
        assert controller.on_completed_wait.await_args.kwargs["answer"] == "normalized"
        assert raw["final_response"] == "" and delivery["final_response"] == ""

    @pytest.mark.asyncio
    async def test_core_interrupt_flag_wins_over_normalized_result(self, controller: MagicMock) -> None:
        controller.on_completed_wait = AsyncMock(return_value=True)
        raw = {"interrupted": False, "final_response": "raw"}
        delivery = {"interrupted": True, "final_response": "normalized"}
        raw, delivery = await self.run()(SimpleNamespace(event_message_id="m"), raw, delivery)
        assert controller.on_completed_wait.await_args.kwargs["answer"] == "normalized"
        assert raw["already_sent"] is True and delivery["final_response"] == ""

    @pytest.mark.asyncio
    async def test_interrupted_turn_is_skipped(self, controller: MagicMock) -> None:
        controller.on_completed_wait = AsyncMock(return_value=True)
        raw = {"interrupted": True, "final_response": "discarded"}
        raw, delivery = await self.run()(SimpleNamespace(event_message_id="m"), raw, {"final_response": "n"})
        controller.on_completed_wait.assert_not_awaited()
        assert raw["final_response"] == "discarded" and delivery["final_response"] == "n"

    def test_result_hook_carries_deepest_id(self, controller: MagicMock) -> None:
        ns = build(f"def carry(next_message_id, pending_event, followup_result):\n{indented(snippets.followup_result)}")
        deep: dict[str, Any] = {"_hermes_lark_completion_id": "deep"}
        ns["carry"]("outer", None, deep)
        assert deep["_hermes_lark_completion_id"] == "deep"
        fresh: dict[str, Any] = {}
        ns["carry"](None, SimpleNamespace(message_id="pending"), fresh)
        assert fresh["_hermes_lark_completion_id"] == "pending"


class TestCron:
    def runner(self, *, media_in_scope: bool = True) -> Any:
        params = "targets, cleaned_delivery_content, loop, transport=None" + (
            ", media_files=None" if media_in_scope else ""
        )
        source = (
            f"def deliver(job, {params}):\n"
            "    unverified_targets = []\n"
            "    mirrored = []\n"
            "    def _maybe_mirror_cron_delivery(*args, **kwargs):\n        mirrored.append(args)\n"
            "    for t in targets:\n"
            f"{indented(snippets.cron_deliver, '        ')}"
            "        unverified_targets.append(('native', t.where))\n"
            "    return unverified_targets, mirrored\n"
        )
        return build(source)["deliver"]

    @staticmethod
    def target(**values: Any) -> SimpleNamespace:
        base = {
            "platform_name": "feishu", "chat_id": "oc_1", "transport": None, "in_channel_surface": False,
            "thread_id": None, "where": "feishu:oc_1", "mirror_text": "m", "origin_user_id": "u",
            "mirror_this_target": True,
        }  # fmt: skip
        return SimpleNamespace(**{**base, **values})

    JOB: ClassVar[dict[str, str]] = {"name": "daily", "next_run_at": "2026-06-10T14:30:00+08:00", "id": "job-1"}

    def test_verified_receipt_mirrors_and_skips_native(self) -> None:
        with patch(f"{BRIDGE}.on_cron_deliver", return_value={"message_id": "om_1"}) as hook:
            unverified, mirrored = self.runner()(
                self.JOB, [self.target()], " body ", object(), media_files=[("/a", False)]
            )
        assert unverified == [] and len(mirrored) == 1
        assert hook.call_args.kwargs == {
            "chat_id": "oc_1", "content": "body", "loop": hook.call_args.kwargs["loop"], "task_name": "daily",
            "run_time": "2026-06-10T14:30:00+08:00", "job_id": "job-1", "media_files": [("/a", False)],
        }  # fmt: skip

    def test_receipt_without_message_id_is_unverified(self) -> None:
        with patch(f"{BRIDGE}.on_cron_deliver", return_value=True):
            unverified, mirrored = self.runner()(self.JOB, [self.target()], "x", None)
        assert unverified == ["feishu:oc_1"] and len(mirrored) == 1

    def test_unknown_outcome_is_unverified_and_not_mirrored(self) -> None:
        with patch(f"{BRIDGE}.on_cron_deliver", return_value={"delivery_outcome": "unknown"}):
            unverified, mirrored = self.runner()(self.JOB, [self.target()], "x", None)
        assert unverified == ["feishu:oc_1"] and mirrored == []

    def test_failed_card_falls_back_to_native(self) -> None:
        with patch(f"{BRIDGE}.on_cron_deliver", return_value=False):
            unverified, _ = self.runner()(self.JOB, [self.target()], "x", None)
        assert unverified == [("native", "feishu:oc_1")]

    @pytest.mark.parametrize(
        "override",
        [
            {"platform_name": "telegram"},
            {"in_channel_surface": True},
            {"thread_id": "t"},
            {"transport": SimpleNamespace(is_relay=True)},
        ],
    )
    def test_other_targets_are_untouched(self, override: dict[str, Any]) -> None:
        with patch(f"{BRIDGE}.on_cron_deliver") as hook:
            unverified, _ = self.runner()(self.JOB, [self.target(**override)], "x", None)
        hook.assert_not_called()
        assert unverified == [("native", "feishu:oc_1")]

    def test_missing_media_files_scope_defaults_to_empty(self) -> None:
        with patch(f"{BRIDGE}.on_cron_deliver", return_value={"message_id": "m"}) as hook:
            self.runner(media_in_scope=False)(self.JOB, [self.target()], "x", None)
        assert hook.call_args.kwargs["media_files"] == []
