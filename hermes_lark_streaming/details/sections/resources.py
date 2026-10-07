"""The 资源 section: GPU / VRAM / RAM / disk of the machine running Hermes."""

# ruff: noqa: RUF001

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...card.model import Metric, Section
from ..values import label
from .fmt import UNKNOWN, gib, percent_value, ratio_of

_SCOPES = {"WSL": "WSL（Linux 子系统视角）", "Linux": "Linux 主机", "Host": "本机"}


def _percent(data: Mapping[str, Any], key: str, name: str, en: str) -> Metric:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return Metric(name, UNKNOWN, label_en=en)
    return Metric(name, percent_value(float(value)), ratio=ratio_of(value, 100), label_en=en)


def _pair(data: Mapping[str, Any], used: str, total: str, name: str, en: str) -> Metric:
    u, t = data.get(used), data.get(total)
    if isinstance(u, bool) or not isinstance(u, int | float) or isinstance(t, bool) or not isinstance(t, int | float):
        return Metric(name, UNKNOWN, label_en=en)
    return Metric(name, f"{gib(float(u))} / {gib(float(t))}", ratio=ratio_of(u, t), label_en=en)


def resources_section(host: Mapping[str, Any] | None) -> Section:
    data = host or {}
    temperature = data.get("gpu_temperature")
    real = isinstance(temperature, int | float) and not isinstance(temperature, bool)
    temp_text = f"{float(temperature):.0f}°C" if real and isinstance(temperature, int | float) else UNKNOWN
    metrics = (
        _percent(data, "cpu_percent", "CPU", "CPU"),
        _percent(data, "gpu_percent", "GPU", "GPU"),
        _pair(data, "gpu_used_gib", "gpu_total_gib", "显存", "VRAM"),
        _pair(data, "ram_used_gib", "ram_total_gib", "内存", "RAM"),
        _pair(data, "disk_used_gib", "disk_total_gib", "磁盘", "Disk"),
        Metric("GPU 温度", temp_text, label_en="GPU temp"),
    )
    notes: list[str] = []
    scope = label(data.get("scope"))
    if scope:
        notes.append(f"范围：{_SCOPES.get(scope, scope)}")
    sampled = label(data.get("sampled_at"))
    notes.append(f"采样于 {sampled}" if sampled else "资源采样未就绪；缺失项显示为未知")
    if data.get("unavailable") and sampled:
        notes.append("部分资源不可读（如无 NVIDIA 驱动）")
    return Section("resources", "资源", metrics, tuple(notes), title_en="Resources")
