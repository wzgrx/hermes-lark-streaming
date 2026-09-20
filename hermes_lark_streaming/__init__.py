"""Hermes Lark streaming package entry point."""

__version__ = "0.15.0"


def register(ctx: object) -> None:
    """Register package presence with Hermes.

    Hermes currently uses the compatibility patcher.  Future runtimes can expose
    ``register_streaming_renderer`` and will be selected without source rewriting.
    """
    from .native_hooks import try_register

    try_register(ctx)
    return None
