"""Firing validation over an already-open EventStore transaction."""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from typing import Any, Mapping, Sequence

from ... import event_store as facade
from ...identities import TypedId
from ...models import PendingEvent, PreparedObject, TypedRelation
from . import agent_loop as agent_loop_validation
from . import provider as provider_validation


def validate_firing_resource_settlement_atomicity(
        db: sqlite3.Connection, objects: Sequence[PreparedObject],
        events: Sequence[PendingEvent]) -> None:
    """Require every live dynamic exact-version use to seal on settlement."""
    from ...event_store import RegistryConflict

    settlements = tuple(
        event for event in events
        if event.event_type == "transition_firing_settled/v1")
    if not settlements:
        return
    proposed = {
        str(item.logical_id): dict(item.metadata)
        for item in objects
        if item.object_type == "resource_access_lifecycle/v1"
        and item.metadata.get("state") == "sealed"
    }
    for event in settlements:
        completion_ref = event.payload.get("firing_completion_ref")
        firing_ref = event.payload.get("transition_firing_ref")
        if not isinstance(completion_ref, Mapping):
            raise RegistryConflict(
                "firing resource settlement lacks completion authority")
        rows = db.execute(
            "SELECT o.logical_id,o.metadata_json FROM objects o WHERE "
            "o.object_type='resource_access_lifecycle/v1' AND o.rowid=("
            "SELECT MAX(h.rowid) FROM objects h WHERE h.object_type="
            "'resource_access_lifecycle/v1' AND h.logical_id=o.logical_id)"
        ).fetchall()
        active: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            value = json.loads(str(row["metadata_json"]))
            if (value.get("transition_firing_ref") == firing_ref
                    and value.get("state") != "sealed"):
                active[str(row["logical_id"])] = value
        for logical_id, prior in active.items():
            closed = proposed.get(logical_id)
            if (closed is None
                    or closed.get("transition_firing_ref") != firing_ref
                    or closed.get("invocation_ref")
                    != prior.get("invocation_ref")
                    or closed.get("request_ref") != prior.get("request_ref")
                    or closed.get("logical_resource_id")
                    != prior.get("logical_resource_id")
                    or closed.get("lock_resource_ref")
                    != prior.get("lock_resource_ref")
                    or closed.get("requested_resource_ref")
                    != prior.get("requested_resource_ref")
                    or closed.get("settlement_ref") != completion_ref
                    or closed.get("settlement_disposition") != "success"
                    or closed.get("seal_ref") is None):
                raise RegistryConflict(
                    "firing settlement leaves a dynamic resource use open")

def active_firing_claim_delta_metadata(
        context,
        firing: Mapping[str, Any],
        owning_firing_version_id: str,
) -> Mapping[str, Any]:
    db = context.db
    net_instance_id = context.net_instance_id
    RegistryConflict = facade.RegistryConflict
    """Read one active firing's exact temporary claim delta only."""

    firing_ref = firing.get("transition_firing_ref")
    delta_ref = firing.get("claim_marking_delta_ref")
    net_ref = firing.get("net_instance_ref")
    if (not isinstance(firing_ref, Mapping)
            or not isinstance(delta_ref, Mapping)
            or not isinstance(net_ref, Mapping)
            or firing_ref.get("entity_type") != "transition_firing/v1"
            or firing_ref.get("version_id")
            != owning_firing_version_id
            or delta_ref.get("entity_type") != "marking_delta/v1"
            or not delta_ref.get("logical_id")
            or not delta_ref.get("version_id")
            or net_ref.get("entity_type") != "net_instance/v1"
            or not net_ref.get("version_id")
            or net_ref.get("logical_id") != str(net_instance_id)):
        raise RegistryConflict(
            "active firing claim authority is not exact")
    row = db.execute(
        "SELECT p.firing_logical_id,p.net_version_id,o.logical_id,"
        "o.metadata_json FROM firing_publications p "
        "JOIN firing_temporary_members m "
        "ON m.firing_version_id=p.firing_version_id "
        "AND m.member_kind='object' AND m.member_identity=? "
        "JOIN objects o ON o.version_id=m.member_identity "
        "AND o.object_type='marking_delta/v1' "
        "WHERE p.firing_version_id=? AND p.state='PROVISIONAL'",
        (str(delta_ref["version_id"]), owning_firing_version_id),
    ).fetchone()
    if (row is None
            or row["firing_logical_id"] != firing_ref.get("logical_id")
            or row["net_version_id"] != net_ref.get("version_id")
            or row["logical_id"] != delta_ref.get("logical_id")):
        raise RegistryConflict(
            "active firing claim delta is not its exact temporary member")
    try:
        delta = json.loads(str(row["metadata_json"]))
    except (TypeError, ValueError) as exc:
        raise RegistryConflict(
            "active firing claim delta metadata is malformed") from exc
    if (not isinstance(delta, Mapping)
            or delta.get("marking_delta_ref") != delta_ref
            or delta.get("net_instance_ref") != net_ref
            or delta.get("transition_firing_refs") != [firing_ref]
            or delta.get("phase") != "claim"):
        raise RegistryConflict(
            "active firing claim delta does not match its exact claim")
    return delta

def firing_claim_sets(
        context,
        firing: Mapping[str, Any],
        *, owning_firing_version_id: str | None = None,
) -> tuple[set[str], set[str]]:
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    active_firing_claim_delta_metadata = partial(globals()["active_firing_claim_delta_metadata"], context)
    raw_claimed = firing.get("claimed_input_version_ids", [])
    claimed = set(raw_claimed)
    delta_ref = firing.get("claim_marking_delta_ref")
    if (len(claimed) != len(raw_claimed)
            or not isinstance(delta_ref, Mapping)):
        raise RegistryConflict(
            "firing claim or claim delta reference is malformed")
    delta = (
        active_firing_claim_delta_metadata(
            firing, owning_firing_version_id)
        if owning_firing_version_id is not None else
        version_metadata(
            str(delta_ref.get("version_id", "")),
            "marking_delta/v1"))
    if not isinstance(delta, Mapping) or delta.get("phase") != "claim":
        raise RegistryConflict(
            "firing claim delta is not a claim-phase delta")
    raw_consumed = (
        delta.get("consumed_refs", [])
        if isinstance(delta, Mapping) else None)
    if not isinstance(raw_consumed, list):
        raise RegistryConflict(
            "firing claim delta lacks consumed refs")
    consumed = {
        str(value.get("version_id", ""))
        for value in raw_consumed if isinstance(value, Mapping)}
    if (len(consumed) != len(raw_consumed)
            or not consumed.issubset(claimed)):
        raise RegistryConflict(
            "firing claim delta exceeds its exact inputs")
    return claimed, consumed

def validate_firing_event(context, pending):
    db = context.db
    event_store = context.event_store
    events = context.events
    native_resume_superseded_firing_ids = context.native_resume_superseded_firing_ids
    net_instance_id = context.net_instance_id
    new_by_version = context.new_by_version
    objects = context.objects
    pending_firing_claims = context.pending_firing_claims
    persisted_member_visible = context.persisted_member_visible
    relations = context.relations
    task_id = context.task_id
    task_round_id = context.task_round_id
    transaction_id = context.transaction_id
    transaction_writer_epoch = context.transaction_writer_epoch
    payload = pending.payload
    event_type = pending.event_type
    exact_ref_exists = context.exact_ref_exists
    has_new_relation = context.has_new_relation
    object_metadata = context.object_metadata
    version_exists = context.version_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _CANONICAL_EVENT_SQL = facade._CANONICAL_EVENT_SQL
    canonical_json = facade.canonical_json
    firing_claim_sets = partial(globals()["firing_claim_sets"], context)
    validate_current_invocation_authority = partial(
        agent_loop_validation.validate_current_invocation_authority, context)
    validate_provider_candidate_output = partial(
        provider_validation.validate_provider_candidate_output, context)
    if event_type == "net_adopted/v1" and "operation_revision" in payload:
        witness = payload["operation_revision"]
        successes = [e for e in events if e.event_type == "transition_firing_settled/v1"
            and e.payload.get("transition_firing_ref") == witness["source_firing_ref"]
            and e.payload.get("operation_result_ref") == witness["operation_result_ref"]
            and e.payload.get("successor_checkpoint_ref") == witness["successor_checkpoint_ref"]]
        if len(successes) != 1 or any(k.startswith(("owner_", "growth_", "repair_")) for k in payload):
            raise RegistryConflict("operation adoption requires its SAMEtransaction ordinary registered Success")
    if event_type == "net_adopted/v1" and "owner_command_ref" in payload:
        from ...owner_adoption import validate_owner_adoption
        validate_owner_adoption(event_store, event_store.catalog, db, task_id=task_id,
            transaction_id=transaction_id, pending=pending, objects=objects, events=events,
            relations=relations)
    if event_type == "firing_admitted/v1":
        firing_ref = payload.get("transition_firing_ref")
        if not isinstance(firing_ref, Mapping):
            raise RegistryConflict(
                "firing admission requires an exact firing ref")
        firing_id = str(firing_ref.get("logical_id", ""))
        firing = object_metadata(firing_id, "transition_firing/v1")
        if (firing is None or pending.aggregate_id != firing_id
                or firing.get("transition_firing_ref") != firing_ref
                or firing.get("firing_admission_ref")
                != payload.get("firing_admission_ref")
                or firing.get("claim_marking_delta_ref")
                != payload.get("claim_marking_delta_ref")
                or firing.get("logical_tau") != payload.get("logical_tau")
                or firing.get("net_instance_ref", {}).get("logical_id")
                != str(net_instance_id)):
            raise RegistryConflict(
                "firing admission does not match its immutable claim")
        requested, requested_consumed = firing_claim_sets(firing)
        active_net = db.execute(
            "SELECT e.payload_json FROM events e WHERE e.task_id=? "
            "AND e.event_type='net_adopted/v1' AND "
            f"{_CANONICAL_EVENT_SQL} "
            "ORDER BY e.task_control_sequence DESC LIMIT 1",
            (str(task_id),)).fetchone()
        active_payload = (json.loads(active_net["payload_json"])
                          if active_net is not None else None)
        active_ref = (active_payload.get("net_instance_ref")
                      if active_payload is not None else None)
        if (active_payload is None
                or active_ref != firing.get("net_instance_ref")):
            raise RegistryConflict(
                "firing admission requires the current adopted net")
        admission_checkpoint = firing.get(
            "admission_marking_checkpoint_ref", {})
        checkpoint_version = admission_checkpoint.get("version_id")
        marking_head = db.execute(
            "SELECT e.payload_json FROM events e WHERE e.task_id=? "
            "AND e.net_instance_id=? "
            "AND e.event_type='marking_checkpoint_committed/v1' AND "
            f"{_CANONICAL_EVENT_SQL} "
            "ORDER BY task_control_sequence DESC LIMIT 1",
            (str(task_id), str(net_instance_id))).fetchone()
        marking_payload = (json.loads(marking_head["payload_json"])
                           if marking_head is not None else None)
        checkpoint_metadata = version_metadata(
            str(checkpoint_version), "marking_checkpoint/v1")
        token_values = (checkpoint_metadata.get("token_refs", [])
                        if isinstance(checkpoint_metadata, Mapping) else [])
        present = {
            str(value.get("version_id")) for value in token_values
            if isinstance(value, Mapping)}
        current_checkpoint = (
            marking_payload.get("checkpoint_ref")
            if marking_payload is not None else None)
        if (marking_payload is None
                or current_checkpoint != admission_checkpoint
                or not requested.issubset(present)):
            raise RegistryConflict(
                "firing admission does not claim the current live marking")
        from ...static_lease_claims import validate_static_lease_claim
        validate_static_lease_claim(context, firing, checkpoint_metadata)
        settled_rows = db.execute(
            "SELECT o.metadata_json,e.payload_json FROM objects o JOIN events e "
            "ON e.aggregate_id=o.logical_id "
            "AND e.event_type='transition_firing_settled/v1' "
            "WHERE o.object_type='transition_firing/v1' "
            "AND e.net_instance_id=? AND "
            f"{_CANONICAL_EVENT_SQL}",
            (str(net_instance_id),)).fetchall()
        already_consumed: set[str] = set()
        for row in settled_rows:
            settled_firing = json.loads(row["metadata_json"])
            settlement = json.loads(row["payload_json"])
            successor_ref = settlement.get(
                "successor_checkpoint_ref")
            successor = (
                version_metadata(
                    str(successor_ref.get("version_id", "")),
                    "marking_checkpoint/v1")
                if isinstance(successor_ref, Mapping) else None)
            raw_successor_tokens = (
                successor.get("token_refs", [])
                if isinstance(successor, Mapping) else None)
            if not isinstance(raw_successor_tokens, list):
                raise RegistryConflict(
                    "settled firing lacks its exact successor marking")
            successor_versions = {
                str(value.get("version_id", ""))
                for value in raw_successor_tokens
                if isinstance(value, Mapping)}
            if len(successor_versions) != len(raw_successor_tokens):
                raise RegistryConflict(
                    "settled firing successor repeats a token version")
            settled_claimed = set(settled_firing.get(
                "claimed_input_version_ids", []))
            already_consumed.update(
                settled_claimed - successor_versions)
        reused = sorted(requested & already_consumed)
        if reused:
            raise RegistryConflict(
                f"firing reclaims already-consumed token versions: {reused}")
        active_rows = db.execute(
            "SELECT publication.firing_version_id,"
            "publication.firing_logical_id,o.logical_id,o.metadata_json,"
            "admission.logical_id AS admission_logical_id,"
            "admission.version_id AS admission_version_id,"
            "admission.metadata_json AS admission_metadata_json,"
            "lease.logical_id AS lease_logical_id,"
            "lease.version_id AS lease_version_id,"
            "lease.metadata_json AS lease_metadata_json "
            "FROM firing_publications publication JOIN objects o "
            "ON o.version_id=publication.firing_version_id "
            "AND o.object_type='transition_firing/v1' "
            "LEFT JOIN objects admission ON admission.version_id="
            "json_extract(o.metadata_json,"
            "'$.firing_admission_ref.version_id') "
            "AND admission.object_type='firing_admission/v1' "
            "LEFT JOIN objects lease ON lease.version_id="
            "json_extract(admission.metadata_json,"
            "'$.operation_execution_lease_ref.version_id') "
            "AND lease.object_type='operation_execution_lease/v1' "
            "WHERE publication.state='PROVISIONAL' "
            "AND publication.net_version_id=?",
            (str(firing.get("net_instance_ref", {}).get(
                "version_id", "")),)).fetchall()
        active_claims: list[
            tuple[str, set[str], set[str]]] = []
        for row in active_rows:
            firing_id = str(row["logical_id"])
            if firing_id in native_resume_superseded_firing_ids:
                continue
            if (row["admission_metadata_json"] is None
                    or row["lease_metadata_json"] is None):
                raise RegistryConflict(
                    "active firing lacks exact admission/lease metadata")
            firing_metadata = json.loads(row["metadata_json"])
            admission_metadata = json.loads(
                row["admission_metadata_json"])
            lease_metadata = json.loads(row["lease_metadata_json"])
            admission_ref = firing_metadata.get("firing_admission_ref")
            lease_ref = admission_metadata.get(
                "operation_execution_lease_ref")
            if (not isinstance(admission_ref, Mapping)
                    or admission_ref.get("entity_type")
                    != "firing_admission/v1"
                    or admission_ref.get("logical_id")
                    != row["admission_logical_id"]
                    or admission_ref.get("version_id")
                    != row["admission_version_id"]
                    or not isinstance(lease_ref, Mapping)
                    or lease_ref.get("entity_type")
                    != "operation_execution_lease/v1"
                    or lease_ref.get("logical_id")
                    != row["lease_logical_id"]
                    or lease_ref.get("version_id")
                    != row["lease_version_id"]):
                raise RegistryConflict(
                    "active firing lacks exact admission/lease metadata")
            epochs: list[int] = []
            for metadata, label in (
                    (admission_metadata, "firing admission"),
                    (lease_metadata, "operation lease")):
                value = metadata.get("writer_fencing_epoch")
                if isinstance(value, bool):
                    raise RegistryConflict(
                        f"active {label} writer epoch is malformed")
                try:
                    epoch = int(value)
                except (TypeError, ValueError) as exc:
                    raise RegistryConflict(
                        f"active {label} writer epoch is malformed") from exc
                if epoch < 0 or epoch > transaction_writer_epoch:
                    raise RegistryConflict(
                        f"active {label} writer epoch is invalid")
                epochs.append(epoch)
            if any(epoch < transaction_writer_epoch for epoch in epochs):
                continue
            active_claimed, active_consumed = firing_claim_sets(
                firing_metadata,
                owning_firing_version_id=str(
                    row["firing_version_id"]))
            active_claims.append((
                firing_id, active_claimed, active_consumed))
        active_claims += pending_firing_claims
        for active_id, claimed, consumed in active_claims:
            overlap = sorted(
                (requested_consumed & claimed)
                | (requested & consumed))
            if overlap:
                raise RegistryConflict(
                    f"firing conflicts with active claim {active_id}: {overlap}")
        pending_firing_claims.append((
            firing_id, requested, requested_consumed))

    growth_recovery_types = {
        "invocation_superseded_by_growth_recovery/v1",
        "transition_firing_superseded_by_growth_recovery/v1",
        "operation_dispatch_superseded_by_growth_recovery/v1",
    }
    recovery_events = [
        item for item in events
        if item.event_type in growth_recovery_types
    ]
    if (event_type in growth_recovery_types
            and pending is recovery_events[0]):
        recovery_groups: dict[str, list[PendingEvent]] = {}
        for item in recovery_events:
            invocation_ref = item.payload.get("invocation_ref", {})
            invocation_id = str(invocation_ref.get("logical_id", ""))
            if not invocation_id:
                raise RegistryConflict(
                    "growth recovery event has no exact invocation")
            recovery_groups.setdefault(invocation_id, []).append(item)
        if (not recovery_groups
                or len({item.idempotency_key
                        for item in recovery_events}) != 1
                or len({item.command_id
                        for item in recovery_events}) != 1):
            raise RegistryConflict(
                "growth recovery requires one atomic command closure")

        common_closure: tuple[Any, ...] | None = None
        recovered_firing_ids: set[str] = set()
        for invocation_id, group in recovery_groups.items():
            group_by_type = {
                item.event_type: item for item in group
            }
            group_payload = group[0].payload
            if (len(group) != len(growth_recovery_types)
                    or set(group_by_type) != growth_recovery_types
                    or any(dict(item.payload) != dict(group_payload)
                           for item in group)):
                raise RegistryConflict(
                    "growth recovery requires one identical three-event "
                    "closure per invocation")
            invocation_ref = group_payload.get("invocation_ref", {})
            firing_ref = group_payload.get(
                "transition_firing_ref", {})
            lease_ref = group_payload.get(
                "operation_execution_lease_ref", {})
            proposal_ref = group_payload.get("growth_proposal_ref", {})
            superseded_net_ref = group_payload.get(
                "superseded_net_ref", {})
            replacement_net_ref = group_payload.get(
                "replacement_net_ref", {})
            checkpoint_ref = group_payload.get(
                "unchanged_marking_checkpoint_ref", {})
            closure = (
                proposal_ref, superseded_net_ref,
                replacement_net_ref, checkpoint_ref,
            )
            if common_closure is None:
                common_closure = closure
            elif closure != common_closure:
                raise RegistryConflict(
                    "growth recovery mappings do not share one exact "
                    "proposal/net/checkpoint closure")
            firing_id = str(firing_ref.get("logical_id", ""))
            lease_id = str(lease_ref.get("logical_id", ""))
            recovered_firing_ids.add(firing_id)
            exact_aggregates = {
                "invocation_superseded_by_growth_recovery/v1": (
                    invocation_id),
                "transition_firing_superseded_by_growth_recovery/v1": (
                    firing_id),
                "operation_dispatch_superseded_by_growth_recovery/v1": (
                    lease_id),
            }
            invocation_typed_id = TypedId.parse(
                invocation_id, expected="invocation")
            for item in events:
                expected_recovery_type = next((
                    kind for kind, aggregate_id
                    in exact_aggregates.items()
                    if item.aggregate_id == aggregate_id
                ), None)
                if (expected_recovery_type is not None
                        and item.event_type
                        != expected_recovery_type):
                    raise RegistryConflict(
                        "growth recovery transaction adds an effect to "
                        "a superseded aggregate")
                if (item.producer_invocation_id
                        == invocation_typed_id and item not in group):
                    raise RegistryConflict(
                        "growth recovery transaction adds a post-admission "
                        "invocation effect")
            if (group_payload.get("reason")
                    != "superseded_by_growth_recovery"
                    or any(group_by_type[kind].aggregate_id
                           != aggregate_id
                           for kind, aggregate_id
                           in exact_aggregates.items())
                    or any(group_by_type[kind].producer_invocation_id
                           != invocation_typed_id
                           for kind in growth_recovery_types)
                    or not exact_ref_exists(
                        invocation_ref, "invocation/v1")
                    or not exact_ref_exists(
                        firing_ref, "transition_firing/v1")
                    or not exact_ref_exists(
                        lease_ref, "operation_execution_lease/v1")
                    or not exact_ref_exists(
                        proposal_ref, "resource_version/v1")
                    or not exact_ref_exists(
                        superseded_net_ref, "net_instance/v1")
                    or not exact_ref_exists(
                        replacement_net_ref, "net_instance/v1")
                    or not exact_ref_exists(
                        checkpoint_ref, "marking_checkpoint/v1")):
                raise RegistryConflict(
                    "growth recovery supersede payload lacks exact authority")
            invocation = version_metadata(
                str(invocation_ref.get("version_id", "")),
                "invocation/v1")
            firing = version_metadata(
                str(firing_ref.get("version_id", "")),
                "transition_firing/v1")
            lease = version_metadata(
                str(lease_ref.get("version_id", "")),
                "operation_execution_lease/v1")
            checkpoint = version_metadata(
                str(checkpoint_ref.get("version_id", "")),
                "marking_checkpoint/v1")
            if (invocation is None or firing is None or lease is None
                    or checkpoint is None
                    or invocation.get("origin") != "petri_operation"
                    or invocation.get("own_transition_firing_ref")
                    != firing_ref
                    or invocation.get("operation_execution_lease_ref")
                    != lease_ref
                    or invocation.get("net_instance_ref")
                    != superseded_net_ref
                    or invocation.get(
                        "admission_marking_checkpoint_ref")
                    != checkpoint_ref
                    or invocation.get("context_digest")
                    != group_payload.get("context_digest")
                    or firing.get("net_instance_ref")
                    != superseded_net_ref
                    or firing.get("admission_marking_checkpoint_ref")
                    != checkpoint_ref
                    or lease.get("invocation_ref") != invocation_ref
                    or checkpoint.get("net_instance_ref")
                    != superseded_net_ref
                    or firing_ref in checkpoint.get(
                        "transition_firing_refs", [])
                    or not set(firing.get(
                        "claimed_input_version_ids", [])).issubset({
                            str(item.get("version_id", ""))
                            for item in checkpoint.get("token_refs", [])
                            if isinstance(item, Mapping)
                        })):
                raise RegistryConflict(
                    "growth recovery firing is not an unchanged preclaim")
            prior_types: dict[str, list[str]] = {}
            for aggregate_id in (
                    invocation_id, firing_id, lease_id):
                rows = db.execute(
                    "SELECT event_type FROM events WHERE aggregate_id=?",
                    (aggregate_id,),
                ).fetchall()
                prior_types[aggregate_id] = [
                    str(row["event_type"]) for row in rows
                ]
            if (prior_types[invocation_id].count(
                    "invocation_started/v1") != 1
                    or any(value in prior_types[invocation_id]
                           for value in {
                               "operation_terminal_ready/v1",
                               "invocation_settled/v1",
                               "invocation_superseded_by_growth_recovery/v1",
                           })
                    or prior_types[firing_id].count(
                        "firing_admitted/v1") != 1
                    or any(value in prior_types[firing_id]
                           for value in {
                               "transition_firing_settled/v1",
                               "transition_firing_superseded_by_growth_recovery/v1",
                           })
                    or prior_types[lease_id].count(
                        "operation_dispatch_reserved/v1") != 1
                    or any(value in prior_types[lease_id]
                           for value in {
                               "operation_execution_started/v1",
                               "operation_execution_finished/v1",
                               "operation_dispatch_superseded_by_growth_recovery/v1",
                           })):
                raise RegistryConflict(
                    "growth recovery refuses a dispatched/output/terminal "
                    "firing")
            admission_rows = db.execute(
                "SELECT transaction_id FROM events WHERE aggregate_id=? "
                "AND event_type='firing_admitted/v1'",
                (firing_id,),
            ).fetchall()
            if len(admission_rows) != 1:
                raise RegistryConflict(
                    "growth recovery firing has no unique admission "
                    "transaction")
            side_effect = db.execute(
                "SELECT event_type FROM events "
                "WHERE producer_invocation_id=? AND transaction_id<>? "
                "LIMIT 1",
                (invocation_id,
                 str(admission_rows[0]["transaction_id"])),
            ).fetchone()
            if side_effect is not None:
                raise RegistryConflict(
                    "growth recovery invocation has post-admission effects")

        assert common_closure is not None
        (
            _proposal_ref, superseded_net_ref,
            replacement_net_ref, checkpoint_ref,
        ) = common_closure
        if any(
                item.event_type == "firing_admitted/v1"
                and item.net_instance_id == TypedId.parse(str(
                    superseded_net_ref.get("logical_id", "")),
                    expected="net_instance")
                for item in events):
            raise RegistryConflict(
                "growth recovery transaction cannot admit another old-net "
                "firing")
        active_net = db.execute(
            "SELECT payload_json FROM events WHERE task_id=? "
            "AND event_type='net_adopted/v1' "
            "ORDER BY task_control_sequence DESC LIMIT 1",
            (str(task_id),),
        ).fetchone()
        marking_head = db.execute(
            "SELECT payload_json FROM events WHERE task_id=? "
            "AND net_instance_id=? "
            "AND event_type='marking_checkpoint_committed/v1' "
            "ORDER BY task_control_sequence DESC LIMIT 1",
            (str(task_id), str(superseded_net_ref.get(
                "logical_id", ""))),
        ).fetchone()
        adoption_events = [
            item for item in events
            if item.event_type == "net_adopted/v1"
        ]
        if (active_net is None
                or json.loads(active_net["payload_json"]).get(
                    "net_instance_ref") != superseded_net_ref
                or marking_head is None
                or json.loads(marking_head["payload_json"]).get(
                    "checkpoint_ref") != checkpoint_ref
                or len(adoption_events) != 1
                or adoption_events[0].payload.get("net_instance_ref")
                != replacement_net_ref
                or adoption_events[0].payload.get("supersedes_net_ref")
                != superseded_net_ref):
            raise RegistryConflict(
                "growth recovery must atomically replace the exact active "
                "net while preserving its checkpoint")
        active_rows = db.execute(
            "SELECT o.logical_id FROM objects o "
            "JOIN events admitted ON admitted.aggregate_id=o.logical_id "
            "AND admitted.event_type='firing_admitted/v1' "
            "LEFT JOIN events settled ON settled.aggregate_id=o.logical_id "
            "AND settled.event_type='transition_firing_settled/v1' "
            "LEFT JOIN events superseded ON superseded.aggregate_id=o.logical_id "
            "AND superseded.event_type="
            "'transition_firing_superseded_by_growth_recovery/v1' "
            "LEFT JOIN events native_superseded ON "
            "native_superseded.aggregate_id=o.logical_id "
            "AND native_superseded.event_type="
            "'transition_firing_superseded_by_native_resume/v1' "
            "WHERE o.object_type='transition_firing/v1' "
            "AND admitted.net_instance_id=? AND settled.event_id IS NULL "
            "AND superseded.event_id IS NULL "
            "AND native_superseded.event_id IS NULL",
            (str(superseded_net_ref.get("logical_id", "")),),
        ).fetchall()
        if ({str(row["logical_id"]) for row in active_rows}
                != recovered_firing_ids):
            raise RegistryConflict(
                "growth recovery mappings are not the old net's complete "
                "active firing set")

    if event_type == "transition_firing_settled/v1":
        prior_rows = db.execute(
            "SELECT event_id FROM events WHERE aggregate_id=? "
            "AND event_type IN "
            "('transition_firing_settled/v1',"
            "'transition_firing_superseded_by_growth_recovery/v1',"
            "'transition_firing_superseded_by_native_resume/v1')",
            (pending.aggregate_id,)).fetchall()
        prior = any(persisted_member_visible(
            "event", str(row["event_id"])) for row in prior_rows)
        if prior or any(
                item is not pending
                and item.event_type in {
                    "transition_firing_settled/v1",
                    "transition_firing_superseded_by_growth_recovery/v1",
                    "transition_firing_superseded_by_native_resume/v1",
                }
                and item.aggregate_id == pending.aggregate_id
                for item in events):
            raise RegistryConflict("transition firing may settle exactly once")
        event_ref = payload.get("event_ref")
        firing_ref = payload.get("transition_firing_ref")
        completion_ref = payload.get("firing_completion_ref")
        result_ref = payload.get("operation_result_ref")
        delta_ref = payload.get("marking_delta_ref")
        checkpoint_ref = payload.get("successor_checkpoint_ref")
        transaction_ref = payload.get("transaction_ref")
        completion = (version_metadata(
            str((completion_ref or {}).get("version_id", "")),
            "firing_completion/v2")
            if isinstance(completion_ref, Mapping) else None)
        result = (version_metadata(
            str((result_ref or {}).get("version_id", "")),
            "operation_result/v1")
            if isinstance(result_ref, Mapping) else None)
        firing = (version_metadata(
            str((firing_ref or {}).get("version_id", "")),
            "transition_firing/v1")
            if isinstance(firing_ref, Mapping) else None)
        delta = (version_metadata(
            str((delta_ref or {}).get("version_id", "")),
            "marking_delta/v1")
            if isinstance(delta_ref, Mapping) else None)
        checkpoint = (version_metadata(
            str((checkpoint_ref or {}).get("version_id", "")),
            "marking_checkpoint/v1")
            if isinstance(checkpoint_ref, Mapping) else None)
        invocation_ref = (
            completion.get("invocation_ref")
            if isinstance(completion, Mapping) else None)
        invocation = (version_metadata(
            str(invocation_ref.get("version_id", "")),
            "invocation/v1")
            if isinstance(invocation_ref, Mapping) else None)
        completion_item = (
            new_by_version.get(str(completion_ref.get("version_id", "")))
            if isinstance(completion_ref, Mapping) else None)
        delta_item = (
            new_by_version.get(str(delta_ref.get("version_id", "")))
            if isinstance(delta_ref, Mapping) else None)
        checkpoint_item = (
            new_by_version.get(str(checkpoint_ref.get("version_id", "")))
            if isinstance(checkpoint_ref, Mapping) else None)
        checkpoint_event = tuple(
            item for item in events
            if item.event_type == "marking_checkpoint_committed/v1"
            and item.payload.get("checkpoint_ref") == checkpoint_ref)
        previous_ref = (
            checkpoint.get("previous_checkpoint_ref")
            if isinstance(checkpoint, Mapping) else None)
        checkpoint_tokens = (
            checkpoint.get("token_refs", [])
            if isinstance(checkpoint, Mapping) else [])
        deposited_refs = (
            delta.get("deposited_refs", [])
            if isinstance(delta, Mapping) else [])
        deposited_versions = {
            str(value.get("version_id", ""))
            for value in deposited_refs
            if isinstance(value, Mapping)}
        prepared_token_versions = {
            str(item.version_id) for item in objects
            if item.object_type == "petri_token/v1"}
        structural_events = tuple(
            item for item in events
            if item.event_type == "structural_growth_adopted/v1"
            and item.payload.get(
                "candidate_marking_checkpoint_ref") == checkpoint_ref)
        operation_revisions = tuple(item for item in events
            if item.event_type == "net_adopted/v1" and
            item.payload.get("operation_revision", {}).get("successor_checkpoint_ref") == checkpoint_ref)
        structural_checkpoint_versions = ({
            str(value.get("version_id", ""))
            for value in checkpoint_tokens
            if isinstance(value, Mapping)
        } if len(structural_events) == 1 or len(operation_revisions) == 1 else set())
        expected_prepared_token_versions = (
            deposited_versions | structural_checkpoint_versions)
        predecessor = (version_metadata(
            str(previous_ref.get("version_id", "")), "marking_checkpoint/v1")
            if isinstance(previous_ref, Mapping) else None)

        def settlement_token_keys(values: object) -> set[bytes]:
            if not isinstance(values, list):
                raise RegistryConflict("settlement token membership is not an array")
            exact_keys = set()
            for value in values:
                if not isinstance(value, Mapping) or value.get("entity_type") != "petri_token/v1":
                    raise RegistryConflict("settlement membership contains a non-token")
                token = version_metadata(str(value.get("version_id", "")), "petri_token/v1")
                if token is None or token.get("petri_token_ref") != value:
                    raise RegistryConflict("settlement token membership is not exact")
                exact_keys.add(canonical_json(value))
            if len(exact_keys) != len(values):
                raise RegistryConflict("settlement repeats a token occurrence")
            return exact_keys

        if predecessor is None:
            raise RegistryConflict("settlement lacks its exact predecessor checkpoint")
        before_tokens = settlement_token_keys(predecessor.get("token_refs"))
        consumed_tokens = settlement_token_keys(delta.get("consumed_refs") if delta is not None else None)
        deposited_tokens = settlement_token_keys(deposited_refs)
        after_tokens = settlement_token_keys(checkpoint_tokens)
        if (not consumed_tokens.issubset(before_tokens)
                or (not structural_events and not operation_revisions and before_tokens - consumed_tokens | deposited_tokens != after_tokens)):
            raise RegistryConflict("settlement violates exact pre-consumed+deposited=post")
        if invocation is not None:
            validate_current_invocation_authority(
                invocation, boundary="settlement",
                operation_result_ref=result_ref,
                allow_recorded_completion_recovery=True)
        if (not isinstance(event_ref, Mapping)
                or event_ref.get("entity_type")
                != "fact_event/v1"
                or firing is None or completion is None
                or result is None or invocation is None
                or delta is None or checkpoint is None
                or completion_item is None
                or completion_item.object_type != "firing_completion/v2"
                or completion_item.producer_invocation_id
                != pending.producer_invocation_id
                or delta_item is None
                or delta_item.object_type != "marking_delta/v1"
                or delta_item.producer_invocation_id
                != pending.producer_invocation_id
                or checkpoint_item is None
                or checkpoint_item.object_type
                != "marking_checkpoint/v1"
                or checkpoint_item.producer_invocation_id
                != pending.producer_invocation_id
                or pending.aggregate_id
                != str(firing_ref.get("logical_id", ""))
                or pending.producer_invocation_id is None
                or str(pending.producer_invocation_id)
                != str(invocation_ref.get("logical_id", ""))
                or not isinstance(transaction_ref, Mapping)
                or transaction_ref.get("entity_type") != "transaction/v1"
                or transaction_ref.get("logical_id")
                != str(transaction_id)
                or firing.get("transition_firing_ref") != firing_ref
                or completion.get("firing_completion_ref")
                != completion_ref
                or completion.get("transition_firing_ref") != firing_ref
                or completion.get("operation_result_ref") != result_ref
                or completion.get("business_outcome")
                != payload.get("business_outcome")
                or payload.get("business_outcome") != "completed"
                or completion.get("workspace_access_set_ref")
                != payload.get("workspace_access_set_ref")
                or completion.get("workspace_revision_ref")
                != payload.get("workspace_revision_ref")
                or completion.get("marking_delta_ref") != delta_ref
                or completion.get("successor_checkpoint_ref")
                != checkpoint_ref
                or result.get("operation_result_ref") != result_ref
                or result.get("invocation_ref") != invocation_ref
                or result.get("transition_firing_ref") != firing_ref
                or result.get("business_outcome")
                != payload.get("business_outcome")
                or result.get("workspace_access_set_ref")
                != payload.get("workspace_access_set_ref")
                or delta.get("marking_delta_ref") != delta_ref
                or delta.get("phase") != "settlement"
                or delta.get("transition_firing_refs") != [firing_ref]
                or delta.get("operation_binding_refs") != [
                    firing.get("operation_binding_ref")]
                or expected_prepared_token_versions
                != prepared_token_versions
                or checkpoint.get("marking_checkpoint_ref")
                != checkpoint_ref
                or checkpoint.get("settlement_delta_ref") != delta_ref
                or checkpoint.get("transition_firing_refs") != [firing_ref]
                or len({canonical_json(value)
                        for value in checkpoint_tokens})
                != len(checkpoint_tokens)
                or len(checkpoint_event) != 1
                or checkpoint_event[0].payload.get(
                    "previous_checkpoint_ref") != previous_ref
                or checkpoint_event[0].payload.get(
                    "settlement_delta_ref") != delta_ref
                or checkpoint_event[0].payload.get(
                    "transition_firing_refs") != [firing_ref]
                or checkpoint_event[0].producer_invocation_id
                != pending.producer_invocation_id
                or not has_new_relation(
                    "derived_from",
                    str(delta_ref.get("version_id", "")),
                    str((previous_ref or {}).get("version_id", "")))
                or not has_new_relation(
                    "derived_from",
                    str(checkpoint_ref.get("version_id", "")),
                    str(delta_ref.get("version_id", "")))):
            raise RegistryConflict(
                "current firing settlement lacks one exact completion, "
                "delta, checkpoint, relation, or terminal fact")
        for output_ref in result.get("output_resource_refs", []):
            validate_provider_candidate_output(invocation, output_ref)
        from ...declared_effect_validation import validate_declared_effect_success
        try:
            from ...normal_root_token_allocation import normal_root_allocation_scheme
            ordinary_token_ref_scheme = normal_root_allocation_scheme(
                objects=objects, task_id=task_id, transaction_id=transaction_id,
                firing=firing, invocation=invocation, result=result, delta=delta,
                completion=completion, checkpoint=checkpoint)
            if operation_revisions:
                if ordinary_token_ref_scheme is not None:
                    raise RegistryConflict('normal root token allocation does not support operation revision')
                from ...module_revision import validate_operation_revision_success
                validate_operation_revision_success(event_store, event_store.catalog, db,
                    pending=operation_revisions[0], firing=firing, invocation=invocation,
                    result=result, delta=delta, predecessor=predecessor, checkpoint=checkpoint,
                    exact=exact_ref_exists, metadata=version_metadata, events=events,
                    objects=objects, relations=relations, transaction_id=transaction_id)
            else:
                validate_declared_effect_success(event_store, db, firing=firing, invocation=invocation,
                    result=result, delta=delta, predecessor=predecessor, checkpoint=checkpoint,
                    exact=exact_ref_exists, metadata=version_metadata,
                    ordinary_token_ref_scheme=ordinary_token_ref_scheme)
        except RegistryConflict:
            raise
        except Exception as exc:
            raise RegistryConflict("declared effect Success closure failed mechanical verification") from exc

    if event_type == "checkpoint_repair_committed/v1":
        repair_ref = payload.get("checkpoint_repair_ref")
        expected_ref = payload.get("expected_checkpoint_ref")
        repair_base_ref = payload.get("repair_base_checkpoint_ref")
        successor_ref = payload.get("successor_checkpoint_ref")
        shared_readdresses = payload.get(
            "shared_read_token_readdresses", [])
        mechanical_receipt = payload.get(
            "mechanical_receipt_repair")
        finalization_feedback = payload.get(
            "finalization_feedback_repair")
        a2c_indicator_return = payload.get(
            "a2c_indicator_return_repair")
        repair_kind = payload.get("repair_kind")
        authority_ref = payload.get("repair_authority_ref")
        added_indicator_refs = (
            a2c_indicator_return.get("added_indicator_token_refs", [])
            if isinstance(a2c_indicator_return, Mapping)
            else payload.get("added_indicator_token_refs", []))
        repair = (version_metadata(
            str(repair_ref.get("version_id", "")),
            "checkpoint_repair/v1")
            if isinstance(repair_ref, Mapping) else None)
        matching_checkpoints = tuple(
            item for item in events
            if item.event_type == "marking_checkpoint_committed/v1"
            and item.payload.get("checkpoint_ref") == successor_ref)
        valid_shared_readdresses = (
            isinstance(shared_readdresses, list)
            and bool(shared_readdresses)
            and all(
                isinstance(item, Mapping)
                and exact_ref_exists(
                    item.get("source_token_ref"), "petri_token/v1")
                and exact_ref_exists(
                    item.get("replacement_token_ref"), "petri_token/v1")
                and isinstance(item.get("previous_consumer_id"), str)
                and bool(item.get("previous_consumer_id"))
                for item in shared_readdresses))
        valid_shared_mode = (
            repair_kind == "shared_read"
            and valid_shared_readdresses)
        valid_mechanical_receipt = (
            repair_kind == "mechanical_finalization_receipt"
            and isinstance(mechanical_receipt, Mapping)
            and all(exact_ref_exists(
                mechanical_receipt.get(name), expected_type)
                for name, expected_type in (
                    ("source_token_ref", "petri_token/v1"),
                    ("replacement_token_ref", "petri_token/v1"),
                    ("source_pool_token_ref", "petri_token/v1"),
                    ("replacement_pool_token_ref", "petri_token/v1"),
                    ("source_resource_ref", "resource_version/v1"),
                    ("replacement_resource_ref", "resource_version/v1"),
                )))
        valid_finalization_feedback = (
            repair_kind == "finalization_feedback"
            and isinstance(finalization_feedback, Mapping)
            and exact_ref_exists(
                finalization_feedback.get("source_route_firing_ref"),
                "transition_firing/v1")
            and exact_ref_exists(
                finalization_feedback.get("review_token_ref"),
                "petri_token/v1")
            and exact_ref_exists(
                finalization_feedback.get("feedback_resource_ref"),
                "resource_version/v1")
            and all(
                isinstance(finalization_feedback.get(name), list)
                and bool(finalization_feedback[name])
                for name in (
                    "requested_target_node_refs",
                    "resolved_root_places",
                    "resolved_producer_transition_ids",
                    "preserved_route_output_token_refs",
                    "preserved_counter_token_refs"))
            and all(exact_ref_exists(item, "node_declaration/v1")
                    for item in finalization_feedback[
                        "requested_target_node_refs"])
            and all(exact_ref_exists(item, "petri_token/v1")
                    for name in (
                        "preserved_route_output_token_refs",
                        "preserved_counter_token_refs")
                    for item in finalization_feedback[name]))
        valid_a2c_indicator_return = (
            repair_kind == "a2c_indicator_return"
            and isinstance(a2c_indicator_return, Mapping)
            and exact_ref_exists(
                a2c_indicator_return.get("source_route_firing_ref"),
                "transition_firing/v1")
            and isinstance(
                a2c_indicator_return.get("target_places"), list)
            and bool(a2c_indicator_return["target_places"])
            and len(a2c_indicator_return["target_places"])
            == len(set(a2c_indicator_return["target_places"]))
            and all(isinstance(item, str) and item
                    for item in a2c_indicator_return["target_places"])
            and isinstance(added_indicator_refs, list)
            and bool(added_indicator_refs)
            and len(added_indicator_refs)
            == len({canonical_json(item)
                    for item in added_indicator_refs})
            and all(exact_ref_exists(item, "petri_token/v1")
                    for item in added_indicator_refs)
            and a2c_indicator_return.get(
                "added_indicator_token_refs")
            == added_indicator_refs)
        if (repair is None
                or not isinstance(expected_ref, Mapping)
                or not isinstance(repair_base_ref, Mapping)
                or not isinstance(successor_ref, Mapping)
                or not exact_ref_exists(
                    expected_ref, "marking_checkpoint/v1")
                or not exact_ref_exists(
                    repair_base_ref, "marking_checkpoint/v1")
                or not (valid_shared_mode or valid_mechanical_receipt
                        or valid_finalization_feedback
                        or valid_a2c_indicator_return)
                or not exact_ref_exists(authority_ref)
                or repair.get("checkpoint_repair_ref") != repair_ref
                or repair.get("expected_checkpoint_ref") != expected_ref
                or repair.get("repair_base_checkpoint_ref")
                != repair_base_ref
                or repair.get("successor_checkpoint_ref")
                != successor_ref
                or repair.get("shared_read_token_readdresses", [])
                != shared_readdresses
                or repair.get("mechanical_receipt_repair")
                != mechanical_receipt
                or repair.get("finalization_feedback_repair")
                != finalization_feedback
                or repair.get("a2c_indicator_return_repair")
                != a2c_indicator_return
                or repair.get("added_indicator_token_refs", [])
                != added_indicator_refs
                or repair.get("repair_kind") != repair_kind
                or repair.get("repair_authority_ref") != authority_ref
                or repair.get("result_state")
                != payload.get("result_state")
                or (valid_finalization_feedback
                    and not has_new_relation(
                        "derived_from",
                        str(repair_ref.get("version_id", "")),
                        str(finalization_feedback[
                            "source_route_firing_ref"].get(
                                "version_id", ""))))
                or str(repair_ref.get("version_id", ""))
                not in new_by_version
                or str(successor_ref.get("version_id", ""))
                not in new_by_version
                or len(matching_checkpoints) != 1
                or matching_checkpoints[0].payload.get(
                    "previous_checkpoint_ref") != expected_ref
                or events.index(pending)
                >= events.index(matching_checkpoints[0])):
            raise RegistryConflict(
                "checkpoint repair commit lacks one exact atomic successor")

    if event_type == "run_reopened/v1":
        authorization_ref = payload.get("run_reopen_authorization_ref")
        checkpoint_ref = payload.get("reentry_checkpoint_ref")
        successor_ref = payload.get("successor_run_authority_ref")
        checkpoints = tuple(
            item for item in events
            if item.event_type == "marking_checkpoint_committed/v1"
            and item.payload.get("checkpoint_ref") == checkpoint_ref
            and item.payload.get("reentry_authorization_ref")
            == authorization_ref)
        authorization = (version_metadata(
            str(authorization_ref.get("version_id", "")),
            "run_reopen_authorization/v1")
            if isinstance(authorization_ref, Mapping) else None)
        successor = (version_metadata(
            str(successor_ref.get("version_id", "")),
            "run_execution_authority/v1")
            if isinstance(successor_ref, Mapping) else None)
        if (len(checkpoints) != 1
                or authorization is None or successor is None
                or str(authorization_ref.get("version_id", ""))
                not in new_by_version
                or str(successor_ref.get("version_id", ""))
                not in new_by_version
                or authorization.get("reentry_checkpoint_ref")
                != checkpoint_ref
                or authorization.get("successor_run_authority_ref")
                != successor_ref
                or authorization.get("workspace_reentry_revision_refs")
                != payload.get("workspace_reentry_revision_refs")
                or authorization.get("execution_generation")
                != payload.get("execution_generation")
                or successor.get("latest_checkpoint_ref") != checkpoint_ref
                or successor.get("reopen_authorization_ref")
                != authorization_ref):
            raise RegistryConflict(
                "run reopen event lacks one exact atomic authority closure")

    if event_type == "marking_checkpoint_committed/v1":
        checkpoint_ref = payload.get("checkpoint_ref")
        net_ref = payload.get("net_instance_ref")
        owner_predecessors = [item.payload.get("supersedes_net_ref") for item in events
            if (item.event_type == "net_adopted/v1"
                and "owner_command_ref" in item.payload
                and item.payload.get("owner_command_ref") == payload.get("owner_command_ref")
                and item.payload.get("owner_command_result_ref") == payload.get("owner_command_result_ref")
                and item.payload.get("net_instance_ref") == net_ref
                and item.payload.get("owner_candidate_checkpoint_ref") == checkpoint_ref
                and item.payload.get("owner_predecessor_checkpoint_ref") == payload.get("previous_checkpoint_ref"))]
        if "owner_command_ref" in payload and len(owner_predecessors) != 1:
            raise RegistryConflict("owner checkpoint requires one SAMEtransaction owner NET adoption")
        structural_predecessors = [
            item.payload.get("predecessor_net_ref")
            for item in events
            if (item.event_type == "structural_growth_adopted/v1"
                and item.payload.get("candidate_net_ref") == net_ref
                and item.payload.get(
                    "candidate_marking_checkpoint_ref")
                == checkpoint_ref
                and item.payload.get(
                    "predecessor_marking_checkpoint_ref")
                == payload.get("previous_checkpoint_ref"))
        ]
        structural_predecessors.extend(item.payload.get("supersedes_net_ref") for item in events
            if (item.event_type == "net_adopted/v1" and "operation_revision" in item.payload
                and item.payload.get("net_instance_ref") == net_ref
                and item.payload["operation_revision"]["successor_checkpoint_ref"] == checkpoint_ref
                and item.payload["operation_revision"]["predecessor_checkpoint_ref"] == payload.get("previous_checkpoint_ref")))
        if len(structural_predecessors) > 1:
            raise RegistryConflict(
                "settlement checkpoint has ambiguous structural predecessors")
        repair_predecessors = [
            item.payload.get("supersedes_net_ref")
            for item in events
            if (item.event_type == "net_adopted/v1"
                and item.payload.get("net_instance_ref") == net_ref
                and item.payload.get("repair_kind")
                == "a2c_indicator_return"
                and item.payload.get("repair_candidate_checkpoint_ref")
                == checkpoint_ref
                and item.payload.get("repair_predecessor_checkpoint_ref")
                == payload.get("previous_checkpoint_ref"))
        ]
        if len(repair_predecessors) > 1:
            raise RegistryConflict(
                "settlement checkpoint has ambiguous repair predecessors")
        if structural_predecessors and repair_predecessors:
            raise RegistryConflict(
                "settlement checkpoint has mixed structural/repair predecessors")
        if owner_predecessors and (structural_predecessors or repair_predecessors):
            raise RegistryConflict("owner checkpoint cannot use growth/repair provenance")
        structural_predecessor = (
            structural_predecessors[0]
            if structural_predecessors else (
                repair_predecessors[0] if repair_predecessors else (
                    owner_predecessors[0] if owner_predecessors else None)))
        transaction_net_matches = (
            isinstance(net_ref, Mapping)
            and net_ref.get("logical_id") == str(net_instance_id))
        structural_net_matches = (
            isinstance(structural_predecessor, Mapping)
            and structural_predecessor.get("logical_id")
            == str(net_instance_id))
        owner_net_matches = (net_instance_id is None and task_round_id is None
            and len(owner_predecessors) == 1)
        if (not isinstance(checkpoint_ref, Mapping)
                or not isinstance(net_ref, Mapping)
                or not (transaction_net_matches
                        or structural_net_matches or owner_net_matches)
                or not exact_ref_exists(
                    checkpoint_ref, "marking_checkpoint/v1")
                or not exact_ref_exists(net_ref, "net_instance/v1")):
            raise RegistryConflict(
                "marking checkpoint commit lacks exact checkpoint/net refs")
        checkpoint = version_metadata(
            str(checkpoint_ref.get("version_id")),
            "marking_checkpoint/v1")
        if (checkpoint is None
                or checkpoint.get("marking_checkpoint_ref") != checkpoint_ref
                or checkpoint.get("net_instance_ref") != net_ref
                or checkpoint.get("team_design_root_ref")
                != payload.get("team_design_root_ref")
                or checkpoint.get("previous_checkpoint_ref")
                != payload.get("previous_checkpoint_ref")
                or checkpoint.get("settlement_delta_ref")
                != payload.get("settlement_delta_ref")
                or checkpoint.get("transition_firing_refs")
                != payload.get("transition_firing_refs")
                or checkpoint.get("workspace_revision_refs")
                != payload.get("workspace_revision_refs")
                or checkpoint.get("settled") is not True
                or payload.get("settled") is not True):
            raise RegistryConflict(
                "checkpoint commit differs from its immutable object")
        reentry_fields = (
            "reentry_source_checkpoint_ref",
            "reentry_authorization_ref",
            "reentry_superseded_terminal_evidence_ref",
            "reentry_generation",
            "reentry_token_mappings",
            "reentry_superseded_token_refs",
        )
        present_reentry = tuple(
            field for field in reentry_fields
            if field in checkpoint or field in payload)
        if present_reentry:
            if (set(present_reentry) != set(reentry_fields)
                    or any(checkpoint.get(field) != payload.get(field)
                           for field in reentry_fields)):
                raise RegistryConflict(
                    "checkpoint reentry provenance tuple is incomplete")
        previous = payload.get("previous_checkpoint_ref")
        for field in ("owner_command_ref", "owner_command_result_ref"):
            if checkpoint.get(field) != payload.get(field):
                raise RegistryConflict("owner checkpoint event differs from immutable command refs")
        if previous is not None:
            marking_net_id = str(net_instance_id)
            if structural_predecessors or repair_predecessors or owner_predecessors:
                if not isinstance(structural_predecessor, Mapping):
                    raise RegistryConflict(
                        "checkpoint predecessor net is malformed")
                marking_net_id = str(
                    structural_predecessor.get("logical_id", ""))
            current = db.execute(
                "SELECT e.payload_json FROM events e WHERE e.task_id=? "
                "AND e.net_instance_id=? "
                "AND e.event_type='marking_checkpoint_committed/v1' AND "
                f"{_CANONICAL_EVENT_SQL} "
                "ORDER BY task_control_sequence DESC LIMIT 1",
                (str(task_id), marking_net_id)).fetchone()
            current_payload = (json.loads(current["payload_json"])
                               if current is not None else None)
            current_ref = (current_payload.get("checkpoint_ref")
                           if current_payload is not None else None)
            if current_ref != previous:
                raise RegistryConflict(
                    "settlement checkpoint does not extend the current marking head")
            delta_ref = payload.get("settlement_delta_ref")
            transition_refs = payload.get("transition_firing_refs")
            matching_repairs = tuple(
                item for item in events
                if (item.event_type
                    == "checkpoint_repair_committed/v1"
                    and item.payload.get("successor_checkpoint_ref")
                    == checkpoint_ref
                    and item.payload.get("expected_checkpoint_ref")
                    == previous))
            if present_reentry:
                source_ref = payload.get(
                    "reentry_source_checkpoint_ref")
                authorization_ref = payload.get(
                    "reentry_authorization_ref")
                mappings = payload.get("reentry_token_mappings")
                superseded_token_refs = payload.get(
                    "reentry_superseded_token_refs")
                source = (version_metadata(
                    str(source_ref.get("version_id", "")),
                    "marking_checkpoint/v1")
                    if isinstance(source_ref, Mapping) else None)
                current_checkpoint = (version_metadata(
                    str(previous.get("version_id", "")),
                    "marking_checkpoint/v1")
                    if isinstance(previous, Mapping) else None)
                committed_source = (
                    isinstance(source_ref, Mapping)
                    and db.execute(
                        "SELECT 1 FROM events e WHERE e.task_id=? AND "
                        "e.net_instance_id=? AND "
                        "e.event_type='marking_checkpoint_committed/v1' AND "
                        "json_extract(e.payload_json,'$.checkpoint_ref.version_id')=? AND "
                        f"{_CANONICAL_EVENT_SQL} LIMIT 1",
                        (str(task_id), str(net_instance_id),
                         str(source_ref.get("version_id", ""))),
                    ).fetchone() is not None)
                mapping_rows = (
                    mappings if isinstance(mappings, list) else [])
                sources = [row.get("source_token_ref") for row in mapping_rows
                    if isinstance(row, Mapping)]
                replacements = [row.get("replacement_token_ref")
                    for row in mapping_rows if isinstance(row, Mapping)]
                source_token_refs = (
                    source.get("token_refs", [])
                    if isinstance(source, Mapping) else [])
                checkpoint_token_refs = checkpoint.get("token_refs", [])
                if (source is None or current_checkpoint is None
                        or not committed_source
                        or source.get("net_instance_ref") != net_ref
                        or source.get("settled") is not True
                        or len(mapping_rows) != len(sources)
                        or len(mapping_rows) != len(replacements)
                        or len({canonical_json(row) for row in sources})
                        != len(sources)
                        or len({canonical_json(row) for row in replacements})
                        != len(replacements)
                        or {canonical_json(row) for row in sources}
                        != {canonical_json(row) for row in source_token_refs}
                        or {canonical_json(row) for row in replacements}
                        != {canonical_json(row)
                            for row in checkpoint_token_refs}
                        or not isinstance(superseded_token_refs, list)
                        or {canonical_json(row)
                            for row in superseded_token_refs}
                        != {canonical_json(row) for row in
                            current_checkpoint.get("token_refs", [])}
                        or any(not has_new_relation(
                            "supersedes",
                            str(authorization_ref.get("version_id", "")),
                            str(row.get("version_id", "")))
                            for row in superseded_token_refs
                            if isinstance(row, Mapping))
                        or checkpoint.get("epoch")
                        != current_checkpoint.get("epoch", -1) + 1
                        or checkpoint.get("attempts")
                        != current_checkpoint.get("attempts")
                        or checkpoint.get("next_token_id")
                        != current_checkpoint.get("next_token_id", 0)
                            + len(mapping_rows)
                        or delta_ref is not None or transition_refs != []
                        or matching_repairs):
                    raise RegistryConflict(
                        "checkpoint reentry differs from its selected/current cuts")
                first_token_id = current_checkpoint.get("next_token_id", 0)
                for offset, row in enumerate(mapping_rows):
                    source_token_ref = row["source_token_ref"]
                    replacement_token_ref = row["replacement_token_ref"]
                    source_token = version_metadata(
                        str(source_token_ref.get("version_id", "")),
                        "petri_token/v1")
                    replacement = version_metadata(
                        str(replacement_token_ref.get("version_id", "")),
                        "petri_token/v1")
                    expected = (None if source_token is None else {
                        **source_token,
                        "petri_token_ref": replacement_token_ref,
                        "token_id": first_token_id + offset,
                        "epoch": checkpoint["epoch"],
                        "consumed_by": None,
                    })
                    if (source_token is None or replacement is None
                            or replacement != expected
                            or str(replacement_token_ref.get(
                                "version_id", "")) not in new_by_version
                            or not has_new_relation(
                                "derived_from",
                                str(replacement_token_ref.get(
                                    "version_id", "")),
                                str(source_token_ref.get(
                                    "version_id", "")))):
                        raise RegistryConflict(
                            "checkpoint reentry token is not one fresh exact clone")
                authority_rows = db.execute(
                    "SELECT o.metadata_json FROM objects o JOIN events e "
                    "ON e.event_id=o.published_event_id WHERE "
                    "o.object_type='run_execution_authority/v1' AND "
                    f"{_CANONICAL_EVENT_SQL} ORDER BY e.ordinal DESC LIMIT 1"
                ).fetchall()
                prior_authority = (
                    json.loads(str(authority_rows[0]["metadata_json"]))
                    if len(authority_rows) == 1 else None)
                staged_authorities = [dict(item.metadata) for item in objects
                    if item.object_type == "run_execution_authority/v1"]
                staged_authorizations = [dict(item.metadata)
                    for item in objects
                    if item.object_type == "run_reopen_authorization/v1"]
                matching_authorizations = [item
                    for item in staged_authorizations
                    if (item.get("run_reopen_authorization_ref")
                        == authorization_ref
                        and item.get("expected_run_authority_ref")
                        == (prior_authority or {}).get(
                            "run_execution_authority_ref")
                        and item.get("expected_current_checkpoint_ref")
                        == previous
                        and item.get("selected_checkpoint_ref") == source_ref
                        and item.get("superseded_current_token_refs")
                        == superseded_token_refs
                        and item.get("selected_source_token_refs") == sources
                        and item.get("workspace_reentry_revision_refs")
                        == checkpoint.get("workspace_revision_refs"))]
                expected_generation = (
                    int(prior_authority.get("execution_generation", 0)) + 1
                    if isinstance(prior_authority, Mapping) else None)
                matching_authorities = [item for item in staged_authorities
                    if (item.get("latest_checkpoint_ref") == checkpoint_ref
                        and item.get("status") == "stopped_by_owner"
                        and item.get("terminal_evidence_ref") is None
                        and item.get("execution_generation")
                        == expected_generation
                        and item.get("generation_source_checkpoint_ref")
                        == source_ref
                        and item.get("reopen_authorization_ref")
                        == authorization_ref)]
                valid_workspace_reentry = False
                if len(matching_authorizations) == 1:
                    authorization = matching_authorizations[0]
                    expected_heads = authorization.get(
                        "expected_workspace_head_refs", [])
                    selected_workspaces = authorization.get(
                        "selected_workspace_revision_refs", [])
                    successor_workspaces = authorization.get(
                        "workspace_reentry_revision_refs", [])
                    workspace_by_version = {
                        str(item.version_id): dict(item.metadata)
                        for item in objects
                        if item.object_type == "workspace_revision/v1"}
                    valid_workspace_reentry = (
                        len(expected_heads) == len(selected_workspaces)
                        == len(successor_workspaces)
                        and all(
                            isinstance(expected, Mapping)
                            and isinstance(selected, Mapping)
                            and isinstance(successor, Mapping)
                            and expected.get("logical_id")
                            == selected.get("logical_id")
                            == successor.get("logical_id")
                            and (document := workspace_by_version.get(
                                str(successor.get("version_id", ""))))
                            is not None
                            and document.get("workspace_revision_ref")
                            == successor
                            and document.get("parent_revision_ref")
                            == expected
                            and document.get("base_revision_ref")
                            == selected
                            and document.get("disposition") == "owner_reopen"
                            and document.get("reopen_authorization_ref")
                            == authorization_ref
                            and document.get("settled") is True
                            for expected, selected, successor in zip(
                                expected_heads, selected_workspaces,
                                successor_workspaces)))
                if (not isinstance(prior_authority, Mapping)
                        or prior_authority.get("latest_checkpoint_ref")
                        != previous
                        or prior_authority.get("status")
                        not in {"terminal", "stopped_by_owner"}
                        or payload.get("reentry_generation")
                        != expected_generation
                        or payload.get(
                            "reentry_superseded_terminal_evidence_ref")
                        != prior_authority.get("terminal_evidence_ref")
                        or len(matching_authorizations) != 1
                        or len(matching_authorities) != 1
                        or not valid_workspace_reentry
                        or matching_authorizations[0].get(
                            "successor_run_authority_ref")
                        != matching_authorities[0].get(
                            "run_execution_authority_ref")
                        or matching_authorizations[0].get(
                            "execution_generation") != expected_generation
                        or not any(
                            item.event_type == "run_reopened/v1"
                            and item.payload.get(
                                "run_reopen_authorization_ref")
                            == authorization_ref
                            and item.payload.get("selected_checkpoint_ref")
                            == source_ref
                            and item.payload.get("reentry_checkpoint_ref")
                            == checkpoint_ref
                            and item.payload.get(
                                "successor_run_authority_ref")
                            == matching_authorities[0].get(
                                "run_execution_authority_ref")
                            and item.payload.get(
                                "workspace_reentry_revision_refs")
                            == checkpoint.get("workspace_revision_refs")
                            and item.payload.get("execution_generation")
                            == expected_generation
                            for item in events)):
                    raise RegistryConflict(
                        "checkpoint reentry lacks its execution-generation successor")
            elif owner_predecessors:
                if delta_ref is not None or transition_refs != [] or matching_repairs:
                    raise RegistryConflict("owner command checkpoint is not firing settlement or repair")
            elif matching_repairs:
                if len(matching_repairs) != 1:
                    raise RegistryConflict(
                        "repair checkpoint has ambiguous repair provenance")
                repair_event = matching_repairs[0]
                repair_ref = repair_event.payload.get(
                    "checkpoint_repair_ref")
                repair_base_ref = repair_event.payload.get(
                    "repair_base_checkpoint_ref")
                delta = (version_metadata(
                    str(delta_ref.get("version_id", "")),
                    "marking_delta/v1")
                    if isinstance(delta_ref, Mapping) else None)
                repair = (version_metadata(
                    str(repair_ref.get("version_id", "")),
                    "checkpoint_repair/v1")
                    if isinstance(repair_ref, Mapping) else None)
                previous_checkpoint = (version_metadata(
                    str(previous.get("version_id", "")),
                    "marking_checkpoint/v1")
                    if isinstance(previous, Mapping) else None)
                finalization_repair = (
                    repair is not None
                    and repair.get("repair_kind")
                    == "finalization_feedback")
                deposited_refs = (
                    delta.get("deposited_refs", [])
                    if delta is not None else [])
                deposited_versions = {
                    str(item.get("version_id", ""))
                    for item in deposited_refs
                    if isinstance(item, Mapping)}
                checkpoint_token_refs = (
                    checkpoint.get("token_refs", [])
                    if checkpoint is not None else [])
                finalization_tokens = tuple(
                    version_metadata(version_id, "petri_token/v1")
                    for version_id in deposited_versions)
                if (delta is None or repair is None
                        or not isinstance(repair_base_ref, Mapping)
                        or transition_refs != []
                        or delta.get("phase") != "settlement"
                        or delta.get("transition_firing_refs") != []
                        or delta.get("net_instance_ref") != net_ref
                        or repair.get("successor_checkpoint_ref")
                        != checkpoint_ref
                        or repair.get("expected_checkpoint_ref")
                        != previous
                        or repair.get("repair_base_checkpoint_ref")
                        != repair_base_ref
                        or repair.get("checkpoint_repair_ref")
                        != repair_ref
                        or (finalization_repair and (
                            previous_checkpoint is None
                            or checkpoint.get("epoch")
                            != previous_checkpoint.get("epoch", -1) + 1
                            or checkpoint.get("attempts")
                            != previous_checkpoint.get("attempts")
                            or delta.get("consumed_refs")
                            != previous_checkpoint.get("token_refs")
                            or {canonical_json(item)
                                for item in deposited_refs}
                            != {canonical_json(item)
                                for item in checkpoint_token_refs}
                            or len(deposited_refs)
                            != len(deposited_versions)
                            or not deposited_refs
                            or any(
                                version_id not in new_by_version
                                for version_id in deposited_versions)
                            or any(
                                item is None
                                or item.get("epoch")
                                != checkpoint.get("epoch")
                                or item.get("token_id", -1)
                                < previous_checkpoint.get(
                                    "next_token_id", 0)
                                for item in finalization_tokens)
                            or {item.get("token_id")
                                for item in finalization_tokens
                                if item is not None}
                            != set(range(
                                previous_checkpoint.get(
                                    "next_token_id", 0),
                                checkpoint.get(
                                    "next_token_id", 0)))))):
                    raise RegistryConflict(
                        "repair checkpoint lacks its exact repair/delta closure")
            elif (not isinstance(delta_ref, Mapping)
                    or not isinstance(transition_refs, list)
                    or not transition_refs):
                raise RegistryConflict(
                    "noninitial marking checkpoint requires its firing settlement delta")
        else:
            prior_marking_rows = db.execute(
                "SELECT event_id FROM events WHERE task_id=? "
                "AND net_instance_id=? "
                "AND event_type='marking_checkpoint_committed/v1'",
                (str(task_id), str(net_instance_id))).fetchall()
            adopted_rows = db.execute(
                "SELECT event_id FROM events WHERE task_id=? "
                "AND event_type='net_adopted/v1' AND aggregate_id=?",
                (str(task_id), str(net_instance_id))).fetchall()
            if (any(persisted_member_visible(
                    "event", str(row["event_id"]))
                    for row in prior_marking_rows)
                    or any(persisted_member_visible(
                        "event", str(row["event_id"]))
                        for row in adopted_rows)):
                raise RegistryConflict(
                    "only pre-adoption net initialization may omit a prior marking head")

    if event_type == "invocation_started/v1":
        invocation_ref = payload.get("invocation_ref")
        if not isinstance(invocation_ref, Mapping):
            raise RegistryConflict(
                "invocation start requires an exact invocation version ref")
        invocation_id = str(invocation_ref.get("logical_id", ""))
        invocation_version = str(invocation_ref.get("version_id", ""))
        invocation = object_metadata(invocation_id, "invocation/v1")
        if (pending.aggregate_id != invocation_id or invocation is None
                or invocation.get("invocation_ref") != invocation_ref
                or invocation.get("context_digest") != payload.get("context_digest")
                or not version_exists(invocation_version, "invocation/v1")):
            raise RegistryConflict(
                "invocation start does not match one exact registered invocation")
        if task_round_id is None or net_instance_id is None:
            raise RegistryConflict("invocation start requires exact round and net refs")
        round_meta = object_metadata(str(task_round_id), "task_round/v1")
        net_meta = object_metadata(str(net_instance_id), "net_instance/v1")
        round_ref = invocation.get("task_round_ref", {})
        net_ref = invocation.get("net_instance_ref", {})
        if (round_meta is None or net_meta is None
                or round_ref.get("logical_id") != str(task_round_id)
                or net_ref.get("logical_id") != str(net_instance_id)):
            raise RegistryConflict("invocation start round/net refs are not registered")

    if event_type == "operation_terminal_ready/v1":
        result_ref = payload.get("operation_result_ref", {})
        invocation_ref = payload.get("invocation_ref")
        result = version_metadata(
            str(result_ref.get("version_id", "")),
            "operation_result/v1")
        output_values = payload.get("output_resource_refs")
        output_refs = (
            isinstance(output_values, list)
            and all(exact_ref_exists(value)
                    for value in output_values)
            and len({canonical_json(value)
                     for value in output_values})
            == len(output_values)
        )
        if (not exact_ref_exists(invocation_ref, "invocation/v1")
                or not exact_ref_exists(result_ref, "operation_result/v1")
                or not exact_ref_exists(
                    payload.get("operation_execution_lease_ref"),
                    "operation_execution_lease/v1")
                or result is None
                or result.get("invocation_ref") != invocation_ref
                or result.get("business_outcome")
                != payload.get("business_outcome")
                or payload.get("business_outcome") != "completed"
                or result.get("output_resource_refs")
                != payload.get("output_resource_refs")
                or (payload.get("output_resource_refs")
                    and not output_refs)):
            raise RegistryConflict(
                "terminal-ready differs from its exact current result")

def validate_authoritative_references(
        event_store, db: sqlite3.Connection, *, task_id: TypedId, branch_id: str,
        task_round_id: TypedId | None, net_instance_id: TypedId | None,
        transaction_id: TypedId, idempotency_key: str, transaction_writer_epoch: int,
        objects: Sequence[PreparedObject], events: Sequence[PendingEvent],
        relations: Sequence[TypedRelation]) -> None:
    """Run authoritative domains in their original transaction order."""
    from ..proposal import TransactionValidationContext
    from . import native_resume, operation, provider, resources, structural
    context = TransactionValidationContext(
        db, objects, events, relations, event_store=event_store, task_id=task_id,
        branch_id=branch_id, task_round_id=task_round_id, net_instance_id=net_instance_id,
        transaction_id=transaction_id, idempotency_key=idempotency_key,
        transaction_writer_epoch=transaction_writer_epoch)
    context.validate_reference_visibility()
    from ...execution_child_closure import validate_child_seal_publication
    validate_child_seal_publication(context)
    # Skip only a known-empty native batch; custom sequences still reach validation.
    if not ((type(objects) is tuple or type(objects) is list) and len(objects) == 0):
        from ..workset_publication import validate_workset_publication
        validate_workset_publication(context)
    from ..source_identity import uses_source_binding_namespace, validate_source_binding
    if uses_source_binding_namespace(objects, events, task_id):
        validate_source_binding(context)
    from ...observer_access import validate_observer_publications
    validate_observer_publications(context)
    if objects or any(getattr(event, "stream_id", "").startswith("object:")
                      or event.event_type == "object_version_published/v1" for event in events):
        from ..branch_publication import validate_branch_publication
        validate_branch_publication(context)
        from ..source_sets import validate_source_set_publication
        validate_source_set_publication(context)
        from ..source_observations import validate_observation_publication
        validate_observation_publication(context)
    provider.validate_provider_provenance(context)
    provider.validate_attempt_response_boundary(context)
    operation.validate_operation_contract_objects(context)
    native_resume.validate_native_resume_closure(context)
    resources.validate_resource_objects(context)
    structural.validate_structural_growth_pair_cas(context)
    context.native_resume_superseded_firing_ids = {
        str(value.payload["superseded_transition_firing_ref"].get("logical_id", ""))
        for value in events if (value.event_type == "transition_firing_superseded_by_native_resume/v1"
        and isinstance(value.payload.get("superseded_transition_firing_ref"), Mapping))}
    for pending in events:
        validate_firing_event(context, pending)
        if pending.event_type == "operation_terminal_ready/v1":
            continue
        provider.validate_provider_pre_resource_event(context, pending)
        resources.validate_resource_event(context, pending)
        provider.validate_registered_host_llm_event(context, pending)
        provider.validate_provider_attempt_event(context, pending)
        operation.validate_operation_event(context, pending)
        provider.validate_provider_outcome_event(context, pending)


def validate_lifecycle(db: sqlite3.Connection,
                        events: Sequence[PendingEvent]) -> None:
    """Reject impossible state transitions before any canonical write."""
    from ...event_store import RegistryConflict

    provider_allowed = {
        None: {"provider_attempt_reserved/v1"},
            "provider_attempt_reserved/v1": {
                "provider_attempt_dispatch_started/v1",
                "provider_attempt_dispatch_started/v2",
                "provider_attempt_cancelled_before_submission/v1",
                "provider_attempt_cancelled_before_submission/v2",
                "provider_attempt_host_closed/v1"},
        "provider_attempt_dispatch_started/v1": {
            "provider_attempt_submission_permitted/v1",
            "provider_attempt_submission_not_permitted/v1"},
            "provider_attempt_dispatch_started/v2": {
                "provider_attempt_submission_permitted/v2",
                "provider_attempt_submission_not_permitted/v1",
                "provider_attempt_host_closed/v1"},
        "provider_attempt_submission_permitted/v1": {
            "provider_attempt_submission_observed/v1",
            "provider_attempt_submission_unknown/v1"},
            "provider_attempt_submission_permitted/v2": {
                "provider_attempt_submission_observed/v1",
                "provider_attempt_submission_unknown/v1",
                "provider_attempt_owner_interrupted/v1",
                "provider_attempt_host_closed/v1"},
        "provider_attempt_submission_unknown/v1": {
            "provider_attempt_proven_not_submitted/v1",
            "provider_attempt_proven_submitted/v1",
            "provider_attempt_reconciled_cancelled_after_submission/v1"},
            "provider_attempt_submission_observed/v1": {
                "provider_attempt_completed/v1", "provider_attempt_failed/v1",
                "provider_attempt_cancelled_after_submission/v1",
                "provider_attempt_outcome_unknown/v1",
                "provider_attempt_host_closed/v1"},
        "provider_attempt_proven_submitted/v1": {
            "provider_attempt_completed/v1", "provider_attempt_failed/v1",
            "provider_attempt_cancelled_after_submission/v1",
            "provider_attempt_outcome_unknown/v1"},
        "provider_attempt_outcome_unknown/v1": {
            "provider_attempt_reconciled_completed/v1",
            "provider_attempt_reconciled_failed/v1",
            "provider_attempt_reconciled_cancelled_after_submission/v1"},
        "provider_attempt_cancelled_before_submission/v1": set(),
        "provider_attempt_cancelled_before_submission/v2": set(),
        "provider_attempt_submission_not_permitted/v1": set(),
        "provider_attempt_proven_not_submitted/v1": set(),
        "provider_attempt_completed/v1": set(),
        "provider_attempt_failed/v1": set(),
        "provider_attempt_cancelled_after_submission/v1": set(),
        "provider_attempt_reconciled_completed/v1": set(),
        "provider_attempt_reconciled_failed/v1": set(),
        "provider_attempt_reconciled_cancelled_after_submission/v1": set(),
            "provider_attempt_owner_interrupted/v1": set(),
            "provider_attempt_host_closed/v1": set(),
    }
    capability_allowed = {
        None: {"capability_issued/v1"},
        "capability_issued/v1": {"capability_activated/v1", "capability_revoked/v1"},
        "capability_activated/v1": {
            "capability_allowed/v1", "capability_denied/v1", "capability_revoked/v1"},
        "capability_allowed/v1": {
            "capability_allowed/v1", "capability_denied/v1", "capability_revoked/v1"},
        "capability_denied/v1": {
            "capability_allowed/v1", "capability_denied/v1", "capability_revoked/v1"},
        "capability_revoked/v1": set(),
    }
    delivery_allowed = {
        None: {"resource_delivery_prepared/v1"},
        "resource_delivery_prepared/v1": {
            "resource_release_authorized/v1", "resource_delivery_failed/v1"},
        "resource_release_authorized/v1": {
            "resource_delivery_acknowledged/v1", "resource_delivery_failed/v1",
            "resource_delivery_unknown/v1"},
        "resource_delivery_acknowledged/v1": set(),
        "resource_delivery_failed/v1": set(),
        "resource_delivery_unknown/v1": set(),
    }
    state: dict[tuple[str, str], str | None] = {}

    llm_call_allowed = {
        None: {"llm_call_result_adopted/v1",
               "llm_call_candidate_not_adopted/v1",
               "llm_call_failed/v1",
               "llm_call_owner_interrupted/v1",
               "llm_call_submission_unknown/v1"},
        "llm_call_submission_unknown/v1": {
            "llm_call_result_adopted/v1",
            "llm_call_candidate_not_adopted/v1",
            "llm_call_failed/v1",
        },
        "llm_call_result_adopted/v1": set(),
        "llm_call_candidate_not_adopted/v1": set(),
        "llm_call_failed/v1": set(),
        "llm_call_owner_interrupted/v1": set(),
    }

    def current(family: str, aggregate_id: str, prefix: str) -> str | None:
        key = (family, aggregate_id)
        if key not in state:
            row = db.execute(
                "SELECT event_type FROM events WHERE aggregate_id=? "
                "AND event_type LIKE ? ORDER BY ordinal DESC LIMIT 1",
                (aggregate_id, f"{prefix}%")).fetchone()
            state[key] = row[0] if row else None
        return state[key]

    for pending in events:
        if pending.event_type.startswith("provider_attempt_"):
            family, allowed, prefix = "provider", provider_allowed, "provider_attempt_"
        elif pending.event_type.startswith("capability_"):
            family, allowed, prefix = "capability", capability_allowed, "capability_"
        elif (pending.event_type in llm_call_allowed[None]
              or pending.event_type
              in llm_call_allowed["llm_call_submission_unknown/v1"]):
            family, allowed, prefix = "llm_call", llm_call_allowed, "llm_call_"
        elif pending.event_type in {
                "resource_delivery_prepared/v1",
                "resource_release_authorized/v1",
                "resource_delivery_acknowledged/v1",
                "resource_delivery_failed/v1",
                "resource_delivery_unknown/v1"}:
            family, allowed, prefix = "resource_delivery", delivery_allowed, "resource_"
        else:
            continue
        prior = current(family, pending.aggregate_id, prefix)
        if pending.event_type not in allowed.get(prior, set()):
            raise RegistryConflict(
                f"invalid {family} lifecycle {prior!r} -> {pending.event_type!r} "
                f"for {pending.aggregate_id}")
        state[(family, pending.aggregate_id)] = pending.event_type


__all__ = (
    "validate_authoritative_references",
    "validate_firing_event",
    "validate_firing_resource_settlement_atomicity",
    "validate_lifecycle",
)
