"""Fixed read-only dependency closure for the first static candidate plan.

This slice accepts default operation Modules and three fixed business-resource
selection roles. It records actual bytes read through the existing controlled store;
it neither validates prospective graph objects nor creates execution handles.
"""
from __future__ import annotations

import hashlib
import json

from jsonschema import Draft7Validator
from referencing import Registry

from ._event_store.collaboration_descriptors import exact_prepared, readable_descriptor, readable_payload
from ._event_store.source_identity import _canonical_commit_event, _exact_static_authority, read_source_binding
from .content_schemas import _validate_schema_bytes
from .event_store import RegistryConflict
from .publication import _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE
from .schema_catalog import canonical_json


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def _validate_resource_instance(schema, instance):
    # jsonschema's default registry still has a remote-retrieval callback.
    # Use its fixed no-retrieval registry, preserving local/recursive scopes.
    # This never modifies the general Core validator or accepts app callbacks.
    Draft7Validator(schema, registry=Registry()).validate(instance)


class RecordingStore:
    """A finite read-only store adapter; no locator supplied by an application."""
    def __init__(self, store):
        self.catalog = store.catalog
        self._store = store
        self.records = {}

    def read_registered(self, prepared):
        payload = self._store.read_registered(prepared)
        ref = {"entity_type": prepared.object_type, "logical_id": str(prepared.logical_id),
            "version_id": str(prepared.version_id)}
        evidence = {"ref": ref, "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload), "media_type": prepared.media_type}
        key = canonical_json(ref)
        if key in self.records:
            previous, prior_payload, prior_evidence = self.records[key]
            if (not same(previous.metadata, prepared.metadata) or previous.schema_ref != prepared.schema_ref
                    or prior_payload != payload or prior_evidence != evidence):
                raise RegistryConflict("candidate dependency changed within one read cut")
        self.records[key] = prepared, payload, evidence
        return payload

    def evidence(self):
        return [self.records[key][2] for key in sorted(self.records)]


class PlanReadClosure:
    """One supplied SQLite cut; fixed owner/source/resource rules only."""
    def __init__(self, db, core):
        self.db, self.catalog, self.task_id, self.branch_id = db, core.catalog, core.task_id, core.branch_id
        self.store = RecordingStore(core.object_store)
        row = read_source_binding(db, self.catalog, self.task_id)
        if row is None:
            raise RegistryConflict("candidate plan requires its bound local source")
        self.binding = json.loads(row["binding_metadata_json"])
        self.descriptor(self.binding["binding_ref"], static=True)
        self._resources_done, self._resources_active = set(), set()
        self._resource_schemas = {}

    def descriptor(self, ref, *, static=False):
        prepared = exact_prepared(self.db, self.store, self.task_id, ref)
        document = readable_descriptor(self.store, prepared)
        payload = self.store.read_registered(prepared)
        if payload != canonical_json(document):
            raise RegistryConflict("candidate static descriptor requires canonical JSON bytes")
        if static:
            row = self.db.execute("SELECT published_event_id,transaction_id FROM objects WHERE version_id=?",
                (ref["version_id"],)).fetchone()
            if (prepared.producer_invocation_id is not None
                    or _canonical_commit_event(self.db, row["transaction_id"], self.task_id) is None
                    or self.db.execute("SELECT 1 FROM firing_temporary_members WHERE "
                        "(member_kind='object' AND member_identity=?) OR (member_kind='event' AND member_identity=?)",
                        (ref["version_id"], row["published_event_id"])).fetchone() is not None):
                raise RegistryConflict("candidate owner authority must be static canonical data")
        return document

    def meta_ref(self, key):
        row = self.db.execute("SELECT value FROM registry_meta WHERE key=?", (key,)).fetchone()
        if row is None:
            raise RegistryConflict("candidate owner registration is incomplete")
        return json.loads(row[0])

    def owner(self, plan):
        identity = plan["run_identity"]
        pinned = {"task_ref": identity["task_ref"], "native_run_ref": identity["run_ref"],
            "native_genesis_ref": identity["genesis_manifest_ref"], "task_branch_ref": identity["task_branch_ref"],
            "bootstrap_command_ref": plan["bootstrap_ref"]}
        if any(not same(ref, self.meta_ref(key)) for key, ref in pinned.items()):
            raise RegistryConflict("candidate plan differs from this Registry's exact native owner")
        if (plan["source_id"] != self.binding["source_id"]
                or not same(plan["owner_task_ref"], identity["task_ref"])
                or not same(identity["run_ref"], self.binding["native_run_ref"])
                or not same(identity["task_ref"], self.binding["task_ref"])
                or not same(plan["bootstrap_ref"], self.binding["bootstrap_command_ref"])
                or plan["producer_principal_ref"]["source_id"] != self.binding["source_id"]
                or not same(plan["producer_principal_ref"]["ref"], plan["principal_ref"])):
            raise RegistryConflict("candidate plan owner/source/producer differs")
        values = {}
        refs = (("task", identity["task_ref"], "task"), ("run", identity["run_ref"], "run"),
            ("branch", identity["task_branch_ref"], "task_branch"),
            ("genesis", identity["genesis_manifest_ref"], "genesis"),
            ("bootstrap", plan["bootstrap_ref"], "bootstrap_command"),
            ("principal", plan["principal_ref"], "principal"),
            ("decision", plan["authority_decision_ref"], "decision"),
            ("round", plan["task_round_ref"], "task_round"))
        expected_types = {"task": "task/v1", "run": "native_run_identity/v1", "branch": "task_branch/v1",
            "genesis": "native_genesis_manifest/v1", "bootstrap": "bootstrap_command/v1", "principal": "principal/v1",
            "decision": "user_authority_decision/v1", "round": "task_round/v1"}
        for name, ref, prefix in refs:
            if ref["entity_type"] != expected_types[name]:
                raise RegistryConflict("candidate owner authority has the wrong declared type")
            if ref["entity_type"] in {"task/v1", "native_run_identity/v1", "bootstrap_command/v1"}:
                _exact_static_authority(self.db, self.catalog, ref, expected_type=ref["entity_type"], task_id=self.task_id)
            body = self.descriptor(ref, static=True)
            if (body.get(prefix + "_id") != ref["logical_id"]
                    or (name != "round" and body.get(prefix + "_version_id") != ref["version_id"])):
                raise RegistryConflict("candidate owner authority self identity differs")
            values[name] = body
        task, run, branch, genesis, decision, round_data = (values[key] for key in
            ("task", "run", "branch", "genesis", "decision", "round"))
        if (task["task_id"] != str(self.task_id) or identity["branch_id"] != self.branch_id
                or run["branch_id"] != self.branch_id or branch["branch_name"] != self.branch_id
                or not same(run["task_ref"], identity["task_ref"])
                or not same(branch["task_ref"], identity["task_ref"])
                or not same(run["task_branch_ref"], identity["task_branch_ref"])
                or not same(run["protocol_versions"], identity["protocol_versions"])
                or not same(genesis["run_identity_ref"], identity["run_ref"])
                or round_data["task_id"] != str(self.task_id)
                or not same(round_data.get("task_branch_ref"), identity["task_branch_ref"])
                or decision["status"] != "effective"
                or not same(decision["decision_ref"], plan["authority_decision_ref"])
                or not same(decision["user_principal_ref"], plan["principal_ref"])
                or not any(same(ref, identity["task_ref"]) for ref in decision["governed_artifact_refs"])):
            raise RegistryConflict("candidate native owner/run/round links differ")
        transactions = [self.db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (ref["version_id"],)).fetchone()[0]
            for ref in (identity["run_ref"], identity["genesis_manifest_ref"], plan["bootstrap_ref"])]
        if len(set(transactions)) != 1 or genesis["bootstrap_transaction_id"] != transactions[0]:
            raise RegistryConflict("candidate genesis differs from its original bootstrap transaction")
        self.descriptor(genesis["type_catalog_ref"], static=True)

    def resource(self, ref):
        key = canonical_json(ref)
        if key in self._resources_active:
            raise RegistryConflict("candidate schema dependency contains a cycle")
        if key in self._resources_done:
            return self.store.records[key][1]
        self._resources_active.add(key)
        try:
            prepared = exact_prepared(self.db, self.store, self.task_id, ref)
            if prepared.object_type != "resource_version/v1":
                raise RegistryConflict("candidate schema authority requires an exact resource")
            body = prepared.metadata
            data = readable_payload(self.store, prepared, media_type=prepared.media_type)
            if (body["resource_id"] != ref["logical_id"] or body["resource_version_id"] != ref["version_id"]
                    or body["size"] != prepared.size or body["media_type"] != prepared.media_type
                    or not same(body["task_ref"], self.binding["task_ref"])):
                raise RegistryConflict("candidate resource identity/envelope/owner differs")
            # Branch/round/net requirements belong to the consuming role.
            # Pure author reads separately prove exact bootstrap provenance;
            # Module business inputs deliberately permit historical scopes.
            schema_id, authority = body["content_schema_ref"], body["content_schema_authority_ref"]
            schema = None
            if schema_id is None:
                if authority is not None:
                    raise RegistryConflict("untyped candidate resource has schema authority")
            else:
                if not isinstance(authority, dict):
                    raise RegistryConflict("candidate resource lacks exact schema authority")
                if set(authority) == {"resource_id", "resource_version_id"}:
                    schema_ref = {"entity_type": "resource_version/v1", "logical_id": authority["resource_id"],
                        "version_id": authority["resource_version_id"]}
                    schema_bytes = self.resource(schema_ref)
                    schema_prepared = self.store.records[canonical_json(schema_ref)][0]
                    if schema_prepared.media_type != "application/schema+json":
                        raise RegistryConflict("candidate schema resource has wrong media")
                elif set(authority) == {"entity_type", "logical_id", "version_id"} and authority["entity_type"] == "registry_type_catalog/v1":
                    catalog = self.descriptor(authority)
                    entry = catalog["schemas"].get(schema_id)
                    if not isinstance(entry, dict) or entry.get("schema_id") != schema_id:
                        raise RegistryConflict("candidate schema is absent from exact catalog")
                    schema_bytes = entry["source"].encode("utf-8")
                else:
                    raise RegistryConflict("unsupported exact candidate schema authority")
                actual_id, schema = _validate_schema_bytes(schema_bytes)
                if actual_id != schema_id:
                    raise RegistryConflict("candidate content schema identity differs")
                instance = _content_schema_instance(data, media_type=prepared.media_type)
                if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
                    _validate_resource_instance(schema, instance)
            if prepared.media_type == "application/schema+json":
                _validate_schema_bytes(data)
            self._resource_schemas[key] = schema
            self._resources_done.add(key)
            return data
        finally:
            self._resources_active.remove(key)

    def business_resource(self, pair, *, schema_id, schema_pair, private_system=False):
        """Original Module input role: exact task/schema, no new scope policy."""
        ref = {"entity_type": "resource_version/v1", "logical_id": pair["resource_id"],
            "version_id": pair["resource_version_id"]}
        data = self.resource(ref)
        key = canonical_json(ref)
        body = self.store.records[key][0].metadata
        if body["content_schema_ref"] != schema_id or (private_system and body["origin_kind"] != "private_system"):
            raise RegistryConflict("candidate business input differs from its selected role/schema")
        selected = {"entity_type": "resource_version/v1", "logical_id": schema_pair["resource_id"],
            "version_id": schema_pair["resource_version_id"]}
        selected_bytes = self.resource(selected)
        if self.store.records[canonical_json(selected)][0].media_type != "application/schema+json":
            raise RegistryConflict("candidate input schema document has wrong media")
        actual_id, selected_schema = _validate_schema_bytes(selected_bytes)
        authority = body["content_schema_authority_ref"]
        if (actual_id != schema_id or not same(self._resource_schemas[key], selected_schema)
                or (set(authority) == {"resource_id", "resource_version_id"} and not same(authority, schema_pair))):
            raise RegistryConflict("candidate business input schema authority differs from the exact author ABI")
        return data

    def business_inputs(self, plan, compiled):
        """The three finite input axes, sharing the actual evidence traversal."""
        schemas = plan["schema_refs"]
        ports = {port.name: port for port in compiled.ports}
        owners = plan["owner_resource_inputs"]
        leases = {lease.name: lease for lease in compiled.symbolic.lease_identities}
        from .module_resources import _validate_owner_resource_inputs
        from .publication import _resource_from_payload
        try:
            _validate_owner_resource_inputs(compiled.symbolic,
                {name: _resource_from_payload(pair) for name, pair in owners.items()})
        except (TypeError, ValueError) as exc:
            raise RegistryConflict(str(exc)) from exc
        def schema_of(pair):
            ref = {"entity_type": "resource_version/v1", "logical_id": pair["resource_id"],
                "version_id": pair["resource_version_id"]}
            self.resource(ref)
            schema_id = self.store.records[canonical_json(ref)][0].metadata["content_schema_ref"]
            if schema_id not in schemas:
                raise RegistryConflict("candidate business input schema is outside the exact declared ABI")
            return schema_id
        for pair in owners.values():
            schema_id = schema_of(pair)
            self.business_resource(pair, schema_id=schema_id, schema_pair=schemas[schema_id])
        for pair in plan["owner_input_resources"]:
            schema_id = schema_of(pair)
            self.business_resource(pair, schema_id=schema_id, schema_pair=schemas[schema_id], private_system=True)
        slots = {slot.name: slot for slot in compiled.symbolic.logical_slots}
        if any(slot.schema not in schemas for slot in slots.values()):
            raise RegistryConflict("candidate logical slot schema is outside the exact declared ABI")
        for arc in compiled.symbolic.variable_resource_arcs:
            for claim in arc.initial_claims:
                lease = leases[claim.lease_identity]
                if lease.kind == "slot" and claim.expected_resource is not None:
                    if schema_of(owners[claim.expected_resource]) != slots[lease.slot].schema:
                        raise RegistryConflict("candidate slot expected resource differs from exact declared schema")
        entries = plan["entry_inputs"]
        if not set(entries) <= set(compiled.symbolic.entry):
            raise RegistryConflict("candidate entry inputs contain unknown symbolic entry keys")
        for name, bundle in entries.items():
            port = ports[compiled.symbolic.entry[name]]
            if not port.minimum <= len(bundle) <= port.maximum:
                raise RegistryConflict("candidate entry input quantity differs from the declared port")
            for pair in bundle:
                self.business_resource(pair, schema_id=port.schema, schema_pair=schemas[port.schema])

    def finish_resources(self):
        """Follow only schema-authority edges of resources actually consumed."""
        while True:
            pending = [record[2]["ref"] for key, record in self.store.records.items()
                if record[0].object_type == "resource_version/v1" and key not in self._resources_done]
            if not pending:
                return self.store.evidence()
            for ref in pending:
                self.resource(ref)
