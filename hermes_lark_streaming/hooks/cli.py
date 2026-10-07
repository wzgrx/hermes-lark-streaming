"""verify / install / uninstall / restore / status for the Hermes source injection."""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from . import locate
from .engine import Engine, PatchError

COMMANDS = ("verify", "install", "uninstall", "restore", "status")
USAGE = """\
  install    Inject hooks into Hermes gateway/run_*.py and cron/scheduler_delivery.py
  uninstall  Remove injected hooks (also removes 0.x markers)
  restore    Remove hooks; fall back to the clean backup if markers are malformed
  status     Show injection status
  verify     Check compatibility without writing"""


def load_hermes_environment() -> None:
    """Load the active Hermes profile environment for standalone CLI commands."""
    try:
        loader = importlib.import_module("hermes_cli.env_loader")
    except ImportError:
        return
    loader.load_hermes_dotenv(hermes_home=locate.hermes_home())


def _engine(root: Path | None) -> Engine | None:
    try:
        return Engine(locate.find_root(root))
    except locate.LayoutError as exc:
        print(f"Error: {exc}")
        return None


def cmd_verify(engine: Engine) -> int:
    print(f"Target: {engine.root}")
    print("Checking compatibility...")
    plan = engine.verify()
    for name, reason in plan.skipped.items():
        print(f"Optional hook {name} skipped: {reason}")
    if not plan.ok:
        for error in plan.errors:
            print(f"Incompatible: {error}")
        return 1
    print(f"Compatible ({len(plan.included)} injection points).")
    return 0


def cmd_install(engine: Engine) -> int:
    try:
        plan = engine.install()
    except PatchError as exc:
        print(f"Install failed: {exc}")
        return 1
    for name, reason in plan.skipped.items():
        print(f"Optional hook {name} skipped: {reason}")
    if plan.changed:
        print(f"Patch applied ({len(plan.included)} hooks in {len(plan.changed)} files).")
    else:
        print("Already patched.")
    return 0


def cmd_uninstall(engine: Engine) -> int:
    try:
        changed = engine.uninstall()
    except PatchError as exc:
        print(f"Remove failed: {exc}")
        return 1
    print("Patch removed." if changed else "Not patched.")
    return 0


def cmd_restore(engine: Engine) -> int:
    try:
        changed = engine.restore()
    except PatchError as exc:
        print(f"Restore failed: {exc}")
        return 1
    print("Restored." if changed else "Nothing to restore.")
    return 0


def cmd_status(engine: Engine) -> int:
    status = engine.status()
    print(f"Patched: {'yes' if status.installed else 'no'}")
    print(f"Target:  {status.root}")
    for item in status.items:
        label = item.name.lower()
        where = Path(item.file).name if item.present else ("MISSING" if not item.count else f"DUPLICATED x{item.count}")
        print(f"  {label}: {where}")
    if status.unknown_markers:
        print(f"Unknown markers: {', '.join(status.unknown_markers)}")
    print(f"Up to date: {'yes' if status.up_to_date else 'no (run install)'}")
    managed = any("/installs/" in e and "/environments/" in e and "site-packages" in e for e in sys.path)
    if managed:
        print("Hermes Python: managed PM runtime (use ~/.local/bin/hermes --run-module hermes_lark_streaming ...)")
    elif (python := locate.hermes_python()) is not None:
        print(f"Hermes Python: {python}")
        current = Path(sys.executable).resolve()
        if current != python.resolve():
            print(f"  warning: running under {current}, but Hermes uses {python}")
            print(f"  rerun commands with: {python} -m hermes_lark_streaming ...")
    return 0


_HANDLERS: dict[str, Callable[[Engine], int]] = {
    "verify": cmd_verify,
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "restore": cmd_restore,
    "status": cmd_status,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes_lark_streaming", add_help=True)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--root", type=Path, default=None, help="Hermes code root (default: auto-detect)")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    engine = _engine(args.root)
    if engine is None:
        return 1
    return _HANDLERS[args.command](engine)
