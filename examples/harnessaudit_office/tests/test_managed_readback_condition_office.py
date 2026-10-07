"""Explicit new reader/prompt identity and readonly archived-v1 compatibility."""
import copy
import pytest
from rpnh_ha import comparison_condition as cc
from rpnh_ha.jsonio import write_new
from test_configuration_condition import baseline_public,config


def plan(condition):
    settings=config();settings.update(configuration_condition=condition,condition_id=condition)
    return cc.plan_condition(baseline_public().as_dict(),settings),settings


def saved(root,document,settings,implementation=None):
    root.mkdir()
    manifest=copy.deepcopy(document['condition_manifest'])
    if implementation is not None:manifest['implementation']=copy.deepcopy(implementation)
    protocol_manifest=copy.deepcopy(manifest);protocol_manifest['bindings_sha256']=None
    write_new(root/'public_input.json',document['public_input'])
    write_new(root/'protocol.json',{'configuration_condition':settings['configuration_condition'],
        'condition_id':settings['condition_id'],'limits':settings['limits_per_run'],'condition_manifest':protocol_manifest})
    write_new(root/'run_status.json',{'configuration_condition':settings['configuration_condition'],'condition_id':settings['condition_id']})
    write_new(root/'configuration_condition.json',manifest)
    return manifest


def test_new_graph_reader_and_prompt_have_explicit_distinct_identity(tmp_path):
    old,old_config=plan(cc.CONDITION_ID);new,new_config=plan(cc.READBACK_CONDITION_ID)
    assert all('read_managed_output' not in n['execution']['tools'] for n in old['graph']['nodes'])
    assert all('read_managed_output' in n['execution']['tools'] for n in new['graph']['nodes'])
    assert new['condition_manifest']['condition_id']==cc.READBACK_CONDITION_ID
    for key in ['graph_sha256','prompt_sha256','prompt_revision','workflow_revision']:
        assert old['condition_manifest'][key]!=new['condition_manifest'][key]
    assert all('confirmed subsequent model-input consumption' not in n['instruction'] for n in new['graph']['nodes'])
    saved(tmp_path/'v2',new,new_config)
    assert cc.saved_condition_record(tmp_path/'v2')['condition_id']==cc.READBACK_CONDITION_ID


def test_archived_v1_identity_is_retained_for_readonly_saved_inspection(tmp_path):
    document,settings=plan(cc.CONDITION_ID)
    saved(tmp_path/'v1',document,settings,cc.HISTORICAL_V1_IMPLEMENTATION)
    record=cc.saved_condition_record(tmp_path/'v1')
    assert record['condition_manifest']['implementation']==cc.HISTORICAL_V1_IMPLEMENTATION
    assert document['condition_manifest']['implementation']==cc.implementation_identity()


def test_old_or_new_binding_digest_is_never_accepted_without_reconstruction(tmp_path):
    document,settings=plan(cc.READBACK_CONDITION_ID)
    launch=saved(tmp_path/'wrong',document,settings)
    launch['bindings_sha256']='0'*64
    (tmp_path/'wrong/configuration_condition.json').write_text(cc.dumps(launch))
    with pytest.raises(ValueError,match='disagree'):cc.saved_condition_record(tmp_path/'wrong')


def test_v2_sidecar_keeps_projection_submission_and_semantic_use_separate():
    observations=[{'surface':'tool_call','agent_id':'verify_1','raw_event':{'rpnh_phase':'returned',
        'model_visible_result':{'kind':'acknowledged_registered_llm_prompt_result/v1','evidence':['registered-ref']}},
        'data':{'tool_name':'search_knowledge_base','tool_result':'{}'}}]
    report=cc.comparison_evidence_report(observations,state_snapshot_available=True,condition_id=cc.READBACK_CONDITION_ID)
    assert report['condition_id']==cc.READBACK_CONDITION_ID
    row=report['tool_evidence'][0]
    assert row['registered_request_projection_available'] is True
    assert row['provider_submission']==row['semantic_use']=='unknown'
    assert 'model_consumption_confirmed' not in row
