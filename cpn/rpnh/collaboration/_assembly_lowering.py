"""Deterministic Assembly composition and provenance, over the real compiler.

Locators describe declarations; JSON pointers describe one exact compiled
resource. Neither is a new runtime identity or an executable HOST binding.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from ..compiler import compile_module
from ..composition import compose_fragments
from ..executable_net import _make_document, load_compiled_net
from ..module import ModuleDeclaration
from ..net_operations.definitions import ComposeConnection, ComposePlan, compose_modules
from ..registry.schema_catalog import canonical_json, canonical_text
from .materials import _elements


LOWERING_SCHEMA = "rpnh/collaboration/assembly_lowering_map/v1"


def prefix(member_id):
    return "m_" + member_id.removeprefix("member:")


def _pointer(*parts):
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def resolve_pointer(document, pointer):
    """Resolve RFC 6901 pointers, including numeric array indexes, without IO."""
    value = document
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _carrier_cut(plan, members, compiled):
    """Keep member internal fusion, removing only added Assembly connections."""
    baseline_module = compose_modules({prefix(item["member_id"]): members[item["member_id"]].module
        for item in plan["members"]}, ComposePlan(plan["name"], prefix(plan["completion"]["member_id"]), (), mode="explicit"))
    baseline, _ = compose_fragments(baseline_module, compiled.fragments)
    return baseline_module, baseline


def compose_plan(plan, members, registration, *, compiled_inventory=None):
    """No labels, Branch heads, implicit links, or member precompiled graphs."""
    by_id = {item["member_id"]: members[item["member_id"]] for item in plan["members"]}
    if len(by_id) != len(plan["members"]):
        raise ValueError("Assembly member identities must be unique")
    budgets, buckets = {}, {}
    for value in by_id.values():
        document = value.module.to_dict()
        if any(component["key"] in {f"rpnh/agent-workflow-graph/v{version}" for version in range(1, 5)}
               for component in document["components"]):
            raise ValueError("Assembly graph-derived members require the future single-source rebuild consumer")
        if document["designer_constraints"]:
            raise ValueError("Assembly does not support nonempty member designer_constraints")
        for values, source, label in ((budgets, document["budgets"].items(), "budget"),
                (buckets, ((b["bucket_id"], b) for b in document["budget_buckets"]), "budget bucket")):
            for key, item in source:
                if key in values and canonical_json(values[key]) != canonical_json(item):
                    raise ValueError(f"shared_exact conflicting {label}: {key}")
                values[key] = item
    lookup = {key: {item["element_id"]: item for item in value.element_map["elements"]}
              for key, value in by_id.items()}

    def boundary(member, identity, kind):
        item = lookup.get(member, {}).get(identity)
        if item is None or item["kind"] != kind:
            raise ValueError(f"Assembly endpoint must name an exact member {kind} element")
        return item["locator"].split("/", 2)[2]

    completion = plan["completion"]
    selected = lookup.get(completion["member_id"], {}).get(completion["terminal_element_id"])
    if selected is None or selected["locator"] != "/terminal":
        raise ValueError("Assembly root completion must select the member's primary terminal element")
    connections = []
    targets = set()
    for item in plan["connections"]:
        left, right = item["source_member_id"], item["target_member_id"]
        source = boundary(left, item["source_exit_element_id"], "exit")
        target = boundary(right, item["target_entry_element_id"], "entry")
        endpoint = by_id[right].module.entry[target]
        target_key = (right, endpoint.component, endpoint.port)
        aliases = [name for name, value in by_id[right].module.entry.items() if value == endpoint]
        if target_key in targets or len(aliases) != 1:
            raise ValueError("Assembly connected entry cannot have aliased or multiple producers")
        targets.add(target_key)
        connections.append(ComposeConnection(prefix(left), source, prefix(right), target))
    instances = {prefix(key): value.module for key, value in by_id.items()}
    module = compose_modules(instances,
        ComposePlan(plan["name"], prefix(completion["member_id"]), tuple(connections), mode="explicit"))
    # compile_module checks all primary/alternative terminal carriers, signatures,
    # schemas, capacities and actual final-context HOST lowering.
    if compiled_inventory is None:
        compiled = compile_module(module, registration)
    else:
        symbolic, _ = compose_fragments(module, compiled_inventory.fragments)
        compiled = load_compiled_net(_make_document(module.to_dict(), compiled_inventory.fragments,
            asdict(symbolic), compiled_inventory.registrations))
    return _contract_carriers(plan, by_id, module, compiled)


def _contract_carriers(plan, members, module, compiled, *, baseline_module=None, _offline_schema_validation=False):
    """Apply actual-carrier guards; an explicit resolver may supply its cut."""
    if type(_offline_schema_validation) is not bool:
        raise TypeError("offline schema validation is a fixed boolean policy")
    if plan["connections"]:
        # Different declared ports can lower to the same place, including via
        # a member's own internal fusion. Keep that fusion, but exclude only
        # Assembly-introduced links when deciding which carriers are reused.
        # Reuse the final-context fragments; do NOT re-lower or prefix member
        # precompiles. Post-Assembly aliases would confuse normal A -> B -> C
        # fusion with competing consumers/producers.
        if baseline_module is None:
            baseline_module, baseline = _carrier_cut(plan, members, compiled)
        else:
            baseline, _ = compose_fragments(baseline_module, compiled.fragments)
        entry_carriers = [baseline.port_places[f"{entry.component}.{entry.port}"]
                          for entry in baseline_module.entry.values()]
        exit_carriers = {baseline.port_places[f"{exit.component}.{exit.port}"]
                         for exit in baseline_module.exit.values()}
        sources, targets = set(), set()
        internal_count = sum(len(value.module.links) for value in members.values())
        for link in module.links[internal_count:]:
            source = baseline.port_places[f"{link.source.component}.{link.source.port}"]
            target = baseline.port_places[f"{link.target.component}.{link.target.port}"]
            if source in sources:
                raise ValueError("Assembly actual source carrier has multiple consumers")
            if target in targets:
                raise ValueError("Assembly actual target carrier has multiple producers")
            if entry_carriers.count(target) != 1:
                raise ValueError("Assembly actual target carrier aliases multiple public entries")
            if source in entry_carriers or target in exit_carriers:
                raise ValueError("Assembly connected carrier has an unsupported cross-direction public boundary alias")
            sources.add(source)
            targets.add(target)
        for terminal in (module.terminal, *module.terminal_alternatives):
            carrier = baseline.port_places[f"{terminal.source.component}.{terminal.source.port}"]
            if carrier in sources:
                raise ValueError("Assembly selected terminal carrier is consumed through an actual place alias")
        document = module.to_dict()
        document["exit"] = {name: endpoint for name, endpoint in document["exit"].items()
            if baseline.port_places[f"{endpoint['component']}.{endpoint['port']}"] not in sources}
        module = ModuleDeclaration.from_dict(document)
        # Contract only public exit aliases; component contexts, original
        # fragments and actual compile-consumed HOST declarations are unchanged.
        # Rehydrate using the sole compiler wire/verification path, not another
        # HOST call or an independently authored PN topology.
        symbolic, _ = compose_fragments(module, compiled.fragments)
        from ..executable_net import _load_compiled_net_offline
        loader = _load_compiled_net_offline if _offline_schema_validation else load_compiled_net
        compiled = loader(_make_document(module.to_dict(), compiled.fragments,
            asdict(symbolic), compiled.registrations))
    return module, compiled


def _declaration_targets(module, compiled):
    document = compiled.to_dict()
    symbolic = document["symbolic"]
    places = {p["name"]: index for index, p in enumerate(symbolic["places"])}
    ports = {p["name"]: index for index, p in enumerate(document["ports"])}
    operations = {p["name"]: index for index, p in enumerate(symbolic["operations"])}

    def port(name):
        place = symbolic["port_places"][name]
        return [_pointer("ports", ports[name]), _pointer("port_handles", name),
                _pointer("symbolic", "port_places", name), _pointer("symbolic", "places", places[place])]

    def operation(name):
        return [_pointer("operation_handles", name), _pointer("symbolic", "operations", operations[name]),
                *[_pointer("symbolic", "transitions", i) for i, t in enumerate(symbolic["transitions"])
                  if t["operation"] == name]]

    targets = {"/": ["/source", "/symbolic"]}
    for component in module.components:
        name = component.name
        base = f"/components/{name}"
        paths = [_pointer("fragments", name)]
        fragment = document["fragments"][name]
        for place in fragment["places"]:
            original = f"{name}.{place['name']}"
            paths.extend((_pointer("place_aliases", original),
                          _pointer("symbolic", "places", places[compiled.place_aliases[original]])))
        for field in ("transitions", "operations", "lease_identities", "lease_pools", "logical_slots"):
            names = {f"{name}.{item['name']}" for item in fragment[field]}
            paths.extend(_pointer("symbolic", field, i) for i, item in enumerate(symbolic[field]) if item["name"] in names)
        transitions = {f"{name}.{item['name']}" for item in fragment["transitions"]}
        for field in ("arcs", "reset_arcs", "variable_resource_arcs"):
            paths.extend(_pointer("symbolic", field, i) for i, item in enumerate(symbolic[field])
                         if item["transition"] in transitions)
        for value in compiled.ports:
            if value.name.startswith(name + "."):
                paths.extend(port(value.name))
        for value in compiled.operations:
            if value.declaration.name.startswith(name + "."):
                paths.extend(operation(value.declaration.name))
        targets[base] = paths
        for value in component.ports:
            targets[f"{base}/ports/{value.name}"] = port(f"{name}.{value.name}")
        for value in component.operations:
            targets[f"{base}/operations/{value.name}"] = operation(f"{name}.{value.name}")
    for index, link in enumerate(module.links):
        targets[f"/links/{index}"] = [_pointer("source", "links", index),
            *port(f"{link.source.component}.{link.source.port}"), *port(f"{link.target.component}.{link.target.port}")]
    for kind in ("entry", "exit"):
        for name, endpoint in getattr(module, kind).items():
            targets[f"/{kind}/{name}"] = [_pointer("source", kind, name), _pointer("symbolic", kind, name),
                *port(f"{endpoint.component}.{endpoint.port}")]
    for index, terminal in enumerate((module.terminal, *module.terminal_alternatives)):
        parts = ("terminal",) if index == 0 else ("terminal_alternatives", index - 1)
        locator = "/terminal" if index == 0 else f"/terminal_alternatives/{index - 1}"
        targets[locator] = [_pointer("source", *parts), _pointer("symbolic", *parts),
            *port(f"{terminal.source.component}.{terminal.source.port}"),
            *operation(f"{terminal.source.component}.{terminal.operation}")]
    targets = {key: sorted(set(value)) for key, value in targets.items()}
    for pointers in targets.values():
        for pointer in pointers:
            resolve_pointer(document, pointer)
    aliases = [{"original_place": original, "compiled_place": representative,
               "compiled_targets": [_pointer("place_aliases", original), _pointer("symbolic", "places", places[representative])]}
              for original, representative in sorted(compiled.place_aliases.items())]
    return targets, aliases


def lowering_map(plan, members, module, compiled, assembly_identity, *, baseline=None):
    """Cover every origin and generated declaration, allowing N:1 and 1:N."""
    targets, aliases = _declaration_targets(module, compiled)
    generated = _elements(module)
    origins, introduced, seeds = [], [], {}
    offset = 0
    connection_offset = sum(len(value.module.links) for value in members.values())
    if baseline is None:
        _, baseline = _carrier_cut(plan, members, compiled)
    for member in plan["members"]:
        identity = member["member_id"]
        value = members[identity]
        qualified = prefix(identity)
        for element in value.element_map["elements"]:
            locator, kind = element["locator"], element["kind"]
            disposition = "retained"
            if kind == "module":
                disposition = "flattened"
                locators = ["/", *[f"/components/{qualified}_{c.name}" for c in value.module.components]]
            elif kind in {"component", "port", "operation"}:
                locators = [locator.replace("/components/", f"/components/{qualified}_", 1)]
            elif kind == "link":
                locators = [f"/links/{offset + int(locator.split('/')[-1])}"]
            elif kind in {"entry", "exit"}:
                name = locator.split("/", 2)[2]
                retained = f"/{kind}/{qualified}_{name}"
                if retained in generated:
                    locators = [retained]
                else:
                    disposition = "consumed"
                    endpoint = getattr(value.module, kind)[name]
                    carrier = baseline.port_places[f"{qualified}_{endpoint.component}.{endpoint.port}"]
                    locators = []
                    for index, link in enumerate(module.links[connection_offset:]):
                        selected = link.source if kind == "exit" else link.target
                        if baseline.port_places[f"{selected.component}.{selected.port}"] == carrier:
                            locators.append(f"/links/{connection_offset + index}")
                    if not locators:
                        raise ValueError("Assembly consumed boundary has no explicit connection provenance")
            elif identity == plan["completion"]["member_id"]:
                disposition = "root_completion"
                locators = [locator]
            else:
                disposition = "not_root_completion"
                terminal = value.module.terminal if locator == "/terminal" else value.module.terminal_alternatives[int(locator.split('/')[-1])]
                locators = [f"/components/{qualified}_{terminal.source.component}/ports/{terminal.source.port}",
                            f"/components/{qualified}_{terminal.source.component}/operations/{terminal.operation}"]
            for destination in locators:
                if destination not in generated:
                    raise ValueError("Assembly origin names an absent generated declaration")
            origins.append({"member_id": identity, "revision_ref": member["revision_ref"],
                "element_id": element["element_id"], "kind": kind, "source_locator": locator,
                "disposition": disposition, "declaration_locators": sorted(locators)})
            if disposition in {"retained", "root_completion"}:
                for destination in locators:
                    seeds[destination] = {"member_id": identity, "element_id": element["element_id"]}
        offset += len(value.module.links)
    introduced.append({"source_locator": "/", "declaration_locators": ["/"]})
    seeds["/"] = {"assembly": "module"}
    for index, connection in enumerate(plan["connections"]):
        destination = f"/links/{connection_offset + index}"
        introduced.append({"source_locator": f"/connections/{index}", "declaration_locators": [destination]})
        seeds[destination] = {"connection": connection}
    for destination, kind in sorted(generated.items()):
        if kind in {"entry", "exit", "terminal"}:
            # These are the Assembly's exposed boundary and root completion,
            # with original member sources also present in `origins`.
            source = "/completion" if kind == "terminal" else "/members"
            introduced.append({"source_locator": source, "declaration_locators": [destination]})
    ids = {locator: "element:" + uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        "assembly_identity": assembly_identity, "origin": seeds[locator]})).hex for locator in generated}
    declarations = [{"element_id": ids[locator], "kind": kind, "locator": locator,
                     "compiled_targets": targets[locator]} for locator, kind in sorted(generated.items())]
    return {"schema_version": LOWERING_SCHEMA, "origins": origins, "introduced": introduced,
            "declarations": declarations, "place_aliases": aliases}, ids
