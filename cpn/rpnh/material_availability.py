"""Original Owner disable-plan and deterministic availability operation ports."""
from __future__ import annotations

import json
from types import MappingProxyType

from .registry._event_store.material_availability import (
    PROTOCOL, EFFECT_SCHEMA, PLAN_PROTOCOL, PLAN_SCHEMA, PLAN_ACTION, PLAN_ROLE,
    EXECUTOR, IDENTITY, CONFIG_SCHEMA, CONTRACTS, exact_ref,
    is_availability, validate_plan_params, build_plan, read_plan,
    availability_definition, validate_availability_baseline,
    resolve_availability, source_governs_materials_at,
)
from .registry.event_store import RegistryConflict
from .registry.identities import new_id
from .registry.models import VersionRef
from .registry.module_host_bindings import HostExecutionBinding, validate_host_execution_bindings
from .registry.operation_execution import verify_operation_execution
from .registry.operations import OperationExecutionResult, register_operation_outputs
from .registry.publication import _version_from_payload
from .registry.resource_verification import verify_resource
from .registry.resources import ResourceVersionRef, PetriOutputOrigin, PublishResource
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload


_REF = {"type": "object", "additionalProperties": False,
    "required": ["entity_type", "logical_id", "version_id"], "properties": {
        "entity_type": {"type": "string", "minLength": 1},
        "logical_id": {"type": "string", "minLength": 1}, "version_id": {"type": "string", "minLength": 1}}}
_RESOURCE_REF = {**_REF, "properties": {**_REF["properties"], "entity_type": {"const": "resource_version/v1"}}}
_TARGETS = {"type": "array", "minItems": 1, "maxItems": 64, "uniqueItems": True, "items": _RESOURCE_REF}
EFFECT_DOCUMENT = {"$id": EFFECT_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["protocol", "action", "target_resource_refs", "confirmed_plan_ref"], "properties": {
        "protocol": {"const": PROTOCOL}, "action": {"const": "disable"},
        "target_resource_refs": _TARGETS, "confirmed_plan_ref": _RESOURCE_REF}}
PLAN_DOCUMENT = {"$id": PLAN_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "required": ["protocol", "source_task_ref", "source_cut", "target_resource_refs", "address_bindings",
        "observed_active_firing_refs", "latest_checkpoint_ref", "carrier_refs", "carrier_inventory_complete", "purge_requested"],
    "properties": {"protocol": {"const": PLAN_PROTOCOL}, "source_task_ref": _REF,
        "source_cut": {"type": "integer", "minimum": 0}, "target_resource_refs": _TARGETS,
        "address_bindings": {"type": "array", "uniqueItems": True, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["address", "expected_binding_ref", "target_resource_ref"], "properties": {
                "address": {"type": "object", "additionalProperties": False,
                    "required": ["scope_ref", "opaque_name"], "properties": {"scope_ref": _REF,
                        "opaque_name": {"type": "string", "minLength": 1}}},
                "expected_binding_ref": _REF, "target_resource_ref": _RESOURCE_REF}}},
        "observed_active_firing_refs": {"type": "array", "uniqueItems": True, "items": _REF},
        "latest_checkpoint_ref": _REF, "carrier_refs": _TARGETS,
        "carrier_inventory_complete": {"const": False}, "purge_requested": {"const": False}}}
CONFIG_DOCUMENT = {"$id": CONFIG_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False, "required": ["outcome_id", "output_port_id"],
    "properties": {"outcome_id": {"type": "string", "minLength": 1},
                   "output_port_id": {"type": "string", "minLength": 1}}}


def resolve_material_availability(core, resource_ref, *, source_cut=None):
    """Current effect policy; callers retain their original permissions/cut."""
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        value = resolve_availability(core.event_store, db, core.task_id, exact_ref(resource_ref))
    return {**value, "source_cut": source_cut}


def require_material_available(core, resource_ref, *, purpose):
    value = resolve_material_availability(core, resource_ref)
    if value["availability"] != "available":
        raise RegistryConflict("MATERIAL_UNAVAILABLE: " + value["availability"] + " for " + purpose)
    return value


def source_governs_materials(core):
    with core.event_store.connection_scope() as db:
        db.execute("BEGIN")
        return source_governs_materials_at(core.event_store, db, core.task_id)


def prepare_material_disable_plan(owner, params, command_id, *, collaboration_action):
    from .registry.user_collaboration import check_handle
    from .registry._event_store.material_control import require_session, row_ref
    validate_plan_params(params)
    core = owner._core
    action = check_handle(core.event_store, collaboration_action)
    if (action.kind != PLAN_ACTION or action.creation != canonical_json({"owner_command": PLAN_ACTION,
            "command_id": command_id, "params": params})):
        raise RegistryConflict("INVALID_PLAN: original plan HOST request differs")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        require_session(core.event_store, db, core.task_id, action.session)
        original = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE t.task_id=? AND t.idempotency_key=?", (str(core.task_id), command_id)).fetchall()
        if original:
            if len(original) != 1:
                raise RegistryConflict("INVALID_PLAN: ambiguous original preparation")
            ref = row_ref(original[0])
            plan = read_plan(core.event_store, db, core.task_id, ref, action=action.material())
            from .registry._event_store.material_availability import _targets
            if plan["target_resource_refs"] != _targets(params["target_resource_refs"]):
                raise RegistryConflict("COMMAND_CONTENT_MISMATCH: original plan targets changed")
            return {"status": "PLAN_PREPARED", "plan_ref": ref, "plan": plan}
        plan = build_plan(core.event_store, db, core.task_id, params["target_resource_refs"])
    tx = core.begin(idempotency_key=command_id)
    tx.expect_registry_ordinal(plan["source_cut"])
    tx.bind_collaboration_action(action)
    ref = owner.control.publish_input({"schema_id": PLAN_SCHEMA, "value": plan,
        "summary": "Finite material disable confirmation plan"}, command_id=command_id, transaction=tx,
        derived_from=tuple(ResourceVersionRef(_version_from_payload(r).entity_id,
            _version_from_payload(r).version_id) for r in plan["target_resource_refs"]),
        descriptors={"owner_input_role": PLAN_ROLE, "owner_input_actor": action.session.actor,
            "owner_input_session_version_id": str(action.session.ref.version_id)})
    tx.commit()
    return {"status": "PLAN_PREPARED", "plan_ref": ref_payload(ref.as_version_ref()), "plan": plan}


def availability_operation_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    return gateway.prepare_material_availability_operation(execution)


def register_material_availability(registration):
    """Explicit registered source contract; does not enable a Viewer menu."""
    for name, document in ((CONFIG_SCHEMA, CONFIG_DOCUMENT), (PLAN_SCHEMA, PLAN_DOCUMENT), (EFFECT_SCHEMA, EFFECT_DOCUMENT)):
        registration.register_schema(name, document)
    registration.register_executor(EXECUTOR, availability_operation_executor, identity=IDENTITY, contracts=CONTRACTS)


def availability_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    from .material_locations import location_host_bindings
    from .material_execution import _material_host_publication
    result = dict(location_host_bindings(core=core, plan=plan, source_ref=source_ref,
        bootstrap_ref=bootstrap_ref, compiled=compiled))
    definitions = {item.declaration.name: item for item in compiled.operations}
    for transition in compiled.symbolic.transitions:
        operation = definitions[transition.operation]
        declaration = operation.executor_declaration
        if declaration.get("contracts", {}).get("owner_material_availability") != PROTOCOL:
            continue
        if (declaration.get("identity") != IDENTITY or declaration.get("contracts") != CONTRACTS
                or operation.declaration.executor != EXECUTOR or operation.declaration.tools
                or len(operation.declaration.outputs) != 1):
            raise RegistryConflict("availability HOST requires its exact original registered operation")
        key = f"material-availability-host:{plan.net_ref.version_id}:{transition.name}"
        saved = _material_host_publication(core, key, "agent/v1")
        ref = (VersionRef("agent/v1", saved.logical_id, saved.version_id) if saved is not None
               else VersionRef("agent/v1", new_id("agent"), new_id("agent_version")))
        metadata = {"agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
            "agent_ref": ref_payload(ref), "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name, "shadow_transition_id": transition.name}
        if saved is not None:
            if saved.metadata != metadata or core.object_store.read_registered(saved) != canonical_json(metadata):
                raise RegistryConflict("availability HOST replay changed its original agent")
        else:
            core.publish_bytes(object_type="agent/v1", logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                schema_ref="registry_v1/agent/v1", idempotency_key=key)
        result[transition.name] = HostExecutionBinding(agent_ref=ref)
    validate_host_execution_bindings(core, plan, result)
    return MappingProxyType(result)


def prepare_availability_operation(owner, execution):
    from .run import RunOwner
    from .registry._event_store.material_settlement import material_admission, completion_evidence
    if not isinstance(owner, RunOwner):
        raise TypeError("availability operation requires the original RunOwner")
    core = owner._core
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    context = execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        found = material_admission(core.event_store, db, core.task_id, ref_payload(context.own_transition_firing_ref), current=True)
        if found is None or not is_availability(found[1]["data"]):
            raise RegistryConflict("availability operation requires its original accepted disable command")
        admission, command = found
        _firing, _binding, _spec, defined, config = availability_definition(core.event_store, db, core.task_id, admission, command)
        if owner.registration.declaration("executor", EXECUTOR) != defined.executor_declaration:
            raise RegistryConflict("availability executor differs from its frozen original registration")
        plan = validate_availability_baseline(core.event_store, db, core.task_id, command["data"], current=True)
        completed = db.execute("SELECT 1 FROM events e JOIN firing_temporary_members m "
            "ON m.member_kind='event' AND m.member_identity=e.event_id WHERE m.firing_version_id=? "
            "AND e.event_type='registered_operation_completion_recorded/v1'", (str(context.own_transition_firing_ref.version_id),)).fetchone()
        if completed is not None:
            evidence = completion_evidence(core.event_store, db, core.task_id, admission)
            refs = [ResourceVersionRef(_version_from_payload(exact_ref(item["resource_ref"])).entity_id,
                _version_from_payload(exact_ref(item["resource_ref"])).version_id) for item in evidence.payload["ordered_outputs"]]
            return OperationExecutionResult(tuple(verify_resource(core, kernel, execution.operation.canonical, ref) for ref in refs),
                                            selected_outcome_id=config["outcome_id"])
    bindings = [item for item in execution.operation.operation_binding.output_port_bindings
                if item.port_id == config["output_port_id"]]
    if len(bindings) != 1:
        raise RegistryConflict("availability operation lacks its actual output binding")
    binding, = bindings
    body = {"protocol": PROTOCOL, "action": "disable", "target_resource_refs": plan["target_resource_refs"],
            "confirmed_plan_ref": command["data"]["confirmed_plan_ref"]}
    ref = kernel.publish_bytes(context, PublishResource(
        origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref), payload=canonical_json(body),
        media_type="application/json", content_schema_ref=EFFECT_SCHEMA, summary="Exact material availability effect",
        lifetime_ref=context.invocation_ref, derived_from=tuple(ResourceVersionRef(_version_from_payload(r).entity_id,
            _version_from_payload(r).version_id) for r in plan["target_resource_refs"]),
        descriptors={"output_outcome_id": config["outcome_id"], "output_port_id": config["output_port_id"], "place": binding.place},
        idempotency_key="material-availability:" + str(context.invocation_ref.version_id) + ":effect"))
    return OperationExecutionResult((verify_resource(core, kernel, execution.operation.canonical, ref),),
                                     selected_outcome_id=config["outcome_id"])


def run_availability_operation(owner, execution):
    from .registry.firing_recovery import record_registered_operation_completion
    result = prepare_availability_operation(owner, execution)
    kernel, repository = owner.operation_repository()
    key = "material-availability:" + str(execution.operation.canonical.context.invocation_ref.version_id)
    outputs = register_operation_outputs(repository, execution, result.outputs,
        selected_outcome_id=result.selected_outcome_id, idempotency_key=key + ":outputs")
    record_registered_operation_completion(owner._core, kernel, repository, outputs, idempotency_key=key + ":completed")
    return outputs
