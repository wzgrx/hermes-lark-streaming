"""Offline protocol fixtures and exact-identity footer integration; no paid APIs."""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_lark_streaming.cardkit.builder import build_complete_card
from hermes_lark_streaming.config import Config
from hermes_lark_streaming.footer.hooks import observe
from hermes_lark_streaming.footer.render import build_footer
from hermes_lark_streaming.footer.state import TurnFooter, requested_reasoning
from hermes_lark_streaming.footer.usage import normalize_usage
from hermes_lark_streaming.streaming.segment_helper import find_tool_split_offset
from hermes_lark_streaming.streaming.segments import SegmentState
from hermes_lark_streaming.streaming.tooluse import ToolUseTracker


def test_enhanced_footer_reserves_nested_elements():
    cfg = Config()
    cfg._raw = {"streaming": {"footer": {"mode": "enhanced"}}}
    assert cfg.footer_element_reserve == 6
    tracker = ToolUseTracker()
    for _ in range(128):
        tracker.record_start("check")
    steps = tracker.build_display_steps()
    state = SegmentState()
    state.on_tool_event(1)
    state.on_tool_event(len(steps))
    assert find_tool_split_offset(base_count=1, seg=state.segments[0], all_steps=steps, footer_reserve=6) == 56
    cfg._raw = {}
    assert cfg.footer_element_reserve == 2
    assert find_tool_split_offset(base_count=1, seg=state.segments[0], all_steps=steps) == 58


@pytest.mark.parametrize(
    ("protocol", "usage", "prompt", "output", "cache"),
    [
        (
            "hermes",
            {"input_tokens": 20, "cache_read_tokens": 70, "cache_write_tokens": 10, "output_tokens": 7},
            100,
            7,
            70,
        ),
        (
            "chat_completions",
            {"prompt_tokens": 100, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 70}},
            100,
            7,
            70,
        ),
        ("chat_completions", {"prompt_tokens": 100, "completion_tokens": 7, "prompt_cache_hit_tokens": 70}, 100, 7, 70),
        (
            "responses",
            {"input_tokens": 100, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 70}},
            100,
            7,
            70,
        ),
        (
            "anthropic_messages",
            {"input_tokens": 20, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 10, "output_tokens": 7},
            100,
            7,
            70,
        ),
        (
            "bedrock_converse",
            {"inputTokens": 20, "cacheReadInputTokens": 70, "cacheWriteInputTokens": 10, "outputTokens": 7},
            100,
            7,
            70,
        ),
        (
            "gemini",
            {
                "promptTokenCount": 100,
                "candidatesTokenCount": 5,
                "thoughtsTokenCount": 2,
                "cachedContentTokenCount": 70,
            },
            100,
            7,
            70,
        ),
        ("ollama", {"prompt_eval_count": 100, "eval_count": 7}, 100, 7, None),
        ("chat_completions", {"prompt_tokens": 100, "completion_tokens": 7}, 100, 7, None),
        ("unknown", {"input_tokens": 100, "output_tokens": 7}, None, None, None),
    ],
)
def test_protocol_usage(protocol, usage, prompt, output, cache):
    actual = normalize_usage(usage, protocol)
    assert (actual.prompt, actual.output, actual.cache_read) == (prompt, output, cache)


@pytest.mark.parametrize("bad", [None, [], "123", True, -1, 1.2, float("nan"), 10**20])
def test_bad_usage_is_unknown(bad):
    assert normalize_usage({"prompt_tokens": bad}, "chat_completions").prompt is None


def test_no_double_count_and_zero_is_not_missing():
    a = normalize_usage({"input_tokens": 30, "prompt_tokens": 100, "cache_read_tokens": 70}, "hermes")
    assert a.prompt == 100
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}, "chat_completions").prompt == 0
    assert normalize_usage({"prompt_tokens": 5, "prompt_cache_hit_tokens": 100}, "chat_completions").cache_read is None


def event(provider="opencode-go", **extra):
    return {
        "platform": "feishu",
        "session_id": "s1",
        "turn_id": "t1",
        "api_request_id": "r1",
        "started_at": time.time(),
        "provider": provider,
        "model": "deepseek-v4.1-flash",
        "api_mode": "chat_completions",
        **extra,
    }


def complete(state, p, **extra):
    return state.observe(
        "post_api_request",
        {
            **p,
            "usage": {"prompt_tokens": 100, "input_tokens": 30, "output_tokens": 7, "cache_read_tokens": 70},
            "context_length": 1000,
            **extra,
        },
    )


def test_dedup_identity_seal():
    s = TurnFooter()
    p = event()
    assert not complete(s, p)  # no orphan late posts
    assert s.observe("pre_api_request", p)
    assert s.observe("pre_api_request", p)
    assert not s.observe("pre_api_request", {**p, "turn_id": "another"})
    assert complete(s, p)
    assert not complete(s, p)
    data = s.finish()
    assert data["input_tokens"] == 100
    assert data["api_calls"] == 1
    assert data["cache_read_tokens"] == 70
    assert not s.observe("pre_api_request", event(api_request_id="r2"))
    assert s.finish() == data


def test_retry_partial_unknown_and_auxiliary_exclusion():
    s = TurnFooter()
    p = event()
    assert not s.observe("pre_api_request", {**p, "aux_task": "compression"})
    assert s.observe("pre_api_request", p)
    s.observe("api_request_error", p)
    p2 = event("siliconflow", api_request_id="r1")  # fresh attempt, same logical request ID
    assert s.observe("pre_api_request", p2)
    complete(s, p2)
    d = s.finish()
    assert d["api_calls"] == 2 and d["retries"] == 1
    assert d["usage_partial"] and "cache_read_tokens" not in d
    assert d["routes"] == ["opencode-go", "siliconflow"]


def test_missing_zero_cache_and_old_event():
    s = TurnFooter()
    assert not s.observe("pre_api_request", event(started_at=0))
    p = event()
    s.observe("pre_api_request", p)
    complete(s, p, usage={"prompt_tokens": 100, "output_tokens": 0, "cache_read_tokens": 0})
    data = s.finish()
    assert "cache_read_tokens" not in data and data["output_tokens"] == 0
    assert "telemetry_missing" in TurnFooter().finish()


def test_latest_request_not_arrival_controls_context_and_model():
    s = TurnFooter()
    p1 = event()
    s.observe("pre_api_request", p1)
    p2 = event(api_request_id="r2")
    s.observe("pre_api_request", p2)
    complete(s, p2, response_model="served", usage={"prompt_tokens": 200, "output_tokens": 3})
    complete(s, p1)
    data = s.finish()
    assert data["model"] == "served" and data["context_used"] == 200
    assert data["input_tokens"] == 300 and data["output_tokens"] == 10


def test_reasoning_body_only_scalar_whitelist():
    assert requested_reasoning({"request": {"body": {"extra_body": {"reasoning_effort": "max"}}}}) == "max"
    assert requested_reasoning({"request": {"body": {"reasoning": {"effort": "high"}}}}) == "high"
    assert requested_reasoning({"request": {"body": {"thinking": {"budget_tokens": 1000}}}}) == "budget:1000"
    assert requested_reasoning({"request": {"body": {"reasoning_effort": "secret content"}}}) == ""


CATALOG = json.loads((Path(__file__).parents[1] / "docs/data/providers-20261003.json").read_text())
PROFILES = json.loads((Path(__file__).parents[1] / "docs/data/hermes-profiles-20261003.json").read_text())


@pytest.mark.parametrize(
    "provider",
    sorted({p["id"] for p in CATALOG["providers"]} | {p["name"] for p in PROFILES} | {"my-private-provider"}),
)
def test_every_catalog_id_uses_same_canonical_path(provider):
    state = TurnFooter()
    p = event(provider)
    state.observe("pre_api_request", p)
    complete(state, p)
    data = state.finish()
    assert data["provider"] == provider and data["input_tokens"] == 100
    card = build_footer(data)
    assert card[-1]["tag"] == "collapsible_panel" and not card[-1]["expanded"]


def test_renderer_unknown_escaping_and_details_switch():
    data = {"model": '<at id="all">x</at> [click](https://evil.invalid)', "provider": "sk-secret", "duration": 1.5}
    rendered = json.dumps(build_footer(data), ensure_ascii=False)
    assert "evil.invalid" not in rendered and "sk-secret" not in rendered and "<at" not in rendered
    assert "0%" not in rendered and "↑0" not in rendered
    assert len(build_footer(data, details=False)) == 2
    escaped = json.dumps(build_footer({"model": "<at>malice</at>"}))
    assert "&lt;at&gt;" in escaped


def test_completion_builder_and_config_modes():
    config = Config()
    config._raw = {"streaming": {"footer": {"mode": "enhanced", "details": False}}}
    assert config.footer_mode == "enhanced" and not config.footer_details
    config._raw = {"streaming": {"footer": []}}
    assert config.footer_mode == "classic"
    card = build_complete_card(segments=[], all_tool_steps=[], footer_mode="enhanced", footer_data={"model": "local"})
    assert any(e["tag"] == "collapsible_panel" for e in card["body"]["elements"])


def test_hook_context_binding_no_fallback(monkeypatch):
    approval = ModuleType("tools.approval_context")
    approval.get_current_session_key = lambda default: "key-a"
    monkeypatch.setitem(__import__("sys").modules, "tools.approval_context", approval)
    state = TurnFooter()
    other = TurnFooter()
    ctrl = SimpleNamespace(
        enabled=True,
        _cfg=SimpleNamespace(footer_mode="enhanced", footer_enabled=True),
        _session_keys={
            "key-a": SimpleNamespace(state=SimpleNamespace(is_terminal=False), footer_state=state),
            "key-b": SimpleNamespace(state=SimpleNamespace(is_terminal=False), footer_state=other),
        },
    )
    p = event()
    scope = ContextVar("HERMES_SESSION_KEY")
    token = scope.set("key-a")
    try:
        with patch("hermes_lark_streaming.controller.get_controller", return_value=ctrl):
            observe("pre_api_request", p)
            observe("post_api_request", {**p, "usage": {"prompt_tokens": 5, "output_tokens": 1}})
    finally:
        scope.reset(token)
    assert state.finish()["input_tokens"] == 5
    assert other.finish()["telemetry_missing"]


def test_hook_ignores_process_environment(monkeypatch):
    monkeypatch.setenv("HERMES_SESSION_KEY", "stale-other-profile")
    with patch("hermes_lark_streaming.controller.get_controller") as controller:
        observe("pre_api_request", event())
    controller.assert_not_called()


def test_rotated_storage_identity_is_partial_not_misattributed():
    state = TurnFooter()
    first = event()
    state.observe("pre_api_request", first)
    complete(state, first)
    assert not state.observe("pre_api_request", event(session_id="rotated", api_request_id="r2"))
    assert state.finish()["usage_partial"]


def test_bounded_requests_are_marked_partial():
    s = TurnFooter()
    for i in range(2050):
        p = event(api_request_id=str(i))
        s.observe("pre_api_request", p)
        complete(s, p)
    assert s.finish()["api_calls"] == 2048
    assert s.finish()["usage_partial"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"output_config": {"effort": "max"}, "thinking": {"type": "adaptive"}}, "max"),
        ({"generationConfig": {"thinkingConfig": {"thinkingLevel": "HIGH"}}}, "high"),
        ({"generation_config": {"thinking_config": {"thinking_budget": 512}}}, "budget:512"),
        ({"extra_body": {"google": {"thinking_config": {"thinking_level": "low"}}}}, "low"),
        ({"additionalModelRequestFields": {"thinking": {"type": "enabled", "budget_tokens": 1024}}}, "budget:1024"),
        ({"extra_body": {"enable_thinking": True}}, "enabled"),
        ({"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}, "disabled"),
    ],
)
def test_reasoning_protocol_controls(body, expected):
    assert requested_reasoning({"request": {"body": body}}) == expected
