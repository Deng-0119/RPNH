"""Data-only stable-ID author normalization and conservative three-way analysis.

This is not a resolver: even conflict-free differences are not a merged Module.
Opaque HOST/config/mechanical semantics remain indivisible contract values.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from ..registry.schema_catalog import canonical_json

NORMALIZATION = "rpnh/plain_author_normalization/v1"
ALGORITHM = "rpnh/plain_author_three_way_analysis/v1"


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def normalize(value):
    """Preserve all author fields, replacing declared internal names with IDs.

    Container bodies exclude child lists. List order is recorded separately;
    names, whole contract values and order each form indivisible diff atoms.
    Mechanical effect references and opaque config are intentionally unchanged.
    """
    doc = deepcopy(value.module.to_dict())
    ids = {r["locator"]: r["element_id"] for r in value.element_map["elements"]}
    atoms, dependencies, locators = {}, {}, {v: k for k, v in ids.items()}

    def atom(key, payload, refs=()):
        atoms[key] = payload
        dependencies[key] = sorted(set(refs))

    def element(locator, kind, parent, name, body, refs=()):
        identity = ids[locator]
        atom(identity + "/identity", {"kind": kind, "parent": parent}, () if parent is None else (parent,))
        atom(identity + "/name", name)
        atom(identity + "/value", body, refs)
        return identity

    def endpoint(value):
        return ids[f"/components/{value['component']}/ports/{value['port']}"]

    def terminal(locator, body):
        body = deepcopy(body)
        component = body["source"]["component"]
        body["source"] = endpoint(body["source"])
        body["operation"] = ids[f"/components/{component}/operations/{body['operation']}"]
        return element(locator, "terminal", ids["/"], None, body, (body["source"], body["operation"]))

    components = doc.pop("components")
    links, entry, exits = doc.pop("links"), doc.pop("entry"), doc.pop("exit")
    main_terminal = doc.pop("terminal")
    alternatives = doc.pop("terminal_alternatives")
    element("/", "module", None, doc.pop("name"), doc)
    component_ids = []
    for component in components:
        name = component.pop("name")
        locator = f"/components/{name}"
        ports, operations = component.pop("ports"), component.pop("operations")
        component_id = element(locator, "component", ids["/"], name, component)
        component_ids.append(component_id)
        port_ids, operation_ids = [], []
        for port in ports:
            port_name = port.pop("name")
            port_ids.append(element(locator + "/ports/" + port_name, "port", component_id, port_name, port))
        port_ref = lambda name: ids[locator + "/ports/" + name]
        for operation in operations:
            operation_name = operation.pop("name")
            refs = []
            for field in ("inputs", "outputs"):
                operation[field] = [port_ref(p) for p in operation[field]]
                refs.extend(operation[field])
            if operation["request_port"] is not None:
                operation["request_port"] = port_ref(operation["request_port"])
                refs.append(operation["request_port"])
            for outcome in operation["outcomes"]:
                for product in outcome["products"]:
                    product["port"] = port_ref(product["port"])
                    refs.append(product["port"])
                for effect in outcome["effects"]:
                    effect["bindings"] = {k: port_ref(v) for k, v in effect["bindings"].items()}
                    refs.extend(effect["bindings"].values())
            operation_ids.append(element(locator + "/operations/" + operation_name, "operation", component_id,
                                         operation_name, operation, refs))
        atom(component_id + "/ports_order", port_ids, port_ids)
        atom(component_id + "/operations_order", operation_ids, operation_ids)
    atom(ids["/"] + "/components_order", component_ids, component_ids)
    link_ids = []
    for index, link in enumerate(links):
        body = {k: endpoint(v) for k, v in link.items()}
        link_ids.append(element(f"/links/{index}", "link", ids["/"], None, body, body.values()))
    atom(ids["/"] + "/links_order", link_ids, link_ids)
    for category, values in (("entry", entry), ("exit", exits)):
        for name, target in values.items():
            ref = endpoint(target)
            element(f"/{category}/{name}", category, ids["/"], name, {"port": ref}, (ref,))
    terminal("/terminal", main_terminal)
    other_ids = [terminal(f"/terminal_alternatives/{index}", body) for index, body in enumerate(alternatives)]
    atom(ids["/"] + "/terminal_alternatives_order", other_ids, other_ids)
    atom("host/selection", deepcopy(value.host_requirements))
    # Derived dependency evidence only: never diff or merge the compiled PN.
    carriers = []
    for name, fragment in sorted(value.compiled.fragments.items()):
        kinds = [field for field in ("lease_identities", "lease_pools", "variable_resource_arcs", "logical_slots", "reset_arcs")
                 if getattr(fragment, field)]
        if any(p.reusable or p.token_kind in {"agent_resource", "resource_lease"} for p in fragment.places):
            kinds.append("resource_places")
        if any(a.lease_claims or a.lease_claim_exclusions or a.lease_claim_set is not None for a in fragment.arcs):
            kinds.append("lease_claim_arcs")
        if kinds:
            carriers.append({"component_element_id": ids[f"/components/{name}"], "carrier_kinds": sorted(kinds)})
    return {"contract": NORMALIZATION, "derived_resource_dependencies": sorted(carriers, key=lambda row: row["component_element_id"]),
            "atoms": dict(sorted(atoms.items())),
            "dependencies": dict(sorted(dependencies.items())), "locators": dict(sorted(locators.items()))}


def _state(atoms, key):
    return {"present": key in atoms, "value": atoms.get(key)}


def compare(base, local, incoming):
    """Return exact B/L/R diffs and conflicts; never select a business answer."""
    models = (base, local, incoming)
    atoms = [m["atoms"] for m in models]
    changes, conflicts = [], {}
    changed = [set(), set()]
    for key in sorted(set().union(*(set(a) for a in atoms))):
        b, l, r = (_state(a, key) for a in atoms)
        lc, rc = not _same(b, l), not _same(b, r)
        if lc: changed[0].add(key)
        if rc: changed[1].add(key)
        if lc or rc:
            changes.append({"subject": key, "base": b, "local": l, "incoming": r,
                            "change": "same" if _same(l, r) else "both" if lc and rc else "local" if lc else "incoming"})

    def conflict(reason, subjects):
        subjects = sorted(set(subjects))
        payload = {"reason": reason, "subjects": subjects,
                   **{role: [{"subject": k, **_state(a, k)} for k in subjects]
                      for role, a in zip(("base", "local", "incoming"), atoms)}}
        identity = "conflict:" + hashlib.sha256(canonical_json(payload)).hexdigest()
        if identity not in conflicts:
            conflicts[identity] = {"conflict_id": identity, **payload}

    for row in changes:
        if row["change"] == "both":
            conflict("divergent_atom", [row["subject"]])
    identities = [set(m["locators"]) for m in models]
    for identity in sorted((identities[1] & identities[2]) - identities[0]):
        conflict("concurrent_identity_introduction", [k for k in set(atoms[1]) | set(atoms[2]) if k.startswith(identity + "/")])
    for index, other in ((0, 1), (1, 0)):
        deleted = identities[0] - identities[index + 1]
        affected_by_identity, referenced_by = {}, {}
        for key in changed[other]:
            affected_by_identity.setdefault(key.split("/", 1)[0], set()).add(key)
            if not key.endswith("_order"):
                for reference in models[other + 1]["dependencies"].get(key, ()):
                    referenced_by.setdefault(reference, set()).add(key)
        for identity in sorted(deleted):
            affected = affected_by_identity.get(identity, set())
            if affected and identity in identities[other + 1]:
                conflict("delete_modify", affected | {identity + "/identity"})
            if identity in referenced_by:
                conflict("delete_dependency", referenced_by[identity] | {identity + "/identity"})
    # Index names rather than expanding every possible cross-side identity pair.
    def named(model, changed_keys):
        result = {}
        for identity in model["locators"]:
            name = model["atoms"][identity + "/name"]
            if name is None or not any(k in changed_keys for k in (identity + "/name", identity + "/identity")):
                continue
            key = canonical_json([model["atoms"][identity + "/identity"], name])
            result.setdefault(key, []).append(identity)
        return result
    ln, rn = named(local, changed[0]), named(incoming, changed[1])
    for key in sorted(ln.keys() & rn.keys()):
        names = set(ln[key]) | set(rn[key])
        if len(names) > 1:
            conflict("name_collision", [identity + suffix for identity in names for suffix in ("/name", "/identity")])
    # Connected author contracts over the union of B/L/R. Container membership
    # joins siblings conservatively, but the module's parent/order edges do not
    # falsely connect independent regions. Existing links join both endpoints.
    parents = {}
    def root(key):
        parents.setdefault(key, key)
        if parents[key] != key: parents[key] = root(parents[key])
        return parents[key]
    def union(left, right):
        lr, rr = root(left), root(right)
        if lr != rr: parents[max(lr, rr)] = min(lr, rr)
    for model in models:
        values = model["atoms"]
        for key, refs in model["dependencies"].items():
            if key.endswith("_order") or key == "host/selection": continue
            identity = key.split("/", 1)[0]
            if values.get(identity + "/identity", {}).get("kind") == "module": continue
            for ref in refs:
                if values.get(ref + "/identity", {}).get("kind") != "module": union(identity, ref)
        for identity in model["locators"]:
            body = values[identity + "/value"]
            if not isinstance(body, dict): continue
            binding = body.get("budget_binding")
            if binding is not None: union(identity, "budget:" + binding["bucket_id"])
            for outcome in body.get("outcomes", []):
                for effect in outcome.get("effects", []):
                    for reference in effect.get("references", {}).values():
                        union(identity, "mechanical:" + canonical_json(reference).decode("ascii"))
    semantic = [{k for k in side if k.endswith("/value") or k.endswith("/identity") or k == "host/selection"}
                for side in changed]
    def group(model, key):
        if key == "host/selection": return "global"
        identity = key.split("/", 1)[0]
        kind = model["atoms"].get(identity + "/identity", {}).get("kind")
        if kind in {"module", "terminal", "entry", "exit"}: return "global"
        return root(identity)
    if any(model["derived_resource_dependencies"] for model in models):
        # Opaque lowerers can couple disconnected author regions through their
        # formal resource carriers. Names can also influence those lowerers.
        resource_changes = [{k for k in side if not k.endswith("_order")} for side in changed]
        subjects = resource_changes[0] | resource_changes[1]
        if resource_changes[0] and resource_changes[1] and len(subjects) > 1:
            if any(not _same(_state(atoms[1], k), _state(atoms[2], k)) for k in subjects):
                conflict("lowered_resource_coupling", subjects)
    groups = [{}, {}]
    for index, model in enumerate((local, incoming)):
        for key in semantic[index]:
            selected = model if key in model["atoms"] else base
            groups[index].setdefault(group(selected, key), set()).add(key)
    # One exact conflict per connected contract, not the Cartesian product of
    # changed atoms. Global contract changes conservatively include both sides.
    if ("global" in groups[0] and semantic[1]) or ("global" in groups[1] and semantic[0]):
        subjects = semantic[0] | semantic[1]
        if len(subjects) > 1 and any(not _same(_state(atoms[1], k), _state(atoms[2], k)) for k in subjects):
            conflict("coupled_contract", subjects)
    else:
        for key in sorted(groups[0].keys() & groups[1].keys()):
            subjects = groups[0][key] | groups[1][key]
            if len(subjects) > 1 and any(not _same(_state(atoms[1], k), _state(atoms[2], k)) for k in subjects):
                conflict("coupled_contract", subjects)
    return changes, [conflicts[key] for key in sorted(conflicts)]
