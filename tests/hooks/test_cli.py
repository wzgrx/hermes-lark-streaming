from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from hermes_lark_streaming import __main__ as entrypoint
from hermes_lark_streaming.hooks import cli, locate
from hermes_lark_streaming.hooks.table import INJECTIONS

from .conftest import FILES


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_install_status_uninstall_roundtrip(hermes_source: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(hermes_source)
    before = {rel: (hermes_source / rel).read_text() for rel in FILES}
    assert run(capsys, "verify", "--root", root)[0] == 0
    code, out = run(capsys, "status", "--root", root)
    assert code == 0 and "Patched: no" in out and "normalize: MISSING" in out
    code, out = run(capsys, "install", "--root", root)
    assert code == 0 and "Patch applied" in out
    code, out = run(capsys, "install", "--root", root)
    assert code == 0 and "Already patched" in out
    code, out = run(capsys, "status", "--root", root)
    assert "Patched: yes" in out and "approval: run_turn_runner.py" in out and "normalize: run_inbound.py" in out
    assert "cron_deliver: scheduler_delivery.py" in out and "Up to date: yes" in out
    assert run(capsys, "restore", "--root", root) == (0, "Restored.\n")
    assert {rel: (hermes_source / rel).read_text() for rel in FILES} == before
    assert run(capsys, "uninstall", "--root", root) == (0, "Not patched.\n")


def test_verify_reports_incompatibility_and_install_refuses(
    hermes_source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = hermes_source / "gateway/run_busy.py"
    target.write_text("pass\n")
    code, out = run(capsys, "verify", "--root", str(hermes_source))
    assert code == 1 and "Incompatible: STOP" in out
    code, out = run(capsys, "install", "--root", str(hermes_source))
    assert code == 1 and "Install failed" in out and target.read_text() == "pass\n"


def test_malformed_markers_make_uninstall_fail_and_restore_use_backup(
    hermes_source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(hermes_source)
    assert run(capsys, "install", "--root", root)[0] == 0
    target = hermes_source / "gateway/run_busy.py"
    clean = (hermes_source / "gateway/run_busy.py.hermes_lark.bak").read_text()
    target.write_text(target.read_text().replace("# HERMES_LARK_STOP_END", ""))
    assert run(capsys, "uninstall", "--root", root)[0] == 1
    assert run(capsys, "restore", "--root", root)[0] == 0
    assert target.read_text() == clean


def test_legacy_single_file_layout_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "gateway").mkdir()
    (tmp_path / "gateway/run.py").write_text("pass\n")
    code, out = run(capsys, "verify", "--root", str(tmp_path))
    assert code == 1 and "single-file" in out


def test_missing_install_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "status", "--root", str(tmp_path / "nothing"))
    assert code == 1 and "Hermes gateway not found" in out


def test_find_root_uses_env_override(hermes_source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_AGENT_DIR", str(hermes_source))
    assert locate.find_root() == hermes_source


def test_status_notes_managed_runtime(
    hermes_source: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "path", [*sys.path, "/tmp/.hermes/installs/id/environments/id/venv/lib/python3.14/site-packages"]
    )
    code, out = run(capsys, "status", "--root", str(hermes_source))
    assert code == 0 and "Hermes Python: managed PM runtime" in out


def test_load_hermes_environment_uses_active_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[Path] = []
    loader = ModuleType("hermes_cli.env_loader")
    loader.load_hermes_dotenv = lambda *, hermes_home=None, project_env=None: calls.append(Path(str(hermes_home)))  # type: ignore[attr-defined]
    package = ModuleType("hermes_cli")
    package.env_loader = loader  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.env_loader", loader)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
    cli.load_hermes_environment()
    assert calls == [tmp_path]


def test_table_files_are_what_the_cli_touches() -> None:
    assert set(FILES) == {i.file for i in INJECTIONS}


class TestEntrypoint:
    def test_no_arguments_prints_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert entrypoint.main([]) == 0
        assert "install" in capsys.readouterr().out

    def test_hook_commands_delegate(self, hermes_source: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert entrypoint.main(["verify", "--root", str(hermes_source)]) == 0
        assert "Compatible" in capsys.readouterr().out

    def test_unknown_command_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert entrypoint.main(["frobnicate"]) == 1
        assert "Unknown command: frobnicate" in capsys.readouterr().out

    def test_other_commands_go_to_cli_module_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = ModuleType("hermes_lark_streaming.cli")
        module.main = lambda argv: 7  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "hermes_lark_streaming.cli", module)
        monkeypatch.setattr(sys.modules["hermes_lark_streaming"], "cli", module, raising=False)
        assert entrypoint.main(["doctor"]) == 7

    def test_run_module_style_invocation(self, hermes_source: Path) -> None:
        import subprocess

        proc = subprocess.run(
            [sys.executable, "-m", "hermes_lark_streaming", "verify", "--root", str(hermes_source)],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert proc.returncode == 0 and "Compatible" in proc.stdout
