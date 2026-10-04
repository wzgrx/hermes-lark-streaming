"""Compact V1 details retain actionable failures and honest timing/state."""

from __future__ import annotations

import json

import pytest

from hermes_lark_streaming.cardkit.reference import build_reference_footer, build_tools


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
