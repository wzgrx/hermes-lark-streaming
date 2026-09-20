"""Terminal-card compaction for long reasoning/tool runs."""

from __future__ import annotations

from copy import copy

from .streaming.segments import Segment, SegmentType
from .streaming.tooluse import ToolDisplayStep


def compact_tool_steps(
    steps: list[ToolDisplayStep], *, compact_after: int, keep_recent: int
) -> tuple[list[ToolDisplayStep], int]:
    if len(steps) <= compact_after:
        return steps, 0
    old = steps[:-keep_recent]
    recent = steps[-keep_recent:]
    errors = [step for step in old if step.get("status") == "error"]
    success_count = sum(step.get("status") == "success" for step in old)
    total_ms = sum(float(step.get("elapsed_ms", 0)) for step in old)
    summary: ToolDisplayStep = {
        "name": "history_summary",
        "title": f"Earlier tool history · {len(old)} steps",
        "status": "success" if not errors else "error",
        "detail": f"{success_count} succeeded · {len(errors)} failed · {total_ms / 1000:.1f}s",
        "output": "",
        "error": "",
        "icon": "history_outlined",
        "elapsed_ms": total_ms,
        "result_block": None,
        "error_block": None,
    }
    # Preserve every old error verbatim, then the most recent full-fidelity window.
    return [summary, *errors, *recent], len(old)


def compact_terminal_segments(
    segments: list[Segment],
    steps: list[ToolDisplayStep],
    *,
    compact_after: int,
    keep_recent: int,
) -> tuple[list[Segment], list[ToolDisplayStep], dict[str, int]]:
    compacted_steps, hidden_tools = compact_tool_steps(steps, compact_after=compact_after, keep_recent=keep_recent)
    result = [copy(segment) for segment in segments]
    if hidden_tools:
        tool_indexes = [index for index, segment in enumerate(result) if segment.type == SegmentType.TOOL]
        if tool_indexes:
            first = result[tool_indexes[0]]
            first.tool_offset = 0
            first.tool_end_offset = len(compacted_steps)
            result = [
                segment
                for index, segment in enumerate(result)
                if segment.type != SegmentType.TOOL or index == tool_indexes[0]
            ]

    reasoning_indexes = [index for index, segment in enumerate(result) if segment.type == SegmentType.REASONING]
    hidden_reasoning = max(0, len(reasoning_indexes) - 2)
    if hidden_reasoning:
        first_index = reasoning_indexes[0]
        first = result[first_index]
        first.text = f"Earlier reasoning history compacted · {hidden_reasoning} round(s)."
        first.elapsed_ms = sum(result[index].elapsed_ms for index in reasoning_indexes[:-2])
        drop = set(reasoning_indexes[1:-2])
        result = [segment for index, segment in enumerate(result) if index not in drop]

    return result, compacted_steps, {"tool_steps": hidden_tools, "reasoning_rounds": hidden_reasoning}
