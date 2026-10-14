"""Counts as short labels; shared by the footer and the details panel so one card never mixes styles."""

from __future__ import annotations

import math

_UNITS = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k"))


def short_count(value: int, *, lower_bound: bool = False) -> str:
    """``value`` in the largest unit it reaches, three significant digits (999.7M rounds up to 1B)."""
    for index, (size, unit) in enumerate(_UNITS):
        if value < size:
            continue
        text = _scaled(value / size, lower_bound)
        if float(text) >= 1000 and index > 0:
            size, unit = _UNITS[index - 1]
            text = _scaled(value / size, lower_bound)
        return text + unit
    return str(value)


def _scaled(scaled: float, lower_bound: bool) -> str:
    decimals = 2 if scaled < 10 else 1 if scaled < 100 else 0
    factor = 10**decimals
    # A lower bound is truncated (never claims more than was seen); otherwise round half up, not to even.
    scaled = math.floor(scaled * factor + (1e-9 if lower_bound else 0.5)) / factor
    return f"{scaled:.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{scaled:.0f}"
