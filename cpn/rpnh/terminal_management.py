"""Explicitly registered, bounded terminal material management.

The original adopted executor/configuration records this capability. Updating
the process cannot confer it on an older location operation. Execution still
uses the original material command, Owner, PN slot and normal Success path.
"""
from __future__ import annotations

from types import MappingProxyType

from .registry.event_store import RegistryConflict
from .registry.identities import new_id
from .registry.models import VersionRef
from .registry.module_host_bindings import HostExecutionBinding, validate_host_execution_bindings
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload


TERMINAL_PROTOCOL = "rpnh/terminal_location_management/v1"
TERMINAL_EXECUTOR = "rpnh/material-terminal-location/v1"
TERMINAL_CONFIG_SCHEMA = "application/rpnh_terminal_location_config/v1"
TERMINAL_IDENTITY = {"implementation_id": "rpnh.material_terminal_location", "revision": "v1"}
TERMINAL_CONTRACTS = {
    "transport": "deterministic", "input_ports": [], "output_ports": [],
    "config_schema": TERMINAL_CONFIG_SCHEMA,
    "owner_material_commands": "rpnh/material_command/v1",
    "owner_location_actions": "rpnh/material_location/v1",
    "owner_terminal_management": TERMINAL_PROTOCOL,
}
TERMINAL_CONFIG = {
    "$id": TERMINAL_CONFIG_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["outcome_id", "idle_place"],
    "properties": {"outcome_id": {"type": "string", "minLength": 1},
                   "idle_place": {"type": "string", "minLength": 1}},
}

# The literal variant retains the original FILE/workspace mechanism and
# authored configuration. Its one idle place is proved from the adopted PN;
# no new lifecycle table, configuration schema or injected capacity is needed.
from .material_execution import (
    EXECUTOR_CONTRACTS as LITERAL_CONTRACTS, MATERIAL_EXECUTOR, MATERIAL_TOOL,
    EXECUTOR_IDENTITY as LITERAL_IDENTITY,
)

TERMINAL_LITERAL_PROTOCOL = "rpnh/terminal_literal_management/v1"
TERMINAL_LITERAL_EXECUTOR = "rpnh/material-terminal-literal/v1"
TERMINAL_LITERAL_IDENTITY = {"implementation_id": "rpnh.material_terminal_literal", "revision": "v1"}
TERMINAL_LITERAL_CONTRACTS = {**LITERAL_CONTRACTS,
    "owner_terminal_management": TERMINAL_LITERAL_PROTOCOL}


def register_terminal_literal_execution(registration):
    """Opt in after registering the original literal tool/schema/executor."""
    from .material_execution import material_operation_executor
    original = registration.declaration("executor", MATERIAL_EXECUTOR)
    if original["identity"] != LITERAL_IDENTITY or original["contracts"] != LITERAL_CONTRACTS:
        raise RegistryConflict("terminal literal requires the original literal executor registration")
    registration.register_executor(TERMINAL_LITERAL_EXECUTOR, material_operation_executor,
        identity=TERMINAL_LITERAL_IDENTITY, contracts=TERMINAL_LITERAL_CONTRACTS)


def validate_terminal_literal_spec(spec, binding):
    """Require one declared text output and the original workspace/tool ABI."""
    if (spec.get("executor_key") != TERMINAL_LITERAL_EXECUTOR
            or spec.get("implementation_identity") != TERMINAL_LITERAL_IDENTITY
            or spec.get("implementation_contracts") != TERMINAL_LITERAL_CONTRACTS
            or spec.get("transport") != "deterministic" or spec.get("input_ports")
            or len(spec.get("output_ports", ())) != 1
            or spec.get("allowed_tool_ids") != [MATERIAL_TOOL]
            or binding.get("workspace_binding_ref") is None
            or binding.get("llm_input_target_ref") is not None
            or len(binding.get("output_binding_refs", ())) != 1):
        raise RegistryConflict("TARGET_UNAVAILABLE: original terminal literal contract is not exact")


def register_terminal_location_execution(registration):
    """Author an explicit new capability; do not modify the old contract."""
    from .material_locations import location_operation_executor
    registration.register_schema(TERMINAL_CONFIG_SCHEMA, TERMINAL_CONFIG)
    registration.register_executor(TERMINAL_EXECUTOR, location_operation_executor,
        identity=TERMINAL_IDENTITY, contracts=TERMINAL_CONTRACTS)


def validate_terminal_location_spec(spec, binding):
    """Require the original exact empty-body, workspace-free variant."""
    if (spec.get("executor_key") != TERMINAL_EXECUTOR
            or spec.get("implementation_identity") != TERMINAL_IDENTITY
            or spec.get("implementation_contracts") != TERMINAL_CONTRACTS
            or spec.get("transport") != "deterministic"
            or spec.get("input_ports") or spec.get("output_ports")
            or spec.get("allowed_tool_ids")
            or binding.get("workspace_binding_ref") is not None
            or binding.get("llm_input_target_ref") is not None
            or binding.get("output_binding_refs")):
        raise RegistryConflict("TARGET_UNAVAILABLE: original terminal location contract is not exact")


def terminal_location_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    """Publish each explicitly declared terminal manager's original HOST agent."""
    from .material_execution import material_host_bindings, _material_host_publication
    result = dict(material_host_bindings(core=core, plan=plan, source_ref=source_ref,
        bootstrap_ref=bootstrap_ref, compiled=compiled))
    definitions = {item.declaration.name: item for item in compiled.operations}
    for transition in compiled.symbolic.transitions:
        operation = definitions[transition.operation]
        declaration = operation.executor_declaration
        contracts = declaration.get("contracts", {})
        if "owner_terminal_management" not in contracts:
            continue
        if (declaration.get("identity") == TERMINAL_LITERAL_IDENTITY
                and contracts == TERMINAL_LITERAL_CONTRACTS
                and operation.declaration.executor == TERMINAL_LITERAL_EXECUTOR
                and not operation.declaration.inputs
                and len(operation.declaration.outputs) == 1
                and tuple(operation.declaration.tools) == (MATERIAL_TOOL,)
                and transition.name in result
                and result[transition.name].workspace_binding_ref is not None):
            # material_host_bindings has already supplied its real original
            # agent, lineage template and write intent. Do not replace them.
            continue
        if (declaration.get("identity") != TERMINAL_IDENTITY
                or contracts != TERMINAL_CONTRACTS
                or operation.declaration.executor != TERMINAL_EXECUTOR
                or operation.declaration.inputs or operation.declaration.outputs
                or operation.declaration.tools):
            raise RegistryConflict("terminal location HOST requires the exact adopted variant")
        core.catalog.validate_schema_ref(TERMINAL_CONFIG_SCHEMA, dict(operation.declaration.config))
        key = f"terminal-location-host:{plan.net_ref.version_id}:{transition.name}"
        saved = _material_host_publication(core, key, "agent/v1")
        ref = (VersionRef("agent/v1", saved.logical_id, saved.version_id) if saved is not None
            else VersionRef("agent/v1", new_id("agent"), new_id("agent_version")))
        metadata = {"agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
            "agent_ref": ref_payload(ref), "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name, "shadow_transition_id": transition.name}
        if saved is not None:
            if saved.metadata != metadata or core.object_store.read_registered(saved) != canonical_json(metadata):
                raise RegistryConflict("terminal location HOST replay changed the original agent")
        else:
            core.publish_bytes(object_type="agent/v1", logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                schema_ref="registry_v1/agent/v1", idempotency_key=key)
        result[transition.name] = HostExecutionBinding(agent_ref=ref)
    validate_host_execution_bindings(core, plan, result)
    return MappingProxyType(result)


__all__ = ("register_terminal_location_execution", "terminal_location_host_bindings",
    "validate_terminal_location_spec", "TERMINAL_PROTOCOL", "TERMINAL_EXECUTOR",
    "TERMINAL_CONFIG_SCHEMA", "TERMINAL_IDENTITY", "TERMINAL_CONTRACTS",
    "register_terminal_literal_execution", "validate_terminal_literal_spec",
    "TERMINAL_LITERAL_PROTOCOL", "TERMINAL_LITERAL_EXECUTOR",
    "TERMINAL_LITERAL_IDENTITY", "TERMINAL_LITERAL_CONTRACTS")
