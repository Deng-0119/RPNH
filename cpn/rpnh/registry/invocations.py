"""Canonical per-firing invocation admission and settlement.

This module implements the approved CA-T/CP-A/AU-A/OR-A lifecycle.  It never
derives execution identity from an agent name, a thread, or runner state: every
input is an exact, already-published D1-C version reference.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence

from ..budgets import (
    BudgetContractError, validate_budget_binding, validate_budget_scopes,
)
from .event_store import (
    RegistryConflict,
    RegistryCorruptError,
    StaleWriterError,
    validate_registered_net_closure,
    verified_adoption_head,
    verified_checkpoint_head,
)
from .identities import TypedId
from .models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from .resources import ResourceVersionRef
from .schema_catalog import canonical_json
from .admission_publication import PreparedFiringAdmissionPublications
from ._invocation.terminal_evidence import terminal_descendant_event_ids


class InvocationAdmissionError(RegistryConflict):
    """The firing was not canonically admitted; no governed I/O may occur."""


class InvocationClosedError(InvocationAdmissionError):
    """The immutable context names an invocation that is no longer open to I/O."""


class _HistoricalMechanicalTerminalReadyRequest:
    """Private selector for the prevalidated mechanical recovery path."""


_HISTORICAL_MECHANICAL_TERMINAL_READY = (
    _HistoricalMechanicalTerminalReadyRequest())


def _ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _ref_from_payload(value: Mapping[str, Any]) -> VersionRef:
    if not isinstance(value, Mapping):
        raise TypeError("exact version reference must be an object")
    return VersionRef(
        str(value["entity_type"]),
        TypedId.parse(str(value["logical_id"])),
        TypedId.parse(str(value["version_id"])),
    )


def _stable_id(kind: str, key: str) -> TypedId:
    return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, f"d1-c:{kind}:{key}").hex)  # type: ignore[arg-type]


def _scope_firing_idempotency_key(
        native_run_ref: VersionRef, caller_key: str,
) -> str:
    if (native_run_ref.entity_type != "native_run_identity/v1"
            or native_run_ref.entity_id.kind != "run"
            or native_run_ref.version_id.kind != "run_version"):
        raise InvocationAdmissionError(
            "firing identity requires one exact native run authority")
    if not isinstance(caller_key, str) or not caller_key:
        raise InvocationAdmissionError(
            "firing identity requires one caller idempotency key")
    return f"native-run:{native_run_ref.version_id}:{caller_key}"


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """Frozen non-authoritative carrier hydrated from a committed invocation."""

    task_ref: VersionRef
    task_branch_ref: VersionRef
    task_round_ref: VersionRef
    net_instance_ref: VersionRef
    plan_ref: VersionRef
    team_design_root_ref: VersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    authority_decision_ref: VersionRef
    admission_marking_checkpoint_ref: VersionRef
    budget_witness_ref: VersionRef
    principal_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    origin: Literal["petri_operation"]
    own_node_ref: VersionRef | None
    own_transition_firing_ref: VersionRef | None
    sponsoring_transition_firing_ref: VersionRef | None
    activation_ref: VersionRef | None
    authorization_lifetime_activation_ref: VersionRef | None
    parent_invocation_ref: VersionRef | None
    accounting_parent_invocation_ref: VersionRef
    budget_scope: str
    finalization_scope: str | None
    agent_ref: VersionRef | None = None

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "task_ref": _ref_payload(self.task_ref),
            "task_branch_ref": _ref_payload(self.task_branch_ref),
            "task_round_ref": _ref_payload(self.task_round_ref),
            "net_instance_ref": _ref_payload(self.net_instance_ref),
            "plan_ref": _ref_payload(self.plan_ref),
            "team_design_root_ref": _ref_payload(self.team_design_root_ref),
            "invocation_ref": _ref_payload(self.invocation_ref),
            "agent_ref": (
                _ref_payload(self.agent_ref) if self.agent_ref else None),
            "operation_binding_ref": _ref_payload(self.operation_binding_ref),
            "authority_decision_ref": _ref_payload(self.authority_decision_ref),
            "admission_marking_checkpoint_ref": _ref_payload(
                self.admission_marking_checkpoint_ref),
            "budget_witness_ref": _ref_payload(self.budget_witness_ref),
            "principal_ref": _ref_payload(self.principal_ref),
            "operation_execution_lease_ref": _ref_payload(
                self.operation_execution_lease_ref),
            "origin": self.origin,
            "own_node_ref": _ref_payload(self.own_node_ref) if self.own_node_ref else None,
            "own_transition_firing_ref": (
                _ref_payload(self.own_transition_firing_ref)
                if self.own_transition_firing_ref else None),
            "sponsoring_transition_firing_ref": (
                _ref_payload(self.sponsoring_transition_firing_ref)
                if self.sponsoring_transition_firing_ref else None),
            "activation_ref": (
                _ref_payload(self.activation_ref)
                if self.activation_ref else None),
            "authorization_lifetime_activation_ref": (
                _ref_payload(self.authorization_lifetime_activation_ref)
                if self.authorization_lifetime_activation_ref else None),
            "parent_invocation_ref": (
                _ref_payload(self.parent_invocation_ref)
                if self.parent_invocation_ref else None),
            "accounting_parent_invocation_ref": _ref_payload(
                self.accounting_parent_invocation_ref),
            "budget_scope": self.budget_scope,
            "finalization_scope": self.finalization_scope,
        }

    def serialized(self) -> dict[str, Any]:
        return self.unsigned_payload()

    @classmethod
    def from_serialized(cls, value: Mapping[str, Any]) -> "InvocationContext":
        from ._invocation.context import decode_invocation_context
        return decode_invocation_context(cls, value)


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Frozen exact-ref carrier for a selected tool execution."""

    invocation_context: InvocationContext
    llm_call_ref: VersionRef
    provider_response_resource_ref: VersionRef
    response_disposition_event_id: TypedId
    tool_request_ref: VersionRef
    tool_execution_ref: VersionRef
    tool_catalog_ref: VersionRef
    tool_profile_ref: VersionRef
    authorization_snapshot_ref: VersionRef

    def serialized(self) -> dict[str, Any]:
        return {
            "invocation_context": self.invocation_context.serialized(),
            "llm_call_ref": _ref_payload(self.llm_call_ref),
            "provider_response_resource_ref": _ref_payload(
                self.provider_response_resource_ref),
            "response_disposition_event_id": str(
                self.response_disposition_event_id),
            "tool_request_ref": _ref_payload(self.tool_request_ref),
            "tool_execution_ref": _ref_payload(self.tool_execution_ref),
            "tool_catalog_ref": _ref_payload(self.tool_catalog_ref),
            "tool_profile_ref": _ref_payload(self.tool_profile_ref),
            "authorization_snapshot_ref": _ref_payload(
                self.authorization_snapshot_ref),
        }

    @classmethod
    def from_serialized(cls, value: Mapping[str, Any]) -> "ToolExecutionContext":
        from ._invocation.context import decode_tool_execution_context
        return decode_tool_execution_context(cls, value)


@dataclass(frozen=True, slots=True)
class FiringClaim:
    """Exact pre-registered inputs for one real Petri transition firing."""

    task_ref: VersionRef
    task_branch_ref: VersionRef
    task_round_ref: VersionRef
    net_instance_ref: VersionRef
    plan_ref: VersionRef
    node_ref: VersionRef
    operation_binding_ref: VersionRef
    marking_checkpoint_ref: VersionRef
    principal_ref: VersionRef
    logical_tau: int | float | str
    attempt_index: int
    claimed_input_refs: tuple[VersionRef, ...] = ()
    consumed_input_refs: tuple[VersionRef, ...] | None = None
    firing_allocation_ref: VersionRef | None = None
    activation_ref: VersionRef | None = None
    agent_ref: VersionRef | None = None
    admission_publications: "PreparedFiringAdmissionPublications | None" = None


@dataclass(frozen=True, slots=True)
class FiringAdmission:
    admission_ref: VersionRef
    context: InvocationContext
    claim_marking_delta_ref: VersionRef
    admission_event_id: TypedId


@dataclass(frozen=True, slots=True)
class TerminalResultPackage:
    business_outcome: Literal["completed"]
    output_resource_refs: tuple[VersionRef, ...] = ()
    observed_read_set_ref: VersionRef | None = None
    provenance_set_ref: VersionRef | None = None
    provider_attempt_evidence_refs: tuple[VersionRef, ...] = ()


@dataclass(frozen=True, slots=True)
class _AgentTurnAcceptanceClosure:
    attempt_ref: VersionRef
    call_ref: VersionRef
    response_ref: VersionRef
    observation_event: EventEnvelope
    completion_event: EventEnvelope
    turn_event: EventEnvelope
    legacy_adoption_event: EventEnvelope | None


class InvocationLifecycle:
    """Compatible façade for canonical per-firing invocation lifecycles."""

    """Authoritative API for independent per-firing execution lifecycles."""

    _LLM_CALL_RESPONSE_ORIGIN = {
        "llm_call_spec/v1": "provider_response",
        "llm_call_spec/v2": "provider_raw_response",
    }

    _TERMINAL_ATTEMPT_EVENTS = frozenset({
        "provider_attempt_cancelled_before_submission/v1",
        "provider_attempt_cancelled_before_submission/v2",
        "provider_attempt_submission_not_permitted/v1",
        "provider_attempt_proven_not_submitted/v1",
        "provider_attempt_completed/v1",
        "provider_attempt_failed/v1",
        "provider_attempt_cancelled_after_submission/v1",
        "provider_attempt_reconciled_completed/v1",
        "provider_attempt_reconciled_failed/v1",
        "provider_attempt_reconciled_cancelled_after_submission/v1",
        "provider_attempt_owner_interrupted/v1",
        "provider_attempt_host_closed/v1",
    })
    _TERMINAL_CALL_EVENTS = frozenset({
        "llm_call_result_adopted/v1", "llm_call_candidate_not_adopted/v1",
        "llm_call_failed/v1",
        "llm_call_owner_interrupted/v1",
    })

    def __init__(self, service: Any) -> None:
        self.service = service

    def _scoped_firing_idempotency_key(self, caller_key: str) -> str:
        from ._invocation.context import _scoped_firing_idempotency_key as implementation
        return implementation(self, caller_key)

    def _require_ref(self, ref: VersionRef, entity_type: str,
                     version_kind: str) -> Mapping[str, Any]:
        from ._invocation.context import _require_ref as implementation
        return implementation(self, ref, entity_type, version_kind)

    def _require_team_design_root(
            self, ref: VersionRef,
    ) -> Mapping[str, Any]:
        from ._invocation.context import _require_team_design_root as implementation
        return implementation(self, ref)

    @staticmethod
    def _metadata_ref(metadata: Mapping[str, Any], name: str) -> VersionRef:
        from ._invocation.context import _metadata_ref as implementation
        return implementation(metadata, name)

    @staticmethod
    def _same_ref(left: VersionRef, right: VersionRef) -> bool:
        from ._invocation.context import _same_ref as implementation
        return implementation(left, right)

    def _require_registered_operation_closure(
            self, *, net_ref: VersionRef, plan_ref: VersionRef,
            team_design_root_ref: VersionRef, node_ref: VersionRef,
            operation_binding_ref: VersionRef,
            ) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
        from ._invocation.context import _require_registered_operation_closure as implementation
        return implementation(self, net_ref=net_ref, plan_ref=plan_ref, team_design_root_ref=team_design_root_ref, node_ref=node_ref, operation_binding_ref=operation_binding_ref)

    def _current_budget_ref(self) -> VersionRef:
        from ._invocation.context import _current_budget_ref as implementation
        return implementation(self, )

    def _current_active_net_ref(self) -> VersionRef:
        from ._invocation.context import _current_active_net_ref as implementation
        return implementation(self, )

    def _current_marking_ref(self, net_ref: VersionRef) -> VersionRef:
        from ._invocation.context import _current_marking_ref as implementation
        return implementation(self, net_ref)

    @staticmethod
    def _marking_token_refs(metadata: Mapping[str, Any]) -> tuple[VersionRef, ...]:
        from ._invocation.context import _marking_token_refs as implementation
        return implementation(metadata)

    def _existing_command_events(self, idempotency_key: str) -> tuple[EventEnvelope, ...]:
        from ._invocation.context import _existing_command_events as implementation
        return implementation(self, idempotency_key)

    def hydrate_context(
            self, invocation_ref: VersionRef, *,
            require_current_writer: bool = True,
    ) -> InvocationContext:
        from ._invocation.context import hydrate_context as implementation
        return implementation(self, invocation_ref, require_current_writer=require_current_writer)

    def hydrate_serialized_context(
            self, value: Mapping[str, Any], *, boundary: str) -> InvocationContext:
        from ._invocation.context import hydrate_serialized_context as implementation
        return implementation(self, value, boundary=boundary)

    def _validate_context_refs(
            self, context: InvocationContext, *,
            require_current_writer: bool = True,
    ) -> None:
        from ._invocation.context import _validate_context_refs as implementation
        return implementation(self, context, require_current_writer=require_current_writer)

    def _validate_claim_scope(self, claim: FiringClaim) -> Mapping[str, Any]:
        from ._invocation.admission import _validate_claim_scope as implementation
        return implementation(self, claim)

    def _validate_registered_budget_binding(
            self, manifest: Mapping[str, Any], binding: Mapping[str, Any],
    ) -> None:
        # REQUIRED producer/schema wiring: the exact published manifest must
        # contain budget_buckets and bindings must contain budget_bucket_id.
        # An unpublished contract never falls back to implicit role policy.
        from ._invocation.admission import _validate_registered_budget_binding as implementation
        return implementation(self, manifest, binding)

    def _assert_current_claim_authority(self, claim: FiringClaim) -> VersionRef:
        from ._invocation.admission import _assert_current_claim_authority as implementation
        return implementation(self, claim)

    def _active_claim_inputs(
            self, net_ref: VersionRef,
    ) -> list[tuple[str, set[str], set[str]]]:
        from ._invocation.admission import _active_claim_inputs as implementation
        return implementation(self, net_ref)

    def admit_firing(self, claim: FiringClaim, *, idempotency_key: str) -> FiringAdmission:
        from ._invocation.admission import admit_firing as implementation
        return implementation(self, claim, idempotency_key=idempotency_key)

    def revalidate_io(self, context: InvocationContext, *, boundary: str) -> None:
        from ._invocation.execution import revalidate_io as implementation
        return implementation(self, context, boundary=boundary)

    def _begin_registered_operation_execution(
            self, context: InvocationContext, *,
            operation_spec_ref: VersionRef,
            transition_firing_ref: VersionRef,
            executable_transition_binding_ref: VersionRef,
            declaration_terminal_delivery_ref: VersionRef | None,
            input_binding_refs: tuple[VersionRef, ...],
            input_resource_refs: tuple[ResourceVersionRef, ...],
            claimed_input_refs: tuple[VersionRef, ...],
            admission_registry_ordinal: int,
            admission_writer_fencing_epoch: int,
            admission_task_control_sequence: int,
            idempotency_key: str,
    ) -> EventEnvelope:
        from ._invocation.execution import _begin_registered_operation_execution as implementation
        return implementation(self, context, operation_spec_ref=operation_spec_ref, transition_firing_ref=transition_firing_ref, executable_transition_binding_ref=executable_transition_binding_ref, declaration_terminal_delivery_ref=declaration_terminal_delivery_ref, input_binding_refs=input_binding_refs, input_resource_refs=input_resource_refs, claimed_input_refs=claimed_input_refs, admission_registry_ordinal=admission_registry_ordinal, admission_writer_fencing_epoch=admission_writer_fencing_epoch, admission_task_control_sequence=admission_task_control_sequence, idempotency_key=idempotency_key)

    def _terminal_descendant_event_ids(
            self, context: InvocationContext,
            provider_submission_unknown_ref: VersionRef | None = None,
    ) -> tuple[str, ...]:
        from ._invocation.terminal import _terminal_descendant_event_ids as implementation
        return implementation(self, context, provider_submission_unknown_ref)

    @staticmethod
    def _relation_endpoint(row: Mapping[str, Any], field: str) -> VersionRef:
        from ._invocation.terminal import _relation_endpoint as implementation
        return implementation(row, field)

    def _exact_relation_rows(
            self, relation_type: str, *, source: VersionRef | None = None,
            target: VersionRef | None = None) -> tuple[Mapping[str, Any], ...]:
        from ._invocation.terminal import _exact_relation_rows as implementation
        return implementation(self, relation_type, source=source, target=target)

    def _resolve_agent_turn_acceptance(
            self, context: InvocationContext,
            candidate_ref: VersionRef) -> _AgentTurnAcceptanceClosure:
        from ._invocation.terminal import _resolve_agent_turn_acceptance as implementation
        return implementation(self, context, candidate_ref)

    def _validate_provider_candidate(
            self, context: InvocationContext,
            candidate_ref: VersionRef) -> tuple[VersionRef, VersionRef]:
        from ._invocation.terminal import _validate_provider_candidate as implementation
        return implementation(self, context, candidate_ref)

    def _adopted_provider_candidates(
        self, context: InvocationContext) -> frozenset[VersionRef]:
        from ._invocation.terminal import _adopted_provider_candidates as implementation
        return implementation(self, context)

    def _validate_terminal_output_refs(
            self, context: InvocationContext,
            values: Sequence[VersionRef], *,
            enforce_normal_cardinality: bool,
            recovering_committed_agent_actions: bool = False,
    ) -> tuple[VersionRef, ...]:
        from ._invocation.terminal import _validate_terminal_output_refs as implementation
        return implementation(self, context, values, enforce_normal_cardinality=enforce_normal_cardinality, recovering_committed_agent_actions=recovering_committed_agent_actions)

    def mark_operation_terminal_ready(
            self, context: InvocationContext, package: TerminalResultPackage, *,
            idempotency_key: str,
            recovering_committed_agent_actions: bool = False,
    ) -> VersionRef:
        from ._invocation.terminal import mark_operation_terminal_ready as implementation
        return implementation(self, context, package, idempotency_key=idempotency_key, recovering_committed_agent_actions=recovering_committed_agent_actions)

    def _mark_historical_mechanical_receipt_terminal_ready(
            self, context: InvocationContext, package: TerminalResultPackage, *,
            idempotency_key: str,
    ) -> VersionRef:
        from ._invocation.terminal import _mark_historical_mechanical_receipt_terminal_ready as implementation
        return implementation(self, context, package, idempotency_key=idempotency_key)

    def _mark_operation_terminal_ready(
            self, context: InvocationContext, package: TerminalResultPackage, *,
            idempotency_key: str,
            recovering_committed_agent_actions: bool,
            historical_request: _HistoricalMechanicalTerminalReadyRequest | None,
    ) -> VersionRef:
        from ._invocation.terminal import _mark_operation_terminal_ready as implementation
        return implementation(self, context, package, idempotency_key=idempotency_key, recovering_committed_agent_actions=recovering_committed_agent_actions, historical_request=historical_request)

    def settle_firing(self, context: InvocationContext, operation_result_ref: VersionRef,
                      *, idempotency_key: str) -> EventEnvelope:
        from ._invocation.terminal import settle_firing as implementation
        return implementation(self, context, operation_result_ref, idempotency_key=idempotency_key)

    def _revalidate_historical_mechanical_receipt_terminal(
            self, context: InvocationContext,
            package: TerminalResultPackage,
    ) -> int:
        from ._invocation.recovery import _revalidate_historical_mechanical_receipt_terminal as implementation
        return implementation(self, context, package)

    def _revalidate_committed_agent_terminal(
            self, context: InvocationContext,
            package: TerminalResultPackage,
    ) -> None:
        from ._invocation.recovery import _revalidate_committed_agent_terminal as implementation
        return implementation(self, context, package)



@dataclass(frozen=True, slots=True)
class ExactFiringBoundary:
    """Cross-phase connector for an existing true Petri firing boundary.

    Phase 2 must supply ``claim_for`` from canonical net registration.  This
    connector admits only an exact typed invocation before calling
    the worker body.
    """

    lifecycle: InvocationLifecycle
    claim_for: Callable[[str, int, int], FiringClaim]

    def admit(self, transition_id: str, claim_epoch: int, pass_index: int) -> FiringAdmission:
        claim = self.claim_for(transition_id, claim_epoch, pass_index)
        if not isinstance(claim, FiringClaim):
            raise InvocationAdmissionError(
                "existing firing boundary requires an exact pre-registered FiringClaim")
        return self.lifecycle.admit_firing(
            claim,
            idempotency_key=(f"firing-admit:{claim.net_instance_ref.version_id}:"
                             f"{claim.marking_checkpoint_ref.version_id}:"
                             f"{transition_id}:{claim_epoch}:{pass_index}"))
