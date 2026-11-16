"""Registered single-carrier cleanup with published authorization and real PN.

Only an exact immutable semantic/file blob can be removed.  Workspace paths,
archives, original inputs and every control/settlement object are retained.
This explicit HOST opt-in does not enable a Viewer cleanup button.
"""
from __future__ import annotations

import json
import os
import stat
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType

from .registry._event_store.material_purge import (
    PROTOCOL, AUTH_SCHEMA, RESULT_SCHEMA, CONFIG_SCHEMA, AUTH_EXECUTOR, PURGE_EXECUTOR,
    PURGE_TOOL, AUTH_IDENTITY, PURGE_IDENTITY, AUTH_CONTRACTS, PURGE_CONTRACTS,
    TOOL_IDENTITY, TOOL_CONTRACTS, is_purge, purge_definition, assert_purge_ready,
    read_purge_authorization,
)
from .registry.event_store import RegistryConflict
from .registry.execution_net import ExecutionNetDefinition, ExecutionTransition, ExecutionInputArc, ExecutionOutputArc
from .registry.identities import new_id
from .registry.models import VersionRef
from .registry.module_host_bindings import HostExecutionBinding, validate_host_execution_bindings
from .registry.operation_execution import verify_operation_execution
from .registry.operations import OperationExecutionResult, register_operation_outputs
from .registry.publication import _version_from_payload
from .registry.resource_verification import verify_resource
from .registry.resources import ResourceVersionRef, PublishResource, PetriOutputOrigin
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload
from .file_execution_net import execute_idempotent_materialization, execution_parent, execution_states_for_parent


PURGE_NET = ExecutionNetDefinition(
    definition_key="material-single-blob-purge-v1", places=("map_ready", "pending"),
    transitions=(ExecutionTransition("purge_exact_blob", "idempotent_materialization"),),
    input_arcs=(ExecutionInputArc("pending", "purge_exact_blob", 1),),
    output_arcs=(ExecutionOutputArc("purge_exact_blob", "map_ready", 1),),
    initial_place="pending", initial_tokens=1, terminal_places=("map_ready",))

_OBSERVATION_SEAL = object()
_tool_observation = ContextVar("rpnh_material_purge_tool_observation", default=None)


@dataclass(frozen=True)
class _ToolObservation:
    seal: object
    store: object
    invocation_ref: VersionRef
    firing_ref: VersionRef
    body: bytes


def validate_purge_tool_preparation(store, db, *, task_id, objects):
    """Original commit gate: arbitrary products cannot assert physical cleanup.

The transient observation is an exact HOST dispatch capability, not durable
state.  Once this original transaction commits, normal publication and child
execution facts remain the sole history and recovery evidence.
"""
    candidates = [item for item in objects if item.object_type == "resource_version/v1"
                  and item.metadata.get("content_schema_ref") == RESULT_SCHEMA]
    if not candidates:
        return
    from .registry._event_store.material_settlement import material_admission
    from .registry._event_store.collaboration_descriptors import descriptor_store
    observation = _tool_observation.get()
    if (len(candidates) != 1 or type(observation) is not _ToolObservation
            or observation.seal is not _OBSERVATION_SEAL or observation.store is not store):
        raise RegistryConflict("PURGE_TOOL_REQUIRED: physical result requires its actual registered HOST tool")
    item, = candidates
    admission, command = material_admission(store, db, task_id, ref_payload(observation.firing_ref), current=True)
    if (command["data"].get("operation") != "purge_carrier"
            or admission["invocation_ref"] != ref_payload(observation.invocation_ref)
            or item.producer_invocation_id != observation.invocation_ref.entity_id
            or item.metadata.get("producer_ref") != admission["invocation_ref"]
            or descriptor_store(store).read_registered(item) != observation.body):
        raise RegistryConflict("PURGE_TOOL_REQUIRED: tool result changed its exact observed identity or bytes")
    purge_definition(store, db, task_id, admission, command)
    assert_purge_ready(store, db, task_id, admission, command)
    # One observation can prepare one original result transaction only.
    # A later failed gate leaves the physical effect under its existing
    # execution responsibility; a retry must make a fresh tool observation.
    _tool_observation.set(None)

_REF = {"type": "object", "additionalProperties": False,
    "required": ["entity_type", "logical_id", "version_id"], "properties": {
        "entity_type": {"type": "string", "minLength": 1}, "logical_id": {"type": "string", "minLength": 1},
        "version_id": {"type": "string", "minLength": 1}}}
_RESOURCE = {**_REF, "properties": {**_REF["properties"], "entity_type": {"const": "resource_version/v1"}}}
_BASE = {"protocol": {"const": PROTOCOL}, "source_task_ref": _REF, "carrier_ref": _RESOURCE,
         "scope": {"const": "one_exact_immutable_blob"}, "all_carriers_cleared": {"const": False}}
_AUTH = {**_BASE, "authorization_command_ref": _RESOURCE, "material_command_ref": _RESOURCE,
         "target_resource_ref": _RESOURCE, "carrier_role": {"enum": ["semantic_output", "workspace_file"]},
         "disable_effect_refs": {"type": "array", "minItems": 1, "uniqueItems": True, "items": _RESOURCE}}
_RESULT = {**_BASE, "authorization_ref": _RESOURCE, "state": {"enum": ["removed", "already_absent"]}}
AUTH_DOCUMENT = {"$id": AUTH_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                 "additionalProperties": False, "required": list(_AUTH), "properties": _AUTH}
RESULT_DOCUMENT = {"$id": RESULT_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                   "additionalProperties": False, "required": list(_RESULT), "properties": _RESULT}
CONFIG_DOCUMENT = {"$id": CONFIG_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
    "additionalProperties": False, "required": ["outcome_id", "output_port_id"], "properties": {
        "outcome_id": {"type": "string", "minLength": 1}, "output_port_id": {"type": "string", "minLength": 1}}}


def purge_operation_executor(*, execution, gateway, resources, host_context):
    del resources, host_context
    return gateway.prepare_material_purge_operation(execution)


def register_material_purge(registration):
    for name, document in ((AUTH_SCHEMA, AUTH_DOCUMENT), (RESULT_SCHEMA, RESULT_DOCUMENT), (CONFIG_SCHEMA, CONFIG_DOCUMENT)):
        registration.register_schema(name, document)
    registration.register_tool(PURGE_TOOL, purge_exact_blob, identity=TOOL_IDENTITY, contracts=TOOL_CONTRACTS)
    registration.register_executor(AUTH_EXECUTOR, purge_operation_executor, identity=AUTH_IDENTITY, contracts=AUTH_CONTRACTS)
    registration.register_executor(PURGE_EXECUTOR, purge_operation_executor, identity=PURGE_IDENTITY, contracts=PURGE_CONTRACTS)


def purge_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    from .material_availability import availability_host_bindings
    from .material_execution import _material_host_publication
    result = dict(availability_host_bindings(core=core, plan=plan, source_ref=source_ref,
        bootstrap_ref=bootstrap_ref, compiled=compiled))
    operations = {item.declaration.name: item for item in compiled.operations}
    for transition in compiled.symbolic.transitions:
        operation = operations[transition.operation]
        declaration = operation.executor_declaration
        phase = declaration.get("contracts", {}).get("owner_material_purge")
        if phase is None:
            continue
        expected = (PURGE_EXECUTOR, PURGE_IDENTITY, PURGE_CONTRACTS, [PURGE_TOOL]) if phase == "purge_carrier" else (
            AUTH_EXECUTOR, AUTH_IDENTITY, AUTH_CONTRACTS, [])
        if (phase not in {"authorize_purge", "purge_carrier"} or declaration.get("identity") != expected[1]
                or declaration.get("contracts") != expected[2] or operation.declaration.executor != expected[0]
                or list(operation.declaration.tools) != expected[3] or len(operation.declaration.outputs) != 1):
            raise RegistryConflict("purge HOST requires its exact original registered operation")
        key = f"material-purge-host:{plan.net_ref.version_id}:{transition.name}"
        saved = _material_host_publication(core, key, "agent/v1")
        ref = VersionRef("agent/v1", saved.logical_id, saved.version_id) if saved is not None else VersionRef(
            "agent/v1", new_id("agent"), new_id("agent_version"))
        metadata = {"agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id), "agent_ref": ref_payload(ref),
            "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name, "shadow_transition_id": transition.name}
        if saved is not None:
            if saved.metadata != metadata or core.object_store.read_registered(saved) != canonical_json(metadata):
                raise RegistryConflict("purge HOST changed its original static identity")
        else:
            core.publish_bytes(object_type="agent/v1", logical_id=ref.entity_id, version_id=ref.version_id,
                payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                schema_ref="registry_v1/agent/v1", idempotency_key=key)
        result[transition.name] = HostExecutionBinding(agent_ref=ref)
    validate_host_execution_bindings(core, plan, result)
    return MappingProxyType(result)


def _current(owner, execution, db):
    from .registry._event_store.material_settlement import material_admission
    from .registry._event_store.material_control import validate_active_target
    core = owner._core
    epoch = db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()
    if epoch is None or int(epoch[0]) != core.writer_epoch:
        raise RegistryConflict("PURGE_STALE_WRITER: original writer authority changed")
    context = execution.operation.canonical.context
    found = material_admission(core.event_store, db, core.task_id, ref_payload(context.own_transition_firing_ref), current=True)
    if found is None or not is_purge(found[1]["data"]):
        raise RegistryConflict("PURGE_UNAUTHORIZED: exact accepted purge command is required")
    admission, command = found
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
        (str(context.own_transition_firing_ref.version_id),)).fetchone()
    if root is None or root["state"] != "PROVISIONAL" or root["invocation_version_id"] != str(context.invocation_ref.version_id):
        raise RegistryConflict("PURGE_CLOSED: old invocation no longer owns an open firing")
    params = {key: value for key, value in command["data"].items() if key != "user_action"}
    params["target"] = command["target"]
    validate_active_target(core.event_store, db, core.task_id, params, command["source"])
    if command["data"]["operation"] == "purge_carrier":
        inputs = [ref_payload(item.resource_ref.as_version_ref()) for item in execution.operation.inputs]
        if inputs != [command["data"]["authorization_ref"]]:
            raise RegistryConflict("PURGE_UNAUTHORIZED: original PN input is not this exact authorization")
    return admission, command


def purge_exact_blob(*, owner, execution, state, publish_result):
    """Actual registered tool; no caller-provided path or payload is accepted."""
    from .registry.execution_runtime import ExecutionRuntime
    from .registry._event_store.collaboration_descriptors import exact_prepared
    core = owner._core
    if core.read_only or core.object_store.read_only:
        raise RegistryConflict("PURGE_READ_ONLY: cleanup requires the original writable Owner")
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    parent = execution_parent(execution.operation.canonical.context)
    actual = ExecutionRuntime(core).hydrate(state.instance_ref, parent=parent)
    expected_key = "material-purge:" + str(parent.invocation_ref.version_id) + ":blob"
    recoveries = ExecutionRuntime(core).classify_active_firings(actual)
    if (actual.definition != PURGE_NET or actual.checkpoint.checkpoint_ref != state.checkpoint.checkpoint_ref
            or len(recoveries) != 1 or recoveries[0].transition_id != "purge_exact_blob"
            or recoveries[0].action != "retry_same_materialization" or recoveries[0].materialization_key != expected_key):
        raise RegistryConflict("PURGE_UNAUTHORIZED: physical work needs its exact active execution PN")
    with core.event_store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Recheck under the same original writer lock as the physical effect.
        verify_operation_execution(core, kernel, repository, execution)
        admission, command = _current(owner, execution, db)
        if command["data"]["operation"] != "purge_carrier":
            raise RegistryConflict("PURGE_UNAUTHORIZED: authorization phase cannot delete")
        _firing, _binding, _spec, defined, _config = purge_definition(core.event_store, db, core.task_id, admission, command)
        if owner.registration.declaration("executor", PURGE_EXECUTOR) != defined.executor_declaration:
            raise RegistryConflict("PURGE_UNAUTHORIZED: current HOST differs from admitted implementation")
        tool = owner.registration.declaration("tool", PURGE_TOOL)
        if tool["identity"] != TOOL_IDENTITY or tool["contracts"] != TOOL_CONTRACTS:
            raise RegistryConflict("PURGE_UNAUTHORIZED: current tool differs from its frozen physical contract")
        authorization = assert_purge_ready(core.event_store, db, core.task_id, admission, command)
        prepared = exact_prepared(db, core.object_store, core.task_id, authorization["carrier_ref"])
        core.object_store.validate_envelope(prepared)
        if prepared.storage_locator != core.object_store.locator_for_version(prepared.version_id):
            raise RegistryConflict("PURGE_UNSUPPORTED: canonical object locator required")
        if not hasattr(os, "O_NOFOLLOW") or os.unlink not in os.supports_dir_fd:
            raise RegistryConflict("PURGE_UNSUPPORTED: exact descriptor-relative cleanup is unavailable")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(core.object_store.root, flags)
        try:
            directory_fd = os.open(prepared.version_id.kind, flags, dir_fd=root_fd)
            try:
                try:
                    info = os.stat(prepared.version_id.value, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    outcome = "already_absent"
                else:
                    if not stat.S_ISREG(info.st_mode) or info.st_size != prepared.size:
                        raise RegistryConflict("PURGE_UNKNOWN: exact payload is not its original regular-file envelope")
                    os.unlink(prepared.version_id.value, dir_fd=directory_fd)
                    os.fsync(directory_fd)
                    outcome = "removed"
            finally:
                os.close(directory_fd)
        finally:
            os.close(root_fd)
    body = {"protocol": PROTOCOL, "source_task_ref": command["data"]["execution"]["task_ref"],
        "carrier_ref": authorization["carrier_ref"], "scope": "one_exact_immutable_blob", "all_carriers_cleared": False,
        "authorization_ref": command["data"]["authorization_ref"], "state": outcome}
    observation = _ToolObservation(_OBSERVATION_SEAL, core.event_store, parent.invocation_ref,
                                  parent.business_firing_ref, canonical_json(body))
    token = _tool_observation.set(observation)
    try:
        return publish_result(body)
    finally:
        _tool_observation.reset(token)


def _existing_result(owner, execution, key):
    core = owner._core
    context = execution.operation.canonical.context
    with core.event_store.connect() as db:
        rows = db.execute("SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE t.task_id=? AND t.idempotency_key=? AND t.status='committed'", (str(core.task_id), key)).fetchall()
    if not rows:
        return None
    if len(rows) != 1 or rows[0]["producer_invocation_id"] != str(context.invocation_ref.entity_id):
        raise RegistryConflict("PURGE_UNKNOWN: durable original result is ambiguous")
    row = rows[0]
    from .registry.identities import TypedId
    return ResourceVersionRef(TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]))


def prepare_purge_operation(owner, execution):
    from .run import RunOwner
    from .registry._event_store.material_settlement import completion_evidence
    from cpn.components.execution_services import invoke_registered_tool
    if not isinstance(owner, RunOwner):
        raise TypeError("purge requires the existing RunOwner")
    core = owner._core
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    context = execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        admission, command = _current(owner, execution, db)
        _firing, _binding, spec, defined, config = purge_definition(core.event_store, db, core.task_id, admission, command)
        if owner.registration.declaration("executor", spec["executor_key"]) != defined.executor_declaration:
            raise RegistryConflict("purge executor differs from its original frozen registration")
        completed = db.execute("SELECT 1 FROM events e JOIN firing_temporary_members m ON m.member_kind='event' "
            "AND m.member_identity=e.event_id WHERE m.firing_version_id=? AND e.event_type='registered_operation_completion_recorded/v1'",
            (str(context.own_transition_firing_ref.version_id),)).fetchone()
        if completed:
            original = completion_evidence(core.event_store, db, core.task_id, admission)
            refs = [item["resource_ref"] for item in original.payload["ordered_outputs"]]
            from .registry.identities import TypedId
            return OperationExecutionResult(tuple(verify_resource(core, kernel, execution.operation.canonical,
                ResourceVersionRef(TypedId.parse(ref["resource_id"]), TypedId.parse(ref["resource_version_id"]))) for ref in refs),
                selected_outcome_id=config["outcome_id"])
        baseline = assert_purge_ready(core.event_store, db, core.task_id, admission, command)
    key = "material-purge:" + str(context.invocation_ref.version_id)
    execute = command["data"]["operation"] == "purge_carrier"
    output_binding, = [binding for binding in execution.operation.operation_binding.output_port_bindings if binding.port_id == config["output_port_id"]]
    common = {"protocol": PROTOCOL, "source_task_ref": command["data"]["execution"]["task_ref"],
        "carrier_ref": baseline["carrier_ref"], "scope": "one_exact_immutable_blob", "all_carriers_cleared": False}

    def publish(body):
        schema = RESULT_SCHEMA if execute else AUTH_SCHEMA
        return kernel.publish_bytes(context, PublishResource(
            origin=PetriOutputOrigin(output_binding.output_binding_ref, context.activation_ref),
            payload=canonical_json(body), media_type="application/json", content_schema_ref=schema,
            summary="One exact immutable carrier cleanup result" if execute else "One exact immutable carrier cleanup authorization",
            lifetime_ref=context.invocation_ref,
            descriptors={"output_outcome_id": config["outcome_id"], "output_port_id": config["output_port_id"], "place": output_binding.place},
            idempotency_key=key + ":result"))

    if execute:
        # Phase B consumes only its published authorization, never disabled bytes.
        def materialize(state):
            ref = _existing_result(owner, execution, key + ":result")
            if ref is None:
                ref = invoke_registered_tool(owner, kernel, repository, execution, PURGE_TOOL,
                    identity=TOOL_IDENTITY, contracts=TOOL_CONTRACTS,
                    kwargs={"owner": owner, "execution": execution, "state": state, "publish_result": publish})
            return (ref.as_version_ref(),)

        _state, evidence = execute_idempotent_materialization(core, context=context, definition=PURGE_NET,
            transition_id="purge_exact_blob", identity_key=key + ":blob", materialize=materialize)
        if len(evidence) != 1 or evidence[0].entity_type != "resource_version/v1":
            raise RegistryConflict("purge execution lacks its exact original result")
        output = ResourceVersionRef(evidence[0].entity_id, evidence[0].version_id)
    else:
        if execution_states_for_parent(core, execution_parent(context)):
            raise RegistryConflict("purge authorization cannot own physical execution")
        output = _existing_result(owner, execution, key + ":result")
        if output is None:
            output = publish({**common, "authorization_command_ref": admission["owner_command_ref"],
                **{name: baseline[name] for name in ("material_command_ref", "target_resource_ref", "carrier_role", "disable_effect_refs")}})
    artifact = verify_resource(core, kernel, execution.operation.canonical, output)
    return OperationExecutionResult((artifact,), selected_outcome_id=config["outcome_id"])


def run_purge_operation(owner, execution):
    from .registry.firing_recovery import record_registered_operation_completion
    result = prepare_purge_operation(owner, execution)
    kernel, repository = owner.operation_repository()
    key = "material-purge:" + str(execution.operation.canonical.context.invocation_ref.version_id)
    outputs = register_operation_outputs(repository, execution, result.outputs,
        selected_outcome_id=result.selected_outcome_id, idempotency_key=key + ":outputs")
    record_registered_operation_completion(owner._core, kernel, repository, outputs, idempotency_key=key + ":completed")
    return outputs


def inspect_purge_carrier(core, carrier_ref):
    """Inspect original finite cleanup facts without granting deletion."""
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        return inspect_purge_carrier_at(core.event_store, db, core.task_id, carrier_ref)


def inspect_purge_carrier_at(store, db, task_id, carrier_ref):
    """Read exact original authorization/result facts at the caller's cut.

Missing bytes alone never establish completed cleanup.  This metadata helper
uses default carrier inspection in its proofs and therefore makes no physical
observations recursively.
"""
    from .registry._event_store.material_control import canonical_rows, row_ref, control_body, OWNER_SCHEMA
    from .registry._event_store.collaboration_descriptors import exact_prepared
    from .registry._event_store.material_settlement import material_admission, terminal_result
    from .registry._event_store.collaboration_descriptors import descriptor_store
    objects = descriptor_store(store)
    exact_prepared(db, objects, task_id, carrier_ref)
    rows = canonical_rows(store, db, task_id,
        conditions="AND json_extract(o.metadata_json,'$.content_schema_ref')=?", parameters=(OWNER_SCHEMA,))
    commands = []
    for row in rows:
        body = control_body(store, exact_prepared(db, objects, task_id, row_ref(row)))
        if body["kind"] == "command" and body.get("data", {}).get("operation") in {"authorize_purge", "purge_carrier"}:
            commands.append((row_ref(row), body))
    authorizations, results, pending = [], [], []
    for ref, command in commands:
        data = command["data"]
        if data["operation"] != "authorize_purge" or data["carrier_ref"] != carrier_ref:
            continue
        admissions = db.execute("SELECT * FROM objects WHERE object_type='firing_admission/v1' "
            "AND json_extract(metadata_json,'$.owner_command_ref.version_id')=?", (ref["version_id"],)).fetchall()
        if len(admissions) > 1:
            raise RegistryConflict("PURGE_UNKNOWN: original authorization has ambiguous admissions")
        if not admissions:
            continue
        admission = json.loads(admissions[0]["metadata_json"])
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
            (admission["transition_firing_ref"]["version_id"],)).fetchone()
        if root is None or root["state"] != "PUBLISHED":
            continue
        admission, original = material_admission(store, db, task_id, admission["transition_firing_ref"])
        _receipt, terminal = terminal_result(store, db, task_id, admission, original, root)
        authorization_ref, = terminal["output_refs"]
        read_purge_authorization(store, db, task_id, authorization_ref)
        authorizations.append(authorization_ref)
    for ref, command in commands:
        data = command["data"]
        if data["operation"] != "purge_carrier" or data["authorization_ref"] not in authorizations:
            continue
        admissions = db.execute("SELECT * FROM objects WHERE object_type='firing_admission/v1' "
            "AND json_extract(metadata_json,'$.owner_command_ref.version_id')=?", (ref["version_id"],)).fetchall()
        if len(admissions) > 1:
            raise RegistryConflict("PURGE_UNKNOWN: original cleanup has ambiguous admissions")
        if not admissions:
            pending.append({"command_ref": ref, "status": "not_admitted"})
            continue
        admission = json.loads(admissions[0]["metadata_json"])
        found = material_admission(store, db, task_id, admission["transition_firing_ref"])
        if found is None or found[0] != admission or found[1] != command:
            raise RegistryConflict("PURGE_UNKNOWN: original cleanup responsibility differs from its command")
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
            (admission["transition_firing_ref"]["version_id"],)).fetchone()
        if root is None:
            raise RegistryConflict("PURGE_UNKNOWN: original cleanup responsibility lost its publication root")
        if root["state"] != "PUBLISHED":
            pending.append({"command_ref": ref, "firing_ref": admission["transition_firing_ref"], "status": "recovery_required"})
            continue
        admission, original = material_admission(store, db, task_id, admission["transition_firing_ref"])
        receipt, terminal = terminal_result(store, db, task_id, admission, original, root)
        result_ref, = terminal["output_refs"]
        result = exact_prepared(db, objects, task_id, result_ref)
        body = json.loads(objects.read_registered(result))
        results.append({"command_ref": ref, "result_ref": result_ref, "receipt_ref": receipt, "state": body["state"]})
    return {"protocol": PROTOCOL, "carrier_ref": carrier_ref, "authorization_refs": authorizations,
            "results": results, "pending": pending, "all_carriers_cleared": False}

