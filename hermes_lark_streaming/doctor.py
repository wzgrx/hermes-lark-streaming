"""Structured diagnostics and dry-run smoke checks."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
from typing import Any

from .config import Config
from .delivery import delivery_ledger
from .metrics import metrics
from .native_hooks import runtime_capability
from .sdk import probe_channel_sdk, probe_lark_oapi


def build_report() -> dict[str, Any]:
    cfg = Config()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    credentials = bool((cfg.env_app_id and cfg.env_app_secret) or (cfg.feishu_app_id and cfg.feishu_app_secret))
    add("streaming.enabled", cfg.enabled, "enabled" if cfg.enabled else "disabled")
    add("credentials", credentials, "configured" if credentials else "missing")
    add("python", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        runtime = importlib.metadata.version("hermes-agent")
    except importlib.metadata.PackageNotFoundError:
        runtime = "not installed in this interpreter"
    add("hermes-runtime", runtime != "not installed in this interpreter", runtime)
    lark_cli = shutil.which("lark-cli")
    add("lark-cli", bool(lark_cli), lark_cli or "optional; not installed")
    sdk = probe_lark_oapi()
    add("lark-oapi", bool(sdk["ok"]), str(sdk["version"]))
    native = runtime_capability()
    add(
        "native-observers",
        bool(native["available"]),
        f"{len(native['observer_hooks'])} supported; AST remains delivery owner",
    )
    try:
        from .patcher import CronPatcher, Patcher

        gateway_patcher = Patcher()
        add("gateway-hook", gateway_patcher.is_patched(), str(gateway_patcher.run_path))
        try:
            cron_patcher = CronPatcher()
            add("cron-hook", cron_patcher.is_patched(), str(cron_patcher.cron_path))
        except Exception as exc:
            add("cron-hook", False, type(exc).__name__)
    except Exception as exc:
        add("gateway-hook", False, type(exc).__name__)
    routing = cfg.bot_registry().diagnostics()
    if routing["enabled"]:
        add("multi-bot", all(bot["configured"] for bot in routing["bots"]), f"{routing['bot_count']} bot(s)")
    return {
        "schema": 1,
        "integration": {"strategy": "native-observer+ast" if native["available"] else "ast", **native},
        "sdk": {"lark_oapi": sdk, "channel_sdk": probe_channel_sdk()},
        "delivery": delivery_ledger.summary(),
        "ok": all(item["ok"] for item in checks if item["name"] not in {"lark-cli", "native-observers"}),
        "checks": checks,
        "routing": routing,
        "backpressure": {
            "enabled": cfg.adaptive_backpressure_enabled,
            "min_ms": cfg.backpressure_min_ms,
            "max_ms": cfg.backpressure_max_ms,
        },
        "metrics_path": str(metrics.path),
    }


def print_report(*, as_json: bool = False) -> int:
    report = build_report()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Hermes Lark Streaming doctor: {'PASS' if report['ok'] else 'CHECK'}")
        for item in report["checks"]:
            print(f"  {'OK' if item['ok'] else '--'} {item['name']}: {item['detail']}")
        print(f"  delivery ledger: {report['delivery']['entries']} entries at {report['delivery']['path']}")
        print(f"  metrics: {report['metrics_path']}")
    return 0 if report["ok"] else 1


def lark_cli_smoke(*, execute: bool = False) -> dict[str, Any]:
    """Run only read-only lark-cli checks; live network calls require explicit execute."""
    binary = shutil.which("lark-cli")
    result: dict[str, Any] = {"available": bool(binary), "execute": execute, "commands": []}
    if not binary or not execute:
        return result
    for args in (
        [binary, "auth", "status"],
        [binary, "schema", "im.messages.delete"],
        [binary, "im", "+messages-send", "--chat-id", "oc_SMOKE_PLACEHOLDER", "--text", "smoke", "--dry-run"],
    ):
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
        result["commands"].append({"argv": args[1:], "returncode": proc.returncode})
    return result
