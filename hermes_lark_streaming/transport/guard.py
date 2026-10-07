"""Stop updating once the target message was deleted or recalled."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from .errors import TERMINAL_MESSAGE_CODES

_logger = logging.getLogger("hermes_lark_streaming")

_CACHE_TTL_SEC = 30 * 60
_CACHE_MAX = 4096
# message_id -> (code, monotonic-free wall time). Deleted messages are a process-wide fact.
_unavailable: dict[str, tuple[int, float]] = {}


def _prune() -> None:
    now = time.time()
    for key in [k for k, (_, at) in _unavailable.items() if now - at > _CACHE_TTL_SEC]:
        del _unavailable[key]
    while len(_unavailable) > _CACHE_MAX:
        del _unavailable[next(iter(_unavailable))]


def mark_unavailable(message_id: str, code: int) -> None:
    _unavailable[message_id] = (code, time.time())
    _prune()


def is_unavailable(message_id: str | None) -> bool:
    if not message_id:
        return False
    _prune()
    return message_id in _unavailable


def clear_unavailable() -> None:
    _unavailable.clear()


def extract_api_code(err: BaseException | None) -> int | None:
    """API error code from ``err.code`` or a ``code=NNN`` fragment of its message."""
    if err is None:
        return None
    code = getattr(err, "code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    if err.args and isinstance(err.args[0], str):
        match = re.search(r"code[=:]\s*(\d+)", err.args[0])
        if match:
            return int(match.group(1))
    return None


def is_terminal_api_code(code: int | None) -> bool:
    return code is not None and code in TERMINAL_MESSAGE_CODES


class UnavailableGuard:
    """Terminates a reply pipeline when its anchor or card message turns out to be gone."""

    def __init__(
        self,
        reply_to_message_id: str | None,
        get_card_message_id: Callable[[], str | None],
        on_terminate: Callable[[], None],
    ) -> None:
        self._reply_to = reply_to_message_id
        self._get_card_message_id = get_card_message_id
        self._on_terminate = on_terminate
        self._terminated = False

    @property
    def terminated(self) -> bool:
        return self._terminated

    def should_skip(self, source: str) -> bool:
        """True when the pipeline is (or just became) terminated."""
        if self._terminated:
            return True
        if self._reply_to and is_unavailable(self._reply_to):
            return self.terminate(source)
        return False

    def terminate(self, source: str, err: BaseException | None = None) -> bool:
        """Terminate if ``err`` (or the cache) shows a terminal message code; True if terminated."""
        if self._terminated:
            return True
        card_msg_id = self._get_card_message_id()
        code = extract_api_code(err)
        if code is None:
            for message_id in (self._reply_to, card_msg_id):
                if message_id and message_id in _unavailable:
                    code = _unavailable[message_id][0]
                    break
        if code is None or not is_terminal_api_code(code):
            return False
        self._terminated = True
        self._on_terminate()
        _logger.warning(
            "reply pipeline terminated by unavailable message: source=%s code=%s message_id=%s",
            source,
            code,
            self._reply_to or card_msg_id or "unknown",
        )
        for message_id in (self._reply_to, card_msg_id):
            if message_id:
                mark_unavailable(message_id, code)
        return True
