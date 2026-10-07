"""Bounded presentation metadata from Hermes' progress callback.

Only terminal-like output fields are projected; read/browser/media results and unknown objects are never
stringified, and the original result stays intact.
"""

from __future__ import annotations

import json
from typing import Any

from ..card.redact import redact

_COMMAND_TOOLS = frozenset({"terminal", "exec", "bash", "command", "run", "process"})


def normalize_tool_completion(
    tool_name: str,
    status: str,
    detail: str,
    *,
    result: Any = None,
    is_error: Any = None,
) -> tuple[str, str]:
    """Prefer explicit error metadata; preserve callbacks without completion kwargs."""
    if status in {"running", "started", "tool.started"}:
        return status, detail
    failed = is_error is True or status in {"error", "failed"}
    command = tool_name.strip().lower().replace("-", "_") in _COMMAND_TOOLS
    data: Any = result
    text = ""
    if command and type(result) is str:
        if result.lstrip().startswith(("{", "[")):
            if len(result) <= 32768:
                try:
                    data = json.loads(result)
                except (ValueError, RecursionError):
                    data = None
                    text = result
            else:
                data = None
                text = "Result omitted (size limit)"
        else:
            text = result
    if command and type(data) is dict:
        code = data.get("exit_code")
        error = data.get("error")
        failed = failed or (type(code) is int and code != 0) or (type(error) is str and bool(error.strip()))
        fields = ("output", "stdout", "stderr", "error")
        pieces = [data[k][:8192] for k in fields if type(data.get(k)) is str and data[k].strip()]
        text = "\n".join(pieces)
        if type(code) is int and code != 0:
            text = f"Exit code {code}" + ("\n" + text if text else "")
    text = redact((text or detail)[:32768])
    if len(text) > 4096:
        text = text[:4000] + "\n… (output truncated)"
    if failed and not text:
        text = "Tool reported failure"
    return ("error" if failed else status), text
