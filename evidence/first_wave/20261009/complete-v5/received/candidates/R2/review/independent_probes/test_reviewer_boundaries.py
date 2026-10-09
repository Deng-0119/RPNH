"""Independent R2 review probes; no production changes, no alternative authority."""
import json
import socket
import subprocess

import pytest

from cpn.rpnh.iteration_profile import compile_iteration_profile
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from examples.rsi_workflows import deterministic as example
from examples.rsi_workflows.tests import test_runtime as fixture


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Independent Registry probes must not use socket/subprocess')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'socketpair', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def sequence_owner(tmp_path):
    registration = example.registration()
    prepared = compile_iteration_profile(example.profile(3), registration=registration)
    initial = OwnerInput(example.STATE, canonical_json({'round': 0, 'value': 0,
        'selected_candidate_ref': None, 'parent_state_ref': None, 'decision': None}), 'review initial')
    inputs = {'initial_state': initial}
    for number, status, target in ((1, 'unknown', 3), (2, 'known', 3), (3, 'known', 0)):
        inputs[f'round_{number}_evaluation_request'] = OwnerInput(example.REQUEST,
            canonical_json({'round': number, 'target': target, 'scorer': example.SCORER,
                'status': status, 'stop': False}), 'review synthetic request')
    return start_run(prepared.module, registration, run_dir=tmp_path/'run', task_input=initial,
        entry_inputs=inputs, budgets=prepared.budgets, model_condition='independent-pure-probe',
        owner_statement='Offline review fixture only', command_id='review:start')


def test_mixed_unknown_select_retain_exact_lineage_and_terminal(tmp_path):
    run = sequence_owner(tmp_path)
    prior_state = selected_ref = None
    for number, expected, value in ((1, 'retain', 0), (2, 'select', 1), (3, 'retain', 1)):
        proposer, proposed = fixture.step(run, f'round_{number}_proposer.run')
        proposer_input = fixture.by_schema(proposer, example.STATE).resource_ref
        if prior_state is not None:
            assert proposer_input == prior_state
        candidate_output, = (x for x in proposed.outputs if x.place.endswith('.candidate'))
        incumbent_output, = (x for x in proposed.outputs if x.place.endswith('.incumbent'))
        assert fixture.body(run, candidate_output)['parent_state_ref'] == example.resource_ref(proposer_input)
        evaluator, evaluated = fixture.step(run, f'round_{number}_evaluator.run')
        evaluation = fixture.body(run, evaluated.outputs[0])
        binding = evaluator.operation.operation_binding.operation_binding_ref
        assert evaluation['scorer_binding_ref'] == {
            'entity_type': binding.entity_type, 'logical_id': str(binding.entity_id),
            'version_id': str(binding.version_id)}
        assert evaluation['request_ref'] == example.resource_ref(fixture.by_schema(evaluator, example.REQUEST).resource_ref)
        assert evaluation['candidate_ref'] == example.resource_ref(candidate_output.resource_ref)
        selector, selected = fixture.step(run, f'round_{number}_selector.run')
        state = fixture.body(run, selected.outputs[0])
        assert selected.selected_outcome_id == expected and state['value'] == value
        assert state['parent_state_ref'] == example.resource_ref(incumbent_output.resource_ref)
        assert state['decision'] == {'outcome': expected, 'candidate_selected': expected == 'select',
            'incumbent_ref': example.resource_ref(incumbent_output.resource_ref),
            'candidate_ref': example.resource_ref(candidate_output.resource_ref),
            'evaluation_ref': example.resource_ref(evaluated.outputs[0].resource_ref),
            'request_ref': evaluation['request_ref'], 'scorer_binding_ref': evaluation['scorer_binding_ref']}
        if expected == 'select':
            selected_ref = example.resource_ref(candidate_output.resource_ref)
        assert state['selected_candidate_ref'] == selected_ref
        prior_state = selected.outputs[0].resource_ref
        if number < 3:
            assert run.terminal() is None
    assert run.terminal() is not None
    before = fixture.snapshot(run)
    observer = _RegistryCore(run._core.run_dir, create=False, read_only=True)
    result = read_run_execution(observer, _ResourceServiceKernel(observer),
        expected_run_ref=run.identity.run_ref, expected_net_ref=run.publication.net_ref)
    assert result.status == 'terminal' and result.terminal.run_outcome == 'complete'
    assert json.loads(read_run_terminal_bytes(observer, result)) == state
    result.cut.assert_unchanged(observer)
    assert fixture.snapshot(run) == before
    assert run._core.event_store.actual_model_call_counts() == (0, 0)


def test_selector_published_product_requires_settlement(tmp_path):
    run, _ = fixture.owner(tmp_path, rounds=1)
    fixture.step(run, 'round_1_proposer.run')
    fixture.step(run, 'round_1_evaluator.run')
    selector = fixture.start(run, 'round_1_selector.run')
    output = fixture.products(run, selector)
    before = fixture.snapshot(run)
    assert run.terminal() is None
    result = read_run_execution(run._core, _ResourceServiceKernel(run._core))
    assert result.status == 'running' and result.terminal is None
    assert fixture.snapshot(run) == before
    assert run.admit('round_1_selector.run', logical_tau=0, command_id='review:duplicate') is None
    assert fixture.snapshot(run) == before
    run.succeed(output, command_id='review:settle-selector')
    assert run.terminal() is not None


@pytest.mark.parametrize('unknown, scores', [(True, (0, 1)), (False, (None, None))])
def test_inconsistent_domain_status_scores_cannot_create_selection(tmp_path, monkeypatch, unknown, scores):
    original = example.evaluate
    def corrupt(*, execution):
        outcome, values = original(execution=execution)
        port, = values
        value = json.loads(values[port][0])
        value['candidate_score'], value['incumbent_score'] = scores
        return outcome, {port: (canonical_json(value),)}
    monkeypatch.setattr(example, 'evaluate', corrupt)
    run, _ = fixture.owner(tmp_path, rounds=1, unknown=unknown)
    fixture.step(run, 'round_1_proposer.run')
    fixture.step(run, 'round_1_evaluator.run')
    selector = fixture.start(run, 'round_1_selector.run')
    before = fixture.snapshot(run)
    with pytest.raises(ValueError, match='status does not match'):
        fixture.products(run, selector)
    assert fixture.snapshot(run) == before and run.terminal() is None
    assert read_run_execution(run._core, _ResourceServiceKernel(run._core)).status == 'running'


def test_raising_evaluator_leaves_physical_unknown_unsettled(tmp_path, monkeypatch):
    def failing(*, execution):
        raise RuntimeError('review synthetic role failure')
    monkeypatch.setattr(example, 'evaluate', failing)
    run, _ = fixture.owner(tmp_path, rounds=1)
    fixture.step(run, 'round_1_proposer.run')
    evaluator = fixture.start(run, 'round_1_evaluator.run')
    before = fixture.snapshot(run)
    with pytest.raises(RuntimeError, match='synthetic role failure'):
        fixture.products(run, evaluator)
    assert fixture.snapshot(run) == before
    assert run.admit('round_1_selector.run', logical_tau=0, command_id='review:failed-evaluator') is None
    assert fixture.snapshot(run) == before and run.terminal() is None
    result = read_run_execution(run._core, _ResourceServiceKernel(run._core))
    assert result.status == 'running' and result.terminal is None
