from __future__ import annotations

import json

from hermes_lark_streaming.card.model import (
    Block,
    BlockKind,
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
    STATUS_ID,
    count_elements,
    partial_for,
    render_final,
    render_streaming,
    streaming_elements,
)

OK = StepStatus.OK
FAIL = StepStatus.FAILED
RUN = StepStatus.RUNNING


def dumps(node):
    return json.dumps(node, ensure_ascii=False)


def zh(element):
    return element.get("i18n_content", {}).get("zh_cn") or element.get("content", "")


def title(panel):
    return zh(panel["header"]["title"])


def panels(card):
    return [e for e in card["body"]["elements"] if e["tag"] == "collapsible_panel"]


QUOTA = Section("accounts", "OpenCode Go · 订阅与额度", (
    Metric("5小时", "12%", 0.12, "4h 后重置"), Metric("每月", "86%", 0.86, "10-20 14:26"),
), layout="bars")


def timeline(*, failed=False, open_last=False):
    steps = (Step("Terminal", "git status", OK, 297, icon="setting_outlined"),)
    later = (Step("Terminal", "exit 7", FAIL if failed else OK, 801, "Exit code 7\nline two"),)
    return (
        Block(BlockKind.THOUGHT, "b0", "先看看状态", elapsed_s=1.6),
        Block(BlockKind.TOOLS, "b1", steps=steps, elapsed_s=1.2),
        Block(BlockKind.ANSWER, "b2", "第一步已完成。"),
        Block(BlockKind.TOOLS, "b3", steps=later, elapsed_s=0.9, open=open_last),
        Block(BlockKind.ANSWER, "b4", "结论如下。"),
    )


def done_view(**kw):
    failed = kw.pop("failed", False)
    base = dict(
        phase=Phase.DONE, elapsed_s=10.4, card_key="abc123",
        steps=timeline(failed=failed)[1].steps + timeline(failed=failed)[3].steps,
        blocks=timeline(failed=failed),
        answers=("第一步已完成。", "结论如下。"),
        footer=Footer(model="deepseek-v4-flash", context_used=20000, context_max=1_000_000, cache_hit=0.83,
                      tag="龙虾3号"),
    )
    base.update(kw)
    return TurnView(**base)


# ---------------------------------------------------------------- the timeline


def test_final_card_keeps_arrival_order_with_no_header():
    card = render_final(done_view())
    assert "header" not in card
    kinds = [e["tag"] for e in card["body"]["elements"]]
    assert kinds[:5] == ["collapsible_panel", "collapsible_panel", "markdown", "collapsible_panel", "markdown"]
    assert kinds[5] == "hr"
    assert "思考了 1.6s" in title(panels(card)[0]) and "工具执行 · 1 步 · (1.2s)" in title(panels(card)[1])


def test_tool_rows_match_the_original_look():
    tools = panels(render_final(done_view()))[1]
    head, detail = tools["elements"][0], tools["elements"][1]
    assert head["tag"] == "div"
    assert head["icon"] == {"tag": "standard_icon", "token": "setting_outlined", "color": "grey"}
    expected = "**Terminal (297 ms)** · <font color='green'>成功</font>"
    assert zh(head["text"]) == expected
    assert head["text"]["content"].endswith("Succeeded</font>")
    assert detail["text"]["content"] == "git status" and detail["text"]["text_color"] == "grey"


def test_finished_panels_collapse_except_failures():
    ok = panels(render_final(done_view()))
    assert all(p["expanded"] is False for p in ok)
    failed = panels(render_final(done_view(failed=True)))
    tools = [p for p in failed if "工具执行" in title(p)]
    assert [p["expanded"] for p in tools] == [False, True]
    assert "1 失败" in title(tools[1]) and "Exit code 7" in dumps(tools[1]) and "```" in dumps(tools[1])


def test_process_option_overrides_auto():
    assert all(p["expanded"] for p in panels(render_final(done_view(), RenderOptions(process="open")))
               if "详情" not in title(p))
    closed = panels(render_final(done_view(failed=True), RenderOptions(process="closed")))
    assert not any(p["expanded"] for p in closed)
    off = render_final(done_view(failed=True), RenderOptions(process="off", show_process=False))
    assert "工具执行" not in dumps(off) and "思考了" not in dumps(off) and "结论如下" in dumps(off)


def test_footer_line_is_one_status_row():
    card = render_final(done_view(failed=True))
    elements = card["body"]["elements"]
    hr = next(i for i, e in enumerate(elements) if e["tag"] == "hr")
    footer = zh(elements[hr + 1])
    assert footer.startswith("✅ 已完成 · 10.4s · 20.0K/1.0M (2%) · deepseek-v4-flash · 缓存 83%")
    assert "1 步失败" in footer and "龙虾3号" in footer
    assert zh(render_final(done_view(phase=Phase.STOPPED))["body"]["elements"][hr + 1]).startswith("🛑 已停止")
    error = render_final(done_view(phase=Phase.FAILED))["body"]["elements"][hr + 1]
    assert "❌ 出错" in zh(error) and zh(error).startswith("<font color='red'>")


def test_details_panel_after_the_footer():
    view = done_view(notices=("已保存记忆",), sections=(
        Section("usage", "用量", (Metric("输入", "70.3k"), Metric("输出", "1.2k")), notes=("统计不完整",)),
        QUOTA,
    ))
    card = render_final(view)
    details = card["body"]["elements"][-1]
    assert details["tag"] == "collapsible_panel" and details["expanded"] is False
    assert details["element_id"] == "details_abc123"
    assert title(details).endswith("详情 · 用量 · 额度 · 后台复盘</font>")
    text = dumps(details)
    for needle in ("输入 **70.3k**", "统计不完整", "▓", "已保存记忆"):
        assert needle in text


def test_unknown_values_are_not_shown():
    section = Section(
        "accounts", "订阅",
        (Metric("账户余额", "未知", hint="API 未返回"), Metric("5小时", "38%", 0.38, "重置时间未知")),
        notes=("订阅到期:未知(API 未返回)", "API 快照 · 22:37"),
    )
    empty = Section("resources", "资源", (Metric("CPU", "未知"),), notes=("采样未就绪",))
    text = dumps(render_final(done_view(sections=(section, empty)))["body"]["elements"][-1])
    assert "未知" not in text and "账户余额" not in text and "CPU" not in text and "资源" not in text
    assert "5小时" in text and "API 快照" in text
    assert render_final(done_view(sections=(empty,)))["body"]["elements"][-1]["tag"] == "markdown"


# ---------------------------------------------------------------- streaming


def test_streaming_card_is_blocks_then_live_line():
    running = (Step("Terminal", "pytest -q", RUN, icon="setting_outlined"),)
    view = TurnView(elapsed_s=42.7, card_key="k1", steps=running, blocks=(
        Block(BlockKind.THOUGHT, "b0", "想", open=False, elapsed_s=1.0),
        Block(BlockKind.TOOLS, "b1", steps=running, open=True),
    ))
    card = render_streaming(view)
    assert card["config"]["streaming_mode"] is True and "header" not in card
    elements = card["body"]["elements"]
    assert [e["element_id"] for e in elements] == ["b0_k1", "b1_k1", STATUS_ID]
    assert all(e["expanded"] for e in elements[:2])  # the reader follows along while running
    assert "运行中" in dumps(elements[1]) and "(" not in title(elements[1])  # open batch: no duration yet
    live = zh(elements[-1])
    assert "正在执行 Terminal" in live and "步骤 0/1" in live and "42s" in live and "42.7" not in live


def test_streaming_answer_is_one_element_per_block():
    view = TurnView(card_key="k1", answers=("先看",), blocks=(Block(BlockKind.ANSWER, "b0", "先看", open=True),))
    elements = streaming_elements(view, RenderOptions())
    assert elements[0] == {"tag": "markdown", "content": "先看", "text_size": "normal_v2", "element_id": "b0_k1"}
    thinking = TurnView(blocks=(Block(BlockKind.THOUGHT, "b0", "嗯", open=True),))
    assert "思考中" in title(streaming_elements(thinking, RenderOptions())[0])


def test_every_streaming_element_has_an_id():
    elements = streaming_elements(done_view(phase=Phase.RUNNING), RenderOptions())
    assert all(e.get("element_id") for e in elements)
    assert len({e["element_id"] for e in elements}) == len(elements)


# ---------------------------------------------------------------- safety and edges


def test_untrusted_text_is_escaped_and_redacted():
    step = Step("Term<b>", "curl -H 'Authorization: Bearer sekrit9xyz' <x>", FAIL, 1, "<e> token=hunter2")
    view = done_view(steps=(step,), blocks=(Block(BlockKind.TOOLS, "b0", steps=(step,)),))
    text = dumps(render_final(view))
    assert "sekrit9xyz" not in text and "hunter2" not in text and "<b>" not in text and "&lt;b&gt;" in text


def test_empty_answer_never_leaves_blank_card():
    for phase, needle in ((Phase.DONE, "完成"), (Phase.FAILED, "失败"), (Phase.STOPPED, "停止")):
        assert needle in dumps(render_final(TurnView(phase=phase))["body"])


def test_continued_card_points_to_the_next_card_without_footer():
    card = render_final(done_view(continued=True))
    text = dumps(card)
    assert "内容见下一张卡片" in text and "✅" not in text
    assert not any(e["tag"] == "hr" for e in card["body"]["elements"])


def test_partial_update_keeps_panel_state():
    panel = panels(render_final(done_view()))[0]
    assert "expanded" not in partial_for(panel)
    assert partial_for(panel, reset_state=True)["expanded"] is False
    live = render_streaming(TurnView())["body"]["elements"][-1]
    assert set(partial_for(live)) <= {"content", "i18n_content"}


def test_card_stays_small():
    card = render_final(done_view(failed=True, sections=(
        Section("usage", "用量", tuple(Metric(f"k{i}", "1") for i in range(8))),
        Section("resources", "资源", tuple(Metric(f"r{i}", "1") for i in range(5))),
        QUOTA,
    )))
    assert len(json.dumps(card, ensure_ascii=False).encode()) < 9000
    assert count_elements(card) < 60
