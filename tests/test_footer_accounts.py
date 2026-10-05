"""Synthetic adapter/concurrency fixtures; no credentials or live API calls."""

import asyncio
import json

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.reference import build_account_panel, build_reference_footer
from hermes_lark_streaming.footer.accounts import Account, AccountsSummary, configured, fetch_account, parse_account
from hermes_lark_streaming.footer.runtime import runtime_actions


def settings():
    return {"enabled": True, "accounts": [{"id": "go1", "provider": "opencode-go"}]}


def go(percent=47):
    return {"usage": {name: {"status": "ok", "percent": percent,
                             "resetsAt": "2026-10-20T06:26:40Z"} for name in ("rolling", "weekly", "monthly")}}


@pytest.mark.parametrize("percent", [0, 47, 100, 110])
def test_go_reports_used_and_remaining_separately(percent):
    result = parse_account("opencode-go", go(percent))
    assert result["status"] == "ok" and len(result["windows"]) == 3
    assert result["windows"][0]["used_percent"] == percent
    assert result["windows"][0]["remaining_percent"] == max(0, 100-percent)
    assert result["balances"] == []  # no cash balance or subscription expiry invented


@pytest.mark.parametrize("percent", [True, None, "47", -1, float("nan"), float("inf"), 10**400])
def test_bad_percent_is_unknown_not_zero(percent):
    assert parse_account("opencode-go", go(percent))["status"] == "unavailable"


@pytest.mark.parametrize("stamp", ["2026-10-20", "wrong", None])
def test_bad_reset_timestamp_is_not_an_expiry(stamp):
    body = go()
    body["usage"]["monthly"]["resetsAt"] = stamp
    result = parse_account("opencode-go", body)
    assert result["windows"][2]["reset_at"] is None
    assert "expires_at" not in result


@pytest.mark.parametrize("amount", ["0", "12.3400", 5])
def test_deepseek_balance_is_not_go_or_key_credit(amount):
    result = parse_account("deepseek", {"balance_infos": [{"currency": "CNY", "total_balance": amount}]})
    assert result["balances"][0] == {"kind": "account_balance", "currency": "CNY", "amount": str(amount)}


@pytest.mark.parametrize("amount", [True, "NaN", "Infinity", "-1", None, "1e500"])
def test_invalid_money_stays_unknown(amount):
    assert parse_account("deepseek", {"balance_infos": [{"currency": "USD", "total_balance": amount}]})[
        "status"] == "unavailable"


def test_openrouter_key_limit_is_not_account_balance():
    result = parse_account("openrouter", {"data": {"limit": 10, "limit_remaining": 4.5}})
    assert result["balances"] == [{"kind": "key_credit_remaining", "currency": "USD", "amount": "4.5"}]
    unlimited = parse_account("openrouter", {"data": {"limit": None, "limit_remaining": None}})
    assert unlimited["key_limit_unset"] and unlimited["balances"] == []


@pytest.mark.parametrize("env", ["FEISHU_APP_SECRET", "GITHUB_TOKEN", "OPENCODE_GO_API_KEY\nBAD", False])
def test_cross_provider_credentials_are_not_accepted(env):
    value = settings()
    value["accounts"][0]["key_env"] = env
    assert configured(value) == ()


def test_bounded_account_list_and_deduplicated_alias():
    value = settings()
    value["accounts"] *= 12
    assert len(configured(value)) == 1
    assert configured(value)[0].key_env == "OPENCODE_GO_API_KEY"


def test_unsupported_provider_does_not_use_credentials_or_network(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("Network must not run"))
    result = fetch_account(Account("sf", "Backup", "siliconflow", ""), "")
    assert result["status"] == "unsupported"


def test_raw_errors_and_secrets_are_not_reported(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise RuntimeError("Bearer SENTINEL_PRIVATE https://private.invalid")
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    text = json.dumps(fetch_account(Account("go", "Go", "opencode-go", "OPENCODE_GO_API_KEY"), "SENTINEL_PRIVATE"))
    assert "RuntimeError" in text and "SENTINEL_PRIVATE" not in text and "private.invalid" not in text


@pytest.mark.asyncio
async def test_refresh_coalesces_and_rotation_discards_old_identity(monkeypatch):
    import threading

    old_started, release = threading.Event(), threading.Event()
    calls = []
    def fetch(specs, keys):
        calls.append(keys)
        if keys == ("old",):
            old_started.set()
            assert release.wait(2)
        return {"accounts": [{"label": "new" if keys == ("new",) else "old"}]}
    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.fetch_accounts", fetch)
    summary = AccountsSummary()
    summary.request(settings(), lambda env: "old")
    assert await asyncio.to_thread(old_started.wait, 2)
    first = summary._task
    summary.request(settings(), lambda env: "old")
    assert summary._task is first
    summary.request(settings(), lambda env: "new")
    assert summary.snapshot()["status"] == "pending"
    release.set()
    await first
    # The same owned task drains the latest identity without a new delta.
    assert summary.snapshot()["accounts"][0]["label"] == "new"
    assert summary._pending_query is None
    summary.request(settings(), lambda env: "new")
    assert summary._task is None and len(calls) == 2


def test_nested_panel_preserves_expansion_and_has_no_fake_expiry_or_balance():
    account = {**parse_account("opencode-go", go()), "provider": "opencode-go", "label": "Go 1",
               "checked_at": "2026-10-05T04:00:00Z"}
    panel = build_account_panel({"accounts": [account]}, "Asia/Shanghai")
    text = json.dumps(panel, ensure_ascii=False)
    assert "53%" in text and "10-20 14:26" in text and "Go 1" in text
    assert "Subscription expiry / account balance · — / — (not reported)" in text and "expires_at" not in text
    card = build_reference_footer({"reference": {"accounts": {"accounts": [account]}}})[0]
    actions = json.dumps(runtime_actions([card], reference=True))
    assert "ref_accounts" in actions and '"expanded"' not in actions
    assert inspect_card({"schema": "2.0", "body": {"elements": [card]}}).safe


def test_default_footer_does_not_add_account_panels():
    assert "ref_accounts" not in json.dumps(build_reference_footer({}))


def test_partial_window_and_stale_snapshot_are_labeled():
    body = go()
    del body["usage"]["rolling"]
    account = {**parse_account("opencode-go", body), "provider": "opencode-go", "label": "Fixture"}
    assert account["partial"] is True
    text = json.dumps(build_account_panel({"stale": True, "accounts": [account]}, "UTC"), ensure_ascii=False)
    assert "部分额度窗口未返回" in text and "上次快照" in text


@pytest.mark.parametrize("enabled,allowed,details,terminal,visible,polls", [
    (False, ["private-fixture"], True, False, False, 0),
    (True, None, True, False, False, 0),
    (True, ["other-fixture"], True, False, False, 0),
    (True, ["private-fixture"], False, False, False, 0),
    (True, ["private-fixture"], True, True, True, 0),
    (True, ["private-fixture"], True, False, True, 1),
])
def test_runtime_account_visibility_requires_explicit_chat_allowlist(
    monkeypatch, tmp_path, enabled, allowed, details, terminal, visible, polls,
):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from hermes_lark_streaming.streaming.runtime_footer import RuntimeFooterController
    from hermes_lark_streaming.streaming.session import SessionState

    calls = []
    class Reader:
        def request(self, settings, resolve):
            calls.append(settings)
        def snapshot(self):
            return {"status": "pending", "accounts": []}
    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.AccountsSummary", Reader)
    ctrl = RuntimeFooterController()
    ctrl._cfg = SimpleNamespace(card_layout="reference", footer_history={},
        footer_accounts={"enabled": enabled, "allowed_chats": allowed}, footer_enabled=True,
        footer_details=details, reference_resources_enabled=False, show_tool_use=False,
        reference_agent_name="Fixture")
    ctrl._reference_host = SimpleNamespace(snapshot=lambda: {})
    ctrl._reference_history = ctrl._reference_accounts = None
    ctrl._profile_home = tmp_path
    ctrl._credential_scope = nullcontext
    session = SimpleNamespace(chat_id="private-fixture",
        state=SessionState.COMPLETED if terminal else SessionState.STREAMING,
        tool_use=SimpleNamespace(build_display_steps=lambda: []), tool_calls_prior=0,
        tools_done_prior=0, tools_failed_prior=0)
    data = ctrl._reference_snapshot(session, {})
    assert (data["reference"]["accounts"] is not None) is visible
    assert len(calls) == polls


def test_snapshot_age_and_copy_are_isolated(monkeypatch):
    summary = AccountsSummary()
    summary._cached = {"status": "snapshot", "accounts": [{"label": "Fixture"}]}
    summary._at = 0
    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.time.monotonic", lambda: 301)
    value = summary.snapshot()
    assert value["stale"] is True
    value["accounts"][0]["label"] = "Changed"
    assert summary.snapshot()["accounts"][0]["label"] == "Fixture"
