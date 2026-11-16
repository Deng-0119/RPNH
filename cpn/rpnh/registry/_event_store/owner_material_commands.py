"""Owner material command bindings in the original admission transaction.

An accepted command is a canonical OwnerControl resource whose publication
carried the original sealed HOST action.  Its one-firing association lives in
the existing firing_admission object, including after that firing closes.
This module adds no command ledger and never opens another SQLite snapshot.
"""
from __future__ import annotations

import json
from collections.abc import Mapping

from ..event_store import RegistryConflict
from ..schema_catalog import canonical_json

OWNER_SCHEMA = "rpnh/owner_control/v1"
MATERIAL_COMMAND = "rpnh/material_command/v1"
MATERIAL_ADMISSION = "rpnh/material_admission/v1"


def _reference(value, kind):
    if (not isinstance(value, Mapping)
            or set(value) != {"entity_type", "logical_id", "version_id"}
            or value["entity_type"] != kind
            or any(not isinstance(value[name], str) or not value[name]
                   for name in ("logical_id", "version_id"))):
        raise RegistryConflict("material admission requires an exact " + kind)
    return dict(value)


def _metadata(db, reference, kind):
    ref = _reference(reference, kind)
    row = db.execute(
        "SELECT metadata_json FROM objects WHERE object_type=? "
        "AND logical_id=? AND version_id=?",
        (kind, ref["logical_id"], ref["version_id"]),
    ).fetchone()
    if row is None:
        raise RegistryConflict("material admission lacks its original " + kind)
    return json.loads(row["metadata_json"])


def validate_owner_material_admissions(
        event_store, db, *, task_id, objects, events, relations):
    """Validate original admissions and return exact material publication keys.

    The original commit's sealed-action validator has already checked new
    preparation/submission resources. Admission receipts acquire their scoped
    publication exception only after this function verifies the whole batch.
    Returned entries never authorize arbitrary bootstrap resources.
    """
    admissions = [item for item in objects
                  if item.object_type == "firing_admission/v1"]
    controls = [item for item in objects
                if item.object_type == "resource_version/v1"
                and item.metadata.get("content_schema_ref") == OWNER_SCHEMA]
    if not admissions and not controls:
        return {}
    from .material_control import control_body, read_accepted_material_command
    receipts = []
    publication_keys = {}
    for item in controls:
        body = control_body(event_store, item)
        protocol = body["data"].get("protocol")
        if protocol == MATERIAL_ADMISSION:
            receipts.append((item, body))
        elif protocol == MATERIAL_COMMAND and body["kind"] == "command":
            publication_keys[str(item.version_id)] = (
                "owner:" + body["command_id"] + ":command:", None)
        elif protocol == "rpnh/material_input_receipt/v1" and body["kind"] == "receipt":
            publication_keys[str(item.version_id)] = (
                "owner:" + body["command_id"] + ":receipt:material-input", None)
    by_ref = {
        (item.object_type, str(item.logical_id), str(item.version_id)): item
        for item in objects
    }
    matched_receipts = set()
    command_refs = set()
    for item in admissions:
        admission = item.metadata
        firing_ref = _reference(
            admission.get("transition_firing_ref"), "transition_firing/v1")
        firing_item = by_ref.get(tuple(firing_ref[name] for name in ("entity_type", "logical_id", "version_id")))
        if firing_item is None:
            raise RegistryConflict("material admission is missing its same-transaction firing")
        firing = firing_item.metadata
        binding = _metadata(db, firing["operation_binding_ref"], "operation_binding/v1")
        spec = _metadata(db, binding["operation_spec_ref"], "operation_spec/v1")
        implementation = spec.get("implementation_contracts", {})
        marker = implementation.get("owner_material_commands")
        command_ref = admission.get("owner_command_ref")
        if marker != MATERIAL_COMMAND:
            if "owner_material_commands" in implementation or command_ref is not None:
                raise RegistryConflict("ordinary operation cannot bind an owner material command")
            continue
        if (spec.get("transport") != "deterministic"
                or firing.get("agent_ref") is None):
            raise RegistryConflict("material operation requires its actual deterministic agent")
        command_ref = _reference(command_ref, "resource_version/v1")
        key = tuple(command_ref[name] for name in ("entity_type", "logical_id", "version_id"))
        if key in command_refs or db.execute(
                "SELECT 1 FROM objects o JOIN transactions t "
                "ON t.transaction_id=o.transaction_id "
                "WHERE o.object_type='firing_admission/v1' "
                "AND json_extract(o.metadata_json,'$.owner_command_ref.version_id')=? "
                "AND t.status='committed' LIMIT 1",
                (command_ref["version_id"],)).fetchone() is not None:
            raise RegistryConflict("owner material command already has a firing admission")
        command_refs.add(key)
        from .material_not_admitted import read_not_admitted
        if read_not_admitted(event_store, db, task_id, command_ref) is not None:
            raise RegistryConflict("COMMAND_NOT_ADMITTED: original material command is already terminal")
        command = read_accepted_material_command(
            event_store, db, task_id, command_ref)
        from .material_control import validate_active_target, validate_material_baseline
        params = {name: value for name, value in command["data"].items() if name != "user_action"}
        params["target"] = command["target"]
        validate_active_target(event_store, db, task_id, params, command["source"])
        validate_material_baseline(event_store, db, task_id, params)
        if (command["target"] != firing["transition_id"]
                or command["data"]["execution"] != {
                    name: firing[name]
                    for name in ("task_ref", "task_round_ref", "net_instance_ref")}):
            raise RegistryConflict("material command differs from the actual firing scope")
        admission_ref = {
            "entity_type": "firing_admission/v1",
            "logical_id": str(item.logical_id), "version_id": str(item.version_id),
        }
        admitted_events = [event for event in events
                           if event.event_type == "firing_admitted/v1"
                           and event.payload.get("firing_admission_ref") == admission_ref
                           and event.payload.get("transition_firing_ref") == firing_ref
                           and event.payload.get("invocation_ref") == admission["invocation_ref"]]
        if len(admitted_events) != 1:
            raise RegistryConflict("material command requires its actual original admission event")
        expected_data = {
            "protocol": MATERIAL_ADMISSION, "command_ref": command_ref,
            "firing_ref": firing_ref, "invocation_ref": admission["invocation_ref"],
            "admission_ref": admission_ref,
        }
        matches = [(receipt, body) for receipt, body in receipts
                   if body["data"] == expected_data
                   and body["kind"] == "receipt"
                   and body["command_id"] == command["command_id"]
                   and body["target"] == command["target"]
                   and body["source"] == command["source"]]
        if len(matches) != 1:
            raise RegistryConflict("material admission requires its one exact same-transaction receipt")
        receipt, body = matches[0]
        if (len(body["lineage"]) != 4
                or set(map(canonical_json, body["lineage"])) != set(map(canonical_json, (
                    command_ref, admission_ref, firing_ref, admission["invocation_ref"])) )):
            raise RegistryConflict("material admission receipt lineage differs from its original facts")
        bootstrap_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='bootstrap_command_ref'"
        ).fetchone()
        if (bootstrap_row is None or receipt.metadata.get("producer_ref")
                != json.loads(bootstrap_row["value"])):
            raise RegistryConflict("material admission receipt is not an owner publication")
        matched_receipts.add(str(receipt.version_id))
        publication_keys[str(receipt.version_id)] = (
            "owner:" + command["command_id"] + ":receipt:material-admission",
            (firing["task_round_ref"]["logical_id"],
             firing["net_instance_ref"]["logical_id"]),
        )
        if not any(relation.relation_type == "derived_from"
                   and relation.strength == "strong"
                   and relation.source.entity_type == "firing_admission/v1"
                   and relation.source.entity_id == item.logical_id
                   and getattr(relation.source, "version_id", None) == item.version_id
                   and relation.target.entity_type == "resource_version/v1"
                   and str(relation.target.entity_id) == command_ref["logical_id"]
                   and str(getattr(relation.target, "version_id", None)) == command_ref["version_id"]
                   for relation in relations):
            raise RegistryConflict("material admission lacks its original command relation")
    if matched_receipts != {str(item.version_id) for item, _body in receipts}:
        raise RegistryConflict("material admission receipt has no matching original admission")
    return publication_keys
