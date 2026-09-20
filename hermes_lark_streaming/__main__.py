"""CLI 入口: python -m hermes_lark_streaming [install|uninstall|status|verify]。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .patcher import CronPatcher, Patcher


def main() -> int:
    args = sys.argv[1:]
    if not args:
        _print_usage()
        return 0

    cmd = args[0]
    commands = _commands()
    handler = commands.get(cmd)
    if handler is not None:
        return handler()

    print(f"Unknown command: {cmd}")
    _print_usage()
    return 1


def _commands() -> dict[str, Callable[[], int]]:
    return {
        "install": _cmd_install,
        "uninstall": _cmd_uninstall,
        "restore": _cmd_restore,
        "status": _cmd_status,
        "verify": _cmd_verify,
    }


def _print_usage() -> None:
    print("Usage: python -m hermes_lark_streaming <command>")
    print()
    print("Commands:")
    print("  install    Apply AST patch to gateway/run.py and cron/scheduler.py")
    print("  uninstall  Remove AST patch")
    print("  restore    Restore from backup")
    print("  status     Show current patch status")
    print("  verify     Verify compatibility without patching")


def _get_patcher() -> Patcher | None:
    from .patcher import Patcher, PatcherError

    try:
        return Patcher()
    except PatcherError as e:
        print(f"Error: {e}")
        return None


def _get_cron_patcher() -> CronPatcher | None:
    from .patcher import CronPatcher, PatcherError

    try:
        return CronPatcher()
    except PatcherError:
        return None


def _cmd_install() -> int:
    patcher = _get_patcher()
    if patcher is None:
        return 1

    if patcher.is_fully_patched():
        print("Already patched.")
    else:
        print("Verifying target compatibility...")
        try:
            patcher.verify_target()
        except Exception as e:
            print(f"Verification failed: {e}")
            return 1
        print("Target compatible.")

        print("Applying patch...")
        try:
            patcher.apply()
        except Exception as e:
            print(f"Patch failed: {e}")
            return 1
        print("Patch applied successfully.")

    cron_patcher = _get_cron_patcher()
    if cron_patcher is not None and not cron_patcher.is_patched():
        try:
            cron_patcher.verify_target()
            cron_patcher.apply()
            print("Cron hook applied.")
        except Exception as e:
            print(f"Cron hook skipped: {e}")

    return 0


def _cmd_uninstall() -> int:
    patcher = _get_patcher()
    if patcher is None:
        return 1

    cron_patcher = _get_cron_patcher()
    if cron_patcher is not None and cron_patcher.is_patched():
        try:
            cron_patcher.remove()
            print("Cron hook removed.")
        except Exception as e:
            print(f"Cron hook remove failed: {e}")

    if not patcher.is_patched():
        print("Not patched.")
        return 0

    print("Removing patch...")
    try:
        patcher.remove()
    except Exception as e:
        print(f"Remove failed: {e}")
        return 1
    print("Patch removed.")
    return 0


def _cmd_restore() -> int:
    patcher = _get_patcher()
    if patcher is None:
        return 1

    cron_patcher = _get_cron_patcher()
    if cron_patcher is not None:
        try:
            cron_patcher.restore()
            print("Cron hook restored.")
        except Exception:
            pass

    print("Restoring from backup...")
    try:
        patcher.restore()
    except Exception as e:
        print(f"Restore failed: {e}")
        return 1
    print("Restored.")
    return 0


def _load_hermes_environment() -> None:
    """Load the active Hermes profile environment for standalone CLI commands."""
    try:
        from hermes_cli.env_loader import load_hermes_dotenv  # type: ignore[import-not-found]
    except ImportError:
        return
    from .config import hermes_home

    load_hermes_dotenv(hermes_home=hermes_home())


def _cmd_status() -> int:
    patcher = _get_patcher()
    if patcher is None:
        return 1

    patched = patcher.is_patched()
    print(f"Patched: {'yes' if patched else 'no'}")
    print(f"Target:  {patcher.run_path}")

    if patched:
        from .patcher import MARKERS

        sources: dict = {}
        _contents = getattr(patcher, "_contents", None)
        if callable(_contents):
            try:
                raw = _contents()
            except OSError:
                raw = None
            if isinstance(raw, dict):
                sources = raw
        if not sources:
            sources = {patcher.run_path: patcher.run_path.read_text(encoding="utf-8")}
        for begin, _end in MARKERS:
            label = begin.replace("# HERMES_LARK_", "").replace("_BEGIN", "").lower()
            found_in = sorted({path.name for path, text in sources.items() if begin in text})
            print(f"  {label}: {found_in[0] if found_in else 'MISSING'}")

    cron_patcher = _get_cron_patcher()
    if cron_patcher is not None:
        print(f"Cron hook: {'installed' if cron_patcher.is_patched() else 'not installed'}")

    # Check config after loading the same profile environment as Gateway.
    _load_hermes_environment()
    from .config import Config

    cfg = Config()
    print(f"Config streaming.enabled: {cfg.enabled}")
    print(f"Feishu credentials: {'configured' if (cfg.env_app_id or cfg.feishu_app_id) else 'MISSING'}")

    # Python interpreter check
    from .patcher import hermes_install_dir, hermes_python

    expected_py = hermes_python()
    if expected_py is not None:
        print(f"Hermes Python: {expected_py}")
        current = Path(sys.executable).resolve()
        if current != expected_py.resolve():
            print(f"  warning: running under {current}, but Hermes uses {expected_py}")
            print(f"  rerun commands with: {expected_py} -m hermes_lark_streaming ...")

    install_dir = hermes_install_dir()
    if install_dir is not None:
        print(f"Hermes install dir: {install_dir}")
    return 0


def _cmd_verify() -> int:
    patcher = _get_patcher()
    if patcher is None:
        return 1

    print(f"Target: {patcher.run_path}")
    print("Checking compatibility...")
    try:
        patcher.verify_target()
    except Exception as e:
        print(f"Incompatible: {e}")
        return 1
    print("Compatible.")

    cron_patcher = _get_cron_patcher()
    if cron_patcher is not None:
        print(f"Cron target: {cron_patcher.cron_path}")
        try:
            cron_patcher.verify_target()
        except Exception as e:
            print(f"Cron incompatible: {e}")
            return 1
        print("Cron target compatible.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
