"""V2 native layout. Reuse audited measurements and the existing single writer."""

# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..footer.layout import column
from ..footer.render import footer_annotations, safe
from ..footer.state import label, seconds
from ..footer.usage import mapping
from .reference import _history_tokens, _panel, build_account_panel, markdown


def _chrome(panel: dict[str, Any]) -> dict[str, Any]:
    """Native border is the separator; remove redundant leading rules only."""
    panel["vertical_spacing"] = "4px"
    if panel.get("elements") and panel["elements"][0].get("tag") == "hr":
        panel["elements"].pop(0)
    return panel


def _is_metric(row: dict[str, Any]) -> bool:
    columns = row.get("columns")
    return (row.get("tag") == "column_set" and isinstance(columns, list) and len(columns) == 2
        and all(len(c.get("elements", [])) == 2
                and all(e.get("tag") == "markdown" for e in c["elements"]) for c in columns))


def _inline_metric(row: dict[str, Any], *, short: bool = False) -> dict[str, Any]:
    result = deepcopy(row)
    replacements = {
        "本轮输入（含缓存）": "输入（含缓存）", "本轮输出": "输出",
        "缓存读取（部分） / 命中率下限": "缓存 / 命中下限",
        "缓存读取（部分） / 命中率": "缓存（部分） / 命中",
        "缓存读取 / 命中率": "缓存 / 命中", "API 请求 / 错误": "请求 / 错误",
        "Input incl. cache": "Input", "Cache read (partial) / hit lower bound": "Cache / hit ≥",
        "Cache read (partial) / hit": "Cache (partial) / hit", "Cache read / hit": "Cache / hit",
        "API attempts / errors": "Attempts / errors",
    }
    for cell in result["columns"]:
        key, value = cell["elements"]
        def combine(locale: str, key: dict[str, Any] = key, value: dict[str, Any] = value) -> str:
            title = key.get("i18n_content", {}).get(locale, key["content"])
            if short:
                for old, new in replacements.items():
                    title = title.replace(old, new)
            content = value.get("i18n_content", {}).get(locale, value["content"])
            return str(title) + "  " + str(content)
        cell["elements"] = [markdown(combine("en_us"), combine("zh_cn"), "notation")]
    return result


def compact_prefix(elements: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    result = deepcopy(elements)
    ref = mapping(data.get("reference"))
    for panel in result:
        _chrome(panel)
        if panel["element_id"] == "reference_tools":
            if ref.get("failed_total"):
                panel["header"]["title"]["text_color"] = "red"
            for element in panel["elements"]:
                if element.get("tag") == "collapsible_panel":
                    _chrome(element)
        else:
            panel["elements"] = [_inline_metric(e) if _is_metric(e) else e for e in panel["elements"]]
            host = mapping(ref.get("host"))
            pairs = []
            for key, name in (("gpu_percent", "GPU"), ("cpu_percent", "CPU")):
                value = seconds(host.get(key))
                if value is not None:
                    number = f"{value:.1f}".rstrip("0").rstrip(".")
                    pairs.append((f"{name} {number}%",) * 2)
            for prefix, en, zh in (("gpu", "VRAM", "显存"), ("ram", "RAM", "内存")):
                used, total = seconds(host.get(prefix+"_used_gib")), seconds(host.get(prefix+"_total_gib"))
                if used is not None and total and used <= total:
                    pairs.append((f"{en} {used/total:.0%}", f"{zh} {used/total:.0%}"))
            if pairs:
                panel["header"]["title"].update(content="🖥 " + " · ".join(en for en, _ in pairs),
                    i18n_content={"zh_cn":"🖥 " + " · ".join(zh for _, zh in pairs)})
    return result


def _history_panel(history: dict[str, Any]) -> dict[str, Any]:
    status = history.get("status")
    if status in {"pending", "unavailable", "no_history"}:
        en, zh = {
            "pending": ("Snapshot pending; next message can refresh", "快照待返回；后续消息可刷新"),
            "unavailable": ("Snapshot read failed; next message can retry", "快照读取失败；后续消息可重试"),
            "no_history": ("No recorded main requests yet", "尚无已记录的主请求"),
        }[status]
        return _chrome(_panel("◷ Local history", "◷ 本机历史", [markdown(en, zh, "notation")], "ref_v2_history"))
    periods = [_history_tokens(mapping(history.get(key))) for key in ("today", "month", "total")]
    children = [markdown(
        f"Main requests · input + output · since {safe(history.get('since')) or 'not recorded'} · "
        f"{safe(history.get('timezone')) or 'UTC'}",
        f"主请求 · 输入＋输出 · 自 {safe(history.get('since')) or '未记录'} 起 · "
        f"{safe(history.get('timezone')) or 'UTC'}", "notation")]
    models = history.get("models")
    if history.get("show_models") and isinstance(models, list) and models:
        children.append(markdown("Subscription / model · cumulative top 3", "订阅商 / 模型 · 累计前 3 项", "notation"))
        for item in models[:3]:
            item = mapping(item)
            subscription, model = label(item.get("subscription")), label(item.get("model"))
            names = safe(subscription[:32]) + ("…" if len(subscription) > 32 else "")
            model_name = safe(model[:48]) + ("…" if len(model) > 48 else "")
            total = _history_tokens(item)
            value = markdown(total, total, "notation")
            value["text_align"] = "right"
            children.append({"tag":"column_set", "flex_mode":"none", "horizontal_spacing":"8px",
                "columns":[column(2,[markdown(names,names,"notation")]),
                           column(4,[markdown(model_name,model_name,"notation")]), column(1,[value])]})
    children.append(markdown(
        "Local observed usage, not allowance/bill. * partial; ≥ lower bound; unknown stays —.",
        "本机可观测用量，非额度或账单。* 不完整；≥ 下限；未知保留 —。", "notation"))
    return _chrome(_panel(
        f"◷ History · today {periods[0]} · month {periods[1]} · total {periods[2]}",
        f"◷ 历史 · 今日 {periods[0]} · 本月 {periods[1]} · 累计 {periods[2]}", children, "ref_v2_history"))


def compact_footer(elements: list[dict[str, Any]], data: dict[str, Any], *, details: bool) -> list[dict[str, Any]]:
    panel = deepcopy(elements[0])
    original = panel["elements"]
    # V1 owns status, first-response, cache math and requested/returned IDs.
    # Preserve its values rather than introducing a second telemetry adapter.
    children = [deepcopy(original[1]), deepcopy(original[2])]
    if (not label(data.get("requested_model")) or not label(data.get("response_model"))
            or label(data.get("requested_model")) != label(data.get("response_model"))):
        children.append(deepcopy(original[3]))
    if details:
        metrics = [row for row in original if _is_metric(row)]
        children.extend(_inline_metric(row, short=True) for row in metrics)
        ref = mapping(data.get("reference"))
        history = ref.get("history")
        if isinstance(history, dict):
            children.append(_history_panel(history))
        accounts = ref.get("accounts")
        if isinstance(accounts, dict):
            timezone = history.get("timezone", "UTC") if isinstance(history, dict) else "UTC"
            children.append(_chrome(build_account_panel(accounts, timezone)))
        bounded = dict(data)
        routes = data.get("routes")
        if isinstance(routes, list):
            bounded["routes"] = [label(r)[:60] for r in routes[:3]]
        annotations, note = footer_annotations(bounded, "notation")
        children.extend(annotations)
        if data.get("usage_partial") or data.get("compression_observed"):
            children.append(note)
        if isinstance(routes, list) and len(routes) > 3:
            children.append(markdown("Only first three provider paths shown", "服务商路径仅展示前 3 项", "notation"))
    panel["elements"] = children
    panel["header"]["title"]["content"] = panel["header"]["title"]["content"].replace("🪙", "📊", 1)
    for locale, title in panel["header"]["title"].get("i18n_content", {}).items():
        panel["header"]["title"]["i18n_content"][locale] = title.replace("🪙", "📊", 1)
    return [_chrome(panel)]
