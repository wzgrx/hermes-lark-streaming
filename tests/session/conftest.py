from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import yaml

from hermes_lark_streaming.session.controller import Controller
from hermes_lark_streaming.transport import CardChannel, DeliveryLedger, FeishuAPIError


class FakeClient:
    """Records every CardKit call; failures are injected per operation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail: dict[str, Exception] = {}
        self.cards = 0

    def _maybe_fail(self, op: str) -> None:
        if op in self.fail:
            raise self.fail[op]

    async def create_card(self, card: dict[str, Any]) -> CardChannel:
        self._maybe_fail("create_card")
        self.cards += 1
        self.calls.append(("create_card", json.loads(json.dumps(card))))
        return CardChannel(f"card_{self.cards}")

    async def reply_card(self, message_id: str, channel: CardChannel, *, request_uuid: str | None = None) -> str:
        self._maybe_fail("reply_card")
        self.calls.append(("reply_card", (message_id, channel.card_id)))
        return f"om_{channel.card_id}"

    async def send_card_entity(self, chat_id: str, channel: CardChannel, *, request_uuid: str | None = None) -> str:
        self.calls.append(("send_card_entity", (chat_id, channel.card_id)))
        return f"om_{channel.card_id}"

    async def send_card(self, chat_id: str, card: dict[str, Any], **kw: Any) -> str:
        self.calls.append(("send_card", (chat_id, card, kw)))
        return "om_static"

    async def send_text(self, chat_id: str, text: str, **kw: Any) -> str:
        self.calls.append(("send_text", text))
        return "om_text"

    async def batch_update(self, channel: CardChannel, actions: list[dict[str, Any]]) -> None:
        self._maybe_fail("batch_update")

        async def op(seq: int) -> None:
            self.calls.append(("batch_update", (channel.card_id, seq, json.loads(json.dumps(actions)))))

        await channel.write(op)

    async def update_element_content(self, channel: CardChannel, element_id: str, content: str) -> None:
        self._maybe_fail("update_element_content")

        async def op(seq: int) -> None:
            self.calls.append(("stream", (channel.card_id, seq, element_id, content)))

        await channel.write(op)

    async def close_streaming(self, channel: CardChannel) -> None:
        async def op(seq: int) -> None:
            self.calls.append(("close", (channel.card_id, seq)))

        await channel.write(op)
        channel.streaming = False

    async def update_card(self, channel: CardChannel, card: dict[str, Any]) -> None:
        self._maybe_fail("update_card")

        async def op(seq: int) -> None:
            self.calls.append(("update_card", (channel.card_id, seq, json.loads(json.dumps(card)))))

        await channel.write(op)

    async def upload_image(self, url: str) -> str | None:
        return None

    def of(self, name: str) -> list[Any]:
        return [payload for op, payload in self.calls if op == name]


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def make_controller(tmp_path, client):
    def build(streaming: dict[str, Any] | None = None) -> Controller:
        config = {
            "streaming": {"enabled": True, "details": {"usage": False}, **(streaming or {})},
            "feishu": {"app_id": "cli_test", "app_secret": "secret"},
        }
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        ctl = Controller(tmp_path)
        ctl.ledger = DeliveryLedger(tmp_path / "ledger.json")
        ctl.pipeline.rt.ledger = ctl.ledger

        async def client_for(_chat: str) -> FakeClient:
            return client

        ctl.pipeline.rt.client_for = client_for  # type: ignore[assignment]
        ctl._client_for = client_for  # type: ignore[method-assign,assignment]
        ctl.static._client_for = client_for  # type: ignore[assignment]
        ctl.static._ledger = ctl.ledger
        return ctl

    return build


def state_tag(card: dict[str, Any]) -> str:
    """The zh footer line of a finished card (✅ 已完成 / 🛑 已停止 / ❌ 出错, plus 'N 步失败'),
    or the continuation pointer of a sealed card."""
    elements = card["body"]["elements"]
    for index, element in enumerate(elements):
        if element["tag"] == "hr" and index + 1 < len(elements):
            nxt = elements[index + 1]
            return str(nxt.get("i18n_content", {}).get("zh_cn") or nxt["content"])
    return json.dumps(elements[-1], ensure_ascii=False)


async def settle(ctl: Controller, delay: float = 0.4) -> None:
    """Let throttled flushes run."""
    await asyncio.sleep(delay)


__all__ = ["FakeClient", "FeishuAPIError", "settle", "state_tag"]
