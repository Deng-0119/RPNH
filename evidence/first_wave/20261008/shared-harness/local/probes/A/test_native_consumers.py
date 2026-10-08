"""Local-only native execution and two-consumer acceptance; no product edits."""
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import os

from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.rpnh import agent_tasks
from cpn.rpnh.agent_workflows import (
    AgentWorkflowArc as A, AgentWorkflowEndpoint as E,
    AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
)
from cpn.rpnh.registry import run_authority
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.collaboration import environment_host
from rpnh_ha.registry_export import export_registry
from rpnh_ha.projection import ObservationCollector
from test_main_session_registry import _write_execution_profile, _response

OUT = Path(__file__).resolve().parent
INPUT = {'type': 'object', 'additionalProperties': False,
         'properties': {'value': {'type': 'integer'}}, 'required': ['value']}


def local_echo(context, arguments):
    """One local request per native worker, with a real local witness ordinal.

    This is deliberately a local fixture service, not an HA Bank/backend.
    """
    context.check_cancelled()
    return {'value': arguments['value'], 'witness_request_sequence': 1,
            'witness_scope': 'one_local_request_in_this_worker',
            'worker_pid': os.getpid(), 'fixture_run_id': context.config['run_id']}


class ScriptedPort:
    def __init__(self):
        self.calls = 0

    def request_once(self, attempt):
        self.calls += 1
        request = json.loads(attempt.canonical_request_bytes)
        text = '\n'.join(m['content'] for m in request['messages']
                         if isinstance(m.get('content'), str))
        first = 'First local node.' in text
        node = 'first' if first else 'finalize'
        if not any(m.get('role') == 'tool' for m in request['messages']):
            return _response([{'id': 'managed-' + node, 'name': 'shared',
                               'arguments': json.dumps({'value': 7})}])
        return _response([
            {'id': 'write-' + node, 'name': 'write_file',
             'arguments': json.dumps({'path': 'out/' + node + '.txt',
                                     'description': 'Local fixture product',
                                     'content': node,
                                     'output_port_id': 'team.output__first__handoff' if first else 'team.result',
                                     'outcome_id': 'complete'})},
            {'id': 'complete-' + node, 'name': 'complete_interaction', 'arguments': '{}'},
        ])

    def close(self):
        pass


def save(path, value):
    path = Path(path)
    assert path.resolve().is_relative_to(OUT) or path.resolve().is_relative_to(Path('<WORKSPACE>/.h26/an'))
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def fingerprint(owner):
    core = owner._core
    objects = {str(p.relative_to(core.object_store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(core.object_store.root.rglob('*')) if p.is_file()}
    return {'head_ordinal': core.event_store.max_ordinal(),
            'writer_epoch': core.event_store.writer_epoch,
            'object_count': len(objects),
            'object_bytes_sha256': hashlib.sha256(json.dumps(objects, sort_keys=True).encode()).hexdigest(),
            'objects': objects}


def test_native_managed_export_and_two_read_only_consumers(tmp_path, monkeypatch):
    owners, ports = [], []
    original_start = agent_tasks.start_run

    def retain_initial_owner(*args, **kwargs):
        owner = original_start(*args, **kwargs)
        owners.append(owner)
        return owner

    def port_factory(*args, **kwargs):
        port = ScriptedPort()
        ports.append(port)
        return port

    definition = PluginDefinition('localprobe', '1', (
        PluginOperation('echo', 'One pure local echo', INPUT, {}, local_echo),
    ), config_schema={'type': 'object', 'additionalProperties': False,
                      'properties': {'run_id': {'type': 'string'}}, 'required': ['run_id']})
    catalog = PluginCatalog((BoundPlugin(definition, {'run_id': 'local-A-native-consumers'}),))
    graph = AgentWorkflowGraph((
        N('first', 'First local node.', (P('request', 'task'),), (P('handoff', 'handoff'),)),
        N('finalize', 'Finalize local node.', (P('handoff', 'handoff'),), (P('result', 'result'),)),
    ), (A('handoff', E('first', 'handoff'), E('finalize', 'handoff')),),
        E('first', 'request'), E('finalize', 'result'))
    monkeypatch.setattr(agent_tasks, 'start_run', retain_initial_owner)
    monkeypatch.setattr(agent_tasks, 'build_llm_input_port', port_factory)
    monkeypatch.setattr('cpn.plugins.catalog.load_catalog', lambda _doc: catalog)
    spec = agent_tasks.AgentTaskSpec(
        tmp_path / 'run', 'Run two local pure managed echoes.', (),
        _write_execution_profile(tmp_path), workflow_graph=graph,
        max_parallel_nodes=1, plugin_configuration={}, plugin_catalog_digest=catalog.digest,
        managed_bindings={node: {'tools': {'shared': {'selector': 'localprobe/echo'}}}
                          for node in ('first', 'finalize')})
    result = agent_tasks.run_agent_task(spec)
    save(tmp_path / 'driver-result.json', result)
    assert len(owners) == 1
    owner = owners[0]
    assert result['stop_reason'] == 'terminal'
    assert result['actual_model_call_counts'] == [4, 0]
    assert sum(p.calls for p in ports) == 4
    assert not (tmp_path / 'run/owner.sock').exists()
    rows = owner._core.event_store.object_rows()
    actions = [json.loads(row['metadata_json']) for row in rows if row['object_type'] == 'agent_action/v3']
    assert len(actions) == 2
    assert all(a['outcome'] == 'returned' and a['output']['worker_pid'] != os.getpid() for a in actions)
    assert len({a['output']['worker_pid'] for a in actions}) == 2
    assert all(a['non_delivery_reason'] == 'provider_delivery_not_recorded' for a in actions)
    assert sum(r['object_type'] == 'agent_action/v2' for r in rows) == 4
    monkeypatch.undo()

    # READ phase: retain the actual original owner; no second execution owner.
    snapshots = {'before': fingerprint(owner)}
    reads, core_opens, forbidden_calls = [], [], []
    real_read = run_authority.read_run_execution
    real_init = _RegistryCore.__init__

    def observe_read(core, kernel, **kwargs):
        value = real_read(core, kernel, **kwargs)
        assert value.cut._core is core
        reads.append((core, value))
        return value

    def observe_core_open(self, *args, **kwargs):
        core_opens.append({'create': kwargs.get('create'), 'read_only': kwargs.get('read_only')})
        assert kwargs.get('create') is False and kwargs.get('read_only') is True
        real_init(self, *args, **kwargs)

    def forbid(*args, **kwargs):
        forbidden_calls.append('writer_or_execution_entry')
        raise AssertionError('READ phase attempted writer/execution')

    collector = ObservationCollector(roles={'local_worker', 'local_finalizer'}, tools={'shared'})
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(run_authority, 'read_run_execution', observe_read))
            stack.enter_context(patch.object(_RegistryCore, '__init__', observe_core_open))
            stack.enter_context(patch.object(_RegistryCore, 'begin', forbid))
            for name in ('start_run', 'resume_run'):
                stack.enter_context(patch('cpn.rpnh.run.' + name, forbid))
            stack.enter_context(patch('cpn.rpnh.agent_tasks.run_agent_task', forbid))
            ha = export_registry(tmp_path / 'run', {'first': 'local_worker', 'finalize': 'local_finalizer'},
                                 collector, terminal_evidence_ref=result['terminal_evidence_ref'], stop_reason='terminal')
            snapshots['after_ha'] = fingerprint(owner)
            env = environment_host._terminal_result(owner, _version_from_payload(result['terminal_evidence_ref']), 'terminal')
            snapshots['after_environment'] = fingerprint(owner)
    finally:
        snapshots['final'] = fingerprint(owner)
        save(tmp_path / 'read-fingerprints-full.json', snapshots)
        save(OUT / 'read-only-observations.json', {
            'snapshots': {key: {k: v for k, v in value.items() if k != 'objects'} for key, value in snapshots.items()},
            'core_opens': core_opens, 'forbidden_calls': forbidden_calls,
            'real_provider_calls': 0, 'scripted_logical_calls': sum(p.calls for p in ports)})
    assert all(s == snapshots['before'] for s in snapshots.values())
    assert len(reads) == 2 and reads[0][0] is not reads[1][0]
    assert reads[0][1].cut is not reads[1][1].cut
    assert reads[0][0].read_only is True and reads[1][0] is owner._core
    identity = ha.capture_diagnostics['terminal_identity']
    for key in ('terminal_evidence_ref', 'terminal_result_ref', 'run_execution_authority_ref'):
        assert identity[key] == env[key]
    assert _ref_payload(reads[0][1].terminal.evidence_ref) == _ref_payload(reads[1][1].terminal.evidence_ref)
    assert _ref_payload(reads[0][1].terminal.result_ref) == _ref_payload(reads[1][1].terminal.result_ref)
    assert reads[0][1].execution_generation == reads[1][1].execution_generation == 0
    assert env['status'] == 'available' and env['output'] == 'finalize'
    assert ha.actual_model_calls == 4 and len(ha.registry_records) == 4
    assert len(collector) == 3
    observations = collector._observations
    assert [o.surface for o in observations].count('tool_call') == 2
    terminal_observation = [o for o in observations if o.surface == 'communication'][0]
    assert terminal_observation.data['target_agent'] == 'user'
    assert terminal_observation.raw_event['handoff_context_raw']['terminal_evidence_ref'] == env['terminal_evidence_ref']
    assert terminal_observation.data['content'] == env['output']
    collector.save(tmp_path / 'ha-observations-full.json')
    save(tmp_path / 'ha-export-full.json', asdict(ha))
    save(OUT / 'native-observations.json', {
        'status': 'PASS', 'driver_result': result, 'environment_result': env,
        'ha_terminal_identity': identity, 'managed_actions': [
            {k: a[k] for k in ('agent_action_ref', 'started_receipt_ref', 'terminal_receipt_ref',
                              'selector', 'effect', 'outcome', 'non_delivery_reason', 'model_visible_result_ref')}
            for a in actions],
        'registry_records': ha.registry_records, 'capture_diagnostics': ha.capture_diagnostics,
        'observation_counts': {'managed_tool_call': 2, 'terminal_communication': 1},
        'cut_handles_distinct_and_bound': True, 'initial_owners': len(owners), 'read_phase_new_owners': 0,
        'worker_pids': [a['output']['worker_pid'] for a in actions], 'fixture_pid': os.getpid(),
        'real_provider_calls': 0, 'scripted_logical_calls': 4,
        'witness_scope': 'local plugin request ordinal, no HA Bank/business score/delivery claim',
    })
