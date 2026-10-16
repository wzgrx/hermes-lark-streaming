"""Turns Hermes starts by itself in a chat (no user message): which ones get a card, and what they say first."""

from __future__ import annotations

import re

# Hermes' completion texts (tools/process_registry_notifications.py, gateway/run_notifications.py)
_DELEGATION = re.compile(r"\[ASYNC DELEGATION (BATCH COMPLETE|COMPLETE|TASK FAILED)\W+(deleg_[A-Za-z0-9_]{1,40})")
_GROUPED = re.compile(r"\[IMPORTANT: (\d+) background subagent delegations completed")
_ANY_BATCH = re.compile(r"deleg_[A-Za-z0-9_]{1,40}")
_PROCESS = re.compile(r"\[IMPORTANT: Background process (proc_[A-Za-z0-9_]{1,40})")


def synthetic_note(text: str) -> tuple[str, str] | None:
    """``(label, detail)`` for a known Hermes-generated turn, None for anything else (left to native delivery).

    Hermes injects these as inbound messages without a real message: the summary turn after background
    subagents finish (one batch, one task, a failed task, or several batches grouped into one turn), and
    the follow-up when a watched background process reports.
    """
    head = (text or "").lstrip()[:200]
    match = _DELEGATION.match(head)
    if match:
        if match.group(1) == "TASK FAILED":
            return "⚠️ 后台子代理任务失败", "失败详情如下"
        return "🔀 后台子代理已完成", "汇总结果如下"
    grouped = _GROUPED.match(head)
    if grouped:
        return "🔀 后台子代理已完成", f"{grouped.group(1)} 批结果合并汇总"
    match = _PROCESS.match(head)
    if match:
        return "🔔 后台进程通知", match.group(1)
    return None


def delegation_batch(text: str) -> str | None:
    """The delegate_task batch a subagent summary turn reports on (``deleg_…``); the first one when Hermes
    grouped several batches into the turn."""
    head = (text or "").lstrip()
    match = _DELEGATION.match(head[:200])
    if match:
        return match.group(2)
    if _GROUPED.match(head[:200]):
        found = _ANY_BATCH.search(head)
        return found.group(0) if found else None
    return None
