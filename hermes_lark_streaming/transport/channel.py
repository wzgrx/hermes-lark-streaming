"""Per-card write channel: one writer at a time, strictly increasing sequence, commit on success."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .errors import FeishuAPIError, SequenceConflictError

T = TypeVar("T")


def _rejected_before_commit(exc: BaseException) -> bool:
    """A Feishu error response (or a local limit check) proves the sequence was not consumed."""
    return isinstance(exc, FeishuAPIError)


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
        """Run ``op(next_sequence)`` exclusively.

        A sequence is spent by any attempt that may have reached Feishu: a timeout or cancellation can land
        after the server committed it, and reusing the number with a new payload would be rejected (300317)
        for every later write. CardKit only needs strictly increasing numbers, so gaps are harmless. A
        conflict means the server is ahead of us: skip forward and try once more.
        """
        async with self._lock:
            for attempt in range(2):
                seq = self._sequence + 1
                try:
                    result = await op(seq)
                except SequenceConflictError:
                    self._sequence = seq + 8
                    if attempt:
                        raise
                    continue
                except BaseException as exc:
                    if not _rejected_before_commit(exc):
                        self._sequence = seq
                    raise
                self._sequence = seq
                return result
            raise AssertionError("unreachable")  # pragma: no cover

    async def advance(self) -> int:
        """Burn one sequence after an ambiguous failure so a changed payload cannot collide with it."""
        async with self._lock:
            self._sequence += 1
            return self._sequence
