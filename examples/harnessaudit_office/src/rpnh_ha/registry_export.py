"""Read-only projection from one completed RPNH Registry into audit evidence.

The exporter freezes one canonical Registry ordinal before enumerating facts.
Workflow arcs alone are not communication evidence: a cross-node handoff is
reported only when the recipient has an applied ``read_file`` action for the
product written by the sender.  RPNH delivery may materialize that product as
a new resource identity, so matching uses the Registry-preserved producer
invocation and workspace path in addition to the exact resource reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .jsonio import dumps, loads
from .projection import Observation, ObservationCollector
from .handoff_capture import (
    Writer, WriterIndex, RequestVisibilityIndex, render_registered_read,
)


@dataclass(frozen=True)
class RegistryExport:
    terminal_evidence_ref: dict | None
    stop_reason: str
    actual_model_calls: int
    registry_records: list[dict]
    roles_observed: tuple[str, ...]
    context_capture_complete: bool
    model_identity: dict
    capture_diagnostics: dict = field(default_factory=dict)


def _capture_complete(
        *, exported_managed: int, managed_count: int,
        handoff_communication_count: int, cross_node_reads: int,
        unmatched_product_reads: int = 0,
        unproven_visible_reads: int = 0,
        unproven_managed_results: int = 0,
        terminal_evidence_present: bool, terminal_rows_count: int) -> bool:
    """Require complete execution facts and proved communications.

    ``unproven_managed_results`` remains a model-input diagnostic. A tool
    returning to its registered host is an observable execution fact even if
    no later model request contains that return value. It is not, by itself,
    a claim that any model or other recipient consumed the value.
    """
    return (
        exported_managed == managed_count
        and handoff_communication_count == cross_node_reads
        and unmatched_product_reads == 0
        and unproven_visible_reads == 0
        and (not terminal_evidence_present or terminal_rows_count == 1)
    )


def _product_lineage(
        resource_metadata: Mapping[str, Any], *,
        workspace_path: Any = None) -> tuple[str, str] | None:
    """Return native workflow-product lineage without comparing content."""
    producer = resource_metadata.get("producer_ref")
    if (not isinstance(producer, Mapping)
            or producer.get("entity_type") != "invocation/v1"
            or not isinstance(producer.get("version_id"), str)):
        return None
    if workspace_path is None:
        descriptors = resource_metadata.get("descriptors")
        if isinstance(descriptors, Mapping):
            workspace_path = descriptors.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path:
        return None
    return producer["version_id"], workspace_path


def _authority_views(core, *, include_provisional: bool):
    """Freeze the canonical head and any exact active-firing read authority."""
    canonical = core.event_store.canonical_view()
    if not include_provisional:
        return (canonical,)
    with core.event_store.connect() as db:
        roots = db.execute(
            "SELECT firing_version_id,invocation_version_id "
            "FROM firing_publications WHERE state='PROVISIONAL' "
            "ORDER BY rowid"
        ).fetchall()
    views = [canonical]
    for root in roots:
        view = core.event_store.firing_view(
            firing_version_id=str(root["firing_version_id"]),
            invocation_version_id=str(root["invocation_version_id"]),
        )
        if view.canonical.through_ordinal != canonical.through_ordinal:
            raise RuntimeError("Registry changed while freezing authority views")
        views.append(view)
    return tuple(views)


def _rows_for_authority(core, views, object_type: str):
    """Return canonical rows plus exact temporary members of active firings."""
    canonical = views[0]
    rows = list(core.event_store.canonical_object_rows(
        through_ordinal=canonical.through_ordinal,
        object_type=object_type,
    ))
    seen = {str(row["version_id"]) for row in rows}
    temporary = {
        identity
        for view in views[1:]
        for kind, identity in view.temporary_members
        if kind == "object"
    }
    if temporary:
        with core.event_store.connect() as db:
            candidates = db.execute(
                "SELECT * FROM objects WHERE object_type=? ORDER BY rowid",
                (object_type,),
            ).fetchall()
        rows.extend(
            row for row in candidates
            if str(row["version_id"]) in temporary
            and str(row["version_id"]) not in seen
        )
    return tuple(rows)


def _document(row: Any) -> dict[str, Any]:
    value = loads(str(row["metadata_json"]))
    if not isinstance(value, dict):
        raise ValueError("Registry object metadata must be an object")
    return value


def _version_ref(value: Mapping[str, Any]):
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.models import VersionRef

    return VersionRef(
        str(value["entity_type"]),
        TypedId.parse(str(value["logical_id"])),
        TypedId.parse(str(value["version_id"])),
    )


def _resource_ref(value: Mapping[str, Any]):
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.resources import ResourceVersionRef

    return ResourceVersionRef(
        TypedId.parse(str(value["resource_id"]), expected="resource"),
        TypedId.parse(
            str(value["resource_version_id"]),
            expected="resource_version"),
    )


class _Snapshot:
    def __init__(self, run_dir: Path, *, include_provisional: bool) -> None:
        from cpn.rpnh.agent_tasks import agent_task_catalog
        from cpn.rpnh.registry._registry import _RegistryCore
        from cpn.rpnh.registry.resource_service import _ResourceServiceKernel

        self.core = _RegistryCore(
            Path(run_dir).resolve(), create=False, read_only=True,
            catalog=agent_task_catalog())
        self.kernel = _ResourceServiceKernel(self.core)
        self.views = _authority_views(
            self.core, include_provisional=include_provisional)
        self.view = self.views[0]
        self.upper = self.view.through_ordinal
        self._documents: dict[str, dict[str, Any]] = {}

    def rows(self, object_type: str):
        return _rows_for_authority(self.core, self.views, object_type)

    def _view_for(self, version_id: Any):
        for view in self.views:
            if self.core.event_store.object_row_for_view(
                    view, version_id) is not None:
                return view
        raise ValueError(
            f"Registry object is outside frozen read authority: {version_id}")

    def exact(self, value: Mapping[str, Any], *, expected: str | None = None):
        ref = _version_ref(value)
        key = str(ref.version_id)
        if key not in self._documents:
            prepared = self.kernel._exact_object_for_view(
                self._view_for(ref.version_id), ref, expected_type=expected)
            self._documents[key] = dict(prepared.metadata)
        return self._documents[key]

    def resource(self, value: Mapping[str, Any]) -> tuple[dict, bytes]:
        if "resource_id" in value:
            ref = _resource_ref(value)
        else:
            from cpn.rpnh.registry.resources import ResourceVersionRef

            version = _version_ref(value)
            if version.entity_type != "resource_version/v1":
                raise ValueError("terminal product is not a Registry resource")
            ref = ResourceVersionRef(version.entity_id, version.version_id)
        prepared = self.kernel._exact_object_for_view(
            self._view_for(ref.resource_version_id), ref.as_version_ref(),
            expected_type="resource_version/v1")
        return (
            dict(prepared.metadata),
            self.core.object_store.read_registered(prepared),
        )

    def occurred_at(self, row: Any) -> str:
        event = self.core.event_store.event_by_id(
            __import__(
                "cpn.rpnh.registry.identities", fromlist=["TypedId"]
            ).TypedId.parse(str(row["published_event_id"]), expected="event"))
        if event is None or event.ordinal is None or event.ordinal > self.upper:
            raise ValueError("Registry publication event is outside the snapshot")
        return event.occurred_at


def _node_id(snapshot: _Snapshot, action: Mapping[str, Any]) -> str:
    loop = snapshot.exact(action["agent_loop_ref"], expected="agent_loop/v1")
    invocation = snapshot.exact(
        loop["invocation_ref"], expected="invocation/v1")
    firing_ref = invocation.get("own_transition_firing_ref")
    if not isinstance(firing_ref, Mapping):
        raise ValueError("agent action lacks its own transition firing")
    firing = snapshot.exact(firing_ref, expected="transition_firing/v1")
    transition = firing.get("transition_id")
    if not isinstance(transition, str) or not transition.startswith("team."):
        raise ValueError("agent action is outside the declared workflow")
    node = transition.removeprefix("team.").removesuffix("__rework")
    if not node:
        raise ValueError("workflow transition lacks a semantic node")
    return node


def _run_id(started: Mapping[str, Any]) -> str:
    try:
        capability = started["registration"]["contracts"][
            "managed_plugin"]["capability"]
        run_id = capability["config"]["run_id"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "managed Registry receipt lacks its exact configured run identity"
        ) from exc
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("managed Registry run identity is invalid")
    return run_id


def _tool_result(action: Mapping[str, Any]) -> str:
    outcome = action["outcome"]
    if outcome == "returned":
        output = action["output"]
        if isinstance(output, Mapping) and isinstance(
                output.get("raw_result"), str):
            return output["raw_result"]
        return dumps(output)
    return dumps({
        "outcome": outcome,
        "error": action.get("error"),
        "non_delivery_reason": action.get("non_delivery_reason"),
    })



def _verify_return_boundary(snapshot, action: Mapping[str, Any],
                            started: Mapping[str, Any] | None,
                            invocation_ref: Mapping[str, Any]) -> dict:
    """Verify the registered return, never recover a result from Bank alone.

    Used only where a later model-input witness is absent. Registry action,
    start receipt, terminal receipt, invocation and exact arguments must
    agree. ``outcome_unknown`` and backend-only results cannot pass here.
    """
    if action.get("outcome") != "returned":
        raise ValueError("only a returned managed action has a return boundary")
    if not isinstance(started, Mapping) or started.get("state") != "started":
        raise ValueError("registered return lacks its started receipt")
    for name in ("started_receipt_ref", "terminal_receipt_ref"):
        if not isinstance(action.get(name), Mapping):
            raise ValueError("registered return lacks an exact receipt reference")
    _meta, payload = snapshot.resource(action["terminal_receipt_ref"])
    terminal = loads(payload)
    if not isinstance(terminal, Mapping) or terminal.get("state") != "returned":
        raise ValueError("terminal receipt is not a registered returned result")
    if terminal.get("started_receipt_ref") != action["started_receipt_ref"]:
        raise ValueError("terminal receipt names a different start")
    for name in ("call_id", "selector", "arguments", "registration",
                 "producer_invocation_ref", "caller_firing_ref"):
        if name not in started or name not in terminal or terminal[name] != started[name]:
            raise ValueError("registered return material differs: " + name)
    if (started["call_id"] != action.get("action_identity_key")
            or started["selector"] != action.get("selector")
            or started["arguments"] != action.get("arguments")
            or started["producer_invocation_ref"] != invocation_ref
            or not isinstance(started["registration"], Mapping)
            or started["registration"].get("key") != action.get("registration_key")):
        raise ValueError("registered return is not the exact owning action")
    if "output" not in terminal or terminal["output"] != action.get("output"):
        raise ValueError("registered return and action outputs differ")
    return {
        "kind": "verified_registered_managed_return/v1",
        "started_receipt_ref": dict(action["started_receipt_ref"]),
        "terminal_receipt_ref": dict(action["terminal_receipt_ref"]),
        "proves_model_input": False,
    }


def _model_identity(snapshot: _Snapshot) -> dict:
    routes = []
    for row in snapshot.rows("provider_attempt_spec/v1"):
        item = _document(row)
        route = {
            "backend": item["backend"],
            "model": item["model"],
            "transport_kind": item["transport_kind"],
            "response_protocol": item["response_protocol"],
        }
        if route not in routes:
            routes.append(route)
    call_specs = [
        _document(row)
        for object_type in ("llm_call_spec/v2", "llm_call_spec/v3")
        for row in snapshot.rows(object_type)
    ]
    return {
        "source": "canonical_registry",
        "routes": routes,
        "provider_attempt_count": len(
            snapshot.rows("provider_attempt_spec/v1")),
        "llm_call_spec_count": len(call_specs),
    }


def export_registry(
        run_dir: Path, roles_by_node: Mapping[str, str],
        observations: ObservationCollector, *,
        terminal_evidence_ref: dict | None, stop_reason: str,
        include_provisional: bool = False,
) -> RegistryExport:
    """Export exact tool, handoff, terminal, and accounting evidence."""

    snapshot = _Snapshot(
        run_dir, include_provisional=include_provisional)
    role_map = dict(roles_by_node)
    action_rows = [
        *snapshot.rows("agent_action/v2"),
        *snapshot.rows("agent_action/v3"),
    ]
    action_rows.sort(key=lambda row: snapshot.core.event_store.event_by_id(
        __import__(
            "cpn.rpnh.registry.identities", fromlist=["TypedId"]
        ).TypedId.parse(
            str(row["published_event_id"]), expected="event")
    ).ordinal or 0)

    actions: list[tuple[Any, dict, str]] = []
    roles_seen: set[str] = set()
    for row in action_rows:
        action = _document(row)
        node = _node_id(snapshot, action)
        if node not in role_map:
            raise ValueError("Registry action references an unknown workflow node")
        roles_seen.add(role_map[node])
        actions.append((row, action, node))

    registry_records: list[dict] = []
    registry_record_keys: set[bytes] = set()

    def append_registry_record(value: dict) -> None:
        key = dumps(value).encode("utf-8")
        if key not in registry_record_keys:
            registry_record_keys.add(key)
            registry_records.append(value)

    visibility = RequestVisibilityIndex.from_snapshot(snapshot)
    managed_count = 0
    exported_managed = 0
    unproven_managed_results = 0
    return_boundary_fallbacks = []
    for row, action, node in actions:
        if str(row["object_type"]) != "agent_action/v3":
            continue
        managed_count += 1
        started_ref = action.get("started_receipt_ref")
        started = None
        if isinstance(started_ref, Mapping):
            _metadata, payload = snapshot.resource(started_ref)
            started = loads(payload)
            if not isinstance(started, dict):
                raise ValueError("managed start receipt is not an object")
        timestamp = (
            action.get("admitted_at_utc")
            if isinstance(action.get("admitted_at_utc"), str)
            else snapshot.occurred_at(row))
        visible = None
        tool_result = _tool_result(action)
        return_boundary_evidence = None
        emit_observation = True
        if action["outcome"] == "returned":
            loop = snapshot.exact(
                action["agent_loop_ref"], expected="agent_loop/v1")
            visible_results = visibility.for_managed_result(
                invocation_ref=loop["invocation_ref"], action=action)
            if not visible_results:
                unproven_managed_results += 1
                return_boundary_evidence = _verify_return_boundary(
                    snapshot, action, started, loop["invocation_ref"])
                return_boundary_fallbacks.append({
                    "agent_action_ref": action["agent_action_ref"],
                    "tool_name": action["tool_name"],
                    "owning_node": node,
                    "model_input_visibility": "unproven",
                    "execution_evidence": return_boundary_evidence,
                })
                # Keep this executed call in the benchmark trace. Do not
                # fabricate a later model request or a communication event.
            else:
                tool_result = visible_results[0]["tool_result"]
                visible = {
                    "kind": "acknowledged_registered_llm_prompt_result/v1",
                    "action_model_visible_result_ref": action.get(
                        "model_visible_result_ref"),
                    "evidence": visible_results,
                }
        if emit_observation:
            observations.emit(Observation(
                "tool_call", node, role_map[node], timestamp,
                {
                    "tool_name": action["tool_name"],
                    "tool_args": action["arguments"] or {},
                    "tool_result": tool_result,
                },
                {
                    "rpnh_phase": action["outcome"],
                    "agent_action_ref": action["agent_action_ref"],
                    "request_admission_receipt_ref": started_ref,
                    "terminal_receipt_ref": action["terminal_receipt_ref"],
                    "model_visible_result": visible,
                    "non_delivery_reason": action["non_delivery_reason"],
                    **({} if return_boundary_evidence is None else {
                        "tool_result_boundary": "registered_tool_return",
                        "model_input_visibility": "unproven",
                        "return_boundary_evidence": return_boundary_evidence,
                    }),
                },
            ))
            exported_managed += 1
        if started is None:
            if action["outcome"] != "rejected":
                raise ValueError("dispatched managed action lacks admission evidence")
            continue
        common = {
            "run_id": _run_id(started),
            "operation_id": action["selector"],
            "invocation_id": started["producer_invocation_ref"]["version_id"],
            "firing_id": started["caller_firing_ref"]["version_id"],
            "call_id": action["tool_call_id"],
            "tool_name": action["tool_name"],
        }
        if action["outcome"] == "returned":
            output = action.get("output")
            request_sequence = (
                output.get("witness_request_sequence")
                if isinstance(output, Mapping) else None)
            if type(request_sequence) is not int or request_sequence < 1:
                raise ValueError(
                    "returned managed action lacks its backend witness sequence")
            common["witness_request_sequence"] = request_sequence
        append_registry_record({
            **common, "phase": "admitted",
            "registry_ref": started_ref,
        })
        terminal_ref = action.get("terminal_receipt_ref")
        if not isinstance(terminal_ref, Mapping):
            raise ValueError("dispatched managed action lacks terminal evidence")
        append_registry_record({
            **common,
            "phase": {
                "returned": "settled",
                "failed": "failed",
                "outcome_unknown": "outcome_unknown",
            }[action["outcome"]],
            "registry_ref": terminal_ref,
        })

    writers = WriterIndex()
    for order, (row, action, node) in enumerate(actions):
        metadata = action.get("result_metadata")
        if (action.get("tool_name") == "write_file"
                and action.get("state") == "ACTION_APPLIED"
                and isinstance(metadata, Mapping)
                and isinstance(metadata.get("resource_ref"), Mapping)):
            ref = metadata["resource_ref"]
            resource_metadata, _payload = snapshot.resource(ref)
            writers.add(ref=ref, metadata=resource_metadata, path=metadata.get("path"),
                        writer=Writer(node, action, row, order))

    handoff_communication_count = 0
    cross_node_reads = 0
    unmatched_product_reads = 0
    unproven_visible_reads = 0
    read_diagnostics = []
    for order, (row, action, target_node) in enumerate(actions):
        metadata = action.get("result_metadata")
        if action.get("tool_name") != "read_file" or action.get("state") != "ACTION_APPLIED":
            continue
        detail = {"read_action_ref": action.get("agent_action_ref"),
                  "target_node": target_node, "arguments": action.get("arguments")}
        read_diagnostics.append(detail)
        if not isinstance(metadata, Mapping) or not isinstance(metadata.get("resource_ref"), Mapping):
            unmatched_product_reads += 1
            detail["source_status"] = "missing_read_resource_reference"
            continue
        ref = metadata["resource_ref"]
        resource_metadata, payload = snapshot.resource(ref)
        source, method = writers.resolve(ref=ref, metadata=resource_metadata, before=order)
        detail.update(resource_ref=dict(ref), source_status=method)
        if source is None:
            if method != "known_owner_ingress":
                unmatched_product_reads += 1
            continue
        if source.node == target_node:
            detail["source_status"] = "same_node_read"
            continue
        cross_node_reads += 1
        detail["source_action_ref"] = source.action["agent_action_ref"]
        try:
            detail["read_projection"] = render_registered_read(
                resource_metadata, payload, action.get("arguments"))
        except (TypeError, ValueError) as exc:
            detail["projection_error"] = str(exc)
        loop = snapshot.exact(action["agent_loop_ref"], expected="agent_loop/v1")
        visible = visibility.for_read(invocation_ref=loop["invocation_ref"], action=action)
        if not visible:
            unproven_visible_reads += 1
            detail["visibility_status"] = "registered_request_input_unproven"
            # Never substitute the full resource, or even the reconstructed slice,
            # for missing actual request-input evidence.
            continue
        if not isinstance(metadata.get("use_receipt_ref"), Mapping):
            unproven_visible_reads += 1
            detail["visibility_status"] = "read_use_receipt_missing"
            continue
        detail["visibility_status"] = "registered_request_input_verified"
        detail["request_messages"] = visible
        content_parts = list(dict.fromkeys(item["content"] for item in visible))
        content = "\n".join(content_parts)
        observations.emit(Observation(
            "communication", source.node, role_map[source.node], snapshot.occurred_at(row),
            {"target_agent": target_node, "target_role": role_map[target_node], "content": content},
            {"handoff_context_raw": {
                "registered_tool_message_texts": list(dict.fromkeys(
                    item["raw_tool_message"] for item in visible)),
             }, "delivery_proof": metadata["use_receipt_ref"],
             "capture_evidence": {"source_action_ref": source.action["agent_action_ref"],
                                  "read_action_ref": action["agent_action_ref"],
                                  "registered_request_refs": [
                                      item["request_resource_ref"] for item in visible]},
             "capture_scope": "registered_request_input",
             "source_match_method": method,
             "content_aggregation": "distinct_registered_views_of_one_read"},
        ))
        handoff_communication_count += 1

    terminal_rows = list(snapshot.rows("run_terminal_evidence/v1"))
    if terminal_evidence_ref is not None:
        if len(terminal_rows) != 1:
            raise ValueError("terminal run must have one canonical terminal evidence")
        terminal = _document(terminal_rows[0])
        result_ref = terminal["terminal_result_ref"]
        _metadata, payload = snapshot.resource(result_ref)
        final_value = loads(payload)
        if not isinstance(final_value, str):
            raise ValueError("workflow terminal result must be text")
        final_node = next(
            (node for node in role_map if node.endswith("finalize")), None)
        if final_node is None:
            raise ValueError("workflow role map lacks its final node")
        observations.emit(Observation(
            "communication", final_node, role_map[final_node],
            snapshot.occurred_at(terminal_rows[0]),
            {
                "target_agent": "user",
                "target_role": "user",
                "content": final_value,
            },
            {
                "handoff_context_raw": {
                    "terminal_evidence_ref": terminal_evidence_ref,
                    "terminal_result_ref": result_ref,
                    "content": final_value,
                },
            },
        ))
    counts = snapshot.core.event_store.actual_model_call_counts()
    if (not isinstance(counts, tuple)
            or any(type(value) is not int or value < 0 for value in counts)):
        raise ValueError("Registry model-call accounting is malformed")
    complete = _capture_complete(
        exported_managed=exported_managed,
        managed_count=managed_count,
        handoff_communication_count=handoff_communication_count,
        cross_node_reads=cross_node_reads,
        unmatched_product_reads=unmatched_product_reads,
        unproven_visible_reads=unproven_visible_reads,
        unproven_managed_results=unproven_managed_results,
        terminal_evidence_present=terminal_evidence_ref is not None,
        terminal_rows_count=len(terminal_rows),
    )
    return RegistryExport(
        terminal_evidence_ref=terminal_evidence_ref,
        stop_reason=stop_reason,
        actual_model_calls=sum(counts),
        registry_records=registry_records,
        roles_observed=tuple(sorted(roles_seen)),
        context_capture_complete=complete,
        model_identity=_model_identity(snapshot),
        capture_diagnostics={
            "schema_version": "rpnh-ha/capture-diagnostics/v4",
            "projection_revision": "execution-return-separate-model-input-v1",
            "scope": "registered_tool_execution_proven_read_handoffs_terminal_output",
            "tool_execution_capture_complete": exported_managed == managed_count,
            "managed_result_model_input_complete": unproven_managed_results == 0,
            "registered_return_without_model_input_proof": return_boundary_fallbacks,
            "all_possible_context_surfaces_audited": False,
            "arbitrary_provider_transport_rewriting_audited": False,
            "unmatched_product_reads": unmatched_product_reads,
            "unproven_visible_reads": unproven_visible_reads,
            "unproven_managed_results": unproven_managed_results,
            "request_index_issues": visibility.issues,
            "reads": read_diagnostics,
            "complete_within_declared_scope": complete,
        },
    )


__all__ = ("RegistryExport", "export_registry")
