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
    en = " · ".join(first_en) + ("\n" + " · ".join(second_en) if second_en else "")
    zh = " · ".join(first_zh) + ("\n" + " · ".join(second_zh) if second_zh else "")
    elements = [{"tag": "hr"}, markdown(en, zh, text_size)]
    if not details:
        return elements
    en_rows: list[str] = []
    zh_rows: list[str] = []

    def row(en_key: str, zh_key: str, value: str, zh_value: str | None = None) -> None:
        if value:
            en_rows.append(f"**{en_key}**: {value}")
            zh_rows.append(f"**{zh_key}**：{zh_value if zh_value is not None else value}")

    for key, en_key, zh_key in (
        ("provider", "Provider", "服务商"),
        ("requested_model", "Requested model", "请求模型"),
        ("response_model", "Reported model", "返回模型"),
        ("api_mode", "API", "接口协议"),
    ):
        row(en_key, zh_key, safe(data.get(key)))
    if reasoning:
        row("Reasoning", "思考档位", reasoning + " (requested, not server confirmation)", reasoning + "（请求参数）")
    if duration is not None:
        row("Wall time", "本轮总耗时", f"{duration:.1f}s")
    first = seconds(data.get("first_response"))
    if first is not None:
        row("First response", "首个响应块", f"{first:.2f}s")
    for key, en_key, zh_key in (
        ("api_calls", "Observed attempts", "已观测请求尝试"),
        ("tool_calls", "Tool calls", "工具调用"),
        ("input_tokens", "Input including cache", "输入（含缓存）"),
        ("output_tokens", "Output", "输出"),
        ("cache_read_tokens", "Cache read", "缓存读取"),
        ("retries", "Observed errors", "已观测请求错误"),
    ):
        n = count(data.get(key))
        if n is not None:
            row(en_key, zh_key, f"{n:,}")
    routes = data.get("routes")
    if isinstance(routes, list) and len(routes) > 1:
        row("Route history", "服务商路径", " → ".join(safe(r) for r in routes[:12]))
    row(
        "Scope",
        "统计口径",
        "Current turn; input includes cache; last request context",
        "本轮主请求累计；输入含缓存；上下文为末次请求",
    )
    row("Missing data", "缺失字段", "Hidden; no account cost or quota is inferred", "未返回字段隐藏；费用与额度不推算")
    panel = {
        "tag": "collapsible_panel",
        "element_id": "footer_details",
        "expanded": False,
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "Turn details",
                "i18n_content": {"en_us": "Turn details", "zh_cn": "本轮详情"},
            },
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "size": "16px 16px"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
            "vertical_align": "center",
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "padding": "8px 8px 8px 8px",
        "elements": [markdown("\n".join(en_rows), "\n".join(zh_rows), text_size)],
    }
    elements.append(panel)
    return elements
