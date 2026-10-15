"""OpenCode Go error shapes Hermes misreads.

* An account-plan wall comes as HTTP 403 instead of 402/429. It belongs to one credential, so another
  account in the same pool may be healthy: rotate first, then fall back.
* The model vendor's momentary 429 comes relayed as ``Upstream request failed``: nothing is wrong with the
  key, so retry it with backoff rather than benching it for an hour.
"""

from __future__ import annotations

from typing import Any

PROVIDER = "opencode-go"
_PLAN_REQUIRED = "active opencode go subscription is required"
HOOK = "transform_api_error_classification"


_UPSTREAM_THROTTLE = "upstream request failed"


def classify(**payload: Any) -> dict[str, Any] | None:
    if str(payload.get("provider") or "").strip().lower() != PROVIDER:
        return None
    if payload.get("status_code") == 429:
        return _upstream_throttle(payload)
    if payload.get("status_code") != 403:
        return None
    text = " ".join(str(payload.get(key) or "") for key in ("error_message", "error_code")).lower()
    if _PLAN_REQUIRED not in text:
        return None
    return {
        "reason": "billing",
        "retryable": False,
        "should_rotate_credential": True,
        "should_fallback": True,
        "message": "OpenCode Go subscription is not active for this credential",
    }


def _upstream_throttle(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Go relays the model vendor's own 429 as ``Upstream request failed: Rate limit exceeded``. The key and
    its plan are fine (the plan wall is ``GoUsageLimitError`` with a reset), yet Hermes benched the key for an
    hour and moved the chat to the fallback. Back off and retry on the same key instead."""
    text = " ".join(str(payload.get(key) or "") for key in ("error_message", "error_type", "error_code")).lower()
    if _UPSTREAM_THROTTLE not in text or "gousagelimit" in text:
        return None
    return {
        "reason": "overloaded",
        "retryable": True,
        "should_rotate_credential": False,
        "message": "OpenCode Go upstream is throttling; retrying on the same key",
    }


def register(ctx: object) -> bool:
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        return False
    try:
        from hermes_cli.plugins import VALID_HOOKS  # type: ignore[import-not-found]
    except ImportError:
        return False
    if HOOK not in VALID_HOOKS:
        return False
    register_hook(HOOK, classify)
    return True
