"""V1 screenshot-reference layout; pure native Card 2.0, no I/O or inference."""

# ruff: noqa: RUF001
from __future__ import annotations

import html
import json
import re
from typing import Any

from ..footer.layout import column
from ..footer.layout import markdown as bilingual_markdown
from ..footer.render import compact, safe
from ..footer.state import label, seconds
from ..footer.usage import count
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


def _duration(value: Any) -> str:
    n = seconds(value)
    if n is None:
        return ""
    if n < 1:
        return f"{n * 1000:.0f}ms"
    return f"{int(n // 60)}m {int(n % 60):02d}s" if n >= 60 else f"{n:.1f}s"


def _tool_text(value: str, limit: int = 160) -> str:
    text = _SECRET.sub("[redacted]", redact_inline_secrets(value[: limit * 2]))
    escaped = re.sub(r"([\\`*_\[\]~])", r"\\\1", html.escape(text))
    encoded = escaped.encode()
    return escaped if len(encoded) <= limit else encoded[:limit].decode(errors="ignore").rstrip("\\") + "…"


def _tool_title(step: ToolDisplayStep) -> str:
    # Tracker titles already include timing; V1 has a dedicated timing column.
    return re.sub(r" \([0-9.]+ (?:s|ms)\)$", "", step.get("title") or step["name"])


def _tool_groups(steps: list[ToolDisplayStep]) -> list[tuple[int, int, ToolDisplayStep, float]]:
    """Only adjacent successful output-free process polls merge; commands never merge."""
    groups: list[tuple[int, int, ToolDisplayStep, float]] = []
    for i, step in enumerate(steps):
        elapsed = (seconds(step.get("elapsed_ms")) or 0) / 1000
        poll = (
            step["name"].lower() in {"process", "process_poll", "poll_process"}
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
                groups[-1] = start, i, previous, total + elapsed
                continue
        groups.append((i, i, step, elapsed))
    return groups


def build_tools(data: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    steps: list[ToolDisplayStep] = reference.get("steps", [])
    prior = count(reference.get("tools_prior")) or 0
    prior_done = count(reference.get("done_prior")) or 0
    prior_failed = count(reference.get("failed_prior")) or 0
    total = prior + len(steps)
    done = prior_done + sum(s["status"] in {"success", "error"} for s in steps)
    failures = prior_failed + sum(s["status"] == "error" for s in steps)
    elapsed = _duration(data.get("duration"))
    tail_en = f" · {failures} failed" if failures else ""
    tail_zh = f" · {failures} 失败" if failures else ""
    elapsed_part = f" · {elapsed}" if elapsed else ""
    en = f"🛠 Tools{elapsed_part} · {done}/{total} ended{tail_en}"
    zh = f"🛠 工具执行{elapsed_part} · {done}/{total} 结束{tail_zh}"
    children: list[dict[str, Any]] = []
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
    # Fixed budget; errors/running steps take priority, but chosen rows retain
    # their original order. Older failures stay explicitly counted.
    selected = [i for i, group in enumerate(groups) if group[2]["status"] in {"error", "running"}][-8:]
    for i in range(len(groups) - 1, -1, -1):
        if len(selected) >= 8:
            break
        if i not in selected:
            selected.append(i)
    shown = [groups[i] for i in sorted(selected)]
    omitted = max(0, len(groups) - 8)
    if omitted or prior:
        children.append(
            markdown(
                f"{omitted} groups omitted · {prior} steps on earlier cards · {failures} total failures",
                f"{omitted} 组未展示 · 前卡 {prior} 步 · 全轮失败 {failures} 次",
                "notation",
            )
        )
    for start, end, step, elapsed_s in shown:
        number = f"{prior + start + 1:02d}" if start == end else f"{prior + start + 1:02d}–{prior + end + 1:02d}"
        name = _tool_text(_tool_title(step), 100)
        error_detail = ""
        if step["status"] == "error":
            block = step.get("error_block")
            error_detail = _tool_text(str((block.get("content") if block else "") or step.get("error") or ""), 100)
        name_content = name + (f"\n<font color='red'>{error_detail}</font>" if error_detail else "")
        copies = end - start + 1
        state = {"success": ("Succeeded", "成功", "green"), "error": ("Failed", "失败", "red")}.get(
            step["status"],
            ("Running", "运行中", "blue"),
        )
        repeat_en, repeat_zh = (f" · {copies} calls", f" · {copies} 次") if copies > 1 else ("", "")
        status_text = markdown(
            f"<font color='{state[2]}'>{state[0]}{repeat_en}</font>",
            f"<font color='{state[2]}'>{state[1]}{repeat_zh}</font>",
            "notation",
        )
        status_text["text_align"] = "right"
        timing = markdown(
            f"<font color='grey'>{_duration(elapsed_s)}</font>",
            f"<font color='grey'>{_duration(elapsed_s)}</font>",
            "notation",
        )
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
        detail = _tool_text(step.get("detail", ""), 180)
        block = step.get("error_block") or step.get("result_block")
        output = _tool_text(
            str((block.get("content") if block else "") or step.get("error") or step.get("output") or ""), 200
        )
        raw.append((i,
            f"**{prior + i + 1} · {_tool_text(_tool_title(step), 70)} · {status}**\n"
            f"{detail}" + (f"\n{output}" if output else "")
        ))
    # Long escaped failures and localized status rows consume bytes before raw
    # records. Keep the complete native tools panel within its 13 KB share.
    rows_bytes = len(json.dumps(children, ensure_ascii=False, separators=(",", ":")).encode())
    raw_limit = max(1000, min(4000, 12500 - rows_bytes))
    while len(raw) > 1 and (len(raw) > 24 or len("\n\n".join(text for _, text in raw).encode()) > raw_limit):
        drop = next((pos for pos, (index, _) in enumerate(raw) if index not in important), 0)
        raw.pop(drop)
    if raw:
        children.append(
            _panel(
                f"Step excerpts · {len(raw)}/{len(steps)} · errors first, bounded output",
                f"步骤摘要 · {len(raw)}/{len(steps)} 步 · 优先保留异常，输出限长",
                [{"tag": "markdown", "content": "\n\n".join(text for _, text in raw), "text_size": "notation"}],
                "ref_tool_records",
            )
        )
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

    title = f"🖥 GPU {gpu} · {temperature} · VRAM {memory_title('gpu')} · RAM {memory_title('ram')}"
    uptime_s = seconds(host.get("uptime"))
    uptime = f"{int(uptime_s // 86400)}d {int(uptime_s % 86400 // 3600)}h" if uptime_s is not None else ""
    uptime_zh = f"{int(uptime_s // 86400)}天{int(uptime_s % 86400 // 3600)}小时" if uptime_s is not None else ""
    processes = count(host.get("processes"))
    extra = f" · Processes {processes} · Uptime {uptime}" if processes is not None and uptime else ""
    extra_zh = f" · 进程 {processes} · 运行 {uptime_zh}" if processes is not None and uptime else ""
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
    return _panel(title, title, children, RESOURCES_ID)


def build_reference_prefix(data: dict[str, Any]) -> list[dict[str, Any]]:
    ref = data.get("reference", {})
    elements = [build_tools(data, ref)] if ref.get("show_tools") else []
    if ref.get("resources_enabled"):
        elements.append(build_resources(ref.get("host") or {}))
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
    # Reuse the audited requested/returned-ID equality and escaping contract.
    from ..footer.render import footer_annotations, model_identity_line

    bounded = dict(data)
    routes = data.get("routes")
    if isinstance(routes, list):
        bounded["routes"] = [label(r)[:60] for r in routes[:3]]
    model = label(data.get("model")) or "Model pending / 模型待返回"
    used, maximum = count(data.get("context_used")), count(data.get("context_max"))
    maximum_label = f"{maximum / 1_000_000:.1f}M" if maximum and maximum % 1_000_000 == 0 else compact(maximum or 0)
    context = (
        f"{compact(used)}/{maximum_label} ({used / maximum:.0%})"
        if used is not None and maximum
        else "Context pending / 上下文待返回"
    )
    title = f"🪙 {model} · {context}"
    if data.get("usage_partial"):
        title += " · Partial / 不完整"
    title_zh = title
    if is_error or is_aborted:
        title = ("✕ Failed · " if is_error else "◼ Stopped · ") + title
        title_zh = ("✕ 本轮失败 · " if is_error else "◼ 已停止 · ") + title_zh
    en, zh, color = (
        ("Failed", "本轮失败", "red")
        if is_error
        else (("Stopped", "已停止", "grey") if is_aborted else ("Answer completed", "回答已完成", "green"))
    )
    duration = _duration(data.get("duration"))
    icon = "✕" if is_error else ("◼" if is_aborted else "✓")
    status = markdown(
        f"<font color='{color}'>{icon} {en} · {duration}</font>",
        f"<font color='{color}'>{icon} {zh} · {duration}</font>",
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
    inp, cached = count(data.get("input_tokens")), count(data.get("cache_read_tokens"))
    cache = (
        f"{compact(cached)} / {cached / inp:.1%}"
        if cached is not None and inp and cached <= inp and not data.get("usage_partial")
        else ""
    )
    requests, errors = count(data.get("api_calls")), count(data.get("retries"))
    attempts = f"{requests} / {errors}" if requests is not None and errors is not None else ""
    attempts_zh = f"{requests} 次 / {errors} 次" if attempts else ""
    if live_status is None and failures is not None:
        succeeded = count(ref.get("succeeded_total"))
        if succeeded is None:
            succeeded = sum(s["status"] == "success" for s in ref.get("steps", []))
        tools = markdown(
            f"<font color='grey'>Tools: {succeeded} succeeded / {failures} failed</font>",
            f"<font color='grey'>工具：{succeeded} 成功 / {failures} 失败</font>",
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
                    _metric("Cache read / hit", "缓存读取 / 命中率", (cache, cache) if cache else _UNKNOWN),
                    _metric(
                        "API attempts / errors", "API 请求 / 错误", (attempts, attempts_zh) if attempts else _UNKNOWN
                    ),
                    text_size,
                ),
                metric_row(
                    _metric(
                        "First response incl. retry", "首响应（含重试）",
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
                children.append(markdown(f"◷ {en_status}", f"◷ {zh_status}", text_size))
            if history_status not in {"pending", "unavailable", "no_history"}:
                periods = []
                for key, en_label, zh_label in (
                    ("today", "Today", "今日"),
                    ("month", "Month", "本月"),
                    ("total", "Total", "累计"),
                ):
                    bucket = history.get(key, {})
                    n = count(bucket.get("tokens"))
                    v = compact(n) if n is not None else "—"
                    partial = "*" if bucket.get("partial") else ""
                    periods.append((f"{en_label} {v}{partial}", f"{zh_label} {v}{partial}"))
                children.append({"tag": "hr"})
                children.append(
                    markdown(
                        "◷ History · <font color='grey'>" + " · ".join(p[0] for p in periods) + "</font>",
                        "◷ 历史用量 · <font color='grey'>" + " · ".join(p[1] for p in periods) + "</font>",
                        text_size,
                    )
                )
                history_partial = any(history.get(k, {}).get("partial") for k in ("today", "month", "total"))
                partial_en, partial_zh = (" · * partial", " · * 不完整") if history_partial else ("", "")
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
                    rows = [markdown("By subscription / model", "按订阅商 / 模型复盘", text_size)]
                    for item in history["models"][:3]:
                        subscription = label(item.get("subscription"))
                        model_label = label(item.get("model"))
                        name = safe(subscription[:32]) + ("…" if len(subscription) > 32 else "")
                        model_value = safe(model_label[:48]) + ("…" if len(model_label) > 48 else "")
                        tokens = compact(item["tokens"]) if count(item.get("tokens")) is not None else "—"
                        if item.get("partial"):
                            tokens += "*"
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
