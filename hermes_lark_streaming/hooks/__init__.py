"""The only package that touches Hermes source: injection table, engine, runtime bridge, native hooks."""

from __future__ import annotations

from .native import register

__all__ = ["register"]
