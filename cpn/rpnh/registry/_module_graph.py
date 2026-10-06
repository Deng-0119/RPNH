"""Pure Module graph projection shared by publication and exact validation.

This module allocates deterministic references and constructs data only. It has
no Registry connection, object store, Registration, HOST factory or executor.
The caller remains responsible for authority validation and atomic publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .models import VersionRef
from .module_host_bindings import HostExecutionBinding, ModuleHostBindingPlan
from .module_resources import (ModuleResourcePlan, entry_resource_bundle,
    prepare_module_resources, module_slot_bindings)
from .schema_catalog import canonical_json
from .strict_contracts import _stable_id, content_schema_ref_payload, ref_payload


def _refs(values):
    return [ref_payload(ref) for ref in sorted(set(values), key=lambda r: canonical_json(ref_payload(r)))]



def allocate_module_graph(compiled, *, identity, task_round_ref, specs, idempotency_key):
    """Allocate the original lexical IDs before any explicit HOST factory runs."""
    def allocate(object_type, logical_kind, version_kind, suffix):
        material = f"{idempotency_key}:{suffix}"
        return VersionRef(object_type, _stable_id(logical_kind, material), _stable_id(version_kind, material))

    plan = allocate("plan_version/v1", "plan", "plan_version", "plan")
    root = allocate("team_design_root/v1", "team_design_root", "team_design_root_version", "root")
    net = allocate("net_instance/v1", "net_instance", "net_instance_version", "net")
    transitions = sorted(compiled.symbolic.transitions, key=lambda t: t.name)
    nodes = {t.name: allocate("node_declaration/v1", "node", "node_declaration_version", f"node:{t.name}") for t in transitions}
    bindings = {t.name: allocate("operation_binding/v1", "operation_binding", "operation_binding_version", f"binding:{t.name}") for t in transitions}
    executables = {t.name: allocate("executable_transition_binding/v1", "executable_transition_binding", "executable_transition_binding_version", f"executable:{t.name}") for t in transitions}
    outputs = {(t.name, p["port_id"]): allocate("output_binding/v1", "output_binding", "output_binding_version", f"output:{t.name}:{p['port_id']}")
               for t in transitions for p in specs[t.operation]["output_ports"]}
    host_plan = ModuleHostBindingPlan(identity.task_ref, identity.run_ref, task_round_ref,
        plan, root, net, MappingProxyType(nodes), MappingProxyType(bindings), MappingProxyType(outputs))
    return plan, root, net, nodes, bindings, executables, outputs, host_plan


@dataclass(frozen=True, slots=True)
class ModuleGraphProjection:
    objects: tuple[tuple[VersionRef, dict], ...]
    resource_plan: ModuleResourcePlan
    resource_bindings: dict


def materialize_module_graph(compiled, *, identity, task_round_ref, principal_ref,
        authority_decision_ref, declaration, schema_refs, operation_refs, specs,
        entry_inputs, owner_resource_inputs, owner_input_resources, preserved_slot_refs,
        host_bindings, host_resources, host_artifacts, idempotency_key):
    """Reconstruct all graph fields from explicit, already validated inputs."""
    schemas = schema_refs
    ports = {port.name: port for port in compiled.ports}
    operations = {item.declaration.name: item for item in compiled.operations}
    transitions = sorted(compiled.symbolic.transitions, key=lambda t: t.name)
    plan, root, net, nodes, bindings, executables, outputs, _ = allocate_module_graph(
        compiled, identity=identity, task_round_ref=task_round_ref, specs=specs,
        idempotency_key=idempotency_key)
    targets = {binding.llm_input_target_ref for binding in host_bindings.values()
               if binding.llm_input_target_ref is not None}
    shared_target = next(iter(targets)) if len(targets) == 1 else None
    resource_plan = prepare_module_resources(compiled, root_ref=root, net_ref=net,
        declaration_ref=declaration, node_refs=nodes, output_binding_refs=outputs,
        owner_resource_inputs=owner_resource_inputs, idempotency_key=idempotency_key,
        preserved_slot_refs=preserved_slot_refs)
    resource_bindings = {"command_id": idempotency_key,
        "owner_resource_inputs": {name: content_schema_ref_payload(ref)
                                  for name, ref in sorted(owner_resource_inputs.items())},
        "lease_refs": {name: ref_payload(ref) for name, ref in sorted(resource_plan.lease_refs.items())},
        "slot_refs": {name: ref_payload(ref) for name, ref in sorted(resource_plan.slot_refs.items())},
        "slot_bindings": module_slot_bindings(resource_plan)}
    shared = {"task_round_ref": ref_payload(task_round_ref),
        "team_design_root_ref": ref_payload(root), "llm_macro_net_ref": ref_payload(declaration.as_version_ref()),
        "team_net_declaration_resource_ref": content_schema_ref_payload(declaration),
        "executable_transition_binding_refs": _refs(executables.values()), "node_refs": _refs(nodes.values()),
        "operation_binding_refs": _refs(bindings.values()),
        "output_binding_refs": _refs(outputs.values())}
    objects = [(plan, {"plan_id": str(plan.entity_id), "plan_version_id": str(plan.version_id),
                       "node_ids": [str(ref.entity_id) for ref in nodes.values()]}),
        (root, dict(shared, owner_principal_ref=ref_payload(principal_ref), run_ref=ref_payload(identity.run_ref),
            task_ref=ref_payload(identity.task_ref), resource_refs=_refs([declaration.as_version_ref(),
                *(r.as_version_ref() for r in schemas.values()),
                *(r.as_version_ref() for bundle in entry_inputs.values() for r in entry_resource_bundle(bundle)),
                *(r.as_version_ref() for r in owner_resource_inputs.values()),
                *(r.as_version_ref() for r in owner_input_resources), *host_resources]),
            artifact_refs=_refs([plan, authority_decision_ref, *operation_refs.values(),
                                 *resource_plan.slot_refs.values(), *host_artifacts]),
            llm_input_target_ref=content_schema_ref_payload(shared_target) if shared_target is not None else None)),
        (net, dict(shared, net_instance_ref=ref_payload(net), plan_ref=ref_payload(plan),
                   module_resource_bindings=resource_bindings))]
    objects.extend((slot.ref, slot.metadata_dict()) for slot in resource_plan.proposed_slots)
    for index, transition in enumerate(transitions):
        name = transition.name
        item = operations[transition.operation]
        spec = specs[transition.operation]
        host_binding = host_bindings.get(name, HostExecutionBinding())
        activation_payload = (ref_payload(host_binding.activation_ref)
                              if host_binding.activation_ref is not None else None)
        workspace_payload = (ref_payload(host_binding.workspace_binding_ref)
                             if host_binding.workspace_binding_ref is not None else None)
        target_payload = (content_schema_ref_payload(host_binding.llm_input_target_ref)
                          if host_binding.llm_input_target_ref is not None else None)
        input_schemas = _refs(schemas[ports[p].schema].as_version_ref() for p in item.declaration.inputs)
        output_schemas = _refs(schemas[ports[p].schema].as_version_ref() for p in item.declaration.outputs)
        entry_resources = [r.as_version_ref() for key, bundle in entry_inputs.items()
            if ports[compiled.symbolic.entry[key]].place in {ports[p].place for p in item.declaration.inputs}
            for r in entry_resource_bundle(bundle)]
        entry_resources.extend(owner_resource_inputs[claim.expected_resource].as_version_ref()
            for arc in compiled.symbolic.variable_resource_arcs if arc.transition == name
            for claim in arc.initial_claims if claim.expected_resource is not None)
        if host_binding.llm_input_target_ref is not None:
            entry_resources.append(host_binding.llm_input_target_ref.as_version_ref())
        entry_resources.extend(ref.as_version_ref() for ref in host_binding.extra_resource_refs)
        resources = _refs([declaration.as_version_ref(), *entry_resources])
        offered = _refs(outputs[name, p["port_id"]] for p in spec["output_ports"])
        budget = item.declaration.budget_binding
        budget_binding = ({"budget_bucket_id": budget.bucket_id,
                           "budget_scope": budget.budget_scope,
                           "finalization_scope": budget.finalization_scope}
                          if budget is not None else {"budget_bucket_id": item.operation_id,
                              "budget_scope": item.operation_id, "finalization_scope": None})
        bounds = item.declaration.config.get("resource_bounds", {"max_llm_attempts": 0, "max_tool_turns": 0})
        objects.append((nodes[name], {"node_ref": ref_payload(nodes[name]),
            "semantic_node_id": item.declaration.config.get("semantic_node_id", f"node_{index}"),
            "node_synopsis": item.declaration.config.get("node_synopsis", name),
            "transition_id": name, "activation_ref": activation_payload,
            "team_design_root_ref": ref_payload(root), "plan_ref": ref_payload(plan),
            "opaque_role_artifact_ref": ref_payload(operation_refs[transition.operation]),
            "input_resource_refs": resources, "input_schema_refs": input_schemas, "output_schema_refs": output_schemas,
            "producer_operation_binding_ref": ref_payload(bindings[name]), "offered_output_binding_refs": offered,
            "resource_bounds": bounds}))
        objects.append((bindings[name], {"operation_binding_id": str(bindings[name].entity_id),
            "operation_binding_version_id": str(bindings[name].version_id), "operation_binding_ref": ref_payload(bindings[name]),
            "origin": "petri_operation", "node_ref": ref_payload(nodes[name]), "principal_ref": ref_payload(principal_ref),
            "team_design_root_ref": ref_payload(root), "operation_spec_ref": ref_payload(operation_refs[transition.operation]),
            "authority_decision_ref": ref_payload(authority_decision_ref), "code_artifact_ref": None, "llm_input_target_ref": target_payload,
            "input_binding_refs": _refs([declaration.as_version_ref(), *(schemas[ports[p].schema].as_version_ref() for p in item.declaration.inputs),
                *entry_resources]),
            "input_schema_refs": input_schemas, "output_schema_refs": output_schemas, "restartability_policy": "registry_reconciled",
            **budget_binding,
            # Role is declaration-owned data, not an executor identity or Core
            # vocabulary. The schema validates its nonempty string contract.
            "agent_loop_role": item.declaration.config.get("agent_loop_role", "mechanical"),
            "module_artifact_refs": _refs(
                host_binding.module_artifact_refs),
            "discoverable_resource_refs": resources, "readable_resource_refs": resources,
            "workspace_binding_ref": workspace_payload, "output_binding_refs": offered,
            "permitted_write_intent_factory_refs": [workspace_payload] if workspace_payload is not None else [],
            "task_header_query": False,
            "allowed_publication_origins": ["petri_output", "workspace_write"] if workspace_payload is not None else ["petri_output"],
            "resource_read_contracts": spec["implementation_contracts"].get("resource_read_contracts", [])}))
        arcs = [a for a in compiled.symbolic.arcs if a.transition == name]
        objects.append((executables[name], {"executable_transition_binding_ref": ref_payload(executables[name]),
            "net_instance_ref": ref_payload(net), "declaration_resource_ref": content_schema_ref_payload(declaration),
            "declaration_schema_ref": compiled.schema_version, "transition_id": name, "node_ref": ref_payload(nodes[name]),
            "activation_ref": activation_payload,
            "agent_ref": ref_payload(host_binding.agent_ref) if host_binding.agent_ref is not None else None,
            "operation_binding_ref": ref_payload(bindings[name]),
            "principal_ref": ref_payload(principal_ref),
            "input_place_ids": sorted({a.place for a in arcs if a.direction == "input"}),
            "output_place_ids": sorted({a.place for a in arcs if a.direction == "output"})}))
        for port in spec["output_ports"]:
            ref = outputs[name, port["port_id"]]
            symbolic_output_name = next(
                output_name for output_name in item.declaration.outputs
                if ports[output_name].port_id == port["port_id"])
            declared_outcomes = tuple(
                outcome.name for outcome in item.declaration.outcomes
                if any(product.port == symbolic_output_name
                       for product in outcome.products))
            output_metadata = {"output_binding_id": str(ref.entity_id), "output_binding_version_id": str(ref.version_id),
                "output_port_id": port["port_id"], "task_round_ref": ref_payload(task_round_ref), "net_ref": ref_payload(net),
                "node_ref": ref_payload(nodes[name]), "team_design_root_ref": ref_payload(root),
                "opaque_action_ref": ref_payload(operation_refs[transition.operation]), "place": port["place"],
                "place_ref": port["schema_ref"], "content_schema_ref": port["content_schema_ref"],
                "content_schema_id": ports[symbolic_output_name].schema,
                "normal_output_cardinality": port["cardinality"]}
            if len(declared_outcomes) == 1:
                output_metadata["declared_outcome_id"] = declared_outcomes[0]
            objects.append((ref, output_metadata))
    return ModuleGraphProjection(tuple(objects), resource_plan, resource_bindings)
