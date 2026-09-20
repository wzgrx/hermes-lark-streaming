"""Hermes Lark streaming package entry point."""

__version__ = "0.13.0"


def register(ctx: object) -> None:
    """Register package presence with Hermes.

    Gateway and cron lifecycle integration is installed by the AST patcher;
    the entry point stays intentionally side-effect free.
    """
    return None
