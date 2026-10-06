"""Direct ordinary-derived composition and full historical member coverage."""
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


def compose_declarations(plan, members, *, connections=True):
    from .assembly_v5 import CONSTRAINTS_CONTRACT
    # Reuse the established pure direct-member declaration projection. The
    # v5 full reader has already checked the distinct recipe and claim domain;
    # no v2 producer/consumer is asked to accept a new derived proof.
    projection = {**plan, "resolver_recipe": _declaration_recipe()}
    module = _compose_direct(projection, members, connections=connections)
    document = module.to_dict()
    document["designer_constraints"]["assembly_member_constraints"]["contract"] = CONSTRAINTS_CONTRACT
    return ModuleDeclaration.from_dict(document)


def compose_plan_v5(plan, members, registration):
    module = compose_declarations(plan, members)
    compiled = compile_module(module, registration)
    baseline = compose_declarations(plan, members, connections=False)
    module, compiled = _contract_carriers(plan, members, module, compiled, baseline_module=baseline)
    return module, compiled


def lowering_map_v5(plan, members, module, compiled, assembly_identity):
    from .assembly_v5 import LOWERING_V5_SCHEMA, ORIGIN_CONTRACT
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
    mapping.update(schema_version=LOWERING_V5_SCHEMA, origin_contract=ORIGIN_CONTRACT,
        member_proofs=[{key: deepcopy(row[key]) for key in
            ("member_id", "revision_ref", "claim", "derived_command_ref", "historical_origins_ref", "resolution")} for row in plan["members"]],
        fragment_origins=coverage)
    return mapping, ids
