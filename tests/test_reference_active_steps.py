"""Bounded V1 rows keep live work visible without inventing timing."""

import json
from copy import deepcopy

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import build_tools
from tests.test_reference_readability import step


def rows(panel):
    return [node for node in panel["elements"] if node["tag"] == "column_set"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_old_active_step_survives_newer_failure_burst(interrupted):
    active = step("running")
    active.update(title="ACTIVE-LONG-JOB", detail="WAIT-FOR-ACTIVE-RESULT")
    failures = [step("error") for _ in range(12)]
    for index, item in enumerate(failures):
        item.update(title=f"FAILURE-{index:02}", error=f"Failure {index}")
    steps = [active, *failures]
    before = deepcopy(steps)
    panel = build_tools({}, {"steps": steps}, interrupted=interrupted)
    text = json.dumps(rows(panel))
    assert len(rows(panel)) == 8
    assert "ACTIVE-LONG-JOB" in text
    assert "FAILURE-11" in text and "FAILURE-00" not in text
    assert text.index("ACTIVE-LONG-JOB") < text.index("FAILURE-11")
    title = panel["header"]["title"]["content"]
    assert "12/13 ended" in title and "12 failed" in title
    assert ("1 unconfirmed" if interrupted else "1 running") in title
    assert ("Unconfirmed" if interrupted else "Running") in text
    assert steps == before


def test_many_live_steps_are_bounded_and_explain_total_pending_count():
    steps = [step("running") for _ in range(30)]
    panel = build_tools({}, {"steps": steps})
    assert len(rows(panel)) == 8
    assert "30 running" in panel["header"]["title"]["content"]
    assert "22 groups omitted" in json.dumps(panel)
    card = build_streaming_card_v2(
        footer_data={"presentation": "reference", "reference": {"show_tools": True, "steps": steps}}
    )
    assert inspect_card(card).safe


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf")])
def test_invalid_finished_timing_is_unknown_not_zero(value):
    item = step()
    item["elapsed_ms"] = value
    panel = build_tools({}, {"steps": [item]})
    assert rows(panel)[0]["columns"][-1]["elements"][0]["content"] == "—"


def test_missing_poll_timing_does_not_become_an_exact_group_total():
    a, b = step(), step()
    for item in (a, b):
        item.update(name="process", title="Process", detail="poll same job")
    a.pop("elapsed_ms")
    b["elapsed_ms"] = 1000
    panel = build_tools({}, {"steps": [a, b]})
    assert len(rows(panel)) == 1
    assert rows(panel)[0]["columns"][-1]["elements"][0]["content"] == "—"


def test_observed_zero_timing_still_displays_zero():
    item = step()
    item["elapsed_ms"] = 0
    assert "0ms" in rows(build_tools({}, {"steps": [item]}))[0]["columns"][-1]["elements"][0]["content"]
