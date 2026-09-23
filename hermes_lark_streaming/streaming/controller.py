"""流式卡片的异步 API 编排 — 创建、刷新、拆卡、完成."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from ..cardkit.builder import (
    _LOADING_ELEMENT_ID,
    build_background_card,
    build_complete_card,
    build_cron_card,
    build_streaming_card_v2,
)
from ..cardkit.markdown import (
    _downgrade_tables,
    optimize_markdown_style,
)
from ..delivery import IDEMPOTENCY_RETRY_WINDOW_SEC, DeliveryLedgerError, DeliveryStatus, delivery_ledger
from ..feishu import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_RATE_LIMITED,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    classify_delivery_failure,
)
from ..history import compact_terminal_segments
from ..metrics import metrics
from .diagnostics import compact_ids, extract_missing_element_id, segment_state_for_log, summarize_actions
from .flush import CARDKIT_MS
from .image import ImageResolver
from .media import deliver_media_files, hook_media_paths, strip_media_directives
from .segment_helper import (
    ELEMENT_THRESHOLD,
    FOOTER_RESERVE,
    build_add_segment_action,
    build_reasoning_finalized_action,
    build_tool_update_action,
    estimate_segment_elements,
    estimate_tool_elements,
    find_tool_split_offset,
    tool_segment_end,
)
from .segments import Segment, SegmentState, SegmentType
from .session import SessionState
from .text import split_reasoning_text
from .tooluse import ToolUseTracker

if TYPE_CHECKING:
    from ..config import Config
    from ..feishu import FeishuClient
    from .session import CardSession
    from .tooluse import ToolDisplayStep

_logger = logging.getLogger("hermes_lark_streaming")


async def _resolve_answer_images(
    segments: list[Segment],
    resolver: ImageResolver,
    *,
    log_prefix: str,
) -> None:
    """解析 answer segment 中的 markdown 图片，并原地更新文本."""
    for seg in segments:
        if seg.type != SegmentType.ANSWER or not seg.text:
            continue
        try:
            seg.text = await resolver.resolve_await(seg.text)
        except Exception:
            _logger.debug("%s image resolve failed: el=%s", log_prefix, seg.el_id, exc_info=True)


def _strip_answer_media_directives(segments: list[Segment]) -> None:
    """卡片正文里去掉 ``MEDIA:`` 指令 — 附件由 media 模块/网关投递，正文不该出现路径."""
    for seg in segments:
        if seg.type == SegmentType.ANSWER and seg.text:
            seg.text = strip_media_directives(seg.text)


class CronDeliveryOutcomeUnknown(RuntimeError):
    """A Cron card send may have committed without a usable message receipt."""


class StreamingController:
    """流式卡片专用方法 — 由 StreamCardController 继承."""

    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[..., Coroutine[Any, Any, None]]
    _client_for_chat: Callable[[str], Coroutine[Any, Any, FeishuClient]]
    _session_client: Callable[[CardSession], FeishuClient]
    _cleanup: Callable[[str], None]
    _cleanup_session: Callable[[CardSession], None]
    _consume_deferred_background_reviews: Callable[[CardSession], None]
    _discard_deferred_background_reviews: Callable[[CardSession], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]
    _wait_for_card_creation: Callable[[CardSession], Coroutine[Any, Any, bool]]

    def _schedule_flush(self, session: CardSession) -> None:
        if session.state == SessionState.IDLE or session.state.is_terminal:
            return
        if session.state == SessionState.CLARIFY_PAUSED:
            return
        if session.guard.should_skip("_schedule_flush"):
            return
        session.flush.schedule_update(lambda: self._do_flush(session))

    def _on_thinking_segment(self, session: CardSession, text: str) -> bool:
        segment_state = session.segment_state
        if segment_state is None:
            return False
        split = split_reasoning_text(text)
        reasoning = split.get("reasoning_text")
        answer = split.get("answer_text")

        if reasoning and self._cfg.show_reasoning:
            segment_state.on_reasoning_delta(reasoning)
        if answer:
            segment_state.on_answer_delta(answer)
        if not (reasoning and self._cfg.show_reasoning) and not answer:
            return False
        self._schedule_flush(session)
        return True

    @staticmethod
    def _logical_delivery_key(session: CardSession, generation: str, route: str) -> str:
        return f"stream:{session.message_id}:generation:{generation}:route:{route}"

    async def _create_and_attach_card(
        self,
        session: CardSession,
        card: dict[str, Any],
        *,
        generation: str,
        reply_to_message_id: str | None,
        existing_card_id: str = "",
    ) -> tuple[str, str]:
        """Create/attach one card using a restart-stable Feishu idempotency UUID."""
        route = "reply" if reply_to_message_id else "chat"
        logical_key = self._logical_delivery_key(session, generation, route)
        entry = delivery_ledger.begin(logical_key, f"card.{route}")
        session.delivery_key = logical_key
        session.delivery_status = entry.status

        if entry.status is DeliveryStatus.DELIVERED and entry.card_id and entry.message_id:
            metrics.increment("delivery.recovered_delivered")
            return entry.card_id, entry.message_id
        if (
            entry.status is DeliveryStatus.UNKNOWN
            and time.time() - entry.created_at >= IDEMPOTENCY_RETRY_WINDOW_SEC
        ):
            # Reusing the UUID after Lark's one-hour deduplication window can
            # create a second card. Keep the uncertain receipt for inspection.
            session.delivery_status = DeliveryStatus.UNKNOWN
            metrics.increment("delivery.expired_unknown_held")
            return entry.card_id or existing_card_id, ""

        client = self._session_client(session)
        card_id = entry.card_id or existing_card_id
        if not card_id:
            card_id = await client.cardkit_create(card)
            entry = delivery_ledger.card_created(logical_key, card_id)
        # The caller owns the active session card. In split-card flows the old card must remain
        # active until it is sealed; an ambiguous initial attach is retained after this helper returns.
        try:
            if reply_to_message_id:
                message_id = await client.reply_card_by_id(
                    reply_to_message_id,
                    card_id,
                    request_uuid=entry.request_uuid,
                )
            else:
                message_id = await client.send_card_to_chat(
                    chat_id=session.chat_id,
                    card={"type": "card", "data": {"card_id": card_id}},
                    request_uuid=entry.request_uuid,
                )
        except Exception as exc:
            status = classify_delivery_failure(exc)
            code = exc.code if isinstance(exc, FeishuAPIError) else 0
            delivery_ledger.failed(logical_key, status, error_code=code)
            session.delivery_status = status
            metrics.increment(f"delivery.{status.value}")
            metrics.persist()
            if status is DeliveryStatus.UNKNOWN:
                _logger.warning(
                    "Card attach outcome unknown: msg=%s card=%s route=%s",
                    session.message_id[:12],
                    card_id[:12],
                    route,
                )
                return card_id, ""
            raise

        delivery_ledger.delivered(logical_key, card_id=card_id, message_id=message_id)
        session.delivery_status = DeliveryStatus.DELIVERED
        metrics.increment("delivery.delivered")
        return card_id, message_id

    async def _send_uncertain_delivery_notice(self, session: CardSession) -> None:
        if session.delivery_notice_sent:
            return
        logical_key = f"stream:{session.message_id}:uncertain-notice"
        entry = delivery_ledger.begin(logical_key, "notice.unknown")
        if entry.status is DeliveryStatus.DELIVERED:
            session.delivery_notice_sent = True
            return
        if (
            entry.status is DeliveryStatus.UNKNOWN
            and time.time() - entry.created_at >= IDEMPOTENCY_RETRY_WINDOW_SEC
        ):
            metrics.increment("delivery.expired_notice_held")
            return
        notice = (
            "⚠️ 卡片投递确认在网络响应阶段中断; 卡片可能已经送达。"
            "请先刷新当前会话, 再决定是否重试。\n"
            "Delivery confirmation was interrupted; refresh this chat before retrying."
        )
        try:
            message_id = await self._session_client(session).send_text_to_chat(
                session.chat_id,
                notice,
                reply_to_message_id=session.anchor_id or session.message_id,
                request_uuid=entry.request_uuid,
            )
        except Exception as exc:
            status = classify_delivery_failure(exc)
            code = exc.code if isinstance(exc, FeishuAPIError) else 0
            delivery_ledger.failed(logical_key, status, error_code=code)
            metrics.increment("delivery.unknown_notice_failed")
            return
        delivery_ledger.delivered(logical_key, card_id="", message_id=message_id)
        session.delivery_notice_sent = True
        metrics.increment("delivery.unknown_notice_sent")

    async def _send_ledger_unavailable_notice(self, session: CardSession) -> None:
        """Report an uncertain delivery without replaying the answer."""
        if session.delivery_notice_sent:
            return
        request_uuid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hermes-lark-streaming:ledger-unavailable:{session.chat_id}:{session.message_id}",
        ).hex
        try:
            await self._session_client(session).send_text_to_chat(
                session.chat_id,
                "⚠️ 本轮卡片投递记录暂不可读取。回复已暂停以避免重复发送。请检查本地投递账本后重试。",
                reply_to_message_id=session.anchor_id or session.message_id,
                request_uuid=request_uuid,
            )
            session.delivery_notice_sent = True
            metrics.increment("delivery.ledger_unavailable_notice")
        except Exception:
            _logger.exception("delivery ledger unavailable notice failed: msg=%s", session.message_id[:12])

    async def _do_create_card(self, session: CardSession) -> None:
        """Create a loading card with crash-safe, tri-state delivery ownership."""
        if session.state != SessionState.IDLE:
            return
        session.state = SessionState.CREATING
        if session.segment_state is None:
            session.segment_state = SegmentState()

        try:
            await self._ensure_init()
            session.client = await self._client_for_chat(session.chat_id)

            reply_to_message_id = session.anchor_id or session.message_id
            card = build_streaming_card_v2(
                show_tool_use=False,
                show_reasoning=False,
                show_streaming_element=False,
                header_enabled=self._cfg.header_enabled,
                text_size=self._cfg.body_text_size,
                width_mode=self._cfg.width_mode,
            )
            try:
                card_id, card_msg_id = await self._create_and_attach_card(
                    session,
                    card,
                    generation="0",
                    reply_to_message_id=reply_to_message_id,
                )
            except FeishuAPIError as error:
                if error.code != CARDKIT_CONTENT_FAILED:
                    raise
                # A structured 230099 response proves no message was created. Build a new card
                # entity and request UUID, then try the reply path once more.
                try:
                    card_id, card_msg_id = await self._create_and_attach_card(
                        session,
                        card,
                        generation="0-retry",
                        reply_to_message_id=reply_to_message_id,
                    )
                except FeishuAPIError:
                    # The reply API conclusively rejected the card twice. Fall back to a chat send
                    # with its own stable UUID; ambiguous outcomes never enter this branch.
                    retry_entry = delivery_ledger.get(self._logical_delivery_key(session, "0-retry", "reply"))
                    card_id, card_msg_id = await self._create_and_attach_card(
                        session,
                        card,
                        generation="0-standalone",
                        reply_to_message_id=None,
                        existing_card_id=retry_entry.card_id if retry_entry else "",
                    )
            if card_msg_id:
                session.set_card(card_id=card_id, card_msg_id=card_msg_id)
            else:
                session.card_id = card_id
                session.card_msg_id = None
                if session.delivery_status is DeliveryStatus.UNKNOWN:
                    # A long-running agent may not reach card completion for many minutes.
                    # Tell the user about the uncertain attach now, not only at completion.
                    # The notice has its own durable UUID, so the completion path remains
                    # idempotent even if this send also loses its response.
                    try:
                        await self._send_uncertain_delivery_notice(session)
                    except DeliveryLedgerError:
                        _logger.error(
                            "CardKit attach is uncertain and notice ledger is unavailable: msg=%s",
                            session.message_id[:12],
                            exc_info=True,
                        )
                        await self._send_ledger_unavailable_notice(session)
            session.element_count = 1
            session.flush.set_throttle(CARDKIT_MS)

            if session.image_resolver is None:
                session.image_resolver = ImageResolver(
                    client=self._session_client(session),
                    on_image_resolved=lambda: self._schedule_flush(session),
                )

            session.flush.set_card_message_ready(True)
            if session.state == SessionState.CREATING:
                session.state = SessionState.STREAMING
            if session.segment_state and session.segment_state.has_dirty:
                self._schedule_flush(session)
            _logger.info(
                "CardKit card created: msg=%s card_id=%s delivery=%s",
                session.message_id[:12],
                (session.card_id or "")[:12],
                session.delivery_status.value,
            )
        except DeliveryLedgerError:
            _logger.error(
                "CardKit delivery ledger unavailable; holding answer: msg=%s",
                session.message_id[:12],
                exc_info=True,
            )
            session.delivery_evidence_unavailable = True
            session.mark_failed()
        except FeishuAPIError:
            _logger.info("CardKit create failed, yielding to gateway", exc_info=True)
            if hasattr(self, "_mark_text_fallback_needed"):
                self._mark_text_fallback_needed(session)
            session.mark_failed()
        except Exception:
            _logger.exception("_do_create_card failed")
            session.mark_failed()
        finally:
            metrics.persist_throttled()

    async def _do_flush(self, session: CardSession) -> None:
        """幂等 flush：按 segment 顺序处理结构性变更，超阈值时拆卡."""
        if session.state.is_terminal or not session.card_id:
            return
        segment_state = session.segment_state
        if segment_state is None:
            return

        _ = self._session_client(session)
        segments = segment_state.segments
        all_steps = session.tool_use.build_display_steps()

        # ── 步骤 1: batch_update — 按 segment 顺序处理结构性变更 ──
        actions: list[dict[str, Any]] = []
        new_el_ids: set[str] = set()
        new_el_estimates: dict[str, int] = {}
        updated_tool_segs: list[Segment] = []
        new_el_total = 0  # 同一 flush 内新 segment 估计 + dirty segment 增量的累计

        for i, seg in enumerate(segments):
            if i < session.split_index:
                continue

            # show_tool_use=False: 流式态跳过所有 TOOL segment 处理
            # （新建与 dirty 更新两条路径），只保留 reasoning/answer
            if seg.type == SegmentType.TOOL and not self._cfg.show_tool_use:
                if not seg.created:
                    seg.created = True  # 防止 next flush 再次进入 not created 分支
                seg.dirty = False
                continue

            if not seg.created:
                estimated = estimate_segment_elements(seg, all_steps)
                if (
                    seg.type == SegmentType.TOOL
                    and session.element_count + new_el_total + estimated + FOOTER_RESERVE > ELEMENT_THRESHOLD
                    and not session.split_disabled
                ):
                    split_offset = find_tool_split_offset(
                        base_count=session.element_count + new_el_total,
                        seg=seg,
                        all_steps=all_steps,
                    )
                    if split_offset is not None:
                        segment_state.split_tool_segment(i, split_offset)
                        estimated = estimate_segment_elements(seg, all_steps)
                if (
                    session.element_count + new_el_total + estimated + FOOTER_RESERVE > ELEMENT_THRESHOLD
                    and session.element_count + new_el_total > 1
                    and not session.split_disabled
                ):
                    split_ok = await self._do_split_card(
                        session,
                        i,
                        actions,
                        new_el_ids,
                        new_el_estimates,
                        updated_tool_segs,
                    )
                    if not split_ok:
                        return
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0

                if seg.type == SegmentType.TOOL:
                    updated_tool_segs.append(seg)
                new_el_ids.add(seg.el_id)
                new_el_estimates[seg.el_id] = estimated
                new_el_total += estimated
                actions.append(build_add_segment_action(seg, all_steps, text_size=self._cfg.body_text_size))
                if (
                    seg.type == SegmentType.TOOL
                    and i + 1 < len(segments)
                    and segments[i + 1].type == SegmentType.TOOL
                    and segments[i + 1].tool_offset == seg.tool_end_offset
                    and not session.split_disabled
                ):
                    split_ok = await self._do_split_card(
                        session,
                        i + 1,
                        actions,
                        new_el_ids,
                        new_el_estimates,
                        updated_tool_segs,
                    )
                    if not split_ok:
                        return
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0
            elif seg.type == SegmentType.REASONING and seg.elapsed_ms > 0 and not seg.reasoning_finalized:
                _logger.info(
                    "CardKit reasoning finalized: msg=%s el=%s elapsed=%.0fms seq=%d",
                    session.message_id[:12],
                    seg.el_id,
                    seg.elapsed_ms,
                    session.sequence + 1,
                )
                actions.append(build_reasoning_finalized_action(seg))
            elif seg.type == SegmentType.TOOL and seg.dirty:
                if seg.tool_end_offset > 0:
                    start, end = seg.tool_offset, seg.tool_end_offset
                else:
                    start, end = seg.tool_offset, len(all_steps)
                rollover = await self._maybe_rollover_tool_segment(
                    session=session,
                    segment_state=segment_state,
                    index=i,
                    seg=seg,
                    all_steps=all_steps,
                    actions=actions,
                    new_el_ids=new_el_ids,
                    new_el_estimates=new_el_estimates,
                    updated_tool_segs=updated_tool_segs,
                    pending_delta=new_el_total,
                )
                if rollover == "failed":
                    return
                if rollover == "split":
                    actions = []
                    new_el_ids = set()
                    new_el_estimates = {}
                    updated_tool_segs = []
                    new_el_total = 0
                    continue
                estimate = estimate_tool_elements(start, end, all_steps)
                actions.append(build_tool_update_action(element_id=seg.el_id, steps=all_steps[start:end]))
                updated_tool_segs.append(seg)
                new_el_estimates[seg.el_id] = estimate
                new_el_total += estimate - seg.element_estimate

        if actions and not await self._do_batch_update(
            session,
            segments,
            actions,
            new_el_ids,
            new_el_estimates,
            updated_tool_segs,
        ):
            return

        # ── 步骤 2: stream_element 刷脏文本 ──
        # A permanent or prolonged API error otherwise retries every flush
        # (often a few hundred milliseconds apart) for the whole agent turn.
        # Keep dirty text for a later flush/final full-card update, but stop
        # the request storm while the provider is rejecting this stream.
        if time.monotonic() < session.stream_retry_after:
            metrics.increment("cardkit.stream.backoff_skipped")
            return
        streamed_any = False
        for seg in segments[session.split_index :]:
            if not seg.created or not seg.dirty:
                continue
            try:
                if seg.type == SegmentType.REASONING:
                    content = optimize_markdown_style(seg.text) or " "
                    next_sequence = session.sequence + 1
                    _logger.info(
                        "CardKit stream element: msg=%s seq=%d type=reasoning len=%d",
                        session.message_id[:12],
                        next_sequence,
                        len(content),
                    )
                    await self._session_client(session).cardkit_stream_element(
                        session.card_id,
                        seg.text_el_id,
                        content,
                        sequence=next_sequence,
                    )
                    session.sequence = next_sequence
                    streamed_any = True
                    seg.dirty = False
                elif seg.type == SegmentType.ANSWER:
                    content = strip_media_directives(seg.text)
                    if session.image_resolver:
                        content = session.image_resolver.resolve_images(content)
                    content = _downgrade_tables(optimize_markdown_style(content)) or " "
                    next_sequence = session.sequence + 1
                    _logger.info(
                        "CardKit stream element: msg=%s seq=%d type=answer len=%d",
                        session.message_id[:12],
                        next_sequence,
                        len(content),
                    )
                    await self._session_client(session).cardkit_stream_element(
                        session.card_id,
                        seg.el_id,
                        content,
                        sequence=next_sequence,
                    )
                    session.sequence = next_sequence
                    streamed_any = True
                    seg.dirty = False
            except FeishuAPIError as e:
                missing_el_id = extract_missing_element_id(e)
                streamed_el_id = seg.text_el_id if seg.type == SegmentType.REASONING else seg.el_id
                if missing_el_id in (streamed_el_id, _LOADING_ELEMENT_ID):
                    await self._reseed_card_after_missing_element(
                        session, segments, missing_el_id, operation="stream_element",
                    )
                    metrics.persist_throttled(min_interval_sec=2.0)
                    return
                self._defer_stream_retry(session, code=e.code)
                return
            except Exception as e:
                self._defer_stream_retry(session, code=0)
                _logger.debug("CardKit stream element exception: %s el=%s", e, seg.el_id, exc_info=True)
                return
        if streamed_any:
            session.stream_failure_streak = 0
            session.stream_retry_after = 0.0
            metrics.persist_throttled()

    @staticmethod
    def _defer_stream_retry(session: CardSession, *, code: int) -> None:
        session.stream_failure_streak = min(16, session.stream_failure_streak + 1)
        delay = min(30.0, 0.5 * (2 ** min(session.stream_failure_streak - 1, 6)))
        session.stream_retry_after = time.monotonic() + delay
        metrics.increment("cardkit.stream.retry_deferred")
        metrics.persist_throttled(min_interval_sec=2.0)
        if session.stream_failure_streak in (1, 4, 8):
            _logger.warning(
                "CardKit stream retry deferred: code=%s streak=%d delay=%.1fs",
                code, session.stream_failure_streak, delay,
            )

    async def _reseed_card_after_missing_element(
        self,
        session: CardSession,
        segments: list[Segment],
        missing_el_id: str,
        *,
        operation: str,
    ) -> None:
        """Rebuild a server-pruned streaming card once, then replay local segments."""
        assert session.card_id is not None
        if session.anchor_recovery_attempts >= 1:
            _logger.error(
                "CardKit element still missing after bounded recovery: card=%s element=%s operation=%s",
                session.card_id[:12], missing_el_id, operation,
            )
            session.mark_failed()
            if hasattr(self, "_mark_text_fallback_needed"):
                self._mark_text_fallback_needed(session)
            return

        session.anchor_recovery_attempts += 1
        try:
            recovery_sequence = session.sequence + 1
            recovery_card = build_streaming_card_v2(
                show_tool_use=False,
                show_reasoning=False,
                show_streaming_element=False,
                header_enabled=self._cfg.header_enabled,
                text_size=self._cfg.body_text_size,
                width_mode=self._cfg.width_mode,
            )
            await self._session_client(session).cardkit_update(
                session.card_id, recovery_card, sequence=recovery_sequence,
            )
            session.sequence = recovery_sequence
            session.stream_failure_streak = 0
            session.stream_retry_after = 0.0
            session.element_count = 1
            for seg in segments[session.split_index :]:
                seg.created = False
                seg.dirty = True
                seg.element_estimate = 0
                seg.reasoning_finalized = False
            session.flush.request_reflush()
            _logger.info(
                "CardKit restored missing streaming element: card=%s element=%s operation=%s seq=%d",
                session.card_id[:12], missing_el_id, operation, recovery_sequence,
            )
        except Exception:
            _logger.exception(
                "CardKit element recovery failed: card=%s element=%s operation=%s",
                session.card_id[:12], missing_el_id, operation,
            )
            session.mark_failed()
            if hasattr(self, "_mark_text_fallback_needed"):
                self._mark_text_fallback_needed(session)

    async def _do_batch_update(
        self,
        session: CardSession,
        segments: list[Segment],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """执行 batch_update 并处理快照/标记。返回 False 表示失败."""
        _ = self._session_client(session)
        assert session.card_id is not None
        next_sequence = session.sequence + 1
        _logger.info(
            "CardKit batch update: msg=%s card=%s seq=%d actions=%d split=%d elements=%d",
            session.message_id[:12],
            session.card_id[:12],
            next_sequence,
            len(actions),
            session.split_index,
            session.element_count,
        )
        pre_flush_reasoning_elapsed = {
            seg.el_id: seg.elapsed_ms for seg in segments if seg.type == SegmentType.REASONING
        }
        pre_flush_tool_offsets = {seg.el_id: seg.tool_end_offset for seg in updated_tool_segs}
        pre_flush_tool_steps = session.tool_use.build_display_steps()
        pre_flush_tool_slices = {
            seg.el_id: pre_flush_tool_steps[seg.tool_offset : tool_segment_end(seg, pre_flush_tool_steps)]
            for seg in updated_tool_segs
        }
        metrics_interval = 10.0
        try:
            await self._session_client(session).cardkit_batch_update(
                session.card_id,
                actions,
                sequence=next_sequence,
            )
            session.sequence = next_sequence
            for seg in segments:
                if seg.el_id in new_el_ids:
                    seg.created = True
                    estimate = new_el_estimates.get(seg.el_id, 0)
                    seg.element_estimate = estimate
                    session.element_count += estimate
            for seg in segments:
                if seg.type == SegmentType.REASONING and pre_flush_reasoning_elapsed.get(seg.el_id, 0) > 0:
                    seg.reasoning_finalized = True
            if new_el_ids:
                for seg in segments:
                    if seg.el_id in new_el_ids or not seg.created:
                        continue
                    if seg.type in (SegmentType.REASONING, SegmentType.ANSWER) and seg.text:
                        seg.dirty = True
            current_tool_steps = session.tool_use.build_display_steps()
            for seg in updated_tool_segs:
                offset_ok = pre_flush_tool_offsets.get(seg.el_id, -1) == seg.tool_end_offset
                current_tool_slice = current_tool_steps[seg.tool_offset : tool_segment_end(seg, current_tool_steps)]
                tool_slice_ok = pre_flush_tool_slices.get(seg.el_id) == current_tool_slice
                if seg.el_id in new_el_estimates:
                    estimate = new_el_estimates[seg.el_id]
                    session.element_count += estimate - seg.element_estimate
                    seg.element_estimate = estimate
                if seg.created and offset_ok and tool_slice_ok:
                    seg.dirty = False
        except FeishuAPIError as e:
            metrics_interval = 2.0
            session.flush.record_failure(rate_limited=e.code == CARDKIT_RATE_LIMITED)
            missing_el_id = extract_missing_element_id(e)
            action_summary = summarize_actions(actions)
            _logger.warning(
                "CardKit batch update failed: %s card=%s seq=%d split=%d elements=%d "
                "missing=%s missing_state=%s new=[%s] tool_updates=[%s] %s",
                e,
                session.card_id[:12],
                next_sequence,
                session.split_index,
                session.element_count,
                missing_el_id or "-",
                segment_state_for_log(segments, missing_el_id),
                compact_ids(new_el_ids),
                compact_ids([seg.el_id for seg in updated_tool_segs]),
                action_summary,
                exc_info=not bool(missing_el_id),
            )
            # 缺失元素（300313）时回滚 stale segment：本地 created=True 但卡片上不存在，
            # 下一轮 flush 会用 add_elements 重建该元素，避免反复 partial_update 死循环。
            if missing_el_id == _LOADING_ELEMENT_ID:
                await self._reseed_card_after_missing_element(
                    session, segments, missing_el_id, operation="batch_update",
                )
            elif missing_el_id and any(
                seg.type == SegmentType.REASONING
                and seg.created
                and seg.text_el_id == missing_el_id
                for seg in segments[session.split_index :]
            ):
                # The panel still exists locally but CardKit pruned its nested
                # text element. Re-adding only the panel ID would collide with
                # the existing panel; reseed the whole card and replay instead.
                await self._reseed_card_after_missing_element(
                    session, segments, missing_el_id, operation="batch_update",
                )
            elif missing_el_id:
                for seg in segments[session.split_index :]:
                    if seg.el_id == missing_el_id and seg.created:
                        seg.created = False
                        seg.dirty = True
                        # 同步扣减元素计数：该 segment 当初 add 成功时已累加进 element_count，
                        # 回滚为未创建后下一轮会重新 add 并再次累加，这里先扣除避免重复计数。
                        session.element_count -= seg.element_estimate
                        if session.element_count < 0:
                            session.element_count = 0
                        _logger.info(
                            "CardKit recovered stale segment %s -> re-adding immediately",
                            seg.el_id,
                        )
                        session.flush.request_reflush()
                        break
            self._handle_flush_error(e)
            return False
        except Exception:
            metrics_interval = 2.0
            raise
        finally:
            metrics.persist_throttled(min_interval_sec=metrics_interval)
        return True

    async def _maybe_rollover_tool_segment(
        self,
        *,
        session: CardSession,
        segment_state: SegmentState,
        index: int,
        seg: Segment,
        all_steps: list[ToolDisplayStep],
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
        pending_delta: int = 0,
    ) -> str | None:
        """按 tool step 边界拆分过大的 dirty tool segment."""
        start = seg.tool_offset
        end = tool_segment_end(seg, all_steps)
        estimate = estimate_tool_elements(start, end, all_steps)
        delta = estimate - seg.element_estimate
        if (
            delta <= 0
            or session.element_count + pending_delta + delta + FOOTER_RESERVE <= ELEMENT_THRESHOLD
            or session.split_disabled
        ):
            return None

        split_offset = find_tool_split_offset(
            base_count=session.element_count + pending_delta - seg.element_estimate,
            seg=seg,
            all_steps=all_steps,
        )
        if split_offset is None:
            return None

        old_estimate = estimate_tool_elements(seg.tool_offset, split_offset, all_steps)
        actions.append(
            build_tool_update_action(
                element_id=seg.el_id,
                steps=all_steps[seg.tool_offset : split_offset],
            )
        )
        updated_tool_segs.append(seg)
        new_el_estimates[seg.el_id] = old_estimate
        segment_state.split_tool_segment(index, split_offset)
        split_ok = await self._do_split_card(
            session,
            index + 1,
            actions,
            new_el_ids,
            new_el_estimates,
            updated_tool_segs,
        )
        if not split_ok:
            return "failed"
        return "split"

    async def _seal_current_card(
        self,
        session: CardSession,
        seal_segments: list[Segment],
        *,
        card_id: str | None = None,
        sequence: int | None = None,
    ) -> None:
        """封印指定卡（默认 session.card_id）：close_streaming + 全量重建。失败仅记录日志。

        card_id 用于 session 已切到新卡、仍需封印旧卡的场景（clarify 切卡）；
        sequence 传入旧卡续用的递增序列（CardKit 要求单调递增，不能用新卡的计数）。
        """
        _ = self._session_client(session)
        old_card_id = card_id or session.card_id
        if not old_card_id:
            return
        _strip_answer_media_directives(seal_segments)
        if session.image_resolver:
            await _resolve_answer_images(
                seal_segments,
                session.image_resolver,
                log_prefix="CardKit seal",
            )
        all_steps = session.tool_use.build_display_steps()
        seal_card = build_complete_card(
            segments=seal_segments,
            all_tool_steps=all_steps,
            footer_fields=[],
            footer_show_label=False,
            footer_enabled=False,
            panel_expanded=self._cfg.panel_expanded,
            header_enabled=False,
            body_text_size=self._cfg.body_text_size,
            show_tool_use=self._cfg.show_tool_use,
            width_mode=self._cfg.width_mode,
        )
        try:
            seq = session.sequence if sequence is None else sequence
            seq += 1
            await self._session_client(session).cardkit_close_streaming(old_card_id, sequence=seq)
            seq += 1
            await self._session_client(session).cardkit_update(old_card_id, seal_card, sequence=seq)
        except Exception:
            _logger.warning(
                "CardKit seal failed for old card %s, continuing",
                old_card_id[:12],
                exc_info=True,
            )

    async def _create_streaming_card(self, session: CardSession) -> tuple[str, str] | None:
        """Create and attach the next split card with restart-stable idempotency."""
        _ = self._session_client(session)
        generation = session.delivery_generation + 1
        prior_delivery_key = session.delivery_key
        prior_delivery_status = session.delivery_status
        try:
            card = build_streaming_card_v2(
                show_tool_use=False,
                show_reasoning=False,
                show_streaming_element=False,
                header_enabled=self._cfg.header_enabled,
                text_size=self._cfg.body_text_size,
                width_mode=self._cfg.width_mode,
            )
            new_card_id, new_msg_id = await self._create_and_attach_card(
                session,
                card,
                generation=str(generation),
                reply_to_message_id=session.anchor_id or session.message_id,
            )
            if not new_msg_id:
                # An uncertain attach is not a usable replacement. Keep the
                # previously delivered card open instead of sealing it and
                # switching the session to an invisible CardKit entity.
                session.delivery_key = prior_delivery_key
                session.delivery_status = prior_delivery_status
                _logger.warning(
                    "CardKit replacement attach unconfirmed; retaining old card: msg=%s",
                    session.message_id[:12],
                )
                return None
            session.delivery_generation = generation
        except Exception:
            session.delivery_key = prior_delivery_key
            session.delivery_status = prior_delivery_status
            _logger.warning(
                "CardKit create streaming card failed for msg=%s",
                session.message_id[:12],
                exc_info=True,
            )
            return None
        return new_card_id, new_msg_id

    async def _do_split_card(
        self,
        session: CardSession,
        split_idx: int,
        actions: list[dict[str, Any]],
        new_el_ids: set[str],
        new_el_estimates: dict[str, int],
        updated_tool_segs: list[Segment],
    ) -> bool:
        """拆卡：先 flush pending actions，封旧卡，创建新卡。返回 False 表示失败需中断 flush."""
        _ = self._session_client(session)
        old_card_id = session.card_id
        assert old_card_id is not None
        segment_state = session.segment_state
        assert segment_state is not None
        segments = segment_state.segments
        seal_start_idx = session.split_index

        if actions and not await self._do_batch_update(
            session,
            segments,
            actions,
            new_el_ids,
            new_el_estimates,
            updated_tool_segs,
        ):
            return False

        seal_segments = [s for s in segments[seal_start_idx:split_idx] if s.created]

        # create → seal → set_card（顺序与原实现一致）
        new_card = await self._create_streaming_card(session)
        if new_card is None:
            session.split_disabled = True  # 降级：继续写当前卡
            return True

        await self._seal_current_card(session, seal_segments)

        new_card_id, new_msg_id = new_card
        session.set_card(card_id=new_card_id, card_msg_id=new_msg_id)
        session.element_count = 1  # loading element
        session.sequence = 1  # 新卡从 1 重新计数
        session.split_disabled = False
        session.split_index = split_idx
        for seg in segments[split_idx:]:
            seg.created = False
        _logger.info(
            "CardKit split: msg=%s old_card=%s sealed=%d split_idx=%d new_card=%s",
            session.message_id[:12],
            old_card_id[:12],
            len(seal_segments),
            split_idx,
            new_card_id[:12],
        )
        return True

    async def _do_clarify_split(self, session: CardSession) -> bool:
        """clarify 工具结束后切卡：建新卡 + 封旧卡。

        返回 False 表示未切卡（无卡可封，或建卡失败降级继续写旧卡）。
        """
        await self._wait_for_card_creation(session)
        if not session.has_card or session.state == SessionState.FAILED:
            _logger.info(
                "clarify_split: no card to seal, msg=%s state=%s",
                session.message_id[:12],
                session.state,
            )
            return False

        # 先禁拆卡再等 flush：否则进行中的 flush 可能先拆卡，随后被本流程封印成空白卡。
        session.split_disabled = True
        try:
            await session.flush.wait_for_flush()
            try:
                await session.flush.flush_now(lambda: self._do_flush(session))
            except Exception:
                _logger.debug("clarify_split: final flush failed", exc_info=True)

            # 先建新卡后封旧卡（与拆卡一致）：建卡失败时旧卡未 close，可降级继续流式。
            new_card = await self._create_streaming_card(session)
            if new_card is None:
                _logger.warning(
                    "clarify_split: create new card failed, continuing on current card, msg=%s",
                    session.message_id[:12],
                )
                return False

            # 先切到新卡再封旧卡：任意时刻被取消（超时兜底）时，session 指向的都是
            # 未 close 的卡，后续 flush 不会写到已关闭的旧卡。
            # seal 必须显式传旧 card_id + 旧 sequence（session 已切新卡，
            # 且 CardKit sequence 要求单调递增，不能用新卡的计数）。
            old_card_id = session.card_id
            old_seq = session.sequence
            new_card_id, new_msg_id = new_card
            session.set_card(card_id=new_card_id, card_msg_id=new_msg_id)
            session.sequence = 1  # 新卡从 1 重新计数

            await self._seal_current_card(
                session,
                session.active_segments(),
                card_id=old_card_id,
                sequence=old_seq,
            )

            # 重置内容，新卡承载 clarify 后的输出
            session.segment_state = SegmentState()
            session.split_index = 0
            session.element_count = 1  # loading element（与拆卡一致）
            session.tool_use = ToolUseTracker()

            _logger.info(
                "clarify_split: sealed old + new card msg=%s card=%s",
                session.message_id[:12],
                new_card_id[:12],
            )
            return True
        finally:
            session.split_disabled = False  # 取消/异常/失败均恢复拆卡能力

    def _handle_flush_error(self, e: FeishuAPIError) -> None:
        if e.code == CARDKIT_RATE_LIMITED:
            return
        if e.code == CARDKIT_STREAMING_CLOSED:
            return
        if e.code == CARDKIT_CONTENT_FAILED:
            sub_code = e.extract_sub_code()
            if sub_code == CARDKIT_ELEMENT_LIMIT:
                _logger.warning("CardKit card element limit exceeded")

    async def _do_complete_card(self, session: CardSession) -> bool:
        """完成流式卡片：close streaming + 全量重建卡片（保持 segments 顺序）."""
        card_sent = False
        try:
            card_sent = await self._do_complete_card_inner(session)
            return card_sent
        finally:
            if card_sent:
                self._discard_deferred_background_reviews(session)
            else:
                self._flush_deferred_background_reviews(session)
            self._cleanup_session(session)

    async def _do_complete_card_inner(self, session: CardSession) -> bool:
        if session.guard.should_skip("_do_complete_card"):
            return False

        await session.flush.wait_for_flush()
        self._consume_deferred_background_reviews(session)
        session.flush.mark_completed()

        segment_state = session.segment_state
        is_error = session.state == SessionState.FAILED
        is_aborted = session.state == SessionState.ABORTED
        all_tool_steps = session.tool_use.build_display_steps()

        if segment_state is not None:
            segment_state.finalize_segments(len(all_tool_steps))

        active_segments = session.active_segments()
        active_segments, all_tool_steps, compacted = compact_terminal_segments(
            active_segments,
            all_tool_steps,
            compact_after=self._cfg.history_compact_after,
            keep_recent=self._cfg.history_keep_recent,
        )
        if compacted["tool_steps"] or compacted["reasoning_rounds"]:
            metrics.increment("history.tool_steps_compacted", compacted["tool_steps"])
            metrics.increment("history.reasoning_rounds_compacted", compacted["reasoning_rounds"])

        _strip_answer_media_directives(active_segments)
        if session.image_resolver:
            await _resolve_answer_images(
                active_segments,
                session.image_resolver,
                log_prefix="CardKit",
            )

        card = build_complete_card(
            segments=active_segments,
            all_tool_steps=all_tool_steps,
            footer_data=session.footer,
            is_error=is_error,
            is_aborted=is_aborted,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
            footer_enabled=self._cfg.footer_enabled,
            footer_text_size=self._cfg.footer_text_size,
            panel_expanded=self._cfg.panel_expanded,
            header_enabled=self._cfg.header_enabled,
            body_text_size=self._cfg.body_text_size,
            show_tool_use=self._cfg.show_tool_use,
            width_mode=self._cfg.width_mode,
        )

        streaming_closed = False
        for attempt in range(3):
            try:
                _ = self._session_client(session)
                if session.card_id:
                    if not streaming_closed:
                        next_sequence = session.sequence + 1
                        await self._session_client(session).cardkit_close_streaming(
                            session.card_id,
                            sequence=next_sequence,
                        )
                        session.sequence = next_sequence
                        streaming_closed = True
                    next_sequence = session.sequence + 1
                    await self._session_client(session).cardkit_update(
                        session.card_id,
                        card,
                        sequence=next_sequence,
                    )
                    session.sequence = next_sequence
                session.state = SessionState.COMPLETED
                metrics.increment("card.completed")
                if session.delivery_status is DeliveryStatus.UNKNOWN:
                    try:
                        await self._send_uncertain_delivery_notice(session)
                    except DeliveryLedgerError:
                        _logger.error(
                            "CardKit final update succeeded but delivery ledger is unavailable: msg=%s",
                            session.message_id[:12],
                            exc_info=True,
                        )
                        await self._send_ledger_unavailable_notice(session)
                metrics.persist()
                return True
            except FeishuAPIError as e:
                _logger.warning(
                    "CardKit complete attempt %d failed: code=%s msg=%s card_id=%s seq=%d",
                    attempt,
                    e.code,
                    e,
                    session.card_id,
                    session.sequence,
                    exc_info=True,
                )
                if session.guard.terminate("_do_complete_card", e):
                    return False
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue
            except Exception as e:
                _logger.warning(
                    "CardKit complete attempt %d failed: %s: %s card_id=%s seq=%d",
                    attempt,
                    type(e).__name__,
                    e,
                    session.card_id,
                    session.sequence,
                    exc_info=True,
                )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue

        _logger.error(
            "CardKit complete failed after 3 attempts: card_id=%s seq=%d",
            session.card_id,
            session.sequence,
        )
        session.mark_failed()
        metrics.increment("card.completion_failed")
        metrics.persist()
        return False

    async def _do_cron_deliver(
        self,
        chat_id: str,
        content: str,
        *,
        task_name: str = "",
        run_time: str = "",
        job_id: str = "",
        media_files: object = None,
    ) -> str:
        client = await self._client_for_chat(chat_id)
        # Hermes 的 cron 投递在调用注入钩子之前就把 MEDIA 标签剥进 media_files 了
        # (cron/scheduler_delivery.py)，钩子返回 True 会跳过它自己的附件投递 —— 所以这里自己补。
        paths = hook_media_paths(media_files)
        card = build_cron_card(
            strip_media_directives(content) if paths else content,
            task_name=task_name,
            run_time=run_time,
        )
        # A scheduled occurrence has a stable job ID and due time. Hash this
        # logical key in the shared ledger and reuse its Feishu request UUID.
        # A prior unknown result is held for receipt inspection, not resent.
        logical_key = f"cron:{job_id}:{run_time}:{chat_id}" if job_id and run_time else ""
        request_uuid: str | None = None
        if logical_key:
            entry, should_send = delivery_ledger.claim_send(logical_key, "cron.card")
            if not should_send:
                if entry.status is DeliveryStatus.DELIVERED and entry.message_id:
                    return entry.message_id
                raise CronDeliveryOutcomeUnknown("prior cron card outcome remains unknown")
            request_uuid = entry.request_uuid
        try:
            message_id = await client.send_card_to_chat(
                chat_id, card, request_uuid=request_uuid
            )
        except FeishuAPIError as exc:
            if logical_key:
                delivery_ledger.failed(
                    logical_key, classify_delivery_failure(exc), error_code=exc.code
                )
            raise  # Structured server rejection is classified by the caller.
        except Exception as exc:
            raise CronDeliveryOutcomeUnknown("Feishu cron card send outcome unknown") from exc
        if not message_id:
            raise CronDeliveryOutcomeUnknown("Feishu cron card send returned no message_id")
        if logical_key:
            delivery_ledger.delivered(logical_key, card_id="", message_id=str(message_id))
        await self._deliver_card_media(chat_id, paths, reply_to_message_id=message_id, client=client)
        return str(message_id)

    async def _deliver_card_media(
        self,
        chat_id: str,
        paths: list[str],
        *,
        reply_to_message_id: str | None = None,
        client: FeishuClient | None = None,
    ) -> int:
        """静态卡片发出后补投附件（best-effort，绝不影响卡片投递结果）."""
        if not paths:
            return 0
        client = client or await self._client_for_chat(chat_id)
        try:
            sent = await deliver_media_files(client, chat_id, paths, reply_to_message_id=reply_to_message_id)
            _logger.info(
                "static card media delivery: chat=%s files=%d sent=%d",
                chat_id[:12],
                len(paths),
                sent,
            )
            return sent
        except Exception:
            _logger.warning("static card media delivery failed: chat=%s", chat_id[:12], exc_info=True)
            return 0

    async def _do_background_deliver(
        self,
        chat_id: str,
        preview: str,
        content: str,
        *,
        reply_to_message_id: str | None = None,
    ) -> None:
        client = await self._client_for_chat(chat_id)
        card = build_background_card(preview, content)
        await client.send_card_to_chat(
            chat_id,
            card,
            reply_to_message_id=reply_to_message_id,
        )
