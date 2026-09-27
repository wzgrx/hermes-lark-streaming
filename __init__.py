"""Hermes directory-plugin shim for the packaged CardKit integration."""

def register(ctx: object) -> None:
    """Register the directory plugin without importing its package at collection time."""
    from .hermes_lark_streaming import register as package_register

    package_register(ctx)

__all__ = ["register"]
