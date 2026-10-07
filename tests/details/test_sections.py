"""build_sections and the collector: Section/Metric values, switches, privacy and unknown-not-zero."""

# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from hermes_lark_streaming.card.model import Metric, Section
from hermes_lark_streaming.details import DetailsCollector, DetailsConfig, TurnTelemetry, build_sections
from hermes_lark_streaming.details.accounts import current_provider_snapshot
from hermes_lark_streaming.details.ledger import UsageLedger
from hermes_lark_streaming.details.sections.accounts import accounts_section
from hermes_lark_streaming.details.sections.resources import resources_section
from hermes_lark_streaming.details.sections.usage import usage_section

from .test_telemetry import complete, event, turn

RESET = "2026-10-20T06:26:40Z"
NOW = datetime(2026, 10, 20, 4, 12, 40, tzinfo=UTC).timestamp()  # 2h14m before the reset


def by_label(section: Section) -> dict[str, Metric]:
    return {m.label: m for m in section.metrics}


def text(section: Section) -> str:
    return " ".join([section.title, *section.notes, *(f"{m.label} {m.value} {m.hint}" for m in section.metrics)])


def snapshot(provider="opencode-go", status="ok", **row):
    return {
        "scope": "active_provider",
        "active_provider": provider,
        "accounts": [
            {
                "provider": provider,
                "label": "Go Primary",
                "status": status,
                "checked_at": "2026-10-05T08:46:00Z",
                "windows": [
                    {"name": name, "remaining_percent": n, "reset_at": RESET}
                    for name, n in (("rolling", 62), ("weekly", 100), ("monthly", 53))
                ],
                **row,
            },
            {"provider": "siliconflow", "label": "Never show SF", "status": "unsupported"},
        ],
    }


# usage


def test_turn_metrics_from_telemetry():
    state = TurnTelemetry()
    p = event(request={"reasoning_effort": "max"})
    state.observe("pre_api_request", p)
    complete(state, p)
    section = usage_section(state.snapshot(), None)
    assert (section.key, section.title, section.title_en, section.layout) == ("usage", "用量", "Usage", "grid")
    m = by_label(section)
    assert m["输入"].value == "100" and m["输出"].value == "7"
    assert m["缓存"].value == "70.0%" and m["缓存"].ratio == pytest.approx(0.7)
    assert m["思考"].value == "max"
    # model, context, provider and wall time live in the footer and status line, not here
    assert not {"上下文（末次）", "服务商", "总耗时", "缓存读取", "请求"} & set(m)
    assert "统计不完整" not in section.notes


def test_partial_turn_shows_floor_and_partial_note():
    state = TurnTelemetry()
    first = event()
    state.observe("pre_api_request", first)
    state.observe("api_request_error", first)
    second = event("other", started_at=first["started_at"] + 1)
    state.observe("pre_api_request", second)
    complete(state, second)
    section = usage_section(state.snapshot(), None)
    m = by_label(section)
    assert m["缓存"].value == "未知" and m["缓存"].ratio is None  # count known, rate not
    assert m["缓存读取"].value == "≥70"
    assert "统计不完整" in section.notes and "服务商路径 opencode-go → other" in section.notes
    assert m["请求"].value == "2 · 错误 1"


def test_cache_hit_lower_bound_is_labelled_and_never_rounded_up():
    section = usage_section(turn([40, None]).snapshot(), None)
    metric = by_label(section)["缓存"]
    assert (metric.value, metric.label_en) == ("≥20.0%", "Cache") and metric.ratio == 0.2
    assert by_label(section)["缓存读取"].value == "≥40"


def test_missing_telemetry_is_unknown_not_zero():
    section = usage_section(None, None)
    m = by_label(section)
    assert m["输入"].value == m["输出"].value == m["缓存"].value == "未知"
    assert "本轮统计待采集" in section.notes
    assert not any(x.ratio for x in section.metrics)


def test_history_rows_partial_models_and_status_notes():
    history = {
        "status": "ok",
        "timezone": "Asia/Shanghai",
        "since": "2026-09-01",
        "today": {"tokens": 1200, "partial": False},
        "month": {"tokens": 1_550_000, "partial": True},
        "total": {"tokens": None, "partial": True},
        "models": [{"model": "m" * 80, "subscription": "Go plan", "tokens": 5000, "partial": False}] * 4,
    }
    section = usage_section({}, history)
    m = by_label(section)
    assert m["今日"].value == "1.2k" and m["本月"].value == "≥1.55M" and m["总计"].value == "未知"
    assert m["总计"].group == "累计" and m["今日"].group == m["总计"].group
    models = [x for x in section.metrics if x.hint == "Go plan"]
    assert len(models) == 3 and models[0].label == "m" * 48  # top three, bounded
    assert any("下限" in n for n in section.notes)
    assert not [x for x in usage_section({}, history, show_models=False).metrics if x.hint == "Go plan"]
    assert "尚无已记录的主请求" in usage_section({}, {"status": "no_history"}).notes
    assert "本轮历史快照尚未就绪；后续消息可重试" in usage_section({}, {"status": "pending"}, terminal=True).notes
    assert "正在读取本机历史" in usage_section({}, {"status": "pending"}).notes


def test_untrusted_labels_never_reach_a_section():
    section = usage_section(
        {"provider": "sk-secret-123", "api_mode": "https://evil.invalid", "routes": ["a", "b"]}, None
    )
    assert "sk-secret" not in text(section) and "evil.invalid" not in text(section)


# resources


def test_resources_have_ratios_where_meaningful_and_unknown_elsewhere():
    host = {
        "cpu_percent": 12.5,
        "gpu_percent": 40,
        "gpu_temperature": 61,
        "gpu_used_gib": 2.0,
        "gpu_total_gib": 8.0,
        "ram_used_gib": 8.0,
        "ram_total_gib": 16.0,
        "disk_used_gib": 100.0,
        "disk_total_gib": 400.0,
        "scope": "WSL",
        "sampled_at": "2026-10-20 12:00:00 +0800",
    }
    section = resources_section(host)
    m = by_label(section)
    assert section.key == "resources" and section.layout == "grid"
    assert m["显存"].value == "2.0/8G" and m["显存"].ratio == 0.25
    assert m["内存"].ratio == 0.5 and m["磁盘"].ratio == 0.25 and m["GPU"].ratio == pytest.approx(0.4)
    assert m["GPU"].value.endswith("·61°C") and "GPU 温度" not in m  # temperature rides along with GPU
    assert "WSL" in section.title and section.notes == ()  # sampled and complete: nothing to explain


@pytest.mark.parametrize("host", [None, {}, {"unavailable": True}, {"gpu_percent": True, "ram_total_gib": 0}])
def test_resources_missing_values_are_unknown(host):
    section = resources_section(host)
    assert all(m.ratio is None for m in section.metrics)
    assert by_label(section)["内存"].value == "未知" and by_label(section)["GPU"].value == "未知"
    assert section.notes


# accounts


def test_quota_windows_are_bars_with_value_ratio_and_reset_hint():
    section = accounts_section(snapshot(), "Asia/Shanghai", now=NOW)
    assert (section.key, section.layout, section.title, section.title_en) == (
        "accounts",
        "bars",
        "OpenCode Go · 订阅与额度",
        "Subscription / quota",
    )
    m = by_label(section)
    assert (m["5小时"].value, m["5小时"].ratio, m["5小时"].hint) == ("38%", pytest.approx(0.38), "2h14m 后重置")
    assert m["每周"].value == "0%" and m["每月"].value == "47%"
    assert "siliconflow" not in text(section).lower() and "Never show" not in text(section)
    assert "Go Primary" in section.notes
    assert "订阅到期：未知（API 未返回）" in section.notes
    assert "快照 10-05 16:46" in section.notes
    assert section.notes[-1] == "Asia/Shanghai"  # reset clocks are in this zone


def test_far_resets_show_the_account_clock_in_the_configured_timezone():
    later = datetime(2026, 10, 16, 4, 12, 40, tzinfo=UTC).timestamp()
    shanghai = by_label(accounts_section(snapshot(), "Asia/Shanghai", now=later))["每月"].hint
    utc = by_label(accounts_section(snapshot(), "UTC", now=later))["每月"].hint
    assert shanghai == "10-20 14:26 重置" and utc == "10-20 06:26 重置"


@pytest.mark.parametrize("bad", [None, {}, [], 7, ""])
def test_invalid_timezone_renders_as_utc(bad):
    section = accounts_section(snapshot(), bad, now=NOW - 5 * 86400)
    assert "10-20 06:26" in by_label(section)["每月"].hint and "UTC" in section.notes[-1]


def test_past_reset_missing_reset_and_missing_window_are_not_invented():
    row = {
        "windows": [
            {"name": "rolling", "remaining_percent": 10, "reset_at": "2020-01-01T00:00:00Z"},
            {"name": "weekly", "remaining_percent": 10},
        ]
    }
    m = by_label(accounts_section(snapshot(**row), "UTC", now=NOW))
    assert m["5小时"].hint == "已过重置时间 · 待刷新" and m["每周"].hint == "重置时间未知"
    assert m["每月"].value == "未知" and m["每月"].ratio is None  # opencode-go always lists its three windows


def test_used_percent_over_100_clamps_the_bar_only():
    row = {"windows": [{"name": "rolling", "used_percent": 110, "remaining_percent": 0, "reset_at": RESET}]}
    metric = by_label(accounts_section(snapshot(**row), "UTC", now=NOW))["5小时"]
    assert metric.value == "110%" and metric.ratio == 1.0


def test_four_windows_are_not_silently_truncated():
    rows = [
        {"name": n, "remaining_percent": v, "reset_at": RESET}
        for n, v in (("rolling", 70), ("weekly", 80), ("monthly", 90), ("mcp_monthly", 40))
    ]
    m = by_label(accounts_section(snapshot("zai", windows=rows), "UTC", now=NOW))
    assert [x.value for x in m.values() if x.ratio is not None] == ["30%", "20%", "10%", "60%"]
    assert "MCP 每月" in m


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("pending", "待查询"),
        ("unsupported", "待接入"),
        ("missing_credentials", "待配置凭据"),
        ("unavailable", "查询失败"),
    ],
)
def test_failed_or_unqueried_states_keep_unknowns_with_an_accurate_reason(status, reason):
    section = accounts_section(snapshot(status=status, windows=[]), "UTC", now=NOW)
    assert reason in text(section)
    assert "API 未返回" not in text(section) or status == "ok"
    assert by_label(section)["账户余额"].value == "未知" and not [m for m in section.metrics if m.ratio is not None]


def test_terminal_pending_does_not_promise_a_refresh_of_the_frozen_card():
    section = accounts_section(snapshot(status="pending", windows=[]), "UTC", terminal=True, now=NOW)
    assert "本轮快照未就绪；后续消息刷新" in text(section) and "待查询" not in text(section)


def test_http_failure_reason_and_retained_snapshot():
    failed = accounts_section(snapshot(status="unavailable", http_status=429, windows=[]), "Asia/Shanghai", now=NOW)
    assert "查询失败 · API 限流 · HTTP 429" in failed.notes
    retained = accounts_section(
        snapshot(stale=True, last_http_status=401, last_attempt_at="2026-10-05T04:10:00Z"), "Asia/Shanghai", now=NOW
    )
    assert "保留上次成功快照；最近读取失败 · HTTP 401 · 10-05 12:10" in retained.notes
    assert "快照 10-05 16:46" in retained.notes
    both = accounts_section({**snapshot(), "stale": True}, "UTC", now=NOW)
    assert "上次快照 · 待刷新" in both.notes


def test_endpoint_retired_is_explained():
    section = accounts_section(
        snapshot("siliconflow", status="unsupported", reason="endpoint_retired", retired_on="2026-08-14", windows=[]),
        "UTC",
        now=NOW,
    )
    assert "官方账户接口已退役 · 2026-08-14" in text(section)


def test_subscription_expiry_sources_and_distinct_facts():
    manual = accounts_section(
        snapshot(subscription_expires_at="2026-12-01T00:00:00Z", subscription_source="manual"), "Asia/Shanghai", now=NOW
    )
    assert "订阅到期：2026-12-01 08:00（手动记录）" in manual.notes
    api = accounts_section(
        snapshot(subscription_expires_at="2026-12-01T00:00:00Z", subscription_source="provider_api"), "UTC", now=NOW
    )
    assert "订阅到期：2026-12-01 00:00（API 时间）" in api.notes
    plan = accounts_section(
        snapshot(
            "zai",
            subscription_expires_on="2026-12-01",
            subscription_renews_on="2026-11-01",
            key_expires_at="2027-01-01T00:00:00+00:00",
        ),
        "UTC",
        now=NOW,
    )
    assert "套餐有效期至：2026-12-01（API 日期，时区未标注）" in plan.notes
    assert "自动续费日期：2026-11-01（不是最终到期）" in plan.notes
    assert "Key 到期：2027-01-01 00:00（不是订阅到期）" in plan.notes
    for status, why in (("not_active", "API 未发现有效套餐"), ("unavailable", "订阅权益查询失败")):
        assert (
            f"订阅到期：未知（{why}）" in accounts_section(snapshot(subscription_status=status), "UTC", now=NOW).notes
        )


def test_balances_signed_wallet_key_limit_and_malformed_input():
    wallet = accounts_section(
        snapshot("openrouter", balances=[{"kind": "account_credit_balance", "currency": "USD", "amount": "-1.50"}]),
        "UTC",
        now=NOW,
    )
    assert by_label(wallet)["账户积分余额"].value == "USD -1.50" and "账户余额" not in by_label(wallet)
    key_only = accounts_section(
        snapshot(
            "openrouter",
            balances=[{"kind": "key_credit_remaining", "currency": "USD", "amount": "4.5"}],
            key_limit_unset=True,
        ),
        "UTC",
        now=NOW,
    )
    m = by_label(key_only)
    assert m["Key 限额"].value == "USD 4.5" and m["账户余额"].value == "未知"
    assert "Key 未设置限额（不是无限余额）" in key_only.notes
    for bad in (None, True, 7, {}, [None], [{"amount": None}], [{"amount": "NaN"}]):
        assert by_label(accounts_section(snapshot(balances=bad), "UTC", now=NOW))["账户余额"].value == "未知"


def test_multiple_accounts_are_named_and_bounded():
    value = snapshot()
    value["accounts"] = [dict(value["accounts"][0], label=f"Go {i}") for i in range(6)]
    section = accounts_section(value, "UTC", now=NOW)
    assert "Go 0 · 5小时" in by_label(section) and "Go 4 · 5小时" not in by_label(section)
    assert any(n.startswith("Go 3：") for n in section.notes) and not any(n.startswith("Go 4：") for n in section.notes)


@pytest.mark.parametrize("bad", [None, True, 7, {}, [None]])
def test_malformed_account_collection_has_a_compact_unknown_section(bad):
    section = accounts_section({"scope": "active_provider", "active_provider": "opencode-go", "accounts": bad}, "UTC")
    assert section.metrics == () and "当前订阅商暂无配置账户快照" in section.notes


def test_discovered_candidates_are_labelled():
    assert "自动候选，非显式配置" in accounts_section(snapshot(discovered=True), "UTC", now=NOW).notes


# build_sections


def full_config(**kw):
    base = {"resources": True, "accounts": True, "history": True, "account_chats": ("chat",), "timezone": "UTC"}
    return DetailsConfig(**{**base, **kw})


def test_each_section_is_independently_switchable():
    host, hist = {"cpu_percent": 1}, {"status": "no_history"}
    sources = {"turn": {}, "history": hist, "host": host, "accounts": snapshot(), "chat_id": "chat", "now": NOW}
    keys = lambda c: [s.key for s in build_sections(c, **sources)]  # noqa: E731
    assert keys(full_config()) == ["usage", "resources", "accounts"]
    assert keys(full_config(usage=False)) == ["resources", "accounts"]
    assert keys(full_config(resources=False)) == ["usage", "accounts"]
    assert keys(full_config(accounts=False)) == ["usage", "resources"]
    assert keys(DetailsConfig(usage=False)) == []


def test_history_rows_only_when_history_is_enabled():
    sources = {"turn": {}, "history": {"status": "no_history"}}
    assert any("尚无已记录" in n for n in build_sections(DetailsConfig(history=True), **sources)[0].notes)
    assert not any("尚无已记录" in n for n in build_sections(DetailsConfig(), **sources)[0].notes)


@pytest.mark.parametrize(
    ("chat", "allowed", "account_chats", "expect"),
    [
        ("chat", (), ("chat",), ["usage", "resources", "accounts"]),
        ("other", (), ("chat",), ["usage", "resources"]),  # accounts need explicit membership
        ("chat", (), (), ["usage", "resources"]),
        ("", (), ("chat",), ["usage", "resources"]),
        ("chat", ("chat",), ("chat",), ["usage", "resources", "accounts"]),
        ("other", ("chat",), ("other",), []),  # a restricted deployment shows nothing elsewhere
    ],
)
def test_chat_allow_lists(chat, allowed, account_chats, expect):
    config = full_config(allowed_chats=allowed, account_chats=account_chats)
    sections = build_sections(config, turn={}, host={}, accounts=snapshot(), chat_id=chat, now=NOW)
    assert [s.key for s in sections] == expect


def test_accounts_section_requires_a_snapshot():
    assert [s.key for s in build_sections(full_config(), turn={}, host={}, chat_id="chat")] == ["usage", "resources"]


def test_sources_may_be_callables_and_are_not_mutated():
    state = TurnTelemetry()
    p = event()
    state.observe("pre_api_request", p)
    complete(state, p)
    host = {"cpu_percent": 3.0}
    before = dict(host)
    sections = build_sections(full_config(accounts=False), turn=state.snapshot, host=lambda: host, history=None)
    assert by_label(sections[0])["输出"].value == "7" and by_label(sections[1])["CPU"].value == "3%"
    assert host == before


# collector


@pytest.mark.asyncio
async def test_collector_reads_history_resources_and_scopes_accounts(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    UsageLedger(path).record(
        "post_api_request",
        {
            "api_request_id": "r",
            "session_id": "s",
            "turn_id": "t",
            "started_at": datetime.now(UTC).timestamp() - 5,
            "usage": {"prompt_tokens": 10, "output_tokens": 1},
            "provider": "opencode-go",
            "model": "m",
        },
    )
    seen = []

    def fetch(specs, keys):
        seen.append((tuple(a.provider for a in specs), keys))
        return {
            "status": "snapshot",
            "accounts": [
                {"id": specs[0].id, "provider": specs[0].provider, "label": "Go", "status": "ok", "windows": []}
            ],
        }

    monkeypatch.setattr("hermes_lark_streaming.details.account_fetch.fetch_accounts", fetch)
    config = DetailsConfig(
        resources=True,
        accounts=True,
        history=True,
        history_path=path,
        account_chats=("chat",),
        account_rows=({"id": "go", "provider": "opencode-go"}, {"id": "ds", "provider": "deepseek"}),
    )
    collector = DetailsCollector(config, lambda name: "KEY")
    collector.request(chat_id="chat", provider="opencode-go")
    await collector.finish("chat", "opencode-go")
    sections = collector.sections(chat_id="chat", provider="opencode-go", terminal=True)
    assert [s.key for s in sections] == ["usage", "resources", "accounts"]
    assert by_label(sections[0])["今日"].value == "11" and by_label(sections[0])["总计"].value == "11"
    assert seen == [(("opencode-go",), ("KEY",))]  # the other provider's account is never queried
    # No active provider, or a chat that is not allowed: the accounts section disappears.
    assert "accounts" not in [s.key for s in collector.sections(chat_id="chat", provider="")]
    assert "accounts" not in [s.key for s in collector.sections(chat_id="nope", provider="opencode-go")]


@pytest.mark.asyncio
async def test_terminal_collector_starts_no_background_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hermes_lark_streaming.details.account_fetch.fetch_accounts", lambda *a: pytest.fail("terminal must not poll")
    )
    config = DetailsConfig(
        resources=True,
        accounts=True,
        history=True,
        history_path=tmp_path / "x.db",
        account_chats=("chat",),
        account_rows=({"id": "go", "provider": "opencode-go"},),
    )
    collector = DetailsCollector(config, lambda name: "KEY")
    collector.request(chat_id="chat", provider="opencode-go", terminal=True)
    await asyncio.sleep(0)
    assert collector._history._task is None and collector._host._task is None
    accounts = next(
        s for s in collector.sections(chat_id="chat", provider="opencode-go", terminal=True) if s.key == "accounts"
    )
    assert "本轮快照未就绪；后续消息刷新" in accounts.notes


def test_snapshot_scoping_helper_matches_section_input():
    scoped = current_provider_snapshot({"accounts": snapshot()["accounts"]}, "opencode-go", terminal=True)
    assert [r["provider"] for r in scoped["accounts"]] == ["opencode-go"] and scoped["terminal"] is True


def test_cost_appears_only_with_a_user_price():
    data = {"model": "deepseek-v4-flash", "input_tokens": 1_000_000, "output_tokens": 100_000,
            "cache_read_tokens": 800_000}
    assert "费用" not in by_label(usage_section(data, None))
    prices = {"deepseek-v4-flash": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "currency": "¥"}}
    # 200k fresh × 1 + 800k cached × 0.1 + 100k out × 2, per million
    assert by_label(usage_section(data, None, pricing=prices))["费用"].value == "¥0.48"
    assert "费用" not in by_label(usage_section(data, None, pricing={"other": prices["deepseek-v4-flash"]}))


def test_pricing_config_drops_malformed_entries():
    from hermes_lark_streaming.details.config import DetailsConfig

    cfg = DetailsConfig.from_mapping({"pricing": {"A": {"input": 1, "output": 2}, "b": {"input": "x"}, "c": 3}})
    assert cfg.pricing == {"a": {"input": 1, "output": 2, "currency": "¥"}}
