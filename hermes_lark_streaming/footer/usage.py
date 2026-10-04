"""Usage adapters. Never infer a wire protocol from a provider/model name.

All input totals include cached tokens. Unknown is None, not zero. These adapters
consume completed usage objects, not partial streaming deltas or provider APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**15 else None


def _cohere_count(value: Any) -> int | None:
    # Cohere's SDK declares these counters as Optional[float]. Accept exact
    # integral representations only, never round or relax other protocols.
    if type(value) is float and 0 <= value <= 10**15 and value.is_integer():
        return int(value)
    return count(value)


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Usage:
    prompt: int | None = None
    output: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    reasoning: int | None = None
    source: str = "unknown"


def normalize_usage(value: Any, protocol: str = "unknown") -> Usage:
    """Map documented final-response schemas; unrecognized schemas stay unknown."""
    u = mapping(value)
    if not u:
        return Usage()
    prompt = output = read = write = reasoning = None
    if protocol == "hermes":
        prompt = count(u.get("prompt_tokens"))
        read, write = count(u.get("cache_read_tokens")), count(u.get("cache_write_tokens"))
        if prompt is None and count(u.get("input_tokens")) is not None:
            prompt = u["input_tokens"] + (read or 0) + (write or 0)
        output, reasoning = count(u.get("output_tokens")), count(u.get("reasoning_tokens"))
    elif protocol in {"chat_completions", "openai_chat"}:
        prompt, output = count(u.get("prompt_tokens")), count(u.get("completion_tokens"))
        read = count(mapping(u.get("prompt_tokens_details")).get("cached_tokens"))
        if read is None:
            read = count(u.get("prompt_cache_hit_tokens"))
        reasoning = count(mapping(u.get("completion_tokens_details")).get("reasoning_tokens"))
    elif protocol in {"codex_responses", "responses"}:
        prompt, output = count(u.get("input_tokens")), count(u.get("output_tokens"))
        read = count(mapping(u.get("input_tokens_details")).get("cached_tokens"))
        reasoning = count(mapping(u.get("output_tokens_details")).get("reasoning_tokens"))
    elif protocol in {"anthropic_messages", "anthropic"}:
        prompt, output = count(u.get("input_tokens")), count(u.get("output_tokens"))
        read, write = count(u.get("cache_read_input_tokens")), count(u.get("cache_creation_input_tokens"))
        if prompt is not None:
            prompt += (read or 0) + (write or 0)
    elif protocol in {"gemini", "generate_content"}:
        prompt = count(u.get("promptTokenCount", u.get("prompt_token_count")))
        output = count(u.get("candidatesTokenCount", u.get("candidates_token_count")))
        reasoning = count(u.get("thoughtsTokenCount", u.get("thoughts_token_count")))
        if output is not None:
            output += reasoning or 0
        read = count(u.get("cachedContentTokenCount", u.get("cached_content_token_count")))
    elif protocol == "bedrock_converse":
        prompt, output = count(u.get("inputTokens")), count(u.get("outputTokens"))
        read, write = count(u.get("cacheReadInputTokens")), count(u.get("cacheWriteInputTokens"))
        if prompt is not None:
            prompt += (read or 0) + (write or 0)
    elif protocol == "ollama":
        prompt, output = count(u.get("prompt_eval_count")), count(u.get("eval_count"))
    elif protocol in {"cohere_v2", "cohere_chat"}:
        # V2 completed response.usage (or completed message-end delta.usage).
        # billed_units excludes provider-added tokens and is not context usage.
        tokens = mapping(u.get("tokens"))
        prompt = _cohere_count(tokens.get("input_tokens"))
        output = _cohere_count(tokens.get("output_tokens"))
        read = _cohere_count(u.get("cached_tokens"))  # subset of input, not extra
    # Inconsistent cache claims must not produce misleading percentages.
    if prompt is None or (read or 0) + (write or 0) > prompt:
        read = write = None
    return Usage(prompt, output, read, write, reasoning, protocol)
