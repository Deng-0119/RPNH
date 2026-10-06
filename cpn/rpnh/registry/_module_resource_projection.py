"""Fixed Module resource hydration semantics with data-only projection.

The legacy wrapper retains its exact read order, repeated reads and default wire
loader. The pure entry only accepts concrete detached data bags, never a reader
or callback. Bags/compiled inventory are data, not Registry authority: a fixed
consumer must establish their canonical bytes and provenance independently.
"""
from __future__ import annotations

from dataclasses import fields
import json
import math
import re

from .. import module as module_records
from .. import petri_contracts as petri_records
from .. import executable_net as compiled_records
from ..executable_net import CompiledPetriNet, load_compiled_net
from .identities import TypedId
from .models import VersionRef
from .resources import ResourceVersionRef
from .publication import _resource_from_payload, _version_from_payload
from .runtime_binding_contracts import freeze_candidate_document
from .strict_contracts import _registered, ref_payload


class _LegacyResourceReads:
    __slots__ = ("core",)
    def __init__(self, core):
        self.core = core

    def metadata(self, ref, expected_type=None):
        return _registered(self.core, ref, expected_type)[1]

    def owner_schema(self, authority, expected_schema_id):
        verified = self.core.verify_registered_content_schema_ref(authority,
            schema_document_ref=authority.as_version_ref())
        prepared = self.core.get_version(authority.resource_version_id)
        # Preserve the original short-circuit: verify and get_version happen,
        # but an ID mismatch must not trigger the second payload read.
        if verified.schema_id != expected_schema_id:
            return verified.schema_id, None
        return verified.schema_id, json.loads(self.core.object_store.read_registered(prepared))

    def creation_compiled(self, ref):
        return load_compiled_net(json.loads(self.core.object_store.read_registered(self.core.get_version(ref.version_id))))


# Closed internal record inventory; no application classes or discovery hooks.
_COMPILER_RECORDS = tuple(
    [getattr(module_records, name) for name in (
        "ComponentDeclaration", "Endpoint", "LinkDeclaration", "TerminalBinding", "BudgetBucketDeclaration",
        "SymbolicNet", "ModuleDeclaration")]
    + [getattr(petri_records, name) for name in (
        "PortDeclaration", "InitialTokenDeclaration", "PlaceDeclaration", "PortBinding", "ProductDeclaration",
        "SymbolReference", "EffectDeclaration", "OutcomeDeclaration", "BudgetBindingDeclaration", "OperationDeclaration",
        "CountGuard", "InputVerdictGuard", "InputColourPredicate", "ColourExpression", "TransitionDeclaration",
        "LeaseClaimExpression", "LeaseClaimSetExpression", "ArcDeclaration", "ResetArcDeclaration", "LeaseIdentityDeclaration",
        "LeaseClaimTemplate", "ResourceLeasePoolBinding", "VariableResourceArc", "LogicalSlotBinding", "PNFragment")]
    + [compiled_records.CompiledPetriNet, compiled_records.CompiledPort, compiled_records.CompiledOperation])


def _copy_compiler_data(value, active=None):
    """Detach only builtin containers and the finite compiler data records."""
    kind = type(value)
    if value is None or kind is str or kind is bool or kind is int:
        return value
    if kind is float:
        if not math.isfinite(value):
            raise TypeError("projection data requires finite numbers")
        return value
    if (kind is not dict and kind is not list and kind is not tuple
            and not any(kind is record_type for record_type in _COMPILER_RECORDS)):
        raise TypeError("projection requires standard compiler data, not application objects")
    active = set() if active is None else active
    if id(value) in active:
        raise TypeError("projection data cannot contain cycles")
    active.add(id(value))
    try:
        if kind is dict:
            if any(type(key) is not str for key in value):
                raise TypeError("compiler data keys must be builtin strings")
            return {key: _copy_compiler_data(item, active) for key, item in value.items()}
        if kind is list or kind is tuple:
            copied = [_copy_compiler_data(item, active) for item in value]
            return copied if kind is list else tuple(copied)
        return kind(**{field.name: _copy_compiler_data(getattr(value, field.name), active) for field in fields(kind)})
    finally:
        active.remove(id(value))


def _copy_ref(ref, expected_type=None):
    if (type(ref) is not VersionRef or type(ref.entity_type) is not str
            or re.fullmatch(r"[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*/v[1-9][0-9]*", ref.entity_type) is None
            or (expected_type is not None and ref.entity_type != expected_type)):
        raise TypeError("projection requires an exact standard VersionRef")
    ids = []
    for value in (ref.entity_id, ref.version_id):
        if type(value) is not TypedId or type(value.kind) is not str or type(value.value) is not str:
            raise TypeError("projection requires standard TypedId fields")
        ids.append(TypedId(value.kind, value.value))
    return VersionRef(ref.entity_type, *ids)


def _copy_document(value):
    return json.loads(freeze_candidate_document(value))


class _DataResourceReads:
    __slots__ = ("_metadata", "_schemas", "_compiled")
    def __init__(self, metadata, schemas, compiled):
        if any(type(bag) is not dict for bag in (metadata, schemas, compiled)):
            raise TypeError("projection read bags must be builtin dictionaries")
        self._metadata = {_copy_ref(ref): _copy_document(value) for ref, value in metadata.items()}
        self._schemas = {}
        for ref, value in schemas.items():
            reference = _copy_ref(ref, "resource_version/v1")
            if type(value) is not tuple or len(value) != 2 or type(value[0]) is not str:
                raise TypeError("owner schema data requires (schema_id, JSON document)")
            self._schemas[reference] = value[0], _copy_document({"schema": value[1]})["schema"]
        self._compiled = {}
        for ref, value in compiled.items():
            if type(value) is not CompiledPetriNet:
                raise TypeError("creation inventories must be standard CompiledPetriNet data")
            self._compiled[_copy_ref(ref, "resource_version/v1")] = _copy_compiler_data(value)

    def metadata(self, ref, expected_type=None):
        reference = _copy_ref(ref, expected_type)
        if reference not in self._metadata:
            raise ValueError("projection lacks exact metadata")
        return self._metadata[reference]

    def owner_schema(self, authority, expected_schema_id):
        reference = _copy_ref(authority.as_version_ref(), "resource_version/v1")
        if reference not in self._schemas:
            raise ValueError("projection lacks exact owner schema data")
        return self._schemas[reference]

    def creation_compiled(self, ref):
        reference = _copy_ref(ref, "resource_version/v1")
        if reference not in self._compiled:
            raise ValueError("projection lacks exact creation inventory")
        return self._compiled[reference]


def project_module_resource_plan(compiled, net_ref, net, root_ref, root, declaration_ref, *,
        exact_metadata, owner_schema_documents, creation_compiled):
    """Pure projection from detached standard data; it grants no authority.

    The compiled records must already come from the compiler/fixed wire reader.
    This copies their finite data tree but does not recompile or validate its
    provenance. Legacy numerical/compiler semantics are preserved in the kernel.
    """
    if type(compiled) is not CompiledPetriNet or type(declaration_ref) is not ResourceVersionRef:
        raise TypeError("projection requires standard compiled and declaration data")
    declaration = _copy_ref(VersionRef("resource_version/v1", declaration_ref.resource_id, declaration_ref.resource_version_id))
    if declaration.entity_id.kind != "resource" or declaration.version_id.kind != "resource_version":
        raise TypeError("projection declaration requires resource typed IDs")
    reads = _DataResourceReads(exact_metadata, owner_schema_documents, creation_compiled)
    return _resource_projection(reads, _copy_compiler_data(compiled),
        _copy_ref(net_ref, "net_instance/v1"), _copy_document(net),
        _copy_ref(root_ref, "team_design_root/v1"), _copy_document(root),
        ResourceVersionRef(declaration.entity_id, declaration.version_id))


def _resource_projection(reads, compiled, net_ref, net, root_ref, root, declaration_ref):
    if type(reads) is not _LegacyResourceReads and type(reads) is not _DataResourceReads:
        raise TypeError("resource projection uses only its fixed internal read modes")
    from .module_resources import prepare_module_resources, module_slot_bindings, compatible_slot_symbols
    binding = net["module_resource_bindings"]
    inputs = {name: _resource_from_payload(value)
              for name, value in binding["owner_resource_inputs"].items()}
    resource_symbols = {lease.name for lease in compiled.symbolic.lease_identities
                        if lease.kind == "resource"}
    if set(inputs) != resource_symbols:
        raise ValueError("registered owner resource symbols differ from compiled leases")
    for ref in inputs.values():
        metadata = reads.metadata(ref.as_version_ref(), "resource_version/v1")
        schema_id = metadata["content_schema_ref"]
        if (metadata["task_ref"] != root["task_ref"]
                or ref_payload(ref.as_version_ref()) not in root["resource_refs"]
                or schema_id not in compiled.source.required_schemas):
            raise ValueError("resource binding lacks exact task/schema/root membership")
        source = metadata["content_schema_authority_ref"]
        authority = _resource_from_payload(source)
        verified_id, schema_document = reads.owner_schema(authority, schema_id)
        if (verified_id != schema_id
                or schema_document != compiled.registrations["schema"][schema_id]["schema"]):
            raise ValueError("bound resource schema differs from exact registered declaration")
    nodes = {}
    outputs = {}
    for value in net["node_refs"]:
        ref = _version_from_payload(value)
        metadata = reads.metadata(ref, "node_declaration/v1")
        name = metadata["transition_id"]
        if name in nodes or metadata["team_design_root_ref"] != ref_payload(root_ref):
            raise ValueError("resource projection has duplicate or foreign producer nodes")
        nodes[name] = ref
    by_node = {ref: name for name, ref in nodes.items()}
    for value in net["output_binding_refs"]:
        ref = _version_from_payload(value)
        metadata = reads.metadata(ref, "output_binding/v1")
        node_ref = _version_from_payload(metadata["node_ref"])
        key = (by_node[node_ref], metadata["output_port_id"])
        if key in outputs or metadata["net_ref"] != ref_payload(net_ref):
            raise ValueError("resource projection has duplicate or foreign output bindings")
        outputs[key] = ref
    preserved = {}
    for name, value in binding["slot_refs"].items():
        ref = _version_from_payload(value)
        metadata = reads.metadata(ref, "logical_artifact_slot/v1")
        if metadata["team_design_root_ref"] != ref_payload(root_ref):
            source_ref = _version_from_payload(metadata["authored_index_ref"])
            source = reads.creation_compiled(source_ref)
            if name not in compatible_slot_symbols(source, compiled):
                raise ValueError("preserved slot differs from immutable creation schema contract")
            source_slot = next(slot for slot in source.symbolic.logical_slots if slot.name == name)
            producer = reads.metadata(_version_from_payload(metadata["producer_node_ref"]), "node_declaration/v1")
            source_port = next(port for port in source.ports if port.name == source_slot.output_port)
            if (producer["transition_id"] != source_slot.producer_transition
                    or source_port.port_id != metadata["output_port_id"]):
                raise ValueError("preserved slot creation producer differs from registered declaration")
            preserved[name] = ref
    plan = prepare_module_resources(compiled, root_ref=root_ref, net_ref=net_ref,
        declaration_ref=declaration_ref, node_refs=nodes, output_binding_refs=outputs,
        owner_resource_inputs=inputs, idempotency_key=binding["command_id"],
        preserved_slot_refs=preserved)
    if (binding["lease_refs"] != {name: ref_payload(ref) for name, ref in plan.lease_refs.items()}
            or binding["slot_refs"] != {name: ref_payload(ref) for name, ref in plan.slot_refs.items()}
            or binding.get("slot_bindings", {}) != module_slot_bindings(plan)):
        raise ValueError("persisted resource identities differ from compiled exact bindings")
    from .module_binding_authority import validate_module_bindings
    validate_module_bindings(net, root,
        {ref: reads.metadata(ref, "output_binding/v1") for ref in outputs.values()},
        {ref: reads.metadata(ref, "node_declaration/v1") for ref in nodes.values()},
        lambda value, expected: reads.metadata(_version_from_payload(value), expected), ValueError)
    for slot in plan.proposed_slots:
        metadata = reads.metadata(slot.ref, "logical_artifact_slot/v1")
        if (metadata != slot.metadata_dict()
                or ref_payload(slot.ref) not in root["artifact_refs"]):
            raise ValueError("logical slot lacks exact declared producer/schema/root authority")
    slots = {slot.name: slot for slot in compiled.symbolic.logical_slots}
    leases = {lease.name: lease for lease in compiled.symbolic.lease_identities}
    for arc in compiled.symbolic.variable_resource_arcs:
        for claim in arc.initial_claims:
            lease = leases[claim.lease_identity]
            if lease.kind == "slot" and claim.expected_resource is not None:
                metadata = reads.metadata(inputs[claim.expected_resource].as_version_ref())
                if metadata["content_schema_ref"] != slots[lease.slot].schema:
                    raise ValueError("slot expected resource schema differs from its declared product")
    return plan
