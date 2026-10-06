"""Finite member proofs checked against exact final-context local fragments."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib

from ..compiler import _compile_module_offline
from ..composition import compose_fragments
from ..control_ir import PROOF_KEY, verify_control_proof
from ..executable_net import _ContractInventory, load_compiled_control_net
from ..module import ModuleDeclaration
from ..net_operations.definitions import ComposeConnection, ComposePlan, compose_modules
from ..registry.event_store import RegistryConflict
from ..registry.schema_catalog import canonical_json
from ._assembly_lowering import prefix, _pointer, _contract_carriers, lowering_map
from .materials import ValidatedClosedRevision

FINITE = "finite_control_ir_v1"
PLAIN = "plain_closed_v1"
GENERATED = "assembly_generated_v9"
CONSTRAINTS_CONTRACT = "rpnh/assembly_member_constraints/v9"


def require_module_role(module, role):
    """A narrow opt-in role, never a global taint or a legacy author policy."""
    constraints = module.to_dict()["designer_constraints"]
    if role == FINITE:
        if (set(constraints) != {PROOF_KEY} or type(constraints[PROOF_KEY]) is not dict
                or type(constraints[PROOF_KEY].get("author")) is not dict
                or constraints[PROOF_KEY]["author"].get("backend") != "business_pn/v1"):
            raise RegistryConflict("claim_not_proved: finite member requires its complete original ControlIR proof")
    elif role == PLAIN:
        if constraints:
            raise RegistryConflict("claim_not_proved: plain member cannot erase typed or generated proof")
    elif role == GENERATED:
        if (set(constraints) != {"native_composition", "assembly_member_constraints"}
                or type(constraints["assembly_member_constraints"]) is not dict
                or set(constraints["assembly_member_constraints"]) != {"contract", "members"}
                or constraints["assembly_member_constraints"]["contract"] != CONSTRAINTS_CONTRACT
                or type(constraints["assembly_member_constraints"]["members"]) is not list
                or not constraints["assembly_member_constraints"]["members"]
                or type(constraints["native_composition"]) is not dict
                or set(constraints["native_composition"]) != {"mode", "instances"}
                or constraints["native_composition"]["mode"] != "explicit"):
            raise RegistryConflict("claim_not_proved: exact v9 generated role required")
    else:
        raise RegistryConflict("unsupported_contract: unknown Assembly v9 history role")
    if role != GENERATED and any(c.key in {f"rpnh/agent-workflow-graph/v{n}" for n in range(1, 5)} for c in module.components):
        raise RegistryConflict("unsupported_contract: graph member requires its own source consumer")


def require_history_role(db, core, reference, binding, role):
    """Check canonical authority before materials can dispatch or invoke lower."""
    from .authoring import _read_net_revision_at
    from .materials import _material, MODULE_SCHEMA
    if reference.ref.entity_type != "collaboration_net_revision/v1":
        raise RegistryConflict("unsupported_contract: v9 history requires exact closed-v1 revisions")
    revision = _read_net_revision_at(db, core, reference, local_source_id=binding["source_id"])
    if (revision.definition_kind != "closed_module" or revision.open_region_contract_ref is not None
            or len(revision.parent_revision_refs) > 1 or revision.selected_change_refs):
        raise RegistryConflict("unsupported_contract: v9 history requires ordinary single-parent closed authority")
    document, metadata = _material(db, core, revision.definition_ref, binding, MODULE_SCHEMA)
    if set(metadata["descriptors"]) != {"closed_author_command_v1"}:
        raise RegistryConflict("claim_not_proved: v9 history cannot erase another author proof")
    require_module_role(ModuleDeclaration.from_dict(document), role)


def _signature(value):
    raw = canonical_json(value)
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _members(plan, members):
    from .assembly_v9 import resolver_recipe
    if canonical_json(plan["resolver_recipe"]) != canonical_json(resolver_recipe()):
        raise ValueError("unsupported_contract: exact Assembly v9 resolver recipe required")
    rows = plan["members"]
    identities = [row["member_id"] for row in rows]
    if (not identities or identities != sorted(set(identities)) or set(members) != set(identities)
            or plan["budget_policy"] != "shared_exact" or plan["deployment_intent"] != "same_run_candidate"):
        raise ValueError("Assembly v9 requires canonical exact members and explicit context policy")
    budgets, buckets, constraints = {}, {}, []
    for row in rows:
        member = members[row["member_id"]]
        if type(member) is not ValidatedClosedRevision or member.revision.revision_ref.to_dict() != row["revision_ref"]:
            raise ValueError("Assembly v9 requires exact full-consumed ordinary closed members")
        require_module_role(member.module, row["claim"])
        if row["claim"] == FINITE:
            # First check: the source member's own complete typed wire and proof.
            load_compiled_control_net(member.compiled.to_json())
        doc = member.module.to_dict()
        constraints.append({"member_id": row["member_id"], "revision_ref": row["revision_ref"],
            "claim": row["claim"], "constraints": deepcopy(doc["designer_constraints"])})
        for target, values, label in ((budgets, doc["budgets"].items(), "budget"),
                (buckets, ((b["bucket_id"], b) for b in doc["budget_buckets"]), "budget bucket")):
            for key, value in values:
                if key in target and canonical_json(target[key]) != canonical_json(value):
                    raise ValueError("shared_exact conflicting " + label + ": " + key)
                target[key] = value
        own = {b["bucket_id"]: b for b in doc["budget_buckets"]}
        for component in member.module.components:
            for operation in component.operations:
                binding = operation.budget_binding
                if binding is not None:
                    bucket = own.get(binding.bucket_id)
                    if bucket is None or any(getattr(binding, key) != bucket[key] for key in ("budget_scope", "finalization_scope")):
                        raise ValueError("member budget binding differs from its exact bucket")
    return {"contract": CONSTRAINTS_CONTRACT, "members": constraints}


def compose_declarations(plan, members, *, connections=True):
    projection = _members(plan, members)
    lookup = {key: {item["element_id"]: item for item in value.element_map["elements"]} for key, value in members.items()}
    selected = lookup.get(plan["completion"]["member_id"], {}).get(plan["completion"]["terminal_element_id"])
    if selected is None or selected["locator"] != "/terminal":
        raise ValueError("completion must select the exact member primary terminal")
    links, targets = [], set()
    for connection in plan["connections"] if connections else ():
        left, right = connection["source_member_id"], connection["target_member_id"]
        endpoints = []
        for member_id, key, kind in ((left, "source_exit_element_id", "exit"), (right, "target_entry_element_id", "entry")):
            element = lookup.get(member_id, {}).get(connection[key])
            if element is None or element["kind"] != kind:
                raise ValueError("connection must select exact member boundary elements")
            endpoints.append(element["locator"].split("/", 2)[2])
        endpoint = members[right].module.entry[endpoints[1]]
        identity = right, endpoint.component, endpoint.port
        if identity in targets or sum(value == endpoint for value in members[right].module.entry.values()) != 1:
            raise ValueError("connected entry cannot alias or have multiple producers")
        targets.add(identity)
        links.append(ComposeConnection(prefix(left), endpoints[0], prefix(right), endpoints[1]))
    module = compose_modules({prefix(row["member_id"]): members[row["member_id"]].module for row in plan["members"]},
        ComposePlan(plan["name"], prefix(plan["completion"]["member_id"]), tuple(links), mode="explicit"))
    document = module.to_dict()
    document["designer_constraints"]["assembly_member_constraints"] = projection
    return ModuleDeclaration.from_dict(document)


def verify_final_projection(plan, members, module, compiled):
    """Second check: original proof against actual final-context fragments.

    Only dictionary keys project back. Every fragment's local symbols and
    values remain untouched; no source-precompiled fragment is substituted.
    """
    actual_components = {component.name: component for component in module.components}
    expected_names = {prefix(row["member_id"]) + "_" + c.name for row in plan["members"]
                      for c in members[row["member_id"]].module.components}
    if set(actual_components) != expected_names or set(compiled.fragments) != expected_names:
        raise ValueError("Assembly v9 exact final component/fragment namespace differs")
    inventory = _ContractInventory(compiled.registrations)
    for row in plan["members"]:
        member = members[row["member_id"]]
        projection = {}
        for original in member.module.components:
            final = prefix(row["member_id"]) + "_" + original.name
            expected = asdict(original)
            expected["name"] = final
            if canonical_json(asdict(actual_components[final])) != canonical_json(expected):
                raise ValueError("Assembly v9 component declaration projection differs")
            projection[original.name] = compiled.fragments[final]
        if row["claim"] == FINITE:
            verify_control_proof(member.module, projection, inventory)
    symbolic, aliases = compose_fragments(module, compiled.fragments)
    if (canonical_json(asdict(symbolic)) != canonical_json(asdict(compiled.symbolic))
            or aliases != compiled.place_aliases):
        raise ValueError("Assembly v9 exact symbolic/fragment composition differs")


def compose_plan_v9(plan, members, registration):
    module = compose_declarations(plan, members)
    compiled = _compile_module_offline(module, registration)
    baseline = compose_declarations(plan, members, connections=False)
    module, compiled = _contract_carriers(plan, members, module, compiled,
        baseline_module=baseline, _offline_schema_validation=True)
    verify_final_projection(plan, members, module, compiled)
    return module, compiled


def lowering_map_v9(plan, members, module, compiled, assembly_identity):
    from .assembly_v9 import LOWERING_V9_SCHEMA, ORIGIN_CONTRACT
    baseline, _ = compose_fragments(compose_declarations(plan, members, connections=False), compiled.fragments)
    mapping, ids = lowering_map(plan, members, module, compiled, assembly_identity, baseline=baseline)
    document = compiled.to_dict()
    coverage, carriers = [], []
    for row in plan["members"]:
        member = members[row["member_id"]]
        elements = {element["locator"]: element["element_id"] for element in member.element_map["elements"]}
        for original in member.module.components:
            final = prefix(row["member_id"]) + "_" + original.name
            fragment = document["fragments"][final]
            context = {"component": final, "ports": [asdict(p) for p in original.ports],
                "required_schemas": list(module.required_schemas), "budgets": deepcopy(module.budgets),
                "operations": [asdict(o) for o in original.operations]}
            coverage.append({"member_id": row["member_id"], "revision_ref": row["revision_ref"],
                "claim": row["claim"], "source_component_element_id": elements["/components/" + original.name],
                "source_component_locator": "/components/" + original.name, "component": final,
                "context": context, "fragment_pointer": _pointer("fragments", final),
                "fragment_signature": _signature(fragment),
                "fields": [{"field": field, "fragment_pointers": [_pointer("fragments", final, field, i)
                    for i in range(len(values))]} for field, values in sorted(fragment.items())]})
            for port in original.ports:
                source_port, final_port = original.name + "." + port.name, final + "." + port.name
                carriers.append({"member_id": row["member_id"], "source_port": source_port,
                    "source_carrier": member.compiled.symbolic.port_places[source_port], "final_port": final_port,
                    "final_cut_carrier": baseline.port_places[final_port],
                    "final_fused_carrier": compiled.symbolic.port_places[final_port]})
    mapping.update(schema_version=LOWERING_V9_SCHEMA, origin_contract=ORIGIN_CONTRACT,
        member_proofs=[{key: deepcopy(row[key]) for key in ("member_id", "revision_ref", "claim", "resolution")} for row in plan["members"]],
        fragment_origins=coverage, carrier_origins=carriers)
    return mapping, ids
