from __future__ import annotations

import pytest

from hermes_lark_streaming.details.providers import canonical_provider, short_name, with_canonical_provider


@pytest.mark.parametrize(
    ("provider", "base_url", "expected"),
    [
        ("custom", "https://amr-link.open-design.ai/v1", "opendesign"),  # Hermes' main-agent name for it
        ("custom:opendesign", "https://amr-link.open-design.ai/v1", "opendesign"),  # auxiliary calls
        ("custom:opendesign", None, "opendesign"),
        ("opencode-go", "https://opencode.ai/zen/go/v1", "opencode-go"),
        ("custom", "https://opencode.ai/zen/go/v1", "opencode-go"),
        ("custom:local-qwen38", "http://127.0.0.1:8080/v1", "local-qwen38"),
        ("deepseek", "https://api.deepseek.com", "deepseek"),
        ("custom", "https://evil.example/amr-link.open-design.ai/v1", "custom"),  # the host decides, not the path
        (None, None, ""),
    ],
)
def test_one_product_id_per_subscription(provider, base_url, expected):
    assert canonical_provider(provider, base_url) == expected


def test_payload_copy_keeps_the_original():
    raw = {"provider": "custom", "base_url": "https://amr-link.open-design.ai/v1", "model": "m"}
    assert with_canonical_provider(raw)["provider"] == "opendesign" and raw["provider"] == "custom"
    assert short_name("opendesign") == "OpenDesign" and short_name("opencode-go") == "Go"
