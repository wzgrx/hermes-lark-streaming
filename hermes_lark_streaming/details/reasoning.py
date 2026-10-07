"""Requested reasoning control, read from scalar request fields only."""

from __future__ import annotations

from typing import Any

from .values import count, mapping

_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def requested_reasoning(payload: dict[str, Any]) -> str:
    """Never retain the request or thinking text; return a whitelisted scalar or ''."""
    envelope = mapping(payload.get("request"))
    request = mapping(envelope.get("body")) or envelope
    extra = mapping(request.get("extra_body"))
    for source in (request, extra, mapping(request.get("additionalModelRequestFields"))):
        effort = source.get("reasoning_effort") or mapping(source.get("reasoning")).get("effort")
        effort = effort or mapping(source.get("output_config")).get("effort")
        if isinstance(effort, str) and effort.lower() in _EFFORTS:
            return effort.lower()
        thinking = mapping(source.get("thinking"))
        budget = count(thinking.get("budget_tokens"))
        if budget is not None:
            return f"budget:{budget}"
        if thinking.get("type") in {"enabled", "disabled", "adaptive"}:
            return str(thinking["type"])
        generation = mapping(source.get("generationConfig")) or mapping(source.get("generation_config"))
        google = mapping(source.get("google"))
        thinking = (
            mapping(generation.get("thinkingConfig"))
            or mapping(generation.get("thinking_config"))
            or mapping(google.get("thinking_config"))
        )
        level = thinking.get("thinkingLevel", thinking.get("thinking_level"))
        if isinstance(level, str) and level.lower() in {"minimal", "low", "medium", "high"}:
            return level.lower()
        budget = count(thinking.get("thinkingBudget", thinking.get("thinking_budget")))
        if budget is not None:
            return f"budget:{budget}"
        enabled = source.get("enable_thinking", mapping(source.get("chat_template_kwargs")).get("enable_thinking"))
        if isinstance(enabled, bool):
            return "enabled" if enabled else "disabled"
    return ""
