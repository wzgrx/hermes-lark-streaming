"""Typed Feishu/CardKit errors, credential redaction and delivery-failure classification."""

from __future__ import annotations

import re

from .ledger import DeliveryStatus

CARDKIT_GATEWAY_TIMEOUT = 2200
CARDKIT_INTERNAL_ERROR = 1663
CARDKIT_SERVER_INTERNAL_ERROR = 300000
CARDKIT_TRANSIENT_ERROR_CODES = frozenset(
    {CARDKIT_GATEWAY_TIMEOUT, CARDKIT_INTERNAL_ERROR, CARDKIT_SERVER_INTERNAL_ERROR}
)

CARDKIT_RATE_LIMITED = 230020  # 频控
CARDKIT_CONTENT_FAILED = 230099  # 卡片内容创建失败(通用码,需检查子错误)
CARDKIT_ELEMENT_LIMIT = 11310  # 子码: 卡片元素数量超限
CARDKIT_STREAMING_CLOSED = 300309  # 卡片流式模式已关闭
CARDKIT_ELEMENT_NOT_FOUND = 300313  # add_elements 后服务端元素尚未可见
CARDKIT_SEQUENCE_CONFLICT = 300317  # 更新序号冲突
MSG_DELETED = 231003
MSG_RECALLED = 230011
MSG_NOT_FOUND = 1000023  # 消息不存在/已删除
TERMINAL_MESSAGE_CODES = frozenset({MSG_DELETED, MSG_RECALLED, MSG_NOT_FOUND})

# Metrics use a fixed allowlist: an arbitrary provider code must not mint unbounded counter names.
METRIC_ERROR_CODES = frozenset(
    {
        CARDKIT_GATEWAY_TIMEOUT,
        CARDKIT_INTERNAL_ERROR,
        CARDKIT_SERVER_INTERNAL_ERROR,
        CARDKIT_RATE_LIMITED,
        CARDKIT_CONTENT_FAILED,
        CARDKIT_STREAMING_CLOSED,
        CARDKIT_ELEMENT_NOT_FOUND,
        CARDKIT_SEQUENCE_CONFLICT,
    }
)


def sanitize_message(msg: str) -> str:
    """Strip tokens and secrets from an error message."""
    msg = re.sub(r'(tenant_access_token["\s:=]+)([A-Za-z0-9_-]{10,})', r"\1***", msg)
    msg = re.sub(r'(app_secret["\s:=]+)([A-Za-z0-9]{10,})', r"\1***", msg)
    return re.sub(r"(Bearer\s+)([A-Za-z0-9_-]{10,})", r"\1***", msg)


class FeishuAPIError(RuntimeError):
    """A Feishu API failure carrying the API error code."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code

    @property
    def transient(self) -> bool:
        return self.code in CARDKIT_TRANSIENT_ERROR_CODES

    def extract_sub_code(self) -> int | None:
        """Sub-code from ``"..., ext=ErrCode: 11310; ..."`` messages."""
        m = re.search(r"ErrCode:\s*(\d+)", str(self))
        return int(m.group(1)) if m else None

    @property
    def element_limit(self) -> bool:
        return self.code == CARDKIT_CONTENT_FAILED and self.extract_sub_code() == CARDKIT_ELEMENT_LIMIT


class RateLimitedError(FeishuAPIError):
    pass


class StreamingClosedError(FeishuAPIError):
    """The card's streaming window ended (about ten minutes after creation)."""


class ElementNotFoundError(FeishuAPIError):
    pass


class SequenceConflictError(FeishuAPIError):
    pass


class MessageUnavailableError(FeishuAPIError):
    """The target message was deleted or recalled."""


class CardLimitError(FeishuAPIError):
    """Raised locally, before any request, when a card exceeds the safe CardKit budget."""


_BY_CODE: dict[int, type[FeishuAPIError]] = {
    CARDKIT_RATE_LIMITED: RateLimitedError,
    CARDKIT_STREAMING_CLOSED: StreamingClosedError,
    CARDKIT_ELEMENT_NOT_FOUND: ElementNotFoundError,
    CARDKIT_SEQUENCE_CONFLICT: SequenceConflictError,
    MSG_DELETED: MessageUnavailableError,
    MSG_RECALLED: MessageUnavailableError,
    MSG_NOT_FOUND: MessageUnavailableError,
}


def api_error(message: str, code: int = 0) -> FeishuAPIError:
    """Build the most specific error type for ``code`` (message is redacted)."""
    return _BY_CODE.get(code, FeishuAPIError)(sanitize_message(message), code)


def classify_delivery_failure(error: BaseException) -> DeliveryStatus:
    """Decide whether a failed user-visible send was rejected or ambiguous.

    A structured non-transient Feishu response proves the request was rejected. Transport failures and
    exhausted gateway/internal errors may have been committed before the response was lost, so they stay
    ``UNKNOWN`` to avoid a duplicate answer.
    """
    if isinstance(error, FeishuAPIError) and error.code and not error.transient:
        return DeliveryStatus.NOT_SENT
    return DeliveryStatus.UNKNOWN
