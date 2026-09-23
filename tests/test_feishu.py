"""Feishu client transient-error behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.feishu import FeishuAPIError, FeishuClient


class _Resp:
    def __init__(self, *, ok: bool, code: int = 0, msg: str = "", data: object | None = None) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self._ok


def _client_with(**methods: AsyncMock) -> FeishuClient:
    client = FeishuClient.__new__(FeishuClient)
    client._client = SimpleNamespace(  # type: ignore[attr-defined]
        cardkit=SimpleNamespace(
            v1=SimpleNamespace(
                card=SimpleNamespace(
                    acreate=methods.get("card_create", AsyncMock()),
                    aupdate=methods.get("card_update", AsyncMock()),
                    abatch_update=methods.get("batch_update", AsyncMock()),
                    asettings=methods.get("settings", AsyncMock()),
                ),
                card_element=SimpleNamespace(content=methods.get("card_element_content", AsyncMock())),
            ),
        ),
        im=SimpleNamespace(
            v1=SimpleNamespace(
                message=SimpleNamespace(
                    acreate=methods.get("create_message", AsyncMock()),
                    areply=methods.get("reply", AsyncMock()),
                ),
            ),
        ),
    )
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (2200, "Gateway timeout. Please try again later."),
        (300000, "Server Internal Error"),
    ],
    ids=["gateway-timeout", "server-internal-error"],
)
async def test_cardkit_create_retries_transient_errors_once(code: int, message: str) -> None:
    create = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=code, msg=message),
            _Resp(ok=True, data=SimpleNamespace(card_id="card-ok")),
        ]
    )
    client = _client_with(card_create=create)

    assert await client.cardkit_create({"schema": "2.0"}) == "card-ok"
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_cardkit_batch_update_retries_internal_error() -> None:
    batch_update = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=1663, msg="internal error"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(batch_update=batch_update)

    await client.cardkit_batch_update("card", [{"action": "add"}], sequence=7)

    assert batch_update.await_count == 2
    first_request = batch_update.await_args_list[0].args[0]
    second_request = batch_update.await_args_list[1].args[0]
    assert second_request.card_id == first_request.card_id
    assert second_request.request_body.sequence == first_request.request_body.sequence
    assert first_request.request_body.uuid
    assert second_request.request_body.uuid == first_request.request_body.uuid


@pytest.mark.asyncio
async def test_cardkit_batch_update_uuid_is_stable_for_same_batch_and_changes_for_repair() -> None:
    batch_update = AsyncMock(return_value=_Resp(ok=True))
    client = _client_with(batch_update=batch_update)
    add = {"action": "add_elements", "params": {"type": "insert_after", "element_id": "tools_7"}}
    reordered_add = {"params": {"element_id": "tools_7", "type": "insert_after"}, "action": "add_elements"}
    repaired_add = {"action": "add_elements", "params": {"type": "insert_after", "element_id": "tools_8"}}

    await client.cardkit_batch_update("card", [add], sequence=36)
    await client.cardkit_batch_update("card", [reordered_add], sequence=36)
    await client.cardkit_batch_update("card", [repaired_add], sequence=36)

    first, repeated, repaired = (
        call.args[0].request_body.uuid for call in batch_update.await_args_list
    )
    assert len(first) == 32
    assert repeated == first
    assert repaired != first


@pytest.mark.asyncio
async def test_partial_only_batch_retries_missing_element_with_same_sequence() -> None:
    batch_update = AsyncMock(side_effect=[
        _Resp(ok=False, code=300313, msg="not find elementID : tools_6"),
        _Resp(ok=True),
    ])
    client = _client_with(batch_update=batch_update)
    actions = [{"action": "partial_update_element", "params": {"element_id": "tools_6"}}]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.feishu.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("hermes_lark_streaming.feishu.metrics.increment", side_effect=recorded.append),
    ):
        await client.cardkit_batch_update("card", actions, sequence=36)

    assert batch_update.await_count == 2
    assert sleep.await_count == 1
    assert {call.args[0].request_body.sequence for call in batch_update.await_args_list} == {36}
    assert recorded.count("api.element_not_found_recovered") == 1


@pytest.mark.asyncio
async def test_batch_with_add_does_not_retry_missing_element() -> None:
    batch_update = AsyncMock(return_value=_Resp(ok=False, code=300313, msg="not find elementID : tools_6"))
    client = _client_with(batch_update=batch_update)
    actions = [
        {"action": "add_elements", "params": {"elements": [{"element_id": "tools_7"}]}},
        {"action": "partial_update_element", "params": {"element_id": "tools_6"}},
    ]

    with pytest.raises(FeishuAPIError) as error:
        await client.cardkit_batch_update("card", actions, sequence=36)

    assert error.value.code == 300313
    assert batch_update.await_count == 1


@pytest.mark.asyncio
async def test_partial_only_missing_element_retry_is_bounded() -> None:
    batch_update = AsyncMock(return_value=_Resp(ok=False, code=300313, msg="not find elementID : tools_6"))
    client = _client_with(batch_update=batch_update)
    actions = [{"action": "partial_update_element", "params": {"element_id": "tools_6"}}]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.feishu.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("hermes_lark_streaming.feishu.metrics.increment", side_effect=recorded.append),
        pytest.raises(FeishuAPIError) as error,
    ):
        await client.cardkit_batch_update("card", actions, sequence=36)

    assert error.value.code == 300313
    assert batch_update.await_count == 3
    assert sleep.await_count == 2
    assert recorded.count("api.element_not_found_recovered") == 0


@pytest.mark.asyncio
async def test_reply_card_by_id_retries_gateway_timeout_once() -> None:
    reply = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=2200, msg="Gateway timeout. Please try again later."),
            _Resp(ok=True, data=SimpleNamespace(message_id="msg-ok")),
        ]
    )
    client = _client_with(reply=reply)

    assert await client.reply_card_by_id("anchor", "card") == "msg-ok"
    assert reply.await_count == 2
    first_request = reply.await_args_list[0].args[0]
    second_request = reply.await_args_list[1].args[0]
    assert first_request.request_body.uuid
    assert second_request.request_body.uuid == first_request.request_body.uuid


@pytest.mark.asyncio
async def test_send_card_to_chat_reuses_uuid_across_retries() -> None:
    create = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=2200, msg="Gateway timeout. Please try again later."),
            _Resp(ok=True, data=SimpleNamespace(message_id="msg-ok")),
        ]
    )
    client = _client_with(create_message=create)

    assert await client.send_card_to_chat("chat", {"schema": "2.0"}) == "msg-ok"
    assert create.await_count == 2
    first_request = create.await_args_list[0].args[0]
    second_request = create.await_args_list[1].args[0]
    assert first_request.request_body.uuid
    assert second_request.request_body.uuid == first_request.request_body.uuid


@pytest.mark.asyncio
async def test_cardkit_create_does_not_retry_non_transient_error() -> None:
    create = AsyncMock(side_effect=[_Resp(ok=False, code=230099, msg="content failed")])
    client = _client_with(card_create=create)

    with pytest.raises(FeishuAPIError):
        await client.cardkit_create({"schema": "2.0"})

    assert create.await_count == 1


@pytest.mark.asyncio
async def test_stream_element_retries_300313_with_same_sequence() -> None:
    content = MagicMock(
        side_effect=[
            _Resp(ok=False, code=300313, msg="element not found"),
            _Resp(ok=False, code=300313, msg="element not found"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]

    with patch("hermes_lark_streaming.feishu.asyncio.sleep", new=AsyncMock()) as sleep:
        await client.cardkit_stream_element("card", "answer_1", "hello", sequence=17)

    assert content.call_count == 3
    assert sleep.await_count == 2
    requests = [call.args[0] for call in content.call_args_list]
    assert {request.request_body.sequence for request in requests} == {17}


@pytest.mark.asyncio
async def test_stream_missing_element_recovery_metric_requires_success() -> None:
    content = MagicMock(side_effect=[
        _Resp(ok=False, code=300313, msg="element not found"),
        _Resp(ok=True),
    ])
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.feishu.asyncio.sleep", new=AsyncMock()),
        patch("hermes_lark_streaming.feishu.metrics.increment", side_effect=recorded.append),
    ):
        await client.cardkit_stream_element("card", "answer_1", "hello", sequence=17)

    assert recorded.count("api.element_not_found") == 1
    assert recorded.count("api.element_not_found_recovered") == 1


@pytest.mark.asyncio
async def test_stream_element_300313_retry_is_bounded() -> None:
    content = MagicMock(return_value=_Resp(ok=False, code=300313, msg="element not found"))
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.feishu.asyncio.sleep", new=AsyncMock()),
        patch("hermes_lark_streaming.feishu.metrics.increment", side_effect=recorded.append),
        pytest.raises(FeishuAPIError) as error,
    ):
        await client.cardkit_stream_element("card", "missing", "hello", sequence=4)

    assert error.value.code == 300313
    assert content.call_count == 4
    assert recorded.count("api.element_not_found_recovered") == 0


@pytest.mark.asyncio
async def test_stream_error_metrics_bucket_only_known_codes() -> None:
    content = MagicMock(
        side_effect=[
            _Resp(ok=False, code=300309, msg="streaming closed"),
            _Resp(ok=False, code=987654321, msg="opaque error"),
        ]
    )
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]
    observed: list[str] = []
    with patch("hermes_lark_streaming.feishu.metrics.increment", side_effect=observed.append):
        with pytest.raises(FeishuAPIError):
            await client.cardkit_stream_element("card", "answer_1", "text", sequence=2)
        with pytest.raises(FeishuAPIError):
            await client.cardkit_stream_element("card", "answer_1", "text", sequence=2)
    assert "api.cardkit_stream_element.error_code.300309" in observed
    assert "api.cardkit_stream_element.error_code.other" in observed
    assert not any("987654321" in name for name in observed)
