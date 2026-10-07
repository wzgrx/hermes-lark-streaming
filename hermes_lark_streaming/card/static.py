"""One-shot cards for Cron and background-task deliveries (no streaming, no process/details)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .markdown import downgrade_tables, optimize_markdown_style, split_long_text
from .render import LOCALES, Bi, _markdown, esc, grey

_SUMMARY_CHARS = 120


def _run_time(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return value


def _card(head: Bi, body: str, text_size: str) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [_markdown(head, "notation")]
    content = downgrade_tables(optimize_markdown_style(body))
    elements.extend(
        {"tag": "markdown", "content": chunk, "text_size": text_size}
        for chunk in split_long_text(content) if chunk.strip()
    )
    summary = " ".join(body.replace("```", " ").split())[:_SUMMARY_CHARS]
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "locales": LOCALES, "summary": {"content": summary or head.zh}},
        "body": {"elements": elements},
    }


def render_cron(
    content: str, *, task_name: str = "", run_time: str = "", text_size: str = "normal_v2",
) -> dict[str, Any]:
    parts = [p for p in (esc(task_name), _run_time(run_time)) if p]
    tail = grey(" · ".join(parts)) if parts else ""
    head = Bi(f"<font color='blue'>●</font> **定时任务** {tail}".rstrip(),
              f"<font color='blue'>●</font> **Scheduled task** {tail}".rstrip())
    return _card(head, content, text_size)


def render_background(preview: str, content: str, *, text_size: str = "normal_v2") -> dict[str, Any]:
    tail = grey(esc(preview)) if preview else ""
    head = Bi(f"<font color='green'>●</font> **后台任务完成** {tail}".rstrip(),
              f"<font color='green'>●</font> **Background task done** {tail}".rstrip())
    return _card(head, content if content.strip() else "(No response generated)", text_size)
