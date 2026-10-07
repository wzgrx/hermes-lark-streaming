"""Compact native Card 2.0 layout; row-local pairs stay aligned when wrapping."""

from __future__ import annotations

from typing import Any

# Four two-cell metric rows plus metadata, exceptions and the panel header.
# Count all nested tags (including plain_text and icons), not just body elements.
DETAIL_ELEMENT_RESERVE = 40
SUMMARY_ELEMENT_RESERVE = 6


def markdown(en: str, zh: str, text_size: str) -> dict[str, Any]:
    return {
        "tag": "markdown", "content": en,
        "i18n_content": {"en_us": en, "zh_cn": zh},
        "text_size": text_size, "margin": "0px", "text_align": "left",
    }


def column(weight: int, elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tag": "column", "width": "weighted", "weight": weight,
        "vertical_align": "top", "vertical_spacing": "4px", "elements": elements,
    }


def metric_row(
    left: tuple[str, str, str, str], right: tuple[str, str, str, str], text_size: str,
) -> dict[str, Any]:
    """Two compact metrics per row; values are already escaped by the renderer."""
    cells = []
    for en_key, zh_key, en_value, zh_value in (left, right):
        cells.append(column(1, [markdown(
            f"<font color='grey'>{en_key}</font>  **{en_value}**",
            f"<font color='grey'>{zh_key}</font>  **{zh_value}**", text_size,
        )]))
    return {
        "tag": "column_set", "flex_mode": "none", "horizontal_spacing": "12px",
        "columns": cells,
    }
