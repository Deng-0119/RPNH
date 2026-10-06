"""Read exact physical delivery closure at a caller-owned local source cut."""
from __future__ import annotations

import hashlib
import json

from ..registry.event_store import RegistryConflict
from ..registry.identities import TypedId
from ..registry.models import PreparedObject
from ..registry.publication import _ref_payload
from ..registry.schema_catalog import canonical_text
from ..registry._event_store.collaboration_descriptors import (
    _canonical_closure, exact_descriptor, exact_prepared, readable_descriptor)
from ..registry._event_store.source_identity import read_source_binding


def _fail(message):
    raise RegistryConflict("physical delivery closure: " + message)


def _object(source, db, ref, *, active=None):
    row = db.execute("SELECT * FROM objects WHERE object_type=? AND logical_id=? AND version_id=?",
                     (ref["entity_type"], ref["logical_id"], ref["version_id"])).fetchone()
    if row is None:
        _fail("exact object is absent")
    if _canonical_closure(db, source.task_id, transaction_id=row["transaction_id"],
            members=(("object", row["version_id"]), ("event", row["published_event_id"]))):
        return exact_descriptor(db, source.object_store, source.task_id, ref)
    if active is None:
        _fail("object is not canonical at the source cut")
    invocation = active.context.invocation_ref
    invocation_self = (ref["entity_type"] == "invocation/v1" and ref == _ref_payload(invocation))
    expected_producer = None if invocation_self else str(invocation.entity_id)
    publication = db.execute("SELECT * FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
    terminals = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                            (row["transaction_id"],)).fetchall()
    transaction = db.execute("SELECT status FROM transactions WHERE transaction_id=?", (row["transaction_id"],)).fetchone()
    root = db.execute("SELECT * FROM firing_publications WHERE invocation_version_id=?", (str(invocation.version_id),)).fetchone()
    if (publication is None or transaction is None or transaction[0] != "committed" or len(terminals) != 1
            or root is None or root["state"] != "PROVISIONAL" or root["invocation_logical_id"] != str(invocation.entity_id)
            or root["net_version_id"] != str(active.context.net_instance_ref.version_id)
            or row["producer_invocation_id"] != expected_producer
            or publication["producer_invocation_id"] != expected_producer
            or publication["event_type"] != "object_version_published/v1"
            or publication["payload_schema_ref"] != "registry_v1/object_version_published/v1"
            or publication["criticality"] != "authoritative"
            or publication["aggregate_id"] != row["logical_id"] or publication["aggregate_type"] != row["object_type"]
            or publication["producer_principal"] != "framework"
            or publication["task_id"] != str(source.task_id) or terminals[0]["task_id"] != str(source.task_id)
            or terminals[0]["criticality"] != "authoritative"
            or terminals[0]["payload_schema_ref"] != "registry_v1/transaction_committed/v1"
            or terminals[0]["stream_id"] != "transaction:" + row["transaction_id"]
            or terminals[0]["aggregate_id"] != row["transaction_id"] or terminals[0]["aggregate_type"] != "transaction"
            or terminals[0]["producer_invocation_id"] is not None or terminals[0]["producer_principal"] != "framework"
            or publication["transaction_id"] != row["transaction_id"]
            or publication["stream_id"] != "object:" + row["logical_id"]):
        _fail("temporary object lacks the exact active sender/commit closure")
    for kind, identity in (("object", row["version_id"]), ("event", row["published_event_id"]),
                           ("event", terminals[0]["event_id"]), ("transaction", row["transaction_id"])):
        roots = db.execute("SELECT firing_version_id FROM firing_temporary_members WHERE member_kind=? AND member_identity=?",
                            (kind, identity)).fetchall()
        if len(roots) != 1 or roots[0][0] != root["firing_version_id"]:
            _fail("temporary member escapes its exact sender root")
    metadata = json.loads(row["metadata_json"])
    if invocation_self and (metadata["invocation_ref"] != ref
            or metadata["principal_ref"] != _ref_payload(active.context.principal_ref)
            or metadata["net_instance_ref"] != _ref_payload(active.context.net_instance_ref)
            or metadata["task_ref"] != _ref_payload(active.context.task_ref)):
        _fail("invocation self object differs from its original sender authority")
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]),
        row["size"], row["media_type"], row["schema_ref"],
        TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] is not None else None,
        row["storage_locator"], metadata)
    if json.loads(publication["payload_json"]) != {
            "object_type": row["object_type"], "logical_id": row["logical_id"], "version_id": row["version_id"],
            "size": row["size"], "media_type": row["media_type"], "schema_ref": row["schema_ref"],
            "storage_locator": row["storage_locator"], "metadata": metadata}:
        _fail("publication differs from exact immutable object")
    return readable_descriptor(source.object_store, prepared)


def read_delivery_evidence(source, db, logical_ref, physical_ref, *, active=None, terminal_required=False):
    """Bind L/export/resource/attempt/witness/receipt and exact source producer."""
    row = read_source_binding(db, source.catalog, source.task_id)
    binding = None if row is None else json.loads(row["binding_metadata_json"])
    if (binding is None or logical_ref["source_id"] != binding["source_id"]
            or physical_ref["source_id"] != binding["source_id"]
            or logical_ref["ref"]["entity_type"] != "collaboration_logical_delivery/v1"
            or physical_ref["ref"]["entity_type"] != "resource_delivery/v1"):
        _fail("source or exact reference type differs")
    logical = exact_descriptor(db, source.object_store, source.task_id, logical_ref["ref"])
    if logical["record_ref"] != logical_ref:
        _fail("logical self identity differs")
    exported_ref = logical["body"]["export_ref"]
    if exported_ref["source_id"] != binding["source_id"]:
        _fail("export changed source")
    exported = exact_descriptor(db, source.object_store, source.task_id, exported_ref["ref"])
    resource_ref = exported["body"]["output_resource_ref"]
    resource = exact_prepared(db, source.object_store, source.task_id, resource_ref)
    payload = source.object_store.read_registered(resource)
    if hashlib.sha256(payload).hexdigest() != exported["body"]["content_digest"]:
        _fail("export bytes changed")
    physical = _object(source, db, physical_ref["ref"], active=active)
    if (physical["delivery_id"] != physical_ref["ref"]["logical_id"]
            or physical["delivery_version_id"] != physical_ref["ref"]["version_id"]):
        _fail("physical self identity differs")
    terminal = physical["state"] in {"acknowledged", "unknown", "failed"}
    if terminal_required and not terminal:
        _fail("physical attempt has no established terminal")
    authorized_ref = physical["previous_delivery_ref"] if terminal else physical_ref["ref"]
    authorized = _object(source, db, authorized_ref, active=active) if terminal else physical
    if authorized["state"] != "release_authorized":
        _fail("attempt has no exact authorized release")
    prepared = _object(source, db, authorized["previous_delivery_ref"], active=active)
    witness = _object(source, db, authorized["witness_ref"], active=active)
    invocation = _object(source, db, authorized["context_ref"], active=active)
    expected_resource = {"resource_id": resource_ref["logical_id"], "resource_version_id": resource_ref["version_id"]}
    for value in (prepared, authorized, physical):
        if (value["delivery_id"] != physical_ref["ref"]["logical_id"]
                or value["resource_ref"] != expected_resource or value["resource_byte_count"] != len(payload)
                or value["purpose"] != canonical_text(logical_ref) or value["boundary"] != "parent_receipt"
                or value["context_ref"] != authorized["context_ref"]
                or value["authorization_ref"] != invocation["operation_binding_ref"]):
            _fail("physical identity/resource/context does not carry this logical export")
    if (prepared["state"] != "prepared" or prepared["previous_delivery_ref"] is not None
            or authorized["delivery_version_id"] != authorized_ref["version_id"]
            or prepared["delivery_version_id"] != authorized["previous_delivery_ref"]["version_id"]
            or authorized["context_ref"] != invocation["invocation_ref"]
            or invocation["task_ref"] != binding["task_ref"]
            or any(witness[key] != authorized[key] for key in
                   ("resource_ref", "resource_byte_count", "boundary", "context_ref", "authorization_ref", "expires_at"))
            or witness["delivery_ref"] != authorized["previous_delivery_ref"]
            or witness["witness_id"] != authorized["witness_ref"]["logical_id"]
            or witness["witness_version_id"] != authorized["witness_ref"]["version_id"]):
        _fail("release witness or sender scope differs")
    release_events = db.execute("SELECT * FROM events WHERE event_type='resource_release_authorized/v1' AND aggregate_id=?",
                                (physical_ref["ref"]["logical_id"],)).fetchall()
    if len(release_events) != 1:
        _fail("attempt needs one original release-authorized fact")
    release_event = release_events[0]
    authorized_tx = db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (authorized_ref["version_id"],)).fetchone()[0]
    witness_tx = db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (authorized["witness_ref"]["version_id"],)).fetchone()[0]
    expected = {"delivery_ref": authorized["previous_delivery_ref"], "witness_ref": authorized["witness_ref"],
                "resource_ref": expected_resource, "resource_byte_count": len(payload),
                "boundary": "parent_receipt", "expires_at": authorized["expires_at"]}
    if (json.loads(release_event["payload_json"]) != expected
            or release_event["criticality"] != "authoritative"
            or release_event["payload_schema_ref"] != "registry_v1/resource_release_authorized/v1"
            or release_event["stream_id"] != "resource-delivery:" + physical_ref["ref"]["logical_id"]
            or release_event["aggregate_type"] != "resource_delivery/v1"
            or release_event["transaction_id"] != authorized_tx or authorized_tx != witness_tx
            or release_event["producer_invocation_id"] != invocation["invocation_ref"]["logical_id"]
            or release_event["producer_principal"] != invocation["principal_ref"]["logical_id"]
            or release_event["task_id"] != str(source.task_id)):
        _fail("release fact differs from its actual producer and witness")
    if active is not None:
        if (active.logical_delivery_ref.to_dict() != logical_ref or active.physical_delivery_ref.to_dict() != physical_ref
                or active.payload != payload or _ref_payload(active.context.invocation_ref) != authorized["context_ref"]
                or _ref_payload(active.release.witness_ref) != authorized["witness_ref"]
                or _ref_payload(active.release.delivery_ref) != authorized_ref):
            _fail("live consumed channel differs from durable source evidence")
        with active.kernel._ResourceServiceKernel__release_lock:
            channel = active.kernel._ResourceServiceKernel__release_channels.get(active.release.broker_channel_ref.channel_id)
            if (channel is None or not channel["released"] or channel["witness_ref"] != active.release.witness_ref
                    or channel["delivery_ref"] != active.release.delivery_ref or channel["context_ref"] != active.context.invocation_ref
                    or channel["resource_ref"] != active.release.exact_resource_ref or channel["byte_count"] != len(payload)
                    or channel["boundary"] != "parent_receipt" or channel["expires_at"] != authorized["expires_at"]):
                _fail("original one-use channel has not delivered these bytes")
    if terminal:
        receipt = _object(source, db, physical["boundary_receipt_ref"], active=active)
        if (physical["witness_ref"] != authorized["witness_ref"] or receipt["delivery_ref"] != authorized_ref
                or receipt["receipt_id"] != physical["boundary_receipt_ref"]["logical_id"]
                or receipt["receipt_version_id"] != physical["boundary_receipt_ref"]["version_id"]
                or receipt["witness_ref"] != authorized["witness_ref"] or receipt["boundary"] != "parent_receipt"
                or receipt["resource_byte_count"] != len(payload) or receipt["outcome"] != physical["state"]
                or (physical["state"] == "acknowledged" and receipt["positive_byte_count"] != len(payload))):
            _fail("physical terminal lacks its original receipt closure")
        terminal_events = db.execute("SELECT * FROM events WHERE aggregate_id=? AND event_type IN "
            "('resource_delivery_acknowledged/v1','resource_delivery_unknown/v1','resource_delivery_failed/v1')",
            (physical_ref["ref"]["logical_id"],)).fetchall()
        expected_terminal = {"delivery_ref": authorized_ref, "terminal_delivery_ref": physical_ref["ref"],
            "witness_ref": authorized["witness_ref"], "boundary_receipt_ref": physical["boundary_receipt_ref"],
            "outcome": physical["state"]}
        physical_tx = db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (physical_ref["ref"]["version_id"],)).fetchone()[0]
        if (len(terminal_events) != 1 or json.loads(terminal_events[0]["payload_json"]) != expected_terminal
                or terminal_events[0]["criticality"] != "authoritative"
                or terminal_events[0]["event_type"] != "resource_delivery_" + physical["state"] + "/v1"
                or terminal_events[0]["payload_schema_ref"] != "registry_v1/resource_delivery_" + physical["state"] + "/v1"
                or terminal_events[0]["stream_id"] != "resource-delivery:" + physical_ref["ref"]["logical_id"]
                or terminal_events[0]["aggregate_type"] != "resource_delivery/v1"
                or terminal_events[0]["transaction_id"] != physical_tx
                or terminal_events[0]["producer_invocation_id"] != invocation["invocation_ref"]["logical_id"]
                or terminal_events[0]["producer_principal"] != invocation["principal_ref"]["logical_id"]
                or terminal_events[0]["task_id"] != str(source.task_id)):
            _fail("physical terminal fact differs from its original receipt and producer")
    return logical, exported, payload, physical
