"""Immutable DTOs for the D1-C native resource contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import (
    TYPE_CHECKING,
    Literal,
    Mapping,
    Protocol,
    TypeAlias,
    runtime_checkable,
)

from .identities import TypedId
from .models import VersionRef
from .schema_catalog import canonical_json
if TYPE_CHECKING:
    from .credentials import ProviderCredentialBinding
    from .invocations import InvocationContext
    from .provider_calls import LLMCallV2, ProviderAttemptV2
    from .operations import (
        OperationFaultRouteAuthority,
    )

JsonScalar: TypeAlias = str | int | float | bool | None
DeliveryBoundary: TypeAlias = Literal[
    "llm_prompt", "tool_result", "subprocess_read", "parent_receipt",
    "petri_input",
]
DeliveryOutcome: TypeAlias = Literal["acknowledged", "unknown", "failed"]
PublicationOriginKind: TypeAlias = Literal[
    "petri_output", "delegated_leaf", "workspace_write", "private_system",
    "checkpoint_repair", "provider_response", "provider_raw_response",
]
def _require_exact_version_ref(
        label: str,
        ref: object,
        *,
        entity_type: str,
        entity_id_kind: str,
        version_id_kind: str,
) -> VersionRef:
    """Reject an entity-shaped or cross-kind ref at a typed authority seam."""
    if (not isinstance(ref, VersionRef)
            or ref.entity_type != entity_type
            or not isinstance(ref.entity_id, TypedId)
            or ref.entity_id.kind != entity_id_kind
            or not isinstance(ref.version_id, TypedId)
            or ref.version_id.kind != version_id_kind):
        raise TypeError(f"{label} requires one exact {entity_type} version ref")
    return ref


def _require_protocol_identity(label: str, value: object) -> str:
    if (not isinstance(value, str) or not value
            or value != value.strip() or len(value) > 160
            or "/v" not in value
            or any(not (character.isalnum() or character in "._-/")
                   for character in value)):
        raise ValueError(f"{label} must be one canonical protocol identity")
    family, version = value.rsplit("/v", 1)
    if not family or not version.isdigit() or int(version) < 1:
        raise ValueError(f"{label} must be one versioned protocol identity")
    return value


def _version_ref_document(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _resource_ref_document(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


def _require_canonical_token_refs(
        label: str,
        refs: object,
        *,
        minimum: int,
) -> tuple[VersionRef, ...]:
    if not isinstance(refs, tuple) or len(refs) < minimum:
        raise TypeError(
            f"{label} requires at least {minimum} exact Petri token refs")
    for index, ref in enumerate(refs):
        _require_exact_version_ref(
            f"{label}[{index}]", ref,
            entity_type="petri_token/v1",
            entity_id_kind="petri_token",
            version_id_kind="petri_token_version",
        )
    ordered = tuple(sorted(
        refs,
        key=lambda ref: (str(ref.entity_id), str(ref.version_id)),
    ))
    if len(set(refs)) != len(refs) or refs != ordered:
        raise ValueError(f"{label} is not a canonical exact-token closure")
    return refs


@dataclass(frozen=True, slots=True)
class ResourceVersionRef:
    resource_id: TypedId
    resource_version_id: TypedId

    def __post_init__(self) -> None:
        if self.resource_id.kind != "resource":
            raise TypeError("resource_id must be a typed resource identity")
        if self.resource_version_id.kind != "resource_version":
            raise TypeError("resource_version_id must be a typed resource-version identity")

    def as_version_ref(self) -> VersionRef:
        return VersionRef(
            "resource_version/v1", self.resource_id, self.resource_version_id)


@dataclass(frozen=True, slots=True)
class RegistryHead:
    ordinal: int
    writer_fencing_epoch: int
    task_control_sequence: int
    stream_heads: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ResourceAddress:
    scope_ref: VersionRef
    opaque_name: str


@dataclass(frozen=True, slots=True)
class ResourceAddressBindingRef:
    binding_id: TypedId
    binding_version_id: TypedId
    tombstone: bool

    def as_version_ref(self) -> VersionRef:
        return VersionRef(
            "resource_address_binding/v1", self.binding_id,
            self.binding_version_id)


@dataclass(frozen=True, slots=True)
class AddressBindingIntent:
    address: ResourceAddress
    authorization_ref: VersionRef
    expected_binding_ref: ResourceAddressBindingRef | None


@dataclass(frozen=True, slots=True)
class PetriOutputOrigin:
    output_binding_ref: VersionRef
    activation_ref: VersionRef | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceWriteOrigin:
    operation_binding_ref: VersionRef
    write_intent_ref: VersionRef


@dataclass(frozen=True, slots=True)
class PrivateSystemOrigin:
    bootstrap_command_ref: VersionRef


@dataclass(frozen=True, slots=True)
class CheckpointRepairOrigin:
    """Registry-only authority for one append-only repair replacement."""

    checkpoint_repair_ref: VersionRef
    source_resource_ref: ResourceVersionRef

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "checkpoint repair origin", self.checkpoint_repair_ref,
            entity_type="checkpoint_repair/v1",
            entity_id_kind="checkpoint_repair",
            version_id_kind="checkpoint_repair_version")
        if not isinstance(self.source_resource_ref, ResourceVersionRef):
            raise TypeError(
                "checkpoint repair origin requires one exact source resource")


@dataclass(frozen=True, slots=True)
class ProviderResponseOrigin:
    """Registry-only provenance for one immutable provider candidate.

    This origin records content provenance only.  It is deliberately not an
    application publication permission and has no Petri marking semantics.
    """

    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef

    def __post_init__(self) -> None:
        if (self.provider_attempt_ref.entity_type != "provider_attempt_spec/v1"
                or self.provider_attempt_ref.entity_id.kind != "provider_attempt"
                or self.provider_attempt_ref.version_id.kind
                != "provider_attempt_version"):
            raise TypeError(
                "provider response origin requires an exact provider-attempt version")
        if (self.llm_call_ref.entity_type != "llm_call_spec/v1"
                or self.llm_call_ref.entity_id.kind != "llm_call"
                or self.llm_call_ref.version_id.kind != "llm_call_version"):
            raise TypeError(
                "provider response origin requires an exact LLM-call version")


@dataclass(frozen=True, slots=True)
class ProviderRawResponseOrigin:
    """Raw-first provenance bound to the parallel v2 call authority."""

    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "raw provider response attempt", self.provider_attempt_ref,
            entity_type="provider_attempt_spec/v1",
            entity_id_kind="provider_attempt",
            version_id_kind="provider_attempt_version")
        _require_exact_version_ref(
            "raw provider response call", self.llm_call_ref,
            entity_type="llm_call_spec/v2", entity_id_kind="llm_call",
            version_id_kind="llm_call_version")


@dataclass(frozen=True, slots=True)
class ProviderResponseMetadata:
    """Closed non-content facts attached to one provider candidate.

    Response content is deliberately absent: the exact registered resource is
    the only response-body authority and transport.
    """

    finish_reason: str | None
    external_request_id: str | None

    def __post_init__(self) -> None:
        if self.finish_reason is not None and not isinstance(self.finish_reason, str):
            raise TypeError("provider finish_reason must be a string or None")
        if (self.external_request_id is not None
                and not isinstance(self.external_request_id, str)):
            raise TypeError(
                "external provider request id must be a string or None")


PublicationOrigin: TypeAlias = (
    PetriOutputOrigin | WorkspaceWriteOrigin | PrivateSystemOrigin
    | CheckpointRepairOrigin | ProviderResponseOrigin
    | ProviderRawResponseOrigin
)


@dataclass(frozen=True, slots=True)
class ProviderAttemptV2Authority:
    """Current exact v2 reservation plus its Registry backend authority."""

    attempt: "ProviderAttemptV2"
    backend_config: ProviderBackendConfig
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        from .provider_calls import ProviderAttemptV2
        if (not isinstance(self.attempt, ProviderAttemptV2)
                or not isinstance(self.backend_config, ProviderBackendConfig)
                or self.attempt.call.backend != self.backend_config.backend
                or self.attempt.call.model != self.backend_config.model
                or self.attempt.call.llm_input_target_ref
                != self.backend_config.resource_ref
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError("v2 provider attempt authority is incomplete")

    @property
    def ref(self) -> VersionRef:
        return self.attempt.ref

    @property
    def provider_attempt_ref(self) -> VersionRef:
        """Exact provider-attempt ref consumed by the permit DTO."""
        return self.attempt.ref

    @property
    def call(self) -> "LLMCallV2":
        return self.attempt.call


@dataclass(frozen=True, slots=True)
class PublishResourceWithoutPayload:
    origin: PublicationOrigin
    media_type: str
    content_schema_ref: str | None
    summary: str
    lifetime_ref: VersionRef
    content_schema_authority_ref: VersionRef | ResourceVersionRef | None = None
    address_bindings: tuple[AddressBindingIntent, ...] = ()
    supersedes: ResourceVersionRef | None = None
    derived_from: tuple[ResourceVersionRef, ...] = ()
    tool_evidence_refs: tuple[VersionRef, ...] = ()
    contributor_delegations: tuple[VersionRef, ...] = ()
    descriptors: Mapping[str, JsonScalar | tuple[JsonScalar, ...]] = field(
        default_factory=dict)
    extensions: Mapping[str, object] = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class PublishResource(PublishResourceWithoutPayload):
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class PublishPathResource:
    source_binding_ref: VersionRef
    source_relative_path: PurePath
    resource: PublishResourceWithoutPayload


@dataclass(frozen=True, slots=True)
class PrepareResourceDelivery:
    resource_ref: ResourceVersionRef
    authorization_ref: VersionRef
    delivery_id: TypedId
    idempotency_key: str
    consumer_boundary: DeliveryBoundary
    purpose: str


@dataclass(frozen=True, slots=True)
class AcknowledgeResourceDelivery:
    delivery_ref: VersionRef
    boundary_receipt_ref: VersionRef
    outcome: DeliveryOutcome
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AuthorizeResourceRelease:
    delivery_ref: VersionRef
    expected_boundary: DeliveryBoundary
    release_nonce: TypedId
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class OpaqueBrokerChannelRef:
    channel_id: str


@dataclass(frozen=True, slots=True)
class PreparedResourceDelivery:
    delivery_ref: VersionRef
    exact_resource_ref: ResourceVersionRef
    byte_count: int
    boundary: DeliveryBoundary
    authorization_ref: VersionRef
    prepared_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class AuthorizedResourceRelease:
    delivery_ref: VersionRef
    witness_ref: VersionRef
    exact_resource_ref: ResourceVersionRef
    byte_count: int
    boundary: DeliveryBoundary
    expires_at: str
    broker_channel_ref: OpaqueBrokerChannelRef


@dataclass(frozen=True, slots=True)
class ResourceDeliveryReceipt:
    delivery_ref: VersionRef
    terminal_event_id: TypedId
    outcome: DeliveryOutcome
    observed_read_event_id: TypedId | None


@dataclass(frozen=True, slots=True)
class BindResourceAddress:
    address: ResourceAddress
    resource_ref: ResourceVersionRef
    authorization_ref: VersionRef
    expected_binding_ref: ResourceAddressBindingRef | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class UnbindResourceAddress:
    address: ResourceAddress
    authorization_ref: VersionRef
    expected_binding_ref: ResourceAddressBindingRef
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RelationFilter:
    relation_type: str
    direction: Literal["forward", "reverse", "either"] = "either"


@dataclass(frozen=True, slots=True)
class QueryCursor:
    query_facts: Mapping[str, Any]
    principal_ref: VersionRef
    grant_ref: VersionRef
    through_head: RegistryHead
    offset: int
    authority_facts: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ResourceQuery:
    task_ref: VersionRef
    scope_refs: tuple[VersionRef, ...]
    producer_refs: tuple[VersionRef, ...] = ()
    relation_filters: tuple[RelationFilter, ...] = ()
    media_types: tuple[str, ...] = ()
    address_name: str | None = None
    through_head: RegistryHead | None = None
    cursor: QueryCursor | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class SafeDescriptor:
    name: str
    values: tuple[JsonScalar, ...]


@dataclass(frozen=True, slots=True)
class ResourceHeader:
    ref: ResourceVersionRef
    task_ref: VersionRef
    round_ref: VersionRef | None
    net_ref: VersionRef | None
    producer_ref: VersionRef
    origin_kind: PublicationOriginKind
    media_type: str
    byte_size: int
    content_schema_ref: str | None
    content_schema_authority_ref: VersionRef | ResourceVersionRef | None
    display_summary: str | None
    descriptor_labels: tuple[SafeDescriptor, ...]
    relation_kinds: tuple[str, ...]
    published_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class ResourceQueryResult:
    headers: tuple[ResourceHeader, ...]
    cursor: QueryCursor | None
    through_head: RegistryHead
    head_sequence: int
    lag: int


@dataclass(frozen=True, slots=True)
class ResourceAddressResult:
    address: ResourceAddress
    binding_ref: ResourceAddressBindingRef
    resource_ref: ResourceVersionRef
    through_head: RegistryHead


@dataclass(frozen=True, slots=True)
class RelationQuery:
    task_ref: VersionRef
    endpoint_refs: tuple[ResourceVersionRef, ...]
    relation_filters: tuple[RelationFilter, ...] = ()
    through_head: RegistryHead | None = None
    cursor: QueryCursor | None = None
    limit: int = 100


@dataclass(frozen=True, slots=True)
class ResourceRelation:
    relation_id: TypedId
    relation_type: str
    source_ref: ResourceVersionRef | VersionRef
    target_ref: ResourceVersionRef | VersionRef
    strength: Literal["strong", "weak"]


@dataclass(frozen=True, slots=True)
class RelationQueryResult:
    relations: tuple[ResourceRelation, ...]
    cursor: QueryCursor | None
    through_head: RegistryHead
    head_sequence: int
    lag: int


@dataclass(frozen=True, slots=True)
class RegistryObserverContext:
    observer_principal_ref: VersionRef
    observer_profile_ref: VersionRef
    task_ref: VersionRef
    grant_ref: VersionRef
    issued_writer_fencing_epoch: int
    issued_task_control_sequence: int
    reader_fence: str
    expires_at: str
    purpose: str


@dataclass(frozen=True, slots=True)
class InvocationContextHandle:
    invocation_ref: VersionRef


@dataclass(frozen=True, slots=True)
class PetriFiringAllocationRecord:
    """One Registry-published occurrence selected from an eligible queue."""

    local_key: int | VersionRef
    transition_id: str
    claim_epoch: int
    attempt_index: int
    token_refs: tuple[VersionRef, ...]

    def __post_init__(self) -> None:
        if (isinstance(self.local_key, bool)
                or not isinstance(self.local_key, (int, VersionRef))
                or (isinstance(self.local_key, int) and self.local_key < 0)
                or not isinstance(self.transition_id, str)
                or not self.transition_id
                or isinstance(self.claim_epoch, bool)
                or not isinstance(self.claim_epoch, int)
                or self.claim_epoch < 0
                or isinstance(self.attempt_index, bool)
                or not isinstance(self.attempt_index, int)
                or self.attempt_index <= 0
                or not isinstance(self.token_refs, tuple)
                or len(set(self.token_refs)) != len(self.token_refs)
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "petri_token/v1"
                       for ref in self.token_refs)):
            raise TypeError("Petri firing allocation record is incomplete")


@dataclass(frozen=True, slots=True)
class PetriFiringAllocationAuthority:
    """Persisted authority for one randomized eligible firing allocation."""

    net_ref: VersionRef
    checkpoint_ref: VersionRef
    claim_epoch: int
    eligible_queue: tuple[tuple[str, tuple[VersionRef, ...]], ...]
    allocations: tuple[PetriFiringAllocationRecord, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.net_ref, VersionRef)
                or self.net_ref.entity_type != "net_instance/v1"
                or not isinstance(self.checkpoint_ref, VersionRef)
                or self.checkpoint_ref.entity_type != "marking_checkpoint/v1"
                or isinstance(self.claim_epoch, bool)
                or not isinstance(self.claim_epoch, int)
                or self.claim_epoch < 0
                or not isinstance(self.eligible_queue, tuple)
                or not self.eligible_queue
                or any(not isinstance(item, tuple) or len(item) != 2
                       or not isinstance(item[0], str) or not item[0]
                       or not isinstance(item[1], tuple)
                       or len(set(item[1])) != len(item[1])
                       or any(not isinstance(ref, VersionRef)
                              or ref.entity_type != "petri_token/v1"
                              for ref in item[1])
                       for item in self.eligible_queue)
                or not isinstance(self.allocations, tuple)
                or not self.allocations
                or any(not isinstance(item, PetriFiringAllocationRecord)
                       or item.claim_epoch != self.claim_epoch
                       for item in self.allocations)
                or len({item.local_key for item in self.allocations})
                != len(self.allocations)
                or len({(item.transition_id, item.attempt_index)
                        for item in self.allocations})
                != len(self.allocations)):
            raise TypeError("Petri firing allocation authority is incomplete")


@dataclass(frozen=True, slots=True)
class AgentLoopResourceLifecycleAuthority:
    """Exact persisted state for one same-firing resource request."""

    lifecycle_ref: VersionRef
    request_ref: VersionRef
    queue_entry_ref: VersionRef
    transition_firing_ref: VersionRef
    invocation_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    operation_binding_ref: VersionRef
    agent_loop_ref: VersionRef
    agent_turn_ref: VersionRef
    agent_action_ref: VersionRef
    requester_agent_ref: VersionRef
    logical_resource_id: TypedId
    lock_resource_ref: ResourceVersionRef
    resource_ref: ResourceVersionRef
    llm_turns_used: int
    access_mode: Literal["read", "edit", "upgrade"]
    access_checkpoint_ref: VersionRef
    access_net_ref: VersionRef
    access_claim_epoch: int
    resource_token_ref: VersionRef
    lease_pool_place: str
    lease_identity_ref: VersionRef
    petri_input_arc_mode: Literal["read", "borrow"]
    petri_output_arc_mode: Literal["return"] | None
    petri_arc_kind: Literal["read", "borrow"]
    return_arc_required: bool
    state: Literal[
        "waiting_resource", "granted", "resumed", "released", "cleaned",
        "sealed"]
    writer_fencing_epoch: int
    heartbeat_ref: VersionRef
    heartbeat_at_utc: str
    grant_ref: VersionRef | None = None
    resume_ref: VersionRef | None = None
    release_ref: VersionRef | None = None
    cleanup_ref: VersionRef | None = None
    lease_ref: VersionRef | None = None
    seal_ref: VersionRef | None = None
    settlement_ref: VersionRef | None = None
    settlement_disposition: Literal[
        "success", "fault", "abandon"] | None = None

    def __post_init__(self) -> None:
        typed = (
            (self.lifecycle_ref, "resource_access_lifecycle/v1"),
            (self.transition_firing_ref, "transition_firing/v1"),
            (self.invocation_ref, "invocation/v1"),
            (self.operation_execution_lease_ref,
             "operation_execution_lease/v1"),
            (self.operation_binding_ref, "operation_binding/v1"),
            (self.agent_loop_ref, "agent_loop/v1"),
            (self.agent_turn_ref, "agent_turn/v1"),
            (self.agent_action_ref, "agent_action/v2"),
        )
        if (any(not isinstance(ref, VersionRef)
                or ref.entity_type != entity_type
                for ref, entity_type in typed)
                or any(not isinstance(ref, VersionRef) for ref in (
                    self.request_ref, self.queue_entry_ref,
                    self.requester_agent_ref,
                    self.heartbeat_ref))
                or not isinstance(self.logical_resource_id, TypedId)
                or self.logical_resource_id.kind != "resource"
                or not isinstance(self.lock_resource_ref, ResourceVersionRef)
                or not isinstance(self.resource_ref, ResourceVersionRef)
                or self.logical_resource_id != self.resource_ref.resource_id
                or self.lock_resource_ref != self.resource_ref
                or not isinstance(self.access_checkpoint_ref, VersionRef)
                or self.access_checkpoint_ref.entity_type
                != "marking_checkpoint/v1"
                or not isinstance(self.access_net_ref, VersionRef)
                or self.access_net_ref.entity_type != "net_instance/v1"
                or isinstance(self.access_claim_epoch, bool)
                or not isinstance(self.access_claim_epoch, int)
                or self.access_claim_epoch < 0
                or not isinstance(self.resource_token_ref, VersionRef)
                or self.resource_token_ref.entity_type != "petri_token/v1"
                or not isinstance(self.lease_pool_place, str)
                or not self.lease_pool_place
                or not isinstance(self.lease_identity_ref, VersionRef)
                or self.petri_input_arc_mode
                != ("read" if self.access_mode == "read" else "borrow")
                or self.petri_output_arc_mode
                != (None if self.access_mode == "read" else "return")
                or self.petri_arc_kind != self.petri_input_arc_mode
                or self.return_arc_required
                != (self.access_mode != "read")
                or isinstance(self.llm_turns_used, bool)
                or not isinstance(self.llm_turns_used, int)
                or self.llm_turns_used < 1
                or self.access_mode not in {"read", "edit", "upgrade"}
                or self.state not in {
                    "waiting_resource", "granted", "resumed", "released",
                    "cleaned", "sealed"}
                or isinstance(self.writer_fencing_epoch, bool)
                or not isinstance(self.writer_fencing_epoch, int)
                or self.writer_fencing_epoch < 1
                or not isinstance(self.heartbeat_at_utc, str)
                or not self.heartbeat_at_utc
                or any(ref is not None and not isinstance(ref, VersionRef)
                       for ref in (self.grant_ref, self.resume_ref,
                                   self.release_ref, self.cleanup_ref,
                                   self.lease_ref, self.seal_ref,
                                   self.settlement_ref))
                or self.settlement_disposition not in {
                    None, "success", "fault", "abandon"}):
            raise TypeError(
                "agent-loop resource lifecycle authority is incomplete")
        if (self.state in {"granted", "resumed", "released", "cleaned"}
                and (self.grant_ref is None or self.lease_ref is None)):
            raise TypeError("granted lifecycle requires grant and lease refs")
        if (self.state in {"resumed", "released", "cleaned"}
                and self.resume_ref is None):
            raise TypeError("resumed lifecycle requires its resume ref")
        if (self.state in {"released", "cleaned"}
                and self.release_ref is None):
            raise TypeError("released lifecycle requires its release ref")
        if self.state == "cleaned" and self.cleanup_ref is None:
            raise TypeError("cleaned lifecycle requires its cleanup ref")
        if (self.state == "sealed") != (
                self.seal_ref is not None
                and self.settlement_ref is not None
                and self.settlement_disposition is not None):
            raise TypeError(
                "sealed lifecycle requires its exact settlement authority")


@dataclass(frozen=True, slots=True)
class AgentLoopResourceGrantAuthority:
    """Exact grant for resuming one WAITING_RESOURCE loop in the same firing."""

    transition_firing_ref: VersionRef
    invocation_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    operation_binding_ref: VersionRef
    queue_entry_id: str
    resource_ref: ResourceVersionRef
    writer_fencing_epoch: int
    agent_loop_ref: VersionRef
    agent_turn_ref: VersionRef
    agent_action_ref: VersionRef
    access_mode: Literal["read", "edit", "upgrade"]
    access_checkpoint_ref: VersionRef
    access_net_ref: VersionRef
    access_claim_epoch: int
    resource_token_ref: VersionRef
    lease_pool_place: str
    lease_identity_ref: VersionRef
    petri_input_arc_mode: Literal["read", "borrow"]
    petri_output_arc_mode: Literal["return"] | None
    petri_arc_kind: Literal["read", "borrow"]
    return_arc_required: bool
    lifecycle_ref: VersionRef | None = None
    request_ref: VersionRef | None = None
    heartbeat_ref: VersionRef | None = None
    grant_ref: VersionRef | None = None
    lease_ref: VersionRef | None = None
    llm_turns_used: int | None = None
    same_firing_continuation: bool = True

    def __post_init__(self) -> None:
        expected = (
            (self.transition_firing_ref, "transition_firing/v1"),
            (self.invocation_ref, "invocation/v1"),
            (self.operation_execution_lease_ref,
             "operation_execution_lease/v1"),
            (self.operation_binding_ref, "operation_binding/v1"),
            (self.agent_loop_ref, "agent_loop/v1"),
            (self.agent_turn_ref, "agent_turn/v1"),
            (self.agent_action_ref, "agent_action/v2"),
        )
        if (any(not isinstance(ref, VersionRef)
                or ref.entity_type != entity_type
                for ref, entity_type in expected)
                or not isinstance(self.resource_ref, ResourceVersionRef)
                or not isinstance(self.queue_entry_id, str)
                or not self.queue_entry_id
                or isinstance(self.writer_fencing_epoch, bool)
                or not isinstance(self.writer_fencing_epoch, int)
                or self.writer_fencing_epoch < 1
                or any(ref is not None and not isinstance(ref, VersionRef)
                       for ref in (self.lifecycle_ref, self.request_ref,
                                   self.heartbeat_ref, self.grant_ref,
                                   self.lease_ref))
                or (self.llm_turns_used is not None and (
                    isinstance(self.llm_turns_used, bool)
                    or not isinstance(self.llm_turns_used, int)
                    or self.llm_turns_used < 1))
                or self.access_mode not in {"read", "edit", "upgrade"}
                or not isinstance(self.access_checkpoint_ref, VersionRef)
                or self.access_checkpoint_ref.entity_type
                != "marking_checkpoint/v1"
                or not isinstance(self.access_net_ref, VersionRef)
                or self.access_net_ref.entity_type != "net_instance/v1"
                or isinstance(self.access_claim_epoch, bool)
                or not isinstance(self.access_claim_epoch, int)
                or self.access_claim_epoch < 0
                or not isinstance(self.resource_token_ref, VersionRef)
                or self.resource_token_ref.entity_type != "petri_token/v1"
                or not isinstance(self.lease_pool_place, str)
                or not self.lease_pool_place
                or not isinstance(self.lease_identity_ref, VersionRef)
                or self.petri_input_arc_mode
                != ("read" if self.access_mode == "read" else "borrow")
                or self.petri_output_arc_mode
                != (None if self.access_mode == "read" else "return")
                or self.petri_arc_kind != self.petri_input_arc_mode
                or self.return_arc_required
                != (self.access_mode != "read")
                or self.same_firing_continuation is not True):
            raise TypeError("agent-loop resource grant authority is incomplete")


BrokerCommand: TypeAlias = (
    PublishResource
    | PublishPathResource
    | ResourceVersionRef
    | ResourceQuery
    | PrepareResourceDelivery
    | AuthorizeResourceRelease
    | AcknowledgeResourceDelivery
    | BindResourceAddress
    | UnbindResourceAddress
    | RelationQuery
)

BrokerResult: TypeAlias = (
    ResourceVersionRef
    | ResourceAddressBindingRef
    | ResourceHeader
    | ResourceQueryResult
    | ResourceAddressResult
    | PreparedResourceDelivery
    | AuthorizedResourceRelease
    | ResourceDeliveryReceipt
    | RelationQueryResult
)


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    protocol: Literal["resource-broker/v1"]
    context_handle: InvocationContextHandle
    command: BrokerCommand
    request_nonce: str

    def __post_init__(self) -> None:
        if (self.protocol != "resource-broker/v1"
                or not isinstance(self.context_handle, InvocationContextHandle)
                or not isinstance(self.request_nonce, str)
                or not self.request_nonce
                or not isinstance(self.command, (
                    PublishResource,
                    PublishPathResource,
                    ResourceVersionRef,
                    ResourceQuery,
                    PrepareResourceDelivery,
                    AuthorizeResourceRelease,
                    AcknowledgeResourceDelivery,
                    BindResourceAddress,
                    UnbindResourceAddress,
                    RelationQuery,
                ))):
            raise TypeError(
                "broker request requires one closed typed command")


@dataclass(frozen=True, slots=True)
class BrokerResponse:
    protocol: Literal["resource-broker/v1"]
    request_nonce: str
    result: BrokerResult

    def __post_init__(self) -> None:
        if (self.protocol != "resource-broker/v1"
                or not isinstance(self.request_nonce, str)
                or not self.request_nonce
                or not isinstance(self.result, (
                    ResourceVersionRef,
                    ResourceAddressBindingRef,
                    ResourceHeader,
                    ResourceQueryResult,
                    ResourceAddressResult,
                    PreparedResourceDelivery,
                    AuthorizedResourceRelease,
                    ResourceDeliveryReceipt,
                    RelationQueryResult,
                ))):
            raise TypeError(
                "broker response requires one closed typed result")


@dataclass(frozen=True, slots=True)
class AdoptedNetAuthority:
    """Typed, verified view of the current immutable adopted-net closure."""

    net_ref: VersionRef
    task_round_ref: VersionRef
    plan_ref: VersionRef
    team_design_root_ref: VersionRef
    llm_macro_net_ref: VersionRef
    node_refs: tuple[VersionRef, ...]
    operation_binding_refs: tuple[VersionRef, ...]
    output_binding_refs: tuple[VersionRef, ...]
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class MarkingCheckpointAuthority:
    """Typed view of the current settled checkpoint for one adopted net."""

    checkpoint_ref: VersionRef
    net_ref: VersionRef
    team_design_root_ref: VersionRef
    token_refs: tuple[VersionRef, ...]
    previous_checkpoint_ref: VersionRef | None
    settlement_delta_ref: VersionRef | None
    transition_firing_refs: tuple[VersionRef, ...]
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class CanonicalInvocationAuthority:
    """Canonical invocation DTO paired with its registry-issued public handle."""

    context: "InvocationContext"
    handle: InvocationContextHandle
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class TransitionFiringAuthority:
    """Verified exact claim envelope for one active Petri transition firing."""

    transition_firing_ref: VersionRef
    transition_id: str
    activation_ref: VersionRef | None
    task_ref: VersionRef
    task_branch_ref: VersionRef
    task_round_ref: VersionRef
    net_ref: VersionRef
    plan_ref: VersionRef
    node_ref: VersionRef
    operation_binding_ref: VersionRef
    principal_ref: VersionRef
    admission_marking_checkpoint_ref: VersionRef
    budget_witness_ref: VersionRef
    firing_admission_ref: VersionRef
    claim_marking_delta_ref: VersionRef
    logical_tau: int | float | str
    attempt_index: int
    claimed_input_refs: tuple[VersionRef, ...]
    verified_at_head: RegistryHead
    agent_ref: VersionRef | None = None

    def __post_init__(self) -> None:
        if (isinstance(self.attempt_index, bool)
                or not isinstance(self.attempt_index, int)
                or self.attempt_index <= 0):
            raise TypeError(
                "transition firing authority requires a positive attempt index")


@dataclass(frozen=True, slots=True)
class RecoveryManifestAuthority:
    """Verified startup/checkpoint manifest without its raw metadata map."""

    ref: VersionRef
    task_id: TypedId
    branch_id: str
    protocol_versions: tuple[str, ...]
    host_resource_inventory_ref: ResourceVersionRef
    writer_fencing_epoch: int
    manifest_byte_count: int
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class VerifiedResourceArtifact:
    """Authorized resource envelope verified from exact Registry references."""

    header: ResourceHeader
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class PetriInputReceiptAuthority:
    """Durable proof that a resource crossed the monitored Petri input boundary."""

    authorized_delivery_ref: VersionRef
    terminal_delivery_ref: VersionRef
    boundary_receipt_ref: VersionRef
    witness_ref: VersionRef
    exact_resource_ref: ResourceVersionRef
    positive_byte_count: int
    terminal_event_id: TypedId
    observed_read_event_id: TypedId
    consumer_evidence: str
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class PetriInputArtifact:
    """The only public byte carrier for an acknowledged ``petri_input`` read."""

    payload: bytes
    resource: VerifiedResourceArtifact
    release: AuthorizedResourceRelease
    receipt: PetriInputReceiptAuthority


@dataclass(frozen=True, slots=True)
class HistoricalPetriInputArtifact:
    """Previously acknowledged file authority with no reopened release channel."""

    payload: bytes
    resource: VerifiedResourceArtifact
    receipt: PetriInputReceiptAuthority


@dataclass(frozen=True, slots=True)
class SettledPetriInputArtifact:
    """Immutable input bytes proven by one published settled firing."""

    payload: bytes
    resource: VerifiedResourceArtifact
    receipt: PetriInputReceiptAuthority
    source_invocation_ref: VersionRef
    source_firing_ref: VersionRef
    operation_start_event_id: TypedId

    def __post_init__(self) -> None:
        if (not isinstance(self.payload, bytes)
                or not isinstance(self.resource, VerifiedResourceArtifact)
                or not isinstance(self.receipt, PetriInputReceiptAuthority)
                or not isinstance(self.source_invocation_ref, VersionRef)
                or self.source_invocation_ref.entity_type != "invocation/v1"
                or not isinstance(self.source_firing_ref, VersionRef)
                or self.source_firing_ref.entity_type
                != "transition_firing/v1"
                or not isinstance(self.operation_start_event_id, TypedId)
                or self.operation_start_event_id.kind != "event"):
            raise TypeError(
                "settled Petri input requires exact immutable source authority")
        if (self.receipt.exact_resource_ref
                != self.resource.header.ref
                or self.receipt.positive_byte_count != len(self.payload)):
            raise ValueError(
                "settled Petri input bytes differ from acknowledged authority")


@dataclass(frozen=True, slots=True)
class NativeLaunchRegisteredArtifact:
    """Registry-owned validation view of a just-published launch document.

    This authority is intentionally not a Petri input artifact: it has no
    delivery/release/receipt and therefore cannot authorize execution.  The
    current Runner must consume the exact file through the real Petri input
    boundary before using it.
    """

    payload: bytes
    resource: VerifiedResourceArtifact


@dataclass(frozen=True, slots=True)
class StructuralGrowthRegisteredArtifact:
    """Registry-owned view of one atomically adopted connected declaration.

    Structural growth is a framework graph/marking mutation, not a business
    Petri-input read.  This authority therefore carries no fabricated delivery
    receipt; consumers must revalidate it against the current adopted pair.
    """

    payload: bytes
    resource: VerifiedResourceArtifact


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    """One immutable provider/model transport selection supplied by run policy.

    The Registry owns validation and freezing of the tuple, but deliberately
    does not own a deployment catalog.  Concrete providers and models belong in
    launcher manifest data so the generic execution substrate remains reusable.
    """

    stable_key: str
    backend: str
    outbound_model: str
    endpoint: str
    response_protocol: str
    timeout_seconds: int
    max_tokens: int
    logical_credential_ref: str
    allowed_header_names: tuple[str, ...]
    header_recipe: str
    context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        textual = (
            self.stable_key, self.backend, self.outbound_model, self.endpoint,
            self.response_protocol, self.logical_credential_ref,
            self.header_recipe,
        )
        if any(not isinstance(value, str) or not value
               or value != value.strip() or len(value) > 2048
               for value in textual):
            raise ValueError("provider target contains noncanonical text")
        if "/" not in self.stable_key or not self.endpoint.startswith("https://"):
            raise ValueError("provider target key or HTTPS endpoint is invalid")
        if "/v" not in self.response_protocol:
            raise ValueError("provider target response protocol is not versioned")
        if (isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, int)
                or not 1 <= self.timeout_seconds <= 3600
                or isinstance(self.max_tokens, bool)
                or not isinstance(self.max_tokens, int)
                or self.max_tokens < 1
                or (self.context_window_tokens is not None
                    and (isinstance(self.context_window_tokens, bool)
                         or not isinstance(self.context_window_tokens, int)
                         or self.context_window_tokens < 1))):
            raise ValueError("provider target transport limits are invalid")
        if (not isinstance(self.allowed_header_names, tuple)
                or not self.allowed_header_names
                or self.allowed_header_names
                != tuple(sorted(set(self.allowed_header_names)))):
            raise ValueError(
                "provider target header names must be nonempty sorted unique text")
        if any(not isinstance(name, str) or not name or len(name) > 128
               for name in self.allowed_header_names):
            raise ValueError("provider target contains an invalid header name")


def provider_model_condition(outbound_model: str) -> str:
    """Preserve the user-configured outbound model as the exact condition."""

    if not isinstance(outbound_model, str) or not outbound_model:
        raise ValueError("provider outbound model is empty")
    return outbound_model


@dataclass(frozen=True, slots=True)
class ProviderPolicyIdentity:
    """Run-policy identity for one exact provider configuration resource."""

    llm_input_target_ref: ResourceVersionRef
    policy_entry_id: str

    def __post_init__(self) -> None:
        if (not isinstance(self.llm_input_target_ref, ResourceVersionRef)
                or not isinstance(self.policy_entry_id, str)
                or not self.policy_entry_id
                or self.policy_entry_id != self.policy_entry_id.strip()
                or len(self.policy_entry_id) > 128
                or any(not (character.isalnum() or character in "._-/")
                       for character in self.policy_entry_id)):
            raise ValueError("provider policy identity is not canonical")


def provider_backend_config_document(
        entry: ProviderCatalogEntry, *,
        credential: ProviderCredentialBinding,
) -> dict[str, object]:
    """Freeze one launcher-owned target into the Registry backend resource."""
    from .credentials import (
        credential_binding_document,
        validate_credential_binding,
    )
    if not isinstance(entry, ProviderCatalogEntry):
        raise TypeError("provider backend config requires a typed target")
    validate_credential_binding(
        credential,
        logical_ref=entry.logical_credential_ref,
        allowed_header_names=entry.allowed_header_names,
        header_recipe=entry.header_recipe,
    )
    document = {
        "catalog_key": entry.stable_key,
        "backend": entry.backend,
        "model": entry.outbound_model,
        "transport_kind": "http",
        "response_protocol": entry.response_protocol,
        "endpoint": entry.endpoint,
        "timeout_seconds": entry.timeout_seconds,
        "max_tokens": entry.max_tokens,
        "credential": credential_binding_document(credential),
    }
    if entry.context_window_tokens is not None:
        document["context_window_tokens"] = entry.context_window_tokens
    return document


def decode_provider_backend_config_document(
        payload: bytes) -> Mapping[str, object]:
    """Decode one canonical config without duplicate-field elision.

    A permissive JSON decode could discard an earlier duplicate field while
    leaving its bytes (including credential material) in the persisted
    resource.  The v1 config is therefore accepted only as the exact canonical
    encoding of one duplicate-free object.
    """
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("provider backend config must be nonempty bytes")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("provider backend config repeats a JSON field")
            result[name] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "provider backend config is not canonical UTF-8 JSON") from exc
    if (not isinstance(decoded, dict)
            or canonical_json(decoded) != payload):
        raise ValueError(
            "provider backend config is not one canonical JSON object")
    return decoded


def validate_provider_request_payload(
        payload: bytes, *, expected_model: str,
        expected_max_tokens: int) -> Mapping[str, object]:
    """Validate one canonical provider body against its frozen backend limits."""
    if (not isinstance(expected_model, str) or not expected_model
            or expected_model != expected_model.strip()):
        raise ValueError("provider request expected model is not canonical")
    if (isinstance(expected_max_tokens, bool)
            or not isinstance(expected_max_tokens, int)
            or expected_max_tokens < 1):
        raise ValueError(
            "provider request expected max_tokens is not a positive integer")
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("provider request must be nonempty bytes")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("provider request repeats a JSON field")
            result[name] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("provider request is not canonical UTF-8 JSON") from exc
    if (not isinstance(decoded, dict)
            or canonical_json(decoded) != payload
            or not {"model", "messages", "max_tokens"}.issubset(decoded)
            or not set(decoded).issubset(
                {"model", "messages", "max_tokens", "tools", "tool_choice"})
            or decoded.get("model") != expected_model
            or decoded.get("max_tokens") != expected_max_tokens
            or isinstance(decoded.get("max_tokens"), bool)
            or not isinstance(decoded.get("max_tokens"), int)
            or int(decoded["max_tokens"]) < 1):
        raise ValueError(
            "provider request is not the exact canonical catalog model body")
    messages = decoded.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("provider request requires nonempty messages")
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else None
        allowed_fields = {
            "role", "content", "tool_calls", "tool_call_id",
        }
        if role == "assistant":
            allowed_fields.add("reasoning_content")
        if (not isinstance(message, dict)
                or role not in {
                    "system", "user", "assistant", "tool"}
                or not isinstance(message.get("content"), str)
                or (role != "assistant"
                    and not message["content"].strip())
                or not set(message).issubset(allowed_fields)
                or ("reasoning_content" in message
                    and not isinstance(message["reasoning_content"], str))):
            raise ValueError("provider request contains an invalid message")
    tools = decoded.get("tools")
    if tools is not None:
        if (not isinstance(tools, list) or not tools
                or decoded.get("tool_choice") != "auto"):
            raise ValueError("provider request tool selection is invalid")
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if (not isinstance(tool, dict)
                    or set(tool) != {"type", "function"}
                    or tool["type"] != "function"
                    or not isinstance(function, dict)
                    or set(function) != {"name", "description", "parameters"}
                    or not isinstance(function["name"], str)
                    or not isinstance(function["description"], str)
                    or not isinstance(function["parameters"], dict)
                    or function["parameters"].get("type") != "object"):
                raise ValueError("provider request contains an invalid tool")
    return decoded


def validate_canonical_provider_response_payload(
        payload: bytes, *, backend: str, model: str,
        response_protocol: str) -> Mapping[str, object]:
    """Accept only the normalized closed response shape persisted by Registry."""
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("canonical provider response must be nonempty bytes")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("canonical provider response repeats a JSON field")
            result[name] = value
        return result

    try:
        decoded = json.loads(
            payload.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "canonical provider response is not canonical UTF-8 JSON") from exc
    required = {
        "protocol", "response_protocol", "backend", "model", "response_id",
        "content", "finish_reason", "usage",
    }
    usage = decoded.get("usage") if isinstance(decoded, dict) else None
    if (not isinstance(decoded, dict)
            or set(decoded) != required
            or canonical_json(decoded) != payload
            or decoded.get("protocol")
            != "registry_normalized_provider_response/v1"
            or decoded.get("response_protocol") != response_protocol
            or decoded.get("backend") != backend
            or decoded.get("model") != model
            or (decoded.get("response_id") is not None
                and (not isinstance(decoded.get("response_id"), str)
                     or not decoded["response_id"]))
            or not isinstance(decoded.get("content"), str)
            or not str(decoded["content"]).strip()
            or decoded.get("finish_reason")
            not in {None, "stop", "length", "content_filter"}
            or not isinstance(usage, dict)
            or set(usage) != {
                "input_tokens", "output_tokens", "total_tokens"}):
        raise ValueError("provider response is not the closed normalized shape")
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage[name]
        if (value is not None
                and (isinstance(value, bool)
                     or not isinstance(value, int) or value < 0)):
            raise ValueError("provider response token counts are invalid")
    return decoded


@dataclass(frozen=True, slots=True)
class ProviderResponseProtocolAuthority:
    """Catalog membership for one bounded provider response protocol id."""

    protocol_id: str
    llm_input_target_ref: ResourceVersionRef

    def __post_init__(self) -> None:
        _require_protocol_identity(
            "provider response protocol authority", self.protocol_id)
        if not isinstance(self.llm_input_target_ref, ResourceVersionRef):
            raise TypeError(
                "provider response protocol authority requires exact config ref")


@dataclass(frozen=True, slots=True)
class ProviderBackendConfig:
    """Closed provider configuration decoded from one registered file."""

    resource_ref: ResourceVersionRef
    catalog_key: str
    backend: str
    model: str
    transport_kind: Literal["http"]
    response_protocol: str
    endpoint: str
    timeout_seconds: int
    max_tokens: int
    credential: ProviderCredentialBinding
    byte_count: int
    context_window_tokens: int | None = None

    @property
    def response_protocol_authority(self) -> ProviderResponseProtocolAuthority:
        return ProviderResponseProtocolAuthority(
            protocol_id=self.response_protocol,
            llm_input_target_ref=self.resource_ref,
        )

    def __post_init__(self) -> None:
        from .credentials import validate_credential_binding

        if (not isinstance(self.resource_ref, ResourceVersionRef)
                or any(not isinstance(value, str) or not value
                       or value != value.strip() for value in (
                           self.catalog_key, self.backend, self.model,
                           self.response_protocol, self.endpoint))
                or "/" not in self.catalog_key
                or self.transport_kind != "http"
                or "/v" not in self.response_protocol
                or not self.endpoint.startswith("https://")
                or isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, int)
                or not 1 <= self.timeout_seconds <= 3600
                or isinstance(self.max_tokens, bool)
                or not isinstance(self.max_tokens, int)
                or self.max_tokens < 1
                or (self.context_window_tokens is not None
                    and (isinstance(self.context_window_tokens, bool)
                         or not isinstance(self.context_window_tokens, int)
                         or self.context_window_tokens < 1))
                or isinstance(self.byte_count, bool)
                or not isinstance(self.byte_count, int)
                or self.byte_count < 1):
            raise ValueError("provider backend config is not one exact target")
        validate_credential_binding(
            self.credential,
            logical_ref=self.credential.logical_ref,
            allowed_header_names=self.credential.allowed_header_names,
            header_recipe=self.credential.header_recipe,
        )


@dataclass(frozen=True, slots=True)
class ProviderPromptDeliveryAuthority:
    """Exact acknowledged metadata-only provider-request recipe delivery."""

    canonical_invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    request_resource_ref: ResourceVersionRef
    terminal_delivery_ref: VersionRef
    authorized_delivery_ref: VersionRef
    boundary_receipt_ref: VersionRef
    witness_ref: VersionRef
    positive_byte_count: int
    terminal_event_id: TypedId
    observed_read_event_id: TypedId
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class ProviderCallAuthority:
    """Registry-minted logical call bound to one exact request resource."""

    llm_call_ref: VersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    request_resource_ref: ResourceVersionRef
    prompt_delivery: ProviderPromptDeliveryAuthority
    tool_turn_sequence: int
    previous_call_ref: VersionRef | None
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class ProviderAttemptAuthority:
    """Reserved provider attempt with no caller-supplied backend scalars."""

    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef
    invocation_ref: VersionRef
    operation_binding_ref: VersionRef
    backend_config: ProviderBackendConfig
    request_resource_ref: ResourceVersionRef
    prompt_delivery: ProviderPromptDeliveryAuthority
    budget_witness_ref: VersionRef
    prior_attempt_ref: VersionRef | None
    reservation_class: str
    finalization_scope: str | None
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class ProviderAttemptClosedBeforeDispatchAuthority:
    """Atomic terminal closure of one exact still-reserved attempt and call."""

    attempt: ProviderAttemptAuthority | ProviderAttemptV2Authority
    closed_reason: Literal[
        "credential_binding_unavailable",
        "credential_worker_unavailable",
        "pre_dispatch_runtime_unavailable",
    ]
    attempt_closed_event_id: TypedId
    call_failed_event_id: TypedId
    dispatch_count: Literal[0]
    permit_count: Literal[0]
    request_count: Literal[0]
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(
                    self.attempt,
                    (ProviderAttemptAuthority, ProviderAttemptV2Authority))
                or self.closed_reason not in {
                    "credential_binding_unavailable",
                    "credential_worker_unavailable",
                    "pre_dispatch_runtime_unavailable",
                }
                or not isinstance(self.attempt_closed_event_id, TypedId)
                or self.attempt_closed_event_id.kind != "event"
                or not isinstance(self.call_failed_event_id, TypedId)
                or self.call_failed_event_id.kind != "event"
                or self.dispatch_count != 0 or self.permit_count != 0
                or self.request_count != 0
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError("pre-dispatch provider closure is incomplete")


@dataclass(frozen=True, slots=True)
class ProviderPayloadMaterializationReceipt:
    """Durable exact-reference receipt for transient provider request bytes."""

    provider_payload_materialization_receipt_ref: VersionRef
    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef
    request_recipe_ref: ResourceVersionRef
    terminal_delivery_ref: VersionRef
    byte_count: int
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "provider payload materialization receipt",
            self.provider_payload_materialization_receipt_ref,
            entity_type="provider_payload_materialization_receipt/v1",
            entity_id_kind="provider_payload_materialization_receipt",
            version_id_kind="provider_payload_materialization_receipt_version",
        )
        _require_exact_version_ref(
            "provider payload materialization attempt", self.provider_attempt_ref,
            entity_type="provider_attempt_spec/v1",
            entity_id_kind="provider_attempt",
            version_id_kind="provider_attempt_version",
        )
        if (not isinstance(self.llm_call_ref, VersionRef)
                or self.llm_call_ref.entity_type not in {
                "llm_call_spec/v1", "llm_call_spec/v2",
                "llm_call_spec/v3"}):
            raise TypeError(
                "provider payload materialization call is not registered")
        _require_exact_version_ref(
            "provider payload materialization call", self.llm_call_ref,
            entity_type=self.llm_call_ref.entity_type,
            entity_id_kind="llm_call", version_id_kind="llm_call_version")
        if not isinstance(self.request_recipe_ref, ResourceVersionRef):
            raise TypeError(
                "provider payload materialization requires an exact recipe ref")
        _require_exact_version_ref(
            "provider payload materialization delivery", self.terminal_delivery_ref,
            entity_type="resource_delivery/v1",
            entity_id_kind="resource_delivery",
            version_id_kind="resource_delivery_version",
        )
        if (isinstance(self.byte_count, bool)
                or not isinstance(self.byte_count, int)
                or self.byte_count <= 0
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError(
                "provider payload materialization receipt is incomplete")


@dataclass(frozen=True, slots=True)
class ProviderRequestMaterialization:
    """Process-local wire bytes paired with their metadata-only receipt."""

    receipt: ProviderPayloadMaterializationReceipt
    request_payload: bytes

    def __post_init__(self) -> None:
        if (not isinstance(self.receipt, ProviderPayloadMaterializationReceipt)
                or not isinstance(self.request_payload, bytes)
                or not self.request_payload
                or len(self.request_payload) != self.receipt.byte_count):
            raise TypeError(
                "provider request materialization differs from its receipt")


@dataclass(frozen=True, slots=True)
class ProviderDispatchAuthority:
    """One-shot metadata-only dispatch authority for an exact materialization."""

    attempt: ProviderAttemptAuthority | ProviderAttemptV2Authority
    materialization_receipt: ProviderPayloadMaterializationReceipt
    response_protocol: str
    response_protocol_authority: ProviderResponseProtocolAuthority
    dispatch_event_id: TypedId
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(
                    self.attempt,
                    (ProviderAttemptAuthority, ProviderAttemptV2Authority))
                or not isinstance(
                    self.response_protocol_authority,
                    ProviderResponseProtocolAuthority)
                or self.response_protocol != (
                    self.response_protocol_authority.protocol_id)
                or self.response_protocol_authority.llm_input_target_ref
                != self.attempt.backend_config.resource_ref
                or self.response_protocol
                != self.attempt.backend_config.response_protocol):
            raise TypeError(
                "provider dispatch protocol lacks registered catalog authority")
        if (not isinstance(
                    self.materialization_receipt,
                    ProviderPayloadMaterializationReceipt)
                or not isinstance(self.dispatch_event_id, TypedId)
                or self.dispatch_event_id.kind != "event"
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError("provider dispatch authority is incomplete")
        if isinstance(self.attempt, ProviderAttemptAuthority):
            call_ref = self.attempt.llm_call_ref
            request_recipe_ref = self.attempt.request_resource_ref
            terminal_delivery_ref = (
                self.attempt.prompt_delivery.terminal_delivery_ref)
        else:
            call_ref = self.attempt.call.ref
            request_recipe_ref = self.attempt.call.request_resource_ref
            terminal_delivery_ref = self.attempt.call.terminal_delivery_ref
        receipt = self.materialization_receipt
        if (receipt.provider_attempt_ref != self.attempt.provider_attempt_ref
                or receipt.llm_call_ref != call_ref
                or receipt.request_recipe_ref != request_recipe_ref
                or receipt.terminal_delivery_ref != terminal_delivery_ref):
            raise TypeError(
                "provider dispatch materialization names another attempt")


@dataclass(frozen=True, slots=True)
class MaterializedProviderDispatchAuthority:
    """Transient wire bytes paired with a metadata-only dispatch authority."""

    dispatch: ProviderDispatchAuthority
    request_payload: bytes

    def __post_init__(self) -> None:
        if (not isinstance(self.dispatch, ProviderDispatchAuthority)
                or not isinstance(self.request_payload, bytes)
                or not self.request_payload):
            raise TypeError("materialized provider dispatch is incomplete")
        receipt = self.dispatch.materialization_receipt
        if len(self.request_payload) != receipt.byte_count:
            raise ValueError(
                "materialized provider dispatch differs from its receipt")


@dataclass(frozen=True, slots=True)
class ProviderDispatchStartedNotice:
    """Secret-free projection of one durable dispatch-start fact."""

    task_ref: VersionRef
    invocation_ref: VersionRef
    operation_execution_lease_ref: VersionRef
    operation_start_event_id: TypedId
    provider_attempt_ref: VersionRef
    llm_call_ref: VersionRef
    dispatch_event_id: TypedId
    provider_payload_materialization_receipt_ref: VersionRef
    request_payload_byte_count: int
    response_protocol: str
    response_protocol_authority: ProviderResponseProtocolAuthority
    state: Literal["dispatch_started"] = "dispatch_started"

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "provider notice task", self.task_ref,
            entity_type="task/v1", entity_id_kind="task",
            version_id_kind="task_version")
        _require_exact_version_ref(
            "provider notice invocation", self.invocation_ref,
            entity_type="invocation/v1", entity_id_kind="invocation",
            version_id_kind="invocation_version")
        _require_exact_version_ref(
            "provider notice lease", self.operation_execution_lease_ref,
            entity_type="operation_execution_lease/v1",
            entity_id_kind="operation_execution_lease",
            version_id_kind="operation_execution_lease_version")
        _require_exact_version_ref(
            "provider notice attempt", self.provider_attempt_ref,
            entity_type="provider_attempt_spec/v1",
            entity_id_kind="provider_attempt",
            version_id_kind="provider_attempt_version")
        if (not isinstance(self.llm_call_ref, VersionRef)
                or self.llm_call_ref.entity_type not in {
                "llm_call_spec/v1", "llm_call_spec/v2",
                "llm_call_spec/v3"}):
            raise TypeError(
                "provider notice call requires one exact registered call version")
        _require_exact_version_ref(
            "provider notice call", self.llm_call_ref,
            entity_type=self.llm_call_ref.entity_type,
            entity_id_kind="llm_call", version_id_kind="llm_call_version")
        _require_exact_version_ref(
            "provider notice materialization receipt",
            self.provider_payload_materialization_receipt_ref,
            entity_type="provider_payload_materialization_receipt/v1",
            entity_id_kind="provider_payload_materialization_receipt",
            version_id_kind="provider_payload_materialization_receipt_version",
        )
        if (not isinstance(self.operation_start_event_id, TypedId)
                or self.operation_start_event_id.kind != "event"
                or not isinstance(self.dispatch_event_id, TypedId)
                or self.dispatch_event_id.kind != "event"
                or not isinstance(
                    self.response_protocol_authority,
                    ProviderResponseProtocolAuthority)
                or self.response_protocol_authority.protocol_id
                != self.response_protocol
                or self.state != "dispatch_started"):
            raise TypeError("provider dispatch notice lacks exact lifecycle refs")
        if (isinstance(self.request_payload_byte_count, bool)
                or not isinstance(self.request_payload_byte_count, int)
                or self.request_payload_byte_count <= 0):
            raise TypeError(
                "provider notice request byte count is not positive")
        _require_protocol_identity(
            "provider notice response protocol", self.response_protocol)


def provider_dispatch_notice_identity(
        notice: ProviderDispatchStartedNotice) -> str:
    if not isinstance(notice, ProviderDispatchStartedNotice):
        raise TypeError("dispatch observation requires a typed notice")
    return str(notice.dispatch_event_id)


def provider_dispatch_observation_identity(
        notice: ProviderDispatchStartedNotice, *,
        evidence_receipt_id: str | None = None) -> str:
    """Bind one observer acknowledgement to its durable recording evidence.

    Registry-only observers intentionally omit the two recording fields.  A
    recording-integrated observer must supply both; partial bindings are
    rejected so a caller cannot downgrade a receipt after publication.
    """
    if not isinstance(notice, ProviderDispatchStartedNotice):
        raise TypeError("dispatch observation requires a typed notice")
    if (evidence_receipt_id is not None
            and (not isinstance(evidence_receipt_id, str)
                 or re.fullmatch(
                     r"rct_[a-f0-9]{32}", evidence_receipt_id) is None)):
        raise ValueError("dispatch recording evidence receipt is malformed")
    return (evidence_receipt_id
            if evidence_receipt_id is not None
            else str(notice.dispatch_event_id))


@dataclass(frozen=True, slots=True)
class ProviderDispatchObservationReceipt:
    """Exact observer receipt consumed by the Registry permit command."""

    task_ref: VersionRef
    provider_attempt_ref: VersionRef
    dispatch_event_id: TypedId
    observation_identity: str
    evidence_receipt_id: str | None = None

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "provider observation task", self.task_ref,
            entity_type="task/v1", entity_id_kind="task",
            version_id_kind="task_version")
        _require_exact_version_ref(
            "provider observation attempt", self.provider_attempt_ref,
            entity_type="provider_attempt_spec/v1",
            entity_id_kind="provider_attempt",
            version_id_kind="provider_attempt_version")
        if (not isinstance(self.dispatch_event_id, TypedId)
                or self.dispatch_event_id.kind != "event"):
            raise TypeError("provider observation requires an exact dispatch event")
        if self.observation_identity != str(self.dispatch_event_id):
            raise TypeError("provider observation identity differs from dispatch")
        if (self.evidence_receipt_id is not None
                and (not isinstance(self.evidence_receipt_id, str)
                     or re.fullmatch(
                         r"rct_[a-f0-9]{32}", self.evidence_receipt_id) is None)):
            raise TypeError("provider observation evidence receipt is malformed")


@runtime_checkable
class ProviderLifecycleObserver(Protocol):
    """Synchronous secret-free observer required before provider submission."""

    def record_dispatch_started(
            self, notice: ProviderDispatchStartedNotice,
    ) -> ProviderDispatchObservationReceipt:
        ...

    def record_submission_permitted(
            self, notice: ProviderDispatchStartedNotice,
            permit: "ProviderSubmissionPermitAuthority",
    ) -> None:
        """Durably record the permit before the worker may submit."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderSubmissionPermitAuthority:
    """Fresh durable cross-store barrier authorizing one provider submission."""

    dispatch: ProviderDispatchAuthority
    receipt: ProviderDispatchObservationReceipt
    submission_permitted_event_id: TypedId
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.dispatch, ProviderDispatchAuthority)
                or not isinstance(
                    self.receipt, ProviderDispatchObservationReceipt)
                or self.receipt.provider_attempt_ref
                != self.dispatch.attempt.provider_attempt_ref
                or self.receipt.dispatch_event_id
                != self.dispatch.dispatch_event_id
                or not isinstance(self.submission_permitted_event_id, TypedId)
                or self.submission_permitted_event_id.kind != "event"
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError("provider permit authority lacks exact typed closure")


ProviderSubmissionClosedReason: TypeAlias = Literal[
    "lifecycle_observer_failed",
    "lifecycle_receipt_invalid",
    "submission_permit_commit_failed",
    "recording_observer_failed_after_permit",
]


@dataclass(frozen=True, slots=True)
class ProviderSubmissionNotPermittedAuthority:
    """Durable nonretryable closure proving that network remained unreachable."""

    dispatch: ProviderDispatchAuthority
    closed_reason: ProviderSubmissionClosedReason
    submission_not_permitted_event_id: TypedId
    call_failed_event_id: TypedId | None
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.dispatch, ProviderDispatchAuthority)
                or self.closed_reason not in {
                    "lifecycle_observer_failed",
                    "lifecycle_receipt_invalid",
                    "submission_permit_commit_failed",
                    "recording_observer_failed_after_permit"}
                or not isinstance(
                    self.submission_not_permitted_event_id, TypedId)
                or self.submission_not_permitted_event_id.kind != "event"
                or (self.call_failed_event_id is not None
                    and (not isinstance(self.call_failed_event_id, TypedId)
                         or self.call_failed_event_id.kind != "event"))
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError(
                "provider not-permitted authority lacks exact typed closure")


@dataclass(frozen=True, slots=True)
class ProviderSubmissionUnknownAuthority:
    """Exact possibly-submitted provider boundary awaiting reconciliation."""

    permit: ProviderSubmissionPermitAuthority
    provider_submission_unknown_ref: VersionRef
    submission_unknown_event_id: TypedId
    call_unknown_event_id: TypedId
    diagnostic_resource_ref: ResourceVersionRef
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.permit, ProviderSubmissionPermitAuthority)
                or not isinstance(
                    self.provider_submission_unknown_ref, VersionRef)
                or self.provider_submission_unknown_ref.entity_type
                != "provider_submission_unknown/v1"
                or not isinstance(self.submission_unknown_event_id, TypedId)
                or self.submission_unknown_event_id.kind != "event"
                or not isinstance(self.call_unknown_event_id, TypedId)
                or self.call_unknown_event_id.kind != "event"
                or not isinstance(
                    self.diagnostic_resource_ref, ResourceVersionRef)
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError(
                "provider submission-unknown authority lacks typed closure")


@dataclass(frozen=True, slots=True)
class ProviderResponseObservedAuthority:
    """Durable exact provider response observed before attempt completion."""

    permit: ProviderSubmissionPermitAuthority
    response_resource_ref: ResourceVersionRef
    submission_observed_event_id: TypedId
    response_byte_count: int
    finish_reason: str | None
    external_request_id: str | None
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.permit, ProviderSubmissionPermitAuthority)
                or not isinstance(
                    self.response_resource_ref, ResourceVersionRef)
                or not isinstance(self.submission_observed_event_id, TypedId)
                or self.submission_observed_event_id.kind != "event"
                or self.finish_reason
                not in {None, "stop", "length", "content_filter"}
                or self.external_request_id is not None
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError(
                "provider response-observed authority lacks typed closure")


@dataclass(frozen=True, slots=True)
class PetriContinuation:
    round: int
    source_ref: ResourceVersionRef


@dataclass(frozen=True, slots=True)
class PetriOverrideWarning:
    reason: str
    gate_transition_id: str
    gate_peer_transition_id: str
    overridden_attempt: str
    escalation_provenance: str
    gate_output_place: str


@dataclass(frozen=True, slots=True)
class PetriLeaseClaim:
    """One closed coloured-resource claim carried by an ordinary Petri token."""

    lease_identity_ref: VersionRef
    expected_resource_ref: ResourceVersionRef | None
    access_mode: str
    staging_place: str | None = None


@dataclass(frozen=True, slots=True)
class PetriTokenState:
    """One immutable Petri-token occurrence carried by a marking.

    The token is the Petri logical identity. ``token_id`` remains its intrinsic
    net-local ordinal, while ``token_ref`` is only the Registry persistence
    locator for that token fact. Mutable marking/firing facts never create a
    second Petri identity.
    """

    token_ref: VersionRef | None
    token_id: int
    place: str
    epoch: int
    producer: str | None
    consumer: str | None
    resource_ref: ResourceVersionRef | None
    work_resource_ref: ResourceVersionRef | None
    kind: str | None
    consumed_by: str | None
    override_warning: PetriOverrideWarning | None
    verdict: bool | str | None
    continuation: PetriContinuation | None
    lease_identity_ref: VersionRef | None = None
    lease_claims: tuple[PetriLeaseClaim, ...] = ()


@dataclass(frozen=True, slots=True)
class PetriTokenAuthority:
    """Registry publication of exactly one immutable token occurrence."""

    token_ref: VersionRef
    state: PetriTokenState
    published_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class AttemptCounterAuthority:
    transition_id: str
    highest_issued: int


@dataclass(frozen=True, slots=True)
class TypedMarkingSnapshot:
    epoch: int
    next_token_id: int
    attempts: tuple[AttemptCounterAuthority, ...]
    tokens: tuple[PetriTokenState, ...]


@dataclass(frozen=True, slots=True)
class ExecutableTransitionAuthority:
    binding_ref: VersionRef
    transition_id: str
    execution_kind: str
    node_ref: VersionRef
    activation_ref: VersionRef | None
    operation_binding_ref: VersionRef
    principal_ref: VersionRef
    agent_ref: VersionRef | None = None


@dataclass(frozen=True, slots=True)
class ExecutableNetAuthority:
    net_ref: VersionRef
    team_design_root_ref: VersionRef
    declaration: (
        PetriInputArtifact
        | HistoricalPetriInputArtifact
        | NativeLaunchRegisteredArtifact
        | StructuralGrowthRegisteredArtifact
    )
    declaration_resource_ref: ResourceVersionRef
    transitions: tuple[ExecutableTransitionAuthority, ...]
    verified_at_head: RegistryHead


@dataclass(frozen=True, slots=True)
class TypedMarkingAuthority:
    checkpoint_ref: VersionRef
    net_ref: VersionRef
    team_design_root_ref: VersionRef
    epoch: int
    next_token_id: int
    attempts: tuple[AttemptCounterAuthority, ...]
    token_refs: tuple[VersionRef, ...]
    tokens: tuple[PetriTokenAuthority, ...]
    previous_checkpoint_ref: VersionRef | None
    settlement_delta_ref: VersionRef | None
    transition_firing_refs: tuple[VersionRef, ...]
    verified_at_head: RegistryHead


CheckpointState: TypeAlias = Literal["goal", "runnable", "waiting", "dead"]


@dataclass(frozen=True, slots=True)
class CheckpointStateAuthority:
    """Registry-owned classification of one exact whole-net marking."""

    checkpoint_ref: VersionRef
    state: CheckpointState
    enabled_transition_ids: tuple[str, ...]
    waiting_transition_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProviderTimeoutAbandonmentCommand:
    """CAS command abandoning one timed-out, submission-unknown attempt."""

    expected_checkpoint_ref: VersionRef
    operation_fault_ref: VersionRef
    repair_authority_ref: VersionRef
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "expected checkpoint", self.expected_checkpoint_ref,
            entity_type="marking_checkpoint/v1",
            entity_id_kind="marking_checkpoint",
            version_id_kind="marking_checkpoint_version")
        _require_exact_version_ref(
            "operation fault", self.operation_fault_ref,
            entity_type="operation_fault/v1",
            entity_id_kind="operation_fault",
            version_id_kind="operation_fault_version")
        if (not isinstance(self.repair_authority_ref, VersionRef)
                or not isinstance(self.idempotency_key, str)
                or not self.idempotency_key):
            raise TypeError("provider timeout abandonment command is incomplete")


@dataclass(frozen=True, slots=True)
class ProviderTimeoutAbandonmentReceipt:
    """Exact Registry receipt for a retryable timeout abandonment."""

    operation_fault_ref: VersionRef
    provider_attempt_ref: VersionRef
    proof_resource_ref: ResourceVersionRef
    terminal_event_id: TypedId


@dataclass(frozen=True, slots=True)
class CheckpointRepairReplacement:
    """Operator-supplied bytes replacing one exact held document resource."""

    source_ref: ResourceVersionRef
    payload: bytes

    def __post_init__(self) -> None:
        if (not isinstance(self.source_ref, ResourceVersionRef)
                or not isinstance(self.payload, bytes)):
            raise TypeError(
                "checkpoint repair replacement requires exact resource bytes")


@dataclass(frozen=True, slots=True)
class CheckpointSharedReadRepair:
    """Restore one exact private or consumed read token to shared addressing."""

    source_token_ref: VersionRef
    expected_consumer_id: str

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "shared-read source token", self.source_token_ref,
            entity_type="petri_token/v1",
            entity_id_kind="petri_token",
            version_id_kind="petri_token_version")
        if (not isinstance(self.expected_consumer_id, str)
                or not self.expected_consumer_id):
            raise TypeError(
                "shared-read repair requires one exact declared read consumer")


@dataclass(frozen=True, slots=True)
class CheckpointMechanicalReceiptRepair:
    """Rebuild one exact live team-settlement receipt from Registry facts."""

    source_token_ref: VersionRef

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "mechanical receipt source token", self.source_token_ref,
            entity_type="petri_token/v1",
            entity_id_kind="petri_token",
            version_id_kind="petri_token_version")


@dataclass(frozen=True, slots=True)
class CheckpointFinalizationFeedbackRepair:
    """Append the omitted epoch rewrite after one settled feedback route."""

    source_route_firing_ref: VersionRef

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "finalization-feedback source firing",
            self.source_route_firing_ref,
            entity_type="transition_firing/v1",
            entity_id_kind="transition_firing",
            version_id_kind="transition_firing_version")


@dataclass(frozen=True, slots=True)
class CheckpointA2CIndicatorReturnRepair:
    """Return the missing contentless indicator for one settled A2C cap route."""

    source_route_firing_ref: VersionRef

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "A2C indicator-return source firing",
            self.source_route_firing_ref,
            entity_type="transition_firing/v1",
            entity_id_kind="transition_firing",
            version_id_kind="transition_firing_version")


@dataclass(frozen=True, slots=True)
class CheckpointCurrentNetPromptAuthorityRepair:
    """Re-adopt one current net with the prompt refs carried by its cut."""

    source_net_ref: VersionRef

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "current-net prompt-authority source net",
            self.source_net_ref,
            entity_type="net_instance/v1",
            entity_id_kind="net_instance",
            version_id_kind="net_instance_version")


@dataclass(frozen=True, slots=True)
class CheckpointRepairCommand:
    """CAS command for one append-only checkpoint repair mode."""

    expected_checkpoint_ref: VersionRef
    operation_fault_ref: VersionRef | None
    repair_authority_ref: VersionRef
    replacements: tuple[CheckpointRepairReplacement, ...] = ()
    shared_read_repairs: tuple[CheckpointSharedReadRepair, ...] = ()
    mechanical_receipt_repair: CheckpointMechanicalReceiptRepair | None = None
    finalization_feedback_repair: CheckpointFinalizationFeedbackRepair | None = None
    a2c_indicator_return_repair: CheckpointA2CIndicatorReturnRepair | None = None
    current_net_prompt_authority_repair: (
        CheckpointCurrentNetPromptAuthorityRepair | None) = None
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        _require_exact_version_ref(
            "expected checkpoint", self.expected_checkpoint_ref,
            entity_type="marking_checkpoint/v1",
            entity_id_kind="marking_checkpoint",
            version_id_kind="marking_checkpoint_version")
        if self.operation_fault_ref is not None:
            _require_exact_version_ref(
                "operation fault", self.operation_fault_ref,
                entity_type="operation_fault/v1",
                entity_id_kind="operation_fault",
                version_id_kind="operation_fault_version")
        fault_mode = self.operation_fault_ref is not None
        address_mode = bool(self.shared_read_repairs)
        mechanical_receipt_mode = self.mechanical_receipt_repair is not None
        finalization_feedback_mode = (
            self.finalization_feedback_repair is not None)
        a2c_indicator_return_mode = (
            self.a2c_indicator_return_repair is not None)
        current_net_prompt_authority_mode = (
            self.current_net_prompt_authority_repair is not None)
        if (not isinstance(self.repair_authority_ref, VersionRef)
                or not isinstance(self.replacements, tuple)
                or any(not isinstance(item, CheckpointRepairReplacement)
                       for item in self.replacements)
                or len({item.source_ref for item in self.replacements})
                != len(self.replacements)
                or not isinstance(self.shared_read_repairs, tuple)
                or any(not isinstance(item, CheckpointSharedReadRepair)
                       for item in self.shared_read_repairs)
                or len({item.source_token_ref
                        for item in self.shared_read_repairs})
                != len(self.shared_read_repairs)
                or (self.mechanical_receipt_repair is not None
                    and not isinstance(
                        self.mechanical_receipt_repair,
                        CheckpointMechanicalReceiptRepair))
                or (self.finalization_feedback_repair is not None
                    and not isinstance(
                        self.finalization_feedback_repair,
                        CheckpointFinalizationFeedbackRepair))
                or (self.a2c_indicator_return_repair is not None
                    and not isinstance(
                        self.a2c_indicator_return_repair,
                        CheckpointA2CIndicatorReturnRepair))
                or (self.current_net_prompt_authority_repair is not None
                    and not isinstance(
                        self.current_net_prompt_authority_repair,
                        CheckpointCurrentNetPromptAuthorityRepair))
                or sum((fault_mode, address_mode, mechanical_receipt_mode,
                        finalization_feedback_mode,
                        a2c_indicator_return_mode,
                        current_net_prompt_authority_mode)) != 1
                or ((address_mode or mechanical_receipt_mode
                     or finalization_feedback_mode
                     or a2c_indicator_return_mode
                     or current_net_prompt_authority_mode)
                    and self.replacements)
                or not isinstance(self.idempotency_key, str)
                or not self.idempotency_key):
            raise TypeError("checkpoint repair command is incomplete")


@dataclass(frozen=True, slots=True)
class CheckpointRepairReceipt:
    """Exact Registry receipt for one committed normal successor checkpoint."""

    repair_ref: VersionRef
    previous_checkpoint_ref: VersionRef
    repair_base_checkpoint_ref: VersionRef
    successor_checkpoint_ref: VersionRef
    replacement_refs: tuple[tuple[ResourceVersionRef, ResourceVersionRef], ...]
    readdressed_token_refs: tuple[tuple[VersionRef, VersionRef], ...]
    checkpoint_state: CheckpointStateAuthority
    added_indicator_token_refs: tuple[VersionRef, ...] = ()


@dataclass(frozen=True, slots=True)
class FiringSettlementAuthority:
    canonical: CanonicalInvocationAuthority
    operation_result_ref: VersionRef


@dataclass(frozen=True, slots=True)
class GrowthAdoptionAuthority:
    adopted: AdoptedNetAuthority
    executable: ExecutableNetAuthority
    marking: TypedMarkingAuthority
    bound_resource_ref: ResourceVersionRef


@dataclass(frozen=True, slots=True)
class StructuralGrowthInputAuthority:
    """Settled workflow design plus framework-compiled growth resources."""

    design_settlement: FiringSettlementAuthority
    workflow_design_ref: ResourceVersionRef
    design_index_ref: ResourceVersionRef
    member_bindings: tuple[tuple[str, ResourceVersionRef], ...]
    compiled_prompt_bindings: tuple[
        tuple[ResourceVersionRef, ResourceVersionRef], ...]
    resource_wait_policy_ref: ResourceVersionRef
    lowered_declaration_ref: ResourceVersionRef
    witness_ref: ResourceVersionRef

    def __post_init__(self) -> None:
        firing_ref = self.design_settlement.canonical.context.own_transition_firing_ref
        if (firing_ref is None
                or firing_ref.entity_type != "transition_firing/v1"
                or not isinstance(self.workflow_design_ref, ResourceVersionRef)
                or not isinstance(self.design_index_ref, ResourceVersionRef)
                or self.workflow_design_ref == self.design_index_ref
                or not isinstance(
                    self.resource_wait_policy_ref, ResourceVersionRef)
                or not isinstance(
                    self.lowered_declaration_ref, ResourceVersionRef)
                or not isinstance(self.witness_ref, ResourceVersionRef)):
            raise TypeError(
                "structural growth input requires one exact settled design closure")
        paths = tuple(path for path, _ref in self.member_bindings)
        if (not self.member_bindings
                or any(not isinstance(path, str) or not path
                       or not isinstance(ref, ResourceVersionRef)
                       for path, ref in self.member_bindings)
                or paths != tuple(sorted(paths))
                or len(paths) != len(set(paths))
                or len({ref for _path, ref in self.member_bindings})
                != len(self.member_bindings)):
            raise ValueError(
                "structural growth member bindings must be a canonical exact subset")
        prompt_sources = tuple(
            source for source, _compiled in self.compiled_prompt_bindings)
        prompt_targets = tuple(
            compiled for _source, compiled in self.compiled_prompt_bindings)
        if (not self.compiled_prompt_bindings
                or any(not isinstance(source, ResourceVersionRef)
                       or not isinstance(compiled, ResourceVersionRef)
                       or source == compiled
                       for source, compiled in self.compiled_prompt_bindings)
                or prompt_sources != tuple(sorted(
                    prompt_sources,
                    key=lambda ref: (
                        str(ref.resource_id), str(ref.resource_version_id))))
                or len(prompt_sources) != len(set(prompt_sources))
                or len(prompt_targets) != len(set(prompt_targets))):
            raise ValueError(
                "structural compiled prompts require a canonical one-to-one mapping")


@dataclass(frozen=True, slots=True)
class StructuralGrowthAdoptionAuthority:
    """Matched executable/marking authority returned by structural adoption."""

    adopted: AdoptedNetAuthority
    executable: ExecutableNetAuthority
    marking: TypedMarkingAuthority
    design_firing_ref: VersionRef
    design_index_ref: ResourceVersionRef
    lowered_declaration_ref: ResourceVersionRef
    witness_ref: ResourceVersionRef
    design_handoff_token_ref: VersionRef


@dataclass(frozen=True, slots=True)
class TimedWaitGuardObservation:
    """Registry-verified projection of one marked v5 queue-wait guard."""

    transition_id: str
    wait_place: str
    blocked_place: str
    wait_token_ref: VersionRef
    resource_wait_policy_ref: ResourceVersionRef
    queue_entered_at: str
    elapsed_seconds: float
    remaining_seconds: float | None
    expired: bool

    def __post_init__(self) -> None:
        if (not self.transition_id or not self.wait_place or not self.blocked_place
                or not isinstance(self.wait_token_ref, VersionRef)
                or self.wait_token_ref.entity_type != "petri_token/v1"
                or not isinstance(
                    self.resource_wait_policy_ref, ResourceVersionRef)
                or not self.queue_entered_at
                or self.elapsed_seconds < 0
                or (self.remaining_seconds is not None
                    and self.remaining_seconds < 0)
                or self.expired != (
                    self.remaining_seconds is not None
                    and self.remaining_seconds == 0)):
            raise ValueError("timed wait observation is malformed")


@dataclass(frozen=True, slots=True)
class TimedWaitGuardEvaluation:
    """Content-blind timed enabling evidence for one exact checkpoint view.

    This DTO is process-local scheduling evidence, not persisted Registry state.
    Binding the observation to the executable net, checkpoint, and marking
    epoch prevents an evaluation from being reused after settlement,
    reconstruction, or adoption advances the current Petri view.
    """

    net_ref: VersionRef
    marking_checkpoint_ref: VersionRef
    marking_epoch: int
    observed_at: str
    waiting: tuple[TimedWaitGuardObservation, ...]
    next_wake_seconds: float | None

    def __post_init__(self) -> None:
        transition_ids = tuple(item.transition_id for item in self.waiting)
        if (not isinstance(self.net_ref, VersionRef)
                or self.net_ref.entity_type != "net_instance/v1"
                or not isinstance(self.marking_checkpoint_ref, VersionRef)
                or self.marking_checkpoint_ref.entity_type
                != "marking_checkpoint/v1"
                or not isinstance(self.marking_epoch, int)
                or isinstance(self.marking_epoch, bool)
                or self.marking_epoch < 0
                or not self.observed_at
                or transition_ids != tuple(sorted(transition_ids))
                or len(transition_ids) != len(set(transition_ids))
                or (self.next_wake_seconds is not None
                    and self.next_wake_seconds < 0)):
            raise ValueError("timed wait evaluation is not canonical")


@dataclass(frozen=True, slots=True)
class PendingGrowthCandidateAuthority:
    producer_invocation_ref: VersionRef
    producer_firing_ref: VersionRef
    authority_decision_ref: VersionRef
    proposal_ref: ResourceVersionRef
    proposal_token_ref: VersionRef


@dataclass(frozen=True, slots=True)
class GrowthRecoveredTransitionMapping:
    superseded_invocation_ref: VersionRef
    superseded_firing_ref: VersionRef
    superseded_operation_execution_lease_ref: VersionRef
    transition_id: str
    recovered_transition: ExecutableTransitionAuthority | None


@dataclass(frozen=True, slots=True)
class GrowthRecoveryAuthority:
    growth: GrowthAdoptionAuthority
    recovered_transition_mappings: tuple[
        GrowthRecoveredTransitionMapping, ...]
