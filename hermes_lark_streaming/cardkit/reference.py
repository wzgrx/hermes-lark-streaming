"""V1 screenshot-reference layout; pure native Card 2.0, no I/O or inference."""

# ruff: noqa: RUF001
from __future__ import annotations

import html
import json
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..footer.account_adapters import money as account_money
from ..footer.layout import column
from ..footer.layout import markdown as bilingual_markdown
from ..footer.render import compact, safe
from ..footer.state import label, seconds
from ..footer.usage import cache_hit, count
from ..streaming.tooluse import ToolDisplayStep, redact_inline_secrets
from .panels import collapsible_panel

TOOLS_ID = "reference_tools"
RESOURCES_ID = "reference_resources"
REFERENCE_ELEMENT_RESERVE = 168
_UNKNOWN = ("Not reported", "未提供")
_SECRET = re.compile(r"(?:sk[-_]|oc_sk_|gh[pousr]_|github_pat_)[A-Za-z0-9_.-]+", re.I)


def markdown(en: str, zh: str, text_size: str) -> dict[str, Any]:
    result = bilingual_markdown(en, zh, text_size)
    # Neutral IDs/numbers/tool payloads need only one copy, not three identical
    # localized strings. This matters for the CardKit serialized byte limit.
    if en == zh:
        result.pop("i18n_content", None)
    return result


def _panel(title_en: str, title_zh: str, elements: list[dict[str, Any]], element_id: str) -> dict[str, Any]:
    panel = collapsible_panel(
        expanded=False,
        title_el={
            "tag": "plain_text",
            "content": title_en,
            "i18n_content": {"en_us": title_en, "zh_cn": title_zh},
            "text_size": "notation",
            "text_color": "grey",
        },
        elements=[{"tag": "hr"}, *elements],
        vertical_spacing="8px",
    )
    panel["element_id"] = element_id
    if title_en == title_zh:
        panel["header"]["title"].pop("i18n_content", None)
    # `content` already is the English/default value. Native i18n_content
    # overrides only the other locale; don't serialize that default twice.
    pending: list[Any] = [panel]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            localized = node.get("i18n_content")
            if isinstance(localized, dict) and localized.get("en_us") == node.get("content"):
                localized.pop("en_us")
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return panel


def _metric(en: str, zh: str, values: tuple[str, str]) -> tuple[str, str, str, str]:
    return en, zh, *values


def metric_row(left: tuple[str, str, str, str], right: tuple[str, str, str, str], text_size: str) -> dict[str, Any]:
    """V1 labels above bold values, matching the approved two-column design."""
    cells = []
    for en, zh, value_en, value_zh in (left, right):
        cells.append(
            column(
                1,
                [
                    markdown(f"<font color='grey'>{en}</font>", f"<font color='grey'>{zh}</font>", text_size),
                    markdown(f"**{value_en}**", f"**{value_zh}**", "normal_v2"),
                ],
            )
        )
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "12px", "columns": cells}


def _number(data: dict[str, Any], key: str, *, short: bool = True) -> tuple[str, str]:
    n = count(data.get(key))
    if n is None:
        return _UNKNOWN
    result = compact(n) if short else f"{n:,}"
    return result, result


def _history_tokens(bucket: dict[str, Any]) -> str:
    """Incomplete observations are lower bounds; unknown is not measured zero."""
    n = count(bucket.get("tokens"))
    partial = bool(bucket.get("partial"))
    if n is None:
        return "—*" if partial else "—"
    value = compact(n, lower_bound=partial)
    return f"≥{value}*" if partial else value


def _duration(value: Any) -> str:
    n = seconds(value)
    if n is None:
        return ""
    if n < 1:
        return f"{n * 1000:.0f}ms"
    return f"{int(n // 60)}m {int(n % 60):02d}s" if n >= 60 else f"{n:.1f}s"


def _tool_characters(text: str, single_line: bool) -> Iterator[str]:
    """Fold row whitespace lazily, without dropping later visible text first."""
    if not single_line:
        yield from text
        return
    seen = pending = False
    for char in text:
        if char.isspace():
            pending = seen
        else:
            if pending:
                yield " "
            yield char
            seen, pending = True, False


def _tool_text(value: str, limit: int = 160, *, single_line: bool = False) -> str:
    # Keep complete quoted assignments/flags/JSON values visible to the
    # redactor. Cutting first can remove their closing quote and expose a
    # credential fragment in both the compact row and the expanded excerpt.
    if limit <= 0:
        return ""
    text = _SECRET.sub("[redacted]", redact_inline_secrets(value))
    # Each source character emits one whole HTML/Markdown escape unit. Reserve
    # the omission marker only when needed, and count it in the same byte cap.
    units: list[tuple[str, int]] = []
    size = 0
    for char in _tool_characters(text, single_line):
        unit = html.escape(char)
        if char in "\\`*_[]~":
            unit = "\\" + unit
        cost = len(unit.encode())
        if size + cost > limit:
            marker = "…" if limit >= 3 else "." * limit
            marker_cost = len(marker.encode())
            while units and size + marker_cost > limit:
                size -= units.pop()[1]
            return "".join(value for value, _ in units) + marker
        units.append((unit, cost))
        size += cost
    return "".join(value for value, _ in units)


def _tool_title(step: ToolDisplayStep) -> str:
    # Tracker titles already include timing; V1 has a dedicated timing column.
    return re.sub(r" \([0-9.]+ (?:s|ms)\)$", "", step.get("title") or step["name"])


def _tool_groups(steps: list[ToolDisplayStep]) -> list[tuple[int, int, ToolDisplayStep, float | None]]:
    """Only adjacent successful output-free process polls merge; commands never merge."""
    groups: list[tuple[int, int, ToolDisplayStep, float | None]] = []
    for i, step in enumerate(steps):
        measured_ms = seconds(step.get("elapsed_ms"))
        elapsed = measured_ms / 1000 if measured_ms is not None else None
        poll = (
            step["name"].lower() in {"process", "process_poll", "poll_process"}
            # Missing details are not evidence of the same poll target.
            and bool((step.get("detail") or "").strip())
            and step["status"] == "success"
            and not step.get("error_block")
            and not step.get("result_block")
            and not step.get("output")
            and not step.get("error")
        )
        if poll and groups:
            start, end, previous, total = groups[-1]
            if (
                end == i - 1
                and previous["name"] == step["name"]
                and previous["status"] == "success"
                and previous.get("detail") == step.get("detail")
                and not previous.get("output")
                and not previous.get("error")
                and not previous.get("result_block")
                and not previous.get("error_block")
            ):
                # A partial sum is not an exact duration for the whole group.
                combined = total + elapsed if total is not None and elapsed is not None else None
                groups[-1] = start, i, previous, combined
                continue
        groups.append((i, i, step, elapsed))
    return groups


def build_tools(
    data: dict[str, Any], reference: dict[str, Any], *, interrupted: bool = False, terminal: bool = False,
) -> dict[str, Any]:
    steps: list[ToolDisplayStep] = reference.get("steps", [])
    prior = count(reference.get("tools_prior")) or 0
    prior_done = count(reference.get("done_prior")) or 0
    prior_failed = count(reference.get("failed_prior")) or 0
    # Clarify/approval handoffs archive the tracker, not missing-result evidence.
    # Earlier-card gaps are unknown, never proof that a tool is still running.
    prior_unconfirmed = max(0, prior - prior_done)
    total = prior + len(steps)
    done = prior_done + sum(s["status"] in {"success", "error"} for s in steps)
    failures = prior_failed + sum(s["status"] == "error" for s in steps)
    # Ending a model turn is not evidence that a background command stopped.
    # Keep observed counts intact; only replace a now-stale live indicator.
    running = sum(s["status"] == "running" for s in steps)
    ended = interrupted or terminal
    unconfirmed = prior_unconfirmed + (running if ended else 0)
    elapsed = _duration(data.get("duration"))
    tail_en = f" · {failures} failed" if failures else ""
    tail_zh = f" · {failures} 失败" if failures else ""
    if unconfirmed:
        tail_en += f" · {unconfirmed} unconfirmed"
        tail_zh += f" · {unconfirmed} 结果未确认"
    if running and not ended:
        tail_en += f" · {running} running"
        tail_zh += f" · {running} 运行中"
    elapsed_part = f" · {elapsed}" if elapsed else ""
    en = f"🛠 Tools{elapsed_part} · {done}/{total} ended{tail_en}"
    zh = f"🛠 工具执行{elapsed_part} · {done}/{total} 结束{tail_zh}"
    children: list[dict[str, Any]] = []
    if unconfirmed and ended:
        children.append(markdown(
            "Turn ended without final tool results; this does not confirm that a background process stopped.",
            "本轮已结束，但未收到部分工具的最终结果；这不代表后台进程已停止。",
            "notation",
        ))
    if failures:
        last_failure = max((i for i, s in enumerate(steps) if s["status"] == "error"), default=-1)
        continued = (
            last_failure >= 0
            and last_failure < len(steps) - 1
            and all(s["status"] == "success" for s in steps[last_failure + 1 :])
        )
        children.append(
            markdown(
                f"<font color='red'>⚠ **{failures} tool failures**"
                f"{' · Later steps completed' if continued else ''}</font>",
                f"<font color='red'>⚠ **{failures} 次工具失败**{' · 后续步骤已完成' if continued else ''}</font>",
                "notation",
            )
        )
    groups = _tool_groups(steps)
    # Live (or terminal-unconfirmed) work must not disappear behind a burst of
    # newer failures. Then prioritize errors, then recent ordinary steps.
    # Render the selected rows chronologically and retain all observed counts.
    selected: list[int] = []
    for status in ("running", "error", None):
        for i in range(len(groups) - 1, -1, -1):
            if len(selected) >= 8:
                break
            if i not in selected and (status is None or groups[i][2]["status"] == status):
                selected.append(i)
    shown = [groups[i] for i in sorted(selected)]
    omitted = max(0, len(groups) - 8)
    if omitted or prior:
        prior_note_en = f" ({prior_unconfirmed} unconfirmed)" if prior_unconfirmed else ""
        prior_note_zh = f"（{prior_unconfirmed} 结果未确认）" if prior_unconfirmed else ""
        children.append(
            markdown(
                f"{omitted} groups omitted · {prior} steps on earlier cards{prior_note_en} · {failures} total failures",
                f"{omitted} 组未展示 · 前卡 {prior} 步{prior_note_zh} · 全轮失败 {failures} 次",
                "notation",
            )
        )
    for start, end, step, elapsed_s in shown:
        number = f"{prior + start + 1:02d}" if start == end else f"{prior + start + 1:02d}–{prior + end + 1:02d}"
        name = _tool_text(_tool_title(step), 100, single_line=True)
        # A short inline hint distinguishes identical tool names without a
        # second expansion or another element. Full details stay in excerpts.
        hint = _tool_text(step.get("detail") or "", 64, single_line=True)
        # Error rows already spend their second line on the actionable cause;
        # don't crowd it out or trade away error excerpts for optional hints.
        if hint and hint != name and step["status"] != "error":
            name += f" · <font color='grey'>{hint}</font>"
        error_detail = ""
        if step["status"] == "error":
            block = step.get("error_block")
            content = str((block.get("content") if block else "") or "").strip() or step.get("error") or ""
            error_detail = _tool_text(content, 100, single_line=True)
        name_content = name + (f"\n<font color='red'>{error_detail}</font>" if error_detail else "")
        copies = end - start + 1
        state = {"success": ("Succeeded", "成功", "green"), "error": ("Failed", "失败", "red")}.get(
            step["status"],
            ("Running", "运行中", "blue"),
        )
        repeat_en, repeat_zh = (f" · {copies} calls", f" · {copies} 次") if copies > 1 else ("", "")
        unresolved = ended and step["status"] == "running"
        if unresolved:
            state = ("Unconfirmed", "结果未确认", "orange")
        status_text = markdown(
            f"<font color='{state[2]}'>{state[0]}{repeat_en}</font>",
            f"<font color='{state[2]}'>{state[1]}{repeat_zh}</font>",
            "notation",
        )
        status_text["text_align"] = "right"
        elapsed_label = ("—" if unresolved else "…") if step["status"] == "running" else (
            "—" if elapsed_s is None else f"<font color='grey'>{_duration(elapsed_s)}</font>"
        )
        timing = markdown(elapsed_label, elapsed_label, "notation")
        timing["text_align"] = "right"
        row = {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "columns": [
                column(
                    1,
                    [
                        markdown(
                            f"<font color='grey'>{number}</font>", f"<font color='grey'>{number}</font>", "notation"
                        )
                    ],
                ),
                column(5, [markdown(name_content, name_content, "notation")]),
                column(2, [status_text]),
                column(1, [timing]),
            ],
        }
        if step["status"] == "error":
            row["background_style"] = "red-50"
        children.append(row)
    # The excerpt must retain the actionable errors highlighted above even when
    # later polling pushes them outside the recent window. Keep chronological
    # order; drop ordinary excerpts first when the serialized budget is tight.
    important = {i for start, end, item, _ in shown if item["status"] in {"error", "running"}
                 for i in range(start, end + 1)}
    indices = sorted(important | set(range(max(0, len(steps) - 24), len(steps))))
    raw: list[tuple[int, str]] = []
    for i in indices:
        step = steps[i]
        status = {"success": "成功 / Succeeded", "error": "失败 / Failed"}.get(step["status"], "运行中 / Running")
        if ended and step["status"] == "running":
            status = "结果未确认 / Unconfirmed"
        detail = _tool_text(step.get("detail", ""), 180)
        block = step.get("error_block") or step.get("result_block")
        content = str((block.get("content") if block else "") or "").strip()
        output = _tool_text(content or step.get("error") or step.get("output") or "", 200)
        raw.append((i,
            f"**{prior + i + 1} · {_tool_text(_tool_title(step), 70)} · {status}**\n"
            f"{detail}" + (f"\n{output}" if output else "")
        ))
    # Long escaped failures and localized status rows consume bytes before raw
    # records. Keep the complete native tools panel within its 13 KB share.
    rows_bytes = len(json.dumps(children, ensure_ascii=False, separators=(",", ":")).encode())
    # Interrupted cards also carry localized status in their footer/preview.
    # Reserve that overhead rather than letting long excerpts consume it.
    raw_limit = max(1000, min(4000, 12500 - rows_bytes - (512 if unconfirmed else 0)))
    def excerpt_panel() -> dict[str, Any]:
        return _panel(
            f"Step excerpts · {len(raw)}/{len(steps)} · pending/errors first, bounded output",
            f"步骤摘要 · {len(raw)}/{len(steps)} 步 · 优先保留待确认与异常，输出限长",
            [{"tag": "markdown", "content": "\n\n".join(text for _, text in raw), "text_size": "notation"}],
            "ref_tool_records",
        )

    while len(raw) > 1:
        # Count the actual localized wrappers too. New inline hints must share
        # the existing 13 KB allocation, not expand the whole-card byte budget.
        candidate = _panel(en, zh, [*children, excerpt_panel()], TOOLS_ID)
        if (len(raw) <= 24 and len("\n\n".join(text for _, text in raw).encode()) <= raw_limit
                and len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()) <= 13000):
            break
        # The excerpt follows the same priorities as the visible rows. Keeping
        # an active row but evicting its command behind long errors is misleading.
        # Within a priority tier, retain newer entries; render chronologically.
        drop = min(range(len(raw)), key=lambda pos: (
            (2 if steps[raw[pos][0]]["status"] == "running" else 1) if raw[pos][0] in important else 0,
            raw[pos][0],
        ))
        raw.pop(drop)
    if raw:
        children.append(excerpt_panel())
    if not children:
        children.append(markdown("No tool calls yet.", "尚无工具调用。", "notation"))
    return _panel(en, zh, children, TOOLS_ID)


def build_resources(host: dict[str, Any]) -> dict[str, Any]:
    def value(key: str, suffix: str = "") -> str:
        n = seconds(host.get(key))
        if n is None:
            return "—"
        number = f"{n:.1f}".rstrip("0").rstrip(".")
        return f"{number}{suffix}"

    def memory(prefix: str) -> str:
        used, maximum = seconds(host.get(prefix + "_used_gib")), seconds(host.get(prefix + "_total_gib"))
        if used is None or not maximum:
            return "—"
        total = f"{maximum:.1f}".rstrip("0").rstrip(".")
        return f"{used:.1f} / {total} GiB · {used / maximum:.0%}"

    gpu, temperature, vram, ram = (
        value("gpu_percent", "%"),
        value("gpu_temperature", "°C"),
        memory("gpu"),
        memory("ram"),
    )

    def memory_title(prefix: str) -> str:
        used, maximum = seconds(host.get(prefix + "_used_gib")), seconds(host.get(prefix + "_total_gib"))
        if used is None or not maximum:
            return "—"
        total = f"{maximum:.1f}".rstrip("0").rstrip(".")
        return f"{used:.1f}/{total}G"

    # Keep the familiar GPU-first summary when observed; do not spend the
    # collapsed row on absent GPU fields on CPU-only/unavailable hosts.
    title_parts = []
    if gpu != "—":
        title_parts.append(f"GPU {gpu}")
    if temperature != "—":
        title_parts.append(temperature if title_parts else f"GPU {temperature}")
    if memory_title("gpu") != "—":
        title_parts.append(f"VRAM {memory_title('gpu')}")
    if not title_parts and value("cpu_percent", "%") != "—":
        title_parts.append(f"CPU {value('cpu_percent', '%')}")
    if memory_title("ram") != "—":
        title_parts.append(f"RAM {memory_title('ram')}")
    title = "🖥 " + " · ".join(title_parts) if title_parts else "🖥 Resources · Not sampled"
    title_zh = title if title_parts else "🖥 系统资源 · 未采集"
    if not title_parts and host.get("sampled_at"):
        title, title_zh = "🖥 Resources · Unavailable", "🖥 系统资源 · 指标未获取"
    uptime_s = seconds(host.get("uptime"))
    uptime = uptime_zh = ""
    if uptime_s is not None:
        n = int(uptime_s)
        if n >= 86400:
            uptime, uptime_zh = f"{n // 86400}d {n % 86400 // 3600}h", f"{n // 86400}天{n % 86400 // 3600}小时"
        elif n >= 3600:
            uptime, uptime_zh = f"{n // 3600}h {n % 3600 // 60}m", f"{n // 3600}小时{n % 3600 // 60}分"
        elif n >= 60:
            uptime, uptime_zh = f"{n // 60}m {n % 60}s", f"{n // 60}分{n % 60}秒"
        else:
            uptime, uptime_zh = f"{n}s", f"{n}秒"
    processes = count(host.get("processes"))
    extra = f" · Processes {processes}" if processes is not None else ""
    extra_zh = f" · 进程 {processes}" if processes is not None else ""
    if uptime:
        extra += f" · Uptime {uptime}"
        extra_zh += f" · 运行 {uptime_zh}"
    children = [
        metric_row(
            _metric("GPU / temperature", "GPU 利用率 / 温度", (f"{gpu} · {temperature}",) * 2),
            _metric("VRAM", "显存使用", (vram,) * 2),
            "notation",
        ),
        metric_row(
            _metric("CPU total", "CPU 总利用率", (value("cpu_percent", "%"),) * 2),
            _metric("WSL memory" if host.get("scope") == "WSL" else "Host memory",
                    "WSL 内存使用" if host.get("scope") == "WSL" else "主机内存使用", (ram,) * 2),
            "notation",
        ),
        {"tag": "hr"},
        markdown(
            f"<font color='grey'>{safe(host.get('scope')) or 'Host'} snapshot{extra} · "
            f"sampled {safe(host.get('sampled_at')) or 'not sampled'}</font>",
            f"<font color='grey'>{safe(host.get('scope')) or '主机'} 快照{extra_zh} · "
            f"采样 {safe(host.get('sampled_at')) or '未采集'}</font>",
            "notation",
        ),
    ]
    if host.get("unavailable"):
        children.append(
            markdown(
                "Some metrics were not collected; this is not a live dashboard.",
                "部分指标未采集；这是快照，不是持续实时监控。",
                "notation",
            )
        )
    return _panel(title, title_zh, children, RESOURCES_ID)


def build_reference_prefix(
    data: dict[str, Any], *, interrupted: bool = False, terminal: bool = False,
) -> list[dict[str, Any]]:
    ref = data.get("reference", {})
    elements = [build_tools(data, ref, interrupted=interrupted, terminal=terminal)] if ref.get("show_tools") else []
    if ref.get("resources_enabled"):
        elements.append(build_resources(ref.get("host") or {}))
    if ref.get("design_version") == 2:
        from .reference_v2 import compact_prefix

        return compact_prefix(elements, data)
    return elements


def build_reference_footer(
    data: dict[str, Any],
    *,
    text_size: str = "notation",
    is_error: bool = False,
    is_aborted: bool = False,
    details: bool = True,
    live_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    elements = _build_reference_footer_v1(data, text_size=text_size, is_error=is_error,
        is_aborted=is_aborted, details=details, live_status=live_status)
    if data.get("reference", {}).get("design_version") == 2:
        from .reference_v2 import compact_footer

        return compact_footer(elements, data, details=details)
    return elements


def _build_reference_footer_v1(
    data: dict[str, Any],
    *,
    text_size: str = "notation",
    is_error: bool = False,
    is_aborted: bool = False,
    details: bool = True,
    live_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    # Reuse the audited requested/returned-ID equality and escaping contract.
    from ..footer.render import footer_annotations, model_identity_line

    bounded = dict(data)
    routes = data.get("routes")
    if isinstance(routes, list):
        bounded["routes"] = [label(r)[:60] for r in routes[:3]]
    model = label(data.get("model"))
    used, maximum = count(data.get("context_used")), count(data.get("context_max"))
    maximum_label = f"{maximum / 1_000_000:.1f}M" if maximum and maximum % 1_000_000 == 0 else compact(maximum or 0)
    context = ""
    if used is not None or maximum:
        # Missing one operand must not erase the other or invent a percentage.
        context = f"{compact(used) if used is not None else '—'}/{maximum_label if maximum else '—'}"
        if used is not None and maximum:
            context += f" ({used / maximum:.0%})"
    live = live_status is not None
    unknown_model = ("Model pending", "模型待返回") if live else ("Model not reported", "模型未提供")
    unknown_context = ("Context pending", "上下文待返回") if live else ("Context not reported", "上下文未提供")
    title = f"🪙 {model or unknown_model[0]} · {context or unknown_context[0]}"
    title_zh = f"🪙 {model or unknown_model[1]} · {context or unknown_context[1]}"
    if data.get("usage_partial"):
        title += " · Partial"
        title_zh += " · 不完整"
    if is_error or is_aborted:
        title = ("✕ Failed · " if is_error else "◼ Stopped · ") + title
        title_zh = ("✕ 本轮失败 · " if is_error else "◼ 已停止 · ") + title_zh
    en, zh, color = (
        ("Failed", "本轮失败", "red")
        if is_error
        else (("Stopped", "已停止", "grey") if is_aborted else ("Answer completed", "回答已完成", "green"))
    )
    duration = _duration(data.get("duration"))
    duration_suffix = f" · {duration}" if duration else ""
    icon = "✕" if is_error else ("◼" if is_aborted else "✓")
    status = markdown(
        f"<font color='{color}'>{icon} {en}{duration_suffix}</font>",
        f"<font color='{color}'>{icon} {zh}{duration_suffix}</font>",
        text_size,
    )
    if live_status is not None:
        status = {k: v for k, v in live_status.items() if k != "element_id"}
        status["i18n_content"] = dict(status["i18n_content"])
    ref = data.get("reference", {})
    failures = count(ref.get("failed_total"))
    if failures and live_status is not None:
        status["content"] += f" · Tools: {failures} failed"
        status["i18n_content"]["en_us"] = status["content"]
        status["i18n_content"]["zh_cn"] += f" · 工具失败 {failures} 次"
    first = seconds(data.get("first_response"))
    first_attempt = seconds(data.get("first_response_attempt"))
    # Wall time may include a previous attempt/backoff; no evidence means no
    # retry claim. A tiny tolerance avoids float-subtraction display noise.
    first_includes_wait = first is not None and first_attempt is not None and first - first_attempt > 1e-6
    inp, cached = count(data.get("input_tokens")), count(data.get("cache_read_tokens"))
    # The count and its ratio have separate availability. A zero denominator or
    # incomplete input must not erase a known cache count or fabricate a rate.
    cache = ""
    cache_partial = bool(data.get("cache_read_partial"))
    if cached is not None and (inp is None or cached <= inp):
        hit = cache_hit(data) or "—"
        cache = f"{'≥' if cache_partial else ''}{compact(cached, lower_bound=cache_partial)} / {hit}"
    requests, errors = count(data.get("api_calls")), count(data.get("retries"))
    attempts = attempts_zh = ""
    if requests is not None or errors is not None:
        attempts = f"{requests if requests is not None else '—'} / {errors if errors is not None else '—'}"
        attempts_zh = (
            f"{str(requests) + ' 次' if requests is not None else '—'} / "
            f"{str(errors) + ' 次' if errors is not None else '—'}"
        )
    if live_status is None and failures is not None:
        succeeded = count(ref.get("succeeded_total"))
        if succeeded is None:
            succeeded = sum(s["status"] == "success" for s in ref.get("steps", []))
        # This branch is terminal (no live_status), including successful answers.
        # Missing tool results stay unknown; do not invent completion or failure.
        prior_unconfirmed = max(0, (count(ref.get("tools_prior")) or 0) - (count(ref.get("done_prior")) or 0))
        unconfirmed = prior_unconfirmed + sum(s["status"] == "running" for s in ref.get("steps", []))
        pending_en = f" / {unconfirmed} unconfirmed" if unconfirmed else ""
        pending_zh = f" / {unconfirmed} 结果未确认" if unconfirmed else ""
        tools = markdown(
            f"<font color='grey'>Tools: {succeeded} succeeded / {failures} failed{pending_en}</font>",
            f"<font color='grey'>工具：{succeeded} 成功 / {failures} 失败{pending_zh}</font>",
            text_size,
        )
        tools["text_align"] = "right"
        status = {
            "tag": "column_set",
            "flex_mode": "none",
            "horizontal_spacing": "8px",
            "columns": [column(1, [status]), column(1, [tools])],
        }
    provider_id = label(data.get("provider"))
    provider = safe({"opencode-go": "OpenCode Go", "siliconflow": "SiliconFlow"}.get(provider_id, provider_id))
    api = safe(data.get("api_mode"))
    reasoning = safe(data.get("reasoning"))
    effort_en, effort_zh = (f"{reasoning} (requested)", f"{reasoning}（请求）") if reasoning else _UNKNOWN
    if not reasoning and data.get("reasoning_missing_reason") == "request_truncated":
        effort_en, effort_zh = "Request metadata truncated", "请求字段已裁剪"
    identity = markdown(
        f"<font color='grey'>{provider or _UNKNOWN[0]} · {api or _UNKNOWN[0]} · {effort_en}</font>",
        f"<font color='grey'>{provider or _UNKNOWN[1]} · {api or _UNKNOWN[1]} · {effort_zh}</font>",
        text_size,
    )
    model_line = model_identity_line(bounded, text_size)
    model_line["content"] = "<font color='grey'>" + re.sub(r"</?font[^>]*>", "", model_line["content"]) + "</font>"
    model_line["i18n_content"] = {
        k: "<font color='grey'>" + re.sub(r"</?font[^>]*>", "", v) + "</font>"
        for k, v in model_line["i18n_content"].items()
    }
    children = [status, identity, model_line]
    if details:
        children.extend(
            [
                {"tag": "hr"},
                metric_row(
                    _metric("Input incl. cache", "本轮输入（含缓存）", _number(data, "input_tokens")),
                    _metric("Output", "本轮输出", _number(data, "output_tokens")),
                    text_size,
                ),
                metric_row(
                    _metric(
                        ("Cache read (partial) / hit lower bound" if cache_hit(data).startswith("≥") else
                         "Cache read (partial) / hit") if cache_partial else "Cache read / hit",
                        ("缓存读取（部分） / 命中率下限" if cache_hit(data).startswith("≥") else
                         "缓存读取（部分） / 命中率") if cache_partial else "缓存读取 / 命中率",
                        (cache, cache) if cache else _UNKNOWN,
                    ),
                    _metric(
                        "API attempts / errors", "API 请求 / 错误", (attempts, attempts_zh) if attempts else _UNKNOWN
                    ),
                    text_size,
                ),
                metric_row(
                    _metric(
                        "First response incl. wait" if first_includes_wait else "First response",
                        "首响应（含等待）" if first_includes_wait else "首响应",
                        (f"{first:.2f}s",) * 2 if first is not None else _UNKNOWN,
                    ),
                    _metric(
                        "Cost", "费用", ("<font color='grey'>Not reported</font>", "<font color='grey'>未提供</font>")
                    ),
                    text_size,
                ),
            ]
        )
        history = data.get("reference", {}).get("history")
        if isinstance(history, dict):
            history_status = history.get("status")
            if history_status in {"pending", "unavailable", "no_history"}:
                en_status, zh_status = {
                    "pending": ("Loading local history…", "正在读取本机历史…"),
                    "unavailable": ("History read failed or timed out; retrying later", "历史读取失败或超时，稍后重试"),
                    "no_history": ("No recorded main requests yet", "尚无已记录的主请求"),
                }[history_status]
                if not live and history_status in {"pending", "unavailable"}:
                    # Terminal cards have no refresh timer. A background read
                    # finishing later does not update this frozen snapshot.
                    en_status, zh_status = (
                        ("History snapshot not ready; a later message can retry",
                         "本轮历史快照尚未就绪；后续消息可重试")
                        if history_status == "pending" else
                        ("History snapshot read failed or timed out; a later message can retry",
                         "本轮历史快照读取失败或超时；后续消息可重试")
                    )
                children.append(markdown(f"◷ {en_status}", f"◷ {zh_status}", text_size))
            if history_status not in {"pending", "unavailable", "no_history"}:
                periods = []
                for key, en_label, zh_label in (
                    ("today", "Today", "今日"),
                    ("month", "Month", "本月"),
                    ("total", "Total", "累计"),
                ):
                    bucket = history.get(key, {})
                    value = _history_tokens(bucket)
                    periods.append((f"{en_label} {value}", f"{zh_label} {value}"))
                children.append({"tag": "hr"})
                children.append(
                    markdown(
                        "◷ History · <font color='grey'>" + " · ".join(p[0] for p in periods) + "</font>",
                        "◷ 历史用量 · <font color='grey'>" + " · ".join(p[1] for p in periods) + "</font>",
                        text_size,
                    )
                )
                history_partial = any(history.get(k, {}).get("partial") for k in ("today", "month", "total"))
                partial_en, partial_zh = (
                    (" · * partial; ≥ observed lower bound", " · * 不完整；≥ 已观测下限")
                    if history_partial else ("", "")
                )
                children.append(
                    markdown(
                        f"<font color='grey'>Main requests · input + output · since "
                        f"{safe(history.get('since')) or 'not recorded'} · {safe(history.get('timezone')) or 'UTC'}"
                        f"{partial_en}</font>",
                        f"<font color='grey'>主请求 · 输入＋输出 · 自 {safe(history.get('since')) or '未记录'} 起 · "
                        f"{safe(history.get('timezone')) or 'UTC'}{partial_zh}</font>",
                        "notation",
                    )
                )
                if history.get("show_models") and history.get("models"):
                    rows = [markdown(
                        "Cumulative by subscription / model · top 3", "按订阅商 / 模型累计 · 前 3 项", text_size,
                    )]
                    for item in history["models"][:3]:
                        subscription = label(item.get("subscription"))
                        model_label = label(item.get("model"))
                        name = safe(subscription[:32]) + ("…" if len(subscription) > 32 else "")
                        model_value = safe(model_label[:48]) + ("…" if len(model_label) > 48 else "")
                        tokens = _history_tokens(item)
                        total_text = markdown(tokens, tokens, text_size)
                        total_text["text_align"] = "right"
                        rows.append(
                            {
                                "tag": "column_set",
                                "flex_mode": "none",
                                "horizontal_spacing": "8px",
                                "columns": [
                                    column(
                                        2,
                                        [
                                            markdown(
                                                f"<font color='grey'>{name}</font>",
                                                f"<font color='grey'>{name}</font>",
                                                text_size,
                                            )
                                        ],
                                    ),
                                    column(4, [markdown(model_value, model_value, text_size)]),
                                    column(1, [total_text]),
                                ],
                            }
                        )
                    children.append(
                        {
                            "tag": "column_set",
                            "background_style": "grey-50",
                            "columns": [{**column(1, rows), "padding": "8px"}],
                        }
                    )
                children.append(
                    markdown(
                        "<font color='grey'>Local observed usage, not a provider bill or account allowance.</font>",
                        "<font color='grey'>累计是本机账本可观测用量，不等于服务商账单或账户额度。</font>",
                        "notation",
                    )
                )
        accounts = ref.get("accounts")
        if isinstance(accounts, dict):
            children.append(build_account_panel(accounts, account_timezone(ref)))
        # Route/error/partial/compression metadata stays visible, not erased to
        # force a polished screenshot. Context belongs in the panel header.
        annotations, note = footer_annotations(bounded, text_size)
        children.extend(annotations)
        if data.get("usage_partial") or data.get("compression_observed"):
            children.append(note)
        if isinstance(routes, list) and len(routes) > 3:
            children.append(
                markdown(
                    "Only the first three provider path entries are shown.", "服务商路径仅展示前 3 项。", "notation"
                )
            )
    return [_panel(title, title_zh, children, "footer_details")]


def build_reference_badge(data: dict[str, Any]) -> list[dict[str, Any]]:
    name = safe(data.get("reference", {}).get("agent_name"))
    if not name:
        return []
    if data.get("reference", {}).get("design_version") == 2:
        name += " · V2"
    return [
        dict(
            markdown(
                f"<text_tag color='neutral'>🤖 {name}</text_tag>",
                f"<text_tag color='neutral'>🤖 {name}</text_tag>",
                "notation",
            ),
            element_id="footer_agent",
        )
    ]


def account_timezone(ref: dict[str, Any]) -> str:
    """Account clock survives disabled/pending local-history snapshots."""
    raw = ref.get("account_timezone")
    if raw is None:
        history = ref.get("history")
        raw = history.get("timezone", "UTC") if isinstance(history, dict) else "UTC"
    return raw if isinstance(raw, str) else "UTC"


def build_account_panel(value: dict[str, Any], timezone: str) -> dict[str, Any]:
    """Pure rendering of configured-account API snapshots, not turn identity."""
    if value.get("scope") == "active_provider":
        from .account_panel_v2 import build_current_account_panel

        return build_current_account_panel(value, timezone)
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
        timezone = "UTC"

    def stamp(raw: Any, *, year: bool = False) -> str:
        if not isinstance(raw, str) or len(raw) > 40:
            return "—"
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M" if year else "%m-%d %H:%M") if dt.tzinfo else "—"
        except (ValueError, OverflowError):
            return "—"

    def failure(code: Any) -> tuple[str, str]:
        if type(code) is not int or not 100 <= code <= 599:
            return "API read failed", "API 读取失败"
        if code in {401, 403}:
            return f"API authentication/permission error · HTTP {code}", f"API 凭据/权限错误 · HTTP {code}"
        if code == 429:
            return "API rate-limited · HTTP 429", "API 限流 · HTTP 429"
        return f"API read failed · HTTP {code}", f"API 读取失败 · HTTP {code}"

    children = []
    rows = value.get("accounts")
    rows = rows[:4] if isinstance(rows, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        heading = f"**{safe(row.get('label')) or 'Account'} · {safe(row.get('provider'))}**"
        heading_en, heading_zh = heading, heading
        if row.get("discovered"):
            heading_en += " · discovered candidate"
            heading_zh += " · 自动候选"
        lines_en, lines_zh = [heading_en], [heading_zh]
        if row.get("status") != "ok":
            en, zh = {
                "pending": ("API snapshot pending", "API 快照待返回"),
                "unsupported": (
                    "Account API adapter pending", "账户 API 待接入",
                ),
                "missing_credentials": ("Credential reference is not configured", "凭据引用未配置"),
            }.get(label(row.get("status")), ("API snapshot unavailable", "API 快照获取失败"))
            if row.get("status") == "unsupported" and row.get("reason") == "endpoint_retired":
                en = "Official account endpoint retired · " + safe(row.get("retired_on"))
                zh = "官方账户接口已退役 · " + safe(row.get("retired_on"))
            elif row.get("status") == "unavailable":
                en, zh = failure(row.get("http_status"))
            lines_en.append(en)
            lines_zh.append(zh)
            checked = stamp(row.get("checked_at"))
            if checked != "—" and row.get("source") != "none":
                lines_en.append(f"Last check · {checked}")
                lines_zh.append(f"最近查询 · {checked}")
        else:
            windows = row.get("windows")
            remaining, resets = [], []
            for window in windows[:3] if isinstance(windows, list) else []:
                if not isinstance(window, dict):
                    continue
                names = {"rolling": ("5h", "5h"), "weekly": ("Week", "周"),
                         "monthly": ("Month", "月"), "mcp_monthly": ("MCP month", "MCP 月")}
                en, zh = names.get(label(window.get("name")), ("", ""))
                n = seconds(window.get("remaining_percent"))
                if en and n is not None and n <= 100:
                    number = f"{n:.1f}".rstrip("0").rstrip(".")
                    remaining.append((f"{en} {number}%", f"{zh} {number}%"))
                    resets.append((f"{en} {stamp(window.get('reset_at'))}", f"{zh} {stamp(window.get('reset_at'))}"))
            if remaining:
                lines_en.append("Remaining · " + " · ".join(a for a, _b in remaining))
                lines_zh.append("剩余 · " + " · ".join(b for _a, b in remaining))
                lines_en.append("Reset · " + " · ".join(a for a, _b in resets))
                lines_zh.append("重置 · " + " · ".join(b for _a, b in resets))
            balances = row.get("balances")
            for balance in balances[:4] if isinstance(balances, list) else []:
                if not isinstance(balance, dict):
                    continue
                en, zh = {
                    "key_credit_remaining":("Key credit remaining","Key 限额剩余"),
                    "account_credit_balance":("Account credits","账户积分余额"),
                    "account_available":("Available balance","可用余额"),
                    "cash_balance":("Cash balance","现金余额"),
                    "voucher_balance":("Voucher balance","代金券"),
                    "debt":("Amount owed","欠费"),
                }.get(label(balance.get("kind")), ("Account balance","账户余额"))
                amount = account_money(
                    balance.get("amount"), signed=balance.get("kind") in ("cash_balance", "account_credit_balance")
                )
                if amount is None:
                    continue
                currency = safe(balance.get("currency"))
                unit_en, unit_zh = (currency,currency) if currency else ("unit not reported","币种未标明")
                lines_en.append(f"{en} · {unit_en} {amount}")
                lines_zh.append(f"{zh} · {unit_zh} {amount}")
            if row.get("key_limit_unset"):
                lines_en.append("Key limit unset; account balance not returned")
                lines_zh.append("Key 未设置限额；账户余额未返回")
            if row.get("partial"):
                lines_en.append("Some quota windows were not reported")
                lines_zh.append("部分额度窗口未返回")
            checked = stamp(row.get("checked_at"))
            lines_en.append(f"API snapshot · {checked}")
            lines_zh.append(f"API 快照 · {checked}")
        balances = row.get("balances")
        known_balance = any(
            isinstance(b, dict) and b.get("kind") != "key_credit_remaining"
            and account_money(
                b.get("amount"), signed=b.get("kind") in ("cash_balance", "account_credit_balance")
            ) is not None
            for b in balances
        ) if isinstance(balances, list) else False
        unknown_en, unknown_zh = {
            "pending": ("pending query", "待查询"),
            "unsupported": ("adapter pending", "待接入"),
            "missing_credentials": ("credential reference missing", "待配置凭据"),
            "unavailable": ("query failed", "查询失败"),
        }.get(label(row.get("status")), ("not reported", "接口未返回"))
        expiry_unknown = not row.get("subscription_expires_on")
        expiry = stamp(row.get("subscription_expires_at"), year=True)
        if row.get("subscription_source") == "manual" and expiry != "—":
            expiry_unknown = False
            lines_en.append(f"Subscription expiry · {expiry} · manual record")
            lines_zh.append(f"订阅到期 · {expiry} · 手动记录")
        elif row.get("subscription_expires_on"):
            date = safe(row.get("subscription_expires_on"))
            lines_en.append(f"Plan period ends · {date} · API date, timezone unspecified")
            lines_zh.append(f"套餐有效期至 · {date} · API 日期，时区未标注")
        else:
            if known_balance:
                lines_en.append(f"Subscription expiry · — ({unknown_en})")
                lines_zh.append(f"订阅到期 · —（{unknown_zh}）")
            else:
                lines_en.append(f"Subscription expiry / account balance · — / — ({unknown_en})")
                lines_zh.append(f"订阅到期 / 账户余额 · — / —（{unknown_zh}）")
        if not known_balance and not expiry_unknown:
            lines_en.append(f"Account balance · — ({unknown_en})")
            lines_zh.append(f"账户余额 · —（{unknown_zh}）")
        if row.get("key_expires_at"):
            lines_en.append(f"API key expiry · {stamp(row['key_expires_at'], year=True)} (not subscription expiry)")
            lines_zh.append(f"Key 到期 · {stamp(row['key_expires_at'], year=True)}（不是订阅到期）")
        if row.get("subscription_renews_on"):
            date=safe(row.get("subscription_renews_on"))
            lines_en.append(f"Auto-renewal date · {date} (not final expiry)")
            lines_zh.append(f"自动续费日期 · {date}（不是最终到期）")
        if row.get("stale"):
            en, zh = failure(row.get("last_http_status"))
            when = stamp(row.get("last_attempt_at"))
            lines_en.append(f"Retained last successful snapshot · {en}" + (f" · {when}" if when != "—" else ""))
            lines_zh.append(f"保留上次成功快照 · {zh}" + (f" · {when}" if when != "—" else ""))
        children.append(markdown("\n".join(lines_en), "\n".join(lines_zh), "notation"))
    if not children:
        children.append(markdown("No configured account snapshot", "暂无配置账户快照", "notation"))
    if value.get("stale"):
        children.append(markdown("Previous snapshot · refresh pending", "上次快照 · 待刷新", "notation"))
    children.append(markdown(
        f"Configured accounts, not turn identity · {safe(timezone)} · reset ≠ expiry; absent balance is unknown.",
        f"配置账户概览，不推断本轮账户 · {safe(timezone)} · 重置≠到期；未返回余额保持未知。", "notation",
    ))
    return _panel("Subscription accounts · API snapshots", "订阅账户 · API 快照", children, "ref_accounts")
