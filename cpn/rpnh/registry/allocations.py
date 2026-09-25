"""Sole durable firing-allocation publisher and exact current UUID identity.

Called under the execution owner's runtime lock. No scheduling, hydration,
callable execution or alternate writer is supplied by this module.
"""
from __future__ import annotations

import json
import uuid
from typing import Mapping, Sequence

from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault, StaleWriterFence
from .identities import TypedId
from .models import VersionRef
from .publication import _ref_payload
from .resource_service import _ResourceServiceKernel
from .resources import (
    ExecutableNetAuthority, TypedMarkingAuthority,
    PetriFiringAllocationAuthority, PetriFiringAllocationRecord,
)
from .schema_catalog import canonical_json


def _firing_allocation_identity_v1(
        checkpoint_ref: VersionRef,
        claim_epoch: int,
        writer_fencing_epoch: int,
        allocations: Sequence[
            tuple[int | VersionRef, str, int, tuple[VersionRef, ...]]],
) -> tuple[VersionRef, str]:
    """Identify one exact allocation inside one current writer generation."""

    if (not isinstance(checkpoint_ref, VersionRef)
            or checkpoint_ref.entity_type != "marking_checkpoint/v1"
            or isinstance(claim_epoch, bool)
            or not isinstance(claim_epoch, int)
            or claim_epoch < 0
            or isinstance(writer_fencing_epoch, bool)
            or not isinstance(writer_fencing_epoch, int)
            or writer_fencing_epoch < 1
            or not allocations):
        raise ResourceIntegrityFault(
            "firing allocation identity lacks current writer authority")
    occurrence_key = json.dumps([{
        "local_key": (local_key if isinstance(local_key, int)
                      else _ref_payload(local_key)),
        "transition_id": transition_id,
        "claim_epoch": item_epoch,
        "token_refs": [_ref_payload(ref) for ref in token_refs],
    } for local_key, transition_id, item_epoch, token_refs in allocations],
        sort_keys=True, separators=(",", ":"))
    identity_scope = (
        f"firing-allocation:{checkpoint_ref}:{claim_epoch}:"
        f"writer-epoch:{writer_fencing_epoch}:{occurrence_key}")
    return VersionRef(
        "firing_allocation/v1",
        TypedId("firing_admission", uuid.uuid5(
            uuid.NAMESPACE_URL, identity_scope).hex),
        TypedId("firing_admission_version", uuid.uuid5(
            uuid.NAMESPACE_URL, f"{identity_scope}:version").hex),
    ), occurrence_key


def register_petri_firing_allocation(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        executable: ExecutableNetAuthority,
        marking: TypedMarkingAuthority, evidence: object, *,
        idempotency_key: str,
) -> PetriFiringAllocationAuthority:
    """Persist the actual eligible queue before any member admission."""

    if (not isinstance(core, _RegistryCore)
            or not isinstance(kernel, _ResourceServiceKernel)
            or not isinstance(executable, ExecutableNetAuthority)
            or not isinstance(marking, TypedMarkingAuthority)
            or marking.net_ref != executable.net_ref
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError(
            "firing allocation requires exact net/marking/key authority")
    claim_epoch = getattr(evidence, "claim_epoch", None)
    eligible_queue = tuple(getattr(evidence, "eligible_queue", ()))
    raw_allocations = tuple(getattr(evidence, "allocations", ()))
    seed = getattr(evidence, "scheduler_seed", None)
    if (isinstance(claim_epoch, bool)
            or not isinstance(claim_epoch, int)
            or claim_epoch != marking.epoch
            or not eligible_queue or not raw_allocations
            or (seed is not None and (
                isinstance(seed, bool) or not isinstance(seed, int)))):
        raise ResourceIntegrityFault(
            "firing allocation evidence differs from current marking")
    transition_by_id = {
        item.transition_id: item for item in executable.transitions}
    observed_head = kernel._head()
    if observed_head.writer_fencing_epoch != core.writer_epoch:
        raise StaleWriterFence(
            "firing allocation observed a stale writer fence")

    # Resolve the exact adopted typed PN once.  The pure firing envelope below
    # must pass before this function publishes an allocation or reserves a
    # durable attempt.
    from .module_runtime import hydrate_module_runtime
    current_executable, structure, current_marking = hydrate_module_runtime(core)
    if current_executable != executable or current_marking != marking:
        raise ResourceIntegrityFault(
            "firing allocation differs from current Module authority")

    zero_input_transitions: set[str] | None = None

    def permits_empty_claim(transition_id: str) -> bool:
        # DTO/schema emptiness alone confers no PN authority. Resolve the
        # actual adopted public declaration and current typed checkpoint only
        # when an empty claim is presented; token-bearing allocation is unchanged.
        nonlocal zero_input_transitions
        if zero_input_transitions is None:
            from ..marking import TeamNetMarking
            local = TeamNetMarking.from_authority(structure, current_marking)
            zero_input_transitions = {
                name for name in structure.transitions
                if not structure.inputs_of(name) and local.is_enabled(name)
            }
        return transition_id in zero_input_transitions

    queue_authorities: list[tuple[str, tuple[VersionRef, ...]]] = []
    for item in eligible_queue:
        if (not isinstance(item, tuple) or len(item) != 2
                or not isinstance(item[0], str)):
            raise ResourceIntegrityFault(
                "eligible firing queue is malformed")
        token_refs = tuple(item[1])
        if (any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "petri_token/v1"
                       for ref in token_refs)
                or item[0] not in transition_by_id
                or (not token_refs and not permits_empty_claim(item[0]))):
            raise ResourceIntegrityFault(
                "eligible queue lacks exact transition/token authority")
        queue_authorities.append((item[0], token_refs))

    raw_authorities: list[tuple[
        int | VersionRef, str, int, tuple[VersionRef, ...]]] = []
    for item in raw_allocations:
        local_key = getattr(item, "local_key", None)
        transition_id = getattr(item, "transition_id", None)
        item_epoch = getattr(item, "claim_epoch", None)
        token_refs = tuple(getattr(item, "token_refs", ()))
        transition = transition_by_id.get(transition_id)
        if ((isinstance(local_key, bool)
                or not isinstance(local_key, (int, VersionRef)))
                or transition is None or item_epoch != claim_epoch
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "petri_token/v1"
                       for ref in token_refs)
                or (not token_refs and not permits_empty_claim(transition_id))
                or (transition_id, token_refs) not in queue_authorities):
            raise ResourceIntegrityFault(
                "allocated occurrence differs from eligible authority")
        raw_authorities.append((
            local_key, transition_id, item_epoch, token_refs))
    allocation_ref, allocation_occurrence_key = (
        _firing_allocation_identity_v1(
            marking.checkpoint_ref,
            claim_epoch,
            observed_head.writer_fencing_epoch,
            raw_authorities,
        ))
    logical_id = allocation_ref.entity_id
    version_id = allocation_ref.version_id
    existing = core.event_store.object_row(version_id)
    if existing is not None:
        registered = kernel._exact_object(
            allocation_ref, expected_type="firing_allocation/v1")
        stored = dict(registered.metadata)
        core.catalog.validate_instance(
            "firing_allocation/v1", category="object", instance=stored)
        stored_allocations = stored.get("allocations")
        evidence_document = {
            "net_ref": _ref_payload(executable.net_ref),
            "checkpoint_ref": _ref_payload(marking.checkpoint_ref),
            "claim_epoch": claim_epoch,
            "eligible_queue": [{
                "transition_id": transition_id,
                "token_refs": [_ref_payload(ref) for ref in token_refs],
            } for transition_id, token_refs in queue_authorities],
        }
        if (registered.logical_id != logical_id
                or registered.version_id != version_id
                or any(stored.get(name) != value
                       for name, value in evidence_document.items())
                or not isinstance(stored_allocations, list)
                or len(stored_allocations) != len(raw_authorities)):
            raise ResourceIntegrityFault(
                "persisted firing allocation differs from current evidence")
        allocation_authorities = []
        for stored_item, raw_item in zip(
                stored_allocations, raw_authorities, strict=True):
            local_key, transition_id, item_epoch, token_refs = raw_item
            expected = {
                "local_key": (local_key if isinstance(local_key, int)
                              else _ref_payload(local_key)),
                "transition_id": transition_id,
                "claim_epoch": item_epoch,
                "token_refs": [_ref_payload(ref) for ref in token_refs],
            }
            if (not isinstance(stored_item, Mapping)
                    or any(stored_item.get(name) != value
                           for name, value in expected.items())):
                raise ResourceIntegrityFault(
                    "persisted firing allocation occurrence changed")
            allocation_authorities.append(PetriFiringAllocationRecord(
                local_key=local_key,
                transition_id=transition_id,
                claim_epoch=item_epoch,
                attempt_index=stored_item.get("attempt_index"),
                token_refs=token_refs,
            ))
        return PetriFiringAllocationAuthority(
            net_ref=executable.net_ref,
            checkpoint_ref=marking.checkpoint_ref,
            claim_epoch=claim_epoch,
            eligible_queue=tuple(queue_authorities),
            allocations=tuple(allocation_authorities),
        )

    from ..firing_preflight import preflight_module_firing_set
    from .module_execution import active_module_firings
    active_occurrences = tuple(
        (firing.transition_id, firing.claimed_input_refs)
        for firing in active_module_firings(core, executable.net_ref))
    new_occurrences = tuple(
        (transition_id, token_refs)
        for _local_key, transition_id, _item_epoch, token_refs
        in raw_authorities)
    preflight_module_firing_set(
        structure,
        marking,
        occurrences=(*active_occurrences, *new_occurrences),
    )

    reserved = {
        item.transition_id: item.highest_issued
        for item in marking.attempts}
    for row in core.event_store.object_rows_by_type(
            "firing_allocation/v1"):
        try:
            prior = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResourceIntegrityFault(
                "durable firing allocation reservation is malformed") from exc
        if prior.get("net_ref") != _ref_payload(executable.net_ref):
            continue
        prior_allocations = prior.get("allocations")
        if not isinstance(prior_allocations, list):
            raise ResourceIntegrityFault(
                "durable firing allocation lacks ordered reservations")
        for prior_item in prior_allocations:
            if not isinstance(prior_item, Mapping):
                raise ResourceIntegrityFault(
                    "durable firing reservation is malformed")
            prior_transition = prior_item.get("transition_id")
            prior_attempt = prior_item.get("attempt_index")
            if (not isinstance(prior_transition, str)
                    or isinstance(prior_attempt, bool)
                    or not isinstance(prior_attempt, int)
                    or prior_attempt <= 0):
                raise ResourceIntegrityFault(
                    "durable firing reservation has no positive ordinal")
            reserved[prior_transition] = max(
                reserved.get(prior_transition, 0), prior_attempt)
    allocation_authorities: list[PetriFiringAllocationRecord] = []
    for local_key, transition_id, item_epoch, token_refs in raw_authorities:
        attempt_index = reserved.get(transition_id, 0) + 1
        reserved[transition_id] = attempt_index
        allocation_authorities.append(PetriFiringAllocationRecord(
            local_key=local_key,
            transition_id=transition_id,
            claim_epoch=item_epoch,
            attempt_index=attempt_index,
            token_refs=token_refs,
        ))
    document = {
        "net_ref": _ref_payload(executable.net_ref),
        "checkpoint_ref": _ref_payload(marking.checkpoint_ref),
        "claim_epoch": claim_epoch,
        "eligible_queue": [{
            "transition_id": transition_id,
            "token_refs": [_ref_payload(ref) for ref in token_refs],
        } for transition_id, token_refs in queue_authorities],
        "allocations": [{
            "local_key": (item.local_key
                          if isinstance(item.local_key, int)
                          else _ref_payload(item.local_key)),
            "transition_id": item.transition_id,
            "claim_epoch": item.claim_epoch,
            "attempt_index": item.attempt_index,
            "token_refs": [
                _ref_payload(ref) for ref in item.token_refs],
        } for item in allocation_authorities],
    }
    core.catalog.validate_instance(
        "firing_allocation/v1", category="object", instance=document)
    tx = core.begin(idempotency_key=(
        f"{idempotency_key}:writer-epoch:"
        f"{observed_head.writer_fencing_epoch}:"
        f"occurrence:{allocation_occurrence_key}"))
    tx.prewrite(
        object_type="firing_allocation/v1",
        logical_id=logical_id, version_id=version_id,
        payload=canonical_json(document), metadata=document,
        media_type="application/json",
        schema_ref="registry_v1/firing_allocation/v1")
    tx.commit()
    if core.writer_epoch != observed_head.writer_fencing_epoch:
        raise StaleWriterFence(
            "writer fence changed during firing allocation registration")
    return PetriFiringAllocationAuthority(
        net_ref=executable.net_ref,
        checkpoint_ref=marking.checkpoint_ref,
        claim_epoch=claim_epoch,
        eligible_queue=tuple(queue_authorities),
        allocations=tuple(allocation_authorities),
    )


__all__ = ("register_petri_firing_allocation",)
