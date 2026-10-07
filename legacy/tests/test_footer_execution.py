"""Transparent public middleware capture, including actual Hermes chain semantics."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from hermes_lark_streaming.footer.hooks import execution_observer, observe, register_execution
from hermes_lark_streaming.footer.state import TurnFooter


def payload(**values):
    return {
        "platform": "feishu", "session_id": "s", "turn_id": "t", "api_request_id": "r",
        "provider": "example", "model": "example-model", "api_mode": "chat_completions",
        "started_at": time.time(), **values,
    }


def prime(state):
    p = payload(request={"_truncated": True, "preview": "PRIVATE TEXT"})
    assert state.observe("pre_api_request", p)
    return p


def test_structured_scalar_restores_truncated_effort_without_retaining_body():
    state = TurnFooter()
    p = prime(state)
    actual = payload(request={"messages": [{"content": "PRIVATE" * 50000}], "reasoning_effort": "max"})
    original = deepcopy(actual)
    assert state.observe_execution(actual)
    assert actual == original
    assert state.observe("pre_api_request", p)  # duplicate pre must not erase execution evidence
    data = state.finish()
    assert data["reasoning"] == "max" and data["reasoning_source"] == "llm_execution"
    assert "reasoning_missing_reason" not in data
    assert "PRIVATE" not in repr(state._requests) + repr(data)
    assert not state.observe_execution(actual)  # sealed turns stay immutable


@pytest.mark.parametrize("changes", [
    {"session_id": "other"}, {"turn_id": "other"}, {"api_request_id": "other"},
    {"platform": "telegram"}, {"aux_task": "compression"}, {"provider": "other"},
    {"model": "other"}, {"api_mode": "other"}, {"model": "x" * 160},
    {"provider": "[redacted] "}, {"turn_id": []}, {"model": None},
])
def test_execution_rejects_foreign_or_ambiguous_route_identity(changes):
    state = TurnFooter()
    prime(state)
    assert not state.observe_execution(payload(request={"reasoning_effort": "max"}, **changes))
    assert state.finish()["reasoning"] == ""


def test_execution_matches_one_active_nonfailed_attempt_only():
    state = TurnFooter()
    assert not state.observe_execution(payload())
    first = prime(state)
    second = payload(started_at=first["started_at"] + 0.01)
    state.observe("pre_api_request", second)
    assert not state.observe_execution(payload(request={"reasoning_effort": "max"}))
    state.observe("api_request_error", first)
    assert state.observe_execution(payload(request={"reasoning_effort": "high"}))
    state.observe("post_api_request", second)
    assert not state.observe_execution(payload(request={"reasoning_effort": "low"}))
    assert state.finish()["reasoning"] == "high"


def test_removed_control_is_not_replaced_with_stale_prehook_effort():
    state = TurnFooter()
    state.observe("pre_api_request", payload(request={"reasoning_effort": "max"}))
    assert state.observe_execution(payload(request={"messages": []}))
    assert state.finish()["reasoning"] == ""


def test_exact_context_binding_and_no_history_forwarding():
    state, other = TurnFooter(), TurnFooter()
    ctrl = SimpleNamespace(
        enabled=True, _cfg=SimpleNamespace(footer_enabled=True, footer_mode="enhanced"),
        _session_keys={key: SimpleNamespace(state=SimpleNamespace(is_terminal=False), footer_state=s)
                       for key, s in [("target", state), ("other", other)]},
    )
    scope = ContextVar("HERMES_SESSION_KEY")
    token = scope.set("target")
    downstream = Mock(return_value=object())
    try:
        with patch("hermes_lark_streaming.controller.get_controller", return_value=ctrl):
            observe("pre_api_request", payload(request={"_truncated": True}))
            with patch("hermes_lark_streaming.footer.history.observe_history") as history:
                result = execution_observer(next_call=downstream, **payload(request={"reasoning_effort": "max"}))
                history.assert_not_called()
    finally:
        scope.reset(token)
    assert result is downstream.return_value
    downstream.assert_called_once_with()
    assert state.finish()["reasoning"] == "max"
    assert other.finish()["telemetry_missing"]


def test_observation_failure_preserves_result_and_exception_identity():
    error = RuntimeError("synthetic provider failure")
    for downstream in (Mock(return_value=object()), Mock(side_effect=error)):
        with patch("hermes_lark_streaming.footer.hooks._observe_bound", side_effect=ValueError):
            if downstream.side_effect:
                with pytest.raises(RuntimeError) as caught:
                    execution_observer(next_call=downstream, **payload())
                assert caught.value is error
            else:
                assert execution_observer(next_call=downstream, **payload()) is downstream.return_value
        downstream.assert_called_once_with()


def test_registration_is_capability_gated(monkeypatch):
    module = ModuleType("hermes_cli.middleware")
    monkeypatch.setitem(sys.modules, "hermes_cli.middleware", module)
    ctx = SimpleNamespace(register_middleware=Mock())
    module.VALID_MIDDLEWARE = set()
    assert not register_execution(ctx)
    assert not register_execution(object())
    module.VALID_MIDDLEWARE = {"llm_execution"}
    assert register_execution(ctx)
    ctx.register_middleware.assert_called_once_with("llm_execution", execution_observer)


@pytest.fixture
def actual_middleware(monkeypatch):
    root = Path(os.environ.get("HERMES_MODULAR_SOURCE", Path.home() / ".hermes/hermes-agent"))
    source = root / "hermes_cli/middleware.py"
    if not source.is_file():
        pytest.skip("Current Hermes middleware source required")
    spec = importlib.util.spec_from_file_location("_card_test_hermes_middleware", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugins = ModuleType("hermes_cli.plugins")
    manager = SimpleNamespace(_middleware={"llm_execution": [execution_observer]}, _report_hook_failure=Mock())
    plugins._delivery_manager = lambda: manager
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    yield module, manager


@pytest.mark.parametrize("fail", [False, True])
def test_actual_hermes_execution_chain_does_not_repeat_or_mutate_calls(actual_middleware, fail):
    module, manager = actual_middleware
    request = {"reasoning_effort": "max", "messages": [{"content": "synthetic"}]}
    original = deepcopy(request)
    error = RuntimeError("synthetic error")
    terminal = Mock(side_effect=error) if fail else Mock(return_value=object())
    with patch("hermes_lark_streaming.footer.hooks._observe_bound"):
        if fail:
            with pytest.raises(RuntimeError) as caught:
                module.run_llm_execution_middleware(request, terminal, **payload())
            assert caught.value is error
        else:
            assert module.run_llm_execution_middleware(request, terminal, **payload()) is terminal.return_value
    terminal.assert_called_once_with(request)
    assert terminal.call_args.args[0] is request and request == original
    manager._report_hook_failure.assert_not_called()
