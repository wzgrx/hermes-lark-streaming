"""Card 2.0 renderer (v2): ``TurnView`` in, card JSON out. Pure functions, no I/O.

Layout, top to bottom (see docs/design/card-redesign.html):

    header             native title bar: agent name, state tag, subtitle; its colour is the state
    live line          running only: what is happening now, step counter, clock
    failures           finished only, when a step failed: each failure with its command and output
    answer             the body; streamed through a single element while running
    meta chips         finished only: model, context, cache hit, quota warning
    details panel      finished only, collapsed: steps, reasoning, usage, resources, quota, reviews
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from .markdown import downgrade_tables, optimize_markdown_style, split_long_text
from .model import Footer, Metric, Phase, RenderOptions, Section, Step, StepStatus, TurnView
from .redact import redact

STATUS_ID = "live"  # the live line; patched while running
PROCESS_ID = "process"  # reserved: v2 has no live process panel
ANSWER_ID = "answer"
FOOTER_ID = "meta"  # reserved: chips only appear on the finished card
DETAILS_ID = "details"

ELEMENT_LIMIT = 200  # CardKit hard cap on elements per card, counted over every nested tag
LOCALES = ["zh_cn", "en_us", "ja_jp", "ko_kr"]
_STREAMING_CONFIG = {
    "print_frequency_ms": {"default": 15},
    "print_step": {"default": 1},
    "print_strategy": "fast",
}
_THOUGHT_CHARS = 1200
_SUMMARY_CHARS = 120
_ERROR_CHARS = 600
_STEP_SUMMARY_CHARS = 90
_MAX_FAILURES = 3
_UNKNOWN = "未知"


@dataclass(frozen=True, slots=True)
class Bi:
    """A zh/en pair. The English text is the card default; zh_cn overrides it per locale."""

    zh: str
    en: str

    @classmethod
    def same(cls, text: str) -> Bi:
        return cls(text, text)

    def __add__(self, other: Bi | str) -> Bi:
        if isinstance(other, str):
            return Bi(self.zh + other, self.en + other)
        return Bi(self.zh + other.zh, self.en + other.en)


def _markdown(text: Bi, size: str, *, element_id: str | None = None) -> dict[str, Any]:
    element: dict[str, Any] = {
        "tag": "markdown",
        "content": text.en,
        "text_size": size,
        "text_align": "left",
        "margin": "0px",
    }
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


def code(value: str) -> str:
    """Inline code span; text with markup characters stays escaped plain text, since entities would show literally."""
    if not value or any(ch in value for ch in "<>&`"):
        return esc(value.replace("`", "'"))
    return f"`{value}`"


def fence(value: str) -> str:
    """A fenced code block that its own content cannot close."""
    longest = max((len(run) for run in value.split("\n") if set(run) == {"`"}), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}\n{value}\n{ticks}"


def clip(value: str, limit: int, *, single_line: bool = True) -> str:
    """Redact, fold whitespace, and cut at ``limit`` characters (escaping happens afterwards)."""
    text = redact(value)
    text = " ".join(text.split()) if single_line else text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def grey(text: str) -> str:
    return f"<font color='grey'>{text}</font>"


def chip(text: str, color: str = "neutral") -> str:
    return f"<text_tag color='{color}'>{text}</text_tag>"


def duration(seconds: float | None, *, whole: bool = False) -> str:
    """``14.4s`` / ``2m05s`` / ``1h02m``; ``whole`` drops the decimal for a live clock."""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 60:
        return f"{int(seconds)}s" if whole else f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def step_time(ms: float | None) -> str:
    if ms is None:
        return ""
    return f"{ms:.0f}ms" if ms < 1000 else duration(ms / 1000)


def compact(value: int) -> str:
    if value >= 1_000_000:
        n = value / 1_000_000
        return f"{n:.0f}M" if n == int(n) else f"{n:.1f}M"
    if value >= 1_000:
        n = value / 1_000
        return f"{n:.0f}k" if n >= 100 else f"{n:.1f}k"
    return str(value)


def _panel_id(base: str, view: TurnView) -> str:
    """Per-card id: Feishu remembers a panel's expanded state by id, so reuse would leak it across cards."""
    return f"{base}_{view.card_key}"[:20] if view.card_key else base


# --------------------------------------------------------------------------- header

_STATE = {
    # key: (template colour, tag colour, zh tag, en tag)
    "running": ("blue", "blue", "运行中", "Running"),
    "done": ("green", "green", "已完成", "Done"),
    "step_failed": ("red", "red", "有失败", "Step failed"),
    "failed": ("red", "red", "失败", "Failed"),
    "stopped": ("grey", "neutral", "已停止", "Stopped"),
    "continued": ("grey", "neutral", "已分页", "Continued"),
}


def state_key(view: TurnView) -> str:
    if view.continued:
        return "continued"
    if view.phase is Phase.RUNNING:
        return "running"
    if view.phase is Phase.FAILED:
        return "failed"
    if view.phase is Phase.STOPPED:
        return "stopped"
    return "step_failed" if view.failed_steps else "done"


def _subtitle(view: TurnView, key: str) -> Bi:
    if key == "running":
        return Bi("处理中…", "Working…")
    if key == "continued":
        return Bi("内容见下一张卡片", "Continued on the next card")
    parts_zh: list[str] = []
    parts_en: list[str] = []
    clock = duration(view.elapsed_s)
    if clock:
        parts_zh.append(clock)
        parts_en.append(clock)
    if view.total_steps:
        parts_zh.append(f"{view.total_steps} 步")
        parts_en.append(f"{view.total_steps} steps")
    if view.failed_steps:
        parts_zh.append(f"{view.failed_steps} 步失败")
        parts_en.append(f"{view.failed_steps} failed")
    return Bi(" · ".join(parts_zh), " · ".join(parts_en))


def header(view: TurnView) -> dict[str, Any]:
    key = state_key(view)
    template, tag_color, zh, en = _STATE[key]
    title = clip(view.footer.tag, 30) if view.footer.tag else "Hermes"
    result: dict[str, Any] = {
        "title": _plain(Bi.same(title)),
        "text_tag_list": [{"tag": "text_tag", "text": _plain(Bi(zh, en)), "color": tag_color}],
        "template": template,
        "icon": {"tag": "standard_icon", "token": "robot_outlined"},
    }
    subtitle = _subtitle(view, key)
    if subtitle.zh:
        result["subtitle"] = _plain(subtitle)
    return result


# --------------------------------------------------------------------------- live line (running)

def live_text(view: TurnView) -> Bi:
    running = next((s for s in reversed(view.steps) if s.status is StepStatus.RUNNING), None)
    clock = duration(view.elapsed_s, whole=True)
    counter_zh = f"步骤 {view.finished_steps}/{view.total_steps}" if view.total_steps else ""
    counter_en = f"step {view.finished_steps}/{view.total_steps}" if view.total_steps else ""
    if running is not None:
        target = f"**{esc(clip(running.name, 40))}**"
        if running.summary:
            target += f" {code(clip(running.summary, 60))}"
        head = Bi(f"正在执行 {target}", f"Running {target}")
    elif view.answers:
        head = Bi("正在回答", "Answering")
    elif view.thoughts:
        head = Bi("正在思考", "Thinking")
    else:
        head = Bi("正在处理", "Working")
    tail_zh = " · ".join(t for t in (counter_zh, clock) if t)
    tail_en = " · ".join(t for t in (counter_en, clock) if t)
    dot = "<font color='blue'>◌</font> "
    return Bi(dot + head.zh + (f" {grey('· ' + tail_zh)}" if tail_zh else ""),
              dot + head.en + (f" {grey('· ' + tail_en)}" if tail_en else ""))


def status_element(view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    """The live line while running; on a sealed (continued) card a one-line pointer to the next card."""
    if view.continued:
        return _markdown(Bi(grey("内容见下一张卡片"), grey("Continued on the next card")), "notation",
                         element_id=STATUS_ID)
    return _markdown(live_text(view), "notation", element_id=STATUS_ID)


def process_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    """v2 shows no live process panel; steps live in the finished card's details panel."""
    return None


def footer_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    """v2 shows the meta chips only on the finished card."""
    return None


# --------------------------------------------------------------------------- failures (finished)

def _failure_element(step: Step, size: str) -> dict[str, Any]:
    head = f"<font color='red'>✕</font> **{esc(clip(step.name, 40))}**"
    if step.summary:
        head += f" {code(clip(step.summary, _STEP_SUMMARY_CHARS))}"
    timing = step_time(step.elapsed_ms)
    if timing:
        head += f" {grey(timing)}"
    children = [_markdown(Bi.same(head), size)]
    if step.error:
        children.append(_markdown(Bi.same(fence(clip(step.error, _ERROR_CHARS, single_line=False))), "notation"))
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "red-50",
        "horizontal_spacing": "0px",
        "margin": "0px",
        "columns": [{"tag": "column", "width": "weighted", "weight": 1, "vertical_spacing": "4px",
                     "padding": "8px 10px 8px 10px", "elements": children}],
    }


def failure_elements(view: TurnView, opts: RenderOptions) -> list[dict[str, Any]]:
    if not opts.show_process:
        return []
    failed = [s for s in view.steps if s.status is StepStatus.FAILED]
    elements = [_failure_element(s, opts.text_size) for s in failed[-_MAX_FAILURES:]]
    hidden = len(failed) - len(elements) + view.steps_before_failed
    if hidden > 0:
        elements.append(_markdown(Bi(grey(f"另有 {hidden} 步失败,见下方过程"),
                                     grey(f"{hidden} more failed steps in the process below")), "notation"))
    return elements


# --------------------------------------------------------------------------- meta chips (finished)

def _quota_chip(view: TurnView) -> str:
    """The most constrained quota window, only once it is worth a glance (≥ 50 %)."""
    worst: Metric | None = None
    for section in view.sections:
        if section.key != "accounts":
            continue
        for m in section.metrics:
            if m.ratio is not None and (worst is None or m.ratio > (worst.ratio or 0)):
                worst = m
    if worst is None or (worst.ratio or 0) < 0.5:
        return ""
    color = "red" if (worst.ratio or 0) >= 0.8 else "orange"
    return chip(f"额度 {esc(worst.label)} {esc(worst.value)}", color)


def meta_text(footer: Footer, view: TurnView) -> str:
    chips: list[str] = []
    if footer.model:
        chips.append(chip(esc(clip(footer.model, 60))))
    if footer.context_used is not None and footer.context_max:
        ratio = footer.context_used / footer.context_max
        color = "red" if ratio >= 0.85 else "orange" if ratio >= 0.6 else "neutral"
        chips.append(chip(f"上下文 {compact(footer.context_used)}/{compact(footer.context_max)} · {ratio:.0%}", color))
    if footer.cache_hit is not None:
        sign = "≥" if footer.cache_hit_is_floor else ""
        chips.append(chip(f"缓存 {sign}{footer.cache_hit:.0%}", "green" if footer.cache_hit >= 0.5 else "neutral"))
    quota = _quota_chip(view)
    if quota:
        chips.append(quota)
    if footer.partial:
        chips.append(chip("用量不完整"))
    return " ".join(chips)


def meta_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    text = meta_text(view.footer, view)
    return _markdown(Bi.same(text), "notation") if text else None


# --------------------------------------------------------------------------- details panel (finished)

_STEP_ICON = {
    StepStatus.OK: "<font color='green'>✓</font>",
    StepStatus.RUNNING: "<font color='blue'>◌</font>",
    StepStatus.FAILED: "<font color='red'>✕</font>",
    StepStatus.UNCONFIRMED: "<font color='orange'>?</font>",
}


def _step_line(step: Step) -> str:
    parts = [_STEP_ICON[step.status], f"**{esc(clip(step.name, 40))}**"]
    if step.summary:
        parts.append(code(clip(step.summary, _STEP_SUMMARY_CHARS)))
    timing = step_time(step.elapsed_ms)
    if timing:
        parts.append(grey(timing))
    if step.status is StepStatus.UNCONFIRMED:
        parts.append(grey("结果未确认"))
    return " ".join(parts)


def _visible_steps(steps: tuple[Step, ...], limit: int) -> tuple[list[Step], int]:
    """Keep every running/failed/unconfirmed step, then the newest ordinary ones up to ``limit``."""
    if len(steps) <= limit:
        return list(steps), 0
    keep = {i for i, s in enumerate(steps) if s.status is not StepStatus.OK}
    for i in range(len(steps) - 1, -1, -1):
        if len(keep) >= limit:
            break
        keep.add(i)
    shown = [s for i, s in enumerate(steps) if i in keep]
    return shown, len(steps) - len(shown)


def known(section: Section) -> Section:
    """Drop what is not known: a metric whose value is unknown, an unknown hint, and notes that say so."""
    metrics = tuple(
        replace(m, hint="" if _UNKNOWN in m.hint else m.hint) for m in section.metrics if m.value != _UNKNOWN
    )
    notes = tuple(n for n in section.notes if _UNKNOWN not in n)
    return replace(section, metrics=metrics, notes=notes)


def _bar(ratio: float) -> str:
    ratio = min(max(ratio, 0.0), 1.0)
    filled = round(ratio * 10)
    color = "red" if ratio >= 0.8 else "orange" if ratio >= 0.5 else "blue"
    return f"<font color='{color}'>{'▓' * filled}</font>{grey('░' * (10 - filled))}"


def _inline(metrics: Iterable[Metric]) -> str:
    """``label **value** · label **value**`` on one line, two-per-line for long lists."""
    cells = [f"{esc(m.label)} **{esc(m.value)}**" + (f" {grey(esc(m.hint))}" if m.hint else "") for m in metrics]
    lines = [" · ".join(cells[i:i + 4]) for i in range(0, len(cells), 4)]
    return "\n".join(lines)


def _bars(metrics: Iterable[Metric]) -> str:
    rows = []
    for m in metrics:
        bar = _bar(m.ratio) + " " if m.ratio is not None else ""
        hint = " " + grey(esc(m.hint)) if m.hint else ""
        rows.append(f"{esc(m.label)}　{bar}**{esc(m.value)}**{hint}")
    return "\n".join(rows)


def _section_block(title: str, body: str, notes: Iterable[str], size: str) -> list[dict[str, Any]]:
    text = f"**{esc(title)}**"
    if body:
        text += "\n" + body
    note_line = " · ".join(esc(n) for n in dict.fromkeys(notes) if n)
    if note_line:
        text += "\n" + grey(note_line)
    return [_markdown(Bi.same(text), size)]


_SECTION_NAMES = {"usage": "用量", "resources": "资源", "accounts": "额度"}


def details_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    if not opts.show_details or view.phase is Phase.RUNNING or view.continued:
        return None
    size = "notation"
    blocks: list[list[dict[str, Any]]] = []
    names: list[str] = []
    if opts.show_process and (view.steps or view.steps_before):
        shown, hidden = _visible_steps(view.steps, opts.max_steps)
        lines = [_step_line(s) for s in shown]
        earlier = view.steps_before + hidden
        if earlier:
            lines.insert(0, grey(f"另有 {earlier} 步较早的步骤未列出"))
        blocks.append(_section_block("过程", "\n".join(lines), (), size))
        names.append(f"{view.total_steps} 步")
    if view.thoughts:
        text = view.thoughts.strip()
        if len(text) > _THOUGHT_CHARS:
            text = "…" + text[-_THOUGHT_CHARS:]
        blocks.append(_section_block("思考", grey(esc(text)), (), size))
        names.append("思考")
    for section in (known(s) for s in view.sections):
        if not section.metrics:
            continue
        body = _bars(section.metrics) if section.layout == "bars" else _inline(section.metrics)
        blocks.append(_section_block(section.title, body, section.notes, size))
        names.append(_SECTION_NAMES.get(section.key, section.title.split(" · ")[0]))
    if view.notices:
        blocks.append(_section_block("后台复盘", "\n".join(esc(n) for n in view.notices), (), size))
        names.append("后台复盘")
    if not blocks:
        return None
    children: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if index:
            children.append({"tag": "hr", "margin": "2px 0px 2px 0px"})
        children.extend(block)
    title = Bi("**过程与详情** " + grey("· " + " · ".join(names)), "**Details** " + grey("· " + " · ".join(names)))
    return panel(title=title, elements=children, expanded=False, element_id=_panel_id(DETAILS_ID, view))


def panel(*, title: Bi, elements: list[dict[str, Any]], expanded: bool, element_id: str,
          border: str = "grey") -> dict[str, Any]:
    title_el: dict[str, Any] = {"tag": "markdown", "content": title.en}
    if title.zh != title.en:
        title_el["i18n_content"] = {"zh_cn": title.zh}
    return {
        "tag": "collapsible_panel",
        "element_id": element_id,
        "expanded": expanded,
        "header": {
            "title": title_el,
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "size": "16px 16px", "color": "grey"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "border": {"color": border, "corner_radius": "8px"},
        "vertical_spacing": "8px",
        "padding": "8px 10px 8px 10px",
        "elements": elements,
    }


# --------------------------------------------------------------------------- answer

_EMPTY_ANSWER = {
    Phase.DONE: Bi("完成。", "Done."),
    Phase.FAILED: Bi("本轮失败,没有可显示的回答。", "The turn failed before producing an answer."),
    Phase.STOPPED: Bi("已停止。", "Stopped."),
}


def answer_elements(view: TurnView, opts: RenderOptions) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for text in view.answers:
        content = downgrade_tables(optimize_markdown_style(text))
        elements.extend(
            {"tag": "markdown", "content": chunk, "text_size": opts.text_size}
            for chunk in split_long_text(content) if chunk.strip()
        )
    if not elements and view.phase.terminal and not view.continued:
        elements.append(_markdown(_EMPTY_ANSWER[view.phase], opts.text_size))
    return elements


def streaming_answer_element(text: str, opts: RenderOptions) -> dict[str, Any]:
    return {
        "tag": "markdown", "content": text, "text_size": opts.text_size,
        "text_align": "left", "margin": "0px", "element_id": ANSWER_ID,
    }


# --------------------------------------------------------------------------- cards

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
    for text in reversed(view.answers):
        plain = " ".join(text.replace("```", " ").split())
        if plain:
            return plain[:_SUMMARY_CHARS]
    return {Phase.FAILED: "本轮失败", Phase.STOPPED: "已停止"}.get(view.phase, "处理中…")


def render_streaming(view: TurnView, opts: RenderOptions | None = None) -> dict[str, Any]:
    """Initial card for CardKit creation. Later updates target the live line and the answer."""
    opts = opts or RenderOptions()
    elements = [status_element(view, opts), streaming_answer_element("\n\n".join(view.answers), opts)]
    return {
        "schema": "2.0",
        "config": _config(opts, streaming=True, summary="处理中…"),
        "header": header(view),
        "body": {"elements": elements},
    }


def render_final(view: TurnView, opts: RenderOptions | None = None) -> dict[str, Any]:
    """Terminal card for a full-card update (also used for a sealed, rolled-over card)."""
    opts = opts or RenderOptions()
    elements: list[dict[str, Any]] = []
    if view.continued:
        elements.append(status_element(view, opts))
    else:
        elements.extend(failure_elements(view, opts))
    elements.extend(answer_elements(view, opts))
    if not view.continued:
        for build in (meta_element, details_element):
            element = build(view, opts)
            if element:
                elements.append(element)
    return {
        "schema": "2.0",
        "config": _config(opts, streaming=False, summary=_summary(view)),
        "header": header(view),
        "body": {"elements": elements},
    }


_PANEL_FIELDS = ("header", "elements", "border")


def partial_for(element: dict[str, Any], *, reset_state: bool = False) -> dict[str, Any]:
    """Fields for a ``partial_update_element`` of ``element``.

    Panels keep the reader's expanded/collapsed choice unless ``reset_state``. Markdown elements update
    their text only.
    """
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
