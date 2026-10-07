"""Multi-bot routing without placing credentials in configuration or diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import DEFAULT_DOMAIN, _get_secret


@dataclass(frozen=True)
class BotConfig:
    bot_id: str
    app_id_env: str
    app_secret_env: str
    base_url: str = DEFAULT_DOMAIN

    def credentials(self) -> tuple[str, str]:
        return _get_secret(self.app_id_env), _get_secret(self.app_secret_env)

    def diagnostic(self) -> dict[str, Any]:
        app_id, secret = self.credentials()
        return {
            "bot_id": self.bot_id,
            "app_id_env": self.app_id_env,
            "app_secret_env": self.app_secret_env,
            "configured": bool(app_id and secret),
            "base_url": self.base_url,
        }


class BotRegistry:
    """Resolve an exact chat binding to a configured bot, otherwise use default."""

    def __init__(
        self, bots: dict[str, BotConfig], *, default: str = "", chat_bindings: dict[str, str] | None = None
    ) -> None:
        self._bots = dict(bots)
        self.default = default if default in bots else (next(iter(bots), ""))
        self._chat_bindings = dict(chat_bindings or {})

    @classmethod
    def from_streaming_config(cls, streaming: dict[str, Any]) -> BotRegistry:
        raw = streaming.get("bots")
        if not isinstance(raw, dict):
            return cls({})
        items = raw.get("items", {})
        bots: dict[str, BotConfig] = {}
        if isinstance(items, dict):
            for bot_id, value in items.items():
                if not isinstance(value, dict):
                    continue
                app_id_env = str(value.get("app_id_env", "")).strip()
                app_secret_env = str(value.get("app_secret_env", "")).strip()
                if not app_id_env or not app_secret_env:
                    continue
                bots[str(bot_id)] = BotConfig(
                    bot_id=str(bot_id),
                    app_id_env=app_id_env,
                    app_secret_env=app_secret_env,
                    base_url=str(value.get("base_url", DEFAULT_DOMAIN)) or DEFAULT_DOMAIN,
                )
        bindings_raw = raw.get("chat_bindings", {})
        bindings = {str(k): str(v) for k, v in bindings_raw.items()} if isinstance(bindings_raw, dict) else {}
        return cls(bots, default=str(raw.get("default", "")), chat_bindings=bindings)

    def resolve(self, chat_id: str) -> BotConfig | None:
        bot_id = self._chat_bindings.get(chat_id, self.default)
        return self._bots.get(bot_id)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": bool(self._bots),
            "default": self.default or None,
            "bot_count": len(self._bots),
            "binding_count": len(self._chat_bindings),
            "bots": [bot.diagnostic() for bot in self._bots.values()],
        }
