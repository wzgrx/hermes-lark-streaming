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
    "_find_tables_outside_code_blocks",
    "_map_outside_fenced_code",
    "_strip_invalid_image_keys",
    "downgrade_tables",
    "optimize_markdown_style",
    "split_long_text",
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


def downgrade_tables(text: str, limit: int = _MAX_CARD_TABLES) -> str:
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


def _split_plain_text(text: str, limit: int) -> list[str]:
    """Lossless paragraph/newline-preferred splitting; do not strip boundaries."""
    chunks: list[str] = []
    offset = 0
    while offset < len(text):
        if len(text) - offset <= limit:
            chunks.append(text[offset:])
            break
        boundary = text.rfind("\n\n", offset, offset + limit)
        cut = boundary + 2 if boundary >= 0 else offset
        if cut - offset < limit // 2:
            boundary = text.rfind("\n", offset, offset + limit)
            cut = boundary + 1 if boundary >= 0 else offset
        if cut - offset < limit // 2 or cut == offset:
            cut = offset + limit
        if cut - offset > 1 and text[cut - 1 : cut + 1] == "\r\n":
            cut -= 1
        chunks.append(text[offset:cut])
        offset = cut
    return chunks


def _split_fenced_block(block: str, limit: int) -> list[str]:
    """Split oversized top-level code into independently fenced display pieces.

    Added delimiters/line endings are presentation scaffolding, not stored text.
    Preserve all body characters and the language/info string. A fence longer
    than any same-character run in the body prevents a hard cut from turning
    an inline literal run into a closing fence in the next display piece.
    """
    opening_end = block.find("\n") + 1
    if not opening_end:
        return _split_plain_text(block, limit)
    opening = block[:opening_end]
    match = _FENCE_LINE.fullmatch(opening.rstrip("\r\n"))
    assert match is not None
    marker, info = match.groups()
    newline = "\r\n" if opening.endswith("\r\n") else "\n"
    last_start = block.rfind("\n", 0, len(block.rstrip("\r\n"))) + 1
    closing = _FENCE_LINE.fullmatch(block[last_start:].rstrip("\r\n"))
    closed = bool(
        closing and closing[1][0] == marker[0] and len(closing[1]) >= len(marker) and not closing[2].strip(" \t")
    )
    body = block[opening_end:last_start] if closed else block[opening_end:]
    longest = max((len(run[0]) for run in re.finditer(re.escape(marker[0]) + "+", body)), default=0)
    marker = marker[0] * max(len(marker), longest + 1)
    prefix = opening[: match.start(1)] + marker + info + newline
    capacity = limit - len(prefix) - len(newline) - len(marker)
    if capacity < 2:
        # Pathological metadata/fence runs leave no room for a balanced wrapper.
        # Keep content bounded and lossless, rather than looping or dropping it.
        _logger.debug("Fence metadata exceeds chunk budget; retaining bounded raw text")
        return _split_plain_text(block, limit)
    pieces = _split_plain_text(body, capacity) or [""]
    return [prefix + piece + ("" if piece.endswith("\n") else newline) + marker for piece in pieces]


def split_long_text(text: str, limit: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Bound final Markdown elements, keeping small fences atomic and large ones balanced.

    Prose separators and code body characters are retained. This is final-card
    presentation only, not a stream/rollover state change or a Markdown parser.
    """
    if limit <= 0:
        raise ValueError("Markdown chunk limit must be positive")
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    pending = ""
    for start, end, code in _markdown_regions(text):
        region = text[start:end]
        if code and len(region) > limit:
            if pending:
                chunks.append(pending)
                pending = ""
            chunks.extend(_split_fenced_block(region, limit))
            continue
        pieces = [region] if code else _split_plain_text(region, limit)
        for piece in pieces:
            if pending and len(pending) + len(piece) > limit:
                chunks.append(pending)
                pending = ""
            pending += piece
    if pending:
        chunks.append(pending)
    return chunks
