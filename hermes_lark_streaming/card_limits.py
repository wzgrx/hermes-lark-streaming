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
_COMPACTION_TEXT = "… older card content compacted for CardKit limits …"
_MIN_TEXT_CHARS = 192


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
        size,
        elements,
        min(tables, MAX_TABLES + 1),
        size <= MAX_JSON_BYTES and elements <= MAX_ELEMENTS,
    )


def _fits(card: dict[str, Any], max_bytes: int) -> bool:
    inspection = inspect_card(card)
    return inspection.json_bytes <= max_bytes and inspection.elements <= MAX_ELEMENTS


def _body_elements(card: dict[str, Any]) -> list[dict[str, Any]] | None:
    body = card.get("body")
    if not isinstance(body, dict):
        return None
    elements = body.get("elements")
    if not isinstance(elements, list) or not all(isinstance(item, dict) for item in elements):
        return None
    return elements


def _text_slots(value: Any) -> list[tuple[dict[str, Any], str]]:
    """Return mutable CardKit content slots, including localized duplicates."""
    slots: list[tuple[dict[str, Any], str]] = []
    for node in _walk(value):
        if not isinstance(node, dict):
            continue
        for key, child in node.items():
            if key == "content" and isinstance(child, str):
                slots.append((node, key))
    return slots


def _trim_verbose_text(card: dict[str, Any], max_bytes: int) -> bool:
    changed = False
    while not _fits(card, max_bytes):
        candidates = [
            (len(str(parent[key])), parent, key)
            for parent, key in _text_slots(card)
            if len(str(parent[key])) > _MIN_TEXT_CHARS
        ]
        if not candidates:
            break
        length, parent, key = max(candidates, key=lambda item: item[0])
        keep = max(_MIN_TEXT_CHARS, length // 2)
        parent[key] = "… " + str(parent[key])[-keep:]
        changed = True
    return changed


def _footer_indexes(elements: list[dict[str, Any]]) -> set[int]:
    indexes: set[int] = set()
    for index, element in enumerate(elements[:-1]):
        if element.get("tag") != "hr":
            continue
        following = elements[index + 1]
        if following.get("tag") in {"markdown", "lark_md"}:
            indexes.update({index, index + 1})
    return indexes


def _last_answer_index(elements: list[dict[str, Any]], footer: set[int]) -> int | None:
    for index in range(len(elements) - 1, -1, -1):
        if index not in footer and elements[index].get("tag") in {"markdown", "lark_md"}:
            return index
    return None


def _remove_top_level_history(card: dict[str, Any], max_bytes: int) -> bool:
    """Drop oldest panels/chunks while preserving the newest answer when possible."""
    elements = _body_elements(card)
    if elements is None:
        return False
    changed = False
    while not _fits(card, max_bytes) and elements:
        footer = _footer_indexes(elements)
        answer = _last_answer_index(elements, footer)
        candidate: int | None = next(
            (i for i, element in enumerate(elements) if element.get("tag") == "collapsible_panel"),
            None,
        )
        if candidate is None:
            candidate = next(
                (
                    i
                    for i, element in enumerate(elements)
                    if i not in footer
                    and i != answer
                    and not _is_compaction_marker(element)
                    and element.get("tag") in {"markdown", "lark_md"}
                ),
                None,
            )
        if candidate is None:
            candidate = next(
                (i for i, element in enumerate(elements) if i != answer and not _is_compaction_marker(element)),
                None,
            )
        if candidate is None:
            break
        elements.pop(candidate)
        changed = True
    return changed


def _is_compaction_marker(element: dict[str, Any]) -> bool:
    return element.get("tag") == "markdown" and element.get("content") == _COMPACTION_TEXT


def _compaction_marker() -> dict[str, Any]:
    return {"tag": "markdown", "content": _COMPACTION_TEXT, "text_size": "notation"}


def _minimal_card(card: dict[str, Any], answer: str) -> dict[str, Any]:
    """Last-resort valid card that retains the newest answer tail."""
    result: dict[str, Any] = {
        "schema": card.get("schema", "2.0"),
        "config": copy.deepcopy(card.get("config", {})),
        "body": {"elements": [_compaction_marker()]},
    }
    if answer:
        result["body"]["elements"].append({"tag": "markdown", "content": answer[-4096:]})
    if isinstance(card.get("header"), dict):
        result["header"] = copy.deepcopy(card["header"])
    return result


def compact_card(card: dict[str, Any], *, max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """Bound terminal cards by both serialized bytes and recursive element count.

    Verbose text is shortened first. If structural CardKit overhead remains too
    large, old panels and old answer chunks are removed while the newest answer
    is retained. The original object is never modified and every changed result
    carries a visible compaction marker.
    """
    result = copy.deepcopy(card)
    if _fits(result, max_bytes):
        return result

    original_elements = _body_elements(result) or []
    footer = _footer_indexes(original_elements)
    answer_index = _last_answer_index(original_elements, footer)
    newest_answer = ""
    if answer_index is not None:
        content = original_elements[answer_index].get("content")
        if isinstance(content, str):
            newest_answer = content

    changed = _trim_verbose_text(result, max_bytes)
    changed = _remove_top_level_history(result, max_bytes) or changed

    elements = _body_elements(result)
    if elements is None:
        result = _minimal_card(result, newest_answer)
        elements = _body_elements(result)
        changed = True
    if changed and elements is not None:
        elements.insert(0, _compaction_marker())

    # The marker itself may push a borderline card over the cap. Apply the same
    # deterministic policy once more, protecting its newest answer when present.
    _trim_verbose_text(result, max_bytes)
    _remove_top_level_history(result, max_bytes)

    if not _fits(result, max_bytes):
        result = _minimal_card(card, newest_answer)
        answer_elements = _body_elements(result)
        while answer_elements and not _fits(result, max_bytes) and len(answer_elements) > 1:
            answer = str(answer_elements[-1].get("content", ""))
            if len(answer) <= _MIN_TEXT_CHARS:
                answer_elements.pop()
                break
            answer_elements[-1]["content"] = answer[-max(_MIN_TEXT_CHARS, len(answer) // 2) :]
        if not _fits(result, max_bytes):
            result = {"schema": "2.0", "body": {"elements": [_compaction_marker()]}}
    return result
