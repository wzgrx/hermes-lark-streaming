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
from dataclasses import dataclass, replace
from typing import Any

from .markdown import downgrade_tables, optimize_markdown_style, split_long_text
from .model import Block, BlockKind, Metric, Phase, RenderOptions, Section, Step, StepStatus, TurnView
from .redact import redact

STATUS_ID = "live"  # the trailing live line while streaming
ANSWER_ID = "answer"  # reserved; v3 streams each answer block into its own element
PROCESS_ID = "process"  # reserved
FOOTER_ID = "footer"  # reserved
DETAILS_ID = "details"

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
    """12.3k / 403.5k / 1.2M / 1M, the same style as the details panel."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(value)


def _id(base: str, view: TurnView) -> str:
    """Per-card element id: Feishu remembers a panel's expanded state by id, so reuse would leak it."""
    return f"{base}_{view.card_key}"[:20] if view.card_key else base


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
        "padding": "8px 8px 8px 8px",
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
    for step in block.steps:
        children.extend(_step_elements(step))
    return panel(title=Bi(grey(zh) + tail_zh, grey(en) + tail_en),
                 elements=children or [_markdown(Bi.same(" "), "notation")],
                 expanded=_expanded(view, opts, failed=bool(failed)), element_id=_id(block.key, view))


def _answer(block: Block, view: TurnView, opts: RenderOptions, *, streaming: bool) -> list[dict[str, Any]]:
    content = downgrade_tables(optimize_markdown_style(block.text))
    if streaming:
        return [{"tag": "markdown", "content": content or " ", "text_size": opts.text_size,
                 "element_id": _id(block.key, view)}]
    return [{"tag": "markdown", "content": chunk, "text_size": opts.text_size}
            for chunk in split_long_text(content) if chunk.strip()]


def block_elements(view: TurnView, opts: RenderOptions, *, streaming: bool) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for block in view.blocks:
        if block.kind is BlockKind.ANSWER:
            if block.text.strip() or streaming:
                elements.extend(_answer(block, view, opts, streaming=streaming))
        elif not opts.show_process:
            continue
        elif block.kind is BlockKind.THOUGHT and block.text.strip():
            elements.append(_thought_panel(block, view, opts))
        elif block.kind is BlockKind.TOOLS and block.steps:
            elements.append(_tools_panel(block, view, opts))
    return elements


# --------------------------------------------------------------------------- live line, footer

def live_text(view: TurnView) -> Bi:
    clock = duration(view.elapsed_s, whole=True)
    running = next((s for s in reversed(view.steps) if s.status is StepStatus.RUNNING), None)
    if running is not None:
        zh, en = f"正在执行 {esc(clip(running.name, 40))}", f"Running {esc(clip(running.name, 40))}"
    else:
        zh, en = "处理中", "Working"
    counter = f"{view.finished_steps}/{view.total_steps}" if view.total_steps else ""
    parts_zh = [p for p in (zh, f"步骤 {counter}" if counter else "", clock) if p]
    parts_en = [p for p in (en, f"step {counter}" if counter else "", clock) if p]
    return Bi(grey("⏳ " + " · ".join(parts_zh)), grey("⏳ " + " · ".join(parts_en)))


def status_element(view: TurnView, opts: RenderOptions) -> dict[str, Any]:
    return _markdown(live_text(view), "notation", element_id=STATUS_ID)


def process_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    return None  # v3 inserts blocks as they arrive; see streaming_elements


def footer_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    return None  # the footer line only appears on the finished card


def footer_text(view: TurnView) -> Bi:
    """``✅ **已完成** · 2m 01s`` then the run's figures in grey; failures in red."""
    if view.phase is Phase.FAILED:
        head = ("<font color='red'>❌ **出错**</font>", "<font color='red'>❌ **Error**</font>")
    elif view.phase is Phase.STOPPED:
        head = ("🛑 **已停止**", "🛑 **Stopped**")
    else:
        head = ("✅ **已完成**", "✅ **Completed**")
    clock = duration(view.elapsed_s)
    zh, en = head[0] + (f" · {clock}" if clock else ""), head[1] + (f" · {clock}" if clock else "")
    if view.failed_steps:
        zh += f" · <font color='red'>{view.failed_steps} 步失败</font>"
        en += f" · <font color='red'>{view.failed_steps} failed</font>"
    footer = view.footer
    meta_zh: list[str] = []
    meta_en: list[str] = []
    if footer.model:
        model = esc(clip(footer.model, 60))
        meta_zh.append(model)
        meta_en.append(model)
    if footer.context_used is not None and footer.context_max:
        ratio = f"{compact(footer.context_used)}/{compact(footer.context_max)} " \
                f"({footer.context_used / footer.context_max:.0%})"
        meta_zh.append(f"上下文 {ratio}")
        meta_en.append(f"context {ratio}")
    if footer.cache_hit is not None:
        hit = f"{'≥' if footer.cache_hit_is_floor else ''}{footer.cache_hit:.0%}"
        meta_zh.append(f"缓存 {hit}")
        meta_en.append(f"cache {hit}")
    if meta_zh:
        zh += "　" + grey(" · ".join(meta_zh))
        en += "　" + grey(" · ".join(meta_en))
    return Bi(zh, en)


# --------------------------------------------------------------------------- details panel (finished)

_SECTION_NAMES = {"usage": "用量", "resources": "资源", "accounts": "额度"}


def known(section: Section) -> Section:
    """Drop what is not known: a metric whose value is unknown, an unknown hint, and notes that say so."""
    metrics = tuple(
        replace(m, hint="" if _UNKNOWN in m.hint else m.hint) for m in section.metrics if m.value != _UNKNOWN
    )
    notes = tuple(n for n in section.notes if _UNKNOWN not in n)
    return replace(section, metrics=metrics, notes=notes)


_TILES_PER_ROW = 4


def _level_color(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    return "red" if ratio >= 0.8 else "orange" if ratio >= 0.5 else None


def _tile(metric: Metric | None, *, colour: bool) -> dict[str, Any]:
    column: dict[str, Any] = {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top",
                              "elements": []}
    if metric is None:
        return column  # keeps the grid aligned on a short row
    value = esc(metric.value)
    color = _level_color(metric.ratio) if colour else None
    if color:
        value = f"<font color='{color}'>{value}</font>"
    zh = f"{grey(esc(metric.label))}\n**{value}**"
    en = f"{grey(esc(metric.label_en or metric.label))}\n**{value}**"
    if metric.hint:
        zh += "\n" + grey(esc(metric.hint))
        en += "\n" + grey(esc(metric.hint))
    column["elements"].append(_markdown(Bi(zh, en), "notation"))
    return column


def _tile_rows(metrics: list[Metric], *, colour: bool) -> list[dict[str, Any]]:
    rows = []
    for start in range(0, len(metrics), _TILES_PER_ROW):
        chunk: list[Metric | None] = list(metrics[start:start + _TILES_PER_ROW])
        chunk += [None] * (_TILES_PER_ROW - len(chunk))
        rows.append({"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px",
                     "columns": [_tile(m, colour=colour) for m in chunk]})
    return rows


def _section_elements(section: Section) -> list[dict[str, Any]]:
    """Heading, then rows of label-over-value tiles per group, then one grey note line."""
    elements: list[dict[str, Any]] = []
    groups: dict[str, list[Metric]] = {}
    for metric in section.metrics:
        groups.setdefault(metric.group or section.title, []).append(metric)
    for heading, metrics in groups.items():
        elements.append(_markdown(Bi.same(f"**{esc(heading)}**"), "notation"))
        elements.extend(_tile_rows(metrics, colour=section.layout == "bars"))
    note_line = " · ".join(esc(n) for n in dict.fromkeys(section.notes) if n)
    if note_line:
        elements.append(_markdown(Bi.same(grey(note_line)), "notation"))
    return elements


def details_element(view: TurnView, opts: RenderOptions) -> dict[str, Any] | None:
    if not opts.show_details or view.phase is Phase.RUNNING or view.continued:
        return None
    blocks: list[list[dict[str, Any]]] = []
    names: list[str] = []
    for section in (known(s) for s in view.sections):
        if not section.metrics:
            continue
        blocks.append(_section_elements(section))
        names.append(_SECTION_NAMES.get(section.key, section.title.split(" · ")[0]))
    if view.notices:
        blocks.append([_markdown(Bi.same("**后台复盘**\n" + "\n".join(esc(n) for n in view.notices)), "notation")])
        names.append("后台复盘")
    if not blocks:
        return None
    children: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if index:
            children.append({"tag": "hr"})
        children.extend(block)
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
    return [*block_elements(view, opts, streaming=True), status_element(view, opts)]


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
    if view.continued:
        elements.append(_markdown(Bi(grey("↪ 内容见下一张卡片"), grey("↪ Continued on the next card")), "notation"))
    else:
        if not any(b.kind is BlockKind.ANSWER and b.text.strip() for b in view.blocks):
            phase = view.phase if view.phase.terminal else Phase.DONE
            elements.append(_markdown(_EMPTY_ANSWER[phase], opts.text_size))
        elements.append({"tag": "hr"})
        elements.append(_markdown(footer_text(view), "notation"))
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
