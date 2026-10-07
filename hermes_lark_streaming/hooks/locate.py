"""Find the Hermes install (the directory holding ``gateway/`` and ``cron/``) and its Python."""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .table import LAYOUT_PROBE

_logger = logging.getLogger("hermes_lark_streaming")
_VENV_PYTHONS = (
    ("venv", "bin", "python3"),
    ("venv", "bin", "python"),
    (".venv", "bin", "python3"),
    (".venv", "bin", "python"),
)


class LayoutError(RuntimeError):
    pass


def hermes_home() -> Path:
    """Active Hermes home: hermes_constants, then HERMES_HOME, then ~/.hermes."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    return Path(str(get_hermes_home()))


def code_roots() -> list[Path]:
    """Candidate code roots by priority (per-user git installer, then root-mode install)."""
    explicit = os.environ.get("HERMES_AGENT_DIR")
    roots = [Path(explicit)] if explicit else []
    return [*roots, hermes_home() / "hermes-agent", Path("/usr/local/lib/hermes-agent")]


def is_modular(root: Path) -> bool:
    return (root / LAYOUT_PROBE).is_file()


def find_root(explicit: Path | None = None) -> Path:
    """Locate a modular Hermes tree; raise ``LayoutError`` with an actionable message otherwise."""
    candidates = [explicit] if explicit is not None else code_roots()
    if explicit is None:
        try:
            spec = importlib.util.find_spec("gateway")
        except (ImportError, ValueError):
            spec = None
        for location in (spec.submodule_search_locations if spec else None) or []:
            candidates.append(Path(location).parent)
    legacy: Path | None = None
    for root in candidates:
        if root is None:
            continue
        if is_modular(root):
            return root
        if (root / "gateway" / "run.py").is_file():
            legacy = root
    if legacy is not None:
        raise LayoutError(
            f"{legacy} uses the single-file gateway/run.py layout (Hermes < 0.21.3), which hermes-lark-streaming 1.x "
            "no longer patches; upgrade Hermes or install the 0.x plugin"
        )
    tried = ", ".join(str(r) for r in candidates if r is not None)
    raise LayoutError(f"Hermes gateway not found (tried: {tried}); set HERMES_HOME to the dir containing hermes-agent/")


def _python_from_cli() -> Path | None:
    cli = shutil.which("hermes")
    if cli is None:
        return None
    try:
        text = Path(cli).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if m := re.search(r"""exec\s+["']([^"']+)["']""", text):
        for name in ("python3", "python"):
            py = Path(m.group(1)).parent / name
            if py.exists():
                return py
    if m := re.match(r"^#!\s*(\S+)", text):
        py = Path(m.group(1))
        if py.exists() and "python" in py.name.lower():
            return py
    return None


def hermes_python() -> Path | None:
    """Python interpreter Hermes runs under: ``which hermes`` first, venvs under known roots second."""
    if py := _python_from_cli():
        return py
    for root in code_roots():
        for parts in _VENV_PYTHONS:
            py = root.joinpath(*parts)
            if py.exists():
                return py
    return None


def home_from_hermes_python(py: Path) -> Path | None:
    """Ask Hermes itself (single source of truth) for its home directory."""
    try:
        result = subprocess.run(
            [str(py), "-c", "from hermes_constants import get_hermes_home; print(get_hermes_home())"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _logger.debug("hermes_constants lookup failed", exc_info=True)
        return None
    return Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None
