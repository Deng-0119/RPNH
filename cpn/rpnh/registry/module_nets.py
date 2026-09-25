"""Shared compiled Module -> immutable Registry closure, before admission.

The execution owner supplies already registered schemas/specs and entry
resources. Only an explicit trusted HOST binding factory may run here; no
executor, checkpoint, adoption or firing is created.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from jsonschema import Draft7Validator

from ..executable_net import CompiledPetriNet, load_compiled_net
from ..registration import Registration
from ._registry import _RegistryCore
from .bootstrap import NativeRunIdentity
from .event_store import validate_registered_net_closure, verified_adoption_head
from .identities import TypedId
from .models import VersionRef
from .module_host_bindings import (HostExecutionBinding, HostExecutionBindings,
    HostExecutionBindingFactory, ModuleHostBindingPlan, validate_host_execution_bindings)
from .module_resources import (ModuleResourcePlan, prepare_module_resources, entry_resource_bundle,
                               compatible_slot_symbols, module_slot_bindings)
from .publication import (_content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE,
                          _content_schema_source_from_payload)
from .resource_service import _publish_private_system
from .resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from .schema_catalog import canonical_json
from .strict_contracts import _registered, _stable_id, content_schema_ref_payload, ref_payload


@dataclass(frozen=True, slots=True)
class ModuleNetPublication:
    plan_ref: VersionRef
    root_ref: VersionRef
    net_ref: VersionRef
    declaration_resource_ref: ResourceVersionRef
    node_refs: Mapping[str, VersionRef]
    operation_refs: Mapping[str, VersionRef]
    binding_refs: Mapping[str, VersionRef]
    output_refs: Mapping[tuple[str, str], VersionRef]
    executable_refs: Mapping[str, VersionRef]
    logical_places: Mapping[str, str]
    transaction_id: TypedId
    resource_plan: ModuleResourcePlan


def _refs(values):
    return [ref_payload(ref) for ref in sorted(set(values), key=lambda r: canonical_json(ref_payload(r)))]


def _verify_input_schema_authority(core, data, schema_document_ref):
    """Reclose the resource's exact catalog-or-document schema authority."""
    source = _content_schema_source_from_payload(data["content_schema_authority_ref"])
    if isinstance(source, VersionRef):
        from .content_schemas import hydrate_registered_content_schema
        _registered(core, source, "registry_type_catalog/v1")
        prepared = core.get_version(source.version_id)
        authority = hydrate_registered_content_schema(core, source,
            schema_id=data["content_schema_ref"])
        bundle = json.loads(core.object_store.read_registered(prepared))
        document = core.get_version(schema_document_ref.resource_version_id)
        if json.loads(bundle["schemas"][authority.schema_id]["source"]) != json.loads(
                core.object_store.read_registered(document)):
            raise ValueError("catalog input schema differs from the exact registered document contract")
    else:
        authority = core.verify_registered_content_schema_ref(source,
            schema_document_ref=schema_document_ref.as_version_ref())
    if authority.schema_id != data["content_schema_ref"]:
        raise ValueError("input schema source resolves to a different registered contract")


def select_preserved_slot_refs(core, old_compiled, candidate_compiled):
    """HOST selector: only unchanged typed slots in the exact CurrentPN."""
    from .module_runtime import hydrate_module_resource_plan
    from .publication import _resource_from_payload, _version_from_payload
    net_ref = verified_adoption_head(core.event_store, core.catalog, core.task_id)
    net = validate_registered_net_closure(core.event_store, core.catalog, net_ref)
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    actual = load_compiled_net(json.loads(core.object_store.read_registered(
        core.get_version(declaration.resource_version_id))))
    if actual != old_compiled:
        raise ValueError("preserved slots require the exact CurrentPN declaration")
    root_ref = _version_from_payload(net["team_design_root_ref"])
    _, root = _registered(core, root_ref, "team_design_root/v1")
    plan = hydrate_module_resource_plan(core, actual, net_ref, net, root_ref, root, declaration)
    symbols = compatible_slot_symbols(actual, candidate_compiled)
    return MappingProxyType({name: ref for name, ref in plan.slot_refs.items() if name in symbols})


def publish_module_net(core: _RegistryCore, compiled: CompiledPetriNet,
                       registration: Registration, *, identity: NativeRunIdentity,
                       bootstrap_ref: VersionRef, principal_ref: VersionRef,
                       task_round_ref: VersionRef, authority_decision_ref: VersionRef,
                       entry_inputs: Mapping[str, ResourceVersionRef],
                       schema_refs: Mapping[str, ResourceVersionRef],
                       operation_refs: Mapping[str, VersionRef],
                       idempotency_key: str,
                       owner_resource_inputs: Mapping[str, ResourceVersionRef] = MappingProxyType({}),
                       owner_input_resources: tuple[ResourceVersionRef, ...] = (),
                       preserved_slot_refs: Mapping[str, VersionRef] = MappingProxyType({}),
                       host_execution_bindings: HostExecutionBindings | HostExecutionBindingFactory = MappingProxyType({}),
                       ) -> ModuleNetPublication:
    """Publish one typed graph transaction, then verify its real exact closure.

    Entry keys are Module entry keys, never typed resource IDs. Lexical node
    handles are framework allocations; original transition/config/PN semantics
    remain in the self-contained registered wire. Specs are shared by qualified
    operation, not republished for each executable transition.
    HOST execution bindings are separate Python authority, never Module JSON.
    A factory receives the prospective identities before the graph transaction
    and returns already registered canonical refs; its writes are HOST-owned.
    """
    if not isinstance(core, _RegistryCore) or core.read_only:
        raise TypeError("module publication requires the execution owner's Registry")
    if not isinstance(compiled, CompiledPetriNet) or not isinstance(registration, Registration):
        raise TypeError("module publication requires shared compiled inventory and HOST Registration")
    if not isinstance(identity, NativeRunIdentity):
        raise TypeError("module publication requires typed fresh run identity")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("module publication needs an explicit command key")
    wire = compiled.to_dict()
    if load_compiled_net(wire) != compiled:
        raise ValueError("compiled fields differ from mechanically verified shared wire")
    for category, declarations in compiled.registrations.items():
        for key, declaration in declarations.items():
            if registration.declaration(category, key) != declaration:
                raise ValueError("compiled declaration differs from exact HOST identity/contracts")
            if category != "schema":
                registration.resolve(category, key)  # Membership only; never execute.
    for ref, expected in ((identity.run_ref, "native_run_identity/v1"),
                          (identity.task_ref, "task/v1"),
                          (identity.task_branch_ref, "task_branch/v1"),
                          (identity.genesis_manifest_ref, "native_genesis_manifest/v1"),
                          (bootstrap_ref, "bootstrap_command/v1"),
                          (principal_ref, "principal/v1"),
                          (task_round_ref, "task_round/v1"),
                          (authority_decision_ref, "user_authority_decision/v1")):
        _registered(core, ref, expected)
    _, run = _registered(core, identity.run_ref)
    _, branch = _registered(core, identity.task_branch_ref)
    _, genesis = _registered(core, identity.genesis_manifest_ref)
    _, round_data = _registered(core, task_round_ref)
    _, decision = _registered(core, authority_decision_ref)
    if (identity.task_ref.entity_id != core.task_id or identity.branch_id != core.branch_id
            or run["task_ref"] != ref_payload(identity.task_ref)
            or run["task_branch_ref"] != ref_payload(identity.task_branch_ref)
            or run["branch_id"] != identity.branch_id
            or run["protocol_versions"] != list(identity.protocol_versions)
            or branch["task_ref"] != ref_payload(identity.task_ref)
            or genesis["run_identity_ref"] != ref_payload(identity.run_ref)
            or round_data["task_id"] != str(core.task_id)
            or decision["status"] != "effective"
            or decision["user_principal_ref"] != ref_payload(principal_ref)
            or ref_payload(identity.task_ref) not in decision["governed_artifact_refs"]):
        raise ValueError("run/round/effective owner authority is outside this task")
    ports = {port.name: port for port in compiled.ports}
    schemas = {}
    for key in compiled.source.required_schemas:
        ref = schema_refs[key]
        if not isinstance(ref, ResourceVersionRef):
            raise TypeError("schema requires an actual ResourceVersionRef")
        authority = core.verify_registered_content_schema_ref(ref, schema_document_ref=ref.as_version_ref())
        prepared = core.get_version(ref.resource_version_id)
        if (authority.schema_id != key or json.loads(core.object_store.read_registered(prepared))
                != registration.declaration("schema", key)["schema"]):
            raise ValueError("schema source differs from exact registered HOST schema")
        schemas[key] = ref
    resource_symbols = {lease.name for lease in compiled.symbolic.lease_identities if lease.kind == "resource"}
    if not isinstance(owner_resource_inputs, Mapping) or set(owner_resource_inputs) != resource_symbols:
        raise ValueError("owner resources must cover exact declared qualified resource lease symbols")
    for ref in owner_resource_inputs.values():
        if not isinstance(ref, ResourceVersionRef):
            raise TypeError("owner resource leases require actual ResourceVersionRef values")
        _, data = _registered(core, ref.as_version_ref(), "resource_version/v1")
        schema_id = data["content_schema_ref"]
        if (data["task_ref"] != ref_payload(identity.task_ref) or schema_id not in schemas):
            raise ValueError("owner resource differs from exact task/declared schema authority")
        _verify_input_schema_authority(core, data, schemas[schema_id])
        prepared = core.get_version(ref.resource_version_id)
        core.catalog.validate_instance("resource_version/v1", category="object", instance=data)
        payload = core.object_store.read_registered(prepared)
        instance = _content_schema_instance(payload, media_type=prepared.media_type)
        if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
            Draft7Validator(registration.declaration("schema", schema_id)["schema"]).validate(instance)
    for slot in compiled.symbolic.logical_slots:
        if slot.schema not in schemas:
            raise ValueError("logical slot schema is outside the exact registered public ABI")
    if not isinstance(preserved_slot_refs, Mapping):
        raise TypeError("preserved slots require a HOST symbolic-to-VersionRef mapping")
    if preserved_slot_refs:
        from .publication import _resource_from_payload, _version_from_payload
        current = validate_registered_net_closure(core.event_store, core.catalog,
            verified_adoption_head(core.event_store, core.catalog, core.task_id))
        current_declaration = _resource_from_payload(current["team_net_declaration_resource_ref"])
        old = load_compiled_net(json.loads(core.object_store.read_registered(
            core.get_version(current_declaration.resource_version_id))))
        selected = select_preserved_slot_refs(core, old, compiled)
        _, current_root = _registered(core, _version_from_payload(current["team_design_root_ref"]))
        if (current_root["run_ref"] != ref_payload(identity.run_ref)
                or any(name not in selected or ref != selected[name]
                       for name, ref in preserved_slot_refs.items())):
            raise ValueError("preserved slot differs from exact compatible CurrentPN authority")
    if (not isinstance(owner_input_resources, tuple)
            or any(not isinstance(ref, ResourceVersionRef) for ref in owner_input_resources)):
        raise TypeError("owner marking inputs require an explicit tuple of registered Resource refs")
    for ref in owner_input_resources:
        _, data = _registered(core, ref.as_version_ref(), "resource_version/v1")
        schema_id = data["content_schema_ref"]
        if (schema_id not in schemas or data["task_ref"] != ref_payload(identity.task_ref)
                or data["origin_kind"] != "private_system"):
            raise ValueError("new owner marking input lacks exact owner/task/schema authority")
        _verify_input_schema_authority(core, data, schemas[schema_id])
    leases = {lease.name: lease for lease in compiled.symbolic.lease_identities}
    slots = {slot.name: slot for slot in compiled.symbolic.logical_slots}
    for arc in compiled.symbolic.variable_resource_arcs:
        for claim in arc.initial_claims:
            lease = leases[claim.lease_identity]
            if lease.kind == "slot" and claim.expected_resource is not None:
                _, data = _registered(core, owner_resource_inputs[claim.expected_resource].as_version_ref())
                if data["content_schema_ref"] != slots[lease.slot].schema:
                    raise ValueError("logical slot expected resource differs from its exact declared schema")
    if not set(entry_inputs) <= set(compiled.symbolic.entry):
        raise ValueError("entry resources contain unknown symbolic Module entry keys")
    for key, bundle in entry_inputs.items():
        port = ports[compiled.symbolic.entry[key]]
        values = entry_resource_bundle(bundle)
        if not port.minimum <= len(values) <= port.maximum:
            raise ValueError("entry input bundle differs from exact declared quantity")
        for ref in values:
            _, data = _registered(core, ref.as_version_ref(), "resource_version/v1")
            if (data["task_ref"] != ref_payload(identity.task_ref)
                    or data["content_schema_ref"] != port.schema):
                raise ValueError("entry input differs from exact task/content schema authority")
            _verify_input_schema_authority(core, data, schemas[port.schema])
    operations = {item.declaration.name: item for item in compiled.operations}
    if set(operation_refs) != set(operations):
        raise ValueError("spec refs must match exact qualified operation inventory")
    specs = {}
    for name, item in operations.items():
        _, spec = _registered(core, operation_refs[name], "operation_spec/v1")
        operation = item.declaration
        host = registration.declaration("executor", item.executor_key)
        if (spec["operation_spec_ref"] != ref_payload(operation_refs[name])
                or spec["operation_id"] != item.operation_id or spec["executor_key"] != item.executor_key
                or spec["implementation_identity"] != host["identity"]
                or spec["implementation_contracts"] != host["contracts"]
                or spec["allowed_tool_ids"] != sorted(operation.tools)
                or spec["llm_prompt_port_id"] != (ports[operation.request_port].port_id
                                                  if operation.request_port is not None else None)):
            raise ValueError("operation spec differs from exact shared HOST declaration")
        for direction, names in (("input", operation.inputs), ("output", operation.outputs)):
            expected = []
            for port_name in names:
                port = ports[port_name]
                if direction == "input":
                    minimum, maximum = port.minimum, port.maximum
                else:
                    products = [{p.port: p for p in outcome.products}.get(port_name)
                                for outcome in operation.outcomes]
                    minimum = min(p.minimum if p else 0 for p in products)
                    maximum = max(p.maximum if p else 0 for p in products)
                expected.append({"port_id": port.port_id, "place": port.place,
                    "schema_ref": ref_payload(schemas[port.schema].as_version_ref()),
                    "content_schema_ref": content_schema_ref_payload(schemas[port.schema]),
                    "cardinality": {"minimum": minimum, "maximum": maximum}, "lease_identity_ref": None})
            if spec[f"{direction}_ports"] != expected:
                raise ValueError("operation port ABI differs from shared exact schema/place/quantity")
        specs[name] = spec

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
    host_bindings = (host_execution_bindings(host_plan)
                     if callable(host_execution_bindings) else host_execution_bindings)
    host_resources, host_artifacts = validate_host_execution_bindings(
        core, host_plan, host_bindings)
    host_bindings = dict(host_bindings)
    targets = {binding.llm_input_target_ref for binding in host_bindings.values()
               if binding.llm_input_target_ref is not None}
    shared_target = next(iter(targets)) if len(targets) == 1 else None
    # Fresh private-system resources have task/bootstrap lineage, not a firing
    # or active-net envelope. Graph payloads still carry their exact round/net.
    tx = core.begin(idempotency_key=idempotency_key)
    declaration = _publish_private_system(core, identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(bootstrap_ref), payload=canonical_json(wire), media_type="application/json",
        content_schema_ref=compiled.schema_version, summary=f"Compiled Module {compiled.source.name}",
        lifetime_ref=bootstrap_ref, derived_from=tuple(schemas.values()),
        idempotency_key=idempotency_key), transaction=tx)
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
    for ref, metadata in objects:
        core.catalog.validate_instance(ref.entity_type, category="object", instance=metadata)
    for ref, metadata in objects:
        tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
                    payload=canonical_json(metadata), metadata=metadata, media_type="application/json",
                    schema_ref=f"registry_v1/{ref.entity_type}")
    tx.commit()
    registered_net = validate_registered_net_closure(core.event_store, core.catalog, net)
    if registered_net["module_resource_bindings"] != resource_bindings:
        raise ValueError("registered resource bindings differ from exact Module publication")
    for slot in resource_plan.proposed_slots:
        _, registered_slot = _registered(core, slot.ref, "logical_artifact_slot/v1")
        if registered_slot != slot.metadata_dict():
            raise ValueError("registered logical slot differs from exact Module publication")
    return ModuleNetPublication(plan, root, net, declaration, MappingProxyType(nodes),
        MappingProxyType(dict(operation_refs)), MappingProxyType(bindings), MappingProxyType(outputs),
        MappingProxyType(executables), MappingProxyType(dict(compiled.place_aliases)), tx.transaction_id,
        resource_plan)


__all__ = ("ModuleNetPublication", "publish_module_net", "select_preserved_slot_refs")
