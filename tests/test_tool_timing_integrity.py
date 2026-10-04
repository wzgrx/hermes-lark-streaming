"""Observed tool durations survive collection, compaction and V1 rendering."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from hermes_lark_streaming.cardkit.reference import build_tools
from hermes_lark_streaming.history import compact_tool_steps
from hermes_lark_streaming.streaming.tooluse import ToolUseTracker
from tests.test_reference_readability import step


@pytest.mark.parametrize("error", ["", "late failure"])
def test_result_without_observed_start_has_unknown_duration(error):
    tracker = ToolUseTracker()
    tracker.record_start("command", "first command")
    tracker.record_end("command")
    tracker.record_end("process", error=error)
    steps = tracker.build_display_steps()
    assert steps[-1]["elapsed_ms"] is None
    assert steps[-1]["status"] == ("error" if error else "success")
    panel = build_tools({}, {"steps":steps})
    rows = [row for row in panel["elements"] if row["tag"] == "column_set"]
    assert rows[-1]["columns"][-1]["elements"][0]["content"] == "—"


@pytest.mark.parametrize("wall_end", [1.0, 90000.0])
def test_tool_duration_uses_monotonic_clock_despite_wall_clock_jump(wall_end):
    with patch("hermes_lark_streaming.streaming.tooluse.time") as clock:
        clock.time.side_effect = [100.0, 100.0, wall_end, wall_end]
        clock.monotonic.side_effect = [100.0, 100.0, 102.5, 103.0]
        tracker = ToolUseTracker()
        tracker.record_start("command", "work")
        tracker.record_end("command")
        assert tracker.build_display_steps()[0]["elapsed_ms"] == 2500.0
        assert tracker.elapsed_ms == 3000.0


@pytest.mark.parametrize("missing", [None, -1.0, float("nan"), float("inf"), "bad"])
def test_history_does_not_crash_or_invent_total_for_unknown_timing(missing):
    steps = [step(), step(), step()]
    steps[0]["elapsed_ms"] = missing
    saved = deepcopy(steps)
    compacted, hidden = compact_tool_steps(steps, compact_after=2, keep_recent=1)
    assert hidden == 2
    assert compacted[0]["elapsed_ms"] is None
    assert "duration unknown" in compacted[0]["detail"]
    assert compacted[-1] == steps[-1]
    assert steps[1:] == saved[1:]
    assert steps[0]["elapsed_ms"] is missing


def test_measured_zero_and_complete_history_sum_remain_known():
    with patch("hermes_lark_streaming.streaming.tooluse.time") as clock:
        clock.time.return_value = clock.monotonic.return_value = 100.0
        tracker = ToolUseTracker()
        tracker.record_start("command", "immediate")
        tracker.record_end("command")
    assert tracker.build_display_steps()[0]["elapsed_ms"] == 0.0
    steps = [step(), step(), step()]
    steps[0]["elapsed_ms"], steps[1]["elapsed_ms"] = 0.0, 250.0
    compacted, _ = compact_tool_steps(steps, compact_after=2, keep_recent=1)
    assert compacted[0]["elapsed_ms"] == 250.0
