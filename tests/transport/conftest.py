from __future__ import annotations

from collections.abc import Iterator

import pytest

from hermes_lark_streaming.transport import guard
from hermes_lark_streaming.transport.ledger import DeliveryLedger
from hermes_lark_streaming.transport.telemetry import metrics


class RecordingSink:
    def __init__(self) -> None:
        self.counters: list[str] = []

    def increment(self, name: str, value: int = 1) -> None:
        self.counters.append(name)

    def observe(self, name: str, elapsed_ms: float) -> None:
        pass


@pytest.fixture(autouse=True)
def _isolate_globals() -> Iterator[None]:
    guard.clear_unavailable()
    yield
    metrics.set_sink(None)
    guard.clear_unavailable()


@pytest.fixture
def sink() -> RecordingSink:
    recorder = RecordingSink()
    metrics.set_sink(recorder)
    return recorder


@pytest.fixture
def ledger(tmp_path):  # type: ignore[no-untyped-def]
    return DeliveryLedger(tmp_path / "delivery.json")
