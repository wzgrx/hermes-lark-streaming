"""V1 history is observed main-request usage, not a rounded-up lower bound."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from hermes_lark_streaming.cardkit.reference import build_reference_footer
from hermes_lark_streaming.footer.history import UsageLedger
from hermes_lark_streaming.footer.history_summary import read_summary


def history(tokens, *, partial=True):
    return {
        "status": "ok", "timezone": "UTC", "since": "2026-10-01",
        **{key: {"tokens": tokens, "partial": partial} for key in ("today", "month", "total")},
        "show_models": True,
        "models": [{"subscription": "fixture", "model": "fixture-model", "tokens": tokens, "partial": partial}],
    }


def rendered(snapshot):
    return build_reference_footer({"reference": {"history": snapshot}})[0]


def values(panel):
    summary = next(e["content"] for e in panel["elements"] if e.get("content", "").startswith("◷ History"))
    group = next(e for e in panel["elements"] if e.get("background_style") == "grey-50")
    token = group["columns"][0]["elements"][1]["columns"][2]["elements"][0]["content"]
    return summary, token


@pytest.mark.parametrize("tokens, expected", [
    (0, "≥0*"), (999, "≥999*"), (1999, "≥1.9k*"),
    (999999, "≥999.9k*"), (1999999, "≥1.99M*"),
    (199999999, "≥199.99M*"), (10**15, "≥1000000000M*"),
])
def test_partial_period_and_model_counts_share_non_overstated_lower_bound(tokens, expected):
    snapshot = history(tokens)
    before = deepcopy(snapshot)
    summary, model_value = values(rendered(snapshot))
    for name in ("Today", "Month", "Total"):
        assert f"{name} {expected}" in summary
    assert model_value == expected
    assert snapshot == before


@pytest.mark.parametrize("tokens", [None, False, -1])
def test_missing_history_usage_is_not_fabricated_as_zero_or_a_lower_bound(tokens):
    summary, model_value = values(rendered(history(tokens)))
    assert all(f"{name} —*" in summary for name in ("Today", "Month", "Total"))
    assert model_value == "—*" and "≥" not in summary


@pytest.mark.parametrize("tokens, expected", [(0, "0"), (1999, "2k"), (999999, "1000k"), (1000000, "1M")])
def test_complete_counts_keep_the_existing_compact_display(tokens, expected):
    summary, model_value = values(rendered(history(tokens, partial=False)))
    assert f"Total {expected}" in summary and model_value == expected
    assert "≥" not in summary and "*" not in model_value


def test_real_readonly_summary_with_missing_request_keeps_measured_subset(tmp_path):
    path = tmp_path / "usage.db"
    ledger = UsageLedger(path)
    base = {"started_at": 1_700_000_000, "session_id": "fixture-session", "turn_id": "fixture-turn",
            "provider": "fixture", "model": "fixture-model"}
    ledger.record("post_api_request", {**base, "api_request_id": "known",
                                         "usage": {"prompt_tokens": 1990, "output_tokens": 9}})
    ledger.record("post_api_request", {**base, "api_request_id": "missing", "usage": {}})
    snapshot = read_summary(path, "UTC", now=1_700_000_001)
    snapshot["show_models"] = True
    assert snapshot["total"] == {"tokens": 1999, "requests": 2, "partial": True}
    assert snapshot["models"][0]["tokens"] == 1999
    summary, model_value = values(rendered(snapshot))
    assert "Total ≥1.9k*" in summary and model_value == "≥1.9k*"
    assert ledger.report()["totals"]["requests"] == 2


def test_history_explains_lower_bounds_and_cumulative_top_three_scope_in_both_locales():
    panel = rendered(history(1999))
    text = json.dumps(panel, ensure_ascii=False)
    assert "≥ observed lower bound" in text and "≥ 已观测下限" in text
    assert "Cumulative by subscription / model · top 3" in text
    assert "按订阅商 / 模型累计 · 前 3 项" in text


def test_history_label_changes_preserve_native_geometry_and_do_not_add_rows():
    panel = rendered(history(1999))
    assert panel["element_id"] == "footer_details" and panel["expanded"] is False
    assert len(panel["elements"]) == 13
    group = next(e for e in panel["elements"] if e.get("background_style") == "grey-50")
    row = group["columns"][0]["elements"][1]
    assert [c["weight"] for c in row["columns"]] == [2, 4, 1]
    assert row["columns"][-1]["elements"][0]["text_align"] == "right"


@pytest.mark.parametrize("status", ["pending", "unavailable", "no_history"])
def test_not_ready_history_does_not_display_synthetic_period_counts(status):
    panel = rendered({**history(1999), "status": status})
    text = json.dumps(panel, ensure_ascii=False)
    assert "≥" not in text and "Cumulative by" not in text
    assert not any(e.get("background_style") == "grey-50" for e in panel["elements"])
