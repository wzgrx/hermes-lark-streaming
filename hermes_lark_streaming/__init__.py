"""Hermes Lark Streaming: streaming Feishu/Lark CardKit cards for Hermes Gateway."""

from __future__ import annotations

__version__ = "1.5.2"


def register(ctx: object) -> None:
    """Hermes plugin entry point: register observer hooks, telemetry and the execution middleware."""
    from .compat import register as register_compat
    from .hooks.native import register as register_native

    register_native(ctx)
    register_compat(ctx)
