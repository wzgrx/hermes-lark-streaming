"""Account management-plane fixtures; no live credentials or account writes."""

import json
from copy import deepcopy

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.reference import build_account_panel
from hermes_lark_streaming.footer.account_adapters import money, parse_subscription
from hermes_lark_streaming.footer.account_catalog import ENDPOINTS, PREFIXES, catalog
from hermes_lark_streaming.footer.accounts import Account, AccountsSummary, discover, fetch_account, parse_account


def empty_settings():
    return {"auto_detect": True, "accounts": []}


def test_catalog_keeps_inventory_separate_from_actual_adapters():
    value = catalog()
    assert value["inventory_entries"] >= 226 and value["implemented_products"] == 10
    assert not value["all_providers_implemented"]
    assert len({r["id"] for r in value["products"]}) == len(value["products"])
    assert all(ENDPOINTS[r["id"]] == r["endpoint"] for r in value["products"] if r["adapter"] == "implemented")
    before = deepcopy(value)
    assert catalog() == before


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "1e-999999999", "1e999999999", "-1"])
def test_invalid_or_unbounded_money_stays_unknown(value):
    assert money(value) is None


@pytest.mark.parametrize("provider,currency", [("moonshot", "CNY"), ("moonshot-global", "USD")])
def test_moonshot_separates_cash_voucher_and_available(provider, currency):
    body = {"code": 0, "status": True, "data": {"available_balance": 3.5, "cash_balance": -1, "voucher_balance": 3.5}}
    row = parse_account(provider, body)
    assert row["status"] == "ok"
    assert [b["currency"] for b in row["balances"]] == [currency] * 3
    assert [b["amount"] for b in row["balances"]] == ["3.5", "-1", "3.5"]


@pytest.mark.parametrize("code", [False, 123, 401, None])
def test_http_200_failed_moonshot_business_envelope_is_not_balance(code):
    assert parse_account("moonshot", {"code": code, "data": {"available_balance": 99}})["status"] == "unavailable"


def test_openrouter_key_expiry_and_wallet_remain_separate():
    key = parse_account("openrouter", {"data": {"limit_remaining": 4.5, "expires_at": "2027-01-01T00:00:00Z"}})
    wallet = parse_account("openrouter-credits", {"data": {"total_credits": 10.5, "total_usage": 5.25}})
    assert key["key_expires_at"] == "2027-01-01T00:00:00+00:00"
    assert "subscription_expires_at" not in key
    assert wallet["balances"] == [{"kind": "account_credit_balance", "currency": "USD", "amount": "5.25"}]


@pytest.mark.parametrize("provider", ["minimax", "minimax-cn"])
def test_minimax_ambiguous_legacy_usage_count_is_not_guessed(provider):
    base = {
        "model_remains": [
            {
                "model_name": "Fixture",
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
    row = parse_account(provider, body)
    assert [w["name"] for w in row["windows"]] == ["rolling", "weekly", "mcp_monthly"]
    body["success"] = False
    assert parse_account(provider, body)["status"] == "unavailable"


@pytest.mark.parametrize("value", [True, None, "47", -1, float("nan"), float("inf"), 10**400])
def test_bad_new_adapter_percentage_is_not_zero(value):
    body = {"model_remains": [{"current_interval_remaining_percent": value}]}
    assert parse_account("minimax", body)["windows"] == []


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


def test_discovery_is_opt_in_and_preserves_explicit_account_alias():
    settings = {"auto_detect": False, "accounts": [{"id": "go-main", "provider": "opencode-go", "label": "Primary"}]}
    def resolve(name):
        return "PRIVATE_FIXTURE" if name.startswith("OPENCODE_GO_API_KEY") else ""
    assert len(discover(settings, resolve, env_names=("OPENCODE_GO_API_KEY_2",))) == 1
    settings["auto_detect"] = True
    assert len(discover(settings, resolve, env_names=("OPENCODE_GO_API_KEY_2",))) == 1
    assert discover(settings, resolve, env_names=())[0].name == "Primary"


def test_independent_keys_are_bounded_and_do_not_escape_in_output():
    keys = {
        "OPENCODE_GO_API_KEY": "SENTINEL_PRIMARY",
        "OPENCODE_GO_API_KEY_2": "SENTINEL_SECOND",
        "DEEPSEEK_API_KEY": "SENTINEL_DS",
        "OPENROUTER_API_KEY": "SENTINEL_OR",
        "MOONSHOT_API_KEY": "SENTINEL_MOON",
    }
    rows = discover(empty_settings(), lambda name: keys.get(name, ""), env_names=tuple(keys))
    assert len(rows) == 4 and rows[0].provider == "opencode-go"
    assert all(row.discovered for row in rows)
    assert "SENTINEL_" not in repr(rows)


def test_unsupported_discovery_is_local_and_never_transmits_key(monkeypatch):
    rows = discover(
        empty_settings(),
        lambda name: "PRIVATE" if name == "SILICONFLOW_API_KEY" else "",
        env_names=("SILICONFLOW_API_KEY",),
    )
    assert len(rows) == 1 and rows[0].key_env == ""
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("No unknown endpoint probes"))
    assert fetch_account(rows[0], "")["status"] == "unsupported"


@pytest.mark.parametrize(
    "provider,secret,expected_suffix,raw_auth",
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


@pytest.mark.asyncio
async def test_same_identity_outage_retains_labeled_previous_success(monkeypatch):
    responses = iter(
        [
            {"status": "snapshot", "accounts": [{"id": "go", "status": "ok", "checked_at": "old", "windows": []}]},
            {"status": "snapshot", "accounts": [{"id": "go", "status": "unavailable"}]},
        ]
    )
    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.fetch_accounts", lambda *args: next(responses))
    settings = {"accounts": [{"id": "go", "provider": "opencode-go"}]}
    summary = AccountsSummary()
    summary.request(settings, lambda name: "key")
    await summary._task
    summary._at = float("-inf")
    summary.request(settings, lambda name: "key")
    await summary._task
    assert summary.snapshot()["accounts"][0]["stale"]
    assert summary.snapshot()["accounts"][0]["checked_at"] == "old"


@pytest.mark.asyncio
async def test_discovery_not_repeated_on_each_delta_but_keys_still_rotate(monkeypatch):
    calls = []
    original = discover

    def observed(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.discover", observed)
    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.fetch_accounts", lambda *args: {"accounts": []})
    summary = AccountsSummary()
    settings = {"accounts": [{"id": "go", "provider": "opencode-go"}]}
    summary.request(settings, lambda name: "old")
    await summary._task
    for _ in range(10):
        summary.request(settings, lambda name: "old")
    assert len(calls) == 1
    summary.request(settings, lambda name: "new")
    assert summary._task is not None
    await summary._task


def test_card_expiry_unknowns_sources_and_key_expiry_are_distinct():
    rows = [
        {"label": "Go", "provider": "opencode-go", "status": "ok", "windows": [], "balances": []},
        {
            "label": "Key",
            "provider": "openrouter",
            "status": "ok",
            "key_expires_at": "2027-01-01T00:00:00Z",
            "subscription_expires_at": "2026-12-01T00:00:00Z",
            "subscription_source": "manual",
            "discovered": True,
        },
    ]
    text = json.dumps(build_account_panel({"accounts": rows}, "Asia/Shanghai"), ensure_ascii=False)
    assert "2027-01-01 08:00" in text and "2026-12-01 08:00" in text
    assert "接口未返回" in text and "手动记录" in text and "不是订阅到期" in text
    assert "自动候选" in text
    assert inspect_card({"schema": "2.0", "body": {"elements": [build_account_panel({"accounts": rows}, "UTC")]}}).safe
