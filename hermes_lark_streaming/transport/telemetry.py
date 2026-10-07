"""Pluggable metrics sink; transport records counters without owning a metrics store."""

from __future__ import annotations

from typing import Protocol


class MetricsSink(Protocol):
    def increment(self, name: str, value: int = 1) -> None: ...

    def observe(self, name: str, elapsed_ms: float) -> None: ...


class _Metrics:
    """Forwards to the installed sink; a no-op until one is installed."""

    def __init__(self) -> None:
        self._sink: MetricsSink | None = None

    def set_sink(self, sink: MetricsSink | None) -> None:
        self._sink = sink

    def increment(self, name: str, value: int = 1) -> None:
        if self._sink is not None:
            self._sink.increment(name, value)

    def observe(self, name: str, elapsed_ms: float) -> None:
        if self._sink is not None:
            self._sink.observe(name, elapsed_ms)


metrics = _Metrics()
