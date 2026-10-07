"""The 用量 section: this turn, then the local-ledger history."""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...card.model import Metric, Section
from ..usage import cache_ratio
from ..values import count, label, mapping, seconds
from .fmt import UNKNOWN, compact, percent

_HISTORY_NOTES = {
    "pending": "正在读取本机历史",
    "unavailable": "历史读取失败或超时，稍后重试",
    "no_history": "尚无已记录的主请求",
}


def _seconds(value: Any, digits: int = 1) -> str:
    """``1.2s`` below a minute, ``1m 43s`` above."""
    n = seconds(value)
    if n is None:
        return UNKNOWN
    if n < 60:
        return f"{n:.{digits}f}s"
    minutes, rest = divmod(int(n), 60)
    return f"{minutes}m {rest:02d}s"


def _turn_metrics(data: Mapping[str, Any]) -> tuple[Metric, ...]:
    """The few turn figures worth a second look; model, context and wall time are already in the footer and status."""
    partial_cache = bool(data.get("cache_read_partial"))
    ratio, floor = cache_ratio(dict(data))
    calls, errors = count(data.get("api_calls")), count(data.get("retries"))
    group = "本轮"
    metrics = [
        Metric("输入", compact(count(data.get("input_tokens"))), label_en="Input", group=group),
        Metric("输出", compact(count(data.get("output_tokens"))), label_en="Output", group=group),
        Metric(
            "缓存",
            percent(ratio, floor=floor),
            ratio=ratio,
            label_en="Cache",
            group=group,
        ),
        Metric("首响应", _seconds(data.get("first_response")), label_en="First response", group=group),
    ]
    if partial_cache:
        read = count(data.get("cache_read_tokens"))
        metrics.append(Metric("缓存读取", "≥" + compact(read, lower_bound=True) if read is not None else UNKNOWN,
                              group=group))
    if calls is not None and (calls > 1 or errors):
        metrics.append(Metric("请求", f"{calls}" + (f" · 错误 {errors}" if errors else ""), label_en="Requests",
                              group=group))
    reasoning = label(data.get("reasoning"))
    if reasoning:
        metrics.append(Metric("思考", reasoning, label_en="Reasoning", group=group))
    return tuple(metrics)


def _history_tokens(bucket: Mapping[str, Any]) -> str:
    tokens = count(bucket.get("tokens"))
    if tokens is None:
        return UNKNOWN
    partial = bool(bucket.get("partial"))
    return f"{'≥' if partial else ''}{compact(tokens, lower_bound=partial)}"


def _history(history: Mapping[str, Any], *, terminal: bool, show_models: bool) -> tuple[tuple[Metric, ...], list[str]]:
    status = history.get("status")
    if status in _HISTORY_NOTES:
        note = _HISTORY_NOTES[str(status)]
        if terminal and status in {"pending", "unavailable"}:
            note = "本轮历史快照尚未就绪；后续消息可重试"
        return (), [note]
    label(history.get("since"))
    group = "累计"
    metrics = [
        Metric(title, _history_tokens(mapping(history.get(key))), label_en=en, group=group)
        for key, title, en in (("today", "今日", "Today"), ("month", "本月", "Month"), ("total", "总计", "Total"))
    ]
    if show_models:
        for item in history.get("models", [])[:3] if isinstance(history.get("models"), list) else []:
            row = mapping(item)
            name = label(row.get("model"))[:48] or UNKNOWN
            metrics.append(Metric(name, _history_tokens(row), hint=label(row.get("subscription"))[:32],
                                  group="模型累计"))
    notes: list[str] = []
    if any(mapping(history.get(k)).get("partial") for k in ("today", "month", "total")):
        notes.append("* 部分请求缺少用量，≥ 表示已观测下限")
    return tuple(metrics), notes


def usage_section(
    turn: Mapping[str, Any] | None,
    history: Mapping[str, Any] | None,
    *,
    terminal: bool = False,
    show_models: bool = True,
) -> Section:
    data = turn or {}
    metrics = list(_turn_metrics(data))
    notes: list[str] = []
    if not data or data.get("telemetry_missing"):
        notes.append("本轮统计待采集")
    elif data.get("usage_partial"):
        notes.append("统计不完整")
    routes = data.get("routes")
    if isinstance(routes, list) and len(routes) > 1:
        notes.append("服务商路径 " + " → ".join(label(r) for r in routes[:12]))
    if label(data.get("last_error_type")):
        notes.append(f"最近 API 错误类型 {label(data.get('last_error_type'))}")
    if history is not None:
        rows, history_notes = _history(history, terminal=terminal, show_models=show_models)
        metrics.extend(rows)
        notes.extend(history_notes)
    return Section("usage", "用量", tuple(metrics), tuple(notes), title_en="Usage")
