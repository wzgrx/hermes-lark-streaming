"""Inline secret redaction for anything rendered into a card (tool args, errors, output)."""

from __future__ import annotations

import re

__all__ = ["redact"]

_SENSITIVE_NAME_RE = re.compile(
    r"token|secret|password|api[_-]?key|authorization|cookie|credential"
    r"|bearer|session[_-]?id|client[_-]?secret|access[_-]?key",
    re.IGNORECASE,
)

_INLINE_ASSIGNMENT_RE = re.compile(r'(^|[\s"\'`])([A-Za-z_][A-Za-z0-9_]*)(=(?:"[^"]*"|\'[^\']*\'|[^\s"\'`]+))')
_AUTH_HEADER_RE = re.compile(
    r"(Authorization\s*:\s*(?:Bearer|Basic|Token)\s+)([^\'\"\s]+)",
    re.IGNORECASE,
)
_SECRET_FLAG_RE = re.compile(
    r'(^|[\s"\'`])(--?[A-Za-z0-9][A-Za-z0-9-]*)(=|\s+)("(?:[^"]*)"|\'(?:[^\']*)\'|[^\s"\'`]+)'
)
_JSON_SECRET_RE = re.compile(r'("([A-Za-z_][A-Za-z0-9_-]*)"\s*:\s*)("(?:\\.|[^"\\])*")')
_BARE_KEY_RE = re.compile(r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]+|(?:ghp_|github_pat_|oc_sk_)[A-Za-z0-9_]+)")


def redact(value: str) -> str:
    """脱敏 key=secret、Authorization header、--flag secret 模式."""

    def _redact_assign(m: re.Match) -> str:
        key = str(m.group(2))
        if _SENSITIVE_NAME_RE.search(key):
            return f"{m.group(1)}{key}=[redacted]"
        return str(m.group(0))

    def _redact_flag(m: re.Match) -> str:
        flag = re.sub(r"^-+", "", str(m.group(2)))
        if _SENSITIVE_NAME_RE.search(flag):
            return f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]"
        return str(m.group(0))

    redacted = _SECRET_FLAG_RE.sub(
        _redact_flag,
        _AUTH_HEADER_RE.sub(r"\1[redacted]", _INLINE_ASSIGNMENT_RE.sub(_redact_assign, value)),
    )
    redacted = _JSON_SECRET_RE.sub(
        lambda m: m.group(1) + '"[redacted]"' if _SENSITIVE_NAME_RE.search(m.group(2)) else m.group(0),
        redacted,
    )
    return _BARE_KEY_RE.sub("[redacted]", redacted)
