from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from hermes_lark_streaming import __main__ as entrypoint
from hermes_lark_streaming import register
from hermes_lark_streaming.__main__ import _load_hermes_environment


def test_entrypoint_register_is_side_effect_free() -> None:
    assert register(object()) is None


def test_load_hermes_environment_uses_active_home(monkeypatch, tmp_path: Path) -> None:
    calls: list[Path] = []

    def fake_loader(*, hermes_home=None, project_env=None):
        calls.append(Path(hermes_home))
        return []

    env_loader = ModuleType("hermes_cli.env_loader")
    env_loader.load_hermes_dotenv = fake_loader
    hermes_cli = ModuleType("hermes_cli")
    hermes_cli.env_loader = env_loader
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.env_loader", env_loader)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _load_hermes_environment()
    assert calls == [tmp_path]

def test_status_reports_all_active_patcher_markers(monkeypatch, tmp_path: Path, capsys) -> None:
    from hermes_lark_streaming import config
    from hermes_lark_streaming import patcher as patcher_module

    class FakePatcher:
        run_path = tmp_path / "gateway" / "run.py"
        MARKERS = (
            ("# HERMES_LARK_NORMALIZE_BEGIN", "# HERMES_LARK_NORMALIZE_END"),
            ("# HERMES_LARK_APPROVAL_BEGIN", "# HERMES_LARK_APPROVAL_END"),
        )

        def is_patched(self) -> bool:
            return True

        def _contents(self) -> dict[Path, str]:
            return {
                tmp_path / "run_inbound.py": "# HERMES_LARK_NORMALIZE_BEGIN\n",
                tmp_path / "run_turn_runner.py": "# HERMES_LARK_APPROVAL_BEGIN\n",
            }

    class FakeConfig:
        enabled = True
        env_app_id = "configured"
        feishu_app_id = ""

    monkeypatch.setattr(entrypoint, "_get_patcher", lambda: FakePatcher())
    monkeypatch.setattr(entrypoint, "_get_cron_patcher", lambda: None)
    monkeypatch.setattr(entrypoint, "_load_hermes_environment", lambda: None)
    monkeypatch.setattr(config, "Config", FakeConfig)
    monkeypatch.setattr(patcher_module, "hermes_python", lambda: None)
    monkeypatch.setattr(patcher_module, "hermes_install_dir", lambda: None)

    assert entrypoint._cmd_status() == 0
    output = capsys.readouterr().out
    assert "normalize: run_inbound.py" in output
    assert "approval: run_turn_runner.py" in output
