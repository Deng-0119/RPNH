"""Real installed catalogs/upstream; lifecycle doubles are explicitly unit scope."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from cpn.plugins import ManagedPluginToolCatalog
from cpn.plugins.catalog import load_catalog
from cpn.plugins.api import json_copy
from rpnh_ab.broker import Broker
from rpnh_ab.io import load, sha, write_new
from rpnh_ab.plugin import bindings, configuration
from rpnh_ab.native import build_spec
from rpnh_ab.run_spec import (benchmark_spec, execution_spec, acceptance_identity,
                             validate_acceptance, write_launch, load_launch, REQUIRED_ACCEPTANCE)
from offline_support.fixtures import synthetic_row,tool_steps,create_profile

@pytest.fixture(scope='module')
def upstream():
    path=os.environ.get('RPNH_AB_UPSTREAM')
    if not path: pytest.skip('set RPNH_AB_UPSTREAM to real upstream')
    from rpnh_ab.upstream import Upstream
    return Upstream(Path(path))

def test_real_upstream_three_tools_world_scoring_without_ipc(upstream,tmp_path):
    row=synthetic_row(); direct=upstream.start(row); state=upstream.start(row)
    broker=Broker(upstream,state,tmp_path,'offline-direct-component')
    results=[]
    for i,step in enumerate(tool_steps()):
        expected=upstream.dispatch(direct,step['tool'],step['arguments'])
        request={'run_id':broker.run_id,'tool':step['tool'],'operation_id':'ab_api/'+step['tool'],
                 'invocation_id':'component-inv','firing_id':'component-fire','call_id':str(i),
                 'arguments':step['arguments']}
        response=broker.call(request)
        assert response['ok'] and response['result']==expected
        assert broker.call(request)==response # no duplicate write
        results.append(response)
    assert upstream.dump_world(state)==upstream.dump_world(direct)
    a=upstream.score(upstream.dump_world(state),state['initial_state'],state['info'])
    b=upstream.score(upstream.dump_world(direct),direct['initial_state'],direct['info'])
    assert a==b and a['task_completed_correctly']==1.0
    assert len(state['world'].gmail.messages)==1
    write_new(tmp_path/'component-evidence.json',{'tool_results':results,'rubric':a,
              'real_upstream':True,'actual_host_admission':False,'real_provider_calls':0})


def test_real_scope_gate_and_empty_dict_sentinel(upstream):
    state=upstream.start(synthetic_row())
    a=upstream.dispatch(state,'api_fetch',{'method':'GET','url':'https://gmail.googleapis.com/gmail/v1/users/me/messages','params':{},'body':{}})
    assert 'error' not in json.loads(a)
    blocked=upstream.dispatch(state,'api_fetch',{'method':'GET','url':'https://slack.com/api/conversations.list','params':None,'body':None})
    assert 'error' in json.loads(blocked)


def test_real_trello_add_label_scalar_reaches_pinned_route(upstream):
    row = {
        'prompt': [{'role': 'user', 'content': 'Local no-model contract test.'}],
        'info': {
            'initial_state': {'meta': {'allowed_services': ['trello']}, 'trello': {}},
            'assertions': [],
            'zapier_tools': [],
        },
    }
    state = upstream.start(row)
    result = upstream.dispatch(state, 'api_fetch', {
        'method': 'POST',
        'url': 'https://api.trello.com/1/cards/card_455/idLabels',
        'params': 'null',
        'body': '"lbl_vendor_hold"',
    })
    assert isinstance(json.loads(result), list)
    actions = upstream.dump_world(state)['trello']['actions']
    assert actions['card_label'][0]['params']['label'] == 'lbl_vendor_hold'


def test_real_catalog_schema_effect_and_native_spec(upstream,tmp_path):
    config=configuration('/tmp/not-running.sock','offline')
    catalog=load_catalog(config)
    binding=bindings(upstream.schemas)['executor']
    managed=ManagedPluginToolCatalog(catalog,binding['tools'],admitted_effects=tuple(binding['admitted_effects']))
    for row in upstream.schemas:
        f=row['function']; declaration=managed.declaration(f['name'])
        assert json_copy(declaration.input_schema)==f['parameters']
        assert declaration.description==f['description']
    assert managed.declaration('api_fetch').effect=='external_write'
    with pytest.raises(Exception):
        ManagedPluginToolCatalog(catalog,binding['tools'],admitted_effects=('pure',))
    profile=create_profile(tmp_path/'profile',tool_steps())
    spec=build_spec(tmp_path/'run',profile,'/tmp/not-running.sock','offline',synthetic_row()['prompt'],upstream.schemas)
    assert spec.max_attempts_per_stage is None
    assert json_copy(spec.managed_bindings)==bindings(upstream.schemas)
    assert spec.plugin_catalog_digest==catalog.digest


def frozen(tmp_path,upstream,host='native'):
    profile=create_profile(tmp_path/'profile',tool_steps())
    plan={'upstream_commit':upstream.identity['commit'],'split':'synthetic','manifest_sha256':sha(['task']),
          'tasks':[{'task_contract_sha256':sha(synthetic_row())}]}
    benchmark=benchmark_spec(plan,upstream.schemas,business_mode='offline_synthetic')
    from rpnh_ab.experiment import installed_rpnh_identity
    execution=execution_spec(profile,host,installed_rpnh_identity())
    work=tmp_path/'work';work.mkdir()
    write_new(work/'conditions.json',{'benchmark_spec':benchmark,'execution_spec':execution})
    return work,profile,benchmark,execution


def test_benchmark_identity_independent_of_frontend_and_host(tmp_path,upstream):
    work,profile,b,e=frozen(tmp_path,upstream)
    d=execution_spec(profile,'dsh',{'commit':'test-real-catalog'})
    assert sha(e)!=sha(d)
    assert not any(k in b for k in ('frontend','pid','socket','endpoint','run_nonce'))
    with pytest.raises(ValueError,match='executor_host'):
        execution_spec(profile,'codex',{})


def test_acceptance_rejects_header_only_stale_missing_and_modified_proof(tmp_path,upstream):
    _,_,b,e=frozen(tmp_path,upstream)
    path=tmp_path/'acceptance.json'
    path.write_text(json.dumps({'status':'passed'}))
    with pytest.raises(ValueError,match='schema'): validate_acceptance(path,b,e)
    proof=tmp_path/'proof.log';proof.write_text('component-only is not host proof')
    record={'schema':'rpnh-ab/acceptance-manifest/v1','identity':acceptance_identity(b,e),
            'real_provider_calls':0,'real_business_api_calls':0,'cases':{}}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='missing or blocked'): validate_acceptance(path,b,e)
    from rpnh_ab.io import file_sha
    for key in REQUIRED_ACCEPTANCE:
        record['cases'][key]={'status':'passed','evidence_path':str(proof),'evidence_sha256':file_sha(proof)}
    path.write_text(json.dumps(record));validate_acceptance(path,b,e) # unit-only gate data
    proof.write_text('changed')
    with pytest.raises(ValueError,match='evidence'):validate_acceptance(path,b,e)
    changed=copy.deepcopy(e);changed['executor_host']='dsh'
    with pytest.raises(ValueError,match='stale'):validate_acceptance(path,b,changed)


def test_launch_tamper_and_profile_drift_rejected(tmp_path,upstream):
    work,profile,b,e=frozen(tmp_path,upstream)
    launch=tmp_path/'launch.json'
    write_launch(launch,work=work,upstream=upstream.root,profile=profile,acceptance=tmp_path/'unaccepted.json')
    value=load_launch(launch,require_acceptance=False)
    value['executor_host']='dsh';launch.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='digest'):load_launch(launch,require_acceptance=False)


def test_unadmitted_attempt_is_retained_unscored_and_blocks_batch(tmp_path,upstream,monkeypatch):
    import rpnh_ab.experiment as exp
    class StartupFailure:
        def __init__(self,*a,**kw):self.closed=False
        def __enter__(self):raise RuntimeError('specific admission failure')
        def __exit__(self,*a):pass
    monkeypatch.setattr(exp,'Broker',StartupFailure)
    case={'id':'synthetic-1','task_name':'offline','domain':'synthetic','task_contract_sha256':sha(synthetic_row()),'row':synthetic_row()}
    attempt=tmp_path/'attempt'
    assert exp.one_attempt(upstream,case,attempt,create_profile(tmp_path/'profile',[])) is True
    life=load(attempt/'lifecycle.json')
    assert life['execution_status']=='configuration_blocked'
    assert life['admitted'] is False
    assert not (attempt/'final_world.json').exists()
    assert not list(attempt.glob('score-*.json'))


def test_observation_only_projection_gap_remains_nonblocking(tmp_path,upstream,monkeypatch):
    import rpnh_ab.experiment as exp
    import rpnh_ab.drivers
    class BrokerComponent:
        def __init__(self,*a,**kw):self.closed=False
        def __enter__(self):return self
        def __exit__(self,*a):self.closed=True
    class DriverComponent:
        def run(self,**kw):
            kw['run_dir'].mkdir(parents=True)
            return {'admitted':True,'host_quiescent':True,'manual_stop':False,'terminal':{'outcome':'complete'}}
        def project(self,*a):raise ValueError('unsupported optional projector version')
    monkeypatch.setattr(exp,'Broker',BrokerComponent)
    monkeypatch.setattr(rpnh_ab.drivers,'get_driver',lambda host:DriverComponent())
    case={'id':'synthetic-1','task_name':'offline','domain':'synthetic','task_contract_sha256':sha(synthetic_row()),'row':synthetic_row()}
    attempt=tmp_path/'attempt'
    assert exp.one_attempt(upstream,case,attempt,create_profile(tmp_path/'profile',[])) is False
    assert load(attempt/'native_evidence.json')['status']=='projection_error'
    assert load(attempt/'score-0001.json')['task_completed_correctly']==0.0


def test_manual_stop_is_retained_but_never_frozen_or_scored(tmp_path,upstream,monkeypatch):
    import rpnh_ab.experiment as exp
    import rpnh_ab.drivers
    class BrokerComponent:
        def __init__(self,*a,**kw):self.closed=False
        def __enter__(self):return self
        def __exit__(self,*a):self.closed=True
    class DriverComponent:
        def run(self,**kw):
            kw['run_dir'].mkdir(parents=True)
            return {'admitted':True,'host_quiescent':True,'manual_stop':True,'terminal':None}
        def project(self,*a):return {'status':'manual-stop-retained'}
    monkeypatch.setattr(exp,'Broker',BrokerComponent)
    monkeypatch.setattr(rpnh_ab.drivers,'get_driver',lambda host:DriverComponent())
    case={'id':'synthetic-1','task_name':'offline','domain':'synthetic','task_contract_sha256':sha(synthetic_row()),'row':synthetic_row()}
    attempt=tmp_path/'attempt'
    assert exp.one_attempt(upstream,case,attempt,create_profile(tmp_path/'profile',[])) is True
    life=load(attempt/'lifecycle.json')
    assert life['execution_status']=='manual_stop'
    assert life['world_owner_quiescent'] is False
    assert not (attempt/'final_world.json').exists()
    assert not list(attempt.glob('score-*.json'))

@pytest.mark.parametrize('result,admitted,quiet,terminal',[
    ({'registry_path':'/exists','host_quiescent':True,'process_quiescent':True,'outcome':None,'admitted':False},False,False,False),
    ({'registry_path':'/exists','host_quiescent':True,'process_quiescent':True,'outcome':{'status':'terminal','answer':'ok'},'admitted':True},True,True,True),
    ({'registry_path':'/exists','host_quiescent':True,'process_quiescent':True,'outcome':{'status':'terminal'},'admitted':False},False,False,True),
    ({'registry_path':'/exists','host_quiescent':False,'process_quiescent':True,'outcome':{'status':'terminal'},'admitted':True},True,False,True),
])
def test_dsh_result_mapping_cannot_turn_startup_failure_into_score(tmp_path,result,admitted,quiet,terminal):
    from rpnh_ab.drivers.dsh import lifecycle_from_result
    life=lifecycle_from_result(result,run_dir=tmp_path,session='offline',return_code=0)
    assert life['admitted'] is admitted
    assert life['host_quiescent'] is quiet
    assert bool(life['terminal']) is terminal


def test_real_final_dsh_blocker_is_not_admission_if_available():
    path=os.environ.get('RPNH_AB_DSH_SMOKE_RESULT')
    if not path:pytest.skip('set RPNH_AB_DSH_SMOKE_RESULT to genuine launcher evidence')
    from rpnh_ab.drivers.dsh import lifecycle_from_result
    result=load(Path(path))
    assert result['execution_status']=='configuration_blocked'
    life=lifecycle_from_result(result,run_dir=Path(path).parent,session=result['session_id'],return_code=0)
    assert life['admitted'] is False and life['host_quiescent'] is False and life['terminal'] is None


def test_core_runtime_identity_hash_covers_already_dirty_source():
    from rpnh_ab.constants import RPNH_REVIEWED_COMMIT
    from rpnh_ab.experiment import installed_rpnh_identity
    value=installed_rpnh_identity()
    assert len(value['runtime_source_sha256'])==64
    assert value['reviewed_commit']==RPNH_REVIEWED_COMMIT
    assert isinstance(value['same_as_reviewed_commit'],bool)
    assert len(value['commit'])==40
