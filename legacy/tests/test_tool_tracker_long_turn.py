"""Long turns retain identity/counts without retaining every full output."""

import pytest

from hermes_lark_streaming.streaming.tooluse import ToolUseTracker


def test_start_and_duration_survive_the_old_128_step_boundary(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("hermes_lark_streaming.streaming.tooluse.time.monotonic", lambda: now[0])
    tracker = ToolUseTracker()
    for i in range(160):
        tracker.record_start("command", f"echo step-{i}")
        assert tracker.build_display_steps()[-1]["status"] == "running"
        now[0] += 0.25
        tracker.record_end("command", output=f"result-{i}")
    steps = tracker.build_display_steps()
    assert len(steps) == 160
    assert all(s["elapsed_ms"] == 250 for s in steps)
    assert steps[-1]["detail"] == "echo step-159"
    assert all(s["status"] == "success" for s in steps)


@pytest.mark.parametrize("failed", [False, True])
def test_old_completed_payload_is_trimmed_not_the_step(failed):
    tracker = ToolUseTracker(max_steps=2)
    tracker.record_start("command", "first " + "界" * 4000)
    tracker.record_end("command", **({"error": "cause " + "界" * 8000} if failed else {"output": "x" * 40000}))
    for i in range(2):
        tracker.record_start("command", f"recent-{i}")
        tracker.record_end("command", output="latest output")
    steps = tracker.build_display_steps()
    assert len(steps) == 3
    old = steps[0]
    assert old["status"] == ("error" if failed else "success")
    assert len(old["detail"].encode()) <= 512
    assert len(old["error"].encode()) <= 512
    assert old["output"] == ""
    assert old["result_block"] is None and old["error_block"] is None
    if failed:
        assert old["error"].startswith("cause ")
    assert steps[-1]["result_block"]["content"] == "latest output"


def test_active_step_is_never_evicted_and_indices_stay_stable():
    tracker = ToolUseTracker(max_steps=1)
    tracker.record_start("long_job", "running detail " * 200)
    for i in range(4):
        tracker.record_start("command", f"short-{i}")
        tracker.record_end("command", output="done")
    before = tracker.build_display_steps()
    assert len(before) == 5
    assert before[0]["status"] == "running" and len(before[0]["detail"]) > 512
    tracker.record_end("long_job", error="late failure")
    after = tracker.build_display_steps()
    assert [s["name"] for s in before] == [s["name"] for s in after]
    assert after[0]["error"] == "late failure"
    assert after[0]["elapsed_ms"] is not None


def test_unmatched_completions_also_share_detail_retention_budget():
    tracker = ToolUseTracker(max_steps=1)
    tracker.record_start("start")
    for i in range(3):
        tracker.record_end(f"orphan-{i}", output="x" * 10000)
    steps = tracker.build_display_steps()
    assert len(steps) == 4
    assert all(s["elapsed_ms"] is None for s in steps[1:])
    assert all(s["output"] == "" for s in steps[1:-1])
    assert len(steps[-1]["output"]) == 10000


def test_zero_detail_budget_keeps_status_counts():
    tracker = ToolUseTracker(max_steps=0)
    tracker.record_start("command", "echo READY")
    tracker.record_end("command", output="READY")
    steps = tracker.build_display_steps()
    assert len(steps) == 1 and steps[0]["status"] == "success"
    assert steps[0]["detail"] == "echo READY" and steps[0]["output"] == ""


def test_archived_secret_is_redacted_before_utf8_truncation():
    tracker = ToolUseTracker(max_steps=0)
    tracker.record_start("command", 'echo TOKEN="' + "synthetic-secret-" * 100 + '"')
    tracker.record_end("command", error='Authorization: Bearer ' + "synthetic-secret-" * 100)
    step = tracker.build_display_steps()[0]
    assert "synthetic-secret" not in str(step)
    assert "redacted" in step["detail"] and "redacted" in step["error"]


def test_count_is_available_without_rendering(monkeypatch):
    tracker = ToolUseTracker(max_steps=1)
    assert tracker.step_count == 0
    monkeypatch.setattr(tracker, "build_display_steps", lambda: pytest.fail("unneeded rendering"))
    for i in range(200):
        tracker.record_start("command", str(i))
        tracker.record_end("command", output="ok")
    assert tracker.step_count == 200
