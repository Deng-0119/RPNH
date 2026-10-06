"""Explicit author choices and lossless stable-reference reconstruction only."""
from __future__ import annotations

from copy import deepcopy

from ..module import ModuleDeclaration
from ..registry.schema_catalog import canonical_json
from ._plain_merge_model import _same, _state

RESOLUTION_ALGORITHM = "rpnh/plain_author_resolution/v1"


class UnresolvedPlainMerge(ValueError):
    """The explicit selections do not describe one complete closed author model."""


def resolve_atoms(analysis, choices):
    models = analysis["normalized"]
    sides = {role: models[role]["atoms"] for role in ("base", "local", "incoming")}
    conflicts = {row["conflict_id"]: row for row in analysis["conflicts"]}
    if not isinstance(choices, (list, tuple)):
        raise TypeError("merge choices must be an explicit sequence")
    requested = {}
    for row in choices:
        if not isinstance(row, dict) or set(row) != {"conflict_id", "choice", "reason", "delete_element_ids"}:
            raise ValueError("each merge choice needs conflict_id, choice, reason and explicit delete_element_ids")
        identity = row["conflict_id"]
        if identity not in conflicts or identity in requested:
            raise ValueError("unknown, stale or duplicate merge conflict choice")
        if row["choice"] not in {"base", "local", "incoming", "delete"}:
            raise ValueError("unknown merge choice")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("merge choice requires the caller's explicit reason")
        targets = row["delete_element_ids"]
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets) or len(set(targets)) != len(targets):
            raise ValueError("delete targets must be an explicit unique element-ID list")
        if bool(targets) != (row["choice"] == "delete"):
            raise ValueError("only delete choices require nonempty delete targets")
        requested[identity] = deepcopy(row)
    if set(requested) != set(conflicts):
        raise ValueError("merge choices must cover every exact conflict")

    keys = set().union(*(set(a) for a in sides.values()))
    identities = {key.split("/", 1)[0] for key in keys if key.endswith("/identity")}
    forced, deleted = {}, set()

    def assign(key, value):
        if key in forced and not _same(forced[key], value):
            raise UnresolvedPlainMerge("contradictory overlapping choices at " + key)
        forced[key] = value

    for identity, row in sorted(requested.items()):
        conflict = conflicts[identity]
        if row["choice"] != "delete":
            for key in conflict["subjects"]:
                assign(key, _state(sides[row["choice"]], key))
            continue
        targets = set(row["delete_element_ids"])
        if targets - identities:
            raise UnresolvedPlainMerge("delete names an unknown author element")
        for target in targets:
            kinds = {a[target + "/identity"]["kind"] for a in sides.values() if target + "/identity" in a}
            if "module" in kinds:
                raise UnresolvedPlainMerge("module root and HOST selection cannot be deleted")
        # Only structural ownership is expanded: component -> ports/operations.
        owned = set(targets)
        for item in identities:
            rows = [values[item + "/identity"] for values in sides.values() if item + "/identity" in values]
            parents = {row["parent"] for row in rows}
            if any(row["kind"] in {"port", "operation"} and row["parent"] in targets for row in rows):
                if len(parents) != 1 and item not in targets:
                    raise UnresolvedPlainMerge("delete ownership moved; an explicit child target is required")
                owned.add(item)
        if any(key == "host/selection" or key.split("/", 1)[0] not in owned for key in conflict["subjects"]):
            raise UnresolvedPlainMerge("delete targets do not resolve the entire conflict footprint")
        for target in targets:
            own = {target} | {item for values in sides.values() for item in identities
                if values.get(item + "/identity", {}).get("kind") in {"port", "operation"}
                and values.get(item + "/identity", {}).get("parent") == target}
            if not any(key.split("/", 1)[0] in own for key in conflict["subjects"]):
                raise UnresolvedPlainMerge("delete target is outside this conflict")
        deleted.update(owned)
        for key in keys:
            if key.split("/", 1)[0] in owned:
                assign(key, {"present": False, "value": None})

    result = {}
    for key in sorted(keys | set(forced)):
        b, l, r = (_state(sides[role], key) for role in ("base", "local", "incoming"))
        if key in forced: state = forced[key]
        elif _same(l, r): state = l
        elif _same(l, b): state = r
        elif _same(r, b): state = l
        else: raise UnresolvedPlainMerge("unresolved author atom " + key)
        if state["present"]: result[key] = deepcopy(state["value"])
    present = {key.split("/", 1)[0] for key in result if key.endswith("/identity")}
    for key in list(result):
        if key != "host/selection" and key.split("/", 1)[0] not in present:
            if key in forced and forced[key]["present"]:
                raise UnresolvedPlainMerge("selected value belongs to a deleted author element: " + key)
            del result[key]
    # Deleted author identities may be removed from structural lists only.
    # Missing live members, dangling business references and new order remain errors.
    for key, value in list(result.items()):
        if key.endswith("_order"):
            result[key] = [item for item in value if item in present]
    for identity in present:
        selected_kind = result[identity + "/identity"]["kind"]
        if any(a.get(identity + "/identity", {}).get("kind", selected_kind) != selected_kind for a in sides.values()):
            raise UnresolvedPlainMerge("retained stable identity changes kind: " + identity)
    json_safe = deepcopy(result)
    # canonical_json is type-sensitive, but does not by itself reject non-JSON input.
    import json
    json.dumps(json_safe, allow_nan=False)
    return result, [requested[key] for key in sorted(requested)], sorted(deleted)


def rebuild_module(atoms):
    entities = {key[:-len("/identity")]: value for key, value in atoms.items() if key.endswith("/identity")}
    roots = [key for key, row in entities.items() if row["kind"] == "module" and row["parent"] is None]
    if len(roots) != 1:
        raise UnresolvedPlainMerge("resolution requires exactly one module root")
    root = roots[0]
    for identity, row in entities.items():
        if identity + "/name" not in atoms or identity + "/value" not in atoms:
            raise UnresolvedPlainMerge("incomplete selected author element: " + identity)
        if identity != root and row["parent"] not in entities:
            raise UnresolvedPlainMerge("dangling author parent: " + identity)
        expected = "component" if row["kind"] in {"port", "operation"} else "module"
        if identity != root and entities[row["parent"]]["kind"] != expected:
            raise UnresolvedPlainMerge("wrong author parent kind: " + identity)

    def body(identity): return deepcopy(atoms[identity + "/value"])
    def name(identity): return atoms[identity + "/name"]
    def children(parent, kind): return {key for key, row in entities.items() if row == {"kind": kind, "parent": parent}}
    def order(parent, field, kind):
        values = atoms.get(parent + "/" + field)
        if not isinstance(values, list) or len(values) != len(set(values)) or set(values) != children(parent, kind):
            raise UnresolvedPlainMerge("unresolved order/membership: " + parent + "/" + field)
        return values
    def port(identity, component=None):
        row = entities.get(identity)
        if row is None or row["kind"] != "port" or (component is not None and row["parent"] != component):
            raise UnresolvedPlainMerge("dangling/wrong-component port reference: " + str(identity))
        return {"component": name(row["parent"]), "port": name(identity)}

    document = body(root)
    document["name"] = name(root)
    document["components"], ids = [], {"/": root}
    for component_id in order(root, "components_order", "component"):
        component = body(component_id); component["name"] = name(component_id)
        locator = "/components/" + component["name"]
        if locator in ids: raise UnresolvedPlainMerge("duplicate component name")
        ids[locator] = component_id
        component["ports"], component["operations"] = [], []
        for identity in order(component_id, "ports_order", "port"):
            value = body(identity); value["name"] = name(identity)
            target = locator + "/ports/" + value["name"]
            if target in ids: raise UnresolvedPlainMerge("duplicate port name")
            ids[target] = identity; component["ports"].append(value)
        for identity in order(component_id, "operations_order", "operation"):
            value = body(identity); value["name"] = name(identity)
            target = locator + "/operations/" + value["name"]
            if target in ids: raise UnresolvedPlainMerge("duplicate operation name")
            ids[target] = identity
            for field in ("inputs", "outputs"):
                value[field] = [port(ref, component_id)["port"] for ref in value[field]]
            if value["request_port"] is not None: value["request_port"] = port(value["request_port"], component_id)["port"]
            for outcome in value["outcomes"]:
                for product in outcome["products"]: product["port"] = port(product["port"], component_id)["port"]
                for effect in outcome["effects"]:
                    effect["bindings"] = {key: port(ref, component_id)["port"] for key, ref in effect["bindings"].items()}
            component["operations"].append(value)
        document["components"].append(component)
    document["links"] = []
    for index, identity in enumerate(order(root, "links_order", "link")):
        value = body(identity)
        document["links"].append({key: port(ref) for key, ref in value.items()})
        ids[f"/links/{index}"] = identity
    for category in ("entry", "exit"):
        document[category] = {}
        for identity in sorted(children(root, category)):
            label = name(identity)
            if label in document[category]: raise UnresolvedPlainMerge("duplicate boundary name")
            document[category][label] = port(body(identity)["port"])
            ids[f"/{category}/{label}"] = identity
    alternatives = atoms.get(root + "/terminal_alternatives_order")
    terminals = children(root, "terminal")
    if not isinstance(alternatives, list) or len(set(alternatives)) != len(alternatives) or set(alternatives) - terminals:
        raise UnresolvedPlainMerge("invalid terminal alternatives order")
    primary = terminals - set(alternatives)
    if len(primary) != 1: raise UnresolvedPlainMerge("resolution requires one explicit primary terminal")
    def terminal(identity, locator):
        value = body(identity); endpoint = port(value["source"])
        operation = entities.get(value["operation"])
        if operation is None or operation["kind"] != "operation" or operation["parent"] != entities[value["source"]]["parent"]:
            raise UnresolvedPlainMerge("dangling/wrong-component terminal operation")
        value["source"], value["operation"] = endpoint, name(value["operation"])
        ids[locator] = identity
        return value
    document["terminal"] = terminal(next(iter(primary)), "/terminal")
    document["terminal_alternatives"] = [terminal(ref, f"/terminal_alternatives/{index}") for index, ref in enumerate(alternatives)]
    if set(ids.values()) != set(entities):
        raise UnresolvedPlainMerge("selected author identities are not covered by the result")
    return ModuleDeclaration.from_dict(document), ids
