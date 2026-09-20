from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

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
