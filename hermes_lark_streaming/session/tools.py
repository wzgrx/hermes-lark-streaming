"""Tool lifecycle tracking: Hermes progress callbacks in, bounded ``card.model.Step`` rows out.

Only presentation metadata is kept: a short sanitized summary, timing and, for failures, a bounded
error excerpt. Tool output of successful calls is never retained.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from ..card.model import Step, StepStatus
from ..card.redact import redact

MAX_TRACKED = 512  # older ordinary steps beyond this are folded into counters
_SUMMARY_BYTES = 200
_ERROR_BYTES = 512

_STARTED = frozenset({"running", "started", "tool.started"})
_COMMAND_TOOLS = frozenset({"terminal", "exec", "bash", "command", "run", "process"})
_PATH_TOOLS = frozenset({"read", "open", "write", "edit", "glob", "read_file", "write_file", "patch"})
_SEARCH_TOOLS = frozenset({"web_search", "search", "grep"})


def _kind(name: str) -> str:
    normalized = name.strip().lower().replace("-", "_")
    for table, kind in ((_COMMAND_TOOLS, "command"), (_PATH_TOOLS, "path"), (_SEARCH_TOOLS, "search")):
        if normalized in table or normalized.split("_", 1)[0] in table:
            return kind
    return ""


def title(name: str) -> str:
    cleaned = name.replace("-", " ").replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "Tool"


def _basenames(text: str) -> str:
    return re.sub(
        r'(^|[\s=\'"()])([~./][^\s\'"()]+)',
        lambda m: f"{m.group(1)}{os.path.basename(m.group(2))}",
        text,
    )


def summarize(name: str, detail: str) -> str:
    """One-line argument preview with credentials redacted and paths reduced to base names."""
    text = re.sub(r"<[^>]+>", "", detail or "").strip()
    if not text:
        return ""
    kind = _kind(name)
    if kind == "path":
        text = re.sub(r"^(?:from|file|path)\s+", "", text, flags=re.IGNORECASE).strip()
        text = os.path.basename(text.replace("\\", "/").rstrip("/")) or text
    elif kind == "command":
        text = _basenames(redact(text))
    else:
        text = redact(text)
    return _cut(" ".join(text.split()), _SUMMARY_BYTES)


def _cut(text: str, limit: int) -> str:
    raw = text.encode()
    if len(raw) <= limit:
        return text
    return raw[: limit - 3].decode("utf-8", errors="ignore") + "…"


def completion(
    name: str, status: str, detail: str, *, result: Any = None, is_error: Any = None,
) -> tuple[bool, str]:
    """``(failed, error_text)`` for a finished tool. Prefers explicit error metadata.

    Terminal-like tools report failure through ``exit_code``/``error`` in a JSON result; other tools
    only fail when Hermes says so. Unknown result objects are never stringified.
    """
    failed = is_error is True or status in {"error", "failed"}
    command = name.strip().lower().replace("-", "_") in _COMMAND_TOOLS
    data: Any = result
    text = ""
    if command and isinstance(result, str):
        if result.lstrip().startswith(("{", "[")):
            if len(result) <= 32768:
                try:
                    data = json.loads(result)
                except (ValueError, RecursionError):
                    data, text = None, result
            else:
                data, text = None, "Result omitted (size limit)"
        else:
            text = result
    if command and isinstance(data, dict):
        code, error = data.get("exit_code"), data.get("error")
        failed = failed or (type(code) is int and code != 0) or (isinstance(error, str) and bool(error.strip()))
        pieces = [
            data[k][:8192] for k in ("output", "stdout", "stderr", "error")
            if isinstance(data.get(k), str) and data[k].strip()
        ]
        text = "\n".join(pieces)
        if type(code) is int and code != 0:
            text = f"Exit code {code}" + ("\n" + text if text else "")
    if not failed:
        return False, ""
    text = redact((text or detail)[:32768]).strip() or "Tool reported failure"
    return True, _cut(text, _ERROR_BYTES)


@dataclass(slots=True)
class _Record:
    name: str
    summary: str
    status: StepStatus
    started: float
    elapsed_ms: float | None = None
    error: str = ""

    def view(self) -> Step:
        return Step(title(self.name), self.summary, self.status, self.elapsed_ms, self.error)


class ToolTracker:
    """Per-turn record of tool calls. Not thread-safe; the session layer serializes access."""

    def __init__(self, max_tracked: int = MAX_TRACKED) -> None:
        self._records: list[_Record] = []
        self._max = max(1, max_tracked)
        self.archived = 0
        self.started_at = time.monotonic()

    @property
    def count(self) -> int:
        """Stable lifecycle count, including archived steps."""
        return self.archived + len(self._records)

    @property
    def running(self) -> int:
        return sum(r.status is StepStatus.RUNNING for r in self._records)

    def start(self, name: str, detail: str = "") -> None:
        self._records.append(_Record(name, summarize(name, detail), StepStatus.RUNNING, time.monotonic()))
        self._trim()

    def finish(
        self, name: str, status: str = "completed", detail: str = "", *, result: Any = None, is_error: Any = None,
    ) -> bool:
        """Close the newest running record of ``name``; returns whether the tool failed."""
        failed, error = completion(name, status, detail, result=result, is_error=is_error)
        now = time.monotonic()
        for record in reversed(self._records):
            if record.name == name and record.status is StepStatus.RUNNING:
                record.elapsed_ms = (now - record.started) * 1000
                break
        else:  # a completion with no observed start still counts
            record = _Record(name, summarize(name, detail), StepStatus.RUNNING, now)
            self._records.append(record)
        record.status = StepStatus.FAILED if failed else StepStatus.OK
        record.error = error
        self._trim()
        return failed

    def unconfirm_running(self) -> None:
        """The turn ended; a still-running record has no final result, which is not a failure."""
        for record in self._records:
            if record.status is StepStatus.RUNNING:
                record.status = StepStatus.UNCONFIRMED

    def steps(self) -> tuple[Step, ...]:
        return tuple(r.view() for r in self._records)

    def _trim(self) -> None:
        excess = len(self._records) - self._max
        if excess <= 0:
            return
        drop = [i for i, r in enumerate(self._records) if r.status is StepStatus.OK][:excess]
        for i in reversed(drop):
            del self._records[i]
        self.archived += len(drop)
