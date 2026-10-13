"""Projection against original offline Registry/runtime fixtures; no model calls."""
import json
from copy import deepcopy
from urllib.parse import urlencode

import pytest

from cpn.frontend.dashboard import RegistryDashboard, exact
from cpn.frontend.execution_nets import ExecutionQueryError, ExecutionStale
from cpn.frontend.server import handle_request
from cpn.rpnh.registry.execution_runtime import ExecutionRuntime, _ref
from test_execution_net_registry import _active_parent, _one_step_definition, _definition, _parent
from test_structural_evidence import _products, _start


def params_for(provider, parent):
    frame = provider.dashboard()
    return {'net_ref': exact(frame['source']['net_ref']),
            'checkpoint_ref': exact(frame['net']['marking']['checkpoint_ref']),
            'cut': frame['position']['cursor'], 'firing_ref': _ref(parent.business_firing_ref),
            'expected_source': {k: frame['source'][k] for k in ('task_id', 'run_dir')},
            'expected_capture': {'head_ordinal': frame['source']['verified_head_ordinal'],
                                 'writer_fencing_epoch': frame['source']['writer_fencing_epoch']}}


def route(params):
    return '/api/v2/execution-nets?' + urlencode({k: json.dumps(v) if isinstance(v, dict) else v for k, v in params.items()})


@pytest.fixture
def world(tmp_path):
    owner, parent, execution = _active_parent(tmp_path)
    provider = RegistryDashboard(tmp_path / 'one' / 'run', catalog=owner._core.catalog)
    runtime = ExecutionRuntime(owner._core)
    state = runtime.instantiate(parent=parent, definition=_definition(), idempotency_key='viewer:weighted')
    return owner, parent, execution, provider, runtime, state


def test_real_attached_topology_tokens_active_firing_and_read_immutability(world):
    owner, parent, _, provider, runtime, state = world
    initial = provider.execution_nets(**params_for(provider, parent))['children'][0]
    assert initial['definition']['input_arcs'] == runtime._definition_metadata(state.definition_ref, state.definition)['input_arcs']
    assert [(t['place'], t['token_ref']) for t in initial['tokens']] == [(t.place, _ref(t.token_ref)) for t in state.checkpoint.tokens]
    active = runtime.start(instance_ref=state.instance_ref, parent=parent,
        checkpoint_ref=state.checkpoint.checkpoint_ref, transition_id='split', idempotency_key='viewer:split')
    params = params_for(provider, parent)
    store = owner._core.event_store
    before = store.max_ordinal(), store.writer_epoch
    with store.connect() as db:
        counts = [db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('events', 'objects', 'transactions')]
    for _ in range(2):
        response = handle_request(provider, 'GET', route(params))
        assert response.status == 200, response.body
        value = json.loads(response.body)
        child, = value['children']
        assert value['publication_state'] == 'PROVISIONAL'
        assert child['checkpoint_ref'] == _ref(active.checkpoint.checkpoint_ref)
        assert child['tokens'] == []
        assert child['active_firings'][0]['execution_transition_firing_ref'] == _ref(active.checkpoint.active_firing_refs[0])
        assert not child['map_ready'] and not child['sealed']
        assert 'materialization_key' not in json.dumps(value)
        assert value['parent']['parent_invocation_ref'] == _ref(parent.invocation_ref)
        assert provider.execution_nets(**params, instance_ref=_ref(state.instance_ref))['children'] == value['children']
    assert handle_request(provider, 'HEAD', route(params)).body == b''
    assert handle_request(provider, 'POST', route(params)).status == 405
    assert (store.max_ordinal(), store.writer_epoch) == before
    with store.connect() as db:
        assert counts == [db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] for table in ('events', 'objects', 'transactions')]


def test_map_ready_then_original_success_seal(tmp_path):
    owner, parent, execution = _active_parent(tmp_path)
    provider = RegistryDashboard(tmp_path / 'one' / 'run', catalog=owner._core.catalog)
    runtime = ExecutionRuntime(owner._core)
    state = runtime.instantiate(parent=parent, definition=_one_step_definition(), idempotency_key='viewer:one')
    active = runtime.start(instance_ref=state.instance_ref, parent=parent,
        checkpoint_ref=state.checkpoint.checkpoint_ref, transition_id='finish', idempotency_key='viewer:start')
    done = runtime.settle(instance_ref=state.instance_ref, parent=parent,
        checkpoint_ref=active.checkpoint.checkpoint_ref, firing_ref=active.checkpoint.active_firing_refs[0],
        evidence_refs=(state.definition_ref,), idempotency_key='viewer:settle')
    child, = provider.execution_nets(**params_for(provider, parent))['children']
    assert child['map_ready'] and not child['sealed']
    assert child['evidence_refs'] == [_ref(state.definition_ref)]
    owner.succeed(_products(owner, execution), command_id='viewer:success')
    child, = provider.execution_nets(**params_for(provider, parent))['children']
    assert child['sealed'] and child['map_ready_checkpoint_ref'] == _ref(done.checkpoint.checkpoint_ref)
    assert child['terminal_mapping_ref']['entity_type'] == 'execution_terminal_mapping/v1'
    assert child['seal_event']['ordinal'] > child['attachment']['ordinal']
    # A real second parent in this very Registry cannot navigate the first child.
    next_parent = _parent(_start(owner, 'gate.execute'))
    next_params = params_for(provider, next_parent)
    assert provider.execution_nets(**next_params)['children'] == []
    with pytest.raises(ExecutionQueryError):
        provider.execution_nets(**next_params, instance_ref=_ref(state.instance_ref))


def test_reject_unregistered_crossparent_source_and_stale_selections(world, tmp_path):
    owner, parent, _, provider, runtime, state = world
    other_owner, other_parent, _ = _active_parent(tmp_path, 'other')
    other = ExecutionRuntime(other_owner._core).instantiate(parent=other_parent,
        definition=_one_step_definition(), idempotency_key='viewer:other')
    params = params_for(provider, parent)
    for ref in (_ref(other.instance_ref), {**_ref(state.instance_ref), 'version_id': 'execution_instance_version:' + 'f' * 32}):
        with pytest.raises(ExecutionQueryError):
            provider.execution_nets(**params, instance_ref=ref)
    bad = deepcopy(params)
    bad['firing_ref'] = _ref(other_parent.business_firing_ref)
    assert handle_request(provider, 'GET', route(bad)).status == 400
    bad = deepcopy(params)
    bad['expected_source']['run_dir'] = '/another-source'
    assert handle_request(provider, 'GET', route(bad)).status == 400
    bad = deepcopy(params)
    # This original fixture deliberately reuses the compiled net in both
    # Registries; change the exact version to test a different net selection.
    bad['net_ref']['version_id'] = 'net_instance_version:' + 'f' * 32
    assert handle_request(provider, 'GET', route(bad)).status == 400
    bad = deepcopy(params)
    bad['expected_capture']['writer_fencing_epoch'] += 1
    assert handle_request(provider, 'GET', route(bad)).status == 409
    assert handle_request(provider, 'GET', route(params) + '&cut=1').status == 400
    runtime.start(instance_ref=state.instance_ref, parent=parent,
        checkpoint_ref=state.checkpoint.checkpoint_ref, transition_id='split', idempotency_key='viewer:advance')
    with pytest.raises(ExecutionStale):
        provider.execution_nets(**params)


def test_access_change_and_mid_read_advance_discard_response(world, monkeypatch):
    owner, parent, _, provider, runtime, state = world
    params = params_for(provider, parent)
    def denied():
        raise PermissionError('revoked')
    monkeypatch.setattr(provider, '_bound', denied)
    assert handle_request(provider, 'GET', route(params)).status == 403
    monkeypatch.setattr(provider, '_bound', lambda: None)
    stable = provider._stable
    calls = 0
    def advance(core, observation):
        nonlocal calls
        calls += 1
        if calls == 2:
            runtime.start(instance_ref=state.instance_ref, parent=parent,
                checkpoint_ref=state.checkpoint.checkpoint_ref, transition_id='split', idempotency_key='viewer:during')
        stable(core, observation)
    monkeypatch.setattr(provider, '_stable', advance)
    assert handle_request(provider, 'GET', route(params)).status == 409
