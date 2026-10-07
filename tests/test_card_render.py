from __future__ import annotations

import json

from hermes_lark_streaming.card.model import (
    Footer,
    Metric,
    Phase,
    RenderOptions,
    Section,
    Step,
    StepStatus,
    TurnView,
)
from hermes_lark_streaming.card.render import (
    ANSWER_ID,
    DETAILS_ID,
    FOOTER_ID,
    PROCESS_ID,
    STATUS_ID,
    count_elements,
    render_final,
    render_streaming,
)

OK = StepStatus.OK
FAIL = StepStatus.FAILED


def ids(card):
    return [e.get("element_id") for e in card["body"]["elements"]]


def by_id(card, element_id):
    return next(e for e in card["body"]["elements"] if e.get("element_id") == element_id)


def done_view(**kw):
    base = dict(
        phase=Phase.DONE, elapsed_s=14.4,
        steps=(Step("Terminal", "git status", OK, 180), Step("Read", "config.yaml", OK, 42)),
        answers=("已修复。",),
        footer=Footer(model="deepseek-v4.1-flash", context_used=70300, context_max=1_000_000, cache_hit=0.82,
                      cache_hit_is_floor=True, tag="龙虾1号"),
    )
    base.update(kw)
    return TurnView(**base)


def test_streaming_card_has_stable_ids_and_streaming_mode():
    view = TurnView(steps=(Step("Terminal", "pytest", StepStatus.RUNNING),), answers=("先看",))
    card = render_streaming(view)
    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is True
    assert ids(card) == [STATUS_ID, PROCESS_ID, ANSWER_ID]
    assert by_id(card, PROCESS_ID)["expanded"] is True  # auto: open while running
    assert by_id(card, ANSWER_ID)["content"] == "先看"


def test_streaming_card_without_steps_has_no_process_panel():
    assert ids(render_streaming(TurnView())) == [STATUS_ID, ANSWER_ID]


def test_done_card_is_collapsed_by_default_and_orders_blocks():
    card = render_final(done_view(sections=(Section("usage", "用量", (Metric("输入", "70.3k"),)),)))
    assert ids(card) == [STATUS_ID, PROCESS_ID, None, FOOTER_ID, DETAILS_ID]
    assert by_id(card, PROCESS_ID)["expanded"] is False
    assert by_id(card, DETAILS_ID)["expanded"] is False
    assert "streaming_mode" not in card["config"]
    assert card["config"]["summary"]["content"] == "已修复。"


def test_failed_step_opens_process_and_marks_red():
    view = done_view(steps=(Step("Terminal", "ok", OK, 10), Step("Terminal", "exit 7", FAIL, 38, "Exit code 7")))
    card = render_final(view)
    panel = by_id(card, PROCESS_ID)
    assert panel["expanded"] is True
    assert panel["border"]["color"] == "red"
    assert any(e.get("background_style") == "red-50" for e in panel["elements"])
    status = by_id(card, STATUS_ID)
    assert "1 failed" in status["content"] and "1 步失败" in status["i18n_content"]["zh_cn"]


def test_process_option_overrides_auto():
    assert by_id(render_final(done_view(), RenderOptions(process="open")), PROCESS_ID)["expanded"] is True
    failing = done_view(steps=(Step("T", "", FAIL, 1),))
    assert by_id(render_final(failing, RenderOptions(process="closed")), PROCESS_ID)["expanded"] is False


def test_details_hidden_while_running_and_when_disabled():
    sections = (Section("usage", "用量", (Metric("输入", "1"),)),)
    assert DETAILS_ID not in ids(render_final(done_view(phase=Phase.RUNNING, sections=sections)))
    assert DETAILS_ID not in ids(render_final(done_view(sections=sections), RenderOptions(show_details=False)))
    assert DETAILS_ID not in ids(render_final(done_view(sections=(Section("accounts", "账户"),))))


def test_details_sections_render_grid_and_bars():
    card = render_final(done_view(sections=(
        Section("usage", "用量", (Metric("输入", "70.3k"), Metric("输出", "1.2k"), Metric("请求", "3"))),
        Section("accounts", "订阅账户", (Metric("5 小时", "38%", 0.38, "2h14m 后重置"),), layout="bars",
                notes=("快照 10-07 14:02",)),
    )))
    text = json.dumps(by_id(card, DETAILS_ID), ensure_ascii=False)
    assert text.count('"column_set"') == 2  # three metrics -> two rows
    assert "▓▓▓▓" in text and "░░░░░░" in text
    assert "快照 10-07 14:02" in text


def test_untrusted_text_is_escaped_and_redacted():
    view = done_view(steps=(Step("Term<b>", "curl -H 'Authorization: Bearer abc123' <x> *y*", OK, 1),))
    text = json.dumps(by_id(render_final(view), PROCESS_ID), ensure_ascii=False)
    assert "abc123" not in text
    assert "<b>" not in text and "&lt;" in text
    assert "\\\\*y\\\\*" in text  # markdown specials escaped


def test_long_step_lists_keep_failures_and_bound_rows():
    steps = tuple(Step("T", f"s{i}", OK, 1) for i in range(40))
    steps = (*steps[:5], Step("T", "bad", FAIL, 1, "boom"), *steps[5:])
    panel = by_id(render_final(done_view(steps=steps), RenderOptions(max_steps=12)), PROCESS_ID)
    text = json.dumps(panel, ensure_ascii=False)
    assert "bad" in text and "已省略 29 步" in text
    assert count_elements(panel) < 60


def test_empty_answer_never_leaves_blank_card():
    for phase, needle in ((Phase.DONE, "完成"), (Phase.FAILED, "失败"), (Phase.STOPPED, "停止")):
        card = render_final(TurnView(phase=phase))
        assert any(needle in json.dumps(e, ensure_ascii=False) for e in card["body"]["elements"][1:])


def test_continued_card_has_no_footer_or_details():
    card = render_final(done_view(continued=True, sections=(Section("usage", "用量", (Metric("a", "b"),)),)))
    assert FOOTER_ID not in ids(card) and DETAILS_ID not in ids(card)
    assert "已分页" in by_id(card, STATUS_ID)["i18n_content"]["zh_cn"]


def test_footer_formats_context_cache_and_partial():
    footer = by_id(render_final(done_view()), FOOTER_ID)["i18n_content"]["zh_cn"]
    assert "70.3k / 1M · 7%" in footer and "缓存命中 ≥82%" in footer and "龙虾1号" in footer
    partial = render_final(done_view(footer=Footer(model="m", partial=True)))
    assert "用量不完整" in by_id(partial, FOOTER_ID)["i18n_content"]["zh_cn"]


def test_default_card_stays_small():
    card = render_final(done_view(sections=(
        Section("usage", "用量", tuple(Metric(f"k{i}", "1") for i in range(6))),
        Section("resources", "资源", tuple(Metric(f"r{i}", "1") for i in range(4))),
        Section("accounts", "账户", tuple(Metric(f"q{i}", "1%", 0.3, "x") for i in range(3)), layout="bars"),
    )))
    assert len(json.dumps(card, ensure_ascii=False).encode()) < 8000
    assert count_elements(card) < 80
