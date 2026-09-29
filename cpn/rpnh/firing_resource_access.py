"""Formal occurrence-local resource arcs for an already active firing.

An external component chooses only an exact immutable resource version and the
semantic access mode ``read`` or ``edit``.  This module derives every Petri
identity and arc inscription from the adopted net and current typed marking.
It performs no resource delivery, Registry publication, queueing, or workflow
policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .marking import (
    MarkingStateError,
    PetriFiringResourceAccess,
    TeamNetMarking,
)
from .registry.models import VersionRef
from .registry.publication import (
    _ref_payload,
    _resource_from_payload,
    _version_from_payload,
)
from .registry.resources import (
    ResourceVersionRef,
    TransitionFiringAuthority,
    TypedMarkingAuthority,
)
from .runtime_net import RuntimeNet


def _resource_payload(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }


@dataclass(frozen=True, slots=True)
class RegisteredPetriFiringResourceAccess:
    """One pure Petri access derivation closed over its exact checkpoint."""

    checkpoint_ref: VersionRef
    net_ref: VersionRef
    access: PetriFiringResourceAccess

    def __post_init__(self) -> None:
        if (not isinstance(self.checkpoint_ref, VersionRef)
                or self.checkpoint_ref.entity_type != "marking_checkpoint/v1"
                or not isinstance(self.net_ref, VersionRef)
                or self.net_ref.entity_type != "net_instance/v1"
                or not isinstance(self.access, PetriFiringResourceAccess)):
            raise TypeError(
                "registered Petri firing resource access is incomplete")


def derive_registered_firing_resource_access(
        structure: RuntimeNet,
        marking: TypedMarkingAuthority,
        firing: TransitionFiringAuthority,
        requested: ResourceVersionRef,
        access_mode: str,
        *,
        existing: Sequence[RegisteredPetriFiringResourceAccess] = (),
) -> RegisteredPetriFiringResourceAccess:
    """Derive one arc after replaying this firing's prior formal accesses."""

    if (not isinstance(structure, RuntimeNet)
            or not isinstance(marking, TypedMarkingAuthority)
            or not isinstance(firing, TransitionFiringAuthority)
            or marking.net_ref != structure.registry_net_ref
            or firing.net_ref != marking.net_ref
            or not isinstance(requested, ResourceVersionRef)
            or access_mode not in {"read", "edit"}
            or not isinstance(existing, Sequence)
            or isinstance(existing, (str, bytes))
            or any(not isinstance(
                item, RegisteredPetriFiringResourceAccess)
                for item in existing)):
        raise MarkingStateError(
            "registered resource access requires one exact net/marking/firing")
    local = TeamNetMarking.from_authority(structure, marking)
    claim_epoch = local.install_registered_firing_claim(
        firing.transition_id, firing.claimed_input_refs)
    local_key = local.active_claim_key(firing.transition_id, claim_epoch)
    local.bind_active_claim(local_key, firing.transition_firing_ref)
    ordered = tuple(sorted(existing, key=lambda item: (
        str(item.access.resource_ref.resource_id),
        str(item.access.resource_ref.resource_version_id),
        item.access.access_mode,
    )))
    for prior in ordered:
        # A disjoint sibling may settle after this access was registered.  The
        # access checkpoint remains immutable provenance; applicability is
        # re-established below by replaying the exact token/arc against the
        # latest same-net marking.
        if (prior.net_ref != marking.net_ref
                or prior.access.firing_ref
                != firing.transition_firing_ref
                or prior.access.transition_id != firing.transition_id
                or prior.access.claim_epoch != claim_epoch):
            raise MarkingStateError(
                "prior resource access differs from the active firing closure")
        local.apply_firing_resource_access(prior.access)
    access = local.derive_firing_resource_access(
        firing.transition_id,
        claim_epoch,
        firing.transition_firing_ref,
        requested,
        access_mode,
    )
    return RegisteredPetriFiringResourceAccess(
        checkpoint_ref=marking.checkpoint_ref,
        net_ref=marking.net_ref,
        access=access,
    )


def registered_firing_resource_access_payload(
        authority: RegisteredPetriFiringResourceAccess,
) -> dict[str, Any]:
    """Encode the complete formal arc witness for Registry event closure."""

    if not isinstance(authority, RegisteredPetriFiringResourceAccess):
        raise TypeError("resource access payload requires typed authority")
    access = authority.access
    return {
        "access_checkpoint_ref": _ref_payload(authority.checkpoint_ref),
        "access_net_ref": _ref_payload(authority.net_ref),
        "access_claim_epoch": access.claim_epoch,
        "resource_token_ref": _ref_payload(access.resource_token_ref),
        "lease_pool_place": access.lease_pool_place,
        "lease_identity_ref": _ref_payload(access.lease_identity_ref),
        "petri_input_arc_mode": access.input_arc_mode,
        "petri_output_arc_mode": access.output_arc_mode,
    }


def registered_firing_resource_access_from_payload(
        payload: Mapping[str, Any],
) -> RegisteredPetriFiringResourceAccess:
    """Decode a closed Registry event back to the typed formal authority."""

    if not isinstance(payload, Mapping):
        raise TypeError("resource access payload must be a mapping")
    try:
        access_mode = str(payload["access_mode"])
        output_mode = payload["petri_output_arc_mode"]
        access = PetriFiringResourceAccess(
            firing_ref=_version_from_payload(
                payload["transition_firing_ref"]),
            transition_id=str(payload["transition_id"]),
            claim_epoch=int(payload["access_claim_epoch"]),
            lease_pool_place=str(payload["lease_pool_place"]),
            resource_token_ref=_version_from_payload(
                payload["resource_token_ref"]),
            lease_identity_ref=_version_from_payload(
                payload["lease_identity_ref"]),
            resource_ref=_resource_from_payload(payload["resource_ref"]),
            access_mode=access_mode,
            input_arc_mode=str(payload["petri_input_arc_mode"]),
            output_arc_mode=(
                None if output_mode is None else str(output_mode)),
        )
        authority = RegisteredPetriFiringResourceAccess(
            checkpoint_ref=_version_from_payload(
                payload["access_checkpoint_ref"]),
            net_ref=_version_from_payload(payload["access_net_ref"]),
            access=access,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarkingStateError(
            "registered resource access payload is malformed") from exc
    if (payload.get("resource_ref")
            != _resource_payload(authority.access.resource_ref)):
        raise MarkingStateError(
            "registered resource access payload changes its resource")
    return authority


def verify_registered_firing_resource_access_conflicts(
        core: Any,
        authority: RegisteredPetriFiringResourceAccess,
) -> None:
    """Reject a granted arc that conflicts with any other active firing.

    Static firing inputs and later firing-local arcs share one compatibility
    relation: read/read is compatible; every access involving an edit is not.
    The exact resource token, rather than a component label, is the conflict
    key.
    """

    from .registry.module_execution import active_module_firings
    from .registry.publication import (
        _effective_firing_resource_access_events,
    )

    if not isinstance(authority, RegisteredPetriFiringResourceAccess):
        raise MarkingStateError(
            "resource conflict verification requires typed authority")
    access = authority.access
    active = active_module_firings(core, authority.net_ref)
    by_ref = {
        firing.transition_firing_ref: firing for firing in active}
    if access.firing_ref not in by_ref:
        raise MarkingStateError(
            "resource access firing is no longer active")

    requested_token = access.resource_token_ref
    for firing in active:
        if firing.transition_firing_ref == access.firing_ref:
            continue
        delta = core.get_version(
            firing.claim_marking_delta_ref.version_id)
        if (delta.object_type != "marking_delta/v1"
                or delta.logical_id
                != firing.claim_marking_delta_ref.entity_id
                or delta.metadata.get("phase") != "claim"):
            raise MarkingStateError(
                "active firing lacks its exact claim marking delta")
        try:
            consumed = {
                _version_from_payload(item)
                for item in delta.metadata["consumed_refs"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise MarkingStateError(
                "active firing claim delta has malformed consumed refs") from exc
        claimed = set(firing.claimed_input_refs)
        if not consumed.issubset(claimed):
            raise MarkingStateError(
                "active firing consumed refs exceed its exact claims")
        if (requested_token in consumed
                or (access.access_mode == "edit"
                    and requested_token in claimed)):
            raise MarkingStateError(
                "resource access conflicts with another active firing claim")

    active_refs = set(by_ref)
    events_by_firing: dict[VersionRef, list[Any]] = {}
    for event in core.event_store.list_events_by_type(
            ("petri_firing_resource_accessed/v1",)):
        if event.writer_fencing_epoch != core.writer_epoch:
            continue
        try:
            firing_ref = _version_from_payload(
                event.payload["transition_firing_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MarkingStateError(
                "live resource access event has malformed firing identity") from exc
        if firing_ref in active_refs and firing_ref != access.firing_ref:
            events_by_firing.setdefault(firing_ref, []).append(event)
    for events in events_by_firing.values():
        for event in _effective_firing_resource_access_events(events):
            other = registered_firing_resource_access_from_payload(
                event.payload).access
            if (other.resource_token_ref == requested_token
                    and (access.access_mode == "edit"
                         or other.access_mode == "edit")):
                raise MarkingStateError(
                    "resource access conflicts with another firing-local arc")


__all__ = (
    "RegisteredPetriFiringResourceAccess",
    "derive_registered_firing_resource_access",
    "registered_firing_resource_access_from_payload",
    "registered_firing_resource_access_payload",
    "verify_registered_firing_resource_access_conflicts",
)
