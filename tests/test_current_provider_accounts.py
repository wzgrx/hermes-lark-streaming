"""Only the observed provider belongs in the message; all discovery stays in CLI."""

import asyncio
import json
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.reference import build_account_panel
from hermes_lark_streaming.footer.accounts import (
    AccountsSummary,
    current_provider_settings,
    current_provider_snapshot,
    discover,
    provider_products,
)
from hermes_lark_streaming.streaming.runtime_footer import RuntimeFooterController
from hermes_lark_streaming.streaming.session import SessionState


def settings():
    return {
        "auto_detect": True,
        "accounts": [
            {"id": "go", "label": "Go Primary", "provider": "opencode-go"},
            {"id": "ds", "provider": "deepseek"},
        ],
    }


def snapshot(provider="opencode-go", status="ok"):
    return {
        "scope": "active_provider",
        "active_provider": provider,
        "accounts": [
            {
                "provider": "opencode-go",
                "label": "Go Primary",
                "status": status,
                "checked_at": "2026-10-05T08:46:00Z",
                "windows": [
                    {"name": name, "remaining_percent": number, "reset_at": "2026-10-20T06:26:40Z"}
                    for name, number in (("rolling", 100), ("weekly", 100), ("monthly", 53))
                ],
            },
            {"provider": "siliconflow", "label": "Never show SF", "status": "unsupported"},
            {"provider": "alibaba", "label": "Never show Alibaba", "status": "unsupported"},
        ],
    }


def controller(monkeypatch, tmp_path):
    ctrl = RuntimeFooterController()
    ctrl._cfg = SimpleNamespace(
        card_layout="reference",
        footer_history={},
        footer_enabled=True,
        footer_details=True,
        footer_accounts={**settings(), "enabled": True, "allowed_chats": ["fixture"]},
        reference_resources_enabled=False,
        show_tool_use=False,
        reference_agent_name="Fixture",
        reference_design_version=2,
    )
    ctrl._reference_host = SimpleNamespace(snapshot=lambda: {})
    ctrl._reference_history = ctrl._reference_accounts = None
    ctrl._profile_home = tmp_path
    ctrl._credential_scope = nullcontext
    return ctrl


def session(terminal=False):
    return SimpleNamespace(
        chat_id="fixture",
        state=SessionState.COMPLETED if terminal else SessionState.STREAMING,
        tool_use=SimpleNamespace(build_display_steps=lambda: []),
        tool_calls_prior=0,
        tools_done_prior=0,
        tools_failed_prior=0,
    )


def test_scoped_discovery_never_resolves_unrelated_existing_keys():
    calls = []

    def resolve(name):
        calls.append(name)
        assert name.startswith("OPENCODE_GO_API_KEY")
        return "PRIVATE"

    specs = discover(
        current_provider_settings(settings(), "opencode-go"),
        resolve,
        env_names=("SILICONFLOW_API_KEY", "DEEPSEEK_API_KEY", "ALIBABA_API_KEY"),
    )
    assert len(specs) == 1 and specs[0].provider == "opencode-go"
    assert calls and all(name.startswith("OPENCODE_GO_API_KEY") for name in calls)


@pytest.mark.parametrize(
    "provider", [None, "", "deepseek-v4.1-flash", "OPENCODE_GO_API_KEY", "https://example.invalid"]
)
def test_unknown_or_model_label_is_not_a_go_billing_identity(provider):
    scoped = current_provider_settings(settings(), provider)
    assert not scoped["accounts"]
    assert "opencode-go" not in provider_products(provider)


def test_openrouter_related_management_wallet_is_explicit_not_cross_vendor():
    assert provider_products("openrouter") == ("openrouter", "openrouter-credits")
    rows = current_provider_snapshot(
        {
            "accounts": [
                {"provider": "openrouter"},
                {"provider": "openrouter-credits"},
                {"provider": "deepseek"},
            ]
        },
        "openrouter",
    )["accounts"]
    assert [row["provider"] for row in rows] == ["openrouter", "openrouter-credits"]


def test_renderer_has_three_quota_cells_and_two_fact_cells_without_other_providers():
    before = snapshot()
    panel = build_account_panel(before, "Asia/Shanghai")
    text = json.dumps(panel, ensure_ascii=False)
    assert "siliconflow" not in text and "Alibaba" not in text and "自动候选" not in text
    assert all(word in text for word in ("OpenCode Go", "100%", "53%", "周剩余", "月剩余", "订阅到期", "账户余额"))
    assert panel["element_id"] == "ref_accounts" and panel["expanded"] is False
    grids = [row for row in panel["elements"] if row.get("tag") == "column_set"]
    assert [len(row["columns"]) for row in grids] == [3, 2]
    assert all(row["flex_mode"] == "none" for row in grids)
    assert before == snapshot() and "Go Primary" in text


def test_missing_window_is_a_dash_not_a_zero_and_each_reset_is_independent():
    value = snapshot()
    value["accounts"][0]["windows"].pop()
    text = json.dumps(build_account_panel(value, "UTC"), ensure_ascii=False)
    assert "Month left</font>  **—**" in text and "0%" not in text.replace("100%", "")
    assert "Reset —" in text and "Reset 10-20 06:26" in text


@pytest.mark.parametrize(
    "status,expected",
    [
        ("pending", "待查询"),
        ("unsupported", "待接入"),
        ("missing_credentials", "待配置凭据"),
        ("unavailable", "查询失败"),
    ],
)
def test_unknown_fields_remain_visible_with_accurate_state(status, expected):
    value = snapshot(status=status)
    value["accounts"][0]["windows"] = []
    text = json.dumps(build_account_panel(value, "UTC"), ensure_ascii=False)
    assert all(word in text for word in ("订阅到期", "账户余额", expected))


def test_terminal_snapshot_does_not_promise_refresh_for_frozen_card():
    value = snapshot(status="pending")
    value["terminal"] = True
    text = json.dumps(build_account_panel(value, "UTC"), ensure_ascii=False)
    assert "后续消息刷新" in text and "Query pending" not in text


@pytest.mark.parametrize("bad", [None, True, 7, {}, [None]])
def test_malformed_account_collection_has_compact_unknown_facts(bad):
    value = {"scope": "active_provider", "active_provider": "opencode-go", "accounts": bad}
    panel = build_account_panel(value, "UTC")
    assert "订阅到期" in json.dumps(panel, ensure_ascii=False)
    assert inspect_card({"schema": "2.0", "body": {"elements": [panel]}}).safe


def test_current_provider_four_account_budget_and_signed_wallet():
    value = snapshot()
    value["accounts"] = [dict(value["accounts"][0]) for _ in range(4)]
    for row in value["accounts"]:
        row["balances"] = [{"kind": "account_credit_balance", "amount": "-1.50", "currency": "USD"}]
    panel = build_account_panel(value, "UTC")
    assert "USD -1.50" in json.dumps(panel, ensure_ascii=False)
    assert inspect_card({"schema": "2.0", "body": {"elements": [panel]}}).safe


def test_no_request_provider_hides_panel_and_queries_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "hermes_lark_streaming.footer.accounts.AccountsSummary", lambda: pytest.fail("No default provider")
    )
    ctrl = controller(monkeypatch, tmp_path)
    result = ctrl._reference_snapshot(session(), {})
    assert result["reference"]["accounts"] is None


def test_concurrent_provider_readers_and_fallback_never_show_other_provider(monkeypatch, tmp_path):
    readers = []

    class Reader:
        _task = None

        def __init__(self):
            self.settings = None
            readers.append(self)

        def request(self, settings, resolve, *, allow_read=True):
            self.settings = settings

        def snapshot(self):
            return {"accounts": [{"provider": p, "status": "ok"} for p in ("opencode-go", "deepseek", "siliconflow")]}

    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.AccountsSummary", Reader)
    ctrl = controller(monkeypatch, tmp_path)
    for provider in ("opencode-go", "deepseek", "opencode-go"):
        data = ctrl._reference_snapshot(session(), {"provider": provider})
        assert [row["provider"] for row in data["reference"]["accounts"]["accounts"]] == [provider]
    assert len(readers) == 2
    assert readers[0].settings["_provider_products"] == ("opencode-go",)
    assert readers[1].settings["_provider_products"] == ("deepseek",)


@pytest.mark.asyncio
async def test_final_wait_is_bounded_and_does_not_cancel_http_worker(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def fetch(*args):
        entered.set()
        assert release.wait(3)
        return {"accounts": []}

    monkeypatch.setattr("hermes_lark_streaming.footer.accounts.fetch_accounts", fetch)
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
        "hermes_lark_streaming.footer.accounts.fetch_accounts",
        lambda *args: calls.append(1) or {"accounts": [{"status": "ok", "provider": "opencode-go"}]},
    )
    reader = AccountsSummary()
    reader.request(settings(), lambda name: "old")
    await reader._task
    reader.request(settings(), lambda name: "new", allow_read=False)
    assert reader._task is None and reader.snapshot()["status"] == "pending" and len(calls) == 1


def test_provider_snapshot_age_is_visible_not_only_failed_row_age():
    value = snapshot()
    value["stale"] = True
    assert "上次快照 · 待刷新" in json.dumps(build_account_panel(value, "UTC"), ensure_ascii=False)


@pytest.mark.parametrize("status,expected", [
    ("not_active", "API 未发现有效套餐"), ("unavailable", "订阅权益查询失败"),
])
def test_subscription_lookup_failure_or_no_active_plan_is_not_generic_missing(status, expected):
    value = snapshot()
    value["accounts"][0]["subscription_status"] = status
    assert expected in json.dumps(build_account_panel(value, "UTC"), ensure_ascii=False)
