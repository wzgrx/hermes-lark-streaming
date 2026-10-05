"""Compact current-provider account facts; no I/O or inferred billing data."""

# ruff: noqa: RUF001
from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..footer.account_adapters import money
from ..footer.accounts import provider_products
from ..footer.layout import column
from ..footer.render import safe
from ..footer.state import label
from .reference import _panel, markdown


def build_current_account_panel(value: dict[str, Any], timezone: str) -> dict[str, Any]:
    provider = label(value.get("active_provider"))
    products = provider_products(provider)
    title = {
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
    }.get(provider, provider or "—")
    try:
        tz = ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        tz, timezone = ZoneInfo("UTC"), "UTC"

    def stamp(raw: Any, year: bool = False) -> str:
        if not isinstance(raw, str) or len(raw) > 40:
            return "—"
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M" if year else "%m-%d %H:%M") if dt.tzinfo else "—"
        except (ValueError, OverflowError):
            return "—"

    def cell(en: str, zh: str, number: str, note_en: str = "", note_zh: str = "") -> dict[str, Any]:
        return column(
            1,
            [
                markdown(
                    f"<font color='grey'>{en}</font>  **{number}**" + (f"\n{note_en}" if note_en else ""),
                    f"<font color='grey'>{zh}</font>  **{number}**" + (f"\n{note_zh}" if note_zh else ""),
                    "notation",
                )
            ],
        )

    def grid(cells: list[dict[str, Any]]) -> dict[str, Any]:
        return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px", "columns": cells}

    raw_rows = value.get("accounts")
    rows = (
        [row for row in raw_rows if isinstance(row, dict) and row.get("provider") in products][:4]
        if isinstance(raw_rows, list)
        else []
    )
    children: list[dict[str, Any]] = []
    if not rows:
        state = value.get("status")
        pending = state == "pending"
        en = "Snapshot not ready; a later message can retry" if pending else "No account snapshot for this provider"
        zh = "本轮快照未就绪；后续消息刷新" if pending else "当前订阅商暂无配置账户快照"
        children.append(markdown(en, zh, "notation"))
        children.append(grid([cell("Subscription expiry", "订阅到期", "—"), cell("Account balance", "账户余额", "—")]))
    for row in rows:
        name = safe(row.get("label"))
        if name:
            children.append(
                markdown(f"<font color='grey'>{name}</font>", f"<font color='grey'>{name}</font>", "notation")
            )
        status = label(row.get("status"))
        pending_en = "Snapshot not ready; later messages can retry" if value.get("terminal") else "Query pending"
        pending_zh = "本轮快照未就绪；后续消息刷新" if value.get("terminal") else "待查询"
        unknown_en, unknown_zh = {
            "pending": (pending_en, pending_zh),
            "unsupported": ("Adapter pending", "待接入"),
            "missing_credentials": ("Credential reference missing", "待配置凭据"),
            "unavailable": ("Query failed", "查询失败"),
        }.get(status, ("Not returned by API", "API 未返回"))
        if status != "ok":
            code = row.get("http_status")
            http = f" · HTTP {code}" if type(code) is int and 100 <= code <= 599 else ""
            if row.get("reason") == "endpoint_retired":
                unknown_en = "Official account endpoint retired · " + safe(row.get("retired_on"))
                unknown_zh = "官方账户接口已退役 · " + safe(row.get("retired_on"))
            children.append(markdown(unknown_en + http, unknown_zh + http, "notation"))
        windows = row.get("windows")
        windows = windows if isinstance(windows, list) else []
        names = (
            ["rolling", "weekly", "monthly"]
            if provider == "opencode-go" and windows
            else [
                name
                for name in ("rolling", "weekly", "monthly", "mcp_monthly")
                if any(isinstance(w, dict) and w.get("name") == name for w in windows)
            ]
        )
        quota = []
        for name in names[:4]:
            window = next((w for w in windows if isinstance(w, dict) and w.get("name") == name), {})
            n = window.get("remaining_percent")
            known = isinstance(n, (int, float)) and not isinstance(n, bool) and 0 <= n <= 100 and math.isfinite(n)
            number = f"{n:.1f}".rstrip("0").rstrip(".") + "%" if known else "—"
            en, zh = {
                "rolling": ("5h left", "5h 剩余"),
                "weekly": ("Week left", "周剩余"),
                "monthly": ("Month left", "月剩余"),
                "mcp_monthly": ("MCP left", "MCP 剩余"),
            }[name]
            reset = stamp(window.get("reset_at"))
            quota.append(
                cell(
                    en,
                    zh,
                    number,
                    f"<font color='grey'>Reset {reset}</font>",
                    f"<font color='grey'>重置 {reset}</font>",
                )
            )
        for start in range(0, len(quota), 3):
            children.append(grid(quota[start : start + 3]))
        expires = stamp(row.get("subscription_expires_at"), True)
        expiry_en, expiry_zh, expiry_note_en, expiry_note_zh = "Subscription expiry", "订阅到期", unknown_en, unknown_zh
        if row.get("subscription_source") == "manual" and expires != "—":
            expiry_note_en, expiry_note_zh = "Manual record", "手动记录"
        elif expires != "—":
            expiry_note_en, expiry_note_zh = "API timestamp", "API 时间"
        elif row.get("subscription_expires_on"):
            expires = safe(row.get("subscription_expires_on"))
            expiry_en, expiry_zh = "Plan period ends", "套餐有效期"
            expiry_note_en, expiry_note_zh = "API date; timezone unspecified", "API 日期；时区未标注"
        else:
            expires = "—"
            if row.get("subscription_status") == "not_active":
                expiry_note_en, expiry_note_zh = "No active plan returned by API", "API 未发现有效套餐"
            elif row.get("subscription_status") == "unavailable":
                expiry_note_en, expiry_note_zh = "Subscription lookup failed", "订阅权益查询失败"
        raw_balances = row.get("balances")
        balances = []
        for item in raw_balances[:4] if isinstance(raw_balances, list) else []:
            if not isinstance(item, dict):
                continue
            kind = label(item.get("kind"))
            amount = money(item.get("amount"), signed=kind in {"cash_balance", "account_credit_balance"})
            if amount is None:
                continue
            currency = safe(item.get("currency"))
            en, zh = {
                "key_credit_remaining": ("Key allowance", "Key 限额"),
                "account_credit_balance": ("Account credits", "账户积分余额"),
                "account_available": ("Available balance", "可用余额"),
                "cash_balance": ("Cash", "现金余额"),
                "voucher_balance": ("Vouchers", "代金券"),
                "debt": ("Amount owed", "欠费"),
            }.get(kind, ("Account balance", "账户余额"))
            balances.append(
                (
                    kind,
                    cell(
                        en,
                        zh,
                        f"{currency} {amount}".strip(),
                        "" if currency else "Unit not reported",
                        "" if currency else "币种未标明",
                    ),
                )
            )
        wallet = next((pair for pair in balances if pair[0] != "key_credit_remaining"), None)
        balance_cell = wallet[1] if wallet else cell("Account balance", "账户余额", "—", unknown_en, unknown_zh)
        children.append(grid([cell(expiry_en, expiry_zh, expires, expiry_note_en, expiry_note_zh), balance_cell]))
        extra = [pair[1] for pair in balances if pair is not wallet]
        if row.get("key_limit_unset"):
            extra.append(cell("Key limit", "Key 限额", "—", "No configured limit", "未设置限额；不是无限余额"))
        if row.get("key_expires_at"):
            extra.append(
                cell(
                    "Key expiry",
                    "Key 到期",
                    stamp(row["key_expires_at"], True),
                    "Not subscription expiry",
                    "不是订阅到期",
                )
            )
        if row.get("subscription_renews_on"):
            extra.append(
                cell(
                    "Auto-renewal", "自动续费", safe(row["subscription_renews_on"]), "Not final expiry", "不是最终到期"
                )
            )
        for i in range(0, len(extra), 2):
            children.append(grid(extra[i : i + 2]))
        checked = stamp(row.get("checked_at"))
        if checked != "—" and row.get("source") != "none":
            children.append(
                markdown(
                    f"<font color='grey'>API snapshot · {checked}</font>",
                    f"<font color='grey'>API 快照 · {checked}</font>",
                    "notation",
                )
            )
        if row.get("stale"):
            code = row.get("last_http_status")
            http = f" · HTTP {code}" if type(code) is int and 100 <= code <= 599 else ""
            when = stamp(row.get("last_attempt_at"))
            suffix = f" · {when}" if when != "—" else ""
            children.append(
                markdown(
                    "Retained last success; latest read failed" + http + suffix,
                    "保留上次成功快照；最近读取失败" + http + suffix,
                    "notation",
                )
            )
    if value.get("stale"):
        children.append(markdown("Previous snapshot; refresh pending", "上次快照 · 待刷新", "notation"))
    children.append(
        markdown(
            f"<font color='grey'>Configured accounts for this provider; reset ≠ expiry · {safe(timezone)}</font>",
            f"<font color='grey'>仅当前订阅商的配置账户；重置≠到期 · {safe(timezone)}</font>",
            "notation",
        )
    )
    return _panel(f"{title} · Subscription / quota", f"{title} · 订阅与额度", children, "ref_accounts")
