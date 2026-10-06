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
    HostExecutionBindingFactory, validate_host_execution_bindings)
from .module_resources import (ModuleResourcePlan, entry_resource_bundle, compatible_slot_symbols)
from .publication import (_content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE,
                          _content_schema_source_from_payload)
from .resource_service import _publish_private_system
from .resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from .schema_catalog import canonical_json
from .strict_contracts import _registered, content_schema_ref_payload, ref_payload


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

    from ._module_graph import allocate_module_graph, materialize_module_graph
    plan, root, net, nodes, bindings, executables, outputs, host_plan = allocate_module_graph(
        compiled, identity=identity, task_round_ref=task_round_ref, specs=specs,
        idempotency_key=idempotency_key)
    host_bindings = (host_execution_bindings(host_plan)
                     if callable(host_execution_bindings) else host_execution_bindings)
    host_resources, host_artifacts = validate_host_execution_bindings(
        core, host_plan, host_bindings)
    host_bindings = dict(host_bindings)
    # Fresh private-system resources have task/bootstrap lineage, not a firing
    # or active-net envelope. Graph payloads still carry their exact round/net.
    tx = core.begin(idempotency_key=idempotency_key)
    declaration = _publish_private_system(core, identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(bootstrap_ref), payload=canonical_json(wire), media_type="application/json",
        content_schema_ref=compiled.schema_version, summary=f"Compiled Module {compiled.source.name}",
        lifetime_ref=bootstrap_ref, derived_from=tuple(schemas.values()),
        idempotency_key=idempotency_key), transaction=tx)
    graph = materialize_module_graph(compiled, identity=identity,
        task_round_ref=task_round_ref, principal_ref=principal_ref,
        authority_decision_ref=authority_decision_ref, declaration=declaration,
        schema_refs=schemas, operation_refs=operation_refs, specs=specs,
        entry_inputs=entry_inputs, owner_resource_inputs=owner_resource_inputs,
        owner_input_resources=owner_input_resources, preserved_slot_refs=preserved_slot_refs,
        host_bindings=host_bindings, host_resources=host_resources,
        host_artifacts=host_artifacts, idempotency_key=idempotency_key)
    objects, resource_plan, resource_bindings = graph.objects, graph.resource_plan, graph.resource_bindings
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
