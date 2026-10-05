"""Hermes Lark streaming package entry point."""

__version__ = "0.20.25"


def register(ctx: object) -> None:
    """Register native observers and select a future owner-capable renderer.

    Current Hermes stream hooks are observer-only, so the reversible AST adapter remains
    the CardKit delivery owner. A future ``register_streaming_renderer`` protocol takes
    precedence without running both delivery paths.
    """
    from .footer.hooks import register as register_footer
    from .native_hooks import try_register

    try_register(ctx)
    register_footer(ctx)
    return None
