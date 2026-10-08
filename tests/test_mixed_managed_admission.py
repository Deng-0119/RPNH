import json
from threading import Event, Lock, get_ident
import pytest

from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.plugins.managed_tools import ManagedPluginToolCatalog
from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
from cpn.rpnh.agent_workflows import (AgentWorkflowArc as A, AgentWorkflowEndpoint as E,
    AgentWorkflowExecution as X, AgentWorkflowGraph as G, AgentWorkflowNode as N, AgentWorkflowPort as P)
from test_optional_context_compaction import _configure_offline_task, _response


def echo(context, args):
    return args


@pytest.mark.parametrize('effects,domains,capacity,overlap,cancel', [
    (('pure', 'pure'), ('A', 'A'), 1, False, False),
    (('external_write', 'external_write'), ('A', 'A'), 2, False, False),
    (('external_write', 'external_read'), ('A', 'A'), 2, False, False),
    (('external_read', 'external_read'), ('A', 'A'), 2, True, False),
    (('external_write', 'external_write'), ('A', 'B'), 2, True, False),
    pytest.param(('pure', 'pure'), ('A', 'A'), 1, False, True, id='cancel_wait'),
])
def test_mixed_turn_shares_run_admission(tmp_path, monkeypatch, effects, domains, capacity, overlap, cancel):
    entered = Event()
    mixed_entered = Event()
    release = Event()
    lock = Lock()
    evidence = {'active': 0, 'peak': 0, 'workers': [], 'requests': []}
    owner_thread = get_ident()
    from cpn.plugins.managed_scheduler import ManagedRunCapacity
    from cpn.rpnh.control_server import OwnerEventLoop
    from cpn.components.agent_loop.mechanical_lifecycle import AgentLoopMechanicalLifecycle
    from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
    loops = []
    settlements = []
    order = []
    initialize = OwnerEventLoop.__init__
    reserve = ManagedRunCapacity._reserve
    settle = AgentLoopMechanicalLifecycle.settle_action_batch
    write = WorkspaceExecutionMixin._write_product

    def initialized(self, *args, **kwargs):
        initialize(self, *args, **kwargs)  # Real AF_UNIX listener and socketpair.
        loops.append(self)

    def reserved(self, access):
        token = reserve(self, access)
        if token is None and entered.is_set():
            # Positive blocked-admission synchronization: do not wait for the
            # very overlap this regression prohibits. Owner must answer while
            # the first worker is still held and the second is waiting.
            evidence['ping_thread'] = loops[0].submit_host(get_ident).result(timeout=10)
            evidence['blocked_admission'] = True
            if cancel:
                import signal
                loops[0].submit_host(lambda: signal.raise_signal(signal.SIGINT)).result(timeout=10)
            release.set()
        return token

    def settled(self, *args, **kwargs):
        result = settle(self, *args, **kwargs)
        settlements.append([r.tool_call_id for r in result[1]])
        return result

    def written(self, execution, loop, arguments, key):
        result = write(self, execution, loop, arguments, key)
        if 'mixed' in arguments['output_port_id']:
            order.append('write')
        return result

    monkeypatch.setattr(OwnerEventLoop, '__init__', initialized)
    monkeypatch.setattr(ManagedRunCapacity, '_reserve', reserved)
    monkeypatch.setattr(AgentLoopMechanicalLifecycle, 'settle_action_batch', settled)
    monkeypatch.setattr(WorkspaceExecutionMixin, '_write_product', written)
    def worker(handler, packet, **kwargs):
        key = packet['context']['call_id']
        with lock:
            evidence['active'] += 1
            evidence['peak'] = max(evidence['peak'], evidence['active'])
            evidence['workers'].append((key, get_ident()))
        if key == 'scheduled-managed':
            entered.set()
            assert (mixed_entered if overlap else release).wait(15), 'admission did not progress'
        elif key == 'mixed-managed':
            assert entered.is_set()
            order.append('managed')
            mixed_entered.set()
        with lock:
            evidence['active'] -= 1
        return {}
    monkeypatch.setattr('cpn.plugins.worker.execute_worker', worker)
    selected = PluginCatalog((BoundPlugin(PluginDefinition('synthetic', '1', (
        *(PluginOperation(name, name, {'type': 'object'}, {}, echo, effect=effect)
          for name, effect in zip(('first', 'second'), effects)),)), {}),))
    monkeypatch.setattr('cpn.plugins.catalog.load_catalog', lambda _: selected)
    tools = X(tools=('complete_interaction', 'write_file'))
    graph = G((
        N('seed', 'ROOT_NODE', (P('request', 'task'),), (P('result', 'seed'),), tools),
        N('scheduled', 'SCHEDULED_NODE', (P('request', 'seed'),), (P('result', 'branch'),), tools),
        N('mixed', 'MIXED_NODE', (P('request', 'seed'),), (P('result', 'branch'),), tools),
        N('join', 'JOIN_NODE', (P('left', 'branch'), P('right', 'branch')), (P('result', 'result'),), tools),
    ), (A('to_scheduled', E('seed', 'result'), E('scheduled', 'request')),
        A('to_mixed', E('seed', 'result'), E('mixed', 'request')),
        A('scheduled_join', E('scheduled', 'result'), E('join', 'left')),
        A('mixed_join', E('mixed', 'result'), E('join', 'right'))),
        E('seed', 'request'), E('join', 'result'))
    class Port:
        def request_once(self, attempt):
            request = json.loads(attempt.canonical_request_bytes)
            evidence['requests'].append(request)
            systems = '\n'.join(m['content'] for m in request['messages'] if m['role'] == 'system')
            prefix = 'Provider-writable semantic output_port_ids (exact): '
            port = next(line.removeprefix(prefix) for line in systems.splitlines() if line.startswith(prefix))
            assert ',' not in port
            node = 'join' if port == 'team.result' else next(n for n in ('seed', 'scheduled', 'mixed') if n in port)
            close = [{'id': node + '-write', 'name': 'write_file', 'arguments': json.dumps({
                'path': 'result.txt', 'description': 'Synthetic regression', 'content': 'ok',
                'output_port_id': port, 'outcome_id': 'complete'})},
                {'id': node + '-complete', 'name': 'complete_interaction', 'arguments': '{}'}]
            native = {'id': node + '-managed', 'name': 'first' if node == 'scheduled' else 'second', 'arguments': '{}'}
            if node == 'scheduled' and not any(m['role'] == 'tool' for m in request['messages']):
                return _response(tool_calls=[native], finish_reason='tool_calls')
            if node == 'mixed':
                assert entered.wait(15), 'scheduled worker never started'
                return _response(tool_calls=[close[0], native, close[1]], finish_reason='tool_calls')
            return _response(tool_calls=close, finish_reason='tool_calls')
        def close(self):
            pass
    config = _configure_offline_task(tmp_path, monkeypatch, Port())
    names = {'first': 'synthetic/first', 'second': 'synthetic/second'}
    catalog = ManagedPluginToolCatalog(selected, names, admitted_effects=tuple(sorted(set(effects))))
    policy = {'policy_id': 'managed_pure_parallel/v1', 'max_in_flight': capacity}
    if effects != ('pure', 'pure'):
        policy = {'policy_id': 'managed_conflict_domains/v1', 'max_in_flight': capacity,
                  'conflict_domains': [
                      {'registration_key': catalog.declaration(name).registration_key,
                       'effect': effect,
                       'reads': [domain] if effect == 'external_read' else [],
                       'writes': [domain] if effect == 'external_write' else [],
                       'unknown': False}
                      for name, effect, domain in zip(names, effects, domains)]}
    binding = {'tools': {name: {'selector': selector} for name, selector in names.items()},
               'admitted_effects': sorted(set(effects))}
    result = run_agent_task(AgentTaskSpec(tmp_path/'run', 'Offline shared pool regression.', (), config,
        workflow_graph=graph, max_attempts_per_stage=3, plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={'scheduled':binding, 'mixed':binding}, managed_tool_policy=policy))
    evidence['result'] = result
    (tmp_path/'evidence.json').write_text(json.dumps(evidence, indent=2, default=str))
    if cancel:
        from test_managed_plugin_tools import _managed_receipts
        from test_optional_managed_plugin_actions import _action_documents
        assert result['stop_reason'] == 'stopped_by_owner', result
        assert evidence['blocked_admission'] and evidence['ping_thread'] == owner_thread
        assert evidence['peak'] == 1 and not mixed_entered.is_set()
        assert order == ['write']
        assert ['mixed-write', 'mixed-managed', 'mixed-complete'] in settlements
        assert [(r['call_id'], r['state']) for r in _managed_receipts(tmp_path / 'run')] == [
            ('scheduled-managed', 'started'), ('scheduled-managed', 'returned')]
        managed = [d for kind, d in _action_documents(tmp_path / 'run') if kind == 'agent_action/v3']
        assert {d['tool_call_id']: d['outcome'] for d in managed} == {
            'scheduled-managed': 'returned', 'mixed-managed': 'rejected'}
        return
    assert result['stop_reason'] == 'terminal', result
    assert mixed_entered.is_set()
    assert evidence['peak'] == (2 if overlap else 1), evidence
    if not overlap:
        assert evidence['blocked_admission'] and evidence['ping_thread'] == owner_thread
    assert order == ['write', 'managed']
    assert ['mixed-write', 'mixed-managed', 'mixed-complete'] in settlements
    assert len(settlements) == 5
    assert dict(evidence['workers'])['mixed-managed'] != owner_thread
    assert dict(evidence['workers'])['scheduled-managed'] != owner_thread

    from test_managed_plugin_tools import _managed_receipts
    receipts = _managed_receipts(tmp_path / 'run')
    assert sorted((r['call_id'], r['state']) for r in receipts) == sorted(
        (call, state) for call in ('scheduled-managed', 'mixed-managed')
        for state in ('started', 'returned'))
    assert len(evidence['workers']) == 2  # Never replay an executed call.
    evidence.update(order=order, settlements=settlements,
                    receipts=[(r['call_id'], r['state']) for r in receipts])
    (tmp_path / 'evidence.json').write_text(json.dumps(evidence, indent=2, default=str))


@pytest.mark.parametrize('selected_policy', [False, True])
def test_interleaved_builtins_keep_order_and_single_settlement(tmp_path, monkeypatch, selected_policy):
    from dataclasses import replace
    from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy, ManagedToolScheduler
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
    from test_managed_tool_scheduler import _joint_fixture, _joint_calls, _joint_managed_actions
    from test_managed_plugin_tools import _plugin_catalog, _managed_receipts

    order = []
    worker_threads = []
    invokes = []
    invoke = ManagedPluginInvocationService.invoke
    write = WorkspaceExecutionMixin._write_product

    def worker(_handler, packet, **kwargs):
        order.append(packet['context']['call_id'])
        worker_threads.append(get_ident())
        return packet['arguments']['value'] * 2

    def invoked(self, *args, **kwargs):
        invokes.append(kwargs['call_id'])
        return invoke(self, *args, **kwargs)

    def written(self, *args, **kwargs):
        order.append('write')
        return write(self, *args, **kwargs)

    monkeypatch.setattr('cpn.plugins.worker.execute_worker', worker)
    monkeypatch.setattr(ManagedPluginInvocationService, 'invoke', invoked)
    monkeypatch.setattr(WorkspaceExecutionMixin, '_write_product', written)
    spec, port, threads, settlements = _joint_fixture(
        tmp_path, monkeypatch, _plugin_catalog(), {'double_value': 'synthetic/double'},
        ManagedSchedulerPolicy(), _joint_calls(['double_value'] * 2))
    calls = port.calls
    close = port.final_calls()
    port.calls = [calls[0], close[0], calls[1], close[1]]
    if not selected_policy:
        spec = replace(spec, managed_tool_policy=None)
        monkeypatch.setattr(ManagedToolScheduler, 'run',
                            lambda *a, **k: pytest.fail('policy-free turn entered scheduler'))
    result = run_agent_task(spec)
    assert result['stop_reason'] == 'terminal', result
    assert order == ['joint-0', 'write', 'joint-1']
    assert invokes == ([] if selected_policy else ['joint-0', 'joint-1'])
    assert all((thread != get_ident()) == selected_policy for thread in worker_threads)
    assert len(port.requests) == 1 and [n for _, n, _ in settlements] == [4]
    assert {thread for _, thread in threads} == {get_ident()}
    actions = _joint_managed_actions(tmp_path / 'run')
    assert [row['tool_call_ordinal'] for row in actions] == [0, 2]
    assert [row['output'] for row in actions] == [0, 2]
    assert len(_managed_receipts(tmp_path / 'run')) == 4


@pytest.mark.parametrize('failure', ['known', 'unknown', 'orphaned'])
def test_mixed_failure_settles_once_without_replay(tmp_path, monkeypatch, failure):
    unknown = failure != 'known'
    from cpn.plugins.managed_scheduler import ManagedConflictDomain, ManagedSchedulerPolicy
    from cpn.plugins.worker import WorkerFailure
    from test_managed_tool_scheduler import _joint_fixture, _joint_calls, _joint_managed_actions
    from test_managed_plugin_tools import _managed_receipts
    effect = 'external_write' if unknown else 'external_read'
    selected = PluginCatalog((BoundPlugin(PluginDefinition('synthetic', '1', (
        PluginOperation('echo', 'Echo', {'type': 'object'}, {}, echo, effect=effect),)), {}),))
    catalog = ManagedPluginToolCatalog(selected, {'echo': 'synthetic/echo'}, admitted_effects=(effect,))
    domain = dict(writes=('same',)) if unknown else dict(reads=('same',))
    policy = ManagedSchedulerPolicy('managed_conflict_domains/v1', 2, (
        ManagedConflictDomain(catalog.declaration('echo').registration_key, effect, **domain),))
    started = []
    if failure == 'orphaned':
        from cpn.plugins.managed_tools import ManagedPluginInvocationService
        prepare = ManagedPluginInvocationService.prepare

        def recovered(self, name, *, execution, call_id, arguments, **kwargs):
            declaration = self.catalog.declaration(name)
            execution = self._authorize(execution, declaration)
            key, refs = self._refs(execution, call_id)
            assert self._claim(execution, declaration, call_id, arguments, key, refs)
            rebuilt = ManagedPluginInvocationService(
                self.owner, self.kernel, self.repository, self.catalog)
            return prepare(rebuilt, name, execution=execution, call_id=call_id,
                           arguments=arguments, **kwargs)

        monkeypatch.setattr(ManagedPluginInvocationService, 'prepare', recovered)

    def worker(_handler, packet, **kwargs):
        started.append(packet['context']['call_id'])
        if len(started) == 1:
            raise WorkerFailure('deadline_exceeded' if unknown else 'handler_failed',
                                may_have_executed=unknown)
        return {'ok': True}

    monkeypatch.setattr('cpn.plugins.worker.execute_worker', worker)
    spec, port, threads, settlements = _joint_fixture(
        tmp_path, monkeypatch, selected, {'echo': 'synthetic/echo'}, policy,
        _joint_calls(['echo'] * 2), effects=(effect,))
    calls, close = port.calls, port.final_calls()
    port.calls = [calls[0], close[0], calls[1], close[1]]
    result = run_agent_task(spec)
    assert result['stop_reason'] == ('blocked_or_waiting' if unknown else 'terminal'), result
    assert started == ([] if failure == 'orphaned' else
                       ['joint-0'] if unknown else ['joint-0', 'joint-1'])
    assert len(port.requests) == 1 and [n for _, n, _ in settlements] == [4]
    actions = _joint_managed_actions(tmp_path / 'run')
    assert [row['outcome'] for row in actions] == (
        ['outcome_unknown', 'rejected'] if unknown else ['failed', 'returned'])
    assert len(_managed_receipts(tmp_path / 'run')) == (2 if unknown else 4)
    if failure == 'orphaned':
        assert actions[0]['error']['code'] == 'managed_terminal_observation_missing'


def test_mixed_policy_rejection_has_no_receipt(tmp_path, monkeypatch):
    from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy, ManagedToolScheduler
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    from test_managed_tool_scheduler import _joint_fixture, _joint_calls, _joint_managed_actions
    from test_managed_plugin_tools import _plugin_catalog, _managed_receipts
    from test_optional_managed_plugin_actions import _action_documents

    def unexpected(*args, **kwargs):
        pytest.fail('policy-rejected action reached scheduling or invocation')

    monkeypatch.setattr(ManagedToolScheduler, 'run', unexpected)
    monkeypatch.setattr(ManagedPluginInvocationService, 'prepare', unexpected)
    monkeypatch.setattr('cpn.plugins.worker.execute_worker', unexpected)
    spec, port, threads, settlements = _joint_fixture(
        tmp_path, monkeypatch, _plugin_catalog(effect='external_read'),
        {'double_value': 'synthetic/double'}, ManagedSchedulerPolicy(),
        _joint_calls(['double_value']), effects=('external_read',))
    close = port.final_calls()
    port.calls = [close[0], port.calls[0], close[1]]
    result = run_agent_task(spec)
    assert result['stop_reason'] == 'terminal', result
    assert len(port.requests) == 1 and [n for _, n, _ in settlements] == [3]
    assert _managed_receipts(tmp_path / 'run') == []
    managed, = _joint_managed_actions(tmp_path / 'run')
    assert managed['outcome'] == 'rejected'
    assert managed['started_receipt_ref'] is None
    assert managed['terminal_receipt_ref'] is None
    assert managed['non_delivery_reason'] == 'rejected_before_dispatch'
    assert 'outside the scheduler policy' in managed['error']['detail']
    actions = [document for _, document in _action_documents(tmp_path / 'run')]
    assert [(row['tool_call_id'], row['tool_call_ordinal'], row['state']) for row in actions] == [
        ('final-write', 0, 'ACTION_APPLIED'),
        ('joint-0', 1, 'ACTION_REJECTED'),
        ('final-complete', 2, 'COMPLETED')]
