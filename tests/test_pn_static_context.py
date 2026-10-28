"""E04 structural reuse is local, ordered, and not a new PN authority."""
from dataclasses import replace
from copy import copy

import pytest

from cpn.rpnh.pn_validation import explorer, projection
from cpn.rpnh.pn_validation.contracts import AnalysisInput, AnalysisPolicy, canonical_json
from cpn.rpnh.runtime_net import RuntimeNet
from test_pn_validation_semantics import make_input


def assert_arc_oracle(net):
    arcs = net._arcs
    # Output projection methods retain their original predicates. Supplying
    # every arc as the candidate sequence is the original full-scan oracle.
    full_scan = copy(net)
    full_scan._outputs_by_transition = {t: arcs for t in (*net.transitions, "missing")}
    def outcome(call):
        try:
            return ("value", call())
        except (ValueError, KeyError, StopIteration, TypeError) as exc:
            return (type(exc).__name__, str(exc))
    for t in (*net.transitions, "missing"):
        for method in ("output_guards_of", "output_emit_of", "output_color_expressions_of", "selected_output_arcs_of"):
            assert outcome(lambda: getattr(net, method)(t)) == outcome(lambda: getattr(full_scan, method)(t))
        for p in (*net.places, "missing"):
            for method in ("forward_source_place", "output_guard_for", "output_emit_for", "output_color_expression_for", "output_lease_claims_for", "output_lease_claim_exclusions_for", "output_lease_claim_set_for", "output_effect_selector_for"):
                assert outcome(lambda: getattr(net, method)(t, p)) == outcome(lambda: getattr(full_scan, method)(t, p))
            assert outcome(lambda: net.project_output_color(t, p, ())) == outcome(lambda: full_scan.project_output_color(t, p, ()))
    for place in (*net.places, "missing"):
        assert net.producers_of(place) == tuple(dict.fromkeys(a.transition for a in net.compiled.symbolic.arcs if a.direction == "output" and a.place == place))
        assert net.consumers_of(place) == tuple(dict.fromkeys(a.transition for a in arcs if a.direction == "input" and a.place == place))
    for transition in (*net.transitions, "missing"):
        assert net.inputs_of(transition) == tuple(dict.fromkeys(a.place for a in arcs if a.direction == "input" and a.mode != "guard" and a.transition == transition))
        assert net.token_inputs_of(transition) == tuple(dict.fromkeys(p for p, t, _ in net.token_input_arcs if t == transition))
        assert net.reference_inputs_of(transition) == tuple(dict.fromkeys(p for p, t, _ in net.registry_read_arcs if t == transition))
        assert net.outputs_of(transition) == tuple(dict.fromkeys(a.place for a in arcs if a.direction == "output" and a.transition == transition))
        assert net.read_scope_of(transition) == tuple((a.place, "read" if a.mode == "read" else "consume") for a in arcs if a.direction == "input" and a.mode != "guard" and a.transition == transition)
        for place in (*net.places, "missing"):
            assert net.input_arc_weight(place, transition) == sum(a.weight for a in arcs if a.direction == "input" and a.mode != "guard" and a.place == place and a.transition == transition)
            output = tuple(a for a in arcs if a.direction == "output" and a.place == place and a.transition == transition)
            outcomes = {a.outcome for a in output if a.outcome is not None}
            weight = sum(a.weight for a in output) if transition in net._selected_outcomes else max((sum(a.weight for a in output if a.outcome in {None, outcome}) for outcome in outcomes), default=sum(a.weight for a in output))
            assert net.output_arc_weight(transition, place) == weight


@pytest.mark.parametrize("kwargs", [{}, {"outcomes": ("complete", "failure")}, {"ordinary_read": True}, {"lease": True}, {"colours": (True, "true"), "guard": True}, {"count_scope": "all"}, {"output_weight": 2}])
def test_ordered_static_index_matches_old_comprehensions(kwargs):
    analysis = make_input(**kwargs)
    net = projection.runtime_net(analysis)
    assert_arc_oracle(net)
    for transition in net.compiled.symbolic.transitions:
        operation = next(o for o in net.compiled.symbolic.operations if o.name == transition.operation)
        for outcome in operation.outcomes:
            selected = net.for_outcome(transition.name, outcome.name)
            assert_arc_oracle(selected)
            claimed = selected.for_claimed_inputs(transition.name, ())
            assert_arc_oracle(claimed)
            assert_arc_oracle(claimed.for_outcome(transition.name, outcome.name))
            assert net._selected_outcomes == {}
    with pytest.raises(TypeError):
        net._outputs_by_pair[("x", "y")] = ()


def test_context_loads_once_and_never_shares_markings(monkeypatch):
    analysis = make_input(occurrences=2)
    original = AnalysisInput.compiled.fget
    calls = []
    def counted(self):
        calls.append(self)
        return original(self)
    monkeypatch.setattr(AnalysisInput, "compiled", property(counted))
    context = projection._ExplorationInput(analysis)
    a = projection.restore_state(context, analysis.state)
    b = projection.restore_state(context, analysis.state)
    assert a is not b and a._tokens is not b._tokens
    assert len(calls) == 1
    assert all(x is not y for x, y in zip(a._tokens, b._tokens))
    other = projection._ExplorationInput(analysis)
    assert other.compiled is not context.compiled
    assert projection.runtime_net(other) is not projection.runtime_net(context)
    assert canonical_json(analysis) == canonical_json(context._original)


def test_setup_time_counts_towards_same_deadline(monkeypatch):
    analysis = make_input(policy=AnalysisPolicy(max_seconds=0.5))
    now = {"value": 0.0}
    original = AnalysisInput.compiled.fget
    def expensive_setup(self):
        result = original(self)
        now["value"] = 1.0
        return result
    monkeypatch.setattr(AnalysisInput, "compiled", property(expensive_setup))
    monkeypatch.setattr(explorer, "monotonic", lambda: now["value"])
    graph = explorer.explore(analysis)
    assert not graph.edges and graph.initial_bindings is None
    assert graph.frontier[0].reasons == ("max_seconds",)
    assert graph.elapsed_seconds == 1.0


@pytest.mark.parametrize("kwargs", [{}, {"occurrences": 2}, {"outcomes": ("complete", "failure")}, {"ordinary_read": True}, {"lease": True}])
def test_call_scoped_reuse_preserves_entire_graph_and_witnesses(monkeypatch, kwargs):
    # The oracle runs all original per-access parsing/restoration paths, using
    # the same ordering, semantics and non-time budgets. Compare every state,
    # binding, active claim, edge, parent/witness, queue frontier and support.
    analysis = make_input(policy=AnalysisPolicy(max_depth=3, max_states=50, max_edges=100, max_seconds=1000), **kwargs)
    original = projection._ExplorationInput
    class NoReuse:
        def __new__(cls, value):
            return value
    monkeypatch.setattr(projection, "_ExplorationInput", NoReuse)
    baseline = explorer.explore(analysis)
    monkeypatch.setattr(projection, "_ExplorationInput", original)
    optimized = explorer.explore(analysis)
    assert replace(baseline, elapsed_seconds=0) == replace(optimized, elapsed_seconds=0)
    assert [baseline.path_to(i) for i in range(len(baseline.nodes))] == [optimized.path_to(i) for i in range(len(optimized.nodes))]
