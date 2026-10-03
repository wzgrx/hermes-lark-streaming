"""Operational warnings use current-process counters without changing receipts."""
from __future__ import annotations

import json
import time

import pytest

from hermes_lark_streaming import doctor
from hermes_lark_streaming import metrics as metrics_module


def snapshot(monkeypatch, counters, status="current"):
    path = metrics_module.metrics.path
    path.write_text(json.dumps({"schema": 1, "process_role": "gateway", "counters": counters}))
    monkeypatch.setattr(metrics_module, "gateway_snapshot_status", lambda value: status)
    return path


def codes(report):
    return {item["code"] for item in report["warnings"]}


def test_current_api_errors_are_visible_without_double_counting_code_buckets(monkeypatch):
    path = snapshot(monkeypatch, {"api.cardkit_update.error": 2, "api.cardkit_update.error_code.300309": 2,
                                  "api.cardkit_update.success": 4, "card.completed": 1})
    before = path.read_bytes()
    report = doctor.build_report()
    assert "api_errors_recorded" in codes(report)
    assert report["metrics"]["activity"]["api_errors"] == 2
    assert report["metrics"]["activity"]["completed_cards"] == 1
    detail = next(w["detail"] for w in report["warnings"] if w["code"] == "api_errors_recorded")
    assert "not proof of a current outage" in detail
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", ["stale", "unverified"])
def test_historical_errors_are_not_current_activity(monkeypatch, status):
    snapshot(monkeypatch, {"api.cardkit_update.error": 100, "card.completion_failed": 3}, status)
    report = doctor.build_report()
    assert "api_errors_recorded" not in codes(report)
    assert "card_completion_failures_recorded" not in codes(report)
    assert report["metrics"]["activity"]["api_errors"] is None


@pytest.mark.parametrize("counter,warning", [
    ("card.completion_failed", "card_completion_failures_recorded"),
    ("card.text_fallback", "card_text_fallbacks_recorded"),
])
def test_current_completion_and_fallback_events_are_visible(monkeypatch, counter, warning):
    snapshot(monkeypatch, {counter: 1})
    assert warning in codes(doctor.build_report())


@pytest.mark.parametrize("counters", [None, [], {"api.x.error": -1}, {"api.x.error": True}, {"api.x.error": "2"}])
def test_bad_counter_shapes_are_unverified_not_zero_success(monkeypatch, counters):
    path = snapshot(monkeypatch, counters)
    before = path.read_bytes()
    report = doctor.build_report()
    assert "metrics_counters_invalid" in codes(report)
    assert report["metrics"]["activity"]["api_errors"] is None
    assert path.read_bytes() == before


def test_expired_pending_receipt_warns_without_reclaim_or_resend(monkeypatch):
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 4000)
    doctor.delivery_ledger.begin("expired-test-receipt", "card.reply")
    monkeypatch.setattr(time, "time", lambda: now)
    before = doctor.delivery_ledger.path.read_bytes()
    report = doctor.build_report()
    assert "delivery_pending_expired" in codes(report)
    assert doctor.delivery_ledger.path.read_bytes() == before


def test_pending_inside_window_has_no_expired_warning():
    doctor.delivery_ledger.begin("active-test-receipt", "card.reply")
    assert "delivery_pending_expired" not in codes(doctor.build_report())


def test_current_success_is_distinct_from_no_measurement(monkeypatch):
    snapshot(monkeypatch, {"card.completed": 1, "api.cardkit_update.success": 1})
    report = doctor.build_report()
    assert report["metrics"]["activity"]["api_errors"] == 0
    assert report["metrics"]["activity"]["completed_cards"] == 1
    assert "api_errors_recorded" not in codes(report)
