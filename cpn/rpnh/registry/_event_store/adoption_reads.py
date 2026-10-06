"""Fixed same-DB canonical reads for the opt-in adoption prefix.

Only specifically consumed witness JSON is read. This is not a complete
payload digest/evidence traversal and does not establish execution permission.
"""
from __future__ import annotations

import json
from typing import Mapping

from .collaboration_descriptors import exact_prepared, readable_payload, _canonical_closure
from ..event_store import RegistryCorruptError, fact_event_envelope
from ..object_store import ObjectStore
from ..schema_catalog import SchemaGovernanceError, canonical_json


# Finite JSON metadata roles reached by the supported basis/owner closure.
# In particular, tar-backed workspace objects are not JSON descriptors here.
_BASIS_DESCRIPTOR_TYPES = frozenset({
    "collaboration_source_binding/v1", "task/v1", "native_run_identity/v1", "bootstrap_command/v1",
    "registry_type_catalog/v1", "task_branch/v1", "native_genesis_manifest/v1", "task_round/v1",
    "principal/v1", "user_authority_decision/v1", "net_instance/v1", "team_design_root/v1",
    "node_declaration/v1", "operation_binding/v1", "output_binding/v1", "operation_spec/v1",
    "logical_artifact_slot/v1", "marking_checkpoint/v1", "transition_firing/v1", "operation_result/v1",
})


class AdoptionPrefixReads:
    """Internal policy constructed from the owning EventStore and supplied cut."""
    __slots__ = ("store", "catalog", "db", "task_id", "object_store", "_plan_reads")

    def __init__(self, store, catalog, db, task_id, *, _plan_reads=None):
        self.store, self.catalog, self.db, self.task_id = store, catalog, db, task_id
        self.object_store = ObjectStore(store.path.parent / "objects", catalog, read_only=True)
        self._plan_reads = _plan_reads
        if _plan_reads is not None:
            from .._candidate_plan_reads import PlanReadClosure, RecordingStore
            if (type(_plan_reads) is not PlanReadClosure or _plan_reads.db is not db
                    or _plan_reads.catalog is not catalog or _plan_reads.task_id != task_id
                    or type(_plan_reads.store) is not RecordingStore
                    or type(_plan_reads.store._store) is not ObjectStore
                    or _plan_reads.store._store.root.resolve() != self.object_store.root.resolve()):
                raise TypeError("basis evidence requires the fixed same-Registry read closure")
            self.object_store = _plan_reads.store

    def prepared(self, ref, expected_type=None):
        if expected_type is not None and ref.entity_type != expected_type:
            raise RegistryCorruptError("adoption prefix reference has the wrong exact type")
        if (self._plan_reads is not None and ref.entity_type != "resource_version/v1"
                and ref.entity_type not in _BASIS_DESCRIPTOR_TYPES):
            raise RegistryCorruptError("unsupported basis dependency object role")
        prepared = self._canonical_prepared(ref)
        payload = {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id), "version_id": str(ref.version_id)}
        if self._plan_reads is not None:
            if prepared.object_type == "resource_version/v1":
                self._plan_reads.resource(payload)
            else:
                self._plan_reads.descriptor(payload)
        return prepared

    def _canonical_prepared(self, ref):
        """Canonical envelope checks shared by fixed typed dependency readers."""
        payload = {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id), "version_id": str(ref.version_id)}
        prepared = exact_prepared(self.db, self.object_store, self.task_id, payload)
        if prepared.schema_ref != self.catalog.require(prepared.object_type, category="object").schema_ref:
            raise RegistryCorruptError("adoption prefix object has the wrong declared schema")
        row = self.db.execute("SELECT e.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.version_id=?", (str(ref.version_id),)).fetchone()
        self.event(row)
        self._canonical_events(row["transaction_id"], (("object", str(ref.version_id)),))
        return prepared

    def finish_dependencies(self):
        """Validate every explicitly consumed resource-authority dependency.

        The finite resource reader can add schema resources/catalogs. Those
        reads also require the stricter prefix publication/terminal checks.
        No arbitrary JSON reference scan is performed.
        """
        from ..publication import _version_from_payload
        if self._plan_reads is None:
            raise TypeError("basis evidence requires its recording read closure")
        checked = set()
        while True:
            self._plan_reads.finish_resources()
            pending = [(key, record) for key, record in self.object_store.records.items() if key not in checked]
            if not pending:
                return self.object_store.evidence()
            for key, record in pending:
                self.prepared(_version_from_payload(record[2]["ref"]))
                checked.add(key)

    def metadata(self, ref, expected_type=None):
        return self.prepared(ref, expected_type).metadata

    def _checked_event(self, row):
        """Validate a consumed event's declared contract, then persisted structure."""
        try:
            event = self.store._row_to_envelope(row)
            definition = self.catalog.require(event.event_type, category="event", criticality="authoritative")
            if (event.criticality != "authoritative" or event.task_id != self.task_id
                    or event.payload_schema_ref != definition.schema_ref):
                raise RegistryCorruptError("adoption prefix event has the wrong task/criticality/schema")
            envelope = fact_event_envelope(event)
            # The persisted-envelope parser normalizes numbers. Validate original
            # SQLite values too, so normalization cannot hide an invalid fact.
            for field in ("stream_sequence", "aggregate_version", "task_control_sequence", "writer_fencing_epoch"):
                envelope[field] = row[field]
            self.catalog.validate_fact_envelope(envelope)
            self.catalog.validate_event_payload(event.event_type, event.payload)
        except (TypeError, ValueError, SchemaGovernanceError) as exc:
            raise RegistryCorruptError("adoption prefix event violates its declared contract") from exc
        self._raw_structure(row)
        self.store._verified_persisted_event_record(self.db, row)

    def _raw_structure(self, row):
        """Retain the old structural scope without its integer coercions.

        Later adoption semantics remain outside the prefix, but task positions
        and the heads/epochs used by the persisted verifier are current facts.
        """
        def integer(value, minimum):
            if type(value) is not int or value < minimum:
                raise RegistryCorruptError("adoption prefix structure requires raw integral positions/epochs")
        for table in ("transactions", "outbox"):
            authority = self.db.execute(f"SELECT writer_epoch FROM {table} WHERE transaction_id=?",
                (row["transaction_id"],)).fetchone()
            integer(None if authority is None else authority[0], 0)
        for head in self.db.execute("SELECT h.sequence FROM (SELECT DISTINCT stream_id FROM events "
                "WHERE transaction_id=?) e LEFT JOIN stream_heads h ON h.stream_id=e.stream_id",
                (row["transaction_id"],)).fetchall():
            integer(head[0], 1)
        for position in self.db.execute("SELECT task_control_sequence FROM events WHERE task_id=? "
                "AND task_control_sequence IS NOT NULL", (str(self.task_id),)).fetchall():
            integer(position[0], 1)
        head = self.db.execute("SELECT sequence FROM task_control_heads WHERE task_id=?",
            (str(self.task_id),)).fetchone()
        integer(None if head is None else head[0], 0)

    def _canonical_events(self, transaction_id, members):
        """Check terminal contracts along the fixed canonical promotion edges."""
        if not _canonical_closure(self.db, self.task_id, transaction_id=transaction_id, members=members):
            raise RegistryCorruptError("adoption prefix event lacks canonical commit/promotion closure")
        transactions, pending = [transaction_id], list(members)
        checked_transactions, checked_members = set(), set()
        while transactions or pending:
            while transactions:
                transaction = transactions.pop()
                if transaction in checked_transactions:
                    continue
                checked_transactions.add(transaction)
                # Uniqueness/status/identity were checked by _canonical_closure.
                terminal = self.db.execute("SELECT * FROM events WHERE transaction_id=? "
                    "AND event_type='transaction_committed/v1'", (transaction,)).fetchone()
                self._checked_event(terminal)
                counts = {
                    "object_count": self.db.execute("SELECT COUNT(*) FROM objects WHERE transaction_id=?", (transaction,)).fetchone()[0],
                    "relation_count": self.db.execute("SELECT COUNT(*) FROM relations WHERE transaction_id=?", (transaction,)).fetchone()[0],
                    "fact_count": self.db.execute("SELECT COUNT(*) FROM events WHERE transaction_id=? AND event_id<>?",
                        (transaction, terminal["event_id"])).fetchone()[0],
                }
                if canonical_json(json.loads(terminal["payload_json"])) != canonical_json(counts):
                    raise RegistryCorruptError("adoption prefix terminal counts differ from the exact committed batch")
                pending.append(("event", terminal["event_id"]))
            while pending:
                member = pending.pop()
                if member in checked_members:
                    continue
                checked_members.add(member)
                roots = self.db.execute("SELECT p.published_transaction_id FROM firing_temporary_members m "
                    "JOIN firing_publications p ON p.firing_version_id=m.firing_version_id "
                    "WHERE m.member_kind=? AND m.member_identity=?", member).fetchall()
                transactions.extend(row["published_transaction_id"] for row in roots)

    def event(self, row):
        self._checked_event(row)
        self._canonical_events(row["transaction_id"], (("event", row["event_id"]),))

    def document(self, ref):
        prepared = self.prepared(ref, "resource_version/v1")
        if (prepared.metadata["resource_id"] != str(ref.entity_id)
                or prepared.metadata["resource_version_id"] != str(ref.version_id)
                or prepared.metadata["size"] != prepared.size or prepared.metadata["media_type"] != prepared.media_type):
            raise RegistryCorruptError("owner prefix witness resource identity/envelope differs")
        return json.loads(readable_payload(self.object_store, prepared, media_type="application/json"))

    def compiled(self, ref):
        from ..content_schemas import _validate_schema_bytes
        from ...executable_net import _load_compiled_net_offline
        value = self.document(ref)
        if (not isinstance(value, Mapping) or not isinstance(value.get("registrations"), Mapping)
                or not isinstance(value["registrations"].get("schema"), Mapping)):
            raise RegistryCorruptError("owner prefix witness lacks its explicit schema inventory")
        for declaration in value["registrations"]["schema"].values():
            _validate_schema_bytes(canonical_json(declaration["schema"]))
        return _load_compiled_net_offline(value)
