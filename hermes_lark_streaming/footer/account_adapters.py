"""Reviewed money, quota and personal-subscription response fields only."""

from __future__ import annotations

import contextlib
import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .state import label
from .usage import mapping


def seconds(value: Any) -> float | None:
    if type(value) not in (int, float) or not 0 <= value <= 4 * 10**12 or not math.isfinite(value):
        return None
    return float(value)


def money(value: Any, *, signed: bool = False) -> str | None:
    if type(value) not in (str, int, float) or len(str(value)) > 48:
        return None
    try:
        n = Decimal(str(value))
        if not n.is_finite():
            return None
        # Bound fractional exponent BEFORE fixed-format allocation.
        exponent = n.as_tuple().exponent
        if not isinstance(exponent, int) or not -18 <= exponent <= 12:
            return None
        if n.copy_abs() > 10**12 or (n < 0 and not signed):
            return None
        return format(n, "f")
    except (InvalidOperation, ValueError):
        return None


def epoch_ms(value: Any) -> str | None:
    n = seconds(value)
    if n is None or not 10**11 <= n <= 4 * 10**12:
        return None
    try:
        return datetime.fromtimestamp(n / 1000, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def add_balance(result: dict[str, Any], value: Any, kind: str, currency: str, *, signed: bool = False) -> None:
    amount = money(value, signed=signed)
    if amount is not None:
        result["balances"].append({"kind": kind, "currency": currency, "amount": amount})


def parse_extra(provider: str, data: dict[str, Any], result: dict[str, Any]) -> None:
    row = mapping(data.get("data"))
    if provider == "openrouter-credits":
        credit_total, credit_used = money(row.get("total_credits")), money(row.get("total_usage"))
        if credit_total is not None and credit_used is not None:
            add_balance(
                result, str(Decimal(credit_total) - Decimal(credit_used)), "account_credit_balance", "USD", signed=True
            )
    elif provider in {"moonshot", "moonshot-global"}:
        if type(data.get("code")) is not int or data["code"] != 0 or data.get("status") is False:
            return
        currency = "CNY" if provider == "moonshot" else "USD"
        for field, kind in (
            ("available_balance", "account_available"),
            ("cash_balance", "cash_balance"),
            ("voucher_balance", "voucher_balance"),
        ):
            add_balance(result, row.get(field), kind, currency, signed=field == "cash_balance")
    elif provider in {"minimax", "minimax-cn"}:
        envelope = mapping(data.get("base_resp"))
        if "status_code" in envelope and (type(envelope["status_code"]) is not int or envelope["status_code"] != 0):
            return
        currency = str(data.get("currency")) if data.get("currency") in {"CNY", "USD"} else ""
        if "available_amount" in data:
            for field, kind in (
                ("available_amount", "account_available"),
                ("cash_balance", "cash_balance"),
                ("voucher_balance", "voucher_balance"),
                ("owed_amount", "debt"),
            ):
                add_balance(result, data.get(field), kind, currency, signed=field == "cash_balance")
            return
        rows = data.get("model_remains")
        for raw in rows[:2] if isinstance(rows, list) else []:
            raw = mapping(raw)
            for prefix, name, end in (
                ("current_interval", "rolling", "end_time"),
                ("current_weekly", "weekly", "weekly_end_time"),
            ):
                # Official CLI documents usage_count as ambiguous. Do NOT guess
                # its direction: use explicit remaining percent/count only.
                percent = seconds(raw.get(prefix + "_remaining_percent"))
                if percent is None:
                    total = seconds(raw.get(prefix + "_total_count"))
                    left = seconds(raw.get(prefix + "_remaining_count"))
                    if total and left is not None and left <= total:
                        percent = left / total * 100
                if percent is not None and percent <= 100:
                    result["windows"].append(
                        {
                            "name": name,
                            "remaining_percent": percent,
                            "used_percent": 100 - percent,
                            "reset_at": epoch_ms(raw.get(end)),
                            "model": label(raw.get("model_name")),
                        }
                    )
            if len(result["windows"]) >= 3:
                break
        result["windows"] = result["windows"][:3]
        result["partial"] = len(result["windows"]) < 2
    elif provider in {"zai", "bigmodel"}:
        if data.get("success") is False or (
            "code" in data and (type(data["code"]) is not int or data["code"] not in {0, 200})
        ):
            return
        rows = row.get("limits")
        for raw in rows[:8] if isinstance(rows, list) else []:
            raw = mapping(raw)
            name = ""
            if raw.get("type") == "TOKENS_LIMIT":
                name = "weekly" if (raw.get("unit"), raw.get("number")) == (6, 1) else "rolling"
            elif raw.get("type") == "TIME_LIMIT":
                name = "mcp_monthly"
            percent = seconds(raw.get("percentage"))
            if name and percent is not None and percent <= 10**6:
                result["windows"].append(
                    {
                        "name": name,
                        "used_percent": percent,
                        "remaining_percent": max(0, 100 - percent),
                        "reset_at": epoch_ms(raw.get("nextResetTime")),
                    }
                )
        result["windows"] = result["windows"][:3]


def parse_subscription(data: Any) -> dict[str, Any]:
    envelope = mapping(data)
    if envelope.get("success") is False or (
        "code" in envelope and (type(envelope["code"]) is not int or envelope["code"] not in {0, 200})
    ):
        return {"subscription_status": "unavailable"}
    rows = envelope.get("data")
    if not isinstance(rows, list):
        return {"subscription_status": "unavailable"}
    for raw in rows[:32]:
        row = mapping(raw)
        ident = label(row.get("productId")) or label(row.get("productName"))
        if "coding" not in ident.lower() or row.get("status") != "VALID" or row.get("inCurrentPeriod") is not True:
            continue
        result: dict[str, Any] = {
            "subscription_status": "active",
            "plan_name": label(row.get("productName")),
            "subscription_source": "provider_api",
            "auto_renew": row.get("autoRenew") in (True, 1),
        }
        # Keep unzoned calendar facts as dates, not fabricated UTC instants.
        field = "nextRenewTime" if not result["auto_renew"] else "valid"
        raw_date = row.get(field)
        matches = re.findall(r"(?<![0-9])\d{4}-\d{2}-\d{2}(?![0-9])", raw_date) if isinstance(raw_date, str) else []
        if not matches and field == "nextRenewTime" and isinstance(row.get("valid"), str):
            matches = re.findall(r"\d{4}-\d{2}-\d{2}", row["valid"])
        if matches:
            try:
                date = datetime.strptime(matches[-1], "%Y-%m-%d").date()
                result["subscription_expires_on"] = date.isoformat()
            except ValueError:
                pass
        if result["auto_renew"] and isinstance(row.get("nextRenewTime"), str):
            matches = re.findall(r"\d{4}-\d{2}-\d{2}", row["nextRenewTime"])
            if matches:
                with contextlib.suppress(ValueError):
                    result["subscription_renews_on"] = datetime.strptime(matches[-1], "%Y-%m-%d").date().isoformat()
        return result
    return {"subscription_status": "not_active"}
