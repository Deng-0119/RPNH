"""Owner-dispatched ordinary Module Start and acknowledged input delivery."""
from __future__ import annotations

from ..firing_preflight import PetriFiringPreflight
from .firing_authority import canonical_invocation, verify_transition_firing
from .identities import new_id
from .module_runtime import hydrate_module_runtime
from .operation_execution import start_registered_operation_execution
from .operations import plan_registered_operation_inputs, hydrate_operation_authority
from .resource_verification import verify_resource, verify_petri_input_receipt
from .resources import (
    AcknowledgeResourceDelivery, AuthorizeResourceRelease, PetriInputArtifact,
    PrepareResourceDelivery,
)


def start_module_firing(core, kernel, repository, invocation_ref=None, *,
                        started=None, preflight=None, idempotency_key):
    from .firing_transition import StartedFiringTransitionAuthority
    if (invocation_ref is None) == (started is None):
        raise TypeError("module operation start requires one admission authority")
    if not isinstance(preflight, PetriFiringPreflight):
        raise TypeError("module operation start requires its formal Petri preflight")
    if started is not None:
        if not isinstance(started, StartedFiringTransitionAuthority):
            raise TypeError("planned module start requires typed Start authority")
        canonical = started.canonical
        firing = started.firing
        executable = started.executable
        plan = started.input_plan
        if started.preflight != preflight:
            raise TypeError("planned module start differs from its Petri preflight")
    else:
        canonical = canonical_invocation(core, kernel, invocation_ref)
        firing = verify_transition_firing(core, kernel, canonical)
        executable, _structure, _marking = hydrate_module_runtime(core)
        transition = next(item for item in executable.transitions
                          if item.transition_id == firing.transition_id)
        plan = plan_registered_operation_inputs(
            repository, canonical=canonical, firing=firing,
            transition=transition)
    if (preflight.net_ref != executable.net_ref
            or preflight.checkpoint_ref
            != firing.admission_marking_checkpoint_ref
            or preflight.transition_id != firing.transition_id
            or preflight.claimed_token_refs != firing.claimed_input_refs):
        raise TypeError("module operation start differs from its Petri preflight")
    context = canonical.context
    artifacts = []
    refs = tuple(dict.fromkeys(claim.resource_ref for claim in plan.claims))
    for ref in refs:
        key = f"{idempotency_key}:input:{ref.resource_version_id}"
        prepared = kernel.prepare_delivery(context, PrepareResourceDelivery(ref, context.operation_binding_ref,
            new_id("resource_delivery"), f"{key}:prepare", "petri_input", "registered operation input"))
        release = kernel.authorize_release(context, AuthorizeResourceRelease(prepared.delivery_ref,
            "petri_input", new_id("release_nonce"), f"{key}:release"))
        payload = kernel._consume_authorized_release(context, release)
        boundary = kernel._record_boundary_receipt(context, release, outcome="acknowledged",
            positive_byte_count=len(payload), consumer_evidence="operation consumed exact declared input bytes",
            idempotency_key=f"{key}:boundary")
        receipt = kernel.acknowledge_delivery(context, AcknowledgeResourceDelivery(release.delivery_ref,
            boundary, "acknowledged", f"{key}:acknowledge"))
        artifacts.append(PetriInputArtifact(payload, verify_resource(core, kernel, canonical, ref), release,
            verify_petri_input_receipt(core, kernel, receipt, ref)))
    operation = hydrate_operation_authority(repository, input_plan=plan, petri_inputs=tuple(artifacts))
    return start_registered_operation_execution(core, kernel, repository, operation,
        executable=executable, idempotency_key=idempotency_key)


__all__ = ("start_module_firing",)
