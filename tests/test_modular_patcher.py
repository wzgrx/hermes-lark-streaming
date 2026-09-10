"""Real modular source roundtrips and execution of migrated callbacks."""
import ast
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import Mock, patch, call
from typing import Optional

import pytest

from hermes_lark_streaming.patcher import Patcher, CronPatcher, PatcherError


@pytest.fixture
def modular_root(tmp_path):
    source = Path(os.environ.get('HERMES_MODULAR_SOURCE', Path.home() / '.hermes/hermes-agent'))
    if not (source / 'gateway/run_turn_runner.py').is_file():
        pytest.skip('Modular Hermes source required')
    for folder, names in {
        'gateway': ['run.py', 'run_inbound.py', 'run_turn.py', 'run_turn_runner.py', 'run_busy.py'],
        'cron': ['scheduler.py', 'scheduler_delivery.py'],
    }.items():
        (tmp_path / folder).mkdir()
        for name in names:
            shutil.copy2(source / folder / name, tmp_path / folder / name)
    for obj in (Patcher(tmp_path / 'gateway/run.py'), CronPatcher(tmp_path / 'cron/scheduler.py')):
        obj.remove()
    return tmp_path


@pytest.mark.parametrize('kind', ['gateway', 'cron'])
def test_roundtrip_and_idempotence(modular_root, kind):
    obj = Patcher(modular_root / 'gateway/run.py') if kind == 'gateway' else CronPatcher(modular_root / 'cron/scheduler.py')
    original = obj._contents()
    obj.verify_target()
    assert obj._contents() == original
    obj.apply()
    assert obj.is_fully_patched()
    patched = obj._contents()
    obj.apply()
    assert obj._contents() == patched
    obj.restore()
    assert obj._contents() == original


def test_missing_anchor_leaves_all_files_untouched(modular_root):
    target = modular_root / 'gateway/run_turn_runner.py'
    target.write_text(target.read_text().replace('def stream_delta_cb(', 'def renamed_delta_cb('))
    obj = Patcher(modular_root / 'gateway/run.py')
    before = obj._contents()
    with pytest.raises(PatcherError):
        obj.apply()
    assert obj._contents() == before


def test_modular_delta_preserves_tts_without_duplicate_text(modular_root):
    obj = Patcher(modular_root / 'gateway/run.py')
    obj.apply()
    tree = ast.parse((modular_root / 'gateway/run_turn_runner.py').read_text())
    callback = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'stream_delta_cb')
    module = ast.Module(body=[callback], type_ignores=[])
    stts, native = Mock(), Mock()
    ctx = SimpleNamespace(event_message_id='message-1', _run_still_current=lambda: True)
    namespace = {'ctx': ctx, 'stts': stts, 'delta_sinks': [native, stts], 'Optional': Optional}
    exec(compile(ast.fix_missing_locations(module), '<modular-delta>', 'exec'), namespace)
    with patch('hermes_lark_streaming.patch.on_answer_delta', return_value=True) as hook:
        namespace['stream_delta_cb']('hello')
    hook.assert_called_once_with(message_id='message-1', text='hello')
    stts.on_delta.assert_called_once_with('hello')
    native.on_delta.assert_not_called()


def test_modular_delta_yields_to_native_when_card_is_unavailable(modular_root):
    obj = Patcher(modular_root / 'gateway/run.py'); obj.apply()
    tree = ast.parse((modular_root / 'gateway/run_turn_runner.py').read_text())
    callback = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'stream_delta_cb')
    native = Mock()
    namespace = {'ctx': SimpleNamespace(event_message_id='m', _run_still_current=lambda: True),
                 'stts': None, 'delta_sinks': [native], 'Optional': Optional}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[])), '<delta>', 'exec'), namespace)
    with patch('hermes_lark_streaming.patch.on_answer_delta', return_value=False):
        namespace['stream_delta_cb']('hello')
    native.on_delta.assert_called_once_with('hello')


def test_modular_flush_signal_reaches_native_and_tts(modular_root):
    obj = Patcher(modular_root / 'gateway/run.py'); obj.apply()
    tree = ast.parse((modular_root / 'gateway/run_turn_runner.py').read_text())
    callback = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'stream_delta_cb')
    native, stts = Mock(), Mock()
    namespace = {'ctx': SimpleNamespace(event_message_id='m', _run_still_current=lambda: True),
                 'stts': stts, 'delta_sinks': [native, stts], 'Optional': Optional}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[])), '<flush>', 'exec'), namespace)
    with patch('hermes_lark_streaming.patch.on_answer_delta') as hook:
        namespace['stream_delta_cb'](None)
    hook.assert_not_called()
    native.on_delta.assert_called_once_with(None)
    stts.on_delta.assert_called_once_with(None)


@pytest.mark.parametrize('already_streamed', [False, True])
def test_modular_commentary_keeps_tts_segment_boundaries(modular_root, already_streamed):
    obj = Patcher(modular_root / 'gateway/run.py'); obj.apply()
    tree = ast.parse((modular_root / 'gateway/run_turn_runner.py').read_text())
    callback = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'interim_assistant_cb')
    native, stts = Mock(), Mock()
    namespace = {'ctx': SimpleNamespace(event_message_id='m', _run_still_current=lambda: True),
                 'stts': stts, 'stream_consumer': native}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[])), '<commentary>', 'exec'), namespace)
    with patch('hermes_lark_streaming.patch.on_thinking_delta', return_value=True) as hook:
        namespace['interim_assistant_cb']('commentary', already_streamed=already_streamed)
    if already_streamed:
        hook.assert_not_called()
        stts.on_delta.assert_called_once_with(None)
        native.on_segment_break.assert_called_once_with()
    else:
        hook.assert_called_once_with(message_id='m', text='commentary')
        assert stts.on_delta.call_args_list == [call(None), call('commentary'), call(None)]
        native.on_commentary.assert_not_called()
