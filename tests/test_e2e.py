"""Explicit CardKit smoke modes must not surprise a user's chat."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_lark_streaming import e2e
from hermes_lark_streaming.feishu import FeishuAPIError


def _entity_client(*, initial_code: int = 300313, retry_code: int = 0) -> SimpleNamespace:
    stream = AsyncMock(side_effect=[
        FeishuAPIError("missing", initial_code),
        FeishuAPIError("retry", retry_code) if retry_code else None,
    ])
    return SimpleNamespace(
        cardkit_create=AsyncMock(return_value="card_entity"),
        cardkit_stream_element=stream,
        cardkit_batch_update=AsyncMock(),
        cardkit_close_streaming=AsyncMock(),
        send_card_to_chat=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_entity_probe_reuses_stream_operation_without_sending_message(monkeypatch) -> None:
    client = _entity_client()
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_entity_probe()

    assert report == {
        "ok": True,
        "mode": "entity-probe",
        "attached_to_chat": False,
        "initial_error_code": 300313,
        "same_uuid_after_add": "accepted",
        "entity_closed": True,
    }
    assert client.cardkit_stream_element.await_count == 2
    assert client.cardkit_stream_element.await_args_list[0] == client.cardkit_stream_element.await_args_list[1]
    assert client.cardkit_batch_update.await_args.kwargs["sequence"] == 2
    assert client.cardkit_close_streaming.await_args.kwargs["sequence"] == 6
    client.send_card_to_chat.assert_not_called()


@pytest.mark.asyncio
async def test_entity_probe_reports_rejected_retry_and_still_closes(monkeypatch) -> None:
    client = _entity_client(retry_code=300313)
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_entity_probe()

    assert report["ok"] is False
    assert report["retry_error_code"] == 300313
    assert report["entity_closed"] is True
    client.cardkit_close_streaming.assert_awaited_once()
    client.send_card_to_chat.assert_not_called()


@pytest.mark.asyncio
async def test_entity_probe_stops_after_unexpected_first_response(monkeypatch) -> None:
    client = _entity_client(initial_code=300317)
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_entity_probe()

    assert report["ok"] is False
    assert report["initial_error_code"] == 300317
    assert report["entity_closed"] is True
    client.cardkit_batch_update.assert_not_awaited()
    client.cardkit_close_streaming.assert_awaited_once()


@pytest.mark.asyncio
async def test_entity_probe_reports_failed_add_and_still_closes(monkeypatch) -> None:
    client = _entity_client()
    client.cardkit_batch_update.side_effect = FeishuAPIError("add failed", 300315)
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_entity_probe()

    assert report["ok"] is False
    assert report["add_error_code"] == 300315
    assert report["entity_closed"] is True
    assert client.cardkit_stream_element.await_count == 1


@pytest.mark.asyncio
async def test_entity_probe_close_failure_makes_report_unsuccessful(monkeypatch) -> None:
    client = _entity_client()
    client.cardkit_close_streaming.side_effect = FeishuAPIError("close failed", 300317)
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_entity_probe()

    assert report["ok"] is False
    assert report["same_uuid_after_add"] == "accepted"
    assert report["entity_closed"] is False
    assert report["close_error_code"] == 300317


def test_entity_probe_requires_explicit_execute_and_never_requires_chat(monkeypatch, capsys) -> None:
    probe = AsyncMock(return_value={"ok": True, "mode": "entity-probe", "entity_closed": True})
    monkeypatch.setattr(e2e, "live_entity_probe", probe)

    assert e2e.run(execute=False, entity_only=True) == 0
    probe.assert_not_awaited()
    capsys.readouterr()

    assert e2e.run(execute=True, entity_only=True) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "entity-probe"
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_closed_stream_probe_updates_unattached_entity_after_300309(monkeypatch) -> None:
    client = SimpleNamespace(
        cardkit_create=AsyncMock(return_value="card_closed"),
        cardkit_stream_element=AsyncMock(side_effect=[
            None, FeishuAPIError("streaming closed", 300309),
        ]),
        cardkit_close_streaming=AsyncMock(),
        cardkit_update=AsyncMock(),
        send_card_to_chat=AsyncMock(),
    )
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_closed_stream_probe()

    assert report == {
        "ok": True,
        "mode": "closed-stream-probe",
        "attached_to_chat": False,
        "entity_closed": True,
        "post_close_stream_error_code": 300309,
        "final_update_accepted": True,
    }
    assert [call.kwargs["sequence"] for call in client.cardkit_stream_element.await_args_list] == [2, 4]
    assert client.cardkit_close_streaming.await_args.kwargs["sequence"] == 3
    assert client.cardkit_update.await_args.kwargs["sequence"] == 4
    client.send_card_to_chat.assert_not_called()


@pytest.mark.asyncio
async def test_closed_stream_probe_reports_unexpected_stream_code(monkeypatch) -> None:
    client = SimpleNamespace(
        cardkit_create=AsyncMock(return_value="card_closed"),
        cardkit_stream_element=AsyncMock(side_effect=[
            None, FeishuAPIError("sequence conflict", 300317),
        ]),
        cardkit_close_streaming=AsyncMock(),
        cardkit_update=AsyncMock(),
        send_card_to_chat=AsyncMock(),
    )
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_closed_stream_probe()

    assert report["ok"] is False
    assert report["post_close_stream_error_code"] == 300317
    assert report["final_update_accepted"] is True
    client.send_card_to_chat.assert_not_called()


@pytest.mark.asyncio
async def test_closed_stream_probe_accepts_unattached_entity_stream_after_close(monkeypatch) -> None:
    client = SimpleNamespace(
        cardkit_create=AsyncMock(return_value="card_closed"),
        cardkit_stream_element=AsyncMock(),
        cardkit_close_streaming=AsyncMock(),
        cardkit_update=AsyncMock(),
        send_card_to_chat=AsyncMock(),
    )
    monkeypatch.setattr(e2e, "_configured_client", lambda: client)

    report = await e2e.live_closed_stream_probe()

    assert report["ok"] is True
    assert report["post_close_stream_error_code"] == 0
    assert report["final_update_accepted"] is True
    assert client.cardkit_update.await_args.kwargs["sequence"] == 5
    client.send_card_to_chat.assert_not_called()


def test_closed_stream_probe_requires_explicit_execute(monkeypatch, capsys) -> None:
    probe = AsyncMock(return_value={
        "ok": True, "mode": "closed-stream-probe", "entity_closed": True,
    })
    monkeypatch.setattr(e2e, "live_closed_stream_probe", probe)

    assert e2e.run(execute=False, closed_stream_probe=True) == 0
    probe.assert_not_awaited()
    capsys.readouterr()

    assert e2e.run(execute=True, closed_stream_probe=True) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "closed-stream-probe"
    probe.assert_awaited_once()
