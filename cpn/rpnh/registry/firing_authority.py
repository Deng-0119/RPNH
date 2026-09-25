"""Exact canonical invocation and firing readers, shared with Current."""
from __future__ import annotations

from typing import Any, Mapping
from ._registry import _RegistryCore
from .event_store import verified_adoption_head
from .errors import ResourceServiceError, ResourceIntegrityFault, StaleInvocationContext
from .invocations import InvocationLifecycle
from .models import VersionRef
from .publication import _ref_payload, _version_from_payload
from .resource_service import _ResourceServiceKernel
from .resources import (CanonicalInvocationAuthority, InvocationContextHandle,
                        TransitionFiringAuthority)


def _refs(value: object, *, label: str) -> tuple[VersionRef, ...]:
    if not isinstance(value, list):
        raise ResourceIntegrityFault(f"{label} is not an exact-ref array")
    try:
        return tuple(_version_from_payload(item) for item in value)
    except Exception as exc:
        raise ResourceIntegrityFault(
            f"{label} contains a malformed exact ref") from exc


def _optional_ref(value: object, *, label: str) -> VersionRef | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ResourceIntegrityFault(f"{label} is not an exact ref")
    try:
        return _version_from_payload(value)
    except Exception as exc:
        raise ResourceIntegrityFault(f"{label} is malformed") from exc


def canonical_invocation(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        invocation_ref: VersionRef, *,
        require_current_writer: bool = True,
) -> CanonicalInvocationAuthority:
    if not isinstance(invocation_ref, VersionRef):
        raise TypeError("canonical invocation requires an exact VersionRef")
    try:
        context = InvocationLifecycle(core).hydrate_context(
            invocation_ref,
            require_current_writer=require_current_writer)
        kernel._revalidate_invocation(
            context, boundary="registry-public-facade",
            native_resume=not require_current_writer)
        adopted = verified_adoption_head(core.event_store, core.catalog, core.task_id)
    except ResourceServiceError:
        raise
    except Exception as exc:
        raise StaleInvocationContext(
            "canonical invocation hydration or fence verification failed") from exc
    if context.net_instance_ref != adopted:
        raise StaleInvocationContext(
            "canonical invocation is outside the current adopted net")
    handle = InvocationContextHandle(
        invocation_ref=context.invocation_ref)
    return CanonicalInvocationAuthority(
        context=context, handle=handle,
        verified_at_head=kernel._head())


def verify_transition_firing(core: _RegistryCore, kernel: _ResourceServiceKernel,
        canonical: CanonicalInvocationAuthority) -> TransitionFiringAuthority:
    """Verify immutable exact firing facts; caller controls live I/O fencing."""
    if not isinstance(canonical, CanonicalInvocationAuthority):
        raise TypeError("firing reader requires exact canonical invocation authority")
    actual_context = InvocationLifecycle(core).hydrate_context(
        canonical.handle.invocation_ref, require_current_writer=False)
    if actual_context != canonical.context:
        raise StaleInvocationContext("firing reader differs from immutable invocation context")
    context = canonical.context
    if (context.origin != "petri_operation"
            or context.own_transition_firing_ref is None
            or context.own_node_ref is None):
        raise StaleInvocationContext(
            "transition-firing authority requires a Petri invocation")
    firing_ref = context.own_transition_firing_ref
    try:
        firing = kernel._exact_object(
            firing_ref, expected_type="transition_firing/v1")
        metadata = dict(firing.metadata)
        core.catalog.validate_instance(
            "transition_firing/v1", category="object", instance=metadata)
        refs = {
            name: _version_from_payload(metadata[name])
            for name in (
                "transition_firing_ref", "task_ref", "task_branch_ref",
                "task_round_ref", "net_instance_ref", "plan_ref",
                "node_ref", "operation_binding_ref", "principal_ref",
                "admission_marking_checkpoint_ref", "budget_witness_ref",
                "firing_admission_ref", "claim_marking_delta_ref",
            )
        }
        agent_ref = _optional_ref(
            metadata.get("agent_ref"), label="agent_ref")
        claimed = _refs(
            metadata["claimed_input_refs"], label="claimed_input_refs")
        for ref in (*refs.values(), *claimed):
            kernel._exact_object(ref)
        if agent_ref is not None:
            kernel._exact_object(
                agent_ref, expected_type="agent/v1")
    except ResourceServiceError:
        raise
    except Exception as exc:
        raise ResourceIntegrityFault(
            "transition firing exact claim envelope failed verification") from exc
    expected_context = {
        "transition_firing_ref": firing_ref,
        "task_ref": context.task_ref,
        "task_branch_ref": context.task_branch_ref,
        "task_round_ref": context.task_round_ref,
        "net_instance_ref": context.net_instance_ref,
        "plan_ref": context.plan_ref,
        "node_ref": context.own_node_ref,
        "agent_ref": context.agent_ref,
        "operation_binding_ref": context.operation_binding_ref,
        "principal_ref": context.principal_ref,
        "admission_marking_checkpoint_ref": (
            context.admission_marking_checkpoint_ref),
        "budget_witness_ref": context.budget_witness_ref,
    }
    expected_activation = (
        _ref_payload(context.activation_ref)
        if context.activation_ref is not None else None)
    node = kernel._exact_object(
        refs["node_ref"], expected_type="node_declaration/v1")
    if (any((agent_ref if name == "agent_ref" else refs[name]) != wanted
            for name, wanted in expected_context.items())
            or metadata["activation_ref"] != expected_activation
            or metadata["transition_id"]
            != node.metadata.get("transition_id")
            or metadata["claimed_input_version_ids"]
            != sorted(str(ref.version_id) for ref in claimed)):
        raise ResourceIntegrityFault(
            "transition firing differs from its canonical invocation claim")
    logical_tau = metadata["logical_tau"]
    attempt_index = metadata["attempt_index"]
    if (isinstance(logical_tau, bool)
            or not isinstance(logical_tau, (int, float, str))
            or isinstance(attempt_index, bool)
            or not isinstance(attempt_index, int)
            or attempt_index <= 0):
        raise ResourceIntegrityFault(
            "transition firing attempt/logical_tau is not closed")
    return TransitionFiringAuthority(
        transition_firing_ref=firing_ref,
        transition_id=str(metadata["transition_id"]),
        activation_ref=_optional_ref(
            metadata["activation_ref"], label="activation_ref"),
        task_ref=refs["task_ref"],
        task_branch_ref=refs["task_branch_ref"],
        task_round_ref=refs["task_round_ref"],
        net_ref=refs["net_instance_ref"],
        plan_ref=refs["plan_ref"],
        node_ref=refs["node_ref"],
        agent_ref=agent_ref,
        operation_binding_ref=refs["operation_binding_ref"],
        principal_ref=refs["principal_ref"],
        admission_marking_checkpoint_ref=refs[
            "admission_marking_checkpoint_ref"],
        budget_witness_ref=refs["budget_witness_ref"],
        firing_admission_ref=refs["firing_admission_ref"],
        claim_marking_delta_ref=refs["claim_marking_delta_ref"],
        logical_tau=logical_tau,
        attempt_index=attempt_index,
        claimed_input_refs=claimed,
        verified_at_head=kernel._head(),
    )


__all__ = ('canonical_invocation', 'verify_transition_firing')
