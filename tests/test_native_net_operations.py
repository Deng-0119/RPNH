"""Deterministic evidence for opt-in native Petri-net operations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
import tarfile

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.components.execution_services import ExecutionServices
from cpn.components.agent_loop.optional_host_bindings import (
    make_optional_agent_host_bindings,
)
from cpn.components.net_operations import (
    COMPONENT_KEY, CONFIG_SCHEMA_ID as NET_CONFIG_SCHEMA_ID, EXECUTOR_KEY,
    register_net_components,
)
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import (
    ComposeConnection, ComposePlan, ExtractPlan, WorkspaceBindingPlan,
    apply_replacement, branch_module, compose_modules, extract_module,
    instantiate_module, prepare_replacement,
)
from cpn.rpnh.registration import Registration
from cpn.rpnh.agent_tasks import (
    AgentStage, agent_task_catalog, agent_task_registration,
    build_agent_task_module,
)
from cpn.rpnh.agent_workflows import TEXT_SCHEMA as AGENT_TEXT_SCHEMA
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run


TEXT = "application/net_operation_test_text/v1"
ALT_TEXT = "application/net_operation_test_alt_text/v1"
EXECUTOR = "test/net-operation-business/v1"
TERMINAL = "test/net-operation-terminal/v1"


def _schema(schema_id):
    return {"$id": schema_id, "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "string"}


def _registration(*, native=False):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, _schema(TEXT))
    registration.register_schema(ALT_TEXT, _schema(ALT_TEXT))
    registration.register_executor(EXECUTOR, lambda **_kwargs: None,
        identity={"implementation_id": "test.net_operation_business", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.net_operation_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    if native:
        register_net_components(registration)
    return registration


def _simple_module(name="Simple", *, input_schema=TEXT, output_schema=TEXT):
    binding = {"bucket_id": "work", "budget_scope": "module",
               "finalization_scope": None}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": name,
        "components": [{
            "name": "step", "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": input_schema},
                {"name": "result", "direction": "output", "schema": output_schema},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"],
                "request_port": None, "tools": [], "config": {},
                "budget_binding": binding,
                "outcomes": [{"name": "complete",
                              "products": [{"port": "result"}]}],
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": TERMINAL,
                     "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete",
                     "config": {"run_outcome": "complete"}},
        "required_schemas": sorted({CONFIG_SCHEMA_ID, input_schema, output_schema}),
        "budgets": {},
        "budget_buckets": [{**binding, "max_attempts": 3}],
    })


def test_extract_whole_and_component_boundary_round_trip():
    registration = _registration()
    first, second = _simple_module("First"), _simple_module("Second")
    serial = compose_modules({"first": first, "second": second}, ComposePlan(
        "Serial", "second", mode="serial"))
    whole = extract_module(serial, ExtractPlan())
    assert whole.module.to_dict() == serial.to_dict()
    selected = extract_module(serial, ExtractPlan(
        kind="components", components=("second_step",),
        output_name="SelectedSecond"))
    assert [item.name for item in selected.module.components] == ["second_step"]
    assert selected.entry_sources == {"second_step_request": "first_step.result"}
    assert list(selected.module.exit) == ["second_result"]
    compile_module(selected.module, registration)


def test_serial_parallel_and_instance_are_explicit_and_lowerable():
    registration = _registration()
    definition = _simple_module()
    serial = compose_modules({"left": definition, "right": definition}, ComposePlan(
        "Serial", "right", mode="serial"))
    assert len(serial.links) == 1
    assert list(serial.entry) == ["left_request"]
    assert list(serial.exit) == ["right_result"]
    assert [item.bucket_id for item in serial.budget_buckets] == ["work"]
    lowered = compile_module(serial, registration)
    assert {item.name for item in lowered.symbolic.transitions} == {
        "left_step.run", "right_step.run"}

    parallel = compose_modules({"left": definition, "right": definition}, ComposePlan(
        "Parallel", "right", mode="parallel"))
    assert parallel.links == ()
    assert set(parallel.entry) == {"left_request", "right_request"}
    assert set(parallel.exit) == {"left_result", "right_result"}
    compile_module(parallel, registration)

    instance = instantiate_module(definition, instance="copy")
    assert [item.name for item in instance.components] == ["copy_step"]
    compile_module(instance, registration)


def test_compose_rejects_implicit_broadcast_and_incompatible_link():
    definition = _simple_module()
    with pytest.raises(ValueError, match="multiple producers"):
        compose_modules({"a": definition, "b": definition, "c": definition}, ComposePlan(
            "Bad", "c", connections=(
                ComposeConnection("a", "result", "c", "request"),
                ComposeConnection("b", "result", "c", "request"),
            )))
    incompatible = _simple_module("Alt", input_schema=ALT_TEXT)
    composed = compose_modules({"a": definition, "b": incompatible}, ComposePlan(
        "BadSchema", "b", mode="serial"))
    with pytest.raises(Exception, match="Fusion token/schema"):
        compile_module(composed, _registration())


def test_compose_rejects_ambiguous_qualified_public_names():
    left_document = _simple_module("Left").to_dict()
    left_document["exit"] = {"x": left_document["exit"]["result"]}
    left = ModuleDeclaration.from_dict(left_document)
    right_document = _simple_module("Right").to_dict()
    right_document["exit"] = {"b_x": right_document["exit"]["result"]}
    right = ModuleDeclaration.from_dict(right_document)
    with pytest.raises(ValueError, match="public exit names collide"):
        compose_modules(
            {"a_b": left, "a": right},
            ComposePlan("Ambiguous", "a", mode="parallel"),
        )


def test_branch_definition_and_instance_keep_source_inert():
    source = _simple_module()
    definition = branch_module(source, ExtractPlan(), output_mode="definition_only")
    assert definition.instance is None
    assert source.to_dict() == _simple_module().to_dict()
    instance = branch_module(source, ExtractPlan(), output_mode="new_instance",
                             instance="branch")
    assert instance.instance is not None
    assert instance.instance.components[0].name == "branch_step"


def _native_operation_module(config):
    binding = {"bucket_id": "definition", "budget_scope": "module",
               "finalization_scope": None}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": "NativeDefinitionOperation",
        "components": [{
            "name": "netop", "key": COMPONENT_KEY,
            "config_schema": NET_CONFIG_SCHEMA_ID, "config": config,
            "ports": [
                {"name": "source", "direction": "input",
                 "schema": "rpnh/module_declaration/v1"},
                {"name": "result", "direction": "output",
                 "schema": "rpnh/module_declaration/v1"},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR_KEY,
                "inputs": ["source", "net_operation_config"],
                "outputs": ["result"], "request_port": None, "tools": [],
                "config": config, "budget_binding": binding,
                "outcomes": [{"name": "complete",
                              "products": [{"port": "result"}]}],
            }],
        }],
        "links": [],
        "entry": {"source": {"component": "netop", "port": "source"}},
        "exit": {"result": {"component": "netop", "port": "result"}},
        "terminal": {"key": TERMINAL,
                     "source": {"component": "netop", "port": "result"},
                     "operation": "run", "outcome": "complete",
                     "config": {"run_outcome": "complete"}},
        "required_schemas": [NET_CONFIG_SCHEMA_ID, "rpnh/module_declaration/v1"],
        "budgets": {},
        "budget_buckets": [{**binding, "max_attempts": 1}],
    })


def _native_owner(tmp_path):
    registration = _registration(native=True)
    config = {
        "kind": "extract", "source_order": ["source"],
        "selection": {
            "kind": "whole_module", "components": [],
            "boundary_policy": "preserve_all_dependencies",
            "output_name": "Extracted",
        },
    }
    module = _native_operation_module(config)
    source = _simple_module()
    owner = start_run(
        module, registration, run_dir=tmp_path / "native-run",
        task_input=OwnerInput("rpnh/module_declaration/v1",
                              canonical_json(source.to_dict()), "Source Module"),
        entry_inputs={"source": OwnerInput(
            "rpnh/module_declaration/v1", canonical_json(source.to_dict()),
            "Source Module")},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="offline-net-operation",
        owner_statement="Deterministic native net operation test",
        command_id="test:native-net-operation:fresh",
    )
    return owner, source


def test_registered_extract_uses_real_success_without_adoption_or_model(tmp_path):
    owner, source = _native_owner(tmp_path)
    before = owner.snapshot()
    adoptions_before = owner._core.event_store.list_events_by_type(("net_adopted/v1",))
    loop = OwnerEventLoop(owner, tmp_path / "native-run" / "owner.sock")
    try:
        services = ExecutionServices(owner=owner, event_loop=loop)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = Orchestrator(
                owner=owner, event_loop=loop,
                prepare_dispatcher=services.prepare_dispatcher,
                submit_operation=pool.submit, max_in_flight=1,
            ).run()
    finally:
        loop.close()
    after = owner.snapshot()
    assert result.stop_reason == "terminal"
    assert before["net_ref"] == after["net_ref"]
    assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    assert owner._core.event_store.list_events_by_type(
        ("net_adopted/v1",)) == adoptions_before
    assert source.name == "Simple"


def test_registered_config_is_exact_per_operation_kind():
    registration = _registration(native=True)
    selection = {
        "kind": "whole_module", "components": [],
        "boundary_policy": "preserve_all_dependencies",
        "output_name": "Branched",
    }
    branch = _native_operation_module({
        "kind": "branch", "source_order": ["source"],
        "selection": selection, "output_mode": "definition_only",
        "instance": None,
    })
    compile_module(branch, registration)

    invalid = _native_operation_module({
        "kind": "extract", "source_order": ["source"],
        "selection": selection, "output_name": "irrelevant",
    })
    with pytest.raises(Exception, match="extract config requires exact fields"):
        compile_module(invalid, registration)

    invalid_selection = _native_operation_module({
        "kind": "extract", "source_order": ["source"],
        "selection": {**selection, "components": ["step"]},
    })
    with pytest.raises(Exception):
        compile_module(invalid_selection, registration)

    invalid_cardinality_document = branch.to_dict()
    invalid_cardinality_document["components"][0]["ports"][0][
        "cardinality"] = 2
    with pytest.raises(Exception, match="exact cardinality 1"):
        compile_module(
            ModuleDeclaration.from_dict(invalid_cardinality_document),
            registration,
        )


def test_replacement_plan_uses_existing_owner_adoption_path(tmp_path):
    registration = _registration()
    module = _simple_module()
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(
        module, registration, run_dir=tmp_path / "replace-run",
        task_input=task, entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            (TEXT,), 3, 0, 3, 0),
        model_condition="offline-replace", owner_statement="Owner replacement test",
        command_id="test:replace:fresh",
    )
    old_net = owner.snapshot()["net_ref"]
    plan = prepare_replacement(owner, _simple_module("Replacement"))
    result = apply_replacement(owner, plan, command_id="test:replace:apply")
    assert result["status"] == "ADOPTED"
    assert owner.snapshot()["net_ref"] != old_net


def test_pending_replacement_blocks_owner_stop_and_budget_expansion(tmp_path):
    registration = _registration()
    module = _simple_module()
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(
        module, registration, run_dir=tmp_path / "pending-replace-run",
        task_input=task, entry_inputs={"request": task},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            (TEXT,), 3, 0, 3, 0),
        model_condition="offline-replace", owner_statement="Pending replacement test",
        command_id="test:pending-replace:fresh",
    )
    admitted = owner.admit("step.run", logical_tau=0,
                           command_id="test:pending-replace:admit")
    assert admitted is not None
    owner.start(admitted, command_id="test:pending-replace:start")
    plan = prepare_replacement(owner, _simple_module("Replacement"))
    result = apply_replacement(owner, plan, command_id="test:pending-replace:apply")
    assert result["status"] == "DRAINING"
    with pytest.raises(RuntimeError, match="replacement is pending"):
        owner.record_owner_stop(idempotency_key="test:pending-replace:stop")

    expanded = instantiate_module(module, instance="next")
    document = expanded.to_dict()
    document["budget_buckets"][0]["max_attempts"] += 1
    expanded = ModuleDeclaration.from_dict(document)
    with pytest.raises(ValueError, match="existing exact budget"):
        prepare_replacement(owner, expanded)


def test_workspace_binding_modes_are_strict_plan_data():
    assert WorkspaceBindingPlan("create_empty").revision_ref is None
    with pytest.raises(TypeError, match="exact workspace revision"):
        WorkspaceBindingPlan("fork_from_revision")


class _OfflineWorkspacePort:
    def request_once(self, _attempt):
        return LLMInputResponseBytes(canonical_json({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": "write-result", "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": "Offline workspace replacement evidence.",
                    "content": json.dumps("preserved workspace content"),
                    "output_port_id": "first_worker.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "complete", "name": "complete_interaction",
                "arguments": "{}",
            }],
            "finish_reason": "tool_calls",
        }), status_code=200, external_request_id="offline-workspace")

    def close(self):
        pass


def test_agent_replacement_preserves_current_workspace_lineage(tmp_path):
    stage = build_agent_task_module(
        (AgentStage("worker", "Return one result."),),
        max_attempts_per_stage=3)
    module = compose_modules(
        {"first": stage, "second": stage},
        ComposePlan("TwoStageAgentTask", "second", mode="serial"))
    registration = agent_task_registration()
    target = LLMInputTarget("offline-workspace-preserve", 64, 65536)
    host_bindings = make_optional_agent_host_bindings(
        target,
        provider_backend_config={
            "schema_version": "optional_agent_execution_provenance/v1",
            "model": target.model_condition,
            "backend": "offline-test",
            "timeout_seconds": 30,
            "selection": {},
            "transport_kind": "offline-test",
            "response_protocol": "llm_response_envelope/v1",
        },
        transport_contract={
            "interaction_protocol_ref": "llm_request_envelope/v1",
            "response_adapter_ref": "llm_response_envelope/v1",
        },
    )
    task = OwnerInput(
        AGENT_TEXT_SCHEMA, canonical_json("task"), "Task")
    owner = start_run(
        module, registration, run_dir=tmp_path / "agent-replace-run",
        task_input=task, entry_inputs={"first_request": task},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 3, 0, 3, 0),
        model_condition=target.model_condition,
        owner_statement="Offline agent workspace replacement test",
        command_id="test:agent-replace:fresh",
        catalog=agent_task_catalog(),
        host_execution_bindings=host_bindings,
    )
    first_loop = OwnerEventLoop(
        owner, tmp_path / "agent-replace-run" / "owner-first.sock")
    try:
        first_services = ExecutionServices(
            owner=owner, event_loop=first_loop,
            llm_input_port=_OfflineWorkspacePort())
        with ThreadPoolExecutor(max_workers=1) as pool:
            first_result = Orchestrator(
                owner=owner, event_loop=first_loop,
                prepare_dispatcher=first_services.prepare_dispatcher,
                submit_operation=pool.submit,
                select_ready=lambda **values: tuple(
                    item for item in values["enabled"]
                    if item == "first_worker.run"),
                max_in_flight=1,
            ).run()
    finally:
        first_loop.close()
    assert first_result.stop_reason == "quiescent_marking"
    old_checkpoint = owner._core.get_version(
        _version_from_payload(owner.snapshot()["checkpoint_ref"]).version_id).metadata
    old_workspace_ref = _version_from_payload(
        old_checkpoint["workspace_revision_refs"][0])
    old_workspace = owner._core.get_version(old_workspace_ref.version_id).metadata
    assert old_workspace["disposition"] == "committed"
    assert old_workspace["inventory_paths"] == ["outputs/result.txt"]
    workspace_payload = owner._core.object_store.read_registered(
        owner._core.get_version(old_workspace_ref.version_id))
    with tarfile.open(fileobj=io.BytesIO(workspace_payload), mode="r:") as archive:
        output = archive.extractfile("outputs/result.txt")
        assert output is not None
        assert b"preserved workspace content" in output.read()
    terminal_token = next(
        item for item in owner.snapshot()["marking"]
        if item["place"] == "first_worker.result")
    candidate_document = module.to_dict()
    candidate_document["name"] = "ReplacementAgentTask"
    plan = prepare_replacement(
        owner, ModuleDeclaration.from_dict(candidate_document),
        marking_mapping={
            terminal_token["token_ref"]["version_id"]:
                "first_worker.request"})
    result = apply_replacement(
        owner, plan, command_id="test:agent-replace:apply")
    assert result["status"] == "ADOPTED"
    new_checkpoint = owner._core.get_version(
        _version_from_payload(owner.snapshot()["checkpoint_ref"]).version_id).metadata
    assert new_checkpoint["workspace_revision_refs"] == old_checkpoint[
        "workspace_revision_refs"]
    admitted = owner.admit(
        "first_worker.run", logical_tau=1,
        command_id="test:agent-replace:second-admit")
    assert admitted is not None
    execution = owner.start(
        admitted, command_id="test:agent-replace:second-start")
    second_loop = OwnerEventLoop(
        owner, tmp_path / "agent-replace-run" / "owner-second.sock")
    try:
        second_services = ExecutionServices(
            owner=owner, event_loop=second_loop, llm_input_port=object())
        service = second_services._optional_agent_service
        assert service is not None
        catalog = service.current_agent_tool_catalog_v1(execution)
        command = service.prepare_agent_loop_start_v1(
            execution, catalog,
            idempotency_key="test:agent-replace:second-loop-prepare")
        replacement_loop = service.start_agent_loop_v1(command)
    finally:
        second_loop.close()
    assert replacement_loop.workspace_base_revision_ref == old_workspace_ref
    assert owner._core.event_store.actual_model_call_counts() == (1, 0)
