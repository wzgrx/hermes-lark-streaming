"""Command Card Builders — 为原生命令构建静态卡片.

支持的命令:
- /status — 会话状态
- /help — 帮助信息
"""

from __future__ import annotations

import logging

_logger = logging.getLogger("hermes_lark_streaming")


def build_status_card(content: str) -> dict:
    """构建 /status 命令的静态卡片.

    Args:
        content: 命令输出文本（markdown 格式）

    Returns:
        Feishu 卡片 JSON (CardKit v2.0)
    """
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📊 Session Status",
                "i18n": {
                    "zh_cn": "📊 会话状态",
                    "en_us": "📊 Session Status",
                },
            },
            "template": "blue",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content,
            }
        ],
    }


def build_help_card(content: str) -> dict:
    """构建 /help 命令的静态卡片.

    Args:
        content: 命令输出文本（markdown 格式）

    Returns:
        Feishu 卡片 JSON (CardKit v2.0)
    """
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "ℹ️ Help",
                "i18n": {
                    "zh_cn": "ℹ️ 帮助",
                    "en_us": "ℹ️ Help",
                },
            },
            "template": "green",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content,
            }
        ],
    }


def build_commands_card(content: str) -> dict:
    """构建 /commands 命令的静态卡片.

    Args:
        content: 命令输出文本（markdown 格式）

    Returns:
        Feishu 卡片 JSON (CardKit v2.0)
    """
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "📜 Commands",
                "i18n": {
                    "zh_cn": "📜 命令列表",
                    "en_us": "📜 Commands",
                },
            },
            "template": "green",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": content,
            }
        ],
    }


def build_command_card(command: str, content: str) -> dict | None:
    """根据命令名称构建对应的卡片.

    Args:
        command: 命令名称（如 "status", "help", "commands"）
        content: 命令输出文本

    Returns:
        卡片 JSON，如果命令不支持则返回 None
    """
    builders = {
        "status": build_status_card,
        "help": build_help_card,
        "commands": build_commands_card,
    }

    builder = builders.get(command)
    if builder is None:
        return None

    try:
        return builder(content)
    except Exception as exc:
        _logger.warning(f"Failed to build card for command '{command}': {exc}")
        return None
