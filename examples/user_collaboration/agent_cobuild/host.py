"""Example HOST assembly using stock AgentLoop and collaboration authorities.

The source authors are explicitly deterministic programs, not author agents.
Only the adopted target's curator and auditor execute real stock AgentLoop roles.
"""
from __future__ import annotations

import json

from cpn.components.agent_loop.optional_execution import optional_agent_loop_schema_data
from cpn.components.basic import CONFIG_SCHEMA_ID
from cpn.rpnh.agent_tasks import agent_task_registration, TEXT_SCHEMA, TERMINAL_KEY
from cpn.rpnh.collaboration import user_collaboration_schema_data
from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.operations import OperationExecutionResult
from cpn.rpnh.registry.resources import PetriOutputOrigin, PublishResource
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from .contracts import contribution_document

PROPOSE = "example/agent_cobuild/propose/v1"


def catalog():
    schemas, types = optional_agent_loop_schema_data()
    extra, extra_types, paths = user_collaboration_schema_data()
    return SchemaCatalog(schemas={**schemas, **extra}, types=(*types, *extra_types), schema_paths=paths)


def propose(*, execution, gateway, resources, host_context):
    """Publish a deterministic edit of the exact delivered accepted Module."""
    del resources
    executable = host_context._executable_net
    if executable.net_ref != execution.operation.firing.net_ref:
        raise ValueError("source configuration belongs to another net")
    compiled = load_compiled_net(json.loads(executable.declaration.payload))
    transition = next(t for t in compiled.symbolic.transitions
                      if t.name == execution.operation.firing.transition_id)
    operation = next(o for o in compiled.operations if o.declaration.name == transition.operation)
    if operation.operation_id != execution.operation.spec.operation_id or operation.executor_key != PROPOSE:
        raise ValueError("source configuration differs from its admitted operation")
    delivered, = execution.operation.inputs
    base = json.loads(json.loads(delivered.artifact.payload))
    document = contribution_document(base, operation.declaration.config["kind"])
    port, = execution.operation.spec.output_ports
    binding, = execution.operation.operation_binding.output_port_bindings
    context = execution.operation.canonical.context
    ref = gateway.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
        payload=canonical_json(canonical_json(document).decode()), media_type="application/json",
        content_schema_ref=TEXT_SCHEMA, summary="Deterministic agent contract proposal",
        lifetime_ref=context.invocation_ref,
        derived_from=(delivered.resource_ref,),
        descriptors={"output_outcome_id": "complete", "output_port_id": port.port_id,
                     "place": binding.place, "example": "agent_cobuild"},
        idempotency_key="proposal:" + str(execution.operation_execution_lease_ref.version_id)))
    return OperationExecutionResult((gateway.verify_resource(execution.operation.canonical, ref),),
                                    selected_outcome_id="complete")


def registration():
    selected = agent_task_registration()
    selected.register_executor(PROPOSE, propose,
        identity={"implementation_id": "agent_cobuild.deterministic_contract_author", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None})
    return selected


def proposal_module(kind):
    if kind not in {"curator", "auditor"}:
        raise ValueError("unknown contract contributor")
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1",
        "name": "DeterministicContractProposal", "components": [{"name": "proposal", "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "base", "direction": "input", "schema": TEXT_SCHEMA},
                      {"name": "result", "direction": "output", "schema": TEXT_SCHEMA}],
            "operations": [{"name": "derive", "executor": PROPOSE, "inputs": ["base"],
                "outputs": ["result"], "tools": [], "config": {"kind": kind},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": "proposal", "budget_scope": "module", "finalization_scope": None}}]}],
        "links": [], "entry": {"base": {"component": "proposal", "port": "base"}},
        "exit": {"result": {"component": "proposal", "port": "result"}},
        "terminal": {"key": TERMINAL_KEY, "source": {"component": "proposal", "port": "result"},
                     "operation": "derive", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT_SCHEMA], "budgets": {},
        "budget_buckets": [{"bucket_id": "proposal", "budget_scope": "module", "finalization_scope": None, "max_attempts": 1}]})
