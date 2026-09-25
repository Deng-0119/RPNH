"""Executable structural view of the exact shared compiled Petri declaration.

This is marking input, not Registry authority. No component is loaded or
lowered again; operation configurations and conditional arc outcomes are data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .executable_net import CompiledPetriNet, load_compiled_net
from .registry.models import VersionRef


@dataclass(frozen=True)
class RuntimeTransition:
    config: Mapping[str, Any]


class RuntimeNet:
    """The shared PN consume/read/produce interface used by the sole marking."""

    def __init__(self, compiled: CompiledPetriNet, *, net_ref: VersionRef, resource_plan=None):
        if not isinstance(compiled, CompiledPetriNet) or load_compiled_net(compiled.to_dict()) != compiled:
            raise TypeError("runtime net requires exact mechanically verified compiled inventory")
        if not isinstance(net_ref, VersionRef) or net_ref.entity_type != "net_instance/v1":
            raise TypeError("runtime net requires an exact registered net address")
        self.compiled = compiled
        if resource_plan is None and (compiled.symbolic.lease_identities or compiled.symbolic.logical_slots):
            raise ValueError("resource-bearing runtime requires verified registered resource bindings")
        self._resource_plan = resource_plan
        self.registry_net_ref = net_ref
        self.places = tuple(place.name for place in compiled.symbolic.places)
        self._places = {place.name: place for place in compiled.symbolic.places}
        operations = {operation.name: operation for operation in compiled.symbolic.operations}
        self.transitions = MappingProxyType({transition.name: RuntimeTransition(
            MappingProxyType(dict(operations[transition.operation].config)))
            for transition in compiled.symbolic.transitions})
        self._arcs = compiled.symbolic.arcs
        self._reset_arcs = compiled.symbolic.reset_arcs
        self._transitions = {t.name: t for t in compiled.symbolic.transitions}
        self._selected_outcomes = {}
        self.initial_marking = MappingProxyType({p.name: sum(t.count for t in p.initial_tokens)
            for p in compiled.symbolic.places if p.initial_tokens})
        self.sources = tuple(place for place in self.places if not self.producers_of(place))
        self.registry_read_arcs = tuple((arc.place, arc.transition, arc.weight)
            for arc in self._arcs if arc.direction == "input" and arc.mode == "read")
        self.token_input_arcs = tuple((arc.place, arc.transition, arc.weight)
            for arc in self._arcs if arc.direction == "input" and arc.mode in {"consume", "borrow"})
        self.output_arcs = tuple((arc.transition, arc.place, arc.weight)
            for arc in self._arcs if arc.direction == "output")
        self.synthetic_ignition_arcs = ()
        self.timed_wait_guards = ()
        self.place_color_sets = MappingProxyType({p.name: tuple(p.colours)
            for p in compiled.symbolic.places if p.colours})
        self.resource_lease_pool_bindings = () if resource_plan is None else resource_plan.lease_pools
        self.initial_marking = MappingProxyType({**self.initial_marking,
            **{pool.lease_pool_place: len(pool.initial_resource_refs + pool.initial_logical_slot_refs)
               for pool in self.resource_lease_pool_bindings}})
        self.variable_resource_arcs = () if resource_plan is None else resource_plan.variable_resource_arcs
        self.logical_artifact_bindings = () if resource_plan is None else resource_plan.logical_slots
        self.registered_fault_transition_routes = MappingProxyType({})
        self.registered_fault_places = ()

    def producers_of(self, place):
        # Producer provenance in a persisted marking is declaration-wide.  A
        # selected outcome only constrains deposits for the firing currently
        # being projected; it must not make a token produced by an earlier,
        # different outcome invalid when that checkpoint is resumed.
        return tuple(dict.fromkeys(arc.transition for arc in self.compiled.symbolic.arcs
                                  if arc.direction == "output" and arc.place == place))

    def consumers_of(self, place):
        return tuple(dict.fromkeys(arc.transition for arc in self._arcs
                                  if arc.direction == "input" and arc.place == place))

    def inputs_of(self, transition_id):
        return tuple(dict.fromkeys(arc.place for arc in self._arcs
                                  if arc.direction == "input" and arc.mode != "guard"
                                  and arc.transition == transition_id))

    def token_inputs_of(self, transition_id):
        return tuple(dict.fromkeys(place for place, target, _ in self.token_input_arcs if target == transition_id))

    def reference_inputs_of(self, transition_id):
        return tuple(dict.fromkeys(place for place, target, _ in self.registry_read_arcs if target == transition_id))

    def outputs_of(self, transition_id):
        return tuple(dict.fromkeys(arc.place for arc in self._arcs
                                  if arc.direction == "output" and arc.transition == transition_id))

    def input_arc_weight(self, place, transition_id):
        return sum(arc.weight for arc in self._arcs
                   if (arc.place, arc.transition, arc.direction) == (place, transition_id, "input")
                   and arc.mode != "guard")

    def output_arc_weight(self, transition_id, place):
        arcs = tuple(arc for arc in self._arcs
                     if (arc.place, arc.transition, arc.direction) == (place, transition_id, "output"))
        if transition_id in self._selected_outcomes:
            return sum(arc.weight for arc in arcs)
        outcomes = {arc.outcome for arc in arcs if arc.outcome is not None}
        return max((sum(arc.weight for arc in arcs if arc.outcome in {None, outcome})
                    for outcome in outcomes), default=sum(arc.weight for arc in arcs))

    def for_outcome(self, transition_id, outcome_id):
        """Derive the executable inscription after registered product selection.

        Outcome selection is an operation-output contract fact, not an input
        token's semantic judgement. The immutable declaration/net identity
        stays unchanged; only this one transition's conditional arcs resolve.
        """
        transition = next(item for item in self.compiled.symbolic.transitions
                          if item.name == transition_id)
        operation = next(item for item in self.compiled.symbolic.operations
                         if item.name == transition.operation)
        if outcome_id not in {item.name for item in operation.outcomes}:
            raise ValueError("runtime inscription selects an undeclared outcome")
        view = RuntimeNet(self.compiled, net_ref=self.registry_net_ref, resource_plan=self._resource_plan)
        view._selected_outcomes = {**self._selected_outcomes, transition_id: outcome_id}
        view._arcs = tuple(arc for arc in self.compiled.symbolic.arcs
            if arc.direction != "output" or arc.transition not in view._selected_outcomes
            or arc.outcome in {None, view._selected_outcomes[arc.transition]})
        view.output_arcs = tuple((arc.transition, arc.place, arc.weight)
            for arc in view._arcs if arc.direction == "output")
        view._reset_arcs = tuple(
            arc for arc in self.compiled.symbolic.reset_arcs
            if arc.transition not in view._selected_outcomes
            or arc.outcome in {
                None, view._selected_outcomes[arc.transition]})
        return view

    def for_claimed_inputs(self, transition_id, claimed_colors):
        """Resolve exact typed input predicates, independently of products."""
        from dataclasses import replace
        view = RuntimeNet(self.compiled, net_ref=self.registry_net_ref, resource_plan=self._resource_plan)
        view._selected_outcomes = dict(self._selected_outcomes)
        view._reset_arcs = self._reset_arcs
        view._arcs = tuple(replace(a, output_predicates=())
            if a.direction == "output" and a.transition == transition_id else a
            for a in self._arcs if a.direction != "output" or a.transition != transition_id
            or self.output_predicates_hold(a, claimed_colors))
        view.output_arcs = tuple((a.transition, a.place, a.weight)
            for a in view._arcs if a.direction == "output")
        return view

    def place_kind(self, place):
        return self._places[place].token_kind

    def agent_resource_places(self):
        return frozenset(p.name for p in self._places.values() if p.token_kind == "agent_resource")
    def resource_lease_places(self):
        return frozenset(p.name for p in self._places.values() if p.token_kind == "resource_lease")
    def lease_identity_ref(self, qualified_name):
        """Return one exact identity from the adopted net's resource plan."""
        if self._resource_plan is None:
            raise KeyError("runtime net has no registered resource plan")
        try:
            return self._resource_plan.lease_refs[qualified_name]
        except KeyError as exc:
            raise KeyError(
                f"runtime net has no declared lease identity {qualified_name!r}"
            ) from exc
    def reusable_resource_places(self):
        return frozenset(p.name for p in self._places.values() if p.reusable)
    def registered_fault_transition(self, transition_id): return None
    def registered_fault_consumers_of(self, place): return ()
    def registered_fault_outputs_of(self, transition_id): return ()
    def variable_resource_arc_for(self, transition_id):
        return next((a for a in self.variable_resource_arcs if a.transition_id == transition_id), None)
    def logical_document_input_places(self, transition_id):
        return frozenset(p for s in self.logical_artifact_bindings
            if transition_id in s.consumer_transition_ids for p in (s.published_place,)
            if p in self.inputs_of(transition_id))
    def count_predicates_of(self, transition_id):
        return tuple((g.place, g.threshold, g.comparison, g.scope)
                     for g in self._transitions[transition_id].count_guards)
    def forward_source_place(self, transition_id, output_place):
        return next((a.forward_source for a in self._arcs if a.transition == transition_id
                     and a.place == output_place and a.direction == "output" and a.emit == "forward"), None)
    def counter_guards_of(self, transition_id):
        return tuple((g.place, g.threshold, g.comparison)
                     for g in self._transitions[transition_id].count_guards)
    def verdict_guards_of(self, transition_id):
        return tuple((g.place, g.expected) for g in self._transitions[transition_id].input_verdicts)

    def output_guards_of(self, transition_id):
        # Only claimed input-colour predicates are PN deposit guards.
        # Registered operation-output outcome selection remains for_outcome.
        return tuple((a.place, p.expected) for a in self._arcs
                     if a.direction == "output" and a.transition == transition_id
                     for p in a.output_predicates)

    def output_guard_for(self, transition_id, place):
        guards = {outcome for target, outcome in self.output_guards_of(transition_id) if target == place}
        if len(guards) > 1:
            # Before completion the exact output colour is unavailable. No
            # input decision can resolve multiple operation-output outcomes;
            # Success derives for_outcome from the registered bundle instead.
            return tuple(sorted(guards))
        return next(iter(guards), None)

    def output_emit_of(self, transition_id):
        return tuple((a.place, a.emit) for a in self._arcs if a.direction == "output"
                     and a.transition == transition_id and a.emit != "produced")
    def output_emit_for(self, transition_id, place):
        return next((emit for target, emit in self.output_emit_of(transition_id) if target == place), None)
    def output_color_expression_for(self, transition_id, place):
        return next((asdict(a.colour_expression) for a in self._arcs if a.transition == transition_id
                     and a.place == place and a.direction == "output" and a.colour_expression is not None), None)
    def output_color_expressions_of(self, transition_id):
        return tuple((a.place, asdict(a.colour_expression)) for a in self._arcs
                     if a.transition == transition_id and a.direction == "output" and a.colour_expression is not None)
    def output_lease_claims_for(self, transition_id, place):
        arcs = tuple(a for a in self._arcs
                     if a.transition == transition_id
                     and a.place == place and a.direction == "output")
        if not arcs:
            raise ValueError(
                "lease-colour projection requires a selected output arc")
        claims = arcs[0].lease_claims
        if any(arc.lease_claims != claims for arc in arcs[1:]):
            raise ValueError(
                "lease-colour projection has ambiguous output declarations")
        return claims
    def output_lease_claim_exclusions_for(self, transition_id, place):
        arcs = tuple(a for a in self._arcs
                     if a.transition == transition_id
                     and a.place == place and a.direction == "output")
        if not arcs:
            raise ValueError(
                "lease-colour exclusion requires a selected output arc")
        exclusions = arcs[0].lease_claim_exclusions
        if any(arc.lease_claim_exclusions != exclusions for arc in arcs[1:]):
            raise ValueError(
                "lease-colour exclusion has ambiguous output declarations")
        return exclusions
    def output_lease_claim_set_for(self, transition_id, place):
        expressions = tuple(
            a.lease_claim_set for a in self._arcs
            if a.transition == transition_id
            and a.place == place and a.direction == "output"
            and a.lease_claim_set is not None)
        if len(set(expressions)) > 1:
            raise ValueError("lease-claim set projection is ambiguous")
        return expressions[0] if expressions else None
    def selected_output_arcs_of(self, transition_id):
        return tuple(
            (arc.effect_selector, arc.place) for arc in self._arcs
            if arc.transition == transition_id
            and arc.direction == "output"
            and arc.effect_selector is not None)
    def selected_output_place_for_selector(self, transition_id, selector):
        places = tuple(
            place for handle, place in self.selected_output_arcs_of(
                transition_id)
            if handle == selector)
        if len(places) > 1:
            raise ValueError("selected-output selector is ambiguous")
        return places[0] if places else None
    def output_effect_selector_for(self, transition_id, place):
        selectors = tuple(
            arc.effect_selector for arc in self._arcs
            if arc.transition == transition_id
            and arc.place == place
            and arc.direction == "output"
            and arc.effect_selector is not None)
        if len(selectors) > 1:
            raise ValueError("selected-output place is ambiguous")
        return selectors[0] if selectors else None
    def lease_pool_place_for(self, qualified_name):
        pools = tuple(
            pool.lease_pool_place
            for pool in self.resource_lease_pool_bindings
            if pool.name == qualified_name)
        if len(pools) != 1:
            raise ValueError("lease-claim set lacks one exact registered pool")
        return pools[0]
    def reset_arcs_of(self, transition_id):
        return tuple(
            (arc.selector, arc.place) for arc in self._reset_arcs
            if arc.transition == transition_id)
    def reset_place_for_selector(self, transition_id, selector):
        places = tuple(
            place for handle, place in self.reset_arcs_of(transition_id)
            if handle == selector)
        if len(places) > 1:
            raise ValueError("reset selector is ambiguous")
        return places[0] if places else None
    def project_output_color(self, transition_id, place, claimed_colors):
        arc = next(a for a in self._arcs if a.transition == transition_id
                   and a.place == place and a.direction == "output")
        if not self.output_predicates_hold(arc, claimed_colors):
            raise ValueError("output colour lacks its exact claimed input predicate")
        expression = arc.colour_expression
        if expression is None:
            return None
        if expression.kind == "literal":
            value = expression.value
        elif expression.kind == "input_colour":
            colours = tuple(c for p, c in claimed_colors if p == expression.source_place)
            if len(colours) != self.input_arc_weight(expression.source_place, transition_id) or len(set(colours)) != 1:
                raise ValueError("input colour projection lacks an exact homogeneous source multiset")
            value = colours[0]
        elif expression.kind == "membership":
            value = expression.present if expression.member in expression.selected else expression.absent
        else:
            raise ValueError("undeclared mechanical colour expression")
        from .petri_contracts import colour_key
        if colour_key(value) not in {colour_key(c) for c in self._places[place].colours}:
            raise ValueError("projected colour is outside exact declared domain")
        return value
    def output_predicates_hold(self, arc, claimed_colors):
        from .petri_contracts import colour_key
        for predicate in arc.output_predicates:
            actual = tuple(c for p, c in claimed_colors if p == predicate.place)
            if len(actual) != self.input_arc_weight(predicate.place, arc.transition) or any(
                    colour_key(c) != colour_key(predicate.expected) for c in actual):
                return False
        return True
    def read_scope_of(self, transition_id):
        return tuple((arc.place, "read" if arc.mode == "read" else "consume")
                     for arc in self._arcs if arc.direction == "input" and arc.mode != "guard"
                     and arc.transition == transition_id)
    def is_guard_only_transition(self, transition_id):
        # The marking's input-free reservation interface includes a declared
        # PN source: no consume/read claim is invented to make it executable.
        # Exact adopted declaration/owner authority is checked by the writer.
        return not self.inputs_of(transition_id)


__all__ = ("RuntimeNet", "RuntimeTransition")
