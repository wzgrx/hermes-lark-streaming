"""Per-card write channel: one writer at a time, strictly increasing sequence, commit on success."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class CardChannel:
    """Owns a CardKit ``card_id`` with its sequence counter and write lock.

    The create call consumes sequence 1, so the first write uses 2. A write that fails leaves the counter
    untouched, which lets the caller retry with the same sequence (and the same idempotency UUID).
    """

    def __init__(self, card_id: str, *, sequence: int = 1) -> None:
        self.card_id = card_id
        self._sequence = sequence
        self._lock = asyncio.Lock()
        self.streaming = True

    @property
    def sequence(self) -> int:
        """Last committed sequence."""
        return self._sequence

    async def write(self, op: Callable[[int], Awaitable[T]]) -> T:
        """Run ``op(next_sequence)`` exclusively; commit the sequence only if it returns normally."""
        async with self._lock:
            seq = self._sequence + 1
            result = await op(seq)
            self._sequence = seq
            return result

    async def advance(self) -> int:
        """Burn one sequence after an ambiguous failure so a changed payload cannot collide with it."""
        async with self._lock:
            self._sequence += 1
            return self._sequence
