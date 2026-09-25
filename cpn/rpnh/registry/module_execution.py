"""Module admission through the same durable Core allocation/lifecycle writer."""
from __future__ import annotations

from dataclasses import dataclass

from ..firing_preflight import PetriFiringPreflight, preflight_module_firing
from ..marking import TeamNetMarking
from ._registry import _RegistryCore
from .allocations import register_petri_firing_allocation, _firing_allocation_identity_v1
from .firing_authority import canonical_invocation, verify_transition_firing
from .firing_transition import StartedFiringTransitionAuthority
from .invocations import (
    FiringAdmission,
    FiringClaim,
    InvocationLifecycle,
)
from .admission_publication import PreparedFiringAdmissionPublications
from .models import VersionRef
from .module_runtime import hydrate_module_runtime
from .operations import (
    PreparedRegisteredOperationInputPlanAuthority,
    RegisteredOperationInputPlanAuthority,
)
from .publication import _version_from_payload
from .resource_service import _ResourceServiceKernel
from .resources import ExecutableNetAuthority, TypedMarkingAuthority, PetriFiringAllocationAuthority
from .strict_contracts import _registered, ref_payload


def active_module_firings(
        core: _RegistryCore, net_ref: VersionRef, *,
        require_current_writer: bool = True,
):
    """Hydrate the owner's exact provisional firings for one adopted net."""

    if not isinstance(core, _RegistryCore) or not isinstance(net_ref, VersionRef):
        raise TypeError("active Module firing query requires exact Core/net")
    with core.event_store.connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT invocation_logical_id,invocation_version_id FROM firing_publications "
            "WHERE state='PROVISIONAL' AND net_version_id=? ORDER BY firing_version_id",
            (str(net_ref.version_id),)).fetchall()]
    kernel = _ResourceServiceKernel(core)
    active = []
    for row in rows:
        invocation = _version_from_payload({"entity_type": "invocation/v1",
            "logical_id": row["invocation_logical_id"],
            "version_id": row["invocation_version_id"]})
        firing = verify_transition_firing(core, kernel,
            canonical_invocation(
                core, kernel, invocation,
                require_current_writer=require_current_writer))
        if firing.net_ref != net_ref:
            raise ValueError(
                "active firing projection differs from the adopted net")
        active.append(firing)
    return tuple(active)


def install_active_module_claims(core: _RegistryCore, local: TeamNetMarking,
                                 net_ref: VersionRef):
    """Project the owner's exact live claims onto the latest committed marking.

    Admission does not consume checkpoint evidence. A sibling settlement may
    advance that checkpoint while these claims remain live, so the admitted
    checkpoint is provenance, not a substitute for the latest marking.
    """
    active = active_module_firings(core, net_ref)
    for firing in active:
        before = {item.local_key for item in local.active_claim_occurrences(local.epoch)}
        epoch = local.install_registered_active_firing_claim(
            firing.transition_id, firing.claimed_input_refs)
        added = [item.local_key for item in local.active_claim_occurrences(epoch)
                 if item.local_key not in before]
        if len(added) != 1:
            raise ValueError("active claim projection must install exactly one occurrence")
        local.bind_active_claim(added[0], firing.transition_firing_ref)
    return active


@dataclass(frozen=True)
class ModuleFiringPreparation:
    """Module-owned input lowering consumed by Registry admission.

    The callback that builds this value cannot allocate, admit, start, settle,
    or advance a marking.  Registry remains the sole writer for those actions.
    """

    input_plan: PreparedRegisteredOperationInputPlanAuthority
    activation_ref: VersionRef | None
    admission_publications: PreparedFiringAdmissionPublications | None = None

    def __post_init__(self) -> None:
        if not isinstance(
                self.input_plan,
                PreparedRegisteredOperationInputPlanAuthority):
            raise TypeError("module firing preparation requires a typed input plan")
        if (self.activation_ref is not None
                and not isinstance(self.activation_ref, VersionRef)):
            raise TypeError("module firing preparation activation is not exact")
        publications = self.admission_publications
        if (publications is not None
                and (not isinstance(
                    publications, PreparedFiringAdmissionPublications)
                     or self.activation_ref
                     != publications.activation_ref)):
            raise TypeError(
                "module firing preparation differs from its publications")


@dataclass(frozen=True)
class ModuleFiringAdmission:
    executable: ExecutableNetAuthority
    predecessor: TypedMarkingAuthority
    allocation: PetriFiringAllocationAuthority
    allocation_ref: VersionRef
    preflight: PetriFiringPreflight
    admission: FiringAdmission
    started: StartedFiringTransitionAuthority | None = None


def admit_module_firing(core: _RegistryCore, *, transition_id: str,
                        logical_tau: int | float | str, idempotency_key: str,
                        prepare_admission=None) -> ModuleFiringAdmission | None:
    """Admit one actually enabled occurrence; dead marking remains interactive.

    The owner dispatches this on its single execution event stream. No executor,
    thread, provider request, fake firing or terminal result is started here.
    """
    if not isinstance(core, _RegistryCore) or core.read_only:
        raise TypeError("Module admission requires the execution owner's Registry")
    executable, structure, marking = hydrate_module_runtime(core)
    if transition_id not in structure.transitions:
        raise ValueError("Module admission names an undeclared symbolic transition")
    local = TeamNetMarking.from_authority(structure, marking)
    install_active_module_claims(core, local, executable.net_ref)
    firings, epoch = local.claim_firing_set(max_count=1, allowed={transition_id})
    if not firings:
        return None
    kernel = _ResourceServiceKernel(core)
    evidence = local.firing_allocation_evidence()
    if len(evidence.allocations) != 1:
        raise ValueError("single admission must select one exact occurrence")
    selected_occurrence = evidence.allocations[0]
    preflight = preflight_module_firing(
        structure,
        marking,
        transition_id=transition_id,
        claimed_token_refs=selected_occurrence.token_refs,
    )
    allocation = register_petri_firing_allocation(core, kernel, executable, marking, evidence,
                                                idempotency_key=f"{idempotency_key}:allocation")
    local.bind_firing_allocation_authority(evidence, allocation)
    if len(allocation.allocations) != 1:
        raise ValueError("single admission must name one exact allocated occurrence")
    occurrence = allocation.allocations[0]
    if occurrence.token_refs != selected_occurrence.token_refs:
        raise ValueError("Module allocation differs from its Petri preflight")
    allocation_ref, _ = _firing_allocation_identity_v1(marking.checkpoint_ref, epoch, core.writer_epoch,
        tuple((item.local_key, item.transition_id, item.claim_epoch, item.token_refs) for item in allocation.allocations))
    _, persisted = _registered(core, allocation_ref, "firing_allocation/v1")
    if (persisted["checkpoint_ref"] != ref_payload(marking.checkpoint_ref)
            or occurrence.transition_id != transition_id):
        raise ValueError("Module allocation differs from current checkpoint/transition")
    transition = next(item for item in executable.transitions if item.transition_id == transition_id)
    preparation = None
    if prepare_admission is not None:
        if not callable(prepare_admission):
            raise TypeError("module admission preparation must be callable")
        preparation = prepare_admission(
            executable=executable,
            predecessor=marking,
            transition=transition,
            claimed_token_refs=occurrence.token_refs,
            idempotency_key=idempotency_key,
        )
        if (not isinstance(preparation, ModuleFiringPreparation)
                or preparation.input_plan.transition != transition
                or {item.claimed_token_ref
                    for item in preparation.input_plan.claims}
                - set(occurrence.token_refs)):
            raise ValueError(
                "module admission preparation differs from the allocated firing")
    _, root = _registered(core, executable.team_design_root_ref, "team_design_root/v1")
    task_round = _version_from_payload(root["task_round_ref"])
    _, round_data = _registered(core, task_round, "task_round/v1")
    by_ref = {token.token_ref: token.state for token in marking.tokens}
    consumed_places = {place for place, target, _ in structure.token_input_arcs if target == transition_id}
    claim = FiringClaim(task_ref=_version_from_payload(root["task_ref"]),
        task_branch_ref=_version_from_payload(round_data["task_branch_ref"]), task_round_ref=task_round,
        net_instance_ref=executable.net_ref, plan_ref=_version_from_payload(
            _registered(core, executable.net_ref, "net_instance/v1")[1]["plan_ref"]),
        node_ref=transition.node_ref, operation_binding_ref=transition.operation_binding_ref,
        marking_checkpoint_ref=marking.checkpoint_ref, principal_ref=transition.principal_ref,
        logical_tau=logical_tau, attempt_index=occurrence.attempt_index,
        claimed_input_refs=occurrence.token_refs,
        consumed_input_refs=tuple(ref for ref in occurrence.token_refs if by_ref[ref].place in consumed_places),
        firing_allocation_ref=allocation_ref,
        activation_ref=(transition.activation_ref if preparation is None
                            else preparation.activation_ref),
        agent_ref=transition.agent_ref,
        admission_publications=(None if preparation is None else
                                preparation.admission_publications))
    admission = InvocationLifecycle(core).admit_firing(claim, idempotency_key=idempotency_key)
    started = None
    if preparation is not None:
        canonical = canonical_invocation(core, kernel, admission.context.invocation_ref)
        firing = verify_transition_firing(core, kernel, canonical)
        plan = RegisteredOperationInputPlanAuthority(
            canonical=canonical,
            firing=firing,
            transition=preparation.input_plan.transition,
            operation_binding=preparation.input_plan.operation_binding,
            spec=preparation.input_plan.spec,
            claims=preparation.input_plan.claims,
            verified_at_head=preparation.input_plan.verified_at_head,
        )
        started = StartedFiringTransitionAuthority(
            predecessor_checkpoint_ref=marking.checkpoint_ref,
            executable=executable,
            canonical=canonical,
            firing=firing,
            input_plan=plan,
            preflight=preflight,
            committed_at_head=kernel._head(),
        )
    return ModuleFiringAdmission(
        executable, marking, allocation, allocation_ref, preflight, admission,
        started)


__all__ = (
    "active_module_firings",
    "ModuleFiringAdmission",
    "ModuleFiringPreparation",
    "admit_module_firing",
    "install_active_module_claims",
)
