"""Thread-safe, privacy-preserving runtime metrics for the streaming plugin."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

_logger = logging.getLogger("hermes_lark_streaming")


def _process_start_time(pid: int) -> int | None:
    """Linux process fingerprint, comparable with Hermes gateway_state.json."""
    try:
        return int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21])
    except (IndexError, OSError, ValueError):
        return None


def _gateway_runtime_identity() -> tuple[int, int] | None:
    """Ask the host to verify the live Gateway, including its PID-reuse guard."""
    try:
        from gateway.status import get_process_start_time, live_gateway_pid_for_home  # type: ignore[import-not-found]

        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        pid = live_gateway_pid_for_home(home)
        start_time = get_process_start_time(pid) if pid is not None else None
    except Exception:
        return None
    return (pid, start_time) if pid is not None and start_time is not None else None


def gateway_snapshot_status(snapshot: dict[str, Any]) -> str:
    """Classify a persisted snapshot without presenting an old PID as current."""
    pid = snapshot.get("pid")
    start_time = snapshot.get("process_start_time")
    if type(pid) is not int or type(start_time) is not int:
        return "unverified"
    identity = _gateway_runtime_identity()
    if identity is None:
        return "unverified"
    return "current" if identity == (pid, start_time) else "stale"


class MetricsStore:
    """Small in-process counter/latency store with an atomic JSON snapshot.

    Labels are intentionally unsupported: message, chat, card and credential values must
    never reach the metrics file.  The snapshot is therefore safe to attach to a bug report.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._role = "gateway"
        self._lock = threading.Lock()
        self._persist_lock = threading.Lock()
        self._last_persist_at = float("-inf")
        self._last_persist_path: Path | None = None
        self._started_at = time.time()
        self._counters: dict[str, int] = defaultdict(int)
        self._latency: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0, "ewma_ms": 0.0}
        )

    @property
    def path(self) -> Path:
        return self.path_for_role(self._role)

    def set_role(self, role: str) -> None:
        """Select this process's snapshot before it begins handling events."""
        if role not in {"gateway", "sidecar"}:
            raise ValueError("unknown metrics process role")
        with self._persist_lock:
            self._role = role

    def path_for_role(self, role: str) -> Path:
        if role not in {"gateway", "sidecar"}:
            raise ValueError("unknown metrics process role")
        if self._path is not None:
            base = self._path
        else:
            home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
            base = home / "state" / "hermes-lark-streaming-metrics.json"
        if role == "gateway":
            return base
        return base.with_name(f"{base.stem}-sidecar{base.suffix}")

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, elapsed_ms: float) -> None:
        value = max(0.0, float(elapsed_ms))
        with self._lock:
            bucket = self._latency[name]
            bucket["count"] += 1
            bucket["total_ms"] += value
            bucket["max_ms"] = max(bucket["max_ms"], value)
            bucket["ewma_ms"] = value if bucket["count"] == 1 else bucket["ewma_ms"] * 0.8 + value * 0.2

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latency = {
                name: {
                    "count": int(values["count"]),
                    "avg_ms": round(values["total_ms"] / values["count"], 2) if values["count"] else 0.0,
                    "max_ms": round(values["max_ms"], 2),
                    "ewma_ms": round(values["ewma_ms"], 2),
                }
                for name, values in sorted(self._latency.items())
            }
            return {
                "schema": 1,
                "process_role": self._role,
                "pid": os.getpid(),
                "process_start_time": _process_start_time(os.getpid()),
                "started_at": self._started_at,
                "updated_at": time.time(),
                "uptime_sec": round(time.time() - self._started_at, 3),
                "counters": dict(sorted(self._counters.items())),
                "latency": latency,
            }

    def load_persisted(self, *, role: str | None = None) -> dict[str, Any] | None:
        selected_role = role or self._role
        try:
            payload = json.loads(self.path_for_role(selected_role).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        # Legacy snapshots had no owner, so a sidecar may have replaced a
        # gateway snapshot at the old shared path. Do not report it as a
        # verified gateway measurement after the role split.
        return payload if isinstance(payload, dict) and payload.get("process_role") == selected_role else None

    def _persist_unlocked(self) -> Path:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        staged: Path | None = None
        try:
            # Multiple processes or threads may still target one role path.
            # A fixed .tmp path lets one writer rename the other's staging file.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f"{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                staged = Path(handle.name)
                handle.write(json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n")
            os.replace(staged, path)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        return path

    def persist(self) -> Path:
        """Publish a terminal snapshot even when a live throttle is active."""
        with self._persist_lock:
            path = self._persist_unlocked()
            self._last_persist_at = time.monotonic()
            self._last_persist_path = path
            return path

    def persist_throttled(self, *, min_interval_sec: float = 10.0) -> bool:
        """Best-effort live snapshot without slowing a card behind another writer."""
        if not self._persist_lock.acquire(blocking=False):
            return False
        try:
            path = self.path
            now = time.monotonic()
            if path == self._last_persist_path and now - self._last_persist_at < max(0.0, min_interval_sec):
                return False
            try:
                self._persist_unlocked()
            except Exception:
                _logger.warning("CardKit live metrics snapshot failed", exc_info=True)
                return False
            self._last_persist_at = time.monotonic()
            self._last_persist_path = path
            return True
        finally:
            self._persist_lock.release()


metrics = MetricsStore()


class timed_operation:
    """Context manager for a single API operation."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.started = 0.0

    def __enter__(self) -> timed_operation:
        self.started = time.monotonic()
        metrics.increment(f"{self.name}.attempt")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        metrics.observe(self.name, (time.monotonic() - self.started) * 1000)
        metrics.increment(f"{self.name}.error" if exc_type else f"{self.name}.success")
