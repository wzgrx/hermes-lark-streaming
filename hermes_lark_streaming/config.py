"""Settings: Hermes ``config.yaml`` in, typed immutable objects out.

Two readers share one cached file load:

* :func:`Settings.parse` turns the ``streaming`` section into a frozen snapshot. The controller takes one
  snapshot per turn, so a card never changes layout half way through.
* :class:`ConfigSource` exposes the few ``display.*`` switches Hermes lets users flip at runtime
  (``/reasoning``, ``/verbose``) and the credential lookup.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .card.model import RenderOptions

DEFAULT_DOMAIN = "https://open.feishu.cn"
LARK_DOMAIN = "https://open.larksuite.com"
_RELOAD_TTL_S = 1.0
_logger = logging.getLogger("hermes_lark_streaming")

_TEXT_SIZES = frozenset({"normal_v2", "heading", "notation", "normal", "small", "large"})
_WIDTH_MODES = frozenset({"default", "compact", "fill"})
_PROCESS_MODES = frozenset({"auto", "open", "closed", "off"})


def hermes_home() -> Path:
    """Hermes home: the host's own resolver when importable, else ``HERMES_HOME`` or ``~/.hermes``."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return Path(get_hermes_home())


def secret(name: str) -> str:
    """Read a secret through Hermes' scoped store when present, else the environment."""
    try:
        from agent.secret_scope import get_secret  # type: ignore[import-not-found]
    except ImportError:
        return os.environ.get(name, "")
    return str(get_secret(name, "") or "")


def _section(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _choice(value: Any, allowed: frozenset[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(float(value), high))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class Backpressure:
    enabled: bool = True
    min_ms: float = 100.0
    max_ms: float = 1500.0


@dataclass(frozen=True, slots=True)
class Settings:
    enabled: bool = False
    text_size: str = "normal_v2"
    width_mode: str = "default"
    process: str = "auto"
    agent_name: str = ""
    card_ttl_sec: int = 600
    rollover_sec: float = 480.0
    backpressure: Backpressure = field(default_factory=Backpressure)
    details: dict[str, Any] = field(default_factory=dict)  # parsed by details.DetailsConfig
    bots: dict[str, Any] = field(default_factory=dict)  # parsed by routing.BotRegistry

    @classmethod
    def parse(cls, streaming: Any) -> Settings:
        raw = _section(streaming)
        body = _section(raw.get("body"))
        pressure = raw.get("adaptive_backpressure", {})
        pressure_raw = _section(pressure)
        low = _number(pressure_raw.get("min_ms"), 100.0, 50.0, 5000.0)
        return cls(
            enabled=raw.get("enabled") is True,
            text_size=_choice(raw.get("text_size", body.get("text_size")), _TEXT_SIZES, "normal_v2"),
            width_mode=_choice(raw.get("width_mode"), _WIDTH_MODES, "default"),
            process=_choice(raw.get("process"), _PROCESS_MODES, "auto"),
            agent_name=str(raw.get("agent_name") or "").strip()[:30],
            card_ttl_sec=int(_number(raw.get("card_ttl_sec"), 600, 60, 86400)),
            rollover_sec=_number(raw.get("rollover_sec"), 480.0, 60.0, 570.0),
            backpressure=Backpressure(
                enabled=bool(pressure.get("enabled", True)) if isinstance(pressure, dict) else bool(pressure),
                min_ms=low,
                max_ms=_number(pressure_raw.get("max_ms"), 1500.0, low, 10000.0),
            ),
            details=_section(raw.get("details")),
            bots=_section(raw.get("bots")),
        )

    @property
    def render(self) -> RenderOptions:
        return RenderOptions(
            text_size=self.text_size,
            width_mode=self.width_mode,
            process="auto" if self.process == "off" else self.process,
            show_process=self.process != "off",
        )


class ConfigSource:
    """Cached, hot-reloading view of one Hermes profile's ``config.yaml``."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home) if home is not None else hermes_home()
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._stat: tuple[int, int, int] | None = None
        self._checked = 0.0
        self._error_at = float("-inf")
        self._startup: Settings | None = None

    @property
    def path(self) -> Path:
        return self.home / "config.yaml"

    def raw(self) -> dict[str, Any]:
        """The parsed file, re-read at most once per second and only when it changed on disk."""
        with self._lock:
            now = time.monotonic()
            if self._cache is not None and now - self._checked < _RELOAD_TTL_S:
                return self._cache
            self._checked = now
            try:
                stat = self.path.stat()
                key = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
                if self._cache is None or key != self._stat:
                    loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                    if not isinstance(loaded, dict) or any(
                        loaded.get(name) is not None and not isinstance(loaded[name], dict)
                        for name in ("display", "streaming")
                    ):
                        raise ValueError("configuration sections must be mappings")
                    self._cache, self._stat = loaded, key
            except FileNotFoundError:
                if self._cache is None:
                    self._cache = {}
            except (OSError, UnicodeError, yaml.YAMLError, ValueError):
                # Never log the YAML error text: it can quote credentials.
                if now - self._error_at >= 60:
                    _logger.warning("config reload failed; keeping the last valid settings")
                    self._error_at = now
                if self._cache is None:
                    self._cache = {}
            return self._cache

    def settings(self) -> Settings:
        """Startup snapshot: card structure and credentials do not change while the gateway runs."""
        if self._startup is None:
            self._startup = Settings.parse(self.raw().get("streaming"))
        return self._startup

    def live_details(self) -> dict[str, Any]:
        """Details settings that may change at runtime (history timezone, toggles)."""
        return _section(_section(self.raw().get("streaming")).get("details"))

    def _display(self, key: str, default: bool) -> bool:
        display = _section(self.raw().get("display"))
        feishu = _section(_section(display.get("platforms")).get("feishu"))
        if key in feishu:
            return bool(feishu[key])
        return bool(display.get(key, default))

    @property
    def show_reasoning(self) -> bool:
        return self._display("show_reasoning", False)

    @property
    def show_tool_use(self) -> bool:
        return self._display("show_tool_use", True)

    def credentials(self) -> tuple[str, str, str]:
        """``(app_id, app_secret, base_url)`` from the environment, else Hermes' platform config."""
        app_id = secret("FEISHU_APP_ID") or secret("LARK_APP_ID")
        app_secret = secret("FEISHU_APP_SECRET") or secret("LARK_APP_SECRET")
        if app_id and app_secret:
            base = secret("FEISHU_BASE_URL") or secret("LARK_BASE_URL") or DEFAULT_DOMAIN
            return app_id, app_secret, base
        platform = self._platform()
        return (
            str(platform.get("app_id", "")),
            str(platform.get("app_secret", "")),
            str(platform.get("base_url", DEFAULT_DOMAIN)),
        )

    def _platform(self) -> dict[str, Any]:
        raw = self.raw()
        for key in ("feishu", "lark"):
            top = _section(raw.get(key))
            if top.get("app_id"):
                return top
        parents = [_section(_section(raw.get("gateway")).get("platforms")), _section(raw.get("platforms"))]
        for parent in parents:
            for key in ("feishu", "lark"):
                platform = _section(parent.get(key))
                extra = _section(platform.get("extra"))
                if not extra.get("app_id"):
                    continue
                result = dict(extra)
                if "base_url" in platform and "base_url" not in result:
                    result["base_url"] = platform["base_url"]
                domain = str(extra.get("domain", platform.get("domain", ""))).lower()
                if "base_url" not in result and domain == "lark":
                    result["base_url"] = LARK_DOMAIN
                return result
        return {}
