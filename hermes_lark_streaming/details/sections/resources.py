"""The 资源 section: GPU / VRAM / RAM / disk of the machine running Hermes."""


from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...card.model import Metric, Section
from ..values import label
from .fmt import UNKNOWN, percent_value, ratio_of


def _percent(data: Mapping[str, Any], key: str, name: str, en: str) -> Metric:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return Metric(name, UNKNOWN, label_en=en)
    return Metric(name, percent_value(float(value)), ratio=ratio_of(value, 100), label_en=en)


def _pair(data: Mapping[str, Any], used: str, total: str, name: str, en: str) -> Metric:
    u, t = data.get(used), data.get(total)
    if isinstance(u, bool) or not isinstance(u, int | float) or isinstance(t, bool) or not isinstance(t, int | float):
        return Metric(name, UNKNOWN, label_en=en)
    return Metric(name, _size_pair(float(u), float(t)), ratio=ratio_of(u, t), label_en=en)


def _size_pair(used: float, total: float) -> str:
    """``2.8/24G``, ``35/126G``, ``1.1/1.5T``: short enough for one table cell."""
    if total >= 1000:
        return f"{used / 1024:.1f}/{total / 1024:.1f}T"
    return f"{used:.0f}/{total:.0f}G" if used >= 10 else f"{used:.1f}/{total:.0f}G"


def resources_section(host: Mapping[str, Any] | None) -> Section:
    data = host or {}
    temperature = data.get("gpu_temperature")
    real = isinstance(temperature, int | float) and not isinstance(temperature, bool)
    temp_text = f"{float(temperature):.0f}°C" if real and isinstance(temperature, int | float) else UNKNOWN
    gpu = _percent(data, "gpu_percent", "GPU", "GPU")
    if gpu.value != UNKNOWN and temp_text != UNKNOWN:
        gpu = Metric(gpu.label, f"{gpu.value}·{temp_text}", ratio=gpu.ratio, label_en=gpu.label_en)
    metrics = (
        _percent(data, "cpu_percent", "CPU", "CPU"),
        gpu,
        _pair(data, "gpu_used_gib", "gpu_total_gib", "显存", "VRAM"),
        _pair(data, "ram_used_gib", "ram_total_gib", "内存", "RAM"),
        _pair(data, "disk_used_gib", "disk_total_gib", "磁盘", "Disk"),
    )
    notes: list[str] = []
    sampled = label(data.get("sampled_at"))
    if not sampled:
        notes.append("资源采样未就绪")  # unreadable cells already say 未知 themselves
    scope = label(data.get("scope"))
    title = f"资源 · {scope}" if scope else "资源"
    return Section("resources", title, metrics, tuple(notes), title_en=f"Resources · {scope}" if scope else "Resources")
