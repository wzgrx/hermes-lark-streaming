"""Hermes native plugin integration: observer hooks and streaming-renderer capability detection.

Hermes 0.21.x observer hooks carry no chat/message id and their return values are ignored, so they feed
telemetry only and never own delivery. The reversible source injection (``table``) stays the card owner;
if a future Hermes context exposes ``register_streaming_renderer``, that protocol takes over.

Telemetry is forwarded to ``hermes_lark_streaming.details.observe(event, payload, session_key=...)`` when the
details package provides it; its absence is tolerated.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from contextvars import copy_context
from typing import Any

from ..metrics import metrics

PROTOCOL_METHOD = "register_streaming_renderer"
OBSERVER_HOOKS = (
    "on_stream_start",
    "on_stream_delta",
    "on_stream_end",
    "on_interim_message",
    "post_tool_call",
    "agent_loop_stopped",
    "pre_approval_request",
    "post_approval_response",
)
TELEMETRY_HOOKS = (
    "pre_api_request",
    "post_api_request",
    "api_request_error",
    "pre_auxiliary_call",
    "post_auxiliary_call",
)
_SESSION_VARS = {"HERMES_SESSION_KEY", "approval_session_key"}
_logger = logging.getLogger("hermes_lark_streaming")


class NativeStreamingRenderer:
    """Thin lifecycle adapter for a future owner-capable Hermes renderer protocol."""

    @staticmethod
    def _controller() -> Any:
        from hermes_lark_streaming.session import get_controller  # type: ignore[attr-defined,unused-ignore]

        return get_controller()

    def on_message_started(self, **payload: Any) -> None:
        self._controller().on_message_started(**payload)

    def on_thinking(self, **payload: Any) -> bool:
        return bool(self._controller().on_thinking(**payload))

    def on_reasoning(self, **payload: Any) -> bool:
        return bool(self._controller().on_reasoning(**payload))

    def on_answer(self, **payload: Any) -> bool:
        return bool(self._controller().on_answer(**payload))

    def on_tool_update(self, **payload: Any) -> bool:
        return bool(self._controller().on_tool_update(**payload))

    async def on_completed(self, **payload: Any) -> bool:
        return bool(await self._controller().on_completed_wait(**payload))


class HermesObserverBridge:
    """Low-cost counters over the stable native hook surface; never renders or suppresses anything."""

    @staticmethod
    def on_stream_start(**payload: Any) -> None:
        metrics.increment("hermes.stream.start")
        if payload.get("surface") in {"feishu", "lark"}:
            metrics.increment("hermes.stream.feishu_start")

    @staticmethod
    def on_stream_delta(**payload: Any) -> None:
        metrics.increment(f"hermes.stream.delta.{'reasoning' if payload.get('kind') == 'reasoning' else 'text'}")

    @staticmethod
    def on_stream_end(**payload: Any) -> None:
        metrics.increment("hermes.stream.end")
        if payload.get("finished") is False or payload.get("error"):
            metrics.increment("hermes.stream.error")

    @staticmethod
    def on_interim_message(**payload: Any) -> None:
        metrics.increment("hermes.stream.interim")
        if payload.get("already_streamed"):
            metrics.increment("hermes.stream.interim_deduplicated")

    @staticmethod
    def post_tool_call(**payload: Any) -> None:
        metrics.increment("hermes.tool.completed")
        status = str(payload.get("status") or "").strip().lower()
        if status in {"error", "failed", "blocked"} or payload.get("error_type"):
            metrics.increment("hermes.tool.failed")

    @staticmethod
    def agent_loop_stopped(**payload: Any) -> None:
        metrics.increment("hermes.agent.stopped")

    @staticmethod
    def pre_approval_request(**payload: Any) -> None:
        metrics.increment("hermes.approval.requested")

    @staticmethod
    def post_approval_response(**payload: Any) -> None:
        metrics.increment("hermes.approval.resolved")
        choice = str(payload.get("choice") or "").strip().lower()
        if choice in {"timeout", "deny"}:
            metrics.increment(f"hermes.approval.{choice}")


def _valid_hooks() -> frozenset[str] | None:
    try:
        return frozenset(importlib.import_module("hermes_cli.plugins").VALID_HOOKS)
    except (ImportError, AttributeError):
        return None


def _supported(names: tuple[str, ...]) -> tuple[str, ...]:
    valid = _valid_hooks()
    return names if valid is None else tuple(n for n in names if n in valid)


def session_key() -> str | None:
    """The one Gateway session key bound to this context, or None when absent or ambiguous.

    Hermes binds these ContextVars in Gateway/tool worker threads; the process-env fallback of its
    ``get_current_session_key`` is deliberately not used in a multiplexed observer.
    """
    keys = {
        value
        for var, value in copy_context().items()
        if var.name in _SESSION_VARS and isinstance(value, str) and value
    }
    return next(iter(keys)) if len(keys) == 1 else None


def observe_telemetry(event: str, payload: dict[str, Any]) -> None:
    """Forward one provider/auxiliary event to ``details.observe``; never raises."""
    try:
        from hermes_lark_streaming import details

        observe: Callable[..., Any] | None = getattr(details, "observe", None)
        if not callable(observe):
            return
        key = session_key()
        if key is None:
            metrics.increment("telemetry.skip.context")
        observe(event, payload, session_key=key)
        metrics.increment("telemetry.forwarded")
    except ImportError:
        return
    except Exception:
        # Do not log payloads or exception text: provider data may contain secrets.
        _logger.debug("telemetry observer skipped an incompatible event")


def execution_observer(*, next_call: Callable[[], Any], **payload: Any) -> Any:
    """Transparent ``llm_execution`` middleware frame; provider execution stays single-use.

    Requested controls only: later middleware may still rewrite them. Failures of ``next_call`` propagate.
    """
    observe_telemetry("llm_execution", payload)
    return next_call()


def register_execution(context: object) -> bool:
    register_middleware = getattr(context, "register_middleware", None)
    if not callable(register_middleware):
        return False
    try:
        if "llm_execution" not in importlib.import_module("hermes_cli.middleware").VALID_MIDDLEWARE:
            return False
        register_middleware("llm_execution", execution_observer)
    except Exception:
        _logger.debug("execution observer not registered")
        return False
    return True


def _register_hooks(context: object, hooks: dict[str, Callable[..., Any]]) -> tuple[str, ...]:
    register_hook = getattr(context, "register_hook", None)
    if not callable(register_hook):
        return ()
    registered: list[str] = []
    for name in _supported(tuple(hooks)):
        try:
            register_hook(name, hooks[name])
        except Exception:
            _logger.warning("native Hermes hook registration failed: %s", name, exc_info=True)
        else:
            registered.append(name)
    return tuple(registered)


def _telemetry_callback(event: str) -> Callable[..., None]:
    def callback(**payload: Any) -> None:
        observe_telemetry(event, payload)

    return callback


def register_observers(context: object, bridge: HermesObserverBridge | None = None) -> tuple[str, ...]:
    bridge = bridge or HermesObserverBridge()
    registered = _register_hooks(context, {name: getattr(bridge, name) for name in OBSERVER_HOOKS})
    if registered:
        metrics.increment("hermes.native_hooks.registered", len(registered))
    return registered


def register_telemetry(context: object) -> tuple[str, ...]:
    register_execution(context)
    return _register_hooks(context, {name: _telemetry_callback(name) for name in TELEMETRY_HOOKS})


def try_register(context: object, renderer: object | None = None) -> bool:
    """Register a native renderer when Hermes offers one; otherwise observers. True means renderer owns delivery."""
    register_renderer = getattr(context, PROTOCOL_METHOD, None)
    if callable(register_renderer):
        register_renderer("feishu", renderer or NativeStreamingRenderer())
        metrics.increment("hermes.native_renderer.registered")
        return True
    register_observers(context)
    return False


def register(context: object) -> None:
    """Plugin ``register(ctx)`` entry: native observers plus telemetry. Side-effect free beyond registration."""
    try_register(context)
    register_telemetry(context)


def capability(context: object | None = None) -> dict[str, Any]:
    renderer = bool(context is not None and callable(getattr(context, PROTOCOL_METHOD, None)))
    observer = bool(context is not None and callable(getattr(context, "register_hook", None)))
    return {
        "strategy": "native-renderer" if renderer else "native-observer+ast" if observer else "ast",
        "protocol": PROTOCOL_METHOD,
        "observer_hooks": list(OBSERVER_HOOKS) if observer else [],
    }


def runtime_capability() -> dict[str, Any]:
    """Inspect the installed Hermes hook registry without importing plugin manager state."""
    valid = _valid_hooks()
    if valid is None:
        return {"available": False, "observer_hooks": [], "missing": list(OBSERVER_HOOKS)}
    available = [n for n in OBSERVER_HOOKS if n in valid]
    return {
        "available": bool(available),
        "observer_hooks": available,
        "missing": [n for n in OBSERVER_HOOKS if n not in valid],
    }
