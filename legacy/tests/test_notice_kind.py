"""Lifecycle continuation is not a background memory review."""

import pytest

from hermes_lark_streaming.cardkit.builder import build_complete_card
from hermes_lark_streaming.streaming.segment_helper import build_add_segment_action
from hermes_lark_streaming.streaming.segments import SegmentState


@pytest.mark.parametrize("kind", ["review", "continuation"])
@pytest.mark.parametrize("stage", ["streaming", "final"])
def test_notice_kind_controls_title_in_streaming_and_final_cards(kind, stage):
    state = SegmentState()
    state.on_notice("Arbitrary notice body, not used to infer a title", kind=kind)
    notice = state.segments[0]
    if stage == "streaming":
        panel = build_add_segment_action(notice, [])['params']['elements'][0]
        assert panel['element_id'] == notice.el_id
    else:
        state.on_answer_delta("Answer retained")
        card = build_complete_card(segments=state.segments, all_tool_steps=[], footer_enabled=False)
        panel = card['body']['elements'][0]
    title = panel['header']['title']
    assert panel['expanded'] is False
    assert panel['elements'][0]['content'] == notice.text
    assert title['content'] == ("↪ Continued from previous card" if kind == "continuation" else "💾 Background review")
    assert title['i18n_content']['zh_cn'] == ("↪ 接续上一张卡片" if kind == "continuation" else "💾 后台复盘")


def test_legacy_notice_still_defaults_to_review_without_guessing_from_body():
    state = SegmentState()
    state.on_notice("↪ Continued from previous card")
    assert state.segments[0].notice_kind == "review"
    state.on_notice(" ", kind="continuation")
    assert len(state.segments) == 1
