from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import hermes_lark_streaming.sidecar as sidecar_module
from hermes_lark_streaming import metrics as metrics_module
from hermes_lark_streaming.__main__ import _cmd_metrics
from hermes_lark_streaming.card_limits import MAX_JSON_BYTES, compact_card, inspect_card
from hermes_lark_streaming.e2e import dry_run
from hermes_lark_streaming.history import compact_terminal_segments, compact_tool_steps
from hermes_lark_streaming.metrics import MetricsStore
from hermes_lark_streaming.native_hooks import OBSERVER_HOOKS, capability, try_register
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


def test_metrics_persist_concurrent_writers_use_distinct_staging_files(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "metrics.json"
    barrier = threading.Barrier(2)
    original_replace = metrics_module.os.replace

    def synchronized_replace(source, target):
        barrier.wait(timeout=5)
        return original_replace(source, target)

    monkeypatch.setattr(metrics_module.os, "replace", synchronized_replace)
    stores = [MetricsStore(path), MetricsStore(path)]
    for index, store in enumerate(stores):
        store.increment(f"writer.{index}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda store: store.persist(), stores))

    assert results == [path, path]
    assert json.loads(path.read_text(encoding="utf-8"))["counters"] in (
        {"writer.0": 1}, {"writer.1": 1},
    )
    assert not list(path.parent.glob("metrics.json.*.tmp"))


def test_sidecar_snapshot_does_not_replace_gateway_diagnostics(tmp_path: Path) -> None:
    path = tmp_path / "state" / "metrics.json"
    gateway = MetricsStore(path)
    sidecar = MetricsStore(path)
    sidecar.set_role("sidecar")
    gateway.increment("api.cardkit_batch_update.error_code.300313")
    sidecar.increment("sidecar.event.message.started")

    gateway.persist()
    sidecar.persist()

    assert gateway.load_persisted()["counters"] == {
        "api.cardkit_batch_update.error_code.300313": 1,
    }
    assert gateway.load_persisted(role="sidecar")["counters"] == {
        "sidecar.event.message.started": 1,
    }
    assert sidecar.path == path.with_name("metrics-sidecar.json")


def test_legacy_shared_snapshot_has_ambiguous_owner(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    path.write_text('{"schema": 1, "counters": {"sidecar.event.x": 1}}', encoding="utf-8")
    assert MetricsStore(path).load_persisted(role="gateway") is None


def test_sidecar_selects_own_snapshot_before_dispatch(monkeypatch) -> None:
    class FakeDispatcher:
        def close(self) -> None:
            pass

    class FakeServer:
        def __init__(self, _address, _handler) -> None:
            assert metrics_module.metrics.path.name == "metrics-sidecar.json"

        def serve_forever(self) -> None:
            pass

    monkeypatch.setattr(sidecar_module, "SidecarDispatcher", FakeDispatcher)
    monkeypatch.setattr(sidecar_module, "ThreadingHTTPServer", FakeServer)
    sidecar_module.serve()


def test_runtime_metrics_path_is_isolated_from_operator_home(tmp_path: Path) -> None:
    assert metrics_module.metrics.path.is_relative_to(tmp_path)


def test_metrics_persist_removes_staging_after_replace_failure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "state" / "metrics.json"
    store = MetricsStore(path)

    def fail_replace(source, target):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(metrics_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        store.persist()
    assert not list(path.parent.glob("metrics.json.*.tmp"))


def test_metrics_cli_does_not_present_its_own_empty_process_as_gateway(capsys) -> None:
    assert _cmd_metrics() == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "metrics_unavailable", "detail": "No readable persisted gateway metrics snapshot"}


def test_metrics_cli_reads_persisted_snapshot(capsys) -> None:
    metrics_module.metrics.increment("card.cli_test")
    metrics_module.metrics.persist()

    assert _cmd_metrics() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["counters"]["card.cli_test"] >= 1
    assert output["schema"] == 1


def test_metrics_cli_selects_sidecar_without_masking_gateway(capsys, monkeypatch) -> None:
    gateway = metrics_module.metrics
    gateway.increment("card.gateway")
    gateway.persist()
    sidecar = MetricsStore(gateway.path)
    sidecar.set_role("sidecar")
    sidecar.increment("sidecar.event.test")
    sidecar.persist()
    monkeypatch.setattr(sys, "argv", ["hermes_lark_streaming", "metrics", "--sidecar"])

    assert _cmd_metrics() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["process_role"] == "sidecar"
    assert output["counters"] == {"sidecar.event.test": 1}
    assert gateway.load_persisted()["counters"]["card.gateway"] == 1


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


def test_card_limits_compact_structural_overhead_and_preserve_latest_answer() -> None:
    panels = [
        {
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {"tag": "plain_text", "content": f"Tool {index}"},
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": "d" * 180}},
                {"tag": "div", "text": {"tag": "lark_md", "content": "r" * 180}},
            ],
        }
        for index in range(64)
    ]
    card = {
        "schema": "2.0",
        "body": {
            "elements": [
                *panels,
                {"tag": "markdown", "content": "the newest answer must survive"},
                {"tag": "hr"},
                {"tag": "markdown", "content": "completed · model"},
            ]
        },
    }

    assert not inspect_card(card).safe
    compacted = compact_card(card)
    inspection = inspect_card(compacted)

    assert inspection.safe
    assert inspection.elements <= 200
    assert inspection.json_bytes <= MAX_JSON_BYTES
    assert "compacted" in compacted["body"]["elements"][0]["content"]
    assert any(element.get("content") == "the newest answer must survive" for element in compacted["body"]["elements"])


def test_card_limits_compact_byte_heavy_short_tool_panels() -> None:
    panels = [
        {
            "tag": "collapsible_panel",
            "expanded": False,
            "header": {"tag": "plain_text", "content": f"Tool {index}"},
            "elements": [
                {
                    "tag": "div",
                    "margin": "0px 0px 0px 22px",
                    "text": {"tag": "plain_text", "content": "x" * 170},
                    "metadata": "m" * 400,
                }
            ],
        }
        for index in range(45)
    ]
    card = {
        "schema": "2.0",
        "config": {"summary": {"content": "done"}},
        "body": {"elements": [*panels, {"tag": "markdown", "content": "final answer"}]},
    }

    assert inspect_card(card).elements < 200
    assert inspect_card(card).json_bytes > MAX_JSON_BYTES
    compacted = compact_card(card)

    assert inspect_card(compacted).safe
    assert any(element.get("content") == "final answer" for element in compacted["body"]["elements"])


def test_card_limits_tight_budget_terminates_and_keeps_latest_answer() -> None:
    card = {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": "old " * 300},
                {"tag": "markdown", "content": "latest answer"},
                {"tag": "hr"},
                {"tag": "markdown", "content": "completed · model"},
            ]
        },
    }

    compacted = compact_card(card, max_bytes=300)

    assert inspect_card(compacted).json_bytes <= 300
    assert any(element.get("content") == "latest answer" for element in compacted["body"]["elements"])
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


def test_native_observer_bridge_registers_supported_lifecycle_hooks(monkeypatch) -> None:
    registered = {}
    increments = []

    class Context:
        def register_hook(self, name, callback):
            registered[name] = callback

    class Metrics:
        def increment(self, name, amount=1):
            increments.append((name, amount))

    monkeypatch.setattr("hermes_lark_streaming.native_hooks.metrics", Metrics())
    context = Context()
    assert try_register(context) is False
    assert tuple(registered) == OBSERVER_HOOKS
    assert capability(context)["strategy"] == "native-observer+ast"

    registered["on_stream_delta"](kind="reasoning", delta="private text is ignored")
    registered["post_tool_call"](status="error", error_type="RuntimeError")
    assert ("hermes.stream.delta.reasoning", 1) in increments
    assert ("hermes.tool.failed", 1) in increments
    assert not any("private text" in str(item) for item in increments)


def test_future_native_renderer_takes_precedence_over_observers(monkeypatch) -> None:
    renderers = []
    observers = []

    class Context:
        def register_streaming_renderer(self, platform, renderer):
            renderers.append((platform, renderer))

        def register_hook(self, name, callback):
            observers.append((name, callback))

    class Metrics:
        def increment(self, name, amount=1):
            pass

    monkeypatch.setattr("hermes_lark_streaming.native_hooks.metrics", Metrics())
    context = Context()
    renderer = object()
    assert try_register(context, renderer) is True
    assert renderers == [("feishu", renderer)]
    assert observers == []
    assert capability(context)["strategy"] == "native-renderer"
