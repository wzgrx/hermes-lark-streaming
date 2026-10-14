from __future__ import annotations

from hermes_lark_streaming.card.model import AgentStatus, Block, BlockKind, Phase, RenderOptions, TurnView
from hermes_lark_streaming.card.render import render_final, render_streaming
from hermes_lark_streaming.session.agents import MAX_AGENTS, AgentBook


def ident(i: int, **extra):
    return {"subagent_id": f"sa-{i}", "task_index": i, "task_count": 2, "goal": f"研究模块 {i}",
            "delegation_id": "deleg_1", **extra}


def test_book_follows_start_tools_and_completion():
    book = AgentBook()
    assert book.apply("subagent.start", "", "研究模块 0", ident(0)) == "deleg_1"
    book.apply("subagent.tool", "terminal", "ls", ident(0, tool_count=1))
    book.apply("subagent.tool", "read_file", "a.py", ident(0, tool_count=2))
    book.apply("subagent.start", "", "", ident(1))
    book.apply("subagent.complete", "", "", ident(1, status="failed", failure_reason="超时", duration_seconds=61.0))
    zero, one = book.views("deleg_1")
    assert (zero.index, zero.goal, zero.tools, zero.recent, zero.status) == (
        1, "研究模块 0", 2, ("terminal", "read_file"), AgentStatus.RUNNING)
    assert (one.status, one.summary, one.elapsed_s) == (AgentStatus.FAILED, "超时", 61.0)
    assert book.running
    book.apply("subagent.complete", "", "找到 3 个入口", ident(0, status="completed", summary="找到 3 个入口"))
    assert not book.running and book.views("deleg_1")[0].summary == "找到 3 个入口"


def test_book_ignores_events_without_identity_and_caps_fan_out():
    book = AgentBook()
    assert book.apply("subagent.progress", "🔀 nested", "", {}) is None  # a grandchild summary: nothing to attach
    for i in range(MAX_AGENTS + 3):
        book.apply("subagent.start", "", "", ident(i))
    assert len(book.records) == MAX_AGENTS


def agents_view(*statuses: AgentStatus, phase: Phase = Phase.DONE) -> TurnView:
    book = AgentBook()
    for i, status in enumerate(statuses):
        book.apply("subagent.start", "", "", ident(i))
        if status is not AgentStatus.RUNNING:
            book.apply("subagent.complete", "", "", ident(
                i, status="completed" if status is AgentStatus.OK else "failed", duration_seconds=90.0,
                summary="完成", failure_reason="超时"))
    block = Block(BlockKind.AGENTS, "b0", agents=book.views("deleg_1"))
    return TurnView(phase=phase, card_key="k", blocks=(block, Block(BlockKind.ANSWER, "b1", "好的")))


def panel_of(card: dict) -> dict:
    return next(e for e in card["body"]["elements"] if e["tag"] == "collapsible_panel" and "子代理" in str(e))


def test_panel_title_rows_and_expansion():
    running = panel_of(render_final(agents_view(AgentStatus.RUNNING, AgentStatus.OK)))
    title = running["header"]["title"]["i18n_content"]["zh_cn"]
    assert "🔀 子代理 · 2 个 · 1 运行中 · 1 完成" in title and running["expanded"] is True  # finished card, live agents
    assert "⏳ **#1 研究模块 0**" in str(running) and "✅ **#2 研究模块 1**" in str(running)
    done = panel_of(render_final(agents_view(AgentStatus.OK, AgentStatus.OK)))
    assert "全部完成 · (1m 30s)" in done["header"]["title"]["i18n_content"]["zh_cn"] and done["expanded"] is False
    failed = panel_of(render_final(agents_view(AgentStatus.OK, AgentStatus.FAILED)))
    assert "1 失败" in str(failed["header"]) and failed["expanded"] is True and "超时" in str(failed)
    live = panel_of(render_streaming(agents_view(AgentStatus.RUNNING, phase=Phase.RUNNING)))
    assert live["expanded"] is True and RenderOptions().show_process
