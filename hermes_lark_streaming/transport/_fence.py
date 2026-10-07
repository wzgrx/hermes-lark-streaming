"""Fenced-code scanner shared by card limits and image resolution (no card/ dependency)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

FENCE_LINE = re.compile(r" {0,3}(`{3,}|~{3,})([^\r\n]*)\Z")


def markdown_regions(text: str) -> Iterator[tuple[int, int, bool]]:
    """Yield original offsets and whether a region is literal fenced code.

    Recognizes top-level backtick/tilde fences, longer closing fences and unfinished streaming blocks.
    Text is never normalized: literal content (including CRLF) survives later transformations.
    """
    offset = 0
    prose_start = 0
    code_start: int | None = None
    marker = ""
    width = 0
    for line_match in re.finditer(r"[^\n]*\n|[^\n]+\Z", text):
        line = line_match.group(0)
        match = FENCE_LINE.fullmatch(line.rstrip("\r\n"))
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


def map_outside_fences(text: str, transform: Callable[[str], str]) -> str:
    """Apply a prose transformation without touching literal fenced examples."""
    return "".join(
        text[start:end] if code else transform(text[start:end]) for start, end, code in markdown_regions(text)
    )
