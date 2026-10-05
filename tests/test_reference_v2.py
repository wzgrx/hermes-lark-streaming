"""Native V2 preserves audited data, budgets, expansion and V1 rollback."""
import json
from copy import deepcopy

import pytest
import yaml

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card, build_streaming_card_v2
from hermes_lark_streaming.cardkit.reference import build_reference_footer, build_reference_prefix
from hermes_lark_streaming.config import Config
from hermes_lark_streaming.footer.runtime import build_runtime_footer, runtime_actions
from hermes_lark_streaming.streaming.segments import Segment, SegmentType


def fixture():
    return {'presentation':'reference','model':'fixture-model','provider':'opencode-go',
        'requested_model':'fixture-model','response_model':'fixture-model','reasoning':'max',
        'api_mode':'chat_completions','input_tokens':150000,'output_tokens':384,
        'cache_read_tokens':99000,'cache_read_partial':True,'api_calls':3,'retries':0,
        'first_response':2.1,'duration':21.2,'context_used':49900,'context_max':1000000,
        'reference':{'design_version':2,'agent_name':'Fixture','show_tools':True,'resources_enabled':True,
            'steps':[],'host':{'scope':'WSL','gpu_percent':1,'cpu_percent':17,
                'gpu_used_gib':10.5,'gpu_total_gib':24,'ram_used_gib':15.5,'ram_total_gib':126},
            'failed_total':0,'history':{'status':'ok','show_models':True,'timezone':'Asia/Shanghai',
                'since':'2026-10-01',**{key:{'tokens':1999,'partial':True} for key in ('today','month','total')},
                'models':[{'model':'fixture-model','subscription':'fixture','tokens':1999,'partial':True}]}}}


def test_v2_layout_preserves_payload_and_reduces_footer_elements():
    data = fixture()
    before = deepcopy(data)
    footer = build_reference_footer(data)[0]
    assert data == before
    children = footer['elements']
    assert len([c for c in children if c.get('element_id')=='ref_v2_history']) == 1
    assert not any(c.get('tag')=='hr' for c in children)
    assert '≥99k / ≥66.0%' in json.dumps(footer,ensure_ascii=False)
    legacy = deepcopy(data)
    legacy['reference']['design_version'] = 1
    old = build_reference_footer(legacy)[0]
    assert inspect_card({'schema':'2.0','body':{'elements':[footer]}}).elements < inspect_card(
        {'schema':'2.0','body':{'elements':[old]}}).elements
    assert '请求\uff1d返回' not in json.dumps(footer,ensure_ascii=False)


@pytest.mark.parametrize('phase',['processing','answer','thinking','tool','waiting','compression',
    'summary_returned','summary_failed','provider_switch','request_error'])
def test_v2_live_states_keep_anchor_and_never_claim_completion(phase):
    data = fixture()
    data['runtime_phase'] = phase
    elements = build_runtime_footer(data)
    assert elements[0]['element_id']=='loading_icon'
    assert 'Answer completed' not in json.dumps(elements)
    actions = runtime_actions(elements,reference=True)
    assert '"expanded":' not in json.dumps(actions)
    assert 'ref_v2_history' in json.dumps(actions)


@pytest.mark.parametrize('is_error,is_aborted,expected',[(True,False,'本轮失败'),(False,True,'已停止')])
def test_v2_terminal_failure_and_stop_remain_visible(is_error,is_aborted,expected):
    text = json.dumps(build_reference_footer(fixture(),is_error=is_error,is_aborted=is_aborted),ensure_ascii=False)
    assert expected in text and '回答已完成' not in text


@pytest.mark.parametrize('status',['pending','unavailable','no_history'])
def test_v2_unknown_history_has_no_fabricated_zero(status):
    data = fixture()
    data['reference']['history']={'status':status}
    text = json.dumps(build_reference_footer(data),ensure_ascii=False)
    assert 'ref_v2_history' in text and '累计 0' not in text and '≥0' not in text


def test_v2_model_change_and_partial_metadata_are_not_hidden():
    data = fixture()
    data.update(response_model='returned-fixture',usage_partial=True,compression_observed=True)
    text = json.dumps(build_reference_footer(data),ensure_ascii=False)
    assert 'returned-fixture' in text and 'fixture-model' in text
    assert '不完整' in text and ' / —' in text


def test_v2_resources_are_compact_but_keep_wsl_scope_and_unknowns():
    data = fixture()
    panels = build_reference_prefix(data)
    resource = panels[1]
    text = json.dumps(resource,ensure_ascii=False)
    assert 'GPU 1%' in text and 'CPU 17%' in text and '显存 44%' in text and '内存 12%' in text
    assert 'WSL' in text and '10.5 / 24 GiB' in text
    data['reference']['host']={}
    assert '未采集' in json.dumps(build_reference_prefix(data),ensure_ascii=False)


def test_v2_account_and_history_groups_have_stable_ids_and_shallow_containers():
    data = fixture()
    data['reference']['accounts']={'status':'pending','accounts':[]}
    panel = build_reference_footer(data)[0]
    groups = [c for c in panel['elements'] if c.get('tag')=='collapsible_panel']
    assert [c['element_id'] for c in groups]==['ref_v2_history','ref_accounts']
    assert all(c['expanded'] is False for c in groups)
    def depth(value, level=0):
        if isinstance(value,dict):
            level += value.get('tag') in {'column_set','column','collapsible_panel'}
            return max([level]+[depth(v,level) for v in value.values()])
        if isinstance(value,list):
            return max([level]+[depth(v,level) for v in value])
        return level
    assert depth(panel) <= 5
    assert inspect_card({'schema':'2.0','body':{'elements':[panel]}}).safe


@pytest.mark.parametrize('layout,version',[('reference',1),('reference-v2',2),('classic',1)])
def test_v2_configuration_is_explicit_and_reversible(tmp_path,monkeypatch,layout,version):
    monkeypatch.setenv('HERMES_HOME',str(tmp_path))
    (tmp_path/'config.yaml').write_text(yaml.safe_dump({'streaming':{'layout':layout,'footer':{'mode':'enhanced'}}}))
    cfg = Config()
    assert cfg.reference_design_version == version
    assert cfg.card_layout == ('classic' if layout=='classic' else 'reference')


def test_v2_whole_cards_keep_body_and_v1_panel_ids():
    data = fixture()
    answer = Segment(SegmentType.ANSWER,'answer')
    answer.text='V2 ANSWER retained'
    for card in (build_streaming_card_v2(footer_data=data),build_complete_card(segments=[answer],
        all_tool_steps=[],footer_data=data,footer_mode='enhanced')):
        ids=[e.get('element_id') for e in card['body']['elements']]
        assert 'reference_tools' in ids and 'reference_resources' in ids and 'footer_details' in ids
        assert inspect_card(card).safe
    assert 'V2 ANSWER retained' in json.dumps(card)
    assert 'Fixture · V2' in json.dumps(card,ensure_ascii=False)


def test_v2_worst_retained_tool_account_and_history_card_fits_budget():
    data = fixture()
    data['reference']['history']['models'] *= 3
    data['reference']['accounts']={'accounts':[{'id':f'fixture-{i}','label':'Fixture '+str(i),
        'provider':'opencode-go','status':'unavailable'} for i in range(4)]}
    data['reference']['failed_total']=128
    data['reference']['steps']=[{'name':'command','title':'Command (1.0 s)','status':'error',
        'detail':'escaped <&> '+('details '*100),'error':'failure <&> '+('error '*200),
        'output':'','elapsed_ms':1000,'error_block':None,'result_block':None} for _ in range(128)]
    answer = Segment(SegmentType.ANSWER,'answer')
    answer.text='Bounded acceptance fixture'
    card=build_complete_card(segments=[answer],all_tool_steps=data['reference']['steps'],
        footer_data=data,footer_mode='enhanced')
    assert inspect_card(card).safe
    assert 'red-50' in json.dumps(card)



@pytest.mark.parametrize('value,expected',[
    (2.50862,'2.5%'), (17,'17%'), (17.12345,'17.1%'), (99.96,'100%'),
    (0,'0%'), (None,None), (-1,None), (float('nan'),None),
    (float('inf'),None), (True,None), ('2.50862',None),
])
def test_v2_collapsed_resource_precision_matches_expanded_metric(value,expected):
    data = fixture()
    data['reference']['host']['cpu_percent'] = value
    before = deepcopy(data)
    resource = build_reference_prefix(data)[1]
    titles = [resource['header']['title']['content'],
              resource['header']['title']['i18n_content']['zh_cn']]
    assert data == before
    if expected is None:
        assert all('CPU ' not in title for title in titles)
    else:
        assert all('CPU '+expected in title for title in titles)
        assert expected in json.dumps(resource['elements'])
    assert '2.50862%' not in json.dumps(resource)
