"""Read-only observers: bind to a real Gateway session key, never own delivery."""

from __future__ import annotations

import logging
from contextvars import copy_context
from typing import Any

HOOKS = ("pre_api_request", "post_api_request", "api_request_error", "post_auxiliary_call")
_logger = logging.getLogger("hermes_lark_streaming")


def observe(event: str, payload: dict[str, Any]) -> None:
    from .history import observe_history

    observe_history(event, payload)
    if payload.get("platform") not in {"feishu", "lark"} or payload.get("aux_task"):
        return
    try:
        from hermes_lark_streaming.controller import get_controller
        from hermes_lark_streaming.metrics import metrics

        # Hermes binds these named ContextVars in Gateway/tool worker threads.
        # Its legacy get_current_session_key() also falls back to process env;
        # do not use that fallback in a multiplexed observer.
        keys = {
            value
            for var, value in copy_context().items()
            if var.name in {"HERMES_SESSION_KEY", "approval_session_key"} and isinstance(value, str) and value
        }
        if len(keys) != 1:
            metrics.increment("footer.skip.context")
            return
        key = next(iter(keys))
        ctrl = get_controller()
        if not ctrl.enabled or ctrl._cfg.footer_mode != "enhanced" or not ctrl._cfg.footer_enabled:
            return
        session = ctrl._session_keys.get(key)
        if session is not None and not session.state.is_terminal:
            accepted = session.footer_state.observe(event, payload)
            metrics.increment("footer.event.accepted" if accepted else "footer.event.rejected")
        else:
            metrics.increment("footer.skip.session")
    except Exception:
        # Avoid logging provider payloads, URLs or exceptions which could contain secrets.
        _logger.debug("footer observer skipped an incompatible event")


def register(context: object) -> tuple[str, ...]:
    register_hook = getattr(context, "register_hook", None)
    if not callable(register_hook):
        return ()
    try:
        from hermes_cli.plugins import VALID_HOOKS  # type: ignore[import-not-found]
    except ImportError:
        return ()
    registered = []
    for name in HOOKS:
        if name not in VALID_HOOKS:
            continue

        def callback(_event: str = name, **payload: Any) -> None:
            observe(_event, payload)

        try:
            register_hook(name, callback)
            registered.append(name)
        except Exception:
            _logger.debug("footer hook not registered: %s", name)
    return tuple(registered)
