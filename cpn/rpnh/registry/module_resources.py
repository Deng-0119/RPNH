"""Pure resource projection for the same shared compiled Module publication.

References and proposals are data, not Registry authority. The execution owner
must verify task/resource/schema membership and stage slots in the Module net's
own transaction before using the resolved DTOs for marking or execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..executable_net import CompiledPetriNet
from .models import VersionRef
from .resources import ResourceVersionRef
from .strict_contracts import _stable_id, ref_payload


def entry_resource_bundle(value):
    """Explicit owner ABI quantity; a single resource denotes one input."""
    values = (value,) if isinstance(value, ResourceVersionRef) else tuple(value)
    if any(not isinstance(ref, ResourceVersionRef) for ref in values):
        raise TypeError("owner entry bundles require exact registered resource refs")
    return values


@dataclass(frozen=True, slots=True)
class ResolvedExactRef:
    """Exact structural shape consumed by the marking, without authority."""

    entity_type: str
    logical_id: str
    version_id: str

    @classmethod
    def from_version_ref(cls, ref: VersionRef) -> ResolvedExactRef:
        return cls(ref.entity_type, str(ref.entity_id), str(ref.version_id))


@dataclass(frozen=True, slots=True)
class ResolvedLeaseClaim:
    lease_identity_ref: ResolvedExactRef
    expected_resource_ref: ResolvedExactRef | None
    access_mode: str


@dataclass(frozen=True, slots=True)
class ResolvedLeasePool:
    name: str
    lease_pool_place: str
    initial_resource_refs: tuple[ResolvedExactRef, ...]
    initial_logical_slot_refs: tuple[ResolvedExactRef, ...]


@dataclass(frozen=True, slots=True)
class ResolvedVariableResourceArc:
    transition_id: str
    claim_token_place: str
    lease_pool_place: str
    initial_claims: tuple[ResolvedLeaseClaim, ...]
    input_inscription: str
    output_inscription: str


@dataclass(frozen=True, slots=True)
class ResolvedLogicalSlot:
    slot_ref: ResolvedExactRef
    producer_node_ref: ResolvedExactRef
    producer_transition_id: str
    producer_output_binding_ref: ResolvedExactRef
    output_port_id: str
    artifact_id: str
    artifact_synopsis: str
    content_kind: str
    content_schema_id: str
    candidate_place: str
    critic_transition_id: str | None
    published_place: str
    route_transition_ids: tuple[str, ...]
    consumer_transition_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposedLogicalSlot:
    ref: VersionRef
    metadata: Mapping[str, object]

    def metadata_dict(self) -> dict[str, object]:
        """Return a serialization copy for the owner's schema gate/prewrite."""
        return {key: dict(value) if isinstance(value, Mapping) else value
                for key, value in self.metadata.items()}


@dataclass(frozen=True, slots=True)
class ModuleResourcePlan:
    lease_refs: Mapping[str, VersionRef]
    slot_refs: Mapping[str, VersionRef]
    proposed_slots: tuple[ProposedLogicalSlot, ...]
    lease_pools: tuple[ResolvedLeasePool, ...]
    variable_resource_arcs: tuple[ResolvedVariableResourceArc, ...]
    logical_slots: tuple[ResolvedLogicalSlot, ...]


def _typed_ref(ref: VersionRef, entity_type: str) -> VersionRef:
    if not isinstance(ref, VersionRef) or ref.entity_type != entity_type:
        raise TypeError(f"resource plan requires an exact {entity_type} VersionRef")
    return ref


def _immutable_metadata(value: dict) -> Mapping[str, object]:
    return MappingProxyType({key: _immutable_metadata(item) if isinstance(item, dict) else item
                             for key, item in value.items()})


def compatible_slot_symbols(old_compiled, candidate_compiled):
    """Same lexical artifact/type and exact declared schema contract."""
    old = {slot.name: slot for slot in old_compiled.symbolic.logical_slots}
    return frozenset(slot.name for slot in candidate_compiled.symbolic.logical_slots
        if slot.name in old
        and (old[slot.name].artifact_id, old[slot.name].content_kind, old[slot.name].schema)
        == (slot.artifact_id, slot.content_kind, slot.schema)
        and old_compiled.registrations["schema"][slot.schema]["schema"]
        == candidate_compiled.registrations["schema"][slot.schema]["schema"])


def module_slot_bindings(plan):
    """Current graph transport, separate from immutable slot creation facts."""
    def payload(ref):
        return {"entity_type": ref.entity_type, "logical_id": ref.logical_id,
                "version_id": ref.version_id}
    names = {str(ref.version_id): name for name, ref in plan.slot_refs.items()}
    return {names[slot.slot_ref.version_id]: {
        "slot_ref": payload(slot.slot_ref),
        "producer_node_ref": payload(slot.producer_node_ref),
        "producer_output_binding_ref": payload(slot.producer_output_binding_ref),
        "producer_transition_id": slot.producer_transition_id,
        "output_port_id": slot.output_port_id, "artifact_id": slot.artifact_id,
        "artifact_synopsis": slot.artifact_synopsis, "content_kind": slot.content_kind,
        "content_schema_id": slot.content_schema_id}
        for slot in plan.logical_slots}


def prepare_module_resources(
    compiled: CompiledPetriNet, *, root_ref: VersionRef, net_ref: VersionRef,
    declaration_ref: ResourceVersionRef, node_refs: Mapping[str, VersionRef],
    output_binding_refs: Mapping[tuple[str, str], VersionRef],
    owner_resource_inputs: Mapping[str, ResourceVersionRef], idempotency_key: str,
    preserved_slot_refs: Mapping[str, VersionRef] = MappingProxyType({}),
) -> ModuleResourcePlan:
    """Resolve authored symbols using explicit resources and publication handles.

Output binding keys are (qualified producer transition, compiled port_id), as
in ModuleNetPublication.output_refs. No JSON locator or resolver callable is
accepted for owner resources. This function does not check stored facts, write
objects, or claim that its proposed slot refs have already been registered.
"""
    if not isinstance(compiled, CompiledPetriNet):
        raise TypeError("resource plan requires the shared CompiledPetriNet")
    _typed_ref(root_ref, "team_design_root/v1")
    _typed_ref(net_ref, "net_instance/v1")
    if not isinstance(declaration_ref, ResourceVersionRef):
        raise TypeError("declaration_ref requires an actual ResourceVersionRef")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("resource plan requires an explicit publication key")
    symbolic = compiled.symbolic
    transitions = {item.name: item for item in symbolic.transitions}
    ports = {item.name: item for item in compiled.ports}
    operations = {item.declaration.name: item.declaration for item in compiled.operations}
    if not isinstance(node_refs, Mapping) or set(node_refs) != set(transitions):
        raise ValueError("node refs must cover the exact compiled transition keys")
    for ref in node_refs.values():
        _typed_ref(ref, "node_declaration/v1")
    expected_outputs = {(name, ports[port].port_id) for name, transition in transitions.items()
                        for port in operations[transition.operation].outputs}
    if not isinstance(output_binding_refs, Mapping) or set(output_binding_refs) != expected_outputs:
        raise ValueError("output refs must cover exact producer/compiled-port handles")
    for ref in output_binding_refs.values():
        _typed_ref(ref, "output_binding/v1")
    resource_symbols = {item.name for item in symbolic.lease_identities if item.kind == "resource"}
    if not isinstance(owner_resource_inputs, Mapping) or set(owner_resource_inputs) != resource_symbols:
        raise ValueError("owner resources must cover the exact qualified resource lease symbols")
    if any(not isinstance(ref, ResourceVersionRef) for ref in owner_resource_inputs.values()):
        raise TypeError("owner resources require actual ResourceVersionRef values")

    if (not isinstance(preserved_slot_refs, Mapping)
            or not set(preserved_slot_refs) <= {slot.name for slot in symbolic.logical_slots}):
        raise ValueError("preserved slots require exact declared symbolic slot keys")
    for ref in preserved_slot_refs.values():
        _typed_ref(ref, "logical_artifact_slot/v1")
    if len(set(preserved_slot_refs.values())) != len(preserved_slot_refs):
        raise ValueError("preserved slot symbols cannot merge exact identities")
    slot_refs = {}
    slots = []
    proposed = []
    for slot in sorted(symbolic.logical_slots, key=lambda item: item.name):
        # Match the Registry's existing deterministic UUID identity algorithm;
        # exact net version and qualified slot key, never authored content.
        material = f"{idempotency_key}:net:{net_ref.version_id}:slot:{slot.name}"
        ref = preserved_slot_refs.get(slot.name)
        if ref is None:
            ref = VersionRef("logical_artifact_slot/v1", _stable_id("logical_slot", material),
                             _stable_id("logical_slot_version", material))
        slot_refs[slot.name] = ref
        port = ports[slot.output_port]
        operation = operations[transitions[slot.producer_transition].operation]
        if (slot.output_port not in operation.outputs or port.place != slot.candidate_place
                or port.schema != slot.schema):
            raise ValueError("logical slot differs from its exact compiled producer port")
        output_ref = output_binding_refs[slot.producer_transition, port.port_id]
        node_ref = node_refs[slot.producer_transition]
        resolved = ResolvedLogicalSlot(
            ResolvedExactRef.from_version_ref(ref), ResolvedExactRef.from_version_ref(node_ref),
            slot.producer_transition, ResolvedExactRef.from_version_ref(output_ref), port.port_id,
            slot.artifact_id, slot.artifact_synopsis, slot.content_kind, slot.schema,
            slot.candidate_place, slot.reviewer_transition, slot.published_place,
            slot.route_transitions, slot.consumer_transitions)
        slots.append(resolved)
        if slot.name in preserved_slot_refs:
            continue
        proposed.append(ProposedLogicalSlot(ref, _immutable_metadata({
            "logical_artifact_slot_ref": ref_payload(ref),
            "team_design_root_ref": ref_payload(root_ref),
            "authored_index_ref": ref_payload(declaration_ref.as_version_ref()),
            "lexical_slot_id": slot.name,
            "artifact_id": slot.artifact_id,
            "artifact_synopsis": slot.artifact_synopsis,
            "content_kind": slot.content_kind,
            "producer_node_ref": ref_payload(node_ref),
            "producer_output_binding_ref": ref_payload(output_ref),
            "output_port_id": port.port_id,
        })))
    lease_refs = {item.name: (owner_resource_inputs[item.name].as_version_ref()
                  if item.kind == "resource" else slot_refs[item.slot])
                  for item in symbolic.lease_identities}
    exact = {name: ResolvedExactRef.from_version_ref(ref) for name, ref in lease_refs.items()}
    pools = {pool.name: ResolvedLeasePool(pool.name, pool.place,
             tuple(exact[name] for name in pool.initial_resources),
             tuple(exact[name] for name in pool.initial_slots))
             for pool in sorted(symbolic.lease_pools, key=lambda item: item.name)}
    for pool in pools.values():
        identities = pool.initial_resource_refs + pool.initial_logical_slot_refs
        if len(set(identities)) != len(identities):
            raise ValueError("declared pool symbols resolve to repeated actual lease identities")
    arcs = tuple(ResolvedVariableResourceArc(arc.transition, arc.claim_token_place,
        pools[arc.lease_pool].lease_pool_place,
        tuple(ResolvedLeaseClaim(exact[claim.lease_identity],
              None if claim.expected_resource is None else exact[claim.expected_resource], claim.access_mode)
              for claim in arc.initial_claims), arc.input_inscription, arc.output_inscription)
        for arc in sorted(symbolic.variable_resource_arcs, key=lambda item: item.transition))
    for arc in arcs:
        identities = tuple(claim.lease_identity_ref for claim in arc.initial_claims)
        if len(set(identities)) != len(identities):
            raise ValueError("declared claims resolve to repeated actual lease identities")
    return ModuleResourcePlan(MappingProxyType(lease_refs), MappingProxyType(slot_refs),
        tuple(proposed), tuple(pools.values()), arcs, tuple(slots))


__all__ = ("ModuleResourcePlan", "ProposedLogicalSlot", "ResolvedExactRef", "ResolvedLeaseClaim",
           "ResolvedLeasePool", "ResolvedVariableResourceArc", "ResolvedLogicalSlot",
           "prepare_module_resources", "compatible_slot_symbols", "module_slot_bindings")
