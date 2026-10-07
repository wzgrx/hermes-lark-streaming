"""The single declarative table of Hermes injection points.

Anchors are tried in order; the first locator with exactly one hit wins, more than one hit fails closed.
Marker names (``# HERMES_LARK_<NAME>_BEGIN/END``) are unchanged from 0.x so old installs are removable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import snippets
from .locators import AfterAssign, AfterStopCommand, Before, FuncBody, Locator

MARKER_PREFIX = "HERMES_LARK"

GATEWAY_INBOUND = "gateway/run_inbound.py"
GATEWAY_TURN = "gateway/run_turn.py"
GATEWAY_RUNNER = "gateway/run_turn_runner.py"
GATEWAY_BUSY = "gateway/run_busy.py"
CRON_DELIVERY = "cron/scheduler_delivery.py"
LAYOUT_PROBE = GATEWAY_RUNNER


@dataclass(frozen=True)
class Injection:
    name: str
    file: str
    anchors: tuple[Locator, ...]
    snippet: Callable[[], list[str]]
    bridge: tuple[str, ...]
    optional: bool = False

    @property
    def begin(self) -> str:
        return f"# {MARKER_PREFIX}_{self.name}_BEGIN"

    @property
    def end(self) -> str:
        return f"# {MARKER_PREFIX}_{self.name}_END"


INJECTIONS: tuple[Injection, ...] = (
    Injection(
        "NORMALIZE",
        GATEWAY_INBOUND,
        (Before("_paused_notice = self._hm_estop_gate("),),
        snippets.normalize,
        ("on_feishu_normalize",),
    ),
    Injection(
        "START",
        GATEWAY_TURN,
        (FuncBody("_handle_message_with_agent"),),
        snippets.start,
        ("on_message_started",),
    ),
    Injection(
        "COMPLETE",
        GATEWAY_TURN,
        (Before("return await self._hmwa_deliver_turn_response("),),
        snippets.complete,
        ("on_message_completed_wait", "on_message_needs_text_fallback"),
    ),
    Injection(
        "ABORT",
        GATEWAY_TURN,
        (Before("self._hmwa_discard_stale_result(source,"),),
        snippets.abort,
        ("on_message_aborted",),
    ),
    Injection("STOP", GATEWAY_BUSY, (AfterStopCommand(),), snippets.stop, ("on_session_aborted",)),
    Injection("TOOL", GATEWAY_RUNNER, (FuncBody("progress_callback"),), snippets.tool, ("on_tool_updated",)),
    Injection("ANSWER", GATEWAY_RUNNER, (FuncBody("stream_delta_cb"),), snippets.answer, ("on_answer_delta",)),
    Injection(
        "THINKING",
        GATEWAY_RUNNER,
        (FuncBody("interim_assistant_cb"),),
        snippets.thinking,
        ("on_thinking_delta",),
    ),
    Injection(
        "REASONING",
        GATEWAY_RUNNER,
        (AfterAssign("agent.reasoning_config"),),
        snippets.reasoning,
        ("on_reasoning_delta",),
    ),
    Injection(
        "BACKGROUND_REVIEW",
        GATEWAY_RUNNER,
        (AfterAssign("agent.background_review_callback"),),
        snippets.background_review,
        ("on_background_review_message",),
    ),
    Injection(
        "CLARIFY",
        GATEWAY_RUNNER,
        (AfterAssign("agent.clarify_callback"),),
        snippets.clarify,
        ("on_clarify_enter", "on_clarify_exit"),
    ),
    Injection(
        "APPROVAL",
        GATEWAY_RUNNER,
        (FuncBody("_approval_notify_sync"),),
        snippets.approval,
        ("on_approval_enter",),
    ),
    Injection(
        "FOLLOWUP_COMPLETE",
        GATEWAY_TURN,
        (Before('if not result.get("interrupted"):'),),
        snippets.followup_complete,
        ("on_queued_followup_boundary",),
    ),
    Injection(
        "INTERRUPT",
        GATEWAY_TURN,
        (Before("# Restart the typing indicator;"),),
        snippets.interrupt,
        ("on_message_interrupted", "on_message_aborted", "on_message_started"),
    ),
    Injection(
        "FOLLOWUP_RESULT",
        GATEWAY_TURN,
        (Before("_preserve_queued_followup_history_offset(result, followup_result)"),),
        snippets.followup_result,
        ("on_queued_followup_result",),
    ),
    Injection(
        "BG_DELIVER",
        GATEWAY_TURN,
        (AfterAssign("(images, text_content)"),),
        snippets.bg_deliver,
        ("on_background_deliver",),
    ),
    Injection(
        "CRON_DELIVER",
        CRON_DELIVERY,
        (Before("target_errors: list = []"),),
        snippets.cron_deliver,
        ("on_cron_deliver",),
        optional=True,
    ),
)

INJECTION_NAMES = tuple(i.name for i in INJECTIONS)
