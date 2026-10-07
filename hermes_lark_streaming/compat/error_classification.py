"""OpenCode Go reports an account-plan wall as HTTP 403 instead of 402/429.

That failure belongs to one credential, so another account in the same pool may be healthy: rotate first,
then fall back, rather than treating it as a generic forbidden response.
"""

from __future__ import annotations

from typing import Any

PROVIDER = "opencode-go"
_PLAN_REQUIRED = "active opencode go subscription is required"
HOOK = "transform_api_error_classification"


def classify(**payload: Any) -> dict[str, Any] | None:
    if str(payload.get("provider") or "").strip().lower() != PROVIDER or payload.get("status_code") != 403:
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
