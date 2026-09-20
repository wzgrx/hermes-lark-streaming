from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hermes_lark_streaming.delivery import DeliveryLedger


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
