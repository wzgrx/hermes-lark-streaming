"""Turn telemetry: identity, de-duplication, partial coverage, scalar-only privacy and the Footer values."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path

import pytest

from hermes_lark_streaming.card.model import Footer
from hermes_lark_streaming.details.reasoning import requested_reasoning
from hermes_lark_streaming.details.telemetry import TurnTelemetry

DATA = Path(__file__).parents[2] / "docs/data"


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
    s = TurnTelemetry()
    p = event()
    assert not complete(s, p)  # no orphan late posts
    assert s.observe("pre_api_request", p)
    assert s.observe("pre_api_request", p)
    assert not s.observe("pre_api_request", {**p, "turn_id": "another"})
    assert complete(s, p)
    assert not complete(s, p)
    data = s.finish()
    assert data["input_tokens"] == 100 and data["api_calls"] == 1 and data["cache_read_tokens"] == 70
    assert not s.observe("pre_api_request", event(api_request_id="r2"))
    assert s.finish() == data


def test_retry_partial_unknown_and_auxiliary_exclusion():
    s = TurnTelemetry()
    p = event()
    assert not s.observe("pre_api_request", {**p, "aux_task": "compression"})
    assert s.observe("pre_api_request", p)
    s.observe("api_request_error", p)
    p2 = event("siliconflow", api_request_id="r1")  # fresh attempt, same logical request ID
    assert s.observe("pre_api_request", p2)
    complete(s, p2)
    d = s.finish()
    assert d["api_calls"] == 2 and d["retries"] == 1
    assert d["usage_partial"] and d["cache_read_partial"] and d["cache_read_tokens"] == 70
    assert d["routes"] == ["opencode-go", "siliconflow"]


def test_missing_zero_cache_and_old_event():
    s = TurnTelemetry()
    assert not s.observe("pre_api_request", event(started_at=0))
    p = event()
    s.observe("pre_api_request", p)
    complete(s, p, usage={"prompt_tokens": 100, "output_tokens": 0, "cache_read_tokens": 0})
    data = s.finish()
    assert "cache_read_tokens" not in data and data["output_tokens"] == 0
    assert "telemetry_missing" in TurnTelemetry().finish()


def test_latest_request_not_arrival_controls_context_and_model():
    s = TurnTelemetry()
    p1 = event()
    s.observe("pre_api_request", p1)
    p2 = event(api_request_id="r2")
    s.observe("pre_api_request", p2)
    complete(s, p2, response_model="served", usage={"prompt_tokens": 200, "output_tokens": 3})
    complete(s, p1)
    data = s.finish()
    assert data["model"] == "served" and data["context_used"] == 200
    assert data["input_tokens"] == 300 and data["output_tokens"] == 10


def test_rotated_storage_identity_is_partial_not_misattributed():
    state = TurnTelemetry()
    first = event()
    state.observe("pre_api_request", first)
    complete(state, first)
    assert not state.observe("pre_api_request", event(session_id="rotated", api_request_id="r2"))
    assert state.finish()["usage_partial"]


def test_bounded_requests_are_marked_partial():
    s = TurnTelemetry()
    for i in range(2050):
        p = event(api_request_id=str(i))
        s.observe("pre_api_request", p)
        complete(s, p)
    assert s.finish()["api_calls"] == 2048
    assert s.finish()["usage_partial"]


@pytest.mark.parametrize("method", ["finish", "snapshot"])
def test_sealed_routes_are_owned_by_the_turn(method):
    state = TurnTelemetry()
    e = event(started_at=state.created_at + 1, provider="first")
    assert state.observe("pre_api_request", e)
    assert state.observe("post_api_request", {**e, "usage": {"prompt_tokens": 10, "output_tokens": 1}})
    state.finish()
    getattr(state, method)()["routes"].append("changed-by-consumer")
    assert state.snapshot()["routes"] == ["first"]
    assert state.finish()["route_count"] == 1


def turn(caches):
    state = TurnTelemetry()
    for i, cache in enumerate(caches):
        e = {"platform": "feishu", "session_id": "s", "turn_id": "t", "api_request_id": str(i)}
        e["started_at"] = state.created_at + i + 1
        assert state.observe("pre_api_request", e)
        usage = {"prompt_tokens": 100, "output_tokens": 1}
        if cache is not None:
            usage["cache_read_tokens"] = cache
        assert state.observe("post_api_request", {**e, "usage": usage})
    return state


@pytest.mark.parametrize("unknown", [0, None, -1, True, 101])
@pytest.mark.parametrize("reverse", [False, True])
def test_positive_cache_survives_a_zero_filled_missing_or_invalid_peer(unknown, reverse):
    state = turn([40, unknown] if reverse else [unknown, 40])
    data = state.finish()
    assert data["input_tokens"] == 200 and data["output_tokens"] == 2 and data["usage_partial"] is False
    assert data["cache_read_tokens"] == 40 and data["cache_read_partial"] is True
    footer = state.footer()
    assert footer.cache_hit == pytest.approx(0.2) and footer.cache_hit_is_floor and not footer.partial


def test_all_positive_cache_remains_a_complete_rate():
    state = turn([20, 40])
    data = state.finish()
    assert data["cache_read_tokens"] == 60 and not data.get("cache_read_partial")
    footer = state.footer()
    assert footer.cache_hit == pytest.approx(0.3) and not footer.cache_hit_is_floor


@pytest.mark.parametrize("caches", [[0, 0], [None, None], [0, None]])
def test_all_unknown_cache_does_not_invent_zero(caches):
    state = turn(caches)
    assert "cache_read_tokens" not in state.finish()
    assert state.footer().cache_hit is None


def test_failed_request_preserves_partial_positive_cache_without_a_rate():
    state = TurnTelemetry()
    first = {"platform": "feishu", "session_id": "s", "turn_id": "t", "api_request_id": "1"}
    first["started_at"] = state.created_at + 1
    assert state.observe("pre_api_request", first)
    assert state.observe("api_request_error", first)
    second = {**first, "api_request_id": "2", "started_at": state.created_at + 2}
    assert state.observe("pre_api_request", second)
    usage = {"prompt_tokens": 100, "output_tokens": 1, "cache_read_tokens": 40}
    assert state.observe("post_api_request", {**second, "usage": usage})
    data = state.finish()
    assert data["usage_partial"] and data["cache_read_partial"] and data["cache_read_tokens"] == 40
    assert data["retries"] == 1
    footer = state.footer()
    assert footer.cache_hit is None and footer.partial  # count known, rate unknown


def test_footer_values():
    state = TurnTelemetry()
    assert state.footer() == Footer()  # nothing observed: all unknown, tag left to the session layer
    p = event()
    state.observe("pre_api_request", p)
    complete(state, p)
    footer = state.footer()
    assert footer == Footer(
        model="DeepSeek V4.1 Flash", context_used=100, context_max=1000, cache_hit=0.7, cache_hit_is_floor=False
    )
    assert footer.tag == ""


def test_footer_humanizes_known_bare_id_only():
    state = TurnTelemetry()
    p = event(model="deepseek-v4-pro")
    state.observe("pre_api_request", p)
    complete(state, p)
    assert state.footer().model == "DeepSeek V4 Pro"


def test_footer_does_not_leak_secrets_or_markup_labels():
    state = TurnTelemetry()
    p = event(model="sk-secret-value")
    state.observe("pre_api_request", p)
    complete(state, p)
    assert "sk-secret" not in repr(state.footer()) and state.footer().model == "[redacted]"


def test_truncated_hook_explains_missing_reasoning_without_reading_preview():
    state = TurnTelemetry()
    p = event(request={"_truncated": True, "preview": '"reasoning_effort":"max" secret content'})
    state.observe("pre_api_request", p)
    complete(state, p)
    result = state.finish()
    assert result["reasoning"] == "" and result["reasoning_missing_reason"] == "request_truncated"
    assert "secret content" not in json.dumps(result)


@pytest.mark.parametrize("nested", [True, False])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RateLimitError", "RateLimitError"),
        ("httpx.TimeoutException", "httpx.TimeoutException"),
        ("PRIVATE TEXT contains body", ""),
        ("https://secret.invalid", ""),
        ("sk_privatevalue", ""),
        (None, ""),
    ],
)
def test_failure_type_only_never_stores_error_message(raw, expected, nested):
    state = TurnTelemetry()
    p = event()
    state.observe("pre_api_request", p)
    error = {"type": raw, "message": "PRIVATE EXCEPTION BODY"} if nested else "PRIVATE EXCEPTION BODY"
    state.observe("api_request_error", {**p, "error_type": None if nested else raw, "error": error})
    data = state.finish()
    assert data.get("last_error_type", "") == expected
    assert "PRIVATE" not in repr(state._requests) + repr(data)


def test_reasoning_body_only_scalar_whitelist():
    assert requested_reasoning({"request": {"body": {"extra_body": {"reasoning_effort": "max"}}}}) == "max"
    assert requested_reasoning({"request": {"body": {"reasoning": {"effort": "high"}}}}) == "high"
    assert requested_reasoning({"request": {"body": {"thinking": {"budget_tokens": 1000}}}}) == "budget:1000"
    assert requested_reasoning({"request": {"body": {"reasoning_effort": "secret content"}}}) == ""


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


def _provider_ids():
    catalog = json.loads((DATA / "providers-20261003.json").read_text())
    profiles = json.loads((DATA / "hermes-profiles-20261003.json").read_text())
    return sorted({p["id"] for p in catalog["providers"]} | {p["name"] for p in profiles} | {"my-private-provider"})


@pytest.mark.parametrize("provider", _provider_ids())
def test_every_catalog_id_uses_same_canonical_path(provider):
    state = TurnTelemetry()
    p = event(provider)
    state.observe("pre_api_request", p)
    complete(state, p)
    data = state.finish()
    assert data["provider"] == provider and data["input_tokens"] == 100


def prime(state):
    p = event(provider="example", request={"_truncated": True, "preview": "PRIVATE TEXT"})
    assert state.observe("pre_api_request", p)
    return p


def test_structured_scalar_restores_truncated_effort_without_retaining_body():
    state = TurnTelemetry()
    p = prime(state)
    actual = event(
        provider="example", request={"messages": [{"content": "PRIVATE" * 50000}], "reasoning_effort": "max"}
    )
    original = deepcopy(actual)
    assert state.observe_execution(actual)
    assert actual == original
    assert state.observe("pre_api_request", p)  # duplicate pre must not erase execution evidence
    data = state.finish()
    assert data["reasoning"] == "max" and data["reasoning_source"] == "llm_execution"
    assert "reasoning_missing_reason" not in data
    assert "PRIVATE" not in repr(state._requests) + repr(data)
    assert not state.observe_execution(actual)  # sealed turns stay immutable


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "other"},
        {"turn_id": "other"},
        {"api_request_id": "other"},
        {"platform": "telegram"},
        {"aux_task": "compression"},
        {"provider": "other"},
        {"model": "other"},
        {"api_mode": "other"},
        {"model": "x" * 160},
        {"provider": "[redacted] "},
        {"turn_id": []},
        {"model": None},
    ],
)
def test_execution_rejects_foreign_or_ambiguous_route_identity(changes):
    state = TurnTelemetry()
    prime(state)
    candidate = event(provider="example", request={"reasoning_effort": "max"})
    candidate.update(changes)
    assert not state.observe_execution(candidate)
    assert state.finish()["reasoning"] == ""


def test_execution_matches_one_active_nonfailed_attempt_only():
    state = TurnTelemetry()
    assert not state.observe_execution(event(provider="example"))
    first = prime(state)
    second = event(provider="example", started_at=first["started_at"] + 0.01)
    state.observe("pre_api_request", second)
    assert not state.observe_execution(event(provider="example", request={"reasoning_effort": "max"}))
    state.observe("api_request_error", first)
    assert state.observe_execution(event(provider="example", request={"reasoning_effort": "high"}))
    state.observe("post_api_request", second)
    assert not state.observe_execution(event(provider="example", request={"reasoning_effort": "low"}))
    assert state.finish()["reasoning"] == "high"


def test_removed_control_is_not_replaced_with_stale_prehook_effort():
    state = TurnTelemetry()
    state.observe("pre_api_request", event(provider="example", request={"reasoning_effort": "max"}))
    assert state.observe_execution(event(provider="example", request={"messages": []}))
    assert state.finish()["reasoning"] == ""
