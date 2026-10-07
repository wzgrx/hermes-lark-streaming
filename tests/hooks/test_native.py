from __future__ import annotations

import sys
from contextvars import ContextVar
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from hermes_lark_streaming.hooks import native
from hermes_lark_streaming.metrics import metrics


class Context:
    def __init__(self) -> None:
        self.hooks: dict[str, Any] = {}

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks[name] = callback


def test_observers_registered_and_privacy_preserving() -> None:
    ctx = Context()
    assert native.try_register(ctx) is False
    assert tuple(ctx.hooks) == native.OBSERVER_HOOKS
    assert native.capability(ctx)["strategy"] == "native-observer+ast"
    ctx.hooks["on_stream_delta"](kind="reasoning", delta="private text")
    ctx.hooks["post_tool_call"](status="error", error_type="RuntimeError")
    ctx.hooks["on_stream_start"](surface="feishu")
    ctx.hooks["post_approval_response"](choice="DENY")
    snap = metrics.snapshot()
    assert snap["hermes.stream.delta.reasoning"] == 1 and snap["hermes.tool.failed"] == 1
    assert snap["hermes.stream.feishu_start"] == 1 and snap["hermes.approval.deny"] == 1
    assert "private" not in repr(snap)
    assert snap["hermes.native_hooks.registered"] == len(native.OBSERVER_HOOKS)


def test_native_renderer_takes_precedence_over_observers() -> None:
    renderers: list[tuple[str, object]] = []

    class WithRenderer(Context):
        def register_streaming_renderer(self, platform: str, renderer: object) -> None:
            renderers.append((platform, renderer))

    ctx = WithRenderer()
    renderer = object()
    assert native.try_register(ctx, renderer) is True
    assert renderers == [("feishu", renderer)] and ctx.hooks == {}
    assert native.capability(ctx)["strategy"] == "native-renderer"


def test_capability_without_context_is_ast_only() -> None:
    assert native.capability(None) == {"strategy": "ast", "protocol": native.PROTOCOL_METHOD, "observer_hooks": []}


def test_register_is_safe_without_hook_support() -> None:
    assert native.register(object()) is None


def test_hook_registration_failure_is_isolated() -> None:
    class Flaky(Context):
        def register_hook(self, name: str, callback: Any) -> None:
            if name == "on_stream_end":
                raise RuntimeError("nope")
            super().register_hook(name, callback)

    ctx = Flaky()
    assert "on_stream_end" not in native.register_observers(ctx)
    assert "on_stream_start" in ctx.hooks


def test_hooks_filtered_by_hermes_valid_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("hermes_cli.plugins")
    module.VALID_HOOKS = {"on_stream_start", "pre_api_request"}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", module)
    ctx = Context()
    native.register(ctx)
    assert set(ctx.hooks) == {"on_stream_start", "pre_api_request"}
    assert native.runtime_capability()["missing"] == [n for n in native.OBSERVER_HOOKS if n != "on_stream_start"]


def test_runtime_capability_without_hermes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", None)
    assert native.runtime_capability()["available"] is False


@pytest.fixture
def details(monkeypatch: pytest.MonkeyPatch) -> Mock:
    module = ModuleType("hermes_lark_streaming.session")
    observe = Mock()
    module.observe = observe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hermes_lark_streaming.session", module)
    monkeypatch.setattr(sys.modules["hermes_lark_streaming"], "session", module, raising=False)
    return observe


def test_telemetry_hooks_forward_to_session_with_bound_key(details: Mock) -> None:
    ctx = Context()
    native.register_telemetry(ctx)
    assert set(ctx.hooks) == set(native.TELEMETRY_HOOKS)
    var: ContextVar[str] = ContextVar("HERMES_SESSION_KEY")
    token = var.set("agent:main:feishu:dm:oc_1")
    try:
        ctx.hooks["pre_api_request"](platform="feishu", model="m")
    finally:
        var.reset(token)
    details.assert_called_once_with(
        "pre_api_request", {"platform": "feishu", "model": "m"}, session_key="agent:main:feishu:dm:oc_1"
    )


def test_ambiguous_session_context_forwards_none(details: Mock) -> None:
    a, b = ContextVar[str]("HERMES_SESSION_KEY"), ContextVar[str]("approval_session_key")
    ta, tb = a.set("one"), b.set("two")
    try:
        native.observe_telemetry("post_api_request", {"platform": "feishu"})
    finally:
        a.reset(ta)
        b.reset(tb)
    assert details.call_args.kwargs["session_key"] is None and metrics.get("telemetry.skip.context") == 1


def test_missing_session_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hermes_lark_streaming.session", None)
    native.observe_telemetry("pre_api_request", {})  # must not raise


def test_session_without_observe_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hermes_lark_streaming.session", ModuleType("hermes_lark_streaming.session"))
    monkeypatch.setattr(
        sys.modules["hermes_lark_streaming"], "session", sys.modules["hermes_lark_streaming.session"], raising=False
    )
    native.observe_telemetry("pre_api_request", {})


def test_execution_observer_is_transparent_on_observer_failure(details: Mock) -> None:
    error = RuntimeError("provider failure")
    details.side_effect = ValueError("observer bug")
    ok = Mock(return_value=object())
    assert native.execution_observer(next_call=ok, platform="feishu") is ok.return_value
    ok.assert_called_once_with()
    failing = Mock(side_effect=error)
    with pytest.raises(RuntimeError) as caught:
        native.execution_observer(next_call=failing, platform="feishu")
    assert caught.value is error
    failing.assert_called_once_with()


def test_execution_registration_is_capability_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("hermes_cli.middleware")
    monkeypatch.setitem(sys.modules, "hermes_cli.middleware", module)
    ctx = SimpleNamespace(register_middleware=Mock())
    module.VALID_MIDDLEWARE = set()  # type: ignore[attr-defined]
    assert not native.register_execution(ctx) and not native.register_execution(object())
    module.VALID_MIDDLEWARE = {"llm_execution"}  # type: ignore[attr-defined]
    assert native.register_execution(ctx)
    ctx.register_middleware.assert_called_once_with("llm_execution", native.execution_observer)


def test_native_renderer_adapter_delegates(controller: Mock) -> None:
    renderer = native.NativeStreamingRenderer()
    controller.on_answer.return_value = True
    assert renderer.on_answer(message_id="m", text="t") is True
    controller.on_answer.assert_called_once_with(message_id="m", text="t")
