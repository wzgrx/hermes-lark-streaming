"""Scalar sanitisers shared by every details module. Unknown is None, never zero."""

from __future__ import annotations

import math
import re
from typing import Any


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**15 else None


def seconds(value: Any) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def label(value: Any) -> str:
    """Bounded single-line text; anything that looks like a credential or URL is replaced."""
    if not isinstance(value, str):
        return ""
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", value.strip()[:160])
    if re.search(r"(?:sk[-_]|gh[pousr]_|github_pat_|Bearer\s|://)", value, re.I):
        return "[redacted]"
    return " ".join(value.split())


def model_display(value: Any) -> str:
    """Humanize a known bare ID only; keep provider prefixes and fine-tunes intact."""
    raw = label(value)
    match = re.fullmatch(r"deepseek-v(\d+(?:\.\d+)?)-(flash|pro)", raw, re.I)
    return f"DeepSeek V{match[1]} {match[2].title()}" if match else raw
