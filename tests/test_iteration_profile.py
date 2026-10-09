"""Inert API checks plus explicit socket-free Registry terminal contract probes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

import pytest

from cpn.components.basic import register_basic_components
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.iteration_profile import (
    IterationProfile, compile_iteration_profile, iteration_profile_schema_data,
    load_iteration_profile,
)
from cpn.rpnh.petri_contracts import DeclarationError
from cpn.rpnh.registration import Registration, RegistrationError
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError


EXAMPLE = Path(__file__).resolve().parents[1] / "cpn/examples/iteration_profile/propose_evaluate_select.json"
CONFIG = "application/iteration_empty_config/v1"
TERMINAL_CONFIG = "application/iteration_terminal_config/v1"


def _must_not_execute(*args, **kwargs):
    raise AssertionError("Inert compilation cannot execute, publish or contact anything")


def _document(rounds=2):
    value = json.loads(EXAMPLE.read_text())
    value["rounds"] = rounds
    value["native_model_call_budget"]["maximum"] = 1
    return value


def _registration(value=None, *, config=None, missing=None, terminal_config=None):
    value = _document() if value is None else value
    registration = Registration()
    register_basic_components(registration)
    for schema in sorted(set(value["schemas"].values())):
        if schema != missing:
            registration.register_schema(schema, {
                "$id": schema, "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object", "properties": {"ref": {"type": "string"}},
                "required": ["ref"], "additionalProperties": False,
            })
    registration.register_schema(CONFIG, {
        "$id": CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
        **({"type": "object", "additionalProperties": False} if config is None else config),
    })
    registration.register_schema(TERMINAL_CONFIG, {
        "$id": TERMINAL_CONFIG, "$schema": "http://json-schema.org/draft-07/schema#",
        **({"type": "object", "properties": {"run_outcome": {"enum": ["complete", "failed"]}},
            "required": ["run_outcome"], "additionalProperties": False}
           if terminal_config is None else terminal_config),
    })
    for role in value["roles"].values():
        if role["executor_key"] != missing:
            registration.register_executor(role["executor_key"], _must_not_execute,
                identity={"implementation_id": "test.inert", "revision": "v1"},
                contracts={"config_schema": CONFIG, "transport": "deterministic",
                           "input_ports": None, "output_ports": None})
    if value["terminal_key"] != missing:
        registration.register_tool(value["terminal_key"], _must_not_execute,
            identity={"implementation_id": "test.inert", "revision": "v1"},
            contracts={"config_schema": TERMINAL_CONFIG, "binding_protocol": "rpnh/module_terminal/v1"})
    return registration


def _prepared(rounds=2):
    value = _document(rounds)
    return compile_iteration_profile(IterationProfile.from_dict(value), registration=_registration(value))


def test_profile_roundtrip_uses_optional_existing_catalog():
    schemas, types, paths = iteration_profile_schema_data()
    assert types == ()
    assert set(schemas) == {"rpnh/iteration_profile/v1"}
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    catalog.validate_schema_ref("rpnh/iteration_profile/v1", _document())
    profile = load_iteration_profile(EXAMPLE)
    assert profile.to_dict() == _document()
    changed = profile.to_dict()
    changed["rounds"] = 10
    assert profile.to_dict()["rounds"] == 2
    assert IterationProfile.from_json(profile._json) == profile


@pytest.mark.parametrize("rounds", [1, 2, 4, 32])
def test_unrolled_materials_match_original_compiler_deterministically(rounds):
    value = _document(rounds)
    profile, registration = IterationProfile.from_dict(value), _registration(value)
    first = compile_iteration_profile(profile, registration=registration)
    second = compile_iteration_profile(profile, registration=registration)
    assert first.compiled.to_dict() == second.compiled.to_dict()
    assert first.compiled.to_dict() == compile_module(first.module, registration).to_dict()
    assert load_compiled_net(first.compiled.to_json()).to_dict() == first.compiled.to_dict()
    assert len(first.module.components) == 3 * rounds
    assert len(first.compiled.symbolic.transitions) == 3 * rounds
    assert len(first.source_map) == 3 * rounds
    assert {item.round for item in first.source_map} == set(range(1, rounds + 1))
    assert set(first.module.entry) == {"initial_state", *(f"round_{n}_evaluation_request" for n in range(1, rounds + 1))}
    assert {item.name for item in first.required_entry_bindings} == set(first.module.entry)
    assert first.required_model_bindings == ()
    assert first.budgets.ordinary_global_cap == 1
    assert first.budgets.task_total_hard_cap == 1
    assert first.budgets.terminal_quota == first.budgets.finalization_budget == 0
    assert [asdict(bucket) for bucket in first.module.budget_buckets] == list(first.budgets.budget_buckets)
    assert all(bucket.max_attempts is None for bucket in first.module.budget_buckets)
    assert not first.module.budgets  # No parallel USD/token or round counter.
    assert not any(hasattr(first, name) for name in ("status", "ready_queue", "current_round", "best_model", "run", "publish"))


def test_declared_ports_and_arcs_carry_all_stage_dependencies():
    prepared = _prepared()
    net = prepared.compiled.symbolic
    operations = {operation.name: operation for operation in net.operations}
    candidate = net.port_places["round_1_proposer.candidate"]
    evaluation = net.port_places["round_1_evaluator.evaluation"]
    assert candidate == net.port_places["round_1_evaluator.candidate"] == net.port_places["round_1_selector.candidate"]
    assert evaluation == net.port_places["round_1_selector.evaluation"]
    assert net.port_places["round_1_selector.next"] == net.port_places["round_2_proposer.state"]
    assert all(place.capacity == 1 and not place.initial_tokens for place in net.places)
    arcs = {(a.place, a.transition, a.direction, a.mode, a.outcome) for a in net.arcs}
    assert (candidate, "round_1_evaluator.run", "input", "read", None) in arcs
    assert (candidate, "round_1_selector.run", "input", "consume", None) in arcs
    assert (evaluation, "round_1_selector.run", "input", "consume", None) in arcs
    assert len(operations["round_1_selector.run"].inputs) == 3
    assert set(operations["round_1_proposer.run"].inputs) == {"round_1_proposer.state"}
    assert all("request" not in port for port in operations["round_1_proposer.run"].inputs)
    assert all(op.budget_binding is not None for op in operations.values())
    assert all(not op.tools and op.request_port is None for op in operations.values())


def test_stop_and_final_selection_are_existing_terminal_outcomes():
    prepared = _prepared()
    module = prepared.module
    terminals = (module.terminal, *module.terminal_alternatives)
    assert {(t.source.component, t.source.port, t.outcome) for t in terminals} == {
        ("round_1_selector", "stop", "stop"), ("round_2_selector", "stop", "stop"),
        ("round_2_selector", "next", "select"), ("round_2_selector", "next", "retain"),
    }
    assert not any(link.source.port == "stop" for link in module.links)
    for component in module.components:
        if component.name.endswith("selector"):
            assert {o.name for o in component.operations[0].outcomes} == {"select", "retain", "stop"}
    with pytest.raises(DeclarationError, match="quantity"):
        prepared.compiled.validate_products("round_1_selector.run", "select", {})
    with pytest.raises(DeclarationError, match="type mismatch"):
        prepared.compiled.validate_products("round_1_selector.run", "select", {"round_1_selector.next": ["wrong"]})
    prepared.compiled.validate_products("round_1_selector.run", "retain", {"round_1_selector.next": [{"ref": "caller-domain-state"}]})


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version="rpnh/iteration_profile/v2"),
    lambda d: d.update(template="teacher_student/v1"),
    lambda d: d.update(best_model="winner"),
    lambda d: d.update(retry_policy="repeat"),
    lambda d: d.update(attempt_budget={"unit": "operation_attempt", "maximum": 6}),
    lambda d: d["native_model_call_budget"].update(unit="operation_attempt"),
    lambda d: d.update(datasets={"test": "hidden.json"}),
    lambda d: d.update(training={}),
    lambda d: d["roles"].update(teacher={"executor_key": "x"}),
    lambda d: d["roles"]["proposer"].update(import_path="untrusted.code"),
    lambda d: d["roles"]["evaluator"].update(config={"callable": "untrusted.code"}),
    lambda d: d["roles"]["selector"].update(model_profile_ref={"credential": "secret"}),
    lambda d: d["schemas"].update(candidate="unversioned"),
    lambda d: d["native_model_call_budget"].update(unit="usd"),
    lambda d: d["native_model_call_budget"].update(unit="tokens"),
    lambda d: d["native_model_call_budget"].update(maximum=0),
    lambda d: d["native_model_call_budget"].update(maximum=True),
    lambda d: d["native_model_call_budget"].update(maximum=1.0),
    lambda d: d.update(rounds=0), lambda d: d.update(rounds=33),
    lambda d: d.update(rounds=True), lambda d: d.update(rounds=2.0),
    lambda d: d.update(rounds=float("nan")), lambda d: d.update(rounds=float("inf")),
    lambda d: d.update(terminal_key=None),
    lambda d: d.pop("terminal_outcomes"),
    lambda d: d["terminal_outcomes"].pop("stop"),
    lambda d: d["terminal_outcomes"].update(final_select="select"),
    lambda d: d["terminal_outcomes"].update(final_retain="unknown"),
    lambda d: d["terminal_outcomes"].update(stop="stopped_by_owner"),
    lambda d: d["terminal_outcomes"].update(exhausted="complete"),
    lambda d: d["terminal_outcomes"].update(stop=True),
])
def test_closed_profile_rejects_unsupported_or_ambiguous_inputs(change):
    value = _document()
    change(value)
    with pytest.raises((DeclarationError, SchemaGovernanceError)):
        IterationProfile.from_dict(value)


def test_profile_rejects_duplicate_nonjson_oversized_and_invalid_text(tmp_path):
    with pytest.raises(DeclarationError, match="Duplicate"):
        IterationProfile.from_json('{"rounds": 1, "rounds": 2}')
    with pytest.raises(DeclarationError):
        IterationProfile.from_dict({1: "coercion"})
    value = _document()
    value["rounds"] = object()
    with pytest.raises(DeclarationError):
        IterationProfile.from_dict(value)
    path = tmp_path / "profile.json"
    for content in (b"{" + b" " * 65536, b"\xff"):
        path.write_bytes(content)
        with pytest.raises(DeclarationError):
            load_iteration_profile(path)
    with pytest.raises(DeclarationError):
        IterationProfile.from_json("{} " + " " * 65536)


@pytest.mark.parametrize("missing", ["schema", "executor", "terminal"])
def test_registration_and_original_compiler_remain_contract_authority(missing):
    value = _document()
    key = {"schema": value["schemas"]["evaluation"], "executor": value["roles"]["selector"]["executor_key"],
           "terminal": value["terminal_key"]}[missing]
    with pytest.raises(RegistrationError, match="unregistered"):
        compile_iteration_profile(IterationProfile.from_dict(value), registration=_registration(value, missing=key))


def test_original_compiler_rejects_incompatible_host_config():
    value = _document()
    registration = _registration(value, config={"type": "object", "required": ["not_supplied"]})
    with pytest.raises(DeclarationError, match="Invalid registered"):
        compile_iteration_profile(IterationProfile.from_dict(value), registration=registration)


def test_compilation_revalidates_direct_constructor():
    value = _document()
    value["template"] = "untrusted/template"
    with pytest.raises(SchemaGovernanceError):
        compile_iteration_profile(IterationProfile(json.dumps(value)), registration=_registration())


def test_standard_lowerer_is_required_before_any_custom_lowering():
    value = _document()
    registration = Registration()
    registration.register_component("operation", _must_not_execute,
                                    identity={"implementation_id": "untrusted"}, contracts={})
    with pytest.raises(DeclarationError, match="standard basic"):
        compile_iteration_profile(IterationProfile.from_dict(value), registration=registration)


def test_external_schema_references_fail_closed_without_retrieval(monkeypatch):
    value = _document()
    registration = _registration(value, config={"$ref": "https://invalid.example/schema.json"})
    monkeypatch.setattr(socket, "socket", _must_not_execute)
    with pytest.raises(DeclarationError, match="local-fragment"):
        compile_iteration_profile(IterationProfile.from_dict(value), registration=registration)


def test_local_schema_references_are_supported():
    value = _document()
    registration = _registration(value, config={"definitions": {"empty": {"type": "object", "additionalProperties": False}}, "$ref": "#/definitions/empty"})
    compile_iteration_profile(IterationProfile.from_dict(value), registration=registration)


def test_compilation_has_no_runtime_writes_or_reference_reads(tmp_path, monkeypatch):
    value = _document()
    never_read = tmp_path / "missing-existing-model-profile.json"
    value["roles"]["proposer"]["model_profile_ref"] = str(never_read)
    profile = IterationProfile.from_dict(value)
    registration = _registration(value)
    before = deepcopy(registration.declarations())
    writes = []
    registration.bind_gateway(lambda *args: writes.append(args))
    writes.clear()
    original_open = io.open

    class UnavailableEnvironment:
        get = keys = items = values = copy = _must_not_execute

        def __getitem__(self, key):
            raise AssertionError("Compilation cannot read environment secrets")

        def __iter__(self):
            raise AssertionError("Compilation cannot enumerate environment secrets")

    def checked_open(path, *args, **kwargs):
        if path == never_read or str(path) == str(never_read):
            raise AssertionError("Model profile references must remain unresolved")
        mode = args[0] if args else kwargs.get("mode", "r")
        assert not any(flag in mode for flag in ("w", "a", "x", "+"))
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(io, "open", checked_open)
        patch.setattr(socket, "socket", _must_not_execute)
        patch.setattr(sqlite3, "connect", _must_not_execute)
        patch.setattr(subprocess, "Popen", _must_not_execute)
        patch.setattr(os, "getenv", _must_not_execute)
        patch.setattr(os, "environ", UnavailableEnvironment())
        for method in ("register_schema", "register_component", "register_executor", "register_tool", "bind_gateway", "bind_schema_catalog"):
            patch.setattr(registration, method, _must_not_execute)
        prepared = compile_iteration_profile(profile, registration=registration)
    assert not writes and registration.declarations() == before
    assert list(tmp_path.iterdir()) == []
    assert [(b.operation, b.profile_ref) for b in prepared.required_model_bindings] == [
        ("round_1_proposer.run", str(never_read)), ("round_2_proposer.run", str(never_read)),
    ]
    assert all(op.declaration.request_port is None for op in prepared.compiled.operations)
    assert "model_profile_ref" not in prepared.module.to_json()


def test_different_role_model_refs_remain_distinct_unresolved_obligations():
    value = _document(1)
    value["roles"]["proposer"]["model_profile_ref"] = "existing-proposer-profile"
    value["roles"]["evaluator"]["model_profile_ref"] = "existing-evaluator-profile"
    prepared = compile_iteration_profile(IterationProfile.from_dict(value), registration=_registration(value))
    assert [(b.operation, b.profile_ref) for b in prepared.required_model_bindings] == [
        ("round_1_proposer.run", "existing-proposer-profile"),
        ("round_1_evaluator.run", "existing-evaluator-profile"),
    ]
    assert prepared.profile.to_dict() == value


def test_native_model_call_budget_is_independent_of_operation_count():
    value = _document(2)
    value["native_model_call_budget"]["maximum"] = 17
    prepared = compile_iteration_profile(IterationProfile.from_dict(value), registration=_registration(value))
    assert len(prepared.compiled.operations) == 6
    assert prepared.budgets.ordinary_global_cap == prepared.budgets.task_total_hard_cap == 17
    assert all(bucket.max_attempts is None for bucket in prepared.module.budget_buckets)


@pytest.mark.parametrize("field", ["stop", "final_select", "final_retain"])
@pytest.mark.parametrize("run_outcome", ["complete", "failed"])
def test_explicit_native_terminal_mapping_is_compiled_without_inference(field, run_outcome):
    value = _document(2)
    value["terminal_outcomes"][field] = run_outcome
    prepared = compile_iteration_profile(IterationProfile.from_dict(value), registration=_registration(value))
    for terminal in (prepared.module.terminal, *prepared.module.terminal_alternatives):
        key = "stop" if terminal.outcome == "stop" else "final_" + terminal.outcome
        assert terminal.config == {"run_outcome": value["terminal_outcomes"][key]}
    assert prepared.profile.to_dict() == value


def test_native_terminal_host_rejecting_run_outcome_fails_at_compile():
    value = _document()
    registration = _registration(value, terminal_config={"type": "object", "additionalProperties": False})
    with pytest.raises(DeclarationError, match="Invalid registered tool config"):
        compile_iteration_profile(IterationProfile.from_dict(value), registration=registration)


# Actual Registry integration probes below deliberately use the existing owner
# step APIs. No scheduler, business executor, provider or socket is constructed.
def _registry_owner(tmp_path, value, *, historical_empty_terminal=False):
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from cpn.rpnh.run import OwnerInput, start_run

    prepared = compile_iteration_profile(IterationProfile.from_dict(value), registration=_registration(value))
    module = prepared.module
    registration = _registration(value)
    if historical_empty_terminal:
        # Exact pre-fix template material differs only in these empty configs.
        # The old profile itself is rejected by the revised profile schema.
        document = module.to_dict()
        for terminal in (document["terminal"], *document["terminal_alternatives"]):
            terminal["config"] = {}
        # Independently emitted by the untouched frozen R1 API with this HOST
        # registration/profile; its provenance accompanies delivery revision 2.
        import hashlib
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical).hexdigest() == "f5c3bac53c4b18ee7c42b7d8fdd946275775f89d5a31c51d9b58718cd9a2e66b"
        module = ModuleDeclaration.from_dict(document)
        registration = _registration(value, terminal_config={"type": "object", "additionalProperties": False})
    inputs = {item.name: OwnerInput(item.schema, canonical_json({"ref": item.name}),
                                   "Deterministic native terminal fixture")
              for item in prepared.required_entry_bindings}
    owner = start_run(module, registration, run_dir=tmp_path / "run",
        task_input=inputs["initial_state"], entry_inputs=inputs, budgets=prepared.budgets,
        model_condition="inert-profile-terminal-contract-test-no-model",
        owner_statement="Deterministic Registry terminal contract validation only",
        command_id="terminal-test:start")
    return owner, prepared


def _registry_probe(owner, transition, outcome, ports, *, settle=True):
    from cpn.rpnh.registry.schema_catalog import canonical_json

    admitted = owner.admit(transition, logical_tau=0, command_id="admit:" + transition)
    assert admitted is not None
    execution = owner.start(admitted, command_id="start:" + transition)
    products = {port: (canonical_json({"ref": port}),) for port in ports}
    outputs = owner.products(execution, outcome_id=outcome, products=products,
                             command_id="products:" + transition)
    if settle:
        owner.succeed(outputs, command_id="success:" + transition)
    return execution, outputs


def _registry_prepare_first_selection(owner):
    _registry_probe(owner, "round_1_proposer.run", "proposed",
                    ("round_1_proposer.incumbent", "round_1_proposer.candidate"))
    _registry_probe(owner, "round_1_evaluator.run", "evaluated", ("round_1_evaluator.evaluation",))


@pytest.fixture
def no_terminal_test_transport(monkeypatch):
    monkeypatch.setattr(socket, "socket", _must_not_execute)
    monkeypatch.setattr(socket, "socketpair", _must_not_execute)
    monkeypatch.setattr(subprocess, "Popen", _must_not_execute)


@pytest.mark.parametrize("field,outcome,rounds", [
    ("stop", "stop", 2), ("final_select", "select", 1), ("final_retain", "retain", 1),
])
@pytest.mark.parametrize("run_outcome", ["complete", "failed"])
def test_real_registry_publishes_each_explicit_terminal_mapping(
        tmp_path, no_terminal_test_transport, field, outcome, rounds, run_outcome):
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes

    value = _document(rounds)
    value["terminal_outcomes"][field] = run_outcome
    owner, prepared = _registry_owner(tmp_path, value)
    assert owner.terminal() is None
    _registry_prepare_first_selection(owner)
    assert owner.terminal() is None
    port = "round_1_selector." + ("stop" if outcome == "stop" else "next")
    _registry_probe(owner, "round_1_selector.run", outcome, (port,))
    terminal_ref = owner.terminal()
    assert terminal_ref is not None
    before = owner._core.event_store.max_ordinal()
    assert owner.terminal() == terminal_ref  # Existing native idempotent closure.
    assert owner._core.event_store.max_ordinal() == before
    observer = _RegistryCore(owner._core.run_dir, create=False, read_only=True)
    read = read_run_execution(observer, _ResourceServiceKernel(observer),
        expected_run_ref=owner.identity.run_ref, expected_net_ref=owner.publication.net_ref,
        expected_terminal_evidence_ref=terminal_ref)
    assert read.status == "terminal" and read.terminal.run_outcome == run_outcome
    assert json.loads(read_run_terminal_bytes(observer, read)) == {"ref": port}
    read.cut.assert_unchanged(observer)
    assert owner._core.event_store.max_ordinal() == before
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    assert prepared.budgets.ordinary_global_cap == 1
    assert len(owner._core.event_store.object_rows_by_type("run_terminal_evidence/v1")) == 1


def test_historical_empty_terminal_material_stays_running_after_settlement(
        tmp_path, no_terminal_test_transport):
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.run_authority import read_run_execution

    value = _document(1)
    old_profile = deepcopy(value)
    old_profile.pop("terminal_outcomes")
    with pytest.raises(SchemaGovernanceError):
        IterationProfile.from_dict(old_profile)
    owner, _ = _registry_owner(tmp_path, value, historical_empty_terminal=True)
    _registry_prepare_first_selection(owner)
    _registry_probe(owner, "round_1_selector.run", "select", ("round_1_selector.next",))
    assert owner.terminal() is None
    read = read_run_execution(owner._core, _ResourceServiceKernel(owner._core))
    assert read.status == "running" and read.terminal is None
    assert not owner._core.event_store.object_rows_by_type("run_terminal_evidence/v1")


def test_provisional_outputs_cannot_be_washed_into_terminal_by_complete_mapping(
        tmp_path, no_terminal_test_transport):
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.run_authority import read_run_execution

    owner, _ = _registry_owner(tmp_path, _document(1))
    _registry_prepare_first_selection(owner)
    admitted = owner.admit("round_1_selector.run", logical_tau=0, command_id="unsettled:admit")
    assert admitted is not None and owner.terminal() is None
    execution = owner.start(admitted, command_id="unsettled:start")
    assert owner.terminal() is None
    from cpn.rpnh.registry.schema_catalog import canonical_json
    owner.products(execution, outcome_id="stop", products={"round_1_selector.stop":
        (canonical_json({"ref": "only-provisional-output"}),)}, command_id="unsettled:products")
    before = owner._core.event_store.max_ordinal()
    assert owner.terminal() is None
    read = read_run_execution(owner._core, _ResourceServiceKernel(owner._core))
    assert read.status == "running" and read.terminal is None
    assert owner._core.event_store.max_ordinal() == before
    assert not owner._core.event_store.object_rows_by_type("run_terminal_evidence/v1")


def test_owner_interruption_is_not_the_domain_stop_outcome(
        tmp_path, no_terminal_test_transport):
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    from cpn.rpnh.registry.run_authority import read_run_execution

    owner, _ = _registry_owner(tmp_path, _document(1))
    owner.record_owner_stop(idempotency_key="authorized-test-owner-stop")
    assert owner.terminal() is None
    read = read_run_execution(owner._core, _ResourceServiceKernel(owner._core))
    assert read.status == "stopped_by_owner" and read.terminal is None
    assert not owner._core.event_store.object_rows_by_type("run_terminal_evidence/v1")
