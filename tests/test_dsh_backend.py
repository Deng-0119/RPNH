"""Python boundary isolation; real DSH services are tested separately in Node."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from cpn.dsh.backend import (
    DEFAULT_ATTEMPT_BUDGET, DshBackend, MAX_BYTES,
    MAX_MANAGED_TOOL_RESULT_BYTES, REVISION, declaration,
)
from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.plugins import (
    BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation,
    build_managed_plugin_tool_catalog,
)
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import canonical_json


def request(session='s1', request_id='q1', allow=True, tools=None):
    return {'session_id':session, 'request_id':request_id, 'upstream_revision':REVISION,
            'route':{'provider':'rpnh-offline','model':'deterministic-v1'},
            'messages':[{'role':'user','id':request_id,'source':{'kind':'user'},'content':[{'type':'text','text':'sum'}]}],
            'policy':{'allow_request':allow,'tools':['read_dataset','sum_values'] if tools is None else tools},
            'data':[2,3,7], 'header':{'id':session,'version':3,'createdAt':0,'isSeeded':False}}


class PhysicalCounter:
    """Explicit external substitute used only for Python boundary isolation."""
    def __init__(self): self.calls=[]
    def __call__(self, e):
        self.calls.append(deepcopy(e))
        args=e['arguments']; ticket=e['ticket']
        if ticket['kind']=='model':
            step=args['step']
            if step==0: block={'type':'tool-call','id':'read','name':'read_dataset','arguments':'{}'}
            elif step==1: block={'type':'tool-call','id':'sum','name':'sum_values','arguments':json.dumps({'values':[2,3,7]})}
            else: block={'type':'text','text':'12'}
            v={'message':{'role':'assistant','id':f'a-{step}','source':{'kind':'model',**args['route']},'content':[block]},'chunks':[],
               'finish':{'kind':'tool-calls' if block['type']=='tool-call' else 'stop'}}
        else:
            call=args['call']; value=args['data'] if call['name']=='read_dataset' else sum(call['arguments']['values'])
            content=[{'type':'text','text':json.dumps(value)}]
            v={'value':value,'isError':False,'content':content,
               'message':{'id':f"result-{call['id']}",'role':'user','source':{'kind':'tool','callId':call['id']},
                          'content':[{'type':'tool-result','toolCallId':call['id'],'content':content,'isError':False}]}}
        return {'ticket':ticket,'value':v}


def configured_selection(tmp_path: Path):
    adapter = tmp_path / 'local-adapter.json'
    adapter.write_text(json.dumps({
        'schema_version': 'local_process_adapter_config/v1',
        'adapter_kind': 'local_process',
        'model_condition': 'configured-model',
        'argv': [sys.executable, '-c', 'raise SystemExit(1)'],
        'probe_argv': [sys.executable, '-c', 'raise SystemExit(1)'],
        'env': {}, 'inherit_env': [],
    }), encoding='utf-8')
    return LLMExecutionSelection(
        LLMInputTarget('configured-model', 64, 65536),
        'local_process', adapter.resolve(), 30)


def configured_external_selection(tmp_path: Path):
    adapter = tmp_path / 'external-adapter.json'
    adapter.write_text(json.dumps({
        'schema_version': 'external_provider_adapter_config/v2',
        'adapter_kind': 'external_provider',
        'model_condition': 'configured-model',
        'recovery': {
            'strategy': 'bounded_same_route_health_probe/v1',
            'max_probe_attempts': 3,
            'probe_timeout_budget_seconds': 300,
            'max_probe_success_formal_failure_cycles': 3,
        },
        'routes': [{
            'route_id': 'primary',
            'provider': 'external-test',
            'backend': 'responses',
            'protocol': 'openai_chat_completions/v1',
            'endpoint': 'https://private.example.invalid/v1/chat',
            'outbound_model': 'configured-model',
            'credential': {
                'kind': 'bearer_env', 'env': 'PRIVATE_API_KEY'},
            'headers': {'X-Private-Route': 'private-header-value'},
        }],
    }), encoding='utf-8')
    return LLMExecutionSelection(
        LLMInputTarget('configured-model', 64, 65536),
        'external_provider', adapter.resolve(), 30)


def configured_profile(selection):
    policy = selection.as_registry_policy()
    provider = (
        policy['route_provenance'][0]['provider']
        if selection.adapter_kind == 'external_provider' else 'local-test')
    return {
        'schema_version': 'rpnh/dsh_execution_profile/v3',
        'profile': 'configured-test',
        'selection_id': 'local/configured-model',
        'provider': provider,
        'provider_display_name': 'Configured test',
        'model_condition': 'configured-model',
        'adapter_kind': selection.adapter_kind,
        'transport_kind': policy['route_provenance'][0]['transport'],
        'timeout_seconds': selection.timeout_seconds,
        'max_output_tokens': selection.input_target.max_output_tokens,
        'max_response_bytes': selection.input_target.max_response_bytes,
        'reasoning_effort': selection.reasoning_effort,
        'supported_reasoning_efforts': list(
            selection.supported_reasoning_efforts),
        'default_reasoning_effort': selection.default_reasoning_effort,
    }


def configured_request(selection, *, session='s1', request_id='q1'):
    value = request(session=session, request_id=request_id, tools=[])
    profile = configured_profile(selection)
    value['route'] = {
        'provider': profile['provider'], 'model': 'configured-model'}
    value['data'] = []
    value['execution_profile'] = profile
    return value


class SharedConfiguredPort:
    def __init__(self, selection):
        self.execution_policy = selection.as_registry_policy()
        self.calls = []

    def request_once(self, attempt):
        document = json.loads(attempt.canonical_request_bytes)
        self.calls.append(document)
        assert document['model_condition'] == 'configured-model'
        assert document['messages'] == [{'role': 'user', 'content': 'sum'}]
        return LLMInputResponseBytes(canonical_json({
            'protocol': 'llm_response_envelope/v1',
            'text': 'configured ready',
            'tool_calls': [],
            'finish_reason': 'stop',
        }), status_code=(
            200 if self.execution_policy['adapter_kind'] ==
            'external_provider' else None),
            external_request_id='configured-test-request')

    def close(self):
        pass


class ConfiguredEffect:
    def __init__(self):
        self.calls = []

    def __call__(self, effect):
        self.calls.append(deepcopy(effect))
        ticket = effect['ticket']; arguments = effect['arguments']
        if ticket['kind'] == 'model_request':
            messages = [{
                'role': item['role'],
                'content': ''.join(
                    block['text'] for block in item['content'])}
                for item in arguments['messages']]
            value = {'provider_request': {
                'protocol': 'registered_llm/v1', 'messages': messages,
                'tools': [], 'tool_choice': 'none', 'placeholders': [],
            }}
        elif ticket['kind'] == 'model_response':
            response = arguments['provider_response']
            value = {
                'message': {
                    'id': 'configured-answer', 'role': 'assistant',
                    'source': {'kind': 'model', **arguments['route']},
                    'content': [{'type': 'text', 'text': response['text']}],
                },
                'chunks': [], 'finish': {'kind': 'stop'},
                'wire_request': {},
            }
        else:
            raise AssertionError(ticket['kind'])
        return {'ticket': ticket, 'value': value}


def managed_double(_context, arguments):
    return arguments['value'] * 2


def managed_plugin_definition(
        *, description='Double one integer', max_result_bytes=4096):
    return PluginDefinition('managed_test', '1', (
        PluginOperation(
            'double', description, {
                'type': 'object', 'additionalProperties': False,
                'properties': {'value': {'type': 'integer'}},
                'required': ['value'],
            }, {'type': 'integer'}, managed_double,
            max_result_bytes=max_result_bytes),
    ))


def configured_managed_plugin(tmp_path, monkeypatch):
    import cpn.plugins.catalog as plugin_catalog

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test', load=lambda: managed_plugin_definition)])
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selected = PluginCatalog((BoundPlugin(managed_plugin_definition(), {}),))
    return plugin_config, selected


def managed_receipts(run_dir):
    core = _RegistryCore(run_dir, create=False, read_only=True)
    receipts = []
    for row in core.event_store.canonical_object_rows(
            object_type='resource_version/v1'):
        metadata = json.loads(row['metadata_json'])
        if 'managed_plugin_call' in metadata.get('descriptors', {}):
            receipts.append(json.loads(core.object_store.read_registered(
                core.get_version(row['version_id']))))
    return receipts


class ManagedConfiguredPort:
    def __init__(self, selection):
        self.execution_policy = selection.as_registry_policy()
        self.calls = []

    def request_once(self, attempt):
        document = json.loads(attempt.canonical_request_bytes)
        self.calls.append(document)
        declaration = {
            'type': 'function',
            'function': {
                'name': 'double_value',
                'description': 'Double one integer',
                'parameters': {
                    'type': 'object', 'additionalProperties': False,
                    'properties': {'value': {'type': 'integer'}},
                    'required': ['value'],
                },
            },
        }
        assert document['tools'] == [declaration]
        if len(self.calls) == 1:
            response = {
                'protocol': 'llm_response_envelope/v1', 'text': '',
                'tool_calls': [{
                    'id': 'call-double', 'name': 'double_value',
                    'arguments': '{"value":4}',
                }],
                'finish_reason': 'tool_calls',
            }
        else:
            assert document['messages'][-1] == {
                'role': 'tool', 'tool_call_id': 'call-double', 'content': '8'}
            response = {
                'protocol': 'llm_response_envelope/v1',
                'text': 'The result is 8.', 'tool_calls': [],
                'finish_reason': 'stop',
            }
        return LLMInputResponseBytes(
            canonical_json(response), status_code=None,
            external_request_id=f'managed-request-{len(self.calls)}')

    def close(self):
        pass


class ManagedOutcomePort:
    def __init__(self, selection, *, arguments, final_text='tool handled'):
        self.execution_policy = selection.as_registry_policy()
        self.arguments = arguments
        self.final_text = final_text
        self.calls = []

    def request_once(self, attempt):
        document = json.loads(attempt.canonical_request_bytes)
        self.calls.append(document)
        if len(self.calls) == 1:
            response = {
                'protocol': 'llm_response_envelope/v1', 'text': '',
                'tool_calls': [{
                    'id': 'call-double', 'name': 'double_value',
                    'arguments': json.dumps(self.arguments),
                }],
                'finish_reason': 'tool_calls',
            }
        else:
            response = {
                'protocol': 'llm_response_envelope/v1',
                'text': self.final_text, 'tool_calls': [],
                'finish_reason': 'stop',
            }
        return LLMInputResponseBytes(
            canonical_json(response), status_code=None,
            external_request_id=f'outcome-request-{len(self.calls)}')

    def close(self):
        pass


class ManagedConfiguredEffect:
    def __call__(self, effect):
        ticket = effect['ticket']; arguments = effect['arguments']
        tools = arguments['registered_tools']
        if ticket['kind'] == 'model_request':
            messages = []
            pending = None
            for message in arguments['messages']:
                if message['role'] == 'assistant':
                    call = next((block for block in message['content']
                                 if block['type'] == 'tool-call'), None)
                    text = ''.join(block['text'] for block in message['content']
                                   if block['type'] in {'text', 'reasoning'})
                    value = {'role': 'assistant', 'content': text}
                    if call is not None:
                        pending = call
                        value['tool_calls'] = [{
                            'id': call['id'], 'type': 'function',
                            'function': {'name': call['name'],
                                         'arguments': call['arguments']},
                        }]
                    messages.append(value)
                elif message.get('source', {}).get('kind') == 'tool':
                    result = message['content'][0]
                    assert pending is not None
                    messages.append({
                        'role': 'tool', 'tool_call_id': pending['id'],
                        'content': ''.join(block['text']
                                         for block in result['content']),
                    })
                    pending = None
                else:
                    messages.append({
                        'role': 'user',
                        'content': ''.join(block['text']
                                         for block in message['content']),
                    })
            value = {'provider_request': {
                'protocol': 'registered_llm/v1', 'messages': messages,
                'tools': tools, 'tool_choice': 'auto', 'placeholders': [],
            }}
        elif ticket['kind'] == 'model_response':
            response = arguments['provider_response']
            if response['tool_calls']:
                call = response['tool_calls'][0]
                content = [{
                    'type': 'tool-call', 'id': call['id'],
                    'name': call['name'], 'arguments': call['arguments'],
                }]
                finish = 'tool-calls'
            else:
                content = [{'type': 'text', 'text': response['text']}]
                finish = 'stop'
            value = {
                'message': {
                    'id': f"managed-answer-{arguments['step']}",
                    'role': 'assistant',
                    'source': {'kind': 'model', **arguments['route']},
                    'content': content,
                },
                'chunks': [], 'finish': {'kind': finish},
                'wire_request': {},
            }
        else:
            raise AssertionError(ticket['kind'])
        return {'ticket': ticket, 'value': value}


def test_full_multistep_and_registry_only_reopen_without_reexecution(tmp_path):
    physical=PhysicalCounter(); b=DshBackend(tmp_path,'s1',physical,create=True)
    r=request(); result=b.turn(r)
    assert result['answer']['text']=='12'
    assert [c['ticket']['kind'] for c in physical.calls]==['model','tool','model','tool','model']
    assert len({c['ticket']['execution_ref']['version_id'] for c in physical.calls})==5
    path=next((tmp_path/'s1'/'main'/'attempts').iterdir())
    view=project_registry_net(path, catalog=b.core.catalog)
    firings={n['id']:n.get('runtime',{}).get('firing_count',0) for n in view['nodes'] if n['kind']=='transition'}
    assert firings['dsh.model']==3 and firings['dsh.tool']==2 and firings['dsh.inspect_tool']==2
    reopened=DshBackend(tmp_path,'s1',lambda _:pytest.fail('history caused physical call'))
    before=reopened.core.event_store.max_ordinal()
    assert reopened.history()['committed_history'][0]['answer']['text']=='12'
    assert reopened.core.event_store.max_ordinal()==before
    assert reopened.turn(r)['replayed'] is True
    changed={**r,'data':[9]}
    with pytest.raises(ValueError,match='reused'): reopened.turn(changed)


def test_default_budget_stays_48_and_unmetered_declaration_has_no_cap(tmp_path):
    assert DEFAULT_ATTEMPT_BUDGET == 48
    assert declaration().to_dict()['budget_buckets'] == [{
        'bucket_id': 'dsh', 'budget_scope': 'module',
        'finalization_scope': None, 'max_attempts': 48,
    }]
    assert declaration(attempt_budget=None).to_dict()['budget_buckets'] == [{
        'bucket_id': 'dsh', 'budget_scope': 'module',
        'finalization_scope': None, 'max_attempts': None,
    }]
    unmetered = DshBackend(
        tmp_path, 's1', PhysicalCounter(), create=True, attempt_budget=None)
    assert unmetered.attempt_budget is None


def test_unmetered_resume_uses_persisted_registry_budget_not_constructor(
        tmp_path,
):
    effect = PhysicalCounter()
    first = DshBackend(
        tmp_path, 's1', effect, create=True, attempt_budget=None)

    def stop_before_first_tool(_owner, execution):
        if execution.operation.firing.transition_id == 'dsh.tool':
            first.cancel()

    first.before_dispatch = stop_before_first_tool
    assert first.turn(request())['status'] == 'stopped_by_owner'

    reopened = DshBackend(tmp_path, 's1', effect, attempt_budget=1)
    resumed = reopened.resume()

    assert resumed['status'] == 'terminal'
    assert resumed['answer']['text'] == '12'


def test_launcher_is_executable_as_a_python_module():
    result = __import__('subprocess').run(
        [sys.executable, '-m', 'cpn.dsh.launcher', '--help'],
        text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert 'pinned DSH source checkout' in result.stdout


def test_empty_session_resume_is_public_failure_without_dispatch(tmp_path):
    physical = PhysicalCounter()
    backend = DshBackend(tmp_path, 's1', physical, create=True)

    result = backend.resume()

    assert result['status'] == 'failed'
    assert result['answer'] is None
    assert result['failure_kind'] == 'no_terminal_current_turn'
    assert result['history']['committed_history'] == []
    assert result['history']['active'] is None
    assert physical.calls == []


@pytest.mark.parametrize('allow,tools,expected_calls',[(False,None,0),(True,[],1)])
def test_denials_stop_physical_dispatch(tmp_path,allow,tools,expected_calls):
    physical=PhysicalCounter(); b=DshBackend(tmp_path,'s1',physical,create=True)
    result=b.turn(request(allow=allow,tools=tools))
    assert result['answer']['status']=='denied'
    assert len(physical.calls)==expected_calls
    assert all(c['ticket']['kind']=='model' for c in physical.calls)


def test_cross_request_response_is_not_a_success(tmp_path):
    physical=PhysicalCounter()
    def mixed(e):
        r=physical(e); r['ticket']={**r['ticket'],'request_id':'some-other-request'}; return r
    b=DshBackend(tmp_path,'s1',mixed,create=True)
    with pytest.raises(ValueError,match='exact execution'): b.turn(request())
    assert len(physical.calls)==1 and b.history()['committed_history']==[]
    assert b.history()['active'] is not None
    with pytest.raises(Exception): b.resume()
    assert len(physical.calls)==1


def test_success_publication_failure_prevents_downstream_tool(tmp_path):
    physical=PhysicalCounter(); b=DshBackend(tmp_path,'s1',physical,create=True)
    def inject(owner):
        original=owner._core.event_store.publish_batch
        def publish(**kw):
            if len(physical.calls)==1 and any(e.event_type=='transition_firing_settled/v1' for e in kw['events']):
                raise RuntimeError('injected settlement failure')
            return original(**kw)
        owner._core.event_store.publish_batch=publish
    b.before_settlement=inject
    with pytest.raises(RuntimeError,match='settlement failure'): b.turn(request())
    assert len(physical.calls)==1 and b.history()['committed_history']==[]


def test_clean_stop_then_explicit_resume_reuses_registered_state(tmp_path):
    physical=PhysicalCounter(); b=DshBackend(tmp_path,'s1',physical,create=True)
    stopped=[]
    def at_dispatch(owner,execution):
        if execution.operation.firing.transition_id=='dsh.tool' and not stopped:
            stopped.append(True); b.cancel()
    b.before_dispatch=at_dispatch
    result=b.turn(request())
    assert result['status']=='stopped_by_owner' and b.history()['committed_history']==[]
    assert [c['ticket']['kind'] for c in physical.calls]==['model']
    other=DshBackend(tmp_path,'s1',physical)
    result=other.resume()
    assert result['answer']['text']=='12'
    assert [c['ticket']['kind'] for c in physical.calls]==['model','tool','model','tool','model']


def test_profile_mismatch_refused_before_acceptance(tmp_path):
    b=DshBackend(tmp_path,'s1',lambda _:pytest.fail('unexpected dispatch'),create=True)
    r=request();r['route']['model']='unselected'
    before=b.core.event_store.max_ordinal()
    with pytest.raises(ValueError,match='exact offline model'):b.turn(r)
    assert b.core.event_store.max_ordinal()==before


def test_invalid_policy_is_rejected_before_main_turn_acceptance(tmp_path):
    b=DshBackend(tmp_path,'s1',lambda _:pytest.fail('unexpected dispatch'),create=True)
    r=request();r['policy']=[]
    before=b.core.event_store.max_ordinal()
    with pytest.raises(Exception,match="not of type 'object'"):
        b.turn(r)
    assert b.core.event_store.max_ordinal()==before
    assert b.thread.project_current_thread()['active_turn_ref'] is None


def test_nonterminal_model_finish_cannot_commit_a_terminal_answer(tmp_path):
    calls=[]
    def truncated(effect):
        calls.append(deepcopy(effect)); args=effect['arguments']
        return {'ticket':effect['ticket'],'value':{
            'message':{'role':'assistant','id':'partial',
                       'source':{'kind':'model',**args['route']},
                       'content':[{'type':'text','text':'partial output'}]},
            'chunks':[],'finish':{'kind':'length'}}}
    b=DshBackend(tmp_path,'s1',truncated,create=True)
    with pytest.raises(RuntimeError,match='admitted outcome'):
        b.turn(request())
    assert len(calls)==1
    assert b.history()['committed_history']==[]


def test_configured_model_uses_shared_registered_host_provider_once(tmp_path):
    selection = configured_selection(tmp_path)
    physical = SharedConfiguredPort(selection)
    effect = ConfiguredEffect()
    backend = DshBackend(
        tmp_path, 's1', effect, create=True, offline=False,
        selection=selection,
        input_port_factory=lambda selected, **_kwargs: (
            physical if selected is selection else pytest.fail(
                'configured selection changed')))

    result = backend.turn(configured_request(selection))

    assert result['answer']['text'] == 'configured ready'
    assert [call['ticket']['kind'] for call in effect.calls] == [
        'model_request', 'model_response']
    assert len(physical.calls) == 1
    assert 'provider_response' not in result['answer']['calls'][0]['arguments']
    run_dir = next((tmp_path / 's1' / 'main' / 'attempts').iterdir())
    core = _RegistryCore(run_dir, create=False, read_only=True)
    assert core.event_store.actual_model_call_counts() == (1, 0)
    assert len(tuple(core.event_store.canonical_object_rows(
        object_type='llm_call_spec/v3'))) == 1
    assert tuple(core.event_store.canonical_object_rows(
        object_type='agent_loop/v1')) == ()


def test_configured_managed_plugin_tool_uses_shared_registry_boundary_once(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test', load=lambda: managed_plugin_definition)])
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)
    physical = ManagedConfiguredPort(selection)
    backend = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']

    result = backend.turn(turn)

    assert result['answer']['text'] == 'The result is 8.'
    assert len(physical.calls) == 2
    run_dir = next((tmp_path / 's1' / 'main' / 'attempts').iterdir())
    core = _RegistryCore(run_dir, create=False, read_only=True)
    assert core.event_store.actual_model_call_counts() == (2, 0)
    receipts = []
    for row in core.event_store.canonical_object_rows(
            object_type='resource_version/v1'):
        metadata = json.loads(row['metadata_json'])
        if 'managed_plugin_call' in metadata.get('descriptors', {}):
            prepared = core.get_version(row['version_id'])
            receipts.append(json.loads(
                core.object_store.read_registered(prepared)))
    assert [item['state'] for item in receipts] == ['started', 'returned']
    assert {item['call_id'] for item in receipts} == {'call-double'}
    assert result['answer']['messages'][-3]['content'][0]['name'] == (
        'double_value')


def test_configured_v2_managed_tool_stop_reopens_with_exact_registration(
        tmp_path, monkeypatch,
):
    plugin_config, _selected = configured_managed_plugin(
        tmp_path, monkeypatch)
    selection = configured_selection(tmp_path)
    physical = ManagedConfiguredPort(selection)
    backend = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    stopped = []

    def stop_before_tool(_owner, execution):
        if (execution.operation.firing.transition_id == 'dsh.tool'
                and not stopped):
            stopped.append(True)
            backend.cancel()

    backend.before_dispatch = stop_before_tool
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']

    interrupted = backend.turn(turn)

    assert interrupted['status'] == 'stopped_by_owner'
    assert len(physical.calls) == 1
    reopened = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), offline=False,
        selection=selection, plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    resumed = reopened.resume()

    assert resumed['status'] == 'terminal'
    assert resumed['answer']['text'] == 'The result is 8.'
    assert len(physical.calls) == 2
    run_dir = next((tmp_path / 's1' / 'main' / 'attempts').iterdir())
    receipts = managed_receipts(run_dir)
    assert [item['state'] for item in receipts] == ['started', 'returned']
    assert all(item['schema_version'].endswith('/v2') for item in receipts)
    assert all(item['admitted_at_utc'].endswith('Z') for item in receipts)


def test_configured_persisted_v1_managed_tool_resumes_and_executes_exactly(
        tmp_path, monkeypatch,
):
    plugin_config, selected = configured_managed_plugin(
        tmp_path, monkeypatch)
    selection = configured_selection(tmp_path)
    physical = ManagedConfiguredPort(selection)
    backend = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    backend.managed_tools = build_managed_plugin_tool_catalog(
        selected, {'double_value': 'managed_test/double'},
        protocol_version='v1')
    backend.managed_tool_names = ('double_value',)

    def stop_before_tool(_owner, execution):
        if execution.operation.firing.transition_id == 'dsh.tool':
            backend.cancel()

    backend.before_dispatch = stop_before_tool
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']

    interrupted = backend.turn(turn)

    assert interrupted['status'] == 'stopped_by_owner'
    assert len(physical.calls) == 1
    reopened = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), offline=False,
        selection=selection, plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    resumed = reopened.resume()

    assert resumed['status'] == 'terminal'
    assert resumed['answer']['text'] == 'The result is 8.'
    assert len(physical.calls) == 2
    run_dir = next((tmp_path / 's1' / 'main' / 'attempts').iterdir())
    receipts = managed_receipts(run_dir)
    assert [item['state'] for item in receipts] == ['started', 'returned']
    assert all(item['schema_version'].endswith('/v1') for item in receipts)
    assert all('admitted_at_utc' not in item for item in receipts)


def test_configured_managed_tools_require_explicit_matching_policy(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test', load=lambda: managed_plugin_definition)])
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)
    backend = DshBackend(
        tmp_path, 's1', lambda _effect: pytest.fail('unexpected effect'),
        create=True, offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: pytest.fail(
            'unexpected provider call'))
    value = configured_request(selection)
    before = backend.core.event_store.max_ordinal()

    with pytest.raises(ValueError, match='managed plugin allowlist'):
        backend.turn(value)

    assert backend.core.event_store.max_ordinal() == before
    assert backend.history()['active'] is None


def test_configured_managed_tool_invalid_arguments_are_denied_before_worker(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog
    import cpn.plugins.worker as plugin_worker

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test', load=lambda: managed_plugin_definition)])
    worker_calls = []
    monkeypatch.setattr(
        plugin_worker, 'execute_worker',
        lambda *_args, **_kwargs: worker_calls.append(True))
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)
    physical = ManagedOutcomePort(selection, arguments={'value': 'four'})
    backend = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']

    result = backend.turn(turn)

    assert result['status'] == 'terminal'
    assert result['answer']['status'] == 'denied'
    assert len(physical.calls) == 1
    assert worker_calls == []
    assert backend.history()['active'] is None


def test_configured_managed_tool_failure_becomes_correlated_error_result(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog
    import cpn.plugins.worker as plugin_worker
    from cpn.plugins.worker import WorkerFailure

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test', load=lambda: managed_plugin_definition)])
    worker_calls = []

    def failed_worker(*_args, **_kwargs):
        worker_calls.append(True)
        raise WorkerFailure('handler_failed')

    monkeypatch.setattr(plugin_worker, 'execute_worker', failed_worker)
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)
    physical = ManagedOutcomePort(
        selection, arguments={'value': 4}, final_text='tool failed safely')
    backend = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']

    result = backend.turn(turn)

    assert result['status'] == 'terminal'
    assert result['answer']['text'] == 'tool failed safely'
    assert worker_calls == [True]
    assert len(physical.calls) == 2
    assert physical.calls[1]['messages'][-1] == {
        'role': 'tool', 'tool_call_id': 'call-double',
        'content': '{"error":"handler_failed"}',
    }
    assert backend.history()['active'] is None
    run_dir = next((tmp_path / 's1' / 'main' / 'attempts').iterdir())
    core = _RegistryCore(run_dir, create=False, read_only=True)
    receipts = []
    for row in core.event_store.canonical_object_rows(
            object_type='resource_version/v1'):
        metadata = json.loads(row['metadata_json'])
        if 'managed_plugin_call' in metadata.get('descriptors', {}):
            receipts.append(json.loads(core.object_store.read_registered(
                core.get_version(row['version_id']))))
    assert [item['state'] for item in receipts] == ['started', 'failed']


def test_configured_managed_tool_declarations_are_in_pre_admission_budget(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog

    oversized_description = 'D' * MAX_BYTES
    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test',
            load=lambda: (lambda: managed_plugin_definition(
                description=oversized_description)))])
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)
    physical = ManagedOutcomePort(selection, arguments={'value': 4})
    effect_calls = []
    backend = DshBackend(
        tmp_path, 's1', lambda value: effect_calls.append(value), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']
    before = backend.core.event_store.max_ordinal()

    with pytest.raises(ValueError, match='response budget'):
        backend.turn(turn)

    assert backend.core.event_store.max_ordinal() == before
    assert backend.history()['active'] is None
    assert physical.calls == []
    assert effect_calls == []


def test_registered_managed_tool_result_limit_is_bounded_at_16_mib(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test',
            load=lambda: (lambda: managed_plugin_definition(
                max_result_bytes=MAX_MANAGED_TOOL_RESULT_BYTES + 1)))])
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)

    with pytest.raises(ValueError, match='invalid registered execution limits'):
        DshBackend(
            tmp_path, 's1', ManagedConfiguredEffect(), create=True,
            offline=False, selection=selection,
            plugin_config_path=plugin_config,
            managed_tools={'double_value': 'managed_test/double'},
            input_port_factory=lambda *_args, **_kwargs: pytest.fail(
                'provider port must not be built'))


def test_configured_managed_tool_future_frame_is_denied_before_worker(
        tmp_path, monkeypatch,
):
    import cpn.plugins.catalog as plugin_catalog
    import cpn.plugins.worker as plugin_worker

    monkeypatch.setattr(
        plugin_catalog.metadata, 'entry_points',
        lambda *, group: [SimpleNamespace(
            name='managed-test',
            load=lambda: (lambda: managed_plugin_definition(
                max_result_bytes=MAX_MANAGED_TOOL_RESULT_BYTES)))])
    worker_calls = []
    monkeypatch.setattr(
        plugin_worker, 'execute_worker',
        lambda *_args, **_kwargs: worker_calls.append(True))
    plugin_config = (tmp_path / 'plugins.json').resolve()
    plugin_config.write_text(json.dumps({
        'schema_version': 'rpnh/plugins/v1',
        'plugins': [{
            'name': 'managed_test', 'entry_point': 'managed-test',
            'version': '1', 'config': {}, 'environment': [],
        }],
    }), encoding='utf-8')
    selection = configured_selection(tmp_path)
    physical = ManagedOutcomePort(selection, arguments={'value': 4})
    backend = DshBackend(
        tmp_path, 's1', ManagedConfiguredEffect(), create=True,
        offline=False, selection=selection,
        plugin_config_path=plugin_config,
        managed_tools={'double_value': 'managed_test/double'},
        input_port_factory=lambda *_args, **_kwargs: physical)
    turn = configured_request(selection)
    turn['policy']['tools'] = ['double_value']
    route = turn['route']
    chosen = None
    for size in range(1_000_000, 8_000_001, 1_000_000):
        turn['messages'][0]['content'][0]['text'] = 'x' * size
        messages = turn['messages']
        if not backend._configured_frame_budget_fits(
                route=route, messages=messages,
                request_id=turn['request_id']):
            continue
        assistant = {
            'id': 'managed-answer-0', 'role': 'assistant',
            'source': {'kind': 'model', **route},
            'content': [{
                'type': 'tool-call', 'id': 'call-double',
                'name': 'double_value', 'arguments': '{"value":4}',
            }],
        }
        result = {
            'id': 'result-call-double', 'role': 'user',
            'source': {'kind': 'tool', 'callId': 'call-double'},
            'content': [{
                'type': 'tool-result', 'toolCallId': 'call-double',
                'content': [{
                    'type': 'text',
                    'text': '\\' * MAX_MANAGED_TOOL_RESULT_BYTES,
                }],
                'isError': False,
            }],
        }
        if not backend._configured_frame_budget_fits(
                route=route, messages=[*messages, assistant, result],
                request_id=turn['request_id']):
            chosen = size
            break
    assert chosen is not None

    outcome = backend.turn(turn)

    assert outcome['status'] == 'terminal'
    assert outcome['answer']['status'] == 'denied'
    assert len(physical.calls) == 1
    assert worker_calls == []
    assert backend.history()['active'] is None


def test_configured_external_route_keeps_private_policy_out_of_dsh_registry(
        tmp_path,
):
    selection = configured_external_selection(tmp_path)
    physical = SharedConfiguredPort(selection)
    backend = DshBackend(
        tmp_path, 's1', ConfiguredEffect(), create=True, offline=False,
        selection=selection,
        input_port_factory=lambda selected, **_kwargs: (
            physical if selected is selection else pytest.fail(
                'configured selection changed')))

    result = backend.turn(configured_request(selection))

    assert result['answer']['text'] == 'configured ready'
    assert result['answer']['route'] == {
        'provider': 'external-test', 'model': 'configured-model'}
    assert len(physical.calls) == 1
    registry_material = b'\n'.join(
        path.read_bytes() for path in (tmp_path / 's1').rglob('*')
        if path.is_file())
    assert b'private.example.invalid' not in registry_material
    assert b'PRIVATE_API_KEY' not in registry_material
    assert b'private-header-value' not in registry_material


def test_configured_profile_mismatch_fails_before_turn_acceptance(tmp_path):
    selection = configured_selection(tmp_path)
    backend = DshBackend(
        tmp_path, 's1', lambda _effect: pytest.fail('unexpected effect'),
        create=True, offline=False, selection=selection,
        input_port_factory=lambda *_args, **_kwargs: pytest.fail(
            'unexpected provider port'))
    value = configured_request(selection)
    value['execution_profile']['timeout_seconds'] = 31
    before = backend.core.event_store.max_ordinal()

    with pytest.raises(ValueError, match='shared selection'):
        backend.turn(value)

    assert backend.core.event_store.max_ordinal() == before
    assert backend.history()['active'] is None


def test_configured_response_frame_budget_fails_before_provider_dispatch(
        tmp_path,
):
    base = configured_selection(tmp_path)
    selection = LLMExecutionSelection(
        LLMInputTarget(
            base.input_target.model_condition,
            base.input_target.max_output_tokens,
            MAX_BYTES),
        base.adapter_kind, base.adapter_config_path, base.timeout_seconds)
    physical = SharedConfiguredPort(selection)
    effect = ConfiguredEffect()
    backend = DshBackend(
        tmp_path, 's1', effect, create=True, offline=False,
        selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)

    with pytest.raises(ValueError, match='response budget'):
        backend.turn(configured_request(selection))

    assert physical.calls == []
    assert effect.calls == []
    assert backend.history()['active'] is None


def test_configured_clean_stop_before_model_resumes_via_shared_provider(
        tmp_path,
):
    selection = configured_selection(tmp_path)
    physical = SharedConfiguredPort(selection)
    effect = ConfiguredEffect()
    backend = DshBackend(
        tmp_path, 's1', effect, create=True, offline=False,
        selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)
    stopped = []

    def stop_before_model(_owner, execution):
        if (execution.operation.firing.transition_id == 'dsh.model'
                and not stopped):
            stopped.append(True)
            backend.cancel()

    backend.before_dispatch = stop_before_model
    result = backend.turn(configured_request(selection))
    assert result['status'] == 'stopped_by_owner'
    assert physical.calls == []

    reopened = DshBackend(
        tmp_path, 's1', effect, offline=False, selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)
    resumed = reopened.resume()

    assert resumed['answer']['text'] == 'configured ready'
    assert len(physical.calls) == 1


def test_configured_resume_rejects_changed_shared_selection_before_provider(
        tmp_path,
):
    selection = configured_selection(tmp_path)
    physical = SharedConfiguredPort(selection)
    backend = DshBackend(
        tmp_path, 's1', ConfiguredEffect(), create=True, offline=False,
        selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)

    def stop_before_model(_owner, execution):
        if execution.operation.firing.transition_id == 'dsh.model':
            backend.cancel()

    backend.before_dispatch = stop_before_model
    assert backend.turn(configured_request(selection))['status'] == (
        'stopped_by_owner')
    changed = LLMExecutionSelection(
        selection.input_target, selection.adapter_kind,
        selection.adapter_config_path, selection.timeout_seconds + 1)
    reopened = DshBackend(
        tmp_path, 's1', ConfiguredEffect(), offline=False,
        selection=changed,
        input_port_factory=lambda *_args, **_kwargs: pytest.fail(
            'changed profile reached the provider'))

    with pytest.raises(ValueError, match='shared selection'):
        reopened.resume()

    assert physical.calls == []


def test_configured_resume_settles_registered_completion_without_replay(
        tmp_path,
):
    selection = configured_selection(tmp_path)
    physical = SharedConfiguredPort(selection)
    backend = DshBackend(
        tmp_path, 's1', ConfiguredEffect(), create=True, offline=False,
        selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)

    def fail_model_settlement(owner):
        original = owner._core.event_store.publish_batch

        def publish(**kwargs):
            if (len(physical.calls) == 1
                    and any(event.event_type ==
                            'transition_firing_settled/v1'
                            for event in kwargs['events'])):
                raise RuntimeError('injected configured settlement failure')
            return original(**kwargs)

        owner._core.event_store.publish_batch = publish

    backend.before_settlement = fail_model_settlement
    with pytest.raises(RuntimeError, match='configured settlement failure'):
        backend.turn(configured_request(selection))
    assert len(physical.calls) == 1

    class NoReplayPort:
        execution_policy = selection.as_registry_policy()

        def request_once(self, _attempt):
            pytest.fail('registered completion replayed the provider')

        def close(self):
            pass

    reopened = DshBackend(
        tmp_path, 's1', ConfiguredEffect(), offline=False,
        selection=selection,
        input_port_factory=lambda *_args, **_kwargs: NoReplayPort())
    resumed = reopened.resume()

    assert resumed['status'] == 'terminal'
    assert resumed['answer']['text'] == 'configured ready'
    assert len(physical.calls) == 1


def test_configured_stop_after_registered_response_does_not_replay_provider(
        tmp_path,
):
    selection = configured_selection(tmp_path)
    physical = SharedConfiguredPort(selection)
    effect = ConfiguredEffect()
    backend = DshBackend(
        tmp_path, 's1', effect, create=True, offline=False,
        selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)
    stopped = []

    def stop_after_registered_response(owner):
        original = owner._core.event_store.publish_batch

        def publish(**kwargs):
            result = original(**kwargs)
            if (not stopped and len(physical.calls) == 1
                    and any(event.event_type ==
                            'registered_operation_completion_recorded/v1'
                            for event in kwargs['events'])):
                stopped.append(True)
                backend.cancel()
            return result

        owner._core.event_store.publish_batch = publish

    backend.before_settlement = stop_after_registered_response
    interrupted = backend.turn(configured_request(selection))

    assert interrupted['status'] == 'stopped_by_owner'
    assert interrupted['answer'] is None
    assert len(physical.calls) == 1
    assert backend.history()['committed_history'] == []

    reopened = DshBackend(
        tmp_path, 's1', effect, offline=False, selection=selection,
        input_port_factory=lambda *_args, **_kwargs: physical)
    resumed = reopened.resume()

    assert resumed['status'] == 'terminal'
    assert resumed['answer']['text'] == 'configured ready'
    assert len(physical.calls) == 1
