from __future__ import annotations

from types import SimpleNamespace

from hermes_lark_streaming import sdk


def test_lark_oapi_probe_feature_checks_active_interpreter() -> None:
    report = sdk.probe_lark_oapi()
    assert report["package"] == "lark-oapi"
    assert report["minimum"] == sdk.MIN_LARK_OAPI
    assert report["meets_minimum"] is True
    assert isinstance(report["missing"], list)
    assert report["ok"] is True


def test_sdk_repair_is_a_noop_when_features_are_healthy(monkeypatch) -> None:
    healthy = {"package": "lark-oapi", "version": "1.7.3", "ok": True, "missing": []}
    monkeypatch.setattr(sdk, "probe_lark_oapi", lambda: healthy)
    monkeypatch.setattr(sdk.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    assert sdk.repair_lark_oapi() == {
        "changed": False,
        "returncode": 0,
        "before": healthy,
        "after": healthy,
    }


def test_sdk_repair_targets_current_interpreter(monkeypatch) -> None:
    broken = {"package": "lark-oapi", "version": "broken", "ok": False, "missing": ["Client"]}
    healthy = {"package": "lark-oapi", "version": "1.7.3", "ok": True, "missing": []}
    probes = iter([broken, healthy])
    calls = []
    monkeypatch.setattr(sdk, "probe_lark_oapi", lambda: next(probes))
    monkeypatch.setattr(sdk.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(sdk.subprocess, "run", fake_run)
    result = sdk.repair_lark_oapi()
    assert result["after"] == healthy
    assert calls[0][0] == ["/usr/bin/uv", "pip", "install", "--python", sdk.sys.executable, "lark-oapi>=1.7.3"]
    assert calls[0][1]["timeout"] == 180


def test_version_tuple_handles_stable_sdk_versions() -> None:
    assert sdk._version_tuple("1.7.3") == (1, 7, 3)
    assert sdk._version_tuple("1.10.0") > sdk._version_tuple("1.7.3")
