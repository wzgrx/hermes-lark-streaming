"""Doctor retains diagnostics when the durable delivery ledger needs inspection."""

from __future__ import annotations

import json

from hermes_lark_streaming import doctor
from hermes_lark_streaming.delivery import DeliveryLedger


def test_doctor_reports_corrupt_ledger_without_mutating_it(tmp_path, monkeypatch, capsys):
    path = tmp_path / "delivery.json"
    malformed = b'{"schema":1,"entries":'
    path.write_bytes(malformed)
    monkeypatch.setattr(doctor, "delivery_ledger", DeliveryLedger(path))

    report = doctor.build_report()
    assert report["delivery"]["entries"] is None
    assert report["delivery"]["path"] == str(path)
    assert "evidence preserved" in report["delivery"]["error"]
    assert any(
        check["name"] == "delivery-ledger" and not check["ok"]
        for check in report["checks"]
    )
    assert report["ok"] is False
    assert path.read_bytes() == malformed

    assert doctor.print_report(as_json=True) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["delivery"]["entries"] is None
    assert printed["ok"] is False
    assert doctor.print_report(as_json=False) == 1
    assert "inspection required" in capsys.readouterr().out
    assert path.read_bytes() == malformed


def test_doctor_flags_full_unresolved_ledger(tmp_path, monkeypatch):
    ledger = DeliveryLedger(tmp_path / "delivery.json", max_entries=32)
    for index in range(32):
        ledger.begin(f"pending:{index}", "card.reply")
    monkeypatch.setattr(doctor, "delivery_ledger", ledger)

    report = doctor.build_report()
    assert report["delivery"]["unresolved_count"] == 32
    assert report["delivery"]["unresolved_capacity_remaining"] == 0
    assert report["ok"] is False
    assert any(
        check["name"] == "delivery-ledger-capacity" and not check["ok"]
        for check in report["checks"]
    )
