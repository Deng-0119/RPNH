"""Pure JSON-ready inspection of one mechanically compiled Petri net."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .executable_net import CompiledPetriNet
from .registry._registry import _RegistryCore
from .registry.module_runtime import hydrate_module_runtime
from .registry.schema_catalog import SchemaCatalog, canonical_json
from .registry.publication import _stable_id, _version_from_payload
from .registry.strict_contracts import ref_payload


SCHEMA_VERSION = "rpnh/net_view/v1"
OBSERVATION_SCHEMA_VERSION = "rpnh/net_observation/v1"
SOURCE_MODES = frozenset({"initial_configured", "registry_current"})
RESOURCE_TOKEN_KINDS = frozenset({"agent_resource", "resource_lease"})
KNOWN_TOKEN_KINDS = frozenset({
    "data", "counter", "decision", *RESOURCE_TOKEN_KINDS,
})


def _json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return _json_ready(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    return str(value)


def _native_capabilities(compiled: CompiledPetriNet) -> dict[str, dict[str, Any]]:
    """Read the common native ABI, never load an installed plugin to inspect it.

    This identifies an already-declared data place, not a new token kind, lease,
    authority or synthetic graph node. No MCP/skill/provider-specific cases.
    """
    import hashlib
    result = {}
    for operation in compiled.operations:
        contract = operation.executor_declaration["contracts"].get("native_plugin")
        if contract is None:
            continue
        declaration = operation.declaration
        binding = declaration.config.get("native_plugin")
        if (not isinstance(contract, Mapping) or not isinstance(binding, Mapping)
                or binding.get("selector") != contract.get("selector")
                or binding.get("binding_digest") != contract.get("binding_digest")
                or hashlib.sha256(canonical_json(binding["capability"])).hexdigest()
                != contract.get("capability_sha256")):
            raise ValueError("native inspection binding differs from registered contract")
        port = binding["capability_port"]
        qualified = port if port in declaration.inputs else (
            declaration.name.rsplit(".", 1)[0] + "." + port)
        place = compiled.symbolic.port_places[qualified]
        transitions = [t.name for t in compiled.symbolic.transitions
                       if t.operation == declaration.name]
        for transition in transitions:
            arcs = [a for a in compiled.symbolic.arcs if a.place == place
                    and a.transition == transition and a.direction == "input"]
            if len(arcs) != 1 or arcs[0].mode != "read":
                raise ValueError("native capability has no exact declared read arc")
            result[transition] = {
                "selector": contract["selector"],
                "plugin_version": binding["capability"]["plugin"]["version"],
                "binding_digest": contract["binding_digest"],
                "effect": contract["operation"]["effect"],
                "place": place, "input_port": qualified,
                "assets": [{k: a[k] for k in ("name", "media_type", "size_bytes", "sha256")}
                           for a in binding["capability"]["assets"]],
            }
    return result


def _resource_places(compiled: CompiledPetriNet) -> frozenset[str]:
    declared = {
        place.name for place in compiled.symbolic.places
        if place.token_kind in RESOURCE_TOKEN_KINDS
    }
    declared.update(pool.place for pool in compiled.symbolic.lease_pools)
    declared.update(item["place"] for item in _native_capabilities(compiled).values())
    return frozenset(declared)


def _marking_projection(marking: object | None) -> tuple[dict[str, int], dict[str, Any] | None]:
    if marking is None:
        return {}, None
    epoch = int(getattr(marking, "epoch"))
    active = [
        token.state for token in getattr(marking, "tokens")
        if token.state.epoch == epoch and token.state.consumed_by is None
    ]
    counts: dict[str, int] = {}
    for token in active:
        counts[token.place] = counts.get(token.place, 0) + 1
    return counts, {
        "checkpoint_ref": _json_ready(getattr(marking, "checkpoint_ref")),
        "epoch": epoch,
        "token_count": len(getattr(marking, "tokens")),
        "active_token_count": len(active),
    }


def _edge_id(edge: Mapping[str, Any], occurrence: int) -> str:
    outcome = "-" if edge["outcome"] is None else str(edge["outcome"])
    return ":".join((
        "edge", str(edge["kind"]), str(edge["source"]),
        str(edge["target"]), str(edge["mode"]), outcome,
        str(occurrence),
    ))


def _summary(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "node_count": len(nodes),
        "transition_count": sum(node["kind"] == "transition" for node in nodes),
        "place_count": sum(node["kind"] == "place" for node in nodes),
        "resource_place_count": sum(node["category"] == "resource" for node in nodes),
        "edge_count": len(edges),
        "resource_edge_count": sum(bool(edge.get("resource")) for edge in edges),
    }


def project_compiled_net(
        compiled: CompiledPetriNet, *, source: Mapping[str, Any],
        marking: object | None = None,
) -> dict[str, Any]:
    """Return a transient view without mutating the net or Registry state."""
    if not isinstance(compiled, CompiledPetriNet):
        raise TypeError("net projection requires CompiledPetriNet")
    if not isinstance(source, Mapping) or source.get("mode") not in SOURCE_MODES:
        raise ValueError("net projection source mode is not current")

    capabilities = _native_capabilities(compiled)
    capabilities_by_place = {value["place"]: value for value in capabilities.values()}
    resource_places = _resource_places(compiled)
    pools_by_place: dict[str, list[dict[str, Any]]] = {}
    for pool in compiled.symbolic.lease_pools:
        pools_by_place.setdefault(pool.place, []).append(_json_ready(pool))
    slots_by_place: dict[str, list[dict[str, Any]]] = {}
    for slot in compiled.symbolic.logical_slots:
        for role, place in (("candidate", slot.candidate_place),
                            ("published", slot.published_place)):
            slots_by_place.setdefault(place, []).append({
                "role": role, **_json_ready(slot),
            })
    marking_counts, marking_view = _marking_projection(marking)

    operations = {
        operation.declaration.name: operation
        for operation in compiled.operations
    }
    nodes: list[dict[str, Any]] = []
    for transition in compiled.symbolic.transitions:
        operation = operations[transition.operation]
        declaration = operation.declaration
        nodes.append({
            "id": transition.name,
            "label": transition.name,
            "kind": "transition",
            "category": "execution",
            "hidden_by_default": False,
            "operation": transition.operation,
            "operation_id": operation.operation_id,
            "executor": operation.executor_key,
            "executor_declaration": _json_ready(
                operation.executor_declaration),
            "inputs": list(declaration.inputs),
            "outputs": list(declaration.outputs),
            "outcomes": _json_ready(declaration.outcomes),
            "tools": list(declaration.tools),
            "config": _json_ready(declaration.config),
            "request_port": declaration.request_port,
            "budget_binding": _json_ready(declaration.budget_binding),
            "count_guards": _json_ready(transition.count_guards),
            "input_verdicts": _json_ready(transition.input_verdicts),
        })
    for node in nodes:
        if node["id"] in capabilities:
            node["capability"] = capabilities[node["id"]]
    for place in compiled.symbolic.places:
        resource = place.name in resource_places
        classification = (
            "resource" if resource else
            "place" if place.token_kind in KNOWN_TOKEN_KINDS else
            "unclassified"
        )
        node = {
            "id": place.name,
            "label": place.name,
            "kind": "place",
            "category": "resource" if resource else "place",
            "hidden_by_default": resource,
            "classification": classification,
            "schema": place.schema,
            "schema_variants": list(place.schema_variants),
            "channel": place.channel,
            "capacity": place.capacity,
            "token_kind": place.token_kind,
            "colours": _json_ready(place.colours),
            "initial_tokens": _json_ready(place.initial_tokens),
            "reusable": place.reusable,
            "resource_pools": sorted(
                pools_by_place.get(place.name, ()),
                key=lambda item: item["name"]),
            "logical_slots": sorted(
                slots_by_place.get(place.name, ()),
                key=lambda item: (item["name"], item["role"])),
        }
        if place.name in capabilities_by_place:
            node["resource_role"] = "registered_capability"
            node["capability"] = capabilities_by_place[place.name]
        if marking is not None:
            node["active_token_count"] = marking_counts.get(place.name, 0)
        nodes.append(node)

    edge_candidates: list[dict[str, Any]] = []
    for arc in compiled.symbolic.arcs:
        source_id, target_id = (
            (arc.place, arc.transition) if arc.direction == "input"
            else (arc.transition, arc.place)
        )
        resource = arc.place in resource_places
        edge_candidates.append({
            "source": source_id,
            "target": target_id,
            "kind": "arc",
            "mode": arc.mode,
            "weight": arc.weight,
            "outcome": arc.outcome,
            "hidden_by_default": resource,
            "resource": resource,
            "direction": arc.direction,
            "emit": arc.emit,
            "forward_source": arc.forward_source,
            "colour_expression": _json_ready(arc.colour_expression),
            "output_predicates": _json_ready(arc.output_predicates),
            "lease_claims": _json_ready(arc.lease_claims),
            "lease_claim_exclusions": list(arc.lease_claim_exclusions),
            "lease_claim_set": _json_ready(arc.lease_claim_set),
            "effect_selector": arc.effect_selector,
        })
    for arc in compiled.symbolic.reset_arcs:
        resource = arc.place in resource_places
        edge_candidates.append({
            "source": arc.place,
            "target": arc.transition,
            "kind": "reset_arc",
            "mode": "reset",
            "weight": 1,
            "outcome": arc.outcome,
            "hidden_by_default": resource,
            "resource": resource,
            "selector": arc.selector,
        })
    pools = {pool.name: pool for pool in compiled.symbolic.lease_pools}
    for arc in compiled.symbolic.variable_resource_arcs:
        pool = pools[arc.lease_pool]
        common = {
            "kind": "variable_resource_arc",
            "weight": 1,
            "outcome": None,
            "hidden_by_default": True,
            "resource": True,
            "lease_pool": arc.lease_pool,
            "claim_token_place": arc.claim_token_place,
            "initial_claims": _json_ready(arc.initial_claims),
        }
        edge_candidates.extend((
            {
                **common,
                "source": pool.place,
                "target": arc.transition,
                "mode": arc.input_inscription,
            },
            {
                **common,
                "source": arc.transition,
                "target": pool.place,
                "mode": arc.output_inscription,
            },
        ))

    edge_candidates.sort(key=lambda edge: (
        str(edge["kind"]), str(edge["source"]), str(edge["target"]),
        str(edge["mode"]), "" if edge["outcome"] is None else str(edge["outcome"]),
        json.dumps(edge, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
    ))
    occurrences: dict[tuple[str, str, str, str, str], int] = {}
    edges: list[dict[str, Any]] = []
    for edge in edge_candidates:
        key = (
            str(edge["kind"]), str(edge["source"]), str(edge["target"]),
            str(edge["mode"]), "" if edge["outcome"] is None else str(edge["outcome"]),
        )
        occurrences[key] = occurrences.get(key, 0) + 1
        edges.append({"id": _edge_id(edge, occurrences[key]), **edge})

    nodes.sort(key=lambda node: node["id"])
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": _json_ready(source),
        "summary": _summary(nodes, edges),
        "nodes": nodes,
        "edges": edges,
    }
    if marking_view is not None:
        result["marking"] = marking_view
    return result


def _exact_metadata(core, reference, expected_type):
    ref = _version_from_payload(reference)
    prepared = core.get_version(ref.version_id)
    if (ref.entity_type != expected_type or prepared.object_type != expected_type
            or prepared.logical_id != ref.entity_id):
        raise ValueError("inspection reference does not identify the exact Registry object")
    return prepared.metadata


def _native_receipt(core, firing_ref, invocation_ref, lease_ref, capability, phase):
    """Read an exact provisional or published receipt, without granting access.

    Do not use canonical-only queries: an active firing's failed/returned
    observation is deliberately provisional until Registry settlement.
    """
    key = f"native-plugin:{lease_ref['version_id']}:{phase}"
    version = _stable_id("resource_version", key)
    if core.event_store.object_row(version) is None:
        return None
    prepared = core.get_version(version)
    meta = prepared.metadata
    if (prepared.object_type != "resource_version/v1"
            or prepared.logical_id != _stable_id("resource", key)
            or meta.get("producer_ref") != invocation_ref
            or meta.get("lifetime_ref") != lease_ref
            or meta.get("descriptors") != {
                "native_plugin_execution": lease_ref["version_id"], "phase": phase}):
        raise ValueError("native inspection receipt has foreign provenance")
    body = json.loads(core.object_store.read_registered(prepared))
    if (body.get("schema_version") != "rpnh/native_plugin_receipt/v1"
            or body.get("execution_ref") != lease_ref
            or body.get("firing_ref") != firing_ref
            or body.get("phase") != phase
            or body.get("selector") != capability["selector"]
            or (phase == "started"
                and body.get("binding_digest") != capability["binding_digest"])):
        raise ValueError("native inspection receipt differs from its firing/contract")
    return {
        "phase": phase,
        "resource_ref": {"resource_id": str(prepared.logical_id),
                         "resource_version_id": str(prepared.version_id)},
        "code": body.get("code"),
        "outcome_unknown": body.get("outcome_unknown", False),
    }


def _execution_projection(core, compiled, net_ref, marking, projection):
    """Attach observed facts to actual nodes, not a parallel execution graph."""
    net = ref_payload(net_ref)
    nodes = {n["id"]: n for n in projection["nodes"]}
    capabilities = _native_capabilities(compiled)
    claimed_by = {}
    for node in nodes.values():
        if node["kind"] == "transition":
            node["runtime"] = {"status": "not_invoked", "firing_count": 0, "firings": []}
        else:
            node["tokens"] = []
    rows = core.event_store.object_rows_by_type("transition_firing/v1")
    for row in rows:
        firing = core.get_version(row["version_id"]).metadata
        if firing["net_instance_ref"] != net:
            continue
        transition = firing["transition_id"]
        if transition not in nodes or nodes[transition]["kind"] != "transition":
            raise ValueError("Registry firing has no transition in the adopted net")
        record = core.event_store.ordered_firing_record(row["version_id"])
        if record["firing"] != firing:
            raise ValueError("Registry firing reconstruction differs from exact metadata")
        firing_ref = firing["transition_firing_ref"]
        events = record["events"]
        admissions = [e for e in events if e.event_type == "firing_admitted/v1"
                      and e.payload.get("transition_firing_ref") == firing_ref]
        starts = [e for e in events if e.event_type == "operation_execution_started/v1"
                  and e.payload.get("transition_firing_ref") == firing_ref]
        if len(admissions) != 1 or len(starts) > 1:
            raise ValueError("firing lacks one admission or has ambiguous Start")
        admission = admissions[0]
        invocation_ref = admission.payload["invocation_ref"]
        lease_ref = admission.payload["operation_execution_lease_ref"]
        inputs = []
        for token_ref in firing["claimed_input_refs"]:
            token = _exact_metadata(core, token_ref, "petri_token/v1")
            if token["net_instance_ref"] != net:
                raise ValueError("firing claimed a token from another net")
            inputs.append({"place": token["place"], "token_ref": token_ref,
                           "resource_ref": token["resource_ref"]})
            if record["state"] == "PROVISIONAL":
                claimed_by.setdefault(token_ref["version_id"], []).append(firing_ref)
        item = {
            "firing_ref": firing_ref, "invocation_ref": invocation_ref,
            "execution_ref": lease_ref, "publication_state": record["state"],
            "admission_event_id": str(admission.event_id),
            "admission_ordinal": admission.ordinal,
            "start_event_id": str(starts[0].event_id) if starts else None,
            "start_ordinal": starts[0].ordinal if starts else None,
            "inputs": inputs, "receipts": [], "result_ref": None,
            "output_resource_refs": [], "settlement_event_id": None,
            "successor_checkpoint_ref": None,
            "status": "started" if starts else "admitted",
        }
        if starts and (starts[0].payload["invocation_ref"] != invocation_ref
                       or starts[0].payload["operation_execution_lease_ref"] != lease_ref):
            raise ValueError("Start differs from its exact firing admission")
        if transition in capabilities:
            cap = capabilities[transition]
            item["receipts"] = [r for phase in ("started", "returned", "failed")
                                if (r := _native_receipt(core, firing_ref, invocation_ref,
                                                       lease_ref, cap, phase)) is not None]
            phases = {r["phase"] for r in item["receipts"]}
            if (phases and (not starts or "started" not in phases)
                    or {"returned", "failed"}.issubset(phases)):
                raise ValueError("native observations have no consistent dispatch sequence")
            if "failed" in phases:
                item["status"] = ("outcome_unknown" if item["receipts"][-1]["outcome_unknown"]
                                  else "failed_unsettled")
            elif "returned" in phases:
                item["status"] = "returned_unsettled"
            elif "started" in phases:
                item["status"] = "dispatch_authorized"
        if record["state"] == "PUBLISHED":
            completion = record["firing_completion"]
            result = _exact_metadata(core, completion["operation_result_ref"], "operation_result/v1")
            if (result["transition_firing_ref"] != firing_ref
                    or result["invocation_ref"] != invocation_ref):
                raise ValueError("operation result belongs to another firing")
            if transition in capabilities and phases != {"started", "returned"}:
                raise ValueError("settled native firing lacks exact dispatch/return observations")
            settled = [e for e in events if e.event_type == "transition_firing_settled/v1"
                       and e.payload.get("transition_firing_ref") == firing_ref]
            if len(settled) != 1:
                raise ValueError("published firing lacks exactly one settlement")
            item.update(status="settled", business_outcome=result["business_outcome"],
                        result_ref=completion["operation_result_ref"],
                        output_resource_refs=result["output_resource_refs"],
                        settlement_event_id=str(settled[0].event_id),
                        settlement_ordinal=settled[0].ordinal,
                        successor_checkpoint_ref=completion["successor_checkpoint_ref"])
        elif record["state"] != "PROVISIONAL":
            item["status"] = "invalidated"
        nodes[transition]["runtime"]["firings"].append(item)
    for node in nodes.values():
        if node["kind"] != "transition":
            continue
        runtime = node["runtime"]
        runtime["firings"].sort(key=lambda x: x["admission_ordinal"])
        runtime["firing_count"] = len(runtime["firings"])
        if runtime["firings"]:
            runtime["status"] = runtime["firings"][-1]["status"]
    for token in marking.tokens:
        state = token.state
        nodes[state.place]["tokens"].append({
            "token_ref": ref_payload(token.token_ref),
            "resource_ref": None if state.resource_ref is None else {
                "resource_id": str(state.resource_ref.resource_id),
                "resource_version_id": str(state.resource_ref.resource_version_id)},
            "active_in_checkpoint": state.epoch == marking.epoch and state.consumed_by is None,
            "claimed_by": claimed_by.get(str(token.token_ref.version_id), []),
        })
    projection["execution"] = {
        "schema_version": "rpnh/net_execution_view/v1", "read_only": True,
        "scope": "current_adopted_net", "status_source": "Registry facts, not process liveness",
        "firing_count": sum(n.get("runtime", {}).get("firing_count", 0) for n in nodes.values()),
    }
    return projection


def project_registry_net(
        run_dir: Path, *, catalog: SchemaCatalog,
) -> dict[str, Any]:
    """Read actual topology and execution facts at one stable Registry head.

    A moving head retries with fresh read-only caches. Never mix marking at one
    head with a later receipt, or open a writer to make the viewer work.
    """
    if not isinstance(run_dir, Path) or not isinstance(catalog, SchemaCatalog):
        raise TypeError("Registry net projection requires pathlib.Path and SchemaCatalog")
    selected = run_dir.resolve()
    for _attempt in range(3):
        core = _RegistryCore(selected, create=False, read_only=True, catalog=catalog)
        before = core.event_store.max_ordinal()
        try:
            executable, structure, marking = hydrate_module_runtime(core)
            head = executable.verified_at_head
            projection = project_compiled_net(structure.compiled, source={
                "mode": "registry_current", "run_dir": str(selected),
                "net_ref": executable.net_ref,
                "verified_head_ordinal": head.ordinal,
                "writer_fencing_epoch": head.writer_fencing_epoch,
            }, marking=marking)
            projection = _execution_projection(core, structure.compiled,
                                              executable.net_ref, marking, projection)
        except (ValueError, RuntimeError):
            if core.event_store.max_ordinal() != before:
                continue
            raise
        if core.event_store.max_ordinal() == before:
            projection["execution"]["head_ordinal"] = before
            return projection
    raise RuntimeError("Registry changed during inspection; refresh to obtain a consistent view")


def project_compiled_boundaries(compiled: CompiledPetriNet) -> dict[str, Any]:
    """Describe declared boundaries, never inferred execution or terminal state."""
    if not isinstance(compiled, CompiledPetriNet):
        raise TypeError("boundary projection requires CompiledPetriNet")
    symbolic = compiled.symbolic

    def ports(endpoints: Mapping[str, Any], ports_by_name: Mapping[str, str]) -> list[dict[str, str]]:
        result = []
        for name, port in sorted(ports_by_name.items()):
            endpoint = endpoints[name]
            if port != f"{endpoint.component}.{endpoint.port}":
                raise ValueError("declared boundary differs from compiled endpoint")
            result.append({"name": name, "port": port,
                           "place": symbolic.port_places[port]})
        return result

    def terminal(rule: Any) -> dict[str, Any]:
        operation = f"{rule.source.component}.{rule.operation}"
        port = f"{rule.source.component}.{rule.source.port}"
        return {
            "key": rule.key,
            "operation": operation,
            "transition_ids": sorted(
                t.name for t in symbolic.transitions if t.operation == operation),
            "outcome": rule.outcome,
            "port": port,
            "place": symbolic.port_places[port],
        }

    return {
        "entry": ports(compiled.source.entry, symbolic.entry),
        "exit": ports(compiled.source.exit, symbolic.exit),
        "terminal_rules": [terminal(rule) for rule in (
            symbolic.terminal, *symbolic.terminal_alternatives)],
        "terminal_evidence": "not_provided",
    }


def project_registry_observation(
        run_dir: Path, *, catalog: SchemaCatalog,
) -> dict[str, Any]:
    """Capture current, verified facts for a dashboard without execution authority.

    This is deliberately separate from the v1 net view and is not an as-of
    history API. If a writer advances during the read, reject the mixed frame.
    """
    if not isinstance(run_dir, Path) or not isinstance(catalog, SchemaCatalog):
        raise TypeError(
            "Registry observation requires pathlib.Path and SchemaCatalog")
    selected = run_dir.resolve()
    core = _RegistryCore(selected, create=False, read_only=True, catalog=catalog)
    before = core.event_store.max_ordinal()
    writer_epoch = core.event_store.writer_epoch
    executable, structure, marking = hydrate_module_runtime(core)
    compiled = structure.compiled
    declared_transitions = {item.name: item for item in compiled.symbolic.transitions}
    declared_operations = {item.declaration.name: item for item in compiled.operations}
    declared_ports = {item.name: item for item in compiled.ports}

    def port_binding(name: str) -> dict[str, str]:
        port = declared_ports[name]
        return {"name": name, "port_id": port.port_id, "place": port.place}

    bindings = []
    for item in executable.transitions:
        operation = declared_operations[declared_transitions[item.transition_id].operation]
        bindings.append({
            "transition_id": item.transition_id,
            "node_ref": _json_ready(item.node_ref),
            "operation_binding_ref": _json_ready(item.operation_binding_ref),
            "executable_binding_ref": _json_ready(item.binding_ref),
            "operation": operation.declaration.name,
            "operation_id": operation.operation_id,
            "input_ports": [port_binding(name) for name in operation.declaration.inputs],
            "output_ports": [port_binding(name) for name in operation.declaration.outputs],
        })
    result = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "source": {
            "mode": "registry_current",
            "run_dir": str(selected),
            "task_id": str(core.task_id),
            "net_ref": _json_ready(executable.net_ref),
            "verified_head_ordinal": before,
            "writer_fencing_epoch": writer_epoch,
        },
        "net": project_compiled_net(compiled, source={
            "mode": "registry_current",
            "run_dir": str(selected),
            "net_ref": executable.net_ref,
            "verified_head_ordinal": before,
            "writer_fencing_epoch": writer_epoch,
        }, marking=marking),
        "boundaries": project_compiled_boundaries(compiled),
        "transition_bindings": bindings,
        "coverage": {
            "firings": "not_provided",
            "history": "unsupported",
            "token_trajectories": "not_provided",
        },
    }
    if (before != executable.verified_at_head.ordinal
            or writer_epoch != executable.verified_at_head.writer_fencing_epoch
            or core.event_store.max_ordinal() != before
            or core.event_store.writer_epoch != writer_epoch):
        raise RuntimeError("Registry advanced during display observation; retry the read")
    return result


__all__ = ("SCHEMA_VERSION", "OBSERVATION_SCHEMA_VERSION",
           "project_compiled_net", "project_compiled_boundaries",
           "project_registry_net", "project_registry_observation")
