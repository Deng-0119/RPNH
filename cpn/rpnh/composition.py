"""One explicit symbolic qualification/fusion implementation; no HOST execution."""
from dataclasses import asdict, replace
import json

from .module import SymbolicNet
from .petri_contracts import DeclarationError


def _inventory(value):
    """Declared arrays are inert; literal initial payloads retain their order."""
    if isinstance(value, dict):
        return {k: v if k == "value" else _inventory(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return sorted((_inventory(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def compose_fragments(source, fragments):
    """Return typed composed net and every original place's fusion representative."""
    places, port_places, external = {}, {}, {}
    for name in sorted(fragments):
        fragment = fragments[name]
        places.update({f"{name}.{p.name}": replace(p, name=f"{name}.{p.name}") for p in fragment.places})
        port_places.update({f"{name}.{p.name}": f"{name}.{p.place}" for p in fragment.ports + fragment.internal_bindings})
    for component in source.components:
        external.update({f"{component.name}.{p.name}": p for p in component.ports})
    parent = {k: k for k in places}

    def root(key):
        while parent[key] != key:
            key = parent[key]
        return key

    def endpoint(ep, direction):
        key = f"{ep.component}.{ep.port}"
        if key not in external or external[key].direction != direction:
            raise DeclarationError(f"Unknown or wrong-direction endpoint: {key}")
        return key

    def compatible(a, b):
        left, right = asdict(a), asdict(b)
        left.pop("name")
        right.pop("name")
        return _inventory(left) == _inventory(right)

    links, incoming = set(), set()
    for link in source.links:
        a, b = endpoint(link.source, "output"), endpoint(link.target, "input")
        if (a, b) in links:
            raise DeclarationError("Duplicate link")
        links.add((a, b))
        incoming.add(b)
        x, y = root(port_places[a]), root(port_places[b])
        if external[a].signature != external[b].signature or not compatible(places[x], places[y]):
            raise DeclarationError("Fusion token/schema/colour/initial/reusable/capacity mismatch")
        parent[max(x, y)] = min(x, y)
    entry = {k: endpoint(v, "input") for k, v in source.entry.items()}
    exit = {k: endpoint(v, "output") for k, v in source.exit.items()}
    if incoming.intersection(entry.values()) or incoming.union(entry.values()) != {
        k for k, p in external.items() if p.direction == "input"
    }:
        raise DeclarationError("Every public input needs an entry or linked producer")
    transitions, arcs, reset_arcs, operations, leases, pools, variable, slots = [], [], [], [], [], [], [], []
    for name in sorted(fragments):
        fragment = fragments[name]
        def q(local):
            return f"{name}.{local}"
        def p(local):
            return root(q(local))
        def optional(local, transform):
            return transform(local) if local is not None else None

        transitions.extend(replace(t, name=q(t.name), operation=q(t.operation),
            count_guards=tuple(replace(g, place=p(g.place)) for g in t.count_guards),
            input_verdicts=tuple(replace(g, place=p(g.place)) for g in t.input_verdicts))
            for t in fragment.transitions)
        for arc in fragment.arcs:
            expression = arc.colour_expression
            if expression is not None:
                expression = replace(expression, source_place=optional(expression.source_place, p),
                    member=optional(expression.member, q),
                    candidates=tuple(map(q, expression.candidates)), selected=tuple(map(q, expression.selected)))
            arcs.append(replace(arc, place=p(arc.place), transition=q(arc.transition),
                forward_source=optional(arc.forward_source, p), colour_expression=expression,
                output_predicates=tuple(replace(g, place=p(g.place)) for g in arc.output_predicates),
                lease_claims=tuple(replace(
                    claim,
                    lease_identity=q(claim.lease_identity),
                    expected_resource_source=optional(
                        claim.expected_resource_source, p),
                ) for claim in arc.lease_claims),
                lease_claim_exclusions=tuple(
                    map(q, arc.lease_claim_exclusions)),
                lease_claim_set=(
                    None if arc.lease_claim_set is None else replace(
                        arc.lease_claim_set,
                        registered_pool_subset=optional(
                            arc.lease_claim_set.registered_pool_subset, q)))))
        reset_arcs.extend(replace(
            arc, place=p(arc.place), transition=q(arc.transition))
            for arc in fragment.reset_arcs)
        for operation in fragment.operations:
            outcomes = tuple(replace(outcome,
                products=tuple(replace(product, port=q(product.port)) for product in outcome.products),
                effects=tuple(replace(effect,
                    bindings={k: q(v) for k, v in effect.bindings.items()},
                    references={k: replace(v, name=p(v.name) if v.kind == "place" else q(v.name))
                                for k, v in effect.references.items()})
                    for effect in outcome.effects)) for outcome in operation.outcomes)
            operations.append(replace(operation, name=q(operation.name),
                inputs=tuple(map(q, operation.inputs)), outputs=tuple(map(q, operation.outputs)),
                outcomes=outcomes, request_port=optional(operation.request_port, q)))
        leases.extend(replace(l, name=q(l.name), slot=optional(l.slot, q)) for l in fragment.lease_identities)
        pools.extend(replace(pool, name=q(pool.name), place=p(pool.place),
            initial_resources=tuple(map(q, pool.initial_resources)), initial_slots=tuple(map(q, pool.initial_slots)))
            for pool in fragment.lease_pools)
        variable.extend(replace(a, transition=q(a.transition), claim_token_place=p(a.claim_token_place),
            lease_pool=q(a.lease_pool), initial_claims=tuple(replace(c,
                lease_identity=q(c.lease_identity), expected_resource=optional(c.expected_resource, q))
                for c in a.initial_claims)) for a in fragment.variable_resource_arcs)
        slots.extend(replace(slot, name=q(slot.name), producer_transition=q(slot.producer_transition),
            output_port=q(slot.output_port), candidate_place=p(slot.candidate_place),
            published_place=p(slot.published_place), reviewer_transition=optional(slot.reviewer_transition, q),
            route_transitions=tuple(map(q, slot.route_transitions)),
            consumer_transitions=tuple(map(q, slot.consumer_transitions))) for slot in fragment.logical_slots)
    for terminal in (source.terminal, *source.terminal_alternatives):
        port = endpoint(terminal.source, "output")
        operation = f"{terminal.source.component}.{terminal.operation}"
        producer = next((op for op in operations if op.name == operation), None)
        selected = next((out for out in producer.outcomes if out.name == terminal.outcome), None) if producer else None
        destinations = {arc.forward_source: set() for arc in arcs if arc.emit == "forward"}
        for arc in arcs:
            if arc.emit == "forward":
                destinations[arc.forward_source].add(arc.place)
        origins = {root(port_places[product.port]) for product in selected.products if product.minimum > 0} if selected else set()
        reachable = set(origins)
        frontier = set(origins)
        while frontier:
            following = {target for place in frontier for target in destinations.get(place, ())} - reachable
            reachable.update(following)
            frontier = following
        if port not in exit.values() or root(port_places[port]) not in reachable:
            raise DeclarationError("Terminal carrier must bind a declared forward path from a required producer product")
    identities = [(a.place, a.transition, a.direction, a.outcome) for a in arcs]
    if len(set(identities)) != len(identities):
        raise DeclarationError("Fusion creates duplicate arcs without distinct port attribution")
    capacities = {k: places[k].capacity for k in places if root(k) == k}
    for transition in transitions:
        operation = next(o for o in operations if o.name == transition.operation)
        selected = [a for a in arcs if a.transition == transition.name]
        for arc in selected:
            if arc.mode != "guard" and capacities[arc.place] is not None and arc.weight > capacities[arc.place]:
                raise DeclarationError("Arc quantity exceeds place capacity")
        for outcome in operation.outcomes:
            counts = {}
            for arc in selected:
                if arc.direction == "output" and arc.outcome in (None, outcome.name):
                    counts[arc.place] = counts.get(arc.place, 0) + arc.weight
            if any(capacities[k] is not None and count > capacities[k] for k, count in counts.items()):
                raise DeclarationError("Outcome quantity exceeds place capacity")
    return SymbolicNet(
        name=source.name, places=tuple(places[k] for k in sorted(places) if root(k) == k),
        transitions=tuple(transitions), arcs=tuple(arcs), operations=tuple(operations),
        port_places={k: root(v) for k, v in port_places.items()}, entry=entry, exit=exit,
        terminal=source.terminal, required_schemas=source.required_schemas, budgets=source.budgets,
        designer_constraints=source.designer_constraints or {}, analyzers=source.analyzers,
        lease_identities=tuple(leases), lease_pools=tuple(pools),
        variable_resource_arcs=tuple(variable), logical_slots=tuple(slots),
        terminal_alternatives=source.terminal_alternatives, budget_buckets=source.budget_buckets,
        reset_arcs=tuple(reset_arcs),
    ), {k: root(k) for k in places}
