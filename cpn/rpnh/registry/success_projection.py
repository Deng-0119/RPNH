"""Pure Module Success projection through the sole Core marking.

The caller supplies an actual verified predecessor and registered outputs of
an admitted firing. Returned DTOs are publication proposals, not a new
checkpoint, settled firing, resource publication or terminal authority.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

from ..marking import MarkingStateError, TeamNetMarking
from ..firing_resource_access import RegisteredPetriFiringResourceAccess
from .models import VersionRef
from .module_effects import PreparedModuleEffects
from .operations import RegisteredOperationOutputsAuthority
from .resources import (
    ExecutableNetAuthority,
    PetriTokenAuthority,
    PetriTokenState,
    TransitionFiringAuthority,
    TypedMarkingAuthority,
)

if TYPE_CHECKING:
    from ..runtime_net import RuntimeNet


def project_module_firing_success(
    executable: ExecutableNetAuthority,
    structure: RuntimeNet,
    predecessor: TypedMarkingAuthority,
    outputs: RegisteredOperationOutputsAuthority,
    *, effects: PreparedModuleEffects | None = None,
    resource_accesses: tuple[
        RegisteredPetriFiringResourceAccess, ...] = (),
) -> tuple[TypedMarkingAuthority, tuple[tuple[VersionRef, PetriTokenState], ...]]:
    """Project typed PN/declaration semantics and bounded prepared effects.

    Only the registered output bundle selects conditional output arcs; input
    prose or verdicts never select a Module outcome. Existing token authorities
    not explicitly retired by the effect are retained unchanged. New token references come from Core's existing UUID
    producer, with the supplied output-verification head as preparation provenance.
    The ordinary Success gateway must verify and publish this proposal atomically.
    """
    if (not isinstance(executable, ExecutableNetAuthority)
            or not isinstance(predecessor, TypedMarkingAuthority)
            or not isinstance(outputs, RegisteredOperationOutputsAuthority)
            or not isinstance(resource_accesses, tuple)
            or any(not isinstance(
                item, RegisteredPetriFiringResourceAccess)
                for item in resource_accesses)):
        raise MarkingStateError("Module Success projection requires typed Registry inputs")
    firing = outputs.execution.operation.firing
    if (not isinstance(firing, TransitionFiringAuthority)
            or predecessor.net_ref != executable.net_ref
            or predecessor.team_design_root_ref != executable.team_design_root_ref
            or firing.net_ref != executable.net_ref
            or structure.registry_net_ref != executable.net_ref):
        raise MarkingStateError("Module Success projection differs from its exact net closure")
    transition = next((item for item in executable.transitions
                       if item.transition_id == firing.transition_id), None)
    if (transition is None
            or transition.operation_binding_ref != firing.operation_binding_ref
            or transition.node_ref != firing.node_ref
            or transition.principal_ref != firing.principal_ref):
        raise MarkingStateError("Module Success firing differs from executable binding")

    selected = structure.for_outcome(firing.transition_id, outputs.selected_outcome_id)
    claimed = {item.token_ref for item in predecessor.tokens
               if item.token_ref in firing.claimed_input_refs}
    if claimed != set(firing.claimed_input_refs):
        raise MarkingStateError("Success input predicates lack exact predecessor claim membership")
    selected = selected.for_claimed_inputs(firing.transition_id,
        tuple((item.state.place, item.state.verdict) for item in predecessor.tokens if item.token_ref in claimed))
    retired = set() if effects is None else set(effects.retired_token_refs)
    if effects is not None and (effects.checkpoint_ref != predecessor.checkpoint_ref
            or effects.net_ref != executable.net_ref
            or effects.firing_ref != firing.transition_firing_ref
            or effects.outcome_id != outputs.selected_outcome_id
            or not retired <= set(predecessor.token_refs)):
        raise MarkingStateError("effect projection differs from exact preparation closure")
    marking = TeamNetMarking.from_authority(selected, replace(predecessor,
        token_refs=tuple(ref for ref in predecessor.token_refs if ref not in retired),
        tokens=tuple(token for token in predecessor.tokens if token.token_ref not in retired)))
    claim_epoch = marking.install_registered_firing_claim(
        firing.transition_id, firing.claimed_input_refs)
    if resource_accesses:
        local_key = marking.active_claim_key(
            firing.transition_id, claim_epoch)
        marking.bind_active_claim(
            local_key, firing.transition_firing_ref)
        for authority in sorted(resource_accesses, key=lambda item: (
                str(item.access.resource_ref.resource_id),
                str(item.access.resource_ref.resource_version_id),
                item.access.access_mode)):
            # The registration checkpoint is immutable provenance.  A
            # disjoint sibling Success may advance the current checkpoint;
            # apply_firing_resource_access re-derives the exact token and arc
            # against this predecessor before changing the local marking.
            if (authority.net_ref != executable.net_ref
                    or authority.access.firing_ref
                    != firing.transition_firing_ref
                    or authority.access.transition_id
                    != firing.transition_id
                    or authority.access.claim_epoch != claim_epoch):
                raise MarkingStateError(
                    "resource access differs from the Success firing closure")
            marking.apply_firing_resource_access(authority.access)
    produced = [{
        "place": output.place,
        "resource_ref": output.resource_ref,
        "output_port_id": output.port_id,
        "output_place_ref": output.place_ref,
        "output_binding_ref": output.output_binding_ref,
        "work_resource_ref": output.work_resource_ref,
        "kind": output.kind,
        "verdict": output.verdict,
        "continuation": (None if output.continuation is None else {
            "round": output.continuation.round,
            "source_ref": output.continuation.source_ref,
        }),
        "lease_claims": tuple({
            "lease_identity_ref": claim.lease_identity_ref,
            "expected_resource_ref": claim.expected_resource_ref,
            "access_mode": claim.access_mode,
            **({"staging_place": claim.staging_place}
               if claim.staging_place is not None else {}),
        } for claim in output.lease_claims),
    } for output in outputs.outputs]
    for route in (() if effects is None else effects.routes):
        produced.append({"place": route.place, "resource_ref": route.source.resource_ref,
            "output_port_id": None, "output_place_ref": None, "output_binding_ref": None,
            "work_resource_ref": None, "kind": None, "verdict": route.colour,
            "continuation": None, "lease_claims": (), "selected_route_occurrence": True})
    for selected_output in (
            () if effects is None else effects.selected_outputs):
        produced.append({
            "place": selected_output.place,
            "resource_ref": None,
            "output_port_id": None,
            "output_place_ref": None,
            "output_binding_ref": None,
            "work_resource_ref": None,
            "kind": None,
            "verdict": selected_output.colour,
            "continuation": None,
            "lease_claims": (),
            "selected_control_occurrence": True,
        })
    declared = set(selected.outputs_of(firing.transition_id))
    represented = {item["place"] for item in produced}
    if represented - declared:
        raise MarkingStateError("registered Module output is outside selected PN outcome")
    for place in sorted(declared - represented):
        emit = selected.output_emit_for(firing.transition_id, place)
        if selected.output_effect_selector_for(
                firing.transition_id, place) is not None:
            continue
        if emit not in {
                "content_less", "control_only", "forward", "route_selected",
                "lease_mint"}:
            raise MarkingStateError("selected Module data output lacks registered resource")
        if emit in {"route_selected", "lease_mint"}:
            continue
        if emit == "forward":
            source = selected.forward_source_place(firing.transition_id, place)
            carriers = [item.state for item in predecessor.tokens
                        if item.token_ref in claimed and item.state.place == source]
            if len(carriers) != selected.output_arc_weight(firing.transition_id, place):
                raise MarkingStateError("forward output lacks its exact claimed source multiplicity")
            if any(carrier.lease_identity_ref is not None for carrier in carriers):
                raise MarkingStateError("ordinary forward cannot transfer reusable lease capacity")
        produced.append({
            "place": place, "resource_ref": None, "output_port_id": None,
            "output_place_ref": None, "output_binding_ref": None,
            "work_resource_ref": None, "kind": None, "verdict": None,
            "continuation": None, "lease_claims": (),
        })
    _deposited, off_arc = marking.deposit_outputs(
        firing.transition_id, produced, claim_epoch=claim_epoch)
    if off_arc:
        raise MarkingStateError("Module projection deposited an undeclared output")
    marking.record_settled_attempt(firing.transition_id, firing.attempt_index)
    # This invokes Core's local PN, colour, identity and exact-ref validators.
    snapshot = marking.pure_typed_snapshot(executable)
    capacities = {place.name: place.capacity for place in selected.compiled.symbolic.places}
    occupied = Counter(state.place for state in snapshot.tokens)
    if any(capacities[place] is not None and count > capacities[place]
           for place, count in occupied.items()):
        raise MarkingStateError("Module Success successor exceeds declared place capacity")

    prior_by_ref = {item.token_ref: item for item in predecessor.tokens}
    prior_ids = {item.state.token_id for item in predecessor.tokens}
    tokens = []
    new_tokens = []
    for state in snapshot.tokens:
        ref = state.token_ref
        if not isinstance(ref, VersionRef):
            raise MarkingStateError("Core Module projection lacks an exact token reference")
        prior = prior_by_ref.get(ref)
        if prior is not None:
            if prior.state != state:
                raise MarkingStateError("Module projection rewrites a retained token")
            tokens.append(prior)
        else:
            if state.token_id in prior_ids:
                raise MarkingStateError("Module projection remints a predecessor token id")
            new_tokens.append((ref, state))
            tokens.append(PetriTokenAuthority(ref, state, outputs.verified_at_head))

    def ref_key(ref: VersionRef) -> tuple[str, str, str]:
        return ref.entity_type, str(ref.entity_id), str(ref.version_id)

    token_refs = tuple(sorted((item.token_ref for item in tokens), key=ref_key))
    if len(token_refs) != len(set(token_refs)):
        raise MarkingStateError("Module projection repeats an exact token reference")
    projected = replace(
        predecessor,
        epoch=snapshot.epoch,
        next_token_id=snapshot.next_token_id,
        attempts=snapshot.attempts,
        token_refs=token_refs,
        tokens=tuple(tokens),
        transition_firing_refs=(*predecessor.transition_firing_refs,
                                firing.transition_firing_ref),
        verified_at_head=outputs.verified_at_head,
    )
    return projected, tuple(sorted(new_tokens, key=lambda item: ref_key(item[0])))


__all__ = ("project_module_firing_success",)
