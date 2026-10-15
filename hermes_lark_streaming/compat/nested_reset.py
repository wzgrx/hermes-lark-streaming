"""OpenDesign (the AMR gateway) names its quota reset under ``error.details.reset_at``.

Hermes reads ``reset_at`` only at the top of the error body, so an exhausted OpenDesign plan looked like a
reset-less 402: the credential was benched for the default hour and the primary cooldown fell back to the
60 s → 4 h exponential walk, retrying a plan that would not reopen for hours. With the nested value lifted
into the error context, both wait exactly until the provider's reset.
"""

from __future__ import annotations

import functools
from typing import Any

_MARK = "_hermes_lark_nested_reset"


def nested_reset(error: Any) -> Any:
    """``error.details.reset_at`` (or ``details.resets_at``) of an OpenAI-SDK-shaped error, else ``None``."""
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    payload = body.get("error") if isinstance(body.get("error"), dict) else body
    details = payload.get("details") if isinstance(payload, dict) else None
    if not isinstance(details, dict):
        return None
    value = next((details.get(k) for k in ("reset_at", "resets_at") if details.get(k) not in (None, "")), None)
    return value if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def install() -> bool:
    import agent.agent_runtime_helpers as helpers  # type: ignore[import-not-found]

    original = helpers.extract_api_error_context
    if getattr(original, _MARK, False):
        return True

    @functools.wraps(original)
    def extract_api_error_context(error: Exception) -> dict[str, Any]:
        context = original(error)
        if isinstance(context, dict) and "reset_at" not in context:
            reset = nested_reset(error)
            if reset is not None:
                context["reset_at"] = reset
        return context  # type: ignore[no-any-return]

    setattr(extract_api_error_context, _MARK, True)
    helpers.extract_api_error_context = extract_api_error_context  # callers resolve it per call (lazy_forward)
    return True
