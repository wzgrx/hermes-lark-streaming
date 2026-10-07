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
    STATUS_ID,
    count_elements,
    partial_for,
    render_final,
    render_streaming,
)

OK = StepStatus.OK
FAIL = StepStatus.FAILED


def ids(card):
    return [e.get("element_id") for e in card["body"]["elements"]]


def by_id(card, element_id):
    return next(e for e in card["body"]["elements"] if e.get("element_id") == element_id)


def details(card):
    return next(e for e in card["body"]["elements"] if str(e.get("element_id", "")).startswith("details"))


def tag(card):
    text = card["header"]["text_tag_list"][0]["text"]
    return text.get("i18n_content", {}).get("zh_cn") or text["content"]


def subtitle(card):
    sub = card["header"].get("subtitle") or {}
    return sub.get("i18n_content", {}).get("zh_cn") or sub.get("content", "")


def dumps(node):
    return json.dumps(node, ensure_ascii=False)


QUOTA = Section("accounts", "OpenCode Go · 订阅与额度", (
    Metric("5小时", "12%", 0.12, "4h 后重置"), Metric("每月", "86%", 0.86, "10-20 14:26"),
), layout="bars")


def done_view(**kw):
    base = dict(
        phase=Phase.DONE, elapsed_s=14.4, card_key="abc123",
        steps=(Step("Terminal", "git status", OK, 180), Step("Read file", "config.yaml", OK, 42)),
        answers=("已修复。",),
        footer=Footer(model="deepseek-v4.1-flash", context_used=70300, context_max=1_000_000, cache_hit=0.82,
                      cache_hit_is_floor=True, tag="龙虾3号"),
    )
    base.update(kw)
    return TurnView(**base)


# ---------------------------------------------------------------- header


def test_header_colour_tag_and_subtitle_follow_the_state():
    cases = (
        (done_view(), "green", "已完成", "14.4s · 2 步"),
        (done_view(steps=(Step("T", "x", FAIL, 5, "boom"),)), "red", "有失败", "1 步失败"),
        (done_view(phase=Phase.FAILED), "red", "失败", "14.4s"),
        (done_view(phase=Phase.STOPPED), "grey", "已停止", "14.4s"),
        (done_view(continued=True), "grey", "已分页", "下一张卡片"),
    )
    for view, template, state, sub in cases:
        card = render_final(view)
        assert card["header"]["template"] == template and tag(card) == state and sub in subtitle(card)
        assert card["header"]["title"]["content"] == "龙虾3号"


def test_header_without_agent_name_falls_back_to_hermes():
    card = render_final(done_view(footer=Footer()))
    assert card["header"]["title"]["content"] == "Hermes"


# ---------------------------------------------------------------- running


def test_streaming_card_is_header_live_line_and_answer():
    view = TurnView(steps=(Step("Terminal", "pytest -q", StepStatus.RUNNING),), answers=("先看",), elapsed_s=42.7)
    card = render_streaming(view)
    assert card["schema"] == "2.0" and card["config"]["streaming_mode"] is True
    assert ids(card) == [STATUS_ID, ANSWER_ID]
    assert tag(card) == "运行中" and card["header"]["template"] == "blue"
    live = by_id(card, STATUS_ID)["i18n_content"]["zh_cn"]
    assert "正在执行" in live and "`pytest -q`" in live and "步骤 0/1" in live and "42s" in live and "42.7" not in live
    assert by_id(card, ANSWER_ID)["content"] == "先看"


def test_live_line_describes_what_happens_without_tools():
    assert "正在处理" in render_streaming(TurnView())["body"]["elements"][0]["i18n_content"]["zh_cn"]
    answering = render_streaming(TurnView(answers=("x",)))["body"]["elements"][0]["i18n_content"]["zh_cn"]
    assert "正在回答" in answering


# ---------------------------------------------------------------- finished body


def test_done_card_order_is_answer_meta_details():
    card = render_final(done_view(sections=(Section("usage", "用量", (Metric("输入", "70.3k"),)),)))
    elements = card["body"]["elements"]
    assert elements[0]["content"] == "已修复。"
    assert "deepseek" in elements[1]["content"].lower()
    panel = details(card)
    assert panel is elements[-1] and panel["expanded"] is False and panel["element_id"] == "details_abc123"
    assert "streaming_mode" not in card["config"] and card["config"]["summary"]["content"] == "已修复。"


def test_failures_lead_the_card_with_command_and_output():
    view = done_view(steps=(Step("Terminal", "ok", OK, 10), Step("Terminal", "exit 7", FAIL, 38, "Exit code 7\nline")))
    first = render_final(view)["body"]["elements"][0]
    text = dumps(first)
    assert first["background_style"] == "red-50"
    assert "`exit 7`" in text and "Exit code 7" in text and "```" in text


def test_failure_list_is_bounded():
    steps = tuple(Step("T", f"c{i}", FAIL, 1, "boom") for i in range(6))
    card = render_final(done_view(steps=steps))
    reds = [e for e in card["body"]["elements"] if e.get("background_style") == "red-50"]
    assert len(reds) == 3 and "另有 3 步失败" in dumps(card)


def test_meta_chips_colour_by_level_and_show_quota_only_when_high():
    meta = render_final(done_view(sections=(QUOTA,)))["body"]["elements"][1]["content"]
    assert "上下文 70.3k/1M · 7%" in meta and "缓存 ≥82%" in meta
    assert "<text_tag color='red'>额度 每月 86%</text_tag>" in meta
    low = Section("accounts", "额度", (Metric("每月", "20%", 0.2),), layout="bars")
    assert "额度" not in render_final(done_view(sections=(low,)))["body"]["elements"][1]["content"]
    full = done_view(footer=Footer(model="m", context_used=900_000, context_max=1_000_000))
    assert "<text_tag color='red'>上下文" in render_final(full)["body"]["elements"][1]["content"]


def test_details_panel_gathers_steps_thoughts_sections_and_reviews():
    view = done_view(thoughts="想一想", notices=("已保存记忆",), sections=(
        Section("usage", "用量", (Metric("输入", "70.3k"), Metric("输出", "1.2k")), notes=("统计不完整",)),
        QUOTA,
    ))
    panel = details(render_final(view))
    text = dumps(panel)
    for needle in ("**过程**", "`git status`", "**思考**", "想一想", "**用量**", "输入 **70.3k**", "统计不完整",
                   "**OpenCode Go · 订阅与额度**", "▓", "**后台复盘**", "已保存记忆"):
        assert needle in text, needle
    title = panel["header"]["title"]["i18n_content"]["zh_cn"]
    assert "过程与详情" in title and "2 步" in title and "用量" in title


def test_unknown_values_are_not_shown():
    section = Section(
        "accounts", "订阅",
        (Metric("账户余额", "未知", hint="API 未返回"), Metric("5小时", "38%", 0.38, "重置时间未知")),
        notes=("订阅到期:未知(API 未返回)", "API 快照 · 22:37"),
    )
    empty = Section("resources", "资源", (Metric("CPU", "未知"),), notes=("采样未就绪",))
    text = dumps(details(render_final(done_view(sections=(section, empty)))))
    assert "未知" not in text and "账户余额" not in text and "CPU" not in text and "**资源**" not in text
    assert "5小时" in text and "API 快照" in text


def test_details_hidden_when_continued_or_disabled():
    sections = (Section("usage", "用量", (Metric("输入", "1"),)),)
    assert not any(str(i).startswith("details") for i in ids(render_final(done_view(continued=True))))
    assert not any(str(i).startswith("details") for i in ids(render_final(
        done_view(sections=sections, steps=()), RenderOptions(show_details=False))))


def test_process_off_hides_failures_and_steps():
    view = done_view(steps=(Step("T", "x", FAIL, 1, "boom"),))
    card = render_final(view, RenderOptions(show_process=False))
    assert not any(e.get("background_style") == "red-50" for e in card["body"]["elements"])
    assert "**过程**" not in dumps(card)


def test_untrusted_text_is_escaped_and_redacted():
    view = done_view(steps=(Step("Term<b>", "curl -H 'Authorization: Bearer sekrit9xyz' <x> *y*", FAIL, 1, "<e>"),))
    text = dumps(render_final(view))
    assert "sekrit9xyz" not in text and "<b>" not in text and "&lt;" in text
    assert "\\\\*y\\\\*" in text


def test_long_step_lists_keep_failures_and_bound_rows():
    steps = tuple(Step("T", f"s{i}", OK, 1) for i in range(40))
    steps = (*steps[:5], Step("T", "bad", FAIL, 1, "boom"), *steps[5:])
    text = dumps(details(render_final(done_view(steps=steps), RenderOptions(max_steps=12))))
    assert "`bad`" in text and "另有 29 步" in text


def test_empty_answer_never_leaves_blank_card():
    for phase, needle in ((Phase.DONE, "完成"), (Phase.FAILED, "失败"), (Phase.STOPPED, "停止")):
        assert needle in dumps(render_final(TurnView(phase=phase))["body"])


def test_continued_card_points_to_the_next_card():
    body = dumps(render_final(done_view(continued=True))["body"])
    assert "下一张卡片" in body and "已修复。" in body


def test_partial_update_keeps_panel_state_unless_reset():
    panel = details(render_final(done_view()))
    assert "expanded" not in partial_for(panel)
    assert partial_for(panel, reset_state=True)["expanded"] is False
    live = render_streaming(TurnView())["body"]["elements"][0]
    assert set(partial_for(live)) <= {"content", "i18n_content"}


def test_default_card_stays_small():
    card = render_final(done_view(sections=(
        Section("usage", "用量", tuple(Metric(f"k{i}", "1") for i in range(8))),
        Section("resources", "资源", tuple(Metric(f"r{i}", "1") for i in range(5))),
        QUOTA,
    )))
    assert len(json.dumps(card, ensure_ascii=False).encode()) < 6000
    assert count_elements(card) < 30


def test_static_cards_use_the_v2_header():
    from hermes_lark_streaming.card.static import render_background, render_cron

    cron = render_cron("# 日报\n内容", task_name="晨报", run_time="2026-10-07T08:00:00")
    assert cron["header"]["title"]["content"] == "晨报" and "2026-10-07 08:00" in dumps(cron["header"])
    bg = render_background("整理文件", "")
    assert "No response generated" in dumps(bg) and bg["header"]["template"] == "green"
