"""Keep partial resource snapshots readable without altering V1 panel geometry."""
import json

import pytest

from hermes_lark_streaming.cardkit.reference import build_resources


@pytest.mark.parametrize("host,shown,absent", [
    ({"processes": 75}, "进程 75", "运行 0"),
    ({"uptime": 61}, "运行 1分1秒", "进程 0"),
    ({"uptime": 3661}, "运行 1小时1分", "进程 0"),
    ({"uptime": 0}, "运行 0秒", "进程 0"),
])
def test_partial_snapshot_preserves_independent_observations(host, shown, absent):
    text = json.dumps(build_resources(host), ensure_ascii=False)
    assert shown in text
    assert absent not in text


def test_no_gpu_title_prioritizes_available_cpu_and_ram():
    panel = build_resources({"cpu_percent": 17, "ram_used_gib": 15.5, "ram_total_gib": 126,
                             "scope": "WSL", "unavailable": True})
    title = panel["header"]["title"]["content"]
    assert "CPU 17%" in title and "RAM 15.5/126G" in title
    assert "GPU —" not in title and "VRAM —" not in title
    assert panel["expanded"] is False
    assert len([e for e in panel["elements"] if e.get("tag") == "column_set"]) == 2
    assert "部分指标未采集" in json.dumps(panel, ensure_ascii=False)


def test_empty_snapshot_title_is_localized_not_a_wall_of_dashes():
    panel = build_resources({})
    assert panel["header"]["title"]["content"] == "🖥 Resources · Not sampled"
    assert panel["header"]["title"]["i18n_content"]["zh_cn"] == "🖥 系统资源 · 未采集"


def test_complete_snapshot_title_preserves_approved_v1_order():
    panel = build_resources({"gpu_percent": 1, "gpu_temperature": 66, "gpu_used_gib": 10.5,
                            "gpu_total_gib": 24, "ram_used_gib": 15.5, "ram_total_gib": 126,
                            "processes": 75, "uptime": 3 * 86400 + 16 * 3600})
    assert panel["header"]["title"]["content"] == "🖥 GPU 1% · 66°C · VRAM 10.5/24G · RAM 15.5/126G"
    assert "运行 3天16小时" in json.dumps(panel, ensure_ascii=False)


def test_attempted_sampling_without_metrics_is_not_labelled_never_sampled():
    title = build_resources({"sampled_at": "2026-10-04 12:00:00 +0800", "unavailable": True})["header"]["title"]
    assert title["content"] == "🖥 Resources · Unavailable"
    assert title["i18n_content"]["zh_cn"] == "🖥 系统资源 · 指标未获取"


def test_temperature_without_utilization_still_names_the_gpu():
    assert build_resources({"gpu_temperature": 66})["header"]["title"]["content"] == "🖥 GPU 66°C"
