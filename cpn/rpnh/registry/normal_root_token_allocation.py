"""Normal-root-only token identities and their original Registry authority.

No catalog is installed or upgraded here. Live and historical callers supply
their own SQLite transaction and durable cut; allocation itself is pure.
"""
from __future__ import annotations

import json
import uuid
from typing import Mapping

from .identities import TypedId
from .models import PreparedObject, VersionRef
from .schema_catalog import canonical_json


NORMAL_ROOT_TOKEN_SCHEME = "normal_root_firing_scoped/v1"
_MARKER = "ordinary_token_ref_scheme"
_ROOT = "collaboration_root_terminal/v2"
_SEAL = "execution_child_seal/v1"
_PROFILE = "execution-v1-normal-only"
_DELTA_SCHEMA = "registry_v1/marking_delta/v1"
_QUALIFIED_SCHEMA = "rpnh/collaboration/source_version_ref/v1"
_KINDS = {
    "task/v1": ("task", "task_version"),
    "task_branch/v1": ("task_branch", "task_branch_version"),
    "native_run_identity/v1": ("run", "run_version"),
    "native_genesis_manifest/v1": ("native_genesis", "native_genesis_version"),
    "bootstrap_command/v1": ("bootstrap_command", "bootstrap_command_version"),
    "registry_type_catalog/v1": ("schema", "resource_version"),
    "net_instance/v1": ("net_instance", "net_instance_version"),
    "transition_firing/v1": ("transition_firing", "transition_firing_version"),
    "invocation/v1": ("invocation", "invocation_version"),
    "operation_binding/v1": ("operation_binding", "operation_binding_version"),
    "operation_result/v1": ("operation_result", "operation_result_version"),
    "firing_completion/v2": ("firing_completion", "firing_completion_version"),
    "marking_delta/v1": ("marking_delta", "marking_delta_version"),
    "marking_checkpoint/v1": ("marking_checkpoint", "marking_checkpoint_version"),
    "petri_token/v1": ("petri_token", "petri_token_version"),
    "resource_version/v1": ("resource", "resource_version"),
    "collaboration_source_binding/v1": ("resource", "resource_version"),
    _ROOT: ("resource", "resource_version"),
    _SEAL: ("resource", "resource_version"),
}


def _fail(message: str) -> None:
    from .event_store import RegistryConflict
    raise RegistryConflict("normal root token allocation: " + message)


def _exact(value: object, kind: str) -> dict[str, str]:
    if (not isinstance(value, Mapping)
            or set(value) != {"entity_type", "logical_id", "version_id"}
            or value.get("entity_type") != kind
            or not all(type(part) is str for part in value.values())):
        _fail("an exact typed reference is required")
    try:
        logical_kind, version_kind = _KINDS[kind]
        TypedId.parse(value["logical_id"], expected=logical_kind)
        TypedId.parse(value["version_id"], expected=version_kind)
    except (KeyError, TypeError, ValueError) as exc:
        _fail("reference identity kinds differ: " + str(exc))
    return dict(value)


def _ref(value: VersionRef) -> dict[str, str]:
    return {"entity_type": value.entity_type, "logical_id": str(value.entity_id),
            "version_id": str(value.version_id)}


def _object_ref(value: PreparedObject) -> dict[str, str]:
    return {"entity_type": value.object_type, "logical_id": str(value.logical_id),
            "version_id": str(value.version_id)}


def normal_root_token_ref(net_ref: VersionRef, firing_ref: VersionRef,
                          token_id: int) -> VersionRef:
    """UUID5(URL, fixed domain + identity kind + canonical exact scope).

    The domain is ``rpnh:normal-root-token-allocation:v1``. Each UUID name
    appends its TypedId kind and the canonical JSON object below. Retained
    token refs are handled by the caller and never enter this allocator.
    """
    if (not isinstance(net_ref, VersionRef)
            or not isinstance(firing_ref, VersionRef)
            or not all(isinstance(value, TypedId) for value in (
                net_ref.entity_id, net_ref.version_id,
                firing_ref.entity_id, firing_ref.version_id))
            or type(token_id) is not int or token_id < 0):
        _fail("allocation requires exact net/firing refs and a nonnegative ordinal")
    material = canonical_json({
        "net_ref": _exact(_ref(net_ref), "net_instance/v1"),
        "firing_ref": _exact(_ref(firing_ref), "transition_firing/v1"),
        "token_id": token_id,
    }).decode("ascii")

    def identity(kind):
        name = f"rpnh:normal-root-token-allocation:v1:{kind}:{material}"
        return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, name).hex)

    return VersionRef("petri_token/v1", identity("petri_token"),
                      identity("petri_token_version"))


def normal_root_allocation_scheme(*, objects, task_id, transaction_id,
        firing, invocation, result, delta, completion, checkpoint,
        historical=False) -> str | None:
    """Derive applicability from this actual Success batch, never a producer flag.

    ``historical`` is only for the existing reader's original-transaction
    adapter. It permits an absent field after proving the same normal closure;
    it does not permit malformed fields or an unrelated tagged settlement.
    """
    if not isinstance(delta, Mapping) or type(historical) is not bool:
        _fail("settlement delta or read mode is malformed")
    tagged = _MARKER in delta
    if tagged and (type(delta[_MARKER]) is not str
                   or delta[_MARKER] != NORMAL_ROOT_TOKEN_SCHEME):
        _fail("unknown ordinary token reference scheme")
    objects = tuple(objects)
    if any(not isinstance(item, PreparedObject) for item in objects):
        _fail("allocation selection requires the actual prepared object batch")
    roots = [item for item in objects if item.object_type == _ROOT]
    seals = [item for item in objects if item.object_type == _SEAL]
    if not roots and not seals:
        if tagged:
            _fail("only the original normal root Success may carry the scheme")
        return None
    if len(roots) != 1 or len(seals) != 1:
        _fail("normal allocation requires exactly one root/v2 and child seal")
    if any(item.object_type in {
            "collaboration_root_terminal/v1", "collaboration_result_export/v1",
            "collaboration_acceptance/v1", "collaboration_contribution/v1",
            "net_instance/v1", "team_design_root/v1",
    } for item in objects) or delta.get("declared_effects") is not None:
        _fail("normal allocation cannot share a different action or revision")
    if not all(isinstance(value, Mapping) for value in
               (firing, invocation, result, completion, checkpoint)):
        _fail("normal allocation requires the complete original Success chain")
    try:
        task = TypedId.parse(str(task_id), expected="task")
        transaction = TypedId.parse(str(transaction_id), expected="transaction")
    except (TypeError, ValueError) as exc:
        _fail("normal allocation task/transaction differs: " + str(exc))
    firing_ref = _exact(firing.get("transition_firing_ref"), "transition_firing/v1")
    invocation_ref = _exact(invocation.get("invocation_ref"), "invocation/v1")
    net_ref = _exact(firing.get("net_instance_ref"), "net_instance/v1")
    binding_ref = _exact(firing.get("operation_binding_ref"), "operation_binding/v1")
    task_ref = _exact(invocation.get("task_ref"), "task/v1")
    admission_ref = _exact(firing.get("admission_marking_checkpoint_ref"), "marking_checkpoint/v1")
    producer = TypedId.parse(invocation_ref["logical_id"], expected="invocation")

    def member(kind, document, self_field):
        matches = [item for item in objects if item.object_type == kind]
        if (len(matches) != 1 or not isinstance(document, Mapping)
                or canonical_json(matches[0].metadata) != canonical_json(document)
                or matches[0].producer_invocation_id != producer
                or matches[0].schema_ref != "registry_v1/" + kind
                or matches[0].media_type != "application/json"):
            _fail("normal Success object is not unique in its producing batch")
        reference = _exact(document.get(self_field), kind)
        if reference != _object_ref(matches[0]):
            _fail("normal Success object self-reference differs")
        return reference

    result_ref = member("operation_result/v1", result, "operation_result_ref")
    completion_ref = member("firing_completion/v2", completion, "firing_completion_ref")
    delta_ref = member("marking_delta/v1", delta, "marking_delta_ref")
    checkpoint_ref = member("marking_checkpoint/v1", checkpoint, "marking_checkpoint_ref")
    root, seal = roots[0].metadata, seals[0].metadata
    if not isinstance(root, Mapping) or not isinstance(seal, Mapping):
        _fail("normal root or seal metadata is malformed")
    seal_ref = member(_SEAL, seal, "execution_child_seal_ref")
    body = root.get("body")
    source_id = seal.get("source_id")
    if not isinstance(body, Mapping) or type(source_id) is not str or not source_id:
        _fail("normal root lacks its exact body/source")

    def qualified(reference):
        return {"schema_version": _QUALIFIED_SCHEMA, "source_id": source_id,
                "ref": reference}

    from ..collaboration.worksets import record_ref
    from .execution_child_closure import child_seal_ref
    command = root.get("command_id")
    if type(command) is not str or not command:
        _fail("normal root lacks its original command")
    expected_transaction = TypedId("transaction", uuid.uuid5(
        uuid.NAMESPACE_URL, f"d1-c:transaction:{task}:{command}").hex)
    root_ref = _ref(record_ref(_ROOT, task, command, command))
    if (roots[0].producer_invocation_id != producer
            or roots[0].media_type != "application/json"
            or roots[0].schema_ref != "registry_v1/" + _ROOT
            or _object_ref(roots[0]) != root_ref
            or root.get("schema_version") != "registry_v1/" + _ROOT
            or root.get("record_ref") != qualified(root_ref)
            or root.get("owner_task_ref") != qualified(task_ref)
            or body.get("required_child_seal_ref") != qualified(seal_ref)
            or seal_ref != _ref(child_seal_ref(task, command))
            or seal.get("success_command_id") != command
            or seal.get("success_transaction_id") != str(transaction)
            or transaction != expected_transaction
            or body.get("closure_profile") != _PROFILE
            or seal.get("closure_profile") != _PROFILE
            or task_ref["logical_id"] != str(task)
            or firing.get("task_ref") != task_ref
            or seal.get("task_ref") != task_ref
            or invocation.get("own_transition_firing_ref") != firing_ref
            or invocation.get("net_instance_ref") != net_ref
            or invocation.get("operation_binding_ref") != binding_ref
            or invocation.get("admission_marking_checkpoint_ref") != admission_ref
            or body.get("firing_ref") != firing_ref
            or body.get("invocation_ref") != invocation_ref
            or body.get("completion_ref") != completion_ref
            or body.get("checkpoint_ref") != checkpoint_ref
            or seal.get("parent_business_firing_ref") != firing_ref
            or seal.get("parent_invocation_ref") != invocation_ref
            or seal.get("parent_business_net_ref") != net_ref
            or seal.get("parent_business_checkpoint_ref") != admission_ref
            or seal.get("operation_result_ref") != result_ref
            or seal.get("successor_business_checkpoint_ref") != checkpoint_ref
            or result.get("invocation_ref") != invocation_ref
            or result.get("transition_firing_ref") != firing_ref
            or completion.get("invocation_ref") != invocation_ref
            or completion.get("transition_firing_ref") != firing_ref
            or result.get("business_outcome") != "completed"
            or completion.get("business_outcome") != "completed"
            or completion.get("operation_result_ref") != result_ref
            or completion.get("marking_delta_ref") != delta_ref
            or completion.get("successor_checkpoint_ref") != checkpoint_ref
            or delta.get("phase") != "settlement"
            or delta.get("net_instance_ref") != net_ref
            or delta.get("transition_firing_refs") != [firing_ref]
            or delta.get("operation_binding_refs") != [binding_ref]
            or checkpoint.get("net_instance_ref") != net_ref
            or checkpoint.get("transition_firing_refs") != [firing_ref]
            or checkpoint.get("settlement_delta_ref") != delta_ref):
        _fail("normal root/seal differs from the original Success scope")
    _exact(seal.get("run_ref"), "native_run_identity/v1")
    occurrence = _exact(body.get("occurrence_ref"), "petri_token/v1")
    output = _exact(body.get("output_resource_ref"), "resource_version/v1")
    tokens = [item for item in objects if _object_ref(item) == occurrence]
    if (len(tokens) != 1 or tokens[0].producer_invocation_id != producer
            or tokens[0].metadata.get("petri_token_ref") != occurrence
            or tokens[0].metadata.get("net_instance_ref") != net_ref
            or tokens[0].metadata.get("resource_ref") != {
                "resource_id": output["logical_id"],
                "resource_version_id": output["version_id"]}
            or output not in result.get("output_resource_refs", ())
            or occurrence not in delta.get("deposited_refs", ())
            or occurrence not in checkpoint.get("token_refs", ())):
        _fail("normal root occurrence is outside its original Success bundle")
    if not tagged and not historical:
        _fail("a new normal root must declare its token allocation scheme")
    return NORMAL_ROOT_TOKEN_SCHEME if tagged else None


def _json_document(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _fail("duplicate JSON member in persisted schema authority")
            result[key] = value
        return result

    def nonfinite(value):
        _fail("nonfinite JSON value in persisted schema authority: " + value)

    try:
        document = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (TypeError, ValueError, UnicodeError) as exc:
        _fail("persisted schema authority is not JSON: " + str(exc))
    if not isinstance(document, dict):
        _fail("persisted schema authority must be an object")
    return document


def _normal_root_delta_schema(bundle) -> dict:
    """Recognize only the fixed optional scalar rule in the persisted bundle."""
    from .content_schemas import _validate_schema_bytes, ContentSchemaAuthorityError
    definitions = bundle.get("definitions")
    index = bundle.get("schema_index")
    schemas = bundle.get("schemas")
    if (bundle.get("catalog_identity") != "rpnh-v1"
            or not isinstance(definitions, list) or not isinstance(index, list)
            or not isinstance(schemas, dict)):
        _fail("persisted catalog shape differs")
    definitions = [item for item in definitions if isinstance(item, dict)
                   and item.get("name") == "marking_delta/v1"]
    expected_definition = {
        "name": "marking_delta/v1", "category": "object", "owner": "registry",
        "schema_ref": _DELTA_SCHEMA, "criticality": None,
        "permission": "task-scoped", "retention": "run-fact",
        "recovery_rule": "registry-reference-replay",
        "integrity_rule": "exact-schema-and-reference-validation",
    }
    expected_index = {"schema_id": _DELTA_SCHEMA,
                      "path": "registry_v1/marking_delta.v1.schema.json"}
    indices = [item for item in index if isinstance(item, dict)
               and item.get("schema_id") == _DELTA_SCHEMA]
    entry = schemas.get(_DELTA_SCHEMA)
    if (definitions != [expected_definition] or indices != [expected_index]
            or not isinstance(entry, dict)
            or set(entry) != {"schema_id", "path", "source"}
            or {key: entry[key] for key in expected_index} != expected_index
            or type(entry.get("source")) is not str):
        _fail("persisted marking-delta definition/index/source differs")
    document = _json_document(entry["source"])
    try:
        schema_id, checked = _validate_schema_bytes(entry["source"].encode("utf-8"))
    except (ContentSchemaAuthorityError, UnicodeError) as exc:
        _fail("persisted marking-delta schema is not self-contained Draft7: " + str(exc))
    if schema_id != _DELTA_SCHEMA or checked != document:
        _fail("persisted marking-delta schema identity differs")
    _require_normal_root_schema_rule(document)
    return document


def _require_normal_root_schema_rule(document) -> None:
    """A finite structural contract, not a search for a field name in text."""
    rule = {"if": {"required": [_MARKER]}, "then": {"properties": {
        "phase": {"const": "settlement"},
        "transition_firing_refs": {"minItems": 1, "maxItems": 1},
    }}}
    allowed = {"$id", "$schema", "title", "description", "type",
               "additionalProperties", "definitions", "properties", "required", "allOf"}
    if (not isinstance(document, dict)
            or document.get("$id") != _DELTA_SCHEMA
            or document.get("$schema") != "http://json-schema.org/draft-07/schema#"
            or set(document) - allowed or document.get("type") != "object"
            or document.get("additionalProperties") is not False
            or not isinstance(document.get("properties"), dict)
            or document["properties"].get(_MARKER) != {
                "type": "string", "const": NORMAL_ROOT_TOKEN_SCHEME}
            or not isinstance(document.get("required"), list)
            or _MARKER in document["required"]
            or document.get("allOf") != [rule]):
        _fail("original persisted catalog lacks the normal-root allocation capability")


def load_normal_root_token_schema(store, db, *, task_id, cut: int) -> dict:
    """Read fixed genesis/catalog authority only through this existing db/cut."""
    from .execution_child_closure import ClosureSnapshot
    from ._event_store.source_identity import read_source_binding

    if not db.in_transaction or type(cut) is not int or cut < 0:
        _fail("catalog capability requires the caller's transaction and exact cut")
    task_id = TypedId.parse(str(task_id), expected="task")
    snapshot = ClosureSnapshot(store, db, task_id, cut=cut)
    checked_transactions = {}

    def meta(key):
        row = db.execute("SELECT value FROM registry_meta WHERE key=?", (key,)).fetchone()
        if row is None:
            _fail("native catalog locator is absent: " + key)
        return row["value"]

    if meta("task_id") != str(task_id):
        _fail("catalog capability belongs to another task")
    branch_id = meta("branch_id")

    def absent_member(kind, identity):
        if db.execute("SELECT 1 FROM firing_temporary_members WHERE member_kind=? "
                      "AND member_identity=?", (kind, identity)).fetchone() is not None:
            _fail("original catalog/owner authority cannot be a firing temporary member")

    def transaction(transaction_id):
        if transaction_id in checked_transactions:
            return checked_transactions[transaction_id]
        row = db.execute("SELECT * FROM transactions WHERE transaction_id=?",
                         (transaction_id,)).fetchone()
        events = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
                            (transaction_id,)).fetchall()
        outbox = db.execute("SELECT * FROM outbox WHERE transaction_id=?",
                            (transaction_id,)).fetchone()
        if (row is None or row["status"] != "committed" or row["task_id"] != str(task_id)
                or not events or events[-1]["event_type"] != "transaction_committed/v1"
                or sum(e["event_type"] == "transaction_committed/v1" for e in events) != 1
                or any(e["event_type"] == "transaction_aborted/v1" for e in events)
                or events[-1]["ordinal"] > cut
                or [e["ordinal"] for e in events] != list(range(events[0]["ordinal"], events[-1]["ordinal"] + 1))
                or outbox is None or outbox["task_id"] != str(task_id)
                or outbox["writer_epoch"] != row["writer_epoch"]
                or json.loads(outbox["event_ids_json"]) != [e["event_id"] for e in events]):
            _fail("catalog/owner transaction lacks its original committed cut/outbox")
        absent_member("transaction", transaction_id)
        for event in events:
            absent_member("event", event["event_id"])
            snapshot.event(event)
            if (event["producer_invocation_id"] is not None
                    or event["producer_principal"] != "framework"
                    or event["branch_id"] != branch_id
                    or event["task_round_id"] is not None or event["net_instance_id"] is not None
                    or event["writer_fencing_epoch"] != row["writer_epoch"]
                    or event["idempotency_key"] != row["idempotency_key"]
                    or event["command_id"] != row["idempotency_key"]
                    or event["event_schema_version"] != event["event_type"].rsplit("/", 1)[-1]
                    or event["payload_schema_ref"] != "registry_v1/" + event["event_type"]):
                _fail("catalog/owner transaction envelope differs")
        objects = db.execute("SELECT * FROM objects WHERE transaction_id=?", (transaction_id,)).fetchall()
        publications = [event for event in events if event["event_type"] == "object_version_published/v1"]
        if (len(publications) != len(objects)
                or {e["event_id"] for e in publications} != {o["published_event_id"] for o in objects}):
            _fail("catalog/owner object publication inventory differs")
        for item in objects:
            absent_member("object", item["version_id"])
        relation_count = db.execute("SELECT COUNT(*) FROM relations WHERE transaction_id=?",
                                    (transaction_id,)).fetchone()[0]
        if json.loads(events[-1]["payload_json"]) != {
                "object_count": len(objects), "relation_count": relation_count,
                "fact_count": len(events) - 1}:
            _fail("catalog/owner transaction terminal counts differ")
        checked_transactions[transaction_id] = (row, events)
        return row, events

    def read(reference, kind):
        reference = _exact(reference, kind)
        raw = snapshot.read(reference, kind, descriptor=False)
        row = snapshot.rows[reference["version_id"]]
        body = _json_document(raw)
        if (row["producer_invocation_id"] is not None
                or row["media_type"] != "application/json"
                or row["schema_ref"] != "registry_v1/" + kind
                or raw != canonical_json(body)
                or canonical_json(body) != canonical_json(_json_document(row["metadata_json"]))):
            _fail("catalog/owner bytes differ from their canonical descriptor")
        absent_member("object", reference["version_id"])
        tx, events = transaction(row["transaction_id"])
        return body, row, tx, events

    def locator(key, kind):
        return _exact(_json_document(meta(key)), kind)

    binding_row = read_source_binding(db, store.catalog, task_id)
    if binding_row is None:
        _fail("normal allocation requires the original local source binding")
    binding_metadata = _json_document(binding_row["binding_metadata_json"])
    binding, _binding_row, _binding_tx, binding_events = read(
        binding_metadata.get("binding_ref"), "collaboration_source_binding/v1")
    if binding != binding_metadata:
        _fail("source binding bytes differ from the registered local source")
    task_ref = locator("task_ref", "task/v1")
    run_ref = locator("native_run_ref", "native_run_identity/v1")
    bootstrap_ref = locator("bootstrap_command_ref", "bootstrap_command/v1")
    genesis_ref = locator("native_genesis_ref", "native_genesis_manifest/v1")
    branch_ref = locator("task_branch_ref", "task_branch/v1")
    if (binding.get("task_ref") != task_ref or binding.get("native_run_ref") != run_ref
            or binding.get("bootstrap_command_ref") != bootstrap_ref):
        _fail("source binding differs from the fixed native owner locators")
    task, _task_row, _task_tx, _task_events = read(task_ref, "task/v1")
    branch, _branch_row, _branch_tx, _branch_events = read(branch_ref, "task_branch/v1")
    run, run_row, _run_tx, _run_events = read(run_ref, "native_run_identity/v1")
    bootstrap, bootstrap_row, _bootstrap_tx, _bootstrap_events = read(bootstrap_ref, "bootstrap_command/v1")
    genesis, genesis_row, genesis_tx, genesis_events = read(genesis_ref, "native_genesis_manifest/v1")
    if (task.get("task_id") != str(task_id) or task_ref["logical_id"] != str(task_id)
            or task.get("task_version_id") != task_ref["version_id"]
            or branch.get("task_branch_id") != branch_ref["logical_id"]
            or branch.get("task_branch_version_id") != branch_ref["version_id"]
            or branch.get("task_ref") != task_ref or branch.get("branch_name") != branch_id
            or run.get("run_id") != run_ref["logical_id"] or run.get("run_version_id") != run_ref["version_id"]
            or run.get("task_ref") != task_ref or run.get("task_branch_ref") != branch_ref
            or run.get("branch_id") != branch_id
            or bootstrap.get("bootstrap_command_id") != bootstrap_ref["logical_id"]
            or bootstrap.get("bootstrap_command_version_id") != bootstrap_ref["version_id"]
            or genesis.get("genesis_id") != genesis_ref["logical_id"]
            or genesis.get("genesis_version_id") != genesis_ref["version_id"]
            or genesis.get("format") != "d1-c-greenfield/v1"
            or genesis.get("run_identity_ref") != run_ref
            or genesis.get("bootstrap_transaction_id") != genesis_row["transaction_id"]
            or run_row["transaction_id"] != genesis_row["transaction_id"]
            or bootstrap_row["transaction_id"] != genesis_row["transaction_id"]
            or genesis_tx["idempotency_key"] != "native-bootstrap:" + run_ref["logical_id"]
            or genesis_events[-1]["ordinal"] >= binding_events[0]["ordinal"]):
        _fail("fixed genesis/source/run/bootstrap identity or transaction differs")
    catalog_ref = _exact(genesis.get("type_catalog_ref"), "registry_type_catalog/v1")
    if (catalog_ref["logical_id"] != meta("type_catalog_logical_id")
            or catalog_ref["version_id"] != meta("type_catalog_version_id")):
        _fail("catalog metadata locator differs from the immutable native genesis")
    bundle, _catalog_row, catalog_tx, catalog_events = read(catalog_ref, "registry_type_catalog/v1")
    if (catalog_tx["idempotency_key"] != "registry-type-catalog:rpnh-v1"
            or catalog_events[-1]["ordinal"] >= genesis_events[0]["ordinal"]):
        _fail("catalog was not the original pre-bootstrap publication")
    return _normal_root_delta_schema(bundle)


def validate_normal_root_token_delta(schema, delta) -> None:
    """Validate the actual tagged settlement using its persisted schema source."""
    from jsonschema import Draft7Validator
    _require_normal_root_schema_rule(schema)
    if (not isinstance(delta, Mapping) or _MARKER not in delta
            or type(delta[_MARKER]) is not str
            or delta[_MARKER] != NORMAL_ROOT_TOKEN_SCHEME):
        _fail("new normal delta lacks its exact allocation scheme")
    try:
        Draft7Validator(schema).validate(dict(delta))
    except Exception as exc:
        _fail("normal delta violates the original persisted schema: " + str(exc))
