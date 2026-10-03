"""读取 Hermes 配置，提供本插件所需的配置项."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DOMAIN = "https://open.feishu.cn"  # SDK 根域名，Larksuite 用 https://open.larksuite.com
LARK_DOMAIN = "https://open.larksuite.com"
_CONFIG_RELOAD_TTL_S = 1.0
_logger = logging.getLogger("hermes_lark_streaming")


def hermes_home() -> Path:
    """Hermes 主目录，优先级 HERMES_HOME 环境变量 → ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return Path(get_hermes_home())


def _get_secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret  # type: ignore[import-not-found]
    except ImportError:
        return os.environ.get(name, "")
    return get_secret(name, "") or ""


def _config_path(home: Path | None = None) -> Path:
    """Hermes 主配置路径，支持绑定到指定 profile home."""
    return (home or hermes_home()) / "config.yaml"


class Config:
    """插件配置，惰性读取 Hermes 主配置."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else None
        self._raw: dict[str, Any] | None = None
        self._reload_lock = threading.Lock()
        self._reload_cache: dict[str, Any] | None = None
        self._reload_cache_at = 0.0
        self._reload_cache_path: Path | None = None
        self._reload_cache_stat: tuple[int, int, int] | None = None
        self._reload_error_at = float("-inf")

    @property
    def enabled(self) -> bool:
        """是否启用流式卡片."""
        sec = self._streaming_sec()
        return bool(sec.get("enabled", False))

    @property
    def panel_expanded(self) -> bool:
        """完成态卡片中面板（工具、推理）是否保持展开."""
        sec = self._streaming_sec()
        return bool(sec.get("panel_expanded", False))

    @property
    def show_reasoning(self) -> bool:
        """是否展示推理过程（display.platforms.feishu.show_reasoning → display.show_reasoning）.

        通过短时缓存感知 /reasoning 命令在运行时修改的配置文件.
        """
        display = self._reload().get("display")
        if not isinstance(display, dict):
            return False
        platforms = display.get("platforms")
        if isinstance(platforms, dict):
            feishu = platforms.get("feishu")
            if isinstance(feishu, dict) and "show_reasoning" in feishu:
                return bool(feishu["show_reasoning"])
        return bool(display.get("show_reasoning", False))

    @property
    def show_tool_use(self) -> bool:
        """是否在卡片中展示工具调用面板.

        优先级：display.platforms.feishu.show_tool_use → display.show_tool_use，
        默认 True（保持向后兼容）。短时缓存支持运行时切换。
        """
        display = self._reload().get("display")
        if not isinstance(display, dict):
            return True
        platforms = display.get("platforms")
        if isinstance(platforms, dict):
            feishu = platforms.get("feishu")
            if isinstance(feishu, dict) and "show_tool_use" in feishu:
                return bool(feishu["show_tool_use"])
        return bool(display.get("show_tool_use", True))

    @property
    def feishu_app_id(self) -> str:
        return str(self._platform_cfg().get("app_id", ""))

    @property
    def feishu_app_secret(self) -> str:
        return str(self._platform_cfg().get("app_secret", ""))

    @property
    def feishu_base_url(self) -> str:
        return str(self._platform_cfg().get("base_url", DEFAULT_DOMAIN))

    @property
    def card_duration_sec(self) -> int:
        """卡片存活检测超时."""
        return int(self._streaming_sec().get("card_ttl_sec", 600))

    @property
    def header_enabled(self) -> bool:
        """流式卡片和完成态卡片是否显示 header."""
        sec = self._streaming_sec()
        header = sec.get("header", {})
        if not isinstance(header, dict):
            return False
        return bool(header.get("enabled", False))

    @property
    def footer_enabled(self) -> bool:
        """完成态卡片是否显示 footer."""
        sec = self._streaming_sec()
        footer = sec.get("footer", {})
        if not isinstance(footer, dict):
            return True
        return bool(footer.get("enabled", True))

    @property
    def footer_mode(self) -> str:
        """Enhanced telemetry is opt-in; retain existing layouts on upgrade."""
        footer = self._streaming_sec().get("footer", {})
        if not isinstance(footer, dict):
            return "classic"
        return "enhanced" if footer.get("mode") == "enhanced" else "classic"

    @property
    def footer_details(self) -> bool:
        footer = self._streaming_sec().get("footer", {})
        return footer.get("details", True) is not False if isinstance(footer, dict) else True

    @property
    def footer_element_reserve(self) -> int:
        if self.card_layout == "reference":
            from .cardkit.reference import REFERENCE_ELEMENT_RESERVE

            return REFERENCE_ELEMENT_RESERVE
        if self.footer_mode == "enhanced":
            from .footer.layout import DETAIL_ELEMENT_RESERVE, SUMMARY_ELEMENT_RESERVE

            return DETAIL_ELEMENT_RESERVE if self.footer_details else SUMMARY_ELEMENT_RESERVE
        return 2

    @property
    def card_layout(self) -> str:
        """Screenshot V1 layout is opt-in and requires enhanced telemetry."""
        return ("reference" if self._streaming_sec().get("layout") == "reference"
                and self.footer_mode == "enhanced" else "classic")

    @property
    def reference_resources_enabled(self) -> bool:
        settings = self._streaming_sec().get("resources", {})
        return isinstance(settings, dict) and settings.get("enabled") is True

    @property
    def reference_agent_name(self) -> str:
        from .footer.state import label

        return label(self._streaming_sec().get("agent_name"))

    @property
    def reference_history_timezone(self) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        value = self.footer_history.get("timezone", "UTC")
        try:
            ZoneInfo(value if isinstance(value, str) else "UTC")
        except (ValueError, ZoneInfoNotFoundError):
            return "UTC"
        return value if isinstance(value, str) else "UTC"

    @property
    def footer_history(self) -> dict[str, Any]:
        # Only history/display settings are live. Card layout, credentials and
        # transport structure retain the controller's startup snapshot.
        sec = self._reload().get("streaming", {})
        footer = sec.get("footer", {}) if isinstance(sec, dict) else {}
        value = footer.get("history", {}) if isinstance(footer, dict) else {}
        return value if isinstance(value, dict) else {}

    @property
    def body_text_size(self) -> str:
        """Body answer markdown 的文字大小."""
        sec = self._streaming_sec()
        body = sec.get("body", {})
        if not isinstance(body, dict):
            return "normal_v2"
        return str(body.get("text_size", "normal_v2")) or "normal_v2"

    @property
    def width_mode(self) -> str:
        """Card 宽度模式: default / compact / fill."""
        raw = str(self._streaming_sec().get("width_mode", "default") or "default").strip().lower()
        if raw in {"default", "compact", "fill"}:
            return raw
        return "default"

    @property
    def footer_text_size(self) -> str:
        """Footer markdown 的文字大小."""
        sec = self._streaming_sec()
        footer = sec.get("footer", {})
        if not isinstance(footer, dict):
            return "notation"
        return str(footer.get("text_size", "notation")) or "notation"

    @property
    def footer_fields(self) -> list[list[str]]:
        """Footer 字段布局（二维数组）."""
        sec = self._streaming_sec()
        footer = sec.get("footer", {})
        if not isinstance(footer, dict):
            return self._default_footer_fields()
        fields = footer.get("fields")
        if not fields:
            return self._default_footer_fields()
        if not isinstance(fields, list):
            return self._default_footer_fields()
        # 一维数组自动包装为二维
        if fields and isinstance(fields[0], str):
            return [fields]
        return fields

    @property
    def footer_show_label(self) -> bool:
        """Footer 是否显示字段标签."""
        sec = self._streaming_sec()
        footer = sec.get("footer", {})
        return bool(footer.get("show_label", False)) if isinstance(footer, dict) else False

    @staticmethod
    def _default_footer_fields() -> list[list[str]]:
        return [["status", "elapsed", "context", "model"]]

    @property
    def adaptive_backpressure_enabled(self) -> bool:
        value = self._streaming_sec().get("adaptive_backpressure", True)
        if isinstance(value, dict):
            return bool(value.get("enabled", True))
        return bool(value)

    @property
    def backpressure_min_ms(self) -> float:
        value = self._streaming_sec().get("adaptive_backpressure", {})
        raw = value.get("min_ms", 100) if isinstance(value, dict) else 100
        return max(50.0, min(float(raw), 5000.0))

    @property
    def backpressure_max_ms(self) -> float:
        value = self._streaming_sec().get("adaptive_backpressure", {})
        raw = value.get("max_ms", 1500) if isinstance(value, dict) else 1500
        return max(self.backpressure_min_ms, min(float(raw), 10000.0))

    @property
    def history_compact_after(self) -> int:
        value = self._streaming_sec().get("history_compaction", {})
        raw = value.get("compact_after", 48) if isinstance(value, dict) else 48
        return max(8, min(int(raw), 128))

    @property
    def history_keep_recent(self) -> int:
        value = self._streaming_sec().get("history_compaction", {})
        raw = value.get("keep_recent", 24) if isinstance(value, dict) else 24
        return max(4, min(int(raw), self.history_compact_after))

    @property
    def callback_ttl_sec(self) -> int:
        return max(30, min(int(self._streaming_sec().get("callback_ttl_sec", 300)), 3600))

    def bot_registry(self) -> Any:
        """Return chat-to-bot routing; credentials are environment-variable references only."""
        from .routing import BotRegistry

        return BotRegistry.from_streaming_config(self._streaming_sec())

    @property
    def env_app_id(self) -> str:
        return _get_secret("FEISHU_APP_ID") or _get_secret("LARK_APP_ID")

    @property
    def env_app_secret(self) -> str:
        return _get_secret("FEISHU_APP_SECRET") or _get_secret("LARK_APP_SECRET")

    def _streaming_sec(self) -> dict[str, Any]:
        raw = self._load()
        sec = raw.get("streaming") or {}
        if isinstance(sec, dict):
            return sec
        return {}

    def _platform_cfg(self) -> dict[str, Any]:
        """从环境变量或平台配置找飞书凭据."""
        if self.env_app_id and self.env_app_secret:
            return {
                "app_id": self.env_app_id,
                "app_secret": self.env_app_secret,
                "base_url": _get_secret("FEISHU_BASE_URL") or _get_secret("LARK_BASE_URL") or DEFAULT_DOMAIN,
            }
        raw = self._load()
        for key in ("feishu", "lark"):
            pf = raw.get(key)
            if isinstance(pf, dict) and pf.get("app_id"):
                return pf
        gateway = raw.get("gateway")
        candidate_parents: list[dict[str, Any]] = []
        if isinstance(gateway, dict) and isinstance(gateway.get("platforms"), dict):
            candidate_parents.append(gateway["platforms"])
        platforms = raw.get("platforms")
        if isinstance(platforms, dict):
            candidate_parents.append(platforms)
        for parent in candidate_parents:
            for key in ("feishu", "lark"):
                platform = parent.get(key)
                if not isinstance(platform, dict):
                    continue
                extra = platform.get("extra")
                if not isinstance(extra, dict) or not extra.get("app_id"):
                    continue
                result = dict(extra)
                if "base_url" in platform and "base_url" not in result:
                    result["base_url"] = platform["base_url"]
                if "base_url" not in result and str(extra.get("domain", platform.get("domain", ""))).lower() == "lark":
                    result["base_url"] = LARK_DOMAIN
                return result
        return {}

    def _load(self) -> dict[str, Any]:
        if self._raw is not None:
            return self._raw
        self._raw = self._reload()
        return self._raw

    def _reload(self) -> dict[str, Any]:
        """短时缓存运行时配置，避免每个流式增量都解析整份 YAML."""
        path = _config_path(self._home)
        with self._reload_lock:
            now = time.monotonic()
            if (
                self._reload_cache is not None
                and self._reload_cache_path == path
                and now - self._reload_cache_at < _CONFIG_RELOAD_TTL_S
            ):
                if self._reload_cache_stat is None and self._raw is not None:
                    return self._raw  # Explicit in-memory baseline before any disk snapshot.
                return self._reload_cache
            try:
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    if self._reload_cache_stat is not None:
                        raise  # A transient replacement must not erase good settings.
                    self._reload_cache = self._raw if self._raw is not None else {}
                    self._reload_cache_path = path
                    self._reload_cache_at = now
                    return self._reload_cache
                file_stat = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
                if (self._reload_cache is None or self._reload_cache_path != path
                        or self._reload_cache_stat != file_stat):
                    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                    if raw is None:
                        raw = {}
                    if not isinstance(raw, dict) or any(
                        raw.get(key) is not None and not isinstance(raw[key], dict)
                        for key in ("display", "streaming")
                    ):
                        raise ValueError("Configuration sections must be mappings")
                    self._reload_cache = raw
                    self._reload_cache_path = path
                    self._reload_cache_stat = file_stat
            except (OSError, UnicodeError, yaml.YAMLError, ValueError):
                # No YAML exception text: it can contain credentials or source lines.
                if now - self._reload_error_at >= 60:
                    _logger.warning("Card configuration reload failed; retaining last valid settings")
                    self._reload_error_at = now
                if self._reload_cache is None:
                    self._reload_cache = self._raw if self._raw is not None else {}
                self._reload_cache_path = path
            self._reload_cache_at = time.monotonic()
            return self._reload_cache
