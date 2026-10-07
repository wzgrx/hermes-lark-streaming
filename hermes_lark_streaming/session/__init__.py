"""Per-message card sessions and the controller facade used by the hooks layer."""

from __future__ import annotations

from typing import Any

from .controller import Controller, get_controller

__all__ = ["Controller", "get_controller", "observe"]


def observe(event: str, payload: dict[str, Any], *, session_key: str | None = None) -> bool:
    """Route a provider/auxiliary request event to the active turn and the usage ledger."""
    return get_controller().observe(event, payload, session_key=session_key)
