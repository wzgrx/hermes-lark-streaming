"""The 订阅与额度 section: one bar per quota window, balances as plain values, facts and failures as notes."""

# ruff: noqa: RUF001

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from ...card.model import Metric, Section
from ..account_adapters import money
from ..accounts import provider_products
from ..values import label, mapping
from .fmt import UNKNOWN, parse_instant, percent_value, span, stamp, zone

_TITLES = {
    "opencode-go": "OpenCode Go",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
    "siliconflow": "SiliconFlow",
    "alibaba": "Alibaba",
    "moonshot": "Kimi",
    "moonshot-global": "Kimi Global",
    "minimax": "MiniMax",
    "minimax-cn": "MiniMax CN",
    "zai": "Z.ai",
    "bigmodel": "智谱",
}
_WINDOWS = {
    "rolling": ("5小时", "5h"),
    "weekly": ("每周", "Weekly"),
    "monthly": ("每月", "Monthly"),
    "mcp_monthly": ("MCP 每月", "MCP monthly"),
}
_BALANCES = {
    "key_credit_remaining": ("Key 限额", "Key allowance"),
    "account_credit_balance": ("账户积分余额", "Account credits"),
    "account_available": ("可用余额", "Available"),
    "cash_balance": ("现金余额", "Cash"),
    "voucher_balance": ("代金券", "Vouchers"),
    "debt": ("欠费", "Owed"),
}
_SIGNED = {"cash_balance", "account_credit_balance"}
_STATUS_REASON = {
    "pending": "待查询",
    "unsupported": "待接入",
    "missing_credentials": "待配置凭据",
    "unavailable": "查询失败",
}


def _http(code: Any) -> str:
    return f" · HTTP {code}" if type(code) is int and 100 <= code <= 599 else ""


def _failure(code: Any) -> str:
    if type(code) is not int or not 100 <= code <= 599:
        return "API 读取失败"
    if code in {401, 403}:
        return f"API 凭据/权限错误 · HTTP {code}"
    if code == 429:
        return "API 限流 · HTTP 429"
    return f"API 读取失败 · HTTP {code}"


def _used(window: Mapping[str, Any]) -> float | None:
    """Used percent; derived from the remaining percent only when the provider reports that instead."""
    used, remaining = window.get("used_percent"), window.get("remaining_percent")
    if isinstance(used, int | float) and not isinstance(used, bool) and math.isfinite(used) and 0 <= used <= 10**6:
        return float(used)
    if (
        isinstance(remaining, int | float)
        and not isinstance(remaining, bool)
        and math.isfinite(remaining)
        and 0 <= remaining <= 100
    ):
        return 100.0 - remaining
    return None


def _reset_hint(raw: Any, tz: ZoneInfo, now: float) -> str:
    moment = parse_instant(raw)
    if moment is None:
        return "重置时间未知"
    delta = moment.timestamp() - now
    if delta <= 0:
        return "已过重置时间 · 待刷新"
    # A day or more away, the clock time says more than "4天7小时"; it also fits a narrow tile.
    return f"{stamp(raw, tz)} 重置" if delta >= 86400 else f"{span(delta)} 后重置"


def _windows(row: Mapping[str, Any]) -> list[Any]:
    raw = row.get("windows")
    return raw if isinstance(raw, list) else []


def _window_names(provider: str, windows: list[Any]) -> list[str]:
    present = {mapping(w).get("name") for w in windows}
    if provider == "opencode-go" and windows:
        return ["rolling", "weekly", "monthly"]
    return [name for name in _WINDOWS if name in present]


def _quota_metrics(row: Mapping[str, Any], tz: ZoneInfo, now: float, prefix: str) -> list[Metric]:
    windows = _windows(row)
    metrics = []
    for name in _window_names(str(row.get("provider")), windows)[:4]:
        window = next((mapping(w) for w in windows if mapping(w).get("name") == name), {})
        used = _used(window)
        zh, en = _WINDOWS[name]
        metrics.append(
            Metric(
                prefix + zh,
                percent_value(used) if used is not None else UNKNOWN,
                ratio=min(1.0, used / 100) if used is not None else None,
                hint=_reset_hint(window.get("reset_at"), tz, now) if window else "API 未返回",
                label_en=en,
            )
        )
    return metrics


def _balance_metrics(row: Mapping[str, Any], prefix: str, reason: str) -> list[Metric]:
    raw = row.get("balances")
    metrics: list[Metric] = []
    wallet = False
    for item in raw[:4] if isinstance(raw, list) else []:
        entry = mapping(item)
        kind = label(entry.get("kind"))
        amount = money(entry.get("amount"), signed=kind in _SIGNED)
        if amount is None:
            continue
        currency = label(entry.get("currency"))
        zh, en = _BALANCES.get(kind, ("账户余额", "Account balance"))
        wallet = wallet or kind != "key_credit_remaining"
        hint = "" if currency else "币种未标明"
        metrics.append(Metric(prefix + zh, f"{currency} {amount}".strip(), hint=hint, label_en=en))
    if not wallet:
        metrics.insert(0, Metric(prefix + "账户余额", UNKNOWN, hint=reason, label_en="Balance"))
    return metrics


def _expiry_notes(row: Mapping[str, Any], tz: ZoneInfo, reason: str) -> list[str]:
    notes = []
    expires = stamp(row.get("subscription_expires_at"), tz, year=True)
    if expires != UNKNOWN:
        source = "手动记录" if row.get("subscription_source") == "manual" else "API 时间"
        notes.append(f"订阅到期：{expires}（{source}）")
    elif row.get("subscription_expires_on"):
        notes.append(f"套餐有效期至：{label(row.get('subscription_expires_on'))}（API 日期，时区未标注）")
    else:
        status = row.get("subscription_status")
        why = {"not_active": "API 未发现有效套餐", "unavailable": "订阅权益查询失败"}.get(str(status), reason)
        notes.append(f"订阅到期：{UNKNOWN}（{why}）")
    if row.get("key_expires_at"):
        notes.append(f"Key 到期：{stamp(row['key_expires_at'], tz, year=True)}（不是订阅到期）")
    if row.get("subscription_renews_on"):
        notes.append(f"自动续费日期：{label(row['subscription_renews_on'])}（不是最终到期）")
    if row.get("key_limit_unset"):
        notes.append("Key 未设置限额（不是无限余额）")
    return notes


def _row(
    row: Mapping[str, Any], tz: ZoneInfo, now: float, *, terminal: bool, multi: bool
) -> tuple[list[Metric], list[str]]:
    name = label(row.get("label"))
    prefix = f"{name} · " if multi and name else ""
    status = label(row.get("status"))
    reason = {"pending": "本轮快照未就绪；后续消息刷新" if terminal else "待查询"}.get(
        status, _STATUS_REASON.get(status, "API 未返回")
    )
    notes: list[str] = []
    if status != "ok":
        if row.get("reason") == "endpoint_retired":
            reason = f"官方账户接口已退役 · {label(row.get('retired_on'))}"
        elif status == "unavailable" and type(row.get("http_status")) is int:
            reason = f"查询失败 · {_failure(row.get('http_status'))}"
        notes.append(reason)
    metrics = _quota_metrics(row, tz, now, prefix) if status == "ok" else []
    metrics += _balance_metrics(row, prefix, reason)
    if status == "ok" and row.get("partial"):
        notes.append("部分额度窗口未返回")
    notes += _expiry_notes(row, tz, reason)
    checked = stamp(row.get("checked_at"), tz)
    if checked != UNKNOWN and row.get("source") != "none":
        notes.append(f"{'API 快照' if status == 'ok' else '最近查询'} · {checked}")
    if row.get("stale"):
        when = stamp(row.get("last_attempt_at"), tz)
        suffix = f" · {when}" if when != UNKNOWN else ""
        notes.append("保留上次成功快照；最近读取失败" + _http(row.get("last_http_status")) + suffix)
    if row.get("discovered"):
        notes.append("自动候选，非显式配置")
    if multi and name:
        notes = [f"{name}：{n}" for n in notes]
    elif name:
        notes.insert(0, f"账户：{name}")
    return metrics, notes


def accounts_section(
    snapshot: Mapping[str, Any],
    timezone: str,
    *,
    terminal: bool = False,
    now: float | None = None,
) -> Section:
    tz = zone(timezone)
    clock = time.time() if now is None else now
    provider = label(snapshot.get("active_provider"))
    provider_rows = snapshot.get("accounts")
    products = provider_products(provider)
    rows = [
        mapping(r)
        for r in (provider_rows if isinstance(provider_rows, list) else [])
        if isinstance(r, dict) and (not products or r.get("provider") in products)
    ][:4]
    metrics: list[Metric] = []
    notes: list[str] = []
    if not rows:
        pending = snapshot.get("status") == "pending"
        notes.append(
            ("本轮快照未就绪；后续消息刷新" if terminal else "快照未就绪") if pending else "当前订阅商暂无配置账户快照"
        )
    for row in rows:
        row_metrics, row_notes = _row(row, tz, clock, terminal=terminal, multi=len(rows) > 1)
        metrics += row_metrics
        notes += row_notes
    if snapshot.get("stale"):
        notes.append("上次快照 · 待刷新")
    if metrics:
        notes.append(tz.key)  # reset clocks are shown in this zone
    title = _TITLES.get(provider, provider)
    return Section(
        "accounts",
        f"{title} · 订阅与额度" if title else "订阅与额度",
        tuple(metrics),
        tuple(notes),
        title_en="Subscription / quota",
        layout="bars",
    )
