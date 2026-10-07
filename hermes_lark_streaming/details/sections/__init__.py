"""Assemble the details panel from independently switchable sections."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ...card.model import Section
from ..config import DetailsConfig
from .accounts import accounts_section
from .resources import resources_section
from .usage import usage_section

Source = Mapping[str, Any] | Callable[[], Mapping[str, Any]] | None

__all__ = ["Source", "build_sections"]


def _resolve(source: Source) -> Mapping[str, Any] | None:
    return source() if callable(source) else source


def build_sections(
    config: DetailsConfig,
    *,
    turn: Source = None,
    history: Source = None,
    host: Source = None,
    accounts: Source = None,
    chat_id: str = "",
    terminal: bool = False,
    now: float | None = None,
) -> tuple[Section, ...]:
    """Up to three sections: usage, resources, accounts.

    Each source is a snapshot mapping or a zero-argument callable returning one (e.g.
    ``telemetry.snapshot``). A section is omitted when its switch is off, the chat is not
    allowed, or (accounts) no snapshot exists; missing values inside a section show 未知.
    """
    if not config.chat_allowed(chat_id):
        return ()
    sections: list[Section] = []
    if config.usage:
        sections.append(
            usage_section(
                _resolve(turn),
                _resolve(history) if config.history else None,
                terminal=terminal,
                show_models=config.show_models,
                pricing=config.pricing,
            )
        )
    if config.resources:
        sections.append(resources_section(_resolve(host)))
    snapshot = _resolve(accounts) if config.accounts_allowed(chat_id) else None
    if snapshot is not None:
        sections.append(accounts_section(snapshot, config.zone, terminal=terminal, now=now))
    return tuple(sections)
