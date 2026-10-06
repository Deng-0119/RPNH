"""Compose exact declaration origins along member-instance paths.

Child source pointers are read from the validated child proof. Root pointers are
recomputed from the actual root declarations and fragments, never string-edited
from child compiled resources.
"""
from ._assembly_lowering import prefix, _pointer as ptr
from .assembly_v3 import ResolvedAssemblyMember
from .graph_authoring import ValidatedGraphRevision
from ._graph_assembly_origins_v3 import graph_origins


def transitive_origins(plan, members, module, compiled, mapping):
    direct = {(row["member_id"], row["element_id"]): row for row in mapping["origins"]}
    targets = {row["locator"]: row["compiled_targets"] for row in mapping["declarations"]}
    instances, assembly_members, introduced, placements = [], [], [], []
    for index, spec in enumerate(plan["members"]):
        identity, reference = spec["member_id"], spec["revision_ref"]
        member = members[identity]
        rows = []
        for origin in mapping["origins"]:
            if origin["member_id"] != identity:
                continue
            row = {k: v for k, v in origin.items() if k not in {"member_id", "disposition"}}
            row.update(member_path=[identity], revision_path=[reference],
                dispositions=[origin["disposition"]], via_elements=[])
            rows.append(row); instances.append(row)
        constraint = ("designer_constraints", "assembly_member_constraints", "members", index, "constraints")
        if isinstance(member, ValidatedGraphRevision):
            placements.append({"member_path": [identity], "revision_path": [reference], "member": member,
                "component": prefix(identity) + "_team", "constraints_pointer": ptr(*constraint), "origins": rows})
        if not isinstance(member, ResolvedAssemblyMember):
            continue
        proof = member.proof
        assembly_members.append({"member_path": [identity], "revision_path": [reference], "revision_ref": reference,
            **{field: getattr(proof.revision, field).to_dict() for field in
                ("plan_ref", "generated_revision_ref", "compiled_inventory_ref", "lowering_mapping_ref")}})
        child_elements = {row["locator"]: row["element_id"] for row in member.element_map["elements"]}

        def project(locators):
            selected = [direct[identity, child_elements[locator]] for locator in locators]
            return (sorted({locator for row in selected for locator in row["declaration_locators"]}),
                sorted({row["disposition"] for row in selected}),
                sorted({child_elements[locator] for locator in locators}))

        for child_index, child_spec in enumerate(proof.plan["members"]):
            child_id, child_ref = child_spec["member_id"], child_spec["revision_ref"]
            leaf = member.members[child_id]
            rows = []
            for origin in proof.lowering_map["origins"]:
                if origin["member_id"] != child_id:
                    continue
                locators, dispositions, via = project(origin["declaration_locators"])
                row = {k: v for k, v in origin.items() if k not in {"member_id", "disposition", "declaration_locators"}}
                row.update(member_path=[identity, child_id], revision_path=[reference, child_ref],
                    declaration_locators=locators, dispositions=sorted(set([origin["disposition"], *dispositions])),
                    via_elements=via)
                rows.append(row); instances.append(row)
            if isinstance(leaf, ValidatedGraphRevision):
                placements.append({"member_path": [identity, child_id], "revision_path": [reference, child_ref],
                    "member": leaf, "component": prefix(identity) + "_" + prefix(child_id) + "_team",
                    "constraints_pointer": ptr(*constraint, "assembly_member_constraints", "members", child_index, "constraints"),
                    "origins": rows})
        for origin in proof.lowering_map["introduced"]:
            locators, dispositions, via = project(origin["declaration_locators"])
            introduced.append({"member_path": [identity], "revision_path": [reference], "revision_ref": reference,
                "source_locator": origin["source_locator"], "child_declaration_locators": origin["declaration_locators"],
                "declaration_locators": locators, "dispositions": dispositions, "via_elements": via,
                "compiled_targets": sorted({target for locator in locators for target in targets[locator]})})
    graph = graph_origins(placements, module, compiled)
    return {**graph, "instance_origins": instances, "assembly_members": assembly_members,
        "assembly_introduced_origins": introduced}
