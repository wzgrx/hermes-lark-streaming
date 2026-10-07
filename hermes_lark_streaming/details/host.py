"""Opt-in, coalesced host snapshots (CPU, RAM, disk, NVIDIA GPU). No shell calls on the render path."""

from __future__ import annotations

import asyncio
import shutil
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any


def read_proc(root: Path = Path("/proc")) -> tuple[dict[str, Any], tuple[int, int] | None]:
    result: dict[str, Any] = {}
    cpu = None
    try:
        values = [int(v) for v in (root / "stat").read_text().splitlines()[0].split()[1:9]]
        if len(values) >= 4:
            cpu = sum(values), values[3] + (values[4] if len(values) > 4 else 0)
    except (OSError, ValueError, IndexError):
        pass
    try:
        memory = {
            line.split(":", 1)[0]: int(line.split()[1])
            for line in (root / "meminfo").read_text().splitlines()
            if ":" in line
        }
        total, available = memory["MemTotal"], memory["MemAvailable"]
        if 0 <= available <= total and total > 0:
            result.update(ram_total_gib=total / 1024**2, ram_used_gib=(total - available) / 1024**2)
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        result["processes"] = sum(entry.name.isdecimal() for entry in root.iterdir())
        result["uptime"] = float((root / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        result["scope"] = "WSL" if "microsoft" in (root / "sys/kernel/osrelease").read_text().lower() else "Linux"
    except OSError:
        result["scope"] = "Host"
    return result, cpu


def read_disk(path: str = "/") -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {}
    if usage.total <= 0 or not 0 <= usage.used <= usage.total:
        return {}
    return {"disk_used_gib": usage.used / 1024**3, "disk_total_gib": usage.total / 1024**3}


def parse_gpu(value: str) -> dict[str, Any]:
    # Only device zero is displayed; multi-device selection is deliberately not
    # guessed. Each missing NVIDIA metric stays absent instead of becoming zero.
    result: dict[str, Any] = {}
    parts = value.strip().splitlines()[0].split(",") if value.strip() else []
    for key, raw in zip(("gpu_percent", "gpu_temperature", "gpu_used_gib", "gpu_total_gib"), parts, strict=False):
        try:
            n = float(raw.strip())
        except ValueError:
            continue
        if n != n or n in (float("inf"), float("-inf")) or n < 0:
            continue
        if key == "gpu_percent" and n > 100:
            continue
        result[key] = n / 1024 if key.endswith("gib") else n
    if result.get("gpu_used_gib", 0) > result.get("gpu_total_gib", float("inf")):
        result.pop("gpu_used_gib", None)
    return result


class HostSampler:
    """One bounded sampler task per controller, not per delta or per card."""

    def __init__(self, *, ttl_s: float = 10.0, disk_path: str = "/") -> None:
        self._ttl, self._disk_path = ttl_s, disk_path
        self._cached: dict[str, Any] = {"unavailable": True}
        self._at = float("-inf")
        self._cpu: tuple[int, int] | None = None
        self._task: asyncio.Task[None] | None = None

    def snapshot(self) -> dict[str, Any]:
        return dict(self._cached)

    def request(self) -> None:
        if self._task is None and time.monotonic() - self._at >= self._ttl:
            self._task = asyncio.create_task(self._collect())

    async def finish(self) -> None:
        self.request()
        if self._task is not None:
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._task), 2.0)

    async def _collect(self) -> None:
        process = None
        try:
            # A timed-out to_thread() keeps running. Retain this task's slot
            # until /proc sampling actually exits; finish() bounds the waiter.
            result, cpu = await asyncio.to_thread(read_proc)
            if cpu is not None and self._cpu is not None:
                total, idle = cpu[0] - self._cpu[0], cpu[1] - self._cpu[1]
                if total > 0 and 0 <= idle <= total:
                    result["cpu_percent"] = (total - idle) / total * 100
            self._cpu = cpu
            result.update(await asyncio.to_thread(read_disk, self._disk_path))
            exe = shutil.which("nvidia-smi")
            if exe:
                try:
                    process = await asyncio.create_subprocess_exec(
                        exe,
                        "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                        "--format=csv,noheader,nounits",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    output, _ = await asyncio.wait_for(process.communicate(), 1.0)
                    if process.returncode == 0:
                        result.update(parse_gpu(output[:4096].decode(errors="replace")))
                except (OSError, TimeoutError):
                    pass
            result["sampled_at"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            result["unavailable"] = not all(k in result for k in ("cpu_percent", "gpu_percent", "ram_used_gib"))
            self._cached = result
        except (OSError, TimeoutError):
            self._cached = {"unavailable": True}
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
            self._at = time.monotonic()
            self._task = None
