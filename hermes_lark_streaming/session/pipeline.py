"""Card I/O for one session: create, stream, hand off to a fresh card, finalize.

Everything here runs on the gateway event loop. The :class:`Flusher` serializes flushes and the
:class:`CardChannel` serializes writes, so a card has exactly one writer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ..card import render
from ..card.model import BlockKind, Phase, RenderOptions
from ..config import ConfigSource, Settings
from ..metrics import metrics
from ..transport import (
    CardChannel,
    CardKitClient,
    DeliveryLedger,
    DeliveryLedgerError,
    DeliveryStatus,
    FeishuAPIError,
    Flusher,
    ImageResolver,
    StreamingClosedError,
    UnavailableGuard,
    classify_delivery_failure,
)
from ..transport.errors import CARDKIT_CONTENT_FAILED, CARDKIT_RATE_LIMITED, ElementNotFoundError
from ..transport.ledger import IDEMPOTENCY_RETRY_WINDOW_SEC
from ..transport.media import strip_media_directives
from ..transport.open_cards import OpenCards
from .state import Session, State

_logger = logging.getLogger("hermes_lark_streaming")

TICK_SEC = 3.0  # live clock refresh while a turn is running
_FINALIZE_ATTEMPTS = 3
_ROLLOVER_RETRY_SEC = 20.0
_UNCERTAIN_NOTICE = (
    "⚠️ 卡片投递确认在网络响应阶段中断; 卡片可能已经送达。请先刷新当前会话, 再决定是否重试。\n"
    "Delivery confirmation was interrupted; refresh this chat before retrying."
)
_LEDGER_NOTICE = "⚠️ 本轮卡片投递记录暂不可读取。回复已暂停以避免重复发送。请检查本地投递账本后重试。"


@dataclass(eq=False)
class Runtime:
    """What the pipeline needs from its owner; replaced piecemeal in tests."""

    source: ConfigSource
    ledger: DeliveryLedger
    client_for: Callable[[str], Awaitable[CardKitClient]]
    details: Any  # details.Runtime: sections(), footer hooks
    open_cards: OpenCards | None = None  # live cards on disk, so a crashed turn's card can be closed later

    @property
    def settings(self) -> Settings:
        return self.source.settings()


def signature(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Pipeline:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime

    # ---------------------------------------------------------------- creation

    def new_flusher(self, session: Session) -> Flusher:
        flusher = Flusher(loop=session.loop)
        pressure = self.rt.settings.backpressure
        flusher.configure_adaptive(enabled=pressure.enabled, min_ms=pressure.min_ms, max_ms=pressure.max_ms)
        return flusher

    def _view(self, session: Session) -> Any:
        """The live view with answer text made card-safe: MEDIA directives stripped, known images swapped in."""
        view = session.view(Phase.RUNNING)
        blocks = tuple(
            replace(b, text=self._clean_answer(session, b.text)) if b.kind is BlockKind.ANSWER else b
            for b in view.blocks
        )
        return replace(view, blocks=blocks)

    @staticmethod
    def _clean_answer(session: Session, text: str) -> str:
        text = strip_media_directives(text)
        return session.images.resolve_images(text) if session.images is not None else text

    def _initial_card(self, session: Session) -> dict[str, Any]:
        return render.render_streaming(self._view(session), self.opts())

    def opts(self) -> RenderOptions:
        """Render options for this instant: Hermes' live ``show_tool_use`` can hide the process panel."""
        opts = self.rt.settings.render
        return opts if self.rt.source.show_tool_use else replace(opts, show_process=False)

    @staticmethod
    def _delivery_key(session: Session, generation: str, route: str) -> str:
        return f"stream:{session.message_id}:generation:{generation}:route:{route}"

    async def _attach(
        self,
        session: Session,
        client: CardKitClient,
        card: dict[str, Any],
        *,
        generation: str,
        reply_to: str | None,
        existing: CardChannel | None = None,
    ) -> tuple[CardChannel, str]:
        """Create the card entity and attach it to the chat with a restart-stable idempotency UUID.

        Returns ``(channel, message_id)``; an empty message id means the attach outcome is unknown.
        """
        ledger = self.rt.ledger
        route = "reply" if reply_to else "chat"
        key = self._delivery_key(session, generation, route)
        entry = ledger.begin(key, f"card.{route}")
        session.delivery_key, session.delivery_status = key, entry.status

        if entry.status is DeliveryStatus.DELIVERED and entry.card_id and entry.message_id:
            metrics.increment("delivery.recovered_delivered")
            return CardChannel(entry.card_id), entry.message_id
        if entry.status is DeliveryStatus.UNKNOWN and time.time() - entry.created_at >= IDEMPOTENCY_RETRY_WINDOW_SEC:
            # Reusing the UUID after Lark's one-hour dedup window could create a second card.
            session.delivery_status = DeliveryStatus.UNKNOWN
            metrics.increment("delivery.expired_unknown_held")
            return CardChannel(entry.card_id) if entry.card_id else (existing or CardChannel("")), ""

        channel = existing if existing is not None else (CardChannel(entry.card_id) if entry.card_id else None)
        if channel is None:
            try:
                channel = await client.create_card(card)
            except Exception:
                ledger.failed(key, DeliveryStatus.NOT_SENT)  # no message was attached; never left pending
                session.delivery_status = DeliveryStatus.NOT_SENT
                raise
            ledger.card_created(key, channel.card_id)
        try:
            if reply_to:
                message_id = await client.reply_card(reply_to, channel, request_uuid=entry.request_uuid)
            else:
                message_id = await client.send_card_entity(session.chat_id, channel, request_uuid=entry.request_uuid)
        except Exception as exc:
            status = classify_delivery_failure(exc)
            ledger.failed(key, status, error_code=exc.code if isinstance(exc, FeishuAPIError) else 0)
            session.delivery_status = status
            metrics.increment(f"delivery.{status.value}")
            if status is DeliveryStatus.UNKNOWN:
                _logger.warning("card attach outcome unknown: msg=%s route=%s", session.message_id[:12], route)
                return channel, ""
            raise
        ledger.delivered(key, card_id=channel.card_id, message_id=message_id)
        session.delivery_status = DeliveryStatus.DELIVERED
        metrics.increment("delivery.delivered")
        return channel, message_id

    async def _attach_with_fallback(
        self, session: Session, client: CardKitClient, card: dict[str, Any], *, generation: str, reply_to: str,
    ) -> tuple[CardChannel, str]:
        """Reply path, then a second reply with a new entity, then a plain chat send.

        Only a structured 230099 (content rejected, nothing created) enters the fallbacks; ambiguous
        outcomes never do.
        """
        try:
            return await self._attach(session, client, card, generation=generation, reply_to=reply_to)
        except FeishuAPIError as error:
            if error.code != CARDKIT_CONTENT_FAILED:
                raise
        try:
            return await self._attach(session, client, card, generation=f"{generation}-retry", reply_to=reply_to)
        except FeishuAPIError:
            previous = self.rt.ledger.get(self._delivery_key(session, f"{generation}-retry", "reply"))
            existing = CardChannel(previous.card_id) if previous and previous.card_id else None
            return await self._attach(
                session, client, card, generation=f"{generation}-standalone", reply_to=None, existing=existing,
            )

    async def create(self, session: Session) -> None:
        """Create and attach the first card, then open the session for streaming."""
        if session.state is not State.IDLE:
            return
        session.state = State.CREATING
        try:
            client = await self.rt.client_for(session.chat_id)
            session.client = client
            card = self._initial_card(session)
            channel, message_id = await self._attach_with_fallback(
                session, client, card, generation="0", reply_to=session.anchor_id or session.message_id,
            )
            self._adopt(session, channel, message_id, card)
            session.images = ImageResolver(client=client, on_image_resolved=lambda: self.schedule(session))
            if not message_id and session.delivery_status is DeliveryStatus.UNKNOWN:
                await self._notify_uncertain(session)
            assert session.flusher is not None
            session.flusher.set_ready(True)
            if session.state is State.CREATING:
                session.state = State.STREAMING
            self._start_timer(session)
            self.schedule(session)
            _logger.info("card created: msg=%s card=%s delivery=%s", session.message_id[:12],
                         (channel.card_id or "")[:12], session.delivery_status.value)
        except DeliveryLedgerError:
            _logger.error("delivery ledger unavailable; holding the answer: msg=%s", session.message_id[:12],
                          exc_info=True)
            session.ledger_unavailable = True
            session.mark_failed()
        except FeishuAPIError:
            _logger.info("card creation rejected; yielding to the gateway", exc_info=True)
            session.mark_failed()
        except Exception:
            _logger.exception("card creation failed")
            session.mark_failed()

    def _adopt(self, session: Session, channel: CardChannel, message_id: str, card: dict[str, Any]) -> None:
        """Make ``channel`` the active card and remember which elements it already contains."""
        session.channel = channel
        session.card_msg_id = message_id or None
        session.card_started = time.monotonic()
        session.streaming_closed = False
        elements = card["body"]["elements"]
        session.pushed = {e["element_id"]: signature(e) for e in elements if e.get("element_id")}
        session.inserted = set(session.pushed)
        if message_id and self.rt.open_cards is not None:
            self.rt.open_cards.add(channel.card_id, session.chat_id, message_id)

    async def _notify_uncertain(self, session: Session) -> None:
        if session.delivery_notice_sent:
            return
        key = f"stream:{session.message_id}:uncertain-notice"
        try:
            entry = self.rt.ledger.begin(key, "notice.unknown")
            if entry.status is DeliveryStatus.DELIVERED:
                session.delivery_notice_sent = True
                return
            expired = time.time() - entry.created_at >= IDEMPOTENCY_RETRY_WINDOW_SEC
            if entry.status is DeliveryStatus.UNKNOWN and expired:
                return
            assert session.client is not None
            message_id = await session.client.send_text(
                session.chat_id, _UNCERTAIN_NOTICE, reply_to=session.anchor_id or session.message_id,
                request_uuid=entry.request_uuid,
            )
            self.rt.ledger.delivered(key, card_id="", message_id=message_id)
            session.delivery_notice_sent = True
        except DeliveryLedgerError:
            await self.notify_ledger_unavailable(session)
        except Exception as exc:
            code = exc.code if isinstance(exc, FeishuAPIError) else 0
            _logger.warning("uncertain-delivery notice failed: code=%s", code)

    async def notify_ledger_unavailable(self, session: Session) -> None:
        if session.delivery_notice_sent or session.client is None:
            return
        request_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL, f"hermes-lark-streaming:ledger-unavailable:{session.chat_id}:{session.message_id}",
        ).hex
        try:
            await session.client.send_text(
                session.chat_id, _LEDGER_NOTICE, reply_to=session.anchor_id or session.message_id,
                request_uuid=request_uuid,
            )
            session.delivery_notice_sent = True
        except Exception:
            _logger.exception("ledger-unavailable notice failed: msg=%s", session.message_id[:12])

    # ---------------------------------------------------------------- streaming

    def schedule(self, session: Session) -> None:
        """Request a throttled flush. Loop thread only (the controller hops threads first)."""
        flusher = session.flusher
        if flusher is None or session.state in (State.IDLE, State.CREATING) or session.state.terminal:
            return
        if session.handoff_in_progress or session.state is State.PAUSED:
            return
        if session.guard is not None and session.guard.should_skip("schedule"):
            return
        flusher.schedule_update(lambda: self.flush(session))

    def _start_timer(self, session: Session) -> None:
        if session.timer is None:
            session.timer = session.loop.create_task(self._tick(session))

    def stop_timer(self, session: Session) -> None:
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None

    async def _tick(self, session: Session) -> None:
        try:
            while not session.state.terminal:
                await asyncio.sleep(TICK_SEC)
                if session.state is State.STREAMING:
                    self.schedule(session)
        except asyncio.CancelledError:
            return

    async def flush(self, session: Session) -> None:
        """Bring the live card up to the session: insert new blocks above the live line, patch panels,
        stream answer text. Elements only ever append or change, so a per-id diff is enough."""
        if session.state.terminal or session.channel is None:
            return
        if session.state is State.PAUSED:
            return
        if await self._maybe_rollover(session):
            return
        if session.streaming_closed:
            return
        if time.monotonic() < session.stream_retry_after:  # one backoff gate for every write, not just streams
            metrics.increment("cardkit.flush.backoff_skipped")
            if session.flusher is not None:
                session.flusher.request_reflush_after(session.stream_retry_after - time.monotonic())
            return
        channel, client = session.channel, session.client
        assert client is not None
        if session.resync_needed:  # an earlier write's outcome is unknown: replace the card from local state
            session.resync_needed = False
            session.recovery_attempts = 0
            await self._recover(session, "resync")
            return
        view = self._view(session)
        elements = render.streaming_elements(view, self.opts())
        too_big = render.count_elements(elements) > render.ELEMENT_BUDGET and len(view.blocks) > 1
        if too_big and await self.handoff(session, reason="elements"):
            return

        actions: list[dict[str, Any]] = []
        committed: dict[str, str] = {}
        streams: list[tuple[str, str, str]] = []
        present = set(session.inserted)
        for element in elements:
            element_id, sig = element["element_id"], signature(element)
            if element_id not in present:
                actions.append({"action": "add_elements", "params": {
                    "type": "insert_before", "target_element_id": render.STATUS_ID, "elements": [element],
                }})
                present.add(element_id)
                committed[element_id] = sig
            elif session.pushed.get(element_id) != sig:
                if element["tag"] == "markdown" and element_id != render.STATUS_ID:
                    streams.append((element_id, element["content"], sig))  # typewriter, not a replace
                else:
                    actions.append({"action": "partial_update_element", "params": {
                        "element_id": element_id, "partial_element": render.partial_for(element),
                    }})
                    committed[element_id] = sig
        if actions:
            try:
                await client.batch_update(channel, actions)
            except StreamingClosedError:
                self._on_stream_closed(session)
                return
            except ElementNotFoundError:
                await self._recover(session, "batch_update")
                return
            except FeishuAPIError as exc:
                self._defer_retry(session, exc.code)
                return
            except Exception:
                # unknown outcome (timeout, connection reset): the channel already spent the sequence; an
                # add_elements may or may not have landed, so rebuild the card from local state next time
                self._defer_retry(session, 0)
                session.resync_needed = True
                return
            session.pushed.update(committed)
            session.inserted = present

        for element_id, content, sig in streams:
            if time.monotonic() < session.stream_retry_after:
                metrics.increment("cardkit.stream.backoff_skipped")
                return
            try:
                await client.update_element_content(channel, element_id, content or " ")
            except StreamingClosedError:
                self._on_stream_closed(session)
                return
            except ElementNotFoundError:
                await self._recover(session, "stream_element")
                return
            except FeishuAPIError as exc:
                self._defer_retry(session, exc.code)
                return
            except Exception:
                self._defer_retry(session, 0)
                return
            session.pushed[element_id] = sig
            session.stream_failures, session.stream_retry_after = 0, 0.0
        session.recovery_attempts = 0  # a clean flush: a later, unrelated element race may recover again

    def _on_stream_closed(self, session: Session) -> None:
        session.streaming_closed = True
        metrics.increment("cardkit.stream.closed_before_completion")
        if session.flusher is not None:
            session.flusher.request_reflush()

    @staticmethod
    def _defer_retry(session: Session, code: int) -> None:
        session.stream_failures = min(16, session.stream_failures + 1)
        delay = min(30.0, 0.5 * (2 ** min(session.stream_failures - 1, 6)))
        if code == CARDKIT_RATE_LIMITED:
            delay = max(delay, 2.0)
        session.stream_retry_after = time.monotonic() + delay
        if session.flusher is not None:
            session.flusher.record_failure(rate_limited=code == CARDKIT_RATE_LIMITED)
            session.flusher.request_reflush_after(delay)  # the text that failed still has to arrive
        metrics.increment("cardkit.stream.retry_deferred")
        if session.stream_failures in (1, 4, 8):
            _logger.warning("card stream retry deferred: code=%s streak=%d delay=%.1fs",
                            code, session.stream_failures, delay)

    async def _recover(self, session: Session, operation: str) -> None:
        """A server-pruned element: rebuild the card once, then replay the local state."""
        assert session.channel is not None and session.client is not None
        if session.recovery_attempts >= 1:
            _logger.error("card element still missing after recovery: op=%s", operation)
            session.mark_failed()
            return
        session.recovery_attempts += 1
        try:
            card = self._initial_card(session)
            await session.client.update_card(session.channel, card)
            self._adopt(session, session.channel, session.card_msg_id or "", card)
            session.pushed.pop(render.ANSWER_ID, None)
            session.stream_failures, session.stream_retry_after = 0, 0.0
            if session.flusher is not None:
                session.flusher.request_reflush()
        except Exception:
            _logger.exception("card element recovery failed: op=%s", operation)
            session.mark_failed()

    # ---------------------------------------------------------------- rollover

    async def _maybe_rollover(self, session: Session) -> bool:
        if session.card_msg_id is None or not session.card_started or session.handoff_in_progress:
            return False
        now = time.monotonic()
        age = now - session.card_started
        if not (session.streaming_closed or age >= self.rt.settings.rollover_sec) or now < session.rollover_retry_after:
            return False
        if await self.handoff(session, reason="closed" if session.streaming_closed else "time_limit"):
            return True
        session.rollover_retry_after = time.monotonic() + _ROLLOVER_RETRY_SEC
        return False

    async def handoff(self, session: Session, *, reason: str) -> bool:
        """Seal the current card (marked as continued) and keep streaming on a fresh one.

        The new card is created first, so a failure leaves the old card open and usable.
        """
        old_channel, client = session.channel, session.client
        if old_channel is None or client is None:
            return False
        generation = session.delivery_generation + 1
        prior = (session.delivery_key, session.delivery_status)
        # Anything the old card should still show is in the view built *before* offsets move.
        sealed_view = session.view(Phase.RUNNING, continued=True)
        steps_total = session.tracker.count
        failed_total = session.steps_offset_failed + sum(
            s.status.value == "failed" for s in sealed_view.steps)
        old_offsets = (session.steps_offset, session.steps_offset_failed, session.answers_offset,
                       session.thoughts_offset)
        session.steps_offset, session.steps_offset_failed = steps_total, failed_total
        session.answers_offset, session.thoughts_offset = len(session.answers), len(session.thoughts)
        session.close_entry()
        old_blocks = session.blocks_offset
        session.blocks_offset = len(session.timeline)
        session.last_was_answer = False
        try:
            card = self._initial_card(session)
            channel, message_id = await self._attach_with_fallback(
                session, client, card, generation=str(generation), reply_to=session.anchor_id or session.message_id,
            )
            if not message_id:
                raise RuntimeError("replacement card attach unconfirmed")
        except BaseException as exc:  # a cancelled handoff must roll back too, or content lands on no card
            session.delivery_key, session.delivery_status = prior
            (session.steps_offset, session.steps_offset_failed, session.answers_offset,
             session.thoughts_offset) = old_offsets
            session.blocks_offset = old_blocks
            if not isinstance(exc, Exception):
                raise
            _logger.warning("card handoff failed; keeping the current card: msg=%s", session.message_id[:12],
                            exc_info=True)
            return False
        session.delivery_generation = generation
        try:
            await self._seal(client, old_channel, sealed_view)
        finally:
            self._adopt(session, channel, message_id, card)
            session.stream_failures, session.stream_retry_after = 0, 0.0
        _logger.info("card handoff: msg=%s reason=%s old=%s new=%s", session.message_id[:12], reason,
                     old_channel.card_id[:12], channel.card_id[:12])
        if session.flusher is not None:
            session.flusher.request_reflush()
        return True

    async def _seal(self, client: CardKitClient, channel: CardChannel, view: Any) -> None:
        card = render.render_final(view, self.opts())
        try:
            if channel.streaming:
                try:
                    await client.close_streaming(channel)
                except StreamingClosedError:
                    channel.streaming = False
            await client.update_card(channel, card)
            if self.rt.open_cards is not None:
                self.rt.open_cards.remove(channel.card_id)
        except Exception:
            _logger.warning("sealing the old card failed; continuing", exc_info=True)

    async def mark_interrupted(self, client: CardKitClient, card_id: str) -> None:
        """Close a card left live by a turn that died with its gateway, keeping what it already shows.

        The old process's sequence counter is gone; a wall-clock second is above anything a turn uses
        (a few hundred writes), and the channel skips ahead on a conflict anyway.
        """
        channel = CardChannel(card_id, sequence=int(time.time()))
        partial = render.partial_for(render.interrupted_status())
        await client.batch_update(channel, [{"action": "partial_update_element", "params": {
            "element_id": render.STATUS_ID, "partial_element": partial,
        }}])
        with contextlib.suppress(Exception):  # already closed by Feishu after ten minutes, usually
            await client.close_streaming(channel)

    async def handoff_for_prompt(self, session: Session) -> bool:
        """Clarify/approval: flush, then move the rest of the turn onto a fresh card."""
        session.handoff_in_progress = True
        try:
            if session.flusher is not None:
                await session.flusher.wait_for_flush()
                try:
                    await session.flusher.flush_now(lambda: self.flush(session))
                except Exception:
                    _logger.debug("pre-handoff flush failed", exc_info=True)
            return await self.handoff(session, reason="prompt")
        finally:
            session.handoff_in_progress = False

    # ---------------------------------------------------------------- finalize

    async def finalize(self, session: Session, phase: Phase) -> bool:
        """Close streaming and replace the card with its terminal form. Returns whether it landed."""
        if session.guard is not None and session.guard.should_skip("finalize"):
            return False
        if session.flusher is not None:
            await session.flusher.wait_for_flush()
            session.flusher.mark_completed()
        self.stop_timer(session)
        if phase is not Phase.RUNNING:
            session.tracker.unconfirm_running()
        if session.images is not None:
            session.answers = [await self._resolve(session, t) for t in session.answers]
        session.answers = [strip_media_directives(t) for t in session.answers]
        view = session.view(phase, finished=True)
        card = render.render_final(view, self.opts())
        channel, client = session.channel, session.client
        if channel is None or client is None:
            return False
        closed = session.streaming_closed or not channel.streaming
        for attempt in range(_FINALIZE_ATTEMPTS):
            try:
                if not closed:
                    await client.close_streaming(channel)
                    closed = True
                await client.update_card(channel, card)
                session.state = {Phase.FAILED: State.FAILED, Phase.STOPPED: State.ABORTED}.get(phase, State.COMPLETED)
                if self.rt.open_cards is not None:
                    self.rt.open_cards.remove(channel.card_id)
                metrics.increment("card.completed")
                _logger.info("card finalized: msg=%s card=%s phase=%s steps=%d failed=%d elapsed=%.1fs",
                             session.message_id[:12], channel.card_id[:12], phase.value, view.total_steps,
                             view.failed_steps, view.elapsed_s or 0.0)
                if session.delivery_status is DeliveryStatus.UNKNOWN:
                    await self._notify_uncertain(session)
                return True
            except StreamingClosedError:
                closed = True
                session.streaming_closed = True
                continue
            except Exception as exc:
                _logger.warning("card finalize attempt %d failed: %s", attempt, type(exc).__name__, exc_info=True)
                if session.guard is not None and session.guard.terminate("finalize", exc):
                    return False
                if attempt < _FINALIZE_ATTEMPTS - 1:
                    await asyncio.sleep(2 ** attempt)
        _logger.error("card finalize failed after %d attempts: card=%s", _FINALIZE_ATTEMPTS, channel.card_id[:12])
        session.mark_failed()
        metrics.increment("card.completion_failed")
        return False

    @staticmethod
    async def _resolve(session: Session, text: str) -> str:
        assert session.images is not None
        try:
            return await session.images.resolve_await(text)
        except Exception:
            _logger.debug("image resolve failed", exc_info=True)
            return text


def guard_for(session: Session, on_terminate: Callable[[], None]) -> UnavailableGuard:
    return UnavailableGuard(session.anchor_id or session.message_id, lambda: session.card_msg_id, on_terminate)
