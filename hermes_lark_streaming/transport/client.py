"""CardKit / IM client over lark-oapi.

Every CardKit mutation takes a :class:`CardChannel`, so the sequence is allocated under the card's write
lock and committed only when the call succeeds. Request UUIDs are derived from (operation, card, sequence,
payload), which keeps retries -- including after a process restart -- idempotent.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import lark_oapi as lark
from lark_oapi.api.cardkit.v1 import (
    BatchUpdateCardRequest,
    BatchUpdateCardRequestBody,
    Card,
    ContentCardElementRequest,
    ContentCardElementRequestBody,
    CreateCardRequest,
    CreateCardRequestBody,
    SettingsCardRequest,
    SettingsCardRequestBody,
    UpdateCardRequest,
    UpdateCardRequestBody,
)
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.core.token.manager import TokenManager

from .channel import CardChannel
from .errors import (
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_NOT_FOUND,
    CARDKIT_RATE_LIMITED,
    CARDKIT_TRANSIENT_ERROR_CODES,
    METRIC_ERROR_CODES,
    CardLimitError,
    FeishuAPIError,
    api_error,
)
from .limits import compact_card, inspect_card
from .telemetry import metrics

_logger = logging.getLogger("hermes_lark_streaming")

DEFAULT_DOMAIN = "https://open.feishu.cn"  # Larksuite: https://open.larksuite.com
_OPEN_APIS_SUFFIX = "/open-apis"
_TRANSIENT_RETRY_DELAYS_SEC = (0.15, 0.5, 1.0)
_ELEMENT_NOT_FOUND_RETRY_DELAYS_SEC = (0.2, 0.4, 0.8)
_BATCH_ELEMENT_NOT_FOUND_RETRY_DELAYS_SEC = (0.2, 0.4)
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # Feishu image upload limit

T = TypeVar("T")
ImageFetcher = Callable[[str], bytes | None]


@dataclass(frozen=True)
class ClientConfig:
    app_id: str
    app_secret: str = field(repr=False)
    base_url: str = DEFAULT_DOMAIN

    def __post_init__(self) -> None:
        if not isinstance(self.app_id, str) or not self.app_id.strip():
            raise ValueError("app_id is required")
        if not isinstance(self.app_secret, str) or not self.app_secret.strip():
            raise ValueError("app_secret is required")


def download_image(url: str, timeout: int = 15) -> bytes | None:
    """Blocking http(s) image download, capped at ``MAX_IMAGE_BYTES`` (run it in a worker thread)."""
    if urlparse(url).scheme not in {"http", "https"}:
        return None
    try:
        req = Request(url, headers={"User-Agent": "hermes-lark-streaming/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = bytes(resp.read(MAX_IMAGE_BYTES + 1))
    except (URLError, OSError, ValueError):
        _logger.debug("image download failed")
        return None
    return data if len(data) <= MAX_IMAGE_BYTES else None


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def request_uuid_for(operation: str, card_id: str, sequence: int, payload: Any) -> str:
    """Stable CardKit mutation UUID: same (operation, card, sequence, payload) -> same UUID."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"hermes-lark-streaming:cardkit:{operation}:{card_id}:{sequence}:{canonical}"
    ).hex


def _card_dumps(card: dict[str, Any]) -> str:
    prepared = compact_card(card)
    inspection = inspect_card(prepared)
    if not inspection.safe:
        metrics.increment("card.limit_reject")
        raise CardLimitError(
            f"card exceeds safe CardKit limits: bytes={inspection.json_bytes}, elements={inspection.elements}",
            CARDKIT_CONTENT_FAILED,
        )
    if prepared != card:
        metrics.increment("card.compacted")
    return json.dumps(prepared, ensure_ascii=False)


def _check(response: Any, operation: str) -> None:
    if not response.success():
        code = response.code or 0
        raise api_error(f"{operation}: code={code}, msg={response.msg or ''}", code)


class CardKitClient:
    """Async Feishu/Lark client. The SDK refreshes ``tenant_access_token`` itself.

    ``sdk`` and ``fetch_image`` exist for tests; production callers pass only the config.
    """

    def __init__(
        self,
        config: ClientConfig,
        *,
        sdk: Any | None = None,
        fetch_image: ImageFetcher = download_image,
    ) -> None:
        self.config = config
        self._fetch_image = fetch_image
        self._token: Callable[[], Any] | None = None
        if sdk is not None:
            self._sdk = sdk
        else:
            domain = config.base_url.strip().rstrip("/").removesuffix(_OPEN_APIS_SUFFIX) or DEFAULT_DOMAIN
            self._sdk = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).domain(domain).build()
            sdk_config = self._sdk.config
            self._token = lambda: TokenManager.get_self_tenant_token(sdk_config)

    async def _warm_token(self) -> None:
        """The SDK's async calls refresh ``tenant_access_token`` with a blocking HTTP request (about every
        two hours, up to its 30s timeout) right on the event loop. Fetch it in a thread first: a cache hit
        costs a thread hop, a refresh no longer stalls the gateway. A failure here is left to the call."""
        if self._token is not None:
            try:
                await asyncio.to_thread(self._token)
            except Exception:
                _logger.debug("tenant token pre-fetch failed", exc_info=True)

    # -- plumbing -----------------------------------------------------------------------------

    async def _call(self, operation: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Run an SDK call, retrying transient server errors (the request UUID is fixed by the caller)."""
        attempts = len(_TRANSIENT_RETRY_DELAYS_SEC) + 1
        for attempt in range(attempts):
            started = time.monotonic()
            metrics.increment(f"api.{operation}.attempt")
            try:
                await self._warm_token()
                resp = await call()
                _check(resp, operation)
                metrics.increment(f"api.{operation}.success")
                return resp
            except FeishuAPIError as exc:
                metrics.increment(f"api.{operation}.error")
                bucket = str(exc.code) if exc.code in METRIC_ERROR_CODES else "other"
                metrics.increment(f"api.{operation}.error_code.{bucket}")
                if exc.code == CARDKIT_RATE_LIMITED:
                    metrics.increment("api.rate_limited")
                if exc.code == CARDKIT_ELEMENT_NOT_FOUND:
                    metrics.increment("api.element_not_found")
                if exc.code not in CARDKIT_TRANSIENT_ERROR_CODES or attempt >= attempts - 1:
                    raise
                delay = _TRANSIENT_RETRY_DELAYS_SEC[attempt]
                _logger.warning(
                    "%s transient Feishu API error code=%s, retrying attempt=%d/%d delay=%.2fs",
                    operation,
                    exc.code,
                    attempt + 2,
                    attempts,
                    delay,
                )
                await asyncio.sleep(delay)
            finally:
                metrics.observe(f"api.{operation}", (time.monotonic() - started) * 1000)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _retry_not_visible(
        self, operation: str, delays: tuple[float, ...], call: Callable[[], Awaitable[Any]]
    ) -> None:
        """Absorb CardKit's eventual-consistency window after add_elements (300313), boundedly."""
        for attempt in range(len(delays) + 1):
            try:
                await self._call(operation, call)
            except FeishuAPIError as exc:
                if exc.code != CARDKIT_ELEMENT_NOT_FOUND or attempt >= len(delays):
                    raise
                _logger.info("%s element not visible yet attempt=%d/%d", operation, attempt + 1, len(delays))
                await asyncio.sleep(delays[attempt])
            else:
                if attempt:
                    metrics.increment("api.element_not_found_recovered")
                return

    # -- cards --------------------------------------------------------------------------------

    async def create_card(self, card: dict[str, Any]) -> CardChannel:
        """Create a CardKit entity; the returned channel starts at sequence 1."""
        request = (
            CreateCardRequest.builder()
            .request_body(CreateCardRequestBody.builder().type("card_json").data(_card_dumps(card)).build())
            .build()
        )
        resp = await self._call("cardkit_create", lambda: self._sdk.cardkit.v1.card.acreate(request))
        if resp.data and resp.data.card_id:
            return CardChannel(str(resp.data.card_id))
        raise FeishuAPIError("cardkit_create: response missing card_id")

    async def update_element_content(self, channel: CardChannel, element_id: str, content: str) -> None:
        """Typewriter update of one element's content."""

        async def op(seq: int) -> None:
            request_uuid = request_uuid_for(
                "stream-element", channel.card_id, seq, {"element_id": element_id, "content": content}
            )
            request = (
                ContentCardElementRequest.builder()
                .card_id(channel.card_id)
                .element_id(element_id)
                .request_body(
                    ContentCardElementRequestBody.builder().content(content).uuid(request_uuid).sequence(seq).build()
                )
                .build()
            )
            await self._retry_not_visible(
                "cardkit_stream_element",
                _ELEMENT_NOT_FOUND_RETRY_DELAYS_SEC,
                lambda: asyncio.to_thread(self._sdk.cardkit.v1.card_element.content, request),
            )

        await channel.write(op)

    async def batch_update(self, channel: CardChannel, actions: list[dict[str, Any]]) -> None:
        """Apply raw CardKit batch actions (add / patch / delete elements) as one sequenced write."""

        async def op(seq: int) -> None:
            request_uuid = request_uuid_for("batch-update", channel.card_id, seq, actions)
            request = (
                BatchUpdateCardRequest.builder()
                .card_id(channel.card_id)
                .request_body(
                    BatchUpdateCardRequestBody.builder()
                    .uuid(request_uuid)
                    .sequence(seq)
                    .actions(_dumps(actions))
                    .build()
                )
                .build()
            )
            # Partial updates overwrite fields, so they are safe to repeat while a fresh element becomes
            # visible. A batch containing add_elements is never replayed on 300313: the caller reconciles
            # first and builds a new batch.
            delays = (
                _BATCH_ELEMENT_NOT_FOUND_RETRY_DELAYS_SEC
                if actions and all(a.get("action") == "partial_update_element" for a in actions)
                else ()
            )
            await self._retry_not_visible(
                "cardkit_batch_update", delays, lambda: self._sdk.cardkit.v1.card.abatch_update(request)
            )

        await channel.write(op)

    async def add_elements(
        self,
        channel: CardChannel,
        elements: list[dict[str, Any]],
        *,
        position: str = "append",
        target_element_id: str | None = None,
    ) -> None:
        """Insert elements: ``position`` is append, insert_before or insert_after (the latter two need a target)."""
        params: dict[str, Any] = {"type": position, "elements": elements}
        if position != "append":
            if not target_element_id:
                raise ValueError(f"{position} requires target_element_id")
            params["target_element_id"] = target_element_id
        await self.batch_update(channel, [{"action": "add_elements", "params": params}])

    async def patch_element(self, channel: CardChannel, element_id: str, partial: dict[str, Any]) -> None:
        """Merge ``partial`` into an existing element."""
        action = {"action": "partial_update_element", "params": {"element_id": element_id, "partial_element": partial}}
        await self.batch_update(channel, [action])

    async def delete_element(self, channel: CardChannel, element_id: str) -> None:
        await self.batch_update(channel, [{"action": "delete_elements", "params": {"element_ids": [element_id]}}])

    async def update_card(self, channel: CardChannel, card: dict[str, Any]) -> None:
        """Replace the whole card (terminal update)."""
        card_data = _card_dumps(card)

        async def op(seq: int) -> None:
            request_uuid = request_uuid_for("update", channel.card_id, seq, card_data)
            request = (
                UpdateCardRequest.builder()
                .card_id(channel.card_id)
                .request_body(
                    UpdateCardRequestBody.builder()
                    .card(Card.builder().type("card_json").data(card_data).build())
                    .uuid(request_uuid)
                    .sequence(seq)
                    .build()
                )
                .build()
            )
            await self._call("cardkit_update", lambda: self._sdk.cardkit.v1.card.aupdate(request))

        await channel.write(op)

    async def close_streaming(self, channel: CardChannel) -> None:
        """Turn off the card's streaming mode."""
        settings = {"streaming_mode": False}

        async def op(seq: int) -> None:
            request_uuid = request_uuid_for("close-streaming", channel.card_id, seq, settings)
            request = (
                SettingsCardRequest.builder()
                .card_id(channel.card_id)
                .request_body(
                    SettingsCardRequestBody.builder()
                    .settings(_dumps(settings))
                    .uuid(request_uuid)
                    .sequence(seq)
                    .build()
                )
                .build()
            )
            await self._call("cardkit_close_streaming", lambda: self._sdk.cardkit.v1.card.asettings(request))

        await channel.write(op)
        channel.streaming = False

    # -- messages -----------------------------------------------------------------------------

    async def _send_message(
        self,
        operation: str,
        msg_type: str,
        content: str,
        *,
        chat_id: str | None,
        reply_to: str | None,
        request_uuid: str | None,
    ) -> str:
        """Create (chat_id) or reply (reply_to) a message; the UUID is fixed before any retry."""
        request_uuid = request_uuid or uuid.uuid4().hex
        if reply_to:
            reply = (
                ReplyMessageRequest.builder()
                .message_id(reply_to)
                .request_body(
                    ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).uuid(request_uuid).build()
                )
                .build()
            )
            resp = await self._call(operation, lambda: self._sdk.im.v1.message.areply(reply))
        else:
            if not chat_id:
                raise ValueError("chat_id or reply_to is required")
            create = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type(msg_type)
                    .content(content)
                    .uuid(request_uuid)
                    .build()
                )
                .build()
            )
            resp = await self._call(operation, lambda: self._sdk.im.v1.message.acreate(create))
        if resp.data and resp.data.message_id:
            return str(resp.data.message_id)
        raise FeishuAPIError(f"{operation}: response missing message_id")

    async def send_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        *,
        reply_to: str | None = None,
        request_uuid: str | None = None,
    ) -> str:
        """Send an inline-JSON card (cron/background); returns message_id."""
        return await self._send_message(
            "send_card",
            "interactive",
            _card_dumps(card),
            chat_id=chat_id,
            reply_to=reply_to,
            request_uuid=request_uuid,
        )

    async def send_card_entity(
        self, chat_id: str, channel: CardChannel | str, *, request_uuid: str | None = None
    ) -> str:
        """Send a CardKit card entity to a chat (the non-reply counterpart of ``reply_card``)."""
        card_id = channel if isinstance(channel, str) else channel.card_id
        return await self._send_message(
            "send_card_entity",
            "interactive",
            _dumps({"type": "card", "data": {"card_id": card_id}}),
            chat_id=chat_id,
            reply_to=None,
            request_uuid=request_uuid,
        )

    async def reply_card(self, message_id: str, channel: CardChannel | str, *, request_uuid: str | None = None) -> str:
        """Reply to ``message_id`` with a CardKit card entity; returns the card message_id."""
        card_id = channel if isinstance(channel, str) else channel.card_id
        return await self._send_message(
            "reply_card",
            "interactive",
            _dumps({"type": "card", "data": {"card_id": card_id}}),
            chat_id=None,
            reply_to=message_id,
            request_uuid=request_uuid,
        )

    async def send_text(
        self, chat_id: str, text: str, *, reply_to: str | None = None, request_uuid: str | None = None
    ) -> str:
        return await self._send_message(
            "send_text", "text", _dumps({"text": text}), chat_id=chat_id, reply_to=reply_to, request_uuid=request_uuid
        )

    async def send_file(
        self, chat_id: str, file_key: str, *, reply_to: str | None = None, request_uuid: str | None = None
    ) -> str:
        return await self._send_message(
            "send_file",
            "file",
            _dumps({"file_key": file_key}),
            chat_id=chat_id,
            reply_to=reply_to,
            request_uuid=request_uuid,
        )

    async def send_image(
        self, chat_id: str, image_key: str, *, reply_to: str | None = None, request_uuid: str | None = None
    ) -> str:
        """Send an inline image message from an ``image_key``."""
        return await self._send_message(
            "send_image",
            "image",
            _dumps({"image_key": image_key}),
            chat_id=chat_id,
            reply_to=reply_to,
            request_uuid=request_uuid,
        )

    # -- uploads (best effort: failures return None) ------------------------------------------

    async def upload_image(self, image_url: str) -> str | None:
        """Download a remote image and upload it; returns ``img_key`` or None."""
        try:
            data = await asyncio.get_running_loop().run_in_executor(None, self._fetch_image, image_url)
        except Exception:
            _logger.debug("image download failed", exc_info=True)
            return None
        if data is None:
            return None
        return await self._upload_image_stream(io.BytesIO(data), "remote image")

    async def upload_local_image(self, image_path: str) -> str | None:
        """Upload a local image for inline display (``msg_type=image``); returns ``image_key`` or None."""
        image = Path(image_path)
        try:
            with image.open("rb") as handle:
                return await self._upload_image_stream(handle, image.name)
        except Exception:
            _logger.warning("image upload failed: %s", image.name, exc_info=True)
            return None

    async def _upload_image_stream(self, stream: Any, label: str) -> str | None:
        request = (
            CreateImageRequest.builder()
            .request_body(CreateImageRequestBody.builder().image_type("message").image(stream).build())
            .build()
        )
        try:
            await self._warm_token()
            resp = await self._sdk.im.v1.image.acreate(request)
        except Exception:
            _logger.warning("image upload failed: %s", label, exc_info=True)
            return None
        if resp.success() and resp.data and resp.data.image_key:
            return str(resp.data.image_key)
        _logger.warning("image upload rejected: %s code=%s", label, getattr(resp, "code", 0))
        return None

    async def upload_file(self, file_path: str, *, file_type: str = "stream") -> str | None:
        """Upload a local file; returns ``file_key`` or None.

        ``file_type`` is stream/mp4/opus/pdf/doc/xls/ppt and decides Feishu's preview mode.
        """
        path = Path(file_path)
        try:
            with path.open("rb") as handle:
                request = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder().file_type(file_type).file_name(path.name).file(handle).build()
                    )
                    .build()
                )
                await self._warm_token()
                resp = await self._sdk.im.v1.file.acreate(request)
        except Exception:
            _logger.warning("file upload failed: %s", path.name, exc_info=True)
            return None
        if resp.success() and resp.data and resp.data.file_key:
            return str(resp.data.file_key)
        _logger.warning("file upload rejected: %s code=%s", path.name, getattr(resp, "code", 0))
        return None
