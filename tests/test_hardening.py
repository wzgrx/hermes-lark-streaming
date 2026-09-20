from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_lark_streaming.card_limits import MAX_JSON_BYTES, compact_card, inspect_card
from hermes_lark_streaming.e2e import dry_run
from hermes_lark_streaming.history import compact_terminal_segments, compact_tool_steps
from hermes_lark_streaming.metrics import MetricsStore
from hermes_lark_streaming.native_hooks import capability, try_register
from hermes_lark_streaming.routing import BotRegistry
from hermes_lark_streaming.security import ReplayGuard, sign_callback
from hermes_lark_streaming.sidecar import SidecarDispatcher
from hermes_lark_streaming.streaming.flush import FlushController
from hermes_lark_streaming.streaming.segments import Segment, SegmentType
from hermes_lark_streaming.streaming.tooluse import ToolDisplayStep


def _step(index: int, status: str = "success") -> ToolDisplayStep:
    return {
        "name": f"tool_{index}",
        "title": f"Tool {index}",
        "status": status,
        "detail": "detail",
        "output": "",
        "error": "boom" if status == "error" else "",
        "icon": "setting_outlined",
        "elapsed_ms": 10,
        "result_block": None,
        "error_block": None,
    }


def test_metrics_snapshot_and_atomic_persist(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "state" / "metrics.json")
    store.increment("card.completed", 2)
    store.observe("api.update", 20)
    store.observe("api.update", 40)
    path = store.persist()
    payload = json.loads(path.read_text())
    assert payload["counters"]["card.completed"] == 2
    assert payload["latency"]["api.update"]["avg_ms"] == 30
    assert not path.with_suffix(".json.tmp").exists()


def test_callback_proof_rejects_replay_and_tampering() -> None:
    body = b'{"event":"done"}'
    proof = sign_callback("secret", body, domain="test", timestamp=100, nonce="nonce")
    guard = ReplayGuard(ttl_sec=30)
    assert guard.verify(proof, body, secret="secret", domain="test", now=100).nonce == "nonce"
    with pytest.raises(ValueError, match="replay"):
        guard.verify(proof, body, secret="secret", domain="test", now=100)
    with pytest.raises(ValueError):
        ReplayGuard(ttl_sec=30).verify(proof, b"changed", secret="secret", domain="test", now=100)


def test_callback_proof_rejects_expired() -> None:
    proof = sign_callback("secret", b"x", domain="test", timestamp=100, nonce="n")
    with pytest.raises(ValueError, match="expired"):
        ReplayGuard(ttl_sec=5).verify(proof, b"x", secret="secret", domain="test", now=106)


def test_exact_chat_multi_bot_routing(monkeypatch) -> None:
    monkeypatch.setenv("BOT_A_ID", "a")
    monkeypatch.setenv("BOT_A_SECRET", "sa")
    monkeypatch.setenv("BOT_B_ID", "b")
    monkeypatch.setenv("BOT_B_SECRET", "sb")
    registry = BotRegistry.from_streaming_config(
        {
            "bots": {
                "default": "a",
                "chat_bindings": {"oc_exact": "b"},
                "items": {
                    "a": {"app_id_env": "BOT_A_ID", "app_secret_env": "BOT_A_SECRET"},
                    "b": {"app_id_env": "BOT_B_ID", "app_secret_env": "BOT_B_SECRET"},
                },
            }
        }
    )
    assert registry.resolve("other").bot_id == "a"  # type: ignore[union-attr]
    assert registry.resolve("oc_exact").bot_id == "b"  # type: ignore[union-attr]
    report = registry.diagnostics()
    assert report["bot_count"] == 2
    assert "BOT_A_SECRET" in str(report)
    assert "sa" not in str(report)


def test_card_limits_compact_large_markdown_without_mutation() -> None:
    card = {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": "x" * 50_000}]}}
    compacted = compact_card(card)
    assert len(card["body"]["elements"][0]["content"]) == 50_000
    assert inspect_card(compacted).json_bytes <= MAX_JSON_BYTES
    assert "compacted" in compacted["body"]["elements"][0]["content"]


def test_tool_history_preserves_old_errors_and_recent_steps() -> None:
    steps = [_step(i, "error" if i == 2 else "success") for i in range(10)]
    compacted, hidden = compact_tool_steps(steps, compact_after=8, keep_recent=4)
    assert hidden == 6
    assert compacted[0]["name"] == "history_summary"
    assert any(step["name"] == "tool_2" for step in compacted)
    assert [step["name"] for step in compacted[-4:]] == ["tool_6", "tool_7", "tool_8", "tool_9"]


def test_terminal_segments_collapse_tool_panels_and_old_reasoning() -> None:
    segments = [Segment(SegmentType.REASONING, f"r{i}") for i in range(4)]
    segments += [Segment(SegmentType.TOOL, "t1"), Segment(SegmentType.TOOL, "t2")]
    compacted, steps, stats = compact_terminal_segments(
        segments, [_step(i) for i in range(12)], compact_after=8, keep_recent=4
    )
    assert stats == {"tool_steps": 8, "reasoning_rounds": 2}
    assert sum(segment.type == SegmentType.TOOL for segment in compacted) == 1
    assert sum(segment.type == SegmentType.REASONING for segment in compacted) == 3
    assert steps[0]["name"] == "history_summary"


@pytest.mark.asyncio
async def test_adaptive_backpressure_increases_and_decays() -> None:
    controller = FlushController(throttle_ms=0.1)
    controller.configure_adaptive(enabled=True, min_ms=100, max_ms=800)
    controller.record_failure(rate_limited=True)
    assert controller.adaptive_snapshot()["current_ms"] == 200
    for _ in range(5):
        controller._record_success(10)
    assert controller.adaptive_snapshot()["current_ms"] == 180


def test_native_hook_capability_and_registration() -> None:
    calls = []

    class Context:
        def register_streaming_renderer(self, platform, renderer):
            calls.append((platform, renderer))

    context = Context()
    assert capability()["strategy"] == "ast"
    assert try_register(context, "renderer") is True
    assert calls == [("feishu", "renderer")]


def test_e2e_dry_run_is_offline_and_safe() -> None:
    report = dry_run()
    assert report["mode"] == "dry-run"
    assert report["ok"] is True


def test_sidecar_dispatcher_owns_event_loop_and_forwards_events() -> None:
    calls = []

    class FakeController:
        def on_message_started(self, **kwargs):
            calls.append(kwargs)

    dispatcher = SidecarDispatcher(FakeController())  # type: ignore[arg-type]
    try:
        assert dispatcher.submit({"event": "message.started", "message_id": "m", "chat_id": "c"}) is True
    finally:
        dispatcher.close()
    assert calls == [{"message_id": "m", "chat_id": "c", "anchor_id": None, "session_key": None}]
