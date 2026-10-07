"""Resource sampling: /proc parsing, GPU output parsing and sampler ownership. No subprocess is started."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from hermes_lark_streaming.details.host import HostSampler, parse_gpu, read_disk, read_proc


def fake_proc(root, *, release="6.6.0-microsoft-standard-WSL2", meminfo=None):
    (root / "sys/kernel").mkdir(parents=True)
    (root / "sys/kernel/osrelease").write_text(release)
    (root / "stat").write_text("cpu  100 0 50 800 50 0 0 0\n")
    (root / "meminfo").write_text(meminfo or "MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n")
    (root / "uptime").write_text("123.4 456.7\n")
    (root / "42").mkdir()
    (root / "self").mkdir()
    return root


def test_read_proc_wsl(tmp_path):
    result, cpu = read_proc(fake_proc(tmp_path))
    assert result["scope"] == "WSL" and result["processes"] == 1 and result["uptime"] == pytest.approx(123.4)
    assert result["ram_total_gib"] == pytest.approx(16) and result["ram_used_gib"] == pytest.approx(8)
    assert cpu == (1000, 850)


def test_read_proc_missing_files_stay_absent(tmp_path):
    result, cpu = read_proc(tmp_path / "nope")
    assert cpu is None and result == {"scope": "Host"}
    result, _ = read_proc(
        fake_proc(tmp_path / "x", release="6.1-generic", meminfo="MemTotal: 10 kB\nMemAvailable: 99 kB\n")
    )
    assert result["scope"] == "Linux" and "ram_used_gib" not in result  # impossible values are not shown


def test_parse_gpu_keeps_only_valid_fields():
    assert parse_gpu("37, 61, 2048, 8192\n") == {
        "gpu_percent": 37.0,
        "gpu_temperature": 61.0,
        "gpu_used_gib": 2.0,
        "gpu_total_gib": 8.0,
    }
    # Each bad metric disappears instead of becoming zero; device zero only.
    assert parse_gpu("[N/A], 61, 9000, 8192\n1, 2, 3, 4") == {"gpu_temperature": 61.0, "gpu_total_gib": 8.0}
    assert parse_gpu("101, nan, -1, 5") == {"gpu_total_gib": 5 / 1024}
    assert parse_gpu("") == {}


def test_read_disk_is_bounded_and_fails_closed(tmp_path):
    result = read_disk(str(tmp_path))
    assert 0 <= result["disk_used_gib"] <= result["disk_total_gib"]
    assert read_disk(str(tmp_path / "missing")) == {}


@pytest.mark.asyncio
async def test_sampler_reports_cpu_delta_and_disk_without_gpu():
    sampler = HostSampler()
    samples = iter([({"ram_used_gib": 1.0, "ram_total_gib": 2.0}, (1000, 900)), ({"ram_used_gib": 1.0}, (2000, 1500))])
    with (
        patch("hermes_lark_streaming.details.host.read_proc", lambda: next(samples)),
        patch("hermes_lark_streaming.details.host.shutil.which", return_value=None),
    ):
        sampler.request()
        await sampler._task
        sampler._at = float("-inf")
        sampler.request()
        await sampler._task
    value = sampler.snapshot()
    assert value["cpu_percent"] == pytest.approx((1000 - 600) / 1000 * 100)
    assert "disk_total_gib" in value and "gpu_percent" not in value and value["unavailable"]


@pytest.mark.asyncio
async def test_slow_thread_stays_owned_and_coalesced():
    started, release = threading.Event(), threading.Event()
    calls = 0

    def slow_read(*args):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(5), "test worker was not released"
        return {"ram_used_gib": 1}, None

    sampler = HostSampler()
    with (
        patch("hermes_lark_streaming.details.host.read_proc", slow_read),
        patch("hermes_lark_streaming.details.host.shutil.which", return_value=None),
    ):
        sampler.request()
        task = sampler._task
        try:
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.sleep(0.1)
            assert sampler._task is task and not task.done()
            sampler._at = float("-inf")
            sampler.request()
            await asyncio.sleep(0)
            assert sampler._task is task and calls == 1
            # A bounded final wait neither cancels nor detaches the worker.
            await asyncio.wait_for(sampler.finish(), 3)
            assert sampler._task is task and not task.done()
        finally:
            release.set()
            await task
    assert sampler._task is None
