"""Keep the Lark SDK's logger from leaking credentials or reporting clean shutdowns as errors.

The SDK logs the connection URL at INFO, and that URL carries ``access_key`` and ``ticket`` query
parameters. A logger filter redacts them whatever level the SDK configures later, and drops the
"receive message loop exit" record the SDK emits for a normal close (1000).
"""

from __future__ import annotations

import logging
import re

LOGGER_NAME = "Lark"
_SECRET_QUERY = re.compile(r"((?:access_key|ticket|device_id|service_id)=)[^&\s'\"]+", re.IGNORECASE)


def _is_normal_close(message: str) -> bool:
    """The SDK's own report of a clean websocket close (code 1000), logged at ERROR during shutdown."""
    return "receive message loop exit" in message and ("1000 (OK)" in message or "ConnectionClosedOK" in message)


class RedactWebsocketSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if _is_normal_close(message):
            return False
        redacted = _SECRET_QUERY.sub(r"\1***", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


def install() -> bool:
    """Attach the filter once; returns whether it was newly added."""
    logger = logging.getLogger(LOGGER_NAME)
    if any(isinstance(f, RedactWebsocketSecrets) for f in logger.filters):
        return False
    logger.addFilter(RedactWebsocketSecrets())
    return True
