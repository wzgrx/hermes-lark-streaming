"""Turn details must keep the same native panel chrome as background review."""

from copy import deepcopy

import pytest

from hermes_lark_streaming.cardkit.builder import _build_notice_panel
from hermes_lark_streaming.footer.render import build_footer
from hermes_lark_streaming.footer.runtime import build_runtime_footer, runtime_actions


def chrome(panel):
    result = deepcopy(panel)
    result.pop("element_id", None)
    result.pop("elements")
    title = result["header"]["title"]
    title.pop("content")
    title.pop("i18n_content")
    return result


@pytest.mark.parametrize("phase", ["completed", "failed", "stopped", "answer", "waiting", "compression"])
def test_details_and_review_share_chrome_across_lifecycle(phase):
    data = {"runtime_phase": phase, "input_tokens": 100, "output_tokens": 3}
    if phase in {"completed", "failed", "stopped"}:
        elements = build_footer(data, is_error=phase == "failed", is_aborted=phase == "stopped")
    else:
        elements = build_runtime_footer(data)
    panel = elements[-1]
    assert panel["element_id"] == "footer_details"
    assert chrome(panel) == chrome(_build_notice_panel("Review content"))
    assert panel["header"]["title"]["i18n_content"]["zh_cn"] == "📊 本轮详情"
    assert not any(e.get("element_id") == "footer_detail_rule" for e in elements)
    # A border replaces the redundant separator; content and statistics survive.
    assert len([e for e in panel["elements"] if e["tag"] == "column_set"]) == 4


def test_runtime_updates_preserve_shared_header_and_reader_expansion():
    actions = runtime_actions(build_runtime_footer({"runtime_phase": "answer"}))
    action = next(a for a in actions if a["params"]["element_id"] == "footer_details")
    assert set(action["params"]["partial_element"]) == {"elements"}


def test_details_off_keeps_summary_only():
    for elements in (build_footer({}, details=False), build_runtime_footer({}, details=False)):
        assert not any(e["tag"] == "collapsible_panel" for e in elements)
