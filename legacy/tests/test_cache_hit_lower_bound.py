import json

import pytest

from hermes_lark_streaming.cardkit.reference import build_reference_footer
from hermes_lark_streaming.footer.render import build_footer
from hermes_lark_streaming.footer.usage import cache_hit


@pytest.mark.parametrize(("prompt", "read", "expected"), [
    (149120, 99072, "≥66.4%"), (3, 2, "≥66.6%"), (200, 40, "≥20.0%"),
    (100, 0, "≥0.0%"), (100, 100, "≥100.0%"), (10**15, 10**15-1, "≥99.9%"),
])
def test_lower_bounds_never_round_up(prompt, read, expected):
    data = {"input_tokens": prompt, "cache_read_tokens": read, "cache_read_partial": True}
    assert cache_hit(data) == expected
    assert expected in json.dumps(build_reference_footer(data), ensure_ascii=False)
    assert expected in json.dumps(build_footer(data), ensure_ascii=False)


@pytest.mark.parametrize(("prompt", "read", "partial"), [
    (0, 0, False), (None, 40, False), (100, 101, False), (100, 40, True),
    (True, 0, False), (100, None, False), (100, -1, False),
])
def test_unknown_denominator_or_invalid_counts_stay_unknown(prompt, read, partial):
    assert cache_hit({"input_tokens": prompt, "cache_read_tokens": read,
                      "cache_read_partial": True, "usage_partial": partial}) == ""


def test_complete_rate_keeps_existing_rounding_and_labels():
    data = {"input_tokens": 3, "cache_read_tokens": 2}
    assert cache_hit(data) == "66.7%"
    assert "lower bound" not in json.dumps(build_reference_footer(data))
