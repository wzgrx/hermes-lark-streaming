"""Feishu SDK capability probes and explicit runtime repair."""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import shutil
import subprocess
import sys
from typing import Any

MIN_LARK_OAPI = "1.7.3"


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value)[:3])


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def probe_lark_oapi() -> dict[str, Any]:
    required = {
        "lark_oapi": ("Client",),
        "lark_oapi.api.cardkit.v1": (
            "CreateCardRequest",
            "ContentCardElementRequest",
            "BatchUpdateCardRequest",
            "SettingsCardRequest",
        ),
        "lark_oapi.api.im.v1": (
            "CreateMessageRequest",
            "ReplyMessageRequest",
            "CreateImageRequest",
            "CreateFileRequest",
        ),
    }
    missing: list[str] = []
    for module_name, names in required.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)
            continue
        missing.extend(f"{module_name}.{name}" for name in names if not hasattr(module, name))
    version = _distribution_version("lark-oapi")
    meets_minimum = version != "not-installed" and _version_tuple(version) >= _version_tuple(MIN_LARK_OAPI)
    return {
        "package": "lark-oapi",
        "version": version,
        "minimum": MIN_LARK_OAPI,
        "meets_minimum": meets_minimum,
        "ok": not missing and meets_minimum,
        "missing": missing,
    }


def probe_channel_sdk() -> dict[str, Any]:
    """Report the official Channel SDK as an optional migration target."""
    try:
        module = importlib.import_module("lark_channel")
    except ImportError:
        return {
            "package": "lark-channel-sdk",
            "version": "not-installed",
            "available": False,
            "cardkit_streaming": False,
        }
    return {
        "package": "lark-channel-sdk",
        "version": _distribution_version("lark-channel-sdk"),
        "available": hasattr(module, "FeishuChannel"),
        "cardkit_streaming": hasattr(module, "FeishuChannel"),
    }


def repair_lark_oapi() -> dict[str, Any]:
    """Explicitly repair the active Hermes interpreter with the supported SDK floor."""
    before = probe_lark_oapi()
    if before["ok"]:
        return {"changed": False, "returncode": 0, "before": before, "after": before}
    uv = shutil.which("uv")
    if not uv:
        return {
            "changed": False,
            "returncode": 127,
            "error": "uv executable not found",
            "before": before,
            "after": before,
        }
    proc = subprocess.run(
        [uv, "pip", "install", "--python", sys.executable, f"lark-oapi>={MIN_LARK_OAPI}"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    importlib.invalidate_caches()
    after = probe_lark_oapi()
    return {
        "changed": proc.returncode == 0,
        "returncode": proc.returncode,
        "before": before,
        "after": after,
        "stderr_tail": proc.stderr[-1000:],
    }
