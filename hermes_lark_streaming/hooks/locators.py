"""Anchor locators: each finds candidate insertion sites in one Hermes source file."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Protocol

FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Site:
    """Insert before 0-based line ``index`` using ``indent``."""

    index: int
    indent: str


class Locator(Protocol):
    def describe(self) -> str: ...

    def find(self, source: str) -> list[Site]: ...


def indent_of(lines: list[str], lineno: int) -> str:
    """Indent of the nearest non-blank line at or above ``lineno`` (then below)."""
    for i in range(lineno, -1, -1):
        if 0 <= i < len(lines) and lines[i].strip():
            return lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    for i in range(lineno + 1, len(lines)):
        if lines[i].strip():
            return lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    return ""


@dataclass(frozen=True)
class Before:
    """Before every line containing ``text``."""

    text: str

    def describe(self) -> str:
        return f"line containing {self.text!r}"

    def find(self, source: str) -> list[Site]:
        lines = source.splitlines(True)
        return [Site(i, indent_of(lines, i)) for i, line in enumerate(lines) if self.text in line]


@dataclass(frozen=True)
class FuncBody:
    """First statement after the docstring of every function named ``name``."""

    name: str

    def describe(self) -> str:
        return f"function {self.name}"

    def find(self, source: str) -> list[Site]:
        lines = source.splitlines(True)
        sites: list[Site] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == self.name:
                body = node.body
                doc = body and isinstance(body[0], ast.Expr) and _is_str(body[0].value)
                start = 1 if doc else 0
                if start < len(body):
                    lineno = body[start].lineno - 1
                    sites.append(Site(lineno, indent_of(lines, lineno)))
        return sites


@dataclass(frozen=True)
class AfterAssign:
    """After every assignment whose target source contains ``target`` (optionally inside ``function``)."""

    target: str
    function: str | None = None

    def describe(self) -> str:
        return f"assignment to {self.target!r}"

    def find(self, source: str) -> list[Site]:
        lines = source.splitlines(True)
        tree: ast.AST = ast.parse(source)
        if self.function:
            scopes = [
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == self.function
            ]
            if len(scopes) != 1:
                return []
            tree = scopes[0]
        sites: list[Site] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(self.target in ast.unparse(t) for t in node.targets):
                if node.end_lineno is None:
                    continue
                sites.append(Site(node.end_lineno, indent_of(lines, node.lineno - 1)))
        return sites


@dataclass(frozen=True)
class AfterStopCommand:
    """After ``await self._interrupt_and_clear_session(..., invalidation_reason="stop_command")``."""

    def describe(self) -> str:
        return "stop_command interrupt call"

    def find(self, source: str) -> list[Site]:
        lines = source.splitlines(True)
        sites: list[Site] = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Await):
                continue
            call = node.value.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr != "_interrupt_and_clear_session":
                continue
            if any(_is_stop_reason(kw) for kw in call.keywords):
                sites.append(Site(node.end_lineno or node.lineno, indent_of(lines, node.lineno - 1)))
        return sites


def _is_str(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_stop_reason(keyword: ast.keyword) -> bool:
    return (
        keyword.arg == "invalidation_reason"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "stop_command"
    )
