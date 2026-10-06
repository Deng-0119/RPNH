"""Reconstruct open selections from exact declarations and actual typed fragments.

The inventory describes structure, not callable behavior or runtime permission.
Every typed fragment is retained as evidence. The initial closing adapter only
understands the existing basic operation lowering; richer contracts stay open.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import uuid

from ..petri_contracts import BindingContext, DeclarationError
from ..executable_net import _canonical
from ..registry.schema_catalog import canonical_json, canonical_text
from ._assembly_lowering import _pointer

KINDS = ("data", "control", "feedback", "lease", "pool", "slot", "reset",
         "external_dependency", "effect", "completion")
INVENTORY_SCHEMA = "rpnh/collaboration/open_region_boundary_inventory/v1"


def signature(document):
    payload = canonical_json(document)
    return {"payload_bytes": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest()}


def element_identity(lineage, kind, origin):
    return "element:" + uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "contract": "open_region_element_identity/v1", "lineage": str(lineage),
        "kind": kind, "origin": origin})).hex


def selected_components(source, selection):
    if (not isinstance(selection, dict) or set(selection) != {"kind", "element_ids"}
            or selection["kind"] != "component_element_ids"):
        raise ValueError("invalid_selection: select exact component element IDs")
    ids = selection["element_ids"]
    if not isinstance(ids, list) or not ids or ids != sorted(set(ids)):
        raise ValueError("invalid_selection: component IDs must be nonempty, sorted and unique")
    elements = {row["element_id"]: row for row in source.element_map["elements"]}
    if any(identity not in elements or elements[identity]["kind"] != "component" for identity in ids):
        raise ValueError("invalid_selection: every ID must identify a current source component")
    names = {elements[identity]["locator"].split("/")[2] for identity in ids}
    if any(component.key != "operation" for component in source.module.components if component.name in names):
        raise ValueError("unsupported_contract: first selection adapter requires whole operation components")
    return names


def scan(source, selection, provenance_ref):
    """Return complete supported evidence, retaining unsupported obligations.

Locations point into the canonical Module/compiled documents, not author-map
locators. Coverage includes inspected empty arrays: absence is an observation.
"""
    from cpn.components.basic import lower_operation
    names = selected_components(source, selection)
    document, compiled = source.module.to_dict(), source.compiled.to_dict()
    revision = source.revision.revision_ref.to_dict()
    ids = {row["locator"]: row["element_id"] for row in source.element_map["elements"]}
    items, coverage = [], {kind: [] for kind in KINDS}
    unsupported = set()

    def location(role, pointer, locator=None):
        return {"revision_ref": revision, "material_role": role,
                "json_pointer": pointer, "element_id": ids.get(locator)}

    def add(kind, locations, selected, outside, disposition, facts, unknowns=()):
        locations = sorted(locations, key=canonical_text)
        identity = "boundary:" + uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            "contract": "open_region_boundary_identity/v1", "source_revision_ref": revision,
            "selection": selection, "kind": kind, "source_locations": locations})).hex
        items.append({"item_id": identity, "kind": kind, "source_locations": locations,
            "selected_element_ids": sorted(set(selected)), "outside_element_ids": sorted(set(outside)),
            "contract": {"facts": [{"semantic_key": key, "value": value,
                "source_locations": locations} for key, value in facts], "unknowns": sorted(set(unknowns))},
            "extraction_disposition": disposition})
        coverage[kind].extend(locations)

    components = {component.name: component for component in source.module.components}
    for index, component in enumerate(source.module.components):
        if component.name not in names:
            continue
        base = _pointer("fragments", component.name)
        locs = [location("definition", _pointer("components", index), f"/components/{component.name}"),
                location("compiled_inventory", base)]
        fragment = compiled["fragments"][component.name]
        selected = [ids[f"/components/{component.name}"]]
        # Compare the complete typed result, including arrays not represented
        # by Module links, with the supported lowering protocol. No function
        # introspection or nominal component-key assumption proves coverage.
        context = BindingContext(component.name, component.ports, source.module.required_schemas,
                                 source.module.budgets, component.operations)
        actual = asdict(source.compiled.fragments[component.name])
        try:
            expected = asdict(lower_operation(component.config, context))
        except DeclarationError:
            expected = None
        supported = expected is not None and canonical_json(_canonical(actual)) == canonical_json(_canonical(expected))
        for kind in KINDS:
            coverage[kind].extend(locs)
        if not supported:
            unsupported.update(KINDS)
        add("external_dependency", locs, selected, [], "internal" if supported else "unrepresentable",
            [("resource", {"declaration": document["components"][index], "actual_fragment": fragment})],
            () if supported else ("unsupported_actual_fragment",))
        for kind, fields in (("lease", ("lease_identities",)), ("pool", ("lease_pools", "variable_resource_arcs")),
                             ("slot", ("logical_slots",)), ("reset", ("reset_arcs",))):
            for field in fields:
                field_locations = [location("compiled_inventory", base + "/" + field)]
                coverage[kind].extend(field_locations)
                if fragment[field]:
                    add(kind, field_locations, selected, [], "needs_adapter", [("resource", fragment[field])],
                        ("structural_adapter",))
        # Effects, return/feedback aliases and resource inscriptions remain
        # visible even when a HOST lowerer returned a compilable fragment.
        effects = [(i, j, outcome["effects"]) for i, operation in enumerate(fragment["operations"])
                   for j, outcome in enumerate(operation["outcomes"]) if outcome["effects"]]
        for i, j, effects_value in effects:
            add("effect", [location("compiled_inventory", base + f"/operations/{i}/outcomes/{j}/effects")],
                selected, [], "needs_adapter", [("effect", effects_value)], ("effect_responsibility",))
        feedback = (component.config.get("interrupt_returns") or
                    any(arc["emit"] != "produced" or arc["mode"] == "read" for arc in fragment["arcs"]) or
                    len({binding["place"] for binding in fragment["ports"]}) != len(fragment["ports"]))
        if feedback:
            add("feedback", locs, selected, [], "needs_adapter", [("resource", fragment)], ("return_semantics",))
        for arc_index, arc in enumerate(fragment["arcs"]):
            if any(arc[field] for field in ("lease_claims", "lease_claim_exclusions", "lease_claim_set", "effect_selector")):
                add("lease", [location("compiled_inventory", base + f"/arcs/{arc_index}")], selected, [],
                    "needs_adapter", [("resource", arc)], ("structural_adapter",))

    for index, link in enumerate(document["links"]):
        source_name, target_name = link["source"]["component"], link["target"]["component"]
        inside = (source_name in names, target_name in names)
        if not any(inside):
            continue
        endpoints, selected, outside = [], [], []
        for field, is_selected in zip(("source", "target"), inside):
            endpoint = link[field]
            component = components[endpoint["component"]]
            port = next(port for port in component.ports if port.name == endpoint["port"])
            endpoints.append(asdict(port))
            identity = ids[f"/components/{component.name}/ports/{port.name}"]
            (selected if is_selected else outside).append(identity)
        locs = [location("definition", f"/links/{index}", f"/links/{index}")]
        for endpoint in link.values():
            qualified = endpoint["component"] + "." + endpoint["port"]
            locs.append(location("compiled_inventory", _pointer("symbolic", "port_places", qualified)))
        kind = "control" if endpoints[0]["channel"] == "control" else "data"
        add(kind, locs, selected, outside, "internal" if all(inside) else "boundary",
            [("schema", [row["schema"] for row in endpoints]), ("direction", link),
             ("cardinality", endpoints), ("resource", {field: compiled["symbolic"]["port_places"][
                 value["component"] + "." + value["port"]] for field, value in link.items()})])

    for field in ("entry", "exit"):
        coverage["data"].append(location("definition", "/" + field))
        coverage["control"].append(location("definition", "/" + field))
        for name, endpoint in sorted(document[field].items()):
            if endpoint["component"] not in names:
                continue
            port = next(port for port in components[endpoint["component"]].ports if port.name == endpoint["port"])
            add(port.channel, [location("definition", _pointer(field, name), f"/{field}/{name}")],
                [ids[f"/components/{endpoint['component']}/ports/{endpoint['port']}"]], [], "boundary",
                [("direction", {field: endpoint}), ("schema", port.schema), ("cardinality", asdict(port))])
    terminals = [("/terminal", document["terminal"]), *[
        (f"/terminal_alternatives/{i}", row) for i, row in enumerate(document["terminal_alternatives"])]]
    for pointer, terminal in terminals:
        identity = ids[pointer]
        inside = terminal["source"]["component"] in names
        add("completion", [location("definition", pointer, pointer)], [identity] if inside else [],
            [] if inside else [identity], "boundary", [("completion", terminal)])
    for field in ("required_schemas", "budgets", "budget_buckets", "analyzers", "designer_constraints"):
        value = document[field]
        locs = [location("definition", "/" + field)]
        coverage["external_dependency"].extend(locs)
        if value:
            unknown = field in {"analyzers", "designer_constraints"}
            add("external_dependency", locs, selection["element_ids"], [], "needs_adapter" if unknown else "outer_owned",
                [("resource", {field: value})], ("opaque_author_contract",) if unknown else ())
    # A total observed fragment plus source-wide author material is the scope
    # of this scanner. It makes no claim about arbitrary executor side effects.
    return {"schema_version": INVENTORY_SCHEMA, "provenance_ref": provenance_ref.to_dict(),
        "scanner": "source_declaration_and_actual_fragments/v1", "source_compile_signature": signature(compiled),
        "coverage": [{"kind": kind, "status": "unsupported" if kind in unsupported else "complete",
            "source_locations": sorted({canonical_text(row): row for row in coverage[kind]}.values(), key=canonical_text)}
            for kind in KINDS], "items": sorted(items, key=lambda row: row["item_id"])}


def required_choices(inventory):
    result = []
    for item in inventory["items"]:
        disposition = item["extraction_disposition"]
        if disposition in {"internal", "outer_owned"}:
            continue
        if disposition in {"needs_adapter", "unrepresentable"}:
            choices = ["adapter"]
        elif item["kind"] == "completion":
            choices = ["completion", "context"]
        else:
            choices = ["context", "ingress"]
        result.append({"item_id": item["item_id"], "choices": choices})
    return result
