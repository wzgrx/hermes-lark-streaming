from __future__ import annotations

import logging
import sys
from types import ModuleType, SimpleNamespace

import yaml

from hermes_lark_streaming import compat
from hermes_lark_streaming.compat import error_classification, log_safety, skills_index
from hermes_lark_streaming.config import ConfigSource


def test_websocket_secrets_are_redacted_once():
    logger = logging.getLogger(log_safety.LOGGER_NAME)
    for f in [f for f in logger.filters if isinstance(f, log_safety.RedactWebsocketSecrets)]:
        logger.removeFilter(f)
    assert log_safety.install() is True and log_safety.install() is False
    url = "wss://x/ws?device_id=1&access_key=AK&ticket=T"
    record = logging.LogRecord("Lark", logging.INFO, "", 0, "connected to %s", (url,), None)
    assert log_safety.RedactWebsocketSecrets().filter(record)
    text = record.getMessage()
    assert "AK" not in text and text != "T" and "access_key=***" in text and "ticket=***" in text


def test_opencode_go_plan_wall_rotates_then_falls_back():
    out = error_classification.classify(
        provider="opencode-go", status_code=403, error_message="Active OpenCode Go subscription is required")
    assert out["reason"] == "billing" and out["should_rotate_credential"] and out["should_fallback"]
    assert error_classification.classify(provider="opencode-go", status_code=403, error_message="forbidden") is None
    assert error_classification.classify(provider="other", status_code=403,
                                         error_message="active opencode go subscription is required") is None
    assert error_classification.classify(provider="opencode-go", status_code=429,
                                         error_message="active opencode go subscription is required") is None


def test_hook_registration_requires_valid_hook(monkeypatch):
    registered = []
    ctx = SimpleNamespace(register_hook=lambda name, fn: registered.append(name))
    plugins = ModuleType("hermes_cli.plugins")
    plugins.VALID_HOOKS = {error_classification.HOOK}
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins)
    assert error_classification.register(ctx) is True and registered == [error_classification.HOOK]
    plugins.VALID_HOOKS = set()
    assert error_classification.register(ctx) is False


def _render(by_category, descriptions, compact, available_tools=None, unloadable=()):
    demoted = bool(compact)
    note = "(Categories marked [names only] are outside the current coding context, so their descriptions are omitted)"
    lines = [
        f"{c} [names only]" if c.split("/")[0] in (compact or ()) else f"{c}: described" for c in sorted(by_category)
    ]
    return "\n".join(lines) + (note if demoted else "")


def test_names_only_forces_every_category_compact():
    mode = {"v": "names_only"}
    wrapped = skills_index.wrap(_render, lambda: mode["v"])
    out = wrapped({"dev/tools": [], "writing": []}, {}, None)
    assert "dev/tools [names only]" in out and "writing [names only]" in out and "compact discovery context" in out
    mode["v"] = "full"
    assert "described" in wrapped({"writing": []}, {}, None)
    assert wrapped({}, {}, None) == ""


def test_install_wraps_once_and_reads_config(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"skills": {"index_mode": "names_only"}}))
    module = ModuleType("agent.prompt_builder")
    module._render_skills_index = _render
    monkeypatch.setitem(sys.modules, "agent", ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.prompt_builder", module)
    monkeypatch.setattr(sys.modules["agent"], "prompt_builder", module, raising=False)
    src = ConfigSource(tmp_path)
    assert skills_index.configured_mode(src) == "names_only"
    assert skills_index.install(src) is True and skills_index.install(src) is False
    assert "writing [names only]" in module._render_skills_index({"writing": []}, {}, None)


def test_register_isolates_failures(monkeypatch):
    monkeypatch.setattr(skills_index, "install", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    compat.register(SimpleNamespace())  # must not raise
