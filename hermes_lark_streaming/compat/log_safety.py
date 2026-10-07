"""Keep ephemeral Feishu websocket credentials out of gateway logs.

The Lark SDK logs the connection URL at INFO, and that URL carries ``access_key`` and ``ticket`` query
parameters. A logger filter redacts them whatever level the SDK configures later.
"""

from __future__ import annotations

import logging
import re

LOGGER_NAME = "Lark"
_SECRET_QUERY = re.compile(r"((?:access_key|ticket|device_id|service_id)=)[^&\s'\"]+", re.IGNORECASE)


class RedactWebsocketSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
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
