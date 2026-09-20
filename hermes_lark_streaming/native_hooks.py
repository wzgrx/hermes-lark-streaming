"""Native Hermes lifecycle-hook capability detection.

AST integration remains the compatibility path until Hermes publishes a stable streaming
renderer API.  As soon as a context exposes the named protocol below, the package entry
point registers directly and install/doctor can report the selected strategy.
"""

from __future__ import annotations

from typing import Any

PROTOCOL_METHOD = "register_streaming_renderer"


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


def try_register(context: object, renderer: object | None = None) -> bool:
    register = getattr(context, PROTOCOL_METHOD, None)
    if not callable(register):
        return False
    register("feishu", renderer or NativeStreamingRenderer())
    return True


def capability(context: object | None = None) -> dict[str, Any]:
    return {
        "strategy": "native" if context is not None and callable(getattr(context, PROTOCOL_METHOD, None)) else "ast",
        "protocol": PROTOCOL_METHOD,
    }
