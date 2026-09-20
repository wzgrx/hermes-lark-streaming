"""CardKit defensive limits and deterministic final-card compaction."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

MAX_TABLES = 5
MAX_ELEMENTS = 200
MAX_JSON_BYTES = 28_000


@dataclass(frozen=True)
class CardInspection:
    json_bytes: int
    elements: int
    tables: int
    safe: bool


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def inspect_card(card: dict[str, Any]) -> CardInspection:
    nodes = list(_walk(card))
    elements = sum(1 for node in nodes if isinstance(node, dict) and isinstance(node.get("tag"), str))
    tables = sum(
        str(node.get("content", "")).count("\n|")
        for node in nodes
        if isinstance(node, dict) and node.get("tag") in {"markdown", "lark_md"}
    )
    size = len(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode())
    return CardInspection(
        size, elements, min(tables, MAX_TABLES + 1), size <= MAX_JSON_BYTES and elements <= MAX_ELEMENTS
    )


def compact_card(card: dict[str, Any], *, max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """Bound final card size by truncating oldest verbose markdown blocks.

    The original object is never modified.  Status/header/footer blocks are naturally
    preserved because compaction targets the longest text first.
    """
    result = copy.deepcopy(card)
    if inspect_card(result).json_bytes <= max_bytes:
        return result
    candidates: list[dict[str, Any]] = [
        node
        for node in _walk(result)
        if isinstance(node, dict)
        and node.get("tag") in {"markdown", "lark_md"}
        and isinstance(node.get("content"), str)
    ]
    marker = "\n\n… content compacted for CardKit limits …"
    for node in sorted(candidates, key=lambda item: len(str(item.get("content", ""))), reverse=True):
        content = str(node["content"])
        while len(content) > 320 and inspect_card(result).json_bytes > max_bytes:
            content = content[len(content) // 4 :]
            node["content"] = marker + content
    return result
