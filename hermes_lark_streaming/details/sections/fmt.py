"""Display formatting for section values. Pure functions; unknown renders as 未知, never as zero."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

UNKNOWN = "未知"


def compact(value: int | None, *, lower_bound: bool = False) -> str:
    """12.3k / 1.2M. A lower bound is truncated, not rounded up."""
    if value is None:
        return UNKNOWN
    if value >= 1_000_000:
        text = f"{value // 10_000 / 100:.2f}" if lower_bound else f"{value / 1_000_000:.2f}"
        return text.rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        text = f"{value // 100 / 10:.1f}" if lower_bound else f"{value / 1_000:.1f}"
        return text.rstrip("0").rstrip(".") + "k"
    return str(value)


def percent(ratio: float | None, *, floor: bool = False) -> str:
    if ratio is None:
        return UNKNOWN
    permille = int(ratio * 1000 + 1e-9) if floor else round(ratio * 1000)
    return f"{'≥' if floor else ''}{permille // 10}.{permille % 10}%"


def percent_value(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def number(value: int | None, *, lower_bound: bool = False) -> str:
    if value is None:
        return UNKNOWN
    return f"{'≥' if lower_bound else ''}{value:,}"


def gib(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:.1f} GiB"


def ratio_of(used: Any, total: Any) -> float | None:
    if not isinstance(used, int | float) or not isinstance(total, int | float) or isinstance(used, bool):
        return None
    return min(1.0, max(0.0, used / total)) if total > 0 else None


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ValueError, OSError, KeyError):
        return ZoneInfo("UTC")


def parse_instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return dt if dt.tzinfo else None


def stamp(raw: Any, tz: ZoneInfo, *, year: bool = False) -> str:
    """Local wall-clock text for an ISO instant in the configured zone, or 未知."""
    dt = parse_instant(raw)
    if dt is None:
        return UNKNOWN
    try:
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M" if year else "%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        return UNKNOWN


def span(delta_s: float) -> str:
    """2h14m / 3天4小时 / 12m for a positive duration."""
    minutes = int(delta_s // 60)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{max(mins, 1)}m"
