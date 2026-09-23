"""Thread-safe, privacy-preserving runtime metrics for the streaming plugin."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


class MetricsStore:
    """Small in-process counter/latency store with an atomic JSON snapshot.

    Labels are intentionally unsupported: message, chat, card and credential values must
    never reach the metrics file.  The snapshot is therefore safe to attach to a bug report.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._counters: dict[str, int] = defaultdict(int)
        self._latency: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0.0, "total_ms": 0.0, "max_ms": 0.0, "ewma_ms": 0.0}
        )

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        return home / "state" / "hermes-lark-streaming-metrics.json"

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
                "started_at": self._started_at,
                "updated_at": time.time(),
                "uptime_sec": round(time.time() - self._started_at, 3),
                "counters": dict(sorted(self._counters.items())),
                "latency": latency,
            }

    def load_persisted(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def persist(self) -> Path:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        staged: Path | None = None
        try:
            # Gateway and optional sidecar processes can persist concurrently.
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
