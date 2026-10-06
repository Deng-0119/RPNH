"""Versioned same-source composition over rebuilt ordinary graph Modules."""
from __future__ import annotations

from ..compiler import compile_module
from ..composition import compose_fragments
from ..module import ModuleDeclaration
from ..net_operations.definitions import ComposeConnection, ComposePlan, compose_modules
from ..registry.schema_catalog import canonical_json
from ._assembly_lowering import prefix, _contract_carriers, lowering_map
from .assembly_v8 import resolver_recipe, CONSTRAINTS_CONTRACT, LOWERING_V8_SCHEMA, _member_kind
from .graph_source import rebuild_graph_module


def _members(plan, members):
    if canonical_json(plan["resolver_recipe"]) != canonical_json(resolver_recipe()):
        raise ValueError("unknown or altered Assembly resolver recipe")
    if plan["budget_policy"] != "shared_exact" or plan["deployment_intent"] != "same_run_candidate":
        raise ValueError("Assembly v8 requires explicit shared_exact and same_run_candidate")
    rows = plan["members"]
    keys = [row["member_id"] for row in rows]
    if not keys or keys != sorted(set(keys)) or set(members) != set(keys):
        raise ValueError("Assembly v8 requires unique canonical exact members")
    budgets, buckets, constraints = {}, {}, []
    for row in rows:
        member = members[row["member_id"]]
        kind = _member_kind(member)
        if member.revision.revision_ref.to_dict() != row["revision_ref"]:
            raise ValueError("Assembly member differs from its exact selected reference")
        graph = kind in {"ordinary_graph_v2", "merged_graph_v3"}
        if graph:
            rebuilt = rebuild_graph_module(member.source, member.recipe)
            if canonical_json(rebuilt.to_dict()) != canonical_json(member.module.to_dict()):
                raise ValueError("Assembly graph member differs from whole source rebuild")
            expected = {"topology_authority": "declared_arcs", "array_order_semantics": "inert",
                "graph_generation": "dependency_graph_with_bounded_feedback_v3",
                "max_rework_cycles": member.source["graph"]["max_rework_cycles"]}
            if canonical_json(member.module.designer_constraints) != canonical_json(expected):
                raise ValueError("unknown graph member constraints")
        elif (member.revision.revision_ref.ref.entity_type != "collaboration_net_revision/v1"
                or member.module.designer_constraints
                or any(c.key in {f"rpnh/agent-workflow-graph/v{n}" for n in range(1,5)} for c in member.module.components)):
            raise ValueError("plain v1 member cannot supply graph or opaque constraints proof")
        doc = member.module.to_dict()
        constraints.append({"member_id": row["member_id"], "constraints": doc["designer_constraints"]})
        for target, values, label in ((budgets, doc["budgets"].items(), "budget"),
                (buckets, ((b["bucket_id"], b) for b in doc["budget_buckets"]), "budget bucket")):
            for key, value in values:
                if key in target and canonical_json(target[key]) != canonical_json(value):
                    raise ValueError("shared_exact conflicting " + label + ": " + key)
                target[key] = value
        # No mutation/remapping of member operation bindings is permitted.
        own = {b["bucket_id"]: b for b in doc["budget_buckets"]}
        for component in member.module.components:
            for operation in component.operations:
                binding = operation.budget_binding
                if binding is not None:
                    bucket = own.get(binding.bucket_id)
                    if bucket is None or any(getattr(binding, k) != bucket[k] for k in ("budget_scope", "finalization_scope")):
                        raise ValueError("member budget binding differs from its exact bucket")
    return {"contract": CONSTRAINTS_CONTRACT, "members": constraints}


def compose_declarations(plan, members, *, connections=True):
    projection = _members(plan, members)
    lookup = {key: {row["element_id"]: row for row in value.element_map["elements"]}
              for key, value in members.items()}
    selected = lookup.get(plan["completion"]["member_id"], {}).get(plan["completion"]["terminal_element_id"])
    if selected is None or selected["locator"] != "/terminal":
        raise ValueError("completion must select the exact member primary terminal")
    links, targets = [], set()
    for connection in plan["connections"] if connections else ():
        left, right = connection["source_member_id"], connection["target_member_id"]
        endpoints = []
        for member_id, key, kind in ((left,"source_exit_element_id","exit"),(right,"target_entry_element_id","entry")):
            element = lookup.get(member_id, {}).get(connection[key])
            if element is None or element["kind"] != kind:
                raise ValueError("connection must select exact member boundary elements")
            endpoints.append(element["locator"].split("/",2)[2])
        endpoint = members[right].module.entry[endpoints[1]]
        identity = (right, endpoint.component, endpoint.port)
        if identity in targets or sum(v == endpoint for v in members[right].module.entry.values()) != 1:
            raise ValueError("connected entry cannot alias or have multiple producers")
        targets.add(identity)
        links.append(ComposeConnection(prefix(left), endpoints[0], prefix(right), endpoints[1]))
    module = compose_modules({prefix(row["member_id"]): members[row["member_id"]].module for row in plan["members"]},
        ComposePlan(plan["name"], prefix(plan["completion"]["member_id"]), tuple(links), mode="explicit"))
    document = module.to_dict()
    document["designer_constraints"]["assembly_member_constraints"] = projection
    return ModuleDeclaration.from_dict(document)


def compose_plan_v8(plan, members, registration):
    module = compose_declarations(plan, members)
    compiled = compile_module(module, registration)
    baseline = compose_declarations(plan, members, connections=False)
    return _contract_carriers(plan, members, module, compiled, baseline_module=baseline)


def lowering_map_v8(plan, members, module, compiled, assembly_identity):
    from ._graph_assembly_origins import graph_origins
    baseline, _ = compose_fragments(compose_declarations(plan, members, connections=False), compiled.fragments)
    mapping, ids = lowering_map(plan, members, module, compiled, assembly_identity, baseline=baseline)
    mapping["schema_version"] = LOWERING_V8_SCHEMA
    mapping.update(graph_origins(plan, members, module, compiled, mapping))
    for row in mapping["graph_members"]:
        member = members[row["member_id"]]
        if _member_kind(member) == "merged_graph_v3":
            row.update(author_command_ref=member.command_ref.to_dict(),
                       resolution_ref=member.resolution_ref.to_dict())
    # Preserve the complete validated introduction/copy evidence independently
    # for every instance. The ordinary builder's source roles remain unchanged.
    from copy import deepcopy
    for row in mapping["graph_source_origins"]:
        member = members[row["member_id"]]
        origin = None
        if _member_kind(member) == "merged_graph_v3":
            identities = {item["element_id"] for item in member.source_map["elements"]}
            if set(member.identity_origins) != identities:
                raise ValueError("Assembly v8 merged graph requires complete identity origin coverage")
            origin = member.identity_origins[row["source_element_id"]]
            if origin["element_id"] != row["source_element_id"]:
                raise ValueError("Assembly v8 graph introduction identity differs from its source role")
        row["identity_origin"] = deepcopy(origin)
    return mapping, ids


def check_lowering_map_v8(plan, members, module, compiled, assembly_identity, persisted):
    """Pure comparison only; Registry authority is the full consumer's job."""
    expected, ids = lowering_map_v8(plan, members, module, compiled, assembly_identity)
    if canonical_json(persisted) != canonical_json(expected):
        raise ValueError("Assembly v8 source/declaration/actual-fragment origin map differs")
    return ids
