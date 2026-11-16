"""Registered deterministic bind/unbind operations on the original Owner.

The worker prepares one original address fact. Only the Owner's normal Success
can publish it; no body is delivered, copied, rewritten, disabled or purged.
"""
from __future__ import annotations

from types import MappingProxyType

from .registry._event_store.material_locations import (
    LOCATION_CONFIG_SCHEMA, LOCATION_EXECUTOR, LOCATION_IDENTITY, LOCATION_PROTOCOL,
    is_location, location_definition, validate_location_baseline,
)
from .registry.event_store import RegistryConflict
from .registry.identities import new_id
from .registry.models import VersionRef
from .registry.module_host_bindings import HostExecutionBinding, validate_host_execution_bindings
from .registry.operation_execution import verify_operation_execution
from .registry.operations import OperationExecutionResult, register_operation_outputs
from .registry.publication import _version_from_payload
from .registry.resources import (
    BindResourceAddress, UnbindResourceAddress, ResourceAddress, ResourceAddressBindingRef,
    ResourceVersionRef,
)
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload


LOCATION_CONTRACTS = {
    "transport": "deterministic", "input_ports": None, "output_ports": None,
    "config_schema": LOCATION_CONFIG_SCHEMA,
    "owner_material_commands": "rpnh/material_command/v1",
    "owner_location_actions": LOCATION_PROTOCOL,
}
LOCATION_CONFIG = {
    "$id": LOCATION_CONFIG_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["outcome_id"], "properties": {"outcome_id": {"type": "string", "minLength": 1}},
}


def location_operation_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    return gateway.prepare_material_location_operation(execution)


def register_location_execution(registration):
    """Explicit registration; it does not enable a Viewer/source capability."""
    registration.register_schema(LOCATION_CONFIG_SCHEMA, LOCATION_CONFIG)
    registration.register_executor(LOCATION_EXECUTOR, location_operation_executor,
        identity=LOCATION_IDENTITY, contracts=LOCATION_CONTRACTS)


def location_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    """Original static agents, with no location workspace or model target."""
    from .material_execution import material_host_bindings, _material_host_publication
    # The existing HOST entry verifies this exact original plan, source, run,
    # bootstrap and adopted graph before it returns even an empty mapping.
    result = dict(material_host_bindings(core=core, plan=plan, source_ref=source_ref,
        bootstrap_ref=bootstrap_ref, compiled=compiled))
    operations = {item.declaration.name: item for item in compiled.operations}
    for transition in compiled.symbolic.transitions:
        operation = operations[transition.operation]
        registered = operation.executor_declaration
        if registered.get("contracts", {}).get("owner_location_actions") != LOCATION_PROTOCOL:
            continue
        if (registered.get("identity") != LOCATION_IDENTITY
                or registered.get("contracts") != LOCATION_CONTRACTS
                or operation.declaration.executor != LOCATION_EXECUTOR
                or operation.declaration.outputs or operation.declaration.tools):
            raise RegistryConflict("location HOST requires its exact registered empty operation")
        key = f"material-location-host:{plan.net_ref.version_id}:{transition.name}"
        saved = _material_host_publication(core, key, "agent/v1")
        ref = (VersionRef("agent/v1", saved.logical_id, saved.version_id) if saved is not None
               else VersionRef("agent/v1", new_id("agent"), new_id("agent_version")))
        metadata = {"agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
            "agent_ref": ref_payload(ref), "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name, "shadow_transition_id": transition.name}
        if saved is not None:
            if saved.metadata != metadata or core.object_store.read_registered(saved) != canonical_json(metadata):
                raise RegistryConflict("location HOST replay changed its exact static agent")
        else:
            core.publish_bytes(object_type="agent/v1", logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                schema_ref="registry_v1/agent/v1", idempotency_key=key)
        result[transition.name] = HostExecutionBinding(agent_ref=ref)
    validate_host_execution_bindings(core, plan, result)
    return MappingProxyType(result)


def prepare_location_operation(owner, execution):
    from .run import RunOwner
    from .registry._event_store.material_settlement import material_admission, completion_evidence
    if not isinstance(owner, RunOwner):
        raise TypeError("location operation requires the existing RunOwner")
    core = owner._core
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    context = execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        found = material_admission(core.event_store, db, core.task_id,
            ref_payload(context.own_transition_firing_ref), current=True)
        if found is None or not is_location(found[1]["data"]):
            raise RegistryConflict("location operation requires its exact accepted location command")
        admission, command = found
        _firing, _binding, _spec, defined, config = location_definition(
            core.event_store, db, core.task_id, admission, command)
        registered = owner.registration.declaration("executor", defined.declaration.executor)
        if registered != defined.executor_declaration:
            raise RegistryConflict("location executor differs from its frozen original registration")
        validate_location_baseline(core.event_store, db, core.task_id, command["data"])
        completed = db.execute("SELECT 1 FROM events e JOIN firing_temporary_members m "
            "ON m.member_kind='event' AND m.member_identity=e.event_id "
            "WHERE m.firing_version_id=? AND e.event_type='registered_operation_completion_recorded/v1'",
            (str(context.own_transition_firing_ref.version_id),)).fetchone()
        if completed is not None:
            completion_evidence(core.event_store, db, core.task_id, admission)
            return OperationExecutionResult((), selected_outcome_id=config["outcome_id"])
        data = command["data"]
        expected = data["expected_binding_ref"]
        if expected is not None:
            from .registry._event_store.collaboration_descriptors import exact_prepared
            prior = exact_prepared(db, core.object_store, core.task_id, expected)
            reference = _version_from_payload(expected)
            expected = ResourceAddressBindingRef(reference.entity_id, reference.version_id,
                prior.metadata["lifecycle_state"] == "tombstoned")
    key = "material-location:" + str(context.invocation_ref.version_id)
    address = ResourceAddress(_version_from_payload(data["address"]["scope_ref"]), data["address"]["opaque_name"])
    if data["operation"] == "bind":
        target = _version_from_payload(data["target_resource_ref"])
        kernel.bind_address(context, BindResourceAddress(address,
            ResourceVersionRef(target.entity_id, target.version_id), context.operation_binding_ref,
            expected, key + ":binding"), reference_only=True)
    else:
        kernel.unbind_address(context, UnbindResourceAddress(address,
            context.operation_binding_ref, expected, key + ":binding"))
    return OperationExecutionResult((), selected_outcome_id=config["outcome_id"])


def run_location_operation(owner, execution):
    """Native synchronous port through original output/completion registrars."""
    from .registry.firing_recovery import record_registered_operation_completion
    result = prepare_location_operation(owner, execution)
    kernel, repository = owner.operation_repository()
    key = "material-location:" + str(execution.operation.canonical.context.invocation_ref.version_id)
    outputs = register_operation_outputs(repository, execution, result.outputs,
        selected_outcome_id=result.selected_outcome_id, idempotency_key=key + ":outputs")
    record_registered_operation_completion(owner._core, kernel, repository, outputs,
        idempotency_key=key + ":executor-completed")
    return outputs


__all__ = ("LOCATION_CONFIG_SCHEMA", "LOCATION_EXECUTOR", "LOCATION_CONTRACTS",
    "register_location_execution", "location_host_bindings", "location_operation_executor",
    "prepare_location_operation", "run_location_operation")
