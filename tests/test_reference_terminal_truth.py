"""Completed answers must not leave orphan live indicators or leak cut secrets."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card, build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import _tool_text, build_reference_footer, build_tools
from tests.test_reference_readability import step


def fixture_data():
    return {"presentation":"reference", "model":"example-model", "reference":{
        "show_tools":True, "steps":[step(), step("running")],
        "failed_total":0, "succeeded_total":1}}


@pytest.mark.parametrize("flags", [{}, {"is_error":True}, {"is_aborted":True}])
@pytest.mark.parametrize("footer_enabled", [True, False])
def test_every_terminal_card_uses_unconfirmed_for_missing_results(flags, footer_enabled):
    data=fixture_data()
    before=deepcopy(data)
    card=build_complete_card(segments=[], all_tool_steps=data['reference']['steps'],
                             footer_data=data, footer_mode="enhanced", footer_enabled=footer_enabled, **flags)
    panel=next(e for e in card['body']['elements'] if e.get('element_id')=='reference_tools')
    text=json.dumps(panel,ensure_ascii=False)
    assert '1/2 ended' in panel['header']['title']['content']
    assert '1 unconfirmed' in panel['header']['title']['content']
    assert 'Running' not in text and '运行中' not in text
    assert 'does not confirm that a background process stopped' in text
    assert 'Unconfirmed' in text and '结果未确认' in text
    assert data==before and inspect_card(card).safe


def test_successful_footer_agrees_with_unconfirmed_tool_panel():
    panel=build_reference_footer(fixture_data())[0]
    text=json.dumps(panel,ensure_ascii=False)
    assert 'Answer completed' in text
    assert '1 succeeded / 0 failed / 1 unconfirmed' in text
    assert '1 结果未确认' in text


def test_streaming_still_shows_running_and_no_terminal_warning():
    card=build_streaming_card_v2(footer_data=fixture_data())
    panel=next(e for e in card['body']['elements'] if e.get('element_id')=='reference_tools')
    text=json.dumps(panel,ensure_ascii=False)
    assert '1 running' in text and 'Running' in text
    assert 'unconfirmed' not in text and 'Turn ended' not in text


@pytest.mark.parametrize('template', ['--password "{}"', "TOKEN='{}'", '{{"api_key":"{}"}}'])
@pytest.mark.parametrize('limit', [64,100,180,200])
def test_quoted_secret_is_redacted_before_byte_excerpt(template,limit):
    secret='SYNTHETIC_CANARY '+('secret with spaces '*100)
    text=_tool_text(template.format(secret),limit)
    assert 'SYNTHETIC_CANARY' not in text and 'secret with' not in text
    assert 'redacted' in text
    assert len(text.encode())<=limit+3


def test_terminal_long_failed_tools_remain_in_native_card_budget():
    steps=[step('running')]+[step('error') for _ in range(12)]
    for item in steps:
        item.update(detail='待确认命令 '*100, error='很长的诊断原因 '*100)
    card=build_complete_card(segments=[],all_tool_steps=steps,footer_mode='enhanced',
        footer_data={'presentation':'reference','reference':{'show_tools':True,'steps':steps,
                     'failed_total':12,'succeeded_total':0}})
    assert inspect_card(card).safe


def test_visible_rows_and_excerpts_both_use_redacted_long_secret():
    item=step('error')
    item.update(name='custom_tool',title='Custom',detail='--password "SYNTHETIC_CANARY '+('x '*400)+'"',
                error='{"api_key":"SYNTHETIC_CANARY '+('x '*400)+'"}')
    panel=build_tools({}, {'steps':[item]})
    text=json.dumps(panel,ensure_ascii=False)
    assert 'SYNTHETIC_CANARY' not in text and 'redacted' in text


@pytest.mark.asyncio
async def test_actual_rollover_seal_preserves_live_tool_snapshot():
    from tests.test_controller import _make_session, _setup_ctrl

    ctrl=_setup_ctrl()
    session=_make_session('synthetic-rollover')
    session.set_card(card_id='old-card',card_msg_id='old-reply')
    with patch.object(ctrl,'_reference_snapshot',return_value=fixture_data()):
        await ctrl._seal_current_card(session,[])
    card=ctrl._client.cardkit_update.await_args.args[1]
    text=json.dumps(card,ensure_ascii=False)
    assert '1 running' in text and '运行中' in text
    assert 'Unconfirmed' not in text and 'Turn ended' not in text
