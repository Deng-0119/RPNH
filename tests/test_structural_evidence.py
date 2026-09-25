"""Offline evidence for existing structural contracts; no production changes.

The test HOST executes a deterministic business inspector through real Module
lowering, Registry admission, Harness completion and ordinary Petri Success.
No model transport, remote service, synthetic Registry authority or alternate
scheduler is used. Fault injection is explicitly at the publication boundary.
"""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import json

import pytest

from cpn.rpnh.compiler import compile_module
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.diagnostics import diagnose
from cpn.rpnh.harness import (
    Harness, HarnessBoundaryError, OperationDispatch, OperationProducts,
)
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.marking import MarkingStateError, TeamNetMarking
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.petri_contracts import (
    ArcDeclaration, ColourExpression, DeclarationError, InputVerdictGuard,
    PNFragment, PlaceDeclaration, PortBinding, PortDeclaration,
    TransitionDeclaration,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run


REQUEST = "application/structural_test_request/v1"
RESULT = "application/structural_test_result/v1"
CONFIG = "application/structural_test_config/v1"
COMPONENT = "test/structural-gate/v1"
EXECUTOR = "test/structural-executor/v1"
TERMINAL = "test/structural-terminal/v1"


def _lower_gate(config, context):
    # Ordinary registered products carry the selected string outcome colour.
    # A JSON boolean inside the resource is business data, not a PN colour.
    return PNFragment(
        places=(
            PlaceDeclaration("request", REQUEST, capacity=1),
            PlaceDeclaration("decision", REQUEST, capacity=1, colours=("allow", "deny")),
            PlaceDeclaration("accepted", RESULT, capacity=1),
            PlaceDeclaration("denied", RESULT, capacity=1),
        ),
        transitions=(
            TransitionDeclaration("inspect", "inspect"),
            TransitionDeclaration("execute", "execute",
                                  input_verdicts=(InputVerdictGuard("decision", "allow"),)),
            TransitionDeclaration("reject", "reject",
                                  input_verdicts=(InputVerdictGuard("decision", "deny"),)),
        ),
        arcs=(
            ArcDeclaration("request", "inspect", "input"),
            ArcDeclaration("decision", "inspect", "output", mode="produce", outcome="allow",
                           colour_expression=ColourExpression(value="allow")),
            ArcDeclaration("decision", "inspect", "output", mode="produce", outcome="deny",
                           colour_expression=ColourExpression(value="deny")),
            ArcDeclaration("decision", "execute", "input"),
            ArcDeclaration("decision", "reject", "input"),
            ArcDeclaration("accepted", "execute", "output", mode="produce", outcome="complete"),
            ArcDeclaration("denied", "reject", "output", mode="produce", outcome="complete"),
        ),
        ports=(PortBinding("request", "request"), PortBinding("accepted", "accepted"),
               PortBinding("denied", "denied")),
        operations=context.operations,
        internal_ports=(PortDeclaration("decision_out", "output", REQUEST),
                        PortDeclaration("decision_in", "input", REQUEST)),
        internal_bindings=(PortBinding("decision_out", "decision"),
                           PortBinding("decision_in", "decision")),
    )


def _business_executor(*, execution):
    """Business policy lives here, not in marking or Registry code."""
    request = json.loads(execution.operation.inputs[0].artifact.payload)
    name = execution.operation.firing.transition_id.rsplit(".", 1)[-1]
    if name == "inspect":
        return ("allow" if request["allow"] else "deny", "gate.decision_out", request)
    assert name in {"execute", "reject"}
    assert request["allow"] is (name == "execute")
    return "complete", ("gate.accepted" if name == "execute" else "gate.denied"), {
        "request_id": request["request_id"],
        "status": "accepted" if name == "execute" else "denied",
    }


def _registration(lower=_lower_gate):
    registration = Registration()
    schemas = {
        REQUEST: {"type": "object", "additionalProperties": False,
                  "properties": {"request_id": {"type": "string"}, "allow": {"type": "boolean"}},
                  "required": ["request_id", "allow"]},
        RESULT: {"type": "object", "additionalProperties": False,
                 "properties": {"request_id": {"type": "string"},
                                "status": {"enum": ["accepted", "denied"]}},
                 "required": ["request_id", "status"]},
        CONFIG: {"type": "object", "additionalProperties": False, "properties": {}},
    }
    for key, body in schemas.items():
        registration.register_schema(key, {**body, "$id": key,
            "$schema": "http://json-schema.org/draft-07/schema#"})
    registration.register_component(COMPONENT, lower,
        identity={"implementation_id": "structural_gate", "revision": "v1"},
        contracts={"config_schema": CONFIG})
    registration.register_executor(EXECUTOR, _business_executor,
        identity={"implementation_id": "structural_executor", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "structural_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration


def _module():
    binding = {"bucket_id": "verification", "budget_scope": "module", "finalization_scope": None}
    def operation(name, inputs, outputs, outcomes):
        return {"name": name, "executor": EXECUTOR, "inputs": inputs,
                "outputs": outputs, "tools": [], "config": {}, "request_port": None,
                "budget_binding": binding, "outcomes": outcomes}
    def outcome(name, port):
        return {"name": name, "products": [{"port": port}]}
    def terminal(operation_name, port):
        return {"key": TERMINAL, "source": {"component": "gate", "port": port},
                "operation": operation_name, "outcome": "complete",
                "config": {"run_outcome": "complete"}}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "StructuralEvidence",
        "components": [{"name": "gate", "key": COMPONENT,
            "config_schema": CONFIG, "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": REQUEST},
                      {"name": "accepted", "direction": "output", "schema": RESULT},
                      {"name": "denied", "direction": "output", "schema": RESULT}],
            "operations": [
                operation("inspect", ["request"], ["decision_out"],
                          [outcome("allow", "decision_out"), outcome("deny", "decision_out")]),
                operation("execute", ["decision_in"], ["accepted"], [outcome("complete", "accepted")]),
                operation("reject", ["decision_in"], ["denied"], [outcome("complete", "denied")]),
            ]}],
        "links": [], "entry": {"request": {"component": "gate", "port": "request"}},
        "exit": {"accepted": {"component": "gate", "port": "accepted"},
                 "denied": {"component": "gate", "port": "denied"}},
        "terminal": terminal("execute", "accepted"),
        "terminal_alternatives": [terminal("reject", "denied")],
        "required_schemas": [REQUEST, RESULT, CONFIG], "budgets": {},
        "budget_buckets": [{**binding, "max_attempts": 4}],
    })


def _owner(tmp_path, allow=True, *, lower=_lower_gate):
    module = _module()
    request = OwnerInput(REQUEST, canonical_json({"request_id": "request-A", "allow": allow}),
                         "Synthetic request; no credentials or external data")
    return start_run(module, _registration(lower), run_dir=tmp_path / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 4, 0, 4, 0),
        model_condition="offline-structural-evidence", owner_statement="Deterministic test policy",
        command_id="test:structural:fresh")


def _runtime(owner):
    executable, structure, marking = hydrate_module_runtime(owner._core)
    return executable, structure, marking, TeamNetMarking.from_authority(structure, marking)


def _start(owner, name):
    admitted = owner.admit(name, logical_tau=0, command_id="test:admit:" + name)
    assert admitted is not None
    return owner.start(admitted, command_id="test:start:" + name)


def _products(owner, execution):
    executor = owner.registration.resolve("executor", execution.operation.spec.executor_key)
    outcome, port, value = executor(execution=execution)
    return owner.products(execution, outcome_id=outcome, products={port: (canonical_json(value),)},
                          command_id="test:products:" + execution.operation.firing.transition_id)


def _immediate(callback):
    """A deterministic synchronous HOST, returning the real completed Future."""
    future = Future()
    try:
        future.set_result(callback())
    except BaseException as exc:
        future.set_exception(exc)
    return future


def _runner(owner, loop, calls, select_ready=None):
    def prepare(*, execution, **_kwargs):
        def invoke():
            calls.append(execution.operation.firing.transition_id)
            return OperationProducts(_products(owner, execution))
        return OperationDispatch(execution, invoke)
    return Harness(owner=owner, event_loop=loop, prepare_dispatcher=prepare,
                   submit_operation=_immediate, max_in_flight=1, select_ready=select_ready)


@pytest.mark.parametrize("allow", [True, False])
def test_inspector_routes_real_execution_and_preserves_exact_request(tmp_path, allow):
    owner = _owner(tmp_path, allow)
    assert set(_runtime(owner)[3].enabled_transitions()) == {"gate.inspect"}
    loop = OwnerEventLoop(owner, tmp_path / "owner.sock")
    calls = []
    try:
        result = _runner(owner, loop, calls).exact_execute()
    finally:
        loop.close()
    selected = "gate.execute" if allow else "gate.reject"
    assert calls == ["gate.inspect", selected]
    assert result.stop_reason == "terminal" and result.terminal_evidence_ref is not None
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    assert len(result.operation_execution_trace) == 2
    inspect_trace, branch_trace = result.operation_execution_trace
    checked_resource, = inspect_trace.output_resource_refs
    assert branch_trace.execution.operation.inputs[0].resource_ref == checked_resource
    inspected_request = json.loads(branch_trace.execution.operation.inputs[0].artifact.payload)
    assert inspected_request == {"request_id": "request-A", "allow": allow}
    final_resource, = branch_trace.output_resource_refs
    final = json.loads(owner._core.object_store.read_registered(
        owner._core.get_version(final_resource.resource_version_id)))
    assert final == {"request_id": "request-A", "status": "accepted" if allow else "denied"}
    core = owner._core
    events = core.event_store.list_events()
    settlements = [e for e in events if e.event_type == "transition_firing_settled/v1"]
    admissions = [e for e in events if e.event_type == "firing_admitted/v1"]
    assert len(settlements) == len(admissions) == 2
    assert admissions[0].ordinal < settlements[0].ordinal < admissions[1].ordinal < settlements[1].ordinal
    before = core.event_store.max_ordinal()
    view = project_registry_net(tmp_path / "run", catalog=core.catalog)
    assert core.event_store.max_ordinal() == before
    assert sum(n.get("runtime", {}).get("firing_count", 0) for n in view["nodes"]) == 2
    assert {n["id"] for n in view["nodes"] if n["kind"] == "transition"} == {
        "gate.inspect", "gate.execute", "gate.reject"}


def test_unenabled_gate_has_no_admission_or_state_change(tmp_path):
    owner = _owner(tmp_path)
    before = owner._core.event_store.max_ordinal()
    checkpoint = _runtime(owner)[2].checkpoint_ref
    for transition in ("gate.execute", "gate.reject"):
        assert owner.admit(transition, logical_tau=0, command_id="test:blocked:" + transition) is None
    with pytest.raises(ValueError, match="undeclared"):
        owner.admit("invented.transition", logical_tau=0, command_id="test:invented")
    assert owner._core.event_store.max_ordinal() == before
    assert _runtime(owner)[2].checkpoint_ref == checkpoint
    assert not owner._core.event_store.object_rows_by_type("transition_firing/v1")


def test_scheduler_cannot_turn_disabled_operation_into_enabled_one(tmp_path):
    owner = _owner(tmp_path)
    loop = OwnerEventLoop(owner, tmp_path / "owner.sock")
    calls = []
    before = owner._core.event_store.max_ordinal()
    try:
        runner = _runner(owner, loop, calls, select_ready=lambda **_: ("gate.execute",))
        with pytest.raises(HarnessBoundaryError, match="enabled subset"):
            runner.schedule_ready()
    finally:
        loop.close()
    assert not calls and owner._core.event_store.max_ordinal() == before


@pytest.mark.parametrize("allow", [True, False])
def test_opposite_verdict_cannot_claim_the_inspected_token(tmp_path, allow):
    owner = _owner(tmp_path, allow)
    execution = _start(owner, "gate.inspect")
    owner.succeed(_products(owner, execution), command_id="test:inspect-success")
    enabled = set(_runtime(owner)[3].enabled_transitions())
    selected, blocked = (("gate.execute", "gate.reject") if allow else ("gate.reject", "gate.execute"))
    assert enabled == {selected}
    before = owner._core.event_store.max_ordinal()
    assert owner.admit(blocked, logical_tau=1, command_id="test:wrong-colour") is None
    assert owner._core.event_store.max_ordinal() == before


@pytest.mark.parametrize("outcome,products,match", [
    ("invented", {}, "undeclared"),
    ("allow", {"gate.invented": (b"{}",)}, "invents"),
    ("allow", {}, "quantity"),
])
def test_invalid_outcome_or_product_cannot_publish_success(tmp_path, outcome, products, match):
    owner = _owner(tmp_path)
    execution = _start(owner, "gate.inspect")
    before = owner._core.event_store.max_ordinal()
    with pytest.raises(ValueError, match=match):
        owner.products(execution, outcome_id=outcome, products=products, command_id="test:invalid")
    assert owner._core.event_store.max_ordinal() == before
    assert not owner._core.event_store.object_rows_by_type("operation_result/v1")


def test_failed_success_publication_preserves_checkpoint_then_same_command_retries(tmp_path, monkeypatch):
    owner = _owner(tmp_path)
    execution = _start(owner, "gate.inspect")
    outputs = _products(owner, execution)
    core = owner._core
    before = core.event_store.max_ordinal()
    checkpoint = _runtime(owner)[2].checkpoint_ref
    original = core.event_store.publish_batch
    injected = []
    def fail_at_publication(**kwargs):
        if any(e.event_type == "transition_firing_settled/v1" for e in kwargs["events"]):
            injected.append(True)
            raise RuntimeError("injected-before-publish-batch")
        return original(**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(core.event_store, "publish_batch", fail_at_publication)
        with pytest.raises(RuntimeError, match="injected-before-publish-batch"):
            owner.succeed(outputs, command_id="test:retry-same-success")
    assert injected == [True]
    assert core.event_store.max_ordinal() == before
    assert _runtime(owner)[2].checkpoint_ref == checkpoint
    assert not core.event_store.object_rows_by_type("operation_result/v1")
    # Unregistered prewritten bytes may exist. They are not published authority.
    successor = owner.succeed(outputs, command_id="test:retry-same-success")
    assert successor.checkpoint_ref != checkpoint
    assert len(core.event_store.object_rows_by_type("operation_result/v1")) == 1
    assert len(core.event_store.list_events_by_type(("transition_firing_settled/v1",))) == 1
    assert set(_runtime(owner)[3].enabled_transitions()) == {"gate.execute"}


def test_diagnostic_report_cannot_admit_or_veto_operations(tmp_path):
    owner = _owner(tmp_path)
    def analyzer(snapshot):
        with pytest.raises(TypeError):
            snapshot["fabricated"] = True
        return {"warnings": ["Synthetic observation"], "reason": "No execution authority",
                "veto": True, "terminal": True, "global_liveness": "PROVED"}
    owner.registration.register_analyzer("test/observation/v1", analyzer,
        identity={"implementation_id": "observation", "revision": "v1"}, contracts={})
    snapshot = owner.snapshot()
    before = owner._core.event_store.max_ordinal()
    result = diagnose(snapshot, owner.registration, analyzer_keys=("test/observation/v1",))
    assert result["global_liveness"] == "UNKNOWN"
    assert result["status"] == "WARNING"
    assert not any(key in result for key in ("veto", "terminal"))
    assert owner._core.event_store.max_ordinal() == before
    assert set(_runtime(owner)[3].enabled_transitions()) == {"gate.inspect"}


def test_read_arc_does_not_silently_gain_consumed_verdict_semantics():
    def invalid_lower(config, context):
        fragment = _lower_gate(config, context)
        return replace(fragment, arcs=tuple(
            replace(a, mode="read") if (a.transition == "execute" and a.direction == "input") else a
            for a in fragment.arcs))
    with pytest.raises(DeclarationError, match="consumed or borrowed"):
        compile_module(_module(), _registration(invalid_lower))


@pytest.mark.parametrize("allow", [True, False])
def test_conflicting_boolean_colour_is_rejected_without_settlement(tmp_path, allow):
    """Retain the original fixture mistake as a negative contract test."""
    def incompatible_lower(config, context):
        fragment = _lower_gate(config, context)
        return replace(fragment,
            places=tuple(replace(p, colours=(True, False)) if p.name == "decision" else p
                         for p in fragment.places),
            transitions=tuple(replace(t, input_verdicts=tuple(
                replace(g, expected=(g.expected == "allow")) for g in t.input_verdicts))
                for t in fragment.transitions),
            arcs=tuple(replace(a, colour_expression=ColourExpression(value=(a.outcome == "allow")))
                       if a.transition == "inspect" and a.direction == "output" else a
                       for a in fragment.arcs))
    owner = _owner(tmp_path, allow, lower=incompatible_lower)
    execution = _start(owner, "gate.inspect")
    outputs = _products(owner, execution)
    assert outputs.outputs[0].verdict == ("allow" if allow else "deny")
    before = owner._core.event_store.max_ordinal()
    checkpoint = _runtime(owner)[2].checkpoint_ref
    with pytest.raises(MarkingStateError, match="contradicted its declared output colour"):
        owner.succeed(outputs, command_id="test:incompatible-colour")
    assert owner._core.event_store.max_ordinal() == before
    assert _runtime(owner)[2].checkpoint_ref == checkpoint
    assert not owner._core.event_store.object_rows_by_type("operation_result/v1")
    assert not owner._core.event_store.list_events_by_type(("transition_firing_settled/v1",))
