"""Markdown 文本处理 — 标题降级、表格降级、图片 key 剥离、长文本分块."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator

_logger = logging.getLogger("hermes_lark_streaming")

_MAX_CARD_TABLES = 5
_MAX_CHUNK_CHARS = 2400
_FENCE_LINE = re.compile(r" {0,3}(`{3,}|~{3,})([^\r\n]*)\Z")

__all__ = [
    "_downgrade_tables",
    "_find_tables_outside_code_blocks",
    "_map_outside_fenced_code",
    "_split_long_text",
    "_strip_invalid_image_keys",
    "optimize_markdown_style",
]


def _markdown_regions(text: str) -> Iterator[tuple[int, int, bool]]:
    """Yield original offsets and whether a region is literal fenced code.

    Recognize top-level backtick/tilde fences, including longer closing fences
    and unfinished streaming blocks. No placeholders or text normalization:
    literal content (including CRLF) must survive subsequent transformations.
    This is a fence scanner, not a complete Markdown/container parser.
    """
    offset = 0
    prose_start = 0
    code_start: int | None = None
    marker = ""
    width = 0
    # Scan LF/CRLF without treating Python's other Unicode separators as lines.
    for line_match in re.finditer(r"[^\n]*\n|[^\n]+\Z", text):
        line = line_match.group(0)
        match = _FENCE_LINE.fullmatch(line.rstrip("\r\n"))
        if match:
            fence, suffix = match.groups()
            if code_start is None:
                # A backtick in a backtick fence's info string invalidates it.
                if fence[0] == "~" or "`" not in suffix:
                    if prose_start < offset:
                        yield prose_start, offset, False
                    code_start = offset
                    marker, width = fence[0], len(fence)
            elif fence[0] == marker and len(fence) >= width and not suffix.strip(" \t"):
                yield code_start, offset + len(line), True
                code_start = None
                prose_start = offset + len(line)
        offset += len(line)
    if code_start is not None:
        yield code_start, len(text), True
    elif prose_start < len(text):
        yield prose_start, len(text), False


def _map_outside_fenced_code(text: str, transform: Callable[[str], str]) -> str:
    """Apply a prose transformation without touching literal fenced examples."""
    return "".join(
        text[start:end] if code else transform(text[start:end]) for start, end, code in _markdown_regions(text)
    )


def _find_tables_outside_code_blocks(text: str) -> list[tuple[int, int, str]]:
    """查找代码块外的 markdown 表格，返回 [(start, end, raw), ...]."""
    results: list[tuple[int, int, str]] = []
    for start, end, code in _markdown_regions(text):
        if code:
            continue
        for m in re.finditer(r"\|.+\|\n\|[-:| ]+\|[\s\S]*?(?=\n\n|\n(?!\|)|$)", text[start:end]):
            results.append((start + m.start(), start + m.end(), m.group(0)))
    return results


def _downgrade_tables(text: str, limit: int = _MAX_CARD_TABLES) -> str:
    """超限表格降级为代码块（保留内容可见但飞书不渲染为表格元素）."""
    matches = _find_tables_outside_code_blocks(text)
    if len(matches) <= limit:
        return text
    result = text
    for start, end, raw in reversed(matches[limit:]):
        replacement = f"```\n{raw}\n```"
        result = result[:start] + replacement + result[end:]
    return result


def _strip_invalid_image_keys(text: str) -> str:
    """移除正文中的非 img_ 图片引用，保留代码块中的字面示例."""
    if "![" not in text:
        return text

    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(2).startswith("img_") else ""

    return _map_outside_fenced_code(text, lambda prose: re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _replace, prose))


def optimize_markdown_style(text: str) -> str:
    """优化流式 Markdown 以适配飞书 CardKit 渲染.

    仅处理围栏代码块以外的正文：标题降级、压缩空行和剥离无效图片。
    代码块、未闭合流式代码块和原始换行保持原样，不使用字符串占位符。
    """
    try:
        downgrade_headings = any(
            re.search(r"^#{1,3} ", text[start:end], re.MULTILINE)
            for start, end, code in _markdown_regions(text)
            if not code
        )

        def _optimize_prose(prose: str) -> str:
            if downgrade_headings:
                prose = re.sub(r"^#{2,6} (.+)$", r"##### \1", prose, flags=re.MULTILINE)
                prose = re.sub(r"^# (.+)$", r"#### \1", prose, flags=re.MULTILINE)
            prose = re.sub(r"\n{3,}", "\n\n", prose)
            return _strip_invalid_image_keys(prose)

        return _map_outside_fenced_code(text, _optimize_prose)
    except Exception:
        _logger.debug("optimize_markdown_style failed", exc_info=True)
        return text


def _split_long_text(text: str, limit: int = _MAX_CHUNK_CHARS) -> list[str]:
    """将超长文本按段落/换行拆分为多个不超过 limit 字符的块."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks
