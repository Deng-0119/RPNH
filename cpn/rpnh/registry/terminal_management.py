"""Stage one terminal successor inside the original normal Success transaction.

All prospective material is checked again at EventStore's final write cut.
This helper never commits, completes a firing, releases claims, or publishes a
second authority to repair a previously committed checkpoint carry.
"""
from __future__ import annotations

from copy import deepcopy

from .event_store import RegistryConflict
from .identities import new_id
from .models import VersionRef, TypedRelation
from .publication import _version_from_payload
from .schema_catalog import canonical_json
from .strict_contracts import ref_payload
from ._event_store.material_settlement import material_admission
from ._event_store.terminal_management import (
    terminal_management_scope, require_single_terminal_firing, validate_terminal_successor)


def require_terminal_management_admission(core, *, transition_id, owner_command_ref):
    """Preflight only; the original admission write must repeat this proof."""
    from ._event_store.material_control import read_accepted_material_command
    if owner_command_ref is None:
        raise RegistryConflict("TARGET_UNAVAILABLE: terminal management requires an original accepted command")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        command = read_accepted_material_command(core.event_store, db, core.task_id,
            ref_payload(owner_command_ref), current=True)
        if command["target"] != transition_id:
            raise RegistryConflict("TARGET_UNAVAILABLE: terminal admission differs from its original material command")
        result = terminal_management_scope(core.event_store, db, core.task_id, target=transition_id,
            operation=command["data"].get("operation"), source=command["source"], execution=command["data"]["execution"])
        require_single_terminal_firing(core.event_store, db, core.task_id)
        return result["authority"]["run_execution_authority_ref"]


def stage_terminal_management_checkpoint(core, kernel, transaction, checkpoint_ref, *,
        idempotency_key, producer_invocation_id):
    """Stage A1/E1/I1 beside the already staged original C1/completion."""
    from .run_authority import current_run_execution_authority
    authority_ref, authority = current_run_execution_authority(core, kernel)
    if authority.get("status") != "terminal":
        return False
    if (not isinstance(checkpoint_ref, VersionRef) or checkpoint_ref.entity_type != "marking_checkpoint/v1"
            or producer_invocation_id is None or not isinstance(idempotency_key, str) or not idempotency_key
            or transaction.event_store is not core.event_store or transaction.writer_epoch != core.writer_epoch):
        raise RegistryConflict("terminal checkpoint requires the original owner Success transaction")
    if any(item.object_type in {"run_execution_authority/v1", "run_terminal_evidence/v1", "final_result_index/v1"}
            for item in transaction._objects):
        raise RegistryConflict("terminal Success cannot stage a second authority or repair closure")
    settlements = [event for event in transaction._events if event.event_type == "transition_firing_settled/v1"]
    if len(settlements) != 1 or len(transaction._firing_publications) != 1:
        raise RegistryConflict("terminal checkpoint requires one original normal Success")
    settled = settlements[0].payload
    if settled["successor_checkpoint_ref"] != ref_payload(checkpoint_ref):
        raise RegistryConflict("terminal checkpoint differs from its original settlement")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        found = material_admission(core.event_store, db, core.task_id,
            settled["transition_firing_ref"], current=True)
        if found is None:
            raise RegistryConflict("terminal checkpoint cannot carry an ordinary business firing")
        admission, command = found
        if admission["invocation_ref"]["logical_id"] != str(producer_invocation_id):
            raise RegistryConflict("terminal checkpoint is outside its original material responsibility")
        value = terminal_management_scope(core.event_store, db, core.task_id, target=command["target"],
            operation=command["data"].get("operation"), source=command["source"], execution=command["data"]["execution"])
        require_single_terminal_firing(core.event_store, db, core.task_id,
            allowed_firing_ref=settled["transition_firing_ref"])
        if value["authority"] != authority:
            raise RegistryConflict("terminal predecessor advanced while preparing its successor")
        physical_head = value["snapshot"].current_cut
    if transaction._expected_registry_ordinal is None:
        transaction.expect_registry_ordinal(physical_head)
    elif transaction._expected_registry_ordinal != physical_head:
        raise RegistryConflict("terminal Success has a different original preflight cut")
    index_ref = _version_from_payload(value["index"]["final_result_index_ref"])
    evidence_ref = _version_from_payload(value["evidence"]["terminal_evidence_ref"])
    next_index = VersionRef(index_ref.entity_type, index_ref.entity_id, new_id("terminal_evidence_version"))
    next_evidence = VersionRef(evidence_ref.entity_type, evidence_ref.entity_id, new_id("terminal_evidence_version"))
    next_authority = VersionRef(authority_ref.entity_type, authority_ref.entity_id, new_id("run_execution_authority_version"))
    index = {**deepcopy(value["index"]), "final_result_index_ref": ref_payload(next_index)}
    evidence = {**deepcopy(value["evidence"]), "terminal_evidence_ref": ref_payload(next_evidence),
        "final_result_index_ref": ref_payload(next_index), "final_checkpoint_ref": ref_payload(checkpoint_ref)}
    successor = {**deepcopy(authority), "run_execution_authority_ref": ref_payload(next_authority),
        "latest_checkpoint_ref": ref_payload(checkpoint_ref), "terminal_evidence_ref": ref_payload(next_evidence)}
    for ref, document in ((next_index, index), (next_evidence, evidence), (next_authority, successor)):
        core.catalog.validate_instance(ref.entity_type, category="object", instance=document)
        transaction.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(document), metadata=document, media_type="application/json",
            schema_ref="registry_v1/" + ref.entity_type, producer_invocation_id=producer_invocation_id)
    occurrence = _version_from_payload(evidence["terminal_occurrence_ref"])
    product = _version_from_payload(evidence["terminal_result_ref"])
    for source, targets in ((next_index, (index_ref, occurrence, product)),
            (next_evidence, (evidence_ref, occurrence, product, checkpoint_ref, next_index)),
            (next_authority, (authority_ref, checkpoint_ref, next_evidence))):
        for target in targets:
            transaction.relate(TypedRelation(new_id("relation"), "derived_from", source, target),
                producer_invocation_id=producer_invocation_id)
    # Validation here diagnoses a candidate without pretending it is already
    # published. The same function must run again under the native write lock.
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        validate_terminal_successor(core.event_store, db, task_id=core.task_id,
            objects=tuple(transaction._objects), events=tuple(transaction._events),
            publication_commands=tuple(transaction._firing_publications))
    return True


__all__ = ("require_terminal_management_admission", "stage_terminal_management_checkpoint")
