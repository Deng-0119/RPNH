"""Pure Petri firing envelope evaluated before any operation action.

The envelope is derived only from the adopted compiled net, one exact typed
marking and one selected occurrence.  It publishes nothing, delivers no bytes
and invokes no component.  Registry allocation/admission may proceed only when
every output bundle declared for the transition has at least one
capacity-valid formal successor.

Actual products, route/reset effects and structural revisions remain subject to
the exact Success projection.  This preflight is the earlier capacity-aware
enabling barrier; it is not a substitute for the final typed successor.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .marking import MarkingStateError, TeamNetMarking
from .registry.models import VersionRef
from .registry.resources import TypedMarkingAuthority
from .runtime_net import RuntimeNet


@dataclass(frozen=True, slots=True)
class PetriPlaceCapacityProjection:
    """One place's declared successor interval for a selected outcome."""

    place: str
    capacity: int | None
    predecessor_count: int
    consumed_count: int
    potential_reset_count: int
    returned_count: int
    produced_upper_bound: int
    successor_minimum: int
    successor_upper_bound: int


@dataclass(frozen=True, slots=True)
class PetriOutcomePreflight:
    """Capacity-aware formal envelope for one declared operation outcome."""

    outcome_id: str
    places: tuple[PetriPlaceCapacityProjection, ...]
    has_declared_effects: bool


@dataclass(frozen=True, slots=True)
class PetriFiringPreflight:
    """Exact pure-math permit produced before allocation/admission/dispatch."""

    checkpoint_ref: VersionRef
    net_ref: VersionRef
    transition_id: str
    claimed_token_refs: tuple[VersionRef, ...]
    outcomes: tuple[PetriOutcomePreflight, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.checkpoint_ref, VersionRef)
                or self.checkpoint_ref.entity_type != "marking_checkpoint/v1"
                or not isinstance(self.net_ref, VersionRef)
                or self.net_ref.entity_type != "net_instance/v1"
                or not isinstance(self.transition_id, str)
                or not self.transition_id
                or not isinstance(self.claimed_token_refs, tuple)
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "petri_token/v1"
                       for ref in self.claimed_token_refs)
                or len(set(self.claimed_token_refs))
                != len(self.claimed_token_refs)
                or not isinstance(self.outcomes, tuple)
                or not self.outcomes
                or any(not isinstance(item, PetriOutcomePreflight)
                       for item in self.outcomes)):
            raise TypeError("Petri firing preflight is incomplete")


@dataclass(frozen=True, slots=True)
class PetriFiringSetPreflight:
    """Joint capacity permit for one conflict-free allocation batch."""

    checkpoint_ref: VersionRef
    net_ref: VersionRef
    firings: tuple[PetriFiringPreflight, ...]
    places: tuple[PetriPlaceCapacityProjection, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.checkpoint_ref, VersionRef)
                or not isinstance(self.net_ref, VersionRef)
                or not self.firings
                or any(not isinstance(item, PetriFiringPreflight)
                       for item in self.firings)
                or any(item.checkpoint_ref != self.checkpoint_ref
                       or item.net_ref != self.net_ref
                       for item in self.firings)
                or any(not isinstance(item, PetriPlaceCapacityProjection)
                       for item in self.places)):
            raise TypeError("Petri firing-set preflight is incomplete")


def _declared_operation(structure: RuntimeNet, transition_id: str):
    transitions = tuple(
        item for item in structure.compiled.symbolic.transitions
        if item.name == transition_id)
    if len(transitions) != 1:
        raise MarkingStateError(
            "Petri firing preflight lacks one declared transition")
    operations = tuple(
        item.declaration for item in structure.compiled.operations
        if item.declaration.name == transitions[0].operation)
    if len(operations) != 1:
        raise MarkingStateError(
            "Petri firing preflight lacks one declared operation")
    return operations[0]


def _returned_counts(
    selected: RuntimeNet,
    transition_id: str,
    consumed_states: tuple[object, ...],
) -> Counter[str]:
    """Mirror the declaration-owned consume/return inscriptions by count."""

    returned: Counter[str] = Counter()
    reusable = {
        place for place in selected.places
        if selected.place_kind(place) in {"agent_resource", "resource_lease"}
    }
    consumed_by_place = Counter(state.place for state in consumed_states)

    # Ordinary read arcs consume one historical occurrence and return one
    # successor occurrence carrying the same immutable resource.
    for place, scope in selected.read_scope_of(transition_id):
        if scope == "read" and place not in reusable:
            returned[place] += consumed_by_place[place]

    # A static reusable capacity colour is returned once per claimed place.
    for place in reusable:
        if (consumed_by_place[place]
                and selected.output_emit_for(transition_id, place)
                == "content_less"):
            returned[place] += 1

    # Variable resource colours are returned one-for-one without an ordinary
    # output arc. Read-only colours were not consumed and remain in the base
    # marking, so only consumed_states participate here.
    variable = selected.variable_resource_arc_for(transition_id)
    if variable is not None:
        returned[variable.lease_pool_place] += consumed_by_place[
            variable.lease_pool_place]
    return returned


def _produced_upper_bounds(
    selected: RuntimeNet,
    transition_id: str,
    operation,
    outcome,
) -> Counter[str]:
    """Compute the largest token multiset permitted by one output bundle."""

    ports = {item.name: item for item in selected.compiled.ports}
    products_by_place: dict[str, list[object]] = {}
    selected_outputs = set(selected.outputs_of(transition_id))
    for product in outcome.products:
        port = ports.get(product.port)
        if port is None or product.port not in operation.outputs:
            raise MarkingStateError(
                "Petri firing preflight product lacks its declared output port")
        if port.place not in selected_outputs:
            raise MarkingStateError(
                "Petri firing preflight product is outside selected output arcs")
        products_by_place.setdefault(port.place, []).append(product)

    produced: Counter[str] = Counter()
    reusable = {
        place for place in selected.places
        if selected.place_kind(place) in {"agent_resource", "resource_lease"}
    }
    for place in selected_outputs:
        emit = selected.output_emit_for(transition_id, place)
        weight = selected.output_arc_weight(transition_id, place)
        products = products_by_place.get(place, ())
        product_maximum = sum(item.maximum for item in products)

        if emit == "route_selected":
            if product_maximum:
                raise MarkingStateError(
                    "route-selected Petri output cannot also be an operation product")
            # A declared effect may select any multiset up to the exact arc
            # multiplicity. Reserve that complete formal envelope before HOST.
            produced[place] += weight
            continue
        if emit == "lease_mint":
            # Each actual registered product mints exactly one M=1 colour;
            # ordinary output-arc multiplication does not apply.
            produced[place] += product_maximum
            continue
        if place in reusable:
            # Reusable returns are calculated from exact consumed colours.
            continue

        product_bound = product_maximum * (weight or 1)
        contentless_bound = (
            weight
            if emit in {"content_less", "control_only", "forward"}
            else 0)
        if not products and not contentless_bound:
            raise MarkingStateError(
                "Petri firing preflight data output has no declared product")
        # If every product is optional, the no-product branch may still emit
        # one mechanical proposal. It is alternative to, not additive with,
        # the product bundle.
        produced[place] += max(product_bound, contentless_bound)
    return produced


def _potential_reset_counts(
    selected: RuntimeNet,
    predecessor: TypedMarkingAuthority,
    transition_id: str,
    claimed_token_refs: tuple[VersionRef, ...],
) -> Counter[str]:
    """Largest legal predecessor retirement declared by formal reset arcs.

    This is an existence bound, not an effect selection.  The later Success
    projector still requires the exact callback instruction and checks the
    actual retired refs.  Structural-revision effects do not receive reset
    credit here.
    """

    reset_places = {
        place for _selector, place in selected.reset_arcs_of(transition_id)}
    if not reset_places:
        return Counter()
    places = {
        item.name: item for item in selected.compiled.symbolic.places}
    ports = {item.name: item for item in selected.compiled.ports}
    terminal_names = {
        f"{terminal.source.component}.{terminal.source.port}"
        for terminal in (
            selected.compiled.source.terminal,
            *selected.compiled.source.terminal_alternatives)}
    terminal_places = {
        ports[name].place for name in terminal_names if name in ports}
    claimed = set(claimed_token_refs)
    resettable: Counter[str] = Counter()
    for token in predecessor.tokens:
        state = token.state
        place = places.get(state.place)
        if (state.place not in reset_places
                or token.token_ref in claimed
                or place is None
                or place.reusable
                or place.token_kind in {"agent_resource", "resource_lease"}
                or state.place in terminal_places
                or state.lease_identity_ref is not None
                or state.lease_claims
                or state.work_resource_ref is not None
                or state.kind is not None
                or state.continuation is not None
                or state.override_warning is not None
                or state.consumed_by is not None):
            continue
        resettable[state.place] += 1
    return resettable


def preflight_module_firing(
    structure: RuntimeNet,
    predecessor: TypedMarkingAuthority,
    *,
    transition_id: str,
    claimed_token_refs: tuple[VersionRef, ...],
) -> PetriFiringPreflight:
    """Prove every declared firing outcome capacity-valid before Registry I/O."""

    if (not isinstance(structure, RuntimeNet)
            or not isinstance(predecessor, TypedMarkingAuthority)
            or predecessor.net_ref != structure.registry_net_ref
            or transition_id not in structure.transitions):
        raise MarkingStateError(
            "Petri firing preflight differs from the adopted net/marking")
    claims = tuple(sorted(
        claimed_token_refs,
        key=lambda ref: (
            ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    if (not isinstance(claimed_token_refs, tuple)
            or any(not isinstance(ref, VersionRef)
                   or ref.entity_type != "petri_token/v1"
                   for ref in claimed_token_refs)
            or len(set(claimed_token_refs)) != len(claimed_token_refs)
            or claims != claimed_token_refs):
        raise MarkingStateError(
            "Petri firing preflight requires canonical exact token claims")
    token_by_ref = {item.token_ref: item.state for item in predecessor.tokens}
    if (len(token_by_ref) != len(predecessor.tokens)
            or not set(claims) <= set(token_by_ref)):
        raise MarkingStateError(
            "Petri firing preflight claims are absent from the predecessor")

    local = TeamNetMarking.from_authority(structure, predecessor)
    claim_epoch = local.install_registered_firing_claim(
        transition_id, claims)
    consumed_states = tuple(local.claimed_tokens(
        transition_id, claim_epoch))
    fresh = Counter(local.marking_snapshot()["fresh_by_place"])
    predecessor_counts = Counter(
        item.state.place for item in predecessor.tokens)
    consumed = predecessor_counts - fresh
    operation = _declared_operation(structure, transition_id)
    claimed_colours = tuple(
        (token_by_ref[ref].place, token_by_ref[ref].verdict)
        for ref in claims)
    capacities = {
        item.name: item.capacity
        for item in structure.compiled.symbolic.places}

    outcome_projections: list[PetriOutcomePreflight] = []
    for outcome in sorted(operation.outcomes, key=lambda item: item.name):
        selected = structure.for_outcome(
            transition_id, outcome.name).for_claimed_inputs(
                transition_id, claimed_colours)
        returned = _returned_counts(
            selected, transition_id, consumed_states)
        produced = _produced_upper_bounds(
            selected, transition_id, operation, outcome)
        potential_reset = _potential_reset_counts(
            selected, predecessor, transition_id, claims)
        rows = []
        for place in sorted(selected.places):
            successor_upper = (
                fresh[place] + returned[place] + produced[place])
            successor_minimum = (
                max(0, fresh[place] - potential_reset[place])
                + returned[place] + produced[place])
            capacity = capacities[place]
            row = PetriPlaceCapacityProjection(
                place=place,
                capacity=capacity,
                predecessor_count=predecessor_counts[place],
                consumed_count=consumed[place],
                potential_reset_count=potential_reset[place],
                returned_count=returned[place],
                produced_upper_bound=produced[place],
                successor_minimum=successor_minimum,
                successor_upper_bound=successor_upper,
            )
            rows.append(row)
            if capacity is not None and successor_minimum > capacity:
                raise MarkingStateError(
                    "Petri firing preflight exceeds declared place capacity: "
                    f"transition={transition_id!r}, outcome={outcome.name!r}, "
                    f"place={place!r}, projected={successor_minimum}, "
                    f"capacity={capacity}")
        outcome_projections.append(PetriOutcomePreflight(
            outcome_id=outcome.name,
            places=tuple(rows),
            has_declared_effects=bool(outcome.effects),
        ))

    return PetriFiringPreflight(
        checkpoint_ref=predecessor.checkpoint_ref,
        net_ref=predecessor.net_ref,
        transition_id=transition_id,
        claimed_token_refs=claims,
        outcomes=tuple(outcome_projections),
    )


def preflight_module_firing_set(
    structure: RuntimeNet,
    predecessor: TypedMarkingAuthority,
    *,
    occurrences: tuple[tuple[str, tuple[VersionRef, ...]], ...],
) -> PetriFiringSetPreflight:
    """Prove one allocation batch before its durable Registry publication.

    Each occurrence must first pass its complete outcome/effect envelope.  For
    a concurrent batch, output/return maxima are then added over the shared
    predecessor after exact consumed occurrences are removed.  Prospective
    reset credits are deliberately not shared across concurrent firings: an
    actual reset selection belongs to one later Success and cannot reserve
    capacity for a sibling before it exists.
    """

    if (not isinstance(occurrences, tuple)
            or not occurrences
            or any(not isinstance(item, tuple) or len(item) != 2
                   for item in occurrences)):
        raise MarkingStateError(
            "Petri firing-set preflight requires exact allocated occurrences")
    permits = tuple(preflight_module_firing(
        structure,
        predecessor,
        transition_id=transition_id,
        claimed_token_refs=claimed_token_refs,
    ) for transition_id, claimed_token_refs in occurrences)

    # A singleton retains its declared route/reset successor interval exactly.
    if len(permits) == 1:
        places = []
        for place in sorted(structure.places):
            choices = tuple(
                next(row for row in outcome.places if row.place == place)
                for outcome in permits[0].outcomes)
            highest = max(
                choices,
                key=lambda row: (
                    row.successor_minimum, row.successor_upper_bound))
            places.append(PetriPlaceCapacityProjection(
                place=place,
                capacity=highest.capacity,
                predecessor_count=highest.predecessor_count,
                consumed_count=highest.consumed_count,
                potential_reset_count=highest.potential_reset_count,
                returned_count=highest.returned_count,
                produced_upper_bound=highest.produced_upper_bound,
                successor_minimum=max(
                    row.successor_minimum for row in choices),
                successor_upper_bound=max(
                    row.successor_upper_bound for row in choices),
            ))
        return PetriFiringSetPreflight(
            predecessor.checkpoint_ref, predecessor.net_ref, permits,
            tuple(places))

    predecessor_counts = Counter(
        item.state.place for item in predecessor.tokens)
    consumed_refs: set[VersionRef] = set()
    consumed_counts: Counter[str] = Counter()
    for transition_id, claimed_token_refs in occurrences:
        local = TeamNetMarking.from_authority(structure, predecessor)
        claim_epoch = local.install_registered_firing_claim(
            transition_id, claimed_token_refs)
        consumed = tuple(local.claimed_tokens(
            transition_id, claim_epoch))
        refs = {state.token_ref for state in consumed}
        if consumed_refs & refs:
            raise MarkingStateError(
                "Petri firing-set preflight repeats one consumed occurrence")
        consumed_refs.update(refs)
        consumed_counts.update(state.place for state in consumed)

    capacities = {
        item.name: item.capacity
        for item in structure.compiled.symbolic.places}
    returned_max: Counter[str] = Counter()
    produced_max: Counter[str] = Counter()
    for permit in permits:
        by_place = {
            place: tuple(
                next(row for row in outcome.places if row.place == place)
                for outcome in permit.outcomes)
            for place in structure.places}
        for place, rows in by_place.items():
            highest = max(
                rows,
                key=lambda row: (
                    row.returned_count + row.produced_upper_bound,
                    row.returned_count,
                    row.produced_upper_bound))
            returned_max[place] += highest.returned_count
            produced_max[place] += highest.produced_upper_bound

    rows = []
    for place in sorted(structure.places):
        successor = (
            predecessor_counts[place]
            - consumed_counts[place]
            + returned_max[place]
            + produced_max[place])
        capacity = capacities[place]
        row = PetriPlaceCapacityProjection(
            place=place,
            capacity=capacity,
            predecessor_count=predecessor_counts[place],
            consumed_count=consumed_counts[place],
            potential_reset_count=0,
            returned_count=returned_max[place],
            produced_upper_bound=produced_max[place],
            successor_minimum=successor,
            successor_upper_bound=successor,
        )
        rows.append(row)
        if capacity is not None and successor > capacity:
            raise MarkingStateError(
                "Petri firing-set preflight exceeds declared place capacity: "
                f"place={place!r}, projected={successor}, "
                f"capacity={capacity}")
    return PetriFiringSetPreflight(
        predecessor.checkpoint_ref,
        predecessor.net_ref,
        permits,
        tuple(rows),
    )


__all__ = (
    "PetriFiringPreflight",
    "PetriFiringSetPreflight",
    "PetriOutcomePreflight",
    "PetriPlaceCapacityProjection",
    "preflight_module_firing",
    "preflight_module_firing_set",
)
