"""Direct-member composition and explicit adaptation-to-target proof."""
from __future__ import annotations

from copy import deepcopy

from ..compiler import compile_module
from ..composition import compose_fragments
from ..module import ModuleDeclaration
from ..registry.event_store import RegistryConflict
from ..registry.schema_catalog import canonical_text
from ._assembly_lowering import prefix, _pointer, _contract_carriers, lowering_map
from ._assembly_v2_lowering import compose_declarations as _compose_direct
from .assembly_v2 import resolver_recipe as _declaration_recipe
from .materials import _same_json


def target_matches(plan, members, module=None, compiled=None):
    """Derive target authority from this actual Assembly, never from intent."""
    from .assembly_v4 import RESOLVER_CONTRACT
    matches = []
    by_id = {row["member_id"]: row for row in plan["members"]}
    for row in plan["members"]:
        if row["claim"] != "adapted_result_current":
            continue
        member, identity = members[row["member_id"]], row["member_id"]
        intent = member.intent
        expected = {"kind": "prospective", "source_id": plan["source_id"], "owner_task_ref": plan["owner_task_ref"],
            "assembly_protocol": "collaboration_assembly_revision/v4", "resolver_contract": RESOLVER_CONTRACT,
            "assembly_command_id": plan["command_id"], "parent_assembly_revision_ref": plan["parent_revision_ref"],
            "target_scope": "direct_member", "member_id": identity, "completion_expectation": "member_primary_terminal"}
        if any(not _same_json(intent[key], value) for key, value in expected.items()):
            raise RegistryConflict("target_mismatch: exact prospective source/owner/command/parent/member differs")
        elements = {item["element_id"]: item for item in member.element_map["elements"]}
        primary = next(item["element_id"] for item in elements.values() if item["locator"] == "/terminal")
        if plan["completion"] != {"member_id": identity, "terminal_element_id": primary}:
            raise RegistryConflict("target_mismatch: adapted member must supply the actual root primary completion")
        dispositions = {item["item_id"]: item for item in member.adaptation["dispositions"]}
        ingress_matches = []
        for expectation in intent["ingress_expectations"]:
            disposition = dispositions.get(expectation["item_id"])
            if disposition is None:
                raise RegistryConflict("claim_not_proved: ingress disposition missing")
            entries = [element for element in disposition["output_element_ids"]
                       if element in elements and elements[element]["kind"] == "entry"]
            if len(entries) != 1:
                raise RegistryConflict("target_mismatch: ingress must identify one exact result entry")
            entry = entries[0]
            connections = [item for item in plan["connections"]
                           if item["target_member_id"] == identity and item["target_entry_element_id"] == entry]
            if expectation["kind"] == "public_entry":
                if connections:
                    raise RegistryConflict("target_mismatch: promised public ingress was consumed")
            else:
                other = by_id.get(expectation["other_member_id"])
                expected_connection = {"source_member_id": expectation["other_member_id"],
                    "source_exit_element_id": expectation["other_exit_element_id"],
                    "target_member_id": identity, "target_entry_element_id": entry}
                if (other is None or other["revision_ref"] != expectation["other_revision_ref"]
                        or connections != [expected_connection]):
                    raise RegistryConflict("target_mismatch: actual ingress connection differs from exact intent")
            if module is not None:
                name = elements[entry]["locator"].split("/", 2)[2]
                scoped_name = prefix(identity) + "_" + name
                endpoint = member.module.entry[name]
                exposed = module.entry.get(scoped_name)
                if expectation["kind"] == "public_entry":
                    if (exposed is None or exposed.component != prefix(identity) + "_" + endpoint.component
                            or exposed.port != endpoint.port):
                        raise RegistryConflict("target_mismatch: actual final Module does not expose promised ingress")
                    carrier = compiled.symbolic.port_places[exposed.component + "." + exposed.port]
                    if sum(compiled.symbolic.port_places[value.component + "." + value.port] == carrier
                           for value in module.entry.values()) != 1:
                        raise RegistryConflict("incompatible_boundary: final public ingress carrier is aliased")
                elif exposed is not None:
                    raise RegistryConflict("target_mismatch: connected ingress remained publicly exposed")
            ingress_matches.append({"item_id": expectation["item_id"], "entry_element_id": entry,
                "kind": expectation["kind"], "connection": connections[0] if connections else None})
        matches.append({"member_id": identity, "revision_ref": row["revision_ref"],
            "adaptation_ref": row["adaptation_ref"], "intent_ref": row["intent_ref"],
            "status": "matched_exact_target", "ingress": ingress_matches, "completion": deepcopy(plan["completion"])})
    return matches


def compose_declarations(plan, members, *, connections=True):
    from .assembly_v4 import CONSTRAINTS_CONTRACT
    # Reuse the established pure direct-member declaration projection. The
    # v4 full reader has already checked the distinct recipe and claim domain;
    # no v2 producer/consumer is asked to accept an adapted proof.
    projection = {**plan, "resolver_recipe": _declaration_recipe()}
    module = _compose_direct(projection, members, connections=connections)
    document = module.to_dict()
    document["designer_constraints"]["assembly_member_constraints"]["contract"] = CONSTRAINTS_CONTRACT
    return ModuleDeclaration.from_dict(document)


def compose_plan_v4(plan, members, registration):
    module = compose_declarations(plan, members)
    compiled = compile_module(module, registration)
    baseline = compose_declarations(plan, members, connections=False)
    module, compiled = _contract_carriers(plan, members, module, compiled, baseline_module=baseline)
    target_matches(plan, members, module, compiled)
    return module, compiled


def lowering_map_v4(plan, members, module, compiled, assembly_identity):
    from .assembly_v4 import LOWERING_V4_SCHEMA, ORIGIN_CONTRACT
    baseline, _ = compose_fragments(compose_declarations(plan, members, connections=False), compiled.fragments)
    mapping, ids = lowering_map(plan, members, module, compiled, assembly_identity, baseline=baseline)
    document = compiled.to_dict()
    coverage = []
    for row in plan["members"]:
        member = members[row["member_id"]]
        components = {item["locator"].split("/")[2]: item["element_id"]
            for item in member.element_map["elements"] if item["kind"] == "component"}
        for name, identity in sorted(components.items()):
            component = prefix(row["member_id"]) + "_" + name
            fragment = document["fragments"][component]
            # Cover every actual typed primitive, including empty field scans.
            # Source author component provenance is independent of runtime IDs.
            coverage.append({"member_id": row["member_id"], "revision_ref": row["revision_ref"],
                "source_component_element_id": identity, "component": component,
                "fields": [{"field": field, "fragment_pointers": [_pointer("fragments", component, field, i)
                    for i in range(len(values))]} for field, values in sorted(fragment.items())]})
    mapping.update(schema_version=LOWERING_V4_SCHEMA, origin_contract=ORIGIN_CONTRACT,
        member_proofs=[{key: deepcopy(row[key]) for key in
            ("member_id", "revision_ref", "claim", "adaptation_ref", "intent_ref", "resolution")} for row in plan["members"]],
        target_matches=target_matches(plan, members, module, compiled), fragment_origins=coverage)
    return mapping, ids
