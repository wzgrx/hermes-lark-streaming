"""Which billing product served a request, from Hermes' provider id and the request's base URL.

Hermes reports a custom OpenAI-compatible provider as plain ``custom`` on main-agent calls and as
``custom:<name>`` on auxiliary calls, so the same subscription showed up under two names and never
matched an account. The base URL is the reliable signal: a known gateway host names the product.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# (host, path prefix) -> product id. Reviewed gateways only; anything else keeps Hermes' own id.
_GATEWAYS: tuple[tuple[str, str, str], ...] = (
    ("amr-link.open-design.ai", "", "opendesign"),
    ("opencode.ai", "/zen/go", "opencode-go"),
)

# Short names for the footer's route chip.
SHORT_NAMES = {
    "opendesign": "OpenDesign",
    "opencode-go": "Go",
    "deepseek": "DeepSeek",
    "openrouter": "OpenRouter",
    "siliconflow": "SiliconFlow",
}


def canonical_provider(provider: Any, base_url: Any) -> str:
    """The product id for accounts, usage history and the footer; ``""`` when nothing is known."""
    if isinstance(base_url, str) and base_url:
        try:
            parsed = urlparse(base_url)
        except ValueError:
            parsed = None
        if parsed is not None:
            host, path = (parsed.hostname or "").lower(), parsed.path or ""
            for gateway, prefix, product in _GATEWAYS:
                if host == gateway and path.startswith(prefix):
                    return product
    if not isinstance(provider, str):
        return ""
    name = provider.strip().lower()
    return name.split(":", 1)[1] if name.startswith("custom:") and len(name) > 7 else name


def with_canonical_provider(payload: dict[str, Any]) -> dict[str, Any]:
    """A copy of a hook payload whose ``provider`` is the product id (the original is left untouched)."""
    product = canonical_provider(payload.get("provider"), payload.get("base_url"))
    if not product or product == payload.get("provider"):
        return payload
    return {**payload, "provider": product}


def short_name(product: str) -> str:
    return SHORT_NAMES.get(product, product)
