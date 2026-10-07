from __future__ import annotations

import json

from hermes_lark_streaming.card.model import StepStatus
from hermes_lark_streaming.session.tools import ToolTracker, completion, summarize


def test_start_finish_records_timing_and_status():
    t = ToolTracker()
    t.start("terminal", "git status")
    assert t.running == 1
    assert t.finish("terminal", "completed", "") is False
    (step,) = t.steps()
    assert step.name == "Terminal" and step.status is StepStatus.OK and step.elapsed_ms is not None
    assert t.running == 0 and t.count == 1


def test_command_exit_code_marks_failure_with_excerpt():
    t = ToolTracker()
    t.start("terminal", "exit 7")
    failed = t.finish("terminal", "completed", "", result=json.dumps({"exit_code": 7, "output": "boom"}))
    (step,) = t.steps()
    assert failed and step.status is StepStatus.FAILED
    assert step.error.startswith("Exit code 7") and "boom" in step.error


def test_explicit_error_flag_and_secret_redaction():
    failed, text = completion("read_file", "completed", "token=abc123 missing", is_error=True)
    assert failed and "abc123" not in text
    assert completion("read_file", "completed", "fine", is_error=False) == (False, "")


def test_completion_without_start_is_still_counted():
    t = ToolTracker()
    t.finish("web_search", "completed", "q")
    assert t.count == 1 and t.steps()[0].status is StepStatus.OK


def test_summaries_sanitize_paths_and_commands():
    assert summarize("read_file", "from /home/u/secret/config.yaml") == "config.yaml"
    assert summarize("terminal", "cat /etc/passwd --token=hunter2") == "cat passwd --token=[redacted]"


def test_unconfirm_marks_running_only():
    t = ToolTracker()
    t.start("a")
    t.start("b")
    t.finish("a")
    t.unconfirm_running()
    assert [s.status for s in t.steps()] == [StepStatus.OK, StepStatus.UNCONFIRMED]


def test_trim_folds_old_ok_steps_but_keeps_failures():
    t = ToolTracker(max_tracked=3)
    t.start("a"); t.finish("a", is_error=True)  # noqa: E702
    for _ in range(5):
        t.start("b"); t.finish("b")  # noqa: E702
    assert t.count == 6 and len(t.steps()) == 3 and t.archived == 3
    assert t.steps()[0].status is StepStatus.FAILED
