"""Cards that are still live (streaming, not yet finalized), kept on disk.

A turn's card is registered once it is attached and removed once its terminal update lands. If the
gateway is killed or crashes mid-turn, nothing would ever close such a card: it would keep its running
look forever. The registry lets the next gateway find those cards and mark them interrupted.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .ledger import hermes_home

_logger = logging.getLogger("hermes_lark_streaming")


OPEN_CARDS_FILE = "hermes-lark-streaming-open-cards.json"


def default_open_cards_path() -> Path:
    return hermes_home() / "state" / OPEN_CARDS_FILE


@dataclass(frozen=True)
class OpenCard:
    card_id: str
    chat_id: str
    message_id: str
    opened_at: float
    attempts: int = 0


class OpenCards:
    """A tiny JSON registry. Only the gateway process writes it; every failure is logged, never raised."""

    MAX_ENTRIES = 256

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path or default_open_cards_path()

    def _read(self) -> dict[str, OpenCard]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            _logger.warning("open card registry unreadable; starting empty", exc_info=True)
            return {}
        cards: dict[str, OpenCard] = {}
        for item in raw.get("cards", []) if isinstance(raw, dict) else []:
            try:
                card = OpenCard(str(item["card_id"]), str(item["chat_id"]), str(item.get("message_id", "")),
                                float(item["opened_at"]), int(item.get("attempts", 0)))
            except (KeyError, TypeError, ValueError):
                continue
            if card.card_id:
                cards[card.card_id] = card
        return cards

    def _write(self, cards: dict[str, OpenCard]) -> None:
        kept = sorted(cards.values(), key=lambda c: c.opened_at, reverse=True)[: self.MAX_ENTRIES]
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps({"cards": [asdict(c) for c in kept]}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    def add(self, card_id: str, chat_id: str, message_id: str) -> None:
        if not card_id:
            return
        try:
            with self._lock:
                cards = self._read()
                if card_id not in cards:
                    cards[card_id] = OpenCard(card_id, chat_id, message_id, time.time())
                    self._write(cards)
        except Exception:
            _logger.warning("open card registry write failed", exc_info=True)

    def remove(self, card_id: str) -> None:
        try:
            with self._lock:
                cards = self._read()
                if cards.pop(card_id, None) is not None:
                    self._write(cards)
        except Exception:
            _logger.warning("open card registry write failed", exc_info=True)

    def orphans(self, live: set[str], *, min_age: float) -> list[OpenCard]:
        """Registered cards no live session owns, older than ``min_age`` seconds."""
        try:
            with self._lock:
                now = time.time()
                return [c for c in self._read().values() if c.card_id not in live and now - c.opened_at >= min_age]
        except Exception:
            _logger.warning("open card registry read failed", exc_info=True)
            return []

    def attempted(self, card: OpenCard, *, give_up_after: int) -> None:
        """Count a failed close; after ``give_up_after`` tries the card is forgotten."""
        try:
            with self._lock:
                cards = self._read()
                if card.card_id in cards:
                    if card.attempts + 1 >= give_up_after:
                        del cards[card.card_id]
                    else:
                        cards[card.card_id] = OpenCard(card.card_id, card.chat_id, card.message_id,
                                                       card.opened_at, card.attempts + 1)
                    self._write(cards)
        except Exception:
            _logger.warning("open card registry write failed", exc_info=True)
