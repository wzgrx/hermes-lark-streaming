"""Turns Hermes starts by itself in a chat (no user message): which ones get a card, and what they say first."""

from __future__ import annotations

import re

_BATCH = re.compile(r"\[ASYNC DELEGATION BATCH COMPLETE\W+(deleg_[A-Za-z0-9_]{1,40})")
_PROCESS = re.compile(r"\[IMPORTANT: Background process (proc_[A-Za-z0-9_]{1,40})")


def synthetic_note(text: str) -> tuple[str, str] | None:
    """``(label, detail)`` for a known Hermes-generated turn, None for anything else (left to native delivery).

    Hermes injects these as inbound messages without a message id: the summary turn after a batch of background
    subagents finishes, and the follow-up when a watched background process reports.
    """
    head = (text or "").lstrip()[:200]
    if head.startswith("[ASYNC DELEGATION BATCH COMPLETE"):
        return "🔀 后台子代理已完成", "汇总结果如下"
    match = _PROCESS.match(head)
    if match:
        return "🔔 后台进程通知", match.group(1)
    return None


def delegation_batch(text: str) -> str | None:
    """The delegate_task batch id a subagent summary turn reports on (``deleg_…``)."""
    match = _BATCH.match((text or "").lstrip()[:200])
    return match.group(1) if match else None
