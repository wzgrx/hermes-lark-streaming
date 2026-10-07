"""Native Hermes plugin integration and streaming-renderer capability detection.

Hermes 0.21.3 exposes observer-only streaming, tool, approval and interruption hooks. They
are useful for diagnostics and lifecycle reconciliation but cannot claim platform delivery,
so the reversible AST layer remains the card-owner compatibility path. If a future Hermes
context exposes ``register_streaming_renderer``, the renderer protocol takes ownership and
the AST layer can be retired.
"""

from __future__ import annotations

import logging
from typing import Any

from .metrics import metrics

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

_logger = logging.getLogger("hermes_lark_streaming")


class NativeStreamingRenderer:
    """Thin lifecycle adapter; Hermes remains the owner of delivery and interaction state."""

    @staticmethod
    def _controller() -> Any:
        from .controller import get_controller

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
    """Low-cost observers for the stable Hermes 0.21.3 native hook surface.

    The bridge intentionally does not render or suppress messages. Observer callbacks lack
    ``chat_id``/``message_id`` and their return values are ignored by Hermes, so treating them as
    a delivery owner would create duplicate or misrouted cards.
    """

    @staticmethod
    def on_stream_start(**payload: Any) -> None:
        metrics.increment("hermes.stream.start")
        if payload.get("surface") in {"feishu", "lark"}:
            metrics.increment("hermes.stream.feishu_start")

    @staticmethod
    def on_stream_delta(**payload: Any) -> None:
        kind = "reasoning" if payload.get("kind") == "reasoning" else "text"
        metrics.increment(f"hermes.stream.delta.{kind}")

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


def _supported_observer_hooks() -> tuple[str, ...]:
    try:
        from hermes_cli.plugins import VALID_HOOKS  # type: ignore[import-not-found]
    except ImportError:
        return OBSERVER_HOOKS
    return tuple(name for name in OBSERVER_HOOKS if name in VALID_HOOKS)


def _register_observers(context: object, bridge: HermesObserverBridge | None = None) -> tuple[str, ...]:
    register_hook = getattr(context, "register_hook", None)
    if not callable(register_hook):
        return ()
    bridge = bridge or HermesObserverBridge()
    registered: list[str] = []
    for name in _supported_observer_hooks():
        callback = getattr(bridge, name)
        try:
            register_hook(name, callback)
        except Exception:
            _logger.warning("native Hermes hook registration failed: %s", name, exc_info=True)
        else:
            registered.append(name)
    if registered:
        metrics.increment("hermes.native_hooks.registered", len(registered))
    return tuple(registered)


def try_register(context: object, renderer: object | None = None) -> bool:
    register_renderer = getattr(context, PROTOCOL_METHOD, None)
    if callable(register_renderer):
        register_renderer("feishu", renderer or NativeStreamingRenderer())
        metrics.increment("hermes.native_renderer.registered")
        return True
    _register_observers(context)
    return False


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
    available = _supported_observer_hooks()
    if available == OBSERVER_HOOKS:
        try:
            from hermes_cli.plugins import VALID_HOOKS  # type: ignore[import-not-found]
        except ImportError:
            return {"available": False, "observer_hooks": [], "missing": list(OBSERVER_HOOKS)}
    return {
        "available": bool(available),
        "observer_hooks": list(available),
        "missing": [name for name in OBSERVER_HOOKS if name not in VALID_HOOKS],
    }
