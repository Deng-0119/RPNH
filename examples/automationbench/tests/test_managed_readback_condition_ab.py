"""Condition-bound synthetic adapter checks; no upstream or provider calls."""
import json

from rpnh_ab import configuration_condition as cc
from rpnh_ab.broker import Broker
from rpnh_ab.native import graph_for
from test_configuration_condition import upstream, request, search_result


def test_readback_condition_exposes_reader_and_preserves_v1_graph(tmp_path):
    up=upstream(tmp_path)
    original=cc.selected(up)
    old_graph,_=graph_for([],configuration_condition=original)
    selected=cc.configure(up,cc.READBACK_CONDITION)
    graph,_=graph_for([],configuration_condition=selected)
    assert selected['id']==cc.READBACK_CONDITION and selected!=original
    assert old_graph.nodes[0].execution.tools==('complete_interaction','read_file','write_file')
    assert graph.nodes[0].execution.tools==('complete_interaction','read_file','read_managed_output','write_file')
    assert graph.nodes[0].instruction!=old_graph.nodes[0].instruction
    cc.configure(up,cc.CONDITION)
    assert graph_for([],configuration_condition=cc.selected(up))[0].to_dict()==old_graph.to_dict()


def test_bound_overlay_feedback_and_corrected_dispatch_in_new_condition(tmp_path):
    up=upstream(tmp_path)
    cc.configure(up,cc.READBACK_CONDITION)
    broker=Broker(up,{},tmp_path,'synthetic')
    response=broker.call(request('api_search',query='public fields'))
    endpoints=json.loads(response['result'])['results']
    assert 'HealthStatus' in endpoints[1]['request']
    assert 'no grid cell data' in endpoints[2]['description']
    assert "sheet-qualified" in endpoints[4]['description'] or 'each needed sheet' in endpoints[4]['description']
    assert len(up.calls)==1
    invalid=broker.call(request(call='invalid',method='PATCH',
        url='https://demo.salesforce.com/services/data/v61.0/sobjects/Account/a',body='[]'))
    feedback=json.loads(invalid['result'])
    assert invalid['ok'] is True and feedback['error']['stage']=='pre_dispatch' and len(up.calls)==1
    assert feedback['error']['upstream_dispatched'] is False
    assert feedback['error']['effect_status']=='not_started'
    corrected=broker.call(request(call='corrected',method='PATCH',
        url='https://demo.salesforce.com/services/data/v61.0/sobjects/Account/a',body='{"Tier":"public"}'))
    assert corrected['ok'] is True and len(up.calls)==2
    assert cc.selected(up)['id']==cc.READBACK_CONDITION
    assert json.loads(search_result())['results'][1]['request']=='Original request'
