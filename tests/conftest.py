from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hermes_lark_streaming.delivery import DeliveryLedger
from hermes_lark_streaming.metrics import metrics


@pytest.fixture(autouse=True)
def isolate_usage_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Observers in tests must never persist synthetic events in the live usage ledger."""
    from hermes_lark_streaming.footer import history

    monkeypatch.setattr(history, "default_path", lambda: tmp_path / "card-usage.sqlite3")


@pytest.fixture(autouse=True)
def isolate_metrics_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep card/Feishu tests from replacing the live gateway metrics snapshot."""
    monkeypatch.setattr(metrics, "_path", tmp_path / "metrics.json")
    monkeypatch.setattr(metrics, "_role", "gateway")


@pytest.fixture(autouse=True)
def isolate_delivery_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DeliveryLedger:
    """Prevent tests from reading or mutating the operator's live delivery ledger."""
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    delivery_module = sys.modules["hermes_lark_streaming.delivery"]
    monkeypatch.setattr(delivery_module, "delivery_ledger", ledger)
    for name in (
        "hermes_lark_streaming.streaming.controller",
        "hermes_lark_streaming.doctor",
    ):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "delivery_ledger", ledger)
    return ledger
