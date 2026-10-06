"""Lossless ordinary graph source atoms and explicit three-way decisions.

Only source/recipe facts are editable. Module declarations and Petri primitives
are reconstructed by the unchanged ordinary builder after decisions are complete.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from ..registry.schema_catalog import canonical_json
from .graph_source import GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, _validate

NORMALIZATION = "rpnh/graph_source_normalization/v1"
ALGORITHM = "rpnh/graph_source_three_way_analysis/v1"
RESOLUTION = "rpnh/graph_source_resolution/v1"


class UnresolvedGraphMerge(ValueError):
    """No complete supported graph follows from the caller's exact selections."""


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def state(atoms, key):
    return {"present": key in atoms, "value": atoms.get(key)}


def normalize(value, origins):
    source = _validate(GRAPH_SOURCE_SCHEMA, value.source)
    recipe = _validate(GRAPH_RECIPE_SCHEMA, value.recipe)
    ids = {row["locator"]: row["element_id"] for row in value.source_map["elements"]}
    atoms, dependencies = {}, {}
    if set(origins) != set(ids.values()):
        raise UnresolvedGraphMerge("source origin evidence must cover every current identity")

    def atom(key, body, refs=()):
        atoms[key], dependencies[key] = deepcopy(body), sorted(set(refs))

    def element(locator, kind, parent, name, body, refs=()):
        identity = ids[locator]
        atom(identity + "/identity", {"kind": kind, "parent": parent}, () if parent is None else (parent,))
        atom(identity + "/name", name)
        atom(identity + "/value", body, refs)
        atom(identity + "/origin", origins[identity])
        return identity

    def endpoint(body, direction):
        return ids[f"/nodes/{body['node_id']}/{direction}_ports/{body['port_id']}"]

    graph = source.pop("graph")
    nodes, arcs = graph.pop("nodes"), graph.pop("arcs")
    ingress, egress = graph.pop("ingress"), graph.pop("egress")
    root = element("/", "graph", None, None, {"source": source, "graph": graph})
    node_ids = []
    for node in nodes:
        name = node.pop("node_id")
        locator = "/nodes/" + name
        ports = {direction: node.pop(direction + "_ports") for direction in ("input", "output")}
        node_id = element(locator, "node", root, name, node)
        node_ids.append(node_id)
        for direction, rows in ports.items():
            order = []
            for port in rows:
                label = port.pop("port_id")
                order.append(element(locator + "/" + direction + "_ports/" + label,
                    direction + "_port", node_id, label, port))
            atom(node_id + "/" + direction + "_ports_order", order, order)
    atom(root + "/nodes_order", node_ids, node_ids)
    arc_ids = []
    for arc in arcs:
        name = arc.pop("arc_id")
        arc["source"], arc["target"] = endpoint(arc["source"], "output"), endpoint(arc["target"], "input")
        arc_ids.append(element("/arcs/" + name, "arc", root, name, arc, (arc["source"], arc["target"])))
    atom(root + "/arcs_order", arc_ids, arc_ids)
    for field, body, direction in (("ingress", ingress, "input"), ("egress", egress, "output")):
        ref = endpoint(body, direction)
        element("/" + field, field, root, None, {"endpoint": ref}, (ref,))
    atom("recipe/complete", recipe)
    return {"contract": NORMALIZATION, "atoms": dict(sorted(atoms.items())),
            "dependencies": dict(sorted(dependencies.items())),
            "locators": dict(sorted((identity, locator) for locator, identity in ids.items()))}


def compare(base, local, incoming):
    models = (base, local, incoming)
    atoms = [model["atoms"] for model in models]
    keys = set().union(*(set(values) for values in atoms))
    changed = [{key for key in keys if not same(state(atoms[0], key), state(side, key))} for side in atoms[1:]]
    changes, conflicts = [], {}

    def conflict(reason, subjects):
        subjects = sorted(set(subjects))
        payload = {"reason": reason, "subjects": subjects,
            **{role: [{"subject": key, **state(values, key)} for key in subjects]
               for role, values in zip(("base", "local", "incoming"), atoms, strict=True)}}
        identity = "conflict:" + hashlib.sha256(canonical_json(payload)).hexdigest()
        conflicts[identity] = {"conflict_id": identity, **payload}

    for key in sorted(changed[0] | changed[1]):
        b, l, r = (state(values, key) for values in atoms)
        change = "same" if same(l, r) else "both" if key in changed[0] & changed[1] else "local" if key in changed[0] else "incoming"
        changes.append({"subject": key, "base": b, "local": l, "incoming": r, "change": change})
        if change == "both": conflict("divergent_atom", (key,))
    identities = [set(model["locators"]) for model in models]
    for identity in sorted(identities[1] & identities[2]):
        if not same(atoms[1][identity + "/origin"], atoms[2][identity + "/origin"]):
            conflict("identity_origin_conflict", (key for key in keys if key.startswith(identity + "/")))
    for index, other in ((0, 1), (1, 0)):
        for identity in sorted(identities[0] - identities[index + 1]):
            own = {key for key in keys if key.startswith(identity + "/")}
            if own & changed[other] and identity in identities[other + 1]:
                conflict("delete_modify", own)
            dependents = {key for key in changed[other]
                if identity in models[other + 1]["dependencies"].get(key, ()) and not key.endswith("_order")}
            if dependents: conflict("delete_dependency", own | dependents)

    def names(model, edited):
        result = {}
        for identity in model["locators"]:
            values = model["atoms"]
            label = values[identity + "/name"]
            if label is not None and {identity + "/name", identity + "/identity"} & edited:
                key = canonical_json([values[identity + "/identity"], label])
                result.setdefault(key, set()).add(identity)
        return result
    left_names, right_names = names(local, changed[0]), names(incoming, changed[1])
    for key in sorted(left_names.keys() & right_names.keys()):
        selected = left_names[key] | right_names[key]
        if len(selected) > 1:
            conflict("name_collision", (item for item in keys if item.split("/", 1)[0] in selected))

    # Source endpoints connect contracts; root ownership and inert wire order do
    # not pretend all graph facts are independent or manufacture a business answer.
    groups = {}
    def root(value):
        groups.setdefault(value, value)
        while groups[value] != value:
            groups[value] = groups[groups[value]]
            value = groups[value]
        return value
    def union(left, right):
        l, r = root(left), root(right)
        if l != r: groups[max(l, r)] = min(l, r)
    for model in models:
        for key, refs in model["dependencies"].items():
            if key.endswith("_order"): continue
            identity = key.split("/", 1)[0]
            for ref in refs:
                if model["atoms"].get(ref + "/identity", {}).get("kind") != "graph":
                    union(identity, ref)
    semantic = [{key for key in side if not key.endswith(("_order", "/origin"))} for side in changed]
    def group(key):
        if key == "recipe/complete": return "global"
        identity = key.split("/", 1)[0]
        kinds = {values.get(identity + "/identity", {}).get("kind") for values in atoms}
        return "global" if kinds & {"graph", "ingress", "egress"} else root(identity)
    if any(group(key) == "global" for side in semantic for key in side):
        shared = semantic[0] | semantic[1]
        if semantic[0] and semantic[1] and len(shared) > 1 and any(not same(state(atoms[1], key), state(atoms[2], key)) for key in shared):
            conflict("graph_recipe_interaction", shared)
    else:
        for component in sorted({group(key) for side in semantic for key in side}):
            l, r = ({key for key in side if group(key) == component} for side in semantic)
            if l and r and len(l | r) > 1 and any(not same(state(atoms[1], key), state(atoms[2], key)) for key in l | r):
                conflict("connected_source_contract", l | r)
    return changes, [conflicts[key] for key in sorted(conflicts)]


def resolve(analysis, choices):
    if analysis["status"] != "analyzed":
        raise UnresolvedGraphMerge("graph merge needs one unique nearest common base")
    if not isinstance(choices, (tuple, list)):
        raise TypeError("graph merge choices must be an explicit sequence")
    models = {role: analysis["normalized"][role]["atoms"] for role in ("base", "local", "incoming")}
    conflicts = {row["conflict_id"]: row for row in analysis["conflicts"]}
    requested, forced = {}, {}
    for row in choices:
        if not isinstance(row, dict) or set(row) != {"conflict_id", "selections", "reason"}:
            raise UnresolvedGraphMerge("every conflict choice needs exact selections and a reason")
        identity = row["conflict_id"]
        if identity not in conflicts or identity in requested:
            raise UnresolvedGraphMerge("unknown, duplicate or stale graph conflict")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise UnresolvedGraphMerge("graph conflict requires the caller's nonempty reason")
        selections = row["selections"]
        if not isinstance(selections, list): raise UnresolvedGraphMerge("conflict selections must be a list")
        chosen = {}
        for item in selections:
            if not isinstance(item, dict) or set(item) != {"subject", "side"} or item["side"] not in models:
                raise UnresolvedGraphMerge("each subject must explicitly select base, local or incoming")
            subject = item["subject"]
            if subject not in conflicts[identity]["subjects"] or subject in chosen:
                raise UnresolvedGraphMerge("unknown or duplicate conflict subject")
            selected = state(models[item["side"]], subject)
            if subject in forced and not same(forced[subject], selected):
                raise UnresolvedGraphMerge("contradictory overlapping graph choices")
            chosen[subject], forced[subject] = item["side"], selected
        if set(chosen) != set(conflicts[identity]["subjects"]):
            raise UnresolvedGraphMerge("selections must cover every exact conflict subject")
        requested[identity] = {"conflict_id": identity, "reason": row["reason"],
            "selections": [{"subject": key, "side": chosen[key]} for key in sorted(chosen)]}
    if set(requested) != set(conflicts):
        raise UnresolvedGraphMerge("caller must resolve every exact graph conflict")
    result = {}
    for key in sorted(set().union(*(set(values) for values in models.values()))):
        b, l, r = (state(models[role], key) for role in ("base", "local", "incoming"))
        if key in forced: selected = forced[key]
        elif same(l, r): selected = l
        elif same(l, b): selected = r
        elif same(r, b): selected = l
        else: raise UnresolvedGraphMerge("unresolved graph source atom: " + key)
        if selected["present"]: result[key] = deepcopy(selected["value"])
    return result, [requested[key] for key in sorted(requested)]


def reconstruct(atoms):
    entities = {key[:-9]: row for key, row in atoms.items() if key.endswith("/identity")}
    roots = [key for key, row in entities.items() if row == {"kind": "graph", "parent": None}]
    if len(roots) != 1: raise UnresolvedGraphMerge("resolution requires one graph root")
    root = roots[0]
    allowed = {"recipe/complete"}
    for identity, row in entities.items():
        required = {identity + suffix for suffix in ("/identity", "/name", "/value", "/origin")}
        if not required <= atoms.keys(): raise UnresolvedGraphMerge("incomplete graph source identity")
        allowed |= required
        if identity != root:
            parent = entities.get(row["parent"])
            expected = "node" if row["kind"] in {"input_port", "output_port"} else "graph"
            if parent is None or parent["kind"] != expected:
                raise UnresolvedGraphMerge("dangling or wrong-kind graph source parent")
    def children(parent, kind): return {key for key, row in entities.items() if row == {"parent": parent, "kind": kind}}
    def body(identity): return deepcopy(atoms[identity + "/value"])
    def name(identity): return atoms[identity + "/name"]
    def order(parent, field, kind):
        key = parent + "/" + field
        allowed.add(key)
        values = atoms.get(key)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values) or len(set(values)) != len(values) or set(values) != children(parent, kind):
            raise UnresolvedGraphMerge("unresolved exact graph order/membership: " + key)
        return values
    def endpoint(identity, kind):
        row = entities.get(identity)
        if row is None or row["kind"] != kind:
            raise UnresolvedGraphMerge("dangling/wrong-direction source endpoint")
        return {"node_id": name(row["parent"]), "port_id": name(identity)}
    root_body = body(root)
    if set(root_body) != {"source", "graph"}: raise UnresolvedGraphMerge("unknown graph source root shape")
    source, graph = root_body["source"], root_body["graph"]
    graph["nodes"], graph["arcs"], ids = [], [], {"/": root}
    def locate(locator, identity):
        if locator in ids: raise UnresolvedGraphMerge("colliding source names")
        ids[locator] = identity
    for identity in order(root, "nodes_order", "node"):
        node = body(identity); node["node_id"] = name(identity)
        locator = "/nodes/" + name(identity); locate(locator, identity)
        for direction in ("input", "output"):
            rows = []
            for port_id in order(identity, direction + "_ports_order", direction + "_port"):
                port = body(port_id); port["port_id"] = name(port_id)
                locate(locator + "/" + direction + "_ports/" + name(port_id), port_id)
                rows.append(port)
            node[direction + "_ports"] = rows
        graph["nodes"].append(node)
    for identity in order(root, "arcs_order", "arc"):
        arc = body(identity); arc["arc_id"] = name(identity)
        arc["source"], arc["target"] = endpoint(arc["source"], "output_port"), endpoint(arc["target"], "input_port")
        locate("/arcs/" + name(identity), identity); graph["arcs"].append(arc)
    for field, kind in (("ingress", "input_port"), ("egress", "output_port")):
        selected = children(root, field)
        if len(selected) != 1: raise UnresolvedGraphMerge("graph requires one exact " + field)
        identity = next(iter(selected)); value = body(identity)
        if set(value) != {"endpoint"}: raise UnresolvedGraphMerge("unknown boundary shape")
        graph[field] = endpoint(value["endpoint"], kind); locate("/" + field, identity)
    if set(ids.values()) != set(entities) or set(atoms) != allowed:
        raise UnresolvedGraphMerge("uncovered selected graph source atoms or identities")
    source["graph"] = graph
    return (_validate(GRAPH_SOURCE_SCHEMA, source), _validate(GRAPH_RECIPE_SCHEMA, atoms["recipe/complete"]),
            ids, {identity: deepcopy(atoms[identity + "/origin"]) for identity in entities})
