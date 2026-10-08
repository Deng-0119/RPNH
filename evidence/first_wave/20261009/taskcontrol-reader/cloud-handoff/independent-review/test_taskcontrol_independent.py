"""Independent H2a checks. Temporary real Registries, no dispatch/socket/writer on reads."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from test_terminal_identity import owner_at, finish, start, reenter, fingerprint, forbidden
from cpn.rpnh.agent_tasks import AgentTaskSpec, AgentStage
from cpn.rpnh.task_control import TaskControl, TaskHandle
from cpn.rpnh.registry.event_store import EventStore
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.run_authority import RunReadCut
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json


def facade(tmp_path, owner):
    root = tmp_path / 'control'
    control = TaskControl(root, channel_ready=lambda _: False)
    spec = AgentTaskSpec(owner._core.run_dir, 'Read only', (AgentStage('worker', 'Read only'),), tmp_path / 'unused.json')
    handle = TaskHandle('task-fixture', 'single_agent', owner._core.run_dir,
                        owner._core.run_dir / 'owner.sock', root / 'unused.log', spec,
                        SimpleNamespace(poll=lambda: 0))
    control._tasks[handle.task_id] = handle
    return control, handle


def test_read_never_writes_and_preserves_exact_shapes(tmp_path):
    owner = owner_at(tmp_path / 'run'); expected = finish(owner)
    control, handle = facade(tmp_path, owner)
    before = fingerprint(owner)
    with patch.object(_RegistryCore, 'begin', forbidden), patch('socket.socket', forbidden), \
            patch('cpn.rpnh.run.resume_run', forbidden), \
            patch('cpn.rpnh.registry.observer_access.issue_observer_access', forbidden):
        result = control.result(handle.task_id)
        status = control._read_terminal_status(handle.run_dir)
    assert set(result) == {'task_id', 'kind', 'terminal_evidence_ref', 'terminal_result_ref', 'run_outcome', 'execution_generation', 'output', 'actual_model_call_counts'}
    assert set(status) == {'task_ref', 'execution_status', 'checkpoint_ref', 'execution_generation', 'terminal_evidence_count', 'final_result_index_count', 'actual_model_call_counts'}
    assert result['terminal_evidence_ref'] == expected
    assert result['output'] == 'canonical final answer'
    assert status['task_ref'] == str(owner._core.task_id)
    assert result['actual_model_call_counts'] == status['actual_model_call_counts'] == [0, 0]
    assert fingerprint(owner) == before


@pytest.mark.parametrize('endpoint', ['result', 'status'])
@pytest.mark.parametrize('field', ['head', 'epoch'])
def test_same_cut_recheck_after_accounting(tmp_path, monkeypatch, endpoint, field):
    owner = owner_at(tmp_path / 'run'); finish(owner)
    control, handle = facade(tmp_path, owner)
    before = fingerprint(owner)
    real_counts = EventStore.actual_model_call_counts
    real_head = EventStore.max_ordinal
    real_epoch = EventStore.writer_epoch
    def move(self):
        value = real_counts(self)
        if field == 'head':
            monkeypatch.setattr(EventStore, 'max_ordinal', lambda store: real_head(store) + 1)
        else:
            monkeypatch.setattr(EventStore, 'writer_epoch', property(lambda store: real_epoch.fget(store) + 1))
        return value
    monkeypatch.setattr(EventStore, 'actual_model_call_counts', move)
    with pytest.raises(RuntimeError, match='Registry advanced'):
        control.result(handle.task_id) if endpoint == 'result' else control._read_terminal_status(handle.run_dir)
    monkeypatch.undo()
    assert fingerprint(owner) == before


def test_reopened_generation_never_returns_old_terminal(tmp_path):
    owner = owner_at(tmp_path / 'run'); old = finish(owner)
    owner = reenter(owner)
    control, handle = facade(tmp_path, owner)
    before = fingerprint(owner)
    with pytest.raises(RuntimeError, match='no registered terminal'):
        control.result(handle.task_id)
    status = control._read_terminal_status(handle.run_dir)
    assert status['execution_generation'] == 1
    assert status['terminal_evidence_count'] == status['final_result_index_count'] == 1
    assert fingerprint(owner) == before
    current = _ref_payload(owner.terminal())
    result = control.result(handle.task_id)
    assert current != old and result['terminal_evidence_ref'] == current
    assert result['execution_generation'] == 1
    assert control._read_terminal_status(handle.run_dir)['terminal_evidence_count'] == 2


def test_legacy_large_result_remains_readable(tmp_path):
    owner = owner_at(tmp_path / 'run')
    execution = start(owner)
    value = 'x' * (4 * 1024 * 1024 + 1)
    outputs = owner.products(execution, outcome_id='complete', products={'hub_finalize.result': (canonical_json(value),)}, command_id='products')
    owner.succeed(outputs, command_id='success'); owner.terminal()
    control, handle = facade(tmp_path, owner)
    assert control.result(handle.task_id)['output'] == value


def test_status_socket_snapshot_remains_independent_observation(tmp_path, monkeypatch):
    owner = owner_at(tmp_path / 'run'); finish(owner)
    control, handle = facade(tmp_path, owner)
    control._channel_ready = lambda _: True
    snapshot = {key: key + '-owner-observation' for key in ('run_ref','task_ref','net_ref','checkpoint_ref','enabled_transitions','active_firings')}
    monkeypatch.setattr('cpn.rpnh.task_control.ControlClient', lambda _: SimpleNamespace(snapshot=lambda: deepcopy(snapshot)))
    result = control.status(handle.task_id)
    assert result['process_status'] == 'EXITED' and result['return_code'] == 0
    assert result['registry'] == snapshot


@pytest.mark.parametrize('endpoint', ['result', 'status'])
def test_terminal_descriptor_bytes_are_shared_authority(tmp_path, endpoint):
    owner = owner_at(tmp_path / 'run'); expected = finish(owner)
    ref = _version_from_payload(expected)
    prepared = owner._core.get_version(ref.version_id)
    document = deepcopy(dict(prepared.metadata))
    document['run_ref']['logical_id'] = 'run:' + '0' * 32
    raw = canonical_json(document)
    assert len(raw) == prepared.size
    owner._core.object_store.path_for_version(ref.version_id).write_bytes(raw)
    control, handle = facade(tmp_path, owner)
    before = fingerprint(owner)
    with pytest.raises(RuntimeError, match='bytes differ from registered metadata'):
        control.result(handle.task_id) if endpoint == 'result' else control._read_terminal_status(handle.run_dir)
    assert fingerprint(owner) == before


def test_default_descriptor_reads_use_registered_size_and_explicit_budget_unchanged(tmp_path, monkeypatch):
    from cpn.rpnh.registry.object_store import ObjectStore
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.run_authority import read_run_execution
    owner = owner_at(tmp_path / 'run'); finish(owner)
    core = _RegistryCore(owner._core.run_dir, create=False, read_only=True, catalog=owner._core.catalog)
    original = ObjectStore.read_registered
    calls = []
    def spy(self, prepared, *, max_bytes=None):
        if prepared.object_type != 'resource_version/v1':
            calls.append((prepared.object_type, prepared.size, max_bytes))
        return original(self, prepared, max_bytes=max_bytes)
    monkeypatch.setattr(ObjectStore, 'read_registered', spy)
    read_run_execution(core, _ResourceServiceKernel(core))
    assert len(calls) == 5 and all(size == bound for _, size, bound in calls)
    calls.clear()
    read_run_execution(core, _ResourceServiceKernel(core), max_descriptor_bytes=8 * 1024 * 1024)
    assert len(calls) == 5 and all(bound == 8 * 1024 * 1024 for _, _, bound in calls)
