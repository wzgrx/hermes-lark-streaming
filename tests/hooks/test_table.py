"""Static consistency of the injection table and snippet builders."""

from __future__ import annotations

import ast
import re

import pytest

from hermes_lark_streaming.hooks import bridge
from hermes_lark_streaming.hooks.engine import render_block, strip_blocks
from hermes_lark_streaming.hooks.snippets import BRIDGE
from hermes_lark_streaming.hooks.table import INJECTION_NAMES, INJECTIONS

EXPECTED = {
    "NORMALIZE", "START", "COMPLETE", "FOLLOWUP_COMPLETE", "FOLLOWUP_RESULT", "TOOL", "ANSWER", "THINKING",
    "REASONING", "BACKGROUND_REVIEW", "ABORT", "STOP", "INTERRUPT", "BG_DELIVER", "CLARIFY", "APPROVAL",
    "CRON_DELIVER",
}  # fmt: skip


def test_table_covers_all_seventeen_hooks_once() -> None:
    assert set(INJECTION_NAMES) == EXPECTED
    assert len(INJECTION_NAMES) == len(EXPECTED) == 17


def test_only_cron_is_optional() -> None:
    assert [i.name for i in INJECTIONS if i.optional] == ["CRON_DELIVER"]


@pytest.mark.parametrize("injection", INJECTIONS, ids=lambda i: i.name)
def test_snippet_imports_exactly_its_declared_bridge_names(injection) -> None:
    body = "\n".join(injection.snippet())
    imported: set[str] = set()
    for node in ast.walk(ast.parse("def f():\n" + "\n".join("    " + line for line in injection.snippet()))):
        if isinstance(node, ast.ImportFrom) and node.module == BRIDGE:
            imported |= {alias.name for alias in node.names}
    assert imported == set(injection.bridge), body
    for name in injection.bridge:
        assert callable(getattr(bridge, name))
    assert "hermes_lark_streaming.patch" not in body


@pytest.mark.parametrize("injection", INJECTIONS, ids=lambda i: i.name)
def test_rendered_block_is_markered_and_strippable(injection) -> None:
    block = render_block(injection, "        ")
    lines = block.splitlines()
    assert lines[0].strip() == injection.begin and lines[-1].strip() == injection.end
    assert strip_blocks("x = 1\n" + block + "y = 2\n") == "x = 1\ny = 2\n"
    assert re.fullmatch(r"# HERMES_LARK_[A-Z_]+_BEGIN", injection.begin)


def test_every_hook_logs_through_exception_guard() -> None:
    for injection in INJECTIONS:
        text = "\n".join(injection.snippet())
        assert "injected hook failed:" in text, injection.name
