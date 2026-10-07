"""Offline protocol fixtures for usage normalisation and the cache-hit lower bound; no paid APIs."""

from __future__ import annotations

import pytest

from hermes_lark_streaming.details.usage import cache_ratio, normalize_usage

HERMES = {"input_tokens": 20, "cache_read_tokens": 70, "cache_write_tokens": 10, "output_tokens": 7}


@pytest.mark.parametrize(
    ("protocol", "usage", "prompt", "output", "cache"),
    [
        ("hermes", HERMES, 100, 7, 70),
        (
            "chat_completions",
            {"prompt_tokens": 100, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 70}},
            100,
            7,
            70,
        ),
        ("chat_completions", {"prompt_tokens": 100, "completion_tokens": 7, "prompt_cache_hit_tokens": 70}, 100, 7, 70),
        (
            "responses",
            {"input_tokens": 100, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 70}},
            100,
            7,
            70,
        ),
        (
            "anthropic_messages",
            {"input_tokens": 20, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 10, "output_tokens": 7},
            100,
            7,
            70,
        ),
        (
            "bedrock_converse",
            {"inputTokens": 20, "cacheReadInputTokens": 70, "cacheWriteInputTokens": 10, "outputTokens": 7},
            100,
            7,
            70,
        ),
        (
            "gemini",
            {"promptTokenCount": 100, "candidatesTokenCount": 5, "thoughtsTokenCount": 2, "cachedContentTokenCount": 70},
            100,
            7,
            70,
        ),
        ("ollama", {"prompt_eval_count": 100, "eval_count": 7}, 100, 7, None),
        ("chat_completions", {"prompt_tokens": 100, "completion_tokens": 7}, 100, 7, None),
        ("unknown", {"input_tokens": 100, "output_tokens": 7}, None, None, None),
    ],
)
def test_protocol_usage(protocol, usage, prompt, output, cache):
    actual = normalize_usage(usage, protocol)
    assert (actual.prompt, actual.output, actual.cache_read) == (prompt, output, cache)


@pytest.mark.parametrize("bad", [None, [], "123", True, -1, 1.2, float("nan"), 10**20])
def test_bad_usage_is_unknown(bad):
    assert normalize_usage({"prompt_tokens": bad}, "chat_completions").prompt is None


def test_no_double_count_and_zero_is_not_missing():
    a = normalize_usage({"input_tokens": 30, "prompt_tokens": 100, "cache_read_tokens": 70}, "hermes")
    assert a.prompt == 100
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}, "chat_completions").prompt == 0
    assert normalize_usage({"prompt_tokens": 5, "prompt_cache_hit_tokens": 100}, "chat_completions").cache_read is None


@pytest.mark.parametrize("protocol", ["cohere_v2", "cohere_chat"])
def test_cohere_physical_tokens_not_billable_units(protocol):
    usage = normalize_usage(
        {
            "tokens": {"input_tokens": 7596, "output_tokens": 645},
            "billed_units": {"input_tokens": 6772, "output_tokens": 248},
        },
        protocol,
    )
    assert (usage.prompt, usage.output) == (7596, 645)
    assert usage.cache_read is None and usage.cache_write is None and usage.reasoning is None


def test_cohere_sdk_integral_floats_and_cache_subset():
    usage = normalize_usage({"tokens": {"input_tokens": 71.0, "output_tokens": 418.0}, "cached_tokens": 25.0}, "cohere_v2")
    assert (usage.prompt, usage.output, usage.cache_read) == (71, 418, 25)
    assert type(usage.prompt) is int and type(usage.output) is int and type(usage.cache_read) is int


@pytest.mark.parametrize("tokens", [None, {}, [], "71", {"input_tokens": None}])
def test_cohere_missing_tokens_do_not_fall_back_to_billable_counts(tokens):
    usage = normalize_usage(
        {"tokens": tokens, "billed_units": {"input_tokens": 100, "output_tokens": 30}, "cached_tokens": 10}, "cohere_v2"
    )
    assert usage.prompt is None and usage.output is None and usage.cache_read is None


@pytest.mark.parametrize(
    "bad", [True, False, "71", -1, -1.0, 1.5, float("nan"), float("inf"), float("-inf"), 10**15 + 1, 1e16]
)
def test_cohere_invalid_counts_are_unknown_without_rounding(bad):
    usage = normalize_usage({"tokens": {"input_tokens": bad, "output_tokens": bad}, "cached_tokens": bad}, "cohere_v2")
    assert usage.prompt is None and usage.output is None and usage.cache_read is None


def test_cohere_zero_partial_and_impossible_cache():
    zero = normalize_usage({"tokens": {"input_tokens": 0.0, "output_tokens": 0}, "cached_tokens": 0.0}, "cohere_v2")
    assert (zero.prompt, zero.output, zero.cache_read) == (0, 0, 0)
    partial = normalize_usage({"tokens": {"input_tokens": 10}}, "cohere_v2")
    assert partial.prompt == 10 and partial.output is None and partial.cache_read is None
    over = normalize_usage({"tokens": {"input_tokens": 5, "output_tokens": 2}, "cached_tokens": 6}, "cohere_v2")
    assert over.prompt == 5 and over.cache_read is None


def test_cohere_requires_explicit_protocol_and_usage_object():
    usage = {"tokens": {"input_tokens": 71, "output_tokens": 418}}
    assert normalize_usage(usage).prompt is None
    assert normalize_usage(usage, "hermes").prompt is None
    assert normalize_usage({"usage": usage}, "cohere_v2").prompt is None
    assert normalize_usage({"prompt_tokens": 71.0}, "hermes").prompt is None


@pytest.mark.parametrize(
    ("prompt", "read", "expected"),
    [(149120, 99072, 0.664), (3, 2, 0.666), (200, 40, 0.2), (100, 0, 0.0), (100, 100, 1.0), (10**15, 10**15 - 1, 0.999)],
)
def test_lower_bounds_never_round_up(prompt, read, expected):
    ratio, floor = cache_ratio({"input_tokens": prompt, "cache_read_tokens": read, "cache_read_partial": True})
    assert floor is True and ratio == pytest.approx(expected, abs=1e-12)
    assert ratio is not None and ratio <= read / prompt


@pytest.mark.parametrize(
    ("prompt", "read", "partial"),
    [(0, 0, False), (None, 40, False), (100, 101, False), (100, 40, True), (True, 0, False), (100, None, False), (100, -1, False)],
)
def test_unknown_denominator_or_invalid_counts_stay_unknown(prompt, read, partial):
    data = {"input_tokens": prompt, "cache_read_tokens": read, "cache_read_partial": True, "usage_partial": partial}
    assert cache_ratio(data) == (None, False)


def test_complete_rate_is_exact_and_not_a_floor():
    ratio, floor = cache_ratio({"input_tokens": 3, "cache_read_tokens": 2})
    assert ratio == pytest.approx(2 / 3) and floor is False
