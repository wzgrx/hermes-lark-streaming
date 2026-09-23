"""Fail-closed hook migration for Hermes' September 2026 gateway decomposition.

All targets are compiled before any file is replaced. Old monolithic layouts
continue to use Patcher; this module never restores an obsolete whole file.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from . import patcher as p


def _after_assignment(source: str, target: str, function: str | None = None) -> tuple[int, str]:
    lines = source.splitlines(True)
    hits: list[tuple[int, str]] = []
    tree: ast.AST = ast.parse(source)
    if function:
        scopes = [
            n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function
        ]
        if len(scopes) != 1:
            raise p.PatcherError(f"Expected one scope {function}")
        tree = scopes[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(target in ast.unparse(t) for t in node.targets):
            if node.end_lineno is None:
                raise p.PatcherError(f"Assignment for {target} has no end line")
            hits.append((node.end_lineno, p._safe_indent(lines, node.lineno - 1)))
    if len(hits) != 1:
        raise p.PatcherError(f"Expected one assignment for {target}; found {len(hits)}")
    return hits[0]


def _before(source: str, anchor: str) -> tuple[int, str]:
    lines = source.splitlines(True)
    hits = [(i, p._safe_indent(lines, i)) for i, line in enumerate(lines) if anchor in line]
    if len(hits) != 1:
        raise p.PatcherError(f"Expected one anchor {anchor!r}; found {len(hits)}")
    return hits[0]


def _function(source: str, name: str) -> tuple[int, str]:
    hits = p._find_func_bodies(ast.parse(source), source.splitlines(True), name)
    if len(hits) != 1:
        raise p.PatcherError(f"Expected one function {name}; found {len(hits)}")
    return hits[0]


def _stop_site(source: str) -> tuple[int, str]:
    site = p._find_stop_site(ast.parse(source), source.splitlines(True))
    if site is None:
        raise p.PatcherError("Expected one stop site")
    return site


class ModularPatcher:
    """Same install/status/remove interface, explicit module ownership."""

    MARKERS: ClassVar[list[tuple[str, str]]] = [*p.MARKERS, (p.MK_APPROVAL, p.MK_APPROVAL_END)]

    def __init__(self, run_path: Path) -> None:
        self.run_path = run_path
        self.root = run_path.parent
        self.names: tuple[str, ...] = ("run_inbound.py", "run_turn.py", "run_turn_runner.py", "run_busy.py")

    def _contents(self) -> dict[Path, str]:
        return {self.root / name: (self.root / name).read_text(encoding="utf-8") for name in self.names}

    def is_patched(self) -> bool:
        return any(p.MK_START in s for s in self._contents().values())

    def is_fully_patched(self) -> bool:
        text = "\n".join(self._contents().values())
        return all(text.count(begin) == text.count(end) == 1 for begin, end in self.MARKERS)

    def _clean(self, contents: dict[Path, str]) -> dict[Path, str]:
        return {path: self._clean_one(s) for path, s in contents.items()}

    def _clean_one(self, s: str) -> str:
        for begin, end in self.MARKERS:
            s = p._remove_block_checked(s, begin, end)
        return s

    def _plan(self) -> tuple[dict[Path, str], dict[Path, str]]:
        before = self._contents()
        clean = self._clean(before)
        slots: list[tuple[Path, int, str]] = []

        def add(
            name: str,
            locate: Callable[[str], tuple[int, str]],
            hook: Callable[[str], str],
        ) -> None:
            path = self.root / name
            idx, indent = locate(clean[path])
            slots.append((path, idx, hook(indent)))

        add("run_inbound.py", lambda s: _before(s, "_paused_notice = self._hm_estop_gate("), p._feishu_normalize_hook)
        add("run_turn.py", lambda s: _function(s, "_handle_message_with_agent"), p._start_hook)
        add(
            "run_turn.py",
            lambda s: _before(s, "return await self._hmwa_deliver_turn_response("),
            lambda i: p._complete_hook(i).replace("duration=_response_time", "duration=_turn_seconds"),
        )
        add("run_turn.py", lambda s: _before(s, "self._hmwa_discard_stale_result(source,"), p._abort_hook)
        add("run_busy.py", _stop_site, p._stop_hook)
        add("run_turn_runner.py", lambda s: _function(s, "progress_callback"), p._tool_hook)
        add(
            "run_turn_runner.py",
            lambda s: _function(s, "stream_delta_cb"),
            lambda i: p._answer_hook(i).replace("_stts_consumer_ref", "stts"),
        )
        add(
            "run_turn_runner.py",
            lambda s: _function(s, "interim_assistant_cb"),
            lambda i: p._thinking_hook(i, tts_consumer="stts"),
        )
        add("run_turn_runner.py", lambda s: _after_assignment(s, "agent.reasoning_config"), p._reasoning_hook)
        add(
            "run_turn_runner.py",
            lambda s: _after_assignment(s, "agent.background_review_callback"),
            p._background_review_hook,
        )
        add("run_turn_runner.py", lambda s: _after_assignment(s, "agent.clarify_callback"), p._clarify_hook)
        add("run_turn_runner.py", lambda s: _function(s, "_approval_notify_sync"), p._approval_hook)
        add(
            "run_turn.py",
            lambda s: _before(s, 'if not result.get("interrupted"):'),
            lambda i: p._followup_complete_hook(i).replace(
                "message_id=event_message_id", "message_id=turn_ctx.event_message_id"
            ),
        )
        add(
            "run_turn.py",
            lambda s: _before(s, "# Restart the typing indicator;"),
            lambda i: (
                p._interrupt_hook(i)
                .replace("was_interrupted", 'result.get("interrupted")')
                .replace("message_id=event_message_id", "message_id=turn_ctx.event_message_id")
            ),
        )
        add(
            "run_turn.py",
            lambda s: _before(s, "_preserve_queued_followup_history_offset(result, followup_result)"),
            p._followup_result_hook,
        )
        add("run_turn.py", lambda s: _after_assignment(s, "(images, text_content)"), p._bg_deliver_hook)
        after = dict(clean)
        for path, idx, hook in sorted(slots, key=lambda t: t[1], reverse=True):
            lines = after[path].splitlines(True)
            lines[idx:idx] = hook.splitlines(True)
            after[path] = "".join(lines)
        for path, text in after.items():
            compile(text, str(path), "exec")
        combined = "\n".join(after.values())
        for begin, end in self.MARKERS:
            if combined.count(begin) != 1 or combined.count(end) != 1:
                raise p.PatcherError(f"Incomplete modular plan: {begin}")
        return before, after

    def verify_target(self) -> None:
        self._plan()

    def _publish(self, before: dict[Path, str], after: dict[Path, str]) -> None:
        written = []
        try:
            for path, content in after.items():
                if content != before[path]:
                    p._atomic_write(path, content)
                    written.append(path)
        except BaseException:
            for path in reversed(written):
                p._atomic_write(path, before[path])
            raise

    def apply(self) -> None:
        if self.is_fully_patched():
            return
        before, after = self._plan()
        self._publish(before, after)

    def remove(self) -> None:
        before = self._contents()
        after = self._clean(before)
        for path, text in after.items():
            compile(text, str(path), "exec")
        self._publish(before, after)

    def restore(self) -> None:
        # Remove owned blocks; never copy pre-update files over new upstream.
        self.remove()


class ModularCronPatcher(ModularPatcher):
    MARKERS: ClassVar[list[tuple[str, str]]] = [(p.MK_CRON_DELIVER, p.MK_CRON_DELIVER_END)]

    def __init__(self, cron_path: Path) -> None:
        self.cron_path = cron_path.parent / "scheduler_delivery.py"
        self.run_path = self.cron_path
        self.root = cron_path.parent
        self.names = ("scheduler_delivery.py",)

    def is_patched(self) -> bool:
        return p.MK_CRON_DELIVER in self.cron_path.read_text()

    def _plan(self) -> tuple[dict[Path, str], dict[Path, str]]:
        before = self._contents()
        clean = self._clean(before)
        source = clean[self.cron_path]
        idx, indent = _before(source, "target_errors: list = []")
        hook = p._make_hook(
            indent,
            p.MK_CRON_DELIVER,
            p.MK_CRON_DELIVER_END,
            [
                "try:",
                "    if (t.platform_name.lower() in ('feishu', 'lark')",
                "            and not getattr(t.transport, 'is_relay', False)",
                "            and not t.in_channel_surface and not t.thread_id):",
                "        from hermes_lark_streaming.patch import on_cron_deliver",
                "        _hermes_lark_cron_receipt = on_cron_deliver(",
                "            chat_id=t.chat_id, content=cleaned_delivery_content.strip(),",
                (
                    "            loop=loop, task_name=job.get('name', ''), "
                    "run_time=job.get('next_run_at', ''),"
                ),
                "            media_files=locals().get('media_files') or [])",
                "        if _hermes_lark_cron_receipt:",
                "            if (isinstance(_hermes_lark_cron_receipt, dict)",
                "                    and _hermes_lark_cron_receipt.get('delivery_outcome') == 'unknown'):",
                "                unverified_targets.append(t.where)",
                "                continue",
                "            _maybe_mirror_cron_delivery(",
                "                job, t.platform_name, t.chat_id, t.mirror_text, thread_id=t.thread_id,",
                "                user_id=t.origin_user_id, enabled=t.mirror_this_target)",
                "            if not (isinstance(_hermes_lark_cron_receipt, dict)",
                "                    and _hermes_lark_cron_receipt.get('message_id')):",
                "                unverified_targets.append(t.where)",
                "            continue",
                *p._hook_exception_lines("cron_deliver"),
            ],
        )
        lines = source.splitlines(True)
        lines[idx:idx] = hook.splitlines(True)
        text = "".join(lines)
        compile(text, str(self.cron_path), "exec")
        return before, {self.cron_path: text}
