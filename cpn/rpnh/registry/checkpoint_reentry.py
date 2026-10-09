"""Owner-authorized append-only reentry from a committed Petri checkpoint."""
from __future__ import annotations

import json
from typing import Any, Mapping

from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .event_store import verified_checkpoint_head
from .identities import TypedId
from .models import PendingEvent, TypedRelation, VersionRef
from .module_execution import active_module_firings
from .module_runtime import hydrate_module_runtime
from .publication import _ref_payload, _stable_id, _version_from_payload
from .resource_service import _ResourceServiceKernel, _resolve_registry_workspace_root
from .run_authority import current_run_execution_authority
from .schema_catalog import canonical_json
from .strict_contracts import _registered


def committed_checkpoint_refs(
        core: _RegistryCore, *, net_ref: VersionRef | None = None,
) -> tuple[VersionRef, ...]:
    """Return the ordered, committed checkpoint lineage for one active net."""
    if not isinstance(core, _RegistryCore):
        raise TypeError("checkpoint listing requires one Registry Core")
    if net_ref is None:
        executable, _structure, _marking = hydrate_module_runtime(core)
        net_ref = executable.net_ref
    if (not isinstance(net_ref, VersionRef)
            or net_ref.entity_type != "net_instance/v1"):
        raise TypeError("checkpoint listing requires an exact net ref")
    verified_checkpoint_head(
        core.event_store, core.catalog, core.task_id, net_ref)
    refs = []
    for event in core.event_store.list_events_by_type(
            ("marking_checkpoint_committed/v1",)):
        if (event.payload.get("net_instance_ref")
                != _ref_payload(net_ref)):
            continue
        ref = _version_from_payload(event.payload["checkpoint_ref"])
        _registered(core, ref, "marking_checkpoint/v1")
        refs.append(ref)
    if not refs:
        raise ResourceIntegrityFault("active net has no committed checkpoints")
    return tuple(refs)


def resolve_committed_checkpoint(
        core: _RegistryCore, checkpoint_version_id: str,
) -> VersionRef:
    """Resolve one exact user-selected version in the active net lineage."""
    try:
        version_id = TypedId.parse(
            checkpoint_version_id, expected="marking_checkpoint_version")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "checkpoint selection requires marking_checkpoint_version:<id>") from exc
    matches = tuple(
        ref for ref in committed_checkpoint_refs(core)
        if ref.version_id == version_id)
    if len(matches) != 1:
        raise ValueError(
            "selected checkpoint is not in the active PetriNet lineage")
    return matches[0]


def _canonical_refs(values: object) -> tuple[VersionRef, ...]:
    if not isinstance(values, list):
        raise ResourceIntegrityFault("checkpoint reference inventory is malformed")
    try:
        refs = tuple(_version_from_payload(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "checkpoint reference inventory is malformed") from exc
    if len(set(refs)) != len(refs):
        raise ResourceIntegrityFault("checkpoint reference inventory is ambiguous")
    return tuple(sorted(refs, key=lambda ref: (
        str(ref.entity_id), str(ref.version_id))))


def _owner_authority(
        core: _RegistryCore, task_ref: VersionRef,
) -> tuple[VersionRef, VersionRef]:
    candidates: list[tuple[int, VersionRef, Mapping[str, Any]]] = []
    for row in core.event_store.canonical_object_rows(
            object_type="user_authority_decision/v1"):
        document = json.loads(str(row["metadata_json"]))
        if (document.get("authority_kind") != "scope"
                or document.get("status") != "effective"
                or _ref_payload(task_ref)
                not in document.get("governed_artifact_refs", [])):
            continue
        ref = VersionRef(
            "user_authority_decision/v1",
            TypedId.parse(str(row["logical_id"]),
                          expected="user_authority_decision"),
            TypedId.parse(str(row["version_id"]),
                          expected="user_authority_decision_version"))
        candidates.append((int(document["effective_sequence"]), ref, document))
    if not candidates:
        raise ResourceIntegrityFault(
            "checkpoint reentry lacks the task owner scope authority")
    maximum = max(item[0] for item in candidates)
    selected = [item for item in candidates if item[0] == maximum]
    if len(selected) != 1:
        raise ResourceIntegrityFault(
            "checkpoint reentry has ambiguous task owner scope authority")
    _sequence, decision_ref, decision = selected[0]
    principal_ref = _version_from_payload(decision["user_principal_ref"])
    _registered(core, principal_ref, "principal/v1")
    return principal_ref, decision_ref


def _path_deltas(
        core: _RegistryCore, current_ref: VersionRef,
        source_ref: VersionRef,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str], bytes]:
    from cpn.rpnh.workspace_settlement import (
        _archive_files, _exact_workspace_resource, _full_archive,
        _workspace_resource_state,
    )

    current_files = _archive_files(core, current_ref)
    source_files = _archive_files(core, source_ref)
    current_resources = _workspace_resource_state(core, current_ref)
    source_resources = _workspace_resource_state(core, source_ref)
    if set(current_files) != set(current_resources):
        raise ResourceIntegrityFault(
            "current workspace archive differs from registered path provenance")
    if set(source_files) != set(source_resources):
        raise ResourceIntegrityFault(
            "selected workspace archive differs from registered path provenance")

    deltas: list[dict[str, Any]] = []
    changed: list[str] = []
    deleted: list[str] = []
    for path in sorted(set(current_files) | set(source_files)):
        before_ref = current_resources.get(path)
        after_ref = source_resources.get(path)
        before = current_files.get(path)
        after = source_files.get(path)
        if before == after and before_ref == after_ref:
            continue
        before_summary = None
        after_summary = None
        if before_ref is not None:
            before_payload, before_summary = _exact_workspace_resource(
                core, before_ref, path)
            if before is None or before_payload != before[0]:
                raise ResourceIntegrityFault(
                    "current workspace resource differs from archive bytes")
        if after_ref is not None:
            after_payload, after_summary = _exact_workspace_resource(
                core, after_ref, path)
            if after is None or after_payload != after[0]:
                raise ResourceIntegrityFault(
                    "selected workspace resource differs from archive bytes")
        if before_ref is None:
            kind = "create"
            changed.append(path)
        elif after_ref is None:
            kind = "delete"
            deleted.append(path)
        else:
            kind = "update"
            changed.append(path)
        deltas.append({
            "path": path, "change_kind": kind,
            "before_resource_ref": (None if before_ref is None else {
                "resource_id": str(before_ref.resource_id),
                "resource_version_id": str(before_ref.resource_version_id)}),
            "after_resource_ref": (None if after_ref is None else {
                "resource_id": str(after_ref.resource_id),
                "resource_version_id": str(after_ref.resource_version_id)}),
            "before_summary": before_summary,
            "after_summary": after_summary,
        })
    return deltas, changed, deleted, sorted(source_files), _full_archive(source_files)


def _workspace_plans(
        core: _RegistryCore, *, current_checkpoint: Mapping[str, Any],
        source_checkpoint: Mapping[str, Any], authorization_ref: VersionRef,
        command_id: str,
) -> tuple[dict[str, Any], ...]:
    current_refs = _canonical_refs(
        current_checkpoint.get("workspace_revision_refs"))
    source_refs = _canonical_refs(
        source_checkpoint.get("workspace_revision_refs"))
    current_by_lineage = {ref.entity_id: ref for ref in current_refs}
    source_by_lineage = {ref.entity_id: ref for ref in source_refs}
    if set(current_by_lineage) != set(source_by_lineage):
        raise ResourceIntegrityFault(
            "selected checkpoint changes the active workspace lineage set")
    plans = []
    for lineage_id in sorted(source_by_lineage, key=str):
        current_ref = current_by_lineage[lineage_id]
        source_ref = source_by_lineage[lineage_id]
        head = core.event_store.workspace_lineage_head(lineage_id)
        if (head is None or head["workspace_revision_version_id"]
                != str(current_ref.version_id)):
            raise ResourceIntegrityFault(
                "current checkpoint workspace differs from its SQL head")
        _current_row, current = _registered(
            core, current_ref, "workspace_revision/v1")
        _source_row, source = _registered(
            core, source_ref, "workspace_revision/v1")
        if (current["run_ref"] != source["run_ref"]
                or current["task_ref"] != source["task_ref"]
                or current["net_instance_ref"] != source["net_instance_ref"]
                or current["workspace_lineage_id"] != str(lineage_id)
                or source["workspace_lineage_id"] != str(lineage_id)):
            raise ResourceIntegrityFault(
                "selected workspace revision is outside the current run lineage")
        successor_ref = VersionRef(
            "workspace_revision/v1", lineage_id,
            _stable_id("workspace_revision", "owner-reopen", command_id,
                       source_ref.version_id, current_ref.version_id))
        deltas, changed, deleted, inventory, payload = _path_deltas(
            core, current_ref, source_ref)
        metadata = {
            "workspace_lineage_id": str(lineage_id),
            "workspace_revision_id": str(successor_ref.version_id),
            "workspace_revision_ref": _ref_payload(successor_ref),
            "run_ref": current["run_ref"], "task_ref": current["task_ref"],
            "net_instance_ref": current["net_instance_ref"],
            "parent_revision_ref": _ref_payload(current_ref),
            "base_revision_ref": _ref_payload(source_ref),
            "producer_invocation_ref": None,
            "transition_firing_ref": None,
            "firing_workspace_binding_ref": None,
            "reopen_authorization_ref": _ref_payload(authorization_ref),
            "disposition": "owner_reopen",
            "changed_paths": changed, "deleted_paths": deleted,
            "path_deltas": deltas, "inventory_paths": inventory,
            "conflict_paths": [], "semantic_output_refs": [],
            "trace_summary_refs": [], "payload_kind": "full_workspace_tar",
            "settled": True,
        }
        core.catalog.validate_instance(
            "workspace_revision/v1", category="object", instance=metadata)
        plans.append({
            "current_ref": current_ref, "source_ref": source_ref,
            "successor_ref": successor_ref, "metadata": metadata,
            "payload": payload,
        })
    return tuple(plans)


def _existing_reentry(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        authorization_ref: VersionRef, command_id: str,
        source_checkpoint_ref: VersionRef, reason: str,
) -> VersionRef | None:
    if core.event_store.object_row(authorization_ref.version_id) is None:
        return None
    _row, authorization = _registered(
        core, authorization_ref, "run_reopen_authorization/v1")
    _authority_ref, authority = current_run_execution_authority(core, kernel)
    if authorization.get("command_id") != command_id:
        raise ResourceIntegrityFault(
            "checkpoint reopen authorization has a conflicting command id")
    if (authorization.get("selected_checkpoint_ref")
            != _ref_payload(source_checkpoint_ref)):
        raise ResourceIntegrityFault(
            "checkpoint reopen command has a conflicting selected checkpoint")
    if authorization.get("reason") != reason:
        raise ResourceIntegrityFault(
            "checkpoint reopen command has a conflicting reason")
    if (authority.get("reopen_authorization_ref")
            != _ref_payload(authorization_ref)
            or authority.get("execution_generation")
            != authorization.get("execution_generation")):
        raise ResourceIntegrityFault(
            "checkpoint reopen command is stale or conflicts with a later generation")
    checkpoint_ref = _version_from_payload(
        authorization["reentry_checkpoint_ref"])
    return checkpoint_ref


def stage_checkpoint_reentry(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        source_checkpoint_ref: VersionRef, command_id: str, reason: str,
) -> VersionRef:
    """Atomically append one owner-selected execution generation."""
    from .parent_bound import reject_bound_reentry
    reject_bound_reentry(core)
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(kernel, _ResourceServiceKernel)
            or kernel._ResourceServiceKernel__core is not core
            or not isinstance(source_checkpoint_ref, VersionRef)
            or source_checkpoint_ref.entity_type != "marking_checkpoint/v1"
            or not isinstance(command_id, str) or not command_id
            or len(command_id) > 300
            or not isinstance(reason, str) or not reason or len(reason) > 600):
        raise TypeError(
            "checkpoint reentry requires owner Core, Kernel, ref, command and reason")
    if core.writer_epoch != core.event_store.writer_epoch:
        raise ResourceIntegrityFault("checkpoint reentry writer epoch is stale")

    authorization_ref = VersionRef(
        "run_reopen_authorization/v1",
        _stable_id("run_reopen_authorization", command_id),
        _stable_id("run_reopen_authorization_version", command_id))
    existing = _existing_reentry(
        core, kernel, authorization_ref=authorization_ref,
        command_id=command_id,
        source_checkpoint_ref=source_checkpoint_ref, reason=reason)
    if existing is not None:
        return existing

    authority_ref, authority = current_run_execution_authority(core, kernel)
    if authority["status"] not in {"terminal", "stopped_by_owner"}:
        raise ResourceIntegrityFault(
            "checkpoint reentry requires terminal or owner-stopped authority")
    if (authority.get("reopen_authorization_ref") is not None
            and authority["status"] == "stopped_by_owner"):
        _pending_row, pending = _registered(
            core, _version_from_payload(
                authority["reopen_authorization_ref"]),
            "run_reopen_authorization/v1")
        if (authority["latest_checkpoint_ref"]
                == pending["reentry_checkpoint_ref"]):
            raise ResourceIntegrityFault(
                "another checkpoint reentry is already prepared")
    executable, _structure, current = hydrate_module_runtime(core)
    if active_module_firings(core, executable.net_ref):
        raise ResourceIntegrityFault(
            "checkpoint reentry requires a drained current checkpoint")
    if source_checkpoint_ref not in committed_checkpoint_refs(
            core, net_ref=executable.net_ref):
        raise ResourceIntegrityFault(
            "checkpoint reentry source is outside the active net lineage")
    _, source = _registered(
        core, source_checkpoint_ref, "marking_checkpoint/v1")
    _, current_checkpoint = _registered(
        core, current.checkpoint_ref, "marking_checkpoint/v1")
    if (source["net_instance_ref"] != _ref_payload(executable.net_ref)
            or source.get("settled") is not True
            or current_checkpoint.get("settled") is not True
            or authority["latest_checkpoint_ref"]
            != _ref_payload(current.checkpoint_ref)):
        raise ResourceIntegrityFault(
            "checkpoint reentry source/current cut is not settled authority")

    generation = int(authority.get("execution_generation", 0)) + 1
    epoch = current.epoch + 1
    next_token_id = current.next_token_id
    replacements: list[tuple[VersionRef, VersionRef, dict[str, Any]]] = []
    mappings = []
    for source_token_ref in _canonical_refs(source["token_refs"]):
        _, source_token = _registered(
            core, source_token_ref, "petri_token/v1")
        replacement_ref = VersionRef(
            "petri_token/v1",
            _stable_id("petri_token", "owner-reopen", command_id,
                       source_token_ref.entity_id, source_token_ref.version_id),
            _stable_id("petri_token_version", "owner-reopen", command_id,
                       source_token_ref.entity_id, source_token_ref.version_id))
        replacement = {
            **source_token, "petri_token_ref": _ref_payload(replacement_ref),
            "token_id": next_token_id, "epoch": epoch, "consumed_by": None,
        }
        core.catalog.validate_instance(
            "petri_token/v1", category="object", instance=replacement)
        replacements.append((source_token_ref, replacement_ref, replacement))
        mappings.append({
            "source_token_ref": _ref_payload(source_token_ref),
            "replacement_token_ref": _ref_payload(replacement_ref),
        })
        next_token_id += 1

    checkpoint_ref = VersionRef(
        "marking_checkpoint/v1",
        _stable_id("marking_checkpoint", "owner-reopen", command_id),
        _stable_id("marking_checkpoint_version", "owner-reopen", command_id))
    successor_ref = VersionRef(
        "run_execution_authority/v1", authority_ref.entity_id,
        _stable_id("run_execution_authority_version", "owner-reopen",
                   command_id))
    workspace_plans = _workspace_plans(
        core, current_checkpoint=current_checkpoint,
        source_checkpoint=source,
        authorization_ref=authorization_ref, command_id=command_id)
    current_token_refs = _canonical_refs(current_checkpoint["token_refs"])
    terminal_ref = authority.get("terminal_evidence_ref")
    reentry = {
        "reentry_source_checkpoint_ref": _ref_payload(source_checkpoint_ref),
        "reentry_authorization_ref": _ref_payload(authorization_ref),
        "reentry_superseded_terminal_evidence_ref": terminal_ref,
        "reentry_generation": generation,
        "reentry_token_mappings": mappings,
        "reentry_superseded_token_refs": [
            _ref_payload(ref) for ref in current_token_refs],
    }
    checkpoint = {
        "marking_checkpoint_ref": _ref_payload(checkpoint_ref),
        "net_instance_ref": _ref_payload(executable.net_ref),
        "team_design_root_ref": _ref_payload(executable.team_design_root_ref),
        "epoch": epoch, "next_token_id": next_token_id,
        "attempts": [
            {"transition_id": item.transition_id,
             "highest_issued": item.highest_issued}
            for item in current.attempts],
        "token_refs": sorted(
            (_ref_payload(item[1]) for item in replacements),
            key=canonical_json),
        "settled": True,
        "previous_checkpoint_ref": _ref_payload(current.checkpoint_ref),
        "settlement_delta_ref": None, "transition_firing_refs": [],
        "workspace_revision_refs": [
            _ref_payload(plan["successor_ref"])
            for plan in workspace_plans],
        **reentry,
    }
    core.catalog.validate_instance(
        "marking_checkpoint/v1", category="object", instance=checkpoint)
    successor = {
        **authority,
        "run_execution_authority_ref": _ref_payload(successor_ref),
        "latest_checkpoint_ref": _ref_payload(checkpoint_ref),
        "status": "stopped_by_owner", "terminal_evidence_ref": None,
        "execution_generation": generation,
        "generation_source_checkpoint_ref": _ref_payload(
            source_checkpoint_ref),
        "reopen_authorization_ref": _ref_payload(authorization_ref),
    }
    core.catalog.validate_instance(
        "run_execution_authority/v1", category="object", instance=successor)

    run_ref = _version_from_payload(authority["run_ref"])
    task_ref = _version_from_payload(authority["task_ref"])
    owner_principal_ref, control_authority_ref = _owner_authority(core, task_ref)
    authorization = {
        "run_reopen_authorization_ref": _ref_payload(authorization_ref),
        "command_id": command_id, "task_ref": _ref_payload(task_ref),
        "run_ref": _ref_payload(run_ref),
        "net_instance_ref": _ref_payload(executable.net_ref),
        "owner_principal_ref": _ref_payload(owner_principal_ref),
        "control_authority_ref": _ref_payload(control_authority_ref),
        "expected_run_authority_ref": _ref_payload(authority_ref),
        "expected_terminal_evidence_ref": terminal_ref,
        "expected_current_checkpoint_ref": _ref_payload(current.checkpoint_ref),
        "selected_checkpoint_ref": _ref_payload(source_checkpoint_ref),
        "superseded_current_token_refs": [
            _ref_payload(ref) for ref in current_token_refs],
        "selected_source_token_refs": [
            item["source_token_ref"] for item in mappings],
        "expected_workspace_head_refs": [
            _ref_payload(plan["current_ref"]) for plan in workspace_plans],
        "selected_workspace_revision_refs": [
            _ref_payload(plan["source_ref"]) for plan in workspace_plans],
        "workspace_reentry_revision_refs": [
            _ref_payload(plan["successor_ref"]) for plan in workspace_plans],
        "reentry_checkpoint_ref": _ref_payload(checkpoint_ref),
        "successor_run_authority_ref": _ref_payload(successor_ref),
        "execution_generation": generation, "reason": reason,
    }
    core.catalog.validate_instance(
        "run_reopen_authorization/v1", category="object", instance=authorization)

    tx = core.begin(
        idempotency_key=f"rpnh:owner-reopen:{command_id}",
        net_instance_id=executable.net_ref.entity_id)
    tx.prewrite(
        object_type="run_reopen_authorization/v1",
        logical_id=authorization_ref.entity_id,
        version_id=authorization_ref.version_id,
        payload=canonical_json(authorization), metadata=authorization,
        media_type="application/json",
        schema_ref="registry_v1/run_reopen_authorization/v1")
    for source_ref, replacement_ref, replacement in replacements:
        tx.prewrite(
            object_type="petri_token/v1", logical_id=replacement_ref.entity_id,
            version_id=replacement_ref.version_id,
            payload=canonical_json(replacement), metadata=replacement,
            media_type="application/json", schema_ref="registry_v1/petri_token/v1")
        tx.relate(TypedRelation(
            _stable_id("relation", "owner-reopen-token", command_id,
                       replacement_ref.version_id),
            "derived_from", replacement_ref, source_ref,
            metadata={"reopen_authorization_ref": _ref_payload(authorization_ref)}),
            system_owned=True)
    for plan in workspace_plans:
        tx.prewrite(
            object_type="workspace_revision/v1",
            logical_id=plan["successor_ref"].entity_id,
            version_id=plan["successor_ref"].version_id,
            payload=plan["payload"], metadata=plan["metadata"],
            media_type="application/x-tar",
            schema_ref="registry_v1/workspace_revision/v1")
        tx.relate(TypedRelation(
            _stable_id("relation", "owner-reopen-workspace-authority",
                       command_id, plan["successor_ref"].version_id),
            "derived_from", plan["successor_ref"], authority_ref,
            metadata={"authority_role": "expected_run_authority"}),
            system_owned=True)
        tx.relate(TypedRelation(
            _stable_id("relation", "owner-reopen-workspace-command",
                       command_id, plan["successor_ref"].version_id),
            "derived_from", plan["successor_ref"], authorization_ref,
            metadata={"authority_role": "owner_reopen"}), system_owned=True)
        for role in ("current_ref", "source_ref"):
            tx.relate(TypedRelation(
                _stable_id("relation", "owner-reopen-workspace", command_id,
                           role, plan["successor_ref"].version_id),
                "derived_from", plan["successor_ref"], plan[role],
                metadata={"workspace_role": role}), system_owned=True)
        tx.advance_workspace_head(
            lineage_ref=plan["source_ref"],
            expected_head_ref=plan["current_ref"],
            successor_ref=plan["successor_ref"],
            authority_ref=authority_ref)
    tx.prewrite(
        object_type="marking_checkpoint/v1",
        logical_id=checkpoint_ref.entity_id, version_id=checkpoint_ref.version_id,
        payload=canonical_json(checkpoint), metadata=checkpoint,
        media_type="application/json", schema_ref="registry_v1/marking_checkpoint/v1")
    event_payload = {key: value for key, value in checkpoint.items()
                     if key not in {"marking_checkpoint_ref", "epoch",
                                    "next_token_id", "attempts", "token_refs"}}
    event_payload["checkpoint_ref"] = _ref_payload(checkpoint_ref)
    tx.append(PendingEvent(
        event_type="marking_checkpoint_committed/v1", criticality="authoritative",
        stream_id=f"marking:{executable.net_ref.entity_id}",
        aggregate_id=str(executable.net_ref.entity_id),
        aggregate_type="marking_checkpoint", idempotency_key=command_id,
        command_id=command_id, payload=event_payload,
        payload_schema_ref="registry_v1/marking_checkpoint_committed/v1",
        task_control=True, producer_principal=str(owner_principal_ref.entity_id)))
    tx.prewrite(
        object_type="run_execution_authority/v1",
        logical_id=successor_ref.entity_id, version_id=successor_ref.version_id,
        payload=canonical_json(successor), metadata=successor,
        media_type="application/json",
        schema_ref="registry_v1/run_execution_authority/v1")
    reopened_payload = {
        "run_reopen_authorization_ref": _ref_payload(authorization_ref),
        "expected_run_authority_ref": _ref_payload(authority_ref),
        "selected_checkpoint_ref": _ref_payload(source_checkpoint_ref),
        "reentry_checkpoint_ref": _ref_payload(checkpoint_ref),
        "successor_run_authority_ref": _ref_payload(successor_ref),
        "workspace_reentry_revision_refs": [
            _ref_payload(plan["successor_ref"]) for plan in workspace_plans],
        "execution_generation": generation, "reason": reason,
    }
    tx.append(PendingEvent(
        event_type="run_reopened/v1", criticality="authoritative",
        stream_id=f"run-reopen:{run_ref.entity_id}",
        aggregate_id=str(run_ref.entity_id), aggregate_type="native_run",
        idempotency_key=command_id, command_id=command_id,
        payload=reopened_payload, payload_schema_ref="registry_v1/run_reopened/v1",
        task_control=True, producer_principal=str(owner_principal_ref.entity_id)))
    for target, role in (
            (authority_ref, "previous_authority"),
            (current.checkpoint_ref, "superseded_checkpoint"),
            (source_checkpoint_ref, "selected_checkpoint"),
            (checkpoint_ref, "reentry_checkpoint"),
            (authorization_ref, "reopen_authorization")):
        tx.relate(TypedRelation(
            _stable_id("relation", "owner-reopen-authority", command_id, role),
            "derived_from", successor_ref, target,
            metadata={"authority_role": role}), system_owned=True)
    for target, role in (
            (authority_ref, "expected_authority"),
            (current.checkpoint_ref, "expected_checkpoint"),
            (source_checkpoint_ref, "selected_checkpoint"),
            (control_authority_ref, "owner_scope")):
        tx.relate(TypedRelation(
            _stable_id("relation", "owner-reopen-authorization", command_id, role),
            "derived_from", authorization_ref, target,
            metadata={"authorization_role": role}), system_owned=True)
    for current_token_ref in current_token_refs:
        tx.relate(TypedRelation(
            _stable_id("relation", "owner-reopen-superseded-token",
                       command_id, current_token_ref.version_id),
            "supersedes", authorization_ref, current_token_ref,
            metadata={"authorization_role": "superseded_current_token"}),
            system_owned=True)
    tx.commit()
    registered_ref, registered = current_run_execution_authority(core, kernel)
    if (registered_ref != successor_ref
            or registered["latest_checkpoint_ref"] != _ref_payload(checkpoint_ref)
            or registered.get("reopen_authorization_ref")
            != _ref_payload(authorization_ref)
            or hydrate_module_runtime(core)[2].checkpoint_ref != checkpoint_ref):
        raise ResourceIntegrityFault(
            "checkpoint reentry did not become the current execution cut")
    return checkpoint_ref


def materialize_reentry_workspaces(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        checkpoint_ref: VersionRef,
) -> None:
    """Idempotently restore mutable trees before any reopened dispatch."""
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(kernel, _ResourceServiceKernel)
            or kernel._ResourceServiceKernel__core is not core):
        raise TypeError("workspace reentry materialization requires owner services")
    _, checkpoint = _registered(core, checkpoint_ref, "marking_checkpoint/v1")
    authorization_payload = checkpoint.get("reentry_authorization_ref")
    if authorization_payload is None:
        return
    authorization_ref = _version_from_payload(authorization_payload)
    _registered(core, authorization_ref, "run_reopen_authorization/v1")
    from cpn.rpnh.workspace_settlement import _archive_files, _restore_tree
    for revision_ref in _canonical_refs(checkpoint["workspace_revision_refs"]):
        _, revision = _registered(core, revision_ref, "workspace_revision/v1")
        if (revision.get("disposition") != "owner_reopen"
                or revision.get("reopen_authorization_ref")
                != _ref_payload(authorization_ref)):
            raise ResourceIntegrityFault(
                "reentry checkpoint workspace lacks owner-reopen authority")
        bindings = []
        for row in core.event_store.canonical_object_rows(
                object_type="workspace_binding/v1"):
            document = json.loads(str(row["metadata_json"]))
            if (document.get("binding_kind") == "lineage_template"
                    and document.get("workspace_lineage_ref", {}).get(
                        "logical_id") == str(revision_ref.entity_id)):
                bindings.append(document)
        roots = {item.get("allowed_root") for item in bindings}
        if len(roots) != 1 or not all(isinstance(root, str) for root in roots):
            raise ResourceIntegrityFault(
                "reentry workspace lineage has no unique registered root")
        root = _resolve_registry_workspace_root(core, next(iter(roots)))
        _restore_tree(root, _archive_files(core, revision_ref))


__all__ = (
    "committed_checkpoint_refs", "materialize_reentry_workspaces",
    "resolve_committed_checkpoint", "stage_checkpoint_reentry",
)
