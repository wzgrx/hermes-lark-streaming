"""Cohere V2 completed usage: physical tokens are not billable units.

Sources are pinned in docs/PROVIDER-COVERAGE.md. These are offline protocol
fixtures, not successful requests to a Cohere account or a new Hermes transport.
"""

import pytest

from hermes_lark_streaming.footer.usage import normalize_usage


@pytest.mark.parametrize("protocol", ["cohere_v2", "cohere_chat"])
def test_cohere_physical_tokens_not_billable_units(protocol):
    usage = normalize_usage({
        "tokens": {"input_tokens": 7596, "output_tokens": 645},
        "billed_units": {"input_tokens": 6772, "output_tokens": 248},
    }, protocol)
    assert (usage.prompt, usage.output) == (7596, 645)
    assert usage.cache_read is None and usage.cache_write is None
    assert usage.reasoning is None  # Do not derive thinking from billed differences.


def test_cohere_sdk_integral_floats_and_cache_subset():
    usage = normalize_usage({
        "tokens": {"input_tokens": 71.0, "output_tokens": 418.0},
        "cached_tokens": 25.0,
    }, "cohere_v2")
    assert (usage.prompt, usage.output, usage.cache_read) == (71, 418, 25)
    assert type(usage.prompt) is int and type(usage.output) is int
    assert type(usage.cache_read) is int


@pytest.mark.parametrize("tokens", [None, {}, [], "71", {"input_tokens": None}])
def test_cohere_missing_tokens_do_not_fall_back_to_billable_counts(tokens):
    usage = normalize_usage({
        "tokens": tokens,
        "billed_units": {"input_tokens": 100, "output_tokens": 30},
        "cached_tokens": 10,
    }, "cohere_v2")
    assert usage.prompt is None and usage.output is None and usage.cache_read is None


@pytest.mark.parametrize("bad", [True, False, "71", -1, -1.0, 1.5, float("nan"),
                                 float("inf"), float("-inf"), 10**15 + 1, 1e16])
def test_cohere_invalid_counts_are_unknown_without_rounding(bad):
    usage = normalize_usage({
        "tokens": {"input_tokens": bad, "output_tokens": bad}, "cached_tokens": bad,
    }, "cohere_v2")
    assert usage.prompt is None and usage.output is None and usage.cache_read is None


def test_cohere_zero_and_partial_usage_remain_distinct():
    zero = normalize_usage({
        "tokens": {"input_tokens": 0.0, "output_tokens": 0}, "cached_tokens": 0.0,
    }, "cohere_v2")
    assert (zero.prompt, zero.output, zero.cache_read) == (0, 0, 0)
    partial = normalize_usage({"tokens": {"input_tokens": 10}}, "cohere_v2")
    assert partial.prompt == 10 and partial.output is None and partial.cache_read is None


def test_cohere_impossible_cache_does_not_inflate_prompt():
    usage = normalize_usage({
        "tokens": {"input_tokens": 5, "output_tokens": 2}, "cached_tokens": 6,
    }, "cohere_v2")
    assert usage.prompt == 5 and usage.cache_read is None


def test_cohere_requires_explicit_protocol_and_usage_object():
    usage = {"tokens": {"input_tokens": 71, "output_tokens": 418}}
    assert normalize_usage(usage).prompt is None
    assert normalize_usage(usage, "hermes").prompt is None
    assert normalize_usage({"usage": usage}, "cohere_v2").prompt is None
    # No global relaxation of the Hermes canonical integer contract.
    assert normalize_usage({"prompt_tokens": 71.0}, "hermes").prompt is None
