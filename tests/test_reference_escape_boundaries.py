"""Truncated compact tool text must not expose partial rendering escape units."""

import html
import re

import pytest

from hermes_lark_streaming.cardkit.reference import _tool_text


def _valid_escape_units(text: str) -> None:
    # Renderer-generated HTML entities and Markdown escapes are indivisible.
    remainder = re.sub(r"&(?:amp|lt|gt|quot|#x27);", "", text)
    assert "&" not in remainder
    remainder = re.sub(r"\\[\\`*_\[\]~]", "", remainder)
    assert "\\" not in remainder


@pytest.mark.parametrize("char", ["<", ">", "&", '"', "'", "\\"])
@pytest.mark.parametrize("limit", [64, 100])
def test_cut_near_html_or_backslash_unit_is_complete_and_within_budget(char, limit):
    text = "A" * (limit - 1) + char + "visible remainder"
    result = _tool_text(text, limit, single_line=True)
    assert len(result.encode()) <= limit
    assert result.endswith("…")
    _valid_escape_units(result)


@pytest.mark.parametrize("char", ["`", "*", "_", "[", "]", "~"])
def test_cut_near_markdown_escape_keeps_whole_pairs(char):
    result = _tool_text("A" * 61 + char + "tail text", 64)
    assert len(result.encode()) <= 64
    _valid_escape_units(result)


@pytest.mark.parametrize("char", ["文", "🙂"])
def test_multibyte_unit_and_ellipsis_share_one_byte_budget(char):
    result = _tool_text("A" * 61 + char * 20, 64)
    assert len(result.encode()) <= 64
    assert result.endswith("…")
    assert "�" not in result


def test_single_line_whitespace_is_folded_before_deciding_what_fits():
    text = "  run\t" + " \r\n\t" * 300 + "END  "
    assert _tool_text(text, 64, single_line=True) == "run END"


def test_blank_single_line_stays_blank():
    assert _tool_text(" \r\n\t" * 300, 64, single_line=True) == ""


def test_short_text_keeps_existing_escape_and_newline_contract():
    text = '<tag> & "quoted"\n\\path * _ ` [ ] ~'
    expected = re.sub(r"([\\`*_\[\]~])", r"\\\1", html.escape(text))
    assert _tool_text(text, 200) == expected
    assert "\n" in _tool_text(text, 200)
    assert "\n" not in _tool_text(text, 200, single_line=True)


@pytest.mark.parametrize("limit", [1, 2, 3, 6])
def test_tiny_limits_terminate_without_partial_entities(limit):
    result = _tool_text("<" * 30, limit)
    assert len(result.encode()) <= limit
    _valid_escape_units(result)


def test_redaction_still_happens_before_any_excerpt_cut():
    text = 'run --api-key "VALUE_WITH_SPACES secret tail" ' + "<" * 100
    result = _tool_text(text, 64, single_line=True)
    assert "VALUE_WITH_SPACES" not in result and "secret tail" not in result
    assert "redacted" in result
    assert len(result.encode()) <= 64
    _valid_escape_units(result)
