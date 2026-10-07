"""``python -m hermes_lark_streaming <command>`` (also ``hermes --run-module hermes_lark_streaming <command>``)."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .hooks.cli import COMMANDS, USAGE
from .hooks.cli import main as hooks_main


def _usage() -> None:
    print("Usage: python -m hermes_lark_streaming <command>\n\nCommands:")
    print(USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        _usage()
        return 0
    if args[0] in COMMANDS:
        return hooks_main(args)
    try:
        from . import cli  # type: ignore[attr-defined,unused-ignore]
    except ImportError:
        cli = None
    if cli is not None and callable(getattr(cli, "main", None)):
        return int(cli.main(args))
    print(f"Unknown command: {args[0]}")
    _usage()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
