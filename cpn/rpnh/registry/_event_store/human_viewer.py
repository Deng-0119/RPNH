"""Original human read command, delivery and trusted HTTP completion proofs.

The new boundary remains an original resource delivery. A consumer_evidence
string is never authority: its receipt transaction must have passed the sealed
transport-action gate inside the original writer transaction.
"""
from __future__ import annotations

import json
import re

from ..event_store import RegistryConflict
from ..schema_catalog import canonical_json
from ..strict_contracts import ref_payload
from .collaboration_descriptors import descriptor_store, exact_prepared, exact_descriptor


READ_PROTOCOL = "rpnh/human_viewer_read/v1"
READ_EXECUTOR = "rpnh/human-viewer-read/v1"
READ_IDENTITY = {"implementation_id": "rpnh.human_viewer_read", "revision": "v1"}
READ_CONFIG_SCHEMA = "application/rpnh_human_viewer_read_config/v1"
READ_CONTRACTS = {"transport": "deterministic", "input_ports": None, "output_ports": None,
    "config_schema": READ_CONFIG_SCHEMA, "owner_material_commands": "rpnh/material_command/v1",
    "owner_material_reads": READ_PROTOCOL}
TRANSPORT_PROTOCOL = "rpnh/human_viewer_transport/v1"
TRANSPORT_ADAPTER = "rpnh.viewer.http/v1"
TRANSPORT_ACTION = "human_viewer_transport"
UNKNOWN_ACTION = "human_viewer_transport_unknown"
READ_PURPOSE = "human viewer material read"


def is_read_material(data):
    return data.get("operation") == "read_material"


def validate_read_params(params):
    from .material_control import COMMAND_PROTOCOL, reference
    if (not isinstance(params, dict)
            or set(params) != {"protocol", "operation", "target", "execution", "target_resource_ref"}
            or params.get("protocol") != COMMAND_PROTOCOL or not is_read_material(params)
            or not isinstance(params.get("target"), str) or not params["target"]):
        raise RegistryConflict("INVALID_COMMAND: human read requires one finite exact material target")
    execution = params["execution"]
    if not isinstance(execution, dict) or set(execution) != {"task_ref", "task_round_ref", "net_instance_ref"}:
        raise RegistryConflict("INVALID_COMMAND: human read requires its original exact execution")
    for key, kind in (("task_ref", "task/v1"), ("task_round_ref", "task_round/v1"),
                      ("net_instance_ref", "net_instance/v1")):
        reference(execution[key], kind)
    reference(params["target_resource_ref"], "resource_version/v1")


def validate_read_baseline(store, db, task_id, params, *, current=False):
    from .material_availability import _target
    target = params["target_resource_ref"]
    item = _target(store, db, task_id, target)
    if item.metadata.get("task_ref") != params["execution"]["task_ref"]:
        raise RegistryConflict("SCOPE_NOT_ALLOWED: human read belongs to another task")
    if current:
        from ...material_body_policy import resolve_body_availability
        if resolve_body_availability(store, db, task_id, target)["availability"] != "available":
            raise RegistryConflict("MATERIAL_UNAVAILABLE: new human read requires current available body")
    return target


def validate_read_spec(spec, binding):
    if (spec.get("executor_key") != READ_EXECUTOR
            or spec.get("implementation_identity") != READ_IDENTITY
            or spec.get("implementation_contracts") != READ_CONTRACTS
            or spec.get("transport") != "deterministic"
            or spec.get("output_ports") or spec.get("allowed_tool_ids")
            or binding.get("workspace_binding_ref") is not None
            or binding.get("llm_input_target_ref") is not None
            or binding.get("output_binding_refs")):
        raise RegistryConflict("TARGET_UNAVAILABLE: human read lacks its exact registered empty operation")


def read_definition(store, db, task_id, admission, command):
    from ...executable_net import load_compiled_net
    from .material_control import _committed_descriptor
    if not is_read_material(command["data"]):
        raise RegistryConflict("INVALID_COMMAND: human viewer is not a material read command")
    row = db.execute("SELECT * FROM objects WHERE version_id=?",
        (admission["transition_firing_ref"]["version_id"],)).fetchone()
    if row is None:
        raise RegistryConflict("human read lacks its original firing")
    firing = _committed_descriptor(store, row)
    objects = descriptor_store(store)
    binding = exact_descriptor(db, objects, task_id, firing["operation_binding_ref"])
    spec = exact_descriptor(db, objects, task_id, binding["operation_spec_ref"])
    validate_read_spec(spec, binding)
    net = exact_descriptor(db, objects, task_id, firing["net_instance_ref"])
    transitions = [exact_descriptor(db, objects, task_id, ref)
                   for ref in net["executable_transition_binding_refs"]]
    matching = [item for item in transitions if item["transition_id"] == command["target"]
                and item["operation_binding_ref"] == firing["operation_binding_ref"]]
    if len(matching) != 1 or matching[0].get("agent_ref") != firing.get("agent_ref"):
        raise RegistryConflict("human read differs from its actual adopted transition")
    resource = matching[0]["declaration_resource_ref"]
    declaration = exact_prepared(db, objects, task_id, {"entity_type": "resource_version/v1",
        "logical_id": resource["resource_id"], "version_id": resource["resource_version_id"]})
    compiled = load_compiled_net(json.loads(objects.read_registered(declaration)))
    definitions = [item for item in compiled.operations if item.operation_id == spec["operation_id"]]
    if len(definitions) != 1:
        raise RegistryConflict("human read lacks its original compiled definition")
    defined, = definitions
    config = dict(defined.declaration.config)
    store.catalog.validate_schema_ref(READ_CONFIG_SCHEMA, config)
    outcomes = [item for item in defined.declaration.outcomes if item.name == config["outcome_id"]]
    if (defined.executor_declaration.get("identity") != READ_IDENTITY
            or defined.executor_declaration.get("contracts") != READ_CONTRACTS
            or defined.declaration.executor != READ_EXECUTOR or defined.declaration.tools
            or defined.declaration.outputs or len(outcomes) != 1
            or outcomes[0].products or outcomes[0].effects):
        raise RegistryConflict("human read requires its exact empty registered outcome")
    return firing, binding, spec, defined, config


def _read_start(store, db, task_id, admission, target):
    from .material_delivery_drain import _Cut
    cut = _Cut(store, db, task_id)
    rows = db.execute("SELECT e.* FROM events e JOIN firing_temporary_members m "
        "ON m.member_kind='event' AND m.member_identity=e.event_id "
        "WHERE m.firing_version_id=? AND e.event_type='operation_execution_started/v1'",
        (admission["transition_firing_ref"]["version_id"],)).fetchall()
    if len(rows) != 1:
        raise RegistryConflict("human read requires one actual original Start")
    row, = rows
    value = cut.event(row)
    target_resource = {"resource_id": target["logical_id"], "resource_version_id": target["version_id"]}
    if (value["transition_firing_ref"] != admission["transition_firing_ref"]
            or value["invocation_ref"] != admission["invocation_ref"]
            or value["operation_execution_lease_ref"] != admission["operation_execution_lease_ref"]
            or target_resource in value["input_resource_refs"]):
        raise RegistryConflict("human read must use an independent control input and exact original Start")
    return row, value


def require_read_delivery_pending(store, db, task_id, admission):
    """A registered executor return cannot be followed by new human I/O."""
    from .material_delivery_drain import _Cut
    rows = db.execute("SELECT * FROM events WHERE producer_invocation_id=? "
        "AND event_type='registered_operation_completion_recorded/v1'",
        (admission["invocation_ref"]["logical_id"],)).fetchall()
    if rows:
        cut = _Cut(store, db, task_id)
        for row in rows:
            cut.event(row)
        raise RegistryConflict("HUMAN_VIEWER_COMPLETION_RECORDED: new delivery cannot follow executor completion")


def validate_read_delivery_event(store, db, task_id, event_type, payload):
    """No arbitrary operation may acquire the new named human boundary."""
    if payload.get("boundary") != "human_viewer":
        return
    from .material_delivery_drain import _Cut
    from .material_settlement import material_admission
    cut = _Cut(store, db, task_id)
    if event_type == "resource_delivery_prepared/v1":
        context = payload["context_ref"]
        original_resource = payload["resource_ref"]
    elif event_type == "resource_release_authorized/v1":
        _row, prepared = cut.obj(payload["delivery_ref"])
        context = prepared["context_ref"]
        original_resource = prepared["resource_ref"]
    else:
        return
    _row, invocation = cut.obj(context)
    found = material_admission(store, db, cut.task_identity,
        invocation["own_transition_firing_ref"], current=True)
    if found is None:
        raise RegistryConflict("human viewer requires its original accepted read command")
    admission, command = found
    read_definition(store, db, cut.task_identity, admission, command)
    require_read_delivery_pending(store, db, cut.task_identity, admission)
    target = validate_read_baseline(store, db, cut.task_identity, command["data"], current=True)
    _read_start(store, db, cut.task_identity, admission, target)
    if (context != admission["invocation_ref"] or original_resource != {
            "resource_id": target["logical_id"], "resource_version_id": target["version_id"]}):
        raise RegistryConflict("human viewer delivery changed its exact accepted target")


def _expected_binding(admission, command, prepared, released_ref, witness_ref, *, channel):
    return {"protocol": TRANSPORT_PROTOCOL, "command_ref": admission["owner_command_ref"],
        "firing_ref": admission["transition_firing_ref"], "invocation_ref": admission["invocation_ref"],
        "actor": command["data"]["user_action"]["actor"],
        "session_ref": command["data"]["user_action"]["session_ref"],
        "resource_ref": command["data"]["target_resource_ref"], "delivery_ref": released_ref,
        "witness_ref": witness_ref, "broker_channel_id": channel,
        "byte_count": prepared["resource_byte_count"]}


def _transport_evidence(value, binding, *, outcome):
    if not isinstance(value, dict) or not isinstance(binding, dict):
        raise RegistryConflict("human viewer transport evidence is malformed")
    completed = outcome == "acknowledged"
    expected = {**binding, "adapter": TRANSPORT_ADAPTER,
        "completion": "completed_and_closed" if completed else "unknown"}
    if not completed:
        if not isinstance(value.get("reason_code"), str) or not re.fullmatch(r"[A-Z_]{1,80}", value["reason_code"]):
            raise RegistryConflict("human viewer unknown requires a finite reason code")
        expected["reason_code"] = value["reason_code"]
    if value != expected or not isinstance(binding.get("broker_channel_id"), str) or not re.fullmatch(
            r"[a-f0-9]{64}", binding["broker_channel_id"]):
        raise RegistryConflict("human viewer transport evidence differs from its exact released channel")
    return value


def validate_transport_action(store, db, *, task_id, idempotency_key, objects, events,
                              relations, action, existing=None):
    """Unconditional original commit gate for every human boundary receipt."""
    candidates = [item for item in objects if item.object_type == "delivery_boundary_receipt/v1"
                  and item.metadata.get("boundary") == "human_viewer"]
    if not candidates and (action is None or action.kind not in {TRANSPORT_ACTION, UNKNOWN_ACTION}):
        return False
    from ..user_collaboration import check_handle
    from ...human_viewer import _validated_transport_permit
    from .material_control import _historical_actor
    from .material_settlement import material_admission
    from .material_delivery_drain import _Cut
    if action is None or action.kind not in {TRANSPORT_ACTION, UNKNOWN_ACTION}:
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: receipt has no original sealed transport action")
    check_handle(store, action)
    permit = _validated_transport_permit(store, action)
    outcome = "acknowledged" if action.kind == TRANSPORT_ACTION else "unknown"
    binding, evidence = permit["binding"], permit["evidence"]
    _transport_evidence(evidence, binding, outcome=outcome)
    if (len(objects) != 1 or len(candidates) != 1 or relations
            or [event.event_type for event in events] != ["object_version_published/v1", "transaction_committed/v1"]
            or action.creation != canonical_json(evidence)):
        raise RegistryConflict("human viewer transport action must publish only its exact boundary receipt")
    item, = candidates
    metadata = dict(item.metadata)
    if (idempotency_key != "human-viewer:" + binding["invocation_ref"]["version_id"] + ":boundary:boundary-receipt"
            or item.producer_invocation_id is None
            or str(item.producer_invocation_id) != binding["invocation_ref"]["logical_id"]
            or metadata != {"receipt_id": str(item.logical_id), "receipt_version_id": str(item.version_id),
                "delivery_ref": binding["delivery_ref"], "witness_ref": binding["witness_ref"],
                "boundary": "human_viewer", "outcome": outcome,
                "positive_byte_count": binding["byte_count"] if outcome == "acknowledged" else None,
                "resource_byte_count": binding["byte_count"],
                "consumer_evidence": canonical_json(evidence).decode("utf-8")}
            or descriptor_store(store).read_registered(item) != canonical_json(metadata)):
        raise RegistryConflict("human viewer boundary receipt differs from its sealed transport observation")
    cut = _Cut(store, db, task_id)
    released, release = cut.obj(binding["delivery_ref"])
    witness_row, witness = cut.obj(binding["witness_ref"])
    prepared_row, prepared = cut.obj(release["previous_delivery_ref"])
    found = material_admission(store, db, cut.task_identity, binding["firing_ref"], current=False)
    if found is None:
        raise RegistryConflict("human viewer transport lost its original read admission")
    admission, command = found
    read_definition(store, db, cut.task_identity, admission, command)
    _read_start(store, db, cut.task_identity, admission, command["data"]["target_resource_ref"])
    wanted = _expected_binding(admission, command, prepared, binding["delivery_ref"],
        binding["witness_ref"], channel=binding["broker_channel_id"])
    _historical_actor(store, db, cut.task_identity, action.material(), current=False)
    if (binding != wanted or action.material() != {"kind": action.kind, "action_ref": None,
            "session_ref": wanted["session_ref"], "actor": wanted["actor"]}
            or release["state"] != "release_authorized" or release["boundary"] != "human_viewer"
            or release["witness_ref"] != binding["witness_ref"]
            or release["context_ref"] != admission["invocation_ref"]
            or witness["delivery_ref"] != release["previous_delivery_ref"]
            or witness["context_ref"] != admission["invocation_ref"]
            or witness["boundary"] != "human_viewer" or prepared["boundary"] != "human_viewer"
            or prepared["purpose"] != READ_PURPOSE
            or any(data["resource_ref"] != {"resource_id": wanted["resource_ref"]["logical_id"],
                "resource_version_id": wanted["resource_ref"]["version_id"]} for data in (prepared, release, witness))
            or cut.owner(released) != admission["transition_firing_ref"]["version_id"]):
        raise RegistryConflict("human viewer transport changed its original command, actor or witness")
    prefix = "human-viewer:" + admission["invocation_ref"]["version_id"]
    for original, expected_key in ((prepared_row, prefix + ":prepare"), (released, prefix + ":release"),
                                   (witness_row, prefix + ":release")):
        original_tx = db.execute("SELECT idempotency_key FROM transactions WHERE transaction_id=?",
                                (original["transaction_id"],)).fetchone()
        if original_tx is None or original_tx["idempotency_key"] != expected_key:
            raise RegistryConflict("human viewer transport differs from its original read delivery step")
    if existing is not None:
        from .user_collaboration import check_replay
        check_replay(store, db, task_id, existing, action)
    return True


def read_completion(store, db, task_id, admission, command, completion):
    """Original empty read completion, after a genuine completed HTTP receipt."""
    from .material_locations import _original_member_objects, _completed_petri_inputs
    from .material_control import row_ref, OWNER_SCHEMA, control_body
    from .material_delivery_drain import _Cut, _delivery_chain
    from ..models import PreparedObject
    from ..identities import TypedId
    firing, _binding, spec, _defined, config = read_definition(store, db, task_id, admission, command)
    target = validate_read_baseline(store, db, task_id, command["data"], current=False)
    data = completion.payload
    if (data["ordered_outputs"] or data["workspace_revision_candidate_ref"] is not None
            or data["selected_outcome_id"] != config["outcome_id"]
            or data["operation_binding_ref"] != firing["operation_binding_ref"]
            or data["operation_spec_ref"] != spec["operation_spec_ref"]):
        raise RegistryConflict("human read completion differs from its original empty outcome")
    rows = _original_member_objects(store, db, task_id, admission)
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
        (admission["transition_firing_ref"]["version_id"],)).fetchone()
    rows = [row for row in rows if row["transaction_id"] != root["published_transaction_id"]]
    if any(row["ordinal"] >= completion.ordinal for row in rows):
        raise RegistryConflict("human read added a responsibility after registered completion")
    human = [row for row in rows if row["object_type"] in {
        "resource_delivery/v1", "resource_release_witness/v1", "delivery_boundary_receipt/v1"}
        and json.loads(row["metadata_json"]).get("boundary") == "human_viewer"]
    preparations = [row for row in human if row["object_type"] == "resource_delivery/v1"
                    and json.loads(row["metadata_json"])["state"] == "prepared"]
    if len(preparations) != 1:
        raise RegistryConflict("human read requires one exact original delivery")
    cut = _Cut(store, db, task_id)
    chain = _delivery_chain(cut, preparations[0]["logical_id"], {(target["logical_id"], target["version_id"])})
    if chain["prepared_only"] or chain["owner"] != admission["transition_firing_ref"]["version_id"]:
        raise RegistryConflict("human read has no completed original transport")
    prepared_row, prepared = chain["stages"]["prepared"]
    released_row, released = chain["stages"]["release_authorized"]
    boundary_row, boundary = chain["boundary"]
    observed, _observed = chain["facts"]["observed_read/v1"]
    start, _start_value = _read_start(store, db, task_id, admission, target)
    if (data["operation_start_event_id"] != start["event_id"]
            or not start["ordinal"] < observed["ordinal"] < completion.ordinal
            or prepared["purpose"] != READ_PURPOSE or prepared["context_ref"] != admission["invocation_ref"]
            or prepared["boundary"] != "human_viewer"):
        raise RegistryConflict("human read delivery is outside its actual original operation")
    evidence = json.loads(boundary["consumer_evidence"])
    binding = _expected_binding(admission, command, prepared, row_ref(released_row),
        row_ref(chain["witness"][0]), channel=evidence.get("broker_channel_id"))
    _transport_evidence(evidence, binding, outcome="acknowledged")
    if canonical_json(evidence).decode("utf-8") != boundary["consumer_evidence"]:
        raise RegistryConflict("human read evidence is not its canonical original receipt")
    tx = db.execute("SELECT * FROM transactions WHERE transaction_id=?", (boundary_row["transaction_id"],)).fetchone()
    original_action = json.loads(tx["command_json"]).get("user_collaboration") if tx is not None else None
    if (tx is None or tx["status"] != "committed" or original_action != {
            "kind": TRANSPORT_ACTION, "action_ref": None,
            "session_ref": binding["session_ref"], "actor": binding["actor"]}
            or tx["idempotency_key"] != "human-viewer:" + admission["invocation_ref"]["version_id"] + ":boundary:boundary-receipt"):
        raise RegistryConflict("human read receipt has no original trusted transport action")
    prefix = "human-viewer:" + admission["invocation_ref"]["version_id"]
    for original, suffix in ((prepared_row, ":prepare"), (released_row, ":release"),
                             (chain["witness"][0], ":release"),
                             (chain["stages"]["acknowledged"][0], ":acknowledge")):
        original_tx = db.execute("SELECT idempotency_key FROM transactions WHERE transaction_id=?",
                                (original["transaction_id"],)).fetchone()
        if original_tx is None or original_tx["idempotency_key"] != prefix + suffix:
            raise RegistryConflict("human read completion differs from its original delivery step")
    proof_versions = {ref["version_id"] for ref in chain["proof_refs"]}
    if proof_versions != {row["version_id"] for row in human}:
        raise RegistryConflict("human read contains an additional unresolved human delivery")
    rest = [row for row in rows if row["version_id"] not in proof_versions]
    inputs = _completed_petri_inputs(store, db, admission, completion, rest)
    grant_row, grant = cut.obj(prepared["authorization_ref"])
    if (grant_row["object_type"] != "capability_grant/v1"
            or grant.get("target_invocation_id") != admission["invocation_ref"]["logical_id"]
            or grant.get("operation_binding_ref") != firing["operation_binding_ref"]
            or grant.get("resource_version_ids") != [target["version_id"]]):
        raise RegistryConflict("human read requires its exact single-target original grant")
    allowed = {"transition_firing/v1", "invocation/v1", "firing_admission/v1", "operation_execution_lease/v1"}
    for row in rest:
        if row["object_type"] in allowed or row["version_id"] in inputs or row_ref(row) == prepared["authorization_ref"]:
            continue
        if row["object_type"] == "marking_delta/v1" and row_ref(row) == admission["claim_marking_delta_ref"]:
            continue
        if row["object_type"] == "resource_version/v1" and json.loads(row["metadata_json"]).get("content_schema_ref") == OWNER_SCHEMA:
            original = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]),
                row["size"], row["media_type"], row["schema_ref"], None, row["storage_locator"], json.loads(row["metadata_json"]))
            receipt = control_body(store, original)
            if (receipt["data"].get("protocol") == "rpnh/material_admission/v1"
                    and receipt["data"].get("command_ref") == admission["owner_command_ref"]
                    and receipt["data"].get("admission_ref") == admission["firing_admission_ref"]):
                continue
        raise RegistryConflict("human read contains an undeclared body or execution responsibility")
    return {"delivery_id": prepared_row["logical_id"], "proof_refs": chain["proof_refs"],
            "boundary_receipt_ref": row_ref(boundary_row), "transport_evidence": evidence}


__all__ = ("READ_PROTOCOL", "READ_EXECUTOR", "READ_IDENTITY", "READ_CONFIG_SCHEMA", "READ_CONTRACTS",
    "is_read_material", "validate_read_params", "validate_read_baseline", "validate_read_spec",
    "read_definition", "read_completion", "validate_transport_action", "validate_read_delivery_event",
    "require_read_delivery_pending")
