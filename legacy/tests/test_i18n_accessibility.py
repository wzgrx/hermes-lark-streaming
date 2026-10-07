from hermes_lark_streaming.cardkit.builder import build_complete_card
from hermes_lark_streaming.cardkit.i18n import _LOCALES, _t
from hermes_lark_streaming.streaming.segments import Segment, SegmentType


def test_status_has_text_and_four_locales() -> None:
    content = _t("status_completed")
    assert set(_LOCALES) == {"zh_cn", "en_us", "ja_jp", "ko_kr"}
    assert all("✅" in content[locale] for locale in _LOCALES)


def test_mobile_compact_snapshot_has_accessible_status() -> None:
    answer = Segment(SegmentType.ANSWER, "answer")
    answer.text = "mobile snapshot"
    card = build_complete_card(
        segments=[answer],
        all_tool_steps=[],
        width_mode="compact",
        header_enabled=True,
        footer_enabled=True,
    )
    assert card["config"]["width_mode"] == "compact"
    rendered = str(card)
    assert "Completed" in rendered
    assert "已完成" in rendered
