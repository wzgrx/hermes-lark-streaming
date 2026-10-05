"""Bounded final-card chunks must not turn literal code into Markdown prose."""

import re

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_background_card, build_complete_card, build_cron_card
from hermes_lark_streaming.cardkit.markdown import _markdown_regions, _split_long_text, optimize_markdown_style
from hermes_lark_streaming.streaming.segments import Segment, SegmentType


def code_bodies(chunks):
    """Fixtures end every body line with LF/CRLF; wrapper delimiters are separate."""
    bodies = []
    for chunk in chunks:
        assert list(_markdown_regions(chunk)) == [(0, len(chunk), True)]
        lines = chunk.splitlines(keepends=True)
        assert re.fullmatch(r" {0,3}(`{3,}|~{3,})[^\r\n]*\r?\n", lines[0])
        assert re.fullmatch(r" {0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?", lines[-1])
        assert not list(_markdown_regions(chunk + "\nPROSE"))[-1][2]
        bodies.append("".join(lines[1:-1]))
    return "".join(bodies)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("opener,closer", [("```python", "```"), ("~~~python", "~~~~"), ("   ````python", "  `````")])
def test_long_fenced_lines_keep_language_and_literal_body(opener, closer, newline):
    body = (
        newline.join(
            [
                "# literal",
                "",
                "",
                "![example](https://example.invalid/literal.png)",
                "| a | b |",
                "|---|---|",
                "| 1 | 2 |",
            ]
            * 16
        )
        + newline
    )
    chunks = _split_long_text(opener + newline + body + closer, limit=400)
    assert len(chunks) > 1 and all(len(part) <= 400 for part in chunks)
    assert all("python" in part.splitlines()[0] for part in chunks)
    assert code_bodies(chunks) == body
    assert all(optimize_markdown_style(part) == part for part in chunks)


def test_short_fence_is_atomic_between_long_prose_regions():
    code = "```python\nprint('literal')\n\n\n# literal\n```\n"
    text = "A" * 180 + "\n\n" + code + "B" * 180
    parts = _split_long_text(text, limit=200)
    assert all(len(part) <= 200 for part in parts)
    assert "".join(parts) == text
    assert sum(code in part for part in parts) == 1


def test_prose_chunking_preserves_delimiters_and_crlf():
    text = ("a" * 65 + "\r\n\r\n\r\n") * 10
    parts = _split_long_text(text, limit=100)
    assert all(len(part) <= 100 for part in parts)
    assert "".join(parts) == text
    assert all(not part.endswith("\r") for part in parts[:-1])


def test_nested_shorter_fence_stays_literal_in_each_long_block():
    body = ("```example\n![literal](https://example.invalid/x.png)\n```\n\n") * 20
    chunks = _split_long_text("````markdown\n" + body + "````", limit=240)
    assert all(len(part) <= 240 for part in chunks)
    assert code_bodies(chunks) == body
    assert all(part.startswith("````markdown\n") for part in chunks)


def test_unclosed_long_fence_has_balanced_display_chunks_without_lost_body():
    body = ("print('still streaming')\n\n\n") * 30
    chunks = _split_long_text("```python\n" + body, limit=220)
    assert all(len(part) <= 220 for part in chunks)
    assert code_bodies(chunks) == body


def test_long_single_code_line_retains_all_characters_in_order():
    body = "汉字" * 1000
    chunks = _split_long_text("```text\n" + body + "\n```", limit=320)
    assert all(len(part) <= 320 for part in chunks)
    # Each display fragment has a synthetic line ending before its closing fence.
    fragments = [part.split("\n", 1)[1].rsplit("\n```", 1)[0] for part in chunks]
    assert "".join(fragment.rstrip("\n") for fragment in fragments) == body


def test_hard_split_does_not_cut_a_crlf_pair():
    text = "x" * 9 + "\r\n" + "y" * 20
    parts = _split_long_text(text, limit=10)
    assert "".join(parts) == text
    assert all(not part.endswith("\r") for part in parts[:-1])


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_fails_immediately_instead_of_looping(limit):
    with pytest.raises(ValueError):
        _split_long_text("content", limit=limit)


def test_final_v1_and_background_builders_use_balanced_code_chunks():
    body = ("print('literal')\n\n\n# literal\n") * 190
    code = "```python\n" + body + "```"
    answer = Segment(SegmentType.ANSWER, "answer")
    answer.text = code
    cards = [
        build_complete_card(
            segments=[answer],
            all_tool_steps=[],
            footer_data={"presentation": "reference", "reference": {"resources_enabled": False}},
            footer_mode="enhanced",
        ),
        build_cron_card(code),
        build_background_card("fixture", code),
    ]
    for card in cards:
        chunks = [
            e["content"]
            for e in card["body"]["elements"]
            if e.get("tag") == "markdown" and "python" in str(e.get("content", ""))
        ]
        assert len(chunks) > 1 and all(len(part) <= 2400 for part in chunks)
        assert code_bodies(chunks) == body
        assert inspect_card(card).safe
    assert answer.text == code


def test_inline_fence_runs_stay_literal_after_a_hard_line_split():
    body = ("a" * 70 + "```" + "b" * 70 + "\n") * 20
    chunks = _split_long_text("```python\n" + body + "```", limit=100)
    assert all(len(part) <= 100 for part in chunks)
    assert all(part.startswith("````python\n") for part in chunks)
    # Hard line cuts add a display newline; every original non-newline character remains.
    assert code_bodies(chunks).replace("\n", "") == body.replace("\n", "")


@pytest.mark.parametrize("block", ["```" + "info" * 80 + "\ncode\n```", "```text\n" + "`" * 500 + "literal\n```"])
def test_pathological_fence_overhead_keeps_bounded_raw_content_without_looping(block):
    chunks = _split_long_text(block, limit=100)
    assert all(len(part) <= 100 for part in chunks)
    assert "".join(chunks) == block


def test_adjacent_code_blocks_and_prose_keep_order_and_short_block_identity():
    first = "```python\na = 1\n```\n"
    second = "~~~text\nliteral\n~~~\n"
    text = "before\n" + first + second + "after\n" * 60
    chunks = _split_long_text(text, limit=100)
    assert "".join(chunks) == text
    assert sum(first in part for part in chunks) == 1
    assert sum(second in part for part in chunks) == 1
