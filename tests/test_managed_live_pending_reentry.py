"""Native offline coverage of managed admission before worker dispatch."""
from __future__ import annotations

import json
from pathlib import Path

from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.plugins.managed_scheduler import ManagedConflictDomain, ManagedSchedulerPolicy
from cpn.plugins.managed_tools import (
    ManagedInvocationObservation, ManagedPluginInvocationService,
    ManagedPluginToolCatalog, PreparedManagedInvocation,
)
from cpn.rpnh.agent_tasks import run_agent_task
from test_managed_plugin_tools import _managed_receipts
from test_managed_tool_scheduler import _joint_fixture, _joint_managed_actions


def incident_handler(context, arguments):
    with (Path(context.config['world']) / 'dispatches.jsonl').open('a') as stream:
        stream.write(json.dumps({'call': context.call_id, 'arguments': arguments}) + '\n')
    return {'created': True}


def status_handler(context, arguments):
    with (Path(context.config['world']) / 'dispatches.jsonl').open('a') as stream:
        stream.write(json.dumps({'call': context.call_id, 'arguments': arguments}) + '\n')
    return {'ready': True}


def test_native_write_read_single_slot(tmp_path, monkeypatch):
    prepare = ManagedPluginInvocationService.prepare
    admissions = []

    def bound_prepare(self, name, *, execution, call_id, arguments, **kwargs):
        prepared = prepare(self, name, execution=execution, call_id=call_id,
                           arguments=arguments, **kwargs)
        if isinstance(prepared, PreparedManagedInvocation):
            # Same post-admission/pre-dispatch hook as the native release HOST.
            # Its JSON frame conversion accepts nested ordinary JSON arguments.
            assert not (tmp_path / 'dispatches.jsonl').exists() or name == 'status'
            active = self._active_calls[(id(self.core), prepared.receipt_key)]
            assert active.prepared is prepared and not active.future.done()
            rebuilt = ManagedPluginInvocationService(
                self.owner, self.kernel, self.repository, self.catalog)
            observer = prepare(rebuilt, name, execution=execution, call_id=call_id,
                               arguments=arguments, **kwargs)
            assert isinstance(observer, ManagedInvocationObservation)
            assert observer.future is active.future
            assert not observer.future.done()
            try:
                frame_arguments = json.loads(json.dumps(dict(arguments)))
            except TypeError as exc:
                print('PRE_DISPATCH_FAILURE', call_id, str(exc),
                      'live_owner=', active.prepared is prepared,
                      'dispatch_exists=', (tmp_path / 'dispatches.jsonl').exists())
                raise
            admissions.append((call_id, frame_arguments, active.future))
        return prepared

    monkeypatch.setattr(ManagedPluginInvocationService, 'prepare', bound_prepare)
    selected = PluginCatalog((BoundPlugin(PluginDefinition('release', '1', (
        PluginOperation('create_incident', 'Create incident', {'type': 'object'},
                        {'type': 'object'}, incident_handler, effect='external_write'),
        PluginOperation('status', 'Status', {'type': 'object'},
                        {'type': 'object'}, status_handler, effect='external_read'),
    ), config_schema={'type': 'object'}), {'world': str(tmp_path)}),))
    tools = {name: 'release/' + name for name in ('create_incident', 'status')}
    effects = ('external_write', 'external_read')
    catalog = ManagedPluginToolCatalog(selected, tools, admitted_effects=effects)
    policy = ManagedSchedulerPolicy('managed_conflict_domains/v1', 1, (
        ManagedConflictDomain(catalog.declaration('create_incident').registration_key,
                              'external_write', writes=('release-world',)),
        ManagedConflictDomain(catalog.declaration('status').registration_key,
                              'external_read', reads=('release-world',)),
    ))
    calls = [
        {'id': 'create-10', 'name': 'create_incident',
         'arguments': json.dumps({'validation_evidence': {'note_version': 'v2'}})},
        {'id': 'status-10', 'name': 'status', 'arguments': '{}'},
    ]
    spec, port, threads, settlements = _joint_fixture(
        tmp_path, monkeypatch, selected, tools, policy, calls, effects=effects)
    result = run_agent_task(spec)
    receipts = _managed_receipts(tmp_path / 'run')
    actions = _joint_managed_actions(tmp_path / 'run')
    print('RESULT', result['stop_reason'])
    print('RECEIPTS', [(r['call_id'], r['state']) for r in receipts])
    print('ACTIONS', [(a['tool_call_id'], a['outcome'], a.get('error')) for a in actions])
    dispatch_path = tmp_path / 'dispatches.jsonl'
    dispatches = ([json.loads(line) for line in dispatch_path.read_text().splitlines()]
                  if dispatch_path.exists() else [])
    print('HANDLER_DISPATCHES', dispatches)
    assert result['stop_reason'] == 'terminal'
    assert [(r['call_id'], r['state']) for r in receipts] == [
        ('create-10', 'started'), ('create-10', 'returned'),
        ('status-10', 'started'), ('status-10', 'returned'),
    ]
    assert [a['outcome'] for a in actions] == ['returned', 'returned']
    assert dispatches == [
        {'call': call['id'], 'arguments': json.loads(call['arguments'])} for call in calls]
    assert [call_id for call_id, _, _ in admissions] == ['create-10', 'status-10']
    assert admissions[0][1] == {'validation_evidence': {'note_version': 'v2'}}
    assert all(future.result()['outcome'] == 'returned' for _, _, future in admissions)
    assert [count for _, count, _ in settlements] == [2, 2]
    assert [m['tool_call_id'] for m in port.requests[1]['messages']
            if m.get('role') == 'tool'] == ['create-10', 'status-10']
