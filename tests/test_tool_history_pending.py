"""Compact summaries keep unresolved work and readable native V1 rows."""

import json
from copy import deepcopy

import pytest

from hermes_lark_streaming.cardkit.reference import build_tools
from hermes_lark_streaming.history import compact_tool_steps
from tests.test_reference_layout import step


@pytest.mark.parametrize("with_error", [False, True])
def test_compaction_preserves_old_pending_steps_in_order(with_error):
    steps = [step(), step(), step(), step()]
    steps[0].update(name="unfinished", status="running", elapsed_ms=None, detail="long command")
    steps[1].update(name="second", status="error" if with_error else "success", error="failed" if with_error else "")
    saved = deepcopy(steps)
    result, summarized = compact_tool_steps(steps, compact_after=3, keep_recent=1)
    assert summarized == 3
    assert result[1] == steps[0]
    assert "1 pending" in result[0]["detail"]
    assert result[0]["status"] == ("error" if with_error else "running")
    if with_error:
        assert result[2] == steps[1]
    assert result[-1] == steps[-1]
    assert steps == saved


@pytest.mark.parametrize("status", ["success", "running"])
def test_native_tool_row_has_inline_sanitized_command_hint(status):
    item = step(name="command", status=status, detail="printf READY\nnext command")
    saved = deepcopy(item)
    panel = build_tools({}, {"steps":[item]})
    row = next(e for e in panel["elements"] if e["tag"] == "column_set")
    title = row["columns"][1]["elements"][0]["content"]
    assert "printf READY next command" in title
    assert "<font color='grey'>" in title
    assert "\n" not in title
    assert item == saved


def test_error_row_keeps_cause_priority_over_optional_command_hint():
    item = step(name="command", status="error", detail="DETAIL-ONLY-IN-EXCERPT", error="ACTIONABLE-CAUSE")
    panel = build_tools({}, {"steps":[item]})
    row = next(e for e in panel["elements"] if e["tag"] == "column_set")
    title = row["columns"][1]["elements"][0]["content"]
    assert "ACTIONABLE-CAUSE" in title
    assert "DETAIL-ONLY-IN-EXCERPT" not in title
    assert "DETAIL-ONLY-IN-EXCERPT" in json.dumps(panel)


def test_inline_hint_redacts_and_escapes_without_new_elements():
    item = step(name="command", detail="TOKEN=synthetic-private-value <img> `text`")
    panel = build_tools({}, {"steps":[item]})
    row = next(e for e in panel["elements"] if e["tag"] == "column_set")
    title = row["columns"][1]["elements"][0]["content"]
    assert "redacted" in title.lower()
    assert "synthetic-private-value" not in json.dumps(panel)
    assert "<img>" not in title and "&lt;img&gt;" in title
    assert len(row["columns"]) == 4
    assert len(row["columns"][1]["elements"]) == 1


def test_empty_hint_keeps_existing_title_and_long_hint_is_bounded():
    blank = build_tools({}, {"steps":[step(detail="")]})
    row = next(e for e in blank["elements"] if e["tag"] == "column_set")
    assert " · " not in row["columns"][1]["elements"][0]["content"]
    long = build_tools({}, {"steps":[step(detail="测试界面" * 1000)]})
    row = next(e for e in long["elements"] if e["tag"] == "column_set")
    title = row["columns"][1]["elements"][0]["content"]
    assert "测试" in title and "…" in title
    assert len(title.encode()) < 200


def test_inline_hints_share_existing_panel_budget_with_localized_wrappers():
    steps = [step("command", "error", "<" * 400, error="失" * 1000) for _ in range(150)]
    panel = build_tools({}, {"steps":steps})
    assert len(json.dumps(panel, ensure_ascii=False, separators=(",", ":")).encode()) <= 13000
    assert len([e for e in panel["elements"] if e["tag"] == "column_set"]) == 8
    assert "150/150" in panel["header"]["title"]["content"]
    assert "150 ·" in json.dumps(panel["elements"][-1], ensure_ascii=False)
