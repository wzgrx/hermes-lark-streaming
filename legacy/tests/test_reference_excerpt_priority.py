"""The bounded excerpt must agree with the visible priority rows."""

import json

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import build_tools
from tests.test_reference_readability import step


@pytest.mark.parametrize("interrupted", [False, True])
def test_long_failure_burst_keeps_active_command_in_bounded_excerpt(interrupted):
    active = step("running")
    active.update(title="ACTIVE-WORK", detail="ACTIVE-COMMAND-TO-INSPECT " + "待" * 180)
    failures = [step("error") for _ in range(12)]
    for i, item in enumerate(failures):
        item.update(title=f"FAIL-{i}" + "错" * 100, detail="command " + "文" * 180,
                    error=f"CAUSE-{i} " + "错" * 200)
    steps = [active, *failures]
    panel = build_tools({}, {"steps": steps}, interrupted=interrupted)
    excerpt = next(e for e in panel["elements"] if e.get("element_id") == "ref_tool_records")
    text = json.dumps(excerpt, ensure_ascii=False)
    assert "ACTIVE-COMMAND-TO-INSPECT" in text
    assert "CAUSE-11" in text
    assert text.index("ACTIVE-COMMAND-TO-INSPECT") < text.index("CAUSE-11")
    assert len(json.dumps(panel, ensure_ascii=False, separators=(",", ":")).encode()) < 14000
    card = build_streaming_card_v2(footer_data={
        "presentation":"reference", "reference":{"show_tools":True,"steps":steps}})
    assert inspect_card(card).safe


@pytest.mark.parametrize("detail", ["", " ", "\n\t"])
def test_poll_groups_require_an_observed_nonblank_target_detail(detail):
    steps = [step(), step()]
    for item in steps:
        item.update(name="process", title="Process", detail=detail)
    panel=build_tools({}, {"steps":steps})
    rows=[e for e in panel["elements"] if e["tag"]=="column_set"]
    assert len(rows)==2
    assert "2/2 ended" in panel["header"]["title"]["content"]


def test_known_adjacent_poll_target_still_merges_without_output():
    steps=[step(),step()]
    for item in steps:
        item.update(name="process", title="Process", detail="poll task-alpha")
    panel=build_tools({}, {"steps":steps})
    rows=[e for e in panel["elements"] if e["tag"]=="column_set"]
    assert len(rows)==1
    assert "2 calls" in json.dumps(rows)
