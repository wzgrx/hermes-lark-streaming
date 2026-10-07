"""飞书卡片 i18n — 中英双语文本映射."""

from __future__ import annotations

__all__ = [
    "_LOCALES",
    "_T",
    "_i18n",
    "_t",
]

_LOCALES = ["zh_cn", "en_us", "ja_jp", "ko_kr"]

_T: dict[str, tuple[str, str]] = {
    "status_completed": ("✅ Completed", "✅ 已完成"),
    "status_error": ("❌ Error", "❌ 出错"),
    "status_stopped": ("🛑 Stopped", "🛑 已停止"),
    "elapsed": ("Elapsed {}", "耗时 {}"),
    "context": ("Context {}", "上下文 {}"),
    "processing": ("Processing...", "处理中..."),
    "processing_prefix": ("💭 Processing...", "💭 处理中..."),
    "tool_use": ("Tool use", "工具执行"),
    "tool_pending": ("🛠️ Tool use pending", "🛠️ 等待工具执行"),
    "steps": ("{} step{}", "{} 步"),
    "thought": ("Thought", "思考"),
    "thinking_panel": ("Thinking", "思考中"),
    "thought_for": ("Thought for {}", "思考了 {}"),
    "done": ("Done.", "完成。"),
}


_JA = {
    "status_completed": "✅ 完了",
    "status_error": "❌ エラー",
    "status_stopped": "🛑 停止",
    "processing": "処理中...",
    "processing_prefix": "💭 処理中...",
    "tool_use": "ツール実行",
    "tool_pending": "🛠️ ツール実行待ち",
    "thought": "思考",
    "thinking_panel": "思考中",
    "done": "完了。",
}
_KO = {
    "status_completed": "✅ 완료",
    "status_error": "❌ 오류",
    "status_stopped": "🛑 중지됨",
    "processing": "처리 중...",
    "processing_prefix": "💭 처리 중...",
    "tool_use": "도구 실행",
    "tool_pending": "🛠️ 도구 실행 대기",
    "thought": "생각",
    "thinking_panel": "생각 중",
    "done": "완료.",
}


def _i18n(en: str, zh: str, ja: str | None = None, ko: str | None = None) -> dict[str, str]:
    return {"zh_cn": zh, "en_us": en, "ja_jp": ja or en, "ko_kr": ko or en}


def _t(key: str) -> dict[str, str]:
    """Return all supported locale strings, with English as the explicit fallback."""
    en, zh = _T[key]
    return _i18n(en, zh, _JA.get(key), _KO.get(key))
