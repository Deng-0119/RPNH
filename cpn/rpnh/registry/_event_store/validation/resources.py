"""Resource validation over an already-open EventStore transaction."""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from typing import Any, Mapping, Sequence

from ... import event_store as facade
from ...identities import TypedId
from ...models import EventEnvelope, PendingEvent, PreparedObject
from ...schema_catalog import canonical_json
from ..accounting import _CANONICAL_EVENT_SQL
from . import agent_loop as agent_loop_validation
from . import provider as provider_validation


def validate_waiting_resource_grant_atomicity(
        db: sqlite3.Connection, objects: Sequence[PreparedObject],
        events: Sequence[PendingEvent], *, writer_epoch: int) -> bool:
    """Validate the extension-only transaction that grants one waiter."""
    from ...event_store import RegistryConflict

    extensions = tuple(
        event for event in events
        if event.event_type == "live_firing_resource_extension/v1")
    agent_objects = tuple(item for item in objects if item.object_type in {
        "agent_loop/v1", "agent_turn/v1", "agent_action/v2",
        "agent_tool_error/v1", "agent_context_compaction/v1",
        "agent_context_compaction/v2", "agent_context_compaction/v3",
        "firing_external_kb_read/v2",
    })
    if not extensions or agent_objects:
        return False
    lifecycles = tuple(
        item for item in objects
        if item.object_type == "resource_access_lifecycle/v1")
    if len(extensions) != 1 or len(lifecycles) != 1:
        raise RegistryConflict(
            "waiting resource grant requires one lifecycle and extension")
    event = extensions[0]
    lifecycle = lifecycles[0]
    value = dict(lifecycle.metadata)
    payload = dict(event.payload)
    prior_row = db.execute(
        "SELECT metadata_json FROM objects WHERE object_type="
        "'resource_access_lifecycle/v1' AND logical_id=? "
        "ORDER BY rowid DESC LIMIT 1",
        (str(lifecycle.logical_id),),
    ).fetchone()
    prior = (json.loads(str(prior_row["metadata_json"]))
             if prior_row is not None else {})
    action_ref = payload.get("agent_action_ref")
    action_row = (db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE object_type="
        "'agent_action/v2' AND version_id=?",
        (str(action_ref.get("version_id", "")),),
    ).fetchone() if isinstance(action_ref, Mapping) else None)
    action = (json.loads(str(action_row["metadata_json"]))
              if action_row is not None else {})
    result = action.get("result_metadata")
    firing_ref = payload.get("transition_firing_ref")
    invocation_ref = payload.get("invocation_ref")
    resource_ref = payload.get("resource_ref")
    firing_row = (db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE object_type="
        "'transition_firing/v1' AND version_id=?",
        (str(firing_ref.get("version_id", "")),),
    ).fetchone() if isinstance(firing_ref, Mapping) else None)
    invocation_row = (db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE object_type="
        "'invocation/v1' AND version_id=?",
        (str(invocation_ref.get("version_id", "")),),
    ).fetchone() if isinstance(invocation_ref, Mapping) else None)
    resource_row = (db.execute(
        "SELECT logical_id,metadata_json FROM objects WHERE object_type="
        "'resource_version/v1' AND version_id=?",
        (str(resource_ref.get("resource_version_id", "")),),
    ).fetchone() if isinstance(resource_ref, Mapping) else None)
    firing = (json.loads(str(firing_row["metadata_json"]))
              if firing_row is not None else {})
    invocation = (json.loads(str(invocation_row["metadata_json"]))
                  if invocation_row is not None else {})
    resource = (json.loads(str(resource_row["metadata_json"]))
                if resource_row is not None else {})
    stable_fields = (
        "request_ref", "queue_entry_ref", "transition_firing_ref",
        "transition_id",
        "invocation_ref", "operation_execution_lease_ref",
        "operation_binding_ref", "agent_loop_ref", "agent_turn_ref",
        "agent_action_ref", "requester_agent_ref",
        "logical_resource_id", "lock_resource_ref",
        "requested_resource_ref", "llm_turns_used", "mode",
        "writer_fencing_epoch",
    )
    formal_fields = (
        "access_checkpoint_ref", "access_net_ref", "access_claim_epoch",
        "resource_token_ref", "lease_pool_place", "lease_identity_ref",
        "petri_input_arc_mode", "petri_output_arc_mode",
        "petri_arc_kind", "return_arc_required",
    )
    exact_lock_ref = (
        {"entity_type": "resource_version/v1",
         "logical_id": resource_ref.get("resource_id"),
         "version_id": resource_ref.get("resource_version_id")}
        if isinstance(resource_ref, Mapping) else None)
    if (prior_row is None
            or prior.get("state") != "waiting_resource"
            or any(value.get(field) != prior.get(field)
                   for field in stable_fields)
            or value.get("state") != "granted"
            or value.get("lifecycle_ref") != {
                "entity_type": "resource_access_lifecycle/v1",
                "logical_id": str(lifecycle.logical_id),
                "version_id": str(lifecycle.version_id)}
            or payload.get("resource_access_lifecycle_ref")
            != value.get("lifecycle_ref")
            or payload.get("resource_use_occurrence_ref")
            != value.get("request_ref")
            or payload.get("resource_access_grant_ref")
            != value.get("grant_ref")
            or payload.get("resource_access_lease_ref")
            != value.get("lease_ref")
            or payload.get("logical_resource_id")
            != value.get("logical_resource_id")
            or payload.get("lock_resource_ref") != exact_lock_ref
            or value.get("lock_resource_ref") != exact_lock_ref
            or value.get("requested_resource_ref") != exact_lock_ref
            or value.get("writer_fencing_epoch") != writer_epoch
            or payload.get("writer_fencing_epoch") != writer_epoch
            or payload.get("access_mode")
            != ("edit" if value.get("mode") == "upgrade"
                else value.get("mode"))
            or any(payload.get(field) != value.get(field)
                   for field in formal_fields)
            or payload.get("petri_input_arc_mode")
            != ("read" if payload.get("access_mode") == "read"
                else "borrow")
            or payload.get("petri_output_arc_mode")
            != (None if payload.get("access_mode") == "read"
                else "return")
            or payload.get("petri_arc_kind")
            != payload.get("petri_input_arc_mode")
            or payload.get("return_arc_required")
            != (payload.get("access_mode") == "edit")
            or payload.get("same_firing_continuation") is not True
            or action_row is None
            or action_row["logical_id"]
            != action_ref.get("logical_id")
            or action.get("state") != "WAITING_RESOURCE"
            or action.get("tool_name") != "request_resource"
            or not isinstance(result, Mapping)
            or result.get("resource_use_occurrence_ref")
            != payload.get("resource_use_occurrence_ref")
            or result.get("logical_resource_id")
            != payload.get("logical_resource_id")
            or result.get("lock_resource_ref") != exact_lock_ref
            or result.get("resource_ref") != resource_ref
            or firing_row is None
            or firing_row["logical_id"] != firing_ref.get("logical_id")
            or firing.get("transition_firing_ref") != firing_ref
            or firing.get("transition_id") != payload.get("transition_id")
            or invocation_row is None
            or invocation_row["logical_id"]
            != invocation_ref.get("logical_id")
            or invocation.get("invocation_ref") != invocation_ref
            or invocation.get("own_transition_firing_ref") != firing_ref
            or invocation.get("operation_binding_ref")
            != payload.get("operation_binding_ref")
            or invocation.get("operation_execution_lease_ref")
            != payload.get("operation_execution_lease_ref")
            or resource_row is None
            or resource_row["logical_id"]
            != resource_ref.get("resource_id")
            or resource.get("task_ref") != firing.get("task_ref")
            or event.stream_id != (
                "transition-firing:"
                f"{firing_ref.get('logical_id', '')}")
            or event.aggregate_id != firing_ref.get("logical_id")
            or event.aggregate_type != "transition_firing"
            or event.producer_invocation_id is None
            or str(event.producer_invocation_id)
            != invocation_ref.get("logical_id")):
        raise RegistryConflict(
            "waiting resource grant lacks its exact lifecycle closure")
    return True


def validate_petri_firing_resource_access(
        event_store, db: sqlite3.Connection, events: Sequence[PendingEvent], *,
        task_id: TypedId, net_instance_id: TypedId | None,
        writer_epoch: int,
) -> None:
    """Reconstruct every dynamic resource arc from Registry authority.

        AgentLoop and workflow code may choose an exact resource version and
        ``read`` or ``edit``.  They cannot establish the Petri place, token,
        lease identity, epoch, or arc inscription carried by the event.  This
        precommit gate proves those fields against the current adopted net and
        checkpoint before any component-specific atomicity rule is considered.
        """
    from ...event_store import (
        RegistryConflict,
        _exact_ref_payload,
        verified_adoption_head,
        verified_checkpoint_head,
    )

    extensions = tuple(
        event for event in events
        if event.event_type == "petri_firing_resource_accessed/v1")
    if not extensions:
        return
    if net_instance_id is None:
        raise RegistryConflict(
            "live resource access requires one current Petri net")

    from ....executable_net import load_compiled_net
    from ....firing_resource_access import (
        registered_firing_resource_access_from_payload,
    )
    from ...errors import ResourceIntegrityFault
    from ...publication import (
        _effective_firing_resource_access_events,
    )

    def exact(ref: Mapping[str, Any], object_type: str):
        if (not isinstance(ref, Mapping)
                or set(ref) != {
                    "entity_type", "logical_id", "version_id"}
                or ref.get("entity_type") != object_type):
            raise RegistryConflict(
                "live resource access contains a malformed exact ref")
        row = db.execute(
            "SELECT logical_id,version_id,object_type,metadata_json,"
            "storage_locator FROM objects WHERE object_type=? "
            "AND logical_id=? AND version_id=?",
            (object_type, str(ref["logical_id"]),
             str(ref["version_id"])),
        ).fetchone()
        if row is None:
            raise RegistryConflict(
                "live resource access authority is not registered")
        return row, json.loads(str(row["metadata_json"]))

    adopted_ref = verified_adoption_head(
        event_store, event_store.catalog, task_id, _db=db)
    if adopted_ref.entity_id != net_instance_id:
        raise RegistryConflict(
            "live resource access net is not the current adopted net")
    checkpoint_ref = verified_checkpoint_head(
        event_store, event_store.catalog, task_id, adopted_ref, _db=db)
    net_payload = _exact_ref_payload(adopted_ref)
    checkpoint_payload = _exact_ref_payload(checkpoint_ref)
    _, net = exact(net_payload, "net_instance/v1")
    _, checkpoint = exact(
        checkpoint_payload, "marking_checkpoint/v1")
    source = net.get("team_net_declaration_resource_ref")
    declaration_ref = (
        {"entity_type": "resource_version/v1",
         "logical_id": source.get("resource_id"),
         "version_id": source.get("resource_version_id")}
        if isinstance(source, Mapping) else {})
    declaration_row, declaration = exact(
        declaration_ref, "resource_version/v1")
    try:
        declaration_version = TypedId.parse(
            str(declaration_ref["version_id"]),
            expected="resource_version")
        declaration_path = (
            event_store.path.parent / "objects" / declaration_version.kind
            / declaration_version.value)
        compiled = load_compiled_net(json.loads(
            declaration_path.read_bytes()))
    except (KeyError, TypeError, ValueError, OSError,
            json.JSONDecodeError) as exc:
        raise RegistryConflict(
            "live resource access declaration is not executable") from exc
    if (net.get("net_instance_ref") != net_payload
            or declaration.get("content_schema_ref")
            != "rpnh/executable_net/v1"
            or str(declaration_row["storage_locator"])
            != f"registry-object:{declaration_version}"):
        raise RegistryConflict(
            "live resource access declaration/net closure differs")

    variable_arcs = {
        arc.transition: arc
        for arc in compiled.symbolic.variable_resource_arcs}
    if len(variable_arcs) != len(
            compiled.symbolic.variable_resource_arcs):
        raise RegistryConflict(
            "live resource access declaration repeats a transition arc")
    pools = {
        pool.name: pool for pool in compiled.symbolic.lease_pools}
    checkpoint_tokens = {
        canonical_json(ref) for ref in checkpoint.get("token_refs", ())}
    committed_rows = db.execute(
        "SELECT e.* FROM events e JOIN firing_publications p ON "
        "p.firing_logical_id=e.aggregate_id JOIN transactions opening "
        "ON opening.transaction_id=p.opened_transaction_id WHERE "
        "e.event_type='petri_firing_resource_accessed/v1' "
        "AND e.writer_fencing_epoch=? AND p.state='PROVISIONAL' "
        "AND p.net_version_id=? AND opening.status='committed' "
        "AND opening.writer_epoch=? AND "
        f"{_CANONICAL_EVENT_SQL} ORDER BY e.ordinal",
        (writer_epoch, str(adopted_ref.version_id), writer_epoch),
    ).fetchall()
    committed_by_firing: dict[str, list[EventEnvelope]] = {}
    for row in committed_rows:
        envelope = event_store._row_to_envelope(row)
        committed_by_firing.setdefault(
            envelope.aggregate_id, []).append(envelope)
    committed_accesses = []
    try:
        for firing_events in committed_by_firing.values():
            committed_accesses.extend(
                registered_firing_resource_access_from_payload(
                    event.payload)
                for event in
                _effective_firing_resource_access_events(
                    firing_events))
    except (TypeError, ValueError, ResourceIntegrityFault) as exc:
        raise RegistryConflict(
            "active firing has malformed formal resource access") from exc
    pending_accesses = []

    for event in extensions:
        payload = dict(event.payload)
        try:
            authority = registered_firing_resource_access_from_payload(
                payload)
        except (TypeError, ValueError) as exc:
            raise RegistryConflict(
                "live resource access has no typed Petri authority") from exc
        access = authority.access
        firing_payload = _exact_ref_payload(access.firing_ref)
        token_payload = _exact_ref_payload(access.resource_token_ref)
        lease_payload = _exact_ref_payload(access.lease_identity_ref)
        if (authority.net_ref != adopted_ref
                or authority.checkpoint_ref != checkpoint_ref
                or payload.get("writer_fencing_epoch") != writer_epoch
                or event.stream_id != (
                    "transition-firing:"
                    f"{access.firing_ref.entity_id}")
                or event.aggregate_id != str(access.firing_ref.entity_id)
                or event.aggregate_type != "transition_firing"):
            raise RegistryConflict(
                "live resource access is outside its current Petri head")

        firing_row, firing = exact(
            firing_payload, access.firing_ref.entity_type)
        active = db.execute(
            "SELECT p.firing_logical_id,p.net_version_id,p.state,"
            "t.writer_epoch,t.status FROM firing_publications p "
            "JOIN transactions t ON t.transaction_id="
            "p.opened_transaction_id WHERE p.firing_version_id=?",
            (str(access.firing_ref.version_id),),
        ).fetchone()
        if (firing_row["logical_id"]
                != str(access.firing_ref.entity_id)
                or firing.get("transition_firing_ref") != firing_payload
                or firing.get("net_instance_ref") != net_payload
                or firing.get("transition_id") != access.transition_id
                or active is None
                or active["firing_logical_id"]
                != str(access.firing_ref.entity_id)
                or active["net_version_id"]
                != str(adopted_ref.version_id)
                or active["state"] != "PROVISIONAL"
                or active["writer_epoch"] != writer_epoch
                or active["status"] != "committed"):
            raise RegistryConflict(
                "live resource access firing is not current and active")

        arc = variable_arcs.get(access.transition_id)
        pool = pools.get(arc.lease_pool) if arc is not None else None
        if (arc is None or pool is None
                or pool.place != access.lease_pool_place
                or arc.input_inscription
                != "consume_exact_claim_multiset"
                or arc.output_inscription
                != "return_exact_claim_multiset"):
            raise RegistryConflict(
                "live resource access has no declared variable Petri arc")

        token_row, token = exact(token_payload, "petri_token/v1")
        if (token_row["logical_id"]
                != str(access.resource_token_ref.entity_id)
                or canonical_json(token_payload) not in checkpoint_tokens
                or checkpoint.get("marking_checkpoint_ref")
                != checkpoint_payload
                or checkpoint.get("net_instance_ref") != net_payload
                or checkpoint.get("epoch") != access.claim_epoch
                or token.get("petri_token_ref") != token_payload
                or token.get("net_instance_ref") != net_payload
                or token.get("place") != access.lease_pool_place
                or token.get("epoch") != access.claim_epoch
                or token.get("resource_ref")
                != payload.get("resource_ref")
                or token.get("lease_identity_ref") != lease_payload
                or token.get("consumed_by") is not None
                or token_payload in firing.get(
                    "claimed_input_refs", ())):
            raise RegistryConflict(
                "live resource access token is not the exact live pool token")

        claimed_refs = firing.get("claimed_input_refs", ())
        claim_places = []
        for claimed_ref in claimed_refs:
            _claim_row, claim = exact(
                claimed_ref, "petri_token/v1")
            if (claim.get("net_instance_ref") != net_payload
                    or claim.get("epoch") != access.claim_epoch):
                raise RegistryConflict(
                    "live resource access firing claim is foreign")
            if claim.get("place") == arc.claim_token_place:
                claim_places.append(claimed_ref)
        if len(claim_places) != 1:
            raise RegistryConflict(
                "live resource access firing lacks its claim-token arc")

        active_firing_rows = db.execute(
            "SELECT p.firing_logical_id,p.firing_version_id,"
            "o.metadata_json FROM firing_publications p "
            "JOIN transactions opening ON opening.transaction_id="
            "p.opened_transaction_id JOIN objects o ON "
            "o.version_id=p.firing_version_id AND "
            "o.object_type='transition_firing/v1' WHERE "
            "p.state='PROVISIONAL' AND p.net_version_id=? "
            "AND opening.status='committed' "
            "AND opening.writer_epoch=?",
            (str(adopted_ref.version_id), writer_epoch),
        ).fetchall()
        for active_row in active_firing_rows:
            if (active_row["firing_version_id"]
                    == str(access.firing_ref.version_id)):
                continue
            other_firing = json.loads(
                str(active_row["metadata_json"]))
            other_claims = other_firing.get("claimed_input_refs", ())
            other_claim_payloads = {
                canonical_json(ref) for ref in other_claims}
            if canonical_json(token_payload) not in other_claim_payloads:
                continue
            delta_ref = other_firing.get("claim_marking_delta_ref")
            _delta_row, delta = exact(delta_ref, "marking_delta/v1")
            consumed = {
                canonical_json(ref)
                for ref in delta.get("consumed_refs", ())}
            if (delta.get("phase") != "claim"
                    or delta.get("net_instance_ref") != net_payload
                    or not consumed <= other_claim_payloads):
                raise RegistryConflict(
                    "active firing has malformed resource claim authority")
            if (canonical_json(token_payload) in consumed
                    or access.access_mode == "edit"):
                raise RegistryConflict(
                    "live resource access conflicts with an active "
                    "Petri claim")

        for other in (*committed_accesses, *pending_accesses):
            other_access = other.access
            if (other_access.firing_ref == access.firing_ref
                    and other_access.resource_token_ref
                    == access.resource_token_ref):
                raise RegistryConflict(
                    "one firing repeats a resource access in one commit")
            if (other_access.firing_ref != access.firing_ref
                    and other_access.resource_token_ref
                    == access.resource_token_ref
                    and (other_access.access_mode == "edit"
                         or access.access_mode == "edit")):
                raise RegistryConflict(
                    "live resource access conflicts with another "
                    "firing-local Petri arc")
        pending_accesses.append(authority)

def validate_fresh_bootstrap_reference_resource(
        context,
        item: PreparedObject, metadata: Mapping[str, Any],
        origin: Mapping[str, Any], direct: Mapping[str, Any]) -> None:
    branch_id = context.branch_id
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    PROTECTED_SCHEMA_REFS = facade.PROTECTED_SCHEMA_REFS
    RegistryConflict = facade.RegistryConflict
    _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS = facade._DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
    _stable_id_text = facade._stable_id_text
    canonical_json = facade.canonical_json
    fresh_bootstrap_resource_id = facade.fresh_bootstrap_resource_id
    require_canonical_producer = partial(
        provider_validation.require_canonical_producer, context)
    """Close fresh bootstrap resources without an invocation owner."""

    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    derived_refs = direct.get("derived_from_refs")
    contributor_refs = direct.get("contributor_refs")
    supersedes_ref = direct.get("supersedes_ref")
    primary = origin.get("primary_ref")
    secondary = origin.get("secondary_ref")
    task_ref = metadata.get("task_ref")
    lifetime_ref = metadata.get("lifetime_ref")
    schema_id = metadata.get("content_schema_ref")
    schema_source = metadata.get("content_schema_authority_ref")
    schema_source_exact = (
        {
            "entity_type": "resource_version/v1",
            "logical_id": schema_source.get("resource_id"),
            "version_id": schema_source.get("resource_version_id"),
        }
        if isinstance(schema_source, Mapping)
        and "resource_id" in schema_source else schema_source)
    expected_schema_type = (
        "registry_type_catalog/v1"
        if isinstance(schema_id, str)
        and schema_id in PROTECTED_SCHEMA_REFS
        else "resource_version/v1")
    if (set(metadata) != _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
            or set(direct) != {
                "schema_version", "producer_invocation_ref",
                "operation_binding_ref", "derived_from_refs",
                "contributor_refs", "supersedes_ref",
                "intended_consumer", "publication"}
            or direct.get("schema_version")
            != "resource_reference_provenance/v1"
            or direct.get("producer_invocation_ref") is not None
            or direct.get("operation_binding_ref") is not None
            or metadata.get("origin_kind") != "private_system"
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(derived_refs, list)
            or contributor_refs != []
            or derived_refs != sorted(derived_refs, key=canonical_json)
            or len(derived_refs) != len({
                canonical_json(value) for value in derived_refs})
            or any(not exact_ref_exists(
                value, "resource_version/v1") for value in derived_refs)
            or (supersedes_ref is not None and not exact_ref_exists(
                supersedes_ref, "resource_version/v1"))
            or not exact_ref_exists(task_ref, "task/v1")
            or task_ref.get("logical_id") != str(task_id)
            or metadata.get("branch_id") != branch_id
            or metadata.get("round_ref") is not None
            or metadata.get("net_ref") is not None
            or task_round_id is not None
            or net_instance_id is not None
            or primary != secondary
            or metadata.get("producer_ref") != primary
            or not exact_ref_exists(primary, "bootstrap_command/v1")
            or not exact_ref_exists(lifetime_ref)
            or item.producer_invocation_id is not None
            or ((schema_id is None) != (schema_source is None))
            or (schema_id is not None and (
                not isinstance(schema_id, str)
                or not exact_ref_exists(
                    schema_source_exact, expected_schema_type)))
            or publication != {
                "origin_kind": "private_system",
                "primary_ref": primary,
                "secondary_ref": secondary,
                "lifetime_ref": lifetime_ref,
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": schema_id,
                "content_schema_authority_ref": schema_source,
            }
            or consumer != {
                "boundary": "not_applicable",
                "consumer_ref": None}
            or str(item.logical_id) != str(
                fresh_bootstrap_resource_id(
                    task_id,
                    TypedId.parse(
                        primary.get("version_id"),
                        expected="bootstrap_command_version"),
                    idempotency_key))
            or str(item.version_id) != _stable_id_text(
                "resource_version", task_id, idempotency_key,
                "fresh_bootstrap_reference", primary.get("version_id"),
                lifetime_ref.get("version_id"))):
        raise RegistryConflict(
            "fresh bootstrap reference authority is not exact")

    resource_ref = {
        "entity_type": "resource_version/v1",
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
    }
    require_canonical_producer(
        resource_ref, primary,
        publication_transaction=str(transaction_id))
    publications = [
        pending for pending in events
        if pending.event_type == "object_version_published/v1"
        and pending.payload.get("version_id") == str(item.version_id)]
    if (len(publications) != 1
            or publications[0].payload.get("metadata") != metadata
            or publications[0].payload.get("size") != item.size
            or publications[0].producer_invocation_id is not None):
        raise RegistryConflict(
            "fresh bootstrap resource lacks one exact publication fact")

    def exact_relation_targets(relation_type: str) -> list[bytes]:
        records = exact_relation_records(
            relation_type, source_version=str(item.version_id))
        if any(record[2] != str(transaction_id)
               or record[3] != "strong" for record in records):
            raise RegistryConflict(
                "fresh bootstrap relation is not born strong in its transaction")
        return sorted(canonical_json({
            "entity_type": record[1].get("entity_type"),
            "logical_id": record[1].get(
                "logical_id", record[1].get("entity_id")),
            "version_id": record[1].get("version_id"),
        }) for record in records)

    expected_derived = sorted(
        canonical_json(value) for value in derived_refs)
    expected_supersedes = (
        [canonical_json(supersedes_ref)]
        if supersedes_ref is not None else [])
    if (exact_relation_targets("derived_from") != expected_derived
            or exact_relation_targets("contributed_by")
            or exact_relation_targets("supersedes")
            != expected_supersedes):
        raise RegistryConflict(
            "fresh bootstrap exact predecessors differ from relations")

def validate_checkpoint_repair_reference_resource(
        context,
        item: PreparedObject, metadata: Mapping[str, Any],
        origin: Mapping[str, Any], direct: Mapping[str, Any]) -> None:
    branch_id = context.branch_id
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    new_by_version = context.new_by_version
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS = facade._DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
    _stable_id_text = facade._stable_id_text
    canonical_json = facade.canonical_json
    require_canonical_producer = partial(
        provider_validation.require_canonical_producer, context)
    """Close a replacement over its repair, source, net, and relations."""

    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    derived_refs = direct.get("derived_from_refs")
    contributor_refs = direct.get("contributor_refs")
    primary = origin.get("primary_ref")
    secondary = origin.get("secondary_ref")
    task_ref = metadata.get("task_ref")
    round_ref = metadata.get("round_ref")
    net_ref = metadata.get("net_ref")
    schema_id = metadata.get("content_schema_ref")
    schema_source = metadata.get("content_schema_authority_ref")
    schema_source_exact = (
        {
            "entity_type": "resource_version/v1",
            "logical_id": schema_source.get("resource_id"),
            "version_id": schema_source.get("resource_version_id"),
        }
        if isinstance(schema_source, Mapping)
        and "resource_id" in schema_source else schema_source)
    expected_schema_type = (
        "registry_type_catalog/v1"
        if isinstance(schema_id, str)
        and schema_id.startswith(("registry_v1/", "rpnh/", "petri/"))
        else "resource_version/v1")
    repair = (version_metadata(
        str(primary.get("version_id", "")), "checkpoint_repair/v1")
        if isinstance(primary, Mapping) else None)
    replacement_ref = {
        "entity_type": "resource_version/v1",
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
    }
    replacement_rows = (
        repair.get("replacements") if isinstance(repair, Mapping)
        else None)
    matching_replacements = (
        [row for row in replacement_rows
         if isinstance(row, Mapping)
         and row.get("source_resource_ref") == secondary
         and row.get("replacement_resource_ref") == replacement_ref]
        if isinstance(replacement_rows, list) else [])
    if (set(metadata) != _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
            or set(direct) != {
                "schema_version", "producer_invocation_ref",
                "operation_binding_ref", "derived_from_refs",
                "contributor_refs", "supersedes_ref",
                "intended_consumer", "publication"}
            or direct.get("schema_version")
            != "resource_reference_provenance/v1"
            or direct.get("producer_invocation_ref") is not None
            or direct.get("operation_binding_ref") is not None
            or direct.get("supersedes_ref") is not None
            or metadata.get("origin_kind") != "checkpoint_repair"
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(derived_refs, list)
            or not derived_refs
            or contributor_refs != []
            or derived_refs != sorted(derived_refs, key=canonical_json)
            or len(derived_refs) != len({
                canonical_json(value) for value in derived_refs})
            or secondary not in derived_refs
            or any(not exact_ref_exists(
                value, "resource_version/v1") for value in derived_refs)
            or not exact_ref_exists(task_ref, "task/v1")
            or task_ref.get("logical_id") != str(task_id)
            or metadata.get("branch_id") != branch_id
            or not exact_ref_exists(round_ref, "task_round/v1")
            or not exact_ref_exists(net_ref, "net_instance/v1")
            or round_ref.get("logical_id") != str(task_round_id)
            or net_ref.get("logical_id") != str(net_instance_id)
            or repair is None
            or primary.get("entity_type") != "checkpoint_repair/v1"
            or str(primary.get("version_id", "")) not in new_by_version
            or repair.get("checkpoint_repair_ref") != primary
            or repair.get("net_instance_ref") != net_ref
            or not exact_ref_exists(repair.get("repair_authority_ref"))
            or len(matching_replacements) != 1
            or metadata.get("producer_ref") != primary
            or metadata.get("lifetime_ref") != primary
            or item.producer_invocation_id is not None
            or ((schema_id is None) != (schema_source is None))
            or (schema_id is not None and (
                not isinstance(schema_id, str)
                or not exact_ref_exists(
                    schema_source_exact, expected_schema_type)))
            or publication != {
                "origin_kind": "checkpoint_repair",
                "primary_ref": primary,
                "secondary_ref": secondary,
                "lifetime_ref": primary,
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": schema_id,
                "content_schema_authority_ref": schema_source,
            }
            or consumer != {
                "boundary": "not_applicable",
                "consumer_ref": None}
            or str(item.logical_id) != _stable_id_text(
                "resource", task_id, "checkpoint_repair",
                primary.get("version_id"), secondary.get("logical_id"),
                secondary.get("version_id"))
            or str(item.version_id) != _stable_id_text(
                "resource_version", task_id, idempotency_key,
                "checkpoint_repair_reference",
                primary.get("version_id"), secondary.get("version_id"))):
        raise RegistryConflict(
            "checkpoint repair resource authority is not exact")

    require_canonical_producer(
        replacement_ref, primary,
        publication_transaction=str(transaction_id))
    publications = [
        pending for pending in events
        if pending.event_type == "object_version_published/v1"
        and pending.payload.get("version_id") == str(item.version_id)]
    if (len(publications) != 1
            or publications[0].payload.get("metadata") != metadata
            or publications[0].payload.get("size") != item.size
            or publications[0].producer_invocation_id is not None):
        raise RegistryConflict(
            "checkpoint repair resource lacks one publication fact")

    def exact_relation_targets(relation_type: str) -> list[bytes]:
        records = exact_relation_records(
            relation_type, source_version=str(item.version_id))
        if any(record[2] != str(transaction_id)
               or record[3] != "strong" for record in records):
            raise RegistryConflict(
                "checkpoint repair relation is not born strong")
        return sorted(canonical_json({
            "entity_type": record[1].get("entity_type"),
            "logical_id": record[1].get(
                "logical_id", record[1].get("entity_id")),
            "version_id": record[1].get("version_id"),
        }) for record in records)

    expected_derived = sorted(
        canonical_json(value) for value in derived_refs)
    if (exact_relation_targets("derived_from") != expected_derived
            or exact_relation_targets("contributed_by")
            or exact_relation_targets("supersedes")):
        raise RegistryConflict(
            "checkpoint repair predecessors differ from relations")

def validate_fresh_reference_resource(
        context,
        item: PreparedObject, metadata: Mapping[str, Any],
        origin: Mapping[str, Any], direct: Mapping[str, Any]) -> None:
    branch_id = context.branch_id
    events = context.events
    net_instance_id = context.net_instance_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS = facade._DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
    canonical_json = facade.canonical_json
    require_canonical_producer = partial(
        provider_validation.require_canonical_producer, context)
    """Close one fresh normal resource over exact Registry refs only."""

    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    invocation_ref = direct.get("producer_invocation_ref")
    binding_ref = direct.get("operation_binding_ref")
    derived_refs = direct.get("derived_from_refs")
    tool_evidence_refs = direct.get("tool_evidence_refs")
    contributor_refs = direct.get("contributor_refs")
    supersedes_ref = direct.get("supersedes_ref")
    invocation = (version_metadata(
        str(invocation_ref.get("version_id", "")), "invocation/v1")
        if isinstance(invocation_ref, Mapping) else None)
    binding = (version_metadata(
        str(binding_ref.get("version_id", "")), "operation_binding/v1")
        if isinstance(binding_ref, Mapping) else None)
    origin_kind = metadata.get("origin_kind")
    primary = origin.get("primary_ref")
    secondary = origin.get("secondary_ref")
    expected_boundary = {
        "petri_output": "petri_input",
        "workspace_write": "tool_result",
    }.get(str(origin_kind), "not_applicable")
    expected_consumer = (
        primary if origin_kind == "petri_output" else
        secondary if origin_kind == "workspace_write" else None)
    schema_id = metadata.get("content_schema_ref")
    schema_source = metadata.get("content_schema_authority_ref")
    schema_source_exact = (
        {
            "entity_type": "resource_version/v1",
            "logical_id": schema_source.get("resource_id"),
            "version_id": schema_source.get("resource_version_id"),
        }
        if isinstance(schema_source, Mapping)
        and "resource_id" in schema_source else schema_source)
    expected_schema_type = (
        "registry_type_catalog/v1"
        if isinstance(schema_source_exact, Mapping)
        and schema_source_exact.get("entity_type") == "registry_type_catalog/v1"
        else "resource_version/v1")
    transaction_scope_errors = []
    if (metadata.get("round_ref", {}).get("logical_id")
            != str(task_round_id)):
        transaction_scope_errors.append("task_round_id")
    if (metadata.get("net_ref", {}).get("logical_id")
            != str(net_instance_id)):
        transaction_scope_errors.append("net_instance_id")
    if transaction_scope_errors:
        raise RegistryConflict(
            "fresh resource transaction scope is not exact: "
            + ",".join(transaction_scope_errors))
    if (set(metadata) != _DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS
            or set(direct) != {
                "schema_version", "producer_invocation_ref",
                "operation_binding_ref", "derived_from_refs",
                "tool_evidence_refs",
                "contributor_refs", "supersedes_ref",
                "intended_consumer", "publication"}
            or direct.get("schema_version")
            != "resource_reference_provenance/v1"
            or origin_kind not in {
                "petri_output", "workspace_write", "private_system"}
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(derived_refs, list)
            or not isinstance(tool_evidence_refs, list)
            or not isinstance(contributor_refs, list)
            or derived_refs != sorted(derived_refs, key=canonical_json)
            or contributor_refs
            != sorted(contributor_refs, key=canonical_json)
            or tool_evidence_refs
            != sorted(tool_evidence_refs, key=canonical_json)
            or len(derived_refs) != len({
                canonical_json(value) for value in derived_refs})
            or len(contributor_refs) != len({
                canonical_json(value) for value in contributor_refs})
            or len(tool_evidence_refs) != len({
                canonical_json(value) for value in tool_evidence_refs})
            or any(not exact_ref_exists(
                value, "resource_version/v1") for value in derived_refs)
            or any(not exact_ref_exists(value)
                   for value in contributor_refs)
            or any(not exact_ref_exists(value, "agent_action/v2")
                   for value in tool_evidence_refs)
            or bool(tool_evidence_refs)
            or (supersedes_ref is not None and not exact_ref_exists(
                supersedes_ref, "resource_version/v1"))
            or invocation is None or binding is None
            or invocation.get("invocation_ref") != invocation_ref
            or invocation.get("operation_binding_ref") != binding_ref
            or invocation.get("task_ref") != metadata.get("task_ref")
            or invocation.get("task_round_ref")
            != metadata.get("round_ref")
            or invocation.get("net_instance_ref")
            != metadata.get("net_ref")
            or metadata.get("branch_id") != branch_id
            or item.producer_invocation_id is None
            or str(item.producer_invocation_id)
            != str(invocation_ref.get("logical_id"))
            or ((schema_id is None) != (schema_source is None))
            or (schema_id is not None and (
                not isinstance(schema_id, str)
                or not exact_ref_exists(
                    schema_source_exact, expected_schema_type)))
            or publication != {
                "origin_kind": origin_kind,
                "primary_ref": primary,
                "secondary_ref": secondary,
                "lifetime_ref": metadata.get("lifetime_ref"),
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": schema_id,
                "content_schema_authority_ref": schema_source,
            }
            or consumer != {
                "boundary": expected_boundary,
                "consumer_ref": expected_consumer}):
        raise RegistryConflict(
            "fresh resource reference authority is not exact")
    if origin_kind == "petri_output":
        output = (version_metadata(
            str(primary.get("version_id", "")), "output_binding/v1")
            if isinstance(primary, Mapping) else None)
        output_bindings = binding.get("output_binding_refs", [])
        if (output is None
                or metadata.get("producer_ref") != invocation_ref
                or invocation.get("activation_ref") != secondary
                or primary not in output_bindings
                or output.get("task_round_ref")
                != metadata.get("round_ref")
                or output.get("net_ref") != metadata.get("net_ref")
                or output.get("node_ref")
                != invocation.get("own_node_ref")):
            raise RegistryConflict(
                "fresh Petri output origin is not exact")
    elif origin_kind == "workspace_write":
        intent = (version_metadata(
            str(secondary.get("version_id", "")),
            "workspace_write_intent/v1")
            if isinstance(secondary, Mapping) else None)
        if (metadata.get("producer_ref") != invocation_ref
                or primary != binding_ref or intent is None
                or intent.get("operation_binding_ref") != binding_ref):
            raise RegistryConflict(
                "fresh workspace-write origin is not exact")
    elif (primary != secondary
          or not exact_ref_exists(primary, "bootstrap_command/v1")
          or metadata.get("producer_ref") != primary):
        raise RegistryConflict(
            "fresh private-system origin is not exact")

    resource_ref = {
        "entity_type": "resource_version/v1",
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
    }
    producer_ref = metadata.get("producer_ref")
    require_canonical_producer(
        resource_ref, producer_ref,
        publication_transaction=str(transaction_id))
    publications = [
        pending for pending in events
        if pending.event_type == "object_version_published/v1"
        and pending.payload.get("version_id") == str(item.version_id)]
    if (len(publications) != 1
            or publications[0].payload.get("metadata") != metadata
            or publications[0].payload.get("size") != item.size):
        raise RegistryConflict(
            "fresh resource lacks one exact publication fact")

    def exact_relation_targets(relation_type: str) -> list[bytes]:
        records = exact_relation_records(
            relation_type, source_version=str(item.version_id))
        if any(record[2] != str(transaction_id)
               or record[3] != "strong" for record in records):
            raise RegistryConflict(
                "fresh resource relation is not born strong in its transaction")
        return sorted(canonical_json({
            "entity_type": record[1].get("entity_type"),
            "logical_id": record[1].get(
                "logical_id", record[1].get("entity_id")),
            "version_id": record[1].get("version_id"),
        }) for record in records)

    expected_derived = sorted(
        canonical_json(value) for value in derived_refs)
    expected_contributors = sorted(
        canonical_json(value) for value in contributor_refs)
    expected_tool_evidence = sorted(
        canonical_json(value) for value in tool_evidence_refs)
    expected_supersedes = (
        [canonical_json(supersedes_ref)]
        if supersedes_ref is not None else [])
    if (exact_relation_targets("derived_from") != expected_derived
            or exact_relation_targets("contributed_by")
            != expected_contributors
            or exact_relation_targets("caused_by_tool_result")
            != expected_tool_evidence
            or exact_relation_targets("supersedes")
            != expected_supersedes):
        raise RegistryConflict(
            "fresh resource exact predecessors differ from relations")

def validate_resource_objects(context):
    branch_id = context.branch_id
    event_store = context.event_store
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    objects = context.objects
    relations = context.relations
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    exact_relation_records = context.exact_relation_records
    has_new_relation = context.has_new_relation
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _PROVIDER_RESPONSE_METADATA_ROOT_KEYS = facade._PROVIDER_RESPONSE_METADATA_ROOT_KEYS
    _ref_json = facade._ref_json
    _stable_id_text = facade._stable_id_text
    canonical_json = facade.canonical_json
    canonical_text = facade.canonical_text
    require_canonical_attempt_response_proposal = partial(
        provider_validation.require_canonical_attempt_response_proposal,
        context)
    require_canonical_producer = partial(
        provider_validation.require_canonical_producer, context)
    require_canonical_provider_lineage = partial(
        provider_validation.require_canonical_provider_lineage, context)
    validate_checkpoint_repair_reference_resource = partial(globals()["validate_checkpoint_repair_reference_resource"], context)
    validate_current_invocation_authority = partial(
        agent_loop_validation.validate_current_invocation_authority, context)
    validate_direct_provider_raw_response = partial(
        provider_validation.validate_direct_provider_raw_response, context)
    validate_fresh_bootstrap_reference_resource = partial(globals()["validate_fresh_bootstrap_reference_resource"], context)
    validate_fresh_reference_resource = partial(globals()["validate_fresh_reference_resource"], context)
    for item in objects:
        if item.object_type != "resource_version/v1":
            continue
        metadata = dict(item.metadata)
        origin = metadata.get("origin")
        round_ref = metadata.get("round_ref")
        net_ref = metadata.get("net_ref")
        if (metadata.get("resource_id") != str(item.logical_id)
                or metadata.get("resource_version_id") != str(item.version_id)
                or metadata.get("size") != item.size
                or metadata.get("media_type") != item.media_type
                or not isinstance(origin, Mapping)
                or origin.get("kind") != metadata.get("origin_kind")
                or not exact_ref_exists(origin.get("primary_ref"))
                or (origin.get("secondary_ref") is not None
                    and not exact_ref_exists(origin.get("secondary_ref")))
                or (origin.get("secondary_ref") is None
                    and origin.get("kind") != "petri_output")
                or not exact_ref_exists(metadata.get("task_ref"), "task/v1")
                or not exact_ref_exists(metadata.get("producer_ref"))
                or not exact_ref_exists(metadata.get("lifetime_ref"))):
            raise RegistryConflict(
                "resource metadata does not close over its immutable envelope")
        direct_provenance = metadata.get("reference_provenance")
        if not isinstance(direct_provenance, Mapping):
            raise RegistryConflict(
                "current resource lacks exact reference provenance")
        if "producer_invocation_ref" in direct_provenance:
            if direct_provenance.get("producer_invocation_ref") is None:
                if metadata.get("origin_kind") == "checkpoint_repair":
                    validate_checkpoint_repair_reference_resource(
                        item, metadata, origin, direct_provenance)
                else:
                    validate_fresh_bootstrap_reference_resource(
                        item, metadata, origin, direct_provenance)
            else:
                validate_fresh_reference_resource(
                    item, metadata, origin, direct_provenance)
        elif metadata.get("origin_kind") == "provider_raw_response":
            validate_direct_provider_raw_response(
                item, metadata, origin, direct_provenance)
        continue
        # The former dependency-material validator remains physically
        # isolated below until deleting it can be handled as a separate
        # cleanup; no current write can enter it.
        dependency_material = metadata.get("dependency_material")
        if (not isinstance(dependency_material, Mapping)
                or dependency_material.get("schema_version")
                != "resource_dependency/v1"
                or canonical_text(dependency_material)
                != metadata.get("command_digest")):
            raise RegistryConflict(
                "resource command_digest is not its strict dependency fingerprint")
        authority_refs = dependency_material.get("authority_refs")
        input_resources = dependency_material.get("input_resources")
        predecessor_fingerprints = dependency_material.get(
            "ordered_predecessor_fingerprints")
        catalog_digests = dependency_material.get("catalog_digests")
        material_producer = dependency_material.get("producer")
        execution = dependency_material.get("execution")
        intended_consumer = dependency_material.get("intended_consumer")
        publication = dependency_material.get("publication")
        content_schema_id = metadata.get("content_schema_ref")
        content_schema_source = metadata.get(
            "content_schema_authority_ref")
        if isinstance(content_schema_source, Mapping):
            if "resource_id" in content_schema_source:
                content_schema_source_exact = {
                    "entity_type": "resource_version/v1",
                    "logical_id": content_schema_source.get("resource_id"),
                    "version_id": content_schema_source.get(
                        "resource_version_id"),
                }
            else:
                content_schema_source_exact = content_schema_source
        else:
            content_schema_source_exact = None
        if (not isinstance(authority_refs, list)
                or authority_refs
                != sorted(authority_refs, key=canonical_json)
                or any(not exact_ref_exists(value) for value in authority_refs)
                or not isinstance(input_resources, list)
                or input_resources != sorted(
                    input_resources,
                    key=lambda value: canonical_json(
                        value.get("resource_ref", {})
                        if isinstance(value, Mapping) else {}))
                or not isinstance(predecessor_fingerprints, list)
                or not isinstance(catalog_digests, Mapping)
                or not isinstance(material_producer, Mapping)
                or not isinstance(execution, Mapping)
                or not isinstance(intended_consumer, Mapping)
                or not isinstance(publication, Mapping)
                or catalog_digests.get("registry_catalog")
                != event_store.catalog.digest
                or ((content_schema_id is None)
                    != (content_schema_source is None))
                or (content_schema_id is not None and (
                    not isinstance(content_schema_id, str)
                    or not exact_ref_exists(content_schema_source_exact)
                    or content_schema_source_exact not in authority_refs
                    or not isinstance(
                        catalog_digests.get("content_schema"), str)
                    or (content_schema_id.startswith(("registry_v1/", "rpnh/", "petri/"))
                        and content_schema_source_exact.get("entity_type")
                        != "registry_type_catalog/v1")
                    or (content_schema_id.startswith(
                            ("application/", "runtime/"))
                        and content_schema_source_exact.get("entity_type")
                        != "resource_version/v1")))):
            raise RegistryConflict(
                "resource dependency authority/input closure is not canonical")
        verified_predecessors: list[str] = []
        for dependency in input_resources:
            resource_ref = (dependency.get("resource_ref")
                            if isinstance(dependency, Mapping) else None)
            prior = version_metadata(
                str(resource_ref.get("version_id", "")),
                "resource_version/v1") if isinstance(
                    resource_ref, Mapping) else None
            if (prior is None
                    or resource_ref.get("entity_type")
                    != "resource_version/v1"
                    or dependency.get("payload_digest")
                    != prior.get("payload_digest")
                    or dependency.get("size") != prior.get("size")
                    or dependency.get("command_digest")
                    != prior.get("command_digest")):
                raise RegistryConflict(
                    "resource direct dependency identity is not exact")
            verified_predecessors.append(str(prior["command_digest"]))
        if verified_predecessors != predecessor_fingerprints:
            raise RegistryConflict(
                "resource predecessor fingerprint order differs")
        expected_publication = {
            "origin_kind": metadata.get("origin_kind"),
            "primary_ref": origin.get("primary_ref"),
            "secondary_ref": origin.get("secondary_ref"),
            "lifetime_ref": metadata.get("lifetime_ref"),
            "size": metadata.get("size"),
            "media_type": metadata.get("media_type"),
            "content_schema_ref": metadata.get("content_schema_ref"),
            "content_schema_authority_ref": metadata.get(
                "content_schema_authority_ref"),
            "supersedes_ref": publication.get("supersedes_ref"),
            "contributor_refs": publication.get("contributor_refs"),
        }
        if publication != expected_publication:
            raise RegistryConflict(
                "resource dependency publication differs from immutable envelope")
        contributor_refs = publication.get("contributor_refs")
        supersedes_ref = publication.get("supersedes_ref")
        if (not isinstance(contributor_refs, list)
                or contributor_refs
                != sorted(contributor_refs, key=canonical_json)
                or any(not exact_ref_exists(value)
                       for value in contributor_refs)
                or (supersedes_ref is not None
                    and not exact_ref_exists(
                        supersedes_ref, "resource_version/v1"))):
            raise RegistryConflict(
                "resource contributor/supersedes dependencies are not exact")
        contributed_records = exact_relation_records(
            "contributed_by", source_version=str(item.version_id))
        supersedes_records = exact_relation_records(
            "supersedes", source_version=str(item.version_id))
        if (sorted(str(record[1].get("version_id", ""))
                   for record in contributed_records)
                != sorted(str(value["version_id"])
                          for value in contributor_refs)
                or any(record[2] != str(transaction_id)
                       or record[3] != "strong"
                       for record in contributed_records)
                or ((supersedes_ref is None and supersedes_records)
                    or (supersedes_ref is not None and (
                        len(supersedes_records) != 1
                        or supersedes_records[0][1].get("version_id")
                        != supersedes_ref.get("version_id")
                        or supersedes_records[0][2] != str(transaction_id)
                        or supersedes_records[0][3] != "strong")))):
            raise RegistryConflict(
                "resource contributor/supersedes file relations differ")
        expected_boundary = {
            "petri_output": "petri_input",
            "workspace_write": "tool_result",
        }.get(str(metadata.get("origin_kind")), "not_applicable")
        expected_consumer = (
            origin.get("primary_ref")
            if metadata.get("origin_kind") == "petri_output" else
            origin.get("secondary_ref")
            if expected_boundary != "not_applicable" else None)
        if intended_consumer != {
                "boundary": expected_boundary,
                "consumer_ref": expected_consumer}:
            raise RegistryConflict(
                "resource dependency intended consumer differs from origin")
        input_relation_records = [
            record for record in exact_relation_records(
                "derived_from", source_version=str(item.version_id))
            if record[1].get("entity_type") == "resource_version/v1"
        ]
        expected_input_versions = [
            str(value["resource_ref"]["version_id"])
            for value in input_resources]
        actual_input_versions = sorted(
            str(record[1].get("version_id", ""))
            for record in input_relation_records)
        if (actual_input_versions != sorted(expected_input_versions)
                or any(record[2] != str(transaction_id)
                       or record[3] != "strong"
                       for record in input_relation_records)):
            raise RegistryConflict(
                "resource dependency inputs differ from atomic file relations")
        if metadata.get("origin_kind") == "private_system":
            expected_private_producer = {
                "principal_ref": origin.get("primary_ref"),
                "invocation_ref": None,
                "operation_binding_ref": None,
                "firing_ref": None,
            }
            expected_private_execution = {
                "task_ref": metadata.get("task_ref"),
                "branch_id": branch_id,
                "round_ref": None, "plan_ref": None,
                "team_design_root_ref": None, "net_ref": None,
                "materialization_digest": None, "budget_ref": None,
            }
            required_authority = {
                canonical_json(value) for value in (
                    origin.get("primary_ref"), origin.get("secondary_ref"),
                    metadata.get("lifetime_ref"), metadata.get("task_ref"))
            }
            if (round_ref is not None or net_ref is not None
                    or material_producer != expected_private_producer
                    or execution != expected_private_execution
                    or not required_authority.issubset({
                        canonical_json(value) for value in authority_refs})):
                raise RegistryConflict(
                    "private-system dependency authority is not closed")
        elif (not exact_ref_exists(round_ref, "task_round/v1")
              or not exact_ref_exists(net_ref, "net_instance/v1")
              or round_ref.get("logical_id") != str(task_round_id)
              or net_ref.get("logical_id") != str(net_instance_id)):
            raise RegistryConflict(
                "application resource round/net differs from transaction authority")
        else:
            invocation_ref = metadata.get("producer_ref")
            invocation = version_metadata(
                str(invocation_ref.get("version_id", "")),
                "invocation/v1") if isinstance(
                    invocation_ref, Mapping) else None
            binding_ref = (invocation.get("operation_binding_ref")
                           if invocation is not None else None)
            binding = version_metadata(
                str(binding_ref.get("version_id", "")),
                "operation_binding/v1") if isinstance(
                    binding_ref, Mapping) else None
            expected_material_producer = {
                "principal_ref": (invocation.get("principal_ref")
                                  if invocation is not None else None),
                "invocation_ref": invocation_ref,
                "operation_binding_ref": binding_ref,
                "firing_ref": (invocation.get("own_transition_firing_ref")
                               if invocation is not None else None),
            }
            expected_execution = {
                "task_ref": invocation.get("task_ref") if invocation else None,
                "branch_id": branch_id,
                "round_ref": (invocation.get("task_round_ref")
                              if invocation else None),
                "plan_ref": invocation.get("plan_ref") if invocation else None,
                "team_design_root_ref": (invocation.get(
                    "team_design_root_ref") if invocation else None),
                "net_ref": (invocation.get("net_instance_ref")
                            if invocation else None),
                "materialization_digest": (invocation.get(
                    "materialization_digest") if invocation else None),
                "budget_ref": (invocation.get("budget_witness_ref")
                               if invocation else None),
            }
            required_values: list[object] = [
                origin.get("primary_ref"), origin.get("secondary_ref"),
                metadata.get("lifetime_ref")]
            if invocation is not None:
                required_values.extend(invocation.get(name) for name in (
                    "task_ref", "task_branch_ref", "task_round_ref",
                    "net_instance_ref", "plan_ref", "team_design_root_ref",
                    "invocation_ref", "operation_binding_ref",
                    "authority_decision_ref", "budget_witness_ref",
                    "principal_ref", "operation_execution_lease_ref",
                    "own_transition_firing_ref", "activation_ref"))
            if binding is not None:
                for name in (
                        "input_binding_refs", "input_schema_refs",
                        "output_schema_refs", "readable_resource_refs",
                        "discoverable_resource_refs", "output_binding_refs",
                        "permitted_write_intent_factory_refs"):
                    required_values.extend(binding.get(name, []))
            required_authority = {
                canonical_json(value) for value in required_values
                if value is not None
            }
            expected_tool_authority = ({
                "operation_spec_ref": binding.get("operation_spec_ref"),
                "permitted_write_intent_factory_refs": binding.get(
                    "permitted_write_intent_factory_refs", []),
            } if binding is not None else None)
            if (invocation is None or binding is None
                    or material_producer != expected_material_producer
                    or execution != expected_execution
                    or not required_authority.issubset({
                        canonical_json(value) for value in authority_refs})
                    or catalog_digests.get("tool_catalog")
                    != expected_tool_authority):
                raise RegistryConflict(
                    "application resource dependency context/binding is incomplete")
        require_canonical_producer(
            {
                "entity_type": "resource_version/v1",
                "logical_id": str(item.logical_id),
                "version_id": str(item.version_id),
            },
            metadata["producer_ref"],
            publication_transaction=str(transaction_id),
        )
        if metadata.get("origin_kind") == "provider_response":
            primary = origin.get("primary_ref")
            secondary = origin.get("secondary_ref")
            extensions = metadata.get("extensions")
            response_facts = (
                extensions.get("registry.provider_response/v1")
                if isinstance(extensions, Mapping) else None)
            if (set(metadata) != _PROVIDER_RESPONSE_METADATA_ROOT_KEYS
                    or metadata.get("media_type") != "application/octet-stream"
                    or metadata.get("content_schema_ref") is not None
                    or metadata.get("summary") != "Provider response candidate"
                    or metadata.get("descriptors") != {
                        "content_role": "provider_response_candidate"}
                    or not isinstance(extensions, Mapping)
                    or set(extensions) != {"registry.provider_response/v1"}
                    or not isinstance(response_facts, Mapping)
                    or set(response_facts) != {
                        "finish_reason", "external_request_id",
                        "reconciliation_proof_ref"}):
                raise RegistryConflict(
                    "provider response metadata is not the closed non-content shape")
            attempt = version_metadata(
                str(primary.get("version_id", "")),
                "provider_attempt_spec/v1") if isinstance(primary, Mapping) else None
            call = version_metadata(
                str(secondary.get("version_id", "")),
                "llm_call_spec/v1") if isinstance(secondary, Mapping) else None
            producer = metadata.get("producer_ref")
            invocation = version_metadata(
                str(producer.get("version_id", "")),
                "invocation/v1") if isinstance(producer, Mapping) else None
            if (isinstance(primary, Mapping)
                    and isinstance(secondary, Mapping)
                    and isinstance(producer, Mapping)):
                require_canonical_provider_lineage(
                    "attempt_of_call", primary, secondary)
                require_canonical_provider_lineage(
                    "call_of_invocation", secondary, producer)
            if (not exact_ref_exists(primary, "provider_attempt_spec/v1")
                    or not exact_ref_exists(secondary, "llm_call_spec/v1")
                    or attempt is None or call is None or invocation is None
                    or not isinstance(producer, Mapping)
                    or producer.get("entity_type") != "invocation/v1"
                    or attempt.get("provider_attempt_id")
                    != primary.get("logical_id")
                    or attempt.get("provider_attempt_version_id")
                    != primary.get("version_id")
                    or attempt.get("llm_call_id")
                    != secondary.get("logical_id")
                    or attempt.get("llm_call_version_id")
                    != secondary.get("version_id")
                    or call.get("llm_call_id") != secondary.get("logical_id")
                    or call.get("llm_call_version_id")
                    != secondary.get("version_id")
                    or attempt.get("invocation_id")
                    != producer.get("logical_id")
                    or attempt.get("invocation_version_id")
                    != producer.get("version_id")
                    or call.get("invocation_id") != producer.get("logical_id")
                    or call.get("invocation_version_id")
                    != producer.get("version_id")
                    or invocation.get("invocation_ref") != producer
                    or attempt.get("context_digest")
                    != call.get("context_digest")
                    or call.get("context_digest")
                    != invocation.get("context_digest")
                    or any(attempt.get(key) != call.get(key) for key in (
                        "activation_id", "origin",
                        "accounting_parent_invocation_id",
                        "accounting_parent_invocation_version_id",
                        "finalization_scope",
                    ))
                    or attempt.get("reservation_class")
                    != call.get("budget_scope")):
                raise RegistryConflict(
                    "provider response provenance is not the exact attempt/call chain")
            validate_current_invocation_authority(
                invocation, boundary="provider completion")
            proof_ref = response_facts.get("reconciliation_proof_ref")
            matching_observations = [
                pending for pending in events
                if (pending.event_type
                    == "provider_attempt_submission_observed/v1"
                    and str(pending.payload.get("provider_attempt_id"))
                    == str(primary.get("logical_id"))
                    and pending.payload.get("response_resource_ref") == {
                        "resource_id": str(item.logical_id),
                        "resource_version_id": str(item.version_id),
                    })
            ]
            matching_completions = [
                pending for pending in events
                if pending.event_type in {
                    "provider_attempt_completed/v1",
                    "provider_attempt_reconciled_completed/v1",
                }
                and str(pending.payload.get("provider_attempt_id"))
                == str(primary.get("logical_id"))
                and str(pending.payload.get("response_version_id"))
                == str(item.version_id)
            ]
            if (len(matching_observations) != 1
                    or matching_completions
                    or proof_ref is not None
                    or not has_new_relation(
                        "attempt_produced_response",
                        str(primary.get("version_id")), str(item.version_id))):
                raise RegistryConflict(
                    "provider response must atomically persist one open observation")
            observation = matching_observations[0]
            finish_reason = response_facts.get("finish_reason")
            external_request_id = response_facts.get(
                "external_request_id")
            observation_material = {
                "provider_attempt_ref": primary,
                "llm_call_ref": secondary,
                "invocation_ref": producer,
                "dispatch_event_id": observation.payload.get(
                    "dispatch_event_id"),
                "response_resource_ref": {
                    "resource_id": str(item.logical_id),
                    "resource_version_id": str(item.version_id),
                },
                "response_size": item.size,
                "finish_reason": finish_reason,
                "external_request_id": external_request_id,
            }
            expected_observation_payload = {
                "provider_attempt_id": str(primary.get("logical_id")),
                "provider_attempt_version_id": str(
                    primary.get("version_id")),
                **observation_material,
            }
            facts_match = (
                response_facts.get("reconciliation_proof_ref") is None
                and dict(observation.payload)
                == expected_observation_payload)
            if not facts_match:
                raise RegistryConflict(
                    "provider response metadata differs from its observation fact")
            expected_lifetime = (
                invocation.get("authorization_lifetime_activation_ref")
                or invocation.get("activation_ref")
                or invocation.get("operation_execution_lease_ref"))
            expected_context = {
                "task_ref": invocation.get("task_ref"),
                "round_ref": invocation.get("task_round_ref"),
                "net_ref": invocation.get("net_instance_ref"),
                "producer_ref": invocation.get("invocation_ref"),
                "lifetime_ref": expected_lifetime,
            }
            if (metadata.get("task_ref") != expected_context["task_ref"]
                    or metadata.get("round_ref") != expected_context["round_ref"]
                    or metadata.get("net_ref") != expected_context["net_ref"]
                    or metadata.get("producer_ref")
                    != expected_context["producer_ref"]
                    or metadata.get("lifetime_ref")
                    != expected_context["lifetime_ref"]
                    or metadata.get("branch_id") != branch_id
                    or not isinstance(metadata.get("task_ref"), Mapping)
                    or metadata["task_ref"].get("logical_id") != str(task_id)
                    or not isinstance(metadata.get("round_ref"), Mapping)
                    or metadata["round_ref"].get("logical_id")
                    != str(task_round_id)
                    or not isinstance(metadata.get("net_ref"), Mapping)
                    or metadata["net_ref"].get("logical_id")
                    != str(net_instance_id)
                    or item.producer_invocation_id
                    != TypedId.parse(
                        str(producer.get("logical_id")), expected="invocation")):
                raise RegistryConflict(
                    "provider response metadata differs from transaction authority")

            expected_resource_id = _stable_id_text(
                "resource", task_id, task_round_id, net_instance_id,
                "provider_response", primary.get("version_id"),
                secondary.get("version_id"))
            expected_version_id = _stable_id_text(
                "resource_version", task_id, idempotency_key)
            if (metadata.get("command_digest") != idempotency_key
                    or str(item.logical_id) != expected_resource_id
                    or str(item.version_id) != expected_version_id
                    or item.storage_locator
                    != f"registry-object:{item.version_id}"
                    or item.schema_ref != "registry_v1/resource_version/v1"):
                raise RegistryConflict(
                    "provider response identity or CAS descriptor differs from its command")

            response_relations = [
                relation for relation in relations
                if str(getattr(relation.source, "version_id", ""))
                == str(item.version_id)
                or str(getattr(relation.target, "version_id", ""))
                == str(item.version_id)
            ]
            expected_relation_ids = {
                "produced_by": _stable_id_text(
                    "relation", idempotency_key, item.version_id,
                    "produced_by"),
                "attempt_produced_response": _stable_id_text(
                    "relation", idempotency_key, "attempt-response"),
            }
            produced_by = [
                relation for relation in response_relations
                if relation.relation_type == "produced_by"]
            attempt_response = [
                relation for relation in response_relations
                if relation.relation_type == "attempt_produced_response"]
            proof_lineage = [
                relation for relation in response_relations
                if relation.relation_type == "derived_from"]
            expected_relation_count = 3 if proof_ref is not None else 2
            proof_lineage_invalid = (
                (proof_ref is None and proof_lineage != [])
                or (proof_ref is not None and (
                    len(proof_lineage) != 1
                    or str(proof_lineage[0].relation_id)
                    != _stable_id_text(
                        "relation", idempotency_key, item.version_id,
                        "derived_from", 0)
                    or _ref_json(proof_lineage[0].source) != {
                        "entity_type": "resource_version/v1",
                        "entity_id": str(item.logical_id),
                        "version_id": str(item.version_id),
                    }
                    or _ref_json(proof_lineage[0].target) != {
                        "entity_type": "resource_version/v1",
                        "entity_id": proof_ref.get("logical_id"),
                        "version_id": proof_ref.get("version_id"),
                    }
                    or proof_lineage[0].strength != "strong"
                    or dict(proof_lineage[0].metadata))))
            if (len(response_relations) != expected_relation_count
                    or len(produced_by) != 1
                    or len(attempt_response) != 1
                    or proof_lineage_invalid
                    or str(produced_by[0].relation_id)
                    != expected_relation_ids["produced_by"]
                    or produced_by[0].source.entity_type != "resource_version/v1"
                    or str(getattr(produced_by[0].source, "entity_id", ""))
                    != str(item.logical_id)
                    or str(getattr(produced_by[0].source, "version_id", ""))
                    != str(item.version_id)
                    or _ref_json(produced_by[0].target) != {
                        "entity_type": producer.get("entity_type"),
                        "entity_id": producer.get("logical_id"),
                        "version_id": producer.get("version_id"),
                    }
                    or produced_by[0].strength != "strong"
                    or dict(produced_by[0].metadata)
                    or str(attempt_response[0].relation_id)
                    != expected_relation_ids["attempt_produced_response"]
                    or _ref_json(attempt_response[0].source) != {
                        "entity_type": primary.get("entity_type"),
                        "entity_id": primary.get("logical_id"),
                        "version_id": primary.get("version_id"),
                    }
                    or attempt_response[0].target.entity_type
                    != "resource_version/v1"
                    or str(getattr(attempt_response[0].target, "entity_id", ""))
                    != str(item.logical_id)
                    or str(getattr(attempt_response[0].target, "version_id", ""))
                    != str(item.version_id)
                    or attempt_response[0].strength != "strong"
                    or dict(attempt_response[0].metadata)):
                raise RegistryConflict(
                    "provider response relations differ from the approved provenance facts")
            if (observation.criticality != "authoritative"
                    or observation.stream_id
                    != f"provider-attempt:{primary.get('logical_id')}"
                    or observation.aggregate_id
                    != str(primary.get("logical_id"))
                    or observation.aggregate_type != "provider_attempt"
                    or observation.idempotency_key != idempotency_key
                    or observation.command_id != idempotency_key
                    or observation.payload_schema_ref
                    != "registry_v1/provider_attempt_submission_observed/v1"
                    or not observation.task_control
                    or observation.producer_principal != "framework"
                    or observation.producer_invocation_id
                    != item.producer_invocation_id
                    or observation.causation_event_id is not None
                    or observation.parent_event_ids
                    or observation.occurred_at is not None):
                raise RegistryConflict(
                    "provider response observation differs from its transaction command")
            require_canonical_attempt_response_proposal(
                primary,
                {
                    "entity_type": "resource_version/v1",
                    "logical_id": str(item.logical_id),
                    "version_id": str(item.version_id),
                },
                metadata,
            )

            publications = [
                pending for pending in events
                if pending.event_type == "object_version_published/v1"
                and str(pending.payload.get("version_id"))
                == str(item.version_id)
            ]
            if len(publications) != 1:
                raise RegistryConflict(
                    "provider response lacks one exact publication fact")
            publication = publications[0]
            expected_publication_payload = {
                "logical_id": str(item.logical_id),
                "version_id": str(item.version_id),
                "object_type": item.object_type,
                "size": item.size,
                "media_type": item.media_type,
                "schema_ref": item.schema_ref,
                "storage_locator": item.storage_locator,
                "metadata": metadata,
            }
            if (dict(publication.payload) != expected_publication_payload
                    or publication.criticality != "authoritative"
                    or publication.stream_id != f"object:{item.logical_id}"
                    or publication.aggregate_id != str(item.logical_id)
                    or publication.aggregate_type != item.object_type
                    or publication.idempotency_key != idempotency_key
                    or publication.command_id != idempotency_key
                    or publication.payload_schema_ref
                    != "registry_v1/object_version_published/v1"
                    or publication.task_control
                    or publication.producer_invocation_id
                    != item.producer_invocation_id):
                raise RegistryConflict(
                    "provider response publication differs from its immutable envelope")
        elif metadata.get("origin_kind") == "provider_raw_response":
            primary = origin.get("primary_ref")
            secondary = origin.get("secondary_ref")
            extensions = metadata.get("extensions")
            raw_facts = (extensions.get("registry.provider_raw_response/v2")
                         if isinstance(extensions, Mapping) else None)
            attempt = (version_metadata(
                str(primary.get("version_id", "")),
                "provider_attempt_spec/v1")
                if isinstance(primary, Mapping) else None)
            call = (version_metadata(
                str(secondary.get("version_id", "")), "llm_call_spec/v2")
                if isinstance(secondary, Mapping) else None)
            producer = metadata.get("producer_ref")
            invocation = (version_metadata(
                str(producer.get("version_id", "")), "invocation/v1")
                if isinstance(producer, Mapping) else None)
            observations = [pending for pending in events
                if pending.event_type
                == "provider_attempt_submission_observed/v1"
                and pending.payload.get("response_resource_ref") == {
                    "resource_id": str(item.logical_id),
                    "resource_version_id": str(item.version_id)}]
            related = [relation for relation in relations
                if str(getattr(relation.source, "version_id", ""))
                == str(item.version_id)
                or str(getattr(relation.target, "version_id", ""))
                == str(item.version_id)]
            if (set(metadata) != _PROVIDER_RESPONSE_METADATA_ROOT_KEYS
                    or metadata.get("media_type")
                    != "application/octet-stream"
                    or metadata.get("content_schema_ref") is not None
                    or metadata.get("content_schema_authority_ref") is not None
                    or metadata.get("summary") != "Raw provider response"
                    or metadata.get("descriptors") != {
                        "content_role": "provider_raw_response_evidence"}
                    or not isinstance(raw_facts, Mapping)
                    or set(raw_facts) != {
                        "status_code", "external_request_id"}
                    or not isinstance(primary, Mapping)
                    or not isinstance(secondary, Mapping)
                    or attempt is None or call is None or invocation is None
                    or primary.get("entity_type")
                    != "provider_attempt_spec/v1"
                    or secondary.get("entity_type") != "llm_call_spec/v2"
                    or attempt.get("llm_call_ref") != secondary
                    or attempt.get("invocation_ref") != producer
                    or call.get("invocation_ref") != producer
                    or call.get("operation_binding_ref")
                    != attempt.get("operation_binding_ref")
                    or call.get("context_digest")
                    != attempt.get("context_digest")
                    or len(observations) != 1
                    or len(related) != 1
                    or related[0].relation_type != "produced_by"
                    or _ref_json(related[0].target) != {
                        "entity_type": producer.get("entity_type"),
                        "entity_id": producer.get("logical_id"),
                        "version_id": producer.get("version_id")}
                    or metadata.get("command_digest")
                    != canonical_text(metadata["dependency_material"])
                    or str(item.logical_id) != _stable_id_text(
                        "resource", task_id, task_round_id, net_instance_id,
                        "provider_raw_response", primary.get("version_id"),
                        secondary.get("version_id"))
                    or str(item.version_id) != _stable_id_text(
                        "resource_version", task_id, idempotency_key,
                        metadata.get("command_digest"))):
                raise RegistryConflict(
                    "raw provider response lacks exact v2 publication closure")
            observation = observations[0]
            material = {
                "provider_attempt_ref": primary,
                "llm_call_ref": secondary,
                "invocation_ref": producer,
                "dispatch_event_id": observation.payload.get(
                    "dispatch_event_id"),
                "response_resource_ref": {
                    "resource_id": str(item.logical_id),
                    "resource_version_id": str(item.version_id)},
                "response_size": item.size,
                "finish_reason": None,
                "external_request_id": raw_facts.get(
                    "external_request_id"),
            }
            if dict(observation.payload) != {
                    "provider_attempt_id": primary.get("logical_id"),
                    "provider_attempt_version_id": primary.get("version_id"),
                    **material}:
                raise RegistryConflict(
                    "raw provider response observation differs from exact bytes")

def validate_resource_event(context, pending):
    db = context.db
    events = context.events
    payload = pending.payload
    event_type = pending.event_type
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    has_relation = context.has_relation
    object_metadata = context.object_metadata
    version_exists = context.version_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    if event_type == "capability_issued/v1":
        grant_id = str(payload["grant_id"])
        grant = object_metadata(grant_id, "capability_grant/v1")
        if (pending.aggregate_id != grant_id or grant is None
                or dict(grant) != dict(payload)):
            raise RegistryConflict("capability issue does not match its grant object")
        invocation = object_metadata(
            str(payload["target_invocation_id"]), "invocation/v1")
        activation_ref = ((invocation.get("authorization_lifetime_activation_ref")
                           or invocation.get("activation_ref"))
                          if invocation is not None else None)
        operation_binding_ref = payload.get("operation_binding_ref")
        invocation_ref = (invocation.get("invocation_ref")
                          if invocation is not None else None)
        lifetime_ref = payload.get("lifetime_ref")
        expected_lifetime = ((invocation.get("own_transition_firing_ref")
                              or invocation_ref)
                             if invocation is not None else None)
        if (invocation is None
                or not exact_ref_exists(invocation_ref, "invocation/v1")
                or invocation_ref.get("logical_id") != payload["target_invocation_id"]
                or lifetime_ref != expected_lifetime
                or not exact_ref_exists(lifetime_ref)
                or (payload.get("activation_id") is not None and (
                    not exact_ref_exists(activation_ref)
                    or activation_ref.get("logical_id") != payload["activation_id"]))
                or operation_binding_ref
                != invocation.get("operation_binding_ref")
                or not exact_ref_exists(
                    operation_binding_ref, "operation_binding/v1")):
            raise RegistryConflict(
                "capability target invocation/lifetime/optional activation/binding is invalid")
        if invocation.get("origin") == "petri_operation":
            firing = (version_metadata(str(lifetime_ref.get("version_id")),
                                       "transition_firing/v1")
                      if exact_ref_exists(lifetime_ref, "transition_firing/v1")
                      else None)
            admissions = [json.loads(row["payload_json"]) for row in db.execute(
                "SELECT payload_json FROM events WHERE aggregate_id=? "
                "AND event_type='firing_admitted/v1'",
                (str(lifetime_ref.get("logical_id")),)).fetchall()]
            admissions.extend(item.payload for item in events
                              if item.event_type == "firing_admitted/v1"
                              and item.aggregate_id == lifetime_ref.get("logical_id"))
            if (firing is None or firing.get("transition_firing_ref") != lifetime_ref
                    or len(admissions) != 1
                    or admissions[0].get("transition_firing_ref") != lifetime_ref
                    or admissions[0].get("invocation_ref") != invocation_ref
                    or firing.get("operation_binding_ref") != operation_binding_ref):
                raise RegistryConflict("capability lifetime is not the target's exact registered firing")
        elif lifetime_ref != invocation_ref:
            raise RegistryConflict("non-Petri capability lifetime must be its exact invocation")
        if any(not version_exists(str(version_id), "resource_version/v1")
               for version_id in payload["resource_version_ids"]):
            raise RegistryConflict("capability references an unknown resource version")

    if event_type in {
            "resource_address_bound/v1", "resource_address_unbound/v1"}:
        binding_ref = payload.get("binding_ref", {})
        binding = version_metadata(
            str(binding_ref.get("version_id", "")),
            "resource_address_binding/v1")
        if (binding is None
                or not version_exists(
                    str(binding_ref.get("version_id", "")),
                    "resource_address_binding/v1")
                or binding.get("scope_ref")
                != payload.get("address", {}).get("scope_ref")
                or binding.get("opaque_name")
                != payload.get("address", {}).get("opaque_name")
                or binding.get("previous_binding_ref")
                != payload.get("previous_binding_ref")
                or binding.get("resulting_stream_sequence")
                != payload.get("resulting_stream_sequence")):
            raise RegistryConflict(
                "resource-address fact does not match its immutable binding")
        if event_type == "resource_address_bound/v1":
            resource_ref = payload.get("resource_ref", {})
            if (binding.get("lifecycle_state") != "bound"
                    or binding.get("resource_ref") != resource_ref
                    or not version_exists(
                        str(resource_ref.get("resource_version_id", "")),
                        "resource_version/v1")):
                raise RegistryConflict("address bind lacks an exact resource")
        elif (binding.get("lifecycle_state") != "tombstoned"
              or binding.get("resource_ref") is not None):
            raise RegistryConflict("address unbind is not an immutable tombstone")

    if event_type == "resource_delivery_prepared/v1":
        delivery_ref = payload.get("delivery_ref", {})
        delivery = version_metadata(
            str(delivery_ref.get("version_id", "")),
            "resource_delivery/v1")
        resource_ref = payload.get("resource_ref", {})
        if (delivery is None or delivery.get("state") != "prepared"
                or not version_exists(
                    str(delivery_ref.get("version_id", "")),
                    "resource_delivery/v1")
                or delivery.get("resource_ref") != resource_ref
                or delivery.get("authorization_ref")
                != payload.get("authorization_ref")
                or delivery.get("context_ref") != payload.get("context_ref")
                or delivery.get("boundary") != payload.get("boundary")
                or not version_exists(
                    str(resource_ref.get("resource_version_id", "")),
                    "resource_version/v1")):
            raise RegistryConflict(
                "delivery prepare does not close over exact resource authority")

    if event_type == "resource_release_authorized/v1":
        witness_ref = payload.get("witness_ref", {})
        witness = version_metadata(
            str(witness_ref.get("version_id", "")),
            "resource_release_witness/v1")
        if (witness is None
                or not version_exists(
                    str(witness_ref.get("version_id", "")),
                    "resource_release_witness/v1")
                or witness.get("delivery_ref") != payload.get("delivery_ref")
                or witness.get("resource_ref") != payload.get("resource_ref")
                or witness.get("resource_digest") != payload.get("resource_digest")
                or witness.get("boundary") != payload.get("boundary")
                or witness.get("expires_at") != payload.get("expires_at")):
            raise RegistryConflict(
                "release authorization does not match its one-use witness")

    if event_type in {
            "resource_delivery_acknowledged/v1",
            "resource_delivery_failed/v1", "resource_delivery_unknown/v1"}:
        terminal_ref = payload.get("terminal_delivery_ref", {})
        terminal = version_metadata(
            str(terminal_ref.get("version_id", "")),
            "resource_delivery/v1")
        receipt_ref = payload.get("boundary_receipt_ref", {})
        if (terminal is None
                or not version_exists(
                    str(terminal_ref.get("version_id", "")),
                    "resource_delivery/v1")
                or terminal.get("state") != payload.get("outcome")
                or terminal.get("boundary_receipt_ref") != receipt_ref
                or not version_exists(
                    str(receipt_ref.get("version_id", "")),
                    "delivery_boundary_receipt/v1")):
            raise RegistryConflict(
                "delivery terminal fact lacks exact immutable evidence")

    if event_type == "observed_read/v1":
        invocation_ref = payload.get("actor_invocation_ref", {})
        invocation_id = str(invocation_ref.get("logical_id", ""))
        invocation_version = str(invocation_ref.get("version_id", ""))
        invocation = object_metadata(invocation_id, "invocation/v1")
        resource_ref = payload.get("resource_ref", {})
        resource_version = str(resource_ref.get("resource_version_id", ""))
        witness_ref = payload.get("witness_ref", {})
        receipt_ref = payload.get("boundary_receipt_ref", {})
        delivery_id = str(payload.get("delivery_id", ""))
        matching_terminal = any(
            item.event_type == "resource_delivery_acknowledged/v1"
            and item.aggregate_id == delivery_id
            and item.payload.get("witness_ref") == witness_ref
            and item.payload.get("boundary_receipt_ref") == receipt_ref
            for item in events)
        if (invocation is None
                or invocation.get("invocation_ref") != invocation_ref
                or not version_exists(invocation_version, "invocation/v1")
                or invocation.get("origin") != payload.get("origin")
                or payload.get("transaction_id") != str(transaction_id)
                or not version_exists(resource_version, "resource_version/v1")
                or not version_exists(
                    str(witness_ref.get("version_id", "")),
                    "resource_release_witness/v1")
                or not version_exists(
                    str(receipt_ref.get("version_id", "")),
                    "delivery_boundary_receipt/v1")
                or not matching_terminal
                or not has_relation(
                    "read_by", resource_version, invocation_version)):
            raise RegistryConflict(
                "observed read is not atomically closed over its exact delivery evidence")
        if (payload.get("task_ref") != invocation.get("task_ref")
                or payload.get("task_branch_ref")
                != invocation.get("task_branch_ref")
                or payload.get("task_round_ref")
                != invocation.get("task_round_ref")
                or payload.get("net_ref") != invocation.get("net_instance_ref")
                or payload.get("accounting_parent_ref")
                != invocation.get("accounting_parent_invocation_ref")):
            raise RegistryConflict(
                "observed read attribution differs from the immutable invocation")
        activation_ref = payload.get("activation_ref")
        if activation_ref is not None and (
                activation_ref != (invocation.get("activation_ref")
                                   or invocation.get("authorization_lifetime_activation_ref"))
                or not exact_ref_exists(activation_ref)):
            raise RegistryConflict(
                "observed read activation differs from the immutable invocation")

    if event_type in {
            "capability_activated/v1", "capability_allowed/v1",
            "capability_denied/v1", "capability_revoked/v1"}:
        grant_id = str(payload["grant_id"])
        if (pending.aggregate_id != grant_id
                or object_metadata(grant_id, "capability_grant/v1") is None):
            raise RegistryConflict("capability transition has no exact grant object")

__all__ = (
    "validate_petri_firing_resource_access",
    "validate_resource_event",
    "validate_resource_objects",
    "validate_waiting_resource_grant_atomicity",
)
