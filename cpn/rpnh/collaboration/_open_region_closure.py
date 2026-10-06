"""First explicit source-component completion adapter; no inferred context."""
from __future__ import annotations

from copy import deepcopy

from ..compiler import compile_module
from ..net_operations.definitions import ExtractPlan, extract_module
from ..registry.event_store import RegistryConflict
from ..registry.schema_catalog import canonical_text
from ._assembly_lowering import _pointer, resolve_pointer
from ._open_region_inventory import element_identity, scan, selected_components
from .materials import _elements, _element_map, _boundaries, _same_json

ADAPTER = "rpnh/collaboration/source_component_completion/v1"
RESOLVER = "rpnh/collaboration/direct_adapted_member_resolver/v1"


def _location_key(item):
    return canonical_text({"kind": item["kind"], "source_locations": item["source_locations"]})


def inventory_for_context(source, opened, union):
    """Keep original O item IDs; inventory new context dependencies as well."""
    original = {_location_key(row): row for row in opened.boundary_inventory["items"]}
    inventory = scan(source, union, opened.provenance_ref)
    if any(row["status"] != "complete" for row in inventory["coverage"]):
        raise ValueError("unresolved_boundary: actual fragment coverage requires another adapter")
    for row in inventory["items"]:
        if _location_key(row) in original:
            row["item_id"] = original[_location_key(row)]["item_id"]
    if set(original) - {_location_key(row) for row in inventory["items"]}:
        raise RegistryConflict("claim_not_proved: context lost an original inventory obligation")
    inventory["items"].sort(key=lambda row: row["item_id"])
    return inventory


def reconstruct(source, opened, request, reference, registration):
    """Rebuild only the exact selected union and caller-declared completion."""
    context = request["context"]
    if set(context) != {"source_revision_ref", "element_ids"} or context["source_revision_ref"] != source.revision.revision_ref.to_dict():
        raise ValueError("invalid_selection: context must pin O's exact source")
    selected_ids = opened.provenance["selection"]["element_ids"]
    additional = context["element_ids"]
    if (not isinstance(additional, list) or additional != sorted(set(additional)) or set(additional) & set(selected_ids)):
        raise ValueError("invalid_selection: context IDs must be sorted, unique and disjoint from O")
    union = {"kind": "component_element_ids", "element_ids": sorted([*selected_ids, *additional])}
    names = selected_components(source, union)
    selected_names = selected_components(source, opened.provenance["selection"])
    ids = {row["locator"]: row["element_id"] for row in source.element_map["elements"]}
    completion = request["completion"]
    expected_completion = {"primary_terminal_element_id": ids["/terminal"],
        "alternative_terminal_element_ids": [ids[f"/terminal_alternatives/{i}"] for i, terminal
            in enumerate(source.module.terminal_alternatives) if terminal.source.component in names]}
    if (source.module.terminal.source.component not in names or not _same_json(completion, expected_completion)):
        raise ValueError("unresolved_boundary: explicitly retain the declared primary and all retained alternatives")
    plan = request["extract_plan"]
    if (set(plan) != {"kind", "components", "boundary_policy", "output_name"}
            or plan["kind"] != "components" or plan["boundary_policy"] != "preserve_all_dependencies"
            or plan["components"] != sorted(names) or not isinstance(plan["output_name"], str)):
        raise ValueError("invalid_selection: full explicit extract plan must select exactly O plus context")
    extracted = extract_module(source.module, ExtractPlan(kind="components", components=tuple(plan["components"]),
        boundary_policy=plan["boundary_policy"], output_name=plan["output_name"]))
    module = extracted.module
    compiled = compile_module(module, registration)
    inventory = inventory_for_context(source, opened, union)
    source_doc, result_doc = source.module.to_dict(), module.to_dict()
    source_ref = source.revision.revision_ref.to_dict()
    source_rows = {row["locator"]: row for row in source.element_map["elements"]}
    open_ids = {row["source_element_id"]: row["element_id"] for row in opened.element_map["elements"]}
    result_ids, origins = {}, []

    def source_element(locator):
        return {"source_revision_ref": source_ref, "source_element_id": ids[locator]}

    def link_locator(link):
        return "/links/" + str(source_doc["links"].index(link))

    for locator, kind in sorted(_elements(module).items()):
        projection = False
        if locator.startswith("/components/"):
            origins_locators = [locator]
            origin_kind = "selected" if locator.split("/")[2] in selected_names else "context"
        elif locator.startswith("/links/"):
            link = result_doc["links"][int(locator.split("/")[2])]
            origins_locators = [link_locator(link)]
            origin_kind = "selected" if all(value["component"] in selected_names for value in link.values()) else "context"
        elif locator == "/":
            origins_locators = ["/", *[f"/components/{name}" for name in sorted(names)]]
            origin_kind, projection = "module_projection", True
        elif kind == "terminal":
            terminal = resolve_pointer(result_doc, locator)
            terminal_sources = [("/terminal", source_doc["terminal"]), *[
                (f"/terminal_alternatives/{i}", row) for i, row in enumerate(source_doc["terminal_alternatives"])]]
            origins_locators = [path for path, row in terminal_sources if _same_json(row, terminal)]
            origin_kind, projection = "boundary_projection", True
        else:
            field, name = locator.strip("/").split("/", 1)
            endpoint = result_doc[field][name]
            origins_locators = [f"/components/{endpoint['component']}/ports/{endpoint['port']}"]
            origins_locators.extend(f"/{field}/{key}" for key, value in source_doc[field].items() if value == endpoint)
            side = "target" if field == "entry" else "source"
            origins_locators.extend(f"/links/{i}" for i, link in enumerate(source_doc["links"])
                if link[side] == endpoint and (link["source"]["component"] in names) != (link["target"]["component"] in names))
            origin_kind, projection = "boundary_projection", True
        if not origins_locators or any(path not in source_rows for path in origins_locators):
            raise RegistryConflict("claim_not_proved: result element lacks an exact source origin")
        source_elements = sorted([source_element(path) for path in set(origins_locators)], key=canonical_text)
        identity_origin = ({"result_locator": locator, "source_elements": source_elements}
                           if projection else source_elements[0])
        identity = element_identity(reference.ref.entity_id, kind, identity_origin)
        source_id_set = {row["source_element_id"] for row in source_elements}
        result_ids[locator] = identity
        origins.append({"result_element_id": identity, "result_locator": locator, "origin_kind": origin_kind,
            "source_elements": source_elements, "open_element_ids": sorted(open_ids[value] for value in source_id_set if value in open_ids),
            "boundary_item_ids": sorted(item["item_id"] for item in inventory["items"] if source_id_set & {
                location["element_id"] for location in item["source_locations"] if location["element_id"] is not None})})
    elements = _element_map(module, result_ids, None, {})
    boundaries = _boundaries(module, elements)
    dispositions = resolve_dispositions(source, opened, inventory, request, module, result_ids, names, origins)
    return module, compiled, elements, boundaries, origins, dispositions


def resolve_dispositions(source, opened, inventory, request, module, result_ids, names, origins):
    """Explicit caller intent supplies ingress meaning; unsupported cuts fail."""
    source_doc, result_doc = source.module.to_dict(), module.to_dict()
    intent = request["intent"]
    expectations = intent["ingress_expectations"]
    if (not isinstance(expectations, list) or expectations != sorted(expectations, key=lambda row: row["item_id"])
            or len({row["item_id"] for row in expectations}) != len(expectations)):
        raise ValueError("target_mismatch: ingress expectations must be sorted and unique")
    ingress = {row["item_id"]: row for row in expectations}
    original = {row["item_id"] for row in opened.boundary_inventory["items"]}
    used_ingress, dispositions = set(), []
    for item in inventory["items"]:
        item_id = item["item_id"]
        if item["extraction_disposition"] in {"needs_adapter", "unrepresentable"} or item["contract"]["unknowns"]:
            raise ValueError("unresolved_boundary: " + item_id + " requires an explicit supported structural adapter")
        declared = [row for row in item["source_locations"] if row["material_role"] == "definition"]
        outputs, request_fields, adapted = [], [], False
        for location in declared:
            path = location["json_pointer"]
            value = resolve_pointer(source_doc, path)
            if path.startswith("/links/"):
                inside = (value["source"]["component"] in names, value["target"]["component"] in names)
                if all(inside):
                    outputs.append("/links/" + str(result_doc["links"].index(value)))
                    adapted = item_id in opened.contract["unresolved_item_ids"]
                    request_fields.append("/context")
                elif inside == (False, True):
                    outputs.extend(_ingress_output(item_id, original, ingress, used_ingress, value["target"], result_doc))
                    request_fields.append("/intent/ingress_expectations")
                    adapted = True
                else:
                    raise ValueError("unresolved_boundary: outgoing context cut needs another adapter: " + item_id)
            elif path.startswith("/entry/"):
                outputs.extend(_ingress_output(item_id, original, ingress, used_ingress, value, result_doc))
                request_fields.append("/intent/ingress_expectations")
                adapted = True
            elif path.startswith("/exit/"):
                outputs.extend("/exit/" + name for name, endpoint in result_doc["exit"].items() if endpoint == value)
            elif path == "/terminal" or path.startswith("/terminal_alternatives/"):
                if value["source"]["component"] in names:
                    outputs.extend(path for path, terminal in [("/terminal", result_doc["terminal"]), *[
                        (f"/terminal_alternatives/{i}", row) for i, row in enumerate(result_doc["terminal_alternatives"])]]
                        if _same_json(terminal, value))
                request_fields.extend(("/context", "/completion"))
                adapted = True
            elif path.startswith("/components/"):
                outputs.append("/components/" + value["name"])
                request_fields.append("/context" if value["name"] not in {row["name"] for row in opened.definition["components"]} else "/open_revision_ref")
            else:
                if not _same_json(value, resolve_pointer(result_doc, path)):
                    raise ValueError("unresolved_boundary: retained source-wide requirement differs")
                outputs.append("/")
                request_fields.append("/extract_plan")
        output_ids = sorted({result_ids[path] for path in outputs})
        # Use real result JSON pointers, rather than the named author locators.
        def pointer(locator):
            if locator == "/":
                return ""
            if locator.startswith("/components/"):
                name = locator.split("/")[2]
                return "/components/" + str(next(i for i, row in enumerate(result_doc["components"]) if row["name"] == name))
            parts = locator.strip("/").split("/")
            return _pointer(*parts)
        output_locations = [{"material_role": "definition", "json_pointer": pointer(path)} for path in sorted(set(outputs))]
        # Each item, including new context dependencies, remains explicit in
        # the complete caller-reviewed request and reconstructed proof.
        dispositions.append({"item_id": item_id, "status": "adapted" if adapted else "direct",
            "adapter": ADAPTER if adapted else None, "output_element_ids": output_ids,
            "evidence": {"source_locations": deepcopy(item["source_locations"]), "output_locations": output_locations,
                "request_fields": sorted(set(request_fields))},
            "remaining_requirements": ["live_input_availability"] if item_id in used_ingress else []})
    if used_ingress != set(ingress):
        raise ValueError("target_mismatch: ingress expectations must cover exactly the surviving incoming boundaries")
    return sorted(dispositions, key=lambda row: row["item_id"])


def _ingress_output(item_id, original, ingress, used, endpoint, document):
    if item_id not in original or item_id not in ingress:
        raise ValueError("unresolved_boundary: incoming boundary needs an exact original O intent: " + item_id)
    expectation = ingress[item_id]
    if set(expectation) != {"item_id", "kind", "other_member_id", "other_revision_ref", "other_exit_element_id"}:
        raise ValueError("target_mismatch: ingress expectation fields differ")
    others = [expectation[field] for field in ("other_member_id", "other_revision_ref", "other_exit_element_id")]
    if expectation["kind"] == "public_entry":
        if any(value is not None for value in others):
            raise ValueError("target_mismatch: public ingress has no other member")
    elif expectation["kind"] == "assembly_connection":
        if any(value is None for value in others):
            raise ValueError("target_mismatch: connection ingress needs an exact other member endpoint")
    else:
        raise ValueError("target_mismatch: explicit public_entry or assembly_connection required")
    names = [name for name, value in document["entry"].items() if value == endpoint]
    if len(names) != 1:
        raise ValueError("incompatible_boundary: incoming projection aliases multiple public entries")
    used.add(item_id)
    return ["/entry/" + names[0]]
