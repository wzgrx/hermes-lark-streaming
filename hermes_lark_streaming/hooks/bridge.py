"""Runtime bridge called by the code injected into Hermes (see ``snippets``).

Every function is exception-safe (never raises into Hermes) and forwards to the session controller, fetched
lazily so importing this module never pulls in the session or transport layers.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Concatenate, ParamSpec, TypeVar

from .tool_result import normalize_tool_completion

_logger = logging.getLogger("hermes_lark_streaming")
P = ParamSpec("P")
R = TypeVar("R")


def _controller() -> Any | None:
    """The active controller, or None when streaming is disabled."""
    from hermes_lark_streaming.session import get_controller  # type: ignore[attr-defined,unused-ignore]

    ctrl = get_controller()
    return ctrl if ctrl is not None and getattr(ctrl, "enabled", True) else None


def _safe(
    default: R, level: int = logging.WARNING
) -> Callable[[Callable[Concatenate[Any, P], R]], Callable[P, R]]:
    def decorator(func: Callable[Concatenate[Any, P], R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                ctrl = _controller()
                return default if ctrl is None else func(ctrl, *args, **kwargs)
            except Exception as exc:
                _logger.log(level, "%s error: %s", func.__name__, exc, exc_info=True)
                return default

        return wrapper

    return decorator


def _safe_async(
    default: R, level: int = logging.WARNING
) -> Callable[[Callable[Concatenate[Any, P], Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def decorator(func: Callable[Concatenate[Any, P], Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                ctrl = _controller()
                return default if ctrl is None else await func(ctrl, *args, **kwargs)
            except Exception as exc:
                _logger.log(level, "%s error: %s", func.__name__, exc, exc_info=True)
                return default

        return wrapper

    return decorator


def _raw_message(event: Any) -> Any:
    raw = getattr(event, "raw_message", None)
    raw_event = raw.get("event") if isinstance(raw, dict) else getattr(raw, "event", None)
    message = None
    if isinstance(raw_event, dict):
        message = raw_event.get("message")
    elif raw_event is not None:
        message = getattr(raw_event, "message", None)
    if message is None and isinstance(raw, dict):
        message = raw.get("message")
    return raw if message is None else message


@_safe(None)
def on_feishu_normalize(
    ctrl: Any, *, message_id: str, source: Any, event: Any, reply_anchor_id: str | None = None
) -> None:
    """Clear the false ``thread_id`` Hermes' Feishu adapter sets on quoted messages (absent in the raw event)."""
    if getattr(getattr(source, "platform", None), "value", "") != "feishu":
        return
    message = _raw_message(event)
    real_thread = message.get("thread_id") if isinstance(message, dict) else getattr(message, "thread_id", None)
    reply_to = getattr(event, "reply_to_message_id", None)
    source_thread = getattr(source, "thread_id", None)
    _logger.info(
        "feishu inbound ids: msg=%s anchor=%s source_thread=%s raw_thread=%s reply_to=%s",
        message_id,
        reply_anchor_id,
        source_thread,
        real_thread,
        reply_to,
    )
    if reply_to and source_thread and not real_thread:
        source.thread_id = None
        event.source = source


@_safe(None)
def on_message_started(
    ctrl: Any,
    *,
    message_id: str,
    chat_id: str,
    anchor_id: str | None = None,
    session_key: str | None = None,
) -> None:
    ctrl.on_message_started(message_id=message_id, chat_id=chat_id, anchor_id=anchor_id, session_key=session_key)


@_safe_async(False)
async def on_message_completed_wait(
    ctrl: Any,
    *,
    message_id: str,
    answer: str = "",
    is_error: bool = False,
    duration: float = 0.0,
    model: str = "",
    tokens: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    deliver_all_media: bool = False,
) -> bool:
    """Await card finalisation; True means the card carries the answer and Hermes must not resend it."""
    return bool(
        await ctrl.on_completed_wait(
            message_id=message_id,
            answer=answer,
            is_error=is_error,
            duration=duration,
            model=model,
            tokens=tokens,
            context=context,
            deliver_all_media=deliver_all_media,
        )
    )


@_safe(False)
def on_message_needs_text_fallback(ctrl: Any, *, message_id: str) -> bool:
    """True once when CardKit failed and the gateway must send plain text."""
    return bool(ctrl.consume_text_fallback(message_id))


@_safe_async(False)
async def on_queued_followup_boundary(
    ctrl: Any, *, message_id: str, result: dict[str, Any], interrupted: bool | None = None
) -> bool:
    """Complete the current card before Hermes drains a queued follow-up turn.

    Hermes decides on the raw result while its delivery result can be a separate dict, so follow the core's
    interrupt decision rather than the normalised dict's flag.
    """
    if not isinstance(result, dict) or (bool(result.get("interrupted")) if interrupted is None else interrupted):
        return False
    sent = bool(
        await ctrl.on_completed_wait(
            message_id=message_id,
            answer=result.get("final_response") or "",
            is_error=bool(result.get("failed")),
            duration=0.0,
            model=result.get("model", ""),
            tokens={
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
            },
            context={
                "used_tokens": result.get("last_prompt_tokens", 0),
                "max_tokens": result.get("context_length", 0),
            },
            # final_response is cleared below, so the gateway cannot scan MEDIA directives: the plugin delivers them.
            deliver_all_media=True,
        )
    )
    if sent:
        result["response_previewed"] = True
        result["already_sent"] = True
        result["final_response"] = ""
    else:
        ctrl.consume_text_fallback(message_id)
    return sent


@_safe(None)
def on_queued_followup_result(ctrl: Any, *, message_id: str, followup_result: dict[str, Any]) -> None:
    """Carry the deepest queued follow-up id back to the outer completion hook."""
    if isinstance(followup_result, dict) and message_id:
        followup_result.setdefault("_hermes_lark_completion_id", message_id)


@_safe(False)
def on_tool_updated(
    ctrl: Any,
    *,
    message_id: str,
    tool_name: str,
    status: str,
    detail: str = "",
    result: Any = None,
    is_error: Any = None,
) -> bool:
    status, detail = normalize_tool_completion(tool_name, status, detail, result=result, is_error=is_error)
    return bool(ctrl.on_tool_update(message_id=message_id, tool_name=tool_name, status=status, detail=detail))


@_safe(False, logging.DEBUG)
def on_answer_delta(ctrl: Any, *, message_id: str, text: str) -> bool:
    return bool(ctrl.on_answer(message_id=message_id, text=text))


@_safe(False, logging.DEBUG)
def on_thinking_delta(ctrl: Any, *, message_id: str, text: str) -> bool:
    return bool(ctrl.on_thinking(message_id=message_id, text=text))


@_safe(False, logging.DEBUG)
def on_reasoning_delta(ctrl: Any, *, message_id: str, text: str) -> bool:
    return bool(ctrl.on_reasoning(message_id=message_id, text=text))


@_safe(False, logging.DEBUG)
def on_background_review_message(ctrl: Any, *, message_id: str, text: str, sender: Callable[[str], Any]) -> bool:
    return bool(ctrl.defer_background_review(message_id=message_id, text=text, sender=sender))


@_safe(None)
def on_message_aborted(ctrl: Any, *, message_id: str) -> None:
    ctrl.on_aborted(message_id=message_id)


@_safe_async(False)
async def on_session_aborted(ctrl: Any, *, session_key: str) -> bool:
    """Terminate the active card after Hermes handles a busy-session /stop."""
    return bool(await ctrl.on_session_aborted(session_key=session_key))


@_safe(None)
def on_message_interrupted(
    ctrl: Any,
    *,
    message_id: str,
    new_message_id: str,
    chat_id: str,
    anchor_id: str | None = None,
    session_key: str | None = None,
) -> None:
    ctrl.on_interrupted(
        old_message_id=message_id,
        new_message_id=new_message_id,
        chat_id=chat_id,
        anchor_id=anchor_id,
        session_key=session_key,
    )


_NO_RECEIPT: dict[str, object] | bool = False


@_safe(_NO_RECEIPT)
def on_cron_deliver(
    ctrl: Any,
    *,
    chat_id: str,
    content: str,
    loop: Any = None,
    task_name: str = "",
    run_time: str = "",
    job_id: str = "",
    media_files: object = None,
) -> dict[str, object] | bool:
    """Send a cron result as a card; returns a receipt dict (with ``message_id``) on verified delivery.

    ``media_files`` (``[(path, is_voice), ...]``) is forwarded because Hermes strips MEDIA tags before this
    hook and skips its own attachment delivery when the hook returns truthy.
    """
    result: dict[str, object] | bool = ctrl.on_cron_deliver(
        chat_id=chat_id,
        content=content,
        loop=loop,
        task_name=task_name,
        run_time=run_time,
        job_id=job_id,
        media_files=media_files,
    )
    return result


@_safe_async(False)
async def on_background_deliver(
    ctrl: Any, *, chat_id: str, preview: str, content: str, reply_to_message_id: str | None = None
) -> bool:
    return bool(
        await ctrl.on_background_deliver(
            chat_id=chat_id, preview=preview, content=content, reply_to_message_id=reply_to_message_id
        )
    )


@_safe(None)
def on_approval_enter(ctrl: Any, *, message_id: str) -> None:
    """Pause CardKit before Hermes sends an actionable native approval card."""
    ctrl.on_approval_enter(message_id=message_id)


@_safe(None)
def on_clarify_enter(
    ctrl: Any, *, message_id: str, chat_id: str | None = None, session_key: str | None = None
) -> None:
    ctrl.on_clarify_enter(message_id=message_id, chat_id=chat_id, session_key=session_key)


@_safe(None)
def on_clarify_exit(
    ctrl: Any, *, message_id: str, chat_id: str | None = None, session_key: str | None = None
) -> None:
    ctrl.on_clarify_exit(message_id=message_id, chat_id=chat_id, session_key=session_key)
