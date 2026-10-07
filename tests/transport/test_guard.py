from __future__ import annotations

from hermes_lark_streaming.transport.errors import MSG_NOT_FOUND, FeishuAPIError, MessageUnavailableError, api_error
from hermes_lark_streaming.transport.guard import (
    UnavailableGuard,
    extract_api_code,
    is_terminal_api_code,
    is_unavailable,
    mark_unavailable,
)


def _guard(reply_to: str | None = "om-anchor", card_id: str | None = "om-card") -> tuple[UnavailableGuard, list[int]]:
    fired: list[int] = []
    return UnavailableGuard(reply_to, lambda: card_id, lambda: fired.append(1)), fired


def test_terminal_error_terminates_once_and_marks_both_messages() -> None:
    guard, fired = _guard()
    assert guard.terminate("update", FeishuAPIError("gone", code=MSG_NOT_FOUND)) is True
    assert guard.terminate("again") is True
    assert fired == [1]
    assert is_unavailable("om-anchor") and is_unavailable("om-card")


def test_non_terminal_error_does_not_terminate() -> None:
    guard, fired = _guard()
    assert guard.terminate("update", FeishuAPIError("rate", code=230020)) is False
    assert guard.terminate("update", None) is False
    assert fired == []
    assert guard.should_skip("x") is False


def test_cached_unavailable_anchor_stops_later_updates() -> None:
    mark_unavailable("om-anchor", 231003)
    guard, fired = _guard()
    assert guard.should_skip("flush") is True
    assert guard.terminated and fired == [1]


def test_no_anchor_never_skips_without_error() -> None:
    guard, _ = _guard(reply_to=None)
    assert guard.should_skip("flush") is False


def test_extract_api_code_variants() -> None:
    assert extract_api_code(FeishuAPIError("x", code=230011)) == 230011
    assert extract_api_code(RuntimeError("request failed code=1000023")) == 1000023
    assert extract_api_code(RuntimeError("nothing")) is None
    assert extract_api_code(None) is None
    assert is_terminal_api_code(231003) and not is_terminal_api_code(None) and not is_terminal_api_code(1)


def test_api_error_picks_typed_subclass() -> None:
    assert isinstance(api_error("m", 231003), MessageUnavailableError)
    assert type(api_error("m", 5)) is FeishuAPIError
