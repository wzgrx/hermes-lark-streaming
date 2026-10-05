"""V1 layout fidelity, budgets, unknown values and single-writer lifecycle."""

from __future__ import annotations

import asyncio
import json
import re
import tomllib
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from hermes_lark_streaming import __version__
from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card, build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import (
    REFERENCE_ELEMENT_RESERVE,
    _tool_groups,
    build_reference_footer,
    build_resources,
    build_tools,
)
from hermes_lark_streaming.config import Config
from hermes_lark_streaming.controller import StreamCardController
from hermes_lark_streaming.feishu import FeishuClient
from hermes_lark_streaming.footer.history import UsageLedger
from hermes_lark_streaming.footer.history_summary import HistorySummary, read_summary
from hermes_lark_streaming.footer.host import HostSampler, parse_gpu, read_proc
from hermes_lark_streaming.footer.runtime import build_runtime_footer, runtime_actions
from hermes_lark_streaming.streaming.segments import SegmentState
from hermes_lark_streaming.streaming.session import CardSession, SessionState


def step(name="process", status="success", detail="poll 1", **extra):
    result = dict(
        name=name,
        status=status,
        title="Process (1.0 s)",
        detail=detail,
        elapsed_ms=1000,
        error="",
        output="",
        error_block=None,
        result_block=None,
    )
    result.update(extra)
    return result


def data(**extra):
    return dict(
        presentation="reference",
        model="deepseek-v4.1-flash",
        provider="opencode-go",
        requested_model="deepseek-v4.1-flash",
        response_model="deepseek-v4.1-flash",
        reasoning="max",
        api_mode="chat_completions",
        duration=301,
        first_response=2.1,
        context_used=70300,
        context_max=1000000,
        input_tokens=143800,
        output_tokens=3900,
        cache_read_tokens=69600,
        api_calls=3,
        retries=0,
        reference={
            "steps": [step("command", "error", "check env")],
            "show_tools": True,
            "resources_enabled": True,
            "host": {
                "scope": "WSL",
                "gpu_percent": 1,
                "gpu_temperature": 66,
                "gpu_used_gib": 10.5,
                "gpu_total_gib": 24,
                "cpu_percent": 17,
                "ram_used_gib": 15.5,
                "ram_total_gib": 126,
                "sampled_at": "2026-10-03 16:07:18 +0800",
            },
            "agent_name": "Hermes",
            "failed_total": 1,
        },
        **extra,
    )


def segments():
    state = SegmentState()
    state.on_tool_event(1)
    state.on_answer_delta("ANSWER preserved")
    state.finalize_segments(1)
    return state.segments


def test_packaged_and_directory_plugin_versions_match():
    root = Path(__file__).parents[1]
    manifest = yaml.safe_load((root / "plugin.yaml").read_text())
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert manifest["version"] == project["project"]["version"] == __version__


def test_completed_layout_has_three_panels_in_reference_order_no_duplicate_summary():
    d = data()
    card = build_complete_card(
        segments=segments(), all_tool_steps=d["reference"]["steps"], footer_data=d, footer_mode="enhanced"
    )
    elements = card["body"]["elements"]
    assert [e.get("element_id") for e in elements] == [
        "reference_tools",
        "reference_resources",
        None,
        "footer_details",
        "footer_agent",
    ]
    panel = elements[3]
    assert "70.3k/1.0M (7%)" in panel["header"]["title"]["content"]
    assert "143.8k" in json.dumps(panel) and "48.4%" in json.dumps(panel)
    assert all(not e["expanded"] for e in elements if e["tag"] == "collapsible_panel")
    assert inspect_card(card).safe


@pytest.mark.parametrize(
    "phase",
    [
        "processing",
        "answer",
        "thinking",
        "tool",
        "waiting",
        "compression",
        "summary_returned",
        "summary_failed",
        "provider_switch",
        "request_error",
    ],
)
def test_runtime_keeps_body_level_anchor_no_false_completion_and_expansion_reset(phase):
    d = data(runtime_phase=phase)
    elements = build_runtime_footer(d)
    assert elements[0]["element_id"] == "loading_icon" and elements[0]["content"].strip()
    text = json.dumps(elements)
    assert "Answer completed" not in text and "回答已完成" not in json.dumps(elements, ensure_ascii=False)
    actions = runtime_actions(elements, reference=True)
    assert "header" in actions[-1]["params"]["partial_element"]
    assert all("expanded" not in a["params"]["partial_element"] for a in actions)


def test_only_identical_adjacent_output_free_successful_polls_merge():
    assert len(_tool_groups([step(), step()])) == 1
    for second in (step(status="error"), step(detail="poll 2"), step(output="real output")):
        assert len(_tool_groups([step(), second])) == 2
    assert len(_tool_groups([step(name="command"), step(name="command")])) == 2


def test_failure_not_hidden_behind_many_later_successes_and_prior_failure_stays():
    steps = [step("command", "error", "first")] + [step("command", detail=str(i)) for i in range(90)]
    p = build_tools({"duration": 301}, {"steps": steps, "tools_prior": 3, "done_prior": 3, "failed_prior": 1})
    assert "94/94" in p["header"]["title"]["content"]
    assert "2 failed" in p["header"]["title"]["content"]
    rows = [e for e in p["elements"] if e["tag"] == "column_set"]
    assert rows[0]["background_style"] == "red-50" and len(rows) == 8


def test_unknown_cache_cost_and_identity_are_not_invented():
    panel = build_reference_footer({})[0]
    s = json.dumps(panel, ensure_ascii=False)
    assert "未提供" in s and "费用" in s and "¥" not in s and "0%" not in s
    assert "上下文未提供" in s


def test_raw_detail_redacts_credentials_html_and_does_not_duplicate_title_time():
    s = step("command", detail="TOKEN=PRIVATE sk-fixturesecret <at>user</at>")
    p = build_tools({}, {"steps": [s]})
    content = json.dumps(p)
    assert "PRIVATE" not in content and "fixturesecret" not in content and "<at>" not in content
    assert "Process (1.0 s)" not in content


def test_worst_case_native_tree_and_bytes_fit_reserved_budget():
    d = data(routes=["路" * 160] * 12, last_error_type="Exception", usage_partial=True, compression_observed=True)
    d["requested_model"], d["response_model"] = "A" * 160, "B" * 160
    d["reference"]["steps"] = [step("command", "error", "<" * 400, error="失" * 1000) for _ in range(150)]
    d["reference"]["history"] = {
        "status": "ok",
        "timezone": "Asia/Shanghai",
        "show_models": True,
        "models": [{"subscription": "订" * 160, "model": "模" * 160, "tokens": 50, "partial": True} for _ in range(3)],
    }
    card = build_streaming_card_v2(footer_data=d, show_streaming_element=False)
    check = inspect_card(card)
    assert check.safe and check.elements <= REFERENCE_ELEMENT_RESERVE, check
    final = build_complete_card(
        segments=segments(), all_tool_steps=d["reference"]["steps"], footer_data=d, footer_mode="enhanced"
    )
    final_check = inspect_card(final)
    assert final_check.safe and final_check.elements <= REFERENCE_ELEMENT_RESERVE + 5, final_check


def test_reference_native_ids_and_nested_expansion_updates_are_valid_and_immutable():
    d = data()
    original = deepcopy(d)
    elements = build_runtime_footer(d)
    from hermes_lark_streaming.cardkit.reference import build_reference_prefix

    elements = [*build_reference_prefix(d), *elements]
    before = deepcopy(elements)
    actions = runtime_actions(elements, reference=True)

    def walk(v):
        if isinstance(v, dict):
            yield v
            for child in v.values():
                yield from walk(child)
        elif isinstance(v, list):
            for child in v:
                yield from walk(child)

    for node in walk(elements):
        if "element_id" in node:
            assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,19}", node["element_id"])
    assert all("expanded" not in node for node in walk(actions))
    assert d == original and elements == before


def test_config_opt_in_and_resources_are_independent_and_timezone_is_validated(tmp_path):
    cfg = Config(tmp_path)
    cfg._raw = {
        "streaming": {
            "layout": "reference",
            "resources": {"enabled": True},
            "footer": {"mode": "enhanced", "history": {"timezone": "BAD"}},
        }
    }
    assert cfg.card_layout == "reference" and cfg.reference_resources_enabled
    assert cfg.reference_history_timezone == "UTC" and cfg.footer_element_reserve == REFERENCE_ELEMENT_RESERVE
    cfg._raw["streaming"]["footer"]["mode"] = "classic"
    assert cfg.card_layout == "classic" and cfg.footer_element_reserve == 2


def test_gpu_missing_fields_do_not_become_zero():
    assert parse_gpu("[N/A], 66, 1024, 24576")["gpu_used_gib"] == 1
    assert "gpu_percent" not in parse_gpu("[N/A], 66, 1024, 24576")
    assert parse_gpu("nan, -1, inf, 24576") == {"gpu_total_gib": 24}


def test_proc_memory_uses_available_not_free(tmp_path):
    (tmp_path / "meminfo").write_text("MemTotal: 1048576 kB\nMemAvailable: 786432 kB\nMemFree: 1 kB\n")
    (tmp_path / "stat").write_text("cpu 10 0 20 70 0 0 0 0\n")
    values, cpu = read_proc(tmp_path)
    assert values["ram_used_gib"] == 0.25 and cpu == (100, 70)


def test_read_only_history_summary_respects_timezone_partial_usage_and_scope(tmp_path):
    path = tmp_path / "history.sqlite3"
    assert read_summary(path, "UTC")["status"] == "no_history" and not path.exists()
    ledger = UsageLedger(path)
    for rid, when, usage, scope in (
        ("prev", "2026-09-30T15:00:00+00:00", {"prompt_tokens": 10, "output_tokens": 1}, "main"),
        ("today", "2026-09-30T17:00:00+00:00", {"prompt_tokens": 20, "output_tokens": 2}, "main"),
        ("missing", "2026-09-30T18:00:00+00:00", {}, "main"),
        ("aux", "2026-09-30T19:00:00+00:00", {"prompt_tokens": 999, "output_tokens": 9}, "auxiliary"),
    ):
        ledger.record(
            "post_auxiliary_call" if scope == "auxiliary" else "post_api_request",
            {
                "api_request_id": rid,
                "started_at": datetime.fromisoformat(when).timestamp(),
                "session_id": "s",
                "turn_id": "t",
                "provider": "fixture",
                "model": "model",
                "usage": usage,
            },
        )
    now = datetime.fromisoformat("2026-10-01T01:00:00+00:00").timestamp()
    before = path.stat().st_mtime_ns
    report = read_summary(path, "Asia/Shanghai", now=now)
    assert report["today"]["tokens"] == 22 and report["month"]["partial"]
    assert report["total"]["tokens"] == 33 and report["total"]["requests"] == 3
    assert report["since"] == "2026-09-30" and report["models"][0]["tokens"] == 33
    assert path.stat().st_mtime_ns == before


@pytest.mark.asyncio
async def test_sampler_coalesces_and_cpu_delta_never_busy_spins():
    sampler = HostSampler()
    with (
        patch("hermes_lark_streaming.footer.host.read_proc", return_value=({"ram_used_gib": 1}, (100, 70))),
        patch("hermes_lark_streaming.footer.host.shutil.which", return_value=None),
    ):
        sampler.request()
        first = sampler._task
        sampler.request()
        assert sampler._task is first
        await sampler.finish()
    assert "cpu_percent" not in sampler.snapshot()
    sampler._at = float("-inf")
    with (
        patch("hermes_lark_streaming.footer.host.read_proc", return_value=({}, (200, 140))),
        patch("hermes_lark_streaming.footer.host.shutil.which", return_value=None),
    ):
        await sampler.finish()
    assert sampler.snapshot()["cpu_percent"] == 30


@pytest.mark.asyncio
async def test_history_background_failure_is_unknown_not_inference_failure(tmp_path):
    reader = HistorySummary(tmp_path / "missing.sqlite3", "invalid")
    await reader.finish()
    assert reader.snapshot()["status"] == "unavailable"


@pytest.mark.asyncio
async def test_history_timeout_is_caught_and_force_refresh_reads_new_terminal_usage(tmp_path):
    reader = HistorySummary(tmp_path / "ledger.sqlite3", "UTC")
    with patch(
        "hermes_lark_streaming.footer.history_summary.asyncio.to_thread", new=AsyncMock(side_effect=TimeoutError)
    ):
        await reader.finish()
    assert reader.snapshot()["status"] == "unavailable" and reader._task is None
    ledger = UsageLedger(tmp_path / "ledger.sqlite3")
    ledger.record(
        "post_api_request",
        {
            "api_request_id": "one",
            "started_at": 1_700_000_000,
            "session_id": "s",
            "turn_id": "t",
            "provider": "fixture",
            "model": "model",
            "usage": {"prompt_tokens": 10, "output_tokens": 2},
        },
    )
    await reader.finish()
    assert reader.snapshot()["total"]["tokens"] == 12
    ledger.record(
        "post_api_request",
        {
            "api_request_id": "two",
            "started_at": 1_700_000_002,
            "session_id": "s",
            "turn_id": "t",
            "provider": "fixture",
            "model": "model",
            "usage": {"prompt_tokens": 20, "output_tokens": 3},
        },
    )
    await reader.finish()
    assert reader.snapshot()["total"]["tokens"] == 35


@pytest.mark.asyncio
async def test_reference_tool_counts_keep_prior_failures_distinct_from_final_status(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"layout": "reference", "footer": {"mode": "enhanced"}}}
    session = CardSession("fixture", "fixture-chat", asyncio.get_running_loop())
    session.state = SessionState.COMPLETED
    session.tool_calls_prior, session.tools_done_prior, session.tools_failed_prior = 3, 3, 1
    session.tool_use.record_start("command", "verify")
    snap = ctrl._reference_snapshot(session, {})
    assert snap["reference"]["failed_total"] == 1 and snap["reference"]["succeeded_total"] == 2
    panel = build_reference_footer(snap)[0]
    content = json.dumps(panel, ensure_ascii=False)
    assert "回答已完成" in content and "本轮失败" not in content and "1 失败" in content


@pytest.mark.asyncio
async def test_reference_controller_aggregates_tools_through_existing_writer_only(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"enabled": True, "layout": "reference", "footer": {"mode": "enhanced"}}}
    ctrl._initialized = True
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("fixture", "fixture-chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id="card", card_msg_id="message")
    session.flush.set_card_message_ready(True)
    session.tool_use.record_start("command", "check env")
    session.segment_state.on_tool_event(1)
    session.segment_state.on_answer_delta("body preserved")
    await ctrl._do_flush(session)
    batches = ctrl._client.cardkit_batch_update.await_args_list
    assert batches
    actions = [a for call in batches for a in call.args[1]]
    assert any(a["params"].get("element_id") == "reference_tools" for a in actions)
    assert not any(
        a["action"] == "add_elements" and any(e["tag"] == "collapsible_panel" for e in a["params"]["elements"])
        for a in actions
    )
    assert session.segment_state.segments[0].created
    assert ctrl._client.cardkit_stream_element.await_count == 1
    assert session.sequence == 1 + len(batches) + 1


@pytest.mark.asyncio
async def test_footer_off_still_updates_reference_tools_without_missing_footer_action(tmp_path):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"layout": "reference", "footer": {"mode": "enhanced", "enabled": False}}}
    session = CardSession("x", "y", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    snap = ctrl._runtime_snapshot(session)
    assert snap["reference"]["footer_enabled"] is False and ctrl._runtime_enabled()
    actions = runtime_actions(build_runtime_footer(snap), reference=True)
    assert all(a["params"]["element_id"] != "footer_details" for a in actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [SessionState.ABORTED, SessionState.FAILED])
async def test_reference_terminal_controller_closes_card_with_honest_unfinished_tools(tmp_path, terminal):
    ctrl = StreamCardController(tmp_path)
    ctrl._cfg._raw = {"streaming": {"layout": "reference", "footer": {"mode": "enhanced"}}}
    ctrl._initialized = True
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("terminal-fixture", "fixture-chat", asyncio.get_running_loop())
    session.set_card(card_id="card", card_msg_id="message")
    session.state = terminal
    session.tool_use.record_start("command", "fixture-only")
    assert await ctrl._do_complete_card_inner(session)
    ctrl._client.cardkit_close_streaming.assert_awaited_once()
    ctrl._client.cardkit_update.assert_awaited_once()
    card = ctrl._client.cardkit_update.await_args.args[1]
    text = json.dumps(card, ensure_ascii=False)
    assert "结果未确认" in text and "运行中" not in text and "Done." not in text
    assert session.tool_use.build_display_steps()[0]["status"] == "running"


def test_terminal_unconfirmed_tools_remain_within_reference_reserve():
    d = data(routes=["路" * 160] * 12, last_error_type="Exception", usage_partial=True, compression_observed=True)
    d["requested_model"], d["response_model"] = "A" * 160, "B" * 160
    d["reference"]["steps"] = [step("command", "running", "<" * 400) for _ in range(128)]
    d["reference"]["steps"][0] = step("command", "error", "<" * 400, error="失" * 1000)
    d["reference"]["history"] = {
        "status": "ok", "show_models": True,
        "models": [{"subscription": "订" * 160, "model": "模" * 160, "tokens": 50} for _ in range(3)],
    }
    card = build_complete_card(segments=[], all_tool_steps=d["reference"]["steps"], footer_data=d,
                               footer_mode="enhanced", is_aborted=True)
    check = inspect_card(card)
    assert check.safe and check.elements <= REFERENCE_ELEMENT_RESERVE + 5, check


PROVIDERS = json.loads((Path(__file__).parents[1] / "docs/data/providers-20261003.json").read_text())["providers"]


@pytest.mark.parametrize("percent, expected", [(10.655, "10.7%"), (0, "0%"), (None, "—")])
def test_reference_resources_round_percent_without_inventing_unknown(percent, expected):
    snap = data()
    snap["reference"]["host"]["cpu_percent"] = percent
    content = json.dumps(build_resources(snap["reference"]["host"]), ensure_ascii=False)
    assert expected in content
    assert "10.655" not in content


@pytest.mark.parametrize("provider", [p["id"] for p in PROVIDERS])
def test_all_catalog_ids_use_same_native_reference_path(provider):
    d = deepcopy(data())
    d["provider"] = provider
    panel = build_reference_footer(d)[0]
    assert inspect_card({"schema": "2.0", "body": {"elements": [panel]}}).safe
    display = {"opencode-go": "OpenCode Go", "siliconflow": "SiliconFlow"}.get(provider, provider)
    assert display.replace("_", r"\_") in json.dumps(panel)
