"""Controller: the facade the hooks layer drives.

Hermes calls in from agent worker threads (deltas, tools) and from its event loop (completion). Every
public ``on_*`` method therefore only mutates session state under the session lock and then hops to the
loop with ``call_soon_threadsafe``; all card I/O happens in :class:`Pipeline` on the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import CancelledError
from concurrent.futures import Future as ConcurrentFuture
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..card.model import Footer, Phase, TodoItem
from ..card.static import render_recovered
from ..config import ConfigSource, hermes_home
from ..details import DetailsCollector, DetailsConfig, TurnTelemetry, observe_history
from ..details.pool import bench_until, pool_route
from ..details.providers import short_name, with_canonical_provider
from ..metrics import metrics
from ..transport import (
    CardKitClient,
    ClientConfig,
    DeliveryLedger,
    DeliveryLedgerError,
    FeishuAPIError,
    classify_delivery_failure,
)
from ..transport.media import deliver_media_files, extract_media_paths, media_paths_to_deliver
from ..transport.open_cards import OPEN_CARDS_FILE, OpenCards
from ..transport.routing import BotRegistry
from .agents import SUBAGENT_EVENTS, AgentBook
from .pipeline import Pipeline, Runtime, guard_for
from .state import Entry, Session, State
from .static import CronDeliveryOutcomeUnknown, StaticDelivery
from .synthetic import delegation_batch, synthetic_note
from .text import split_reasoning_text, strip_reasoning_tags

_logger = logging.getLogger("hermes_lark_streaming")
_CARD_CREATION_WAIT_SEC = 10.0
_PROMPT_SPLIT_WAIT_SEC = _CARD_CREATION_WAIT_SEC * 2 + 30
_ABANDONED_SEC = 6 * 3600.0  # a turn silent this long has lost its completion
_SWEEP_INTERVAL_SEC = 300.0  # look for cards left live by a dead gateway at most this often
_ORPHAN_MIN_AGE_SEC = 120.0
_POOLED = {"opencode-go": short_name("opencode-go")}  # providers whose keys rotate in a Hermes pool
_NOTICE_MAX_CHARS = 400  # longer text (a /status reply, an answer) is never a notice
_NOTICE_MAX_LINES = 5  # newlines; six lines or more is content, not a notice
_NOTICE_IDS_MAX = 512
_FOLD_ACK_SEC = 15.0  # Hermes' busy ack after a folded message repeats the "💬 你补充" line
_FOLD_ACK_HEADS = ("↪", "⏩", "⚡")  # redirect / steer / interrupt acks, in every Hermes language
_RECALL = re.compile(r"recalled (?:(\d+) memor(?:y|ies)|relevant memory)")  # Hermes writes this line in English
_RECOVERED = "♻️ Recovered reply"
_RECOVERED_REASONS = (
    ("gateway restarted", "网关重启时没确认送达 · 可能与上面重复"),
    ("reconnected", "平台重连后补发 · 可能与上面重复"),
    ("rate limit", "原消息被限流拒收 · 部分内容可能已在上面"),
)
_RECENT_SEC = 1800.0  # how long a finished turn's ids still identify their chat (see ``_revive``)
_RECENT_MAX = 256
_BACKGROUND_REFRESH_SEC = 5.0  # a finished card re-renders at most this often while its subagents run
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
        self._synthetic: dict[str, str] = {}  # session key -> id of the running Hermes-generated turn
        self._background: dict[str, Session] = {}  # finished cards whose background subagents still report
        self._batches: dict[str, AgentBook] = {}  # recent background batches, for their summary turn's card
        self._last_batch: dict[str, tuple[str, AgentBook, float]] = {}  # chat -> newest background batch
        self._interrupts: dict[str, str] = {}
        # ids of turns that already finished -> (chat, session key, when): Hermes may start another turn
        # under one of them (a queued follow-up, a notice anchored to the last message)
        self._recent: dict[str, tuple[str, str | None, float]] = {}
        self._revive_lock = threading.Lock()
        # ids handed back to Hermes for notices folded into a card -> (session, timeline entry or None)
        self._notice_ids: dict[str, tuple[Session, Entry | None]] = {}
        self._fallback: dict[str, set[str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.details_config = DetailsConfig.from_mapping(self.source.settings().details)
        self.collector = DetailsCollector(self.details_config)
        self.ledger = DeliveryLedger()
        self.open_cards = OpenCards(self.home / "state" / OPEN_CARDS_FILE)
        self.pipeline = Pipeline(Runtime(self.source, self.ledger, self._client_for, self.collector,
                                         open_cards=self.open_cards))
        self._swept_at: float | None = None
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
        session.touched = time.monotonic()
        return session

    def _live(self, message_id: str) -> Session | None:
        """The running session for a content event, opening a card for a turn whose start was missed."""
        return self._active(message_id) or self._revive(message_id)

    def _revive(self, message_id: str) -> Session | None:
        """A turn streams under the id of one that already finished in this chat, without a start event
        (Hermes ran a queued follow-up or a notice under the old anchor). Give it a card instead of letting
        the reply fall back to plain text. Ids never seen here stay native."""
        if not message_id or not self.enabled:
            return None
        with self._revive_lock:
            if (session := self._active(message_id)) is not None:
                return session
            seen = self._recent.get(message_id)
            if seen is None or time.monotonic() - seen[2] > _RECENT_SEC:
                return None
            existing = self._sessions.get(message_id)
            if existing is not None and not existing.state.terminal:
                return None  # alive but skipping hooks (guard): not ours to replace
            loop = self._get_loop()
            if loop is None:
                return None
            del self._recent[message_id]
            self._sessions.pop(message_id, None)
            session = self._new_session(message_id, seen[0], loop, anchor_id=None, session_key=seen[1])
            session.create_task = self._fire(self.pipeline.create(session), loop)
            metrics.increment("session.revived")
            _logger.info("session revived: msg=%s chat=%s", message_id[:12], seen[0][:12])
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
        now = time.monotonic()
        for key in {session.message_id, session.anchor_id, *session.aliases}:
            if key and not key.startswith("synthetic-"):
                self._recent[key] = (session.chat_id, session.session_key, now)
        while len(self._recent) > _RECENT_MAX:
            del self._recent[next(iter(self._recent))]
        if session.session_key and self._synthetic.get(session.session_key) == session.message_id:
            del self._synthetic[session.session_key]
        for key in {session.message_id, session.anchor_id, *session.aliases}:
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
        """Forget finished sessions after ``card_ttl_sec``. A running turn is never cut short for its age
        (agent tasks run for hours); only one silent for ``_ABANDONED_SEC`` has lost its completion, and
        its card is closed as stopped instead of being left spinning."""
        ttl = self.source.settings().card_ttl_sec
        now = time.monotonic()
        for parked in {id(s): s for s in self._background.values()}.values():
            if now - max(parked.touched, parked.parked_at) > _ABANDONED_SEC:
                self._unpark(parked)  # its subagents went silent; the card keeps its last state
        for session in {id(s): s for s in self._sessions.values()}.values():
            idle = now - session.touched
            if session.state.terminal and idle > ttl:
                self._drop(session)
            elif idle > max(ttl, _ABANDONED_SEC):
                _logger.warning("closing abandoned session: msg=%s idle=%.0fs", session.message_id[:12], idle)
                self._fire(self._abandon(session), session.loop)

    async def _sweep_open_cards(self) -> None:
        """Close cards a previous gateway (or a turn that lost its completion) left with the running look."""
        live = {s.channel.card_id for s in self._sessions.values() if s.channel is not None}
        for card in self.open_cards.orphans(live, min_age=_ORPHAN_MIN_AGE_SEC):
            try:
                client = await self._client_for(card.chat_id)
                await asyncio.wait_for(self.pipeline.mark_interrupted(client, card.card_id), timeout=30)
            except Exception as exc:
                _logger.warning("closing interrupted card failed: card=%s error=%s", card.card_id[:12],
                                type(exc).__name__)
                self.open_cards.attempted(card, give_up_after=3)
            else:
                self.open_cards.remove(card.card_id)
                _logger.info("interrupted card closed: card=%s chat=%s", card.card_id[:12], card.chat_id[:12])

    async def _abandon(self, session: Session) -> None:
        try:
            if session.has_card and not session.state.terminal:
                await asyncio.wait_for(self.pipeline.finalize(session, Phase.STOPPED), timeout=30)
        except Exception:
            _logger.warning("abandoned card close failed: msg=%s", session.message_id[:12], exc_info=True)
        finally:
            self._drop(session)

    # ------------------------------------------------------------------ turn start

    def on_message_started(
        self, *, message_id: str | None, chat_id: str, anchor_id: str | None = None, session_key: str | None = None,
        text: str = "",
    ) -> None:
        if not self.enabled:
            return
        synthetic = None
        if not message_id:
            # A Hermes-generated turn (subagent batch results, a background process notice) has no message.
            # The known kinds get a card of their own, posted into the chat; anything else stays native.
            synthetic = synthetic_note(text)
            if synthetic is None or not session_key or not chat_id:
                metrics.increment("session.keyless_native_fallback")
                return
            message_id = f"synthetic-{uuid.uuid4().hex[:16]}"
            anchor_id = None
        existing = self._sessions.get(message_id)
        if existing is not None:
            if not existing.state.terminal:
                return
            # Hermes reuses the id of a turn that just finished (a queued follow-up, a notice anchored to the
            # last message): this is a new turn and gets its own card, not the finished one's leftovers.
            self._sessions.pop(message_id, None)
        notice = synthetic_note(text) if synthetic is None else None
        self._prune()
        loop = self._get_loop()
        if loop is None:
            _logger.warning("no event loop available, skipping: msg=%s", message_id[:12])
            return
        now = time.monotonic()
        if self._swept_at is None or now - self._swept_at >= _SWEEP_INTERVAL_SEC:
            self._swept_at = now
            self._fire(self._sweep_open_cards(), loop)
        reply = anchor_id if anchor_id and anchor_id != message_id else None
        session = self._new_session(message_id, chat_id, loop, anchor_id=reply, session_key=session_key)
        if notice is not None:
            # A Hermes notice anchored to an old message: say what it is and post it, don't quote that message
            session.synthetic = True
            session.add_note(notice[1], label=notice[0])
        if synthetic is not None and session_key:
            session.synthetic = True
            session.add_note(synthetic[1], label=synthetic[0])
            self._synthetic[session_key] = message_id
            found = self._summary_batch(chat_id, delegation_batch(text))
            if found is not None:
                session.add_agents_snapshot(found[1], found[0])  # what the subagents did, above the summary
        session.create_task = self._fire(self.pipeline.create(session), loop)
        _logger.info("session created: msg=%s chat=%s", message_id[:12], chat_id[:12])

    def on_todos(self, *, message_id: str, todos: list[TodoItem]) -> bool:
        """The agent rewrote its task list (Hermes' todo tool): show the newest revision."""
        if not self.enabled or (session := self._active(message_id)) is None:
            return False
        with session.lock:
            session.todos = tuple(todos)
        self._wake(session)
        return True

    # ------------------------------------------------------------------ subagents

    def on_subagent_event(
        self, *, message_id: str | None, event_type: str, tool_name: str = "", preview: str = "",
        details: dict[str, Any] | None = None,
    ) -> bool:
        """A delegated child's progress, relayed through the parent turn's tool callback (worker threads)."""
        if not self.enabled or not message_id or event_type not in SUBAGENT_EVENTS:
            return False
        session = self._sessions.get(message_id)
        parked = False
        if session is None or session.state.terminal:
            session, parked = self._background.get(message_id), True
        if session is None:
            return False
        session.touched = time.monotonic()
        with session.lock:
            changed = session.record_subagent(event_type, tool_name, preview, details or {})
        if changed and parked:
            urgent = event_type == "subagent.complete"
            with contextlib.suppress(RuntimeError):  # loop closed during shutdown
                session.loop.call_soon_threadsafe(self._schedule_refresh, session, urgent)
        elif changed:
            self._wake(session)
        return True  # ours: Hermes' own subagent progress bubbles would duplicate the panel

    def _park(self, session: Session) -> None:
        """The turn ended but its background subagents still run: keep the card to update their panel."""
        for batch in session.agents.batches():
            self._batches[batch] = session.agents
            self._last_batch[session.chat_id] = (batch, session.agents, time.monotonic())
        while len(self._batches) > 16:
            del self._batches[next(iter(self._batches))]
        if session.channel is None or session.final_phase is None:
            return
        session.parked_at = time.monotonic()
        for key in {session.message_id, session.anchor_id, *session.aliases}:
            if key:
                self._background[key] = session
        _logger.info("tracking background subagents: msg=%s", session.message_id[:12])

    def _unpark(self, session: Session) -> None:
        for key, value in list(self._background.items()):
            if value is session:
                del self._background[key]

    def _schedule_refresh(self, session: Session, urgent: bool) -> None:
        """Loop thread: coalesce refreshes; a finished subagent refreshes almost at once."""
        delay = 0.5 if urgent else _BACKGROUND_REFRESH_SEC
        if session.background_refresh is not None:
            if not urgent:
                return
            session.background_refresh.cancel()
        session.background_refresh = session.loop.call_later(delay, self._start_refresh, session)

    def _start_refresh(self, session: Session) -> None:
        session.background_refresh = None
        session.loop.create_task(self._refresh(session))

    async def _refresh(self, session: Session) -> None:
        try:
            await asyncio.wait_for(self.pipeline.refresh_finished(session), timeout=30)
        except Exception:
            _logger.warning("background subagent refresh failed: msg=%s", session.message_id[:12], exc_info=True)
        if not session.agents.running:
            self._unpark(session)
            _logger.info("background subagents done: msg=%s", session.message_id[:12])

    def _summary_batch(self, chat_id: str, batch: str | None) -> tuple[str, AgentBook] | None:
        """The background batch a summary turn reports on. Hermes names the async batch and the live-progress
        batch with separate ids, so when the id does not match, the chat's newest batch (once) stands in: the
        summary turn arrives right after its batch finishes."""
        if batch and (book := self._batches.get(batch)) is not None:
            return batch, book
        last = self._last_batch.pop(chat_id, None)
        if last is not None and time.monotonic() - last[2] < 1800:
            return last[0], last[1]
        return None

    def synthetic_id_for(self, session_key: str | None) -> str | None:
        """Hooks of a Hermes-generated turn carry no message id; its session key names the card instead."""
        return self._synthetic.get(session_key) if session_key else None

    def on_turn_folded(
        self, *, message_id: str | None, new_message_id: str | None, anchor_id: str | None = None, text: str = "",
        session_key: str | None = None,
    ) -> bool:
        """Hermes folded a message sent mid-turn into the running turn (steer, or interrupt-mode redirect).

        A redirect re-anchors the turn: from here on its deltas, follow-up boundary and completion carry
        the *new* message id. Without this alias every later update was refused as an unknown session, the
        card froze and Hermes delivered the rest as plain text.
        """
        if not self.enabled:
            return False
        session = self._sessions.get(message_id) if message_id else None
        if session is None and session_key:
            session = self._by_key.get(session_key)
        if session is None or session.state.terminal:
            return False
        for alias in (new_message_id, anchor_id):
            if alias and self._sessions.get(alias) is not session:
                self._sessions[alias] = session
                session.aliases.append(alias)
        session.folded_at = time.monotonic()
        if text.strip():
            with session.lock:
                session.add_note(text.strip())
            self._wake(session)
        _logger.info("turn folded: msg=%s new=%s", session.message_id[:12], (new_message_id or "")[:12])
        return True

    # ------------------------------------------------------------------ Hermes notices

    def _running_in(self, chat_id: str) -> Session | None:
        """The one card running in a chat, if it may take a notice: not waiting on an approval or a clarify
        answer (those prompts must stay separate messages), not handed back to native delivery."""
        running = [s for s in {id(s): s for s in self._sessions.values()}.values()
                   if s.chat_id == chat_id and not s.state.terminal]
        if len(running) != 1:
            return None  # several people's turns in one group: the text could belong to any of them
        session = running[0]
        if session.state is State.PAUSED or session.message_id in self._fallback:
            return None
        if session.guard is not None and session.guard.should_skip("hook"):
            return None
        return session

    async def on_outbound(self, *, chat_id: str, content: str, reply_to: str | None = None) -> str | None:
        """Every plain message Hermes sends to a Feishu chat: a recovered reply becomes a card, a short notice
        a line in the running card; ``None`` lets the adapter send it itself."""
        if self.enabled and isinstance(content, str) and content.startswith(_RECOVERED):
            return await self._recovered_card(chat_id, content)
        return self.on_outbound_text(chat_id=chat_id, content=content, reply_to=reply_to)

    async def _recovered_card(self, chat_id: str, content: str) -> str | None:
        head, _, body = content.partition("\n\n")
        reason = next((zh for key, zh in _RECOVERED_REASONS if key in head), "补发 · 可能与上面重复")
        try:
            client = await self._client_for(chat_id)
            message_id = await client.send_card(chat_id, render_recovered(body or content, reason))
        except Exception:
            _logger.warning("recovered reply card failed, Hermes sends text: chat=%s", chat_id[:12], exc_info=True)
            return None
        metrics.increment("notice.recovered_card")
        return str(message_id) if message_id else None

    def on_outbound_text(self, *, chat_id: str, content: str, reply_to: str | None = None) -> str | None:
        """Hermes is about to post a plain message into a chat whose card is running (a busy ack, the
        memory-recall line, a process receipt, a credits notice). Keep it in the card as a grey line and
        hand back an id for later edits; ``None`` lets the message go out as usual."""
        if not self.enabled or not chat_id or not isinstance(content, str):
            return None
        text = content.strip()
        if not text or len(text) > _NOTICE_MAX_CHARS or text.count("\n") >= _NOTICE_MAX_LINES:
            return None
        session = self._running_in(chat_id)
        if session is None:
            return None
        note_id = f"lark-notice-{uuid.uuid4().hex[:16]}"
        if self._redundant_ack(session, text, reply_to):
            metrics.increment("notice.ack_dropped")
            self._remember_notice(note_id, session, None)
            return note_id  # the "💬 你补充" line already says it
        recall = _RECALL.search(text)
        if recall is not None:
            session.memories = (session.memories or 0) + int(recall.group(1) or 0)  # a footer chip, not a line
            self._remember_notice(note_id, session, None)
            self._wake(session)
            return note_id
        with session.lock:
            entry = session.add_notice(text)
        self._remember_notice(note_id, session, entry)
        session.touched = time.monotonic()
        self._wake(session)
        metrics.increment("notice.folded")
        return note_id

    def _remember_notice(self, note_id: str, session: Session, entry: Entry | None) -> None:
        self._notice_ids[note_id] = (session, entry)
        while len(self._notice_ids) > _NOTICE_IDS_MAX:
            del self._notice_ids[next(iter(self._notice_ids))]

    @staticmethod
    def _redundant_ack(session: Session, text: str, reply_to: str | None) -> bool:
        if not text.startswith(_FOLD_ACK_HEADS):
            return False
        folded = {*session.aliases} - {session.message_id}
        return bool(reply_to and reply_to in folded) or time.monotonic() - session.folded_at < _FOLD_ACK_SEC

    def on_outbound_edit(self, *, message_id: str, content: str) -> bool:
        """Hermes edits a notice it believes it sent: rewrite the line (a finished card keeps its text)."""
        found = self._notice_ids.get(message_id) if isinstance(message_id, str) else None
        if found is None:
            return False
        session, entry = found
        if entry is not None and not session.state.terminal and isinstance(content, str) and content.strip():
            with session.lock:
                entry.text = content.strip()[:_NOTICE_MAX_CHARS]
            self._wake(session)
        return True

    def on_outbound_delete(self, *, message_id: str) -> bool:
        """Hermes cleans up a notice (progress bubbles at the end of a turn): drop the line while it runs."""
        found = self._notice_ids.pop(message_id, None) if isinstance(message_id, str) else None
        if found is None:
            return False
        session, entry = found
        if entry is not None and not session.state.terminal:
            with session.lock:
                entry.text = ""
            self._wake(session)
        return True

    # ------------------------------------------------------------------ content events

    def on_thinking(self, *, message_id: str, text: str) -> bool:
        if not self.enabled or not text or (session := self._live(message_id)) is None:
            return False
        parts = split_reasoning_text(text)
        reasoning, answer = parts.get("reasoning_text"), parts.get("answer_text")
        show = bool(reasoning) and self.source.show_reasoning
        if not show and not answer:
            return False
        with session.lock:
            if show:
                session.add_thought(reasoning or "")
            if answer:
                session.add_answer(answer)
        self._wake(session)
        return True

    def on_reasoning(self, *, message_id: str, text: str) -> bool:
        if not self.enabled or not self.source.show_reasoning or (session := self._active(message_id)) is None:
            return False
        with session.lock:
            session.add_thought(text)
        self._wake(session)
        return True

    def on_answer(self, *, message_id: str, text: str) -> bool:
        if not self.enabled or not text or (session := self._live(message_id)) is None:
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
        if not self.enabled:
            return False
        session = self._live(message_id) if status in _STARTED else self._active(message_id)
        if session is None:
            return False
        split = ""
        with session.lock:
            if status in _STARTED:
                session.add_tool(session.tracker.start(tool_name, detail))
            else:
                _, record = session.tracker.finish(tool_name, status, detail, result=result, is_error=is_error)
                session.add_tool(record)
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

    def _close_late_card(self, session: Session) -> None:
        """Creation finished after the turn gave up on it (the answer went out as text): close the card."""
        if session.has_card:
            self._fire(self.pipeline.finalize(session, Phase.STOPPED), session.loop)

    async def _wait_creation(self, session: Session) -> bool:
        task = session.create_task
        if task is None:
            return True
        try:
            future = task if isinstance(task, asyncio.Future) else asyncio.wrap_future(task)
            # shielded: cancelling mid reply_card would leave a visible card nobody finalizes
            await asyncio.wait_for(asyncio.shield(future), timeout=_CARD_CREATION_WAIT_SEC)
            return True
        except TimeoutError:
            _logger.warning("card creation timed out: msg=%s", session.message_id[:12])
            session.mark_failed()
            future.add_done_callback(lambda _f: self._close_late_card(session))
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
            cache_hit_is_floor=footer.cache_hit_is_floor, partial=footer.partial,
            route=self._route_with_keys(footer.route, session), reasoning=footer.reasoning,
            memories=session.memories,
        )

    def _note_route_switch(self, session: Session) -> None:
        """A request went to another subscription than the one before: say so in the timeline, with when the
        exhausted key comes back (worker thread; reads the pool state Hermes persisted for the rotation)."""
        route = self._route_with_keys(session.telemetry.footer().route, session)
        tail = route.split(" → ")[-1] if route else ""
        if not tail or tail == session.route_tail:
            return
        old, session.route_tail = session.route_tail, tail
        if not old:
            return
        rows = [r for r in self.details_config.account_settings().get("accounts") or [] if isinstance(r, dict)]
        until = next((u for p in _POOLED if (u := bench_until(self.home, p, old, rows)) is not None), None)
        why = ""
        if until is not None:
            moment = datetime.fromtimestamp(until, ZoneInfo(self.details_config.zone))
            when = moment.strftime("%H:%M" if until - time.time() < 86400 else "%m-%d %H:%M")
            why = f" · {old} 额度用完 · {when} 恢复"
        with session.lock:
            session.add_note(f"{old} → {tail}{why}", label="🔀 换线路")
        _logger.info("route switched: msg=%s %s -> %s", session.message_id[:12], old, tail)

    def _route_with_keys(self, route: str, session: Session) -> str:
        """Name the subscription key behind a pooled provider ("Go" → "Go a22 → Go 221")."""
        if not route:
            return route
        since = session.telemetry.created_at if session.telemetry is not None else time.time()
        rows = [r for r in self.details_config.account_settings().get("accounts") or [] if isinstance(r, dict)]
        parts = route.split(" → ")
        for provider, short in _POOLED.items():
            if short in parts:
                keys = pool_route(self.home, provider, since, rows)
                if keys:
                    i = len(parts) - 1 - parts[::-1].index(short)  # the newest use of the provider
                    parts[i:i + 1] = keys
        return " → ".join(parts)[:60]

    async def on_completed_wait(
        self, *, message_id: str | None, answer: str = "", is_error: bool = False,
        duration: float = 0.0, model: str = "",
        tokens: dict[str, Any] | None = None, context: dict[str, Any] | None = None, deliver_all_media: bool = False,
    ) -> bool:
        """Finish the card and report whether it was delivered (the gateway then skips its own send)."""
        if not self.enabled or not isinstance(message_id, str) or not message_id:
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
        if not session.has_card:
            self._need_text_fallback(session)
            self._drop(session)
            return False
        # A FAILED session (writes kept failing mid-turn) still owns a visible card: one terminal update
        # usually lands and carries the whole answer; only if it does not does Hermes send text.

        if answer and not session.answers:
            final = strip_reasoning_tags(answer)
            if final:
                session.add_answer(final)
        session.elapsed_s = duration if duration > 0 else None
        session.final_footer = self._footer_for(session, model, context)
        with contextlib.suppress(Exception):  # the file list is a courtesy; never block the card on it
            paths = dict.fromkeys([*extract_media_paths(session.raw_text()), *extract_media_paths(answer or "")])
            session.outputs = tuple(Path(p).name for p in paths)
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
            if delivered and session.agents.running:
                self._park(session)

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
        payload = with_canonical_provider(payload)  # one product id per subscription, whatever Hermes calls it
        try:
            observe_history(self.details_config, event, payload)
        except Exception:
            _logger.debug("history observation failed", exc_info=True)
        session = self._by_key.get(session_key or "")
        if session is None or session.telemetry is None or session.state.terminal:
            return False
        # The pre-request payload may be truncated; execution middleware still carries the reasoning controls.
        observed = (session.telemetry.observe_execution(payload) if event == "llm_execution"
                    else session.telemetry.observe(event, payload))
        if not observed:
            return False
        if event == "pre_api_request":
            self._note_route_switch(session)
        provider = _provider(session)
        if provider and provider != session.provider:
            # Account readers are per provider, so they can only start once the turn's provider is known.
            # Observer hooks run on agent worker threads and the readers start asyncio tasks: hop to the loop.
            session.provider = provider
            request = functools.partial(self.collector.request, chat_id=session.chat_id, provider=provider)
            with contextlib.suppress(RuntimeError):  # loop closed during shutdown
                session.loop.call_soon_threadsafe(request)
        self._wake(session)
        return True

    # ------------------------------------------------------------------ static deliveries

    def on_cron_deliver(
        self, *, chat_id: str, content: str, loop: asyncio.AbstractEventLoop | None, task_name: str = "",
        run_time: str = "", job_id: str = "", media_files: object = None, execution_id: str = "",
    ) -> dict[str, object] | bool:
        if not self.enabled or not content or not chat_id:
            return False
        coro = self.static.cron(chat_id, content, task_name=task_name, run_time=run_time, job_id=job_id,
                                media_files=media_files, text_size=self.source.settings().text_size,
                                execution_id=execution_id)
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
