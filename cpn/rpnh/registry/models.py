"""Immutable D1-C registry DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from .identities import TypedId

Criticality = Literal["authoritative", "observational"]
RelationStrength = Literal["strong", "weak"]


@dataclass(frozen=True, slots=True)
class ObjectRef:
    entity_type: str
    entity_id: TypedId


@dataclass(frozen=True, slots=True)
class VersionRef:
    entity_type: str
    entity_id: TypedId
    version_id: TypedId


@dataclass(frozen=True, slots=True)
class PreparedObject:
    object_type: str
    logical_id: TypedId
    version_id: TypedId
    size: int
    media_type: str
    schema_ref: str
    producer_invocation_id: TypedId | None
    storage_locator: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    envelope_version: str
    event_id: TypedId
    event_type: str
    event_schema_version: str
    criticality: Criticality
    task_id: TypedId
    branch_id: str
    task_round_id: TypedId | None
    net_instance_id: TypedId | None
    stream_id: str
    aggregate_id: str
    aggregate_type: str
    stream_sequence: int
    aggregate_version: int
    task_control_sequence: int | None
    idempotency_key: str
    command_id: str
    correlation_id: str
    causation_event_id: TypedId | None
    parent_event_ids: tuple[TypedId, ...]
    producer_principal: str
    producer_invocation_id: TypedId | None
    transaction_id: TypedId
    occurred_at: str
    recorded_at: str
    payload_schema_ref: str
    payload: Mapping[str, Any]
    writer_fencing_epoch: int
    # Database publication order is read-side authority only.  Events assembled
    # before INSERT do not have an ordinal yet; persisted envelopes always do.
    ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class PendingEvent:
    event_type: str
    criticality: Criticality
    stream_id: str
    aggregate_id: str
    aggregate_type: str
    idempotency_key: str
    command_id: str
    payload: Mapping[str, Any]
    payload_schema_ref: str
    task_control: bool = False
    causation_event_id: TypedId | None = None
    parent_event_ids: tuple[TypedId, ...] = ()
    producer_principal: str = "framework"
    producer_invocation_id: TypedId | None = None
    occurred_at: str | None = None


@dataclass(frozen=True, slots=True)
class TypedRelation:
    relation_id: TypedId
    relation_type: str
    source: VersionRef | ObjectRef
    target: VersionRef | ObjectRef
    strength: RelationStrength = "strong"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    producer_invocation_id: TypedId | None = None
    system_owned: bool = False


@dataclass(frozen=True, slots=True)
class ProjectionManifest:
    projection_name: str
    projector_version: str
    schema_version: str
    through_sequence: int
    stream_heads: Mapping[str, int]
    build_status: str


@dataclass(frozen=True, slots=True)
class MarkingCheckpoint:
    net_instance_id: TypedId
    marking: Mapping[str, Any]
    settled: bool

    def install_for(self, net_instance_id: TypedId) -> Mapping[str, Any]:
        if not self.settled:
            raise ValueError("only a settled marking checkpoint can be installed")
        if self.net_instance_id != net_instance_id:
            raise ValueError("marking checkpoint belongs to a different net instance")
        return self.marking


class RuntimeTimedCPNError(RuntimeError):
    """Base class for fail-closed runtime-observed timing failures."""


class RuntimeTimedQueueOverflowError(RuntimeTimedCPNError):
    """A complete runtime queue refresh exceeds its runtime ceiling."""


class RuntimeTimedStateConflictError(RuntimeTimedCPNError):
    """A lifecycle command conflicts with the fact-ordered durable state."""


class RuntimeTimedRecoveryError(RuntimeTimedCPNError):
    """Runtime timing facts cannot be replayed without inference or repair."""


RuntimeElapsedStatus = Literal[
    "NOT_STARTED", "CONTIGUOUS", "SEGMENTED_LOWER_BOUND", "CLOCK_ANOMALY"]
RuntimeOperationState = Literal[
    "NOT_SUBMITTED", "STARTED", "SUBMISSION_UNKNOWN", "OUTCOME_UNKNOWN",
    "TERMINAL", "PROVEN_NOT_SUBMITTED"]
RuntimeTerminalStatus = Literal["SUCCEEDED", "FAILED", "CANCELLED"]
RuntimeReconciliationState = Literal[
    "NONE", "REQUIRED", "PROVEN_TERMINAL", "PROVEN_NOT_SUBMITTED", "UNSAFE"]


@dataclass(frozen=True, slots=True)
class RuntimeClockSample:
    utc: str
    monotonic: float
    segment_id: str


@dataclass(frozen=True, slots=True)
class RuntimeClockSegment:
    segment_id: str
    first_utc: str
    last_utc: str
    first_monotonic: float
    last_monotonic: float
    observed_seconds: float
    anomaly: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeTimingPolicy:
    runtime_config_ref: VersionRef
    eligible_terminal_statuses: tuple[RuntimeTerminalStatus, ...]
    max_time_seconds: float | None
    deadline_utc: str | None
    max_retries: int
    backoff_seconds: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class RuntimeTimedQueueCandidate:
    """One exact scheduler queue entry observed at runtime."""

    queue_entry_id: str
    scheduler_binding_id: str
    binding_ref: VersionRef
    binding_key: str
    transition_id: str
    config_json: str
    agent_id: str
    input_claim_refs: tuple[VersionRef, ...]
    timing_policy: RuntimeTimingPolicy
    queue_sequence: int
    queued_sample: RuntimeClockSample
    queued_event_sequence: int
    logical_firing_id: str | None = None
    retry_number: int = 0
    predecessor_firing_id: str | None = None
    predecessor_attempt_id: str | None = None
    predecessor_firing_ref: VersionRef | None = None
    predecessor_attempt_ref: VersionRef | None = None
    scheduled_firing_ref: VersionRef | None = None
    scheduled_attempt_ref: VersionRef | None = None
    scheduled_firing_id: str | None = None
    scheduled_attempt_id: str | None = None
    scheduled_claim_sequence: int | None = None
    not_before_utc: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTimedStartCandidate:
    """One queued firing plus claims to commit before dispatch."""

    queue_entry_id: str
    scheduler_firing_id: str
    scheduler_attempt_id: str
    scheduler_operation_id: str
    scheduler_worker_id: str
    logical_firing_id: str
    firing_ref: VersionRef
    attempt_ref: VersionRef
    operation_ref: VersionRef
    admission_checkpoint_ref: VersionRef
    firing_admission_ref: VersionRef
    claim_marking_delta_ref: VersionRef
    input_claim_refs: tuple[VersionRef, ...]
    worker_claim_refs: tuple[VersionRef, ...]
    resource_claim_refs: tuple[VersionRef, ...]
    resource_claims: tuple[tuple[str, tuple[VersionRef, ...]], ...]
    claimed_sample: RuntimeClockSample
    claimed_event_sequence: int
    started_sample: RuntimeClockSample
    started_event_sequence: int
    reuse_input_claim: bool
    agent_runtime_ceiling: int
    worker_runtime_ceiling: int
    resource_runtime_ceilings: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RuntimeTimedQueueEntry:
    queue_entry_id: str
    scheduler_binding_id: str
    binding_ref: VersionRef
    binding_key: str
    transition_id: str
    config_json: str
    agent_id: str
    input_claim_refs: tuple[VersionRef, ...]
    timing_policy: RuntimeTimingPolicy
    queue_sequence: int
    queued_sample: RuntimeClockSample
    queued_event_sequence: int
    logical_firing_id: str | None
    retry_number: int
    predecessor_firing_id: str | None
    predecessor_attempt_id: str | None
    predecessor_firing_ref: VersionRef | None
    predecessor_attempt_ref: VersionRef | None
    scheduled_firing_ref: VersionRef | None
    scheduled_attempt_ref: VersionRef | None
    scheduled_firing_id: str | None
    scheduled_attempt_id: str | None
    scheduled_claim_sequence: int | None
    not_before_utc: str | None


@dataclass(frozen=True, slots=True)
class RuntimeTimedActiveFiring:
    scheduler_firing_id: str
    scheduler_attempt_id: str
    scheduler_operation_id: str
    scheduler_worker_id: str
    logical_firing_id: str
    firing_ref: VersionRef
    attempt_ref: VersionRef
    operation_ref: VersionRef
    admission_checkpoint_ref: VersionRef
    firing_admission_ref: VersionRef
    claim_marking_delta_ref: VersionRef
    queue_entry: RuntimeTimedQueueEntry
    input_claim_refs: tuple[VersionRef, ...]
    worker_claim_refs: tuple[VersionRef, ...]
    resource_claim_refs: tuple[VersionRef, ...]
    resource_claims: tuple[tuple[str, tuple[VersionRef, ...]], ...]
    claimed_sample: RuntimeClockSample
    claimed_event_sequence: int
    started_sample: RuntimeClockSample
    started_event_sequence: int
    reuse_input_claim: bool
    agent_runtime_ceiling: int
    worker_runtime_ceiling: int
    resource_runtime_ceilings: tuple[tuple[str, int], ...]
    operation_state: RuntimeOperationState
    elapsed_seconds: float
    elapsed_status: RuntimeElapsedStatus
    clock_segments: tuple[RuntimeClockSegment, ...]
    last_observed_sample: RuntimeClockSample
    max_time_reached: bool
    deadline_reached: bool
    timed_out: bool
    reconciliation_state: RuntimeReconciliationState
    uncertain_state: Literal["SUBMISSION_UNKNOWN", "OUTCOME_UNKNOWN"] | None
    uncertainty_evidence_id: str | None
    uncertainty_evidence_ref: VersionRef | None
    uncertainty_event_sequence: int | None
    uncertainty_sample: RuntimeClockSample | None
    reconciled_state: Literal["PROVEN_TERMINAL", "PROVEN_NOT_SUBMITTED"] | None
    reconciled_event_sequence: int | None
    retry_safety_evidence_id: str | None
    retry_safety_evidence_ref: VersionRef | None


@dataclass(frozen=True, slots=True)
class RuntimeTimedSettledFiring:
    active: RuntimeTimedActiveFiring
    terminal_status: RuntimeTerminalStatus
    terminal_payload_json: str
    terminal_witness_ref: VersionRef
    terminal_sample: RuntimeClockSample
    terminal_event_sequence: int
    observed_duration_seconds: float | None
    observed_segmented_duration_seconds: float
    output_refs: tuple[VersionRef, ...]
    marking_delta_ref: VersionRef
    released_runtime_claim_refs: tuple[VersionRef, ...]
    retained_input_claim_refs: tuple[VersionRef, ...]
    logical_claim_released: bool
    settled_sample: RuntimeClockSample
    settled_event_sequence: int
    retry_safety_evidence_id: str | None = None
    retry_safety_evidence_ref: VersionRef | None = None
    retry_authorization_id: str | None = None
    retry_proof_ref: VersionRef | None = None
    retry_proof_kind: Literal["SAFELY_TERMINAL", "PROVEN_NOT_SUBMITTED"] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTimedMarkingClaim:
    """Canonical row from TeamNetMarking.timed_claim_snapshot()."""

    logical_firing_id: str
    transition_id: str
    binding_key: str
    claim_epoch: int
    input_ids: tuple[str, ...]
    resource_claims: tuple[tuple[str, int, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class RuntimeTimedMarkingCheckpointAuthority:
    """Registry form of TimedMarkingCheckpointAuthority."""

    marking_checkpoint_ref: VersionRef
    epoch: int
    claims: tuple[RuntimeTimedMarkingClaim, ...]


@dataclass(frozen=True, slots=True)
class RuntimeTimedSchedulerProjectionEvidence:
    """Exact RuntimeObservedTimedCPNScheduler snapshot evidence."""

    snapshot_json: str
    event_count: int
    last_event_sequence: int
    claimed_event_sequences: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RuntimeTimedCPNProjection:
    """Exact runtime-observed scheduler state reconstructed from fact order."""

    net_instance_ref: VersionRef
    declaration_ref: VersionRef
    declaration_schema_ref: str
    protocol: Literal["runtime_observed_timed_cpn_queue/v2"]
    queue_capacity: int
    binding_cartesian_bound: int
    max_firings_per_event_cycle: int
    max_runtime_seconds_per_event_cycle: float
    queue: tuple[RuntimeTimedQueueEntry, ...]
    active: tuple[RuntimeTimedActiveFiring, ...]
    settled: tuple[RuntimeTimedSettledFiring, ...]
    marking_authority: RuntimeTimedMarkingCheckpointAuthority
    scheduler_projection: RuntimeTimedSchedulerProjectionEvidence
    last_snapshot_ref: VersionRef
    next_scheduler_event_sequence: int
    next_queue_sequence: int
    recovery_status: Literal["runnable", "reconciliation_required"]
    reconciliation_attempt_refs: tuple[VersionRef, ...]


@dataclass(frozen=True, slots=True)
class RetryScheduleResult:
    """Stable retry scheduling result for both new and existing exact children."""

    projection: RuntimeTimedCPNProjection
    child: RuntimeTimedQueueEntry
    existing_child: bool


@dataclass(frozen=True, slots=True)
class TimedSuccessorSettlementReceipt:
    """Atomic Registry receipt for one timed terminal successor commit."""

    base_checkpoint_ref: VersionRef
    logical_firing_id: str
    runtime_projection: RuntimeTimedCPNProjection
    marking_authority: object
    marking_delta_ref: VersionRef
    checkpoint_ref: VersionRef


@dataclass(frozen=True, slots=True)
class RuntimeTimedRecoveryAuthority:
    """Public recovery closure; callers never receive Registry internals."""

    runtime_projection: RuntimeTimedCPNProjection
    marking_authority: object
    reconciliation_attempt_refs: tuple[VersionRef, ...]
