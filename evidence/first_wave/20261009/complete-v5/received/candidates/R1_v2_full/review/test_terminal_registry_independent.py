"""Independent native-step terminal checks, not a scheduler or business executor."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import pytest
import cpn.rpnh.iteration_profile as api
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes
from cpn.rpnh.registry.schema_catalog import canonical_json

SRC = Path(api.__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('reviewed_author_terminal_fixture', SRC / 'tests/test_iteration_profile.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@pytest.fixture
def deny_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No socket, subprocess, provider or model in native-step review')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'socketpair', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def test_last_round_stop_uses_explicit_failed_mapping_after_nonterminal_retain(tmp_path, deny_external):
    data = fixture._document(2)
    data['terminal_outcomes'] = {'stop': 'failed', 'final_select': 'complete', 'final_retain': 'complete'}
    owner, prepared = fixture._registry_owner(tmp_path, data)
    fixture._registry_prepare_first_selection(owner)
    fixture._registry_probe(owner, 'round_1_selector.run', 'retain', ('round_1_selector.next',))
    assert owner.terminal() is None
    fixture._registry_probe(owner, 'round_2_proposer.run', 'proposed', ('round_2_proposer.incumbent', 'round_2_proposer.candidate'))
    fixture._registry_probe(owner, 'round_2_evaluator.run', 'evaluated', ('round_2_evaluator.evaluation',))
    fixture._registry_probe(owner, 'round_2_selector.run', 'stop', ('round_2_selector.stop',))
    terminal = owner.terminal()
    assert terminal is not None
    before = owner._core.event_store.max_ordinal()
    observer = _RegistryCore(owner._core.run_dir, create=False, read_only=True)
    read = read_run_execution(observer, _ResourceServiceKernel(observer), expected_run_ref=owner.identity.run_ref,
        expected_net_ref=owner.publication.net_ref, expected_terminal_evidence_ref=terminal)
    assert read.status == 'terminal'
    assert read.terminal.run_outcome == 'failed'
    assert json.loads(read_run_terminal_bytes(observer, read)) == {'ref': 'round_2_selector.stop'}
    assert len(owner._core.event_store.object_rows_by_type('operation_result/v1')) == 6
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    assert len(owner._core.event_store.object_rows_by_type('run_terminal_evidence/v1')) == 1
    assert owner.terminal() == terminal
    assert owner._core.event_store.max_ordinal() == before
    read.cut.assert_unchanged(observer)


def test_last_selector_provisional_product_cannot_publish_complete(tmp_path, deny_external):
    data = fixture._document(1)
    data['terminal_outcomes'] = dict.fromkeys(('stop', 'final_select', 'final_retain'), 'complete')
    owner, _ = fixture._registry_owner(tmp_path, data)
    fixture._registry_prepare_first_selection(owner)
    admission = owner.admit('round_1_selector.run', logical_tau=0, command_id='independent:admit')
    execution = owner.start(admission, command_id='independent:start')
    owner.products(execution, outcome_id='select', products={'round_1_selector.next':
        (canonical_json({'ref': 'provisional-only'}),)}, command_id='independent:products')
    before = owner._core.event_store.max_ordinal()
    assert owner.terminal() is None
    observer = _RegistryCore(owner._core.run_dir, create=False, read_only=True)
    read = read_run_execution(observer, _ResourceServiceKernel(observer), expected_run_ref=owner.identity.run_ref)
    assert read.status == 'running' and read.terminal is None
    assert not owner._core.event_store.object_rows_by_type('run_terminal_evidence/v1')
    assert owner._core.event_store.max_ordinal() == before
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    read.cut.assert_unchanged(observer)


def test_owner_stop_after_settled_terminal_product_cannot_be_overwritten(tmp_path, deny_external):
    from cpn.rpnh.registry.errors import ResourceIntegrityFault
    data = fixture._document(1)
    data['terminal_outcomes'] = dict.fromkeys(('stop', 'final_select', 'final_retain'), 'complete')
    owner, _ = fixture._registry_owner(tmp_path, data)
    fixture._registry_prepare_first_selection(owner)
    fixture._registry_probe(owner, 'round_1_selector.run', 'select', ('round_1_selector.next',))
    owner.record_owner_stop(idempotency_key='independent:owner-stopped-before-terminal-publication')
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(ResourceIntegrityFault, match='conflicting run authority'):
        owner.terminal()
    observer = _RegistryCore(owner._core.run_dir, create=False, read_only=True)
    read = read_run_execution(observer, _ResourceServiceKernel(observer))
    assert read.status == 'stopped_by_owner' and read.terminal is None
    assert not owner._core.event_store.object_rows_by_type('run_terminal_evidence/v1')
    assert owner._core.event_store.max_ordinal() == before
    read.cut.assert_unchanged(observer)
