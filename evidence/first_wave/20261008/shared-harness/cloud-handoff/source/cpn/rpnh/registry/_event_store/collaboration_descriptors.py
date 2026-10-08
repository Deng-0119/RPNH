"""Exact readable descriptors at one collaboration publication snapshot.

These checks establish descriptor authority, not the semantics of referenced
author materials. Files are read only through the Registry's immutable store.
"""

from __future__ import annotations

import json

from ..identities import TypedId
from ..models import PreparedObject
from ..schema_catalog import canonical_json
from ..object_store import ObjectIntegrityError, ObjectStore


def descriptor_store(event_store):
    # The same fixed layout owned by _RegistryCore, never a submitted locator.
    return ObjectStore(event_store.path.parent / "objects", event_store.catalog, read_only=True)


def readable_payload(store, prepared, *, media_type, max_bytes=None):
    from ..event_store import RegistryConflict

    if (prepared.media_type != media_type
            or prepared.schema_ref != store.catalog.require(prepared.object_type, category="object").schema_ref):
        raise RegistryConflict("collaboration descriptor media/schema is not supported")
    try:
        return (store.read_registered(prepared) if max_bytes is None else
                store.read_registered(prepared, max_bytes=max_bytes))
    except ObjectIntegrityError as exc:
        raise RegistryConflict("collaboration descriptor lacks readable exact immutable bytes") from exc


def readable_descriptor(store, prepared, *, max_bytes=None, strict=False):
    from ..event_store import RegistryConflict

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON descriptor key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON descriptor number")

    try:
        raw = readable_payload(store, prepared, media_type="application/json", max_bytes=max_bytes)
        document = (json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
                    if strict else json.loads(raw))
        if strict:
            json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise RegistryConflict("collaboration descriptor lacks readable exact immutable bytes") from exc
    if canonical_json(document) != canonical_json(dict(prepared.metadata)):
        raise RegistryConflict("collaboration descriptor bytes differ from registered metadata")
    store.catalog.validate_instance(prepared.object_type, category="object", instance=document)
    return document


def _canonical_closure(db, task_id, *, transaction_id, members):
    """Require committed facts and any PUBLISHED promotion at this same cut.

    A published firing's own terminal may belong to that firing. Visit each
    transaction once after validating its terminal, allowing that legal closure
    without treating PROVISIONAL membership as canonical authority.
    """
    pending_transactions = [transaction_id]
    pending_members = list(members)
    checked_transactions, checked_members = set(), set()
    while pending_transactions or pending_members:
        while pending_transactions:
            transaction = pending_transactions.pop()
            if transaction in checked_transactions:
                continue
            row = db.execute("SELECT status FROM transactions WHERE transaction_id=?", (transaction,)).fetchone()
            terminals = db.execute("SELECT * FROM events WHERE transaction_id=? "
                "AND event_type='transaction_committed/v1'", (transaction,)).fetchall()
            if row is None or row["status"] != "committed" or len(terminals) != 1:
                return False
            terminal = terminals[0]
            if (terminal["criticality"] != "authoritative" or terminal["task_id"] != str(task_id)
                    or terminal["stream_id"] != f"transaction:{transaction}"
                    or terminal["aggregate_id"] != transaction or terminal["aggregate_type"] != "transaction"
                    or terminal["payload_schema_ref"] != "registry_v1/transaction_committed/v1"
                    or terminal["producer_invocation_id"] is not None):
                return False
            checked_transactions.add(transaction)
            pending_members.append(("event", terminal["event_id"]))
        while pending_members:
            member = pending_members.pop()
            if member in checked_members:
                continue
            checked_members.add(member)
            roots = db.execute("SELECT p.state,p.published_transaction_id FROM firing_temporary_members m "
                "LEFT JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
                "WHERE m.member_kind=? AND m.member_identity=?", member).fetchall()
            for root in roots:
                if root["state"] != "PUBLISHED" or root["published_transaction_id"] is None:
                    return False
                pending_transactions.append(root["published_transaction_id"])
    return True


def exact_prepared(db, store, task_id, ref):
    """Resolve exact metadata and canonical publication, without reading payload."""
    from ..event_store import RegistryConflict

    row = db.execute("SELECT o.*, e.event_type AS publication_type, e.transaction_id AS publication_transaction, "
        "e.task_id AS publication_task, e.stream_id AS publication_stream, e.aggregate_id AS publication_aggregate, "
        "e.aggregate_type AS publication_aggregate_type, e.criticality AS publication_criticality, "
        "e.payload_schema_ref AS publication_schema, e.producer_invocation_id AS publication_invocation, "
        "e.payload_json AS publication_json FROM objects o "
        "JOIN events e ON e.event_id=o.published_event_id WHERE o.object_type=? AND o.logical_id=? AND o.version_id=?",
        (ref["entity_type"], ref["logical_id"], ref["version_id"])).fetchone()
    if (row is None or row["publication_type"] != "object_version_published/v1"
            or row["publication_transaction"] != row["transaction_id"]
            or row["publication_task"] != str(task_id) or row["publication_stream"] != f"object:{ref['logical_id']}"
            or row["publication_aggregate"] != ref["logical_id"] or row["publication_aggregate_type"] != ref["entity_type"]
            or row["publication_criticality"] != "authoritative"
            or row["publication_invocation"] != row["producer_invocation_id"]
            or row["publication_schema"] != "registry_v1/object_version_published/v1"
            or not _canonical_closure(db, task_id, transaction_id=row["transaction_id"],
                members=(("object", ref["version_id"]), ("event", row["published_event_id"])))):
        raise RegistryConflict("collaboration descriptor lacks canonical publication/commit closure")
    metadata = json.loads(row["metadata_json"])
    prepared = PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]),
        row["size"], row["media_type"], row["schema_ref"],
        TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] else None,
        row["storage_locator"], metadata)
    if canonical_json(json.loads(row["publication_json"])) != canonical_json({
            "object_type": prepared.object_type, "logical_id": str(prepared.logical_id), "version_id": str(prepared.version_id),
            "schema_ref": prepared.schema_ref, "storage_locator": prepared.storage_locator,
            "size": prepared.size, "media_type": prepared.media_type, "metadata": metadata}):
        raise RegistryConflict("collaboration descriptor differs from its exact publication")
    store.catalog.validate_instance(prepared.object_type, category="object", instance=metadata)
    return prepared


def exact_descriptor(db, store, task_id, ref):
    """Read a pre-existing exact object, publication, terminal and bytes."""
    return readable_descriptor(store, exact_prepared(db, store, task_id, ref))
