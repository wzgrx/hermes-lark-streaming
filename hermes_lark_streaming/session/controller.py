"""Controller: the facade the hooks layer drives.

Hermes calls in from agent worker threads (deltas, tools) and from its event loop (completion). Every
public ``on_*`` method therefore only mutates session state under the session lock and then hops to the
loop with ``call_soon_threadsafe``; all card I/O happens in :class:`Pipeline` on the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import CancelledError
from concurrent.futures import Future as ConcurrentFuture
from pathlib import Path
from typing import Any

from ..card.model import Footer, Phase
from ..config import ConfigSource, hermes_home
from ..details import DetailsCollector, DetailsConfig, TurnTelemetry, observe_history
from ..metrics import metrics
from ..transport import (
    CardKitClient,
    ClientConfig,
    DeliveryLedger,
    DeliveryLedgerError,
    FeishuAPIError,
    classify_delivery_failure,
)
from ..transport.media import deliver_media_files, media_paths_to_deliver
from ..transport.routing import BotRegistry
from .pipeline import Pipeline, Runtime, guard_for
from .state import Session, State
from .static import CronDeliveryOutcomeUnknown, StaticDelivery
from .text import split_reasoning_text, strip_reasoning_tags

_logger = logging.getLogger("hermes_lark_streaming")
_CARD_CREATION_WAIT_SEC = 10.0
_PROMPT_SPLIT_WAIT_SEC = _CARD_CREATION_WAIT_SEC * 2 + 30
_STARTED = frozenset({"running", "started", "tool.started"})


class Controller:
    def __init__(self, home: Path | None = None) -> None:
        self.home = (home or hermes_home()).resolve()
        self.source = ConfigSource(self.home)
        self._bots = BotRegistry.from_config(self.source.settings().bots)
        self._clients: dict[str, CardKitClient] = {}
        self._client_lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._by_key: dict[str, Session] = {}
        self._interrupts: dict[str, str] = {}
        self._fallback: dict[str, set[str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.details_config = DetailsConfig.from_mapping(self.source.settings().details)
        self.collector = DetailsCollector(self.details_config)
        self.ledger = DeliveryLedger()
        self.pipeline = Pipeline(Runtime(self.source, self.ledger, self._client_for, self.collector))
        self.static = StaticDelivery(self.ledger, self._client_for)

    # ------------------------------------------------------------------ plumbing

    @property
    def enabled(self) -> bool:
        if not self.source.settings().enabled:
            return False
        app_id, secret, _ = self.source.credentials()
        return bool((app_id and secret) or self._bots.resolve(""))

    async def _client_for(self, chat_id: str) -> CardKitClient:
        """One immutable client per configured bot; an exact chat binding wins over the default."""
        bot = self._bots.resolve(chat_id)
        key = bot.bot_id if bot else "default"
        with self._client_lock:
            client = self._clients.get(key)
            if client is not None:
                return client
            if bot is not None:
                app_id, secret = bot.credentials()
                base = bot.base_url
            else:
                app_id, secret, base = self.source.credentials()
            if not app_id or not secret:
                raise RuntimeError(f"feishu credentials for bot {key!r} are not configured")
            client = CardKitClient(ClientConfig(app_id=app_id, app_secret=secret, base_url=base))
            self._clients[key] = client
            return client

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            self._loop = asyncio.get_running_loop()
            return self._loop
        except RuntimeError:
            pass
        loop = self._loop
        return loop if loop is not None and not loop.is_closed() else None

    def _fire(self, coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop) -> Any:
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except Exception:
            coro.close()
            _logger.debug("scheduling failed", exc_info=True)
            return None
        future.add_done_callback(self._log_failure)
        return future

    @staticmethod
    def _log_failure(future: ConcurrentFuture[Any]) -> None:
        try:
            future.result()
        except (asyncio.CancelledError, CancelledError):
            return
        except Exception:
            _logger.warning("background task failed", exc_info=True)

    def _wake(self, session: Session) -> None:
        """Ask the loop for a throttled flush; safe from any thread."""
        with contextlib.suppress(RuntimeError):  # loop closed during shutdown
            session.loop.call_soon_threadsafe(self.pipeline.schedule, session)

    def _active(self, message_id: str) -> Session | None:
        session = self._sessions.get(message_id)
        if session is None or session.state.terminal:
            return None
        if session.guard is not None and session.guard.should_skip("hook"):
            return None
        return session

    def _register(self, session: Session) -> None:
        self._sessions[session.message_id] = session
        if session.anchor_id:
            self._sessions[session.anchor_id] = session
        if session.session_key:
            self._by_key[session.session_key] = session

    def _new_session(self, message_id: str, chat_id: str, loop: asyncio.AbstractEventLoop, *,
                     anchor_id: str | None, session_key: str | None) -> Session:
        session = Session(message_id, chat_id, loop, anchor_id=anchor_id, session_key=session_key)
        session.tag = self.source.settings().agent_name
        session.telemetry = TurnTelemetry()
        session.flusher = self.pipeline.new_flusher(session)
        session.guard = guard_for(session, lambda: self._on_terminated(session))
        self._register(session)
        self.collector.request(chat_id=chat_id)
        return session

    def _on_terminated(self, session: Session) -> None:
        """The reply anchor or card message vanished: stop writing and let Hermes deliver natively."""
        session.state = State.ABORTED
        if session.flusher is not None:
            session.flusher.mark_completed()

    def _drop(self, session: Session) -> None:
        for key in {session.message_id, session.anchor_id}:
            if key and self._sessions.get(key) is session:
                del self._sessions[key]
        if session.session_key and self._by_key.get(session.session_key) is session:
            del self._by_key[session.session_key]
        for old, new in list(self._interrupts.items()):
            if new == session.message_id:
                del self._interrupts[old]
        if session.flusher is not None:
            session.flusher.mark_completed()
        self.pipeline.stop_timer(session)
        if session.images is not None:
            session.images.cancel_pending()

    def _prune(self) -> None:
        ttl = self.source.settings().card_ttl_sec
        now = time.time()
        for session in {id(s): s for s in self._sessions.values()}.values():
            if now - session.created_at > ttl:
                _logger.warning("pruning stale session: msg=%s", session.message_id[:12])
                self._drop(session)

    # ------------------------------------------------------------------ turn start

    def on_message_started(
        self, *, message_id: str | None, chat_id: str, anchor_id: str | None = None, session_key: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        if not message_id:
            # Synthetic turns have no safe reply anchor; native delivery owns them.
            metrics.increment("session.keyless_native_fallback")
            return
        if message_id in self._sessions:
            return
        self._prune()
        loop = self._get_loop()
        if loop is None:
            _logger.warning("no event loop available, skipping: msg=%s", message_id[:12])
            return
        reply = anchor_id if anchor_id and anchor_id != message_id else None
        session = self._new_session(message_id, chat_id, loop, anchor_id=reply, session_key=session_key)
        session.create_task = self._fire(self.pipeline.create(session), loop)
        _logger.info("session created: msg=%s chat=%s", message_id[:12], chat_id[:12])

    # ------------------------------------------------------------------ content events

    def on_thinking(self, *, message_id: str, text: str) -> bool:
        if not self.enabled or (session := self._active(message_id)) is None:
            return False
        parts = split_reasoning_text(text)
        reasoning, answer = parts.get("reasoning_text"), parts.get("answer_text")
        show = bool(reasoning) and self.source.show_reasoning
        if not show and not answer:
            return False
        with session.lock:
            if show:
                session.thoughts += reasoning or ""
                session.last_was_answer = False
            if answer:
                session.add_answer(answer)
        self._wake(session)
        return True

    def on_reasoning(self, *, message_id: str, text: str) -> bool:
        if not self.enabled or not self.source.show_reasoning or (session := self._active(message_id)) is None:
            return False
        with session.lock:
            session.thoughts += text
            session.last_was_answer = False
        self._wake(session)
        return True

    def on_answer(self, *, message_id: str, text: str) -> bool:
        if not self.enabled or (session := self._active(message_id)) is None:
            return False
        with session.lock:
            session.raw_answer.append(text)  # MEDIA: directives are scanned from the raw deltas
            answer = strip_reasoning_tags(text)
            if not answer:
                return False
            session.add_answer(answer)
        self._wake(session)
        return True

    def on_tool_update(
        self, *, message_id: str, tool_name: str, status: str, detail: str = "",
        result: Any = None, is_error: Any = None,
    ) -> bool:
        if not self.enabled or (session := self._active(message_id)) is None:
            return False
        split = ""
        with session.lock:
            session.last_was_answer = False
            if status in _STARTED:
                session.tracker.start(tool_name, detail)
            else:
                session.tracker.finish(tool_name, status, detail, result=result, is_error=is_error)
                split = self._take_pending_split(session, tool_name)
        if split:
            self._split_blocking(session)
        else:
            self._wake(session)
        return True

    @staticmethod
    def _take_pending_split(session: Session, tool_name: str) -> str:
        pending = session.pending_split
        if pending == "clarify" and tool_name.strip().lower() != "clarify":
            _logger.warning("clarify split pending but tool %r did not match: msg=%s", tool_name,
                            session.message_id[:12])
            return ""
        if pending:
            session.pending_split = ""
            if pending == "approval":
                session.state = State.STREAMING
        return pending

    def _split_blocking(self, session: Session) -> None:
        """Move the rest of the turn to a fresh card before Hermes continues, bounded by a timeout."""
        if session.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        coro = self._split(session)
        if running is session.loop:  # called on the loop itself: blocking would deadlock
            session.loop.create_task(coro)
            return
        future = self._fire(coro, session.loop)
        if future is None:
            return
        try:
            future.result(timeout=_PROMPT_SPLIT_WAIT_SEC)
        except Exception:
            _logger.warning("prompt split failed or timed out: msg=%s", session.message_id[:12], exc_info=True)
            future.cancel()

    async def _split(self, session: Session) -> None:
        await self._wait_creation(session)
        if session.has_card and session.state is not State.FAILED:
            await self.pipeline.handoff_for_prompt(session)

    # ------------------------------------------------------------------ interruptions

    def on_aborted(self, *, message_id: str) -> None:
        if not self.enabled or (session := self._active(message_id)) is None:
            return
        self._finish_later(session, Phase.STOPPED)

    async def on_session_aborted(self, *, session_key: str) -> bool:
        if not self.enabled or not session_key:
            return False
        session = self._by_key.pop(session_key, None)
        if session is None or session.state.terminal:
            return False
        return await self._finish_after_creation(session, Phase.STOPPED)

    def on_interrupted(
        self, *, old_message_id: str, new_message_id: str, chat_id: str, anchor_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        old = self._active(old_message_id)
        session_key = session_key or (old.session_key if old is not None else None)
        if old is not None:
            self._finish_later(old, Phase.STOPPED)
        existing = self._sessions.get(new_message_id)
        if existing is None or existing.state.terminal:
            loop = self._get_loop()
            if loop is not None:
                reply = anchor_id if anchor_id and anchor_id != new_message_id else None
                session = self._new_session(new_message_id, chat_id, loop, anchor_id=reply, session_key=session_key)
                session.create_task = self._fire(self.pipeline.create(session), loop)
        self._interrupts[old_message_id] = new_message_id
        for key, value in list(self._interrupts.items()):
            if value == old_message_id:
                self._interrupts[key] = new_message_id

    def on_approval_enter(self, *, message_id: str) -> None:
        if not self.enabled or (session := self._active(message_id)) is None:
            return
        session.pending_split = "approval"
        if session.state is State.STREAMING:
            session.state = State.PAUSED

    def on_clarify_enter(self, *, message_id: str, chat_id: str | None = None, session_key: str | None = None) -> None:
        if not self.enabled or (session := self._active(message_id)) is None or session.state is not State.STREAMING:
            return
        session.state = State.PAUSED

    def on_clarify_exit(self, *, message_id: str, chat_id: str | None = None, session_key: str | None = None) -> None:
        if not self.enabled:
            return
        session = self._sessions.get(message_id)
        if session is None or session.state is not State.PAUSED:
            return  # already taken over by an interrupt
        session.pending_split = "clarify"
        session.state = State.STREAMING  # let the clarify tool's completion reach the card

    # ------------------------------------------------------------------ completion

    async def _wait_creation(self, session: Session) -> bool:
        task = session.create_task
        if task is None:
            return True
        try:
            future = task if isinstance(task, asyncio.Future) else asyncio.wrap_future(task)
            await asyncio.wait_for(future, timeout=_CARD_CREATION_WAIT_SEC)
            return True
        except TimeoutError:
            _logger.warning("card creation timed out: msg=%s", session.message_id[:12])
            task.cancel()
            session.mark_failed()
            return False
        except asyncio.CancelledError:
            session.mark_failed()
            return False
        except Exception:
            _logger.debug("card creation task failed", exc_info=True)
            return False

    def _need_text_fallback(self, session: Session) -> None:
        metrics.increment("card.text_fallback")
        keys = {session.message_id, *([session.anchor_id] if session.anchor_id else [])}
        for key in keys:
            self._fallback[key] = keys

    def consume_text_fallback(self, message_id: str) -> bool:
        """Whether the gateway should undo ``already_sent`` and deliver plain text itself."""
        keys = self._fallback.pop(message_id, None)
        if keys is None:
            return False
        for key in keys:
            self._fallback.pop(key, None)
        return True

    def _completion_session(self, message_id: str) -> Session | None:
        session = self._sessions.get(message_id)
        if session is not None and (not session.state.terminal or session.state is State.FAILED):
            return session
        redirected = self._interrupts.pop(message_id, None)
        if redirected is not None:
            target = self._sessions.get(redirected)
            if target is not None and not target.state.terminal:
                return target
        return None

    def _footer_for(self, session: Session, model: str, context: dict[str, Any] | None) -> Footer:
        """Telemetry wins; Hermes' result payload only fills what telemetry never saw."""
        footer = session.telemetry.footer() if session.telemetry is not None else Footer()
        used = footer.context_used
        cap = footer.context_max
        if context:
            used = used if used is not None else _int(context.get("used_tokens"))
            cap = cap or _int(context.get("max_tokens"))
        return Footer(
            model=footer.model or model, context_used=used, context_max=cap, cache_hit=footer.cache_hit,
            cache_hit_is_floor=footer.cache_hit_is_floor, partial=footer.partial, tag=session.tag,
        )

    async def on_completed_wait(
        self, *, message_id: str, answer: str = "", is_error: bool = False, duration: float = 0.0, model: str = "",
        tokens: dict[str, Any] | None = None, context: dict[str, Any] | None = None, deliver_all_media: bool = False,
    ) -> bool:
        """Finish the card and report whether it was delivered (the gateway then skips its own send)."""
        if not self.enabled:
            return False
        if (session := self._completion_session(message_id)) is None:
            _logger.info("completion for unknown session: msg=%s", message_id[:12])
            return False
        _logger.info("completion: msg=%s has_card=%s state=%s error=%s", session.message_id[:12], session.has_card,
                     session.state.value, is_error)
        if not await self._wait_creation(session):
            if not session.has_card:
                self._need_text_fallback(session)
            self._drop(session)
            return False
        if session.ledger_unavailable:
            await self.pipeline.notify_ledger_unavailable(session)
            self._drop(session)
            return True  # own this outcome so Hermes does not replay an uncertain answer
        if session.state is State.FAILED or not session.has_card:
            if not session.has_card:
                self._need_text_fallback(session)
            self._drop(session)
            return False

        if answer and not session.answers:
            final = strip_reasoning_tags(answer)
            if final:
                session.add_answer(final)
        session.elapsed_s = duration if duration > 0 else None
        session.final_footer = self._footer_for(session, model, context)
        phase = Phase.FAILED if is_error else Phase.DONE
        sent = await self._finish(session, phase)
        if sent:
            await self._deliver_media(session, answer, gateway_delivers=not (deliver_all_media or is_error))
        return sent

    async def _finish(self, session: Session, phase: Phase) -> bool:
        delivered = False
        try:
            self._close_reviews(session, embed=True)
            provider = _provider(session)
            await self.collector.finish(session.chat_id, provider)
            session.sections = self.collector.sections(
                session.telemetry.snapshot, chat_id=session.chat_id, provider=provider, terminal=True,
            ) if session.telemetry is not None else ()
            delivered = await self.pipeline.finalize(session, phase)
            return delivered
        finally:
            self._flush_reviews(session, delivered)
            self._drop(session)

    def _finish_later(self, session: Session, phase: Phase) -> None:
        if session.flusher is not None:
            session.flusher.mark_completed()
        self._fire(self._finish_after_creation(session, phase), session.loop)

    async def _finish_after_creation(self, session: Session, phase: Phase) -> bool:
        session.state = State.ABORTED if phase is Phase.STOPPED else session.state
        if not await self._wait_creation(session):
            self._drop(session)
            return False
        session.final_footer = self._footer_for(session, "", None)
        return await self._finish(session, phase)

    async def _deliver_media(self, session: Session, answer: str, *, gateway_delivers: bool) -> int:
        """Send ``MEDIA:`` attachments the gateway cannot see (best effort, never affects the card)."""
        try:
            paths = media_paths_to_deliver(
                streamed=session.raw_text(), gateway_text=answer, gateway_delivers=gateway_delivers,
            )
            if not paths:
                return 0
            return await deliver_media_files(await self._client_for(session.chat_id), session.chat_id, paths)
        except Exception:
            _logger.warning("media delivery failed: msg=%s", session.message_id[:12], exc_info=True)
            return 0

    # ------------------------------------------------------------------ background review

    def defer_background_review(self, *, message_id: str, text: str, sender: Callable[[str], Any]) -> bool:
        if not self.enabled or not text or not callable(sender) or (session := self._active(message_id)) is None:
            return False
        with session.reviews_lock:
            if session.reviews_closed:
                return False
            session.reviews.append((text, sender))
        return True

    @staticmethod
    def _close_reviews(session: Session, *, embed: bool) -> None:
        with session.reviews_lock:
            session.reviews_closed = True
            pending = list(session.reviews)
        if embed:
            session.notices.extend(text.strip() for text, _ in pending if text.strip())

    @staticmethod
    def _flush_reviews(session: Session, delivered: bool) -> None:
        """Embedded in a delivered card: drop the senders. Otherwise let Hermes send them natively."""
        with session.reviews_lock:
            session.reviews_closed = True
            pending, session.reviews = list(session.reviews), []
        if delivered:
            return
        for text, sender in pending:
            try:
                sender(text)
            except Exception:
                _logger.debug("background review sender failed", exc_info=True)

    # ------------------------------------------------------------------ telemetry

    def observe(self, event: str, payload: dict[str, Any], *, session_key: str | None = None) -> bool:
        """Provider/auxiliary request events from Hermes' observer hooks, bound by session key."""
        try:
            observe_history(self.details_config, event, payload)
        except Exception:
            _logger.debug("history observation failed", exc_info=True)
        session = self._by_key.get(session_key or "")
        if session is None or session.telemetry is None or session.state.terminal:
            return False
        if not session.telemetry.observe(event, payload):
            return False
        provider = _provider(session)
        if provider and provider != session.provider:
            # Account readers are per provider, so they can only start once the turn's provider is known.
            session.provider = provider
            self.collector.request(chat_id=session.chat_id, provider=provider)
        self._wake(session)
        return True

    # ------------------------------------------------------------------ static deliveries

    def on_cron_deliver(
        self, *, chat_id: str, content: str, loop: asyncio.AbstractEventLoop | None, task_name: str = "",
        run_time: str = "", job_id: str = "", media_files: object = None,
    ) -> dict[str, object] | bool:
        if not self.enabled or not content or not chat_id:
            return False
        coro = self.static.cron(chat_id, content, task_name=task_name, run_time=run_time, job_id=job_id,
                                media_files=media_files, text_size=self.source.settings().text_size)
        try:
            if loop is not None and loop.is_running() and not loop.is_closed():
                try:
                    future = asyncio.run_coroutine_threadsafe(coro, loop)
                except Exception:
                    coro.close()
                    raise
                message_id = future.result(timeout=30)
            else:
                message_id = asyncio.run(coro)
            if not message_id:
                raise RuntimeError("cron card send returned no message_id")
            return {"success": True, "message_id": str(message_id), "raw_response": {"delivery": "feishu_card"}}
        except (CronDeliveryOutcomeUnknown, DeliveryLedgerError, TimeoutError):
            _logger.warning("cron card outcome unknown; suppressing native replay", exc_info=True)
            return {"success": False, "delivery_outcome": "unknown"}
        except FeishuAPIError as exc:
            _logger.warning("cron card delivery failed: code=%s", exc.code, exc_info=True)
            if classify_delivery_failure(exc).value == "unknown":
                return {"success": False, "delivery_outcome": "unknown"}
            return False
        except Exception:
            _logger.warning("cron card delivery failed", exc_info=True)
            return False

    async def on_background_deliver(
        self, *, chat_id: str, preview: str, content: str, reply_to_message_id: str | None = None,
    ) -> bool:
        if not self.enabled or not content or not chat_id:
            return False
        try:
            await self.static.background(chat_id, preview, content, reply_to=reply_to_message_id,
                                         text_size=self.source.settings().text_size)
            return True
        except Exception:
            _logger.warning("background card delivery failed", exc_info=True)
            return False


def _int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _provider(session: Session) -> str:
    if session.telemetry is None:
        return ""
    value = session.telemetry.snapshot().get("provider")
    return value if isinstance(value, str) else ""


_controllers: dict[str, Controller] = {}
_controllers_lock = threading.Lock()


def get_controller() -> Controller:
    home = hermes_home().resolve()
    with _controllers_lock:
        controller = _controllers.get(str(home))
        if controller is None:
            controller = _controllers[str(home)] = Controller(home)
        return controller
