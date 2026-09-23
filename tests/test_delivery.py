from __future__ import annotations

import json
import multiprocessing
import stat
import time
from pathlib import Path
from threading import Thread

import pytest

from hermes_lark_streaming.delivery import DeliveryLedger, DeliveryStatus
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
    monkeypatch.setattr(time, "time", lambda: now)
    for index in range(40):
        ledger.begin(f"fresh:{index}", "card.reply")
    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 32
    assert ledger.fingerprint("expired") not in payload["entries"]


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
