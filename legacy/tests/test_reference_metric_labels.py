"""Native footer labels and rounded bounds must retain their measurement meaning."""

from decimal import Decimal

import pytest

from hermes_lark_streaming.cardkit.reference import build_reference_footer
from hermes_lark_streaming.footer.render import compact


@pytest.mark.parametrize("value", [999, 1050, 48384, 999999, 1009999, 999999999, 10**15 - 1])
def test_partial_cache_abbreviation_never_increases_the_lower_bound(value):
    panel = build_reference_footer({"input_tokens": value * 2, "cache_read_tokens": value,
                                    "cache_read_partial": True})[0]
    row = next(e for e in panel["elements"] if e.get("tag") == "column_set"
               and "Cache read" in str(e))
    displayed = row["columns"][0]["elements"][1]["content"].removeprefix("**≥").split(" / ")[0]
    multiplier = {"k": 1000, "M": 1000000}.get(displayed[-1], 1)
    numeric = displayed[:-1] if multiplier != 1 else displayed
    assert Decimal(numeric) * multiplier <= value
    # A complete valid input denominator permits a cache-hit lower bound;
    # counters outside the supported range still stay unknown.
    expected = "≥50.0%" if value * 2 <= 10**15 else "—"
    assert f" / {expected}**" in row["columns"][0]["elements"][1]["content"]
    # Ordinary measured totals retain existing nearest rounding.
    assert compact(48384) == "48.4k"


def test_unknown_fields_have_native_locale_titles_and_no_trailing_separator():
    panel = build_reference_footer({})[0]
    title = panel["header"]["title"]
    assert title["content"] == "🪙 Model not reported · Context not reported"
    assert title["i18n_content"]["zh_cn"] == "🪙 模型未提供 · 上下文未提供"
    assert " · </font>" not in panel["elements"][1]["content"]
    partial = build_reference_footer({"usage_partial": True})[0]["header"]["title"]
    assert partial["content"].endswith(" · Partial")
    assert partial["i18n_content"]["zh_cn"].endswith(" · 不完整")


@pytest.mark.parametrize("elapsed,attempt,waited", [(4.4, 4.4, False), (8.0, 2.0, True), (4.4, None, False)])
def test_first_response_label_only_mentions_wait_when_measurements_show_it(elapsed, attempt, waited):
    panel = build_reference_footer({"first_response": elapsed, "first_response_attempt": attempt})[0]
    row = next(e for e in panel["elements"] if e.get("tag") == "column_set"
               and "First response" in str(e))
    label = row["columns"][0]["elements"][0]
    assert "重试" not in label["i18n_content"]["zh_cn"]
    assert ("含等待" in label["i18n_content"]["zh_cn"]) is waited
    assert row["columns"][0]["elements"][1]["content"] == f"**{elapsed:.2f}s**"
