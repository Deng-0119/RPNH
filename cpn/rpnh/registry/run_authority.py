"""Exact run-authority reader and same-transaction checkpoint successor."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Mapping
from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .identities import TypedId, new_id
from .models import PreparedObject, TypedRelation, VersionRef
from .event_store import CanonicalView
from .resources import ResourceVersionRef
from ._event_store.collaboration_descriptors import readable_descriptor
from .resource_service import _ResourceServiceKernel
from .publication import _ref_payload, _version_from_payload, _stable_id
from .schema_catalog import canonical_json


def current_run_execution_authority(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        expected_model_condition: str | None = None,
        view: CanonicalView | None = None,
) -> tuple[VersionRef, dict[str, Any]]:
    """Return the sole authority's newest version, optionally at one canonical cut."""

    if view is not None and type(view) is not CanonicalView:
        raise TypeError("run authority requires a canonical view")
    rows = core.event_store.canonical_object_rows(
        object_type="run_execution_authority/v1",
        through_ordinal=None if view is None else view.through_ordinal)
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
        run = (kernel._exact_object(run_ref, expected_type="native_run_identity/v1")
               if view is None else kernel._exact_object_for_view(
                   view, run_ref, expected_type="native_run_identity/v1"))
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


@dataclass(frozen=True, slots=True)
class RunReadCut:
    """Current canonical read boundary, bound to one existing Registry handle.

    This is an in-process read fence, not an execution or delivery grant.
    It cannot open an owner, advance a run, or authorize a historical fallback.
    """

    view: CanonicalView
    physical_head: int
    writer_epoch: int
    task_id: TypedId
    native_run_ref: str | None
    _core: _RegistryCore = field(repr=False, compare=False)

    @classmethod
    def capture(cls, core: _RegistryCore) -> RunReadCut:
        epoch = core.event_store.writer_epoch
        view = core.event_store.canonical_view()
        cut = cls(view, view.through_ordinal, epoch, core.task_id,
                  core.event_store.get_meta("native_run_ref"), core)
        cut.assert_unchanged(core)
        return cut

    def assert_unchanged(self, core: _RegistryCore) -> None:
        if (type(self.view) is not CanonicalView
                or type(self.physical_head) is not int
                or type(self.writer_epoch) is not int
                or self.view.through_ordinal != self.physical_head
                or core is not self._core
                or core.task_id != self.task_id
                or core.event_store.max_ordinal() != self.physical_head
                or core.event_store.writer_epoch != self.writer_epoch
                or core.event_store.get_meta("native_run_ref") != self.native_run_ref):
            raise ResourceIntegrityFault("Registry advanced during read; retry the read")


@dataclass(frozen=True, slots=True)
class RunTerminalRead:
    """Exact current closure; prepared storage material stays in-process."""

    evidence_ref: VersionRef
    checkpoint_ref: VersionRef
    index_ref: VersionRef
    occurrence_ref: VersionRef
    result_ref: VersionRef
    run_outcome: str
    published_at: str
    result: PreparedObject = field(repr=False)


@dataclass(frozen=True, slots=True)
class RunExecutionRead:
    """Validated Registry execution identity at one stable current cut."""

    cut: RunReadCut
    authority_ref: VersionRef
    run_ref: VersionRef
    task_ref: VersionRef
    checkpoint_ref: VersionRef
    status: str
    execution_generation: int
    terminal: RunTerminalRead | None


def _expected_ref(value, actual: VersionRef | None, label: str) -> None:
    if value is None:
        return
    if isinstance(value, VersionRef):
        matches = value == actual
    else:
        matches = (isinstance(value, Mapping)
                   and set(value) == {"entity_type", "logical_id", "version_id"}
                   and actual is not None and dict(value) == _ref_payload(actual))
    if not matches:
        raise ResourceIntegrityFault(f"expected {label} differs from current authority")


def _read_run_descriptor(core, kernel, view, ref, kind, max_bytes):
    prepared = kernel._exact_object_for_view(view, ref, expected_type=kind)
    if max_bytes is not None and prepared.size > max_bytes:
        raise ResourceIntegrityFault("run descriptor exceeds reader byte bound")
    return readable_descriptor(core.object_store, prepared,
        max_bytes=prepared.size if max_bytes is None else max_bytes, strict=True)


def read_run_execution(
        core: _RegistryCore, kernel: _ResourceServiceKernel, *,
        cut: RunReadCut | None = None,
        expected_terminal_evidence_ref: VersionRef | Mapping[str, Any] | None = None,
        expected_run_ref: VersionRef | None = None,
        expected_task_ref: VersionRef | None = None,
        expected_net_ref: VersionRef | None = None,
        max_descriptor_bytes: int | None = None,
) -> RunExecutionRead:
    """Read the existing run authority and its exact current terminal closure.

    Expected refs are assertions only. A missing terminal expectation derives
    the current terminal, or returns ``terminal=None`` for a nonterminal run.
    Historical terminal rows never select a result. All resolution, descriptor
    bytes, direct resource provenance, and closure matching stay in Registry.
    Callers must recheck ``read.cut`` after rendering and before exposing output.
    No session grant, writer, owner, replay, resume, or new fact is created.
    """
    if (not isinstance(core, _RegistryCore)
            or not isinstance(kernel, _ResourceServiceKernel)
            or kernel._ResourceServiceKernel__core is not core):
        raise TypeError("run reader requires one existing Core and its Kernel")
    if max_descriptor_bytes is not None and (type(max_descriptor_bytes) is not int
                                             or max_descriptor_bytes < 0):
        raise TypeError("descriptor byte bound must be a nonnegative integer")
    cut = RunReadCut.capture(core) if cut is None else cut
    if not isinstance(cut, RunReadCut):
        raise TypeError("run reader requires a RunReadCut")
    cut.assert_unchanged(core)
    authority_ref, selected = current_run_execution_authority(core, kernel, view=cut.view)
    def document(ref, kind):
        return _read_run_descriptor(core, kernel, cut.view, ref, kind, max_descriptor_bytes)
    authority = document(authority_ref, "run_execution_authority/v1")
    if authority != selected:
        raise ResourceIntegrityFault("current authority differs from registered descriptor")
    run_ref = _version_from_payload(authority["run_ref"])
    task_ref = _version_from_payload(authority["task_ref"])
    run = document(run_ref, "native_run_identity/v1")
    if (run["run_id"] != str(run_ref.entity_id)
            or run["run_version_id"] != str(run_ref.version_id)
            or run["task_ref"] != _ref_payload(task_ref)
            or task_ref.entity_id != cut.task_id):
        raise ResourceIntegrityFault("current run identity differs from Registry task")
    _expected_ref(expected_run_ref, run_ref, "run")
    _expected_ref(expected_task_ref, task_ref, "task")
    checkpoint_ref = _version_from_payload(authority["latest_checkpoint_ref"])
    terminal_payload = authority["terminal_evidence_ref"]
    if (authority["status"] == "terminal") != (terminal_payload is not None):
        raise ResourceIntegrityFault("current terminal authority is internally inconsistent")
    terminal_ref = None if terminal_payload is None else _version_from_payload(terminal_payload)
    _expected_ref(expected_terminal_evidence_ref, terminal_ref, "terminal evidence")
    terminal = None
    if terminal_ref is not None:
        evidence = document(terminal_ref, "run_terminal_evidence/v1")
        checkpoint = document(checkpoint_ref, "marking_checkpoint/v1")
        index_ref = _version_from_payload(evidence["final_result_index_ref"])
        index = document(index_ref, "final_result_index/v1")
        result_ref = _version_from_payload(evidence["terminal_result_ref"])
        prepared = kernel._exact_object_for_view(cut.view, result_ref, expected_type="resource_version/v1")
        if (evidence["terminal_evidence_ref"] != _ref_payload(terminal_ref)
                or evidence["run_ref"] != _ref_payload(run_ref)
                or evidence["final_checkpoint_ref"] != _ref_payload(checkpoint_ref)
                or checkpoint["marking_checkpoint_ref"] != _ref_payload(checkpoint_ref)
                or index["final_result_index_ref"] != _ref_payload(index_ref)
                or index["terminal_result_ref"] != _ref_payload(result_ref)
                or index["terminal_occurrence_ref"] != evidence["terminal_occurrence_ref"]
                or index["terminal_outcome"] != evidence["run_outcome"]
                or prepared.metadata.get("task_ref") != _ref_payload(task_ref)):
            raise ResourceIntegrityFault("current terminal identity chain differs")
        _expected_ref(expected_net_ref, _version_from_payload(checkpoint["net_instance_ref"]), "net")
        rows = tuple(core.event_store.object_row_for_view(cut.view, ref.version_id)
                     for ref in (authority_ref, terminal_ref, index_ref))
        if any(row is None for row in rows) or len({row["transaction_id"] for row in rows}) != 1:
            raise ResourceIntegrityFault("current terminal closure is not one registered transaction")
        prepared = kernel._prepared_reference(
            ResourceVersionRef(result_ref.entity_id, result_ref.version_id),
            prepared=prepared, view=cut.view)
        event = core.event_store.event_by_id(TypedId.parse(str(rows[1]["published_event_id"]), expected="event"))
        if event is None or event.ordinal is None or event.ordinal > cut.physical_head:
            raise ResourceIntegrityFault("terminal publication is outside the read cut")
        terminal = RunTerminalRead(terminal_ref, checkpoint_ref, index_ref,
            _version_from_payload(evidence["terminal_occurrence_ref"]), result_ref,
            evidence["run_outcome"], event.occurred_at,
            replace(prepared, metadata=deepcopy(dict(prepared.metadata))))
    elif expected_net_ref is not None:
        checkpoint = document(checkpoint_ref, "marking_checkpoint/v1")
        _expected_ref(expected_net_ref, _version_from_payload(checkpoint["net_instance_ref"]), "net")
    result = RunExecutionRead(cut, authority_ref, run_ref, task_ref, checkpoint_ref,
                             authority["status"], authority.get("execution_generation", 0), terminal)
    cut.assert_unchanged(core)
    return result


def read_run_terminal_bytes(core: _RegistryCore, read: RunExecutionRead, *,
                            max_bytes: int | None = None) -> bytes:
    """Read exact registered product bytes, optionally with a physical bound."""
    read.cut.assert_unchanged(core)
    if read.terminal is None:
        raise ResourceIntegrityFault("current run has no terminal product")
    payload = core.object_store.read_registered(read.terminal.result, max_bytes=max_bytes)
    read.cut.assert_unchanged(core)
    return payload


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
    from .parent_bound import reject_bound_reentry
    reject_bound_reentry(core)
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
    from .parent_bound import reject_bound_reentry
    reject_bound_reentry(core)
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
    'RunReadCut', 'RunExecutionRead', 'RunTerminalRead',
    'read_run_execution', 'read_run_terminal_bytes',
    'current_run_execution_authority', 'stage_run_execution_checkpoint',
    'record_owner_stop', 'record_recovered_run_entry',
    'resume_owner_stopped_run')
