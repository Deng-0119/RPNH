"""Closed Registry authority for operation execution and produced resources.

This module contains no executable implementation locator.  An operation is
identified by immutable Registry objects, a non-import-shaped semantic id and
explicit HOST-registered implementation identity. Input bytes arrive only as acknowledged
live, settled, or already-acknowledged historical ``PetriInputArtifact`` files; output
bytes must already be immutable verified resources before this boundary will
close them over an operation invocation.  Historical input never reopens a
release channel.

The private repository protocol is a persistence seam for ``RegistryFacade``.
It is deliberately not an operation dispatcher or a caller supplied callback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol, Sequence

from .identities import TypedId
from .models import VersionRef
from .schema_catalog import _SCHEMA_ID as _CONTENT_SCHEMA_ID
from ._operation import registration as _operation_registration
from ._operation.registration import (
    _LEXICAL_ID,
    _require_executor_key,
    _require_lexical_id,
    _require_registered_tool,
    _validate_operation_contract,
    OperationAuthorityError,
    RegisteredOperationPortContract,
    bind_operation_registration,
    register_operation_contract,
    registered_operation_contract,
    registered_operation_host_protocols,
    registered_operation_executor,
    registered_operation_ids,
    registered_operation_port_contract,
    registered_operation_transport,
)
from .resources import (
    CanonicalInvocationAuthority,
    ExecutableTransitionAuthority,
    HistoricalPetriInputArtifact,
    PetriInputArtifact,
    PetriContinuation,
    PetriLeaseClaim,
    ProviderAttemptAuthority,
    ProviderAttemptV2Authority,
    RegistryHead,
    ResourceVersionRef,
    SettledPetriInputArtifact,
    TransitionFiringAuthority,
    VerifiedResourceArtifact,
)

if TYPE_CHECKING:
    from .resources import (
        ProviderSubmissionUnknownAuthority,
    )


FaultBoundary = Literal[
    "provider", "tool", "process", "petri_input",
    "publication", "delivery", "cancellation",
]
FaultCause = Literal[
    "not_submitted", "submitted_outcome_unknown", "execution_failed",
    "schema_mismatch", "digest_mismatch", "stale_authority", "cancelled",
    "capacity_unavailable",
]
FaultCertainty = Literal["proven", "unknown_requires_reconciliation"]
OperationFaultObservationKind = Literal[
    "retryable_provider_failure", "terminal_provider_failure",
    "provider_cancellation_after_submission", "protocol_invalidity",
    "resource_limit_exhaustion", "submission_unknown", "outcome_unknown",
    "tool_error", "framework_fault",
]
OperationExecutionBlockKind = Literal[
    "llm_retry_wait", "framework_repair", "submission_reconciliation",
    "llm_repair",
]

_FAULT_BOUNDARIES = frozenset(FaultBoundary.__args__)
_FAULT_CAUSES = frozenset(FaultCause.__args__)
_FAULT_CERTAINTIES = frozenset(FaultCertainty.__args__)
_FAULT_OBSERVATION_KINDS = frozenset(OperationFaultObservationKind.__args__)
_OPERATION_EXECUTION_BLOCK_KINDS = frozenset(
    OperationExecutionBlockKind.__args__)
# Current graph lowering encodes the exact declared node/port identity into
# framework-owned slot handles.  The producer contract is lexical shape only;
# imposing a shorter local limit here rejects handles produced by the current
# loader before operation input binding can consume their Registry authority.
_PROJECTION_PATH = re.compile(
    r"^(?:|/[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*)$")


def __getattr__(name: str) -> Any:
    """Forward the former private registration-state read without copying it."""
    if name == "_OPERATION_REGISTRATION":
        return _operation_registration._OPERATION_REGISTRATION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _canonical_operation_fault_refs(
        refs: Sequence[VersionRef], *, label: str,
) -> tuple[VersionRef, ...]:
    """Return one closed Petri-token tuple or reject noncanonical input.

    Fault partition authority, the typed command, persistent material, and
    replay verification all share this exact ordering.  Keeping the tuple
    closed here prevents the publisher and consumer from independently
    choosing different UUID orderings.
    """
    values = tuple(refs)
    if (any(not isinstance(ref, VersionRef)
            or ref.entity_type != "petri_token/v1" for ref in values)
            or len(set(values)) != len(values)):
        raise OperationAuthorityError(
            f"operation fault {label} refs are not exact unique Petri tokens")
    ordered = tuple(sorted(
        values,
        key=lambda ref: (
            ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    if values != ordered:
        raise OperationAuthorityError(
            f"operation fault {label} refs are not canonically ordered")
    return values


def _matches_operation_fault_metadata(
        metadata: Mapping[str, Any],
        expected: tuple[tuple[str, object], ...],
) -> bool:
    """Compare a closed persisted projection without missing-key defaults."""
    for name, value in expected:
        if name not in metadata:
            return False
        actual = metadata[name]
        if isinstance(value, tuple):
            if not isinstance(actual, list) or tuple(actual) != value:
                return False
        elif actual != value:
            return False
    return True

def _ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _resource_ref_payload(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


def _parse_ref(value: object, *, label: str) -> VersionRef:
    if not isinstance(value, Mapping) or set(value) != {
            "entity_type", "logical_id", "version_id"}:
        raise OperationAuthorityError(f"{label} is not one exact version reference")
    try:
        ref = VersionRef(
            str(value["entity_type"]),
            TypedId.parse(str(value["logical_id"])),
            TypedId.parse(str(value["version_id"])),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise OperationAuthorityError(
            f"{label} is not one exact version reference") from exc
    if not re.fullmatch(r"[a-z][a-z0-9_]*/v[1-9][0-9]*", ref.entity_type):
        raise OperationAuthorityError(f"{label} has an invalid entity type")
    return ref


def _parse_refs(value: object, *, label: str) -> tuple[VersionRef, ...]:
    if not isinstance(value, list):
        raise OperationAuthorityError(f"{label} must be an exact reference array")
    refs = tuple(_parse_ref(item, label=f"{label}[{index}]")
                 for index, item in enumerate(value))
    if len(set(refs)) != len(refs):
        raise OperationAuthorityError(f"{label} contains duplicate exact references")
    return refs


def _parse_resource_ref(value: object, *, label: str) -> ResourceVersionRef:
    if not isinstance(value, Mapping) or set(value) != {
            "resource_id", "resource_version_id"}:
        raise OperationAuthorityError(f"{label} is not one exact resource ref")
    try:
        return ResourceVersionRef(
            TypedId.parse(str(value["resource_id"]), expected="resource"),
            TypedId.parse(
                str(value["resource_version_id"]),
                expected="resource_version"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise OperationAuthorityError(
            f"{label} is not one exact resource ref") from exc


def _same_ref_set(left: Sequence[VersionRef], right: Sequence[VersionRef]) -> bool:
    from collections import Counter
    return Counter(left) == Counter(right)


@dataclass(frozen=True, slots=True)
class RegisteredContentSchemaAuthority:
    """Registry-hydrated authority for one exact, self-contained JSON schema."""

    schema_id: str
    catalog_ref: VersionRef | None = None
    resource_ref: ResourceVersionRef | None = None

    @property
    def content_schema_ref(self) -> VersionRef | ResourceVersionRef:
        source = self.catalog_ref if self.catalog_ref is not None else self.resource_ref
        assert source is not None
        return source

    def __post_init__(self) -> None:
        if (not isinstance(self.schema_id, str)
                or _CONTENT_SCHEMA_ID.fullmatch(self.schema_id) is None):
            raise OperationAuthorityError(
                "content schema authority has a noncanonical schema id")
        if ((self.catalog_ref is None) == (self.resource_ref is None)):
            raise OperationAuthorityError(
                "content schema authority requires exactly one exact source")
        if self.catalog_ref is not None:
            if (not isinstance(self.catalog_ref, VersionRef)
                    or self.catalog_ref.entity_type
                    != "registry_type_catalog/v1"):
                raise OperationAuthorityError(
                    "catalog content schema authority has the wrong source/id")
        if self.resource_ref is not None:
            if not isinstance(self.resource_ref, ResourceVersionRef):
                raise OperationAuthorityError(
                    "resource content schema authority requires an exact resource")


@dataclass(frozen=True, slots=True)
class OperationFieldProjection:
    """One declared lossless producer-field to consumer-field projection."""

    producer_path: str
    consumer_path: str

    def __post_init__(self) -> None:
        if (not isinstance(self.producer_path, str)
                or _PROJECTION_PATH.fullmatch(self.producer_path) is None
                or not isinstance(self.consumer_path, str)
                or _PROJECTION_PATH.fullmatch(self.consumer_path) is None):
            raise OperationAuthorityError(
                "operation input projection paths must be root or closed "
                "object-field paths")


@dataclass(frozen=True, slots=True)
class OperationInputProjection:
    """Closed projection accepted from one additional producer schema."""

    producer_content_schema_ref: str
    field_projection: tuple[OperationFieldProjection, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.producer_content_schema_ref, str)
                or _CONTENT_SCHEMA_ID.fullmatch(
                    self.producer_content_schema_ref) is None):
            raise OperationAuthorityError(
                "operation input projection producer schema ref is invalid")
        if (not isinstance(self.field_projection, tuple)
                or not self.field_projection
                or any(not isinstance(item, OperationFieldProjection)
                       for item in self.field_projection)):
            raise OperationAuthorityError(
                "operation input projection must declare mapped fields")
        ordered = tuple(sorted(
            self.field_projection,
            key=lambda item: (item.consumer_path, item.producer_path)))
        if self.field_projection != ordered:
            raise OperationAuthorityError(
                "operation input field projection is not canonically ordered")
        producer_paths = tuple(
            item.producer_path for item in self.field_projection)
        consumer_paths = tuple(
            item.consumer_path for item in self.field_projection)
        if (len(set(producer_paths)) != len(producer_paths)
                or len(set(consumer_paths)) != len(consumer_paths)):
            raise OperationAuthorityError(
                "operation input field projection is duplicate or ambiguous")
        for paths in (producer_paths, consumer_paths):
            if any(
                    left != right
                    and left
                    and right.startswith(left + "/")
                    for left in paths for right in paths):
                raise OperationAuthorityError(
                    "operation input field projection paths overlap")
        if (("" in producer_paths and len(producer_paths) != 1)
                or ("" in consumer_paths and len(consumer_paths) != 1)):
            raise OperationAuthorityError(
                "operation input root projection contradicts field mappings")


@dataclass(frozen=True, slots=True)
class OperationPortAuthority:
    port_id: str
    place: str
    schema_ref: VersionRef
    content_schema: RegisteredContentSchemaAuthority
    minimum: int
    maximum: int
    input_projections: tuple[OperationInputProjection, ...] = ()
    # Explicit relation for a static lease token in the shared Petri pool.
    # This is never inferred from place names or collection ordering.
    lease_identity_ref: VersionRef | None = None

    @property
    def content_schema_ref(self) -> VersionRef | ResourceVersionRef:
        return self.content_schema.content_schema_ref

    @property
    def content_schema_id(self) -> str:
        return self.content_schema.schema_id

    def input_projection_from(
            self, producer_content_schema_ref: str,
    ) -> OperationInputProjection | None:
        """Return the sole declared producer projection, or exact fast path."""

        if producer_content_schema_ref == self.content_schema_id:
            return None
        matches = tuple(
            item for item in self.input_projections
            if item.producer_content_schema_ref
            == producer_content_schema_ref)
        if len(matches) != 1:
            raise OperationAuthorityError(
                "producer schema has no unique declared consumer projection")
        return matches[0]

    @property
    def accepted_input_schema_ids(self) -> tuple[str, ...]:
        return tuple(sorted({
            self.content_schema_id,
            *(item.producer_content_schema_ref
              for item in self.input_projections),
        }))

    def __post_init__(self) -> None:
        _require_lexical_id("operation port_id", self.port_id)
        if not isinstance(self.place, str) or not self.place:
            raise OperationAuthorityError(
                "operation port requires its exact Petri place")
        if (self.lease_identity_ref is not None
                and not isinstance(self.lease_identity_ref, VersionRef)):
            raise TypeError(
                "operation port lease identity must be one exact VersionRef")
        if not isinstance(self.schema_ref, VersionRef):
            raise TypeError("operation port requires one exact schema VersionRef")
        if not isinstance(self.content_schema, RegisteredContentSchemaAuthority):
            raise TypeError(
                "operation port requires registered content schema authority")
        if (isinstance(self.minimum, bool) or not isinstance(self.minimum, int)
                or isinstance(self.maximum, bool) or not isinstance(self.maximum, int)
                or self.minimum < 0 or self.maximum < self.minimum):
            raise OperationAuthorityError(
                "operation port cardinality must satisfy 0 <= minimum <= maximum")
        if (not isinstance(self.input_projections, tuple)
                or any(not isinstance(item, OperationInputProjection)
                       for item in self.input_projections)):
            raise OperationAuthorityError(
                "operation port input projections must be typed declarations")
        ordered = tuple(sorted(
            self.input_projections,
            key=lambda item: item.producer_content_schema_ref))
        producer_refs = tuple(
            item.producer_content_schema_ref
            for item in self.input_projections)
        if (self.input_projections != ordered
                or len(set(producer_refs)) != len(producer_refs)):
            raise OperationAuthorityError(
                "operation port input projections must be uniquely sorted by "
                "producer schema ref")
        if self.content_schema_id in producer_refs:
            raise OperationAuthorityError(
                "operation port input projection contradicts exact-schema default")


@dataclass(frozen=True, slots=True)
class OperationSpecAuthority:
    operation_spec_ref: VersionRef
    operation_id: str
    executor_key: str = field(kw_only=True)
    transport: Literal["llm", "deterministic"]
    llm_prompt_port_id: str | None
    input_ports: tuple[OperationPortAuthority, ...]
    output_ports: tuple[OperationPortAuthority, ...]
    allowed_tool_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.operation_spec_ref, VersionRef)
                or self.operation_spec_ref.entity_type != "operation_spec/v1"):
            raise OperationAuthorityError(
                "operation spec authority requires an exact operation_spec/v1 ref")
        _require_lexical_id("operation_id", self.operation_id)
        if self.transport != registered_operation_transport(self.executor_key):
            raise OperationAuthorityError(
                "operation transport differs from its source registration")
        for label, ports in (
                ("input_ports", self.input_ports),
                ("output_ports", self.output_ports)):
            if any(not isinstance(port, OperationPortAuthority) for port in ports):
                raise TypeError(f"{label} must contain typed operation ports")
            ids = tuple(port.port_id for port in ports)
            direction: Literal["input", "output"] = (
                "input" if label == "input_ports" else "output")
            contract = registered_operation_port_contract(self.executor_key, direction)
            expected_ids = (ids if contract is None else
                            tuple(port.port_id for port in contract))
            if ids != expected_ids or len(set(ids)) != len(ids):
                raise OperationAuthorityError(
                    f"{label} must have exact registered order and unique port_id")
            # Multiple logical artifacts can share a content schema. Exact
            # Petri place/port identity disambiguates the claimed subset.
            if direction == "output" and any(
                    port.input_projections for port in ports):
                raise OperationAuthorityError(
                    "operation output ports cannot declare input projections")
            if direction == "output" and any(
                    port.lease_identity_ref is not None for port in ports):
                raise OperationAuthorityError(
                    "operation output ports cannot declare lease identities")
            if direction == "input":
                lease_identity_refs = tuple(
                    port.lease_identity_ref for port in ports
                    if port.lease_identity_ref is not None)
                if len(set(lease_identity_refs)) != len(lease_identity_refs):
                    raise OperationAuthorityError(
                        "operation input lease identities must be unique")
            if contract is not None:
                if len(ports) != len(contract):
                    raise OperationAuthorityError(
                        f"{self.operation_id} {direction} ports differ from source ABI")
                for actual, expected in zip(ports, contract, strict=True):
                    if (actual.port_id != expected.port_id
                            or actual.content_schema_id
                            != expected.content_schema_id
                            or actual.minimum != expected.minimum
                            or (expected.maximum is not None
                                and actual.maximum != expected.maximum)
                            or (expected.maximum is None
                                and actual.maximum < expected.minimum)):
                        raise OperationAuthorityError(
                            f"{self.operation_id} {direction} port "
                            f"{expected.port_id!r} differs from source ABI")
        if (any(not isinstance(tool_id, str) or not tool_id
                for tool_id in self.allowed_tool_ids)
                or self.allowed_tool_ids != tuple(sorted(self.allowed_tool_ids))
                or len(set(self.allowed_tool_ids)) != len(self.allowed_tool_ids)):
            raise OperationAuthorityError(
                "allowed_tool_ids must be unique sorted registered keys")
        for tool_id in self.allowed_tool_ids:
            _require_registered_tool(tool_id)
        if self.transport == "llm":
            _require_lexical_id(
                "llm_prompt_port_id", self.llm_prompt_port_id)
            prompt_ports = [
                port for port in self.input_ports
                if port.port_id == self.llm_prompt_port_id]
            if (len(prompt_ports) != 1 or prompt_ports[0].minimum != 1
                    or prompt_ports[0].maximum != 1):
                raise OperationAuthorityError(
                    "LLM prompt must name one exact cardinality-one input port")
        elif self.llm_prompt_port_id is not None:
            raise OperationAuthorityError(
                "deterministic operation has no LLM prompt")


@dataclass(frozen=True, slots=True)
class OperationExecutionLimits:
    max_llm_attempts: int
    max_tool_turns: int

    def __post_init__(self) -> None:
        values = (self.max_llm_attempts, self.max_tool_turns)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in values):
            raise OperationAuthorityError(
                "operation execution limits must be nonnegative integers")


@dataclass(frozen=True, slots=True)
class OperationOutputPortBindingAuthority:
    port_id: str
    place: str
    place_ref: VersionRef
    output_binding_ref: VersionRef
    content_schema: RegisteredContentSchemaAuthority

    def __post_init__(self) -> None:
        _require_lexical_id("output port binding port_id", self.port_id)
        if not isinstance(self.place, str) or not self.place:
            raise OperationAuthorityError(
                "output port binding requires an explicit declared Petri place")
        if not isinstance(self.place_ref, VersionRef):
            raise TypeError("output port binding requires one exact place ref")
        if (not isinstance(self.output_binding_ref, VersionRef)
                or self.output_binding_ref.entity_type != "output_binding/v1"):
            raise OperationAuthorityError(
                "output port binding requires an exact output_binding/v1 ref")
        if not isinstance(
                self.content_schema, RegisteredContentSchemaAuthority):
            raise TypeError(
                "output port binding requires registered content schema authority")


@dataclass(frozen=True, slots=True)
class OperationBindingAuthority:
    operation_binding_ref: VersionRef
    operation_spec_ref: VersionRef
    node_ref: VersionRef
    principal_ref: VersionRef
    authority_decision_ref: VersionRef
    code_artifact_ref: VersionRef | None
    llm_input_target_ref: ResourceVersionRef | None
    workspace_binding_ref: VersionRef | None
    input_binding_refs: tuple[VersionRef, ...]
    discoverable_resource_refs: tuple[VersionRef, ...]
    readable_resource_refs: tuple[VersionRef, ...]
    input_schema_refs: tuple[VersionRef, ...]
    output_schema_refs: tuple[VersionRef, ...]
    output_port_bindings: tuple[OperationOutputPortBindingAuthority, ...]
    allowed_publication_origins: tuple[str, ...]
    limits: OperationExecutionLimits

    def __post_init__(self) -> None:
        if (not isinstance(self.operation_binding_ref, VersionRef)
                or self.operation_binding_ref.entity_type != "operation_binding/v1"):
            raise OperationAuthorityError(
                "operation binding authority requires operation_binding/v1")
        if (not isinstance(self.operation_spec_ref, VersionRef)
                or self.operation_spec_ref.entity_type != "operation_spec/v1"):
            raise OperationAuthorityError(
                "operation binding does not exact-reference operation_spec/v1")
        if (self.llm_input_target_ref is not None
                and not isinstance(self.llm_input_target_ref, ResourceVersionRef)):
            raise TypeError(
                "operation LLM target must be an exact resource or null")
        if (self.workspace_binding_ref is not None
                and (not isinstance(self.workspace_binding_ref, VersionRef)
                     or self.workspace_binding_ref.entity_type
                     != "workspace_binding/v1")):
            raise TypeError(
                "operation workspace binding must be an exact v1 ref or null")
        for label, refs in (
                ("input binding", self.input_binding_refs),
                ("discoverable resource", self.discoverable_resource_refs),
                ("readable resource", self.readable_resource_refs),
                ("input schema", self.input_schema_refs),
                ("output schema", self.output_schema_refs)):
            if any(not isinstance(ref, VersionRef) for ref in refs):
                raise TypeError(f"{label} authority contains a non-VersionRef")
            if len(set(refs)) != len(refs):
                raise OperationAuthorityError(f"{label} refs contain duplicates")
        port_ids = tuple(item.port_id for item in self.output_port_bindings)
        if (any(not isinstance(item, OperationOutputPortBindingAuthority)
                for item in self.output_port_bindings)
                or port_ids != tuple(sorted(port_ids))
                or len(set(port_ids)) != len(port_ids)):
            raise OperationAuthorityError(
                "output port bindings must be uniquely sorted by port_id")
        expected_origins = (
            ("petri_output", "workspace_write")
            if self.workspace_binding_ref is not None else
            ("petri_output",)
        )
        if self.allowed_publication_origins != expected_origins:
            raise OperationAuthorityError(
                "registered Petri operation publication origins differ from "
                "its exact workspace authority")
        if not isinstance(self.limits, OperationExecutionLimits):
            raise TypeError("operation binding requires typed execution limits")


@dataclass(frozen=True, slots=True)
class RegisteredOperationInputAuthority:
    port_id: str
    input_binding_ref: VersionRef
    claimed_token_ref: VersionRef | None
    schema_ref: VersionRef
    artifact: (
        PetriInputArtifact
        | SettledPetriInputArtifact
        | HistoricalPetriInputArtifact)
    projected_payload: bytes | None = None
    source_resource_ref: ResourceVersionRef | None = None
    substituted_content_schema_id: str | None = None

    @property
    def resource_ref(self) -> ResourceVersionRef:
        return self.artifact.resource.header.ref

    @property
    def consumer_payload(self) -> bytes:
        return (self.projected_payload
                if self.projected_payload is not None
                else self.artifact.payload)

    def __post_init__(self) -> None:
        _require_lexical_id("registered operation input port_id", self.port_id)
        if not isinstance(self.input_binding_ref, VersionRef):
            raise TypeError("operation input requires one exact input binding ref")
        if (self.claimed_token_ref is not None
                and (not isinstance(self.claimed_token_ref, VersionRef)
                     or self.claimed_token_ref.entity_type != "petri_token/v1")):
            raise OperationAuthorityError(
                "operation input claimed token must be exact petri_token/v1")
        if not isinstance(self.schema_ref, VersionRef):
            raise TypeError("operation input requires one exact schema ref")
        if not isinstance(
                self.artifact,
                (PetriInputArtifact, SettledPetriInputArtifact,
                 HistoricalPetriInputArtifact)):
            raise TypeError(
                "operation input bytes require live/settled/historical Petri input")
        artifact = self.artifact
        if (self.projected_payload is not None
                and not isinstance(self.projected_payload, bytes)):
            raise TypeError(
                "operation input projected consumer payload must be bytes")
        if (self.source_resource_ref is not None
                and (not isinstance(
                    self.source_resource_ref, ResourceVersionRef)
                     or self.source_resource_ref
                     == artifact.resource.header.ref)):
            raise OperationAuthorityError(
                "operation input substitution lacks its distinct exact source")
        if ((self.substituted_content_schema_id is not None
             and self.source_resource_ref is None)
                or (self.substituted_content_schema_id is not None
                    and (not isinstance(
                        self.substituted_content_schema_id, str)
                         or not self.substituted_content_schema_id))):
            raise OperationAuthorityError(
                "operation input substitution schema is not explicit")
        if (artifact.receipt.exact_resource_ref != artifact.resource.header.ref
                or artifact.receipt.positive_byte_count != len(artifact.payload)):
            raise OperationAuthorityError(
                "operation input lacks an acknowledged exact petri_input receipt")


@dataclass(frozen=True, slots=True)
class RegisteredOperationInputClaimAuthority:
    """One claimed token/resource selected by the exact operation port closure."""

    claimed_token_ref: VersionRef
    resource_ref: ResourceVersionRef
    port: OperationPortAuthority
    source_resource_ref: ResourceVersionRef | None = None
    substituted_content_schema_id: str | None = None

    @property
    def claimed_resource_ref(self) -> ResourceVersionRef:
        """Return the resource carried by the admitted Petri token."""

        return self.source_resource_ref or self.resource_ref

    def __post_init__(self) -> None:
        if (not isinstance(self.claimed_token_ref, VersionRef)
                or self.claimed_token_ref.entity_type != "petri_token/v1"):
            raise OperationAuthorityError(
                "planned operation input requires exact petri_token/v1")
        if not isinstance(self.resource_ref, ResourceVersionRef):
            raise OperationAuthorityError(
                "planned operation input requires one exact resource version")
        if not isinstance(self.port, OperationPortAuthority):
            raise TypeError("planned operation input requires one typed port")
        if (self.source_resource_ref is not None
                and not isinstance(self.source_resource_ref,
                                   ResourceVersionRef)):
            raise OperationAuthorityError(
                "planned operation input source requires one exact resource version")
        if ((self.substituted_content_schema_id is not None
             and self.source_resource_ref is None)
                or (self.substituted_content_schema_id is not None
                    and (not isinstance(
                        self.substituted_content_schema_id, str)
                         or not self.substituted_content_schema_id))):
            raise OperationAuthorityError(
                "planned operation input substitution schema is not explicit")


@dataclass(frozen=True, slots=True)
class RegisteredOperationInputPlanAuthority:
    """Pre-delivery proof of the semantic subset of one Petri firing claim."""

    canonical: CanonicalInvocationAuthority
    firing: TransitionFiringAuthority
    transition: ExecutableTransitionAuthority
    operation_binding: OperationBindingAuthority
    spec: OperationSpecAuthority
    claims: tuple[RegisteredOperationInputClaimAuthority, ...]
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.canonical, CanonicalInvocationAuthority)
                or not isinstance(self.firing, TransitionFiringAuthority)
                or not isinstance(self.transition, ExecutableTransitionAuthority)
                or not isinstance(self.operation_binding,
                                   OperationBindingAuthority)
                or not isinstance(self.spec, OperationSpecAuthority)
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError(
                "operation input plan requires one complete typed authority")
        if any(not isinstance(
                item, RegisteredOperationInputClaimAuthority)
               for item in self.claims):
            raise TypeError("operation input plan claims must be typed")
        if (self.canonical.context.own_transition_firing_ref
                != self.firing.transition_firing_ref
                or self.transition.transition_id != self.firing.transition_id
                or self.operation_binding.operation_binding_ref
                != self.firing.operation_binding_ref):
            raise OperationAuthorityError(
                "operation input plan differs from its invocation/firing")
        if (len({item.claimed_token_ref for item in self.claims})
                != len(self.claims)
                or any(item.claimed_token_ref
                       not in self.firing.claimed_input_refs
                       for item in self.claims)
                or any(item.port not in self.spec.input_ports
                       for item in self.claims)):
            raise OperationAuthorityError(
                "operation input plan is not an exact unique firing subset")
        by_port = {
            port.port_id: sum(
                item.port.port_id == port.port_id for item in self.claims)
            for port in self.spec.input_ports
        }
        for port in self.spec.input_ports:
            count = by_port[port.port_id]
            if count < port.minimum or count > port.maximum:
                raise OperationAuthorityError(
                    f"planned operation input port {port.port_id!r} "
                    "violates declared cardinality")


@dataclass(frozen=True, slots=True)
class PreparedRegisteredOperationInputPlanAuthority:
    """Operation input relation proven before firing admission is published."""

    transition: ExecutableTransitionAuthority
    operation_binding: OperationBindingAuthority
    spec: OperationSpecAuthority
    claims: tuple[RegisteredOperationInputClaimAuthority, ...]
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.transition, ExecutableTransitionAuthority)
                or not isinstance(
                    self.operation_binding, OperationBindingAuthority)
                or not isinstance(self.spec, OperationSpecAuthority)
                or not isinstance(self.verified_at_head, RegistryHead)
                or any(not isinstance(
                    item, RegisteredOperationInputClaimAuthority)
                       for item in self.claims)):
            raise TypeError(
                "prepared operation input plan requires typed authorities")
        if (self.transition.operation_binding_ref
                != self.operation_binding.operation_binding_ref
                or self.transition.node_ref != self.operation_binding.node_ref
                or len({item.claimed_token_ref for item in self.claims})
                != len(self.claims)
                or any(item.port not in self.spec.input_ports
                       for item in self.claims)):
            raise OperationAuthorityError(
                "prepared operation input relation is not exact")
        by_port = {
            port.port_id: sum(
                item.port.port_id == port.port_id for item in self.claims)
            for port in self.spec.input_ports
        }
        for port in self.spec.input_ports:
            count = by_port[port.port_id]
            if count < port.minimum or count > port.maximum:
                raise OperationAuthorityError(
                    f"prepared operation input port {port.port_id!r} "
                    "violates declared cardinality")


@dataclass(frozen=True, slots=True)
class ClaimedPetriInputAuthority:
    """One exact resource-backed firing claim bound to its registered port."""

    token_ref: VersionRef
    resource_ref: ResourceVersionRef
    port: OperationPortAuthority
    artifact: (
        PetriInputArtifact
        | SettledPetriInputArtifact
        | HistoricalPetriInputArtifact)
    source_resource_ref: ResourceVersionRef | None = None
    substituted_content_schema_id: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.token_ref, VersionRef)
                or self.token_ref.entity_type != "petri_token/v1"):
            raise OperationAuthorityError(
                "claimed operation input requires exact petri_token/v1")
        if not isinstance(self.port, OperationPortAuthority):
            raise TypeError("claimed operation input requires typed port authority")
        if not isinstance(self.resource_ref, ResourceVersionRef):
            raise OperationAuthorityError(
                "claimed operation input requires exact resource ref")
        if not isinstance(
                self.artifact,
                (PetriInputArtifact, SettledPetriInputArtifact,
                 HistoricalPetriInputArtifact)):
            raise TypeError(
                "claimed operation input requires registered Petri input")
        if (self.source_resource_ref is not None
                and (not isinstance(
                    self.source_resource_ref, ResourceVersionRef)
                     or self.source_resource_ref == self.resource_ref)):
            raise OperationAuthorityError(
                "claimed input substitution lacks its distinct exact source")
        if ((self.substituted_content_schema_id is not None
             and self.source_resource_ref is None)
                or (self.substituted_content_schema_id is not None
                    and (not isinstance(
                        self.substituted_content_schema_id, str)
                         or not self.substituted_content_schema_id))):
            raise OperationAuthorityError(
                "claimed input substitution schema is not explicit")
        schema_matches_port = (
            self.artifact.resource.header.content_schema_ref
            == self.port.content_schema_id)
        producer_schema_id = (
            self.artifact.resource.header.content_schema_ref)
        try:
            declared_input_projection = (
                self.port.input_projection_from(producer_schema_id)
                if isinstance(producer_schema_id, str) else None)
        except OperationAuthorityError:
            declared_input_projection = None
        projection_matches_port = (
            self.source_resource_ref is None
            and isinstance(
                declared_input_projection, OperationInputProjection))
        declared_substitution = (
            self.substituted_content_schema_id is not None
            and self.source_resource_ref is not None
            and self.artifact.resource.header.content_schema_ref
            == self.substituted_content_schema_id)
        if (self.resource_ref != self.artifact.resource.header.ref
                or self.artifact.receipt.exact_resource_ref
                != self.artifact.resource.header.ref
                or not (schema_matches_port or projection_matches_port
                        or declared_substitution)):
            raise OperationAuthorityError(
                "claimed input token/port/binding/artifact closure differs")


def canonical_claimed_petri_inputs(
        values: Sequence[ClaimedPetriInputAuthority],
) -> tuple[ClaimedPetriInputAuthority, ...]:
    if any(not isinstance(item, ClaimedPetriInputAuthority) for item in values):
        raise TypeError("claimed Petri inputs must be typed authorities")
    ordered = tuple(sorted(
        values,
        key=lambda item: (
            item.port.port_id,
            str(item.resource_ref.resource_version_id),
            str(item.token_ref.version_id),
        )))
    if len({item.token_ref for item in ordered}) != len(ordered):
        raise OperationAuthorityError(
            "claimed operation inputs contain a duplicate token")
    return ordered


@dataclass(frozen=True, slots=True)
class FaultPetriPlaceAuthority:
    place_ref: VersionRef
    subnet_template_ref: VersionRef
    role: Literal[
        "fault_pending", "original_work_hold", "capacity_return",
        "reconciliation_pending", "structural_terminal_intake",
    ]
    place: str

    def __post_init__(self) -> None:
        if (not isinstance(self.place_ref, VersionRef)
                or self.place_ref.entity_type != "fault_petri_place/v1"):
            raise OperationAuthorityError(
                "fault subnet place requires fault_petri_place/v1")
        if (not isinstance(self.subnet_template_ref, VersionRef)
                or self.subnet_template_ref.entity_type
                != "fault_subnet_template/v1"):
            raise OperationAuthorityError(
                "fault subnet place requires its exact template")
        if self.role not in {
                "fault_pending", "original_work_hold", "capacity_return",
                "reconciliation_pending", "structural_terminal_intake"}:
            raise OperationAuthorityError("fault subnet place role is not closed")
        if not isinstance(self.place, str) or not self.place:
            raise OperationAuthorityError("fault subnet place name is empty")


@dataclass(frozen=True, slots=True)
class FaultPetriTransitionAuthority:
    transition_ref: VersionRef
    subnet_template_ref: VersionRef
    role: Literal["retry", "exhaust", "reconcile"]
    input_place_refs: tuple[VersionRef, ...]
    output_place_refs: tuple[VersionRef, ...]
    accepted_verdicts: tuple[str, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.transition_ref, VersionRef)
                or self.transition_ref.entity_type
                != "fault_petri_transition/v1"):
            raise OperationAuthorityError(
                "fault subnet transition requires fault_petri_transition/v1")
        if (not isinstance(self.subnet_template_ref, VersionRef)
                or self.subnet_template_ref.entity_type
                != "fault_subnet_template/v1"):
            raise OperationAuthorityError(
                "fault subnet transition requires its exact template")
        verdicts = {
            "retry": ("retry",),
            "exhaust": ("exhaust",),
            "reconcile": ("reconcile",),
        }
        if self.role not in verdicts or self.accepted_verdicts != verdicts[self.role]:
            raise OperationAuthorityError(
                "fault subnet transition verdict guard is not canonical")
        for label, refs in (
                ("input", self.input_place_refs),
                ("output", self.output_place_refs)):
            if (not refs
                    or any(not isinstance(ref, VersionRef)
                           or ref.entity_type != "fault_petri_place/v1"
                           for ref in refs)
                    or len(set(refs)) != len(refs)):
                raise OperationAuthorityError(
                    f"fault subnet transition {label} arcs are not closed")


@dataclass(frozen=True, slots=True)
class FaultSubnetAuthority:
    subnet_template_ref: VersionRef
    places: tuple[FaultPetriPlaceAuthority, ...]
    transitions: tuple[FaultPetriTransitionAuthority, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.subnet_template_ref, VersionRef)
                or self.subnet_template_ref.entity_type
                != "fault_subnet_template/v1"):
            raise OperationAuthorityError(
                "fault subnet authority requires exact template")
        expected_place_roles = (
            "capacity_return", "fault_pending", "original_work_hold",
            "reconciliation_pending", "structural_terminal_intake",
        )
        expected_transition_roles = ("exhaust", "reconcile", "retry")
        if (tuple(item.role for item in self.places) != expected_place_roles
                or tuple(item.role for item in self.transitions)
                != expected_transition_roles
                or len({item.place_ref for item in self.places}) != 5
                or len({item.place for item in self.places}) != 5
                or len({item.transition_ref for item in self.transitions}) != 3
                or any(item.subnet_template_ref != self.subnet_template_ref
                       for item in (*self.places, *self.transitions))):
            raise OperationAuthorityError(
                "fault subnet roles/refs are incomplete or noncanonical")
        places = {item.role: item for item in self.places}
        expected_arcs = {
            "retry": (
                (places["fault_pending"].place_ref,
                 places["original_work_hold"].place_ref),
                (places["original_work_hold"].place_ref,),
            ),
            "exhaust": (
                (places["fault_pending"].place_ref,),
                (places["structural_terminal_intake"].place_ref,),
            ),
            "reconcile": (
                (places["fault_pending"].place_ref,),
                (places["reconciliation_pending"].place_ref,),
            ),
        }
        if any(
                (item.input_place_refs, item.output_place_refs)
                != expected_arcs[item.role]
                for item in self.transitions):
            raise OperationAuthorityError(
                "fault subnet arcs differ from retry/exhaust/reconcile topology")


@dataclass(frozen=True, slots=True)
class FaultWorkRouteAuthority:
    """One exact port-to-hold-to-original route; ordering is not authority."""

    port_ref: VersionRef
    held_place_ref: VersionRef
    held_place: str
    original_place: str

    def __post_init__(self) -> None:
        if not isinstance(self.port_ref, VersionRef):
            raise TypeError("fault work route requires one exact port ref")
        if (not isinstance(self.held_place_ref, VersionRef)
                or self.held_place_ref.entity_type != "fault_petri_place/v1"):
            raise TypeError(
                "fault work route requires one exact held place ref")
        if (not isinstance(self.held_place, str) or not self.held_place
                or not isinstance(self.original_place, str)
                or not self.original_place
                or self.held_place == self.original_place):
            raise OperationAuthorityError(
                "fault work route requires distinct held/original places")


@dataclass(frozen=True, slots=True)
class FaultMechanicalTransitionRouteAuthority:
    """Route-expanded mechanical transition with no operation binding."""

    transition_ref: VersionRef
    role: Literal["retry", "exhaust", "reconcile"]
    input_places: tuple[str, ...]
    output_places: tuple[str, ...]
    accepted_verdicts: tuple[str, ...]
    emit: Literal["forward"]

    def __post_init__(self) -> None:
        if (not isinstance(self.transition_ref, VersionRef)
                or self.transition_ref.entity_type
                != "fault_petri_transition/v1"):
            raise OperationAuthorityError(
                "mechanical route requires fault_petri_transition/v1")
        verdicts = {
            "retry": ("retry",),
            "exhaust": ("exhaust",),
            "reconcile": ("reconcile",),
        }
        if (self.role not in verdicts
                or self.accepted_verdicts != verdicts[self.role]
                or self.emit != "forward"):
            raise OperationAuthorityError(
                "mechanical route role/guard/emit is not canonical")
        for label, places in (
                ("input", self.input_places),
                ("output", self.output_places)):
            if (not places
                    or any(not isinstance(place, str) or not place
                           for place in places)
                    or len(set(places)) != len(places)):
                raise OperationAuthorityError(
                    f"mechanical route {label} places are not closed")


@dataclass(frozen=True, slots=True)
class OperationFaultRouteAuthority:
    """Exact born-D1 Petri route declared for one operation fault boundary."""

    fault_route_binding_ref: VersionRef
    operation_binding_ref: VersionRef
    policy_ref: VersionRef
    subnet_template_ref: VersionRef
    subnet: FaultSubnetAuthority
    fault_pending_place_ref: VersionRef
    fault_pending_place: str
    held_work_port_refs: tuple[VersionRef, ...]
    held_work_place_refs: tuple[VersionRef, ...]
    held_work_places: tuple[str, ...]
    original_work_places: tuple[str, ...]
    capacity_return_port_refs: tuple[VersionRef, ...]
    capacity_return_places: tuple[str, ...]
    capacity_return_place_ref: VersionRef
    reconcile_ref: VersionRef
    reconcile_place_ref: VersionRef
    reconcile_place: str
    terminal_place_ref: VersionRef
    terminal_place: str
    retry_transition_ref: VersionRef
    exhaust_transition_ref: VersionRef
    mechanical_transitions: tuple[
        FaultMechanicalTransitionRouteAuthority, ...]
    route_digest: str
    dependency_fingerprint: str

    def __post_init__(self) -> None:
        if (not isinstance(self.fault_route_binding_ref, VersionRef)
                or self.fault_route_binding_ref.entity_type
                != "fault_route_binding/v1"):
            raise OperationAuthorityError(
                "fault route authority requires fault_route_binding/v1")
        if (not isinstance(self.operation_binding_ref, VersionRef)
                or self.operation_binding_ref.entity_type
                != "operation_binding/v1"):
            raise OperationAuthorityError(
                "fault route authority requires operation_binding/v1")
        if (not isinstance(self.policy_ref, VersionRef)
                or self.policy_ref.entity_type
                != "fault_disposition_policy/v1"):
            raise OperationAuthorityError(
                "fault route authority requires fault disposition policy")
        if (not isinstance(self.subnet_template_ref, VersionRef)
                or self.subnet_template_ref.entity_type
                != "fault_subnet_template/v1"):
            raise OperationAuthorityError(
                "fault route authority requires fault subnet template")
        if (not isinstance(self.subnet, FaultSubnetAuthority)
                or self.subnet.subnet_template_ref
                != self.subnet_template_ref):
            raise OperationAuthorityError(
                "fault route authority requires typed registered subnet")
        for label, ref in (
                ("fault pending place", self.fault_pending_place_ref),
                ("capacity return place", self.capacity_return_place_ref),
                ("reconcile transition", self.reconcile_ref),
                ("reconcile place", self.reconcile_place_ref),
                ("terminal place", self.terminal_place_ref),
                ("retry transition", self.retry_transition_ref),
                ("exhaust transition", self.exhaust_transition_ref)):
            if not isinstance(ref, VersionRef):
                raise TypeError(f"fault route {label} requires one exact ref")
        for label, refs, places in (
                ("capacity return", self.capacity_return_port_refs,
                 self.capacity_return_places),):
            if (not refs or len(refs) != len(places)
                    or any(not isinstance(ref, VersionRef) for ref in refs)
                    or len(set(refs)) != len(refs)
                    or any(not isinstance(place, str) or not place
                           for place in places)
                    or len(set(places)) != len(places)):
                raise OperationAuthorityError(
                    f"fault route {label} port/place declaration is not closed")
        if (not self.held_work_port_refs
                or len(self.held_work_port_refs) != len(self.held_work_places)
                or len(self.held_work_place_refs) != len(self.held_work_places)
                or len(self.held_work_places) != len(self.original_work_places)
                or any(not isinstance(ref, VersionRef)
                       for ref in self.held_work_port_refs)
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "fault_petri_place/v1"
                       for ref in self.held_work_place_refs)
                or len(set(self.held_work_place_refs))
                != len(self.held_work_place_refs)
                or any(not isinstance(place, str) or not place
                       for place in (
                           *self.held_work_places,
                           *self.original_work_places))
                or len(set(self.held_work_places))
                != len(self.held_work_places)
                or len(set(self.original_work_places))
                != len(self.original_work_places)
                or set(self.held_work_places) & set(self.original_work_places)):
            raise OperationAuthorityError(
                "fault route work port/hold/original mapping is not closed")
        if set(self.held_work_places) & set(self.capacity_return_places):
            raise OperationAuthorityError(
                "fault route work/capacity declarations overlap")
        for label, place in (
                ("fault pending", self.fault_pending_place),
                ("reconcile", self.reconcile_place),
                ("terminal", self.terminal_place)):
            if not isinstance(place, str) or not place:
                raise OperationAuthorityError(
                    f"fault route {label} place is empty")
        all_route_places = (
            self.fault_pending_place,
            *self.held_work_places,
            self.reconcile_place,
            self.terminal_place,
            *self.original_work_places,
            *self.capacity_return_places,
        )
        if len(set(all_route_places)) != len(all_route_places):
            raise OperationAuthorityError(
                "fault route control/business/capacity place names overlap")
        transitions = {item.role: item for item in self.subnet.transitions}
        route_place_refs = (
            self.fault_pending_place_ref, *self.held_work_place_refs,
            self.reconcile_place_ref, self.terminal_place_ref)
        if (len(set(route_place_refs)) != len(route_place_refs)
                or any(ref.entity_type != "fault_petri_place/v1"
                       for ref in route_place_refs)
                or self.reconcile_ref
                != transitions["reconcile"].transition_ref
                or self.retry_transition_ref
                != transitions["retry"].transition_ref
                or self.exhaust_transition_ref
                != transitions["exhaust"].transition_ref):
            raise OperationAuthorityError(
                "fault route endpoints are not one route-local registered subnet")
        expected_mechanical = {
            "retry": (
                self.retry_transition_ref,
                (self.fault_pending_place, *self.held_work_places),
                self.original_work_places,
                ("retry",),
            ),
            "exhaust": (
                self.exhaust_transition_ref,
                (self.fault_pending_place, *self.held_work_places),
                (self.terminal_place,),
                ("exhaust",),
            ),
            "reconcile": (
                self.reconcile_ref,
                (self.fault_pending_place, *self.held_work_places),
                (self.reconcile_place,),
                ("reconcile",),
            ),
        }
        if (tuple(item.role for item in self.mechanical_transitions)
                != ("exhaust", "reconcile", "retry")
                or any(
                    (item.transition_ref, item.input_places,
                     item.output_places, item.accepted_verdicts)
                    != expected_mechanical[item.role]
                    for item in self.mechanical_transitions)):
            raise OperationAuthorityError(
                "fault route mechanical transitions differ from its work mapping")

    @property
    def work_routes(self) -> tuple[FaultWorkRouteAuthority, ...]:
        """Return verified records so callers never infer tuple alignment."""
        return tuple(
            FaultWorkRouteAuthority(
                port_ref, held_place_ref, held_place, original_place)
            for port_ref, held_place_ref, held_place, original_place in zip(
                self.held_work_port_refs,
                self.held_work_place_refs,
                self.held_work_places,
                self.original_work_places,
                strict=True))

    def mechanical_transition_id(
            self, role: Literal["retry", "exhaust", "reconcile"],
    ) -> str:
        """Return the route-instance id for one shared template transition."""
        if role not in {"retry", "exhaust", "reconcile"}:
            raise OperationAuthorityError(
                "fault mechanical transition role is not closed")
        return f"{self.fault_route_binding_ref.entity_id}::{role}"


_HistoricalOperationFaultRouteAuthority = OperationFaultRouteAuthority


@dataclass(frozen=True, slots=True)
class StaticOperationFaultRouteAuthority:
    """Current static sidecar route; disposition is post-fault authority."""

    fault_route_binding_ref: VersionRef
    operation_binding_ref: VersionRef
    subnet_template_ref: VersionRef
    subnet: FaultSubnetAuthority
    fault_pending_place_ref: VersionRef
    blocked_waiting_retry_decision_control: str
    retry_transition_ref: VersionRef
    exhaust_transition_ref: VersionRef
    reconcile_transition_ref: VersionRef
    original_work_places: tuple[str, ...] = ()
    capacity_return_places: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        refs = (
            (self.fault_route_binding_ref, "fault_route_binding/v1"),
            (self.operation_binding_ref, "operation_binding/v1"),
            (self.subnet_template_ref, "fault_subnet_template/v1"),
            (self.fault_pending_place_ref, "fault_petri_place/v1"),
            (self.retry_transition_ref, "fault_petri_transition/v1"),
            (self.exhaust_transition_ref, "fault_petri_transition/v1"),
            (self.reconcile_transition_ref, "fault_petri_transition/v1"),
        )
        if any(not isinstance(ref, VersionRef) or ref.entity_type != expected
               for ref, expected in refs):
            raise OperationAuthorityError("static fault route contains a mistyped ref")
        if (not isinstance(self.subnet, FaultSubnetAuthority)
                or self.subnet.subnet_template_ref != self.subnet_template_ref
                or self.blocked_waiting_retry_decision_control
                != "blocked_waiting_retry_decision"):
            raise OperationAuthorityError("static fault route authority is incomplete")
        transitions = {item.role: item.transition_ref
                       for item in self.subnet.transitions}
        if transitions != {
                "retry": self.retry_transition_ref,
                "exhaust": self.exhaust_transition_ref,
                "reconcile": self.reconcile_transition_ref}:
            raise OperationAuthorityError("static route transition refs differ from subnet")

    @property
    def fault_pending_place(self) -> str:
        return next(item.place for item in self.subnet.places
                    if item.place_ref == self.fault_pending_place_ref)

    @property
    def reconcile_ref(self) -> VersionRef:
        return self.reconcile_transition_ref

    @property
    def mechanical_transitions(
            self) -> tuple[FaultMechanicalTransitionRouteAuthority, ...]:
        places = {item.place_ref: item.place for item in self.subnet.places}
        return tuple(FaultMechanicalTransitionRouteAuthority(
            transition_ref=item.transition_ref,
            role=item.role,
            input_places=tuple(places[ref] for ref in item.input_place_refs),
            output_places=tuple(places[ref] for ref in item.output_place_refs),
            accepted_verdicts=item.accepted_verdicts,
            emit="forward",
        ) for item in self.subnet.transitions)

    def mechanical_transition_id(
            self, role: Literal["retry", "exhaust", "reconcile"],
    ) -> str:
        if role not in {"retry", "exhaust", "reconcile"}:
            raise OperationAuthorityError("fault sidecar role is not closed")
        return f"{self.fault_route_binding_ref.entity_id}::{role}"


OperationFaultRouteAuthority = StaticOperationFaultRouteAuthority


def validate_fault_route_place_isolation(
        routes: Sequence[OperationFaultRouteAuthority],
) -> tuple[OperationFaultRouteAuthority, ...]:
    """Prove one exact static route is owned by each executable operation."""
    values = tuple(routes)
    if (not values
            or any(not isinstance(route, OperationFaultRouteAuthority)
                   for route in values)
            or len({route.fault_route_binding_ref for route in values})
            != len(values)
            or len({route.operation_binding_ref for route in values})
            != len(values)):
        raise OperationAuthorityError(
            "fault route isolation requires distinct typed operation routes")
    return values


@dataclass(frozen=True, slots=True)
class OperationFaultPartitionAuthority:
    """Registry-verified declared route and partition of every claimed token."""

    route: OperationFaultRouteAuthority
    held_work_refs: tuple[VersionRef, ...]
    returned_capacity_refs: tuple[VersionRef, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.route, OperationFaultRouteAuthority):
            raise TypeError(
                "operation fault partition requires typed declared route")
        for label, refs in (
                ("held work", self.held_work_refs),
                ("returned capacity", self.returned_capacity_refs)):
            _canonical_operation_fault_refs(refs, label=label)
        if set(self.held_work_refs) & set(self.returned_capacity_refs):
            raise OperationAuthorityError(
                "operation fault work/capacity partitions overlap")


@dataclass(frozen=True, slots=True)
class RegisteredOperationAuthority:
    input_plan: RegisteredOperationInputPlanAuthority
    canonical: CanonicalInvocationAuthority
    firing: TransitionFiringAuthority
    transition: ExecutableTransitionAuthority
    operation_binding: OperationBindingAuthority
    spec: OperationSpecAuthority
    inputs: tuple[RegisteredOperationInputAuthority, ...]
    verified_at_head: RegistryHead

    @property
    def llm_prompt_input(self) -> RegisteredOperationInputAuthority:
        if self.spec.transport != "llm":
            raise OperationAuthorityError(
                "deterministic operation has no LLM prompt input")
        matches = tuple(
            item for item in self.inputs
            if item.port_id == self.spec.llm_prompt_port_id)
        if len(matches) != 1:
            raise OperationAuthorityError(
                "operation authority lacks one exact LLM prompt input")
        return matches[0]

    def __post_init__(self) -> None:
        if not isinstance(
                self.input_plan, RegisteredOperationInputPlanAuthority):
            raise TypeError("operation authority requires its exact input plan")
        if not isinstance(self.canonical, CanonicalInvocationAuthority):
            raise TypeError("operation authority requires canonical invocation authority")
        if not isinstance(self.firing, TransitionFiringAuthority):
            raise TypeError("operation authority requires transition firing authority")
        if not isinstance(self.transition, ExecutableTransitionAuthority):
            raise TypeError("operation authority requires executable transition authority")
        if not isinstance(self.operation_binding, OperationBindingAuthority):
            raise TypeError("operation authority requires typed operation binding")
        if not isinstance(self.spec, OperationSpecAuthority):
            raise TypeError("operation authority requires typed operation spec")
        if not isinstance(self.verified_at_head, RegistryHead):
            raise TypeError("operation authority requires Registry head proof")
        if (self.input_plan.canonical != self.canonical
                or self.input_plan.firing != self.firing
                or self.input_plan.transition != self.transition
                or self.input_plan.operation_binding != self.operation_binding
                or self.input_plan.spec != self.spec):
            raise OperationAuthorityError(
                "operation authority differs from its planned input closure")
        planned_substitutions = {
            (item.claimed_token_ref, item.resource_ref, item.port.port_id,
             item.source_resource_ref)
            for item in self.input_plan.claims
            if item.source_resource_ref is not None}
        resolved_substitutions = {
            (item.claimed_token_ref, item.resource_ref, item.port_id,
             item.source_resource_ref)
            for item in self.inputs
            if item.source_resource_ref is not None}
        if resolved_substitutions != planned_substitutions:
            raise OperationAuthorityError(
                "operation input substitution differs from its exact source plan")


@dataclass(frozen=True, slots=True)
class RegisteredOperationPermit:
    """Fresh Registry-hydrated barrier required before operation dispatch."""

    authority: RegisteredOperationAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.authority, RegisteredOperationAuthority):
            raise TypeError("operation permit requires registered authority")


@dataclass(frozen=True, slots=True)
class OperationExecutionAuthority:
    """Durable proof that one exact registered operation began execution."""

    operation: RegisteredOperationAuthority
    operation_execution_lease_ref: VersionRef
    start_event_id: TypedId
    declaration_terminal_delivery_ref: VersionRef | None
    admission_head: RegistryHead
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        if not isinstance(self.operation, RegisteredOperationAuthority):
            raise TypeError("operation execution requires registered authority")
        if (not isinstance(self.operation_execution_lease_ref, VersionRef)
                or self.operation_execution_lease_ref.entity_type
                != "operation_execution_lease/v1"
                or self.operation_execution_lease_ref
                != self.operation.canonical.context.operation_execution_lease_ref):
            raise OperationAuthorityError(
                "operation execution lease differs from invocation authority")
        if (not isinstance(self.start_event_id, TypedId)
                or self.start_event_id.kind != "event"):
            raise OperationAuthorityError(
                "operation execution requires one exact start event id")
        if (self.declaration_terminal_delivery_ref is not None
                and (not isinstance(
                    self.declaration_terminal_delivery_ref, VersionRef)
                     or self.declaration_terminal_delivery_ref.entity_type
                     != "resource_delivery/v1")):
            raise OperationAuthorityError(
                "operation execution declaration delivery ref is mistyped")
        if (not isinstance(self.admission_head, RegistryHead)
                or not isinstance(self.verified_at_head, RegistryHead)):
            raise TypeError("operation execution requires Registry head proofs")


def _parse_utc_timestamp(label: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise OperationAuthorityError(f"{label} must be one UTC timestamp")
    try:
        parsed = datetime.fromisoformat(
            f"{value[:-1]}+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise OperationAuthorityError(
            f"{label} must be one UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(
            parsed):
        raise OperationAuthorityError(f"{label} must be one UTC timestamp")
    return parsed


def _require_utc_timestamp(label: str, value: object) -> str:
    _parse_utc_timestamp(label, value)
    return value


@dataclass(frozen=True, slots=True)
class OperationExecutionBlockAuthority:
    """In-memory disposition for one provisional, incomplete execution."""

    execution: OperationExecutionAuthority
    block_kind: OperationExecutionBlockKind
    operation_or_tool_identity: str
    error_code: str
    error_message: str | None
    boundary: str
    consecutive_count: int
    exact_error_ref: VersionRef
    retry_not_before_utc: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.execution, OperationExecutionAuthority):
            raise TypeError("execution block requires typed operation execution")
        if (self.block_kind not in _OPERATION_EXECUTION_BLOCK_KINDS
                or any(not isinstance(value, str) or not value
                       for value in (
                           self.operation_or_tool_identity, self.error_code,
                           self.boundary))
                or (self.error_message is not None
                    and not isinstance(self.error_message, str))
                or isinstance(self.consecutive_count, bool)
                or not isinstance(self.consecutive_count, int)
                or self.consecutive_count < 1
                or not isinstance(self.exact_error_ref, VersionRef)):
            raise OperationAuthorityError(
                "operation execution block disposition is invalid")
        if (self.block_kind == "llm_retry_wait"
                and self.consecutive_count not in {1, 2}):
            raise OperationAuthorityError(
                "LLM retry wait requires failure count one or two")
        if self.block_kind == "llm_retry_wait":
            _require_utc_timestamp(
                "retry_not_before_utc", self.retry_not_before_utc)
        elif self.retry_not_before_utc is not None:
            raise OperationAuthorityError(
                "only LLM retry wait may carry a scheduled wake time")


@dataclass(frozen=True, slots=True)
class RegisteredOperationOutputAuthority:
    port_id: str
    place: str
    place_ref: VersionRef
    output_binding_ref: VersionRef
    schema_ref: VersionRef
    artifact: VerifiedResourceArtifact
    work_resource_ref: ResourceVersionRef | None = None
    kind: str | None = None
    verdict: bool | str | None = None
    continuation: PetriContinuation | None = None
    lease_claims: tuple[PetriLeaseClaim, ...] = ()

    @property
    def resource_ref(self) -> ResourceVersionRef:
        return self.artifact.header.ref

    def __post_init__(self) -> None:
        _require_lexical_id("registered operation output port_id", self.port_id)
        if not isinstance(self.place, str) or not self.place:
            raise OperationAuthorityError(
                "registered operation output requires an explicit Petri place")
        if not isinstance(self.place_ref, VersionRef):
            raise TypeError("registered operation output requires an exact place ref")
        if (not isinstance(self.output_binding_ref, VersionRef)
                or self.output_binding_ref.entity_type != "output_binding/v1"):
            raise OperationAuthorityError(
                "operation output requires exact output_binding/v1 provenance")
        if not isinstance(self.schema_ref, VersionRef):
            raise TypeError("operation output requires exact schema ref")
        if not isinstance(self.artifact, VerifiedResourceArtifact):
            raise TypeError("operation output must already be a verified Registry resource")
        if self.artifact.header.origin_kind != "petri_output":
            raise OperationAuthorityError(
                "operation output was not published through petri_output")
        if (self.work_resource_ref is not None
                and not isinstance(self.work_resource_ref, ResourceVersionRef)):
            raise TypeError("operation work output requires exact resource ref")
        if self.kind is not None and not isinstance(self.kind, str):
            raise TypeError("operation output kind must be a string or None")
        if (self.verdict is not None
                and not isinstance(self.verdict, (bool, str))):
            raise TypeError("operation output verdict must be bool, string or None")
        if (self.continuation is not None
                and not isinstance(self.continuation, PetriContinuation)):
            raise TypeError("operation continuation must be typed Registry authority")
        if (not isinstance(self.lease_claims, tuple)
                or any(not isinstance(claim, PetriLeaseClaim)
                       for claim in self.lease_claims)):
            raise TypeError("operation output lease claims must be typed")


@dataclass(frozen=True, slots=True)
class RegisteredOperationOutputsAuthority:
    execution: OperationExecutionAuthority
    outputs: tuple[RegisteredOperationOutputAuthority, ...]
    verified_at_head: RegistryHead
    selected_outcome_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution, OperationExecutionAuthority):
            raise TypeError("registered outputs require operation execution authority")
        if any(not isinstance(item, RegisteredOperationOutputAuthority)
               for item in self.outputs):
            raise TypeError("registered outputs contain an untyped output")
        if not isinstance(self.verified_at_head, RegistryHead):
            raise TypeError("registered outputs require Registry head proof")
        if self.selected_outcome_id is not None and (
                not isinstance(self.selected_outcome_id, str)
                or not self.selected_outcome_id):
            raise OperationAuthorityError("registered output outcome is malformed")


@dataclass(frozen=True, slots=True)
class RegisterOperationFault:
    observation_kind: OperationFaultObservationKind
    certainty: FaultCertainty
    provider_attempt_evidence_ref: VersionRef | None
    provider_submission_unknown_ref: VersionRef | None
    fault_terminal_evidence_ref: VersionRef | None

    def __post_init__(self) -> None:
        if self.observation_kind not in _FAULT_OBSERVATION_KINDS:
            raise OperationAuthorityError(
                "operation fault observation kind is not closed")
        if self.certainty not in _FAULT_CERTAINTIES:
            raise OperationAuthorityError("operation fault certainty is not closed")
        typed_optional = (
            (self.provider_attempt_evidence_ref,
             "provider_attempt_evidence/v1"),
            (self.provider_submission_unknown_ref,
             "provider_submission_unknown/v1"),
            (self.fault_terminal_evidence_ref,
             "fault_terminal_witness/v1"),
        )
        if any(ref is not None and (
                not isinstance(ref, VersionRef) or ref.entity_type != expected)
               for ref, expected in typed_optional):
            raise OperationAuthorityError(
                "operation fault contains a mistyped exact evidence ref")
        if self.observation_kind in {"submission_unknown", "outcome_unknown"}:
            if (self.certainty != "unknown_requires_reconciliation"
                    or self.provider_submission_unknown_ref is None):
                raise OperationAuthorityError(
                    "unknown operation fault requires its persistent ref")
        elif self.provider_submission_unknown_ref is not None:
            raise OperationAuthorityError(
                "ordinary operation fault cannot cite submission uncertainty")


@dataclass(frozen=True, slots=True)
class _HistoricalOperationFaultAuthority:
    execution: OperationExecutionAuthority
    operation_fault_ref: VersionRef
    route: OperationFaultRouteAuthority
    provider_submission_unknown_ref: VersionRef | None
    boundary: FaultBoundary
    cause_class: FaultCause
    certainty: FaultCertainty
    fault_terminal_evidence_ref: VersionRef
    held_work_refs: tuple[VersionRef, ...]
    returned_capacity_refs: tuple[VersionRef, ...]
    dependency_fingerprint: str
    verified_at_head: RegistryHead
    business_firing_settlement_ref: VersionRef | None = None
    fault_sidecar_action_ref: VersionRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution, OperationExecutionAuthority):
            raise TypeError(
                "operation fault requires parent execution authority")
        if (not isinstance(self.operation_fault_ref, VersionRef)
                or self.operation_fault_ref.entity_type != "operation_fault/v1"):
            raise OperationAuthorityError(
                "operation fault authority requires operation_fault/v1")
        if not isinstance(self.route, OperationFaultRouteAuthority):
            raise TypeError("operation fault requires typed declared Petri route")
        if self.route != self.execution.operation.fault_partition.route:
            raise OperationAuthorityError(
                "operation fault route differs from execution authority")
        is_unknown = (
            self.boundary == "provider"
            and self.cause_class == "submitted_outcome_unknown"
            and self.certainty == "unknown_requires_reconciliation"
        )
        if is_unknown:
            if (not isinstance(self.provider_submission_unknown_ref, VersionRef)
                    or self.provider_submission_unknown_ref.entity_type
                    != "provider_submission_unknown/v1"):
                raise OperationAuthorityError(
                    "operation uncertainty requires its persistent witness")
        elif self.provider_submission_unknown_ref is not None:
            raise OperationAuthorityError(
                "ordinary operation fault cannot cite submission uncertainty")
        if (not isinstance(self.fault_terminal_evidence_ref, VersionRef)
                or self.fault_terminal_evidence_ref.entity_type
                != "fault_terminal_witness/v1"):
            raise OperationAuthorityError(
                "operation fault lacks generic terminal evidence")
        if self.boundary not in _FAULT_BOUNDARIES:
            raise OperationAuthorityError("operation fault boundary is not closed")
        if self.cause_class not in _FAULT_CAUSES:
            raise OperationAuthorityError("operation fault cause is not closed")
        if self.certainty not in _FAULT_CERTAINTIES:
            raise OperationAuthorityError("operation fault certainty is not closed")
        if (self.held_work_refs
                != self.execution.operation.fault_partition.held_work_refs
                or self.returned_capacity_refs
                != self.execution.operation.fault_partition.returned_capacity_refs):
            raise OperationAuthorityError(
                "operation fault differs from declared route partition")
        if not isinstance(self.verified_at_head, RegistryHead):
            raise TypeError("operation fault requires Registry head proof")
        if ((self.business_firing_settlement_ref is None)
                != (self.fault_sidecar_action_ref is None)):
            raise OperationAuthorityError(
                "operation fault sidecar refs are atomic")
        if (self.fault_sidecar_action_ref is not None
                and (self.fault_sidecar_action_ref.entity_type
                     != "fault_sidecar_action/v1"
                     or not isinstance(
                         self.business_firing_settlement_ref, VersionRef))):
            raise OperationAuthorityError(
                "operation fault sidecar refs are mistyped")


@dataclass(frozen=True, slots=True)
class _HistoricalOperationUncertainAuthority:
    """Nonterminal reconciliation authority for a submitted unknown attempt."""

    execution: OperationExecutionAuthority
    submission_unknown: ProviderSubmissionUnknownAuthority
    operation_fault_ref: VersionRef
    route: OperationFaultRouteAuthority
    boundary: Literal["provider"]
    cause_class: Literal["submitted_outcome_unknown"]
    certainty: Literal["unknown_requires_reconciliation"]
    fault_terminal_evidence_ref: VersionRef
    held_work_refs: tuple[VersionRef, ...]
    returned_capacity_refs: tuple[VersionRef, ...]
    dependency_fingerprint: str
    verified_at_head: RegistryHead

    def __post_init__(self) -> None:
        from .resources import ProviderSubmissionUnknownAuthority
        if not isinstance(self.execution, OperationExecutionAuthority):
            raise TypeError("operation uncertainty requires execution authority")
        if not isinstance(
                self.submission_unknown, ProviderSubmissionUnknownAuthority):
            raise TypeError(
                "operation uncertainty requires provider submission-unknown authority")
        if (not isinstance(self.operation_fault_ref, VersionRef)
                or self.operation_fault_ref.entity_type != "operation_fault/v1"):
            raise OperationAuthorityError(
                "operation uncertainty requires exact operation fault ref")
        if (not isinstance(self.route, OperationFaultRouteAuthority)
                or self.route
                != self.execution.operation.fault_partition.route):
            raise OperationAuthorityError(
                "operation uncertainty lacks its declared Petri route")
        if (self.submission_unknown.provider_submission_unknown_ref.entity_type
                != "provider_submission_unknown/v1"):
            raise OperationAuthorityError(
                "operation uncertainty has no persistent submission witness")
        if (self.boundary != "provider"
                or self.cause_class != "submitted_outcome_unknown"
                or self.certainty != "unknown_requires_reconciliation"):
            raise OperationAuthorityError(
                "operation uncertainty is not the closed reconcile phase")
        if (not isinstance(self.fault_terminal_evidence_ref, VersionRef)
                or self.fault_terminal_evidence_ref.entity_type
                != "fault_terminal_witness/v1"):
            raise OperationAuthorityError(
                "operation uncertainty lacks generic terminal evidence")
        if (self.held_work_refs
                != self.execution.operation.fault_partition.held_work_refs
                or self.returned_capacity_refs
                != self.execution.operation.fault_partition.returned_capacity_refs):
            raise OperationAuthorityError(
                "operation uncertainty differs from fault route partition")
        if not isinstance(self.verified_at_head, RegistryHead):
            raise TypeError("operation uncertainty requires Registry head proof")


@dataclass(frozen=True, slots=True)
class OperationExecutionRequest:
    """Typed shell request; all bytes and authority are already Registry-issued."""

    authority: OperationExecutionAuthority

    def __post_init__(self) -> None:
        if not isinstance(self.authority, OperationExecutionAuthority):
            raise TypeError(
                "operation execution request requires started Registry authority")


@dataclass(frozen=True, slots=True)
class OperationExecutionResult:
    """Typed shell outcome before Registry output registration."""

    outputs: tuple[VerifiedResourceArtifact, ...]
    selected_outcome_id: str | None = None

    def __post_init__(self) -> None:
        if self.selected_outcome_id is not None and (
                not isinstance(self.selected_outcome_id, str) or not self.selected_outcome_id):
            raise OperationAuthorityError("operation result outcome must be an explicit registered key")
        if not self.outputs and self.selected_outcome_id is None:
            raise OperationAuthorityError(
                "empty operation result requires an explicit declared outcome")
        if any(not isinstance(item, VerifiedResourceArtifact) for item in self.outputs):
            raise TypeError("operation outputs must be verified Registry resources")


class _OperationAuthorityRepository(Protocol):
    """Registry persistence/read seam; never an implementation dispatcher."""

    def registry_head(self) -> RegistryHead: ...

    def exact_metadata(
            self, ref: VersionRef, *, expected_type: str) -> Mapping[str, Any]: ...

    def require_registered_ref(self, ref: VersionRef) -> None: ...

    def _historical_hydrate_fault_route(
            self, route_ref: VersionRef, *,
            expected_operation_binding_ref: VersionRef,
    ) -> OperationFaultRouteAuthority: ...

    def resolve_operation_inputs(
            self, *, canonical: CanonicalInvocationAuthority,
            firing: TransitionFiringAuthority,
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            petri_inputs: tuple[
                PetriInputArtifact
                | SettledPetriInputArtifact
                | HistoricalPetriInputArtifact, ...],
    ) -> tuple[RegisteredOperationInputAuthority, ...]: ...

    def resolve_operation_input_claims(
            self, *, firing: TransitionFiringAuthority,
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            input_resource_substitutions: tuple[object, ...] = (),
    ) -> tuple[RegisteredOperationInputClaimAuthority, ...]: ...

    def resolve_pre_admission_input_claims(
            self, *, claimed_input_refs: tuple[VersionRef, ...],
            binding: OperationBindingAuthority,
            spec: OperationSpecAuthority,
            static_excluded_port_ids: frozenset[str] = frozenset(),
    ) -> tuple[RegisteredOperationInputClaimAuthority, ...]: ...

    def _historical_resolve_operation_fault_partition(
            self, *, firing: TransitionFiringAuthority,
            binding: OperationBindingAuthority,
    ) -> OperationFaultPartitionAuthority: ...

    def registered_content_schema_authority(
            self, ref: VersionRef | ResourceVersionRef, *,
            schema_document_ref: VersionRef,
    ) -> RegisteredContentSchemaAuthority: ...

    def resolve_operation_output(
            self, *, authority: RegisteredOperationAuthority,
            artifact: VerifiedResourceArtifact,
    ) -> RegisteredOperationOutputAuthority: ...

    def validate_operation_output_bundle(
            self, *, authority: RegisteredOperationAuthority,
            outputs: tuple[RegisteredOperationOutputAuthority, ...],
            selected_outcome_id: str | None = None,
    ) -> tuple[str, tuple[RegisteredOperationOutputAuthority, ...]]: ...

    def _historical_publish_operation_fault_closure(
            self, *, authority: RegisteredOperationAuthority,
            command: RegisterOperationFault,
            idempotency_key: str,
    ) -> tuple[VersionRef, VersionRef]: ...



def _parse_content_schema_ref(
        value: object, *, label: str) -> VersionRef | ResourceVersionRef:
    from ._operation.declaration import _parse_content_schema_ref as implementation
    return implementation(value, label=label)


def _parse_field_projection(
        value: object, *, label: str) -> OperationFieldProjection:
    from ._operation.declaration import _parse_field_projection as implementation
    return implementation(value, label=label)


def _parse_input_projection(
        value: object, *, label: str) -> OperationInputProjection:
    from ._operation.declaration import _parse_input_projection as implementation
    return implementation(value, label=label)


def _parse_port(
        repository: _OperationAuthorityRepository,
        value: object, *, label: str,
        allow_input_projections: bool) -> OperationPortAuthority:
    from ._operation.declaration import _parse_port as implementation
    return implementation(
        repository, value, label=label,
        allow_input_projections=allow_input_projections)


def _hydrate_spec(
        repository: _OperationAuthorityRepository, spec_ref: VersionRef,
        metadata: Mapping[str, Any]) -> OperationSpecAuthority:
    from ._operation.declaration import _hydrate_spec as implementation
    return implementation(repository, spec_ref, metadata)


def _hydrate_binding(
        repository: _OperationAuthorityRepository,
        binding_ref: VersionRef, metadata: Mapping[str, Any],
        spec: OperationSpecAuthority) -> OperationBindingAuthority:
    from ._operation.declaration import _hydrate_binding as implementation
    return implementation(repository, binding_ref, metadata, spec)


def _hydrate_registered_operation_declaration(
        repository: _OperationAuthorityRepository, *,
        canonical: CanonicalInvocationAuthority,
        firing: TransitionFiringAuthority,
        transition: ExecutableTransitionAuthority,
) -> tuple[OperationBindingAuthority, OperationSpecAuthority]:
    from ._operation.declaration import hydrate_registered_operation_declaration
    return hydrate_registered_operation_declaration(
        repository, canonical=canonical, firing=firing, transition=transition)


def _validate_inputs(
        authority_spec: OperationSpecAuthority,
        binding: OperationBindingAuthority,
        firing: TransitionFiringAuthority,
        petri_inputs: Sequence[PetriInputArtifact | SettledPetriInputArtifact
                               | HistoricalPetriInputArtifact],
        resolved: Sequence[RegisteredOperationInputAuthority],
) -> tuple[RegisteredOperationInputAuthority, ...]:
    from ._operation.inputs import _validate_inputs as implementation
    return implementation(authority_spec, binding, firing, petri_inputs, resolved)


def hydrate_operation_authority(
        repository: _OperationAuthorityRepository, *,
        input_plan: RegisteredOperationInputPlanAuthority,
        petri_inputs: tuple[PetriInputArtifact | SettledPetriInputArtifact
                            | HistoricalPetriInputArtifact, ...],
) -> RegisteredOperationAuthority:
    from ._operation.inputs import hydrate_operation_authority as implementation
    return implementation(repository, input_plan=input_plan, petri_inputs=petri_inputs)


def prepare_registered_operation_inputs(
        repository: _OperationAuthorityRepository, *,
        transition: ExecutableTransitionAuthority,
        claimed_input_refs: tuple[VersionRef, ...],
        static_excluded_port_ids: frozenset[str] = frozenset(),
) -> PreparedRegisteredOperationInputPlanAuthority:
    from ._operation.inputs import prepare_registered_operation_inputs as implementation
    return implementation(
        repository, transition=transition, claimed_input_refs=claimed_input_refs,
        static_excluded_port_ids=static_excluded_port_ids)


def plan_registered_operation_inputs(
        repository: _OperationAuthorityRepository, *,
        canonical: CanonicalInvocationAuthority,
        firing: TransitionFiringAuthority,
        transition: ExecutableTransitionAuthority,
        input_resource_substitutions: tuple[object, ...] = (),
) -> RegisteredOperationInputPlanAuthority:
    from ._operation.inputs import plan_registered_operation_inputs as implementation
    return implementation(
        repository, canonical=canonical, firing=firing, transition=transition,
        input_resource_substitutions=input_resource_substitutions)


def register_operation_outputs(
        repository: _OperationAuthorityRepository,
        execution: OperationExecutionAuthority,
        outputs: tuple[VerifiedResourceArtifact, ...], *,
        idempotency_key: str,
        selected_outcome_id: str | None = None,
) -> RegisteredOperationOutputsAuthority:
    from ._operation.outputs import register_operation_outputs as implementation
    return implementation(
        repository, execution, outputs, idempotency_key=idempotency_key,
        selected_outcome_id=selected_outcome_id)


def _historical_register_operation_fault(
        repository: _OperationAuthorityRepository,
        execution: OperationExecutionAuthority,
        fault: RegisterOperationFault, *,
        idempotency_key: str,
) -> _HistoricalOperationFaultAuthority:
    from ._operation.legacy_faults import _historical_register_operation_fault as implementation
    return implementation(repository, execution, fault, idempotency_key=idempotency_key)


def _historical_register_operation_uncertain(
        repository: _OperationAuthorityRepository,
        execution: OperationExecutionAuthority,
        submission_unknown: ProviderSubmissionUnknownAuthority,
        fault: RegisterOperationFault, *,
        idempotency_key: str,
) -> _HistoricalOperationUncertainAuthority:
    from ._operation.legacy_faults import _historical_register_operation_uncertain as implementation
    return implementation(
        repository, execution, submission_unknown, fault,
        idempotency_key=idempotency_key)


__all__ = [
    "OperationAuthorityError",
    "RegisteredContentSchemaAuthority",
    "OperationFieldProjection",
    "OperationInputProjection",
    "RegisteredOperationPortContract",
    "registered_operation_ids",
    "bind_operation_registration",
    "register_operation_contract",
    "registered_operation_contract",
    "registered_operation_host_protocols",
    "registered_operation_port_contract",
    "registered_operation_transport",
    "OperationPortAuthority",
    "OperationSpecAuthority",
    "OperationExecutionLimits",
    "OperationOutputPortBindingAuthority",
    "OperationBindingAuthority",
    "RegisteredOperationInputAuthority",
    "RegisteredOperationInputClaimAuthority",
    "RegisteredOperationInputPlanAuthority",
    "ClaimedPetriInputAuthority",
    "canonical_claimed_petri_inputs",
    "PreparedRegisteredOperationInputPlanAuthority",
    "RegisteredOperationAuthority",
    "OperationExecutionAuthority",
    "OperationExecutionBlockKind",
    "OperationExecutionBlockAuthority",
    "RegisteredOperationOutputAuthority",
    "RegisteredOperationOutputsAuthority",
    "OperationExecutionRequest",
    "OperationExecutionResult",
    "prepare_registered_operation_inputs",
    "plan_registered_operation_inputs",
    "hydrate_operation_authority",
    "register_operation_outputs",
]
