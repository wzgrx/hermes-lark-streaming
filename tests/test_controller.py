"""controller.py 测试 — 会话生命周期边界条件."""

from __future__ import annotations

import time
from types import SimpleNamespace

from hermes_lark_streaming.controller import StreamCardController


def _enable(ctrl: StreamCardController) -> None:
    ctrl._cfg._raw = {
        "streaming": {"enabled": True},
        "feishu": {"app_id": "app", "app_secret": "secret"},
    }


class _DummyFlush:
    def mark_completed(self) -> None:
        pass


def test_on_message_started_ignores_none_message_id() -> None:
    ctrl = StreamCardController()
    _enable(ctrl)

    ctrl.on_message_started(message_id=None, chat_id="chat")  # type: ignore[arg-type]

    assert ctrl._sessions == {}


def test_prune_stale_sessions_ignores_none_key() -> None:
    ctrl = StreamCardController()
    stale_session = SimpleNamespace(
        created_at=time.time() - ctrl._session_ttl - 1,
        flush=_DummyFlush(),
        image_resolver=None,
    )
    ctrl._sessions[None] = stale_session  # type: ignore[index,assignment]

    ctrl._prune_stale_sessions()

    assert ctrl._sessions[None] is stale_session  # type: ignore[index]
