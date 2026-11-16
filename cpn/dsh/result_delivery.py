"""Carry explicitly delivered DSH results through existing Owner inputs.

This is a trusted MainThread HOST adapter, not a cross-Registry read grant.
Only committed turns of the supplied original thread can contribute. Their
selected current terminal product must have been produced by the registered
DSH export executor. Original run readers enforce terminal identity, current
body availability and the governed-delivery boundary. Historical answer JSON
is never an authority or a source of result bodies.
"""
from __future__ import annotations

import json
import stat
from collections.abc import Mapping

from cpn.plugins.managed_output import (
    MANAGED_OPERATION_RESULT_IMPORT_KIND,
    MANAGED_OPERATION_RESULTS_EXPORT_TOOL,
    validate_managed_operation_result_delivery,
    validate_managed_operation_result_import,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import (
    read_run_execution,
    read_run_terminal_bytes,
)


MANAGED_TERMINAL_EXECUTOR = "rpnh/dsh-managed-terminal/v1"
_RESULT_EXECUTORS = frozenset({
    "rpnh/dsh-managed-results/v1", "rpnh/dsh-managed-results/v2"})


class ManagedResultDeliveryError(ValueError):
    """Original committed-source delivery cannot be closed at this boundary."""


def _ref(value, kind):
    ref = value if isinstance(value, VersionRef) else _version_from_payload(value)
    if ref.entity_type != kind:
        raise ManagedResultDeliveryError(f"result delivery requires exact {kind}")
    return ref


def _descriptor(kernel, read, value, kind):
    ref = _ref(value, kind)
    return kernel._exact_object_for_view(
        read.cut.view, ref, expected_type=kind).metadata


def _receipt(thread, cut, committed):
    """Reclose the existing committed/running/receipt links, without body IO."""
    committed_ref = _ref(committed["main_turn_ref"], "main_turn/v1")
    running_ref = _ref(committed["previous_version_ref"], "main_turn/v1")
    running = thread._read_exact(
        thread.core, running_ref, expected_type="main_turn/v1", view=cut.view)
    unchanged = ("main_thread_id", "ordinal", "predecessor_turn_ref",
                 "user_input", "attempt_relative_path")
    if (committed.get("state") != "committed"
            or running.get("state") != "running"
            or running_ref.entity_id != committed_ref.entity_id
            or any(committed.get(key) != running.get(key) for key in unchanged)):
        raise ManagedResultDeliveryError("result delivery turn differs from its running source")
    receipt_ref = _ref(
        committed["execution_receipt_ref"], "main_turn_execution_receipt/v1")
    receipt = thread._read_exact(
        thread.core, receipt_ref,
        expected_type="main_turn_execution_receipt/v1", view=cut.view)
    if (receipt.get("main_turn_execution_receipt_ref") != _ref_payload(receipt_ref)
            or receipt.get("main_turn_ref") != _ref_payload(running_ref)
            or receipt.get("attempt_relative_path") != running["attempt_relative_path"]
            or receipt.get("outcome") != "terminal"):
        raise ManagedResultDeliveryError("result delivery receipt differs from its original turn")
    return committed_ref, receipt_ref, receipt


def _selected_source(thread, receipt):
    """Resolve one original receipt to its current selected producer facts."""
    # The path comes only from the exact registered parent turn/receipt pair.
    child = _RegistryCore(thread.resolve_child_path(
        receipt["attempt_relative_path"]), create=False, read_only=True)
    kernel = _ResourceServiceKernel(child)
    read = read_run_execution(
        child, kernel,
        expected_run_ref=_ref(receipt["child_run_ref"], "native_run_identity/v1"),
        expected_task_ref=_ref(receipt["child_task_ref"], "task/v1"),
        expected_terminal_evidence_ref=receipt["terminal_evidence_ref"])
    if read.status != "terminal" or read.terminal is None:
        raise ManagedResultDeliveryError("result delivery source is no longer terminal")
    observed = {
        "validated_through_ordinal": read.cut.physical_head,
        "child_task_ref": _ref_payload(read.task_ref),
        "child_run_ref": _ref_payload(read.run_ref),
        "run_execution_authority_ref": _ref_payload(read.authority_ref),
        "final_checkpoint_ref": _ref_payload(read.checkpoint_ref),
        "outcome": read.status,
        "run_outcome": read.terminal.run_outcome,
        "terminal_evidence_ref": _ref_payload(read.terminal.evidence_ref),
        "terminal_result_ref": _ref_payload(read.terminal.result_ref),
        "final_result_index_ref": _ref_payload(read.terminal.index_ref),
    }
    if any(receipt.get(key) != value for key, value in observed.items()):
        raise ManagedResultDeliveryError("result delivery source differs from its recorded terminal receipt")

    metadata = read.terminal.result.metadata
    if metadata.get("origin_kind") != "petri_output":
        # Existing non-DSH/legacy terminal products are not retroactive exports.
        read.cut.assert_unchanged(child)
        return child, kernel, read, None, None, None
    binding_ref = metadata.get("reference_provenance", {}).get("operation_binding_ref")
    binding = _descriptor(kernel, read, binding_ref, "operation_binding/v1")
    spec = _descriptor(kernel, read, binding["operation_spec_ref"], "operation_spec/v1")
    if spec.get("executor_key") == MANAGED_TERMINAL_EXECUTOR:
        if (spec.get("implementation_identity") != {
                "implementation_id": "rpnh.dsh.managed_terminal", "revision": "v1"}
                or MANAGED_OPERATION_RESULTS_EXPORT_TOOL not in spec.get("allowed_tool_ids", ())):
            raise ManagedResultDeliveryError("result terminal differs from its original HOST identity or exporter")
    read.cut.assert_unchanged(child)
    return child, kernel, read, binding_ref, binding, spec


def _read_source(thread, cut, committed, *, optional=False, max_terminal_bytes=None, notices=None):
    turn_ref, receipt_ref, receipt = _receipt(thread, cut, committed)
    if optional:
        path = thread.resolve_child_path(receipt["attempt_relative_path"])
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            notices.append({"main_turn_ref": _ref_payload(turn_ref),
                            "reason": "source_directory_unavailable"})
            return None, None
        if not stat.S_ISDIR(mode):
            raise ManagedResultDeliveryError("original source path is not a directory")
    child, kernel, read, binding_ref, binding, spec = _selected_source(thread, receipt)
    if spec is None or spec.get("executor_key") != MANAGED_TERMINAL_EXECUTOR:
        # Do not read a legacy body's bytes merely to look for a field that its
        # original executor never declared. Its old inline history is retained.
        read.cut.assert_unchanged(child)
        return None, (child, read)

    # This original reader checks current availability and refuses governed
    # sources without their real delivery path. No raw ObjectStore fallback.
    if optional:
        from cpn.rpnh.material_body_policy import resolve_material_body_availability
        from cpn.rpnh.material_availability import source_governs_materials
        body_ref = VersionRef(read.terminal.result.object_type,
            read.terminal.result.logical_id, read.terminal.result.version_id)
        availability = resolve_material_body_availability(child, body_ref)
        reason = None
        if availability["availability"] == "disabled":
            reason = "source_body_disabled"
        elif availability["availability"] != "available":
            # Unknown proof is not evidence that a source may be safely omitted.
            raise ManagedResultDeliveryError("source material availability proof is unknown")
        elif source_governs_materials(child):
            reason = "source_governed_delivery_required"
        elif max_terminal_bytes is not None and read.terminal.result.size > max_terminal_bytes:
            # Conservative whole-terminal read bound, not claimed body size.
            reason = "terminal_read_budget_exhausted"
        if reason:
            notices.append({"main_turn_ref": _ref_payload(turn_ref), "reason": reason})
            return None, (child, read)
    payload = read_run_terminal_bytes(child, read, max_bytes=max_terminal_bytes)
    output = json.loads(payload)
    if not isinstance(output, Mapping) or "managed_result_delivery" not in output:
        raise ManagedResultDeliveryError("registered DSH terminal omitted its explicit result delivery")
    delivery = output["managed_result_delivery"]
    validate_managed_operation_result_delivery(delivery)
    if (delivery["source_task_ref"] != _ref_payload(read.task_ref)
            or delivery["source_run_ref"] != _ref_payload(read.run_ref)):
        raise ManagedResultDeliveryError("result delivery names another source task or run")

    # The delivery producer is the actual terminal-product invocation, not a
    # caller-supplied firing or a token occurrence confused with a firing.
    metadata = read.terminal.result.metadata
    producer_ref = _ref(metadata["producer_ref"], "invocation/v1")
    invocation = _descriptor(kernel, read, producer_ref, "invocation/v1")
    firing_ref = _ref(invocation["own_transition_firing_ref"], "transition_firing/v1")
    execution_ref = _ref(invocation["operation_execution_lease_ref"],
                         "operation_execution_lease/v1")
    if (delivery["producer_firing_ref"] != _ref_payload(firing_ref)
            or delivery["producer_execution_ref"] != _ref_payload(execution_ref)
            or invocation["operation_binding_ref"] != binding_ref
            or invocation["task_ref"] != _ref_payload(read.task_ref)):
        raise ManagedResultDeliveryError("result delivery producer differs from the selected terminal product")
    lease = _descriptor(kernel, read, execution_ref, "operation_execution_lease/v1")
    firing = _descriptor(kernel, read, firing_ref, "transition_firing/v1")
    record = child.event_store.ordered_firing_record(firing_ref.version_id)
    starts = tuple(event for event in record["events"]
                   if event.event_type == "operation_execution_started/v1")
    if (record["state"] != "PUBLISHED" or len(starts) != 1
            or lease["invocation_ref"] != _ref_payload(producer_ref)
            or firing["operation_binding_ref"] != binding_ref
            or starts[0].ordinal > read.cut.physical_head):
        raise ManagedResultDeliveryError("result delivery lacks its original terminal execution Start")
    expected_start = {
        "invocation_ref": _ref_payload(producer_ref),
        "operation_execution_lease_ref": _ref_payload(execution_ref),
        "operation_binding_ref": binding_ref,
        "operation_spec_ref": binding["operation_spec_ref"],
        "transition_firing_ref": _ref_payload(firing_ref),
    }
    if any(starts[0].payload.get(key) != value for key, value in expected_start.items()):
        raise ManagedResultDeliveryError("result delivery Start differs from the original producer")
    source_binding = _descriptor(
        kernel, read, delivery["source_operation_binding_ref"], "operation_binding/v1")
    source_spec = _descriptor(
        kernel, read, source_binding["operation_spec_ref"], "operation_spec/v1")
    source_node = _descriptor(
        kernel, read, source_binding["node_ref"], "node_declaration/v1")
    if (source_node.get("transition_id") != "dsh.tool"
            or source_spec.get("executor_key") not in _RESULT_EXECUTORS):
        raise ManagedResultDeliveryError("result delivery source is not the registered managed tool operation")
    if source_spec.get("implementation_identity") != {
            "implementation_id": "rpnh.dsh.managed_results",
            "revision": source_spec["executor_key"].rsplit("/", 1)[-1]}:
        raise ManagedResultDeliveryError("result delivery source differs from its original HOST implementation")
    read.cut.assert_unchanged(child)
    return {
        "main_turn_ref": _ref_payload(turn_ref),
        "execution_receipt_ref": _ref_payload(receipt_ref),
        "terminal_evidence_ref": _ref_payload(read.terminal.evidence_ref),
        "terminal_result_ref": _ref_payload(read.terminal.result_ref),
        "final_result_index_ref": _ref_payload(read.terminal.index_ref),
        "delivery": delivery,
    }, (child, read)


def is_managed_result_terminal(thread, *, turn_ref, receipt_ref):
    """Select the new terminal display projection from original source facts.

    This reads no terminal body and grants no resource access. The original
    running turn and freshly recorded receipt are also available during the
    terminal-before-parent-commit recovery window. A similarly named field in
    an old or reader-disabled terminal remains ordinary application content.
    """
    if not isinstance(thread, MainThreadRegistry):
        raise TypeError("result terminal selection requires the original MainThreadRegistry")
    cut = thread.capture_read_cut()
    epoch = thread.core.event_store.writer_epoch
    exact_turn = _ref(turn_ref, "main_turn/v1")
    exact_receipt = _ref(receipt_ref, "main_turn_execution_receipt/v1")
    turn = thread._read_exact(thread.core, exact_turn,
                              expected_type="main_turn/v1", view=cut.view)
    receipt = thread._read_exact(thread.core, exact_receipt,
                                 expected_type="main_turn_execution_receipt/v1", view=cut.view)
    if (turn.get("state") != "running"
            or turn.get("main_thread_id") != str(cut.thread_ref.entity_id)
            or receipt.get("main_turn_execution_receipt_ref") != _ref_payload(exact_receipt)
            or receipt.get("main_turn_ref") != _ref_payload(exact_turn)
            or receipt.get("attempt_relative_path") != turn.get("attempt_relative_path")
            or receipt.get("outcome") != "terminal"):
        raise ManagedResultDeliveryError("result terminal selector lacks its original running turn and receipt")
    child, _kernel, read, _binding_ref, _binding, spec = _selected_source(thread, receipt)
    selected = spec is not None and spec.get("executor_key") == MANAGED_TERMINAL_EXECUTOR
    read.cut.assert_unchanged(child)
    thread._validate_read_cut(cut)
    if (thread.core.event_store.max_ordinal() != cut.view.through_ordinal
            or thread.core.event_store.writer_epoch != epoch):
        raise ManagedResultDeliveryError("parent Registry advanced during terminal projection selection")
    return selected


def collect_result_deliveries(thread, *, expected_thread_ref=None, optional=False, max_import_bytes=None, notices=None):
    """Return one immutable-input document from this idle original MainThread.

    Each earlier run contributes only the delta explicitly exported in its
    selected terminal product. The caller registers the returned data through
    ordinary ``start_run(resource_inputs=...)``. This reads historical source
    material once per new run; it does not keep a second persistent directory,
    promise zero-copy transfer or grant access to the original child later.
    Resume uses the already registered local resource instead of this helper.
    """
    if type(optional) is not bool:
        raise TypeError("optional import mode requires bool")
    if max_import_bytes is not None and (type(max_import_bytes) is not int or max_import_bytes < 0):
        raise ValueError("import budget requires exact nonnegative integer")
    if not optional and (max_import_bytes is not None or notices is not None):
        raise ValueError("bounded import and notices require explicit optional mode")
    if notices is not None and not isinstance(notices, list):
        raise TypeError("import notices require a list")
    if not isinstance(thread, MainThreadRegistry):
        raise TypeError("result delivery requires the original MainThreadRegistry")
    cut = thread.capture_read_cut()
    projection = thread.project_thread_at(cut)
    if projection.state != "idle" or projection.active_turn_ref is not None:
        raise ManagedResultDeliveryError("result delivery must precede accepting the next main turn")
    if (expected_thread_ref is not None
            and _ref(expected_thread_ref, "main_thread/v1") != cut.thread_ref):
        raise ManagedResultDeliveryError("result delivery parent differs from the expected thread version")
    parent_head = thread.core.event_store.max_ordinal()
    parent_epoch = thread.core.event_store.writer_epoch
    if parent_head != cut.view.through_ordinal:
        raise ManagedResultDeliveryError("parent Registry advanced before result delivery")
    deliveries, sources, staged_notices = [], [], []
    refs = tuple(turn.turn_ref for turn in projection.turns)
    size = thread.HISTORY_PAGE_MAX_ENTRIES
    for offset in range(0, len(refs), size):
        _projection, documents = thread._committed_turn_documents_at(
            cut, refs[offset:offset + size])
        for document in documents:
            current = {"schema_version": MANAGED_OPERATION_RESULT_IMPORT_KIND,
                       "parent_thread_ref": _ref_payload(cut.thread_ref), "deliveries": deliveries}
            used = len(json.dumps(current, ensure_ascii=True, allow_nan=False,
                                  separators=(",", ":")).encode()) if deliveries else 0
            remaining = None if max_import_bytes is None else max(0, max_import_bytes - used)
            if optional:
                delivery, source = _read_source(thread, cut, document,
                    optional=True, max_terminal_bytes=remaining, notices=staged_notices)
            else:
                delivery, source = _read_source(thread, cut, document)
            if source is not None:
                sources.append(source)
            if delivery is not None:
                candidate = {**current, "deliveries": [*deliveries, delivery]}
                total = len(json.dumps(candidate, ensure_ascii=True, allow_nan=False,
                                       separators=(",", ":")).encode())
                if max_import_bytes is not None and total > max_import_bytes:
                    staged_notices.append({"main_turn_ref": delivery["main_turn_ref"],
                                           "reason": "import_document_budget_exhausted"})
                else:
                    deliveries.append(delivery)
    result = {
        "schema_version": MANAGED_OPERATION_RESULT_IMPORT_KIND,
        "parent_thread_ref": _ref_payload(cut.thread_ref),
        "deliveries": deliveries,
    }
    validate_managed_operation_result_import(result)
    for child, read in sources:
        read.cut.assert_unchanged(child)
    thread._validate_read_cut(cut)
    if (thread.core.event_store.max_ordinal() != parent_head
            or thread.core.event_store.writer_epoch != parent_epoch):
        raise ManagedResultDeliveryError("parent Registry advanced during result delivery")
    if notices is not None:
        notices.extend(staged_notices)
    return result
