"""Opt-in Feishu CardKit end-to-end smoke test."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .card_limits import inspect_card
from .cardkit.builder import build_complete_card, build_streaming_card_v2
from .config import Config
from .feishu import FeishuClient, FeishuClientConfig
from .streaming.segments import Segment, SegmentType


def dry_run() -> dict[str, Any]:
    card = build_streaming_card_v2(show_streaming_element=True, width_mode="compact")
    report = inspect_card(card)
    return {"ok": report.safe, "mode": "dry-run", "inspection": report.__dict__}


async def live_run(chat_id: str) -> dict[str, Any]:
    """Create → attach → stream → close → final update in an explicit test chat."""
    cfg = Config()
    app_id = cfg.env_app_id or cfg.feishu_app_id
    app_secret = cfg.env_app_secret or cfg.feishu_app_secret
    if not app_id or not app_secret:
        raise RuntimeError("Feishu credentials are not configured")
    client = FeishuClient(FeishuClientConfig(app_id=app_id, app_secret=app_secret, base_url=cfg.feishu_base_url))
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


def run(*, execute: bool, chat_id: str = "") -> int:
    if not execute:
        print(json.dumps(dry_run(), ensure_ascii=False, indent=2))
        return 0
    if not chat_id:
        print("--chat-id is required with --execute")
        return 2
    print(json.dumps(asyncio.run(live_run(chat_id)), ensure_ascii=False, indent=2))
    return 0
