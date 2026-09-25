"""Read-only lifecycle observations and the shared minimal live wait graph.

Neither a cycle nor its absence is a global Petri-net liveness proof.
Subrefs in lifecycle metadata are exact recorded identities, not independently
published objects; the immutable lifecycle document is their authority.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .publication import _version_from_payload
from .schema_catalog import canonical_json, canonical_text
from .strict_contracts import ref_payload


def resource_wait_cycle(documents: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Shortest cycle; preserve the default facade's existing graph semantics."""
    waiting = tuple(item for item in documents if item.get("state") == "waiting_resource")
    held = tuple(item for item in documents if item.get("state") in {"granted", "resumed"})
    edges = []
    for request in waiting:
        for authority in held:
            if (request.get("transition_firing_ref") == authority.get("transition_firing_ref")
                    or request.get("requested_resource_ref") != authority.get("requested_resource_ref")
                    or (request.get("mode") == "read" and authority.get("mode") == "read")):
                continue
            required = (request.get("lifecycle_ref"), request.get("request_ref"),
                request.get("queue_entry_ref"), request.get("transition_firing_ref"),
                request.get("requested_resource_ref"), authority.get("lifecycle_ref"),
                authority.get("transition_firing_ref"), authority.get("grant_ref"),
                authority.get("lease_ref"))
            if any(not isinstance(value, Mapping) for value in required):
                raise ValueError("resource wait edge lacks exact Registry authority refs")
            edges.append(dict(zip(("waiting_lifecycle_ref", "request_ref", "queue_entry_ref",
                "waiting_firing_ref", "resource_ref", "held_lifecycle_ref", "holder_firing_ref",
                "held_grant_ref", "held_lease_ref"), (dict(value) for value in required))))
    edges.sort(key=canonical_text)
    by_waiter = {}
    for edge in edges:
        by_waiter.setdefault(str(edge["waiting_firing_ref"]["version_id"]), []).append(edge)
    cycles = []
    for first in edges:
        source = str(first["waiting_firing_ref"]["version_id"])
        target = str(first["holder_firing_ref"]["version_id"])
        frontier = [(target, (first,), frozenset({source, target}))]
        while frontier:
            node, path, visited = frontier.pop(0)
            if node == source:
                cycles.append(path)
                break
            for edge in by_waiter.get(node, ()):
                next_node = str(edge["holder_firing_ref"]["version_id"])
                if next_node != source and next_node in visited:
                    continue
                frontier.append((next_node, (*path, edge), visited | {next_node}))
    return min(cycles, key=lambda cycle: (len(cycle), canonical_text(list(cycle)))) if cycles else ()


def resource_wait_snapshot(core, *, task_ref, net_ref, active_firing_refs):
    """Detached latest immutable lifecycle facts, without writing or fencing.

    Keep historical heads as provenance; graph input is restricted to exact
    current task/net/active firing identities and the actual Registry epoch.
    """
    epoch = core.event_store.writer_epoch
    result = {"status": "AVAILABLE", "writer_fencing_epoch": epoch,
        "task_ref": ref_payload(task_ref), "net_ref": ref_payload(net_ref),
        "lifecycles": [], "live_documents": [], "global_liveness": "UNKNOWN"}
    heads = {}
    for row in core.event_store.object_rows_by_type("resource_access_lifecycle/v1"):
        heads[str(row["logical_id"])] = row
    active = [ref_payload(ref) for ref in active_firing_refs]
    try:
        for key in sorted(heads):
            row = heads[key]
            ref = _version_from_payload({"entity_type": row["object_type"],
                "logical_id": row["logical_id"], "version_id": row["version_id"]})
            prepared = core.get_version(ref.version_id)
            payload = core.object_store.read_registered(prepared)
            document = json.loads(payload)
            core.catalog.validate_instance("resource_access_lifecycle/v1", category="object", instance=document)
            if (prepared.schema_ref != "registry_v1/resource_access_lifecycle/v1"
                    or document != prepared.metadata or payload != canonical_json(document)
                    or document["lifecycle_ref"] != ref_payload(ref)):
                raise ValueError("lifecycle payload/schema/ref differs from Registry metadata")
            firing_ref = _version_from_payload(document["transition_firing_ref"])
            firing = core.get_version(firing_ref.version_id)
            if (firing.object_type != firing_ref.entity_type
                    or firing.object_type != "transition_firing/v1"
                    or firing.logical_id != firing_ref.entity_id
                    or firing.metadata["transition_firing_ref"] != ref_payload(firing_ref)):
                raise ValueError("lifecycle firing identity differs from Registry")
            current = (document["writer_fencing_epoch"] == epoch
                and firing.metadata["task_ref"] == result["task_ref"]
                and firing.metadata["net_instance_ref"] == result["net_ref"]
                and ref_payload(firing_ref) in active)
            result["lifecycles"].append({"document": document,
                "schema_ref": prepared.schema_ref, "storage_locator": prepared.storage_locator,
                "published_event_id": str(row["published_event_id"]),
                "transaction_id": str(row["transaction_id"]),
                "firing_task_ref": firing.metadata["task_ref"],
                "firing_net_ref": firing.metadata["net_instance_ref"], "current_scope": current})
            if current:
                result["live_documents"].append(document)
        if core.event_store.writer_epoch != epoch:
            raise ValueError("Registry epoch changed during observation")
    except Exception as exc:
        result.update(status="UNKNOWN", reason=f"Lifecycle observation unavailable: {type(exc).__name__}: {exc}",
                      live_documents=[])
    return result
