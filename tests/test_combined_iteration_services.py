"""D0 RSI/services integration: original HOST, Registry and Orchestrator.

The existing memory IPC fixture is imported unchanged. Gateway waits are pumped
on the owning thread through the original submit/dispatch methods, so no worker
or OS socket is needed. No executor/result adapter hides a HOST ABI mismatch.
"""
from concurrent.futures import Future
import json

import pytest

from cpn.components.execution_services import ExecutionServices
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.iteration_profile import compile_iteration_profile
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from examples.rsi_workflows import deterministic as example
from registered_agent_owner_fixtures import install_owner_test_boundaries


@pytest.mark.parametrize("rounds,target,stop,outcomes,value", [
    pytest.param(2, 3, False, ["select", "select"], 2, id="select-two-rounds"),
    pytest.param(2, 0, False, ["retain", "retain"], 0, id="retain-two-rounds"),
    pytest.param(2, 3, True, ["stop"], 1, id="stop-before-second-round"),
    pytest.param(10, 0, False, ["retain"] * 10, 0, id="retain-ten-rounds"),
])
def test_original_rsi_through_execution_services(
        tmp_path, monkeypatch, record_property, rounds, target, stop, outcomes, value):
    boundaries = install_owner_test_boundaries(monkeypatch)
    from cpn.llm_adapters import factory, local_process, external_provider
    from cpn.components.registered_material_checks import RegisteredMaterialContext
    monkeypatch.setattr(factory, "build_llm_input_port", boundaries.forbid("port_factory"))
    for cls in (factory.BoundLLMInputPort, local_process.LocalProcessInputPort,
                external_provider.ExternalProviderInputPort, RegisteredMaterialContext):
        monkeypatch.setattr(cls, "__init__", boundaries.forbid(cls.__name__ + ".construct"))

    registration = example.registration()
    prepared = compile_iteration_profile(example.profile(rounds), registration=registration)
    state = OwnerInput(example.STATE, canonical_json({
        "round": 0, "value": 0, "selected_candidate_ref": None,
        "parent_state_ref": None, "decision": None}), "Synthetic initial state")
    inputs = {"initial_state": state}
    for n in range(1, rounds + 1):
        inputs[f"round_{n}_evaluation_request"] = OwnerInput(example.REQUEST,
            canonical_json({"round": n, "target": target, "scorer": example.SCORER,
                            "status": "known", "stop": stop}),
            "Synthetic validation data; no model or external service")
    owner = start_run(prepared.module, registration, run_dir=tmp_path / "run",
        task_input=state, entry_inputs=inputs, budgets=prepared.budgets,
        model_condition="synthetic-pure-integer-distance-v1",
        owner_statement="D0 memory IPC; original deterministic RSI HOST",
        command_id="combined-rsi:start")
    loop = OwnerEventLoop(owner, tmp_path / "owner.sock")
    services = ExecutionServices(owner=owner, event_loop=loop)
    prepared_transitions = []
    gateway_calls = []
    original_submit = services.gateway.submit

    def pump_gateway(name, *args, **kwargs):
        gateway_calls.append(name)
        future = original_submit(name, *args, **kwargs)
        loop.dispatch_ready(timeout=0)
        assert future.done(), "original owner must finish each inline D0 gateway request"
        return future

    monkeypatch.setattr(services.gateway, "submit", pump_gateway)

    def prepare(**kwargs):
        assert kwargs["event_loop"] is loop
        prepared_transitions.append(kwargs["execution"].operation.firing.transition_id)
        return services.prepare_dispatcher(**kwargs)

    def submit(callback):
        future = Future()
        try:
            future.set_result(callback())
        except Exception as exc:
            future.set_exception(exc)
        return future

    try:
        assert services._llm_input_port is services._registered_material_context is None
        runner = Orchestrator(owner=owner, event_loop=loop,
            prepare_dispatcher=prepare, submit_operation=submit)
        result = runner.run()
        if result.completion_error is not None:
            raise result.completion_error
        expected = [f"round_{n}_{role}.run" for n in range(1, len(outcomes) + 1)
                    for role in ("proposer", "evaluator", "selector")]
        assert prepared_transitions == expected
        assert [t.transition_id for t in result.operation_execution_trace] == expected
        assert result.stop_reason == "terminal" and result.terminal_evidence_ref is not None
        assert not owner.admission_paused, "RSI stop outcome is not an owner stop"
        read = read_run_execution(owner._core, _ResourceServiceKernel(owner._core),
            expected_terminal_evidence_ref=result.terminal_evidence_ref)
        final = json.loads(read_run_terminal_bytes(owner._core, read))
        assert read.status == "terminal" and read.terminal.run_outcome == "complete"
        assert final["round"] == len(outcomes) and final["value"] == value
        assert final["decision"]["outcome"] == outcomes[-1]
        decisions = []
        for entry in result.operation_execution_trace:
            if "_selector." in entry.transition_id:
                ref, = entry.output_resource_refs
                body = json.loads(owner._core.object_store.read_registered(owner._core.get_version(
                    ref.resource_version_id)))
                decisions.append(body["decision"]["outcome"])
        assert decisions == outcomes
        assert gateway_calls.count("register_operation_outputs") == len(expected)
        assert gateway_calls.count("record_registered_operation_completion") == len(expected)
        assert len(owner._core.event_store.object_rows_by_type("operation_result/v1")) == len(expected)
        _, structure, marking = hydrate_module_runtime(owner._core)
        assert not TeamNetMarking.from_authority(structure, marking).enabled_transitions()
        if stop:
            before = owner._core.event_store.max_ordinal()
            with pytest.raises(ValueError, match="current running execution authority"):
                owner.admit("round_2_proposer.run", logical_tau=0,
                            command_id="combined-rsi:after-stop")
            assert owner._core.event_store.max_ordinal() == before
        read.cut.assert_unchanged(owner._core)
        assert services._llm_input_port is services._registered_material_context is None
        assert services._input_bindings == {}
    finally:
        loop.close()
        counts = owner._core.event_store.actual_model_call_counts()
        record_property("d0_evidence", json.dumps({
            "transport": "D0 unchanged registered_agent_owner_fixtures memory IPC",
            "prepared_transitions": prepared_transitions, "gateway_calls": gateway_calls,
            "model_counts": list(counts), "boundary_counts": dict(boundaries.counts)}, sort_keys=True))
        assert counts == (0, 0)
        boundaries.assert_no_external_calls()
        assert all(channel.closed for channel in boundaries.sockets)
