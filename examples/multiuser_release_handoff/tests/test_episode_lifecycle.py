"""Synthetic driver controls, not four completed native business agents.

Run the actual formal driver and ThreadPool with declared fake User results and
mock native Source snapshots. No Registry, provider, or business execution runs.
"""
from contextlib import nullcontext
import io
import json
import threading
from types import SimpleNamespace as NS

import pytest

from cpn.rpnh.registry import module_execution, module_runtime
from examples.multiuser_release_handoff import episode, viewer_export


def token(place, *, epoch=7, consumed_by=None, claims=()):
    return NS(state=NS(place=place, epoch=epoch, consumed_by=consumed_by,
                       lease_claims=claims))


@pytest.fixture
def driver(tmp_path, monkeypatch):
    captures, owner_stops, backends = [], [], []
    marking = NS(epoch=7, tokens=[])
    active = []
    source_core = object()
    source_owner = NS(
        _core=source_core,
        record_owner_stop=lambda **kw: owner_stops.append(kw),
    )
    current = {'version': 'synthetic-note-v2'}
    source = NS(owner=source_owner, current=lambda: current)
    users = {role: NS(role=role, owner=NS(
        identity=NS(task_ref='synthetic-task-' + role),
        principal_ref='synthetic-principal-' + role)) for role in episode.ROLES}
    specs = {role: NS(workflow_graph=None) for role in episode.ROLES}
    plan = {'roles': {role: [{'stage_id': role}] for role in episode.ROLES}}
    terminal = {'stop_reason': 'terminal', 'output': '{}',
                'terminal_result_ref': 'synthetic-result'}
    control = NS(peer_stops=[], returned=None, residual=None)
    barrier = threading.Barrier(4)

    def run_user(user, spec, *, interruption_requested, **kwargs):
        if user is designer:
            return dict(terminal)
        barrier.wait(timeout=3)
        if control.returned is not None:
            if user.role == 'operations':
                return dict(control.returned)
            # Bound the pre-fix failure: peers await the same shared stop used
            # by HOST waits, then return an explicitly synthetic observation.
            observed = backends[-1].stop.wait(timeout=1)
            control.peer_stops.append(observed)
            return {'stop_reason': 'synthetic_stop_observed' if observed
                    else 'synthetic_peer_timeout'}
        return dict(terminal)

    def backend(root, users, *, stop):
        value = NS(source=source, stop=stop, prepared=lambda *a: None,
                   events=io.StringIO(), world=NS(snapshot=lambda: {}),
                   reads={}, pending_edit=None,
                   confirmations={r: {'note_version': current['version']}
                                  for r in ('qa', 'support')})
        if control.residual == 'host_read':
            value.reads['synthetic-read'] = object()
        if control.residual == 'host_edit':
            value.pending_edit = object()
        backends.append(value)
        return value

    def hydrate(core):
        assert core is source_core
        return NS(net_ref='synthetic-source-net'), None, marking

    def firings(core, net_ref, *, require_current_writer=True):
        assert core is source_core and net_ref == 'synthetic-source-net'
        return tuple(active)

    designer = object()
    monkeypatch.setattr(episode.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(episode, 'actor_contracts', lambda task: {})
    monkeypatch.setattr(episode, 'designer_prompt', lambda *a: 'synthetic-design')
    monkeypatch.setattr(episode, 'AgentTaskSpec', lambda **kw: NS(**kw))
    monkeypatch.setattr(episode, 'create_user', lambda *a, **kw: designer)
    monkeypatch.setattr(episode, 'run_user', run_user)
    monkeypatch.setattr(episode, 'parse_design', lambda text: plan)
    monkeypatch.setattr(episode, 'user_specs', lambda *a: specs)
    monkeypatch.setattr(episode, 'prepared_users', lambda *a: users)
    monkeypatch.setattr(episode, 'ReleaseBackend', backend)
    monkeypatch.setattr(episode, 'ReleaseServer', lambda *a: nullcontext())
    monkeypatch.setattr(module_runtime, 'hydrate_module_runtime', hydrate)
    monkeypatch.setattr(module_execution, 'active_module_firings', firings)
    monkeypatch.setattr(viewer_export, 'capture', lambda owner, destination:
                        captures.append((owner, destination, bool(owner_stops))))

    def run(name):
        root = tmp_path / name
        root.mkdir()
        code = episode.formal(root, root / 'synthetic-execution.json', {})
        return code, json.loads((root / 'RESULT.json').read_text())

    return NS(run=run, control=control, marking=marking, active=active,
              captures=captures, owner_stops=owner_stops, backends=backends,
              source_owner=source_owner)


def test_returned_nonterminal_stops_awaiting_peers(driver):
    original = {'stop_reason': 'budget_exhausted', 'output': None}
    driver.control.returned = original
    code, result = driver.run('nonterminal')
    assert driver.control.peer_stops == [True, True, True]
    assert code == 2 and result['status'] == 'partial'
    assert result['roles']['operations'] == original
    assert result['manual_hold'] is True
    assert not driver.owner_stops


def test_live_claim_and_other_residuals_refuse_complete(driver):
    for residual in ('lease_claim', 'active_firing', 'read_request',
                     'write_request', 'host_read', 'host_edit'):
        driver.marking.tokens[:] = []
        driver.active[:] = []
        driver.control.residual = residual
        if residual == 'lease_claim':
            driver.marking.tokens.append(token('source.lease', claims=('read-a', 'read-b')))
        elif residual == 'active_firing':
            driver.active.append(object())
        elif residual in ('read_request', 'write_request'):
            place = 'source.request2' if residual == 'read_request' else 'source.write_request'
            driver.marking.tokens.append(token(place))
        code, result = driver.run(residual)
        assert code == 2 and result['status'] == 'partial', residual
        assert not driver.owner_stops, residual
        state = result['source_quiescence']
        assert state == {
            'quiescent': residual in ('host_read', 'host_edit'),
            'active_firings': int(residual == 'active_firing'),
            'pending_requests': int(residual in ('read_request', 'write_request')),
            'lease_claims': 2 if residual == 'lease_claim' else 0,
        }
        assert result['source_owner_stopped'] is False
        assert result['active_reads'] == (['synthetic-read'] if residual == 'host_read' else [])
        assert result['source_pending_edit'] == (residual == 'host_edit')
        assert result['manual_hold'] is False
        # Inspection must preserve the unresolved native and HOST facts.
        assert len(driver.active) == int(residual == 'active_firing')
        if residual == 'lease_claim':
            assert driver.marking.tokens[0].state.lease_claims == ('read-a', 'read-b')
        elif residual in ('read_request', 'write_request'):
            assert driver.marking.tokens[0].state.consumed_by is None
        assert (driver.backends[-1].pending_edit is not None) == (residual == 'host_edit')


def test_clean_closeout_owner_stops_source_before_final_capture(driver):
    # Old epochs and consumed tokens cannot block current Source quiescence.
    driver.marking.tokens[:] = [
        token('source.request0', epoch=6, claims=('old',)),
        token('source.write_request', consumed_by='synthetic-firing', claims=('spent',)),
        token('source.lease'),
    ]
    code, result = driver.run('clean')
    assert code == 0 and result['status'] == 'complete'
    assert len(driver.owner_stops) == 1
    assert driver.owner_stops[0]['idempotency_key']
    assert result['source_quiescence'] == {
        'quiescent': True, 'active_firings': 0, 'pending_requests': 0, 'lease_claims': 0,
    }
    assert result['source_owner_stopped'] is True
    source_captures = [(path.name, stopped) for owner, path, stopped in driver.captures
                       if owner is driver.source_owner]
    assert source_captures == [('initial', False), ('final', True)]
