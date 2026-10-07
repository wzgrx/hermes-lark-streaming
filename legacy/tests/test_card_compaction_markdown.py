"""Total-card compaction must retain code presentation, not orphan its tail."""

import copy
import re

import pytest

from hermes_lark_streaming import card_limits
from hermes_lark_streaming.cardkit.markdown import _markdown_regions


def _answer(card: dict) -> str:
    return card["body"]["elements"][-1]["content"]


def _retained_body(text: str, language: str = "python") -> str:
    regions = [(start, end) for start, end, code in _markdown_regions(text) if code]
    assert len(regions) == 1
    start, end = regions[0]
    assert start > 0, "the omission notice belongs outside literal code"
    block = text[start:end]
    opening, body = block.split("\n", 1)
    assert opening.rstrip().endswith(language)
    marker = re.match(r" {0,3}(`{3,}|~{3,})", opening)[1]
    assert body.rstrip("\r\n").endswith(marker), "a terminal display block must be closed"
    return body[: body.rfind(marker)].rstrip("\r\n")


@pytest.mark.parametrize("marker,indent,newline,closed", [
    ("```", "", "\n", True),
    ("~~~", "", "\n", True),
    ("`````", "  ", "\r\n", True),
    ("```", "", "\n", False),
    ("~~~~~", "   ", "\r\n", False),
])
def test_compaction_keeps_code_fence_and_language(marker, indent, newline, closed) -> None:
    body = newline.join(f"print('row {index}: 中文')" for index in range(2000))
    text = indent + marker + "python" + newline + body
    if closed:
        text += newline + indent + marker
    card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": text}]}}
    original = copy.deepcopy(card)

    result = card_limits.compact_card(card, max_bytes=2200)

    assert card == original
    assert card_limits.inspect_card(result).json_bytes <= 2200
    retained = _retained_body(_answer(result))
    assert body.endswith(retained)
    assert "row 1999" in retained
    assert "compacted" in result["body"]["elements"][0]["content"]


def test_compaction_cut_inside_code_preserves_following_prose() -> None:
    body = "item = 42\n" * 1000 + "LAST_CODE_LINE"
    tail = "\n\n**Result:** completed."
    card = {"schema": "2.0", "body": {"elements": [{
        "tag": "markdown", "content": "earlier prose\n\n```python\n" + body + "\n```" + tail,
    }]}}
    result = card_limits.compact_card(card, max_bytes=1700)
    content = _answer(result)
    assert content.endswith(tail)
    assert "LAST_CODE_LINE" in _retained_body(content)


def test_compaction_shortens_nested_markdown() -> None:
    text = "```python\n" + "print(1)\n" * 1000 + "```"
    card = {"schema": "2.0", "body": {"elements": [{
        "tag": "markdown", "content": text,
        "i18n_content": {"en_us": {"tag": "lark_md", "content": text}},
    }]}}
    result = card_limits.compact_card(card, max_bytes=2000)
    assert card_limits.inspect_card(result).json_bytes <= 2000
    for parent, key in card_limits._text_slots(result):
        if "print(1)" in parent[key]:
            _retained_body(parent[key])


def test_minimal_card_keeps_newest_code_tail_fenced() -> None:
    text = "```python\n" + "value = 7\n" * 900 + "```"
    result = card_limits._minimal_card({"schema": "2.0"}, text)
    content = _answer(result)
    assert len(content) <= 4096
    assert "value = 7" in _retained_body(content)


def test_last_resort_shrinking_keeps_fence(monkeypatch) -> None:
    text = "```python\n" + "value = 7\n" * 900 + "```"
    card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": text}]}}
    monkeypatch.setattr(card_limits, "_trim_verbose_text", lambda *args: False)
    monkeypatch.setattr(card_limits, "_remove_top_level_history", lambda *args: False)
    result = card_limits.compact_card(card, max_bytes=1200)
    assert card_limits.inspect_card(result).json_bytes <= 1200
    assert "value = 7" in _retained_body(_answer(result))


def test_plain_text_tail_keeps_original_policy_without_markdown_wrapper() -> None:
    text = "```python\n" + "print(1)\n" * 100
    assert card_limits._shorten_content(text, 192, markdown=False) == "… " + text[-190:]


def test_short_content_and_prose_are_not_reformatted() -> None:
    text = "```python\nprint(1)\n```"
    assert card_limits._shorten_content(text, 192) == text
    assert card_limits._shorten_content("prose " * 500, 192) == "…\n" + ("prose " * 500)[-190:]


def test_retained_literal_fence_run_is_not_promoted_to_a_closing_fence() -> None:
    body = "old row\n" * 1000 + "literal = '```'\nLAST_CODE_LINE"
    text = "```python\n" + body + "\n```"
    result = card_limits._shorten_content(text, 192)
    retained = _retained_body(result)
    assert body.endswith(retained)
    assert "'```'" in retained
    assert result.splitlines()[1] == "````python"


@pytest.mark.parametrize("limit", [1, 2, 8, 32, 192])
def test_pathological_fence_metadata_is_bounded(limit: int) -> None:
    text = "`" * 500 + "python" * 200 + "\n" + "value = 7\n" * 100
    result = card_limits._shorten_content(text, limit)
    assert len(result) <= limit
    if limit >= 32:
        regions = [result[start:end] for start, end, code in _markdown_regions(result) if code]
        assert len(regions) == 1
        assert regions[0].rstrip().endswith("```")


def test_cut_in_trailing_prose_does_not_add_a_code_wrapper() -> None:
    text = "```python\nvalue = 7\n```\n\n" + "final prose\n" * 100
    result = card_limits._shorten_content(text, 192)
    assert len(result) <= 192
    assert not any(code for _, _, code in _markdown_regions(result))


def test_compaction_result_is_idempotent() -> None:
    card = {"schema": "2.0", "body": {"elements": [{
        "tag": "markdown", "content": "```python\n" + "value = 7\n" * 5000 + "```",
    }]}}
    result = card_limits.compact_card(card)
    assert card_limits.compact_card(result) == result
