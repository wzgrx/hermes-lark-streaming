"""Subagents of one turn, from the progress events Hermes relays to the parent's tool callback.

``delegate_task`` reports each child as ``subagent.start`` / ``subagent.tool`` / ``subagent.progress`` /
``subagent.complete`` with identity fields (``subagent_id``, ``task_index``, ``task_count``, ``goal``,
``delegation_id``). One delegate_task call is one batch; a batch is one panel in the card's timeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..card.model import AgentStatus, Subagent
from ..card.redact import redact

SUBAGENT_EVENTS = frozenset({
    "subagent.start", "subagent.tool", "subagent.progress", "subagent.complete", "subagent.thinking", "subagent.text",
})
_FAILED = frozenset({"failed", "error", "timeout", "timed_out", "interrupted", "cancelled", "canceled", "stale"})
_RECENT = 3
MAX_AGENTS = 12  # per turn; a runaway fan-out must not blow the card's element budget


def _text(value: Any, limit: int) -> str:
    return " ".join(redact(value).split())[:limit] if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass
class AgentRecord:
    key: str
    batch: str
    index: int
    goal: str = ""
    status: AgentStatus = AgentStatus.RUNNING
    tools: int = 0
    recent: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    elapsed_s: float | None = None
    summary: str = ""

    def view(self, now: float) -> Subagent:
        elapsed = self.elapsed_s if self.elapsed_s is not None else now - self.started
        return Subagent(self.index, self.goal, self.status, self.tools, tuple(self.recent), elapsed, self.summary)


class AgentBook:
    """All subagents of a turn, grouped by batch; not thread-safe (the session lock guards it)."""

    def __init__(self) -> None:
        self.records: dict[str, AgentRecord] = {}

    @property
    def running(self) -> bool:
        return any(r.status is AgentStatus.RUNNING for r in self.records.values())

    def batches(self) -> list[str]:
        return list(dict.fromkeys(r.batch for r in self.records.values()))

    def views(self, batch: str) -> tuple[Subagent, ...]:
        now = time.monotonic()
        rows = sorted((r for r in self.records.values() if r.batch == batch), key=lambda r: r.index)
        return tuple(r.view(now) for r in rows)

    def apply(self, event_type: str, tool_name: str, preview: str, details: dict[str, Any]) -> str | None:
        """Record one relayed event; returns the batch it belongs to, or None when it carries no identity."""
        ident = details.get("subagent_id")
        index = _int(details.get("task_index"))
        batch = _text(details.get("delegation_id"), 64) or "batch"
        if not isinstance(ident, str) or not ident:
            if index is None:
                return None  # e.g. a nested grandchild's pre-batched summary: nothing to attach it to
            ident = f"{batch}:{index}"
        record = self.records.get(ident)
        if record is None:
            if len(self.records) >= MAX_AGENTS:
                return None
            record = self.records[ident] = AgentRecord(ident, batch, (index or 0) + 1)
        if not record.goal:
            fallback = _text(preview, 80) if event_type == "subagent.start" else ""
            record.goal = _text(details.get("goal"), 80) or fallback
        tools = _int(details.get("tool_count"))
        if tools is not None:
            record.tools = max(record.tools, tools)
        if event_type == "subagent.tool" and tool_name:
            record.recent = [*record.recent, _text(tool_name, 32)][-_RECENT:]
        elif event_type == "subagent.complete":
            status = str(details.get("status") or "").lower()
            record.status = AgentStatus.FAILED if status in _FAILED else AgentStatus.OK
            seconds = details.get("duration_seconds")
            if isinstance(seconds, int | float) and not isinstance(seconds, bool) and seconds >= 0:
                record.elapsed_s = float(seconds)
            else:
                record.elapsed_s = time.monotonic() - record.started
            reason = details.get("failure_reason") if record.status is AgentStatus.FAILED else None
            record.summary = _text(reason or details.get("summary") or preview, 160)
        return record.batch
