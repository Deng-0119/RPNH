"""Authoritative transaction-domain validation."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ... import event_store as facade
from ...models import PendingEvent

def historical_validate_fault_mechanical_event_closure(context) -> None:
    db = context.db
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    new_by_version = context.new_by_version
    objects = context.objects
    persisted_member_visible = context.persisted_member_visible
    task_id = context.task_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    version_metadata = context.version_metadata
    version_transaction = context.version_transaction
    RegistryConflict = facade.RegistryConflict
    _CANONICAL_EVENT_SQL = facade._CANONICAL_EVENT_SQL
    _HISTORICAL_FAULT_MECHANICAL_ADMISSION_FIELDS = facade._HISTORICAL_FAULT_MECHANICAL_ADMISSION_FIELDS
    _HISTORICAL_FAULT_MECHANICAL_COMPLETION_FIELDS = facade._HISTORICAL_FAULT_MECHANICAL_COMPLETION_FIELDS
    canonical_json = facade.canonical_json
    canonical_text = facade.canonical_text
    """Close the sidecar producer without manufacturing business facts."""

    admitted_events = [
        value for value in events
        if value.event_type == "fault_mechanical_firing_admitted/v1"
    ]
    completed_events = [
        value for value in events
        if value.event_type == "fault_mechanical_firing_completed/v1"
    ]
    settled_events = [
        value for value in events
        if value.event_type == "fault_mechanical_firing_settled/v1"
    ]
    if not (admitted_events or completed_events or settled_events):
        return

    def exact_sidecar_envelope(
            pending: PendingEvent, firing_ref: Mapping[str, Any],
            ) -> bool:
        firing_id = str(firing_ref.get("logical_id", ""))
        return bool(
            pending.criticality == "authoritative"
            and pending.stream_id
            == f"fault-mechanical-firing:{firing_id}"
            and pending.aggregate_id == firing_id
            and pending.aggregate_type
            == "fault_mechanical_firing/v1"
            and pending.idempotency_key == idempotency_key
            and pending.command_id == idempotency_key
            and pending.payload_schema_ref
            == f"registry_v1/{pending.event_type}"
            and pending.task_control
            and pending.producer_principal == "framework"
            and pending.producer_invocation_id is None
            and pending.causation_event_id is None
            and not pending.parent_event_ids
            and pending.occurred_at is None)

    if admitted_events and (completed_events or settled_events):
        raise RegistryConflict(
            "fault mechanical admission and settlement require separate commands")

    pending_claims: list[tuple[str, set[bytes]]] = []
    for admitted in admitted_events:
        firing_ref = admitted.payload.get(
            "fault_mechanical_firing_ref")
        if not isinstance(firing_ref, Mapping):
            raise RegistryConflict(
                "fault mechanical admission lacks its exact firing ref")
        firing_version = str(firing_ref.get("version_id", ""))
        firing_item = new_by_version.get(firing_version)
        firing = version_metadata(
            firing_version, "fault_mechanical_firing/v1")
        if (firing_item is None
                or firing_item.object_type
                != "fault_mechanical_firing/v1"
                or firing is None
                or firing.get("fault_mechanical_firing_ref")
                != firing_ref):
            raise RegistryConflict(
                "fault mechanical admission must publish its firing object")
        expected_payload = {
            **{
                name: firing.get(name)
                for name in _HISTORICAL_FAULT_MECHANICAL_ADMISSION_FIELDS
            },
            "fault_mechanical_firing_ref": firing_ref,
            "admission_closure_digest": firing.get(
                "admission_closure_digest"),
        }
        delta_ref = firing.get("claim_marking_delta_ref", {})
        delta_item = new_by_version.get(str(
            delta_ref.get("version_id", "")))
        prior_rows = db.execute(
            "SELECT event_id FROM events WHERE aggregate_id=? AND "
            "event_type='fault_mechanical_firing_admitted/v1'",
            (str(firing_ref.get("logical_id", "")),),
        ).fetchall()
        prior = any(persisted_member_visible(
            "event", str(row["event_id"])) for row in prior_rows)
        duplicates = [
            value for value in admitted_events
            if value is not admitted
            and value.aggregate_id == admitted.aggregate_id
        ]
        if (dict(admitted.payload) != expected_payload
                or not exact_sidecar_envelope(admitted, firing_ref)
                or prior or duplicates
                or delta_item is None
                or delta_item.object_type != "marking_delta/v1"
                or delta_item.producer_invocation_id is not None):
            raise RegistryConflict(
                "fault mechanical firing admission is not one idempotent "
                "object/event claim")

        checkpoint_ref = firing.get("admission_checkpoint_ref", {})
        marking_head = db.execute(
            "SELECT e.payload_json FROM events e WHERE e.task_id=? "
            "AND e.net_instance_id=? "
            "AND e.event_type='marking_checkpoint_committed/v1' AND "
            f"{_CANONICAL_EVENT_SQL} "
            "ORDER BY task_control_sequence DESC LIMIT 1",
            (str(task_id), str(net_instance_id)),
        ).fetchone()
        active_net = db.execute(
            "SELECT e.payload_json FROM events e WHERE e.task_id=? "
            "AND e.event_type='net_adopted/v1' AND "
            f"{_CANONICAL_EVENT_SQL} "
            "ORDER BY task_control_sequence DESC LIMIT 1",
            (str(task_id),),
        ).fetchone()
        if (net_instance_id is None
                or firing.get("net_instance_ref", {}).get("logical_id")
                != str(net_instance_id)
                or marking_head is None
                or json.loads(marking_head["payload_json"]).get(
                    "checkpoint_ref") != checkpoint_ref
                or active_net is None
                or json.loads(active_net["payload_json"]).get(
                    "net_instance_ref") != firing.get(
                        "net_instance_ref")):
            raise RegistryConflict(
                "fault mechanical admission does not claim the active marking")

        requested = {
            canonical_json(value)
            for value in firing.get("claimed_token_refs", [])
        }
        active_rows = db.execute(
            "SELECT o.logical_id,o.metadata_json FROM objects o "
            "JOIN events admitted ON admitted.aggregate_id=o.logical_id "
            "AND admitted.event_type="
            "'fault_mechanical_firing_admitted/v1' "
            "LEFT JOIN events settled ON settled.aggregate_id=o.logical_id "
            "AND settled.event_type="
            "'fault_mechanical_firing_settled/v1' "
            "WHERE o.object_type='fault_mechanical_firing/v1' "
            "AND admitted.net_instance_id=? AND settled.event_id IS NULL",
            (str(net_instance_id),),
        ).fetchall()
        active_claims = [
            (str(row["logical_id"]), {
                canonical_json(value) for value in json.loads(
                    row["metadata_json"]).get(
                        "claimed_token_refs", [])
            })
            for row in active_rows
        ] + pending_claims
        for active_id, active_refs in active_claims:
            overlap = requested & active_refs
            if overlap:
                raise RegistryConflict(
                    "fault mechanical firing conflicts with active claim "
                    f"{active_id}")
        pending_claims.append((admitted.aggregate_id, requested))

    if not (completed_events or settled_events):
        return
    completed_by_firing: dict[str, PendingEvent] = {}
    settled_by_firing: dict[str, PendingEvent] = {}
    for collection, target in (
            (completed_events, completed_by_firing),
            (settled_events, settled_by_firing)):
        for value in collection:
            firing_ref = value.payload.get(
                "fault_mechanical_firing_ref")
            firing_id = (str(firing_ref.get("logical_id", ""))
                         if isinstance(firing_ref, Mapping) else "")
            if not firing_id or firing_id in target:
                raise RegistryConflict(
                    "fault mechanical settlement repeats a firing event")
            target[firing_id] = value
    if (set(completed_by_firing) != set(settled_by_firing)
            or not completed_by_firing):
        raise RegistryConflict(
            "fault mechanical completion and settlement must be an exact pair")

    settlement_metadata: list[Mapping[str, Any]] = []
    for firing_id, completed in completed_by_firing.items():
        settled = settled_by_firing[firing_id]
        firing_ref = completed.payload.get(
            "fault_mechanical_firing_ref")
        settlement_ref = completed.payload.get(
            "fault_mechanical_settlement_ref")
        if (not isinstance(firing_ref, Mapping)
                or not isinstance(settlement_ref, Mapping)):
            raise RegistryConflict(
                "fault mechanical settlement lacks exact producer refs")
        firing = version_metadata(
            str(firing_ref.get("version_id", "")),
            "fault_mechanical_firing/v1")
        settlement_version = str(settlement_ref.get("version_id", ""))
        settlement_item = new_by_version.get(settlement_version)
        settlement = version_metadata(
            settlement_version, "fault_mechanical_settlement/v1")
        common = ({
            name: settlement.get(name)
            for name in _HISTORICAL_FAULT_MECHANICAL_COMPLETION_FIELDS
        } if isinstance(settlement, Mapping) else {})
        completion_digest = canonical_text(common)
        expected_completed = {
            **common,
            "completion_closure_digest": completion_digest,
        }
        settlement_digest = canonical_text({
            **common,
            "completion_closure_digest": completion_digest,
        })
        expected_settled = {
            **expected_completed,
            "settlement_closure_digest": settlement_digest,
        }
        prior = db.execute(
            "SELECT event_id,event_type FROM events WHERE aggregate_id=? "
            "AND event_type IN "
            "('fault_mechanical_firing_completed/v1',"
            "'fault_mechanical_firing_settled/v1')",
            (firing_id,),
        ).fetchall()
        admitted_rows = db.execute(
            "SELECT e.event_id,e.payload_json,e.transaction_id,"
            "t.idempotency_key "
            "FROM events e JOIN transactions t "
            "ON t.transaction_id=e.transaction_id "
            "WHERE e.aggregate_id=? AND e.event_type="
            "'fault_mechanical_firing_admitted/v1'",
            (firing_id,),
        ).fetchall()
        prior = [row for row in prior if persisted_member_visible(
            "event", str(row["event_id"]))]
        admitted_rows = [
            row for row in admitted_rows if persisted_member_visible(
                "event", str(row["event_id"]))]
        expected_admitted_payload = ({
            **{
                name: firing.get(name)
                for name in _HISTORICAL_FAULT_MECHANICAL_ADMISSION_FIELDS
            },
            "fault_mechanical_firing_ref": firing_ref,
            "admission_closure_digest": firing.get(
                "admission_closure_digest"),
        } if isinstance(firing, Mapping) else {})
        if (firing is None or settlement is None
                or settlement_item is None
                or settlement_item.object_type
                != "fault_mechanical_settlement/v1"
                or settlement.get("fault_mechanical_settlement_ref")
                != settlement_ref
                or settlement.get("fault_mechanical_firing_ref")
                != firing_ref
                or settlement.get("completion_closure_digest")
                != completion_digest
                or dict(completed.payload) != expected_completed
                or dict(settled.payload) != expected_settled
                or events.index(completed) >= events.index(settled)
                or not exact_sidecar_envelope(completed, firing_ref)
                or not exact_sidecar_envelope(settled, firing_ref)
                or prior or len(admitted_rows) != 1
                or json.loads(admitted_rows[0]["payload_json"])
                != expected_admitted_payload
                or str(admitted_rows[0]["transaction_id"])
                == str(transaction_id)):
            raise RegistryConflict(
                "fault mechanical settlement is not the unique descendant "
                "of its admitted firing")
        for ref_name, expected_type in (
                ("settlement_checkpoint_ref", "marking_checkpoint/v1"),
                ("settlement_marking_delta_ref", "marking_delta/v1")):
            ref = settlement.get(ref_name, {})
            if (not exact_ref_exists(ref, expected_type)
                    or version_transaction(str(ref.get(
                        "version_id", ""))) != str(transaction_id)):
                raise RegistryConflict(
                    "fault mechanical settlement checkpoint/delta was not "
                    "published atomically")
        if any(version_transaction(str(ref.get("version_id", "")))
               != str(transaction_id)
               for ref in settlement.get("emitted_token_refs", [])):
            raise RegistryConflict(
                "fault mechanical emitted tokens were not published atomically")
        settlement_metadata.append(settlement)

    delta_refs = {
        canonical_json(value.get("settlement_marking_delta_ref"))
        for value in settlement_metadata
    }
    checkpoint_refs = {
        canonical_json(value.get("settlement_checkpoint_ref"))
        for value in settlement_metadata
    }
    if len(delta_refs) != 1 or len(checkpoint_refs) != 1:
        raise RegistryConflict(
            "fault mechanical batch does not share one delta/checkpoint")
    mechanical_delta_ref = settlement_metadata[0][
        "settlement_marking_delta_ref"]
    mechanical_checkpoint_ref = settlement_metadata[0][
        "settlement_checkpoint_ref"]
    delta = version_metadata(
        str(mechanical_delta_ref.get("version_id", "")),
        "marking_delta/v1")
    checkpoint = version_metadata(
        str(mechanical_checkpoint_ref.get("version_id", "")),
        "marking_checkpoint/v1")
    mechanical_firing_refs = sorted((
        value["fault_mechanical_firing_ref"]
        for value in settlement_metadata), key=canonical_json)
    mechanical_claimed_refs = sorted((
        ref
        for value in settlement_metadata
        for ref in version_metadata(
            str(value["fault_mechanical_firing_ref"].get(
                "version_id", "")),
            "fault_mechanical_firing/v1"
        ).get("claimed_token_refs", [])
    ), key=canonical_json)
    mechanical_emitted_refs = sorted((
        ref for value in settlement_metadata
        for ref in value.get("emitted_token_refs", [])
    ), key=canonical_json)
    mechanical_control_refs = sorted((
        value["control_ref"] for value in settlement_metadata
    ), key=canonical_json)
    business_firing_refs = (
        delta.get("transition_firing_refs", [])
        if isinstance(delta, Mapping) else [])
    business_firings = [
        version_metadata(
            str(ref.get("version_id", "")),
            "transition_firing/v1")
        for ref in business_firing_refs
        if isinstance(ref, Mapping)
    ]
    business_claimed_refs = sorted((
        ref for value in business_firings
        if isinstance(value, Mapping)
        for ref in value.get("claimed_input_refs", [])
    ), key=canonical_json)
    expected_consumed_refs = sorted(
        [*business_claimed_refs, *mechanical_claimed_refs],
        key=canonical_json)
    expected_binding_refs = sorted({
        canonical_json(value.get("operation_binding_ref")):
        value.get("operation_binding_ref")
        for value in business_firings
        if isinstance(value, Mapping)
    }.values(), key=canonical_json)
    business_fault_refs = [
        value.payload.get("fault_ref") for value in events
        if (value.event_type == "transition_firing_settled/v1"
            and value.payload.get("fault_ref") is not None)
    ]
    expected_unknown_refs = sorted({
        canonical_json(ref): ref
        for ref in (
            value.payload.get("provider_submission_unknown_ref")
            for value in events
            if value.event_type == "transition_firing_settled/v1")
        if ref is not None
    }.values(), key=canonical_json)
    expected_fault_refs = sorted({
        canonical_json(ref): ref
        for ref in [*business_fault_refs, *mechanical_control_refs]
    }.values(), key=canonical_json)
    transaction_token_refs = sorted(({
        "entity_type": "petri_token/v1",
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
    } for item in objects
        if item.object_type == "petri_token/v1"), key=canonical_json)
    if (delta is None or checkpoint is None
            or len(business_firings) != len(business_firing_refs)
            or any(value is None for value in business_firings)
            or delta.get("mechanical_firing_refs")
            != mechanical_firing_refs
            or checkpoint.get("mechanical_firing_refs")
            != mechanical_firing_refs
            or checkpoint.get("transition_firing_refs")
            != business_firing_refs
            or delta.get("consumed_refs") != expected_consumed_refs
            or delta.get("operation_binding_refs")
            != expected_binding_refs
            or delta.get("deposited_refs")
            != transaction_token_refs
            or any(ref not in transaction_token_refs
                   for ref in mechanical_emitted_refs)
            or delta.get("operation_fault_refs")
            != expected_fault_refs
            or delta.get("provider_submission_unknown_refs")
            != expected_unknown_refs):
        raise RegistryConflict(
            "fault mechanical batch discriminant/token closure is not exact")
    for business in (
            value for value in events
            if value.event_type == "transition_firing_settled/v1"):
        if (business.payload.get("settlement_marking_delta_ref")
                != mechanical_delta_ref
                or business.payload.get(
                    "settlement_marking_checkpoint_ref")
                != mechanical_checkpoint_ref):
            raise RegistryConflict(
                "mixed business/mechanical batch must share one "
                "delta/checkpoint")
    checkpoint_commits = [
        value for value in events
        if (value.event_type == "marking_checkpoint_committed/v1"
            and value.payload.get("checkpoint_ref")
            == mechanical_checkpoint_ref)
    ]
    if (len(checkpoint_commits) != 1
            or checkpoint_commits[0].payload.get(
                "settlement_delta_ref") != mechanical_delta_ref):
        raise RegistryConflict(
            "fault mechanical settlement lacks its atomic marking commit")

__all__ = ('historical_validate_fault_mechanical_event_closure',)
