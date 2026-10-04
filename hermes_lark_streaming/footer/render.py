"""Pure bilingual CardKit footer. All provider text is escaped and bounded."""

# ruff: noqa: RUF001

from __future__ import annotations

import html
import re
from typing import Any

from ..cardkit.panels import collapsible_panel
from .layout import markdown, metric_row
from .state import label, seconds
from .usage import count


def safe(value: Any) -> str:
    return re.sub(r"([\\`*_\[\]~])", r"\\\1", html.escape(label(value)))


def compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(value)


def model_display(value: Any) -> str:
    """Humanize a known bare ID only; keep provider prefixes and fine-tunes intact."""
    raw = label(value)
    match = re.fullmatch(r"deepseek-v(\d+(?:\.\d+)?)-(flash|pro)", raw, re.I)
    if match:
        return f"DeepSeek V{match[1]} {match[2].title()}"
    return safe(raw)


def model_identity_line(data: dict[str, Any], text_size: str) -> dict[str, Any]:
    """Shared exact-ID comparison; reference layout does not index another layout."""
    unknown_en, unknown_zh = "Not reported", "未提供"
    requested, returned = safe(data.get("requested_model")), safe(data.get("response_model"))
    # The collector bounds labels at 160 characters. Equal bounded prefixes do
    # not prove equal IDs; retain both rows at that boundary or after escaping.
    same_model = (
        isinstance(data.get("requested_model"), str)
        and 0 < len(data["requested_model"]) < 160
        and data["requested_model"] == data.get("response_model")
    )
    if same_model:
        model_line = markdown(
            f"<font color='grey'>Requested = reported</font> {requested}",
            f"<font color='grey'>请求＝返回</font> {requested}", text_size,
        )
    else:
        model_line = markdown(
            f"<font color='grey'>Requested</font> {requested or unknown_en}\n"
            f"<font color='grey'>Reported</font> {returned or unknown_en}",
            f"<font color='grey'>请求</font> {requested or unknown_zh}\n"
            f"<font color='grey'>返回</font> {returned or unknown_zh}", text_size,
        )
    return model_line


def footer_annotations(data: dict[str, Any], text_size: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Shared route/error annotations and an explicit measurement caveat."""
    annotations: list[dict[str, Any]] = []
    routes = data.get("routes")
    if isinstance(routes, list) and len(routes) > 1:
        route_text = " → ".join(safe(r) for r in routes[:12])
        annotations.append(markdown(
            f"<font color='orange'>Provider path</font> {route_text}",
            f"<font color='orange'>服务商路径</font> {route_text}", text_size,
        ))
    error_type = safe(data.get("last_error_type"))
    if error_type:
        annotations.append(markdown(
            f"<font color='orange'>Latest API error type</font> {error_type}",
            f"<font color='orange'>最近 API 错误类型</font> {error_type}", text_size,
        ))
    note_en = "Main requests only; cost not reported · compression not observed"
    note_zh = "主请求累计；费用未提供 · 压缩尚未观测"
    if data.get("compression_observed"):
        note_en = "Main requests only; cost not reported · summary observed; compression commit unverified"
        note_zh = "主请求累计；费用未提供 · 已观测摘要请求，压缩提交待确认"
    if data.get("usage_partial"):
        note_en, note_zh = "Partial usage · " + note_en, "统计不完整 · " + note_zh
    note = markdown(f"<font color='grey'>{note_en}</font>", f"<font color='grey'>{note_zh}</font>", "notation")
    return annotations, note


def build_footer(
    data: dict[str, Any],
    *,
    is_error: bool = False,
    is_aborted: bool = False,
    text_size: str = "notation",
    details: bool = True,
) -> list[dict[str, Any]]:
    status = ("Failed", "本轮失败") if is_error else ("Stopped", "已停止") if is_aborted else ("Completed", "完成")
    first_en, first_zh = [status[0]], [status[1]]
    duration = seconds(data.get("duration"))
    if duration is not None:
        first_en.append(f"{duration:.1f}s")
        first_zh.append(f"{duration:.1f}s")
    model = model_display(data.get("model"))
    if model:
        first_en.append(model)
        first_zh.append(model)
    reasoning = safe(data.get("reasoning"))
    if reasoning:
        first_en.append(f"{reasoning} (requested)")
        first_zh.append(f"{reasoning}（请求）")
    second_en: list[str] = []
    second_zh: list[str] = []
    inp, out = count(data.get("input_tokens")), count(data.get("output_tokens"))
    if inp is not None and out is not None:
        value = f"↑{compact(inp)} ↓{compact(out)}"
        partial = bool(data.get("usage_partial"))
        second_en.append(f"{'Measured' if partial else 'Turn'} {value}")
        second_zh.append(f"{'已统计' if partial else '本轮'} {value}")
    used, maximum = count(data.get("context_used")), count(data.get("context_max"))
    if used is not None and maximum:
        value = f"{compact(used)}/{compact(maximum)} ({used / maximum:.1%})"
        second_en.append(f"Last context {value}")
        second_zh.append(f"末次上下文 {value}")
    cached = count(data.get("cache_read_tokens"))
    cache_partial = bool(data.get("cache_read_partial"))
    if cached is not None and inp and cached <= inp and not data.get("usage_partial") and not cache_partial:
        second_en.append(f"Cache {cached / inp:.0%}")
        second_zh.append(f"缓存 {cached / inp:.0%}")
    if data.get("usage_partial"):
        second_en.append("Partial usage")
        second_zh.append("统计不完整")
    elif data.get("telemetry_missing"):
        second_en.append("Turn usage unavailable")
        second_zh.append("本轮统计待采集")
    summary = markdown(" · ".join(first_en), " · ".join(first_zh), text_size)
    summary.update(element_id="footer_status", icon={
        "tag": "standard_icon", "token": "info_outlined" if is_error or is_aborted else "done_outlined",
        "color": "red" if is_error else "grey" if is_aborted else "green",
    })
    elements: list[dict[str, Any]] = [{"tag": "hr", "element_id": "footer_separator"}, summary]
    if second_en:
        usage = markdown(" · ".join(second_en), " · ".join(second_zh), text_size)
        usage.update(element_id="footer_usage", icon={
            "tag": "standard_icon", "token": "info_outlined", "color": "blue",
        })
        elements.append(usage)
    if not details:
        return elements
    unknown_en, unknown_zh = "Not reported", "未提供"

    def display_value(raw: Any) -> tuple[str, str]:
        text = safe(raw)
        return (text, text) if text else (unknown_en, unknown_zh)

    def number(key: str) -> tuple[str, str]:
        n = count(data.get(key))
        prefix = "≥" if key == "cache_read_tokens" and cache_partial else ""
        return (f"{prefix}{n:,}", f"{prefix}{n:,}") if n is not None else (unknown_en, unknown_zh)

    def metric(en: str, zh: str, values: tuple[str, str]) -> tuple[str, str, str, str]:
        return en, zh, *values

    provider_en, provider_zh = display_value(data.get("provider"))
    api_en, api_zh = display_value(data.get("api_mode"))
    effort_en, effort_zh = (
        (f"{reasoning} (requested)", f"{reasoning}（请求）") if reasoning
        else ("Not reported", "未提供")
    )
    if data.get("reasoning_missing_reason") == "request_truncated" and not reasoning:
        effort_en, effort_zh = "Request metadata truncated", "请求字段已裁剪"
    identity = markdown(
        f"<font color='grey'>Provider</font> {provider_en} · {api_en} · Reasoning {effort_en}",
        f"<font color='grey'>服务</font> {provider_zh} · {api_zh} · 思考 {effort_zh}", text_size,
    )
    model_line = model_identity_line(data, text_size)
    first = seconds(data.get("first_response"))
    elapsed_value = (f"{duration:.1f}s", f"{duration:.1f}s") if duration is not None else (unknown_en, unknown_zh)
    first_value = (f"{first:.2f}s", f"{first:.2f}s") if first is not None else (unknown_en, unknown_zh)
    ratio = (f"{cached / inp:.1%}"
             if cached is not None and inp and cached <= inp
             and not data.get("usage_partial") and not cache_partial else "")
    attempts_en, attempts_zh = number("api_calls")
    errors = count(data.get("retries"))
    if errors is not None:
        attempts_en += f" · {errors} errors"
        attempts_zh += f" · 错误 {errors}"
    groups = [
        identity, model_line,
        metric_row(metric("Wall time", "总耗时", elapsed_value),
                   metric("First response", "首响应", first_value), text_size),
        metric_row(metric("Input incl. cache", "输入（含缓存）", number("input_tokens")),
                   metric("Output", "输出", number("output_tokens")), text_size),
        metric_row(metric("Cache read (partial)" if cache_partial else "Cache read",
                          "缓存读取（部分）" if cache_partial else "缓存读取", number("cache_read_tokens")),
                   metric("Cache hit", "命中率", (ratio, ratio) if ratio else (unknown_en, unknown_zh)), text_size),
        metric_row(metric("Attempts", "请求尝试", (attempts_en, attempts_zh)),
                   metric("Tools", "工具", number("tool_calls")), text_size),
    ]
    context = f"{used:,} / {maximum:,} · {used / maximum:.1%}" if used is not None and maximum else ""
    groups.append(markdown(
        f"<font color='grey'>Last context</font> {context or unknown_en}",
        f"<font color='grey'>末次上下文</font> {context or unknown_zh}", text_size,
    ))
    annotations, note = footer_annotations(data, text_size)
    groups.extend(annotations)
    groups.append(note)
    # Use the same native chrome as background review, not a second UI system.
    panel = collapsible_panel(
        expanded=False,
        title_el={
            "tag": "plain_text",
            "content": "📊 Turn details",
            "i18n_content": {"en_us": "📊 Turn details", "zh_cn": "📊 本轮详情"},
            "text_color": "grey",
            "text_size": "notation",
        },
        elements=groups,
        vertical_spacing="8px",
    )
    panel["element_id"] = "footer_details"
    elements.append(panel)
    return elements
