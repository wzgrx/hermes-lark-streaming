"""One-shot card deliveries: Cron results and finished background tasks."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from ..card.static import render_background, render_cron
from ..transport import CardKitClient, DeliveryLedger, DeliveryStatus, FeishuAPIError, classify_delivery_failure
from ..transport.media import deliver_media_files, hook_media_paths, strip_media_directives

_logger = logging.getLogger("hermes_lark_streaming")


class CronDeliveryOutcomeUnknown(RuntimeError):
    """A Cron card send may have committed without a usable message receipt."""


class StaticDelivery:
    def __init__(self, ledger: DeliveryLedger, client_for: Callable[[str], Awaitable[CardKitClient]]) -> None:
        self._ledger = ledger
        self._client_for = client_for

    async def cron(
        self, chat_id: str, content: str, *, task_name: str = "", run_time: str = "", job_id: str = "",
        media_files: object = None, text_size: str = "normal_v2",
    ) -> str:
        client = await self._client_for(chat_id)
        # Hermes strips MEDIA tags into media_files before calling the hook, and a True return skips its own
        # attachment delivery, so the attachments are sent here.
        paths = hook_media_paths(media_files)
        card = render_cron(strip_media_directives(content) if paths else content, task_name=task_name,
                           run_time=run_time, text_size=text_size)
        # A scheduled occurrence has a stable job id and due time; a prior unknown result is held, not resent.
        key = f"cron:{job_id}:{run_time}:{chat_id}" if job_id and run_time else ""
        request_uuid: str | None = None
        if key:
            entry, should_send = self._ledger.claim_send(key, "cron.card")
            if not should_send:
                if entry.status is DeliveryStatus.DELIVERED and entry.message_id:
                    await self._media(client, chat_id, paths, entry.message_id, key)
                    return entry.message_id
                raise CronDeliveryOutcomeUnknown("prior cron card outcome remains unknown")
            request_uuid = entry.request_uuid
        try:
            message_id = await client.send_card(chat_id, card, request_uuid=request_uuid)
        except FeishuAPIError as exc:
            if key:
                self._ledger.failed(key, classify_delivery_failure(exc), error_code=exc.code)
            raise
        except Exception as exc:
            raise CronDeliveryOutcomeUnknown("cron card send outcome unknown") from exc
        if not message_id:
            raise CronDeliveryOutcomeUnknown("cron card send returned no message_id")
        if key:
            self._ledger.delivered(key, card_id="", message_id=str(message_id))
        await self._media(client, chat_id, paths, message_id, key)
        return str(message_id)

    async def background(
        self, chat_id: str, preview: str, content: str, *, reply_to: str | None = None, text_size: str = "normal_v2",
    ) -> None:
        client = await self._client_for(chat_id)
        await client.send_card(chat_id, render_background(preview, content, text_size=text_size), reply_to=reply_to)

    async def _media(
        self, client: CardKitClient, chat_id: str, paths: list[str], reply_to: str, key: str,
    ) -> None:
        """Attachments follow the card; failures are logged and never change the card's outcome."""
        if not paths:
            return
        try:
            await deliver_media_files(
                client, chat_id, paths, reply_to_message_id=reply_to, delivery_key_prefix=key,
                ledger=self._ledger if key else None,
            )
        except Exception:
            _logger.warning("static card media delivery failed: chat=%s", chat_id[:12], exc_info=True)
