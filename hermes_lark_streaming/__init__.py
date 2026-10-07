"""Hermes Lark Streaming: streaming Feishu/Lark CardKit cards for Hermes Gateway."""

from __future__ import annotations

__version__ = "1.0.0.dev0"


def register(ctx: object) -> None:
    """Hermes plugin entry point: register observer hooks, telemetry and the execution middleware."""
    from .hooks.native import register as register_native

    register_native(ctx)
