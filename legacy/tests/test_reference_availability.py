"""Frozen cards must not promise live reads or discard half-known metrics."""

import json
from copy import deepcopy

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card
from hermes_lark_streaming.cardkit.reference import build_reference_footer
from hermes_lark_streaming.footer.runtime import build_runtime_footer, runtime_actions


@pytest.mark.parametrize("flags", [{}, {"is_error": True}, {"is_aborted": True}])
def test_terminal_title_distinguishes_unreported_from_pending(flags):
    title = build_reference_footer({}, **flags)[0]["header"]["title"]
    assert "Model not reported" in title["content"] and "Context not reported" in title["content"]
    assert "模型未提供" in title["i18n_content"]["zh_cn"] and "上下文未提供" in title["i18n_content"]["zh_cn"]
    assert "pending" not in title["content"].lower() and "待返回" not in title["i18n_content"]["zh_cn"]


def test_live_title_still_indicates_pending_and_keeps_expansion_state():
    elements = build_runtime_footer({"presentation": "reference"})
    panel = next(x for x in elements if x.get("element_id") == "footer_details")
    assert "Model pending" in panel["header"]["title"]["content"]
    assert "上下文待返回" in panel["header"]["title"]["i18n_content"]["zh_cn"]
    assert all("expanded" not in x["params"]["partial_element"] for x in runtime_actions(elements, reference=True))


@pytest.mark.parametrize(
    "data,shown",
    [
        ({"context_used": 70300}, "70.3k/—"),
        ({"context_max": 1000000}, "—/1.0M"),
        ({"context_used": 0}, "0/—"),
        ({"context_used": 70300, "context_max": 0}, "70.3k/—"),
    ],
)
def test_half_known_context_keeps_available_operand_without_fake_percent(data, shown):
    title = build_reference_footer(data)[0]["header"]["title"]
    assert shown in title["content"] and shown in title["i18n_content"]["zh_cn"]
    assert "%" not in title["content"]


@pytest.mark.parametrize(
    "data,en,zh",
    [
        ({"api_calls": 3}, "3 / —", "3 次 / —"),
        ({"retries": 2}, "— / 2", "— / 2 次"),
        ({"api_calls": 0}, "0 / —", "0 次 / —"),
        ({"api_calls": 3, "retries": 0}, "3 / 0", "3 次 / 0 次"),
        ({"api_calls": True, "retries": 0}, "— / 0", "— / 0 次"),
    ],
)
def test_request_and_error_counts_have_independent_availability(data, en, zh):
    panel = build_reference_footer(data)[0]
    row = next(e for e in panel["elements"] if e.get("tag") == "column_set" and "API attempts" in str(e))
    value = row["columns"][1]["elements"][1]
    assert value["content"] == f"**{en}**"
    assert value["i18n_content"]["zh_cn"] == f"**{zh}**"


@pytest.mark.parametrize("status", ["pending", "unavailable"])
@pytest.mark.parametrize("flags", [{}, {"is_error": True}, {"is_aborted": True}])
def test_frozen_history_does_not_promise_in_place_refresh(status, flags):
    data = {"presentation": "reference", "reference": {"history": {"status": status}}}
    before = deepcopy(data)
    card = build_complete_card(segments=[], all_tool_steps=[], footer_data=data, footer_mode="enhanced", **flags)
    text = json.dumps(card, ensure_ascii=False)
    assert "后续消息" in text and "later message" in text
    assert "正在读取本机历史" not in text and "稍后重试" not in text
    assert data == before and inspect_card(card).safe


@pytest.mark.parametrize("status,expected", [("pending", "正在读取本机历史"), ("unavailable", "稍后重试")])
def test_live_history_preserves_its_real_retry_state(status, expected):
    elements = build_runtime_footer({"presentation": "reference", "reference": {"history": {"status": status}}})
    assert expected in json.dumps(elements, ensure_ascii=False)


def test_complete_context_and_no_history_remain_distinct():
    panel = build_reference_footer(
        {"context_used": 70300, "context_max": 1000000, "reference": {"history": {"status": "no_history"}}}
    )[0]
    assert "70.3k/1.0M (7%)" in panel["header"]["title"]["content"]
    text = json.dumps(panel, ensure_ascii=False)
    assert "尚无已记录的主请求" in text and "后续消息" not in text
