"""Card 2.0 renderer: ``TurnView`` in, card JSON out. Pure functions, no I/O.

Layout, top to bottom (see docs/design/card-redesign.html):

    status line        one line: dot, state, step counter, elapsed
    process panel      tools and reasoning, one collapsible panel
    answer             the body; streamed through a single element while running
    footer             one grey line: model, context, cache, identity tag
    details panel      usage / resources / accounts, only after the turn ends
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .markdown import downgrade_tables, optimize_markdown_style, split_long_text
from .model import Footer, Metric, Phase, RenderOptions, Step, StepStatus, TurnView
from .redact import redact

STATUS_ID = "status"
PROCESS_ID = "process"
ANSWER_ID = "answer"
FOOTER_ID = "footer"
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
_ERROR_CHARS = 240
_STEP_SUMMARY_CHARS = 90


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


def clip(value: str, limit: int, *, single_line: bool = True) -> str:
    """Redact, fold whitespace, and cut at ``limit`` characters (escaping happens afterwards)."""
    text = redact(value)
    text = " ".join(text.split()) if single_line else text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def grey(text: str) -> str:
    return f"<font color='grey'>{text}</font>"


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


# --------------------------------------------------------------------------- status

def _dot(color: str) -> str:
    return f"<font color='{color}'>●</font>"


def _line(color: str, label: Bi, bits: Iterable[Bi]) -> Bi:
    """``● label  grey(bit · bit)``; empty bits are skipped."""
    parts = [b for b in bits if b.zh or b.en]
    zh = " · ".join(b.zh for b in parts)
    en = " · ".join(b.en for b in parts)
    return Bi(f"{_dot(color)} {label.zh}" + (f" {grey(zh)}" if zh else ""),
              f"{_dot(color)} {label.en}" + (f" {grey(en)}" if en else ""))


def status_text(view: TurnView) -> Bi:
    if view.continued:
        return _line("grey", Bi("**已分页**", "**Continued**"), [Bi("内容见下一张卡片", "see the next card")])
    clock = Bi.same(duration(view.elapsed_s, whole=view.phase is Phase.RUNNING))
    steps = view.total_steps
    if view.phase is Phase.RUNNING:
        done = view.finished_steps
        counter = Bi(f"步骤 {done}/{steps}", f"step {done}/{steps}") if steps else Bi("", "")
        return _line("blue", Bi("**运行中**", "**Running**"), [counter, clock])
    if view.phase is Phase.FAILED:
        return _line("red", Bi("**<font color='red'>本轮失败</font>**", "**<font color='red'>Failed</font>**"), [clock])
    if view.phase is Phase.STOPPED:
        return _line("grey", Bi("**已停止**", "**Stopped**"), [clock])
    failed = view.failed_steps
    if failed:
        return _line(
            "red",
            Bi(f"**<font color='red'>{failed} 步失败</font>**", f"**<font color='red'>{failed} failed</font>**"),
            [Bi("回答已完成", "answer finished"), clock],
        )
    return _line("green", Bi("**已完成**", "**Done**"), [clock])


def status_element(view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    return _markdown(status_text(view), opts.text_size, element_id=STATUS_ID)


# --------------------------------------------------------------------------- process

_STEP_ICON = {
    StepStatus.OK: "<font color='green'>✓</font>",
    StepStatus.RUNNING: "<font color='blue'>◌</font>",
    StepStatus.FAILED: "<font color='red'>✕</font>",
    StepStatus.UNCONFIRMED: "<font color='orange'>?</font>",
}


def _step_line(step: Step) -> str:
    parts = [_STEP_ICON[step.status], f"**{esc(clip(step.name, 40))}**"]
    if step.summary:
        parts.append("· " + code(clip(step.summary, _STEP_SUMMARY_CHARS)))
    timing = step_time(step.elapsed_ms)
    if timing:
        parts.append(grey(timing))
    if step.status is StepStatus.UNCONFIRMED:
        parts.append(grey("结果未确认"))
    return " ".join(parts)


def _step_element(step: Step, size: str) -> dict[str, Any]:
    line = _step_line(step)
    if step.status is not StepStatus.FAILED:
        return _markdown(Bi.same(line), size)
    if step.error:
        line += "\n<font color='red'>" + esc(clip(step.error, _ERROR_CHARS, single_line=False)) + "</font>"
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "red-50",
        "horizontal_spacing": "0px",
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top",
            "elements": [_markdown(Bi.same(line), size)],
        }],
    }


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


def _process_open(view: TurnView, opts: RenderOptions) -> bool:
    if opts.process == "open":
        return True
    if opts.process == "closed":
        return False
    return view.phase is Phase.RUNNING or view.failed_steps > 0


def _process_title(view: TurnView) -> Bi:
    total = view.total_steps
    if not total:
        return Bi("**过程** " + grey("· 思考"), "**Process** " + grey("· thinking"))
    running = sum(s.status is StepStatus.RUNNING for s in view.steps)
    unconfirmed = sum(s.status is StepStatus.UNCONFIRMED for s in view.steps)
    failed = view.failed_steps
    if failed:
        return Bi(
            f"**过程** {grey(f'· {view.finished_steps}/{total} 步结束')} <font color='red'>· {failed} 失败</font>",
            f"**Process** {grey(f'· {view.finished_steps}/{total} steps ended')} "
            f"<font color='red'>· {failed} failed</font>",
        )
    if running and view.phase is Phase.RUNNING:
        return Bi(f"**过程** {grey(f'· {total} 步 · {running} 进行中')}",
                  f"**Process** {grey(f'· {total} steps · {running} running')}")
    if unconfirmed:
        return Bi(f"**过程** {grey(f'· {total} 步 · {unconfirmed} 未确认')}",
                  f"**Process** {grey(f'· {total} steps · {unconfirmed} unconfirmed')}")
    return Bi(f"**过程** {grey(f'· {total} 步 · 全部成功')}", f"**Process** {grey(f'· {total} steps · all ok')}")


def panel(*, title: Bi, elements: list[dict[str, Any]], expanded: bool, element_id: str,
          border: str = "grey") -> dict[str, Any]:
    title_el: dict[str, Any] = {"tag": "markdown", "content": title.en, "text_size": "notation"}
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
        "border": {"color": border, "corner_radius": "6px"},
        "vertical_spacing": "6px",
        "padding": "6px 8px 6px 8px",
        "elements": elements,
    }


def process_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    if not opts.show_process or not (view.steps or view.thoughts or view.steps_before):
        return None
    size = "notation"
    children: list[dict[str, Any]] = []
    if view.thoughts:
        text = view.thoughts.strip()
        if len(text) > _THOUGHT_CHARS:
            text = "…" + text[-_THOUGHT_CHARS:]
        children.append(_markdown(Bi(grey("思考 · ") + esc(text), grey("Thinking · ") + esc(text)), size))
    shown, hidden = _visible_steps(view.steps, opts.max_steps)
    if hidden:
        children.append(_markdown(Bi(grey(f"已省略 {hidden} 步较早的成功步骤"),
                                     grey(f"{hidden} earlier successful steps omitted")), size))
    children.extend(_step_element(s, size) for s in shown)
    if not children:
        return None
    return panel(
        title=_process_title(view), elements=children, expanded=_process_open(view, opts),
        element_id=PROCESS_ID, border="red" if view.failed_steps else "grey",
    )


# --------------------------------------------------------------------------- footer & details

def footer_text(footer: Footer, view: TurnView) -> Bi | None:
    parts: list[str] = []
    if footer.model:
        parts.append(esc(clip(footer.model, 60)))
    if footer.context_used is not None or footer.context_max:
        used = compact(footer.context_used) if footer.context_used is not None else "—"
        cap = compact(footer.context_max) if footer.context_max else "—"
        text = f"{used} / {cap}"
        if footer.context_used is not None and footer.context_max:
            text += f" · {footer.context_used / footer.context_max:.0%}"
        parts.append(text)
    zh = en = ""
    if footer.cache_hit is not None:
        sign = "≥" if footer.cache_hit_is_floor else ""
        zh, en = f"缓存命中 {sign}{footer.cache_hit:.0%}", f"cache hit {sign}{footer.cache_hit:.0%}"
    if footer.partial and view.phase.terminal:
        zh_tail, en_tail = "用量不完整", "usage partial"
    else:
        zh_tail = en_tail = ""
    base = " · ".join(parts)
    tag = f" <text_tag color='neutral'>{esc(clip(footer.tag, 30))}</text_tag>" if footer.tag else ""
    bits_zh = [b for b in (base, zh, zh_tail) if b]
    bits_en = [b for b in (base, en, en_tail) if b]
    if not bits_zh and not tag:
        return None
    return Bi(grey(" · ".join(bits_zh)) + tag if bits_zh else tag.strip(),
              grey(" · ".join(bits_en)) + tag if bits_en else tag.strip())


def footer_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    if view.continued:
        return None
    text = footer_text(view.footer, view)
    return _markdown(text, "notation", element_id=FOOTER_ID) if text else None


def _bar(ratio: float) -> str:
    ratio = min(max(ratio, 0.0), 1.0)
    filled = round(ratio * 10)
    color = "red" if ratio >= 0.95 else "orange" if ratio >= 0.8 else "blue"
    return f"<font color='{color}'>{'▓' * filled}</font>{grey('░' * (10 - filled))}"


def _metric_cell(metric: Metric, size: str) -> dict[str, Any]:
    zh = grey(esc(metric.label)) + f"  **{esc(metric.value)}**"
    en = grey(esc(metric.label_en or metric.label)) + f"  **{esc(metric.value)}**"
    return {
        "tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top",
        "elements": [_markdown(Bi(zh, en), size)],
    }


def _grid(metrics: Iterable[Metric], size: str) -> list[dict[str, Any]]:
    items = list(metrics)
    rows = []
    for i in range(0, len(items), 2):
        cells = [_metric_cell(m, size) for m in items[i:i + 2]]
        if len(cells) == 1:
            cells.append({"tag": "column", "width": "weighted", "weight": 1, "elements": []})
        rows.append({"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "12px", "columns": cells})
    return rows


def _bars(metrics: Iterable[Metric], size: str) -> list[dict[str, Any]]:
    out = []
    for m in metrics:
        bar = _bar(m.ratio) + " " if m.ratio is not None else ""
        hint = grey("· " + esc(m.hint)) if m.hint else ""
        zh = f"{esc(m.label)}  {bar}**{esc(m.value)}** {hint}".rstrip()
        en = f"{esc(m.label_en or m.label)}  {bar}**{esc(m.value)}** {hint}".rstrip()
        out.append(_markdown(Bi(zh, en), size))
    return out


def details_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    sections = [s for s in view.sections if s.metrics or s.notes]
    if not opts.show_details or not sections or view.phase is Phase.RUNNING or view.continued:
        return None
    size = "notation"
    children: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        if index:
            children.append({"tag": "hr", "margin": "2px 0px 2px 0px"})
        heading = Bi(f"**{esc(section.title)}**", f"**{esc(section.title_en or section.title)}**")
        children.append(_markdown(heading, size))
        body = _bars if section.layout == "bars" else _grid
        children.extend(body(section.metrics, size))
        notes = [n for n in dict.fromkeys(section.notes) if n]
        if notes:  # one quiet line per section instead of a stack of footnotes
            children.append(_markdown(Bi.same(grey(" · ".join(esc(n) for n in notes))), size))
    names_zh = " · ".join(s.title for s in sections)
    names_en = " · ".join(s.title_en or s.title for s in sections)
    title = Bi("**详情** " + grey("· " + names_zh), "**Details** " + grey("· " + names_en))
    return panel(title=title, elements=children, expanded=False, element_id=DETAILS_ID)


def notice_element(view: TurnView) -> dict[str, Any] | None:
    """Background-review messages captured during the turn, kept apart from the answer."""
    if not view.notices:
        return None
    children = [_markdown(Bi.same(esc(text)), "notation") for text in view.notices]
    return panel(
        title=Bi("**后台复盘**", "**Background review**"), elements=children, expanded=False, element_id="notices",
    )


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
    """Initial card for CardKit creation. Later updates target the element ids above."""
    opts = opts or RenderOptions()
    elements: list[dict[str, Any]] = [status_element(view, opts)]
    process = process_element(view, opts)
    if process:
        elements.append(process)
    elements.append(streaming_answer_element("\n\n".join(view.answers), opts))
    footer = footer_element(view, opts)
    if footer:
        elements.append(footer)
    return {
        "schema": "2.0",
        "config": _config(opts, streaming=True, summary="处理中…"),
        "body": {"elements": elements},
    }


def render_final(view: TurnView, opts: RenderOptions | None = None) -> dict[str, Any]:
    """Terminal card for a full-card update (also used for a sealed, rolled-over card)."""
    opts = opts or RenderOptions()
    elements: list[dict[str, Any]] = [status_element(view, opts)]
    process = process_element(view, opts)
    if process:
        elements.append(process)
    notices = notice_element(view)
    if notices:
        elements.append(notices)
    elements.extend(answer_elements(view, opts))
    for build in (footer_element, details_element):
        element = build(view, opts)
        if element:
            elements.append(element)
    return {
        "schema": "2.0",
        "config": _config(opts, streaming=False, summary=_summary(view)),
        "body": {"elements": elements},
    }


_PANEL_FIELDS = ("header", "elements", "border")


def partial_for(element: dict[str, Any], *, reset_state: bool = False) -> dict[str, Any]:
    """Fields for a ``partial_update_element`` of ``element``.

    Panels keep the reader's expanded/collapsed choice unless ``reset_state`` (used once, when the
    renderer itself decides the panel should open). Markdown elements update their text only.
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
