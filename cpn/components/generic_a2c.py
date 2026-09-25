"""Optional pure, fail-closed helpers for the actor-to-critic protocol.

This module deliberately operates on immutable references and mechanical state
only.  It does not read candidate bodies, interpret critic evidence, or assign
domain meaning to an output binding.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import re
from types import MappingProxyType
from typing import Iterable, Mapping


_ENTITY_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:/v[1-9][0-9]*)?$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*:[a-f0-9]{32}$")
A2C_VERDICTS = ("continue", "pass", "escalate", "give_up")
DESIGN_CRITIC_VERDICTS = ("adopt", "rework", "reject")


class ProtocolViolation(ValueError):
    """Raised when an exact-reference protocol check fails closed."""


class DesignCriticRoute(Enum):
    """Closed mechanical routes for the dedicated design-review boundary."""

    ADOPT = "adopt"
    REWORK = "rework"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DesignCriticRouting:
    """Literal design verdict plus unmodified provider-authored critique."""

    literal_verdict: str
    route: DesignCriticRoute
    critique: str


def map_design_critic_verdict(
        literal_verdict: str, critique: str,
) -> DesignCriticRouting:
    """Mechanically map one literal verdict without choosing business meaning."""

    if (not isinstance(literal_verdict, str)
            or literal_verdict not in DESIGN_CRITIC_VERDICTS):
        raise ProtocolViolation(
            "design critic verdict must be exactly adopt, rework, or reject")
    if not isinstance(critique, str) or not critique:
        raise ProtocolViolation(
            "design critic critique must be nonempty text")
    return DesignCriticRouting(
        literal_verdict=literal_verdict,
        route=DesignCriticRoute(literal_verdict),
        critique=critique,
    )


@dataclass(frozen=True, slots=True)
class ExactRef:
    """A complete immutable version reference; partial references are invalid."""

    entity_type: str
    logical_id: str
    version_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.entity_type, str) or not _ENTITY_TYPE.fullmatch(
            self.entity_type
        ):
            raise ProtocolViolation("invalid exact-ref entity_type")
        if not isinstance(self.logical_id, str) or not _IDENTIFIER.fullmatch(
            self.logical_id
        ):
            raise ProtocolViolation("invalid exact-ref logical_id")
        if not isinstance(self.version_id, str) or not _IDENTIFIER.fullmatch(
            self.version_id
        ):
            raise ProtocolViolation("invalid exact-ref version_id")


def require_exact_ref(actual: ExactRef, expected: ExactRef, *, label: str) -> None:
    """Require equality of the complete reference triple."""

    if not isinstance(actual, ExactRef) or not isinstance(expected, ExactRef):
        raise ProtocolViolation(f"{label} must be an ExactRef")
    if actual != expected:
        raise ProtocolViolation(f"{label} does not match its frozen exact ref")


def _typed_ref(value: ExactRef, *, label: str) -> None:
    if not isinstance(value, ExactRef):
        raise ProtocolViolation(f"{label} must be an ExactRef")


def _refs(values: Iterable[ExactRef], *, label: str, nonempty: bool = False) -> tuple[ExactRef, ...]:
    result = tuple(values)
    if nonempty and not result:
        raise ProtocolViolation(f"{label} must not be empty")
    if any(not isinstance(value, ExactRef) for value in result):
        raise ProtocolViolation(f"{label} must contain only ExactRef values")
    if len(set(result)) != len(result):
        raise ProtocolViolation(f"{label} must contain unique exact refs")
    return result


@dataclass(frozen=True, slots=True)
class OutputBindingOffer:
    """One frozen binding identity and its registered A2C outcome identity."""

    output_binding_ref: ExactRef
    description_ref: ExactRef
    declared_outcome_id: str

    def __post_init__(self) -> None:
        _typed_ref(self.output_binding_ref, label="output_binding_ref")
        _typed_ref(self.description_ref, label="description_ref")
        if self.declared_outcome_id not in A2C_VERDICTS:
            raise ProtocolViolation(
                "output binding offer declares a non-canonical A2C verdict")


def _offers(
    values: Iterable[OutputBindingOffer], *, label: str
) -> tuple[OutputBindingOffer, ...]:
    result = tuple(values)
    if len(result) != len(A2C_VERDICTS):
        raise ProtocolViolation(
            f"{label} must contain exactly the four canonical A2C bindings")
    if any(not isinstance(value, OutputBindingOffer) for value in result):
        raise ProtocolViolation(f"{label} must contain OutputBindingOffer values")
    binding_refs = tuple(value.output_binding_ref for value in result)
    _refs(binding_refs, label=label, nonempty=True)
    outcomes = tuple(value.declared_outcome_id for value in result)
    if set(outcomes) != set(A2C_VERDICTS):
        raise ProtocolViolation(
            f"{label} must map each canonical A2C verdict exactly once")
    return result


def offered_binding_for_verdict(
        invocation: "CriticInvocation", verdict: str) -> ExactRef:
    """Resolve a literal by its registered output binding outcome identity."""

    if not isinstance(invocation, CriticInvocation):
        raise ProtocolViolation("verdict resolution requires a CriticInvocation")
    if verdict not in A2C_VERDICTS:
        raise ProtocolViolation("critic verdict is not one canonical literal")
    offers = invocation.offered_output_bindings
    if len(offers) != len(A2C_VERDICTS):
        raise ProtocolViolation("critic invocation lacks four exact offered bindings")
    matches = tuple(
        offer.output_binding_ref
        for offer in offers
        if offer.declared_outcome_id == verdict
    )
    if len(matches) != 1:
        raise ProtocolViolation(
            "critic invocation does not map the verdict to one offered binding")
    return matches[0]


@dataclass(frozen=True, slots=True)
class ActivationAuthority:
    """Frozen authority against which an activation is admitted."""

    net_instance_ref: ExactRef
    team_design_root_ref: ExactRef
    node_ref: ExactRef
    producer_operation_binding_ref: ExactRef
    generic_critic_prompt_ref: ExactRef
    offered_output_bindings: tuple[OutputBindingOffer, ...]
    mechanical_resource_refs: tuple[ExactRef, ...]

    def __post_init__(self) -> None:
        for label in (
            "net_instance_ref",
            "team_design_root_ref",
            "node_ref",
            "producer_operation_binding_ref",
            "generic_critic_prompt_ref",
        ):
            _typed_ref(getattr(self, label), label=label)
        object.__setattr__(
            self,
            "offered_output_bindings",
            _offers(self.offered_output_bindings, label="offered_output_bindings"),
        )
        object.__setattr__(
            self,
            "mechanical_resource_refs",
            _refs(self.mechanical_resource_refs, label="mechanical_resource_refs"),
        )


@dataclass(frozen=True, slots=True)
class ActivationRequest:
    """Materialized activation whose authority-bearing fields require admission."""

    activation_ref: ExactRef
    net_instance_ref: ExactRef
    team_design_root_ref: ExactRef
    node_ref: ExactRef
    producer_operation_binding_ref: ExactRef
    generic_critic_prompt_ref: ExactRef
    offered_output_bindings: tuple[OutputBindingOffer, ...]
    mechanical_resource_refs: tuple[ExactRef, ...]

    def __post_init__(self) -> None:
        for label in (
            "activation_ref",
            "net_instance_ref",
            "team_design_root_ref",
            "node_ref",
            "producer_operation_binding_ref",
            "generic_critic_prompt_ref",
        ):
            _typed_ref(getattr(self, label), label=label)
        object.__setattr__(
            self,
            "offered_output_bindings",
            _offers(self.offered_output_bindings, label="offered_output_bindings"),
        )
        object.__setattr__(
            self,
            "mechanical_resource_refs",
            _refs(self.mechanical_resource_refs, label="mechanical_resource_refs"),
        )


@dataclass(frozen=True, slots=True)
class ActivationAdmission:
    activation_ref: ExactRef
    authority: ActivationAuthority

    def __post_init__(self) -> None:
        _typed_ref(self.activation_ref, label="activation_ref")
        if not isinstance(self.authority, ActivationAuthority):
            raise ProtocolViolation("authority must be an ActivationAuthority")


def admit_activation(
    request: ActivationRequest, authority: ActivationAuthority
) -> ActivationAdmission:
    """Admit only an activation that exactly matches every frozen authority field."""

    if not isinstance(request, ActivationRequest) or not isinstance(
        authority, ActivationAuthority
    ):
        raise ProtocolViolation("activation admission requires typed records")
    for item in (
        "net_instance_ref",
        "team_design_root_ref",
        "node_ref",
        "producer_operation_binding_ref",
        "generic_critic_prompt_ref",
    ):
        require_exact_ref(getattr(request, item), getattr(authority, item), label=item)
    if request.offered_output_bindings != authority.offered_output_bindings:
        raise ProtocolViolation("offered output bindings differ from frozen authority")
    if request.mechanical_resource_refs != authority.mechanical_resource_refs:
        raise ProtocolViolation("mechanical resource refs differ from frozen authority")
    return ActivationAdmission(request.activation_ref, authority)


@dataclass(frozen=True, slots=True)
class CriticReferenceClosure:
    """Domain-free, opaque reference groups supplied to the invariant critic."""

    candidate_refs: tuple[ExactRef, ...]
    input_refs: tuple[ExactRef, ...]
    task_refs: tuple[ExactRef, ...]
    context_refs: tuple[ExactRef, ...]
    requirement_refs: tuple[ExactRef, ...]
    criterion_refs: tuple[ExactRef, ...]
    evidence_refs: tuple[ExactRef, ...]

    def __post_init__(self) -> None:
        for item in fields(self):
            object.__setattr__(
                self,
                item.name,
                _refs(
                    getattr(self, item.name),
                    label=item.name,
                    nonempty=item.name in {"candidate_refs", "task_refs", "criterion_refs"},
                ),
            )


@dataclass(frozen=True, slots=True)
class CriticInvocation:
    generic_critic_invocation_ref: ExactRef
    activation_ref: ExactRef
    team_design_root_ref: ExactRef
    node_ref: ExactRef
    reference_closure: CriticReferenceClosure
    generic_critic_prompt_ref: ExactRef
    offered_output_bindings: tuple[OutputBindingOffer, ...]
    critic_invocation_ref: ExactRef
    critic_principal_ref: ExactRef

    def __post_init__(self) -> None:
        for label in (
            "generic_critic_invocation_ref",
            "activation_ref",
            "team_design_root_ref",
            "node_ref",
            "generic_critic_prompt_ref",
            "critic_invocation_ref",
            "critic_principal_ref",
        ):
            _typed_ref(getattr(self, label), label=label)
        if not isinstance(self.reference_closure, CriticReferenceClosure):
            raise ProtocolViolation("reference_closure must be a CriticReferenceClosure")
        object.__setattr__(
            self,
            "offered_output_bindings",
            _offers(self.offered_output_bindings, label="offered_output_bindings"),
        )


def build_invariant_critic_invocation(
    admission: ActivationAdmission,
    *,
    generic_critic_invocation_ref: ExactRef,
    reference_closure: CriticReferenceClosure,
    critic_invocation_ref: ExactRef,
    critic_principal_ref: ExactRef,
) -> CriticInvocation:
    """Bind one critic invocation to the activation's invariant prompt and offers."""

    if not isinstance(admission, ActivationAdmission):
        raise ProtocolViolation("critic invocation requires an admitted activation")
    authority = admission.authority
    return CriticInvocation(
        generic_critic_invocation_ref=generic_critic_invocation_ref,
        activation_ref=admission.activation_ref,
        team_design_root_ref=authority.team_design_root_ref,
        node_ref=authority.node_ref,
        reference_closure=reference_closure,
        generic_critic_prompt_ref=authority.generic_critic_prompt_ref,
        offered_output_bindings=authority.offered_output_bindings,
        critic_invocation_ref=critic_invocation_ref,
        critic_principal_ref=critic_principal_ref,
    )


@dataclass(frozen=True, slots=True)
class CriticSelection:
    """The critic's structural response; it carries no invented verdict value."""

    selected_output_binding_refs: tuple[ExactRef, ...]
    critic_evidence_ref: ExactRef
    provenance_refs: tuple[ExactRef, ...]

    def __post_init__(self) -> None:
        _typed_ref(self.critic_evidence_ref, label="critic_evidence_ref")
        object.__setattr__(
            self,
            "selected_output_binding_refs",
            _refs(
                self.selected_output_binding_refs,
                label="selected_output_binding_refs",
                nonempty=True,
            ),
        )
        object.__setattr__(
            self,
            "provenance_refs",
            _refs(self.provenance_refs, label="provenance_refs", nonempty=True),
        )


@dataclass(frozen=True, slots=True)
class ValidatedSelection:
    generic_critic_invocation_ref: ExactRef
    selected_output_binding_ref: ExactRef
    critic_evidence_ref: ExactRef
    provenance_refs: tuple[ExactRef, ...]

    def __post_init__(self) -> None:
        _typed_ref(
            self.generic_critic_invocation_ref,
            label="generic_critic_invocation_ref",
        )
        _typed_ref(self.selected_output_binding_ref, label="selected_output_binding_ref")
        _typed_ref(self.critic_evidence_ref, label="critic_evidence_ref")
        object.__setattr__(
            self,
            "provenance_refs",
            _refs(self.provenance_refs, label="provenance_refs", nonempty=True),
        )


def select_offered_output_binding(
    invocation: CriticInvocation, selection: CriticSelection
) -> ValidatedSelection:
    """Accept exactly one selection only when its complete ref was offered."""

    if not isinstance(invocation, CriticInvocation) or not isinstance(
        selection, CriticSelection
    ):
        raise ProtocolViolation("selection requires typed protocol records")
    if len(selection.selected_output_binding_refs) != 1:
        raise ProtocolViolation("critic must select exactly one output binding")
    selected = selection.selected_output_binding_refs[0]
    offered = {offer.output_binding_ref for offer in invocation.offered_output_bindings}
    if selected not in offered:
        raise ProtocolViolation("selected exact output binding ref was not offered")
    return ValidatedSelection(
        invocation.generic_critic_invocation_ref,
        selected,
        selection.critic_evidence_ref,
        selection.provenance_refs,
    )


@dataclass(frozen=True, slots=True)
class SuccessSettlement:
    activation_ref: ExactRef
    team_design_root_ref: ExactRef
    round_number: int
    actor_invocation_ref: ExactRef
    generic_critic_invocation_ref: ExactRef
    critic_invocation_ref: ExactRef
    critic_evidence_ref: ExactRef
    selected_output_binding_ref: ExactRef
    settlement_checkpoint_ref: ExactRef


def _require_invariant_invocation(
    admission: ActivationAdmission, invocation: CriticInvocation
) -> None:
    if not isinstance(admission, ActivationAdmission) or not isinstance(
        invocation, CriticInvocation
    ):
        raise ProtocolViolation("settlement requires typed activation and invocation")
    authority = admission.authority
    require_exact_ref(
        invocation.activation_ref, admission.activation_ref, label="activation_ref"
    )
    for label in (
        "team_design_root_ref",
        "node_ref",
        "generic_critic_prompt_ref",
    ):
        require_exact_ref(getattr(invocation, label), getattr(authority, label), label=label)
    if invocation.offered_output_bindings != authority.offered_output_bindings:
        raise ProtocolViolation("critic offers differ from admitted activation")


def settle_success(
    admission: ActivationAdmission,
    invocation: CriticInvocation,
    selection: ValidatedSelection,
    *,
    round_number: int,
    actor_invocation_ref: ExactRef,
    settlement_checkpoint_ref: ExactRef,
) -> SuccessSettlement:
    """Create a success settlement only from a complete, exact selection chain."""

    if not isinstance(selection, ValidatedSelection):
        raise ProtocolViolation("success settlement requires a validated selection")
    _require_invariant_invocation(admission, invocation)
    require_exact_ref(
        selection.generic_critic_invocation_ref,
        invocation.generic_critic_invocation_ref,
        label="generic_critic_invocation_ref",
    )
    offered = {
        offer.output_binding_ref for offer in admission.authority.offered_output_bindings
    }
    if selection.selected_output_binding_ref not in offered:
        raise ProtocolViolation("settlement selection was not offered")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        raise ProtocolViolation("round_number must be a positive integer")
    return SuccessSettlement(
        activation_ref=admission.activation_ref,
        team_design_root_ref=admission.authority.team_design_root_ref,
        round_number=round_number,
        actor_invocation_ref=actor_invocation_ref,
        generic_critic_invocation_ref=invocation.generic_critic_invocation_ref,
        critic_invocation_ref=invocation.critic_invocation_ref,
        critic_evidence_ref=selection.critic_evidence_ref,
        selected_output_binding_ref=selection.selected_output_binding_ref,
        settlement_checkpoint_ref=settlement_checkpoint_ref,
    )


class MechanicalSignalKind(Enum):
    PROVIDER_FAILED_RETRYABLE = "provider_failed_retryable"
    PROVIDER_FAILED_TERMINAL = "provider_failed_terminal"
    PROVIDER_CANCELLED_AFTER_SUBMISSION = "provider_cancelled_after_submission"
    PROTOCOL_INVALID = "protocol_invalid"
    RESOURCE_LIMIT_EXHAUSTED = "resource_limit_exhausted"
    SUBMISSION_UNKNOWN = "submission_unknown"
    OUTCOME_UNKNOWN = "outcome_unknown"


class OperationFaultKind(Enum):
    PROVIDER_FAILURE = "provider_failure"
    PROVIDER_CANCELLED = "provider_cancelled"
    PROTOCOL_FAILURE = "protocol_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


@dataclass(frozen=True, slots=True)
class MechanicalSignal:
    signal_ref: ExactRef
    attempt_chain_ref: ExactRef
    kind: MechanicalSignalKind

    def __post_init__(self) -> None:
        _typed_ref(self.signal_ref, label="signal_ref")
        _typed_ref(self.attempt_chain_ref, label="attempt_chain_ref")
        if not isinstance(self.kind, MechanicalSignalKind):
            raise ProtocolViolation("mechanical signal kind must be typed")


@dataclass(frozen=True, slots=True)
class OperationFault:
    """Mechanically typed fault; intentionally has no output-binding field."""

    fault_ref: ExactRef
    attempt_chain_ref: ExactRef
    kind: OperationFaultKind
    retry_allowed: bool

    def __post_init__(self) -> None:
        _typed_ref(self.fault_ref, label="fault_ref")
        _typed_ref(self.attempt_chain_ref, label="attempt_chain_ref")
        if not isinstance(self.kind, OperationFaultKind):
            raise ProtocolViolation("operation fault kind must be typed")
        if not isinstance(self.retry_allowed, bool):
            raise ProtocolViolation("retry_allowed must be boolean")


@dataclass(frozen=True, slots=True)
class ReconciliationRequired:
    """Uncertain provider state that may proceed only through reconciliation."""

    uncertainty_ref: ExactRef
    attempt_chain_ref: ExactRef

    def __post_init__(self) -> None:
        _typed_ref(self.uncertainty_ref, label="uncertainty_ref")
        _typed_ref(self.attempt_chain_ref, label="attempt_chain_ref")


FaultMapping = OperationFault | ReconciliationRequired


_FAULT_MAP: Mapping[MechanicalSignalKind, tuple[OperationFaultKind, bool]] = (
    MappingProxyType({
    MechanicalSignalKind.PROVIDER_FAILED_RETRYABLE: (
        OperationFaultKind.PROVIDER_FAILURE,
        True,
    ),
    MechanicalSignalKind.PROVIDER_FAILED_TERMINAL: (
        OperationFaultKind.PROVIDER_FAILURE,
        False,
    ),
    MechanicalSignalKind.PROVIDER_CANCELLED_AFTER_SUBMISSION: (
        OperationFaultKind.PROVIDER_CANCELLED,
        False,
    ),
    MechanicalSignalKind.PROTOCOL_INVALID: (
        OperationFaultKind.PROTOCOL_FAILURE,
        False,
    ),
    MechanicalSignalKind.RESOURCE_LIMIT_EXHAUSTED: (
        OperationFaultKind.RESOURCE_EXHAUSTION,
        False,
    ),
    })
)
_UNCERTAIN = frozenset({
    MechanicalSignalKind.SUBMISSION_UNKNOWN,
    MechanicalSignalKind.OUTCOME_UNKNOWN,
})


def map_mechanical_signal(signal: MechanicalSignal) -> FaultMapping:
    """Map a closed mechanical signal set to a disjoint fault/uncertainty type."""

    if not isinstance(signal, MechanicalSignal):
        raise ProtocolViolation("fault mapping requires a MechanicalSignal")
    if signal.kind in _UNCERTAIN:
        return ReconciliationRequired(signal.signal_ref, signal.attempt_chain_ref)
    try:
        fault_kind, retry_allowed = _FAULT_MAP[signal.kind]
    except KeyError as error:  # pragma: no cover - guards future enum expansion
        raise ProtocolViolation("unmapped mechanical signal kind") from error
    return OperationFault(
        signal.signal_ref, signal.attempt_chain_ref, fault_kind, retry_allowed
    )


@dataclass(frozen=True, slots=True)
class RetryChain:
    attempt_chain_ref: ExactRef
    attempts_used: int
    max_attempts: int

    def __post_init__(self) -> None:
        _typed_ref(self.attempt_chain_ref, label="attempt_chain_ref")
        for label, value in (
            ("attempts_used", self.attempts_used),
            ("max_attempts", self.max_attempts),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ProtocolViolation(f"{label} must be a non-negative integer")
        if self.attempts_used > self.max_attempts:
            raise ProtocolViolation("attempts_used cannot exceed max_attempts")


class RetryDisposition(Enum):
    RETRY = "retry"
    EXHAUSTED = "exhausted"
    TERMINAL = "terminal"
    RECONCILE_ONLY = "reconcile_only"


def guard_retry(chain: RetryChain, mapping: FaultMapping) -> RetryDisposition:
    """Decide by typed state and per-chain counts, never by content."""

    if not isinstance(chain, RetryChain):
        raise ProtocolViolation("retry guard requires a RetryChain")
    if not isinstance(mapping, (OperationFault, ReconciliationRequired)):
        raise ProtocolViolation("retry guard requires a mapped mechanical outcome")
    require_exact_ref(
        mapping.attempt_chain_ref, chain.attempt_chain_ref, label="attempt_chain_ref"
    )
    if isinstance(mapping, ReconciliationRequired):
        return RetryDisposition.RECONCILE_ONLY
    if not mapping.retry_allowed:
        return RetryDisposition.TERMINAL
    if chain.attempts_used >= chain.max_attempts:
        return RetryDisposition.EXHAUSTED
    return RetryDisposition.RETRY


__all__ = [
    "ActivationAdmission",
    "ActivationAuthority",
    "ActivationRequest",
    "CriticInvocation",
    "CriticReferenceClosure",
    "CriticSelection",
    "ExactRef",
    "FaultMapping",
    "MechanicalSignal",
    "MechanicalSignalKind",
    "OperationFault",
    "OperationFaultKind",
    "OutputBindingOffer",
    "ProtocolViolation",
    "ReconciliationRequired",
    "RetryChain",
    "RetryDisposition",
    "SuccessSettlement",
    "ValidatedSelection",
    "admit_activation",
    "build_invariant_critic_invocation",
    "guard_retry",
    "A2C_VERDICTS",
    "offered_binding_for_verdict",
    "map_mechanical_signal",
    "require_exact_ref",
    "select_offered_output_binding",
    "settle_success",
]
