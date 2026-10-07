"""Feishu/Lark transport: CardKit client, sequencing, throttled flushing, delivery ledger, media."""

from __future__ import annotations

from .channel import CardChannel
from .client import CardKitClient, ClientConfig
from .errors import (
    CardLimitError,
    ElementNotFoundError,
    FeishuAPIError,
    MessageUnavailableError,
    RateLimitedError,
    SequenceConflictError,
    StreamingClosedError,
    classify_delivery_failure,
    sanitize_message,
)
from .flush import Flusher
from .guard import UnavailableGuard
from .images import ImageResolver
from .ledger import DeliveryEntry, DeliveryLedger, DeliveryLedgerError, DeliveryStatus
from .limits import CardInspection, compact_card, inspect_card
from .media import deliver_media_files, extract_media_paths, strip_media_directives

__all__ = [
    "CardChannel",
    "CardInspection",
    "CardKitClient",
    "CardLimitError",
    "ClientConfig",
    "DeliveryEntry",
    "DeliveryLedger",
    "DeliveryLedgerError",
    "DeliveryStatus",
    "ElementNotFoundError",
    "FeishuAPIError",
    "Flusher",
    "ImageResolver",
    "MessageUnavailableError",
    "RateLimitedError",
    "SequenceConflictError",
    "StreamingClosedError",
    "UnavailableGuard",
    "classify_delivery_failure",
    "compact_card",
    "deliver_media_files",
    "extract_media_paths",
    "inspect_card",
    "sanitize_message",
    "strip_media_directives",
]
