"""Opt-in real-chat probes must close entities and keep failure reports safe."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_lark_streaming import e2e
from hermes_lark_streaming.feishu import FeishuAPIError


def client_fixture():
    return SimpleNamespace(
        cardkit_create=AsyncMock(return_value="card_test_entity"),
        send_card_to_chat=AsyncMock(return_value="message_test_reply"),
        cardkit_stream_element=AsyncMock(),
        cardkit_close_streaming=AsyncMock(),
        cardkit_update=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,phase", [
    ("cardkit_create", "create"),
    ("send_card_to_chat", "attach"),
    ("cardkit_stream_element", "stream"),
    ("cardkit_update", "final_update"),
])
async def test_failed_live_probe_closes_owned_entity_without_resending(monkeypatch, operation, phase):
    client=client_fixture()
    getattr(client,operation).side_effect=FeishuAPIError("SYNTHETIC_SECRET do not print",300317)
    monkeypatch.setattr(e2e,"_configured_client",lambda:client)
    report=await e2e.live_run("explicit-test-chat")
    assert report["ok"] is False and report["failed_phase"]==phase
    assert report["error_code"]==300317
    assert "SYNTHETIC_SECRET" not in json.dumps(report)
    assert client.send_card_to_chat.await_count<=1
    if operation=="cardkit_create":
        client.cardkit_close_streaming.assert_not_awaited()
        assert report["entity_created"] is False
    else:
        client.cardkit_close_streaming.assert_awaited_once()
        assert report["entity_closed"] is True


@pytest.mark.asyncio
async def test_ambiguous_send_stays_unknown_and_cleanup_preserves_primary_error(monkeypatch):
    client=client_fixture()
    client.send_card_to_chat.side_effect=TimeoutError("SYNTHETIC_SECRET")
    client.cardkit_close_streaming.side_effect=FeishuAPIError("cleanup secret",300317)
    monkeypatch.setattr(e2e,"_configured_client",lambda:client)
    report=await e2e.live_run("explicit-test-chat")
    assert report["ok"] is False and report["failed_phase"]=="attach"
    assert report["error_type"]=="TimeoutError"
    assert report["attachment_status"]=="unknown"
    assert report["entity_closed"] is False and report["cleanup_error_code"]==300317
    assert "secret" not in json.dumps(report).lower()
    client.send_card_to_chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_cleanup_uses_higher_sequence_after_uncertain_close(monkeypatch):
    client=client_fixture()
    client.cardkit_close_streaming.side_effect=[TimeoutError("late close response"),None]
    monkeypatch.setattr(e2e,"_configured_client",lambda:client)
    report=await e2e.live_run("explicit-test-chat")
    assert report["ok"] is False and report["failed_phase"]=="close"
    assert report["entity_closed"] is True
    assert [x.kwargs["sequence"] for x in client.cardkit_close_streaming.await_args_list]==[3,4]
    client.cardkit_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_stays_explicitly_api_only(monkeypatch):
    client=client_fixture()
    monkeypatch.setattr(e2e,"_configured_client",lambda:client)
    report=await e2e.live_run("explicit-test-chat")
    assert report["ok"] is True and report["entity_closed"] is True
    assert report["attachment_status"]=="confirmed"
    assert report["gateway_turn_verified"] is False and report["client_visual_verified"] is False
    assert client.cardkit_update.await_args.kwargs["sequence"]==4
    client.cardkit_close_streaming.assert_awaited_once()


def test_live_cli_returns_failure_exit_and_structured_report(monkeypatch,capsys):
    probe=AsyncMock(return_value={"ok":False,"mode":"live","error_type":"TimeoutError"})
    monkeypatch.setattr(e2e,"live_run",probe)
    assert e2e.run(execute=True,chat_id="explicit-test-chat")==1
    assert json.loads(capsys.readouterr().out)["error_type"]=="TimeoutError"


@pytest.mark.asyncio
async def test_config_failure_is_safe_without_creating_an_entity(monkeypatch):
    def broken_config():
        raise ValueError("SYNTHETIC_SECRET config detail")
    monkeypatch.setattr(e2e,"_configured_client",broken_config)
    report=await e2e.live_run("explicit-test-chat")
    assert report["ok"] is False and report["failed_phase"]=="configure"
    assert report["error_type"]=="ValueError" and report["entity_created"] is False
    assert report["attachment_status"]=="not_attempted"
    assert "SYNTHETIC_SECRET" not in json.dumps(report)


@pytest.mark.asyncio
async def test_cancellation_propagates_after_entity_cleanup(monkeypatch):
    import asyncio
    client=client_fixture()
    client.cardkit_stream_element.side_effect=asyncio.CancelledError()
    monkeypatch.setattr(e2e,"_configured_client",lambda:client)
    with pytest.raises(asyncio.CancelledError):
        await e2e.live_run("explicit-test-chat")
    client.cardkit_close_streaming.assert_awaited_once()
    assert client.cardkit_close_streaming.await_args.kwargs["sequence"]==3
    client.send_card_to_chat.assert_awaited_once()
