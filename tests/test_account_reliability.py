"""Account identity handoff, explicit aliases and honest compact state labels."""
import asyncio
import json
import threading

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.reference import build_account_panel
from hermes_lark_streaming.footer.account_catalog import ENDPOINTS, catalog
from hermes_lark_streaming.footer.accounts import Account, AccountsSummary, discover, fetch_account


def settings(**kw):
    return {"accounts": [{"id": "first", "label": "Preferred", "provider": "opencode-go"}], **kw}


@pytest.mark.parametrize("auto", [False, True])
def test_explicit_same_key_alias_keeps_first_record_with_discovery_on_or_off(auto):
    value = settings(auto_detect=auto)
    value["accounts"].append({"id": "second", "provider": "opencode-go", "key_env": "OPENCODE_GO_API_KEY_2"})
    rows = discover(value, lambda name: "PRIVATE_SAME" if name.startswith("OPENCODE_GO_API_KEY") else "", env_names=())
    assert len(rows) == 1 and rows[0].id == "first" and rows[0].name == "Preferred"
    assert "PRIVATE_SAME" not in repr(rows)


def test_explicit_identical_reference_is_resolved_once_and_distinct_keys_survive():
    calls = []
    value = settings()
    value["accounts"] += [
        {"id": "duplicate", "provider": "opencode-go"},
        {"id": "distinct", "provider": "opencode-go", "key_env": "OPENCODE_GO_API_KEY_2"},
    ]
    def resolve(name):
        calls.append(name)
        return name + "_PRIVATE"
    rows = discover(value, resolve)
    assert [r.id for r in rows] == ["first", "distinct"] and len(calls) == 2


def test_full_explicit_capacity_does_not_scan_unrelated_credentials():
    value = {"auto_detect": True, "accounts": [
        {"id": "alias" + str(i), "provider": "opencode-go", "key_env": "OPENCODE_GO_API_KEY_" + str(i)}
        for i in range(4)
    ]}
    calls = []
    def resolve(name):
        calls.append(name)
        assert name.startswith("OPENCODE_GO_API_KEY_")
        return name
    assert len(discover(value, resolve, env_names=("DEEPSEEK_API_KEY",))) == 4
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_inflight_rotations_coalesce_to_latest_without_another_delta(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []
    def fetch(specs, keys):
        calls.append(keys)
        if keys == ("old",):
            started.set()
            assert release.wait(3)
        return {"status": "snapshot", "accounts": [{"id": "first", "status": "ok", "label": keys[0]}]}
    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.fetch_accounts", fetch)
    summary = AccountsSummary()
    summary.request(settings(), lambda name: "old")
    assert await asyncio.to_thread(started.wait, 3)
    first = summary._task
    summary.request(settings(), lambda name: "intermediate")
    summary.request(settings(), lambda name: "latest")
    assert summary._task is first and summary.snapshot()["status"] == "pending"
    release.set()
    await asyncio.wait_for(first, 3)
    assert calls == [("old",), ("latest",)]
    assert summary.snapshot()["accounts"][0]["label"] == "latest"
    assert summary._task is summary._pending_query is None


@pytest.mark.asyncio
async def test_same_identity_failure_records_new_attempt_without_relabeling_last_success(monkeypatch):
    rows = iter([
        {"id": "first", "status": "ok", "checked_at": "2026-10-05T04:00:00Z", "balances": []},
        {"id": "first", "status": "unavailable", "http_status": 429, "checked_at": "2026-10-05T04:05:00Z"},
        {"id": "first", "status": "unavailable", "checked_at": "2026-10-05T04:10:00Z"},
        {"id": "first", "status": "ok", "checked_at": "2026-10-05T04:15:00Z", "balances": []},
    ])
    monkeypatch.setattr(
        "hermes_lark_streaming.footer.accounts.fetch_accounts", lambda *args: {"accounts": [next(rows)]}
    )
    summary = AccountsSummary()
    for index in range(4):
        summary._at = float("-inf")
        summary.request(settings(), lambda name: "same")
        await summary._task
        row = summary.snapshot()["accounts"][0]
        if index in {1, 2}:
            assert row["checked_at"] == "2026-10-05T04:00:00Z" and row["stale"]
            assert row["last_attempt_at"] == ("2026-10-05T04:05:00Z" if index == 1 else "2026-10-05T04:10:00Z")
        if index == 1:
            assert row["last_http_status"] == 429
        elif index >= 2:
            assert "last_http_status" not in row
        if index == 3:
            assert not row.get("stale") and row["checked_at"].endswith("04:15:00Z")


@pytest.mark.parametrize("status,reason", [
    ("pending", "待查询"), ("unsupported", "待接入"),
    ("missing_credentials", "待配置凭据"), ("unavailable", "查询失败"),
])
def test_unqueried_and_failed_unknowns_do_not_claim_api_omitted_fields(status, reason):
    panel = build_account_panel({"accounts": [{"status": status, "label": "Fixture"}]}, "UTC")
    text = json.dumps(panel, ensure_ascii=False)
    assert reason in text and "接口未返回" not in text


@pytest.mark.parametrize("code,label", [
    (401, "凭据/权限错误"), (403, "凭据/权限错误"), (429, "限流"), (503, "读取失败"),
])
def test_failed_and_retained_snapshots_show_bounded_http_reason_and_attempt_time(code, label):
    rows = [
        {"status": "unavailable", "http_status": code, "checked_at": "2026-10-05T04:05:00Z"},
        {"status": "ok", "stale": True, "last_http_status": code,
         "last_attempt_at": "2026-10-05T04:10:00Z", "checked_at": "2026-10-05T04:00:00Z"},
    ]
    text = json.dumps(build_account_panel({"accounts": rows}, "Asia/Shanghai"), ensure_ascii=False)
    assert label in text and f"HTTP {code}" in text
    assert "12:05" in text and "12:10" in text and "12:00" in text
    assert "保留上次成功快照" in text


@pytest.mark.parametrize("bad", [None, True, 7, {}, [None], [{"amount": None}], [{"amount": "NaN"}]])
def test_malformed_balance_neither_crashes_card_nor_claims_known_money(bad):
    panel = build_account_panel({"accounts": [{"status": "ok", "balances": bad}]}, "UTC")
    text = json.dumps(panel, ensure_ascii=False)
    assert "Subscription expiry / account balance · — / — (not reported)" in text
    assert inspect_card({"schema": "2.0", "body": {"elements": [panel]}}).safe


def test_signed_known_wallet_balance_is_preserved_not_hidden_by_renderer():
    panel = build_account_panel({"accounts": [{"status": "ok", "balances": [
        {"kind": "account_credit_balance", "currency": "USD", "amount": "-1.50"},
    ]}]}, "UTC")
    text = json.dumps(panel, ensure_ascii=False)
    assert "USD -1.50" in text and "账户余额 · —" not in text


def test_documented_retired_endpoint_is_explained_but_never_probed(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("No retired endpoint query"))
    row = fetch_account(Account("fixture", "SF", "siliconflow", ""), "PRIVATE_SENTINEL")
    assert row["status"] == "unsupported" and row["reason"] == "endpoint_retired"
    assert row["retired_on"] == "2026-08-14" and "siliconflow" not in ENDPOINTS
    assert "PRIVATE_SENTINEL" not in json.dumps(row)
    text = json.dumps(build_account_panel({"accounts": [row]}, "UTC"), ensure_ascii=False)
    assert "官方账户接口已退役" in text and "2026-08-14" in text
    product = next(p for p in catalog()["products"] if p["id"] == "siliconflow")
    assert product["adapter"] == "not_implemented" and product["reason"] == "endpoint_retired"
