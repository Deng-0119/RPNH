"""SQLite metadata/event store with transactions and writer fencing."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from ..effects import EffectDeclarationError, structural_effect_declaration

from .identities import TypedId, fresh_bootstrap_resource_id, new_id
from .models import (
    EventEnvelope,
    ObjectRef,
    PendingEvent,
    PreparedObject,
    TypedRelation,
    VersionRef,
)
from .schema_catalog import (
    PROTECTED_SCHEMA_REFS,
    SchemaCatalog,
    SchemaGovernanceError,
    canonical_json,
    canonical_text,
)


class RegistryConflict(RuntimeError):
    pass


def _closing_firing_owner_for_system_relation(
        open_endpoint_roots: set[str],
        closing_firing_versions: set[str],
) -> str | None:
    """Bind a system relation only to the exact firing closed by its commit."""

    if not open_endpoint_roots:
        return None
    if (len(open_endpoint_roots) != 1
            or open_endpoint_roots != closing_firing_versions):
        raise RegistryConflict(
            "system relation targets an open firing endpoint")
    return next(iter(open_endpoint_roots))


class TaskModelCallLimitExceeded(RegistryConflict):
    """The registered task has consumed its actual returned-model-call cap."""




def _action_result_contains_authoritative_fields(
        result_metadata: object,
        authoritative_fields: Mapping[str, Any],
) -> bool:
    """Compare Registry facts without promoting model-facing hints."""

    return (
        isinstance(result_metadata, Mapping)
        and all(
            result_metadata.get(key) == expected
            for key, expected in authoritative_fields.items()))


































def _structural_adoption_has_required_deposits(
        deposited_refs: object, *, handoff_ref: object,
        receipt_ref: object, returned_capacity_ref: object,
) -> bool:
    """Require structural deposits without rejecting ordinary net deposits."""

    if not isinstance(deposited_refs, list):
        return False
    required = {
        json.dumps(value, sort_keys=True)
        for value in (handoff_ref, receipt_ref, returned_capacity_ref)
    }
    actual = {
        json.dumps(value, sort_keys=True) for value in deposited_refs}
    return required.issubset(actual)


def _structural_growth_design_adoption_issues(
        closure: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return exact-reference violations in one materialized design/adopt chain."""

    executable = closure.get("adopt_executable")
    spec = closure.get("adopt_spec")
    try:
        effect = structural_effect_declaration(
            executable.get("effects") if isinstance(executable, Mapping) else None,
            spec.get("implementation_contracts") if isinstance(spec, Mapping) else None)
    except EffectDeclarationError:
        return ("structural_effect_declaration",)
    if effect is None:
        return ("structural_effect_declaration",)
    witness = effect["witness"]
    adoption_transition = executable.get("transition_id")
    issues: list[str] = []

    def require(condition: bool, issue: str) -> None:
        if not condition:
            issues.append(issue)

    def resource_version_ref(value: object) -> Mapping[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        resource_id = value.get("resource_id")
        version_id = value.get("resource_version_id")
        if not isinstance(resource_id, str) or not isinstance(version_id, str):
            return None
        return {
            "entity_type": "resource_version/v1",
            "logical_id": resource_id,
            "version_id": version_id,
        }

    def producer_reference_matches(
            resource: Mapping[str, Any], *, invocation_ref: object,
            operation_binding_ref: object,
    ) -> bool:
        """Match the current exact producer references."""

        direct = resource.get("reference_provenance")
        return (isinstance(direct, Mapping)
                and direct.get("schema_version")
                == "resource_reference_provenance/v1"
                and direct.get("producer_invocation_ref") == invocation_ref
                and direct.get("operation_binding_ref")
                == operation_binding_ref)

    predecessor_net = closure.get("predecessor_net_ref")
    predecessor_checkpoint_ref = closure.get("predecessor_checkpoint_ref")
    candidate_net = closure.get("candidate_net_ref")
    candidate_checkpoint_ref = closure.get("candidate_checkpoint_ref")
    design_firing_ref = closure.get("settled_design_firing_ref")
    design_firing = closure.get("design_firing")
    design_settlements = closure.get("design_settlements")
    design_result = closure.get("design_result")
    business_resource = closure.get("business_resource")
    business_ref = closure.get("business_resource_ref")
    predecessor_checkpoint = closure.get("predecessor_checkpoint")
    handoffs = closure.get("source_handoff_tokens")
    receipts = closure.get("adopt_receipt_tokens")
    returned_capacities = closure.get("adopt_returned_capacity_tokens")

    require(isinstance(design_firing, Mapping), "design_firing")
    require(isinstance(design_settlements, Sequence)
            and not isinstance(design_settlements, (str, bytes))
            and len(design_settlements) == 1, "design_settlement_cardinality")
    require(isinstance(design_result, Mapping), "design_result")
    require(isinstance(business_resource, Mapping), "business_resource")
    require(isinstance(predecessor_checkpoint, Mapping),
            "predecessor_checkpoint")
    require(isinstance(handoffs, Sequence)
            and not isinstance(handoffs, (str, bytes))
            and len(handoffs) == 1, "source_handoff_cardinality")
    require(isinstance(receipts, Sequence)
            and not isinstance(receipts, (str, bytes))
            and len(receipts) == 1, "adopt_receipt_cardinality")
    require(isinstance(returned_capacities, Sequence)
            and not isinstance(returned_capacities, (str, bytes))
            and len(returned_capacities) == 1,
            "adopt_returned_capacity_cardinality")
    if issues:
        return tuple(issues)

    assert isinstance(design_firing, Mapping)
    assert isinstance(design_settlements, Sequence)
    assert isinstance(design_result, Mapping)
    assert isinstance(business_resource, Mapping)
    assert isinstance(predecessor_checkpoint, Mapping)
    assert isinstance(handoffs, Sequence)
    assert isinstance(receipts, Sequence)
    assert isinstance(returned_capacities, Sequence)
    design_settlement = design_settlements[0]
    handoff = handoffs[0]
    receipt_token = receipts[0]
    returned_capacity = returned_capacities[0]
    require(isinstance(design_settlement, Mapping), "design_settlement")
    require(isinstance(handoff, Mapping), "source_handoff_token")
    require(isinstance(receipt_token, Mapping), "adopt_receipt_token")
    require(isinstance(returned_capacity, Mapping),
            "adopt_returned_capacity_token")
    if issues:
        return tuple(issues)
    assert isinstance(design_settlement, Mapping)
    assert isinstance(handoff, Mapping)
    assert isinstance(receipt_token, Mapping)
    assert isinstance(returned_capacity, Mapping)

    design_result_ref = design_settlement.get("operation_result_ref")
    design_invocation_ref = design_result.get("invocation_ref")
    business_generic_ref = resource_version_ref(business_ref)
    require(design_firing.get("transition_firing_ref") == design_firing_ref
            and design_firing.get("transition_id") == witness["source_transition"]
            and design_firing.get("net_instance_ref") == predecessor_net,
            "design_firing_identity")
    require(design_settlement.get("transition_firing_ref") == design_firing_ref,
            "design_settlement_firing")
    require(design_result.get("operation_result_ref") == design_result_ref
            and design_result.get("transition_firing_ref")
            == design_firing_ref
            and design_result.get("invocation_ref")
            == design_invocation_ref
            and design_result.get("business_outcome") == "completed"
            and business_generic_ref is not None
            and design_result.get("output_resource_refs")
            == [business_generic_ref], "design_result_output")
    require(business_resource.get("content_schema_ref")
            == witness["candidate_schema"]
            and business_resource.get("producer_ref") == design_invocation_ref
            and producer_reference_matches(
                business_resource,
                invocation_ref=design_invocation_ref,
                operation_binding_ref=design_firing.get("operation_binding_ref")),
            "workflow_design_provenance")
    require(predecessor_checkpoint.get("marking_checkpoint_ref")
            == predecessor_checkpoint_ref
            and predecessor_checkpoint.get("net_instance_ref")
            == predecessor_net, "predecessor_checkpoint_identity")

    require(handoff.get("net_instance_ref") == predecessor_net
            and handoff.get("place") == witness["handoff_place"]
            and handoff.get("producer") == adoption_transition
            and handoff.get("resource_ref") == business_ref
            and handoff.get("work_resource_ref") == business_ref
            and handoff.get("consumed_by") is None,
            "source_handoff_identity")
    require(receipt_token.get("net_instance_ref") == predecessor_net
            and receipt_token.get("place") == witness["receipt_place"]
            and receipt_token.get("producer") == adoption_transition
            and receipt_token.get("consumed_by") is None
            and receipt_token.get("verdict") == witness["accepted_verdict"],
            "adopt_receipt_token_identity")
    require(returned_capacity.get("net_instance_ref") == predecessor_net
            and returned_capacity.get("place") == witness["capacity_place"]
            and returned_capacity.get("producer") == adoption_transition
            and returned_capacity.get("consumer") == adoption_transition
            and returned_capacity.get("resource_ref") is None
            and returned_capacity.get("work_resource_ref") is None
            and returned_capacity.get("kind") is None
            and returned_capacity.get("consumed_by") is None,
            "adopt_returned_capacity_identity")

    receipt_resource = closure.get("receipt_resource")
    adopt_invocation_ref = closure.get("adopt_invocation_ref")
    adopt_invocation = closure.get("adopt_invocation")
    adopt_starts = closure.get("adopt_starts")
    adopt_terminals = closure.get("adopt_terminals")
    adopt_settlements = closure.get("adopt_settlements")
    adopt_firing = closure.get("adopt_firing")
    adopt_binding = closure.get("adopt_binding")
    adopt_spec = closure.get("adopt_spec")
    adopt_executable = closure.get("adopt_executable")
    adopt_result = closure.get("adopt_result")
    adopt_delta = closure.get("adopt_delta")
    adopt_claimed_tokens = closure.get("adopt_claimed_tokens")
    require(all(isinstance(value, Mapping) for value in (
        receipt_resource, adopt_invocation, adopt_firing, adopt_binding,
        adopt_spec, adopt_executable, adopt_result, adopt_delta,
    )), "adopt_object_closure")
    require(isinstance(adopt_starts, Sequence)
            and not isinstance(adopt_starts, (str, bytes))
            and len(adopt_starts) == 1, "adopt_start_cardinality")
    require(isinstance(adopt_terminals, Sequence)
            and not isinstance(adopt_terminals, (str, bytes))
            and len(adopt_terminals) == 1, "adopt_terminal_cardinality")
    require(isinstance(adopt_settlements, Sequence)
            and not isinstance(adopt_settlements, (str, bytes))
            and len(adopt_settlements) == 1, "adopt_settlement_cardinality")
    require(isinstance(adopt_claimed_tokens, Sequence)
            and not isinstance(adopt_claimed_tokens, (str, bytes))
            and len(adopt_claimed_tokens) == 3,
            "adopt_claimed_token_cardinality")
    if issues:
        return tuple(issues)
    assert isinstance(receipt_resource, Mapping)
    assert isinstance(adopt_invocation, Mapping)
    assert isinstance(adopt_starts, Sequence)
    assert isinstance(adopt_terminals, Sequence)
    assert isinstance(adopt_settlements, Sequence)
    assert isinstance(adopt_firing, Mapping)
    assert isinstance(adopt_binding, Mapping)
    assert isinstance(adopt_spec, Mapping)
    assert isinstance(adopt_executable, Mapping)
    assert isinstance(adopt_result, Mapping)
    assert isinstance(adopt_delta, Mapping)
    assert isinstance(adopt_claimed_tokens, Sequence)
    adopt_start_record = adopt_starts[0]
    adopt_terminal_record = adopt_terminals[0]
    adopt_settlement_record = adopt_settlements[0]
    require(all(isinstance(value, Mapping) for value in (
        adopt_start_record, adopt_terminal_record, adopt_settlement_record,
    )), "adopt_event_closure")
    if issues:
        return tuple(issues)
    assert isinstance(adopt_start_record, Mapping)
    assert isinstance(adopt_terminal_record, Mapping)
    assert isinstance(adopt_settlement_record, Mapping)
    adopt_start = adopt_start_record.get("payload")
    adopt_terminal = adopt_terminal_record.get("payload")
    adopt_settlement = adopt_settlement_record.get("payload")
    require(all(isinstance(value, Mapping) for value in (
        adopt_start, adopt_terminal, adopt_settlement,
    )), "adopt_event_payloads")
    if issues:
        return tuple(issues)
    assert isinstance(adopt_start, Mapping)
    assert isinstance(adopt_terminal, Mapping)
    assert isinstance(adopt_settlement, Mapping)

    receipt_ref = receipt_token.get("resource_ref")
    receipt_generic_ref = resource_version_ref(receipt_ref)
    adopt_firing_ref = adopt_start.get("transition_firing_ref")
    adopt_binding_ref = adopt_start.get("operation_binding_ref")
    adopt_spec_ref = adopt_start.get("operation_spec_ref")
    adopt_executable_ref = adopt_start.get(
        "executable_transition_binding_ref")
    adopt_lease_ref = adopt_start.get("operation_execution_lease_ref")
    adopt_result_ref = adopt_terminal.get("operation_result_ref")
    adopt_delta_ref = adopt_settlement.get("marking_delta_ref")
    decision_ref = closure.get("decision_resource_ref")
    decision_generic_ref = resource_version_ref(decision_ref)
    require(receipt_resource.get("content_schema_ref")
            == witness["receipt_schema"]
            and receipt_resource.get("producer_ref") == adopt_invocation_ref
            and producer_reference_matches(
                receipt_resource,
                invocation_ref=adopt_invocation_ref,
                operation_binding_ref=adopt_binding_ref),
            "adopt_receipt_resource_provenance")
    require(adopt_invocation.get("invocation_ref") == adopt_invocation_ref
            and adopt_invocation.get("net_instance_ref") == predecessor_net
            and adopt_invocation.get("operation_binding_ref")
            == adopt_binding_ref
            and adopt_invocation.get("operation_execution_lease_ref")
            == adopt_lease_ref, "adopt_invocation_identity")
    require(adopt_start.get("invocation_ref") == adopt_invocation_ref
            and adopt_start.get("input_resource_refs")
            == [business_ref, decision_ref], "adopt_start_inputs")
    require(adopt_firing.get("transition_firing_ref") == adopt_firing_ref
            and adopt_firing.get("transition_id") == adoption_transition
            and adopt_firing.get("net_instance_ref") == predecessor_net
            and adopt_firing.get("operation_binding_ref")
            == adopt_binding_ref, "adopt_firing_identity")
    require(adopt_binding.get("operation_binding_ref") == adopt_binding_ref
            and adopt_binding.get("operation_spec_ref") == adopt_spec_ref
            and adopt_binding.get("node_ref") == adopt_firing.get("node_ref")
            and adopt_binding.get("origin") == "petri_operation"
            and adopt_binding.get("llm_input_target_ref") is None
            and adopt_binding.get("workspace_binding_ref") is None,
            "adopt_binding_identity")
    require(adopt_executable.get("executable_transition_binding_ref")
            == adopt_executable_ref
            and adopt_executable.get("transition_id") == adoption_transition
            and adopt_executable.get("net_instance_ref") == predecessor_net
            and adopt_executable.get("operation_binding_ref")
            == adopt_binding_ref
            and adopt_executable.get("node_ref")
            == adopt_firing.get("node_ref"), "adopt_registered_route")

    input_ports = adopt_spec.get("input_ports")
    output_ports = adopt_spec.get("output_ports")
    require(adopt_spec.get("operation_spec_ref") == adopt_spec_ref
            and adopt_spec.get("transport") == "deterministic"
            and isinstance(input_ports, list)
            and [value.get("port_id") for value in input_ports
                 if isinstance(value, Mapping)]
            == [witness["candidate_port"], witness["decision_port"]]
            and all(value.get("cardinality")
                    == {"minimum": 1, "maximum": 1}
                    for value in input_ports if isinstance(value, Mapping))
            and isinstance(output_ports, list)
            and [value.get("port_id") for value in output_ports
                 if isinstance(value, Mapping)] == [witness["receipt_port"]]
            and all(value.get("cardinality")
                    == {"minimum": 1, "maximum": 1}
                    for value in output_ports if isinstance(value, Mapping)),
            "adopt_operation_spec")

    claimed_by_ref = {
        json.dumps(value.get("petri_token_ref"), sort_keys=True): value
        for value in adopt_claimed_tokens if isinstance(value, Mapping)
    }
    input_binding_refs = adopt_start.get("input_binding_refs")
    claimed_refs = adopt_firing.get("claimed_input_refs")
    claimed_by_token_id = {
        value.get("token_id"): value
        for value in adopt_claimed_tokens if isinstance(value, Mapping)
    }
    require(isinstance(input_binding_refs, list)
            and len(input_binding_refs) == 2
            and isinstance(claimed_refs, list)
            and len(claimed_refs) == 3
            and None not in claimed_by_token_id
            and set(claimed_by_ref)
            == {json.dumps(value, sort_keys=True) for value in claimed_refs},
            "adopt_claimed_refs")
    if isinstance(input_binding_refs, list) and len(input_binding_refs) == 2:
        reviewed = claimed_by_ref.get(json.dumps(
            input_binding_refs[0], sort_keys=True))
        decision = claimed_by_ref.get(json.dumps(
            input_binding_refs[1], sort_keys=True))
        capacity = [
            value for key, value in claimed_by_ref.items()
            if key not in {
                json.dumps(input_binding_refs[0], sort_keys=True),
                json.dumps(input_binding_refs[1], sort_keys=True),
            }
        ]
        require(isinstance(reviewed, Mapping)
                and reviewed.get("place") == witness["reviewed_place"]
                and reviewed.get("producer") == witness["review_transition"]
                and reviewed.get("resource_ref") == business_ref
                and reviewed.get("work_resource_ref") == business_ref,
                "adopt_business_input_token")
        require(isinstance(decision, Mapping)
                and decision.get("place") == witness["decision_place"]
                and decision.get("producer") == witness["review_transition"]
                and decision.get("resource_ref") == decision_ref
                and decision.get("verdict") == witness["accepted_verdict"],
                "adopt_decision_input_token")
        require(len(capacity) == 1
                and capacity[0].get("place") == witness["capacity_place"]
                and capacity[0].get("consumer") == adoption_transition
                and capacity[0].get("resource_ref") is None,
                "adopt_capacity_input_token")

    require(adopt_terminal.get("invocation_ref") == adopt_invocation_ref
            and adopt_terminal.get("operation_execution_lease_ref")
            == adopt_lease_ref
            and adopt_terminal.get("operation_result_ref") == adopt_result_ref
            and adopt_terminal.get("business_outcome") == "completed"
            and receipt_generic_ref is not None
            and adopt_terminal.get("output_resource_refs")
            == [receipt_generic_ref], "adopt_terminal_identity")
    require(adopt_result.get("operation_result_ref") == adopt_result_ref
            and adopt_result.get("transition_firing_ref")
            == adopt_firing_ref
            and adopt_result.get("business_outcome") == "completed"
            and receipt_generic_ref is not None
            and adopt_result.get("output_resource_refs")
            == [receipt_generic_ref], "adopt_result_identity")
    require(adopt_settlement.get("transition_firing_ref")
            == adopt_firing_ref
            and adopt_settlement.get("operation_result_ref")
            == adopt_result_ref
            and adopt_settlement.get("successor_checkpoint_ref")
            == candidate_checkpoint_ref, "adopt_settlement_identity")
    require(adopt_delta.get("marking_delta_ref") == adopt_delta_ref
            and adopt_delta.get("net_instance_ref") == predecessor_net
            and adopt_delta.get("consumed_refs") == claimed_refs
            and adopt_delta.get("operation_binding_refs")
            == [adopt_binding_ref]
            and adopt_delta.get("transition_firing_refs")
            == [adopt_firing_ref],
            "adopt_delta_identity")
    deposited_refs = adopt_delta.get("deposited_refs")
    require(_structural_adoption_has_required_deposits(
                deposited_refs,
                handoff_ref=handoff.get("petri_token_ref"),
                receipt_ref=receipt_token.get("petri_token_ref"),
                returned_capacity_ref=(
                    returned_capacity.get("petri_token_ref"))),
            "adopt_deposited_tokens")
    candidate_checkpoint = closure.get("candidate_checkpoint")
    require(isinstance(candidate_checkpoint, Mapping)
            and candidate_checkpoint.get("settlement_delta_ref")
            == adopt_delta_ref
            and candidate_checkpoint.get("transition_firing_refs")
            == [adopt_firing_ref], "adopt_checkpoint_settlement")
    settlement_transaction = adopt_settlement_record.get("transaction_id")
    require(isinstance(settlement_transaction, str)
            and settlement_transaction
            and closure.get("adopt_delta_transaction_id")
            == settlement_transaction
            and closure.get("candidate_checkpoint_transaction_id")
            == settlement_transaction
            and closure.get("source_handoff_transaction_id")
            == settlement_transaction
            and closure.get("adopt_receipt_token_transaction_id")
            == settlement_transaction
            and closure.get("adopt_returned_capacity_transaction_id")
            == settlement_transaction,
            "adopt_settlement_transaction")

    decision_resource = closure.get("decision_resource")
    critic_invocation_ref = closure.get("critic_invocation_ref")
    critic_invocation = closure.get("critic_invocation")
    critic_starts = closure.get("critic_starts")
    critic_terminals = closure.get("critic_terminals")
    critic_firing = closure.get("critic_firing")
    critic_spec = closure.get("critic_spec")
    require(all(isinstance(value, Mapping) for value in (
        decision_resource, critic_invocation, critic_firing, critic_spec,
    )), "critic_object_closure")
    require(isinstance(critic_starts, Sequence)
            and not isinstance(critic_starts, (str, bytes))
            and len(critic_starts) == 1, "critic_start_cardinality")
    require(isinstance(critic_terminals, Sequence)
            and not isinstance(critic_terminals, (str, bytes))
            and len(critic_terminals) == 1, "critic_terminal_cardinality")
    if not issues:
        assert isinstance(decision_resource, Mapping)
        assert isinstance(critic_invocation, Mapping)
        assert isinstance(critic_starts, Sequence)
        assert isinstance(critic_terminals, Sequence)
        assert isinstance(critic_firing, Mapping)
        assert isinstance(critic_spec, Mapping)
        critic_start = critic_starts[0].get("payload")
        critic_terminal = critic_terminals[0].get("payload")
        critic_firing_ref = (
            critic_start.get("transition_firing_ref")
            if isinstance(critic_start, Mapping) else None)
        critic_binding_ref = (
            critic_start.get("operation_binding_ref")
            if isinstance(critic_start, Mapping) else None)
        require(decision_resource.get("content_schema_ref")
                == witness["decision_schema"]
                and decision_resource.get("producer_ref")
                == critic_invocation_ref
                and producer_reference_matches(
                    decision_resource,
                    invocation_ref=critic_invocation_ref,
                    operation_binding_ref=critic_binding_ref),
                "critic_decision_provenance")
        require(isinstance(critic_start, Mapping)
                and critic_start.get("invocation_ref")
                == critic_invocation_ref
                and isinstance(critic_start.get("input_resource_refs"), list)
                and business_ref in critic_start["input_resource_refs"],
                "critic_start_business_input")
        require(critic_firing.get("transition_firing_ref")
                == critic_firing_ref
                and critic_firing.get("transition_id") == witness["review_transition"]
                and critic_firing.get("net_instance_ref") == predecessor_net
                and critic_firing.get("operation_binding_ref")
                == critic_binding_ref, "critic_firing_identity")
        require(critic_invocation.get("invocation_ref")
                == critic_invocation_ref
                and critic_invocation.get("net_instance_ref")
                == predecessor_net
                and critic_invocation.get("operation_binding_ref")
                == critic_binding_ref, "critic_invocation_identity")
        require(critic_spec.get("operation_spec_ref")
                == critic_start.get("operation_spec_ref")
                and critic_spec.get("transport") == "llm",
                "critic_operation_spec")
        require(isinstance(critic_terminal, Mapping)
                and critic_terminal.get("invocation_ref")
                == critic_invocation_ref
                and critic_terminal.get("business_outcome") == "completed"
                and decision_generic_ref is not None
                and critic_terminal.get("output_resource_refs")
                == [decision_generic_ref], "critic_terminal_decision")

    migrated = closure.get("migrated_handoff_token")
    migrated_receipt = closure.get("migrated_receipt_token")
    migrated_capacity = closure.get("migrated_capacity_token")
    candidate_checkpoint = closure.get("candidate_checkpoint")
    design_index_ref = closure.get("design_index_ref")
    require(isinstance(migrated, Mapping)
            and isinstance(candidate_checkpoint, Mapping)
            and migrated.get("net_instance_ref") == candidate_net
            and migrated.get("place") == witness["handoff_place"]
            and migrated.get("producer") == adoption_transition
            and migrated.get("resource_ref") == design_index_ref
            and migrated.get("consumed_by") is None
            and candidate_checkpoint.get("marking_checkpoint_ref")
            == candidate_checkpoint_ref
            and candidate_checkpoint.get("net_instance_ref") == candidate_net
            and migrated.get("petri_token_ref")
            in candidate_checkpoint.get("token_refs", []),
            "migrated_handoff_identity")
    receipt_state_fields = (
        "token_id", "place", "epoch", "producer", "consumer",
        "resource_ref", "work_resource_ref", "kind",
        "consumed_by", "override_warning", "verdict",
        "continuation", "lease_identity_ref", "lease_claims",
    )
    require(isinstance(migrated_receipt, Mapping)
            and isinstance(candidate_checkpoint, Mapping)
            and migrated_receipt.get("net_instance_ref") == candidate_net
            and all(migrated_receipt.get(field) == receipt_token.get(field)
                    for field in receipt_state_fields)
            and migrated_receipt.get("petri_token_ref")
            in candidate_checkpoint.get("token_refs", []),
            "migrated_receipt_identity")
    require(isinstance(migrated_capacity, Mapping)
            and isinstance(candidate_checkpoint, Mapping)
            and migrated_capacity.get("net_instance_ref") == candidate_net
            and all(migrated_capacity.get(field)
                    == returned_capacity.get(field)
                    for field in receipt_state_fields)
            and migrated_capacity.get("petri_token_ref")
            in candidate_checkpoint.get("token_refs", []),
            "migrated_capacity_identity")
    return tuple(issues)


class StaleWriterError(RegistryConflict):
    pass


class RegistryCorruptError(RuntimeError):
    pass


class CanonicalProducerViolation(ValueError):
    """A resource's persisted ``produced_by`` authority is not closed."""


class CanonicalProviderLineageViolation(ValueError):
    """A provider attempt/call lineage relation is not canonical."""


class CanonicalAttemptResponseViolation(ValueError):
    """A provider attempt's persisted response authority is not canonical."""


from ._event_store.accounting import (
    _ACTUAL_MODEL_CALL_EVENT_TYPES,
    _AGENT_CAP_REVIEW_BUNDLE_KIND,
    _AGENT_CAP_SLOT_RETURN_KIND,
    _AGENT_CAP_TRACE_SUMMARY_KIND,
    _CANONICAL_EVENT_SQL,
    _TASK_MODEL_CALL_TERMINAL_REASON,
    _accountable_agent_loop_model_call_monitoring_counts,
    _accountable_model_call_event_sql,
    _accountable_returned_model_call_attempt_ids,
    _accountable_returned_task_model_call_attempt_version_ids,
    _cumulative_task_model_call_attempt_version_ids,
    _current_task_recovery_manifest_row,
    _physical_model_call_attempt_version_id,
    _project_actual_model_call_counts,
    _published_model_call_baseline_count,
    _registered_raw_return_attempt_version_ids,
    _returned_attempt_counts_toward_cap,
    _task_model_call_cap_handoff_member_authority_sql,
    _task_model_call_cap_handoff_member_publication_ordinal,
    _task_model_call_terminal_phase_entered,
)


@dataclass(frozen=True, slots=True)
class CanonicalView:
    """Immutable authority for facts canonical at one physical ordinal."""

    through_ordinal: int


@dataclass(frozen=True, slots=True)
class FiringView:
    """Immutable canonical plus exact-firing temporary read authority."""

    canonical: CanonicalView
    firing_version_id: str
    invocation_version_id: str
    temporary_members: frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class ProvisionalObservationView:
    """Immutable diagnostics-only snapshot of one provisional firing."""

    firing_version_id: str
    invocation_version_id: str
    opened_transaction_id: str
    temporary_members: frozenset[tuple[str, str]]


RegistryAuthorityView = CanonicalView | FiringView


@dataclass(frozen=True, slots=True)
class _HistoricalMechanicalTerminalReadyAuthorization:
    """One-use in-memory authority for one exact stale-reference append."""

    nonce: object
    task_id: TypedId
    branch_id: str
    task_round_id: TypedId
    net_instance_id: TypedId
    transaction_id: TypedId
    idempotency_key: str
    writer_epoch: int
    lease_writer_epoch: int
    task_ref: tuple[str, str, str]
    task_round_ref: tuple[str, str, str]
    net_instance_ref: tuple[str, str, str]
    invocation_ref: tuple[str, str, str]
    firing_ref: tuple[str, str, str]
    lease_ref: tuple[str, str, str]
    operation_result_ref: tuple[str, str, str]
    event_material: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _HistoricalMechanicalTerminalReadyEvent(PendingEvent):
    authorization: _HistoricalMechanicalTerminalReadyAuthorization | None = None





def _canonical_provider_lineage_endpoint_types(relation_type: str, source_ref: Mapping[str, Any], target_ref: Mapping[str, Any]) -> tuple[str, str, str]:
    from ._event_store import provenance
    return provenance._canonical_provider_lineage_endpoint_types(relation_type, source_ref, target_ref)


_PROVIDER_RESPONSE_METADATA_ROOT_KEYS = frozenset({
    "resource_id",
    "resource_version_id",
    "origin_kind",
    "origin",
    "task_ref",
    "branch_id",
    "round_ref",
    "net_ref",
    "producer_ref",
    "lifetime_ref",
    "size",
    "media_type",
    "content_schema_ref",
    "content_schema_authority_ref",
    "summary",
    "descriptors",
    "extensions",
    "dependency_material",
    "command_digest",
})

_DIRECT_PROVIDER_RESOURCE_METADATA_ROOT_KEYS = frozenset({
    "resource_id",
    "resource_version_id",
    "origin_kind",
    "origin",
    "task_ref",
    "branch_id",
    "round_ref",
    "net_ref",
    "producer_ref",
    "lifetime_ref",
    "size",
    "media_type",
    "content_schema_ref",
    "content_schema_authority_ref",
    "summary",
    "descriptors",
    "extensions",
    "reference_provenance",
})


_HISTORICAL_FAULT_MECHANICAL_ADMISSION_FIELDS = (
    "net_instance_ref",
    "admission_checkpoint_ref",
    "fault_route_binding_ref",
    "control_ref",
    "subnet_template_ref",
    "template_transition_ref",
    "transition_role",
    "route_transition_id",
    "fault_terminal_evidence_ref",
    "retry_admission_ref",
    "claimed_token_refs",
    "verdict",
    "claim_marking_delta_ref",
)

_HISTORICAL_FAULT_MECHANICAL_COMPLETION_FIELDS = (
    "fault_mechanical_firing_ref",
    "fault_mechanical_settlement_ref",
    "net_instance_ref",
    "admission_checkpoint_ref",
    "settlement_checkpoint_ref",
    "fault_route_binding_ref",
    "control_ref",
    "subnet_template_ref",
    "template_transition_ref",
    "transition_role",
    "route_transition_id",
    "fault_terminal_evidence_ref",
    "retry_admission_ref",
    "verdict",
    "emitted_token_refs",
    "settlement_marking_delta_ref",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _transaction_command_event_payload(
        *, event_type: str, payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Apply the writer's expiry-insensitive event command identity rule."""

    stable_payload = dict(payload)
    if event_type == "resource_release_authorized/v1":
        stable_payload.pop("expires_at", None)
    if (event_type == "object_version_published/v1"
            and stable_payload.get("object_type") in {
                "resource_delivery/v1", "resource_release_witness/v1"
            } and isinstance(stable_payload.get("metadata"), Mapping)
            and "expires_at" in stable_payload["metadata"]):
        metadata = dict(stable_payload["metadata"])
        metadata.pop("expires_at", None)
        stable_payload["metadata"] = metadata
    return stable_payload


def _transaction_command_material(
        *, branch_id: str, task_round_id: str | None,
        net_instance_id: str | None,
        objects: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        relations: Sequence[Mapping[str, Any]],
        expected_snapshot_predecessors: Mapping[
            str, Mapping[str, Any] | None],
        expected_dependency_root_predecessor: Mapping[
            str, str | None] | None,
        workspace_head_advances: Sequence[Mapping[str, str]] = (),
) -> Mapping[str, Any]:
    """Return the exact structural material for one Registry command."""

    material = {
        "branch_id": branch_id,
        "task_round_id": task_round_id,
        "net_instance_id": net_instance_id,
        "objects": [{
            "object_type": item["object_type"],
            "logical_id": str(item["logical_id"]),
            "version_id": str(item["version_id"]),
            "metadata": dict(item["metadata"]),
            "producer_invocation_id": item["producer_invocation_id"],
        } for item in objects],
        "events": [{
            "event_type": item["event_type"],
            # Transaction terminal facts are derived from the command. Their
            # stream identity changes on a retry and is not command input.
            "stream_id": (None if item["event_type"] in {
                "transaction_committed/v1", "transaction_aborted/v1"
            } else item["stream_id"]),
            "aggregate_id": (None if item["event_type"] in {
                "transaction_committed/v1", "transaction_aborted/v1"
            } else item["aggregate_id"]),
            "aggregate_type": item["aggregate_type"],
            "criticality": item["criticality"],
            "payload": _transaction_command_event_payload(
                event_type=str(item["event_type"]),
                payload=item["payload"]),
            "payload_schema_ref": item["payload_schema_ref"],
            "task_control": bool(item["task_control"]),
            "producer_principal": item["producer_principal"],
            "producer_invocation_id": item["producer_invocation_id"],
        } for item in events],
        "relations": [{
            "relation_id": str(item["relation_id"]),
            "relation_type": item["relation_type"],
            "source": item["source"],
            "target": item["target"],
            "strength": item["strength"],
            "metadata": dict(item["metadata"]),
            "producer_invocation_id": item["producer_invocation_id"],
            "system_owned": bool(item["system_owned"]),
        } for item in relations],
        "expected_snapshot_predecessors": {
            stream_id: value for stream_id, value in sorted(
                expected_snapshot_predecessors.items())},
        "expected_dependency_root_predecessor": (
            dict(expected_dependency_root_predecessor)
            if expected_dependency_root_predecessor is not None else None),
    }
    # Preserve the exact historical idempotency material for ordinary
    # transactions.  The generic CAS command becomes material only when used.
    if workspace_head_advances:
        material["workspace_head_advances"] = [
            dict(item) for item in workspace_head_advances]
    return material


def _ref_json(ref: Any) -> dict[str, Any]:
    payload = {"entity_type": ref.entity_type, "entity_id": str(ref.entity_id)}
    if hasattr(ref, "version_id"):
        payload["version_id"] = str(ref.version_id)
    return payload


def _exact_ref_payload(ref: VersionRef | None) -> dict[str, str] | None:
    if ref is None:
        return None
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _version_ref_from_payload(value: Mapping[str, Any]) -> VersionRef:
    from ._event_store import net_lineage
    return net_lineage._version_ref_from_payload(value)


def _exact_object_metadata(event_store: 'EventStore', ref: VersionRef, *, expected_type: str | None=None, db: sqlite3.Connection | None=None, rows: Mapping[str, sqlite3.Row] | None=None) -> Mapping[str, Any]:
    from ._event_store import net_lineage
    return net_lineage._exact_object_metadata(event_store, ref, expected_type=expected_type, db=db, rows=rows)


def _canonical_ref_array(value: Any, *, label: str) -> tuple[VersionRef, ...]:
    from ._event_store import net_lineage
    return net_lineage._canonical_ref_array(value, label=label)


def validate_registered_net_closure(event_store: 'EventStore', catalog: SchemaCatalog, net_ref: VersionRef, *, _db: sqlite3.Connection | None=None) -> Mapping[str, Any]:
    from ._event_store import net_lineage
    return net_lineage.validate_registered_net_closure(event_store, catalog, net_ref, _db=_db)




def verified_adoption_lineage(event_store: 'EventStore', catalog: SchemaCatalog, task_id: TypedId, *, _db: sqlite3.Connection | None=None) -> tuple[VersionRef, ...]:
    from ._event_store import net_lineage
    return net_lineage.verified_adoption_lineage(event_store, catalog, task_id, _db=_db)


def verified_adoption_prefix(event_store: 'EventStore', catalog: SchemaCatalog, task_id: TypedId, *,
                             adoption_event_id: TypedId, _db: sqlite3.Connection | None=None) -> tuple[VersionRef, ...]:
    """Exact-event semantic prefix; not a historical DB snapshot or execution grant."""
    from ._event_store import net_lineage
    return net_lineage.verified_adoption_prefix(event_store, catalog, task_id,
        adoption_event_id=adoption_event_id, _db=_db)


def verified_adoption_head(event_store: 'EventStore', catalog: SchemaCatalog, task_id: TypedId, *, _db: sqlite3.Connection | None=None) -> VersionRef:
    from ._event_store import net_lineage
    return net_lineage.verified_adoption_head(event_store, catalog, task_id, _db=_db)


def _require_exact_checkpoint_identity(checkpoint: Mapping[str, Any], *, checkpoint_ref: VersionRef, net_ref: VersionRef, previous_checkpoint_ref: VersionRef | None) -> None:
    from ._event_store import net_lineage
    return net_lineage._require_exact_checkpoint_identity(checkpoint, checkpoint_ref=checkpoint_ref, net_ref=net_ref, previous_checkpoint_ref=previous_checkpoint_ref)


def verified_checkpoint_head(event_store: 'EventStore', catalog: SchemaCatalog, task_id: TypedId, net_ref: VersionRef, *, _db: sqlite3.Connection | None=None) -> VersionRef:
    from ._event_store import net_lineage
    return net_lineage.verified_checkpoint_head(event_store, catalog, task_id, net_ref, _db=_db)


def _stable_id_text(kind: str, *parts: object) -> str:
    from ._event_store import provenance
    return provenance._stable_id_text(kind, *parts)


def canonical_provider_lineage_record(relation_type: str, source_ref: Mapping[str, Any], target_ref: Mapping[str, Any], *, publication_transaction: str, publication_key: str, source_metadata: Mapping[str, Any], target_metadata: Mapping[str, Any], relation_records: Iterable[Mapping[str, Any]], registered_exact_refs: Iterable[tuple[str, str, str]]) -> Mapping[str, Any]:
    from ._event_store import provenance
    return provenance.canonical_provider_lineage_record(relation_type, source_ref, target_ref, publication_transaction=publication_transaction, publication_key=publication_key, source_metadata=source_metadata, target_metadata=target_metadata, relation_records=relation_records, registered_exact_refs=registered_exact_refs)


def canonical_attempt_response_record(attempt_ref: Mapping[str, Any], response_ref: Mapping[str, Any], *, publication_transaction: str, publication_key: str, attempt_metadata: Mapping[str, Any], response_metadata: Mapping[str, Any], relation_records: Iterable[Mapping[str, Any]], completion_records: Iterable[Mapping[str, Any]], registered_exact_refs: Iterable[tuple[str, str, str]]) -> Mapping[str, Any]:
    from ._event_store import provenance
    return provenance.canonical_attempt_response_record(attempt_ref, response_ref, publication_transaction=publication_transaction, publication_key=publication_key, attempt_metadata=attempt_metadata, response_metadata=response_metadata, relation_records=relation_records, completion_records=completion_records, registered_exact_refs=registered_exact_refs)


def canonical_producer_record(resource_ref: Mapping[str, Any], producer_ref: Mapping[str, Any], *, publication_transaction: str, publication_key: str, relation_records: Iterable[Mapping[str, Any]], valid_resource_versions: Iterable[str], registered_exact_refs: Iterable[tuple[str, str, str]]) -> Mapping[str, Any]:
    from ._event_store import provenance
    return provenance.canonical_producer_record(resource_ref, producer_ref, publication_transaction=publication_transaction, publication_key=publication_key, relation_records=relation_records, valid_resource_versions=valid_resource_versions, registered_exact_refs=registered_exact_refs)


def canonical_producer_logical_set(resource_records: Iterable[Mapping[str, Any]], *, relation_records: Iterable[Mapping[str, Any]], transaction_keys: Mapping[str, str], registered_exact_refs: Iterable[tuple[str, str, str]]) -> Mapping[str, Mapping[str, Any]]:
    from ._event_store import provenance
    return provenance.canonical_producer_logical_set(resource_records, relation_records=relation_records, transaction_keys=transaction_keys, registered_exact_refs=registered_exact_refs)


def fact_event_envelope(event: EventEnvelope) -> dict[str, Any]:
    """Return the complete current fact envelope."""
    return {
        "envelope_version": event.envelope_version, "event_id": str(event.event_id),
        "event_type": event.event_type, "event_schema_version": event.event_schema_version,
        "criticality": event.criticality, "task_id": str(event.task_id),
        "branch_id": event.branch_id,
        "task_round_id": str(event.task_round_id) if event.task_round_id else None,
        "net_instance_id": str(event.net_instance_id) if event.net_instance_id else None,
        "stream_id": event.stream_id, "aggregate_id": event.aggregate_id,
        "aggregate_type": event.aggregate_type, "stream_sequence": event.stream_sequence,
        "aggregate_version": event.aggregate_version,
        "task_control_sequence": event.task_control_sequence,
        "idempotency_key": event.idempotency_key, "command_id": event.command_id,
        "correlation_id": event.correlation_id,
        "causation_event_id": str(event.causation_event_id) if event.causation_event_id else None,
        "parent_event_ids": [str(item) for item in event.parent_event_ids],
        "producer_principal": event.producer_principal,
        "producer_invocation_id": (str(event.producer_invocation_id)
                                   if event.producer_invocation_id else None),
        "transaction_id": str(event.transaction_id), "occurred_at": event.occurred_at,
        "recorded_at": event.recorded_at, "payload_schema_ref": event.payload_schema_ref,
        "payload": dict(event.payload),
        "writer_fencing_epoch": event.writer_fencing_epoch,
    }


def prepared_object_envelope(item: PreparedObject) -> dict[str, Any]:
    return {
        "object_type": item.object_type,
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
        "size": item.size,
        "media_type": item.media_type,
        "schema_ref": item.schema_ref,
        "producer_invocation_id": (
            str(item.producer_invocation_id)
            if item.producer_invocation_id is not None else None
        ),
        "storage_locator": item.storage_locator,
        "metadata": dict(item.metadata),
    }


class EventStore:
    def __init__(self, path: Path | str, catalog: SchemaCatalog, *,
                 read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = bool(read_only)
        if not self.read_only:
            from .parent_bound import preflight_bound_writer
            preflight_bound_writer(self.path)
        if self.read_only:
            if not self.path.is_file():
                raise RegistryCorruptError("read-only Registry database is missing")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog = catalog
        self._lock = threading.RLock()
        self.__historical_terminal_ready_authorizations: dict[
            object, _HistoricalMechanicalTerminalReadyAuthorization] = {}
        self._net_closure_memo: dict[str, Mapping[str, Any]] = {}
        self._adoption_lineage_memo: dict[
            str, tuple[int, tuple[VersionRef, ...]]] = {}
        self._checkpoint_head_memo: dict[
            tuple[str, str], tuple[int, VersionRef]] = {}
        self._verified_read_only_transactions: set[str] = set()
        self._object_row_memo: dict[str, sqlite3.Row] = {}
        self._journal_mode_ready = self.read_only
        if not self.read_only:
            self._initialize()

    def classify_child_acceptance_history(self, assertion):
        """Read-only source-qualified history; never receipt delivery or authority."""
        from .acceptance_history import classify_child_acceptance_history
        return classify_child_acceptance_history(self, assertion)

    @staticmethod
    def _pending_event_authorization_material(
            event: PendingEvent) -> Mapping[str, Any]:
        return {
            "event_type": event.event_type,
            "criticality": event.criticality,
            "stream_id": event.stream_id,
            "aggregate_id": event.aggregate_id,
            "aggregate_type": event.aggregate_type,
            "idempotency_key": event.idempotency_key,
            "command_id": event.command_id,
            "payload": dict(event.payload),
            "payload_schema_ref": event.payload_schema_ref,
            "task_control": event.task_control,
            "causation_event_id": (
                str(event.causation_event_id)
                if event.causation_event_id is not None else None),
            "parent_event_ids": [str(value) for value in event.parent_event_ids],
            "producer_principal": event.producer_principal,
            "producer_invocation_id": (
                str(event.producer_invocation_id)
                if event.producer_invocation_id is not None else None),
            "occurred_at": event.occurred_at,
        }

    @staticmethod
    def _authorization_ref_identity(
            ref: VersionRef | Mapping[str, Any]) -> tuple[str, str, str]:
        if isinstance(ref, VersionRef):
            return (ref.entity_type, str(ref.entity_id), str(ref.version_id))
        if not isinstance(ref, Mapping):
            raise TypeError("historical authorization requires an exact version ref")
        return (
            str(ref.get("entity_type", "")),
            str(ref.get("logical_id", "")),
            str(ref.get("version_id", "")),
        )

    def _authorize_historical_mechanical_terminal_ready_event(
            self, event: PendingEvent, *, task_id: TypedId, branch_id: str,
            task_round_id: TypedId, net_instance_id: TypedId,
            transaction_id: TypedId, writer_epoch: int,
            lease_writer_epoch: int, task_ref: VersionRef,
            task_round_ref: VersionRef, net_instance_ref: VersionRef,
            invocation_ref: VersionRef, firing_ref: VersionRef,
            lease_ref: VersionRef, operation_result_ref: VersionRef,
    ) -> PendingEvent:
        """Attach a private one-use proof to one exact terminal-ready event."""

        if (event.event_type != "operation_terminal_ready/v1"
                or event.payload.get("invocation_ref") != _exact_ref_payload(
                    invocation_ref)
                or event.payload.get("operation_execution_lease_ref")
                != _exact_ref_payload(lease_ref)
                or event.payload.get("operation_result_ref")
                != _exact_ref_payload(operation_result_ref)
                or event.idempotency_key != event.command_id
                or task_round_id != task_round_ref.entity_id
                or net_instance_id != net_instance_ref.entity_id
                or task_id != task_ref.entity_id
                or writer_epoch != self.writer_epoch
                or lease_writer_epoch >= writer_epoch):
            raise RegistryConflict(
                "historical terminal-ready authorization binding differs")
        nonce = object()
        authorization = _HistoricalMechanicalTerminalReadyAuthorization(
            nonce=nonce,
            task_id=task_id,
            branch_id=branch_id,
            task_round_id=task_round_id,
            net_instance_id=net_instance_id,
            transaction_id=transaction_id,
            idempotency_key=event.idempotency_key,
            writer_epoch=writer_epoch,
            lease_writer_epoch=lease_writer_epoch,
            task_ref=self._authorization_ref_identity(task_ref),
            task_round_ref=self._authorization_ref_identity(task_round_ref),
            net_instance_ref=self._authorization_ref_identity(net_instance_ref),
            invocation_ref=self._authorization_ref_identity(invocation_ref),
            firing_ref=self._authorization_ref_identity(firing_ref),
            lease_ref=self._authorization_ref_identity(lease_ref),
            operation_result_ref=self._authorization_ref_identity(
                operation_result_ref),
            event_material=self._pending_event_authorization_material(event),
        )
        with self._lock:
            self.__historical_terminal_ready_authorizations[nonce] = authorization
        return _HistoricalMechanicalTerminalReadyEvent(
            event_type=event.event_type,
            criticality=event.criticality,
            stream_id=event.stream_id,
            aggregate_id=event.aggregate_id,
            aggregate_type=event.aggregate_type,
            idempotency_key=event.idempotency_key,
            command_id=event.command_id,
            payload=event.payload,
            payload_schema_ref=event.payload_schema_ref,
            task_control=event.task_control,
            causation_event_id=event.causation_event_id,
            parent_event_ids=event.parent_event_ids,
            producer_principal=event.producer_principal,
            producer_invocation_id=event.producer_invocation_id,
            occurred_at=event.occurred_at,
            authorization=authorization,
        )

    def connect(self) -> sqlite3.Connection:
        from ._event_store import backend
        return backend.connect(self)

    def _initialize(self) -> None:
        from ._event_store import backend
        return backend._initialize(self)

    @property
    def writer_epoch(self) -> int:
        from ._event_store import backend
        return backend.writer_epoch(self)

    def get_or_create_meta(self, key: str, candidate: str) -> str:
        from ._event_store import backend
        return backend.get_or_create_meta(self, key, candidate)

    def get_meta(self, key: str) -> str | None:
        from ._event_store import backend
        return backend.get_meta(self, key)

    def install_published_model_call_baseline_v1(self, *, source_run_ref: Mapping[str, Any], source_task_ref: Mapping[str, Any], source_checkpoint_ref: Mapping[str, Any], exact_model: str, published_call_count: int, idempotency_key: str) -> VersionRef:
        from ._event_store import accounting
        return accounting.install_published_model_call_baseline_v1(self, source_run_ref=source_run_ref, source_task_ref=source_task_ref, source_checkpoint_ref=source_checkpoint_ref, exact_model=exact_model, published_call_count=published_call_count, idempotency_key=idempotency_key)

    def rotate_writer(self, *, expected_epoch: int) -> int:
        from ._event_store import backend
        return backend.rotate_writer(self, expected_epoch=expected_epoch)

    def acquire_writer(self) -> int:
        from ._event_store import backend
        return backend.acquire_writer(self)

    def stream_heads(self) -> dict[str, int]:
        from ._event_store import queries
        return queries.stream_heads(self)

    def stream_head_at(self, stream_id: str, *, through_ordinal: int) -> int:
        from ._event_store import queries
        return queries.stream_head_at(self, stream_id, through_ordinal=through_ordinal)

    def max_ordinal(self) -> int:
        from ._event_store import queries
        return queries.max_ordinal(self)

    def _verify_ref(self, db: sqlite3.Connection, ref_json: Mapping[str, Any],
                    new_objects: Mapping[str, tuple[str, str]]) -> None:
        version = ref_json.get("version_id")
        entity_type = ref_json.get("entity_type")
        logical = ref_json.get("entity_id")
        if version is None:
            found = db.execute(
                "SELECT object_type FROM objects WHERE logical_id=? AND object_type=? LIMIT 1",
                (logical, entity_type)).fetchone()
            new = new_objects.get(str(logical))
            if found is None and (new is None or new[0] != entity_type):
                raise RegistryConflict(f"strong relation has missing object ref {logical!r}")
            return
        found = db.execute(
            "SELECT logical_id,object_type FROM objects WHERE version_id=?", (version,)).fetchone()
        new = new_objects.get(str(version))
        if found is None and new is None:
            raise RegistryConflict(f"strong relation has missing version ref {version!r}")
        found_logical, found_type = ((found["logical_id"], found["object_type"])
                                     if found is not None else (new[1], new[0]))
        if found_logical != logical or found_type != entity_type:
            raise RegistryConflict(f"strong relation has mismatched typed ref {version!r}")

    @staticmethod
    def _historical_validate_operation_fault_evidence_atomicity(
            db: sqlite3.Connection,
            objects: Sequence[PreparedObject]) -> None:
        """Require every operation fault and generic witness in one new pair."""
        fault_type = "operation_fault/v1"
        witness_type = "fault_terminal_witness/v1"
        faults = [item for item in objects if item.object_type == fault_type]
        witnesses = [
            item for item in objects if item.object_type == witness_type]
        if not faults and not witnesses:
            return
        if len(faults) != len(witnesses):
            raise RegistryConflict(
                "operation fault and generic terminal evidence must be "
                "published atomically as exact pairs")

        faults_by_version = {str(item.version_id): item for item in faults}
        if len(faults_by_version) != len(faults):
            raise RegistryConflict(
                "operation fault and generic terminal evidence contain "
                "duplicate exact versions")
        matched_fault_versions: set[str] = set()
        for witness in witnesses:
            fault_ref = witness.metadata.get("operation_fault_ref")
            if not isinstance(fault_ref, Mapping):
                raise RegistryConflict(
                    "operation fault and generic terminal evidence must be "
                    "published atomically as exact pairs")
            fault_version = str(fault_ref.get("version_id", ""))
            fault = faults_by_version.get(fault_version)
            if (fault is None
                    or fault_ref != {
                        "entity_type": fault_type,
                        "logical_id": str(fault.logical_id),
                        "version_id": str(fault.version_id),
                    }
                    or fault.metadata.get("fault_terminal_evidence_ref") != {
                        "entity_type": witness_type,
                        "logical_id": str(witness.logical_id),
                        "version_id": str(witness.version_id),
                    }
                    or fault_version in matched_fault_versions):
                raise RegistryConflict(
                    "operation fault and generic terminal evidence must be "
                    "published atomically as exact pairs")
            matched_fault_versions.add(fault_version)
        if len(matched_fault_versions) != len(faults):
            raise RegistryConflict(
                "operation fault and generic terminal evidence must be "
                "published atomically as exact pairs")

        proposed_versions = tuple(
            str(item.version_id) for item in (*faults, *witnesses))
        placeholders = ",".join("?" for _value in proposed_versions)
        preexisting = db.execute(
            f"SELECT version_id,object_type,transaction_id FROM objects "
            f"WHERE version_id IN ({placeholders})",
            proposed_versions,
        ).fetchall()
        if preexisting:
            raise RegistryConflict(
                "operation fault closure conflicts with pre-existing partial "
                "or separately committed protocol state")

    @staticmethod
    def _is_parent_internal_leaf_kb_transaction(
            db: sqlite3.Connection, agent_objects: Sequence[PreparedObject],
            agent_events: Sequence[PendingEvent]) -> bool:
        """Recognize one parent-owned leaf KB closure without a child loop."""
        kb_reads = tuple(
            item for item in agent_objects
            if item.object_type == "firing_external_kb_read/v2")
        kb_events = tuple(
            item for item in agent_events
            if item.event_type == "firing_external_kb_read_recorded/v2")
        if (not kb_reads or len(kb_reads) != len(kb_events)
                or len(kb_reads) != len(agent_objects)
                or len(kb_events) != len(agent_events)):
            return False

        recorded_by_version = {
            str(event.payload.get("kb_access_ref", {}).get("version_id", "")):
                event
            for event in kb_events
            if isinstance(event.payload.get("kb_access_ref"), Mapping)
        }
        if (len(recorded_by_version) != len(kb_events)
                or set(recorded_by_version)
                != {str(item.version_id) for item in kb_reads}):
            return False
        for read in kb_reads:
            value = dict(read.metadata)
            access_ref = value.get("kb_access_ref")
            action_ref = value.get("agent_action_ref")
            firing_ref = value.get("transition_firing_ref")
            index_ref = value.get("external_resource_index_ref")
            package_ref = value.get("kb_package_ref")
            event = recorded_by_version[str(read.version_id)]
            if (not isinstance(access_ref, Mapping)
                    or access_ref.get("entity_type")
                    != "firing_external_kb_read/v2"
                    or access_ref.get("logical_id") != str(read.logical_id)
                    or access_ref.get("version_id") != str(read.version_id)
                    or not isinstance(action_ref, Mapping)
                    or action_ref.get("entity_type") != "agent_action/v2"
                    or not isinstance(firing_ref, Mapping)
                    or firing_ref.get("entity_type")
                    != "transition_firing/v1"
                    or not isinstance(index_ref, Mapping)
                    or index_ref.get("entity_type")
                    != "external_resource_index/v1"
                    or not isinstance(package_ref, Mapping)
                    or package_ref.get("entity_type")
                    != "external_kb_package/v1"
                    or event.payload.get("kb_access_ref") != access_ref
                    or event.payload.get("transition_firing_ref") != firing_ref
                    or set(event.payload) != {
                        "event_ref", "kb_access_ref",
                        "transition_firing_ref", "recorded_at_utc"}
                    or event.aggregate_id != firing_ref.get("logical_id")
                    or event.aggregate_type != "transition_firing"
                    or event.stream_id != (
                        f"transition-firing:{firing_ref.get('logical_id')}")
                    or read.producer_invocation_id is None
                    or event.producer_invocation_id
                    != read.producer_invocation_id):
                return False
            action_row = db.execute(
                "SELECT metadata_json,producer_invocation_id FROM objects "
                "WHERE object_type='agent_action/v2' AND logical_id=? "
                "AND version_id=?",
                (str(action_ref.get("logical_id", "")),
                 str(action_ref.get("version_id", ""))),
            ).fetchone()
            package_row = db.execute(
                "SELECT 1 FROM objects WHERE object_type=? AND logical_id=? "
                "AND version_id=?",
                ("external_kb_package/v1",
                 str(package_ref.get("logical_id", "")),
                 str(package_ref.get("version_id", ""))),
            ).fetchone()
            index_row = db.execute(
                "SELECT 1 FROM objects WHERE object_type=? AND logical_id=? "
                "AND version_id=?",
                ("external_resource_index/v1",
                 str(index_ref.get("logical_id", "")),
                 str(index_ref.get("version_id", ""))),
            ).fetchone()
            if action_row is None or package_row is None or index_row is None:
                return False
            action = json.loads(action_row["metadata_json"])
            if (action.get("agent_action_ref") != action_ref
                    or action.get("tool_name") != "delegate_leaf"
                    or action_row["producer_invocation_id"]
                    != read.producer_invocation_id):
                return False
        return True

        def exact_object(
                ref: object, expected_type: str,
        ) -> tuple[sqlite3.Row, Mapping[str, Any]] | None:
            if (not isinstance(ref, Mapping)
                    or ref.get("entity_type") != expected_type):
                return None
            row = db.execute(
                "SELECT logical_id,version_id,object_type,metadata_json,"
                "producer_invocation_id FROM objects WHERE version_id=? "
                "AND object_type=?",
                (str(ref.get("version_id", "")), expected_type),
            ).fetchone()
            if (row is None
                    or row["logical_id"] != str(ref.get("logical_id", ""))):
                return None
            return row, json.loads(row["metadata_json"])

        def exact_resource(
                ref: object,
        ) -> tuple[sqlite3.Row, Mapping[str, Any]] | None:
            if not isinstance(ref, Mapping):
                return None
            row = db.execute(
                "SELECT logical_id,version_id,object_type,metadata_json,"
                "producer_invocation_id FROM objects WHERE version_id=? "
                "AND object_type='resource_version/v1'",
                (str(ref.get("resource_version_id", "")),),
            ).fetchone()
            if (row is None
                    or row["logical_id"] != str(ref.get("resource_id", ""))):
                return None
            return row, json.loads(row["metadata_json"])

        recorded_by_version: dict[str, PendingEvent] = {}
        for event in kb_events:
            raw_ref = event.payload.get("firing_external_kb_read_ref")
            if not isinstance(raw_ref, Mapping):
                return False
            version_id = str(raw_ref.get("version_id", ""))
            if not version_id or version_id in recorded_by_version:
                return False
            recorded_by_version[version_id] = event
        if set(recorded_by_version) != {
                str(item.version_id) for item in kb_reads}:
            return False

        parent_action_ref: Mapping[str, Any] | None = None
        sponsoring_call_ref: Mapping[str, Any] | None = None
        for read in kb_reads:
            value = dict(read.metadata)
            read_ref = value.get("firing_external_kb_read_ref")
            invocation_ref = value.get("invocation_ref")
            binding_ref = value.get("operation_binding_ref")
            lease_ref = value.get("operation_execution_lease_ref")
            firing_ref = value.get("transition_firing_ref")
            loop_ref = value.get("agent_loop_ref")
            turn_ref = value.get("agent_turn_ref")
            action_ref = value.get("agent_action_ref")
            if (not isinstance(read_ref, Mapping)
                    or read_ref.get("entity_type")
                    != "firing_external_kb_read/v2"
                    or read_ref.get("logical_id") != str(read.logical_id)
                    or read_ref.get("version_id") != str(read.version_id)
                    or not all(isinstance(ref, Mapping) for ref in (
                        invocation_ref, binding_ref, lease_ref, firing_ref,
                        loop_ref, turn_ref))
                    or not isinstance(action_ref, Mapping)
                    or action_ref.get("entity_type") != "agent_action/v2"):
                return False

            event = recorded_by_version[str(read.version_id)]
            external_node = value.get("external_node")
            if not isinstance(external_node, Mapping):
                return False
            event_keys = (
                "firing_external_kb_read_ref", "run_execution_authority_ref",
                "net_instance_ref", "declaration_resource_ref",
                "declaration_schema_ref", "declaration_digest",
                "transition_firing_ref", "operation_binding_ref",
                "operation_execution_lease_ref", "invocation_ref",
                "agent_loop_ref", "agent_turn_ref", "agent_action_ref",
                "transition_id", "turn_sequence", "tool_call_ordinal",
                "tool_call_id", "action_identity_kind",
                "action_identity_key", "tool_name", "external_node",
                "search_metadata", "outcome", "selection_digest", "items",
            )
            expected_event = {key: value[key] for key in event_keys}
            expected_event.update({
                "external_node_id": external_node["node_id"],
                "package_id": external_node["package_id"],
                "version": external_node["version"],
                "package_digest": external_node["package_digest"],
                "index_digest": external_node["index_digest"],
            })
            if (dict(event.payload) != expected_event
                    or event.aggregate_id != firing_ref.get("logical_id")
                    or event.aggregate_type != "transition_firing"
                    or event.stream_id
                    != f"transition-firing:{firing_ref.get('logical_id')}"
                    or event.producer_invocation_id is None
                    or str(event.producer_invocation_id)
                    != invocation_ref.get("logical_id")
                    or read.producer_invocation_id is None
                    or str(read.producer_invocation_id)
                    != invocation_ref.get("logical_id")):
                return False

            exact_invocation = exact_object(invocation_ref, "invocation/v1")
            exact_binding = exact_object(binding_ref, "operation_binding/v1")
            exact_lease = exact_object(
                lease_ref, "operation_execution_lease/v1")
            exact_firing = exact_object(firing_ref, "transition_firing/v1")
            exact_loop = exact_object(loop_ref, "agent_loop/v1")
            if any(item is None for item in (
                    exact_invocation, exact_binding, exact_lease,
                    exact_firing, exact_loop)):
                return False
            invocation = exact_invocation[1]
            binding = exact_binding[1]
            lease = exact_lease[1]
            firing = exact_firing[1]
            loop = exact_loop[1]
            if (invocation.get("invocation_ref") != invocation_ref
                    or invocation.get("origin") != "petri_operation"
                    or invocation.get("operation_binding_ref") != binding_ref
                    or invocation.get("operation_execution_lease_ref")
                    != lease_ref
                    or invocation.get("own_transition_firing_ref") != firing_ref
                    or invocation.get("accounting_parent_invocation_ref")
                    != invocation_ref
                    or invocation.get("net_instance_ref")
                    != value.get("net_instance_ref")
                    or binding.get("operation_binding_ref") != binding_ref
                    or binding.get("origin") != "petri_operation"
                    or lease.get("operation_execution_lease_ref") != lease_ref
                    or lease.get("invocation_ref") != invocation_ref
                    or firing.get("transition_firing_ref") != firing_ref
                    or firing.get("operation_binding_ref") != binding_ref
                    or firing.get("net_instance_ref")
                    != value.get("net_instance_ref")
                    or firing.get("transition_id") != value.get("transition_id")
                    or loop.get("agent_loop_ref") != loop_ref
                    or loop.get("invocation_ref") != invocation_ref
                    or loop.get("operation_binding_ref") != binding_ref):
                return False

            matching_turns = []
            if (not isinstance(turn_ref, Mapping)
                    or turn_ref.get("entity_type") != "agent_turn/v1"):
                return False
            turn_rows = db.execute(
                "SELECT aggregate_id,producer_invocation_id,payload_json "
                "FROM events WHERE event_type='agent_turn_recorded/v1' AND ("
                "aggregate_id=? OR "
                "json_extract(payload_json,'$.agent_turn_version_id')=?)",
                (str(loop_ref.get("logical_id", "")),
                 str(turn_ref.get("version_id", ""))),
            ).fetchall()
            for row in turn_rows:
                payload = json.loads(row["payload_json"])
                if (payload.get("agent_turn_id") == turn_ref.get("logical_id")
                        and payload.get("agent_turn_version_id")
                        == turn_ref.get("version_id")):
                    matching_turns.append((row, payload))
            if (len(matching_turns) != 1
                    or matching_turns[0][0]["aggregate_id"]
                    != loop_ref.get("logical_id")
                    or matching_turns[0][1].get("agent_loop_id")
                    != loop_ref.get("logical_id")
                    or matching_turns[0][0]["producer_invocation_id"]
                    != invocation_ref.get("logical_id")):
                return False

            matching_calls = []
            call_rows = db.execute(
                "SELECT logical_id,version_id,metadata_json,"
                "producer_invocation_id FROM objects "
                "WHERE object_type='llm_call_spec/v2' AND "
                "json_extract(metadata_json,"
                "'$.delegation_callsite.parent_action_id')=? AND "
                "json_extract(metadata_json,"
                "'$.delegation_callsite.leaf_turn_sequence')=?",
                (str(action_ref.get("logical_id", "")),
                 value.get("turn_sequence")),
            ).fetchall()
            for row in call_rows:
                call = json.loads(row["metadata_json"])
                callsite = call.get("delegation_callsite")
                if (isinstance(callsite, Mapping)
                        and callsite.get("kind")
                        == "parent_internal_leaf_callsite/v1"
                        and callsite.get("parent_invocation_ref")
                        == invocation_ref
                        and callsite.get("sponsoring_transition_firing_ref")
                        == firing_ref
                        and callsite.get("parent_operation_binding_ref")
                        == binding_ref
                        and callsite.get(
                            "parent_operation_execution_lease_ref") == lease_ref
                        and callsite.get("parent_agent_loop_ref") == loop_ref
                        and callsite.get("parent_agent_turn_ref") == turn_ref
                        and callsite.get("parent_action_id")
                        == action_ref.get("logical_id")
                        and callsite.get("delegation_depth") == 1
                        and callsite.get("restartability_policy")
                        == "discard_on_process_stop"
                        and callsite.get("leaf_turn_sequence")
                        == value.get("turn_sequence")
                        and len(callsite.get(
                            "prior_leaf_llm_call_refs", ()))
                        == value.get("turn_sequence")
                        and len(callsite.get(
                            "prior_leaf_tool_step_refs", ()))
                        == value.get("turn_sequence")
                        and call.get("invocation_ref") == invocation_ref
                        and call.get("operation_binding_ref") == binding_ref
                        and call.get("agent_loop_ref") == loop_ref
                        and call.get("turn_sequence")
                        == value.get("turn_sequence")
                        and call.get("prior_turn_refs")
                        == callsite.get("prior_leaf_llm_call_refs")
                        and call.get("llm_call_ref") == {
                            "entity_type": "llm_call_spec/v2",
                            "logical_id": row["logical_id"],
                            "version_id": row["version_id"],
                        }
                        and row["producer_invocation_id"]
                        == invocation_ref.get("logical_id")):
                    matching_calls.append((row, call))
            if len(matching_calls) != 1:
                return False
            call_row, call = matching_calls[0]
            call_ref = call["llm_call_ref"]

            matching_attempts = []
            attempt_rows = db.execute(
                "SELECT logical_id,version_id,metadata_json,"
                "producer_invocation_id FROM objects "
                "WHERE object_type='provider_attempt_spec/v1' AND "
                "json_extract(metadata_json,'$.llm_call_ref.logical_id')=? "
                "AND json_extract(metadata_json,"
                "'$.llm_call_ref.version_id')=?",
                (str(call_ref.get("logical_id", "")),
                 str(call_ref.get("version_id", ""))),
            ).fetchall()
            for row in attempt_rows:
                attempt = json.loads(row["metadata_json"])
                if (attempt.get("llm_call_ref") == call_ref
                        and attempt.get("invocation_ref") == invocation_ref
                        and attempt.get("operation_binding_ref") == binding_ref
                        and attempt.get("origin") == "petri_operation"
                        and attempt.get("accounting_parent_invocation_id")
                        == invocation_ref.get("logical_id")
                        and attempt.get(
                            "accounting_parent_invocation_version_id")
                        == invocation_ref.get("version_id")
                        and attempt.get("provider_attempt_ref") == {
                            "entity_type": "provider_attempt_spec/v1",
                            "logical_id": row["logical_id"],
                            "version_id": row["version_id"],
                        }
                        and row["producer_invocation_id"]
                        == invocation_ref.get("logical_id")):
                    matching_attempts.append((row, attempt))
            if len(matching_attempts) != 1:
                return False
            attempt_row, attempt = matching_attempts[0]
            attempt_ref = attempt["provider_attempt_ref"]

            matching_observations = []
            observed_rows = db.execute(
                "SELECT aggregate_id,producer_invocation_id,payload_json "
                "FROM events WHERE "
                "event_type='provider_attempt_submission_observed/v1' "
                "AND (aggregate_id=? OR json_extract(payload_json,"
                "'$.provider_attempt_version_id')=?)",
                (str(attempt_ref.get("logical_id", "")),
                 str(attempt_ref.get("version_id", ""))),
            ).fetchall()
            for row in observed_rows:
                payload = json.loads(row["payload_json"])
                if (payload.get("provider_attempt_ref") == attempt_ref
                        and payload.get("llm_call_ref") == call_ref
                        and payload.get("invocation_ref") == invocation_ref):
                    matching_observations.append((row, payload))
            if (len(matching_observations) != 1
                    or matching_observations[0][0]["aggregate_id"]
                    != attempt_ref["logical_id"]
                    or matching_observations[0][0]["producer_invocation_id"]
                    != invocation_ref["logical_id"]):
                return False
            response_ref = matching_observations[0][1].get(
                "response_resource_ref")
            exact_response = exact_resource(response_ref)
            if exact_response is None:
                return False
            response_row, response = exact_response
            if (response.get("producer_ref") != invocation_ref
                    or response.get("origin_kind") != "provider_raw_response"
                    or response.get("origin") != {
                        "kind": "provider_raw_response",
                        "primary_ref": attempt_ref,
                        "secondary_ref": call_ref,
                    }
                    or response_row["producer_invocation_id"]
                    != invocation_ref["logical_id"]):
                return False

            if parent_action_ref is None:
                parent_action_ref = action_ref
                sponsoring_call_ref = call_ref
            elif (action_ref != parent_action_ref
                    or call_ref != sponsoring_call_ref):
                return False

            existing_action = db.execute(
                "SELECT 1 FROM objects WHERE logical_id=? "
                "AND object_type='agent_action/v2'",
                (str(action_ref.get("logical_id", "")),),
            ).fetchone()
            if existing_action is not None:
                return False
        return True

    @staticmethod
    def _validate_waiting_resource_grant_atomicity(
            db: sqlite3.Connection, objects: Sequence[PreparedObject],
            events: Sequence[PendingEvent], *, writer_epoch: int) -> bool:
        from ._event_store.validation import resources

        return resources.validate_waiting_resource_grant_atomicity(
            db, objects, events, writer_epoch=writer_epoch)

    @staticmethod
    def _validate_firing_resource_settlement_atomicity(
            db: sqlite3.Connection, objects: Sequence[PreparedObject],
            events: Sequence[PendingEvent]) -> None:
        from ._event_store.validation import firing

        firing.validate_firing_resource_settlement_atomicity(
            db, objects, events)

    def _registered_operation_config(
            self, db: sqlite3.Connection, invocation_ref, binding_ref) -> Mapping[str, Any]:
        """Read config only through exact invocation/net/spec registration."""
        from ..executable_net import load_compiled_net

        def exact(ref, object_type):
            if (not isinstance(ref, Mapping)
                    or set(ref) != {"entity_type", "logical_id", "version_id"}
                    or ref["entity_type"] != object_type):
                raise RegistryConflict("turn extension requires exact Registry refs")
            row = db.execute(
                "SELECT logical_id,metadata_json,storage_locator FROM objects "
                "WHERE object_type=? AND logical_id=? AND version_id=?",
                (object_type, ref["logical_id"], ref["version_id"])).fetchone()
            if row is None:
                raise RegistryConflict("turn extension authority is not registered")
            return row, json.loads(str(row["metadata_json"]))

        _, invocation = exact(invocation_ref, "invocation/v1")
        _, binding = exact(binding_ref, "operation_binding/v1")
        _, spec = exact(binding["operation_spec_ref"], "operation_spec/v1")
        _, node = exact(binding["node_ref"], "node_declaration/v1")
        _, net = exact(invocation["net_instance_ref"], "net_instance/v1")
        source = net["team_net_declaration_resource_ref"]
        resource_ref = {"entity_type": "resource_version/v1",
            "logical_id": source["resource_id"], "version_id": source["resource_version_id"]}
        row, resource = exact(resource_ref, "resource_version/v1")
        version = TypedId.parse(source["resource_version_id"], expected="resource_version")
        if (str(row["storage_locator"]) != f"registry-object:{version}"
                or invocation["operation_binding_ref"] != binding_ref
                or binding_ref not in net["operation_binding_refs"]
                or binding["node_ref"] not in net["node_refs"]
                or resource.get("content_schema_ref") != "rpnh/executable_net/v1"):
            raise RegistryConflict("turn extension declaration/binding closure is not exact")
        compiled = load_compiled_net(json.loads((
            self.path.parent / "objects" / version.kind / version.value).read_bytes()))
        transition = next((item for item in compiled.symbolic.transitions
                           if item.name == node["transition_id"]), None)
        operation = next((item for item in compiled.operations
                          if transition is not None
                          and item.declaration.name == transition.operation), None)
        if (operation is None or operation.operation_id != spec["operation_id"]
                or operation.executor_key != spec["executor_key"]
                or compiled.registrations["executor"][operation.executor_key]["identity"]
                != spec["implementation_identity"]
                or compiled.registrations["executor"][operation.executor_key]["contracts"]
                != spec["implementation_contracts"]):
            raise RegistryConflict("turn extension differs from exact operation registration")
        return operation.declaration.config

    def _registered_turn_budget_extension(
            self, db: sqlite3.Connection, invocation_ref, binding_ref) -> int | None:
        increment = self._registered_operation_config(
            db, invocation_ref, binding_ref).get("turn_budget_extension")
        if increment is not None and (type(increment) is not int or increment < 1):
            raise RegistryConflict("registered turn extension must be positive or disabled")
        return increment

    def _validate_petri_firing_resource_access(
            self, db: sqlite3.Connection, events: Sequence[PendingEvent], *,
            task_id: TypedId, net_instance_id: TypedId | None,
            writer_epoch: int,
    ) -> None:
        from ._event_store.validation import resources

        resources.validate_petri_firing_resource_access(
            self, db, events, task_id=task_id,
            net_instance_id=net_instance_id, writer_epoch=writer_epoch)

    def _validate_agent_loop_atomicity(
            self, db: sqlite3.Connection, objects: Sequence[PreparedObject],
            events: Sequence[PendingEvent], *, writer_epoch: int) -> None:
        """Linearize one closed agent-loop revision and its child records."""
        from ._event_store.validation.agent_loop import (
            validate_agent_loop_atomicity,
        )

        validate_agent_loop_atomicity(
            db,
            objects,
            events,
            writer_epoch=writer_epoch,
            validate_waiting_resource_grant_atomicity=(
                EventStore._validate_waiting_resource_grant_atomicity),
            is_parent_internal_leaf_kb_transaction=(
                EventStore._is_parent_internal_leaf_kb_transaction),
            registered_turn_budget_extension=(
                self._registered_turn_budget_extension),
            registered_operation_config=self._registered_operation_config,
        )

    @staticmethod
    def _validate_provider_materialization_atomicity(
            objects: Sequence[PreparedObject],
            events: Sequence[PendingEvent]) -> None:
        from ._event_store.validation import provider

        provider.validate_provider_materialization_atomicity(objects, events)

    def publish_batch(self, *, task_id: TypedId, branch_id: str,
                      task_round_id: TypedId | None, net_instance_id: TypedId | None,
                      transaction_id: TypedId, idempotency_key: str,
                      writer_epoch: int, objects: Sequence[PreparedObject],
                      events: Sequence[PendingEvent], relations: Sequence[TypedRelation],
                      expected_heads: Mapping[str, int],
                      expected_snapshot_predecessors: Mapping[
                          str, Mapping[str, Any] | None] | None = None,
                      expected_dependency_root_predecessor: Mapping[
                          str, str | None] | None = None,
                      firing_publications: Sequence[
                          Mapping[str, object]] = (),
                      workspace_head_advances: Sequence[
                          Mapping[str, object]] = (),
                      expected_registry_ordinal: int | None = None,
                      ) -> tuple[EventEnvelope, ...]:
        from ._event_store import commit

        return commit.publish_batch(
            self, task_id=task_id, branch_id=branch_id,
            task_round_id=task_round_id, net_instance_id=net_instance_id,
            transaction_id=transaction_id, idempotency_key=idempotency_key,
            writer_epoch=writer_epoch, objects=objects, events=events,
            relations=relations, expected_heads=expected_heads,
            expected_snapshot_predecessors=expected_snapshot_predecessors,
            expected_dependency_root_predecessor=(
                expected_dependency_root_predecessor),
            firing_publications=firing_publications,
            workspace_head_advances=workspace_head_advances,
            expected_registry_ordinal=expected_registry_ordinal,
        )

    def _before_firing_authority_mutation(
            self, db: sqlite3.Connection, *, firing_version_id: str,
            transaction_id: str) -> None:
        """No-op boundary after closure while the exact root is provisional."""

        del db, firing_version_id, transaction_id

    def _after_firing_authority_mutation(
            self, db: sqlite3.Connection, *, firing_version_id: str,
            transaction_id: str) -> None:
        """No-op boundary used to verify rollback after publication mutation."""

        del db, firing_version_id, transaction_id

    def _validate_authoritative_references(
            self, db: sqlite3.Connection, *, task_id: TypedId, branch_id: str,
            task_round_id: TypedId | None, net_instance_id: TypedId | None,
            transaction_id: TypedId, idempotency_key: str,
            transaction_writer_epoch: int,
            objects: Sequence[PreparedObject], events: Sequence[PendingEvent],
            relations: Sequence[TypedRelation]) -> None:
        from ._event_store.validation import firing

        firing.validate_authoritative_references(
            self, db, task_id=task_id, branch_id=branch_id,
            task_round_id=task_round_id, net_instance_id=net_instance_id,
            transaction_id=transaction_id, idempotency_key=idempotency_key,
            transaction_writer_epoch=transaction_writer_epoch,
            objects=objects, events=events, relations=relations)

    @staticmethod
    def _validate_llm_model_call_budget(
            db: sqlite3.Connection,
            objects: Sequence[PreparedObject],
            events: Sequence[PendingEvent]) -> None:
        from ._event_store.validation import provider

        provider.validate_llm_model_call_budget(db, objects, events)

    @staticmethod
    def _validate_lifecycle(
            db: sqlite3.Connection,
            events: Sequence[PendingEvent]) -> None:
        from ._event_store.validation import firing

        firing.validate_lifecycle(db, events)

    @staticmethod
    def _insert_event(db: sqlite3.Connection, event: EventEnvelope) -> None:
        db.execute("""INSERT INTO events(
            event_id,event_type,event_schema_version,criticality,task_id,branch_id,
            task_round_id,net_instance_id,stream_id,aggregate_id,aggregate_type,
            stream_sequence,aggregate_version,task_control_sequence,idempotency_key,
            command_id,correlation_id,causation_event_id,parent_event_ids_json,
            producer_principal,producer_invocation_id,transaction_id,occurred_at,recorded_at,
            payload_schema_ref,payload_json,writer_fencing_epoch)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                str(event.event_id), event.event_type, event.event_schema_version,
                event.criticality, str(event.task_id), event.branch_id,
                str(event.task_round_id) if event.task_round_id else None,
                str(event.net_instance_id) if event.net_instance_id else None,
                event.stream_id, event.aggregate_id, event.aggregate_type,
                event.stream_sequence, event.aggregate_version, event.task_control_sequence,
                event.idempotency_key, event.command_id, event.correlation_id,
                str(event.causation_event_id) if event.causation_event_id else None,
                json.dumps([str(item) for item in event.parent_event_ids]),
                event.producer_principal,
                str(event.producer_invocation_id) if event.producer_invocation_id else None,
                str(event.transaction_id), event.occurred_at, event.recorded_at,
                event.payload_schema_ref,
                json.dumps(event.payload, sort_keys=True, default=str),
                event.writer_fencing_epoch))

    def abort(self, *, task_id: TypedId, branch_id: str, transaction_id: TypedId,
              idempotency_key: str, writer_epoch: int, reason: str) -> tuple[EventEnvelope, ...]:
        pending = PendingEvent(
            event_type="transaction_aborted/v1", criticality="authoritative",
            stream_id=f"transaction:{transaction_id}", aggregate_id=str(transaction_id),
            aggregate_type="transaction", idempotency_key=idempotency_key,
            command_id=idempotency_key, payload={"reason": reason},
            payload_schema_ref="registry_v1/transaction_aborted/v1")
        return self.publish_batch(
            task_id=task_id, branch_id=branch_id, task_round_id=None,
            net_instance_id=None, transaction_id=transaction_id,
            idempotency_key=idempotency_key, writer_epoch=writer_epoch,
            objects=(), events=(pending,), relations=(),
            expected_heads={pending.stream_id: self.stream_heads().get(pending.stream_id, 0)})

    def list_events(self, *, after_ordinal: int=0) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.list_events(self, after_ordinal=after_ordinal)

    def canonical_events(self, *, after_ordinal: int=0, through_ordinal: int | None=None, stream_id: str | None=None) -> tuple[EventEnvelope, ...]:
        from ._event_store import views
        return views.canonical_events(self, after_ordinal=after_ordinal, through_ordinal=through_ordinal, stream_id=stream_id)

    def firing_activity_page(self, **kwargs):
        """Read a bounded diagnostic activity page; never an authority view."""
        from ._event_store import views
        return views.firing_activity_page(self, **kwargs)

    def ordered_firing_record(self, firing_version_id: TypedId | str, *, _db=None) -> Mapping[str, Any]:
        from ._event_store import views
        return views.ordered_firing_record(self, firing_version_id, _db=_db)

    def reconstruct_firing_state(self, firing_version_id: TypedId | str) -> Mapping[str, Any]:
        from ._event_store import views
        return views.reconstruct_firing_state(self, firing_version_id)

    def event_by_id(self, event_id: TypedId) -> EventEnvelope | None:
        from ._event_store import queries
        return queries.event_by_id(self, event_id)

    def first_and_last_events(
            self,
    ) -> tuple[EventEnvelope | None, EventEnvelope | None]:
        from ._event_store import queries
        return queries.first_and_last_events(self)

    def has_event_idempotency_prefix(self, prefix: str) -> bool:
        from ._event_store import queries
        return queries.has_event_idempotency_prefix(self, prefix)

    def list_events_by_type(self, event_types: tuple[str, ...], *, after_ordinal: int=0) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.list_events_by_type(self, event_types, after_ordinal=after_ordinal)

    def list_events_by_aggregate(self, aggregate_id: str, *, event_types: tuple[str, ...]=(), after_ordinal: int=0) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.list_events_by_aggregate(self, aggregate_id, event_types=event_types, after_ordinal=after_ordinal)

    def list_events_by_producer(self, producer_invocation_id: TypedId | str, *, event_types: tuple[str, ...]=(), after_ordinal: int=0) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.list_events_by_producer(self, producer_invocation_id, event_types=event_types, after_ordinal=after_ordinal)

    def firing_lifecycle_events_for_net(self, net_ref: VersionRef) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.firing_lifecycle_events_for_net(self, net_ref)

    def agent_turn_events_for_closure(self, *, call_version_id: TypedId | str, attempt_version_id: TypedId | str, response_version_id: TypedId | str, loop_id: TypedId | str, sequence: int) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.agent_turn_events_for_closure(self, call_version_id=call_version_id, attempt_version_id=attempt_version_id, response_version_id=response_version_id, loop_id=loop_id, sequence=sequence)

    def agent_turn_events_for_ref(self, turn_ref: VersionRef) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.agent_turn_events_for_ref(self, turn_ref)

    def adopted_call_events_for_invocation(self, invocation_id: TypedId | str) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.adopted_call_events_for_invocation(self, invocation_id)

    def actual_model_call_counts(self) -> tuple[int, int]:
        from ._event_store import accounting
        return accounting.actual_model_call_counts(self)

    def actual_model_call_count(self) -> int:
        from ._event_store import accounting
        return accounting.actual_model_call_count(self)

    def extra_model_call_count(self) -> int:
        from ._event_store import accounting
        return accounting.extra_model_call_count(self)

    def agent_loop_model_call_monitoring_counts(self, agent_loop_id: str) -> tuple[int, int, int]:
        from ._event_store import accounting
        return accounting.agent_loop_model_call_monitoring_counts(self, agent_loop_id)

    def agent_loop_parent_direct_model_call_count(self, agent_loop_id: str) -> int:
        from ._event_store import accounting
        return accounting.agent_loop_parent_direct_model_call_count(self, agent_loop_id)

    def task_model_call_terminal_phase_entered(self) -> bool:
        from ._event_store import accounting
        return accounting.task_model_call_terminal_phase_entered(self)

    def actual_model_call_limit(self) -> int | None:
        from ._event_store import accounting
        return accounting.actual_model_call_limit(self)

    def ordinary_model_call_limit(self) -> int | None:
        from ._event_store import accounting
        return accounting.ordinary_model_call_limit(self)

    def list_events_by_transaction(self, transaction_id: str) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.list_events_by_transaction(self, transaction_id)

    def list_events_by_idempotency_key(self, idempotency_key: str) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.list_events_by_idempotency_key(self, idempotency_key)

    @staticmethod
    def _row_to_envelope(row: sqlite3.Row) -> EventEnvelope:
        from ._event_store import queries
        return queries._row_to_envelope(row)

    def _verified_persisted_event_record(self, db: sqlite3.Connection, row: sqlite3.Row) -> Mapping[str, Any]:
        from ._event_store import queries
        return queries._verified_persisted_event_record(self, db, row)

    def object_row(self, version_id: TypedId) -> sqlite3.Row | None:
        from ._event_store import queries
        return queries.object_row(self, version_id)

    def firing_publication_row(self, firing_version_id: TypedId | str) -> sqlite3.Row | None:
        from ._event_store import queries
        return queries.firing_publication_row(self, firing_version_id)

    def provisional_firing_rows_for_checkpoint(self, *, net_version_id: TypedId | str, checkpoint_version_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.provisional_firing_rows_for_checkpoint(self, net_version_id=net_version_id, checkpoint_version_id=checkpoint_version_id)

    def firing_publication_for_invocation(self, invocation_id: TypedId | str) -> sqlite3.Row | None:
        from ._event_store import queries
        return queries.firing_publication_for_invocation(self, invocation_id)

    def firing_publication_for_member(self, member_kind: str, member_identity: TypedId | str) -> sqlite3.Row | None:
        from ._event_store import queries
        return queries.firing_publication_for_member(self, member_kind, member_identity)

    def canonical_view(self, *, through_ordinal: int | None=None) -> CanonicalView:
        from ._event_store import views
        return views.canonical_view(self, through_ordinal=through_ordinal)

    def firing_view(self, *, firing_version_id: TypedId | str, invocation_version_id: TypedId | str) -> FiringView:
        from ._event_store import views
        return views.firing_view(self, firing_version_id=firing_version_id, invocation_version_id=invocation_version_id)

    def provisional_observation_view(self, *, firing_version_id: TypedId | str, invocation_version_id: TypedId | str) -> ProvisionalObservationView:
        from ._event_store import views
        return views.provisional_observation_view(self, firing_version_id=firing_version_id, invocation_version_id=invocation_version_id)

    @staticmethod
    def _authority_ordinal(view: RegistryAuthorityView) -> int:
        from ._event_store import views
        return views._authority_ordinal(view)

    @staticmethod
    def _canonical_member_sql(*, member_kind: str, member_identity_sql: str, published_event_sql: str) -> str:
        from ._event_store import views
        return views._canonical_member_sql(member_kind=member_kind, member_identity_sql=member_identity_sql, published_event_sql=published_event_sql)

    def canonical_object_row(self, version_id: TypedId, *, through_ordinal: int | None=None) -> sqlite3.Row | None:
        from ._event_store import views
        return views.canonical_object_row(self, version_id, through_ordinal=through_ordinal)

    def object_row_for_view(self, view: RegistryAuthorityView, version_id: TypedId | str) -> sqlite3.Row | None:
        from ._event_store import views
        return views.object_row_for_view(self, view, version_id)

    def canonical_object_publication_ordinal(self, version_id: TypedId, *, through_ordinal: int | None=None) -> int | None:
        from ._event_store import views
        return views.canonical_object_publication_ordinal(self, version_id, through_ordinal=through_ordinal)

    def canonical_object_rows(self, *, through_ordinal: int | None=None, object_type: str | None=None) -> tuple[sqlite3.Row, ...]:
        from ._event_store import views
        return views.canonical_object_rows(self, through_ordinal=through_ordinal, object_type=object_type)

    def canonical_relation_rows(self, *, through_ordinal: int | None=None, version_id: TypedId | str | None=None) -> tuple[sqlite3.Row, ...]:
        from ._event_store import views
        return views.canonical_relation_rows(self, through_ordinal=through_ordinal, version_id=version_id)

    def relation_rows_for_view(self, view: RegistryAuthorityView, *, version_id: TypedId | str | None=None, relation_type: str | None=None, endpoint: str='either') -> tuple[sqlite3.Row, ...]:
        from ._event_store import views
        return views.relation_rows_for_view(self, view, version_id=version_id, relation_type=relation_type, endpoint=endpoint)

    def events_for_view(self, view: RegistryAuthorityView, *, event_types: tuple[str, ...]=(), aggregate_id: str | None=None, idempotency_key: str | None=None, net_instance_id: TypedId | str | None=None, producer_invocation_id: TypedId | str | None=None) -> tuple[EventEnvelope, ...]:
        from ._event_store import views
        return views.events_for_view(self, view, event_types=event_types, aggregate_id=aggregate_id, idempotency_key=idempotency_key, net_instance_id=net_instance_id, producer_invocation_id=producer_invocation_id)

    def provisional_firing_inventory(self, firing_version_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import views
        return views.provisional_firing_inventory(self, firing_version_id)

    def provisional_firing_object_rows(self, firing_version_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import views
        return views.provisional_firing_object_rows(self, firing_version_id)

    def provisional_firing_events(self, firing_version_id: TypedId | str) -> tuple[EventEnvelope, ...]:
        from ._event_store import views
        return views.provisional_firing_events(self, firing_version_id)

    def workspace_lineage_head(self, workspace_lineage_id: TypedId | str) -> sqlite3.Row | None:
        from ._event_store import queries
        return queries.workspace_lineage_head(self, workspace_lineage_id)

    def object_publication_event(self, version_id: TypedId, *, object_type: str) -> EventEnvelope:
        from ._event_store import queries
        return queries.object_publication_event(self, version_id, object_type=object_type)

    def latest_object_row(self, logical_id: TypedId, *, object_type: str) -> sqlite3.Row | None:
        from ._event_store import queries
        return queries.latest_object_row(self, logical_id, object_type=object_type)

    def object_rows_by_logical(self, logical_id: TypedId | str, *, object_type: str | None=None) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.object_rows_by_logical(self, logical_id, object_type=object_type)

    def object_rows_by_type(self, object_type: str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.object_rows_by_type(self, object_type)

    def current_task_recovery_manifest_row(self) -> sqlite3.Row | None:
        from ._event_store import backend
        return backend.current_task_recovery_manifest_row(self)

    def compare_and_set_task_recovery_manifest_pointer(self, *, expected_ref: VersionRef, replacement_ref: VersionRef, writer_epoch: int) -> None:
        from ._event_store import backend
        return backend.compare_and_set_task_recovery_manifest_pointer(self, expected_ref=expected_ref, replacement_ref=replacement_ref, writer_epoch=writer_epoch)

    def execution_environment_inventory_rows(self, *, task_ref: VersionRef, task_round_ref: VersionRef, net_instance_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.execution_environment_inventory_rows(self, task_ref=task_ref, task_round_ref=task_round_ref, net_instance_ref=net_instance_ref)

    def transition_firing_rows_for_task_net_transition(self, *, task_ref: VersionRef, net_instance_ref: VersionRef, transition_id: str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.transition_firing_rows_for_task_net_transition(self, task_ref=task_ref, net_instance_ref=net_instance_ref, transition_id=transition_id)

    def object_rows_by_producer(self, producer_invocation_id: TypedId | str, *, object_types: tuple[str, ...]=()) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.object_rows_by_producer(self, producer_invocation_id, object_types=object_types)

    def object_rows_by_invocation_id(self, invocation_id: TypedId | str, *, object_types: tuple[str, ...]=()) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.object_rows_by_invocation_id(self, invocation_id, object_types=object_types)

    def resource_address_binding_rows_for_invocation_scope(self, *, invocation_scope_ref: VersionRef, operation_binding_authorization_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.resource_address_binding_rows_for_invocation_scope(self, invocation_scope_ref=invocation_scope_ref, operation_binding_authorization_ref=operation_binding_authorization_ref)

    def provider_attempt_rows_for_call(self, call_id: TypedId | str, call_version_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.provider_attempt_rows_for_call(self, call_id, call_version_id)

    def agent_action_rows_for_turn(self, turn_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.agent_action_rows_for_turn(self, turn_ref)

    def settled_agent_action_rows_for_turn(self, turn_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.settled_agent_action_rows_for_turn(self, turn_ref)

    def completed_agent_context_compaction_rows_for_loop(self, loop_id: TypedId | str) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.completed_agent_context_compaction_rows_for_loop(self, loop_id)

    def agent_loop_rows_for_invocation(self, *, invocation_ref: VersionRef, operation_binding_ref: VersionRef) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.agent_loop_rows_for_invocation(self, invocation_ref=invocation_ref, operation_binding_ref=operation_binding_ref)

    def provider_materialization_events_for_call(self, call_ref: VersionRef) -> tuple[EventEnvelope, ...]:
        from ._event_store import queries
        return queries.provider_materialization_events_for_call(self, call_ref)

    def relation_rows_from_version(self, version_id: TypedId) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.relation_rows_from_version(self, version_id)

    def relation_rows_for_version(self, version_id: TypedId | str, *, relation_type: str | None=None, endpoint: str='either') -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.relation_rows_for_version(self, version_id, relation_type=relation_type, endpoint=endpoint)

    def require_canonical_provider_lineage(self, relation_type: str, source_ref: Any, target_ref: Any, *, through_ordinal: int | None=None, view: RegistryAuthorityView | None=None) -> Mapping[str, Any]:
        from ._event_store import provenance
        return provenance.require_canonical_provider_lineage(self, relation_type, source_ref, target_ref, through_ordinal=through_ordinal, view=view)

    def require_canonical_attempt_response(self, attempt_ref: Any, response_ref: Any) -> Mapping[str, Any]:
        from ._event_store import provenance
        return provenance.require_canonical_attempt_response(self, attempt_ref, response_ref)

    def require_canonical_producer(self, resource_ref: Any, producer_ref: Any) -> Mapping[str, Any]:
        from ._event_store import provenance
        return provenance.require_canonical_producer(self, resource_ref, producer_ref)

    def object_rows(self) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.object_rows(self)

    def relation_rows(self) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.relation_rows(self)

    def outbox_rows(self) -> tuple[sqlite3.Row, ...]:
        from ._event_store import queries
        return queries.outbox_rows(self)

    def _historical_seal_stream(self, stream_id: str) -> dict[str, Any]:
        with self.connect() as db:
            events = db.execute(
                "SELECT stream_sequence,event_id,event_hash FROM events "
                "WHERE stream_id=? ORDER BY stream_sequence", (stream_id,)).fetchall()
            if not events:
                raise ValueError(f"cannot seal empty stream {stream_id!r}")
            head = events[-1]
            index = [{"sequence": int(row["stream_sequence"]), "event_id": row["event_id"]}
                     for row in events]
            seal = canonical_text({"stream_id": stream_id, "head": head["event_hash"],
                                "index": index})
            db.execute("INSERT OR IGNORE INTO segment_seals VALUES(?,?,?,?)", (
                stream_id, int(head["stream_sequence"]), head["event_hash"], seal))
            stored = db.execute(
                "SELECT head_hash,seal_digest FROM segment_seals "
                "WHERE stream_id=? AND through_sequence=?",
                (stream_id, int(head["stream_sequence"]))).fetchone()
            if stored["head_hash"] != head["event_hash"] or stored["seal_digest"] != seal:
                raise RegistryCorruptError(f"immutable segment seal conflict: {stream_id}")
        return {"stream_id": stream_id, "through_sequence": int(head["stream_sequence"]),
                "head_hash": head["event_hash"], "seal_digest": seal,
                "retrieval_index": index}


from .task_ledger import TaskControlLedger
