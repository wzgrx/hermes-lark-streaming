"""Card formatting and image transport must preserve literal fenced examples."""

from unittest.mock import AsyncMock, patch

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card
from hermes_lark_streaming.cardkit.markdown import (
    _downgrade_tables,
    _find_tables_outside_code_blocks,
    _strip_invalid_image_keys,
    optimize_markdown_style,
)
from hermes_lark_streaming.feishu import FeishuClient
from hermes_lark_streaming.streaming.image import ImageResolver
from hermes_lark_streaming.streaming.segments import Segment, SegmentType

TABLE = "| A | B |\n|---|---|\n| 1 | 2 |"
IMAGE = "![example](https://example.invalid/code-example.png)"


@pytest.mark.parametrize(
    "opener,closer",
    [
        ("```markdown", "```"),
        ("````markdown", "`````"),
        ("~~~markdown", "~~~~"),
        ("   ```markdown", "  ```"),
        ("```markdown", ""),
        ("~~~markdown", ""),
    ],
)
def test_fenced_examples_keep_images_blank_lines_and_headings(opener, closer):
    code = f"{opener}\n# literal heading\n\n\n{IMAGE}\n\n{TABLE}\n{closer}"
    text = f"# Real title\n{code}"
    assert optimize_markdown_style(text) == f"#### Real title\n{code}"
    assert _strip_invalid_image_keys(text) == text


def test_literal_old_placeholder_cannot_duplicate_or_replace_code():
    text = "Literal ___CB_0___ and ___CB_1___\n```python\nprint('___CB_0___')\n```"
    assert optimize_markdown_style(text) == text


def test_heading_only_inside_code_does_not_change_outer_h4():
    text = "#### Existing compact heading\n```markdown\n# literal\n```"
    assert optimize_markdown_style(text) == text


@pytest.mark.parametrize(
    "code",
    [
        f"~~~markdown\n{TABLE}\n~~~",
        f"````markdown\n```\n{TABLE}\n```\n````",
        f"```markdown\n{TABLE}",
        f"```markdown\n```not-a-closing-fence\n{TABLE}",
    ],
)
def test_example_tables_are_excluded_from_card_table_quota(code):
    assert _find_tables_outside_code_blocks(code) == []
    assert _downgrade_tables(code, limit=0) == code


def test_outside_table_offsets_remain_correct_after_fenced_example():
    code = f"~~~markdown\n{TABLE}\n~~~\n\n"
    text = code + TABLE
    assert _find_tables_outside_code_blocks(text) == [(len(code), len(text), TABLE)]
    result = _downgrade_tables(text, limit=0)
    assert result.startswith(code) and result[len(code) :] == f"```\n{TABLE}\n```"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_longer_closing_fence_and_line_endings_preserve_literal_content(newline):
    text = newline.join(["```markdown", IMAGE, "", "", "# literal", "`````", "## Real subheading"])
    assert optimize_markdown_style(text) == text.rsplit("## Real subheading", 1)[0] + "##### Real subheading"


@pytest.mark.parametrize("code", [f"```markdown\n{IMAGE}\n```", f"~~~markdown\n{IMAGE}"])
def test_code_example_images_do_not_start_uploads_or_use_cached_keys(code):
    resolver = ImageResolver(AsyncMock(spec=FeishuClient))
    resolver._cache["https://example.invalid/code-example.png"] = "img_v3_cached"
    with patch.object(resolver, "_start_upload") as start:
        assert resolver.resolve_images(code) == code
        start.assert_not_called()


def test_outside_image_still_uses_cache_while_same_url_in_code_stays_literal():
    resolver = ImageResolver(AsyncMock(spec=FeishuClient))
    resolver._cache["https://example.invalid/code-example.png"] = "img_v3_cached"
    code = f"```markdown\n{IMAGE}\n```"
    assert resolver.resolve_images(f"{IMAGE}\n{code}") == f"![example](img_v3_cached)\n{code}"


@pytest.mark.asyncio
async def test_terminal_image_resolution_preserves_code_without_network_work():
    client = AsyncMock(spec=FeishuClient)
    resolver = ImageResolver(client)
    code = f"````markdown\n{IMAGE}\n\n\n# literal\n````"
    assert await resolver.resolve_await(code) == code
    client.upload_image.assert_not_awaited()
    assert not resolver._pending


def test_v1_final_answer_retains_code_content_and_native_budget():
    code = f"```markdown\n{IMAGE}\n\n\n# literal\n```"
    answer = Segment(SegmentType.ANSWER, "answer")
    answer.text = code
    card = build_complete_card(
        segments=[answer],
        all_tool_steps=[],
        footer_data={"presentation": "reference", "reference": {"resources_enabled": False}},
        footer_mode="enhanced",
    )
    assert any(e.get("tag") == "markdown" and e.get("content") == code for e in card["body"]["elements"])
    assert inspect_card(card).safe


@pytest.mark.parametrize("false_close", ["```", "~~~~", "    ````", "````still literal"])
def test_short_mixed_overindented_and_suffixed_fences_do_not_close_code(false_close):
    text = f"````markdown\n{false_close}\n{IMAGE}\n\n\n# literal\n````\t "
    assert optimize_markdown_style(text) == text
    assert _find_tables_outside_code_blocks(text + "\n" + TABLE) == [(len(text) + 1, len(text) + 1 + len(TABLE), TABLE)]


@pytest.mark.parametrize("not_an_opener", ["```bad`info", "    ```markdown"])
def test_invalid_fence_openers_do_not_hide_prose_images(not_an_opener):
    text = f"{not_an_opener}\n{IMAGE}\n# Real title"
    assert optimize_markdown_style(text) == f"{not_an_opener}\n\n#### Real title"


@pytest.mark.parametrize("text", ["", "plain prose", "```", "~~~"])
def test_empty_plain_and_eof_openers_are_stable(text):
    assert optimize_markdown_style(text) == text
    assert _strip_invalid_image_keys(text) == text
    assert _find_tables_outside_code_blocks(text) == []


@pytest.mark.parametrize("separator", ["\v", "\u2028"])
def test_non_markdown_line_separator_does_not_end_a_code_line(separator):
    text = f"```markdown\nx{separator}```\n{IMAGE}\n# literal\n```"
    assert optimize_markdown_style(text) == text


def test_multiple_code_regions_keep_literal_bytes_and_only_normalize_prose():
    code_one = f"```markdown\n{IMAGE}\n\n\n## literal\n```\n"
    code_two = f"~~~markdown\n{TABLE}\n~~~"
    text = f"# Real title\n{code_one}\n\n\n## Real subtitle\n{code_two}"
    assert optimize_markdown_style(text) == f"#### Real title\n{code_one}\n\n##### Real subtitle\n{code_two}"
