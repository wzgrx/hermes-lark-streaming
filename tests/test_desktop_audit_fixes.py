"""Real config files and stateful CardKit fixtures for the desktop V1 audit."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from hermes_lark_streaming.cardkit.builder import build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import build_reference_footer, build_resources
from hermes_lark_streaming.config import Config
from hermes_lark_streaming.controller import StreamCardController
from hermes_lark_streaming.feishu import FeishuAPIError, FeishuClient
from hermes_lark_streaming.footer.runtime import reconcile_runtime_panels
from hermes_lark_streaming.footer.state import TurnFooter
from hermes_lark_streaming.streaming.session import CardSession, SessionState


def config(show=True):
    return {
        "streaming": {"enabled": True, "layout": "reference", "resources": {"enabled": False},
                      "footer": {"mode": "enhanced", "history": {"timezone": "UTC"}}},
        "display": {"show_tool_use": show},
    }


@pytest.fixture
def live_config(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_lark_streaming.config._CONFIG_RELOAD_TTL_S", 0)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config()))
    return path, Config(tmp_path)


@pytest.mark.parametrize("broken", [
    "token: secret-fixture\nstreaming: [", "- wrong-root", "display: []", "streaming: false", None,
])
def test_invalid_or_temporarily_missing_config_keeps_last_good_and_recovers(live_config, caplog, broken):
    path, cfg = live_config
    assert cfg.show_tool_use and cfg.reference_history_timezone == "UTC"
    if broken is None:
        path.unlink()
    else:
        path.write_text(broken)
    for _ in range(3):
        assert cfg.show_tool_use and cfg.reference_history_timezone == "UTC"
    assert caplog.text.count("retaining last valid settings") == 1
    assert "secret-fixture" not in caplog.text
    new = config(False)
    new["streaming"]["footer"]["history"]["timezone"] = "Asia/Shanghai"
    path.write_text(yaml.safe_dump(new))
    assert not cfg.show_tool_use and cfg.reference_history_timezone == "Asia/Shanghai"


def test_display_and_history_live_but_structural_layout_pinned(live_config):
    path, cfg = live_config
    assert cfg.card_layout == "reference"
    new = config(False)
    new["streaming"]["layout"] = "classic"
    new["streaming"]["footer"]["history"]["timezone"] = "Asia/Shanghai"
    path.write_text(yaml.safe_dump(new))
    assert cfg.card_layout == "reference"
    assert not cfg.show_tool_use and cfg.reference_history_timezone == "Asia/Shanghai"
    assert Config(path.parent).card_layout == "classic"


def test_malformed_initial_config_uses_defaults_until_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_lark_streaming.config._CONFIG_RELOAD_TTL_S", 0)
    path = tmp_path / "config.yaml"
    path.write_text("- not-a-mapping")
    cfg = Config(tmp_path)
    assert cfg.card_layout == "classic" and cfg.reference_history_timezone == "UTC"
    path.write_text(yaml.safe_dump(config(False)))
    assert not cfg.show_tool_use  # live settings recover without a new Config
    assert cfg.card_layout == "classic"  # structural settings still need restart


def panel(name):
    return {"tag": "collapsible_panel", "element_id": name, "expanded": False,
            "header": {"title": {"tag": "plain_text", "content": name}}, "elements": []}


class CardFixture:
    """Strict target/order checks, not a substitute for server/client acceptance."""

    def __init__(self, elements):
        self.elements = deepcopy(elements)
        self.batches = []

    async def batch(self, card_id, actions, **kwargs):
        staged = deepcopy(self.elements)
        for action in actions:
            params = action["params"]
            ids = [el.get("element_id") for el in staged]
            if action["action"] == "delete_elements":
                assert set(params["element_ids"]) <= set(ids)
                staged = [el for el in staged if el.get("element_id") not in params["element_ids"]]
            elif action["action"] == "add_elements":
                assert params["target_element_id"] in ids
                assert not set(el["element_id"] for el in params["elements"]) & set(ids)
                pos = ids.index(params["target_element_id"]) + (params["type"] == "insert_after")
                staged[pos:pos] = deepcopy(params["elements"])
            else:
                assert action["action"] == "partial_update_element"
                assert params["element_id"] in ids
                staged[ids.index(params["element_id"])].update(deepcopy(params["partial_element"]))
        self.elements = staged
        self.batches.append((deepcopy(actions), kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [set(), {"reference_tools"}, {"reference_resources"}, {"footer_details"}])
async def test_reconcile_adds_before_answer_and_retains_expanded(existing):
    ordered = ["reference_tools", "reference_resources", "answer_0", "loading_icon", "footer_details"]
    fixture = CardFixture([{**panel(name), "expanded": True} for name in ordered
                           if name in existing or name in {"answer_0", "loading_icon"}])
    desired = {"reference_tools", "reference_resources", "footer_details"}
    elements = [panel(name) for name in ordered if name in desired]
    actions, accepted = reconcile_runtime_panels(elements, existing, prefix_anchor="answer_0")
    assert existing <= desired  # caller-owned input remains intact
    await fixture.batch("card", actions)
    assert accepted == desired
    assert [el["element_id"] for el in fixture.elements] == ordered
    assert all(el["expanded"] for el in fixture.elements if el["element_id"] in existing)


@pytest.mark.asyncio
async def test_live_tools_toggle_adds_and_deletes_without_reseed_or_body_stall(live_config):
    path, _ = live_config
    path.write_text(yaml.safe_dump(config(False)))
    ctrl = StreamCardController(path.parent)
    ctrl._initialized = True
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("fixture", "chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id="card", card_msg_id="message")
    session.flush.set_card_message_ready(True)
    card = build_streaming_card_v2(footer_data=ctrl._runtime_snapshot(session))
    ctrl._remember_runtime_panels(session, card, "card")
    fixture = CardFixture(card["body"]["elements"])
    ctrl._client.cardkit_batch_update.side_effect = fixture.batch
    session.segment_state.on_answer_delta("before toggle")
    await ctrl._do_flush(session)
    segment = session.segment_state.segments[0]
    assert ctrl._client.cardkit_stream_element.await_count == 1
    path.write_text(yaml.safe_dump(config(True)))
    session.segment_state.on_answer_delta(" after toggle")
    await ctrl._do_flush(session)
    ids = [el.get("element_id") for el in fixture.elements]
    assert ids.index("reference_tools") < ids.index(segment.el_id) < ids.index("footer_details")
    assert ctrl._client.cardkit_stream_element.await_count == 2
    path.write_text(yaml.safe_dump(config(False)))
    await ctrl._do_flush(session)
    assert "reference_tools" not in session.runtime_panel_ids
    assert "reference_tools" not in [el.get("element_id") for el in fixture.elements]
    ctrl._client.cardkit_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_toggle_commits_neither_sequence_nor_membership(live_config):
    path, _ = live_config
    ctrl = StreamCardController(path.parent)
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("fixture", "chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id="card", card_msg_id="message")
    session.runtime_panel_ids = {"footer_details"}  # tools were hidden on the accepted card
    before = session.sequence
    ctrl._client.cardkit_batch_update.side_effect = FeishuAPIError("limited", code=99991400)
    assert not await ctrl._flush_runtime_footer(session)
    assert session.sequence == before and session.runtime_panel_ids == {"footer_details"}
    ctrl._client.cardkit_batch_update.side_effect = None
    session.runtime_retry_after = 0
    assert await ctrl._flush_runtime_footer(session)
    assert session.sequence == before + 1 and session.runtime_panel_ids == {"footer_details", "reference_tools"}


@pytest.mark.asyncio
async def test_toggle_during_await_commits_sent_snapshot_then_reconciles_again(live_config):
    path, _ = live_config
    ctrl = StreamCardController(path.parent)
    ctrl._client = AsyncMock(spec=FeishuClient)
    session = CardSession("fixture", "chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id="card", card_msg_id="message")
    session.runtime_panel_ids = {"footer_details"}
    fixture = CardFixture([panel("loading_icon"), panel("footer_details")])

    async def apply_then_toggle(*args, **kwargs):
        await fixture.batch(*args, **kwargs)
        path.write_text(yaml.safe_dump(config(False)))

    ctrl._client.cardkit_batch_update.side_effect = apply_then_toggle
    assert await ctrl._flush_runtime_footer(session)
    assert "reference_tools" in session.runtime_panel_ids  # what actually reached the server
    ctrl._client.cardkit_batch_update.side_effect = fixture.batch
    assert await ctrl._flush_runtime_footer(session)
    assert "reference_tools" not in session.runtime_panel_ids
    assert len(fixture.batches) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["reference_tools", "reference_resources"])
async def test_missing_prefix_reseeds_once_and_marks_body_for_replay(live_config, missing):
    path, _ = live_config
    settings = config()
    settings["streaming"]["resources"]["enabled"] = True
    path.write_text(yaml.safe_dump(settings))
    ctrl = StreamCardController(path.parent)
    ctrl._client = AsyncMock(spec=FeishuClient)
    # Isolate host sampling as well as network traffic.
    ctrl._reference_host = AsyncMock()
    ctrl._reference_host.request = lambda: None
    ctrl._reference_host.snapshot = lambda: {}
    session = CardSession("fixture", "chat", asyncio.get_running_loop())
    session.state = SessionState.STREAMING
    session.set_card(card_id="card", card_msg_id="message")
    session.segment_state.on_answer_delta("retained answer")
    segment = session.segment_state.segments[0]
    segment.created, segment.dirty = True, False
    ctrl._client.cardkit_batch_update.side_effect = FeishuAPIError("missing fixture element", code=300313)
    with patch("hermes_lark_streaming.streaming.controller.extract_missing_element_id", return_value=missing):
        assert not await ctrl._flush_runtime_footer(session)
        assert not segment.created and segment.dirty
        assert session.anchor_recovery_attempts == 1 and missing in session.runtime_panel_ids
        session.runtime_retry_after = 0
        assert not await ctrl._flush_runtime_footer(session)
    ctrl._client.cardkit_update.assert_awaited_once()
    assert session.state == SessionState.FAILED  # bounded; never an infinite metadata retry


@pytest.mark.asyncio
async def test_card_handoff_keeps_only_membership_of_accepted_new_card():
    session = CardSession("fixture", "chat", asyncio.get_running_loop())
    session.set_card(card_id="old", card_msg_id="message")
    StreamCardController._remember_runtime_panels(session, {"body": {"elements": [panel("reference_tools")]}}, "new")
    session.set_card(card_id="new", card_msg_id="new-message")
    assert session.runtime_panel_ids == {"reference_tools"}
    session.set_card(card_id="third", card_msg_id="third-message")
    assert session.runtime_panel_ids is None


@pytest.mark.parametrize("delay", [0, 2])
def test_first_response_after_failed_attempt_includes_retry_wait(delay):
    state = TurnFooter()
    start = state.created_at + 1
    event = dict(platform="feishu", session_id="s", turn_id="t", api_request_id="r", started_at=start)
    state.observe("pre_api_request", event)
    state.observe("api_request_error", {**event, "error_type": "RateLimitError"})
    retry = {**event, "started_at": start + 5}
    state.observe("pre_api_request", retry)
    state.observe("post_api_request", {**retry, "first_chunk_at": start + 5 + delay})
    assert state.snapshot()["first_response"] == 5 + delay
    assert state.snapshot()["first_response_attempt"] == delay


@pytest.mark.parametrize("delay", [0, 2])
def test_single_attempt_first_response_is_unchanged(delay):
    state = TurnFooter()
    start = state.created_at + 1
    event = dict(platform="feishu", session_id="s", turn_id="t", api_request_id="r", started_at=start)
    state.observe("pre_api_request", event)
    state.observe("post_api_request", {**event, "first_chunk_at": start + delay})
    assert state.snapshot()["first_response"] == state.snapshot()["first_response_attempt"] == delay


def test_first_response_uses_earliest_observed_chunk_not_callback_order():
    state = TurnFooter()
    start = state.created_at + 1
    a = dict(platform="feishu", session_id="s", turn_id="t", api_request_id="a", started_at=start)
    b = {**a, "api_request_id": "b", "started_at": start + 1}
    state.observe("pre_api_request", b)
    state.observe("pre_api_request", a)
    state.observe("post_api_request", {**a, "first_chunk_at": start + 5})
    state.observe("post_api_request", {**b, "first_chunk_at": start + 3})
    assert state.snapshot()["first_response"] == 3
    assert state.snapshot()["first_response_attempt"] == 2


@pytest.mark.parametrize("status, text", [("pending", "正在读取本机历史"), ("unavailable", "历史读取失败或超时"),
                                         ("no_history", "尚无已记录的主请求")])
def test_history_statuses_are_distinct(status, text):
    rendered = json.dumps(build_reference_footer({"reference": {"history": {"status": status}}}), ensure_ascii=False)
    assert text in rendered
    assert "今日 —" not in rendered and "自 未记录 起" not in rendered


@pytest.mark.parametrize("scope, label", [("WSL", "WSL 内存使用"), ("Linux", "主机内存使用")])
def test_resource_units_and_scope_are_honest(scope, label):
    rendered = json.dumps(build_resources({"scope": scope, "ram_used_gib": 1, "ram_total_gib": 16}), ensure_ascii=False)
    assert label in rendered and "GiB" in rendered
