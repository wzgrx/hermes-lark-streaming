"""Crash-safe idempotency ledger for user-visible Feishu deliveries.

The ledger deliberately stores no message bodies, credentials, user ids, or chat ids.  A
caller-supplied logical key is reduced to a SHA-256 fingerprint before it reaches disk.  Card and
message ids are retained because they are required to resume an interrupted attach operation.
The file is local operator state, created with mode 0600 and updated atomically.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import hermes_home

# A Gateway and cron worker can touch the same ledger in separate processes. A
# per-instance RLock alone does not protect the read/modify/replace sequence.
_PROCESS_LOCK = threading.RLock()


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import importlib

            msvcrt = importlib.import_module("msvcrt")
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    NOT_SENT = "not_sent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeliveryEntry:
    key: str
    operation: str
    request_uuid: str
    status: DeliveryStatus
    created_at: float
    updated_at: float
    attempt: int = 1
    card_id: str = ""
    message_id: str = ""
    error_code: int = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DeliveryEntry | None:
        try:
            return cls(
                key=str(payload["key"]),
                operation=str(payload["operation"]),
                request_uuid=str(payload["request_uuid"]),
                status=DeliveryStatus(str(payload["status"])),
                created_at=float(payload["created_at"]),
                updated_at=float(payload["updated_at"]),
                attempt=max(1, int(payload.get("attempt", 1))),
                card_id=str(payload.get("card_id", "")),
                message_id=str(payload.get("message_id", "")),
                error_code=int(payload.get("error_code", 0)),
            )
        except (KeyError, TypeError, ValueError):
            return None


class DeliveryLedger:
    """Bounded, atomic delivery state shared across Gateway restarts."""

    SCHEMA = 1

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_entries: int = 1024,
        retention_sec: int = 7 * 24 * 60 * 60,
    ) -> None:
        self._path = path
        self.max_entries = max(32, int(max_entries))
        self.retention_sec = max(3600, int(retention_sec))
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path or hermes_home() / "state" / "hermes-lark-streaming-delivery.json"

    @staticmethod
    def fingerprint(logical_key: str) -> str:
        return hashlib.sha256(logical_key.encode("utf-8", errors="replace")).hexdigest()

    def _read(self) -> dict[str, DeliveryEntry]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        rows = raw.get("entries", {}) if isinstance(raw, dict) else {}
        if not isinstance(rows, dict):
            return {}
        parsed: dict[str, DeliveryEntry] = {}
        for key, payload in rows.items():
            if not isinstance(payload, dict):
                continue
            entry = DeliveryEntry.from_dict(payload)
            if entry is not None and entry.key == key:
                parsed[key] = entry
        return parsed

    def _write(self, entries: dict[str, DeliveryEntry]) -> None:
        now = time.time()
        kept = [entry for entry in entries.values() if now - entry.updated_at <= self.retention_sec]
        kept.sort(key=lambda entry: entry.updated_at, reverse=True)
        kept = kept[: self.max_entries]
        payload = {
            "schema": self.SCHEMA,
            "updated_at": now,
            "entries": {entry.key: {**asdict(entry), "status": entry.status.value} for entry in kept},
        }
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            with contextlib.suppress(OSError):
                path.chmod(0o600)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        with _PROCESS_LOCK, self._lock, _file_lock(self.path):
            yield

    def get(self, logical_key: str) -> DeliveryEntry | None:
        key = self.fingerprint(logical_key)
        with self._transaction():
            return self._read().get(key)

    def begin(self, logical_key: str, operation: str, *, retry_not_sent: bool = True) -> DeliveryEntry:
        """Return the stable request UUID for one logical delivery.

        Pending, unknown, and delivered rows are resumed.  A confirmed ``not_sent`` row starts a
        fresh attempt by default because the previous UUID was conclusively rejected.
        """
        key = self.fingerprint(logical_key)
        now = time.time()
        with self._transaction():
            entries = self._read()
            previous = entries.get(key)
            if previous is not None and not (retry_not_sent and previous.status is DeliveryStatus.NOT_SENT):
                return previous
            attempt = previous.attempt + 1 if previous is not None else 1
            entry = DeliveryEntry(
                key=key,
                operation=operation,
                request_uuid=uuid.uuid4().hex,
                status=DeliveryStatus.PENDING,
                created_at=previous.created_at if previous is not None else now,
                updated_at=now,
                attempt=attempt,
            )
            entries[key] = entry
            self._write(entries)
            return entry

    def update(
        self,
        logical_key: str,
        *,
        status: DeliveryStatus | None = None,
        card_id: str | None = None,
        message_id: str | None = None,
        error_code: int | None = None,
    ) -> DeliveryEntry:
        key = self.fingerprint(logical_key)
        with self._transaction():
            entries = self._read()
            previous = entries.get(key)
            if previous is None:
                now = time.time()
                previous = DeliveryEntry(
                    key=key,
                    operation="unknown",
                    request_uuid=uuid.uuid4().hex,
                    status=DeliveryStatus.PENDING,
                    created_at=now,
                    updated_at=now,
                )
            updated = DeliveryEntry(
                key=previous.key,
                operation=previous.operation,
                request_uuid=previous.request_uuid,
                status=status or previous.status,
                created_at=previous.created_at,
                updated_at=time.time(),
                attempt=previous.attempt,
                card_id=previous.card_id if card_id is None else card_id,
                message_id=previous.message_id if message_id is None else message_id,
                error_code=previous.error_code if error_code is None else int(error_code),
            )
            entries[key] = updated
            self._write(entries)
            return updated

    def card_created(self, logical_key: str, card_id: str) -> DeliveryEntry:
        return self.update(logical_key, card_id=card_id)

    def delivered(self, logical_key: str, *, card_id: str, message_id: str) -> DeliveryEntry:
        return self.update(
            logical_key,
            status=DeliveryStatus.DELIVERED,
            card_id=card_id,
            message_id=message_id,
            error_code=0,
        )

    def failed(self, logical_key: str, status: DeliveryStatus, *, error_code: int = 0) -> DeliveryEntry:
        if status not in {DeliveryStatus.NOT_SENT, DeliveryStatus.UNKNOWN}:
            raise ValueError("failed delivery status must be not_sent or unknown")
        return self.update(logical_key, status=status, error_code=error_code)

    def summary(self) -> dict[str, Any]:
        with self._transaction():
            entries = self._read()
        counts = {status.value: 0 for status in DeliveryStatus}
        for entry in entries.values():
            counts[entry.status.value] += 1
        return {
            "schema": self.SCHEMA,
            "path": str(self.path),
            "entries": len(entries),
            "counts": counts,
        }


delivery_ledger = DeliveryLedger()
