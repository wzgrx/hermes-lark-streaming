from __future__ import annotations

import pytest

from hermes_lark_streaming.session import jobs

from .conftest import settle
from .test_flow import start

pytestmark = pytest.mark.asyncio

L3 = ("W=~/projects/reviews/wechat-play-8079 && systemctl --user reset-failed wchat-play-l3 2>/dev/null; "
      "systemd-run --user --unit=wchat-play-l3 -p MemoryMax=infinity --collect bash $W/scripts/l3-run-all.sh "
      "--workers 128 2>&1 | tail -1")


async def test_detached_launches_are_recognised_and_ordinary_commands_are_not():
    job = jobs.detect(L3, "oc_1")
    assert job is not None and (job.unit, job.script, job.workers) == ("wchat-play-l3", "l3-run-all.sh", 128)
    assert job.describe() == "wchat-play-l3 · l3-run-all.sh · 128 并发"
    assert jobs.detect("nohup python3 scripts/crawl.py > out.log 2>&1 &", "oc_1").script == "crawl.py"
    assert jobs.detect("setsid bash long.sh", "oc_1") is not None
    for ordinary in ("git status && date", "sleep 20 && date '+%H'", "ls 2>&1 | tail -3", "make -j8"):
        assert jobs.detect(ordinary, "oc_1") is None, ordinary


async def test_a_detached_job_gets_a_stop_button_on_its_card(make_controller, client):
    ctl = make_controller()
    await start(ctl)
    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="started", detail="systemd-run …",
                       command=L3)
    await settle(ctl)
    assert await ctl.on_completed_wait(message_id="om_1", answer="已在后台启动。") is True
    elements = client.of("update_card")[-1][2]["body"]["elements"]
    body = str(elements)
    assert "🧵 **后台任务**" in body and "wchat-play-l3 · l3-run-all.sh · 128 并发" in body
    button = next(e for e in elements if e.get("tag") == "button" and "停止任务" in str(e))
    job_id = button["behaviors"][0]["value"]["job"]
    assert button["behaviors"][0]["value"]["lark_card"] == "stopjob" and job_id in ctl._jobs


async def test_stop_job_stops_the_unit_and_reports_in_the_chat(make_controller, client, monkeypatch):
    ctl = make_controller()
    await start(ctl)
    ctl.on_tool_update(message_id="om_1", tool_name="terminal", status="started", command=L3)
    job_id = next(iter(ctl._jobs))
    calls = []

    async def fake_run(*argv):
        calls.append(argv)
        return 0, ""

    monkeypatch.setattr(jobs, "_run", fake_run)
    assert ctl.stop_job(job_id=job_id, chat_id="oc_1") is True
    await settle(ctl)
    for _ in range(20):
        if client.of("send_text"):
            break
        import asyncio
        await asyncio.sleep(0.05)
    assert calls == [("systemctl", "--user", "stop", "wchat-play-l3")]
    assert client.of("send_text")[-1] == "⏹ 已停止 wchat-play-l3"
    assert ctl.stop_job(job_id="nope", chat_id="oc_1") is False


async def test_a_quota_spike_sends_one_alert_with_the_running_jobs(make_controller, client, monkeypatch):
    ctl = make_controller()
    monkeypatch.setattr(ctl, "_alert_chat", lambda: "oc_1")
    readings = iter([[("Go 221", 2.0)], [("Go 221", 13.0)], [("Go 221", 23.0)]])
    monkeypatch.setattr(ctl, "_rolling_usage", lambda: next(readings))

    async def no_units():
        return [("wchat-play-l3", "/usr/bin/bash l3-run-all.sh")]

    monkeypatch.setattr(jobs, "running_units", no_units)
    t0 = 1_000_000.0
    await ctl._check_quota(now=t0)
    await ctl._check_quota(now=t0 + 600)  # +11 in 10 min: alert
    await ctl._check_quota(now=t0 + 1200)  # still rising, but one alert an hour
    cards = client.of("send_card")
    assert len(cards) == 1
    text = str(cards[0][1])
    assert "额度突增 · Go 221" in text and "2% → **13%**" in text and "wchat-play-l3" in text
    assert "unit:wchat-play-l3" in text  # its stop button
