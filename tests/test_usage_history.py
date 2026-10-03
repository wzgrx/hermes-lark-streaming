"""Persistent history acceptance with synthetic usage, never user chats/keys."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest

from hermes_lark_streaming.config import Config
from hermes_lark_streaming.footer import history
from hermes_lark_streaming.footer.history import UsageLedger


def payload(**kw):
    return {
        "session_id": "session",
        "turn_id": "turn",
        "api_request_id": "request",
        "started_at": datetime(2026, 9, 30, 16, tzinfo=UTC).timestamp(),
        "provider": "opencode-go",
        "model": "requested-model",
        "response_model": "reported-model",
        "platform": "feishu",
        "usage": {"prompt_tokens": 100, "output_tokens": 20, "cache_read_tokens": 70},
        **kw,
    }


def test_persistent_dedup_and_no_content(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    p = payload(request={"body": "PRIVATE_PROMPT"}, response={"body": "PRIVATE_RESPONSE"}, api_key="PRIVATE_KEY")
    for _ in range(3):
        assert UsageLedger(path).record("post_api_request", p, {"opencode-go": "Go plan"})
    report = UsageLedger(path).report()
    assert report["totals"]["requests"] == 1
    assert report["totals"]["total_tokens"] == 120
    assert report["totals"]["cache_read_tokens"] == 70
    assert report["groups"][0]["key"] == ["opencode-go", "Go plan", "requested-model", "reported-model"]
    with sqlite3.connect(path) as db:
        dump = "\n".join(db.iterdump())
    assert "PRIVATE_" not in dump
    assert path.stat().st_mode & 0o777 == 0o600


def test_error_then_success_and_late_error_preserve_usage(tmp_path):
    ledger = UsageLedger(tmp_path / "ledger.db")
    p = payload()
    ledger.record("api_request_error", {**p, "usage": None})
    ledger.record("post_api_request", p)
    ledger.record("api_request_error", {**p, "usage": None})
    total = ledger.report()["totals"]
    assert (total["requests"], total["completed_requests"], total["error_attempts"]) == (1, 1, 1)
    assert total["total_tokens"] == 120


def test_retry_auxiliary_missing_usage_and_zero(tmp_path):
    ledger = UsageLedger(tmp_path / "ledger.db")
    p = payload()
    ledger.record("api_request_error", {**p, "usage": None})
    ledger.record("post_api_request", {**p, "started_at": p["started_at"] + 1})
    ledger.record("post_auxiliary_call", payload(aux_task="compression", api_request_id="aux", usage=None))
    ledger.record(
        "post_auxiliary_call",
        payload(aux_task="title", api_request_id="aux2", usage={"prompt_tokens": 0, "output_tokens": 0}),
    )
    assert not ledger.record("post_api_request", payload(aux_task="title"))
    report = ledger.report()
    total = report["totals"]
    assert (total["requests"], total["measured_requests"], total["auxiliary_requests"]) == (4, 2, 2)
    assert total["usage_partial"] and total["total_tokens"] == 120
    assert ledger.report(scope="main")["totals"]["requests"] == 2
    assert ledger.report(scope="auxiliary")["totals"]["input_tokens"] == 0


def test_month_timezone_and_exclusive_range(tmp_path):
    ledger = UsageLedger(tmp_path / "ledger.db")
    p = payload()
    ledger.record("post_api_request", p)
    assert ledger.report(group_by="month")["groups"][0]["key"] == ["2026-09"]
    assert ledger.report(group_by="month", timezone="Asia/Shanghai")["groups"][0]["key"] == ["2026-10"]
    assert ledger.report(end=p["started_at"])["status"] == "empty_range"
    assert ledger.report(start=p["started_at"])["totals"]["requests"] == 1


@pytest.mark.parametrize("group", ["provider", "model", "subscription", "provider-model", "day", "month"])
def test_group_totals_agree(tmp_path, group):
    ledger = UsageLedger(tmp_path / "ledger.db")
    for i, provider in enumerate(["opencode-go", "siliconflow", "custom"]):
        ledger.record("post_api_request", payload(provider=provider, api_request_id=str(i)))
    r = ledger.report(group_by=group)
    assert sum(g["total_tokens"] for g in r["groups"]) == r["totals"]["total_tokens"] == 360


def test_missing_ledger_and_invalid_events_do_not_create_files(tmp_path):
    ledger = UsageLedger(tmp_path / "new" / "ledger.db")
    assert ledger.report()["status"] == "no_history"
    assert ledger.report()["totals"]["input_tokens"] is None
    assert not ledger.record("pre_api_request", payload())
    assert not ledger.record("post_api_request", payload(api_request_id=""))
    assert not ledger.path.exists()


def test_cli_month_json_and_validation(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(history, "default_path", lambda: path)
    UsageLedger(path).record("post_api_request", payload())
    assert history.cli(["--month", "2026-10", "--timezone", "Asia/Shanghai", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["total_tokens"] == 120
    assert history.cli(["--from", "2026-10-02", "--to", "2026-10-01"]) == 2
    assert history.cli(["--month", "bad"]) == 2
    assert history.cli(["--timezone", "No/SuchZone"]) == 2


def test_opt_in_hook_and_locked_db_fail_open(tmp_path, monkeypatch, caplog):
    cfg = Config(home=tmp_path)
    cfg._raw = {"streaming": {"footer": {"history": {"enabled": True}}}}
    monkeypatch.setattr("hermes_lark_streaming.config.Config", lambda: cfg)
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(history, "default_path", lambda: path)
    history.observe_history("post_api_request", payload(platform="cli"))
    assert UsageLedger(path).report()["totals"]["requests"] == 1
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        history.observe_history("post_api_request", payload(api_request_id="blocked"))
        db.rollback()
    assert "not recorded" in caplog.text
    cfg._raw = {}
    history.observe_history("post_api_request", payload(api_request_id="disabled"))
    assert UsageLedger(path).report()["totals"]["requests"] == 1


def test_bad_config_is_disabled_and_labels_are_sanitized(tmp_path):
    cfg = Config(home=tmp_path)
    for value in [None, [], "bad"]:
        cfg._raw = {"streaming": {"footer": {"history": value}}}
        assert cfg.footer_history == {}
    ledger = UsageLedger(tmp_path / "ledger.db")
    ledger.record("post_api_request", payload(), {"opencode-go": "https://secret.example/token"})
    assert "secret.example" not in json.dumps(ledger.report())
