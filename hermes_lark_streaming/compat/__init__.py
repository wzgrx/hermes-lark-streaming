"""Hermes behaviours that used to be carried as local commits, kept in this project instead.

Each module attaches through Hermes' public plugin surface or a guarded runtime wrapper, so the Hermes
checkout can stay identical to upstream and ``hermes update`` always fast-forwards.
"""

from __future__ import annotations

import logging

from . import error_classification, log_safety, skills_index

_logger = logging.getLogger("hermes_lark_streaming")


def register(ctx: object) -> None:
    """Install every compat behaviour; a failure in one never blocks the others or the gateway."""
    for name, install in (
        ("log_safety", log_safety.install),
        ("error_classification", lambda: error_classification.register(ctx)),
        ("skills_index", skills_index.install),
    ):
        try:
            install()
        except Exception:
            _logger.warning("compat %s not installed", name, exc_info=True)
