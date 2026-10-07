"""One-shot cards for Cron and background-task deliveries (no streaming, no process/details)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .markdown import downgrade_tables, optimize_markdown_style, split_long_text
from .render import LOCALES, Bi, _plain, clip

_SUMMARY_CHARS = 120


def _run_time(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return value


def _card(title: Bi, subtitle: str, template: str, tag: Bi, body: str, text_size: str) -> dict[str, Any]:
    content = downgrade_tables(optimize_markdown_style(body))
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": chunk, "text_size": text_size}
        for chunk in split_long_text(content) if chunk.strip()
    ]
    summary = " ".join(body.replace("```", " ").split())[:_SUMMARY_CHARS]
    head: dict[str, Any] = {
        "title": _plain(title),
        "text_tag_list": [{"tag": "text_tag", "text": _plain(tag), "color": template}],
        "template": template,
        "icon": {"tag": "standard_icon", "token": "robot_outlined"},
    }
    if subtitle:
        head["subtitle"] = _plain(Bi.same(subtitle))
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "locales": LOCALES, "summary": {"content": summary or title.zh}},
        "header": head,
        "body": {"elements": elements},
    }


def render_cron(
    content: str, *, task_name: str = "", run_time: str = "", text_size: str = "normal_v2",
) -> dict[str, Any]:
    title = Bi(clip(task_name, 40), clip(task_name, 40)) if task_name else Bi("定时任务", "Scheduled task")
    return _card(title, _run_time(run_time), "wathet", Bi("定时任务", "Scheduled"), content, text_size)


def render_background(preview: str, content: str, *, text_size: str = "normal_v2") -> dict[str, Any]:
    title = Bi(clip(preview, 40), clip(preview, 40)) if preview else Bi("后台任务", "Background task")
    body = content if content.strip() else "(No response generated)"
    return _card(title, "", "green", Bi("后台任务完成", "Background done"), body, text_size)
