"""Data layer for the card footer and the details panel; produces ``card.model`` values only."""

from __future__ import annotations

from .collector import DetailsCollector
from .config import DetailsConfig
from .ledger import UsageLedger, observe_history
from .sections import build_sections
from .telemetry import TurnTelemetry

__all__ = [
    "DetailsCollector",
    "DetailsConfig",
    "TurnTelemetry",
    "UsageLedger",
    "build_sections",
    "observe_history",
]
