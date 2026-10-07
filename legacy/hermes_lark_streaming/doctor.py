"""Structured diagnostics and dry-run smoke checks."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
from typing import Any

from . import metrics as metrics_module
from .config import Config
from .delivery import DeliveryLedgerError, delivery_ledger
from .metrics import metrics
from .native_hooks import runtime_capability
from .sdk import probe_channel_sdk, probe_lark_oapi


def _runtime_identity() -> dict[str, Any]:
    """Use the host's executing-source identity, not a PM placeholder wheel."""
    try:
        from hermes_cli.version_info import get_version_info  # type: ignore[import-not-found,import-untyped]

        info = get_version_info()
        version = info.display_version
        if isinstance(version, str) and version not in {"", "unknown", "0.0.0"}:
            return {"available": True, "version": version, "source": info.source, "commit": info.commit}
    except Exception:
        # Older supported Hermes hosts predate canonical source identity.
        pass
    try:
        version = importlib.metadata.version("hermes-agent")
    except importlib.metadata.PackageNotFoundError:
        return {"available": False, "version": "not installed in this interpreter", "source": "missing", "commit": None}
    return {
        "available": True, "version": version if version != "0.0.0" else "unknown",
        "source": "package" if version != "0.0.0" else "package-placeholder", "commit": None,
    }


def _current_activity(snapshot: dict[str, Any] | None, status: str) -> dict[str, Any]:
    """Process-lifetime totals, not an outage verdict or a per-message receipt."""
    result: dict[str, Any] = {
        "verified": False, "api_errors": None, "completed_cards": None,
        "completion_failures": None, "text_fallbacks": None,
    }
    if status != "current" or snapshot is None:
        return result
    counters = snapshot.get("counters")
    if not isinstance(counters, dict) or any(
        not isinstance(k, str) or type(v) is not int or v < 0 for k, v in counters.items()
    ):
        return result
    result.update(
        verified=True,
        api_errors=sum(v for k, v in counters.items() if k.startswith("api.") and k.endswith(".error")),
        completed_cards=counters.get("card.completed", 0),
        completion_failures=counters.get("card.completion_failed", 0),
        text_fallbacks=counters.get("card.text_fallback", 0),
    )
    return result


def build_report() -> dict[str, Any]:
    cfg = Config()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    credentials = bool((cfg.env_app_id and cfg.env_app_secret) or (cfg.feishu_app_id and cfg.feishu_app_secret))
    add("streaming.enabled", cfg.enabled, "enabled" if cfg.enabled else "disabled")
    add("credentials", credentials, "configured" if credentials else "missing")
    add("python", sys.version_info >= (3, 11), sys.version.split()[0])
    runtime = _runtime_identity()
    add("hermes-runtime", runtime["available"], runtime["version"])
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
    try:
        delivery = delivery_ledger.summary()
    except DeliveryLedgerError as exc:
        # Doctor is the repair entry point; a damaged ledger must remain
        # untouched while the rest of the diagnostic report stays available.
        add("delivery-ledger", False, str(exc))
        delivery = {
            "schema": delivery_ledger.SCHEMA,
            "path": str(delivery_ledger.path),
            "entries": None,
            "counts": None,
            "error": str(exc),
        }
    if delivery.get("unresolved_capacity_remaining") == 0:
        add(
            "delivery-ledger-capacity",
            False,
            "unresolved delivery evidence fills the ledger; inspect receipts before new sends",
        )
    # Warnings describe evidence gaps, not necessarily a broken idle Gateway.
    # Reading diagnostics must not create a fresh empty snapshot or resend data.
    warnings: list[dict[str, str]] = []
    snapshot = metrics.load_persisted(role="gateway")
    snapshot_status = metrics_module.gateway_snapshot_status(snapshot) if snapshot is not None else "missing"
    activity = _current_activity(snapshot, snapshot_status)
    if snapshot_status != "current":
        warnings.append({
            "code": "metrics_" + snapshot_status,
            "detail": (
                "No verified metrics from the current Gateway; historical counters are not live delivery evidence."
            ),
        })
    elif not activity["verified"]:
        warnings.append({
            "code": "metrics_counters_invalid",
            "detail": "Current-process snapshot counters are malformed; activity is unverified, not zero errors.",
        })
    for key, code, label in (
        ("api_errors", "api_errors_recorded", "API error attempt(s), including recovered retries"),
        ("completion_failures", "card_completion_failures_recorded", "card completion failure(s)"),
        ("text_fallbacks", "card_text_fallbacks_recorded", "plain-text fallback(s)"),
    ):
        if activity[key]:
            warnings.append({
                "code": code,
                "detail": f"{activity[key]} {label} recorded since Gateway start; not proof of a current outage.",
            })
    if runtime["version"] == "unknown":
        warnings.append({
            "code": "runtime_identity_unknown", "detail": "Hermes is installed but its source version is unverified.",
        })
    unknown = (delivery.get("counts") or {}).get("unknown", 0)
    if unknown:
        warnings.append({
            "code": "delivery_unknown",
            "detail": f"{unknown} delivery outcome(s) remain unconfirmed; inspect receipts before any resend.",
        })
    expired_pending = delivery.get("expired_pending_count", 0)
    if expired_pending:
        warnings.append({
            "code": "delivery_pending_expired",
            "detail": f"{expired_pending} pending receipt(s) exceeded the retry window; inspect before any resend.",
        })
    return {
        "schema": 1,
        "runtime": runtime,
        "warnings": warnings,
        "metrics": {
            "snapshot_status": snapshot_status,
            "updated_at": snapshot.get("updated_at") if snapshot else None,
            "activity": activity,
        },
        "integration": {"strategy": "native-observer+ast" if native["available"] else "ast", **native},
        "sdk": {"lark_oapi": sdk, "channel_sdk": probe_channel_sdk()},
        "delivery": delivery,
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
        delivery = report["delivery"]
        if delivery.get("error"):
            print(f"  delivery ledger: inspection required at {delivery['path']}")
        else:
            print(
                f"  delivery ledger: {delivery['entries']} entries, "
                f"{delivery['unresolved_count']} unresolved "
                f"({delivery['unresolved_capacity_remaining']} slots remaining) "
                f"at {delivery['path']}"
            )
        print(f"  metrics: {report['metrics_path']} (snapshot={report['metrics']['snapshot_status']})")
        for warning in report["warnings"]:
            print(f"  WARN {warning['code']}: {warning['detail']}")
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
