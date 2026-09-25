"""Registry protocol for both firing transition boundaries.

Both proposal variants enter the same predecessor-resolution and registered-
binding validator in this module.  There is no Start validator versus Success
validator distinction; only the atomic commit performed after that shared
validation differs.  Start claims/admits one firing and fixes its input plan,
while Success commits the operation result, firing settlement, Petri successor,
and published checkpoint in one Registry transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from ..firing_preflight import PetriFiringPreflight
from .identities import TypedId
from .models import VersionRef
from .operations import (
    OperationExecutionAuthority,
    RegisteredOperationInputPlanAuthority,
    RegisteredOperationOutputsAuthority,
)
from .resources import (
    CanonicalInvocationAuthority,
    ExecutableNetAuthority,
    PetriFiringAllocationAuthority,
    RegistryHead,
    StructuralGrowthAdoptionAuthority,
    TypedMarkingAuthority,
    TransitionFiringAuthority,
)


@dataclass(frozen=True, slots=True)
class StartFiringTransitionProposal:
    """Submit the Start boundary to the shared firing validator."""

    executable: ExecutableNetAuthority
    predecessor: TypedMarkingAuthority
    transition_id: str
    logical_tau: int | float | str
    claimed_token_refs: tuple[VersionRef, ...]
    allocation_authority: PetriFiringAllocationAuthority
    idempotency_key: str

    def __post_init__(self) -> None:
        if (not isinstance(self.executable, ExecutableNetAuthority)
                or not isinstance(self.predecessor, TypedMarkingAuthority)
                or not isinstance(self.transition_id, str)
                or not self.transition_id
                or not isinstance(self.logical_tau, (int, float, str))
                or isinstance(self.logical_tau, bool)
                or not isinstance(self.claimed_token_refs, tuple)
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "petri_token/v1"
                       for ref in self.claimed_token_refs)
                or len(set(self.claimed_token_refs))
                != len(self.claimed_token_refs)
                or not isinstance(
                    self.allocation_authority,
                    PetriFiringAllocationAuthority)
                or not isinstance(self.idempotency_key, str)
                or not self.idempotency_key):
            raise TypeError("start firing transition proposal is incomplete")


@dataclass(frozen=True, slots=True)
class SucceedFiringTransitionProposal:
    """Submit the Success boundary to the shared firing validator."""

    start: "StartedFiringTransitionAuthority"
    operation_outputs: RegisteredOperationOutputsAuthority
    idempotency_key: str
    workspace_access_set_ref: VersionRef | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.start, StartedFiringTransitionAuthority)
                or not isinstance(
                    self.operation_outputs,
                    RegisteredOperationOutputsAuthority)
                or (self.workspace_access_set_ref is not None
                    and (not isinstance(
                        self.workspace_access_set_ref, VersionRef)
                         or self.workspace_access_set_ref.entity_type
                         != "workspace_access_set/v1"))
                or not isinstance(self.idempotency_key, str)
                or not self.idempotency_key):
            raise TypeError("success firing transition proposal is incomplete")
FiringTransitionProposal: TypeAlias = (
    StartFiringTransitionProposal | SucceedFiringTransitionProposal)


@dataclass(frozen=True, slots=True)
class StartedFiringTransitionAuthority:
    """Registry evidence for the committed start boundary."""

    predecessor_checkpoint_ref: VersionRef
    executable: ExecutableNetAuthority
    canonical: CanonicalInvocationAuthority
    firing: TransitionFiringAuthority
    input_plan: RegisteredOperationInputPlanAuthority
    preflight: PetriFiringPreflight
    committed_at_head: RegistryHead

    def __post_init__(self) -> None:
        if (not isinstance(self.predecessor_checkpoint_ref, VersionRef)
                or not isinstance(self.executable, ExecutableNetAuthority)
                or not isinstance(self.canonical, CanonicalInvocationAuthority)
                or not isinstance(self.firing, TransitionFiringAuthority)
                or not isinstance(
                    self.input_plan, RegisteredOperationInputPlanAuthority)
                or not isinstance(self.preflight, PetriFiringPreflight)
                or not isinstance(self.committed_at_head, RegistryHead)
                or self.canonical.context.own_transition_firing_ref
                != self.firing.transition_firing_ref
                or self.input_plan.canonical.context
                != self.canonical.context
                or self.input_plan.firing.transition_firing_ref
                != self.firing.transition_firing_ref
                or self.preflight.net_ref != self.executable.net_ref
                or self.preflight.checkpoint_ref
                != self.predecessor_checkpoint_ref
                or self.preflight.transition_id != self.firing.transition_id
                or self.preflight.claimed_token_refs
                != self.firing.claimed_input_refs):
            raise TypeError("started firing transition authority is incomplete")


@dataclass(frozen=True, slots=True)
class PlannedFiringOperationExecution:
    """Internal execution action over the fixed admitted input plan."""

    start: StartedFiringTransitionAuthority
    execution: OperationExecutionAuthority

    def __post_init__(self) -> None:
        if (not isinstance(self.start, StartedFiringTransitionAuthority)
                or not isinstance(self.execution, OperationExecutionAuthority)):
            raise TypeError(
                "planned firing execution differs from admitted start")
        execution_plan = self.execution.operation.input_plan
        start_plan = self.start.input_plan
        if (execution_plan.canonical.context != start_plan.canonical.context
                or execution_plan.canonical.handle != start_plan.canonical.handle
                or execution_plan.firing != start_plan.firing
                or execution_plan.transition != start_plan.transition
                or execution_plan.operation_binding
                != start_plan.operation_binding
                or execution_plan.spec != start_plan.spec
                or execution_plan.claims != start_plan.claims
                or execution_plan.verified_at_head
                != start_plan.verified_at_head
                or self.execution.operation.canonical.context
                != self.start.canonical.context
                or self.execution.operation.firing.transition_firing_ref
                != self.start.firing.transition_firing_ref):
            raise TypeError(
                "planned firing execution differs from admitted start")


@dataclass(frozen=True, slots=True)
class SucceededFiringTransitionAuthority:
    """Registry evidence for the one-cut successful successor."""

    predecessor_checkpoint_ref: VersionRef
    canonical: CanonicalInvocationAuthority
    operation_result_ref: VersionRef
    settlement_event_id: TypedId
    successor: TypedMarkingAuthority
    committed_at_head: RegistryHead
    structural_growth_adoption: StructuralGrowthAdoptionAuthority | None = None


FiringTransitionAuthority: TypeAlias = (
    StartedFiringTransitionAuthority | SucceededFiringTransitionAuthority)


__all__ = [
    "FiringTransitionAuthority",
    "FiringTransitionProposal",
    "PlannedFiringOperationExecution",
    "StartFiringTransitionProposal",
    "StartedFiringTransitionAuthority",
    "SucceedFiringTransitionProposal",
    "SucceededFiringTransitionAuthority",
]
