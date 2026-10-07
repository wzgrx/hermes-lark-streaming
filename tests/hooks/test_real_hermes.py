"""The engine against a copy of the real current Hermes sources, plus execution of the injected callbacks."""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, call, patch

import pytest

from hermes_lark_streaming.hooks.engine import BACKUP_SUFFIX, Engine, PatchError, strip_blocks
from hermes_lark_streaming.hooks.table import INJECTIONS

from .conftest import FILES, HERMES_SOURCE


def read_all(root: Path) -> dict[str, str]:
    return {rel: (root / rel).read_text() for rel in FILES}


def test_verify_install_verify_uninstall_roundtrip_is_byte_identical(hermes_source: Path) -> None:
    eng = Engine(hermes_source)
    original = read_all(hermes_source)
    plan = eng.verify()
    assert plan.ok and not plan.skipped and len(plan.included) == 17
    assert read_all(hermes_source) == original  # verify never writes

    eng.install()
    status = eng.status()
    assert status.fully_installed and status.up_to_date
    assert {i.name: i.count for i in status.items} == {i.name: 1 for i in INJECTIONS}
    for rel, text in read_all(hermes_source).items():
        compile(text, rel, "exec")

    patched = read_all(hermes_source)
    assert not eng.install().changed and read_all(hermes_source) == patched

    eng.uninstall()
    assert read_all(hermes_source) == original
    for rel in FILES:
        assert (hermes_source / (rel + BACKUP_SUFFIX)).read_text() == original[rel]


def test_zero_x_install_is_upgraded_in_place_and_removable(hermes_source: Path) -> None:
    """A 0.x install (same markers, bridge imported from hermes_lark_streaming.patch) is rewritten, then removable."""
    eng = Engine(hermes_source)
    original = read_all(hermes_source)
    eng.install()
    for rel in FILES:
        path = hermes_source / rel
        path.write_text(path.read_text().replace("hermes_lark_streaming.hooks.bridge", "hermes_lark_streaming.patch"))
    stale = eng.status()
    assert stale.installed and not stale.up_to_date
    eng.install()
    assert all("hermes_lark_streaming.patch" not in t for t in read_all(hermes_source).values())
    assert eng.status().up_to_date
    for rel in FILES:  # a 0.x uninstall path: the new engine strips whatever blocks are present
        path = hermes_source / rel
        path.write_text(path.read_text().replace("hermes_lark_streaming.hooks.bridge", "hermes_lark_streaming.patch"))
    eng.uninstall()
    assert read_all(hermes_source) == original


def test_current_hermes_files_survive_install_even_if_already_hooked(tmp_path: Path) -> None:
    """Straight from the live tree (possibly carrying 0.x hooks): install, then uninstall to the stripped text."""
    if not (HERMES_SOURCE / "gateway/run_turn_runner.py").is_file():
        pytest.skip("modular Hermes source required")
    root = tmp_path / "live"
    for rel in FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERMES_SOURCE / rel, root / rel)
    clean = {rel: strip_blocks(text) for rel, text in read_all(root).items()}
    eng = Engine(root)
    eng.install()
    assert eng.status().fully_installed
    eng.uninstall()
    assert read_all(root) == clean


def test_missing_anchor_in_real_source_leaves_all_files_untouched(hermes_source: Path) -> None:
    target = hermes_source / "gateway/run_turn_runner.py"
    target.write_text(target.read_text().replace("def stream_delta_cb(", "def renamed_delta_cb("))
    before = read_all(hermes_source)
    with pytest.raises(PatchError, match="ANSWER"):
        Engine(hermes_source).install()
    assert read_all(hermes_source) == before


def test_missing_cron_anchor_is_optional(hermes_source: Path) -> None:
    target = hermes_source / "cron/scheduler_delivery.py"
    target.write_text(target.read_text().replace("target_errors: list = []", "target_errors = []"))
    plan = Engine(hermes_source).install()
    assert "CRON_DELIVER" in plan.skipped
    assert "HERMES_LARK_CRON_DELIVER" not in target.read_text()
    assert Engine(hermes_source).status().fully_installed


def _callback(root: Path, rel: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((root / rel).read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def _load(root: Path, rel: str, name: str, namespace: dict[str, Any]) -> Any:
    module = ast.Module(body=[_callback(root, rel, name)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), f"<{name}>", "exec"), namespace)
    return namespace[name]


@pytest.fixture
def installed(hermes_source: Path) -> Path:
    Engine(hermes_source).install()
    return hermes_source


def test_delta_preserves_tts_without_duplicate_text(installed: Path) -> None:
    stts, native = Mock(), Mock()
    native.stream_deltas_enabled = True
    ctx = SimpleNamespace(
        event_message_id="message-1", _run_still_current=lambda: True, stream_consumer_holder=[native]
    )
    from typing import Optional

    namespace = {"ctx": ctx, "stts": stts, "delta_sinks": [native, stts], "Optional": Optional}
    callback = _load(installed, "gateway/run_turn_runner.py", "stream_delta_cb", namespace)
    with patch("hermes_lark_streaming.hooks.bridge.on_answer_delta", return_value=True) as hook:
        callback("hello")
    hook.assert_called_once_with(message_id="message-1", text="hello")
    stts.on_delta.assert_called_once_with("hello")
    native.on_delta.assert_not_called()
    assert native.stream_deltas_enabled is False


def test_delta_yields_to_native_when_card_is_unavailable(installed: Path) -> None:
    from typing import Optional

    native = Mock()
    ctx = SimpleNamespace(event_message_id="m", _run_still_current=lambda: True)
    namespace = {"ctx": ctx, "stts": None, "delta_sinks": [native], "Optional": Optional}
    callback = _load(installed, "gateway/run_turn_runner.py", "stream_delta_cb", namespace)
    with patch("hermes_lark_streaming.hooks.bridge.on_answer_delta", return_value=False):
        callback("hello")
    native.on_delta.assert_called_once_with("hello")


def test_flush_signal_reaches_native_and_tts(installed: Path) -> None:
    from typing import Optional

    native, stts = Mock(), Mock()
    ctx = SimpleNamespace(event_message_id="m", _run_still_current=lambda: True)
    namespace = {"ctx": ctx, "stts": stts, "delta_sinks": [native, stts], "Optional": Optional}
    callback = _load(installed, "gateway/run_turn_runner.py", "stream_delta_cb", namespace)
    with patch("hermes_lark_streaming.hooks.bridge.on_answer_delta") as hook:
        callback(None)
    hook.assert_not_called()
    native.on_delta.assert_called_once_with(None)
    stts.on_delta.assert_called_once_with(None)


@pytest.mark.parametrize("already_streamed", [False, True])
def test_commentary_keeps_tts_segment_boundaries(installed: Path, already_streamed: bool) -> None:
    native, stts = Mock(), Mock()
    ctx = SimpleNamespace(event_message_id="m", _run_still_current=lambda: True)
    namespace = {"ctx": ctx, "stts": stts, "stream_consumer": native}
    callback = _load(installed, "gateway/run_turn_runner.py", "interim_assistant_cb", namespace)
    with patch("hermes_lark_streaming.hooks.bridge.on_thinking_delta", return_value=True) as hook:
        callback("commentary", already_streamed=already_streamed)
    if already_streamed:
        hook.assert_not_called()
        stts.on_delta.assert_called_once_with(None)
        native.on_segment_break.assert_called_once_with()
    else:
        hook.assert_called_once_with(message_id="m", text="commentary")
        assert stts.on_delta.call_args_list == [call(None), call("commentary"), call(None)]
        native.on_commentary.assert_not_called()


def test_approval_boundary_calls_bridge(installed: Path) -> None:
    source = (installed / "gateway/run_turn_runner.py").read_text()
    assert source.count("# HERMES_LARK_APPROVAL_BEGIN") == 1
    rendered = ast.unparse(_callback(installed, "gateway/run_turn_runner.py", "_approval_notify_sync"))
    assert "on_approval_enter(message_id=self._ctx.event_message_id)" in rendered


def test_cron_hook_requires_message_id_evidence(installed: Path) -> None:
    source = (installed / "cron/scheduler_delivery.py").read_text()
    assert "_hermes_lark_cron_receipt.get('message_id')" in source
    assert "job_id=job.get('id', '')" in source
    assert "unverified_targets.append(t.where)" in source
    begin = source.index("# HERMES_LARK_CRON_DELIVER_BEGIN")
    assert source.index("_hermes_lark_cron_receipt.get('delivery_outcome')") < source.index(
        "_maybe_mirror_cron_delivery(", begin
    )
