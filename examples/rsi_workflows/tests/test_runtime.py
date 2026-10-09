"""R2 real Registry steps plus the original Orchestrator with explicit transport.

The named step helper is a test probe, not a loop, scheduler or permission rule.
Native tests default to genuine AF_UNIX. --rsi-transport=pipe is a labelled D0
transport double reused unchanged from the existing tool_pipeline test fixture.
"""
from concurrent.futures import Future
import json
import socket
import subprocess

import pytest

from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.harness import OperationDispatch, OperationProducts
from cpn.rpnh.iteration_profile import IterationProfile, compile_iteration_profile
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from examples.rsi_workflows import deterministic as example


def owner(tmp_path, rounds=2, *, target=3, unknown=False, stop=False, terminal_outcomes=None):
    registration = example.registration()
    profile = example.profile(rounds)
    if terminal_outcomes is not None:
        value = profile.to_dict()
        value["terminal_outcomes"] = terminal_outcomes
        profile = IterationProfile.from_dict(value)
    prepared = compile_iteration_profile(profile, registration=registration)
    state = OwnerInput(example.STATE, canonical_json({"round": 0, "value": 0,
        "selected_candidate_ref": None, "parent_state_ref": None, "decision": None}), "Synthetic initial state")
    inputs = {"initial_state": state}
    for n in range(1, rounds + 1):
        inputs[f"round_{n}_evaluation_request"] = OwnerInput(example.REQUEST,
            canonical_json({"round": n, "target": target, "scorer": example.SCORER,
                            "status": "unknown" if unknown else "known", "stop": stop}),
            "Synthetic validation data; no model or external service")
    run = start_run(prepared.module,
        registration, run_dir=tmp_path / "run", task_input=state, entry_inputs=inputs,
        budgets=prepared.budgets, model_condition="synthetic-pure-integer-distance-v1",
        owner_statement="Deterministic offline fixture; no provider or external actions",
        command_id="rsi:test:start")
    return run, prepared


def enabled(run):
    _, structure, marking = hydrate_module_runtime(run._core)
    return set(TeamNetMarking.from_authority(structure, marking).enabled_transitions())


def start(run, transition):
    admission = run.admit(transition, logical_tau=0, command_id="admit:" + transition)
    assert admission is not None
    return run.start(admission, command_id="start:" + transition)


def products(run, execution):
    role = execution.operation.firing.transition_id.rsplit(".", 1)[0].rsplit("_", 1)[1]
    pure = {"proposer": example.propose, "evaluator": example.evaluate, "selector": example.select}[role]
    outcome, values = pure(execution=execution)
    return run.products(execution, outcome_id=outcome, products=values,
                        command_id="products:" + execution.operation.firing.transition_id)


def step(run, transition):
    execution = start(run, transition)
    outputs = products(run, execution)
    run.succeed(outputs, command_id="success:" + transition)
    return execution, outputs


def body(run, output):
    return json.loads(run._core.object_store.read_registered(
        run._core.get_version(output.resource_ref.resource_version_id)))


def by_schema(execution, schema):
    ports = {p.port_id for p in execution.operation.spec.input_ports if p.content_schema_id == schema}
    item, = (i for i in execution.operation.inputs if i.port_id in ports)
    return item


def snapshot(run):
    return run._core.event_store.max_ordinal(), hydrate_module_runtime(run._core)[2].checkpoint_ref


@pytest.fixture
def deny_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure Registry-step test must not use socket or subprocess")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "socketpair", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def test_missing_unsettled_or_report_evaluation_cannot_admit_selector(tmp_path, deny_external):
    run, _ = owner(tmp_path)
    assert enabled(run) == {"round_1_proposer.run"}
    before = snapshot(run)
    assert run.admit("round_1_selector.run", logical_tau=0, command_id="missing:all") is None
    assert snapshot(run) == before
    step(run, "round_1_proposer.run")
    report = tmp_path / "untrusted-report.json"
    report.write_text('{"round": 1, "evaluated": true, "winner": "candidate", "status": "terminal"}')
    before = snapshot(run)
    assert run.admit("round_1_selector.run", logical_tau=0, command_id="missing:evaluation") is None
    report.unlink()
    assert snapshot(run) == before
    execution = start(run, "round_1_evaluator.run")
    outputs = products(run, execution)
    before = snapshot(run)
    assert run.admit("round_1_selector.run", logical_tau=0, command_id="unsettled:evaluation") is None
    assert snapshot(run) == before
    assert run.terminal() is None
    read = read_run_execution(run._core, _ResourceServiceKernel(run._core))
    assert read.status == "running" and read.terminal is None
    run.succeed(outputs, command_id="settle:evaluation")
    assert enabled(run) == {"round_1_selector.run"}
    assert run._core.event_store.actual_model_call_counts() == (0, 0)


def test_exact_lineage_and_stale_previous_round_do_not_enable_next_selector(tmp_path, deny_external, record_property):
    run, _ = owner(tmp_path)
    proposer, proposed = step(run, "round_1_proposer.run")
    evaluator, evaluated = step(run, "round_1_evaluator.run")
    selector, selected = step(run, "round_1_selector.run")
    candidate = by_schema(evaluator, example.CANDIDATE)
    assert by_schema(selector, example.CANDIDATE).resource_ref == candidate.resource_ref
    assert by_schema(selector, example.EVALUATION).resource_ref == evaluated.outputs[0].resource_ref
    assert body(run, evaluated.outputs[0])["candidate_ref"] == example.resource_ref(candidate.resource_ref)
    assert body(run, evaluated.outputs[0])["request_ref"] == example.resource_ref(by_schema(evaluator, example.REQUEST).resource_ref)
    assert body(run, evaluated.outputs[0])["scorer_binding_ref"]["version_id"] == str(evaluator.operation.operation_binding.operation_binding_ref.version_id)
    final = body(run, selected.outputs[0])
    assert final["decision"]["evaluation_ref"] == example.resource_ref(evaluated.outputs[0].resource_ref)
    assert final["decision"]["candidate_ref"] == example.resource_ref(candidate.resource_ref)
    proposer2, _ = step(run, "round_2_proposer.run")
    assert by_schema(proposer2, example.STATE).resource_ref == selected.outputs[0].resource_ref
    before = snapshot(run)
    assert run.admit("round_2_selector.run", logical_tau=0, command_id="stale:previous-round") is None
    assert snapshot(run) == before
    # The old evaluation exists in this Registry, but no token occupies round 2's input place.
    assert enabled(run) == {"round_2_evaluator.run"}
    assert {p.content_schema_id for p in proposer.operation.spec.input_ports} == {example.STATE}
    assert {p.content_schema_id for p in proposer2.operation.spec.input_ports} == {example.STATE}
    record_property("exact_lineage", json.dumps({
        "evaluation": body(run, evaluated.outputs[0]),
        "selection": final, "selection_ref": example.resource_ref(selected.outputs[0].resource_ref),
        "round_2_state_ref": example.resource_ref(by_schema(proposer2, example.STATE).resource_ref)}, sort_keys=True))


@pytest.mark.parametrize("target,unknown,expected", [(3, False, "select"), (0, False, "retain"), (3, True, "retain")])
def test_select_retain_unknown_and_native_terminal_read(tmp_path, deny_external, target, unknown, expected):
    run, _ = owner(tmp_path, rounds=1, target=target, unknown=unknown)
    step(run, "round_1_proposer.run")
    step(run, "round_1_evaluator.run")
    _, outputs = step(run, "round_1_selector.run")
    assert outputs.selected_outcome_id == expected
    assert body(run, outputs.outputs[0])["value"] == (1 if expected == "select" else 0)
    assert run.terminal() is not None
    before = snapshot(run)
    observer = _RegistryCore(run._core.run_dir, create=False, read_only=True)
    read = read_run_execution(observer, _ResourceServiceKernel(observer),
        expected_run_ref=run.identity.run_ref, expected_net_ref=run.publication.net_ref)
    assert read.status == "terminal" and read.terminal.run_outcome == "complete"
    assert json.loads(read_run_terminal_bytes(observer, read)) == body(run, outputs.outputs[0])
    read.cut.assert_unchanged(observer)
    assert snapshot(run) == before
    assert run._core.writer_epoch == observer.writer_epoch


def test_stop_outcome_prevents_later_round_and_is_not_owner_stop(tmp_path, deny_external):
    run, _ = owner(tmp_path, rounds=3, stop=True)
    step(run, "round_1_proposer.run")
    step(run, "round_1_evaluator.run")
    _, outputs = step(run, "round_1_selector.run")
    assert outputs.selected_outcome_id == "stop"
    assert enabled(run) == set()
    before = snapshot(run)
    assert run.admit("round_2_proposer.run", logical_tau=0, command_id="after:stop") is None
    assert snapshot(run) == before
    assert run.terminal() is not None
    read = read_run_execution(run._core, _ResourceServiceKernel(run._core))
    assert read.status == "terminal" and read.terminal.run_outcome == "complete"
    assert len(run._core.event_store.object_rows_by_type("operation_result/v1")) == 3


def test_r1_supplies_terminal_bindings_without_runtime_overlay():
    registration = example.registration()
    prepared = compile_iteration_profile(example.profile(2), registration=registration)
    assert prepared.profile.to_dict()["terminal_outcomes"] == {
        "stop": "complete", "final_select": "complete", "final_retain": "complete"}
    assert all(t.config == {"run_outcome": "complete"}
               for t in (prepared.module.terminal, *prepared.module.terminal_alternatives))


def test_explicit_failed_terminal_is_never_relabelled_complete(tmp_path, deny_external):
    run, _ = owner(tmp_path, rounds=1, target=0, terminal_outcomes={
        "stop": "complete", "final_select": "complete", "final_retain": "failed"})
    step(run, "round_1_proposer.run")
    step(run, "round_1_evaluator.run")
    _, outputs = step(run, "round_1_selector.run")
    assert outputs.selected_outcome_id == "retain"
    assert run.terminal() is not None
    read = read_run_execution(run._core, _ResourceServiceKernel(run._core))
    assert read.status == "terminal" and read.terminal.run_outcome == "failed"


@pytest.mark.parametrize("field", ["candidate_ref", "parent_state_ref", "round"])
def test_mismatched_evaluation_is_not_selected(tmp_path, monkeypatch, deny_external, field):
    real_evaluate = example.evaluate
    def wrong_evaluate(*, execution):
        outcome, values = real_evaluate(execution=execution)
        port, = values
        value = json.loads(values[port][0])
        value[field] = (2 if field == "round" else
                        example.resource_ref(by_schema(execution, example.REQUEST).resource_ref))
        return outcome, {port: (canonical_json(value),)}
    monkeypatch.setattr(example, "evaluate", wrong_evaluate)
    run, _ = owner(tmp_path, rounds=1)
    step(run, "round_1_proposer.run")
    step(run, "round_1_evaluator.run")
    execution = start(run, "round_1_selector.run")
    before = snapshot(run)
    with pytest.raises(ValueError, match="same-round"):
        products(run, execution)
    assert snapshot(run) == before
    assert len(run._core.event_store.object_rows_by_type("operation_result/v1")) == 2
    assert run.terminal() is None


def _immediate(callback):
    future = Future()
    try:
        future.set_result(callback())
    except BaseException as exc:
        future.set_exception(exc)
    return future


@pytest.mark.parametrize("rounds,target,stop,expected_count,expected_value", [
    (1, 0, False, 3, 0), (2, 3, False, 6, 2),
    (4, 3, False, 12, 3), (3, 3, True, 3, 1),
])
def test_original_orchestrator_owns_multiround_execution(tmp_path, rsi_loop_factory,
        rounds, target, stop, expected_count, expected_value, record_property):
    run, prepared = owner(tmp_path, rounds, target=target, stop=stop)
    loop = rsi_loop_factory(run, tmp_path / "owner.sock")
    def prepare(*, execution, **kwargs):
        return OperationDispatch(execution, lambda: OperationProducts(products(run, execution)))
    try:
        result = Orchestrator(owner=run, event_loop=loop,
            prepare_dispatcher=prepare, submit_operation=_immediate).run()
    finally:
        loop.close()
    assert result.stop_reason == "terminal" and result.terminal_evidence_ref is not None
    assert len(result.operation_execution_trace) == expected_count
    assert [t.transition_id for t in result.operation_execution_trace] == [
        f"round_{n}_{role}.run" for n in range(1, (1 if stop else rounds) + 1)
        for role in ("proposer", "evaluator", "selector")]
    assert run._core.event_store.actual_model_call_counts() == (0, 0)
    assert prepared.budgets.ordinary_global_cap == prepared.budgets.task_total_hard_cap == 1
    assert all(bucket.max_attempts is None for bucket in prepared.module.budget_buckets)
    read = read_run_execution(run._core, _ResourceServiceKernel(run._core),
        expected_terminal_evidence_ref=result.terminal_evidence_ref)
    value = json.loads(read_run_terminal_bytes(run._core, read))
    assert value["value"] == expected_value
    assert value["round"] == (1 if stop else rounds)
    read.cut.assert_unchanged(run._core)
    record_property("runtime_evidence", json.dumps({
        "transport": rsi_loop_factory.__name__, "operations": len(result.operation_execution_trace),
        "returned_model_calls": list(run._core.event_store.actual_model_call_counts()),
        "terminal_evidence_version": str(result.terminal_evidence_ref.version_id),
        "terminal_result_version": str(read.terminal.result_ref.version_id), "state": value}, sort_keys=True))
