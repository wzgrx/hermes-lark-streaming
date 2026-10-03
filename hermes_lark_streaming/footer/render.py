"""Pure bilingual CardKit footer. All provider text is escaped and bounded."""

# ruff: noqa: RUF001

from __future__ import annotations

import html
import re
from typing import Any

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


def markdown(en: str, zh: str, text_size: str) -> dict[str, Any]:
    return {"tag": "markdown", "content": en, "i18n_content": {"en_us": en, "zh_cn": zh}, "text_size": text_size}


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
    model = safe(data.get("model"))
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
    if cached is not None and inp and cached <= inp and not data.get("usage_partial"):
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
    groups: list[dict[str, Any]] = []
    rows: list[tuple[str, str, str, str]] = []
    unknown_en, unknown_zh = "Not reported", "未提供"

    def row(en_key: str, zh_key: str, value: str, zh_value: str | None = None) -> None:
        rows.append((en_key, zh_key, value or unknown_en, (zh_value if zh_value is not None else value) or unknown_zh))

    def group(en_title: str, zh_title: str) -> None:
        # Each label/value pair shares a line inside one column, so long model
        # IDs wrap together instead of shifting all subsequent label rows.
        if groups:
            groups.append({"tag": "hr"})
        groups.append({
            "tag": "column_set", "flex_mode": "none", "horizontal_spacing": "12px",
            "columns": [
                {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top",
                 "elements": [markdown(f"**{en_title}**", f"**{zh_title}**", text_size)]},
                {"tag": "column", "width": "weighted", "weight": 3, "vertical_align": "top",
                 "elements": [markdown(
                     "\n".join(f"**{e}**: {v}" for e, _, v, _ in rows),
                     "\n".join(f"**{z}**：{v}" for _, z, _, v in rows), text_size)]},
            ],
        })
        rows.clear()

    for key, en_key, zh_key in (
        ("provider", "Provider", "服务商"),
        ("requested_model", "Requested model", "请求模型"),
        ("response_model", "Reported model", "返回模型"),
        ("api_mode", "API", "接口协议"),
    ):
        row(en_key, zh_key, safe(data.get(key)))
    row("Reasoning", "思考档位", reasoning + " (requested)" if reasoning else "",
        reasoning + "（请求参数）" if reasoning else "")
    group("A · Model & provider", "A · 这次用了谁")
    row("Wall time", "本轮总耗时", f"{duration:.1f}s" if duration is not None else "")
    first = seconds(data.get("first_response"))
    row("First response", "首次收到响应", f"{first:.2f}s" if first is not None else "")
    for key, en_key, zh_key in (
        ("api_calls", "Observed attempts", "已观测请求尝试"),
        ("tool_calls", "Tool calls", "工具调用"),
    ):
        n = count(data.get(key))
        row(en_key, zh_key, f"{n:,}" if n is not None else "")
    group("B · Timing", "B · 时间花在哪里")
    for key, en_key, zh_key in (
        ("input_tokens", "Input including cache", "输入（含缓存）"),
        ("output_tokens", "Output", "输出"),
        ("cache_read_tokens", "Cache read", "缓存读取"),
    ):
        n = count(data.get(key))
        row(en_key, zh_key, f"{n:,}" if n is not None else "")
    ratio = (f"{cached / inp:.1%}"
             if cached is not None and inp and cached <= inp and not data.get("usage_partial") else "")
    row("Cache hit rate", "缓存命中率", ratio)
    group("C · Turn usage", "C · 本轮累计用了多少")
    row("Last request context", "末次请求上下文", f"{used:,} / {maximum:,}" if used is not None and maximum else "")
    row("Context occupancy", "占用比例", f"{used / maximum:.1%}" if used is not None and maximum else "")
    row("Compression", "本轮压缩", "Not observed by this footer", "此页脚尚未观测")
    group("D · Context", "D · 对话有多长")
    routes = data.get("routes")
    if isinstance(routes, list) and routes:
        row("Route history", "服务商路径", " → ".join(safe(r) for r in routes[:12]))
    errors = count(data.get("retries"))
    row("Observed errors", "已观测请求错误", str(errors) if errors is not None else "")
    row("Billing", "计费", "Per-request cost not reported", "单次费用未提供")
    group("E · Routing & billing", "E · 切换与计费")
    groups.append(markdown(
        "Measured main requests in this turn; input includes cache. Missing fields are not estimated.",
        "本轮主请求累计；输入含缓存；缺失字段不推算。", "notation"))
    panel = {
        "tag": "collapsible_panel",
        "element_id": "footer_details",
        "expanded": False,
        "header": {
            "title": {
                "tag": "markdown",
                "content": "<font color='blue'>**Turn details**</font>",
                "i18n_content": {
                    "en_us": "<font color='blue'>**Turn details**</font>",
                    "zh_cn": "<font color='blue'>**本轮详情**</font>",
                },
            },
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "size": "16px 16px"},
            "icon_position": "left",
            "icon_expanded_angle": -180,
            "vertical_align": "center",
        },
        "padding": "8px 8px 8px 8px",
        "elements": groups,
    }
    elements.append(panel)
    return elements
