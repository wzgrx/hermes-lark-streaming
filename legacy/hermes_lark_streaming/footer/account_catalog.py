"""Versioned provider inventory and reviewed management-plane capabilities."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

# Keys and products are intentionally separate from the inference-provider IDs.
ENDPOINTS = {
    "opencode-go": "https://opencode.ai/zen/go/v1/usage",
    "deepseek": "https://api.deepseek.com/user/balance",
    "openrouter": "https://openrouter.ai/api/v1/key",
    "openrouter-credits": "https://openrouter.ai/api/v1/credits",
    "moonshot": "https://api.moonshot.cn/v1/users/me/balance",
    "moonshot-global": "https://api.moonshot.ai/v1/users/me/balance",
    "minimax": "https://api.minimax.io/v1/token_plan/remains",
    "minimax-cn": "https://api.minimax.cn/v1/token_plan/remains",
    "zai": "https://api.z.ai/api/monitor/usage/quota/limit",
    "bigmodel": "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
}
PREFIXES = {
    "opencode-go": "OPENCODE_GO_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openrouter-credits": "OPENROUTER_MANAGEMENT_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "moonshot-global": "MOONSHOT_GLOBAL_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_CN_API_KEY",
    "zai": "ZAI_API_KEY",
    "bigmodel": "ZHIPU_API_KEY",
}
CAPABILITIES = {
    "opencode-go": ("quota",),
    "deepseek": ("balance",),
    "openrouter": ("key_limit", "key_expiry"),
    "openrouter-credits": ("wallet",),
    "moonshot": ("balance",),
    "moonshot-global": ("balance",),
    "minimax": ("quota_or_balance_by_key_type",),
    "minimax-cn": ("quota_or_balance_by_key_type",),
    "zai": ("quota", "personal_subscription"),
    "bigmodel": ("quota", "personal_subscription"),
}
SOURCES = {
    "opencode-go": "https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/go/v1/usage.ts",
    "deepseek": "https://api-docs.deepseek.com/api/get-user-balance/",
    "openrouter": "https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key",
    "openrouter-credits": "https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits",
    "moonshot": "https://platform.kimi.com/docs/api/balance",
    "moonshot-global": "https://platform.kimi.ai/docs/api/balance",
    "minimax": "https://github.com/MiniMax-AI/cli/blob/06e47c70b76f419196678367dae62acca4c94076/src/client/endpoints.ts",
    "minimax-cn": "https://github.com/MiniMax-AI/cli/blob/06e47c70b76f419196678367dae62acca4c94076/src/config/schema.ts",
    "zai": "https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/services/src/usage-stats/providers/bigmodelSubscriptionProvider.ts",
    "bigmodel": "https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/services/src/usage-stats/providers/bigmodelUsageQuotaMapper.ts",
}


# Preserve documented deprecation instead of treating old specifications as
# proof of a working account endpoint. No retired URL is registered for GET.
UNAVAILABLE = {
    "siliconflow": {
        "reason": "endpoint_retired",
        "retired_on": "2026-08-14",
        "source_url": "https://docs.siliconflow.cn/docs/release-notes/overview",
    },
}


@lru_cache(maxsize=1)
def inventory() -> dict[str, Any]:
    value = json.loads(files(__package__).joinpath("account_catalog.json").read_text(encoding="utf-8"))
    return dict(value)


def catalog() -> dict[str, Any]:
    base = inventory()
    rows = {
        r["id"]: {
            **r,
            **UNAVAILABLE.get(r["id"], {}),
            "adapter": "not_implemented",
            "capabilities": [],
            "credential_plane": "provider-specific; no speculative network probes",
        }
        for r in base["providers"]
    }
    for ident, endpoint in ENDPOINTS.items():
        rows[ident] = {
            **rows.get(ident, {"id": ident, "name": ident}),
            "adapter": "implemented",
            "capabilities": list(CAPABILITIES[ident]),
            "endpoint": endpoint,
            "env": [PREFIXES[ident]],
            "credential_plane": "management_key" if ident == "openrouter-credits" else "provider_api_key",
            "source": SOURCES[ident],
            "live_account_verified": False,
        }
    return {
        "inventory_source": base["source"],
        "inventory_retrieved_at": base["retrieved_at"],
        "inventory_entries": base["count"],
        "products": list(rows.values()),
        "implemented_products": len(ENDPOINTS),
        "all_providers_implemented": False,
        "subscription_expiry_is_not_quota_reset": True,
    }
