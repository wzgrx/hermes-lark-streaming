"""``skills.index_mode: names_only``: list every skill by name only in the system prompt.

Hermes renders each skill's description into the always-on index. With this mode set, every category is
rendered as one names-only line, which keeps the prompt short; skills still load through ``skill_view``.
The wrapper reads the mode on each render, so only the prompt cache needs a gateway restart to notice a change.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from ..config import ConfigSource

MODE_KEY = "index_mode"
NAMES_ONLY = "names_only"
_NOTE_OLD = "are outside the current coding context, so their descriptions are omitted"
_NOTE_NEW = "use a compact discovery context, so their descriptions are omitted"
_MARK = "_hermes_lark_names_only"


def configured_mode(source: ConfigSource | None = None) -> str:
    skills = (source or ConfigSource()).raw().get("skills")
    mode = str(skills.get(MODE_KEY) or "").strip().lower() if isinstance(skills, dict) else ""
    return mode if mode == NAMES_ONLY else "full"


def wrap(render: Callable[..., str], mode: Callable[[], str]) -> Callable[..., str]:
    @functools.wraps(render)
    def wrapper(skills_by_category: dict[str, Any], descriptions: Any, compact: Any, *rest: Any, **kw: Any) -> str:
        if mode() == NAMES_ONLY and skills_by_category:
            compact = frozenset(category.split("/", 1)[0] for category in skills_by_category)
        return render(skills_by_category, descriptions, compact, *rest, **kw).replace(_NOTE_OLD, _NOTE_NEW)

    setattr(wrapper, _MARK, True)
    return wrapper


def install(source: ConfigSource | None = None) -> bool:
    """Wrap Hermes' index renderer once; returns whether it was newly wrapped."""
    try:
        from agent import prompt_builder  # type: ignore[import-not-found]
    except ImportError:
        return False
    render = getattr(prompt_builder, "_render_skills_index", None)
    if not callable(render) or getattr(render, _MARK, False):
        return False
    src = source or ConfigSource()
    prompt_builder._render_skills_index = wrap(render, lambda: configured_mode(src))
    return True
