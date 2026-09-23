from __future__ import annotations

import json
import multiprocessing
import stat
import time
from pathlib import Path
from threading import Thread

import pytest

from hermes_lark_streaming.delivery import DeliveryLedger, DeliveryLedgerError, DeliveryStatus
from hermes_lark_streaming.feishu import CARDKIT_GATEWAY_TIMEOUT, FeishuAPIError, classify_delivery_failure


def _write_concurrent_entries(path: str, worker_id: int, start, results) -> None:
    ledger = DeliveryLedger(Path(path))
    original_write = ledger._write

    def slow_write(entries) -> None:
        time.sleep(0.005)
        original_write(entries)

    ledger._write = slow_write
    start.wait(10)
    for index in range(8):
        ledger.begin(f"worker:{worker_id}:{index}", "card.reply")
    results.put(worker_id)


def _claim_same_delivery(path: str, start, results) -> None:
    start.wait(10)
    entry, should_send = DeliveryLedger(Path(path)).claim_send("cron:same-occurrence", "cron.card")
    results.put((entry.request_uuid, should_send))


def test_atomic_claim_allows_one_send_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "delivery.json"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(target=_claim_same_delivery, args=(str(path), start, results))
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=20)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        assert worker.exitcode == 0
    claims = [results.get(timeout=5) for _ in workers]
    assert len({request_uuid for request_uuid, _ in claims}) == 1
    assert sum(should_send for _, should_send in claims) == 1
    assert DeliveryLedger(path).summary()["counts"]["unknown"] == 1


def test_claim_send_preserves_pending_uuid_and_rotates_only_after_rejection(tmp_path: Path) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    pending = ledger.begin("cron:due", "cron.card")
    claimed, should_send = ledger.claim_send("cron:due", "cron.card")
    assert should_send is True
    assert claimed.request_uuid == pending.request_uuid
    held, should_send = ledger.claim_send("cron:due", "cron.card")
    assert should_send is False
    assert held.request_uuid == pending.request_uuid

    ledger.failed("cron:due", DeliveryStatus.NOT_SENT, error_code=230099)
    retried, should_send = ledger.claim_send("cron:due", "cron.card")
    assert should_send is True
    assert retried.request_uuid != pending.request_uuid
    assert retried.attempt == 2
    ledger.delivered("cron:due", card_id="", message_id="om-card")
    delivered, should_send = ledger.claim_send("cron:due", "cron.card")
    assert should_send is False
    assert delivered.message_id == "om-card"


def test_old_pending_attempt_is_held_after_uuid_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 3301)
    prepared = ledger.begin("stream:old", "card.reply")
    monkeypatch.setattr(time, "time", lambda: now)

    held = ledger.begin("stream:old", "card.reply")
    assert held.status is DeliveryStatus.UNKNOWN
    assert held.request_uuid == prepared.request_uuid
    assert held.created_at == prepared.created_at
    assert DeliveryLedger(ledger.path).get("stream:old") == held


def test_old_pending_cron_claim_is_not_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 3301)
    prepared = ledger.begin("cron:old", "cron.card")
    monkeypatch.setattr(time, "time", lambda: now)

    held, should_send = ledger.claim_send("cron:old", "cron.card")
    assert should_send is False
    assert held.status is DeliveryStatus.UNKNOWN
    assert held.request_uuid == prepared.request_uuid


def test_rejected_retry_gets_new_uuid_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 7200)
    old = ledger.begin("stream:rejected", "card.reply")
    ledger.failed("stream:rejected", DeliveryStatus.NOT_SENT)
    monkeypatch.setattr(time, "time", lambda: now)

    retry = ledger.begin("stream:rejected", "card.reply")
    assert retry.status is DeliveryStatus.PENDING
    assert retry.request_uuid != old.request_uuid
    assert retry.created_at == now


def test_separate_ledger_instances_preserve_threaded_updates(tmp_path: Path) -> None:
    path = tmp_path / "delivery.json"
    threads = [
        Thread(target=lambda worker=worker: [
            DeliveryLedger(path).begin(f"thread:{worker}:{index}", "card.reply")
            for index in range(8)
        ])
        for worker in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert DeliveryLedger(path).summary()["entries"] == 32


def test_gateway_and_cron_processes_preserve_concurrent_updates(tmp_path: Path) -> None:
    path = tmp_path / "delivery.json"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    workers = [
        context.Process(target=_write_concurrent_entries, args=(str(path), worker, start, results))
        for worker in range(4)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=20)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=5) for _ in workers) == list(range(4))
    assert DeliveryLedger(path).summary()["entries"] == 32
    assert stat.S_IMODE(path.with_name("delivery.json.lock").stat().st_mode) == 0o600


def test_stable_uuid_survives_pending_unknown_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "delivery.json"
    first = DeliveryLedger(path).begin("chat:private:message:secret", "card.reply")
    DeliveryLedger(path).failed("chat:private:message:secret", DeliveryStatus.UNKNOWN, error_code=599)
    resumed = DeliveryLedger(path).begin("chat:private:message:secret", "card.reply")
    assert resumed.request_uuid == first.request_uuid
    assert resumed.status is DeliveryStatus.UNKNOWN


def test_confirmed_not_sent_starts_new_attempt(tmp_path: Path) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    first = ledger.begin("message:one", "card.reply")
    ledger.failed("message:one", DeliveryStatus.NOT_SENT, error_code=230099)
    retried = ledger.begin("message:one", "card.reply")
    assert retried.request_uuid != first.request_uuid
    assert retried.attempt == 2
    assert retried.status is DeliveryStatus.PENDING


def test_delivered_entry_resumes_without_losing_entity_ids(tmp_path: Path) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json")
    first = ledger.begin("message:two", "card.reply")
    delivered = ledger.delivered("message:two", card_id="card-1", message_id="om-1")
    resumed = DeliveryLedger(ledger.path).begin("message:two", "card.reply")
    assert resumed == delivered
    assert resumed.request_uuid == first.request_uuid
    assert resumed.status is DeliveryStatus.DELIVERED
    assert (resumed.card_id, resumed.message_id) == ("card-1", "om-1")


def test_ledger_write_error_preserves_prior_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "delivery.json"
    ledger = DeliveryLedger(path)
    ledger.begin("existing", "card.reply")
    original = path.read_bytes()

    def fail_write(_entries) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(ledger, "_write", fail_write)
    with pytest.raises(DeliveryLedgerError, match="evidence preserved"):
        ledger.begin("new", "card.reply")
    assert path.read_bytes() == original


    path = tmp_path / "delivery.json"
    raw = '{"entries":'
    path.write_text(raw)
    ledger = DeliveryLedger(path)
    with pytest.raises(DeliveryLedgerError, match="evidence preserved"):
        ledger.begin("new-message", "card.reply")
    assert path.read_text() == raw


@pytest.mark.parametrize("payload", ["[]", '{"schema": 1, "entries": []}', '{"schema": 2, "entries": {}}'])
def test_invalid_ledger_schema_is_preserved(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "delivery.json"
    path.write_text(payload)
    with pytest.raises(DeliveryLedgerError, match="evidence preserved"):
        DeliveryLedger(path).begin("new-message", "card.reply")
    assert path.read_text() == payload


def test_ledger_hashes_logical_key_and_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "delivery.json"
    logical_key = "chat:oc_sensitive:message:om_sensitive"
    ledger = DeliveryLedger(path)
    ledger.begin(logical_key, "card.reply")
    raw = path.read_text(encoding="utf-8")
    assert logical_key not in raw
    assert "oc_sensitive" not in raw
    assert ledger.fingerprint(logical_key) in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ledger_prunes_old_rows_and_bounds_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json", max_entries=32, retention_sec=3600)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 7200)
    ledger.begin("expired", "card.reply")
    ledger.delivered("expired", card_id="card-old", message_id="om-old")
    monkeypatch.setattr(time, "time", lambda: now)
    for index in range(40):
        key = f"fresh:{index}"
        ledger.begin(key, "card.reply")
        ledger.delivered(key, card_id=f"card-{index}", message_id=f"om-{index}")
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 32
    assert ledger.fingerprint("expired") not in payload["entries"]


def test_unresolved_delivery_evidence_survives_age_and_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json", max_entries=32, retention_sec=3600)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 7200)
    pending = ledger.begin("old-pending", "card.reply")
    unknown = ledger.begin("old-unknown", "cron.card")
    ledger.failed("old-unknown", DeliveryStatus.UNKNOWN)
    monkeypatch.setattr(time, "time", lambda: now)
    for index in range(40):
        key = f"fresh:{index}"
        ledger.begin(key, "card.reply")
        ledger.delivered(key, card_id=f"card-{index}", message_id=f"om-{index}")

    restarted = DeliveryLedger(ledger.path, max_entries=32, retention_sec=3600)
    assert restarted.summary()["entries"] == 32
    assert restarted.get("old-pending").request_uuid == pending.request_uuid
    assert restarted.get("old-unknown").request_uuid == unknown.request_uuid
    assert restarted.get("old-unknown").status is DeliveryStatus.UNKNOWN


def test_summary_reports_unresolved_capacity_and_expired_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json", max_entries=32)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now - 4000)
    ledger.begin("old-pending", "card.reply")
    ledger.begin("old-unknown", "cron.card")
    ledger.failed("old-unknown", DeliveryStatus.UNKNOWN)
    monkeypatch.setattr(time, "time", lambda: now)

    summary = ledger.summary()
    assert summary["unresolved_count"] == 2
    assert summary["unresolved_capacity_remaining"] == 30
    assert summary["oldest_unresolved_age_sec"] == 4000
    assert summary["expired_pending_count"] == 1
    assert summary["counts"]["unknown"] == 1
    assert "old-pending" not in json.dumps(summary)


def test_unresolved_capacity_holds_new_send_without_erasing_evidence(tmp_path: Path) -> None:
    ledger = DeliveryLedger(tmp_path / "delivery.json", max_entries=32)
    for index in range(32):
        ledger.begin(f"pending:{index}", "card.reply")
    original = ledger.path.read_bytes()

    with pytest.raises(DeliveryLedgerError, match="capacity reached"):
        ledger.begin("overflow", "card.reply")

    assert ledger.path.read_bytes() == original
    assert ledger.summary()["counts"]["pending"] == 32


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FeishuAPIError("rejected", code=230099), DeliveryStatus.NOT_SENT),
        (FeishuAPIError("gateway timeout", code=CARDKIT_GATEWAY_TIMEOUT), DeliveryStatus.UNKNOWN),
        (TimeoutError("response lost"), DeliveryStatus.UNKNOWN),
        (FeishuAPIError("no structured code"), DeliveryStatus.UNKNOWN),
    ],
)
def test_delivery_failure_classification(error: BaseException, expected: DeliveryStatus) -> None:
    assert classify_delivery_failure(error) is expected
