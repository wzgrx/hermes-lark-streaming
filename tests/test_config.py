from __future__ import annotations

import yaml

from hermes_lark_streaming.config import ConfigSource, Settings


def write(home, data):
    (home / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_defaults_are_disabled_and_safe():
    s = Settings.parse(None)
    assert s.enabled is False and s.process == "auto" and s.text_size == "normal_v2"
    assert s.render.show_process is True


def test_values_are_validated_and_clamped():
    s = Settings.parse({
        "enabled": True, "text_size": "bogus", "width_mode": "FILL", "process": "off",
        "rollover_sec": 9999, "adaptive_backpressure": {"min_ms": 1, "max_ms": 99999},
        "details": {"usage": True},
    })
    assert s.enabled and s.text_size == "normal_v2" and s.width_mode == "fill"
    assert s.rollover_sec == 570.0
    assert s.backpressure.min_ms == 50.0 and s.backpressure.max_ms == 10000.0
    assert s.render.show_process is False and s.details == {"usage": True}


def test_legacy_body_text_size_is_still_read():
    assert Settings.parse({"body": {"text_size": "heading"}}).text_size == "heading"


def test_source_reads_display_overrides_and_reloads(tmp_path, monkeypatch):
    write(tmp_path, {"display": {"show_reasoning": False, "platforms": {"feishu": {"show_reasoning": True}}}})
    src = ConfigSource(tmp_path)
    assert src.show_reasoning is True and src.show_tool_use is True
    write(tmp_path, {"display": {"show_tool_use": False}})
    src._checked = 0.0  # skip the 1s TTL
    assert src.show_tool_use is False and src.show_reasoning is False


def test_bad_yaml_keeps_last_good_settings(tmp_path):
    write(tmp_path, {"streaming": {"enabled": True}})
    src = ConfigSource(tmp_path)
    assert src.settings().enabled is True
    (tmp_path / "config.yaml").write_text("streaming: [oops", encoding="utf-8")
    src._checked = 0.0
    assert src.raw()["streaming"]["enabled"] is True


def test_credentials_prefer_env_then_platform_config(tmp_path, monkeypatch):
    for name in ("FEISHU_APP_ID", "LARK_APP_ID", "FEISHU_APP_SECRET", "LARK_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    extra = {"app_id": "cli_x", "app_secret": "s", "domain": "lark"}
    write(tmp_path, {"gateway": {"platforms": {"feishu": {"extra": extra}}}})
    src = ConfigSource(tmp_path)
    assert src.credentials() == ("cli_x", "s", "https://open.larksuite.com")
    monkeypatch.setenv("FEISHU_APP_ID", "env_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "env_secret")
    assert src.credentials()[:2] == ("env_id", "env_secret")
