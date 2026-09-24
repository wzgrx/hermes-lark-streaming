"""Opt-in Feishu CardKit end-to-end smoke test."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .card_limits import inspect_card
from .cardkit.builder import build_complete_card, build_streaming_card_v2
from .config import Config
from .feishu import (
    CARDKIT_ELEMENT_NOT_FOUND,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    FeishuClient,
    FeishuClientConfig,
)
from .streaming.segments import Segment, SegmentType


def dry_run() -> dict[str, Any]:
    card = build_streaming_card_v2(show_streaming_element=True, width_mode="compact")
    report = inspect_card(card)
    return {"ok": report.safe, "mode": "dry-run", "inspection": report.__dict__}


def _configured_client() -> FeishuClient:
    cfg = Config()
    app_id = cfg.env_app_id or cfg.feishu_app_id
    app_secret = cfg.env_app_secret or cfg.feishu_app_secret
    if not app_id or not app_secret:
        raise RuntimeError("Feishu credentials are not configured")
    return FeishuClient(FeishuClientConfig(app_id=app_id, app_secret=app_secret, base_url=cfg.feishu_base_url))


async def live_run(chat_id: str) -> dict[str, Any]:
    """Create → attach → stream → close → final update in an explicit test chat."""
    client = _configured_client()
    started = time.monotonic()
    card = build_streaming_card_v2(show_streaming_element=True, width_mode="compact")
    card_id = await client.cardkit_create(card)
    message_id = await client.send_card_to_chat(chat_id, {"type": "card", "data": {"card_id": card_id}})
    await client.cardkit_stream_element(card_id, "streaming_content", "E2E: streaming ✓", sequence=2)
    await client.cardkit_close_streaming(card_id, sequence=3)
    segment = Segment(SegmentType.ANSWER, "answer_e2e")
    segment.text = "E2E: completed ✓"
    final = build_complete_card(segments=[segment], all_tool_steps=[], width_mode="compact")
    await client.cardkit_update(card_id, final, sequence=4)
    return {
        "ok": True,
        "mode": "live",
        "card_id_prefix": card_id[:8],
        "message_id_prefix": message_id[:8],
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
    }


async def live_entity_probe() -> dict[str, Any]:
    """Check whether CardKit accepts the same UUID after a rejected 300313.

    Creates one CardKit entity but never attaches it to a chat. The second
    stream request has exactly the same card, element, sequence, content, and
    UUID as the first; adding the missing element in between changes only the
    server-side element tree.
    """
    client = _configured_client()
    card_id = await client.cardkit_create(build_streaming_card_v2(
        show_tool_use=False, show_reasoning=False, show_streaming_element=False,
    ))
    result: dict[str, Any] = {"ok": False, "mode": "entity-probe", "attached_to_chat": False}
    try:
        try:
            await client.cardkit_stream_element(card_id, "probe_text", "probe", sequence=5)
        except FeishuAPIError as exc:
            result["initial_error_code"] = exc.code
        else:
            result["initial_error_code"] = 0
        if result["initial_error_code"] != CARDKIT_ELEMENT_NOT_FOUND:
            return result

        try:
            await client.cardkit_batch_update(card_id, [{
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": "loading_icon",
                    "elements": [{"tag": "markdown", "element_id": "probe_text", "content": "ready"}],
                },
            }], sequence=2)
        except FeishuAPIError as exc:
            result["add_error_code"] = exc.code
            return result
        try:
            await client.cardkit_stream_element(card_id, "probe_text", "probe", sequence=5)
        except FeishuAPIError as exc:
            result["retry_error_code"] = exc.code
            return result
        result["ok"] = True
        result["same_uuid_after_add"] = "accepted"
        return result
    finally:
        try:
            await client.cardkit_close_streaming(card_id, sequence=6)
            result["entity_closed"] = True
        except FeishuAPIError as exc:
            result["entity_closed"] = False
            result["ok"] = False
            result["close_error_code"] = exc.code
        except Exception as exc:
            result["entity_closed"] = False
            result["ok"] = False
            result["close_error_type"] = type(exc).__name__


async def live_closed_stream_probe() -> dict[str, Any]:
    """Observe post-close behavior and verify an unattached final update."""
    client = _configured_client()
    card_id = await client.cardkit_create(build_streaming_card_v2(
        show_tool_use=False, show_reasoning=False, show_streaming_element=True,
    ))
    result: dict[str, Any] = {
        "ok": False,
        "mode": "closed-stream-probe",
        "attached_to_chat": False,
    }
    sequence = 1
    closed = False
    try:
        await client.cardkit_stream_element(
            card_id, "streaming_content", "probe before close", sequence=sequence + 1,
        )
        sequence += 1
        await client.cardkit_close_streaming(card_id, sequence=sequence + 1)
        sequence += 1
        closed = True
        result["entity_closed"] = True

        try:
            await client.cardkit_stream_element(
                card_id, "streaming_content", "probe after close", sequence=sequence + 1,
            )
        except FeishuAPIError as exc:
            result["post_close_stream_error_code"] = exc.code
        else:
            sequence += 1
            result["post_close_stream_error_code"] = 0

        segment = Segment(SegmentType.ANSWER, "closed_probe_answer")
        segment.text = "Closed-stream final update probe"
        final = build_complete_card(segments=[segment], all_tool_steps=[])
        try:
            await client.cardkit_update(card_id, final, sequence=sequence + 1)
        except FeishuAPIError as exc:
            result["final_update_error_code"] = exc.code
        else:
            result["final_update_accepted"] = True
            result["ok"] = (
                result["post_close_stream_error_code"] in (0, CARDKIT_STREAMING_CLOSED)
            )
        return result
    except FeishuAPIError as exc:
        result["setup_error_code"] = exc.code
        return result
    finally:
        if not closed:
            try:
                await client.cardkit_close_streaming(card_id, sequence=sequence + 1)
                result["entity_closed"] = True
            except FeishuAPIError as exc:
                result["entity_closed"] = False
                result["cleanup_error_code"] = exc.code
            except Exception as exc:
                result["entity_closed"] = False
                result["cleanup_error_type"] = type(exc).__name__


def run(*, execute: bool, chat_id: str = "", entity_only: bool = False,
        closed_stream_probe: bool = False) -> int:
    if not execute:
        print(json.dumps(dry_run(), ensure_ascii=False, indent=2))
        return 0
    if closed_stream_probe:
        try:
            report = asyncio.run(live_closed_stream_probe())
        except FeishuAPIError as exc:
            report = {
                "ok": False, "mode": "closed-stream-probe",
                "attached_to_chat": False, "error_code": exc.code,
            }
        except Exception as exc:
            report = {
                "ok": False, "mode": "closed-stream-probe",
                "attached_to_chat": False, "error_type": type(exc).__name__,
            }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] and report.get("entity_closed") else 1
    if entity_only:
        try:
            report = asyncio.run(live_entity_probe())
        except FeishuAPIError as exc:
            report = {"ok": False, "mode": "entity-probe", "attached_to_chat": False, "error_code": exc.code}
        except Exception as exc:
            report = {"ok": False, "mode": "entity-probe", "attached_to_chat": False, "error_type": type(exc).__name__}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] and report.get("entity_closed") else 1
    if not chat_id:
        print("--chat-id is required with --execute")
        return 2
    print(json.dumps(asyncio.run(live_run(chat_id)), ensure_ascii=False, indent=2))
    return 0
