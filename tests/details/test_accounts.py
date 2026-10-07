"""Synthetic adapter, discovery and refresh fixtures; no credentials, no live API calls."""

from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy

import pytest

from hermes_lark_streaming.details.account_adapters import money, parse_subscription
from hermes_lark_streaming.details.account_catalog import ENDPOINTS, PREFIXES, catalog
from hermes_lark_streaming.details.account_fetch import fetch_account, parse_account
from hermes_lark_streaming.details.account_monitor import AccountsSummary
from hermes_lark_streaming.details.accounts import (
    Account,
    configured,
    current_provider_settings,
    current_provider_snapshot,
    discover,
    provider_products,
)


def settings(**kw):
    return {"accounts": [{"id": "first", "label": "Preferred", "provider": "opencode-go"}], **kw}


def go(percent=47):
    return {
        "usage": {
            name: {"status": "ok", "percent": percent, "resetsAt": "2026-10-20T06:26:40Z"}
            for name in ("rolling", "weekly", "monthly")
        }
    }


@pytest.mark.parametrize("percent", [0, 47, 100, 110])
def test_go_reports_used_and_remaining_separately(percent):
    result = parse_account("opencode-go", go(percent))
    assert result["status"] == "ok" and len(result["windows"]) == 3
    assert result["windows"][0]["used_percent"] == percent
    assert result["windows"][0]["remaining_percent"] == max(0, 100 - percent)
    assert result["balances"] == []  # no cash balance or subscription expiry invented


@pytest.mark.parametrize("percent", [True, None, "47", -1, float("nan"), float("inf"), 10**400])
def test_bad_percent_is_unknown_not_zero(percent):
    assert parse_account("opencode-go", go(percent))["status"] == "unavailable"


@pytest.mark.parametrize("stamp", ["2026-10-20", "wrong", None])
def test_bad_reset_timestamp_is_not_an_expiry(stamp):
    body = go()
    body["usage"]["monthly"]["resetsAt"] = stamp
    result = parse_account("opencode-go", body)
    assert result["windows"][2]["reset_at"] is None and "expires_at" not in result


@pytest.mark.parametrize("amount", ["0", "12.3400", 5])
def test_deepseek_balance_is_not_go_or_key_credit(amount):
    result = parse_account("deepseek", {"balance_infos": [{"currency": "CNY", "total_balance": amount}]})
    assert result["balances"][0] == {"kind": "account_balance", "currency": "CNY", "amount": str(amount)}


@pytest.mark.parametrize("amount", [True, "NaN", "Infinity", "-1", None, "1e500"])
def test_invalid_money_stays_unknown(amount):
    body = {"balance_infos": [{"currency": "USD", "total_balance": amount}]}
    assert parse_account("deepseek", body)["status"] == "unavailable"


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "1e-999999999", "1e999999999", "-1"])
def test_invalid_or_unbounded_money_stays_unknown_directly(value):
    assert money(value) is None


def test_openrouter_key_limit_is_not_account_balance():
    result = parse_account("openrouter", {"data": {"limit": 10, "limit_remaining": 4.5}})
    assert result["balances"] == [{"kind": "key_credit_remaining", "currency": "USD", "amount": "4.5"}]
    unlimited = parse_account("openrouter", {"data": {"limit": None, "limit_remaining": None}})
    assert unlimited["key_limit_unset"] and unlimited["balances"] == []


def test_openrouter_key_expiry_and_wallet_remain_separate():
    key = parse_account("openrouter", {"data": {"limit_remaining": 4.5, "expires_at": "2027-01-01T00:00:00Z"}})
    wallet = parse_account("openrouter-credits", {"data": {"total_credits": 10.5, "total_usage": 5.25}})
    assert key["key_expires_at"] == "2027-01-01T00:00:00+00:00" and "subscription_expires_at" not in key
    assert wallet["balances"] == [{"kind": "account_credit_balance", "currency": "USD", "amount": "5.25"}]


@pytest.mark.parametrize(("provider", "currency"), [("moonshot", "CNY"), ("moonshot-global", "USD")])
def test_moonshot_separates_cash_voucher_and_available(provider, currency):
    body = {"code": 0, "status": True, "data": {"available_balance": 3.5, "cash_balance": -1, "voucher_balance": 3.5}}
    row = parse_account(provider, body)
    assert row["status"] == "ok" and [b["currency"] for b in row["balances"]] == [currency] * 3
    assert [b["amount"] for b in row["balances"]] == ["3.5", "-1", "3.5"]


@pytest.mark.parametrize("code", [False, 123, 401, None])
def test_http_200_failed_moonshot_business_envelope_is_not_balance(code):
    assert parse_account("moonshot", {"code": code, "data": {"available_balance": 99}})["status"] == "unavailable"


@pytest.mark.parametrize("provider", ["minimax", "minimax-cn"])
def test_minimax_ambiguous_legacy_usage_count_is_not_guessed(provider):
    base = {
        "model_remains": [
            {
                "model_name": "F",
                "current_interval_total_count": 1000,
                "current_interval_usage_count": 900,
                "end_time": 1792000000000,
            }
        ]
    }
    assert parse_account(provider, base)["windows"] == []
    base["model_remains"][0]["current_interval_remaining_percent"] = 12.3456
    row = parse_account(provider, base)
    assert row["windows"][0]["remaining_percent"] == 12.3456 and row["partial"]
    assert row["windows"][0]["reset_at"] is not None


@pytest.mark.parametrize("value", [True, None, "47", -1, float("nan"), float("inf"), 10**400])
def test_bad_new_adapter_percentage_is_not_zero(value):
    assert parse_account("minimax", {"model_remains": [{"current_interval_remaining_percent": value}]})["windows"] == []


@pytest.mark.parametrize("provider", ["zai", "bigmodel"])
def test_coding_quotas_keep_mcp_and_token_windows_distinct(provider):
    body = {
        "code": 200,
        "success": True,
        "data": {
            "limits": [
                {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 47, "nextResetTime": 1792000000000},
                {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 10},
                {"type": "TIME_LIMIT", "percentage": 25},
            ]
        },
    }
    assert [w["name"] for w in parse_account(provider, body)["windows"]] == ["rolling", "weekly", "mcp_monthly"]
    body["success"] = False
    assert parse_account(provider, body)["status"] == "unavailable"


@pytest.mark.parametrize("auto", [True, 1, False, 0])
def test_subscription_validity_is_not_next_auto_charge(auto):
    row = {
        "productId": "coding-plan",
        "productName": "Coding Plan",
        "status": "VALID",
        "inCurrentPeriod": True,
        "autoRenew": auto,
        "nextRenewTime": "2026-11-01 00:00:00",
        "valid": "2026-10-01 00:00:00 ~ 2026-12-01 00:00:00",
    }
    result = parse_subscription({"code": 200, "data": [row]})
    assert result["subscription_expires_on"] == ("2026-12-01" if auto in (True, 1) else "2026-11-01")
    assert bool(result.get("subscription_renews_on")) == (auto in (True, 1))
    assert "subscription_expires_at" not in result


def test_no_active_subscription_and_bad_business_envelopes_are_not_faked():
    assert parse_subscription({"code": 200, "data": []}) == {"subscription_status": "not_active"}
    assert parse_subscription({"code": 401, "data": []}) == {"subscription_status": "unavailable"}


def test_catalog_keeps_inventory_separate_from_actual_adapters():
    value = catalog()
    assert value["inventory_entries"] >= 226 and value["implemented_products"] == 10
    assert not value["all_providers_implemented"]
    assert len({r["id"] for r in value["products"]}) == len(value["products"])
    assert all(ENDPOINTS[r["id"]] == r["endpoint"] for r in value["products"] if r["adapter"] == "implemented")
    before = deepcopy(value)
    assert catalog() == before


def test_documented_retired_endpoint_is_explained_but_never_probed(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("No retired endpoint query"))
    row = fetch_account(Account("fixture", "SF", "siliconflow", ""), "PRIVATE_SENTINEL")
    assert row["status"] == "unsupported" and row["reason"] == "endpoint_retired"
    assert row["retired_on"] == "2026-08-14" and "siliconflow" not in ENDPOINTS
    assert "PRIVATE_SENTINEL" not in json.dumps(row)
    product = next(p for p in catalog()["products"] if p["id"] == "siliconflow")
    assert product["adapter"] == "not_implemented" and product["reason"] == "endpoint_retired"


@pytest.mark.parametrize("env", ["FEISHU_APP_SECRET", "GITHUB_TOKEN", "OPENCODE_GO_API_KEY\nBAD", False])
def test_cross_provider_credentials_are_not_accepted(env):
    value = settings()
    value["accounts"][0]["key_env"] = env
    assert configured(value) == ()


def test_bounded_account_list_and_deduplicated_alias():
    value = settings()
    value["accounts"] *= 12
    assert len(configured(value)) == 1 and configured(value)[0].key_env == "OPENCODE_GO_API_KEY"


def test_unsupported_provider_does_not_use_credentials_or_network(monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("Network must not run"))
    assert fetch_account(Account("sf", "Backup", "siliconflow", ""), "")["status"] == "unsupported"


def test_raw_errors_and_secrets_are_not_reported(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise RuntimeError("Bearer SENTINEL_PRIVATE https://private.invalid")

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    text = json.dumps(fetch_account(Account("go", "Go", "opencode-go", "OPENCODE_GO_API_KEY"), "SENTINEL_PRIVATE"))
    assert "RuntimeError" in text and "SENTINEL_PRIVATE" not in text and "private.invalid" not in text


@pytest.mark.parametrize(
    ("provider", "secret", "expected_suffix", "raw_auth"),
    [
        ("minimax", "sk-api-fixture", "/account/query_balance", False),
        ("minimax", "plan-fixture", "/v1/token_plan/remains", False),
        ("minimax-cn", "sk-api-fixture", "/account/query_balance", False),
        ("zai", "plan-fixture", "/api/monitor/usage/quota/limit", True),
        ("bigmodel", "plan-fixture", "/api/monitor/usage/quota/limit", True),
        ("openrouter-credits", "management-fixture", "/api/v1/credits", False),
    ],
)
def test_endpoint_and_auth_plane_follow_official_source(monkeypatch, provider, secret, expected_suffix, raw_auth):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return b"{}"

    class Opener:
        def open(self, request, **kwargs):
            seen.append((request.full_url, request.get_header("Authorization")))
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    fetch_account(Account("fixture", "Fixture", provider, PREFIXES[provider]), secret)
    assert seen[0][0].endswith(expected_suffix)
    assert seen[0][1] == (secret if raw_auth else "Bearer " + secret)
    if provider in {"zai", "bigmodel"}:
        assert len(seen) == 2 and seen[1][0].endswith("/api/biz/subscription/list")


# discovery and identity de-duplication


@pytest.mark.parametrize("auto", [False, True])
def test_explicit_same_key_alias_keeps_first_record_with_discovery_on_or_off(auto):
    value = settings(auto_detect=auto)
    value["accounts"].append({"id": "second", "provider": "opencode-go", "key_env": "OPENCODE_GO_API_KEY_2"})
    rows = discover(value, lambda n: "PRIVATE_SAME" if n.startswith("OPENCODE_GO_API_KEY") else "", env_names=())
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

    assert [r.id for r in discover(value, resolve)] == ["first", "distinct"] and len(calls) == 2


def test_full_explicit_capacity_does_not_scan_unrelated_credentials():
    value = {
        "auto_detect": True,
        "accounts": [
            {"id": f"alias{i}", "provider": "opencode-go", "key_env": f"OPENCODE_GO_API_KEY_{i}"} for i in range(4)
        ],
    }
    calls = []

    def resolve(name):
        calls.append(name)
        assert name.startswith("OPENCODE_GO_API_KEY_")
        return name

    assert len(discover(value, resolve, env_names=("DEEPSEEK_API_KEY",))) == 4 and len(calls) == 4


def test_discovery_is_opt_in_and_preserves_explicit_account_alias():
    value = {"auto_detect": False, "accounts": [{"id": "go-main", "provider": "opencode-go", "label": "Primary"}]}

    def resolve(name):
        return "PRIVATE_FIXTURE" if name.startswith("OPENCODE_GO_API_KEY") else ""

    assert len(discover(value, resolve, env_names=("OPENCODE_GO_API_KEY_2",))) == 1
    value["auto_detect"] = True
    assert len(discover(value, resolve, env_names=("OPENCODE_GO_API_KEY_2",))) == 1
    assert discover(value, resolve, env_names=())[0].name == "Primary"


def test_independent_keys_are_bounded_and_do_not_escape_in_output():
    keys = {
        "OPENCODE_GO_API_KEY": "SENTINEL_PRIMARY",
        "OPENCODE_GO_API_KEY_2": "SENTINEL_SECOND",
        "DEEPSEEK_API_KEY": "SENTINEL_DS",
        "OPENROUTER_API_KEY": "SENTINEL_OR",
        "MOONSHOT_API_KEY": "SENTINEL_MOON",
    }
    rows = discover({"auto_detect": True, "accounts": []}, lambda n: keys.get(n, ""), env_names=tuple(keys))
    assert len(rows) == 4 and rows[0].provider == "opencode-go" and all(r.discovered for r in rows)
    assert "SENTINEL_" not in repr(rows)


def test_unsupported_discovery_is_local_and_never_transmits_key(monkeypatch):
    rows = discover(
        {"auto_detect": True, "accounts": []},
        lambda n: "PRIVATE" if n == "SILICONFLOW_API_KEY" else "",
        env_names=("SILICONFLOW_API_KEY",),
    )
    assert len(rows) == 1 and rows[0].key_env == ""
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("No unknown endpoint probes"))
    assert fetch_account(rows[0], "")["status"] == "unsupported"


def full_settings():
    return {
        "auto_detect": True,
        "accounts": [
            {"id": "go", "label": "Go Primary", "provider": "opencode-go"},
            {"id": "ds", "provider": "deepseek"},
        ],
    }


def test_scoped_discovery_never_resolves_unrelated_existing_keys():
    calls = []

    def resolve(name):
        calls.append(name)
        assert name.startswith("OPENCODE_GO_API_KEY")
        return "PRIVATE"

    specs = discover(
        current_provider_settings(full_settings(), "opencode-go"),
        resolve,
        env_names=("SILICONFLOW_API_KEY", "DEEPSEEK_API_KEY", "ALIBABA_API_KEY"),
    )
    assert len(specs) == 1 and specs[0].provider == "opencode-go"
    assert calls and all(n.startswith("OPENCODE_GO_API_KEY") for n in calls)


@pytest.mark.parametrize(
    "provider", [None, "", "deepseek-v4.1-flash", "OPENCODE_GO_API_KEY", "https://example.invalid"]
)
def test_unknown_or_model_label_is_not_a_billing_identity(provider):
    assert not current_provider_settings(full_settings(), provider)["accounts"]
    assert "opencode-go" not in provider_products(provider)


def test_openrouter_related_management_wallet_is_explicit_not_cross_vendor():
    assert provider_products("openrouter") == ("openrouter", "openrouter-credits")
    rows = current_provider_snapshot(
        {"accounts": [{"provider": "openrouter"}, {"provider": "openrouter-credits"}, {"provider": "deepseek"}]},
        "openrouter",
    )["accounts"]
    assert [r["provider"] for r in rows] == ["openrouter", "openrouter-credits"]


# refresh ownership


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

    monkeypatch.setattr("hermes_lark_streaming.details.account_fetch.fetch_accounts", fetch)
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
    summary.request(settings(), lambda name: "latest")
    assert summary._task is None and len(calls) == 2  # fresh and unchanged: no new read


@pytest.mark.asyncio
async def test_same_identity_failure_records_new_attempt_without_relabeling_last_success(monkeypatch):
    rows = iter(
        [
            {"id": "first", "status": "ok", "checked_at": "2026-10-05T04:00:00Z", "balances": []},
            {"id": "first", "status": "unavailable", "http_status": 429, "checked_at": "2026-10-05T04:05:00Z"},
            {"id": "first", "status": "unavailable", "checked_at": "2026-10-05T04:10:00Z"},
            {"id": "first", "status": "ok", "checked_at": "2026-10-05T04:15:00Z", "balances": []},
        ]
    )
    monkeypatch.setattr(
        "hermes_lark_streaming.details.account_fetch.fetch_accounts", lambda *args: {"accounts": [next(rows)]}
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


@pytest.mark.asyncio
async def test_discovery_not_repeated_on_each_delta_but_keys_still_rotate(monkeypatch):
    calls = []
    original = discover

    def observed(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr("hermes_lark_streaming.details.account_monitor.discover", observed)
    monkeypatch.setattr("hermes_lark_streaming.details.account_fetch.fetch_accounts", lambda *a: {"accounts": []})
    summary = AccountsSummary()
    value = {"accounts": [{"id": "go", "provider": "opencode-go"}]}
    summary.request(value, lambda name: "old")
    await summary._task
    for _ in range(10):
        summary.request(value, lambda name: "old")
    assert len(calls) == 1
    summary.request(value, lambda name: "new")
    assert summary._task is not None
    await summary._task


@pytest.mark.asyncio
async def test_final_wait_is_bounded_and_does_not_cancel_http_worker(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def fetch(*args):
        entered.set()
        assert release.wait(3)
        return {"accounts": []}

    monkeypatch.setattr("hermes_lark_streaming.details.account_fetch.fetch_accounts", fetch)
    reader = AccountsSummary()
    reader.request(settings(), lambda name: "key")
    assert await asyncio.to_thread(entered.wait, 3)
    task = reader._task
    await reader.finish(0.01)
    assert not task.done() and not task.cancelled()
    release.set()
    await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
async def test_terminal_identity_validation_discards_old_account_without_starting_read(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hermes_lark_streaming.details.account_fetch.fetch_accounts",
        lambda *args: calls.append(1) or {"accounts": [{"status": "ok", "provider": "opencode-go"}]},
    )
    reader = AccountsSummary()
    reader.request(settings(), lambda name: "old")
    await reader._task
    reader.request(settings(), lambda name: "new", allow_read=False)
    assert reader._task is None and reader.snapshot()["status"] == "pending" and len(calls) == 1


def test_snapshot_age_and_copy_are_isolated(monkeypatch):
    summary = AccountsSummary()
    summary._cached = {"status": "snapshot", "accounts": [{"label": "Fixture"}]}
    summary._at = 0
    monkeypatch.setattr("hermes_lark_streaming.details.account_monitor.time.monotonic", lambda: 301)
    value = summary.snapshot()
    assert value["stale"] is True
    value["accounts"][0]["label"] = "Changed"
    assert summary.snapshot()["accounts"][0]["label"] == "Fixture"
    assert AccountsSummary(ttl_s=1000).snapshot()["stale"] is False
