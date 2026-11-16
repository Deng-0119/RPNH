"""Firing-scoped ordinary Success token identities, with original authority.

The PN counter and retained references are unchanged.  A failed prewrite has
its own exact firing/predecessor namespace, so it cannot occupy a later
firing's token identity.  A marker is accepted only with its actual original
Success batch and the schema recorded in that Registry's native catalog.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import uuid

from .event_store import RegistryConflict
from .identities import TypedId
from .models import PreparedObject, VersionRef
from .schema_catalog import canonical_json
from .normal_root_token_allocation import (
    NORMAL_ROOT_TOKEN_SCHEME, _exact, _object_ref, _ref,
    _load_original_token_schema, _require_normal_root_schema_rule,
    normal_root_allocation_scheme,
)


SUCCESS_FIRING_TOKEN_SCHEME = "success_firing_scoped/v1"
_MARKER = "ordinary_token_ref_scheme"
_RULE = {"type": "string", "enum": [NORMAL_ROOT_TOKEN_SCHEME, SUCCESS_FIRING_TOKEN_SCHEME]}


def _fail(message):
    raise RegistryConflict("Success token allocation: " + message)


def success_firing_token_ref(net_ref, firing_ref, predecessor_ref, token_id):
    """Allocate a pure reference; this function grants no publication right."""
    if (any(not isinstance(ref, VersionRef) for ref in (net_ref, firing_ref, predecessor_ref))
            or type(token_id) is not int or token_id < 0):
        _fail("exact net/firing/predecessor and nonnegative counter are required")
    material = canonical_json({
        "net_ref": _exact(_ref(net_ref), "net_instance/v1"),
        "firing_ref": _exact(_ref(firing_ref), "transition_firing/v1"),
        "predecessor_ref": _exact(_ref(predecessor_ref), "marking_checkpoint/v1"),
        "token_id": token_id,
    }).decode("ascii")

    def identity(kind):
        return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL,
            f"rpnh:success-firing-token-allocation:v1:{kind}:{material}").hex)

    return VersionRef("petri_token/v1", identity("petri_token"), identity("petri_token_version"))


def load_success_firing_token_schema(store, db, *, task_id, cut):
    """Return this Registry's declared capability, or explicit legacy absence.

    Absence is recognized only after proving the original persisted catalog.
    Malformed/unknown schema rules never become a fallback to legacy identity.
    Ordinary Success does not acquire a collaboration source-binding duty.
    """
    schema = _load_original_token_schema(store, db, task_id=task_id, cut=cut,
        require_source_binding=False, require_normal_capability=False)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        _fail("original marking schema properties are malformed")
    rule = properties.get(_MARKER)
    if rule is None:
        return None
    _require_normal_root_schema_rule(schema)
    if rule == {"type": "string", "const": NORMAL_ROOT_TOKEN_SCHEME}:
        return None
    if rule != _RULE:
        _fail("original marking schema has an unknown allocation rule")
    return schema


def validate_success_firing_token_delta(schema, delta):
    from jsonschema import Draft7Validator
    _require_normal_root_schema_rule(schema)
    if (schema["properties"].get(_MARKER) != _RULE
            or not isinstance(delta, Mapping)
            or delta.get(_MARKER) != SUCCESS_FIRING_TOKEN_SCHEME
            or delta.get("declared_effects") is not None):
        _fail("ordinary firing allocation lacks its original declared capability")
    try:
        Draft7Validator(schema).validate(dict(delta))
    except Exception as exc:
        _fail("delta violates the original persisted schema: " + str(exc))


def success_token_allocation_scheme(*, objects, task_id, transaction_id,
        firing, invocation, result, delta, completion, checkpoint,
        historical=False):
    """Select from exact live/historical Success objects, never from a flag."""
    if not isinstance(delta, Mapping):
        _fail("settlement delta is malformed")
    if delta.get(_MARKER) != SUCCESS_FIRING_TOKEN_SCHEME:
        return normal_root_allocation_scheme(objects=objects, task_id=task_id,
            transaction_id=transaction_id, firing=firing, invocation=invocation,
            result=result, delta=delta, completion=completion, checkpoint=checkpoint,
            historical=historical)
    if (type(historical) is not bool
            or not all(isinstance(value, Mapping) for value in (firing, invocation, result, completion, checkpoint))):
        _fail("the complete original Success closure is required")
    objects = tuple(objects)
    if (any(not isinstance(item, PreparedObject) for item in objects)
            or any(item.object_type in {"collaboration_root_terminal/v2", "execution_child_seal/v1",
                "net_instance/v1", "team_design_root/v1"} for item in objects)
            or delta.get("declared_effects") is not None):
        _fail("ordinary allocation cannot replace normal-root or structural/effect authority")
    task = TypedId.parse(str(task_id), expected="task")
    TypedId.parse(str(transaction_id), expected="transaction")
    firing_ref = _exact(firing.get("transition_firing_ref"), "transition_firing/v1")
    invocation_ref = _exact(invocation.get("invocation_ref"), "invocation/v1")
    net_ref = _exact(firing.get("net_instance_ref"), "net_instance/v1")
    binding_ref = _exact(firing.get("operation_binding_ref"), "operation_binding/v1")
    task_ref = _exact(invocation.get("task_ref"), "task/v1")
    producer = TypedId.parse(invocation_ref["logical_id"], expected="invocation")

    def member(kind, document, field):
        matches = [item for item in objects if item.object_type == kind]
        reference = _exact(document.get(field), kind)
        if (len(matches) != 1 or matches[0].producer_invocation_id != producer
                or matches[0].schema_ref != "registry_v1/" + kind
                or matches[0].media_type != "application/json"
                or _object_ref(matches[0]) != reference
                or canonical_json(matches[0].metadata) != canonical_json(document)):
            _fail("tagged Success is not one exact original producing batch")
        return reference

    result_ref = member("operation_result/v1", result, "operation_result_ref")
    member("firing_completion/v2", completion, "firing_completion_ref")
    delta_ref = member("marking_delta/v1", delta, "marking_delta_ref")
    checkpoint_ref = member("marking_checkpoint/v1", checkpoint, "marking_checkpoint_ref")
    _exact(checkpoint.get("previous_checkpoint_ref"), "marking_checkpoint/v1")
    tokens = [item for item in objects if item.object_type == "petri_token/v1"]
    if (not isinstance(delta.get("deposited_refs"), list)
            or {canonical_json(ref) for ref in delta["deposited_refs"]}
            != {canonical_json(_object_ref(item)) for item in tokens}
            or any(item.producer_invocation_id != producer
                   or item.schema_ref != "registry_v1/petri_token/v1"
                   or item.media_type != "application/json"
                   or item.metadata.get("petri_token_ref") != _object_ref(item)
                   for item in tokens)):
        _fail("tagged deposit inventory differs from its exact producing batch")
    if (task_ref["logical_id"] != str(task) or firing.get("task_ref") != task_ref
            or invocation.get("own_transition_firing_ref") != firing_ref
            or invocation.get("net_instance_ref") != net_ref
            or invocation.get("operation_binding_ref") != binding_ref
            or invocation.get("admission_marking_checkpoint_ref") != firing.get("admission_marking_checkpoint_ref")
            or result.get("invocation_ref") != invocation_ref or result.get("transition_firing_ref") != firing_ref
            or result.get("business_outcome") != "completed"
            or not isinstance(result.get("selected_outcome_id"), str) or not result["selected_outcome_id"]
            or completion.get("invocation_ref") != invocation_ref or completion.get("transition_firing_ref") != firing_ref
            or completion.get("business_outcome") != "completed"
            or completion.get("operation_result_ref") != result_ref
            or completion.get("marking_delta_ref") != delta_ref
            or completion.get("successor_checkpoint_ref") != checkpoint_ref
            or delta.get("phase") != "settlement" or delta.get("net_instance_ref") != net_ref
            or delta.get("transition_firing_refs") != [firing_ref]
            or delta.get("operation_binding_refs") != [binding_ref]
            or checkpoint.get("net_instance_ref") != net_ref
            or checkpoint.get("transition_firing_refs") != [firing_ref]
            or checkpoint.get("settlement_delta_ref") != delta_ref):
        _fail("tagged tokens are outside their actual firing/Success closure")
    return SUCCESS_FIRING_TOKEN_SCHEME


def validate_success_firing_token_identities(*, firing, invocation, predecessor,
                                            delta, checkpoint, exact, metadata):
    """Bind new references only; retain the original PN/workspace validators.

    This identity gate does not select outcomes, alter counters, authorize a
    resource, or replace ordinary Success semantics.  Its caller has already
    proved the exact producing batch through success_token_allocation_scheme.
    """
    if delta.get(_MARKER) != SUCCESS_FIRING_TOKEN_SCHEME:
        _fail("identity gate requires its exact declared scheme")
    from .publication import _version_from_payload
    before_ref = _exact(predecessor.get("marking_checkpoint_ref"), "marking_checkpoint/v1")
    net = _exact(firing.get("net_instance_ref"), "net_instance/v1")
    firing_value = _exact(firing.get("transition_firing_ref"), "transition_firing/v1")
    lower, upper = predecessor.get("next_token_id"), checkpoint.get("next_token_id")
    if (checkpoint.get("previous_checkpoint_ref") != before_ref
            or predecessor.get("net_instance_ref") != net
            or checkpoint.get("net_instance_ref") != net
            or invocation.get("own_transition_firing_ref") != firing_value
            or invocation.get("net_instance_ref") != net
            or not exact(before_ref, "marking_checkpoint/v1")
            or type(lower) is not int or type(upper) is not int
            or not 0 <= lower <= upper):
        _fail("identity gate differs from the original settlement predecessor/counter")
    net_ref, firing_ref, predecessor_ref = map(_version_from_payload, (net, firing_value, before_ref))
    references = delta.get("deposited_refs")
    if not isinstance(references, list) or len({canonical_json(ref) for ref in references}) != len(references):
        _fail("identity gate requires exact unique deposit references")
    seen_ids = set()
    for reference in references:
        reference = _exact(reference, "petri_token/v1")
        if not exact(reference, "petri_token/v1"):
            _fail("deposited identity is outside the original Success visibility")
        data = metadata(reference["version_id"], "petri_token/v1")
        if not isinstance(data, Mapping):
            _fail("deposited identity has no original token metadata")
        token_id = data.get("token_id")
        if (type(token_id) is not int or not lower <= token_id < upper or token_id in seen_ids
                or data.get("petri_token_ref") != reference or data.get("net_instance_ref") != net
                or success_firing_token_ref(net_ref, firing_ref, predecessor_ref, token_id)
                != _version_from_payload(reference)):
            _fail("token identity differs from exact firing/predecessor/counter")
        seen_ids.add(token_id)


def validate_success_firing_reconstruction(store, db, *, publication, firing,
                                          predecessor, delta, completion, checkpoint):
    """Reclose a new-scheme historical root and its exact deposited identities."""
    from .execution_child_closure import ClosureSnapshot
    if delta.get(_MARKER) != SUCCESS_FIRING_TOKEN_SCHEME:
        return
    task_id = TypedId.parse(firing["task_ref"]["logical_id"], expected="task")
    snapshot = ClosureSnapshot(store, db, task_id)
    rows = db.execute("SELECT * FROM objects WHERE transaction_id=?",
                      (publication["published_transaction_id"],)).fetchall()
    objects = tuple(PreparedObject(row["object_type"], TypedId.parse(row["logical_id"]),
        TypedId.parse(row["version_id"]), row["size"], row["media_type"], row["schema_ref"],
        TypedId.parse(row["producer_invocation_id"]) if row["producer_invocation_id"] else None,
        row["storage_locator"], json.loads(row["metadata_json"])) for row in rows)
    invocation_ref = {"entity_type": "invocation/v1", "logical_id": publication["invocation_logical_id"],
                      "version_id": publication["invocation_version_id"]}
    invocation = snapshot.read(invocation_ref, "invocation/v1")
    result = snapshot.read(completion["operation_result_ref"], "operation_result/v1")
    for document, field in ((firing, "transition_firing_ref"), (predecessor, "marking_checkpoint_ref"),
                            (delta, "marking_delta_ref"), (completion, "firing_completion_ref"),
                            (checkpoint, "marking_checkpoint_ref")):
        if snapshot.read(document[field]) != document:
            _fail("historical token authority differs from original published bytes")
    if (publication["state"] != "PUBLISHED"
            or publication["firing_version_id"] != firing["transition_firing_ref"]["version_id"]
            or publication["net_version_id"] != firing["net_instance_ref"]["version_id"]
            or publication["operation_result_version_id"] != result["operation_result_ref"]["version_id"]
            or publication["marking_checkpoint_version_id"] != checkpoint["marking_checkpoint_ref"]["version_id"]):
        _fail("historical token closure differs from its original publication root")
    success_token_allocation_scheme(objects=objects, task_id=task_id,
        transaction_id=publication["published_transaction_id"], firing=firing, invocation=invocation,
        result=result, delta=delta, completion=completion, checkpoint=checkpoint, historical=True)
    schema = load_success_firing_token_schema(store, db, task_id=task_id, cut=snapshot.cut)
    if schema is None:
        _fail("historical firing uses a scheme absent from its original catalog")
    validate_success_firing_token_delta(schema, delta)
    deposited = {canonical_json(ref) for ref in delta["deposited_refs"]}
    tokens = [item for item in objects if item.object_type == "petri_token/v1"]
    if deposited != {canonical_json(_object_ref(item)) for item in tokens}:
        _fail("historical deposit inventory differs from its Success batch")
    for item in tokens:
        data = snapshot.read(_object_ref(item), "petri_token/v1")
        if (item.producer_invocation_id != TypedId.parse(invocation_ref["logical_id"])
                or canonical_json(item.metadata) != canonical_json(data)):
            _fail("historical token differs from its original producer/bytes")

    def exact(reference, kind):
        snapshot.read(reference, kind)
        return True

    def metadata(version_id, kind):
        row = db.execute("SELECT logical_id FROM objects WHERE version_id=? AND object_type=?",
                         (version_id, kind)).fetchone()
        if row is None:
            return None
        return snapshot.read({"entity_type": kind, "logical_id": row["logical_id"], "version_id": version_id}, kind)

    validate_success_firing_token_identities(firing=firing, invocation=invocation,
        predecessor=predecessor, delta=delta, checkpoint=checkpoint, exact=exact, metadata=metadata)


__all__ = ("SUCCESS_FIRING_TOKEN_SCHEME", "success_firing_token_ref",
    "load_success_firing_token_schema", "validate_success_firing_token_delta",
    "success_token_allocation_scheme", "validate_success_firing_token_identities",
    "validate_success_firing_reconstruction")
