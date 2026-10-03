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
from rpnh_ab.plugin import bindings, configuration, factory
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


def test_dsh_identity_normalizes_clean_and_exact_prepared_seam(
        tmp_path, monkeypatch,
):
    import subprocess
    import rpnh_ab.upstream as upstream_module
    from integrations.dsh.patch_upstream import FACTORY_PATH, prepared_source
    from rpnh_ab.run_spec import dsh_identity

    checkout = tmp_path / 'dsh'
    target = checkout / FACTORY_PATH
    target.parent.mkdir(parents=True)
    clean = "\n".join((
        "import { ReactLoopAgent } from './react.js'",
        "interface PreparedAgent {",
        "  agent: ReactLoopAgent",
        "}",
        "let machine: ReactLoopAgent | undefined",
        "const loopCtx = this.runtime.ctx",
        "machine = new ReactLoopAgent(loopCtx, id, options, session)",
        "  constructor(ctx: Context, config: Config) {",
    ))
    target.write_text(clean)
    revision = 'ddefc45fbc7f8e46dd73185e68295696d1297887'
    monkeypatch.setattr(upstream_module, 'git_identity',
                        lambda _root: {'commit': revision, 'tracked_changes': ''})

    def git_output(command, text=False):
        if 'diff' in command:
            changed = target.read_text() != clean
            value = (FACTORY_PATH + '\0').encode() if changed else b''
        elif 'show' in command:
            return clean if text else clean.encode()
        elif 'ls-files' in command:
            value = (FACTORY_PATH + '\0').encode()
        else:
            raise AssertionError(command)
        return value.decode() if text else value

    monkeypatch.setattr(subprocess, 'check_output', git_output)
    clean_identity = dsh_identity(checkout)
    prepared, changed = prepared_source(clean)
    assert changed is True
    target.write_text(prepared)
    assert dsh_identity(checkout) == clean_identity
    target.write_text(prepared + '\n// unrelated change\n')
    with pytest.raises(ValueError, match='neither clean nor exactly prepared'):
        dsh_identity(checkout)


def test_acceptance_rejects_header_only_stale_missing_and_modified_proof(tmp_path,upstream):
    _,_,b,e=frozen(tmp_path,upstream)
    path=tmp_path/'acceptance.json'
    path.write_text(json.dumps({'status':'passed'}))
    with pytest.raises(ValueError,match='schema'): validate_acceptance(path,b,e)
    proof=tmp_path/'proof.log';proof.write_text('component-only is not host proof')
    record={'schema':'rpnh-ab/acceptance-manifest/v1','identity':acceptance_identity(b,e),
            'real_provider_calls':0,'real_business_api_calls':0,
            'historical_benchmark_tasks_executed':0,'synthetic_acceptance_only':True,
            'cases':{}}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='missing or blocked'): validate_acceptance(path,b,e)
    from rpnh_ab.io import file_sha
    for key in REQUIRED_ACCEPTANCE:
        record['cases'][key]={'status':'passed','evidence_path':str(proof),'evidence_sha256':file_sha(proof)}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='evidence'):
        validate_acceptance(path,b,e)
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


def _dsh_schemas():
    parameters = {
        'api_search': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'query': {'type': 'string'},
                'top_k': {'type': 'integer', 'minimum': 1},
            },
            'required': ['query'],
        },
        'api_fetch': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'method': {'type': 'string'}, 'url': {'type': 'string'},
                'params': {}, 'body': {},
            },
            'required': ['method', 'url'],
        },
        'base64_encode': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'text': {'type': 'string'}},
            'required': ['text'],
        },
    }
    return [{'type': 'function', 'function': {
        'name': name, 'description': f'Upstream AutomationBench {name}',
        'parameters': parameters[name],
    }} for name in ('api_search', 'api_fetch', 'base64_encode')]


def test_dsh_backend_catalog_uses_complete_automationbench_binding(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog
    from cpn.dsh.backend import DshBackend
    from cpn.llm_adapters import load_llm_execution_selection

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(name='ab_api', load=lambda: factory)])
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps(
        configuration('/tmp/not-running.sock', 'catalog-test')))
    selection_path = create_profile(tmp_path / 'profile', [])
    selected = bindings(_dsh_schemas())['executor']

    backend = DshBackend(
        tmp_path / 'registry', 'catalog-test',
        lambda _request: pytest.fail('catalog construction must not dispatch'),
        create=True, offline=False,
        selection=load_llm_execution_selection(selection_path),
        plugin_config_path=plugin_config,
        managed_tools=selected['tools'],
        managed_tool_admitted_effects=selected['admitted_effects'])

    for schema in _dsh_schemas():
        function = schema['function']
        declaration = backend.managed_tools.declaration(function['name'])
        assert declaration.description == function['description']
        assert json_copy(declaration.input_schema) == function['parameters']
    assert backend.managed_tools.declaration('api_fetch').effect == 'external_write'

    with pytest.raises(Exception, match='effect was not explicitly admitted'):
        DshBackend(
            tmp_path / 'pure-registry', 'catalog-test',
            lambda _request: pytest.fail('catalog construction must not dispatch'),
            create=True, offline=False,
            selection=load_llm_execution_selection(selection_path),
            plugin_config_path=plugin_config,
            managed_tools=selected['tools'])


def test_dsh_driver_uses_console_history_and_writes_durable_result(
        tmp_path, monkeypatch,
):
    import rpnh_ab.drivers.dsh as driver_module

    commands = []

    class FinishedProcess:
        pid = 43210

        def __init__(self, command, *, stdout, stderr, start_new_session):
            commands.append(command)
            assert stderr is driver_module.subprocess.STDOUT
            assert start_new_session is True
            stdout.write(b'RPNH session: session-automationbench\n')
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

    ref = {'entity_type': 'turn/v1', 'entity_id': 'turn-1',
           'version_id': 'turn-version-1'}
    history = {
        'active': None, 'active_turn_ref': None,
        'latest_turn_ref': ref, 'latest_committed_turn_ref': ref,
        'committed_history': [{'answer': {'status': 'completed', 'text': 'done'}}],
    }

    def read_history(command, *, text, capture_output, check):
        commands.append(command)
        assert text and capture_output and check is False
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(history), stderr='')

    monkeypatch.setattr(driver_module.subprocess, 'Popen', FinishedProcess)
    monkeypatch.setattr(driver_module.subprocess, 'run', read_history)
    monkeypatch.setattr(driver_module, '_group_alive', lambda _process: False)
    checkout = tmp_path / 'pinned-dsh'; checkout.mkdir()
    profile = tmp_path / 'profile.json'; profile.write_text('{}')
    run_dir = tmp_path / 'run'

    lifecycle = driver_module.DshDriver().run(
        run_dir=run_dir, profile=profile,
        broker=SimpleNamespace(endpoint='/tmp/ab.sock', run_id='run-1'),
        messages=[{'role': 'user', 'content': 'complete the task'}],
        schemas=_dsh_schemas(), control_root=tmp_path / 'control',
        dsh_checkout=checkout)

    launch, history_command = commands
    assert '--host-task' not in launch
    assert launch[:4] == [
        os.sys.executable, '-m', 'cpn.dsh.launcher', str(checkout.resolve())]
    assert launch[launch.index('--execution') + 1] == str(profile.resolve())
    assert launch[launch.index('--attempt-budget') + 1] == 'unmetered'
    assert '--task' in launch and '--plugin-config' in launch
    assert '--managed-tool' not in launch
    binding_path = Path(launch[launch.index('--managed-bindings') + 1])
    assert binding_path == (run_dir / 'managed-bindings.json').resolve()
    binding_document = load(binding_path)
    assert binding_document == {
        'schema_version': 'rpnh/dsh_managed_bindings/v1',
        **bindings(_dsh_schemas())['executor'],
    }
    assert history_command[4:6] == ['--history', '--root']
    assert history_command[-2:] == ['--session-id', 'session-automationbench']
    result = load(run_dir / 'host-result.json')
    assert result['history'] == history
    assert result['session_id'] == 'session-automationbench'
    assert result['outcome']['status'] == 'terminal'
    assert result['admitted'] is True
    assert result['host_quiescent'] is True
    assert lifecycle['terminal'] == result
    assert lifecycle['host_quiescent'] is True
    assert not (run_dir / 'host-task.json').exists()


def test_dsh_preexisting_stop_never_launches_a_host(
        tmp_path, monkeypatch,
):
    import rpnh_ab.drivers.dsh as driver_module
    monkeypatch.setattr(driver_module.subprocess, 'Popen',
                        lambda *_args, **_kwargs: pytest.fail('stopped run must not spawn'))
    checkout = tmp_path / 'pinned-dsh'; checkout.mkdir()
    profile = tmp_path / 'profile.json'; profile.write_text('{}')
    stop_path = tmp_path / 'stop.request'; stop_path.touch()

    lifecycle = driver_module.DshDriver().run(
        run_dir=tmp_path / 'run', profile=profile,
        broker=SimpleNamespace(endpoint='/tmp/ab.sock', run_id='run-1'),
        messages=[{'role': 'user', 'content': 'complete the task'}],
        schemas=_dsh_schemas(), control_root=tmp_path / 'control',
        dsh_checkout=checkout, stop_path=stop_path)

    result = load(tmp_path / 'run' / 'host-result.json')
    assert result['stop_requested'] is True
    assert result['outcome']['status'] == 'nonterminal'
    assert result['admitted'] is False
    assert result['process_exit_confirmed'] is True
    assert result['process_quiescent'] is True
    assert result['process_group_alive'] is False
    assert result['forced_termination'] is False
    assert result['host_quiescent'] is True
    assert lifecycle['manual_stop'] is True
    assert lifecycle['terminal'] is None
    assert lifecycle['host_quiescent'] is True
    assert lifecycle['admitted'] is False


def test_dsh_stop_while_active_is_nonterminal_but_host_quiescent(
        tmp_path, monkeypatch,
):
    import rpnh_ab.drivers.dsh as driver_module
    stop_path = tmp_path / 'stop.request'

    class ActiveProcess:
        pid = 43211

        def __init__(self, _command, *, stdout, **_kwargs):
            stdout.write(b'RPNH session: session-stopped\n')
            self.returncode = None
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls == 1:
                stop_path.touch()
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    process = None

    def start(*args, **kwargs):
        nonlocal process
        process = ActiveProcess(*args, **kwargs)
        return process

    def stop(active):
        active.returncode = -15
        return True

    active_ref = {'entity_type': 'turn/v1', 'entity_id': 'turn-1',
                  'version_id': 'turn-version-1'}
    history = {
        'active': {'state': 'running'}, 'active_turn_ref': active_ref,
        'latest_turn_ref': active_ref, 'latest_committed_turn_ref': None,
        'committed_history': [],
    }
    monkeypatch.setattr(driver_module.subprocess, 'Popen', start)
    monkeypatch.setattr(driver_module.subprocess, 'run', lambda *_args, **_kwargs:
        SimpleNamespace(returncode=0, stdout=json.dumps(history), stderr=''))
    monkeypatch.setattr(driver_module, '_stop_group', stop)
    monkeypatch.setattr(driver_module, '_group_alive', lambda _process: False)
    checkout = tmp_path / 'pinned-dsh'; checkout.mkdir()
    profile = tmp_path / 'profile.json'; profile.write_text('{}')

    lifecycle = driver_module.DshDriver().run(
        run_dir=tmp_path / 'run', profile=profile,
        broker=SimpleNamespace(endpoint='/tmp/ab.sock', run_id='run-1'),
        messages=[{'role': 'user', 'content': 'complete the task'}],
        schemas=_dsh_schemas(), control_root=tmp_path / 'control',
        dsh_checkout=checkout, stop_path=stop_path)

    result = load(tmp_path / 'run' / 'host-result.json')
    assert result['stop_requested'] is True
    assert result['admitted'] is True
    assert result['process_group_alive'] is False
    assert result['forced_termination'] is True
    assert result['host_quiescent'] is True
    assert lifecycle['manual_stop'] is True
    assert lifecycle['terminal'] is None
    assert lifecycle['host_quiescent'] is True


def test_dsh_stop_kills_surviving_group_after_leader_exit(monkeypatch):
    import rpnh_ab.drivers.dsh as driver_module

    class ExitedLeader:
        pid = 43211
        def poll(self): return 0

    alive = {'value': True}
    signals = []
    def killpg(_pid, sig):
        signals.append(sig)
        if sig == driver_module.signal.SIGKILL:
            alive['value'] = False
    ticks = iter((0.0, 16.0, 17.0))
    monkeypatch.setattr(driver_module, '_group_alive',
                        lambda _process: alive['value'])
    monkeypatch.setattr(driver_module.os, 'killpg', killpg)
    monkeypatch.setattr(driver_module.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(driver_module.time, 'sleep', lambda _seconds: None)
    assert driver_module._stop_group(ExitedLeader()) is True
    assert signals == [driver_module.signal.SIGTERM,
                       driver_module.signal.SIGKILL]

@pytest.mark.parametrize('result,admitted,quiet,terminal',[
    ({'registry_path':'/exists','process_exit_confirmed':True,'host_quiescent':True,'process_quiescent':True,'outcome':None,'admitted':False},False,True,False),
    ({'registry_path':'/exists','process_exit_confirmed':True,'host_quiescent':True,'process_quiescent':True,'outcome':{'status':'terminal','answer':'ok'},'admitted':True},True,True,True),
    ({'registry_path':'/exists','process_exit_confirmed':True,'host_quiescent':True,'process_quiescent':True,'outcome':{'status':'terminal'},'admitted':False},False,True,False),
    ({'registry_path':'/exists','process_exit_confirmed':True,'host_quiescent':False,'process_quiescent':True,'outcome':{'status':'terminal'},'admitted':True},True,False,False),
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
