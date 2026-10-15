"""Card 2.0 renderer (v3, the original project's look): ``TurnView`` in, card JSON out. Pure functions, no I/O.

The card is a timeline in arrival order, with no header:

    💭 思考了 1.6s            collapsible reasoning
    answer text
    🛠️ 工具执行 · 3 步 · (5.7s) collapsible tool list: icon, **Terminal (297 ms)** · 成功, grey command line
    answer text …
    ─────────
    ✅ 已完成 · 10.4s · 20.0K/1.0M (2%) · deepseek-v4-flash     one footer line
    📊 详情                     collapsed usage / resources / quota (finished cards only)

While streaming, a grey live line at the bottom says what is happening; new blocks are inserted above it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from typing import Any

from .markdown import downgrade_tables, optimize_markdown_style, split_long_text
from .model import (
    AgentStatus,
    Block,
    BlockKind,
    Metric,
    Phase,
    RenderOptions,
    Section,
    Step,
    StepStatus,
    Subagent,
    TurnView,
)
from .numbers import short_count
from .redact import redact

STATUS_ID = "live"  # the trailing live line while streaming
DETAILS_ID = "details"
TODO_ID = "todo"  # the task list sits just above the live line; new blocks are inserted above it
STOP_ID = "stop"  # the stop button under the live line, only while the turn runs

ELEMENT_LIMIT = 200  # CardKit hard cap on elements per card, counted over every nested tag
ELEMENT_BUDGET = 160  # hand over to a fresh card before the live card gets this big
LOCALES = ["zh_cn", "en_us", "ja_jp", "ko_kr"]
_STREAMING_CONFIG = {
    "print_frequency_ms": {"default": 15},
    "print_step": {"default": 1},
    "print_strategy": "fast",
}
_THOUGHT_CHARS = 4000
_SUMMARY_CHARS = 120
_ERROR_CHARS = 600
_DETAIL_CHARS = 160
_NOTE_CHARS = 300
_UNKNOWN = "未知"


@dataclass(frozen=True, slots=True)
class Bi:
    """A zh/en pair. The English text is the card default; zh_cn overrides it per locale."""

    zh: str
    en: str

    @classmethod
    def same(cls, text: str) -> Bi:
        return cls(text, text)


def _markdown(text: Bi, size: str, *, element_id: str | None = None) -> dict[str, Any]:
    element: dict[str, Any] = {"tag": "markdown", "content": text.en, "text_size": size}
    if text.zh != text.en:
        element["i18n_content"] = {"zh_cn": text.zh}
    if element_id:
        element["element_id"] = element_id
    return element


def _plain(text: Bi) -> dict[str, Any]:
    element: dict[str, Any] = {"tag": "plain_text", "content": text.en}
    if text.zh != text.en:
        element["i18n_content"] = {"zh_cn": text.zh}
    return element


def esc(value: str) -> str:
    """Escape untrusted text for a markdown element: HTML first, then markdown specials."""
    out = html.escape(value, quote=False)
    for ch in "\\`*_[]~":
        out = out.replace(ch, "\\" + ch)
    return out


def fence(value: str) -> str:
    """A fenced code block that its own content cannot close."""
    longest = max((len(run) for run in value.split("\n") if run and set(run) == {"`"}), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{value}\n{ticks}"


def clip(value: str, limit: int, *, single_line: bool = True) -> str:
    """Redact, fold whitespace, and cut at ``limit`` characters (escaping happens afterwards)."""
    text = redact(value)
    text = " ".join(text.split()) if single_line else text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def grey(text: str) -> str:
    return f"<font color='grey'>{text}</font>"


def duration(seconds: float | None, *, whole: bool = False) -> str:
    """``14.4s`` / ``2m 05s`` / ``1h 02m``; ``whole`` drops the decimal for a live clock."""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 60:
        return f"{int(seconds)}s" if whole else f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def step_time(ms: float | None) -> str:
    if ms is None:
        return ""
    return f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def compact(value: int) -> str:
    """404k / 1M / 4.83B, the same style as the details panel."""
    return short_count(value)


def _id(base: str, view: TurnView) -> str:
    """Per-card element id: Feishu remembers a panel's expanded state by id, so reuse would leak it."""
    return f"{base}_{view.card_key}"[:20] if view.card_key else base


_PANEL_PAD = 8  # px inside a panel's 1px border
# The footer starts where panel content starts (border + padding), so ✅ / 🤖 line up with 📊 below them.
_PANEL_INSET = f"0px 0px 0px {_PANEL_PAD + 1}px"


def panel(*, title: Bi, elements: list[dict[str, Any]], expanded: bool, element_id: str | None = None,
          vertical_spacing: str = "4px") -> dict[str, Any]:
    """The original project's native panel chrome: grey notation title, chevron on the right, rounded border."""
    title_el: dict[str, Any] = {"tag": "markdown", "content": title.en, "text_size": "notation"}
    if title.zh != title.en:
        title_el["i18n_content"] = {"zh_cn": title.zh}
    result: dict[str, Any] = {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": title_el,
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "size": "16px 16px", "color": "grey"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "vertical_spacing": vertical_spacing,
        "padding": f"{_PANEL_PAD}px {_PANEL_PAD}px {_PANEL_PAD}px {_PANEL_PAD}px",
        "elements": elements,
    }
    if element_id:
        result["element_id"] = element_id
    return result


# --------------------------------------------------------------------------- blocks

def _expanded(view: TurnView, opts: RenderOptions, *, failed: bool = False) -> bool:
    if view.phase is Phase.RUNNING and not view.continued:
        return True  # the reader follows along while the turn runs
    if opts.process == "open":
        return True
    if opts.process == "closed":
        return False
    return failed


def _thought_panel(block: Block, view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    if block.open:
        title = Bi(grey("💭 思考中…"), grey("💭 Thinking…"))
    elif block.elapsed_s:
        d = duration(block.elapsed_s)
        title = Bi(grey(f"💭 思考了 {d}"), grey(f"💭 Thought for {d}"))
    else:
        title = Bi(grey("💭 思考"), grey("💭 Thought"))
    text = block.text.strip()
    if len(text) > _THOUGHT_CHARS:
        text = "…" + text[-_THOUGHT_CHARS:]
    body = _markdown(Bi.same(optimize_markdown_style(text) or " "), "notation", element_id=_id(block.key + "t", view))
    return panel(title=title, elements=[body], expanded=_expanded(view, opts), element_id=_id(block.key, view),
                 vertical_spacing="8px")


_STATUS = {
    StepStatus.RUNNING: ("turquoise", "运行中", "Running"),
    StepStatus.OK: ("green", "成功", "Succeeded"),
    StepStatus.FAILED: ("red", "失败", "Failed"),
    StepStatus.UNCONFIRMED: ("orange", "结果未确认", "Unconfirmed"),
}


def _step_elements(step: Step) -> list[dict[str, Any]]:
    color, zh, en = _STATUS[step.status]
    timing = step_time(step.elapsed_ms)
    name = esc(clip(step.name, 40)) + (f" ({timing})" if timing else "")
    title_text: dict[str, Any] = {
        "tag": "lark_md",
        "content": f"**{name}** · <font color='{color}'>{en}</font>",
        "i18n_content": {"zh_cn": f"**{name}** · <font color='{color}'>{zh}</font>"},
        "text_size": "notation",
    }
    elements: list[dict[str, Any]] = [{
        "tag": "div",
        "icon": {"tag": "standard_icon", "token": step.icon, "color": "grey"},
        "text": title_text,
    }]
    if step.summary:
        elements.append({
            "tag": "div",
            "margin": "0px 0px 0px 22px",
            "text": {"tag": "plain_text", "content": clip(step.summary, _DETAIL_CHARS), "text_color": "grey",
                     "text_size": "notation"},
        })
    if step.status is StepStatus.FAILED and step.error:
        error = fence(clip(step.error, _ERROR_CHARS, single_line=False))
        elements.append({
            "tag": "div",
            "margin": "0px 0px 0px 22px",
            "text": {"tag": "lark_md", "content": f"**Error**\n{error}",
                     "i18n_content": {"zh_cn": f"**错误**\n{error}"}, "text_size": "notation"},
        })
    return elements


_VISIBLE_STEPS = 8  # per tools panel; long runs otherwise exhaust the card's element budget


def _fold_steps(steps: tuple[Step, ...]) -> tuple[list[Step], list[Step]]:
    """``(shown, folded)``: the latest steps in full; earlier successes fold into one line, failures never do."""
    if len(steps) <= _VISIBLE_STEPS:
        return list(steps), []
    early, late = steps[:-_VISIBLE_STEPS], steps[-_VISIBLE_STEPS:]
    kept = [s for s in early if s.status is not StepStatus.OK]
    return [*kept, *late], [s for s in early if s.status is StepStatus.OK]


def _folded_steps_line(folded: list[Step]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for step in folded:
        counts[step.name] = counts.get(step.name, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    zh = " · ".join(f"{esc(clip(name, 24))} {n} 次" for name, n in top)
    en = " · ".join(f"{esc(clip(name, 24))} x{n}" for name, n in top)
    return _markdown(Bi(grey(f"… 前 {len(folded)} 步已完成 · {zh}"), grey(f"… {len(folded)} earlier steps · {en}")),
                     "notation")


def _tools_panel(block: Block, view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    count = len(block.steps)
    failed = sum(s.status is StepStatus.FAILED for s in block.steps)
    zh = f"🛠️ 工具执行 · {count} 步"
    en = f"🛠️ Tool use · {count} step{'s' if count != 1 else ''}"
    if block.elapsed_s and not block.open:
        zh += f" · ({duration(block.elapsed_s)})"
        en += f" · ({duration(block.elapsed_s)})"
    tail_zh = f" <font color='red'>· {failed} 失败</font>" if failed else ""
    tail_en = f" <font color='red'>· {failed} failed</font>" if failed else ""
    children: list[dict[str, Any]] = []
    shown, folded = _fold_steps(block.steps)
    if folded:
        children.append(_folded_steps_line(folded))
    for step in shown:
        children.extend(_step_elements(step))
    return panel(title=Bi(grey(zh) + tail_zh, grey(en) + tail_en),
                 elements=children or [_markdown(Bi.same(" "), "notation")],
                 expanded=_expanded(view, opts, failed=bool(failed)), element_id=_id(block.key, view))


_AGENT_STATUS = {
    AgentStatus.RUNNING: ("⏳", "turquoise", "运行中", "Running"),
    AgentStatus.OK: ("✅", "green", "完成", "Done"),
    AgentStatus.FAILED: ("❌", "red", "失败", "Failed"),
}


def _agent_elements(agent: Subagent) -> list[dict[str, Any]]:
    mark, color, zh, en = _AGENT_STATUS[agent.status]
    goal = esc(clip(agent.goal, 50)) or f"子代理 {agent.index}"
    head = f"{mark} **#{agent.index} {goal}**"
    elements: list[dict[str, Any]] = [_markdown(
        Bi(f"{head} · <font color='{color}'>{zh}</font>", f"{head} · <font color='{color}'>{en}</font>"), "notation",
    )]
    detail = [f"{agent.tools} 步"] if agent.tools else []
    if agent.elapsed_s and agent.elapsed_s >= 1:
        detail.append(duration(agent.elapsed_s, whole=agent.status is AgentStatus.RUNNING))
    if agent.status is AgentStatus.RUNNING and agent.recent:
        detail.append("最近 " + ", ".join(agent.recent))
    elif agent.summary:
        detail.append(agent.summary)
    if detail:
        elements.append({
            "tag": "div",
            "margin": "0px 0px 0px 22px",
            "text": {"tag": "plain_text", "content": clip(" · ".join(detail), _DETAIL_CHARS), "text_color": "grey",
                     "text_size": "notation"},
        })
    return elements


def _agents_panel(block: Block, view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    """One delegate_task batch: a row per subagent. It stays open while any of them still runs, even on a
    finished card (background subagents keep reporting after the parent turn ended)."""
    agents = block.agents
    running = sum(a.status is AgentStatus.RUNNING for a in agents)
    failed = sum(a.status is AgentStatus.FAILED for a in agents)
    done = len(agents) - running - failed
    zh, en = f"🔀 子代理 · {len(agents)} 个", f"🔀 Subagents · {len(agents)}"
    if running:
        zh += f" · {running} 运行中" + (f" · {done} 完成" if done else "")
        en += f" · {running} running" + (f" · {done} done" if done else "")
    else:
        longest = max((a.elapsed_s or 0.0 for a in agents), default=0.0)
        zh += " · 全部完成" if not failed else ""
        en += " · all done" if not failed else ""
        if longest:
            zh += f" · ({duration(longest)})"
            en += f" · ({duration(longest)})"
    tail_zh = f" <font color='red'>· {failed} 失败</font>" if failed else ""
    tail_en = f" <font color='red'>· {failed} failed</font>" if failed else ""
    children: list[dict[str, Any]] = []
    for agent in agents:
        children.extend(_agent_elements(agent))
    return panel(title=Bi(grey(zh) + tail_zh, grey(en) + tail_en),
                 elements=children or [_markdown(Bi.same(" "), "notation")],
                 expanded=bool(running) or _expanded(view, opts, failed=bool(failed)),
                 element_id=_id(block.key, view))


def _answer(block: Block, view: TurnView, opts: RenderOptions, *, streaming: bool) -> list[dict[str, Any]]:
    content = downgrade_tables(optimize_markdown_style(block.text))
    if streaming:
        return [{"tag": "markdown", "content": content or " ", "text_size": opts.text_size,
                 "element_id": _id(block.key, view)}]
    return [{"tag": "markdown", "content": chunk, "text_size": opts.text_size}
            for chunk in split_long_text(content) if chunk.strip()]


def _note_element(block: Block, view: TurnView) -> dict[str, Any]:
    """The user's mid-turn message, shown where it arrived so the answer after it reads in context; or, with a
    label, the reason a Hermes-generated turn (subagent results, a background process) exists."""
    text = esc(clip(redact(block.text), _NOTE_CHARS))
    if block.notice:
        return _markdown(Bi.same(grey(f"📣 {text}")), "notation", element_id=_id(block.key, view))
    if block.label:
        line = f"**{esc(block.label)}** {grey('· ' + text)}" if text else f"**{esc(block.label)}**"
        return _markdown(Bi.same(line), "notation", element_id=_id(block.key, view))
    return _markdown(Bi(f"{grey('💬 你补充 · ')}{text}", f"{grey('💬 You added: ')}{text}"), "notation",
                     element_id=_id(block.key, view))


def block_elements(view: TurnView, opts: RenderOptions, *, streaming: bool) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for block in view.blocks:
        if block.kind is BlockKind.ANSWER:
            if block.text.strip() or streaming:
                elements.extend(_answer(block, view, opts, streaming=streaming))
        elif block.kind is BlockKind.NOTE:
            if block.text.strip() or block.label:
                elements.append(_note_element(block, view))
        elif not opts.show_process:
            continue
        elif block.kind is BlockKind.THOUGHT and block.text.strip():
            elements.append(_thought_panel(block, view, opts))
        elif block.kind is BlockKind.TOOLS and block.steps:
            elements.append(_tools_panel(block, view, opts))
        elif block.kind is BlockKind.AGENTS and block.agents:
            elements.append(_agents_panel(block, view, opts))
    return elements


# --------------------------------------------------------------------------- live line, footer

def live_text(view: TurnView) -> Bi:
    clock = duration(view.elapsed_s, whole=True)
    running = next((s for s in reversed(view.steps) if s.status is StepStatus.RUNNING), None)
    if running is not None:
        zh, en = f"正在执行 {esc(clip(running.name, 40))}", f"Running {esc(clip(running.name, 40))}"
        if view.running_s is not None and view.running_s >= 5:  # a slow command: show how long it has run
            took = duration(view.running_s, whole=True)
            zh, en = f"{zh} · 已 {took}", f"{en} · {took}"
    else:
        zh, en = "处理中", "Working"
    counter = f"{view.finished_steps}/{view.total_steps}" if view.total_steps else ""
    parts_zh = [p for p in (zh, f"步骤 {counter}" if counter else "", clock) if p]
    parts_en = [p for p in (en, f"step {counter}" if counter else "", clock) if p]
    return Bi(grey("⏳ " + " · ".join(parts_zh)), grey("⏳ " + " · ".join(parts_en)))


def status_element(view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    return _markdown(live_text(view), "notation", element_id=STATUS_ID)


def interrupted_status() -> dict[str, Any]:
    """The live line of a card whose turn died with the gateway (killed, crashed, restarted mid-turn)."""
    text = Bi("<font color='orange'>⚠️ 本轮回复已中断 · 网关重启或异常退出 · 内容可能不完整 · 请重新发送</font>",
              "<font color='orange'>⚠️ Interrupted · the gateway restarted or exited · reply may be incomplete</font>")
    return _markdown(text, "notation", element_id=STATUS_ID)


def footer_text(view: TurnView) -> Bi:
    """The status line: ``✅ 已完成 · ⏱️ 2m 01s · 🛠️ 2 步 · ❗ 1 步失败``."""
    if view.phase is Phase.FAILED:
        head = ("<font color='red'>❌ **出错**</font>", "<font color='red'>❌ **Error**</font>")
    elif view.phase is Phase.STOPPED:
        head = ("🛑 **已停止**", "🛑 **Stopped**")
    else:
        head = ("✅ **已完成**", "✅ **Completed**")
    zh, en = [head[0]], [head[1]]
    clock = duration(view.elapsed_s)
    if clock:
        zh.append(f"⏱️ {clock}")
        en.append(f"⏱️ {clock}")
    if view.total_steps:
        zh.append(f"🛠️ {view.total_steps} 步")
        en.append(f"🛠️ {view.total_steps} step{'s' if view.total_steps != 1 else ''}")
    if view.failed_steps:
        zh.append(f"❗ <font color='red'>{view.failed_steps} 步失败</font>")
        en.append(f"❗ <font color='red'>{view.failed_steps} failed</font>")
    return Bi(" · ".join(zh), " · ".join(en))


def footer_chips(view: TurnView) -> list[Bi]:
    """``🤖 model``, ``🧠 effort``, ``📑 context``, ``⚡ cache``: the effort sits right after its model."""
    footer = view.footer
    chips: list[Bi] = []
    if footer.model:
        route = f" · {esc(clip(footer.route, 60))}" if footer.route else ""  # which subscription paid for it
        chips.append(Bi.same(f"🤖 {esc(clip(footer.model, 60))}{route}"))
    window = key_window(view)
    if window is not None:
        chips.append(window)
    if footer.reasoning:
        chips.append(Bi.same(f"🧠 {esc(footer.reasoning)}"))
    if footer.context_used is not None and footer.context_max:
        chips.append(Bi.same(f"📑 {compact(footer.context_used)}/{compact(footer.context_max)} "
                             f"({footer.context_used / footer.context_max:.0%})"))
    if footer.cache_hit is not None:
        permille = int(footer.cache_hit * 1000 + 1e-9)  # truncated like the details value, so both agree
        hit = f"{'≥' if footer.cache_hit_is_floor else ''}{permille // 10}.{permille % 10}%".replace(".0%", "%")
        chips.append(Bi(f"⚡ 缓存 {hit}", f"⚡ cache {hit}"))
    if footer.memories is not None:
        chips.append(Bi(f"📚 {footer.memories} 条记忆" if footer.memories else "📚 用了记忆",
                        f"📚 {footer.memories} memor{'y' if footer.memories == 1 else 'ies'}"
                        if footer.memories else "📚 memory used"))
    warning = quota_warning(view)
    if warning is not None:
        chips.append(warning)
    return chips


_ROLLING = "5小时"


def key_window(view: TurnView) -> Bi | None:
    """The 5-hour window of the key that served the turn (the route's last hop), e.g. ``⏳ 5小时 37%``;
    orange from 80%, red from 95%, so the switch to the next key or the fallback is no surprise."""
    route = view.footer.route
    section = next((s for s in view.sections if s.key == "accounts"), None)
    if not route or section is None:
        return None
    label = f"{route.split(' → ')[-1].strip()} · {_ROLLING}"
    metric = next((m for m in section.metrics if m.label == label and m.ratio is not None), None)
    if metric is None or metric.ratio is None:
        return None
    pct = f"{int(metric.ratio * 100)}%"
    color = "red" if metric.ratio >= 0.95 else "orange" if metric.ratio >= 0.8 else ""
    zh, en = f"⏳ {_ROLLING} {pct}", f"⏳ 5h {pct}"
    if color:
        zh, en = f"<font color='{color}'>{zh}</font>", f"<font color='{color}'>{en}</font>"
    return Bi(zh, en)


_DAYS = re.compile(r"剩 (\d+) 天")


def quota_warning(view: TurnView) -> Bi | None:
    """A coloured chip when the subscription that served this turn is nearly used up.

    Several accounts of one provider rotate (Hermes' credential pool moves on when one is exhausted), so only
    the account with the most headroom counts: one account at 97% beside a fresh one is no reason to worry.
    A fixed-period subscription without a quota API warns in its last three days instead.
    """
    section = next((s for s in view.sections if s.key == "accounts"), None)
    if section is None:
        return None
    worst_window: dict[str, float] = {}
    days_left: int | None = None
    for metric in section.metrics:
        account, _, window = metric.label.rpartition(" · ")
        if window == "订阅期":
            match = _DAYS.match(metric.value)
            days_left = int(match.group(1)) if match else 0 if metric.value == "已到期" else days_left
        elif metric.ratio is not None:
            worst_window[account] = max(worst_window.get(account, 0.0), metric.ratio)
    if worst_window:
        headroom = min(worst_window.values())
        if headroom >= 0.9:
            color = "red" if headroom >= 0.97 else "orange"
            pct = f"{int(headroom * 100)}%"
            return Bi(f"<font color='{color}'>⚠️ 额度 {pct}</font>", f"<font color='{color}'>⚠️ quota {pct}</font>")
    if days_left is not None and days_left <= 3:
        color = "red" if days_left <= 1 else "orange"
        text = "订阅已到期" if days_left == 0 else f"订阅剩 {days_left} 天"
        return Bi(f"<font color='{color}'>⏳ {text}</font>", f"<font color='{color}'>⏳ {days_left}d left</font>")
    return None


def _footer_elements(view: TurnView) -> list[dict[str, Any]]:
    """The status line, then the meta chips as ``flow`` columns: a narrow card moves a whole chip to the
    next row, where free text would break a Chinese word in two."""
    status = _markdown(footer_text(view), "notation")
    status["margin"] = _PANEL_INSET
    out = [status]
    chips = footer_chips(view)
    if chips:
        columns = [{"tag": "column", "width": "auto", "vertical_align": "center",
                    "elements": [_markdown(c if c.zh.startswith("<font") else Bi(grey(c.zh), grey(c.en)), "notation")]}
                   for c in chips]  # a warning chip keeps its own colour
        out.append({"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "12px", "margin": _PANEL_INSET,
                    "columns": columns})
    return out


# --------------------------------------------------------------------------- details panel (finished)

_SECTION_NAMES = {"usage": "用量", "resources": "资源", "accounts": "额度"}
_ROW_EMOJI = {"本轮": "📊", "用量": "📊", "累计": "🪙", "模型累计": "🤖", "资源": "🖥️"}
_WINDOW_EMOJI = {"5小时": "⏱️", "每周": "📅", "每月": "🗓️"}


def known(section: Section) -> Section:
    """Drop what is not known: a metric whose value is unknown, an unknown hint, and notes that say so."""
    metrics = tuple(
        replace(m, hint="" if _UNKNOWN in m.hint else m.hint) for m in section.metrics if m.value != _UNKNOWN
    )
    notes = tuple(n for n in section.notes if _UNKNOWN not in n)
    return replace(section, metrics=metrics, notes=notes)


_BAR_CELLS = 8


def _level_color(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    return "red" if ratio >= 0.8 else "orange" if ratio >= 0.5 else None


def _value(metric: Metric, *, colour: bool = False) -> str:
    color = _level_color(metric.ratio) if colour else None
    text = esc(metric.value)
    return f"**<font color='{color}'>{text}</font>**" if color else f"**{text}**"


def _cell(metric: Metric) -> dict[str, Any]:
    """One grid cell: grey label over the bold value."""
    label = esc(metric.label)
    hint = f" {grey(esc(metric.hint))}" if metric.hint else ""
    return {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top",
            "elements": [_markdown(Bi.same(f"{grey(label)}\n{_value(metric)}{hint}"), "notation")]}


def _grid(metrics: list[Metric], columns: int) -> dict[str, Any]:
    """Equal cells in one row; on a narrow screen Feishu regroups them three per row (``trisect``)
    instead of squeezing the row, so a label never breaks away from its value. Every grid in the panel
    has the same column count (empty cells pad the short ones), so the columns line up from row to row."""
    cells = [_cell(m) for m in metrics]
    blank = {"tag": "column", "width": "weighted", "weight": 1,  # Feishu drops a column with no elements
             "elements": [{"tag": "markdown", "content": "\u200b", "text_size": "notation"}]}
    if -(-len(cells) // 3) == -(-columns // 3):  # pad only when a narrow screen gets no extra, empty row
        cells += [blank] * (columns - len(cells))
    return {"tag": "column_set", "flex_mode": "trisect", "horizontal_spacing": "8px", "margin": "0px",
            "columns": cells}


def _groups(section: Section) -> dict[str, list[Metric]]:
    groups: dict[str, list[Metric]] = {}
    for metric in section.metrics:
        groups.setdefault(metric.group or section.title.split(" · ")[0], []).append(metric)
    return groups


def _bar(ratio: float) -> str:
    ratio = min(max(ratio, 0.0), 1.0)
    filled = round(ratio * _BAR_CELLS)
    color = _level_color(ratio) or "blue"
    return f"<font color='{color}'>{'▓' * filled}</font>{grey('░' * (_BAR_CELLS - filled))}"


def _wide(label: str) -> str:
    """ASCII digits as full-width ones, so ``5小时`` is as wide as ``每周`` plus one ideographic space."""
    return label.translate({ord(c): ord(c) + 0xFEE0 for c in "0123456789"})


def _note(lines: list[str]) -> str:
    return grey(" · ".join(esc(n) for n in dict.fromkeys(lines) if n))


def _section_elements(section: Section, columns: int) -> list[dict[str, Any]]:
    """Grid sections: a heading line, then a cell grid per group. Quota: one short bar line per window."""
    out: list[dict[str, Any]] = []
    if section.layout == "bars":
        # Several accounts of one provider arrive as "Go a22 · 5小时": a heading line per account, then
        # its windows by their own names, so every bar still starts at the same x.
        split = [m.label.rsplit(" · ", 1) if " · " in m.label else ["", m.label] for m in section.metrics]
        names = [_wide(window) for _account, window in split]
        width = max(len(n) for n in names)
        value_width = max(len(m.value) for m in section.metrics)
        lines, current = [], None
        for m, (account, window), name in zip(section.metrics, split, names, strict=True):
            if account and account != current:
                lines.append(f"**{esc(account)}**")
                current = account
            emoji = _WINDOW_EMOJI.get(window, "🎫")
            pad = "\u3000" * (width - len(name))  # ideographic spaces: every bar starts at the same x
            vpad = "\u2007" * (value_width - len(m.value))  # figure spaces: the reset hints line up too
            m = replace(m, label=name)
            bar = _bar(m.ratio) + " " if m.ratio is not None else ""
            hint = f" {grey('· ' + esc(m.hint))}" if m.hint else ""
            lines.append(f"{emoji} **{esc(m.label)}**{pad} {bar}{vpad}{_value(m, colour=True)}{hint}")
        note = _note([section.title.replace(" · 订阅与额度", ""), *section.notes])
        text = "\n".join(lines) + (f"\n{note}" if note else "")
        return [_markdown(Bi.same(text), "notation")]
    for heading, metrics in _groups(section).items():
        emoji = _ROW_EMOJI.get(heading, "")
        out.append(_markdown(Bi.same(f"{emoji} **{esc(heading)}**".strip()), "notation"))
        out.append(_grid(metrics, columns))
    if section.notes:
        out.append(_markdown(Bi.same(_note(list(section.notes))), "notation"))
    return out


def details_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    if not opts.show_details or view.phase is Phase.RUNNING or view.continued:
        return None
    parts: list[list[dict[str, Any]]] = []
    names: list[str] = []
    sections = [s for s in (known(s) for s in view.sections) if s.metrics]
    columns = max((len(g) for s in sections if s.layout != "bars" for g in _groups(s).values()), default=0)
    for section in sections:
        parts.append(_section_elements(section, columns))
        names.append(_SECTION_NAMES.get(section.key, section.title.split(" · ")[0]))
    if view.notices:
        parts.append([_markdown(Bi.same("📝 **后台复盘**\n" + "\n".join(esc(n) for n in view.notices)), "notation")])
        names.append("后台复盘")
    if not parts:
        return None
    children: list[dict[str, Any]] = []
    for index, part in enumerate(parts):
        if index:
            children.append({"tag": "hr"})
        children.extend(part)
    title = Bi(grey("📊 详情 · " + " · ".join(names)), grey("📊 Details · " + " · ".join(names)))
    return panel(title=title, elements=children, expanded=False, element_id=_id(DETAILS_ID, view),
                 vertical_spacing="6px")


# --------------------------------------------------------------------------- cards

_EMPTY_ANSWER = {
    Phase.DONE: Bi("完成。", "Done."),
    Phase.FAILED: Bi("本轮失败,没有可显示的回答。", "The turn failed before producing an answer."),
    Phase.STOPPED: Bi("已停止。", "Stopped."),
}


def _config(opts: RenderOptions, *, streaming: bool, summary: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "width_mode": opts.width_mode,
        "update_multi": True,
        "locales": LOCALES,
        "summary": {"content": summary or "…"},
    }
    if streaming:
        config["streaming_mode"] = True
        config["streaming_config"] = _STREAMING_CONFIG
    return config


def _summary(view: TurnView) -> str:
    for block in reversed(view.blocks):
        if block.kind is BlockKind.ANSWER:
            plain = " ".join(block.text.replace("```", " ").split())
            if plain:
                return plain[:_SUMMARY_CHARS]
    return {Phase.FAILED: "本轮失败", Phase.STOPPED: "已停止"}.get(view.phase, "处理中…")


def streaming_elements(view: TurnView, opts: RenderOptions) -> list[dict[str, Any]]:
    """Every element of the live card, in order; all carry ids so the pipeline can diff them."""
    todo = todo_element(view, streaming=True)
    return [*block_elements(view, opts, streaming=True), *([todo] if todo else []), status_element(view, opts),
            stop_button()]


def stop_button() -> dict[str, Any]:
    """Hermes' Feishu adapter turns the click into ``/card button {"lark_card": "stop"}``; the plugin's inbound
    hook rewrites that to ``/stop``, which Hermes honours even while the turn is busy."""
    return {
        "tag": "button",
        "element_id": STOP_ID,
        "text": {"tag": "plain_text", "content": "⏹ 停止", "i18n_content": {"en_us": "⏹ Stop"}},
        "type": "default",
        "size": "small",
        "behaviors": [{"type": "callback", "value": {"lark_card": "stop"}}],
    }


_TODO_MARK = {"completed": "✅", "in_progress": "⏳", "cancelled": "🚫", "pending": "⬜"}
_TODO_LIMIT = 20


def todo_id(view: TurnView) -> str:
    return _id(TODO_ID, view)


def todo_element(view: TurnView, *, streaming: bool) -> dict[str, Any] | None:
    """``📋 任务清单 · 3/7``: the agent's own plan, so a long task shows where it stands."""
    items = [t for t in view.todos if t.content.strip()]
    if not items:
        return None
    done = sum(t.status == "completed" for t in items)
    active = sum(t.status != "cancelled" for t in items)
    lines = []
    for item in items[:_TODO_LIMIT]:
        text = esc(clip(item.content, 80))
        if item.status == "completed" or item.status == "cancelled":
            text = grey(f"~~{text}~~")
        elif item.status == "in_progress":
            text = f"**{text}**"
        lines.append(("　" if item.child else "") + f"{_TODO_MARK.get(item.status, '⬜')} {text}")
    if len(items) > _TODO_LIMIT:
        lines.append(grey(f"… 另有 {len(items) - _TODO_LIMIT} 项"))
    body = _markdown(Bi.same("\n".join(lines)), "notation")
    title = Bi(grey(f"📋 任务清单 · {done}/{active} 完成"), grey(f"📋 Tasks · {done}/{active} done"))
    return panel(title=title, elements=[body], expanded=streaming, element_id=todo_id(view))


def render_streaming(view: TurnView, opts: RenderOptions | None = None) -> dict[str, Any]:
    """Live card for CardKit creation; the pipeline then inserts and patches elements by id."""
    opts = opts or RenderOptions()
    return {
        "schema": "2.0",
        "config": _config(opts, streaming=True, summary="处理中…"),
        "body": {"elements": streaming_elements(view, opts)},
    }


def render_final(view: TurnView, opts: RenderOptions | None = None) -> dict[str, Any]:
    """Terminal card for a full-card update (also used for a sealed, rolled-over card)."""
    opts = opts or RenderOptions()
    elements = block_elements(view, opts, streaming=False)
    todo = todo_element(view, streaming=False)
    if todo is not None and not view.continued:
        elements.append(todo)
    if view.outputs and not view.continued:
        names = " · ".join(esc(clip(name, 40)) for name in view.outputs[:8])
        more = f" · 等 {len(view.outputs)} 个" if len(view.outputs) > 8 else ""
        elements.append(_markdown(Bi(grey(f"📎 产出 · {names}{more}"), grey(f"📎 Files · {names}")), "notation"))
    if view.continued:
        elements.append(_markdown(Bi(grey("↪ 内容见下一张卡片"), grey("↪ Continued on the next card")), "notation"))
    else:
        if not any(b.kind is BlockKind.ANSWER and b.text.strip() for b in view.blocks):
            phase = view.phase if view.phase.terminal else Phase.DONE
            elements.append(_markdown(_EMPTY_ANSWER[phase], opts.text_size))
        elements.append({"tag": "hr"})
        elements.extend(_footer_elements(view))
        details = details_element(view, opts)
        if details:
            elements.append(details)
    return {
        "schema": "2.0",
        "config": _config(opts, streaming=False, summary=_summary(view)),
        "body": {"elements": elements},
    }


_PANEL_FIELDS = ("header", "elements", "border")


def partial_for(element: dict[str, Any], *, reset_state: bool = False) -> dict[str, Any]:
    """Fields for a ``partial_update_element``; panels keep the reader's expanded state unless ``reset_state``."""
    if element.get("tag") == "collapsible_panel":
        fields = {k: element[k] for k in _PANEL_FIELDS if k in element}
        if reset_state:
            fields["expanded"] = element["expanded"]
        return fields
    return {k: element[k] for k in ("content", "i18n_content") if k in element}


def count_elements(node: Any) -> int:
    """Count tagged nodes the way CardKit does for its element cap."""
    if isinstance(node, dict):
        own = 1 if "tag" in node else 0
        return own + sum(count_elements(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_elements(v) for v in node)
    return 0
