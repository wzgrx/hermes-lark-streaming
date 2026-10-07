"""One engine: plan every target in memory, compile all, then replace atomically with rollback.

Nothing is written unless every required injection located exactly one site and every resulting file
compiles. Removal understands current and legacy (0.x) marker blocks.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .locators import Site
from .table import INJECTIONS, MARKER_PREFIX, Injection

BACKUP_SUFFIX = ".hermes_lark.bak"
_MARKER_RE = re.compile(rf"^# {MARKER_PREFIX}_([A-Z_]+)_(BEGIN|END)$")


class PatchError(RuntimeError):
    pass


def strip_blocks(source: str) -> str:
    """Remove every ``# HERMES_LARK_<X>_BEGIN`` .. ``_END`` block (any name, current or legacy)."""
    out: list[str] = []
    open_name: str | None = None
    for line in source.splitlines(keepends=True):
        match = _MARKER_RE.match(line.strip())
        if match is None:
            if open_name is None:
                out.append(line)
            continue
        name, kind = match.groups()
        if kind == "BEGIN":
            if open_name is not None:
                raise PatchError(f"Malformed injected marker block: nested {name} inside {open_name}")
            open_name = name
        else:
            if open_name != name:
                raise PatchError(f"Malformed injected marker block: unexpected end of {name}")
            open_name = None
    if open_name is not None:
        raise PatchError(f"Malformed injected marker block: unterminated {open_name}")
    return "".join(out)


def render_block(injection: Injection, indent: str) -> str:
    body = "".join(f"{indent}{line}\n" for line in injection.snippet())
    return f"{indent}{injection.begin}\n{body}{indent}{injection.end}\n"


def resolve(injection: Injection, source: str) -> Site:
    """First locator with exactly one hit; several hits fail closed instead of guessing."""
    for locator in injection.anchors:
        sites = locator.find(source)
        if len(sites) == 1:
            return sites[0]
        if len(sites) > 1:
            raise PatchError(f"{injection.name}: {len(sites)} candidates for {locator.describe()} in {injection.file}")
    wanted = " | ".join(a.describe() for a in injection.anchors)
    raise PatchError(f"{injection.name}: cannot find {wanted} in {injection.file}")


def atomic_write(path: Path, content: str) -> None:
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, dir=str(path.parent), prefix=".hermes_lark_", mode="w", encoding="utf-8"
        ) as handle:
            tmp = Path(handle.name)
            handle.write(content)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
        raise


@dataclass
class Plan:
    before: dict[Path, str]
    clean: dict[Path, str]
    after: dict[Path, str]
    included: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def changed(self) -> list[Path]:
        return [p for p in self.after if self.after[p] != self.before[p]]


@dataclass(frozen=True)
class ItemStatus:
    name: str
    file: str
    count: int
    optional: bool

    @property
    def present(self) -> bool:
        return self.count == 1


@dataclass(frozen=True)
class Status:
    root: Path
    items: tuple[ItemStatus, ...]
    unknown_markers: tuple[str, ...]
    up_to_date: bool

    @property
    def installed(self) -> bool:
        return any(i.count for i in self.items)

    @property
    def fully_installed(self) -> bool:
        return all(i.present for i in self.items if not i.optional) and not self.unknown_markers


class Engine:
    def __init__(self, root: Path, table: Sequence[Injection] = INJECTIONS) -> None:
        self.root = root
        self.table = tuple(table)

    def path(self, relative: str) -> Path:
        return self.root / relative

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(i.file for i in self.table))

    def _read(self) -> dict[Path, str]:
        """Read every target; a file needed only by optional injections may be absent."""
        needed = {i.file for i in self.table if not i.optional}
        contents: dict[Path, str] = {}
        for rel in self.files:
            path = self.path(rel)
            if path.is_file():
                contents[path] = path.read_text(encoding="utf-8")
            elif rel in needed:
                raise PatchError(f"Hermes source file not found: {path}")
        return contents

    def plan(self) -> Plan:
        before = self._read()
        clean = {path: strip_blocks(text) for path, text in before.items()}
        plan = Plan(before=before, clean=clean, after={})
        slots: list[tuple[Path, int, str]] = []
        for injection in self.table:
            path = self.path(injection.file)
            try:
                if path not in clean:
                    raise PatchError(f"{injection.name}: {injection.file} not found")
                site = resolve(injection, clean[path])
            except (PatchError, SyntaxError) as exc:
                if injection.optional:
                    plan.skipped[injection.name] = str(exc)
                else:
                    plan.errors.append(str(exc))
                continue
            slots.append((path, site.index, render_block(injection, site.indent)))
            plan.included.append(injection.name)
        after = dict(clean)
        for path, index, block in sorted(slots, key=lambda slot: slot[1], reverse=True):
            lines = after[path].splitlines(keepends=True)
            lines[index:index] = block.splitlines(keepends=True)
            after[path] = "".join(lines)
        plan.after = after
        if plan.ok:
            self._check(plan)
        return plan

    def _check(self, plan: Plan) -> None:
        for path, text in plan.after.items():
            try:
                compile(text, str(path), "exec")
            except SyntaxError as exc:
                plan.errors.append(f"{path.name}: injected source does not compile: {exc}")
        combined = "\n".join(plan.after.values())
        for injection in self.table:
            expected = 1 if injection.name in plan.included else 0
            if combined.count(injection.begin) != expected or combined.count(injection.end) != expected:
                plan.errors.append(f"{injection.name}: marker count mismatch after planning")

    def verify(self) -> Plan:
        """Plan without writing; ``plan.ok`` says whether install would succeed."""
        return self.plan()

    def status(self) -> Status:
        before = self._read()
        text = "\n".join(before.values())
        items = tuple(
            ItemStatus(
                i.name,
                i.file,
                before.get(self.path(i.file), "").count(i.begin),
                i.optional,
            )
            for i in self.table
        )
        known = {i.name for i in self.table}
        seen = {m.group(1) for line in text.splitlines() if (m := _MARKER_RE.match(line.strip()))}
        unknown = tuple(sorted(seen - known))
        try:
            plan = self.plan()
            up_to_date = plan.ok and not plan.changed
        except PatchError:
            up_to_date = False
        return Status(self.root, items, unknown, up_to_date)

    def install(self) -> Plan:
        plan = self.plan()
        if not plan.ok:
            raise PatchError("; ".join(plan.errors))
        self._publish(plan.before, plan.after, backup=plan.clean)
        return plan

    def uninstall(self) -> list[Path]:
        before = self._read()
        after = {path: strip_blocks(text) for path, text in before.items()}
        for path, text in after.items():
            compile(text, str(path), "exec")
        self._publish(before, after)
        return [p for p in after if after[p] != before[p]]

    def restore(self) -> list[Path]:
        """Uninstall; only when marker blocks are malformed fall back to the clean backup copy."""
        try:
            return self.uninstall()
        except PatchError:
            restored: list[Path] = []
            for rel in self.files:
                path = self.path(rel)
                backup = path.with_name(path.name + BACKUP_SUFFIX)
                if not backup.is_file():
                    raise
                atomic_write(path, backup.read_text(encoding="utf-8"))
                restored.append(path)
            return restored

    def _publish(
        self, before: dict[Path, str], after: dict[Path, str], *, backup: dict[Path, str] | None = None
    ) -> None:
        written: list[Path] = []
        try:
            for path, content in after.items():
                if content == before[path]:
                    continue
                if backup is not None:
                    self._write_backup(path, backup[path])
                atomic_write(path, content)
                written.append(path)
        except BaseException:
            for path in reversed(written):
                atomic_write(path, before[path])
            raise

    @staticmethod
    def _write_backup(path: Path, clean: str) -> None:
        """Keep the last known-clean upstream copy; refreshed from the stripped source on every change."""
        target = path.with_name(path.name + BACKUP_SUFFIX)
        if not target.is_file() or target.read_text(encoding="utf-8") != clean:
            target.write_text(clean, encoding="utf-8")
