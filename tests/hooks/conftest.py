from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from hermes_lark_streaming.hooks.engine import Engine
from hermes_lark_streaming.hooks.table import INJECTIONS

HERMES_SOURCE = Path(os.environ.get("HERMES_MODULAR_SOURCE", Path.home() / ".hermes/hermes-agent"))
FILES = sorted({i.file for i in INJECTIONS})


def indented(builder: Callable[[], list[str]], indent: str = "    ") -> str:
    return "".join(f"{indent}{line}\n" for line in builder())


@pytest.fixture
def hermes_source(tmp_path: Path) -> Path:
    """A tmp copy of the real current Hermes modular sources, stripped of any installed hook block."""
    if not (HERMES_SOURCE / "gateway/run_turn_runner.py").is_file():
        pytest.skip("modular Hermes source required (set HERMES_MODULAR_SOURCE)")
    root = tmp_path / "hermes"
    for rel in FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERMES_SOURCE / rel, root / rel)
    Engine(root).uninstall()
    return root


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A fake controller served by ``hermes_lark_streaming.session.get_controller``."""
    ctrl = MagicMock()
    ctrl.enabled = True
    module = sys.modules.get("hermes_lark_streaming.session")
    if module is None:
        module = ModuleType("hermes_lark_streaming.session")
        monkeypatch.setitem(sys.modules, "hermes_lark_streaming.session", module)
    monkeypatch.setattr(module, "get_controller", lambda: ctrl, raising=False)
    return ctrl


@pytest.fixture(autouse=True)
def fresh_metrics() -> Iterator[None]:
    from hermes_lark_streaming.metrics import metrics

    metrics.reset()
    yield
    metrics.reset()
