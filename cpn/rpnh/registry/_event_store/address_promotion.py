"""Final address-chain validation in the original firing publication write.

Address preparation consumes ordinary stream sequences but remains private to
its firing.  Its predecessor must therefore be checked again when that entire
root becomes canonical.  This helper reads the caller's existing transaction;
it neither changes an intent nor publishes a second address index.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from ..event_store import RegistryConflict
from ..identities import TypedId


_ADDRESS_EVENTS = ("resource_address_bound/v1", "resource_address_unbound/v1")
_BINDING_TYPE = "resource_address_binding/v1"


class ResourceAddressPromotionConflict(RegistryConflict):
    """A prepared address chain no longer extends its canonical predecessor."""

    def __init__(self, *, firing_version_id, address, expected_binding_ref,
                 actual_binding_ref):
        self.firing_version_id = str(firing_version_id)
        self.address = {"scope_ref": dict(address["scope_ref"]),
                        "opaque_name": address["opaque_name"]}
        self.expected_binding_ref = (None if expected_binding_ref is None
                                     else dict(expected_binding_ref))
        self.actual_binding_ref = (None if actual_binding_ref is None
                                   else dict(actual_binding_ref))
        super().__init__("resource address promotion predecessor changed")


def _document(raw, label):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RegistryConflict(f"address promotion has malformed {label}") from exc
    if not isinstance(value, dict):
        raise RegistryConflict(f"address promotion lacks exact {label}")
    return value


def _reference(value, *, binding=False):
    if (not isinstance(value, Mapping)
            or set(value) != {"entity_type", "logical_id", "version_id"}
            or any(not isinstance(item, str) or not item for item in value.values())):
        raise RegistryConflict("address promotion requires an exact reference")
    try:
        logical = TypedId.parse(value["logical_id"])
        version = TypedId.parse(value["version_id"])
    except (TypeError, ValueError) as exc:
        raise RegistryConflict("address promotion reference is malformed") from exc
    if binding and (value["entity_type"] != _BINDING_TYPE
                    or logical.kind != "resource_address_binding"
                    or version.kind != "resource_address_binding_version"):
        raise RegistryConflict("address promotion predecessor is not a binding")
    return dict(value)


def _binding_event(db, event):
    """Read the immutable binding paired with one real address event."""
    payload = _document(event["payload_json"], "address event")
    ref = _reference(payload.get("binding_ref"), binding=True)
    address = payload.get("address")
    if (not isinstance(address, dict)
            or set(address) != {"scope_ref", "opaque_name"}
            or not isinstance(address["opaque_name"], str)
            or not address["opaque_name"] or "\x00" in address["opaque_name"]):
        raise RegistryConflict("address promotion has an invalid address")
    scope = _reference(address["scope_ref"])
    previous = payload.get("previous_binding_ref")
    if previous is not None:
        previous = _reference(previous, binding=True)
    row = db.execute(
        "SELECT * FROM objects WHERE object_type=? AND version_id=?",
        (_BINDING_TYPE, ref["version_id"])).fetchone()
    if row is None:
        raise RegistryConflict("address promotion event has no immutable binding")
    data = _document(row["metadata_json"], "address binding")
    tombstone = event["event_type"] == "resource_address_unbound/v1"
    resource = None if tombstone else payload.get("resource_ref")
    if not tombstone and (not isinstance(resource, dict)
                         or set(resource) != {"resource_id", "resource_version_id"}):
        raise RegistryConflict("address promotion bind has no exact resource")
    authorization = _reference(data.get("authorization_ref"))
    expected_command = {
        "address": address, "resource_ref": resource,
        "authorization_ref": authorization,
        "expected_binding_ref": previous, "tombstone": tombstone,
    }
    if (event["event_type"] not in _ADDRESS_EVENTS
            or event["stream_id"] != f"resource-address:{scope['version_id']}:{address['opaque_name']}"
            or event["aggregate_id"] != ref["logical_id"]
            or str(row["logical_id"]) != ref["logical_id"]
            or data.get("binding_id") != ref["logical_id"]
            or data.get("binding_version_id") != ref["version_id"]
            or data.get("scope_ref") != scope
            or data.get("opaque_name") != address["opaque_name"]
            or data.get("previous_binding_ref") != previous
            or data.get("lifecycle_state") != ("tombstoned" if tombstone else "bound")
            or data.get("resource_ref") != resource
            or data.get("resulting_stream_sequence") != event["stream_sequence"]
            or payload.get("resulting_stream_sequence") != event["stream_sequence"]
            or data.get("commit_transaction_id") != str(event["transaction_id"])
            or str(row["transaction_id"]) != str(event["transaction_id"])
            or row["producer_invocation_id"] != event["producer_invocation_id"]
            or data.get("command_material") != expected_command
            or (tombstone and "resource_ref" in payload)):
        raise RegistryConflict("address promotion event differs from its immutable intent")
    return ref, address, previous, authorization, row


def validate_address_promotions(event_store, db, *, task_id, transaction_id,
                                publication_commands: Sequence[Mapping[str, object]]) -> None:
    """Validate every address of every root before its PUBLISHED mutation.

    The authoritative caller is ``publish_batch`` under BEGIN IMMEDIATE, after
    all new objects/events/temporary memberships have been inserted and before
    any firing publication changes state.  Exact transaction replay must have
    already returned.  A read-cut caller may use the same check as a preflight,
    but that result does not authorize a later commit.
    """
    if not db.in_transaction:
        raise RegistryConflict("address promotion requires the caller's Registry transaction")
    if not publication_commands:
        return
    # Reuse the original authority predicate.  No provisional/rejected member
    # becomes a predecessor merely because its address stream advanced.
    from ..event_store import _CANONICAL_EVENT_SQL

    seen_roots, address_owners = set(), {}
    for command in publication_commands:
        firing_id = str(command["firing_version_id"])
        if firing_id in seen_roots:
            raise RegistryConflict("address promotion repeats a firing root")
        seen_roots.add(firing_id)
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                          (firing_id,)).fetchone()
        if (root is None or root["state"] != "PROVISIONAL"
                or str(root["invocation_version_id"]) != str(command["invocation_version_id"])):
            raise RegistryConflict("address promotion requires one exact provisional root")
        invocation = db.execute(
            "SELECT o.*,t.task_id FROM objects o JOIN transactions t ON "
            "t.transaction_id=o.transaction_id WHERE o.object_type='invocation/v1' AND o.version_id=?",
            (root["invocation_version_id"],)).fetchone()
        if (invocation is None or invocation["task_id"] != str(task_id)
                or invocation["logical_id"] != root["invocation_logical_id"]):
            raise RegistryConflict("address promotion invocation differs from its root/task")
        context = _document(invocation["metadata_json"], "invocation")
        allowed_scopes = tuple(context.get(field) for field in (
            "task_ref", "task_round_ref", "net_instance_ref", "invocation_ref", "activation_ref")
            if context.get(field) is not None)
        bindings = {
            str(row["version_id"]): row for row in db.execute(
                "SELECT o.*,m.transaction_id AS member_transaction_id FROM "
                "firing_temporary_members m JOIN objects o ON o.version_id=m.member_identity "
                "WHERE m.firing_version_id=? AND m.member_kind='object' "
                "AND o.object_type='resource_address_binding/v1'", (firing_id,))}
        events = db.execute(
            "SELECT e.*,m.transaction_id AS member_transaction_id,t.status AS transaction_status "
            "FROM firing_temporary_members m JOIN events e ON e.event_id=m.member_identity "
            "JOIN transactions t ON t.transaction_id=e.transaction_id "
            "WHERE m.firing_version_id=? AND m.member_kind='event' "
            "AND e.event_type IN (?,?) ORDER BY e.ordinal",
            (firing_id, *_ADDRESS_EVENTS)).fetchall()
        seen_bindings, heads = set(), {}
        for event in events:
            if (event["task_id"] != str(task_id)
                    or event["producer_invocation_id"] != root["invocation_logical_id"]
                    or event["member_transaction_id"] != event["transaction_id"]
                    or (event["transaction_status"] != "committed" and not (
                        event["transaction_status"] == "prepared"
                        and str(event["transaction_id"]) == str(transaction_id)))):
                raise RegistryConflict("address promotion lacks exact committed candidate ownership")
            ref, address, previous, authorization, binding = _binding_event(db, event)
            member = bindings.get(ref["version_id"])
            if (member is None or ref["version_id"] in seen_bindings
                    or member["member_transaction_id"] != binding["transaction_id"]
                    or address["scope_ref"] not in allowed_scopes
                    or authorization != context.get("operation_binding_ref")
                    or authorization["version_id"] != str(root["operation_binding_version_id"])):
                raise RegistryConflict("address promotion binding is outside its exact firing authority")
            seen_bindings.add(ref["version_id"])
            stream = event["stream_id"]
            if stream not in heads:
                owner = address_owners.setdefault(stream, firing_id)
                if owner != firing_id:
                    raise RegistryConflict("one address promotion spans distinct firing roots")
                current = db.execute(
                    "SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
                    "WHERE e.task_id=? AND e.stream_id=? AND e.event_type IN (?,?) "
                    "AND t.status='committed' AND " + _CANONICAL_EVENT_SQL +
                    " ORDER BY e.ordinal DESC LIMIT 1",
                    (str(task_id), stream, *_ADDRESS_EVENTS)).fetchone()
                actual = None
                if current is not None:
                    actual, current_address, _previous, _authorization, _binding = _binding_event(db, current)
                    if current_address != address:
                        raise RegistryConflict("canonical address head differs from its exact scope/name")
                if previous != actual:
                    raise ResourceAddressPromotionConflict(
                        firing_version_id=firing_id, address=address,
                        expected_binding_ref=previous, actual_binding_ref=actual)
            elif previous != heads[stream]:
                raise RegistryConflict("prepared address intents do not form one exact internal chain")
            heads[stream] = ref
        if seen_bindings != set(bindings):
            raise RegistryConflict("address promotion binding lacks one exact address event")


__all__ = ("ResourceAddressPromotionConflict", "validate_address_promotions")
