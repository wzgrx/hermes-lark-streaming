"""Live footer updates share the existing flush mutex and CardKit sequence."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..footer.runtime import build_runtime_footer, runtime_actions, runtime_signature
from ..footer.state import label
from .session import SessionState

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from ..config import Config
    from ..footer.history_summary import HistorySummary
    from ..footer.host import HostSampler
    from .session import CardSession


class RuntimeFooterController:
    _cfg: Config
    _schedule_flush: Callable[[CardSession], None]
    _do_batch_update: Callable[..., Coroutine[Any, Any, bool]]
    _profile_home: Path
    _reference_host: HostSampler | None
    _reference_history: HistorySummary | None

    def _runtime_enabled(self) -> bool:
        return self._cfg.card_layout == "reference" or (
            self._cfg.footer_mode == "enhanced" and self._cfg.footer_enabled
        )

    def _reference_snapshot(self, session: CardSession, data: dict[str, Any]) -> dict[str, Any]:
        if self._cfg.card_layout != "reference":
            return data
        from ..footer.history_summary import HistorySummary
        from ..footer.host import HostSampler

        if self._reference_host is None:
            self._reference_host = HostSampler()
        history_enabled = self._cfg.footer_history.get("enabled") is True
        if history_enabled and self._reference_history is None:
            self._reference_history = HistorySummary(
                self._profile_home / "state/card-usage.sqlite3", self._cfg.reference_history_timezone
            )
        if not session.state.is_terminal:
            # Background coalesced reads do not block answer/segment streaming.
            if self._cfg.reference_resources_enabled:
                self._reference_host.request()
            if history_enabled and self._reference_history is not None:
                self._reference_history.request()
        steps = session.tool_use.build_display_steps()
        history = (
            self._reference_history.snapshot() if history_enabled and self._reference_history is not None else None
        )
        if history is not None:
            history["show_models"] = self._cfg.footer_history.get("show_models") is True
        data.update(
            presentation="reference",
            reference={
                "steps": steps,
                "tools_prior": session.tool_calls_prior,
                "done_prior": session.tools_done_prior,
                "failed_prior": session.tools_failed_prior,
                "failed_total": session.tools_failed_prior + sum(s["status"] == "error" for s in steps),
                "succeeded_total": session.tools_done_prior
                - session.tools_failed_prior
                + sum(s["status"] == "success" for s in steps),
                "show_tools": self._cfg.show_tool_use,
                "resources_enabled": self._cfg.reference_resources_enabled,
                "host": self._reference_host.snapshot(),
                "history": history,
                "agent_name": self._cfg.reference_agent_name,
                "footer_enabled": self._cfg.footer_enabled,
            },
        )
        return data

    async def _finish_reference_snapshot(self, session: CardSession) -> None:
        if self._cfg.card_layout != "reference":
            return
        self._reference_snapshot(session, {})
        waits = []
        if self._cfg.reference_resources_enabled and self._reference_host is not None:
            waits.append(self._reference_host.finish())
        if self._cfg.footer_history.get("enabled") is True and self._reference_history is not None:
            waits.append(self._reference_history.finish())
        if waits:
            await asyncio.gather(*waits)

    def _runtime_snapshot(self, session: CardSession) -> dict[str, Any] | None:
        if not self._runtime_enabled():
            return None
        data = {**session.footer_state.snapshot(), **session.runtime_status.snapshot()}
        steps = session.tool_use.build_display_steps()
        data["tool_calls"] = session.tool_calls_prior + len(steps)
        if session.state == SessionState.CLARIFY_PAUSED or session.approval_pending_split:
            data["runtime_phase"] = "waiting"
        elif data["runtime_phase"] not in {"compression", "summary_returned", "summary_failed", "provider_switch"}:
            running = [step for step in steps if step["status"] == "running"]
            if running:
                data.update(
                    runtime_phase="tool",
                    runtime_tool=label(running[-1]["name"]),
                    runtime_tools_done=session.tools_done_prior + sum(step["status"] != "running" for step in steps),
                )
        return self._reference_snapshot(session, data)

    def request_runtime_update(self, session: CardSession) -> None:
        """Public-hook callbacks can run on worker threads, not the Gateway loop."""
        if not self._runtime_enabled() or session.state.is_terminal or session._loop.is_closed():
            return
        # The loop can close concurrently; normal completion owns the final card.
        with suppress(RuntimeError):
            session._loop.call_soon_threadsafe(self._schedule_flush, session)

    def _start_runtime_timer(self, session: CardSession) -> None:
        if (
            not self._runtime_enabled()
            or session.state.is_terminal
            or session.flush.completed
            or session.runtime_timer is not None
        ):
            return

        def tick() -> None:
            session.runtime_timer = None
            if not self._runtime_enabled() or session.state.is_terminal or session.flush.completed:
                return
            self._schedule_flush(session)
            self._start_runtime_timer(session)

        session.runtime_timer = session._loop.call_later(5.0, tick)

    async def _flush_runtime_footer(self, session: CardSession) -> bool:
        """Called only INSIDE FlushController; never start another writer/task."""
        data = self._runtime_snapshot(session)
        if data is None:
            return True
        now = time.monotonic()
        if now < max(session.runtime_retry_after, session.stream_retry_after):
            return True
        signature = runtime_signature(data)
        if signature == session.runtime_last_signature and now < session.runtime_next_update:
            return True
        elements = build_runtime_footer(data, text_size=self._cfg.footer_text_size, details=self._cfg.footer_details)
        reference = self._cfg.card_layout == "reference"
        if reference:
            from ..cardkit.reference import build_reference_prefix

            elements = build_reference_prefix(data) + elements
        try:
            # An empty segment list avoids marking body reasoning/tool segments
            # clean merely because footer metadata was updated.
            ok = await self._do_batch_update(session, [], runtime_actions(elements, reference=reference), set(), {}, [])
        except (Exception, asyncio.CancelledError):
            session.runtime_retry_after = time.monotonic() + 5.0
            raise
        if ok:
            session.runtime_last_signature = signature
            session.runtime_next_update = time.monotonic() + 2.0
            session.runtime_retry_after = 0.0
        else:
            session.runtime_retry_after = time.monotonic() + 5.0
        return ok
