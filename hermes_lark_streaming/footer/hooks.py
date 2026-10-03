"""Read-only observers: bind to a real Gateway session key, never own delivery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import copy_context
from typing import Any

HOOKS = ("pre_api_request", "post_api_request", "api_request_error", "post_auxiliary_call")
_logger = logging.getLogger("hermes_lark_streaming")


def observe(event: str, payload: dict[str, Any]) -> None:
    from .history import observe_history

    observe_history(event, payload)
    _observe_bound(event, payload)


def _observe_bound(event: str, payload: dict[str, Any]) -> None:
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
            accepted = (
                session.footer_state.observe_execution(payload)
                if event == "llm_execution" else session.footer_state.observe(event, payload)
            )
            metrics.increment("footer.event.accepted" if accepted else "footer.event.rejected")
        else:
            metrics.increment("footer.skip.session")
    except Exception:
        # Avoid logging provider payloads, URLs or exceptions which could contain secrets.
        _logger.debug("footer observer skipped an incompatible event")


def execution_observer(*, next_call: Callable[[], Any], **payload: Any) -> Any:
    """A transparent middleware frame; provider execution remains single-use.

    Do not pass this raw request to history/logging or catch next_call failures.
    Later middleware may still rewrite controls: this is requested effort at
    this stage, not an assertion that the server accepted that effort.
    """
    try:
        _observe_bound("llm_execution", payload)
    except Exception:
        _logger.debug("footer execution metadata skipped")
    return next_call()


def register_execution(context: object) -> bool:
    register_middleware = getattr(context, "register_middleware", None)
    if not callable(register_middleware):
        return False
    try:
        from hermes_cli.middleware import VALID_MIDDLEWARE  # type: ignore[import-not-found]

        if "llm_execution" not in VALID_MIDDLEWARE:
            return False
        register_middleware("llm_execution", execution_observer)
        return True
    except Exception:
        _logger.debug("footer execution observer not registered")
        return False


def register(context: object) -> tuple[str, ...]:
    register_execution(context)
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
