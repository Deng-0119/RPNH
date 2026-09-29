"""Exact run-authority reader and same-transaction checkpoint successor."""
from __future__ import annotations

import json
from typing import Any
from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .identities import TypedId, new_id
from .models import TypedRelation, VersionRef
from .resource_service import _ResourceServiceKernel
from .publication import _ref_payload, _version_from_payload, _stable_id
from .schema_catalog import canonical_json


def current_run_execution_authority(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        expected_model_condition: str | None = None,
) -> tuple[VersionRef, dict[str, Any]]:
    """Return the newest immutable version of the run's sole authority."""

    rows = core.event_store.canonical_object_rows(
        object_type="run_execution_authority/v1")
    if not rows:
        raise ResourceIntegrityFault(
            "run run lacks its execution authority")
    logical_ids = {str(row["logical_id"]) for row in rows}
    if len(logical_ids) != 1:
        raise ResourceIntegrityFault(
            "run run has multiple execution-authority lineages")
    try:
        publication_ordinals = {}
        for candidate in rows:
            publication = core.event_store.event_by_id(TypedId.parse(
                str(candidate["published_event_id"]), expected="event"))
            if publication is None:
                raise ValueError("run authority publication event is absent")
            publication_ordinals[str(candidate["version_id"])] = (
                publication.ordinal)
        row = max(
            rows,
            key=lambda item: publication_ordinals[str(item["version_id"])],
        )
        document = json.loads(str(row["metadata_json"]))
        authority_ref = VersionRef(
            "run_execution_authority/v1",
            TypedId.parse(
                str(row["logical_id"]),
                expected="run_execution_authority"),
            TypedId.parse(
                str(row["version_id"]),
                expected="run_execution_authority_version"),
        )
        raw_run_ref = core.event_store.get_meta("native_run_ref")
        if raw_run_ref is None:
            raise ValueError("native run pointer is absent")
        run_ref = _version_from_payload(json.loads(raw_run_ref))
        run = kernel._exact_object(
            run_ref, expected_type="native_run_identity/v1")
        task_ref = _version_from_payload(run.metadata["task_ref"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResourceIntegrityFault(
            "run execution authority is malformed") from exc
    core.catalog.validate_instance(
        "run_execution_authority/v1", category="object",
        instance=document)
    if (document.get("run_execution_authority_ref")
            != _ref_payload(authority_ref)
            or document.get("run_ref") != _ref_payload(run_ref)
            or document.get("task_ref") != _ref_payload(task_ref)
            or (expected_model_condition is not None
                and document.get("model_condition") != expected_model_condition)):
        raise ResourceIntegrityFault(
            "run execution authority differs from its run identity")
    return authority_ref, document


def stage_run_execution_checkpoint(
        core: _RegistryCore, kernel: _ResourceServiceKernel, transaction: Any, checkpoint_ref: VersionRef, *,
        idempotency_key: str,
        producer_invocation_id: TypedId | None = None,
        declaration_ref: VersionRef | None = None,
        declaration_schema_ref: str | None = None,
        mutable_stage_ref: VersionRef | None = None,
) -> None:
    """Carry the existing run authority to one committed checkpoint.

    The checkpoint commit and this same-lineage authority successor must
    become visible together.  This updates no historical object and does
    not introduce another authority representation.
    """
    if (not isinstance(checkpoint_ref, VersionRef)
            or checkpoint_ref.entity_type != "marking_checkpoint/v1"
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError(
            "run execution checkpoint update requires exact checkpoint/key")
    authority_ref, current = current_run_execution_authority(core, kernel)
    checkpoint_payload = _ref_payload(checkpoint_ref)
    prospective = {}
    if any(value is not None for value in (declaration_ref, declaration_schema_ref, mutable_stage_ref)):
        if (not isinstance(declaration_ref, VersionRef)
                or declaration_ref.entity_type != "resource_version/v1"
                or not isinstance(declaration_schema_ref, str) or not declaration_schema_ref
                or mutable_stage_ref != declaration_ref):
            raise TypeError("prospective graph source requires exact declaration/schema/mutable-stage refs")
        declaration = kernel._exact_object(declaration_ref, expected_type="resource_version/v1")
        if (declaration.metadata["task_ref"] != current["task_ref"]
                or declaration.metadata["content_schema_ref"] != declaration_schema_ref):
            raise ResourceIntegrityFault("prospective graph source differs from registered task/schema")
        prospective = {"declaration_ref": _ref_payload(declaration_ref),
                       "declaration_schema_ref": declaration_schema_ref}
    if (current.get("latest_checkpoint_ref") == checkpoint_payload
            and all(current.get(k) == v for k,v in prospective.items())):
        return
    successor_ref = VersionRef(
        "run_execution_authority/v1",
        authority_ref.entity_id,
        _stable_id(
            "run_execution_authority_version",
            authority_ref.entity_id,
            authority_ref.version_id,
            checkpoint_ref.version_id,
            idempotency_key),
    )
    successor = {
        **current,
        "run_execution_authority_ref": _ref_payload(successor_ref),
        "latest_checkpoint_ref": checkpoint_payload,
        **prospective,
    }
    core.catalog.validate_instance(
        "run_execution_authority/v1", category="object",
        instance=successor)
    existing = core.event_store.object_row(
        successor_ref.version_id)
    if existing is not None:
        registered = kernel._exact_object(
            successor_ref, expected_type="run_execution_authority/v1")
        if dict(registered.metadata) != successor:
            raise ResourceIntegrityFault(
                "run execution checkpoint successor differs from its command")
        return
    transaction.prewrite(
        object_type="run_execution_authority/v1",
        logical_id=successor_ref.entity_id,
        version_id=successor_ref.version_id,
        payload=canonical_json(successor), metadata=successor,
        media_type="application/json",
        schema_ref="registry_v1/run_execution_authority/v1",
        producer_invocation_id=producer_invocation_id)
    if mutable_stage_ref is not None:
        transaction.relate(TypedRelation(
            _stable_id("relation", idempotency_key, "mutable_stage", successor_ref.version_id),
            "derived_from", successor_ref, mutable_stage_ref,
            metadata={"authority_role": "mutable_stage"}),
            producer_invocation_id=producer_invocation_id,
            system_owned=producer_invocation_id is None)


def record_owner_stop(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        idempotency_key: str,
) -> VersionRef:
    """Account for an explicitly authorized stop using the live sole writer.

    Called at the owner's safe boundary, never from a signal handler or an
    observer. This changes execution authority only: unresolved firings and
    their claims remain historical facts, not fabricated cancellation/terminal.
    """
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise TypeError("owner stop requires one nonempty idempotency key")
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(kernel, _ResourceServiceKernel)
            or kernel._ResourceServiceKernel__core is not core):
        raise TypeError("owner stop requires execution-owner Core and Kernel")
    if core.writer_epoch != core.event_store.writer_epoch:
        raise ResourceIntegrityFault("owner stop writer epoch is stale")
    authority_ref, current = current_run_execution_authority(core, kernel)
    if (current["status"] == "terminal"
            or current["terminal_evidence_ref"] is not None):
        raise ResourceIntegrityFault("terminal run cannot be stopped by owner")
    from .module_runtime import hydrate_module_runtime
    executable, _structure, marking = hydrate_module_runtime(core)
    checkpoint_payload = _ref_payload(marking.checkpoint_ref)
    if (current["declaration_schema_ref"] != "rpnh/executable_net/v1"
            or current["declaration_ref"] != _ref_payload(executable.declaration_resource_ref.as_version_ref())
            or current["latest_checkpoint_ref"] != checkpoint_payload):
        raise ResourceIntegrityFault("owner stop differs from current Module source/checkpoint")
    if current["status"] == "stopped_by_owner":
        return authority_ref
    successor_ref = VersionRef("run_execution_authority/v1", authority_ref.entity_id,
                               new_id("run_execution_authority_version"))
    successor = {**current, "run_execution_authority_ref": _ref_payload(successor_ref),
                 "latest_checkpoint_ref": checkpoint_payload,
                 "status": "stopped_by_owner", "terminal_evidence_ref": None}
    core.catalog.validate_instance("run_execution_authority/v1", category="object", instance=successor)
    tx = core.begin(idempotency_key=canonical_json({
        "command": "record_owner_stop", "owner_idempotency_key": idempotency_key,
        "predecessor_ref": _ref_payload(authority_ref)}).decode("utf-8"))
    tx.prewrite(object_type="run_execution_authority/v1", logical_id=successor_ref.entity_id,
        version_id=successor_ref.version_id, payload=canonical_json(successor), metadata=successor,
        media_type="application/json", schema_ref="registry_v1/run_execution_authority/v1")
    for target in (authority_ref, marking.checkpoint_ref):
        tx.relate(TypedRelation(new_id("relation"), "derived_from", successor_ref, target), system_owned=True)
    tx.commit()
    registered = kernel._exact_object(successor_ref, expected_type="run_execution_authority/v1")
    if dict(registered.metadata) != successor:
        raise ResourceIntegrityFault("registered owner-stop authority differs from its command")
    return successor_ref


def _record_process_configuration(
        core: _RegistryCore, *, immutable_input_ref, mutable_stage_ref,
        recovery_manifest_ref, authority_ref: VersionRef,
) -> None:
    from .strict_contracts import content_schema_ref_payload
    pointer = {
        "immutable_genesis_ref": content_schema_ref_payload(
            immutable_input_ref),
        "mutable_stage_ref": content_schema_ref_payload(mutable_stage_ref),
        "recovery_manifest_ref": _ref_payload(recovery_manifest_ref),
        "run_execution_authority_ref": _ref_payload(authority_ref),
    }
    key = f"execution_process_configuration:writer-{core.writer_epoch}"
    value = canonical_json(pointer).decode("utf-8")
    if core.event_store.get_or_create_meta(key, value) != value:
        raise ResourceIntegrityFault(
            "resume writer entry selected another process configuration")


def resume_owner_stopped_run(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        immutable_input_ref, mutable_stage_ref, recovery_manifest_ref,
        idempotency_key: str,
) -> VersionRef:
    """Reauthorize one drained owner-stop checkpoint for this writer entry."""
    from .resources import ResourceVersionRef
    if (not isinstance(immutable_input_ref, ResourceVersionRef)
            or not isinstance(mutable_stage_ref, ResourceVersionRef)
            or not isinstance(recovery_manifest_ref, VersionRef)
            or recovery_manifest_ref.entity_type
            != "task_recovery_manifest/v1"
            or not isinstance(idempotency_key, str) or not idempotency_key):
        raise TypeError("run resume requires exact process configuration refs")
    authority_ref, current = current_run_execution_authority(core, kernel)
    if (current["status"] != "stopped_by_owner"
            or current["terminal_evidence_ref"] is not None):
        raise ResourceIntegrityFault(
            "run resume requires stopped_by_owner without terminal evidence")
    from .module_execution import active_module_firings
    from .module_runtime import hydrate_module_runtime
    executable, _structure, marking = hydrate_module_runtime(core)
    if active_module_firings(core, executable.net_ref):
        raise ResourceIntegrityFault(
            "run resume requires a drained interruption checkpoint")
    if (current["latest_checkpoint_ref"] != _ref_payload(marking.checkpoint_ref)
            or current["declaration_ref"]
            != _ref_payload(executable.declaration_resource_ref.as_version_ref())
            or mutable_stage_ref != executable.declaration_resource_ref
            or core.recovery_manifest_ref() != recovery_manifest_ref):
        raise ResourceIntegrityFault(
            "run resume process configuration differs from current checkpoint")
    for resource in (immutable_input_ref, mutable_stage_ref):
        registered = kernel._exact_object(
            resource.as_version_ref(), expected_type="resource_version/v1")
        if registered.metadata["task_ref"] != current["task_ref"]:
            raise ResourceIntegrityFault(
                "run resume resource belongs to another task")

    successor_ref = VersionRef(
        "run_execution_authority/v1", authority_ref.entity_id,
        new_id("run_execution_authority_version"))
    successor = {
        **current,
        "run_execution_authority_ref": _ref_payload(successor_ref),
        "status": "running",
        "terminal_evidence_ref": None,
    }
    core.catalog.validate_instance(
        "run_execution_authority/v1", category="object",
        instance=successor)
    tx = core.begin(idempotency_key=canonical_json({
        "command": "resume_owner_stopped_run",
        "owner_idempotency_key": idempotency_key,
        "predecessor_ref": _ref_payload(authority_ref),
        "writer_epoch": core.writer_epoch,
    }).decode("utf-8"))
    tx.prewrite(
        object_type="run_execution_authority/v1",
        logical_id=successor_ref.entity_id,
        version_id=successor_ref.version_id,
        payload=canonical_json(successor), metadata=successor,
        media_type="application/json",
        schema_ref="registry_v1/run_execution_authority/v1")
    for label, target in (
            ("previous_authority", authority_ref),
            ("checkpoint", marking.checkpoint_ref),
            ("immutable_genesis", immutable_input_ref.as_version_ref()),
            ("mutable_stage", mutable_stage_ref.as_version_ref()),
            ("recovery_manifest", recovery_manifest_ref)):
        tx.relate(TypedRelation(
            new_id("relation"), "derived_from", successor_ref, target,
            metadata={"authority_role": label}), system_owned=True)
    tx.commit()
    _record_process_configuration(
        core, immutable_input_ref=immutable_input_ref,
        mutable_stage_ref=mutable_stage_ref,
        recovery_manifest_ref=recovery_manifest_ref,
        authority_ref=successor_ref)
    return successor_ref


def record_recovered_run_entry(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        immutable_input_ref, mutable_stage_ref, recovery_manifest_ref,
) -> VersionRef:
    """Bind a recovered running checkpoint to the immediate new writer."""
    from .resources import ResourceVersionRef
    if (not isinstance(immutable_input_ref, ResourceVersionRef)
            or not isinstance(mutable_stage_ref, ResourceVersionRef)
            or not isinstance(recovery_manifest_ref, VersionRef)
            or recovery_manifest_ref.entity_type
            != "task_recovery_manifest/v1"):
        raise TypeError(
            "recovered run entry requires exact process configuration refs")
    authority_ref, current = current_run_execution_authority(core, kernel)
    from .module_execution import active_module_firings
    from .module_runtime import hydrate_module_runtime
    executable, _structure, marking = hydrate_module_runtime(core)
    if (current["status"] != "running"
            or current["terminal_evidence_ref"] is not None
            or active_module_firings(core, executable.net_ref)
            or current["latest_checkpoint_ref"]
            != _ref_payload(marking.checkpoint_ref)
            or current["declaration_ref"]
            != _ref_payload(
                executable.declaration_resource_ref.as_version_ref())
            or mutable_stage_ref != executable.declaration_resource_ref
            or core.recovery_manifest_ref() != recovery_manifest_ref):
        raise ResourceIntegrityFault(
            "recovered run entry differs from settled current checkpoint")
    for resource in (immutable_input_ref, mutable_stage_ref):
        registered = kernel._exact_object(
            resource.as_version_ref(), expected_type="resource_version/v1")
        if registered.metadata["task_ref"] != current["task_ref"]:
            raise ResourceIntegrityFault(
                "recovered run resource belongs to another task")
    _record_process_configuration(
        core, immutable_input_ref=immutable_input_ref,
        mutable_stage_ref=mutable_stage_ref,
        recovery_manifest_ref=recovery_manifest_ref,
        authority_ref=authority_ref)
    return authority_ref


__all__ = (
    'current_run_execution_authority', 'stage_run_execution_checkpoint',
    'record_owner_stop', 'record_recovered_run_entry',
    'resume_owner_stopped_run')
