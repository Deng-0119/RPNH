"""Authoritative transaction-domain validation."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ... import event_store as facade

def validate_native_resume_closure(context) -> None:
    branch_id = context.branch_id
    db = context.db
    events = context.events
    idempotency_key = context.idempotency_key
    net_instance_id = context.net_instance_id
    objects = context.objects
    task_id = context.task_id
    exact_ref_exists = context.exact_ref_exists
    has_new_relation = context.has_new_relation
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    _CANONICAL_EVENT_SQL = facade._CANONICAL_EVENT_SQL
    canonical_json = facade.canonical_json
    canonical_text = facade.canonical_text
    """Require one atomic old-to-current-writer pre-effect re-fence."""

    resume_event_types = {
        "native_resume_refenced/v1",
        "invocation_superseded_by_native_resume/v1",
        "transition_firing_superseded_by_native_resume/v1",
        "operation_dispatch_superseded_by_native_resume/v1",
    }
    resume_events = [
        value for value in events
        if value.event_type in resume_event_types]
    resume_objects = [
        value for value in objects
        if value.object_type == "native_resume_authority/v1"]
    if not resume_events and not resume_objects:
        return
    by_type = {
        event_type: [
            value for value in resume_events
            if value.event_type == event_type]
        for event_type in resume_event_types
    }
    if (len(resume_objects) != 1
            or any(len(by_type[event_type]) != 1
                   for event_type in resume_event_types)):
        raise RegistryConflict(
            "native resume requires one mapping and one exact fact quartet")

    item = resume_objects[0]
    metadata = dict(item.metadata)
    mapping_ref = metadata.get("native_resume_authority_ref")
    expected_mapping_ref = {
        "entity_type": "native_resume_authority/v1",
        "logical_id": str(item.logical_id),
        "version_id": str(item.version_id),
    }
    mapping = metadata.get("mapping")
    if not isinstance(mapping, Mapping):
        raise RegistryConflict("native resume mapping is absent")
    mapping_digest = canonical_text(mapping)
    old_specs = (
        ("superseded_invocation_ref", "invocation/v1"),
        ("superseded_transition_firing_ref", "transition_firing/v1"),
        ("superseded_operation_execution_lease_ref",
         "operation_execution_lease/v1"),
        ("superseded_firing_admission_ref", "firing_admission/v1"),
    )
    new_specs = (
        ("replacement_invocation_ref", "invocation/v1"),
        ("replacement_transition_firing_ref", "transition_firing/v1"),
        ("replacement_operation_execution_lease_ref",
         "operation_execution_lease/v1"),
        ("replacement_firing_admission_ref", "firing_admission/v1"),
    )
    expected_mapping_fields = {
        name for name, _object_type in (*old_specs, *new_specs)}
    if (set(mapping) != expected_mapping_fields
            or any(not exact_ref_exists(mapping.get(name), object_type)
                   for name, object_type in (*old_specs, *new_specs))):
        raise RegistryConflict(
            "native resume mapping does not contain eight exact refs")
    old_refs = [mapping[name] for name, _object_type in old_specs]
    new_refs = [mapping[name] for name, _object_type in new_specs]
    if ({canonical_json(value) for value in old_refs}
            & {canonical_json(value) for value in new_refs}):
        raise RegistryConflict(
            "native resume replacement overlaps superseded authority")

    run_ref = metadata.get("run_identity_ref")
    recovery_ref = metadata.get("recovery_manifest_ref")
    net_ref = metadata.get("net_instance_ref")
    checkpoint_ref = metadata.get("marking_checkpoint_ref")
    writer_row = db.execute(
        "SELECT value FROM registry_meta WHERE key='writer_epoch'",
    ).fetchone()
    manifest_pointer = db.execute(
        "SELECT value FROM registry_meta WHERE "
        "key='task_recovery_manifest_version_id'",
    ).fetchone()
    active_row = db.execute(
        "SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.event_type='net_adopted/v1' AND "
        f"{_CANONICAL_EVENT_SQL} "
        "ORDER BY task_control_sequence DESC LIMIT 1",
        (str(task_id),),
    ).fetchone()
    marking_row = db.execute(
        "SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.net_instance_id=? "
        "AND e.event_type='marking_checkpoint_committed/v1' AND "
        f"{_CANONICAL_EVENT_SQL} "
        "ORDER BY task_control_sequence DESC LIMIT 1",
        (str(task_id), str((net_ref or {}).get("logical_id", ""))),
    ).fetchone()
    run = version_metadata(
        str((run_ref or {}).get("version_id", "")),
        "native_run_identity/v1") if isinstance(run_ref, Mapping) else None
    recovery = version_metadata(
        str((recovery_ref or {}).get("version_id", "")),
        "task_recovery_manifest/v1") if isinstance(
            recovery_ref, Mapping) else None
    active = (json.loads(active_row["payload_json"])
              if active_row is not None else None)
    marking_head = (json.loads(marking_row["payload_json"])
                    if marking_row is not None else None)
    current_epoch = int(writer_row["value"]) if writer_row else -1
    if (mapping_ref != expected_mapping_ref
            or metadata.get("mapping_digest") != mapping_digest
            or not exact_ref_exists(run_ref, "native_run_identity/v1")
            or run is None
            or run.get("task_ref", {}).get("logical_id") != str(task_id)
            or not exact_ref_exists(
                recovery_ref, "task_recovery_manifest/v1")
            or recovery is None
            or recovery.get("task_id") != str(task_id)
            or recovery.get("branch_id") != branch_id
            or manifest_pointer is None
            or recovery_ref.get("version_id")
            != str(manifest_pointer["value"])
            or not exact_ref_exists(net_ref, "net_instance/v1")
            or not exact_ref_exists(
                checkpoint_ref, "marking_checkpoint/v1")
            or active is None or active.get("net_instance_ref") != net_ref
            or marking_head is None
            or marking_head.get("checkpoint_ref") != checkpoint_ref
            or metadata.get("writer_fencing_epoch") != current_epoch
            or net_instance_id is None
            or str(net_instance_id) != net_ref.get("logical_id")):
        raise RegistryConflict(
            "native resume mapping is outside current run/recovery/net/marking/fence")

    old_invocation_ref = mapping["superseded_invocation_ref"]
    old_firing_ref = mapping["superseded_transition_firing_ref"]
    old_lease_ref = mapping[
        "superseded_operation_execution_lease_ref"]
    old_admission_ref = mapping["superseded_firing_admission_ref"]
    new_invocation_ref = mapping["replacement_invocation_ref"]
    new_firing_ref = mapping["replacement_transition_firing_ref"]
    new_lease_ref = mapping[
        "replacement_operation_execution_lease_ref"]
    new_admission_ref = mapping["replacement_firing_admission_ref"]
    old_invocation = version_metadata(
        str(old_invocation_ref["version_id"]), "invocation/v1")
    old_firing = version_metadata(
        str(old_firing_ref["version_id"]), "transition_firing/v1")
    old_lease = version_metadata(
        str(old_lease_ref["version_id"]),
        "operation_execution_lease/v1")
    old_admission = version_metadata(
        str(old_admission_ref["version_id"]), "firing_admission/v1")
    new_invocation = version_metadata(
        str(new_invocation_ref["version_id"]), "invocation/v1")
    new_firing = version_metadata(
        str(new_firing_ref["version_id"]), "transition_firing/v1")
    new_lease = version_metadata(
        str(new_lease_ref["version_id"]),
        "operation_execution_lease/v1")
    new_admission = version_metadata(
        str(new_admission_ref["version_id"]), "firing_admission/v1")
    old_claim_delta_ref = old_firing.get("claim_marking_delta_ref")
    new_claim_delta_ref = new_firing.get("claim_marking_delta_ref")
    old_claim_delta = version_metadata(
        str((old_claim_delta_ref or {}).get("version_id", "")),
        "marking_delta/v1") if isinstance(
            old_claim_delta_ref, Mapping) else None
    new_claim_delta = version_metadata(
        str((new_claim_delta_ref or {}).get("version_id", "")),
        "marking_delta/v1") if isinstance(
            new_claim_delta_ref, Mapping) else None
    if any(value is None for value in (
            old_invocation, old_firing, old_lease, old_admission,
            new_invocation, new_firing, new_lease, new_admission,
            old_claim_delta, new_claim_delta)):
        raise RegistryConflict(
            "native resume mapping endpoint object is absent")
    assert old_invocation is not None and new_invocation is not None
    assert old_firing is not None and new_firing is not None
    assert old_lease is not None and new_lease is not None
    assert old_admission is not None and new_admission is not None
    assert old_claim_delta is not None and new_claim_delta is not None

    def context_digest_valid(value: Mapping[str, Any]) -> bool:
        unsigned = dict(value)
        actual = unsigned.pop("context_digest", None)
        return actual == canonical_text(unsigned)

    invariant_invocation = {
        name for name in old_invocation
        if name not in {
            "invocation_ref", "operation_execution_lease_ref",
            "own_transition_firing_ref",
            "accounting_parent_invocation_ref", "context_digest",
        }
    }
    if (not context_digest_valid(old_invocation)
            or not context_digest_valid(new_invocation)
            or old_invocation.get("origin") != "petri_operation"
            or new_invocation.get("origin") != "petri_operation"
            or old_invocation.get("invocation_ref")
            != old_invocation_ref
            or old_invocation.get("own_transition_firing_ref")
            != old_firing_ref
            or old_invocation.get("operation_execution_lease_ref")
            != old_lease_ref
            or new_invocation.get("invocation_ref")
            != new_invocation_ref
            or new_invocation.get("own_transition_firing_ref")
            != new_firing_ref
            or new_invocation.get("operation_execution_lease_ref")
            != new_lease_ref
            or new_invocation.get("accounting_parent_invocation_ref")
            != new_invocation_ref
            or any(old_invocation.get(name)
                   != new_invocation.get(name)
                   for name in invariant_invocation)
            or old_invocation.get("net_instance_ref") != net_ref
            or old_invocation.get("admission_marking_checkpoint_ref")
            != checkpoint_ref
            or old_invocation.get("budget_witness_ref") != recovery_ref):
        raise RegistryConflict(
            "native resume replacement invocation changes immutable authority")

    old_firing_stable = {
        name: value for name, value in old_firing.items()
        if name not in {
            "transition_firing_ref", "firing_admission_ref",
            "claim_marking_delta_ref"}
    }
    new_firing_stable = {
        name: value for name, value in new_firing.items()
        if name not in {
            "transition_firing_ref", "firing_admission_ref",
            "claim_marking_delta_ref"}
    }
    old_claim_delta_stable = {
        name: value for name, value in old_claim_delta.items()
        if name not in {
            "marking_delta_ref", "transition_firing_refs"}
    }
    new_claim_delta_stable = {
        name: value for name, value in new_claim_delta.items()
        if name not in {
            "marking_delta_ref", "transition_firing_refs"}
    }
    if (old_firing.get("transition_firing_ref") != old_firing_ref
            or old_firing.get("firing_admission_ref")
            != old_admission_ref
            or new_firing.get("transition_firing_ref")
            != new_firing_ref
            or new_firing.get("firing_admission_ref")
            != new_admission_ref
            or old_firing_stable != new_firing_stable
            or old_firing.get("net_instance_ref") != net_ref
            or old_firing.get("admission_marking_checkpoint_ref")
            != checkpoint_ref
            or old_claim_delta_ref is None
            or new_claim_delta_ref is None
            or old_claim_delta.get("marking_delta_ref")
            != old_claim_delta_ref
            or new_claim_delta.get("marking_delta_ref")
            != new_claim_delta_ref
            or old_claim_delta.get("transition_firing_refs")
            != [old_firing_ref]
            or new_claim_delta.get("transition_firing_refs")
            != [new_firing_ref]
            or old_claim_delta_stable != new_claim_delta_stable):
        raise RegistryConflict(
            "native resume replacement firing changes its Petri claim")
    admission_stable_fields = {
        "admission_marking_checkpoint_ref", "logical_tau",
    }
    if (old_admission.get("firing_admission_ref")
            != old_admission_ref
            or old_admission.get("transition_firing_ref")
            != old_firing_ref
            or old_admission.get("invocation_ref")
            != old_invocation_ref
            or old_admission.get("operation_execution_lease_ref")
            != old_lease_ref
            or new_admission.get("firing_admission_ref")
            != new_admission_ref
            or new_admission.get("transition_firing_ref")
            != new_firing_ref
            or new_admission.get("invocation_ref")
            != new_invocation_ref
            or new_admission.get("operation_execution_lease_ref")
            != new_lease_ref
            or new_admission.get("writer_fencing_epoch") != current_epoch
            or old_admission.get("claim_marking_delta_ref")
            != old_claim_delta_ref
            or new_admission.get("claim_marking_delta_ref")
            != new_claim_delta_ref
            or any(old_admission.get(name) != new_admission.get(name)
                   for name in admission_stable_fields)
            or old_lease.get("invocation_ref") != old_invocation_ref
            or new_lease.get("invocation_ref") != new_invocation_ref
            or new_lease.get("writer_fencing_epoch") != current_epoch
            or new_lease.get("lease_generation")
            != int(old_lease.get("lease_generation", 0)) + 1):
        raise RegistryConflict(
            "native resume replacement admission/lease is not a re-fence")

    checkpoint = version_metadata(
        str(checkpoint_ref["version_id"]), "marking_checkpoint/v1")
    present = {
        str(value.get("version_id", ""))
        for value in (checkpoint or {}).get("token_refs", [])
        if isinstance(value, Mapping)
    }
    claimed = set(old_firing.get("claimed_input_version_ids", []))
    if checkpoint is None or not claimed.issubset(present):
        raise RegistryConflict(
            "native resume old firing is not an unchanged current preclaim")

    old_invocation_id = str(old_invocation_ref["logical_id"])
    old_firing_id = str(old_firing_ref["logical_id"])
    old_lease_id = str(old_lease_ref["logical_id"])
    lifecycle: dict[str, list[str]] = {}
    for aggregate_id in (
            old_invocation_id, old_firing_id, old_lease_id):
        lifecycle[aggregate_id] = [
            str(row["event_type"]) for row in db.execute(
                "SELECT event_type FROM events WHERE aggregate_id=?",
                (aggregate_id,),
            ).fetchall()
        ]
    if (lifecycle[old_invocation_id].count(
            "invocation_started/v1") != 1
            or lifecycle[old_firing_id].count(
                "firing_admitted/v1") != 1
            or lifecycle[old_firing_id].count(
                "transition_firing_started/v1") != 1
            or lifecycle[old_lease_id].count(
                "operation_dispatch_reserved/v1") != 1
            or any(value in lifecycle[old_invocation_id] for value in {
                "operation_terminal_ready/v1", "invocation_settled/v1",
                "invocation_superseded_by_growth_recovery/v1",
                "invocation_superseded_by_native_resume/v1",
            })
            or any(value in lifecycle[old_firing_id] for value in {
                "transition_firing_settled/v1",
                "transition_firing_superseded_by_growth_recovery/v1",
                "transition_firing_superseded_by_native_resume/v1",
            })
            or any(value in lifecycle[old_lease_id] for value in {
                "operation_execution_started/v1",
                "operation_execution_finished/v1",
                "operation_dispatch_superseded_by_growth_recovery/v1",
                "operation_dispatch_superseded_by_native_resume/v1",
            })):
        raise RegistryConflict(
            "native resume refuses an effected/terminal old lifecycle")
    admission_row = db.execute(
        "SELECT transaction_id FROM events WHERE aggregate_id=? "
        "AND event_type='firing_admitted/v1'",
        (old_firing_id,),
    ).fetchall()
    if len(admission_row) != 1:
        raise RegistryConflict(
            "native resume old firing has no unique admission transaction")
    side_effect = db.execute(
        "SELECT event_type FROM events WHERE producer_invocation_id=? "
        "AND transaction_id<>? LIMIT 1",
        (old_invocation_id, str(admission_row[0]["transaction_id"])),
    ).fetchone()
    if side_effect is not None:
        raise RegistryConflict(
            "native resume refuses post-admission invocation effects")

    common = {
        "firing_admission_ref": new_admission_ref,
        "transition_firing_ref": new_firing_ref,
        "invocation_ref": new_invocation_ref,
        "operation_execution_lease_ref": new_lease_ref,
        "claim_marking_delta_ref": new_firing[
            "claim_marking_delta_ref"],
        "logical_tau": new_firing["logical_tau"],
        "context_digest": new_invocation["context_digest"],
    }
    standard_expected = {
        "firing_admitted/v1": common,
        "transition_firing_started/v1": common,
        "invocation_started/v1": {
            "origin": "petri_operation", **common},
        "operation_dispatch_reserved/v1": {
            "origin": "petri_operation",
            "invocation_ref": new_invocation_ref,
            "operation_execution_lease_ref": new_lease_ref,
            "context_digest": new_invocation["context_digest"],
        },
    }
    for event_type, expected_payload in standard_expected.items():
        matches = [
            value for value in events
            if value.event_type == event_type
            and dict(value.payload) == expected_payload]
        if len(matches) != 1:
            raise RegistryConflict(
                "native resume lacks one exact replacement lifecycle fact")

    custom_expected = {
        "invocation_superseded_by_native_resume/v1": (
            old_invocation_ref,
            {"native_resume_authority_ref": mapping_ref,
             "superseded_invocation_ref": old_invocation_ref,
             "replacement_invocation_ref": new_invocation_ref,
             "mapping_digest": mapping_digest},
            f"invocation:{old_invocation_id}", "invocation"),
        "transition_firing_superseded_by_native_resume/v1": (
            old_firing_ref,
            {"native_resume_authority_ref": mapping_ref,
             "superseded_transition_firing_ref": old_firing_ref,
             "replacement_transition_firing_ref": new_firing_ref,
             "mapping_digest": mapping_digest},
            f"transition-firing:{old_firing_id}",
            "transition_firing"),
        "operation_dispatch_superseded_by_native_resume/v1": (
            old_lease_ref,
            {"native_resume_authority_ref": mapping_ref,
             "superseded_operation_execution_lease_ref": old_lease_ref,
             "replacement_operation_execution_lease_ref": new_lease_ref,
             "mapping_digest": mapping_digest},
            f"operation-lease:{old_lease_id}",
            "operation_execution_lease"),
    }
    for event_type, (old_ref, expected_payload, stream_id,
                     aggregate_type) in custom_expected.items():
        pending = by_type[event_type][0]
        if (dict(pending.payload) != expected_payload
                or pending.stream_id != stream_id
                or pending.aggregate_id != old_ref["logical_id"]
                or pending.aggregate_type != aggregate_type
                or pending.idempotency_key != idempotency_key
                or pending.command_id != idempotency_key
                or pending.payload_schema_ref
                != f"registry_v1/{event_type}"
                or not pending.task_control
                or pending.producer_principal != "framework"
                or pending.producer_invocation_id is not None):
            raise RegistryConflict(
                "native resume superseded fact differs from mapping")
    refenced = by_type["native_resume_refenced/v1"][0]
    refenced_payload = {
        "native_resume_authority_ref": mapping_ref,
        "run_identity_ref": run_ref,
        "recovery_manifest_ref": recovery_ref,
        "net_instance_ref": net_ref,
        "marking_checkpoint_ref": checkpoint_ref,
        "replacement_invocation_ref": new_invocation_ref,
        "replacement_transition_firing_ref": new_firing_ref,
        "replacement_operation_execution_lease_ref": new_lease_ref,
        "replacement_firing_admission_ref": new_admission_ref,
        "writer_fencing_epoch": current_epoch,
        "mapping_digest": mapping_digest,
    }
    if (dict(refenced.payload) != refenced_payload
            or refenced.stream_id
            != f"native-resume:{run_ref['logical_id']}"
            or refenced.aggregate_id != mapping_ref["logical_id"]
            or refenced.aggregate_type != "native_resume_authority"
            or refenced.idempotency_key != idempotency_key
            or refenced.command_id != idempotency_key
            or refenced.payload_schema_ref
            != "registry_v1/native_resume_refenced/v1"
            or not refenced.task_control
            or refenced.producer_principal != "framework"
            or refenced.producer_invocation_id is not None):
        raise RegistryConflict(
            "native resume refenced fact differs from mapping")

    required_relations = (
        ("derived_from", mapping_ref, recovery_ref),
        ("derived_from", mapping_ref, checkpoint_ref),
        ("supersedes", new_invocation_ref, old_invocation_ref),
        ("supersedes", new_firing_ref, old_firing_ref),
        ("supersedes", new_lease_ref, old_lease_ref),
        ("supersedes", new_admission_ref, old_admission_ref),
    )
    if any(not has_new_relation(
            relation_type, str(source["version_id"]),
            str(target["version_id"]))
            for relation_type, source, target in required_relations):
        raise RegistryConflict(
            "native resume lacks exact same-transaction provenance relations")

__all__ = ('validate_native_resume_closure',)
