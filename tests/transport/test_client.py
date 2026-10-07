"""Feishu client transient-error behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_lark_streaming.transport.channel import CardChannel
from hermes_lark_streaming.transport.client import CardKitClient, ClientConfig
from hermes_lark_streaming.transport.errors import (
    CardLimitError,
    ElementNotFoundError,
    FeishuAPIError,
    StreamingClosedError,
)
from hermes_lark_streaming.transport.limits import CardInspection


class _Resp:
    def __init__(self, *, ok: bool, code: int = 0, msg: str = "", data: object | None = None) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self._ok


def _client_with(**methods: AsyncMock) -> CardKitClient:
    sdk = SimpleNamespace(
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
                image=SimpleNamespace(acreate=methods.get("image_create", AsyncMock())),
                file=SimpleNamespace(acreate=methods.get("file_create", AsyncMock())),
            ),
        ),
    )
    return CardKitClient(ClientConfig("app", "secret-value"), sdk=sdk, fetch_image=lambda url: b"png-bytes")


def _channel(sequence: int) -> CardChannel:
    """A channel whose next write uses ``sequence``."""
    return CardChannel("card", sequence=sequence - 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "image"])
@pytest.mark.parametrize("reply_to", [None, "om-parent"])
async def test_media_send_preserves_caller_request_uuid(kind: str, reply_to: str | None) -> None:
    create = AsyncMock(return_value=_Resp(ok=True, data=SimpleNamespace(message_id="om-media")))
    reply = AsyncMock(return_value=_Resp(ok=True, data=SimpleNamespace(message_id="om-media")))
    client = _client_with(create_message=create, reply=reply)

    if kind == "file":
        result = await client.send_file(
            "chat",
            "file-key",
            reply_to=reply_to,
            request_uuid="stable-media-uuid",
        )
    else:
        result = await client.send_image(
            "chat",
            "image-key",
            reply_to=reply_to,
            request_uuid="stable-media-uuid",
        )

    assert result == "om-media"
    send = reply if reply_to else create
    assert send.await_args.args[0].request_body.uuid == "stable-media-uuid"


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

    assert (await client.create_card({"schema": "2.0"})).card_id == "card-ok"
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

    await client.batch_update(_channel(7), [{"action": "add"}])

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

    await client.batch_update(_channel(36), [add])
    await client.batch_update(_channel(36), [reordered_add])
    await client.batch_update(_channel(36), [repaired_add])

    first, repeated, repaired = (call.args[0].request_body.uuid for call in batch_update.await_args_list)
    assert len(first) == 32
    assert repeated == first
    assert repaired != first


@pytest.mark.asyncio
async def test_cardkit_full_update_retries_with_same_idempotency_uuid() -> None:
    update = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=1663, msg="internal error"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(card_update=update)

    with patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()):
        await client.update_card(_channel(18), {"schema": "2.0"})

    first, retried = (call.args[0].request_body for call in update.await_args_list)
    assert first.sequence == retried.sequence == 18
    assert first.uuid and first.uuid == retried.uuid


@pytest.mark.asyncio
async def test_cardkit_close_retries_with_same_idempotency_uuid() -> None:
    settings = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=2200, msg="gateway timeout"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(settings=settings)

    with patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()):
        await client.close_streaming(_channel(19))

    first, retried = (call.args[0].request_body for call in settings.await_args_list)
    assert first.sequence == retried.sequence == 19
    assert first.uuid and first.uuid == retried.uuid


@pytest.mark.asyncio
async def test_partial_only_batch_retries_missing_element_with_same_sequence() -> None:
    batch_update = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=300313, msg="not find elementID : tools_6"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(batch_update=batch_update)
    actions = [{"action": "partial_update_element", "params": {"element_id": "tools_6"}}]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("hermes_lark_streaming.transport.client.metrics.increment", side_effect=recorded.append),
    ):
        await client.batch_update(_channel(36), actions)

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
        await client.batch_update(_channel(36), actions)

    assert error.value.code == 300313
    assert batch_update.await_count == 1


@pytest.mark.asyncio
async def test_partial_only_missing_element_retry_is_bounded() -> None:
    batch_update = AsyncMock(return_value=_Resp(ok=False, code=300313, msg="not find elementID : tools_6"))
    client = _client_with(batch_update=batch_update)
    actions = [{"action": "partial_update_element", "params": {"element_id": "tools_6"}}]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("hermes_lark_streaming.transport.client.metrics.increment", side_effect=recorded.append),
        pytest.raises(FeishuAPIError) as error,
    ):
        await client.batch_update(_channel(36), actions)

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

    assert await client.reply_card("anchor", "card") == "msg-ok"
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

    assert await client.send_card("chat", {"schema": "2.0"}) == "msg-ok"
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
        await client.create_card({"schema": "2.0"})

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

    with patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()) as sleep:
        await client.update_element_content(_channel(17), "answer_1", "hello")

    assert content.call_count == 3
    assert sleep.await_count == 2
    requests = [call.args[0] for call in content.call_args_list]
    assert {request.request_body.sequence for request in requests} == {17}
    assert len({request.request_body.uuid for request in requests}) == 1
    assert requests[0].request_body.uuid


@pytest.mark.asyncio
async def test_stream_missing_element_recovery_metric_requires_success() -> None:
    content = MagicMock(
        side_effect=[
            _Resp(ok=False, code=300313, msg="element not found"),
            _Resp(ok=True),
        ]
    )
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()),
        patch("hermes_lark_streaming.transport.client.metrics.increment", side_effect=recorded.append),
    ):
        await client.update_element_content(_channel(17), "answer_1", "hello")

    assert recorded.count("api.element_not_found") == 1
    assert recorded.count("api.element_not_found_recovered") == 1


@pytest.mark.asyncio
async def test_stream_element_300313_retry_is_bounded() -> None:
    content = MagicMock(return_value=_Resp(ok=False, code=300313, msg="element not found"))
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]
    recorded: list[str] = []

    with (
        patch("hermes_lark_streaming.transport.client.asyncio.sleep", new=AsyncMock()),
        patch("hermes_lark_streaming.transport.client.metrics.increment", side_effect=recorded.append),
        pytest.raises(FeishuAPIError) as error,
    ):
        await client.update_element_content(_channel(4), "missing", "hello")

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
    with patch("hermes_lark_streaming.transport.client.metrics.increment", side_effect=observed.append):
        with pytest.raises(FeishuAPIError):
            await client.update_element_content(_channel(2), "answer_1", "text")
        with pytest.raises(FeishuAPIError):
            await client.update_element_content(_channel(2), "answer_1", "text")
    assert "api.cardkit_stream_element.error_code.300309" in observed
    assert "api.cardkit_stream_element.error_code.other" in observed
    assert not any("987654321" in name for name in observed)


@pytest.mark.asyncio
async def test_sequence_is_committed_only_on_success_and_strictly_increases() -> None:
    update = AsyncMock(
        side_effect=[
            _Resp(ok=False, code=230099, msg="rejected"),
            _Resp(ok=True),
            _Resp(ok=True),
        ]
    )
    client = _client_with(card_update=update)
    channel = CardChannel("card")

    with pytest.raises(FeishuAPIError):
        await client.update_card(channel, {"schema": "2.0"})
    assert channel.sequence == 1
    await client.update_card(channel, {"schema": "2.0"})
    await client.update_card(channel, {"schema": "2.0"})

    assert [call.args[0].request_body.sequence for call in update.await_args_list] == [2, 2, 3]
    assert channel.sequence == 3


@pytest.mark.asyncio
async def test_concurrent_writers_on_one_card_are_serialized_in_order() -> None:
    active = 0
    peak = 0
    seen: list[int] = []

    async def slow_batch(request: object) -> _Resp:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        seen.append(request.request_body.sequence)  # type: ignore[attr-defined]
        await asyncio.sleep(0.01)
        active -= 1
        return _Resp(ok=True)

    client = _client_with(batch_update=AsyncMock(side_effect=slow_batch))
    channel = CardChannel("card")
    await asyncio.gather(*(client.delete_element(channel, f"e{i}") for i in range(5)))

    assert peak == 1
    assert seen == [2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_close_streaming_marks_channel() -> None:
    client = _client_with(settings=AsyncMock(return_value=_Resp(ok=True)))
    channel = CardChannel("card")
    await client.close_streaming(channel)
    assert channel.streaming is False


@pytest.mark.asyncio
async def test_card_over_limit_is_rejected_before_any_request_or_sequence() -> None:
    update = AsyncMock()
    client = _client_with(card_update=update)
    channel = CardChannel("card")
    unsafe = CardInspection(json_bytes=10**6, elements=999, tables=0, safe=False)
    with (
        patch("hermes_lark_streaming.transport.client.inspect_card", return_value=unsafe),
        pytest.raises(CardLimitError) as error,
    ):
        await client.update_card(channel, {"schema": "2.0"})
    assert error.value.code == 230099
    update.assert_not_awaited()
    assert channel.sequence == 1


@pytest.mark.asyncio
async def test_batch_helpers_build_expected_actions() -> None:
    batch = AsyncMock(return_value=_Resp(ok=True))
    client = _client_with(batch_update=batch)
    channel = CardChannel("card")

    await client.add_elements(channel, [{"tag": "markdown", "element_id": "x"}])
    await client.add_elements(channel, [{"tag": "markdown"}], position="insert_before", target_element_id="loading")
    await client.patch_element(channel, "answer", {"content": "hi"})
    await client.delete_element(channel, "old")
    with pytest.raises(ValueError):
        await client.add_elements(channel, [], position="insert_after")

    import json

    actions = [json.loads(call.args[0].request_body.actions)[0] for call in batch.await_args_list]
    assert actions[0] == {
        "action": "add_elements",
        "params": {"type": "append", "elements": [{"tag": "markdown", "element_id": "x"}]},
    }
    assert actions[1]["params"]["target_element_id"] == "loading"
    assert actions[2] == {
        "action": "partial_update_element",
        "params": {"element_id": "answer", "partial_element": {"content": "hi"}},
    }
    assert actions[3] == {"action": "delete_elements", "params": {"element_ids": ["old"]}}
    assert channel.sequence == 5


@pytest.mark.asyncio
async def test_typed_errors_by_code() -> None:
    content = MagicMock(side_effect=[_Resp(ok=False, code=300309, msg="closed")])
    client = _client_with(card_element_content=content)  # type: ignore[arg-type]
    with pytest.raises(StreamingClosedError):
        await client.update_element_content(CardChannel("card"), "answer", "x")

    batch = AsyncMock(return_value=_Resp(ok=False, code=300313, msg="nf"))
    client = _client_with(batch_update=batch)
    with pytest.raises(ElementNotFoundError):
        await client.add_elements(CardChannel("card"), [{"tag": "markdown"}])


@pytest.mark.asyncio
async def test_error_messages_redact_credentials() -> None:
    secret_msg = "bad Bearer t-abcdefghijklmnop app_secret=ABCDEFGHIJKLMNOP tenant_access_token: t-0123456789abcdef"
    create = AsyncMock(return_value=_Resp(ok=False, code=230099, msg=secret_msg))
    client = _client_with(card_create=create)
    with pytest.raises(FeishuAPIError) as error:
        await client.create_card({"schema": "2.0"})
    text = str(error.value)
    assert "abcdefghijklmnop" not in text
    assert "ABCDEFGHIJKLMNOP" not in text
    assert "0123456789abcdef" not in text


def test_config_repr_hides_secret_and_requires_values() -> None:
    config = ClientConfig("cli_app", "super-secret-value")
    assert "super-secret-value" not in repr(config)
    with pytest.raises(ValueError):
        ClientConfig("", "x")
    with pytest.raises(ValueError):
        ClientConfig("a", "  ")


@pytest.mark.asyncio
async def test_upload_image_downloads_then_uploads_and_swallows_failures() -> None:
    ok = AsyncMock(return_value=_Resp(ok=True, data=SimpleNamespace(image_key="img_v3_x")))
    client = _client_with(image_create=ok)
    assert await client.upload_image("https://example.com/a.png") == "img_v3_x"

    rejected = AsyncMock(return_value=_Resp(ok=False, code=234001, msg="bad"))
    assert await _client_with(image_create=rejected).upload_image("https://example.com/a.png") is None

    client = _client_with(image_create=ok)
    client._fetch_image = lambda url: None  # type: ignore[method-assign]
    assert await client.upload_image("https://example.com/a.png") is None


def test_download_image_rejects_non_http_schemes() -> None:
    from hermes_lark_streaming.transport.client import download_image

    assert download_image("file:///etc/passwd") is None
    assert download_image("ftp://example.com/a.png") is None


@pytest.mark.asyncio
async def test_upload_file_returns_key_or_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "a.txt"
    path.write_text("x")
    good = AsyncMock(return_value=_Resp(ok=True, data=SimpleNamespace(file_key="file_x")))
    assert await _client_with(file_create=good).upload_file(str(path)) == "file_x"
    bad = AsyncMock(return_value=_Resp(ok=False, code=1, msg="no"))
    assert await _client_with(file_create=bad).upload_file(str(path)) is None
    assert await _client_with(file_create=good).upload_file(str(tmp_path / "missing")) is None
