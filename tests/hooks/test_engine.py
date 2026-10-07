"""Engine behaviour on a small synthetic tree: all-or-nothing, rollback, markers, backups."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_lark_streaming.hooks import engine as engine_module
from hermes_lark_streaming.hooks.engine import BACKUP_SUFFIX, Engine, PatchError, strip_blocks
from hermes_lark_streaming.hooks.locators import AfterAssign, Before, FuncBody
from hermes_lark_streaming.hooks.table import Injection


def _snippet(name: str):
    return lambda: [f"{name.lower()}_hit = True"]


TABLE = (
    Injection("ALPHA", "pkg/a.py", (Before("# anchor-a"),), _snippet("ALPHA"), ()),
    Injection("BETA", "pkg/a.py", (FuncBody("handler"),), _snippet("BETA"), ()),
    Injection("GAMMA", "pkg/b.py", (Before("# nope"), AfterAssign("state.value")), _snippet("GAMMA"), ()),
    Injection("OPT", "pkg/c.py", (Before("# anchor-c"),), _snippet("OPT"), (), optional=True),
)
A = '''\
class K:
    def handler(self):
        """doc"""
        x = 1
        # anchor-a
        return x
'''
B = "state.value = 3\nprint(state)\n"
C = "def f():\n    # anchor-c\n    pass\n"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text(A)
    (tmp_path / "pkg/b.py").write_text(B)
    (tmp_path / "pkg/c.py").write_text(C)
    return tmp_path


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_install_injects_at_anchors_with_indent(tree: Path) -> None:
    plan = Engine(tree, TABLE).install()
    assert plan.ok and set(plan.included) == {"ALPHA", "BETA", "GAMMA", "OPT"}
    a = (tree / "pkg/a.py").read_text()
    assert "        # HERMES_LARK_ALPHA_BEGIN\n        alpha_hit = True\n        # HERMES_LARK_ALPHA_END\n        # anchor-a" in a
    assert a.index('"""doc"""') < a.index("BETA_BEGIN") < a.index("x = 1")
    assert (tree / "pkg/b.py").read_text() == "state.value = 3\n# HERMES_LARK_GAMMA_BEGIN\ngamma_hit = True\n# HERMES_LARK_GAMMA_END\nprint(state)\n"


def test_install_is_idempotent_and_status_reports_up_to_date(tree: Path) -> None:
    eng = Engine(tree, TABLE)
    assert not eng.status().installed
    eng.install()
    first = snapshot(tree)
    assert not eng.install().changed
    assert snapshot(tree) == first
    status = eng.status()
    assert status.fully_installed and status.up_to_date and all(i.present for i in status.items)


def test_uninstall_restores_bytes_and_restore_equals_uninstall(tree: Path) -> None:
    before = {k: v for k, v in snapshot(tree).items()}
    eng = Engine(tree, TABLE)
    eng.install()
    assert eng.uninstall()
    assert {k: v for k, v in snapshot(tree).items() if not k.endswith(BACKUP_SUFFIX)} == before
    eng.install()
    eng.restore()
    assert {k: v for k, v in snapshot(tree).items() if not k.endswith(BACKUP_SUFFIX)} == before
    assert eng.uninstall() == []


def test_missing_required_anchor_writes_nothing(tree: Path) -> None:
    (tree / "pkg/b.py").write_text("print(1)\n")
    before = snapshot(tree)
    eng = Engine(tree, TABLE)
    plan = eng.verify()
    assert not plan.ok and "GAMMA" in plan.errors[0]
    with pytest.raises(PatchError, match="GAMMA"):
        eng.install()
    assert snapshot(tree) == before


def test_ambiguous_anchor_fails_closed(tree: Path) -> None:
    (tree / "pkg/a.py").write_text(A + "# anchor-a\n")
    with pytest.raises(PatchError, match="2 candidates"):
        Engine(tree, TABLE).install()


def test_fallback_anchor_used_when_primary_missing(tree: Path) -> None:
    assert Engine(tree, TABLE).verify().ok  # GAMMA resolved through AfterAssign fallback


def test_optional_injection_skipped_not_fatal(tree: Path) -> None:
    (tree / "pkg/c.py").write_text("pass\n")
    plan = Engine(tree, TABLE).install()
    assert plan.ok and "OPT" in plan.skipped and "OPT" not in plan.included
    (tree / "pkg/c.py").unlink()
    assert "OPT" in Engine(tree, TABLE).verify().skipped


def test_missing_required_file_is_an_error(tree: Path) -> None:
    (tree / "pkg/b.py").unlink()
    with pytest.raises(PatchError, match="not found"):
        Engine(tree, TABLE).install()


def test_uncompilable_result_is_rejected(tree: Path) -> None:
    bad = (Injection("BAD", "pkg/b.py", (Before("print(state)"),), lambda: ["if True"], ()),)
    before = snapshot(tree)
    with pytest.raises(PatchError, match="does not compile"):
        Engine(tree, bad).install()
    assert snapshot(tree) == before


def test_write_failure_rolls_back_every_file(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_module.atomic_write
    calls: list[Path] = []

    def flaky(path: Path, content: str) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real(path, content)

    monkeypatch.setattr(engine_module, "atomic_write", flaky)
    before = {k: v for k, v in snapshot(tree).items()}
    with pytest.raises(OSError, match="disk full"):
        Engine(tree, TABLE).install()
    monkeypatch.setattr(engine_module, "atomic_write", real)
    assert {k: v for k, v in snapshot(tree).items() if not k.endswith(BACKUP_SUFFIX)} == before


def test_backup_holds_clean_source_and_refreshes(tree: Path) -> None:
    eng = Engine(tree, TABLE)
    eng.install()
    assert (tree / f"pkg/a.py{BACKUP_SUFFIX}").read_text() == A
    eng.uninstall()
    (tree / "pkg/a.py").write_text(A + "# upstream update\n")
    eng.install()
    assert (tree / f"pkg/a.py{BACKUP_SUFFIX}").read_text() == A + "# upstream update\n"


def test_permissions_preserved(tree: Path) -> None:
    (tree / "pkg/a.py").chmod(0o640)
    Engine(tree, TABLE).install()
    assert (tree / "pkg/a.py").stat().st_mode & 0o777 == 0o640


def test_stale_block_content_is_rewritten_on_install(tree: Path) -> None:
    eng = Engine(tree, TABLE)
    eng.install()
    path = tree / "pkg/b.py"
    path.write_text(path.read_text().replace("gamma_hit = True", "old_import_path = True"))
    assert not eng.status().up_to_date
    assert eng.install().changed == [path]
    assert "gamma_hit = True" in path.read_text() and "old_import_path" not in path.read_text()


def test_legacy_and_unknown_marker_blocks_are_stripped(tree: Path) -> None:
    (tree / "pkg/b.py").write_text(
        "state.value = 3\n# HERMES_LARK_OLD_THING_BEGIN\nboom()\n# HERMES_LARK_OLD_THING_END\nprint(state)\n"
    )
    eng = Engine(tree, TABLE)
    assert eng.status().unknown_markers == ("OLD_THING",)
    assert not eng.status().fully_installed
    eng.install()
    assert "boom" not in (tree / "pkg/b.py").read_text() and eng.status().unknown_markers == ()


@pytest.mark.parametrize(
    "text",
    [
        "# HERMES_LARK_A_BEGIN\nx\n",
        "x\n# HERMES_LARK_A_END\n",
        "# HERMES_LARK_A_BEGIN\n# HERMES_LARK_B_BEGIN\n# HERMES_LARK_B_END\n# HERMES_LARK_A_END\n",
        "# HERMES_LARK_A_BEGIN\n# HERMES_LARK_B_END\n",
    ],
)
def test_malformed_blocks_raise(text: str) -> None:
    with pytest.raises(PatchError, match="Malformed"):
        strip_blocks(text)


def test_malformed_markers_block_install_and_leave_files(tree: Path) -> None:
    (tree / "pkg/b.py").write_text(B + "# HERMES_LARK_GAMMA_BEGIN\n")
    before = snapshot(tree)
    with pytest.raises(PatchError, match="Malformed"):
        Engine(tree, TABLE).install()
    assert snapshot(tree) == before


def test_restore_falls_back_to_backup_when_markers_malformed(tree: Path) -> None:
    eng = Engine(tree, TABLE)
    eng.install()
    (tree / "pkg/a.py").write_text("# HERMES_LARK_ALPHA_BEGIN\nbroken\n")
    eng.restore()
    assert (tree / "pkg/a.py").read_text() == A


def test_restore_without_backup_raises(tree: Path) -> None:
    (tree / "pkg/a.py").write_text("# HERMES_LARK_ALPHA_BEGIN\nbroken\n")
    with pytest.raises(PatchError, match="Malformed"):
        Engine(tree, TABLE).restore()
