from __future__ import annotations

from pathlib import Path

from hermes_lark_streaming import register
from hermes_lark_streaming.__main__ import _load_hermes_environment


def test_entrypoint_register_is_side_effect_free() -> None:
    assert register(object()) is None


def test_load_hermes_environment_uses_active_home(monkeypatch, tmp_path: Path) -> None:
    calls: list[Path] = []

    def fake_loader(*, hermes_home=None, project_env=None):
        calls.append(Path(hermes_home))
        return []

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("hermes_cli.env_loader.load_hermes_dotenv", fake_loader)
    _load_hermes_environment()
    assert calls == [tmp_path]
