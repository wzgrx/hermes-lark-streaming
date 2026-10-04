"""Compact V1 details retain actionable failures and honest timing/state."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card, build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import build_reference_footer, build_tools
from hermes_lark_streaming.streaming.segments import SegmentState


def step(status="success", **extra):
    return dict(name="command", title="Command", status=status, detail="check", elapsed_ms=68,
                error="", output="", error_block=None, result_block=None, **extra)


def test_old_failure_excerpt_survives_many_later_successes_within_budget():
    failed = step("error")
    failed.update(detail="EARLY-FAILURE-COMMAND", error="ACTIONABLE-ROOT-CAUSE")
    steps = [failed] + [step() for _ in range(90)]
    panel = build_tools({}, {"steps": steps})
    excerpts = next(el for el in panel["elements"] if el.get("element_id") == "ref_tool_records")
    text = json.dumps(excerpts, ensure_ascii=False)
    assert "EARLY-FAILURE-COMMAND" in text and "ACTIONABLE-ROOT-CAUSE" in text
    assert len(json.dumps(panel, ensure_ascii=False, separators=(",", ":")).encode()) < 14000
    assert "latest" not in excerpts["header"]["title"]["content"]


@pytest.mark.parametrize("elapsed, expected", [(15, "15ms"), (68, "68ms"), (999, "999ms"), (0, "0ms")])
def test_subsecond_tool_timing_is_not_rounded_to_zero(elapsed, expected):
    item = step()
    item["elapsed_ms"] = elapsed
    panel = build_tools({}, {"steps": [item]})
    row = next(el for el in panel["elements"] if el["tag"] == "column_set")
    assert expected in row["columns"][-1]["elements"][0]["content"]


@pytest.mark.parametrize("flag, expected", [("is_error", "Failed"), ("is_aborted", "Stopped")])
def test_collapsed_footer_exposes_terminal_failure_or_stop(flag, expected):
    panel = build_reference_footer({"model": "example-model"}, **{flag: True})[0]
    assert expected in panel["header"]["title"]["content"]
    assert "example-model" in panel["header"]["title"]["content"]
    assert panel["expanded"] is False


def test_blank_structured_error_does_not_hide_useful_plain_error():
    item = step("error")
    item.update(error_block={"content": ""}, error="VISIBLE-CAUSE")
    panel = build_tools({}, {"steps": [item]})
    row = next(el for el in panel["elements"] if el["tag"] == "column_set")
    assert "VISIBLE-CAUSE" in json.dumps(row)


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"input_tokens": 0, "cache_read_tokens": 0}, "0 / —"),
        ({"input_tokens": 100, "cache_read_tokens": 0}, "0 / 0.0%"),
        ({"cache_read_tokens": 40}, "40 / —"),
        ({"input_tokens": 100, "cache_read_tokens": 40, "usage_partial": True}, "40 / —"),
        ({"input_tokens": 100, "cache_read_tokens": 40}, "40 / 40.0%"),
    ],
)
def test_known_cache_count_is_not_erased_when_hit_rate_is_unknown(data, expected):
    panel = build_reference_footer(data)[0]
    row = next(el for el in panel["elements"] if el.get("tag") == "column_set"
               and "Cache read" in json.dumps(el))
    assert row["columns"][0]["elements"][1]["content"] == f"**{expected}**"
    if data.get("usage_partial"):
        assert "Partial" in panel["header"]["title"]["content"]


@pytest.mark.parametrize("data", [{}, {"input_tokens": 10, "cache_read_tokens": 11},
                                 {"input_tokens": 0, "cache_read_tokens": 1}])
def test_missing_or_inconsistent_cache_is_still_unknown(data):
    panel = build_reference_footer(data)[0]
    row = next(el for el in panel["elements"] if el.get("tag") == "column_set"
               and "Cache read" in json.dumps(el))
    assert row["columns"][0]["elements"][1]["content"] == "**Not reported**"


@pytest.mark.parametrize("flag", ["is_error", "is_aborted"])
@pytest.mark.parametrize("footer_enabled", [True, False])
def test_terminal_reference_does_not_claim_unfinished_tools_are_still_running(flag, footer_enabled):
    unfinished = step("running")
    unfinished["elapsed_ms"] = 0
    data = {"presentation": "reference", "reference": {
        "show_tools": True, "steps": [step(), unfinished], "failed_total": 0, "succeeded_total": 1,
    }}
    before = deepcopy(data)
    card = build_complete_card(segments=[], all_tool_steps=data["reference"]["steps"], footer_data=data,
                               footer_mode="enhanced", footer_enabled=footer_enabled, **{flag: True})
    text = json.dumps(card, ensure_ascii=False)
    assert "Running" not in text and "运行中" not in text
    assert "结果未确认" in text and "Unconfirmed" in text
    assert "1/2 ended" in text  # no fabricated completion or failure
    panel = card["body"]["elements"][0]
    assert "1 unconfirmed" in panel["header"]["title"]["content"]
    rows = [el for el in panel["elements"] if el["tag"] == "column_set"]
    assert rows[-1]["columns"][-1]["elements"][0]["content"] == "—"
    assert "not confirm that a background process stopped" in text
    assert data == before  # presentation does not rewrite tracker observations
    assert inspect_card(card).safe


@pytest.mark.parametrize("flag, expected", [("is_error", "Error"), ("is_aborted", "Stopped")])
@pytest.mark.parametrize("presentation", ["reference", "classic"])
def test_empty_failed_or_stopped_card_never_displays_done_even_without_footer(flag, expected, presentation):
    card = build_complete_card(segments=[], all_tool_steps=[], footer_enabled=False,
                               footer_data={"presentation": presentation}, **{flag: True})
    text = json.dumps(card, ensure_ascii=False)
    assert "Done." not in text
    assert expected in card["body"]["elements"][-1]["content"]
    assert "zh_cn" in card["body"]["elements"][-1]["i18n_content"]
    assert expected in card["config"]["summary"]["content"]


@pytest.mark.parametrize("flag", ["is_error", "is_aborted"])
def test_terminal_card_keeps_received_answer_and_notification_preview(flag):
    state = SegmentState()
    state.on_answer_delta("PARTIAL_ANSWER_PRESERVED")
    card = build_complete_card(segments=state.segments, all_tool_steps=[], **{flag: True})
    assert card["body"]["elements"][0]["content"] == "PARTIAL_ANSWER_PRESERVED"
    assert card["config"]["summary"]["content"] == "PARTIAL_ANSWER_PRESERVED"


def test_live_and_sealed_continuation_keep_genuine_running_tool_status():
    data = {"presentation": "reference", "reference": {"show_tools": True, "steps": [step("running")]}}
    live = build_streaming_card_v2(footer_data=data)
    sealed = build_complete_card(segments=[], all_tool_steps=data["reference"]["steps"],
                                 footer_data=data, footer_enabled=False)
    for card in (live, sealed):
        panel = card["body"]["elements"][0]
        assert "运行中" in json.dumps(panel, ensure_ascii=False)
        assert "Unconfirmed" not in json.dumps(panel)
