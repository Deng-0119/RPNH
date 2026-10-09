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
    __slots__ = ("store", "catalog", "db", "task_id", "object_store", "_plan_reads", "terminal",
                 "_terminal_prepared", "_terminal_payloads", "_terminal_resources",
                 "_terminal_active_resources", "_terminal_events", "_terminal_transactions",
                 "_owner_mapping_index", "max_objects")

    def __init__(self, store, catalog, db, task_id, *, _plan_reads=None, terminal=False, max_objects=None):
        self.store, self.catalog, self.db, self.task_id = store, catalog, db, task_id
        self.object_store = ObjectStore(store.path.parent / "objects", catalog, read_only=True)
        self._plan_reads = _plan_reads
        if type(terminal) is not bool or (terminal and _plan_reads is not None):
            raise TypeError("terminal adoption reads require their own fixed read mode")
        if max_objects is not None and (not terminal or type(max_objects) is not int or max_objects < 0):
            raise TypeError("terminal object read budget must be a nonnegative integer")
        self.terminal, self.max_objects = terminal, max_objects
        self._terminal_prepared, self._terminal_payloads = {}, {}
        self._terminal_resources, self._terminal_active_resources = set(), set()
        self._terminal_events, self._terminal_transactions = set(), set()
        self._owner_mapping_index = None
        if terminal:
            import sqlite3
            from pathlib import Path
            if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
                raise TypeError("terminal adoption reads require an existing SQLite read cut")
            main = next((row for row in db.execute("PRAGMA database_list") if row["name"] == "main"), None)
            if main is None or not main["file"] or Path(main["file"]).resolve() != store.path.resolve():
                raise RegistryCorruptError("terminal adoption read cut belongs to another Registry")
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
        if self.terminal and ref in self._terminal_prepared:
            return self._terminal_prepared[ref]
        if self.terminal and self.max_objects is not None and len(self._terminal_prepared) >= self.max_objects:
            from ..errors import TerminalReadIncomplete
            raise TerminalReadIncomplete("READ_BUDGET_EXHAUSTED",
                f"object limit={self.max_objects}; consumed={len(self._terminal_prepared)}; pending={ref.entity_type}")
        prepared = self._canonical_prepared(ref)
        if self.terminal:
            self._terminal_prepared[ref] = prepared
            try:
                if ref.entity_type == "resource_version/v1":
                    self._resource(ref, prepared)
                else:
                    if prepared.media_type != "application/json":
                        from ..errors import TerminalReadUnsupported
                        raise TerminalReadUnsupported("NON_JSON_DESCRIPTOR_UNSUPPORTED", ref.entity_type)
                    raw = self._payload(ref, prepared)
                    value = self._json(raw)
                    if raw != canonical_json(dict(prepared.metadata)) or canonical_json(value) != canonical_json(dict(prepared.metadata)):
                        raise RegistryCorruptError("terminal descriptor bytes differ from canonical metadata")
                    self.catalog.validate_instance(prepared.object_type, category="object", instance=value)
            except Exception as exc:
                self._terminal_prepared.pop(ref, None)
                from jsonschema.exceptions import ValidationError, SchemaError
                if isinstance(exc, (KeyError, TypeError, ValueError, SchemaGovernanceError, ValidationError, SchemaError)):
                    raise RegistryCorruptError("terminal witness bytes or schema are invalid") from exc
                raise
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
        from ..event_store import RegistryConflict
        try:
            prepared = exact_prepared(self.db, self.object_store, self.task_id, payload)
        except RegistryConflict as exc:
            if not self.terminal:
                raise
            raise RegistryCorruptError("terminal witness lacks exact canonical publication") from exc
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
        if row is None:
            raise RegistryCorruptError("adoption prefix required event is absent")
        if self.terminal and row["event_id"] in self._terminal_events:
            return
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
        if self.terminal:
            self._terminal_events.add(row["event_id"])

    def _raw_structure(self, row):
        """Retain the old structural scope without its integer coercions.

        Later adoption semantics remain outside the prefix, but task positions
        and the heads/epochs used by the persisted verifier are current facts.
        """
        def integer(value, minimum):
            if type(value) is not int or value < minimum:
                raise RegistryCorruptError("adoption prefix structure requires raw integral positions/epochs")
        if self.terminal:
            integer(row["ordinal"], 1)
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
                if transaction in checked_transactions or (self.terminal and transaction in self._terminal_transactions):
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
                if self.terminal:
                    self._terminal_transactions.add(transaction)
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
        if self.terminal:
            if prepared.media_type != "application/json":
                raise RegistryCorruptError("terminal witness document has the wrong media type")
            return self._json(self._payload(ref, prepared))
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


    @staticmethod
    def _json(payload):
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value:
                    raise RegistryCorruptError("terminal witness has duplicate JSON keys")
                value[key] = item
            return value
        def constant(value):
            raise RegistryCorruptError("terminal witness has nonfinite JSON numbers")
        return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)

    def _payload(self, ref, prepared):
        from ..object_store import ObjectIntegrityError
        from ..errors import TerminalReadIncomplete
        if ref in self._terminal_payloads:
            return self._terminal_payloads[ref]
        try:
            payload = self.object_store.read_registered(prepared)
        except ObjectIntegrityError as exc:
            # ObjectStore wraps OS failures. Classify the actual cause before
            # the historical owner reader's broad malformed-witness wrapper.
            cause = exc.__cause__
            if isinstance(cause, OSError) and not isinstance(cause, FileNotFoundError):
                raise TerminalReadIncomplete("STORAGE_READ_UNAVAILABLE", "canonical payload could not be read") from exc
            raise RegistryCorruptError("terminal witness payload is absent or differs from its envelope") from exc
        except FileNotFoundError as exc:
            raise RegistryCorruptError("terminal witness payload is absent") from exc
        except OSError as exc:
            raise TerminalReadIncomplete("STORAGE_READ_UNAVAILABLE", "canonical payload could not be read") from exc
        self._terminal_payloads[ref] = payload
        return payload

    def _resource(self, ref, prepared):
        """Close only the resource's fixed schema-authority dependency edge."""
        from ..content_schemas import _validate_schema_bytes
        from ..publication import _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE, _version_from_payload
        from .._candidate_plan_reads import _validate_resource_instance
        if ref in self._terminal_resources:
            return
        if ref in self._terminal_active_resources:
            raise RegistryCorruptError("terminal resource schema authority is cyclic")
        self._terminal_active_resources.add(ref)
        try:
            body = prepared.metadata
            raw = self._payload(ref, prepared)
            if (body["resource_id"] != str(ref.entity_id) or body["resource_version_id"] != str(ref.version_id)
                    or body["size"] != prepared.size or body["media_type"] != prepared.media_type
                    or body["task_ref"]["logical_id"] != str(self.task_id)):
                raise RegistryCorruptError("terminal resource exact identity/envelope/task differs")
            schema_id, authority = body["content_schema_ref"], body["content_schema_authority_ref"]
            if schema_id is None:
                if authority is not None:
                    raise RegistryCorruptError("untyped terminal resource has schema authority")
            else:
                if not isinstance(authority, dict):
                    raise RegistryCorruptError("terminal resource lacks exact schema authority")
                if set(authority) == {"resource_id", "resource_version_id"}:
                    schema_ref = _version_from_payload({"entity_type": "resource_version/v1",
                        "logical_id": authority["resource_id"], "version_id": authority["resource_version_id"]})
                    if schema_ref in self._terminal_active_resources:
                        raise RegistryCorruptError("terminal resource schema authority is cyclic")
                    schema_prepared = self.prepared(schema_ref, "resource_version/v1")
                    if schema_prepared.media_type != "application/schema+json":
                        raise RegistryCorruptError("terminal schema authority has the wrong media type")
                    schema_bytes = self._payload(schema_ref, schema_prepared)
                elif set(authority) == {"entity_type", "logical_id", "version_id"} and authority["entity_type"] == "registry_type_catalog/v1":
                    catalog = self.metadata(_version_from_payload(authority), "registry_type_catalog/v1")
                    entry = catalog["schemas"].get(schema_id)
                    if not isinstance(entry, dict) or entry.get("schema_id") != schema_id:
                        raise RegistryCorruptError("terminal schema is absent from exact catalog")
                    schema_bytes = entry["source"].encode("utf-8")
                else:
                    raise RegistryCorruptError("terminal resource schema authority has the wrong exact role")
                actual_id, schema = _validate_schema_bytes(schema_bytes)
                if actual_id != schema_id:
                    raise RegistryCorruptError("terminal resource schema identity differs")
                instance = _content_schema_instance(raw, media_type=prepared.media_type)
                if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
                    _validate_resource_instance(schema, instance)
            if prepared.media_type == "application/schema+json":
                _validate_schema_bytes(raw)
            self._terminal_resources.add(ref)
        finally:
            self._terminal_active_resources.remove(ref)

    def publication(self, ref, *, ordinary=False):
        """Return a checked object's original publication, never a guessed edge."""
        self.prepared(ref)
        row = self.db.execute("SELECT e.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.version_id=?", (str(ref.version_id),)).fetchone()
        self.event(row)
        if ordinary and (row["producer_invocation_id"] is not None or self.db.execute(
                "SELECT 1 FROM firing_temporary_members WHERE (member_kind='object' AND member_identity=?) "
                "OR (member_kind='event' AND member_identity=?) OR (member_kind='transaction' AND member_identity=?)",
                (str(ref.version_id), row["event_id"], row["transaction_id"])).fetchone() is not None):
            raise RegistryCorruptError("owner witness must be ordinary canonical publication")
        return row

    def transaction(self, transaction_id):
        rows = self.db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
            (str(transaction_id),)).fetchall()
        if len(rows) != 1:
            raise RegistryCorruptError("terminal witness transaction has no unique commit")
        self.event(rows[0])
        return rows[0]

    def relation(self, row, *, ordinary=False):
        """Check persisted relation identity and its exact canonical publication."""
        if row is None:
            raise RegistryCorruptError("terminal witness relation is absent")
        event = self.db.execute("SELECT * FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()
        self.event(event)
        expected = {"relation_id": row["relation_id"], "relation_type": row["relation_type"],
            "source": self._json(row["source_json"]), "target": self._json(row["target_json"]),
            "strength": row["strength"], "metadata": self._json(row["metadata_json"])}
        if (event["event_type"] != "relation_published/v1" or event["transaction_id"] != row["transaction_id"]
                or event["stream_id"] != "relation:" + row["relation_id"]
                or event["aggregate_id"] != row["relation_id"] or event["aggregate_type"] != "typed_relation/v1"
                or canonical_json(self._json(event["payload_json"])) != canonical_json(expected)):
            raise RegistryCorruptError("terminal relation differs from its exact publication")
        self._canonical_events(row["transaction_id"], (("relation", row["relation_id"]),))
        if ordinary and (event["producer_invocation_id"] is not None or self.db.execute(
                "SELECT 1 FROM firing_temporary_members WHERE (member_kind='relation' AND member_identity=?) "
                "OR (member_kind='event' AND member_identity=?) OR (member_kind='transaction' AND member_identity=?)",
                (row["relation_id"], event["event_id"], row["transaction_id"])).fetchone() is not None):
            raise RegistryCorruptError("owner mapping relation is not ordinary system-owned authority")
        return expected

    def resource_plan(self, compiled, net_ref, net, root_ref, root, declaration_ref):
        """Gather the original projection's finite dependency roles at this DB cut."""
        from ..publication import _resource_from_payload, _version_from_payload
        from .._module_resource_projection import project_module_resource_plan
        from ..content_schemas import _validate_schema_bytes
        metadata, schemas, creations = {}, {}, {}
        def read(value, expected):
            ref = value if hasattr(value, "version_id") else _version_from_payload(value)
            metadata[ref] = self.metadata(ref, expected)
            return metadata[ref]
        for field, expected in (("node_refs", "node_declaration/v1"), ("output_binding_refs", "output_binding/v1")):
            for value in net[field]:
                read(value, expected)
        binding = net["module_resource_bindings"]
        for pair in binding["owner_resource_inputs"].values():
            ref = _resource_from_payload(pair).as_version_ref()
            body = read(ref, "resource_version/v1")
            schema_ref = _resource_from_payload(body["content_schema_authority_ref"]).as_version_ref()
            prepared = self.prepared(schema_ref, "resource_version/v1")
            read(schema_ref, "resource_version/v1")
            schemas[schema_ref] = _validate_schema_bytes(self._payload(schema_ref, prepared))
        for value in binding["slot_refs"].values():
            slot = read(value, "logical_artifact_slot/v1")
            read(slot["team_design_root_ref"], "team_design_root/v1")
            declaration = _version_from_payload(slot["authored_index_ref"])
            read(declaration, "resource_version/v1")
            creations[declaration] = self.compiled(declaration)
            node = read(slot["producer_node_ref"], "node_declaration/v1")
            output = read(slot["producer_output_binding_ref"], "output_binding/v1")
            read(output["net_ref"], "net_instance/v1")
            operation = read(node["producer_operation_binding_ref"], "operation_binding/v1")
            read(operation["operation_spec_ref"], "operation_spec/v1")
            read(_resource_from_payload(output["content_schema_ref"]).as_version_ref(), "resource_version/v1")
        for value in binding["lease_refs"].values():
            read(value, value["entity_type"])
        return project_module_resource_plan(compiled, net_ref, net, root_ref, root, declaration_ref,
            exact_metadata=metadata, owner_schema_documents=schemas, creation_compiled=creations)

    def owner_input(self, ref, *, task_ref, root, source_ref):
        """Same registered-owner-input rule, with same-cut exact byte readers."""
        from ..publication import _ref_payload, _version_from_payload
        body = self.metadata(ref.as_version_ref(), "resource_version/v1")
        source = _version_from_payload(body["origin"]["primary_ref"])
        self.publication(ref.as_version_ref(), ordinary=True)
        self.publication(source, ordinary=True)
        if (source.entity_type != "bootstrap_command/v1" or source != source_ref
                or body["task_ref"] != task_ref or body["origin_kind"] != "private_system"
                or body["origin"]["kind"] != "private_system"
                or body["producer_ref"] != _ref_payload(source)
                or body["origin"]["secondary_ref"] != _ref_payload(source)
                or body["reference_provenance"]["producer_invocation_ref"] is not None
                or body["reference_provenance"]["operation_binding_ref"] is not None
                or _ref_payload(ref.as_version_ref()) not in root["resource_refs"]):
            raise RegistryCorruptError("owner input lacks exact ordinary source/task/root authority")

    def owner_mapping_indexes(self):
        """Complete canonical inventory; an unfinished scan proves no uniqueness."""
        from ..event_store import _CANONICAL_EVENT_SQL
        if self._owner_mapping_index is None:
            sources, targets = {}, {}
            rows = self.db.execute("SELECT e.* FROM events e WHERE e.task_id=? AND e.event_type='net_adopted/v1' AND "
                + _CANONICAL_EVENT_SQL + " ORDER BY e.ordinal", (str(self.task_id),)).fetchall()
            for row in rows:
                self.event(row)
                payload = self._json(row["payload_json"])
                for mapping in payload.get("token_mappings", ()):
                    for index, field in ((sources, "old_token_ref"), (targets, "new_token_ref")):
                        index.setdefault(canonical_json(mapping[field]), []).append(row["event_id"])
                for mapping in payload.get("owner_input_mappings", ()):
                    targets.setdefault(canonical_json(mapping["new_token_ref"]), []).append(row["event_id"])
            self._owner_mapping_index = sources, targets
        return self._owner_mapping_index
