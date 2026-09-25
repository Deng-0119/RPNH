"""Finite registered structural-effect additions, never candidate M0 replay."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict

from .errors import ResourceIntegrityFault
from .owner_adoption import _terminal_source_places
from .owner_mapping import transfer_consumer
from .publication import _ref_payload, _version_from_payload
from .resources import PetriTokenState, ResourceVersionRef


@dataclass(frozen=True, slots=True)
class DeclaredModuleActivation:
    """Only symbolic selectors; exact identities are resolved by Core.

    RESOURCE_ENTRY selects either a whole Core-bound bundle or one candidate
    resource lease. IDLE_CAPACITY selects its exact execution transition.
    LEASE_POOL selects one explicitly declared initial pool lease.
    """
    activation_class: str
    place: str
    count: int = 1
    entry_key: str | None = None
    source_binding: str | None = None
    transition: str | None = None
    pool: str | None = None
    lease: str | None = None

    def to_dict(self):
        value = {k: v for k, v in asdict(self).items() if v is not None}
        _instruction(value)
        return value


_SELECTORS = {
    "RESOURCE_ENTRY": {"entry_key", "source_binding", "lease"},
    "IDLE_CAPACITY": {"transition"},
    "LEASE_POOL": {"pool", "lease"},
    "CONTENTLESS_CONTROL": set(),
}


def _instruction(value):
    kind = value.get("activation_class")
    if (kind not in _SELECTORS or set(value) - {"activation_class", "place", "count"} - _SELECTORS[kind]
            or type(value.get("count")) is not int or value["count"] <= 0
            or any(not isinstance(v, str) or not v or ":" in v
                   for k, v in value.items() if k != "count")):
        raise ResourceIntegrityFault("activation requires finite typed symbolic selectors")
    selectors = set(value) - {"activation_class", "place", "count"}
    if (kind == "RESOURCE_ENTRY" and ("entry_key" not in selectors
            or len(selectors & {"source_binding", "lease"}) != 1)
            or kind == "IDLE_CAPACITY" and selectors != {"transition"}
            or kind == "LEASE_POOL" and (selectors != {"pool", "lease"} or value["count"] != 1)):
        raise ResourceIntegrityFault("activation class has incomplete source selectors")


def validate_activation_contract(contracts, instructions):
    """Called before candidate compilation and again at transaction commit."""
    allowed = contracts.get("allowed_activation_classes", {})
    if (contracts.get("effect_kind") != "structural_revision" or not isinstance(allowed, dict)
            or set(allowed) - _SELECTORS.keys()
            or any(n != "candidate_declaration" and (type(n) is not int or n <= 0)
                   for n in allowed.values())):
        raise ResourceIntegrityFault("invalid registered structural activation contract")
    totals = Counter()
    for value in instructions:
        _instruction(value)
        totals[value["activation_class"]] += value["count"]
    if any(kind not in allowed or allowed[kind] != "candidate_declaration" and n > allowed[kind]
           for kind, n in totals.items()):
        raise ResourceIntegrityFault("activation inventory exceeds exact registered class contract")


def resolve_module_activations(old, candidate, structure, old_plan, plan,
        instructions, contracts, mapped, *, epoch, next_token_id, declaration_ref,
        bindings, bound_resources, check_resource, check_identity):
    """Shared pure finite resolution over independently verified Registry DTOs.

    Returns unpersisted states and their required strong authority endpoints.
    It inspects only explicitly selected places/pools, not the candidate M0.
    """
    validate_activation_contract(contracts, instructions)
    places = {p.name: p for p in candidate.symbolic.places}
    old_places = {p.name for p in old.symbolic.places}
    ports = {p.name: p for p in candidate.ports}
    terminals = _terminal_source_places(candidate)
    pools = {p.name: p for p in candidate.symbolic.lease_pools}
    leases = {l.name: l for l in candidate.symbolic.lease_identities}
    identities = set(old_plan.lease_refs.values())
    for state, _ in mapped:
        identities.update(c.lease_identity_ref for c in state.lease_claims)
        if state.lease_identity_ref is not None:
            identities.add(state.lease_identity_ref)
    occupied = Counter(destination for _, destination in mapped)
    result = []
    selected_places = set()
    entry_counts = Counter()
    for value in instructions:
        kind, name, count = value["activation_class"], value["place"], value["count"]
        if name not in places or name in terminals:
            raise ResourceIntegrityFault("activation destination lacks nonterminal candidate authority")
        place = places[name]
        authorities = (declaration_ref,)
        resources = (None,) * count
        identity = None
        if kind == "RESOURCE_ENTRY":
            port_name = candidate.symbolic.entry.get(value["entry_key"])
            port = ports.get(port_name)
            if (port is None or port.place != name or not port.minimum <= count <= port.maximum
                    or place.reusable or place.token_kind != "data" or place.colours):
                raise ResourceIntegrityFault("resource activation differs from exact candidate entry type/quantity")
            if "source_binding" in value:
                resources = tuple(bound_resources.get(value["source_binding"], ()))
            else:
                ref = plan.lease_refs.get(value["lease"])
                if ref is None or ref.entity_type != "resource_version/v1":
                    raise ResourceIntegrityFault("resource activation lacks candidate resource-plan selector")
                resources = (ResourceVersionRef(ref.entity_id, ref.version_id),)
            if len(resources) != count:
                raise ResourceIntegrityFault("resource activation does not select the exact whole source bundle")
            entry_counts[value["entry_key"]] += count
            if entry_counts[value["entry_key"]] > port.maximum:
                raise ResourceIntegrityFault("resource activation inventory exceeds exact entry quantity")
            for resource in resources:
                check_resource(resource, port.schema)
        elif kind == "LEASE_POOL":
            pool = pools.get(value["pool"])
            lease = leases.get(value["lease"])
            identity = plan.lease_refs.get(value["lease"])
            if (pool is None or pool.place != name or lease is None or identity is None
                    or value["lease"] not in (*pool.initial_resources, *pool.initial_slots)
                    or identity in identities or place.token_kind != "resource_lease"):
                raise ResourceIntegrityFault("lease activation must name one NEW exact declared idle pool identity")
            check_identity(identity)
            identities.add(identity)
            authorities += (identity,)
            if lease.kind == "resource":
                resource = ResourceVersionRef(identity.entity_id, identity.version_id)
                check_resource(resource, place.admitted_schemas)
                resources = (resource,)
        else:
            if name in old_places or name in selected_places or occupied[name]:
                raise ResourceIntegrityFault("activation cannot duplicate existing capacity/control authority")
            if (place.colours or any(t.value is not None or t.colour is not None or t.schema is not None
                                    for t in place.initial_tokens)):
                raise ResourceIntegrityFault("idle/control activation cannot confer literal/schema/colour authority")
            initial_capacity = sum(t.count for t in place.initial_tokens)
            if kind == "IDLE_CAPACITY":
                transition = value["transition"]
                binding = bindings.get(transition)
                inputs = [a for a in candidate.symbolic.arcs if a.place == name
                    and a.transition == transition and a.direction == "input"]
                outputs = [a for a in candidate.symbolic.arcs if a.place == name
                    and a.transition == transition and a.direction == "output"]
                target = next((t for t in candidate.symbolic.transitions if t.name == transition), None)
                operation = next((o for o in candidate.symbolic.operations
                    if target is not None and o.name == target.operation), None)
                conserved = (len(inputs) == 1 and inputs[0].mode in {"consume", "borrow"}
                    and operation is not None and bool(outputs)
                    and all(a.mode == ("return" if inputs[0].mode == "borrow" else "produce")
                            and a.emit == "content_less" and not a.output_predicates
                            and a.colour_expression is None for a in outputs)
                    and all(sum(a.weight for a in outputs if a.outcome in {None, outcome.name})
                            == inputs[0].weight for outcome in operation.outcomes))
                if (not place.reusable or place.token_kind != "agent_resource" or binding is None
                        or count > initial_capacity or not conserved):
                    raise ResourceIntegrityFault("capacity activation lacks declared initial capacity/exact execution binding")
                authorities += (binding,)
            elif (place.reusable or place.token_kind != "data" or place.channel not in {"data", "control"}
                    or name in {a.claim_token_place for a in candidate.symbolic.variable_resource_arcs}
                    or count > initial_capacity):
                raise ResourceIntegrityFault("contentless activation lacks new ordinary declaration/count authority")
        selected_places.add(name)
        occupied[name] += count
        if place.capacity is not None and occupied[name] > place.capacity:
            raise ResourceIntegrityFault("activated successor exceeds exact candidate place capacity")
        for resource in resources:
            sources = authorities + (() if resource is None else (resource.as_version_ref(),))
            state = PetriTokenState(None, next_token_id + len(result), name, epoch, None,
                transfer_consumer(structure, name), resource, None, None, None, None, None, None, identity, ())
            result.append((state, tuple(dict.fromkeys(sources))))
    return tuple(result)


__all__ = ("DeclaredModuleActivation",)
