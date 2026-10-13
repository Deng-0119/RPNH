"""Real native human-input validation, without agent/model authorship claims."""
from __future__ import annotations
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from cpn.components.agent_loop.optional_execution import optional_agent_loop_schema_data
from cpn.components.basic import CONFIG_SCHEMA_ID
from cpn.components.execution_services import ExecutionServices
from cpn.orchestrator.runner import Orchestrator
from ..agent_tasks import agent_task_registration, TEXT_SCHEMA, TERMINAL_KEY
from ..module import ModuleDeclaration
from ..compiler import compile_module
from ..control_server import OwnerEventLoop
from ..run import start_run, OwnerInput
from ..registry.operations import OperationExecutionResult
from ..registry.resources import PetriOutputOrigin, PublishResource
from ..registry.module_budgets import ModuleBudgetDeclaration
from ..registry.schema_catalog import SchemaCatalog, canonical_json
from ..registry.user_collaboration_source import strict_proposal_json
from .schema_catalog import user_collaboration_schema_data, plain_merge_result_schema_data

VALIDATE = "rpnh/project-human-input-validation/v1"
MODEL_CONDITION = "project-controlled-local-no-model"


def catalog():
    documents, types = optional_agent_loop_schema_data()
    paths = {}
    known = {t.name: t for t in types}
    for load in (user_collaboration_schema_data, plain_merge_result_schema_data):
        more, definitions, locations = load()
        documents.update(more); paths.update(locations)
        known.update({t.name: t for t in definitions})
    return SchemaCatalog(schemas=documents, types=tuple(known.values()), schema_paths=paths)


def validate_human_input(*, execution, gateway, resources, host_context):
    """Validate exact human-supplied Module; output is validation, not creation."""
    del resources, host_context
    delivered, = execution.operation.inputs
    envelope = json.loads(json.loads(delivered.artifact.payload))
    if type(envelope) is not dict or set(envelope) != {"input_kind", "module", "restore_from"} or envelope["input_kind"] != "human_submitted_module":
        raise ValueError("explicit human input envelope required")
    document = strict_proposal_json(canonical_json(envelope["module"]))
    module = ModuleDeclaration.from_dict(document)
    compile_module(module, registration())
    port, = execution.operation.spec.output_ports
    binding, = execution.operation.operation_binding.output_port_bindings
    context = execution.operation.canonical.context
    reference = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(canonical_json(document).decode()), media_type="application/json",
        content_schema_ref=TEXT_SCHEMA,
        summary="Validated human-submitted Module; no model authorship or business-task completion",
        lifetime_ref=context.invocation_ref, derived_from=(delivered.resource_ref,),
        descriptors={"output_outcome_id": "complete", "output_port_id": port.port_id,
                     "place": binding.place, "input_kind": "human_submitted_module",
                     "execution_kind": "deterministic_validation_only"},
        idempotency_key="human-validation:" + str(execution.operation_execution_lease_ref.version_id)))
    return OperationExecutionResult((gateway.verify_resource(execution.operation.canonical, reference),),
                                    selected_outcome_id="complete")


def registration():
    selected = agent_task_registration()
    selected.register_executor(VALIDATE, validate_human_input,
        identity={"implementation_id": "rpnh.project_human_input_validation", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None})
    return selected


def validation_module():
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1",
        "name": "HumanModuleValidation",
        "components": [{"name": "validator", "key": "operation", "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": TEXT_SCHEMA},
                      {"name": "result", "direction": "output", "schema": TEXT_SCHEMA}],
            "operations": [{"name": "validate", "executor": VALIDATE, "inputs": ["request"], "outputs": ["result"],
                "tools": [], "config": {}, "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": "validation", "budget_scope": "module", "finalization_scope": None}}]}],
        "links": [], "entry": {"request": {"component": "validator", "port": "request"}},
        "exit": {"result": {"component": "validator", "port": "result"}},
        "terminal": {"key": TERMINAL_KEY, "source": {"component": "validator", "port": "result"},
                     "operation": "validate", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT_SCHEMA], "budgets": {},
        "budget_buckets": [{"bucket_id": "validation", "budget_scope": "module", "finalization_scope": None, "max_attempts": 1}]})


def start(module, inputs, path, name, *, selection=None):
    buckets = tuple(module.to_dict()["budget_buckets"])
    cap = sum(b["max_attempts"] or 1 for b in buckets) or 1
    bindings = None
    if selection is not None:
        from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
        from ..agent_tasks import _execution_route
        bindings = make_optional_agent_host_bindings(selection.input_target,
            workspace_policy=selection.runtime_policy.workspace,
            provider_backend_config=_execution_route(selection),
            transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                                "response_adapter_ref": "llm_response_envelope/v1"})
    return start_run(module, registration(), run_dir=path, task_input=next(iter(inputs.values())), entry_inputs=inputs,
        budgets=ModuleBudgetDeclaration(buckets, ("rpnh/module_declaration/v1",), cap, 0, cap, 0),
        catalog=catalog(), model_condition=MODEL_CONDITION if selection is None else selection.input_target.model_condition,
        host_execution_bindings=bindings, owner_name=name,
        owner_statement="Controlled local project; business execution requires an explicit owner run command",
        command_id="project-entry-start")


def input_value(document, *, restore_from=None):
    envelope = {"input_kind": "human_submitted_module", "module": document, "restore_from": restore_from}
    return OwnerInput(TEXT_SCHEMA, canonical_json(canonical_json(envelope).decode()), "Human Module input and selected restore provenance")


def execute_validation(owner, socket_path):
    loop = OwnerEventLoop(owner, socket_path)
    try:
        services = ExecutionServices(owner=owner, event_loop=loop, llm_input_port=None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = Orchestrator(owner=owner, event_loop=loop, prepare_dispatcher=services.prepare_dispatcher,
                submit_operation=pool.submit, max_in_flight=1).run()
        if result.stop_reason != "terminal" or owner.terminal() is None:
            raise RuntimeError("human validation did not reach successful native terminal: " + result.stop_reason)
        if owner._core.event_store.actual_model_call_counts() != (0, 0):
            raise RuntimeError("human validation unexpectedly used a model")
    finally:
        loop.close()


def execute_task(owner, socket_path, selection, *, event_loop=None):
    """Explicit original task execution using a launcher-selected approved profile.

    Call only on the original owner route. This does not prepare environments,
    install dependencies, change permissions or choose a provider for the user.
    """
    from cpn.llm_adapters import build_llm_input_port
    if selection is None:
        raise ValueError("business execution requires an existing approved execution profile at HOST startup")
    own_loop = event_loop is None
    loop = OwnerEventLoop(owner, socket_path) if own_loop else event_loop
    port = None
    try:
        port = build_llm_input_port(selection, destination_run_root=owner._core.run_dir)
        services = ExecutionServices(owner=owner, event_loop=loop, llm_input_port=port)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = Orchestrator(owner=owner, event_loop=loop, prepare_dispatcher=services.prepare_dispatcher,
                submit_operation=pool.submit, max_in_flight=1).run()
        terminal = owner.terminal()
        return {"stop_reason": result.stop_reason,
            "terminal": None if terminal is None else {"entity_type": terminal.entity_type,
                "logical_id": str(terminal.entity_id), "version_id": str(terminal.version_id)},
            "actual_model_call_counts": list(owner._core.event_store.actual_model_call_counts())}
    finally:
        if port is not None:
            port.close()
        if own_loop:
            loop.close()
