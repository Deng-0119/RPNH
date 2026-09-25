"""Authoritative transaction-domain validation."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ... import event_store as facade

def validate_structural_growth_pair_cas(context) -> None:
    db = context.db
    events = context.events
    new_by_version = context.new_by_version
    objects = context.objects
    persisted_member_visible = context.persisted_member_visible
    task_id = context.task_id
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    version_metadata = context.version_metadata
    EffectDeclarationError = facade.EffectDeclarationError
    RegistryConflict = facade.RegistryConflict
    _CANONICAL_EVENT_SQL = facade._CANONICAL_EVENT_SQL
    _structural_growth_design_adoption_issues = facade._structural_growth_design_adoption_issues
    structural_effect_declaration = facade.structural_effect_declaration
    """Close structural-growth net/marking authority in this DB snapshot."""

    structural = [
        value for value in events
        if value.event_type == "structural_growth_adopted/v1"
    ]
    if not structural:
        return
    if len(structural) != 1:
        raise RegistryConflict(
            "structural growth requires one authoritative adoption fact")
    event = structural[0]
    payload = event.payload
    predecessor_net = payload.get("predecessor_net_ref")
    predecessor_checkpoint = payload.get(
        "predecessor_marking_checkpoint_ref")
    candidate_net = payload.get("candidate_net_ref")
    candidate_checkpoint = payload.get(
        "candidate_marking_checkpoint_ref")
    design_firing_ref = payload.get("settled_design_firing_ref")
    handoff_token_ref = payload.get("design_handoff_token_ref")

    net_events = [
        value for value in events
        if value.event_type == "net_adopted/v1"
    ]
    marking_events = [
        value for value in events
        if (value.event_type == "marking_checkpoint_committed/v1"
            and value.payload.get("net_instance_ref") == candidate_net)
    ]
    if (len(net_events) != 1 or len(marking_events) != 1
            or net_events[0].payload.get("supersedes_net_ref")
            != predecessor_net
            or net_events[0].payload.get("net_instance_ref")
            != candidate_net
            or marking_events[0].payload.get("checkpoint_ref")
            != candidate_checkpoint
            or marking_events[0].payload.get(
                "previous_checkpoint_ref")
            != predecessor_checkpoint):
        raise RegistryConflict(
            "structural growth requires one matched net/checkpoint commit pair")

    active_row = db.execute(
        "SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.event_type='net_adopted/v1' AND "
        f"{_CANONICAL_EVENT_SQL} "
        "ORDER BY e.task_control_sequence DESC LIMIT 1",
        (str(task_id),),
    ).fetchone()
    predecessor_net_id = str(
        (predecessor_net or {}).get("logical_id", ""))
    marking_row = db.execute(
        "SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.net_instance_id=? "
        "AND e.event_type='marking_checkpoint_committed/v1' AND "
        f"{_CANONICAL_EVENT_SQL} "
        "ORDER BY e.task_control_sequence DESC LIMIT 1",
        (str(task_id), predecessor_net_id),
    ).fetchone()
    active = (json.loads(active_row["payload_json"])
              if active_row is not None else None)
    marking = (json.loads(marking_row["payload_json"])
               if marking_row is not None else None)
    if (active is None
            or active.get("net_instance_ref") != predecessor_net
            or marking is None
            or marking.get("checkpoint_ref")
            != predecessor_checkpoint):
        raise RegistryConflict(
            "structural growth predecessor net/marking pair is stale")

    def exact_resource(value: object, schema_ref: str) -> bool:
        if not isinstance(value, Mapping):
            return False
        metadata = version_metadata(
            str(value.get("resource_version_id", "")),
            "resource_version/v1",
        )
        return (metadata is not None
                and metadata.get("resource_id")
                == value.get("resource_id")
                and metadata.get("resource_version_id")
                == value.get("resource_version_id")
                and metadata.get("content_schema_ref") == schema_ref)

    checkpoint = version_metadata(
        str((candidate_checkpoint or {}).get("version_id", "")),
        "marking_checkpoint/v1",
    )
    handoff_token = version_metadata(
        str((handoff_token_ref or {}).get("version_id", "")),
        "petri_token/v1",
    )
    design_firing = version_metadata(
        str((design_firing_ref or {}).get("version_id", "")),
        "transition_firing/v1",
    )

    def transaction_visible_event_records(
            event_type: str, aggregate_id: object,
    ) -> tuple[Mapping[str, Any], ...]:
        """Read exact events as they will exist after this commit.

            Reference validation runs before insertion. Structural
            adoption closes T_design_adopt in this same transaction, so
            its finish and settlement events belong to the closure being
            validated even though they are not persisted yet.
            """
        aggregate = str(aggregate_id or "")
        rows = db.execute(
            "SELECT e.event_id,e.transaction_id,e.payload_json "
            "FROM events e WHERE e.event_type=? AND e.aggregate_id=?",
            (event_type, aggregate),
        ).fetchall()
        persisted = tuple({
            "transaction_id": str(row["transaction_id"]),
            "payload": json.loads(row["payload_json"]),
        } for row in rows if persisted_member_visible(
            "event", str(row["event_id"])))
        proposed = tuple({
            "transaction_id": str(transaction_id),
            "payload": dict(value.payload),
        } for value in events
            if (value.event_type == event_type
                and value.aggregate_id == aggregate))
        return (*persisted, *proposed)

    def ref_metadata(
            value: object, object_type: str,
    ) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        return version_metadata(
            str(value.get("version_id", "")), object_type)

    def resource_metadata(
            value: object,
    ) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        result = version_metadata(
            str(value.get("resource_version_id", "")),
            "resource_version/v1",
        )
        if (result is None
                or result.get("resource_id")
                != value.get("resource_id")
                or result.get("resource_version_id")
                != value.get("resource_version_id")):
            return None
        return result

    def object_transaction_id(
            value: object, object_type: str,
    ) -> str | None:
        if not isinstance(value, Mapping):
            return None
        proposed = new_by_version.get(str(value.get("version_id", "")))
        if (proposed is not None
                and proposed.object_type == object_type
                and str(proposed.logical_id)
                == str(value.get("logical_id", ""))):
            return str(transaction_id)
        rows = db.execute(
            "SELECT transaction_id FROM objects WHERE logical_id=? "
            "AND version_id=? AND object_type=?",
            (str(value.get("logical_id", "")),
             str(value.get("version_id", "")), object_type),
        ).fetchall()
        return (str(rows[0]["transaction_id"])
                if len(rows) == 1 else None)

    predecessor_checkpoint_metadata = ref_metadata(
        predecessor_checkpoint, "marking_checkpoint/v1")

    design_settlement_records = transaction_visible_event_records(
        "transition_firing_settled/v1",
        ((design_firing_ref or {}).get("logical_id")
         if isinstance(design_firing_ref, Mapping) else None),
    )
    design_settlements = tuple(
        value["payload"] for value in design_settlement_records)
    design_result_ref = (
        design_settlements[0].get("operation_result_ref")
        if len(design_settlements) == 1
        and isinstance(design_settlements[0], Mapping) else None)
    design_result = ref_metadata(
        design_result_ref, "operation_result/v1")
    design_outputs = (
        design_result.get("output_resource_refs", [])
        if isinstance(design_result, Mapping) else ())
    business_generic_ref = (
        design_outputs[0] if len(design_outputs) == 1
        and isinstance(design_outputs[0], Mapping) else None)
    business_ref = ({
        "resource_id": business_generic_ref.get("logical_id"),
        "resource_version_id": business_generic_ref.get("version_id"),
    } if isinstance(business_generic_ref, Mapping) else None)
    business_resource = resource_metadata(business_ref)

    candidate_transition_firings = (
        checkpoint.get("transition_firing_refs", [])
        if isinstance(checkpoint, Mapping) else ())
    adopt_firing_ref = (
        candidate_transition_firings[0]
        if isinstance(candidate_transition_firings, list)
        and len(candidate_transition_firings) == 1 else None)
    adopt_firing = ref_metadata(
        adopt_firing_ref, "transition_firing/v1")
    predecessor_metadata = ref_metadata(predecessor_net, "net_instance/v1")
    routes = tuple(
        (value, ref_metadata(value, "executable_transition_binding/v1"))
        for value in (predecessor_metadata or {}).get(
            "executable_transition_binding_refs", []))
    matching_routes = tuple((ref, value) for ref, value in routes
                            if isinstance(value, Mapping)
                            and isinstance(adopt_firing, Mapping)
                            and value.get("transition_id") == adopt_firing.get("transition_id")
                            and value.get("net_instance_ref") == predecessor_net
                            and value.get("operation_binding_ref") == adopt_firing.get("operation_binding_ref"))
    if len(matching_routes) != 1:
        raise RegistryConflict("structural effect lacks exact predecessor route membership")
    effect_route_ref, effect_route = matching_routes[0]
    effect_binding = ref_metadata(effect_route.get("operation_binding_ref"), "operation_binding/v1")
    effect_spec = ref_metadata((effect_binding or {}).get("operation_spec_ref"), "operation_spec/v1")
    try:
        effect = structural_effect_declaration(
            effect_route.get("effects"),
            (effect_spec or {}).get("implementation_contracts"))
    except EffectDeclarationError as exc:
        raise RegistryConflict(str(exc)) from exc
    if effect is None:
        raise RegistryConflict("structural adoption is not a registered declared effect")
    witness_contract = effect["witness"]
    adopt_settlements = transaction_visible_event_records(
        "transition_firing_settled/v1",
        (adopt_firing_ref.get("logical_id")
         if isinstance(adopt_firing_ref, Mapping) else None),
    )
    adopt_settlement = (
        adopt_settlements[0]["payload"]
        if len(adopt_settlements) == 1 else {})
    adopt_delta_ref = adopt_settlement.get("marking_delta_ref")
    adopt_delta = ref_metadata(adopt_delta_ref, "marking_delta/v1")
    adopt_result_ref = adopt_settlement.get("operation_result_ref")
    adopt_result = ref_metadata(
        adopt_result_ref, "operation_result/v1")
    deposited_token_metadata = tuple(
        ref_metadata(value, "petri_token/v1")
        for value in (
            adopt_delta.get("deposited_refs", [])
            if isinstance(adopt_delta, Mapping) else ()))
    source_handoffs = tuple(
        value for value in deposited_token_metadata
        if (isinstance(value, Mapping)
            and value.get("place") == witness_contract["handoff_place"]))
    adopt_receipts = tuple(
        value for value in deposited_token_metadata
        if (isinstance(value, Mapping)
            and value.get("place") == witness_contract["receipt_place"]))
    adopt_returned_capacities = tuple(
        value for value in deposited_token_metadata
        if (isinstance(value, Mapping)
            and value.get("place") == witness_contract["capacity_place"]
            and value.get("consumed_by") is None))

    receipt_token = (
        adopt_receipts[0] if len(adopt_receipts) == 1 else None)
    receipt_ref = (
        receipt_token.get("resource_ref")
        if isinstance(receipt_token, Mapping) else None)
    receipt_resource = resource_metadata(receipt_ref)
    adopt_invocation_ref = (
        adopt_result.get("invocation_ref")
        if isinstance(adopt_result, Mapping) else None)
    adopt_invocation = ref_metadata(
        adopt_invocation_ref, "invocation/v1")
    adopt_lease_ref = (
        adopt_invocation.get("operation_execution_lease_ref")
        if isinstance(adopt_invocation, Mapping) else None)
    adopt_starts = transaction_visible_event_records(
        "operation_execution_started/v1",
        (adopt_lease_ref.get("logical_id")
         if isinstance(adopt_lease_ref, Mapping) else None),
    )
    adopt_terminals = transaction_visible_event_records(
        "operation_terminal_ready/v1",
        (adopt_invocation_ref.get("logical_id")
         if isinstance(adopt_invocation_ref, Mapping) else None),
    )
    adopt_start = (
        adopt_starts[0]["payload"] if len(adopt_starts) == 1 else {})
    adopt_binding = ref_metadata(
        adopt_start.get("operation_binding_ref"),
        "operation_binding/v1")
    adopt_spec = ref_metadata(
        adopt_start.get("operation_spec_ref"), "operation_spec/v1")
    adopt_executable = ref_metadata(
        adopt_start.get("executable_transition_binding_ref"),
        "executable_transition_binding/v1")
    if (adopt_start.get("executable_transition_binding_ref") != effect_route_ref
            or adopt_executable != effect_route or adopt_spec != effect_spec
            or adopt_binding != effect_binding):
        raise RegistryConflict("structural effect registered route differs from invocation witnesses")
    adopt_claimed_tokens = tuple(
        ref_metadata(value, "petri_token/v1")
        for value in (
            adopt_firing.get("claimed_input_refs", [])
            if isinstance(adopt_firing, Mapping) else ()))

    adopt_inputs = adopt_start.get("input_resource_refs", [])
    decision_ref = (
        adopt_inputs[1] if isinstance(adopt_inputs, list)
        and len(adopt_inputs) == 2 else None)
    decision_resource = resource_metadata(decision_ref)
    critic_invocation_ref = (
        decision_resource.get("producer_ref")
        if isinstance(decision_resource, Mapping) else None)
    critic_invocation = ref_metadata(
        critic_invocation_ref, "invocation/v1")
    critic_lease_ref = (
        critic_invocation.get("operation_execution_lease_ref")
        if isinstance(critic_invocation, Mapping) else None)
    critic_starts = transaction_visible_event_records(
        "operation_execution_started/v1",
        (critic_lease_ref.get("logical_id")
         if isinstance(critic_lease_ref, Mapping) else None),
    )
    critic_terminals = transaction_visible_event_records(
        "operation_terminal_ready/v1",
        (critic_invocation_ref.get("logical_id")
         if isinstance(critic_invocation_ref, Mapping) else None),
    )
    critic_start = (
        critic_starts[0]["payload"]
        if len(critic_starts) == 1 else {})
    critic_firing = ref_metadata(
        critic_start.get("transition_firing_ref"),
        "transition_firing/v1")
    critic_spec = ref_metadata(
        critic_start.get("operation_spec_ref"), "operation_spec/v1")
    candidate_token_metadata = tuple(
        ref_metadata(value, "petri_token/v1")
        for value in (
            checkpoint.get("token_refs", [])
            if isinstance(checkpoint, Mapping) else ()))
    migrated_receipts = tuple(
        value for value in candidate_token_metadata
        if (isinstance(value, Mapping)
            and value.get("place") == witness_contract["receipt_place"]))
    migrated_capacities = tuple(
        value for value in candidate_token_metadata
        if (isinstance(value, Mapping)
            and value.get("place") == witness_contract["capacity_place"]
            and value.get("consumed_by") is None))

    closure = {
        "predecessor_net_ref": predecessor_net,
        "predecessor_checkpoint_ref": predecessor_checkpoint,
        "candidate_net_ref": candidate_net,
        "candidate_checkpoint_ref": candidate_checkpoint,
        "settled_design_firing_ref": design_firing_ref,
        "design_firing": design_firing,
        "design_settlements": design_settlements,
        "design_result": design_result,
        "business_resource_ref": business_ref,
        "business_resource": business_resource,
        "predecessor_checkpoint": predecessor_checkpoint_metadata,
        "source_handoff_tokens": source_handoffs,
        "adopt_receipt_tokens": adopt_receipts,
        "adopt_returned_capacity_tokens": adopt_returned_capacities,
        "receipt_resource": receipt_resource,
        "adopt_invocation_ref": adopt_invocation_ref,
        "adopt_invocation": adopt_invocation,
        "adopt_starts": adopt_starts,
        "adopt_terminals": adopt_terminals,
        "adopt_settlements": adopt_settlements,
        "adopt_firing": adopt_firing,
        "adopt_binding": adopt_binding,
        "adopt_spec": adopt_spec,
        "adopt_executable": adopt_executable,
        "adopt_result": adopt_result,
        "adopt_delta": adopt_delta,
        "adopt_claimed_tokens": adopt_claimed_tokens,
        "decision_resource_ref": decision_ref,
        "decision_resource": decision_resource,
        "critic_invocation_ref": critic_invocation_ref,
        "critic_invocation": critic_invocation,
        "critic_starts": critic_starts,
        "critic_terminals": critic_terminals,
        "critic_firing": critic_firing,
        "critic_spec": critic_spec,
        "migrated_handoff_token": handoff_token,
        "migrated_receipt_token": (
            migrated_receipts[0]
            if len(migrated_receipts) == 1 else None),
        "migrated_capacity_token": (
            migrated_capacities[0]
            if len(migrated_capacities) == 1 else None),
        "candidate_checkpoint": checkpoint,
        "design_index_ref": payload.get("design_index_ref"),
        "adopt_delta_transaction_id": object_transaction_id(
            adopt_delta_ref, "marking_delta/v1"),
        "candidate_checkpoint_transaction_id":
            object_transaction_id(
                candidate_checkpoint, "marking_checkpoint/v1"),
        "source_handoff_transaction_id": object_transaction_id(
            (source_handoffs[0].get("petri_token_ref")
             if len(source_handoffs) == 1 else None),
            "petri_token/v1"),
        "adopt_receipt_token_transaction_id": object_transaction_id(
            (adopt_receipts[0].get("petri_token_ref")
             if len(adopt_receipts) == 1 else None),
            "petri_token/v1"),
        "adopt_returned_capacity_transaction_id":
            object_transaction_id(
                (adopt_returned_capacities[0].get("petri_token_ref")
                 if len(adopt_returned_capacities) == 1 else None),
                "petri_token/v1"),
    }
    design_adoption_issues = (
        _structural_growth_design_adoption_issues(closure))
    closure_issues = list(design_adoption_issues)
    for condition, label in (
            (exact_ref_exists(predecessor_net, "net_instance/v1"),
             "predecessor_net_ref"),
            (exact_ref_exists(
                predecessor_checkpoint, "marking_checkpoint/v1"),
             "predecessor_checkpoint_ref"),
            (exact_ref_exists(candidate_net, "net_instance/v1"),
             "candidate_net_ref"),
            (exact_ref_exists(
                candidate_checkpoint, "marking_checkpoint/v1"),
             "candidate_checkpoint_ref"),
            (exact_ref_exists(
                design_firing_ref, "transition_firing/v1"),
             "design_firing_ref"),
            (exact_ref_exists(handoff_token_ref, "petri_token/v1"),
             "migrated_handoff_ref"),
            (exact_resource(
                payload.get("design_index_ref"),
                "registry_v1/generic_team_design_index/v1"),
             "design_index_ref"),
            (exact_resource(
                payload.get("lowered_declaration_ref"),
                "registry_v1/team_net_declaration/v7"),
             "lowered_declaration_ref"),
            (exact_resource(
                payload.get("witness_ref"),
                "registry_v1/structural_growth_witness/v1"),
             "witness_ref"),
            (isinstance(checkpoint, Mapping)
             and checkpoint.get("net_instance_ref") == candidate_net,
             "candidate_checkpoint_net"),
            (isinstance(handoff_token, Mapping)
             and handoff_token.get("net_instance_ref") == candidate_net
             and isinstance(checkpoint, Mapping)
             and handoff_token_ref in checkpoint.get("token_refs", [])
             and handoff_token.get("place") == witness_contract["handoff_place"]
             and handoff_token.get("consumed_by") is None
             and handoff_token.get("resource_ref")
             == payload.get("design_index_ref"),
             "migrated_handoff")):
        if not condition:
            closure_issues.append(label)
    if closure_issues:
        raise RegistryConflict(
            "structural growth exact design/candidate/handoff closure "
            "is incomplete: " + ",".join(closure_issues))

    if any(value.event_type == "firing_admitted/v1" for value in events):
        raise RegistryConflict(
            "structural growth transaction cannot admit a firing")
    current_adopt_settlements = [
        value for value in events
        if (value.event_type == "transition_firing_settled/v1"
            and isinstance(adopt_firing_ref, Mapping)
            and value.aggregate_id
            == str(adopt_firing_ref.get("logical_id", ""))
            and value.payload.get("transition_firing_ref")
            == adopt_firing_ref)
    ]
    if len(current_adopt_settlements) != 1:
        raise RegistryConflict(
            "structural growth must close its exact adoption firing "
            "in the adoption transaction")
    checkpoint_token_metadata = [
        version_metadata(
            str(value.get("version_id", "")), "petri_token/v1")
        for value in checkpoint.get("token_refs", [])
        if isinstance(value, Mapping)
    ]
    if (any(value is None for value in checkpoint_token_metadata)
            or any(item.object_type == "transition_firing/v1"
                   for item in objects)
            or any(item.object_type == "resource_version/v1"
                   and item.metadata.get("content_schema_ref")
                   == "registry_v1/mechanical_transition_receipt/v1"
                   for item in objects)):
        raise RegistryConflict(
            "structural growth transaction cannot execute or receipt a transition")

__all__ = ('validate_structural_growth_pair_cas',)
