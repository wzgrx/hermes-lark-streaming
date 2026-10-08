"""Single-writer throttle for card updates: at most one flush in flight, coalesced to ~100ms."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from .telemetry import metrics

_logger = logging.getLogger("hermes_lark_streaming")

CARDKIT_INTERVAL_SEC = 0.100  # CardKit streaming refresh interval
LONG_GAP_SEC = 2.000  # idle longer than this counts as a fresh burst
BATCH_AFTER_GAP_SEC = 0.300  # wait this long after an idle gap so the first flush carries more content

FlushFn = Callable[[], Awaitable[None]]


class Flusher:
    """Decides when ``do_flush`` runs; knows nothing about Feishu.

    Flushes are serialized (a request during a flush sets a reflush flag), so together with the per-card
    :class:`CardChannel` lock there is exactly one writer per card.
    """

    def __init__(self, interval_sec: float = CARDKIT_INTERVAL_SEC, *, loop: asyncio.AbstractEventLoop | None = None):
        self._interval = interval_sec
        self._adaptive = False
        self._min_interval = interval_sec
        self._max_interval = max(interval_sec, 1.5)
        self._success_streak = 0
        self._latency_ewma_ms = 0.0
        self._flush_in_progress = False
        self._needs_reflush = False
        self._pending_timer: asyncio.TimerHandle | None = None
        self._last_update_time = 0.0
        self._completed = False
        self._ready = False
        self._waiters: list[asyncio.Future[None]] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._fn: FlushFn | None = None  # the last flush function, for delayed retries

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def interval_sec(self) -> float:
        return self._interval

    @interval_sec.setter
    def interval_sec(self, value: float) -> None:
        self._interval = value

    @property
    def last_update_time(self) -> float:
        return self._last_update_time

    def set_ready(self, ready: bool) -> None:
        """Mark the card message as attached; flushing is a no-op until then."""
        self._ready = ready
        if ready:
            self._last_update_time = time.monotonic()

    def schedule_update(self, do_flush: FlushFn) -> None:
        """Request a throttled flush."""
        self._fn = do_flush
        if self._completed or not self._ready:
            return
        elapsed = time.monotonic() - self._last_update_time
        if elapsed >= self._interval:
            if elapsed > LONG_GAP_SEC:
                if self._pending_timer is None:
                    self._schedule(BATCH_AFTER_GAP_SEC, do_flush)
            else:
                self._spawn(do_flush)
        elif self._pending_timer is None:
            self._schedule(self._interval - elapsed, do_flush)

    async def flush_now(self, do_flush: FlushFn) -> None:
        """Flush immediately and wait for it."""
        self._fn = do_flush
        if self._completed or not self._ready:
            return
        self._cancel_timer()
        await self._do_flush(do_flush)

    async def wait_for_flush(self) -> None:
        """Wait for the in-flight flush, if any."""
        if not self._flush_in_progress:
            return
        future: asyncio.Future[None] = self._loop.create_future()
        self._waiters.append(future)
        await future

    def mark_completed(self) -> None:
        """Accept no further updates and release waiters."""
        self._completed = True
        self._cancel_timer()
        self._release_waiters()

    def request_reflush(self) -> None:
        """Run another flush right after the current one (used when a flush self-heals a server race)."""
        if not self._completed:
            self._needs_reflush = True

    def request_reflush_after(self, delay: float) -> None:
        """Flush again once a backoff has passed, even if no new content arrives meanwhile."""
        if self._completed or self._fn is None or self._pending_timer is not None:
            return
        self._schedule(max(delay, self._interval), self._fn)

    def configure_adaptive(self, *, enabled: bool, min_ms: float, max_ms: float) -> None:
        """Enable back-pressure between ``min_ms`` and ``max_ms`` (public values are milliseconds)."""
        self._adaptive = enabled
        self._min_interval = max(0.05, min_ms / 1000.0)
        self._max_interval = max(self._min_interval, max_ms / 1000.0)
        self._interval = min(max(self._interval, self._min_interval), self._max_interval)

    def record_failure(self, *, rate_limited: bool = False) -> None:
        if not self._adaptive:
            return
        factor = 2.0 if rate_limited else 1.35
        self._interval = min(self._max_interval, max(self._min_interval, self._interval * factor))
        self._success_streak = 0
        metrics.increment("backpressure.rate_limited" if rate_limited else "backpressure.failure")

    def adaptive_snapshot(self) -> dict[str, float | int | bool]:
        return {
            "enabled": self._adaptive,
            "current_ms": round(self._interval * 1000, 2),
            "min_ms": round(self._min_interval * 1000, 2),
            "max_ms": round(self._max_interval * 1000, 2),
            "latency_ewma_ms": round(self._latency_ewma_ms, 2),
            "success_streak": self._success_streak,
        }

    def _record_success(self, elapsed_ms: float) -> None:
        ewma = self._latency_ewma_ms
        self._latency_ewma_ms = elapsed_ms if not ewma else ewma * 0.8 + elapsed_ms * 0.2
        metrics.observe("flush", elapsed_ms)
        if not self._adaptive:
            return
        self._success_streak += 1
        if elapsed_ms > self._interval * 2000:
            self._interval = min(self._max_interval, self._interval * 1.2)
            self._success_streak = 0
        elif self._success_streak >= 5:
            self._interval = max(self._min_interval, self._interval * 0.9)
            self._success_streak = 0

    def _spawn(self, do_flush: FlushFn) -> None:
        self._pending_timer = None
        coro: Coroutine[Any, Any, None] = self._do_flush(do_flush)
        task = self._loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _schedule(self, delay: float, do_flush: FlushFn) -> None:
        self._cancel_timer()
        self._pending_timer = self._loop.call_later(delay, self._spawn, do_flush)

    def _cancel_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None

    def _release_waiters(self) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    async def _do_flush(self, do_flush: FlushFn) -> None:
        if self._completed or self._flush_in_progress:
            self._needs_reflush = True
            return
        self._flush_in_progress = True
        self._needs_reflush = False
        started = time.monotonic()
        succeeded = False
        try:
            await do_flush()
            succeeded = True
        except Exception:
            self.record_failure()
            metrics.increment("flush.error")
            _logger.debug("flush error suppressed", exc_info=True)
        finally:
            if succeeded:
                metrics.increment("flush.success")
                self._record_success((time.monotonic() - started) * 1000)
            self._flush_in_progress = False
            self._last_update_time = time.monotonic()
            self._release_waiters()
        if self._needs_reflush and not self._completed:
            self._needs_reflush = False
            self._spawn(do_flush)
