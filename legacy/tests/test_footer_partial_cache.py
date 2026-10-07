"""Keep positive cache evidence even when another canonical bucket is unknown."""

import json

import pytest

from hermes_lark_streaming.cardkit.reference import build_reference_footer
from hermes_lark_streaming.footer.render import build_footer
from hermes_lark_streaming.footer.state import TurnFooter


def turn(caches):
    state = TurnFooter()
    for i, cache in enumerate(caches):
        event = dict(platform="feishu", session_id="s", turn_id="t", api_request_id=str(i),
                     started_at=state.created_at + i + 1)
        assert state.observe("pre_api_request", event)
        usage = {"prompt_tokens": 100, "output_tokens": 1}
        if cache is not None:
            usage["cache_read_tokens"] = cache
        assert state.observe("post_api_request", {**event, "usage": usage})
    return state.finish()


@pytest.mark.parametrize("unknown", [0, None, -1, True, 101])
@pytest.mark.parametrize("reverse", [False, True])
def test_positive_cache_survives_a_zero_filled_missing_or_invalid_peer(unknown, reverse):
    data = turn([40, unknown] if reverse else [unknown, 40])
    assert data["input_tokens"] == 200 and data["output_tokens"] == 2
    assert data["usage_partial"] is False
    assert data["cache_read_tokens"] == 40
    assert data["cache_read_partial"] is True
    row = next(el for el in build_reference_footer(data)[0]["elements"]
               if el.get("tag") == "column_set" and "Cache read" in json.dumps(el))
    assert row["columns"][0]["elements"][1]["content"] == "**≥40 / ≥20.0%**"
    assert "partial" in row["columns"][0]["elements"][0]["content"].lower()
    regular = json.dumps(build_footer(data), ensure_ascii=False)
    assert "≥40" in regular and "≥20.0%" in regular and "Cache ≥20%" in regular


def test_all_positive_cache_remains_a_complete_rate():
    data = turn([20, 40])
    assert data["cache_read_tokens"] == 60
    assert not data.get("cache_read_partial")
    assert "60 / 30.0%" in json.dumps(build_reference_footer(data), ensure_ascii=False)


@pytest.mark.parametrize("caches", [[0, 0], [None, None], [0, None]])
def test_all_unknown_cache_does_not_invent_zero(caches):
    data = turn(caches)
    assert "cache_read_tokens" not in data
    assert not data.get("cache_read_partial")


def test_failed_request_preserves_partial_positive_cache_without_a_rate():
    state = TurnFooter()
    first = dict(platform="feishu", session_id="s", turn_id="t", api_request_id="1",
                 started_at=state.created_at + 1)
    assert state.observe("pre_api_request", first)
    assert state.observe("api_request_error", first)
    second = {**first, "api_request_id": "2", "started_at": state.created_at + 2}
    assert state.observe("pre_api_request", second)
    assert state.observe("post_api_request", {**second, "usage": {
        "prompt_tokens": 100, "output_tokens": 1, "cache_read_tokens": 40}})
    data = state.finish()
    assert data["usage_partial"] and data["cache_read_partial"]
    assert data["cache_read_tokens"] == 40 and data["retries"] == 1
    assert "≥40 / —" in json.dumps(build_reference_footer(data), ensure_ascii=False)
