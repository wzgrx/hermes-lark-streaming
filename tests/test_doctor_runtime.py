"""Runtime diagnosis separates code identity, old metrics and delivery evidence."""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from hermes_lark_streaming import doctor
from hermes_lark_streaming import metrics as metrics_module


def _version_module(monkeypatch, **values):
    module = ModuleType("hermes_cli.version_info")
    module.get_version_info = lambda: SimpleNamespace(**values)
    monkeypatch.setitem(sys.modules, "hermes_cli.version_info", module)


def _check(report):
    return next(item for item in report["checks"] if item["name"] == "hermes-runtime")


def test_doctor_uses_canonical_runtime_identity_not_placeholder_package(monkeypatch):
    _version_module(monkeypatch, display_version="0.21.5+42", commit="a" * 40, source="git")
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "0.0.0")
    report = doctor.build_report()
    assert _check(report)["detail"] == "0.21.5+42"
    assert report["runtime"]["commit"] == "a" * 40
    assert report["runtime"]["source"] == "git"


def test_doctor_legacy_runtime_keeps_metadata_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.version_info", None)
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "0.14.0")
    report = doctor.build_report()
    assert _check(report)["detail"] == "0.14.0"
    assert report["runtime"]["source"] == "package"


def test_unknown_runtime_is_not_advertised_as_version_zero(monkeypatch):
    _version_module(monkeypatch, display_version="unknown", commit=None, source="unknown")
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "0.0.0")
    report = doctor.build_report()
    assert _check(report)["detail"] == "unknown"
    assert any(w["code"] == "runtime_identity_unknown" for w in report["warnings"])


def test_missing_runtime_remains_a_failed_check(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.version_info", None)
    def missing(name):
        raise doctor.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(doctor.importlib.metadata, "version", missing)
    assert _check(doctor.build_report())["ok"] is False


@pytest.mark.parametrize("status", ["stale", "unverified", "current"])
def test_doctor_exposes_snapshot_evidence_without_rewriting_it(monkeypatch, status, capsys):
    metrics_module.metrics.persist()
    before = metrics_module.metrics.path.read_bytes()
    monkeypatch.setattr(metrics_module, "gateway_snapshot_status", lambda snapshot: status)
    report = doctor.build_report()
    assert report["metrics"]["snapshot_status"] == status
    warnings = {w["code"] for w in report["warnings"]}
    assert ("metrics_" + status in warnings) == (status != "current")
    doctor.print_report(as_json=False)
    assert "snapshot=" + status in capsys.readouterr().out
    assert metrics_module.metrics.path.read_bytes() == before


def test_doctor_keeps_unknown_delivery_visible_without_resending(monkeypatch):
    ledger = doctor.delivery_ledger
    ledger.claim_send("synthetic-unknown", "card.reply")
    before = ledger.path.read_bytes()
    report = doctor.build_report()
    assert any(w["code"] == "delivery_unknown" for w in report["warnings"])
    assert ledger.path.read_bytes() == before


def test_doctor_missing_metrics_is_not_a_live_success():
    report = doctor.build_report()
    assert report["metrics"]["snapshot_status"] == "missing"
    assert any(w["code"] == "metrics_missing" for w in report["warnings"])


def test_invalid_utf8_metrics_is_unavailable_and_preserved():
    metrics_module.metrics.path.write_bytes(b"\xff\xfe")
    assert metrics_module.metrics.load_persisted() is None
    assert metrics_module.metrics.path.read_bytes() == b"\xff\xfe"


@pytest.mark.parametrize("name", ["python worker", "python (worker)", "a) b(c)"])
def test_process_fingerprint_parses_proc_comm_with_spaces(monkeypatch, name):
    # /proc/stat fields: pid (comm) state, then fields 4..21, then starttime.
    stat = "123 (" + name + ") S " + " ".join("0" for _ in range(18)) + " 987654 0 0"
    monkeypatch.setattr(metrics_module.Path, "read_text", lambda self, **kw: stat)
    assert metrics_module._process_start_time(123) == 987654


@pytest.mark.parametrize("name", ["README.md", "README.en.md"])
def test_readme_uses_managed_runtime_and_update(name):
    from pathlib import Path

    text = (Path(__file__).parents[1] / name).read_text(encoding="utf-8")
    assert "--run-module hermes_lark_streaming" in text
    assert "plugins update hermes-lark-streaming" in text
    assert "plugins remove hermes-lark-streaming" in text
    assert "hermes-agent/venv/bin/python" not in text
    assert "pip install -e" not in text
    assert "pip uninstall hermes-lark-streaming" not in text
