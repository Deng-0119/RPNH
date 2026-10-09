"""Budget regressions use sentinels, never allocate the large weighted pool."""
from dataclasses import replace
from itertools import combinations as real_combinations

import pytest

from cpn.rpnh.pn_validation import AnalysisPolicy, analyze, verify_report
from cpn.rpnh.pn_validation import semantics
from test_pn_validation_semantics import make_input


def weighted_input():
    def weighted(fragment):
        return replace(fragment, arcs=tuple(replace(arc, weight=15)
            if arc.direction == "input" else arc for arc in fragment.arcs))
    return make_input(occurrences=30, fragment_transform=weighted,
        policy=AnalysisPolicy(max_bindings=1, max_states=4, max_edges=4, max_depth=2))


def install_sentinel(monkeypatch, *, maximum, on_yield=None):
    counts = {"yielded": 0}
    def guarded(tokens, weight):
        for group in real_combinations(tokens, weight):
            counts["yielded"] += 1
            assert counts["yielded"] <= maximum, "combinations consumed beyond bounded prefix"
            if on_yield:
                on_yield(counts["yielded"])
            yield group
    monkeypatch.setattr(semantics, "combinations", guarded)
    return counts


def test_weighted_binding_budget_consumes_only_candidate_and_lookahead(monkeypatch):
    analysis = weighted_input()
    counts = install_sentinel(monkeypatch, maximum=2)
    result = semantics.enabled_bindings(analysis, analysis.state)
    assert result.candidates_examined == 1 and not result.complete
    assert result.reasons == ("max_bindings",) and counts["yielded"] == 2
    assert len(result.bindings) == 1
    assert result.bindings[0].token_refs == tuple(token.token_ref for token in analysis.state.base.tokens[:15])


def test_lazy_product_keeps_order_and_empty_input_never_builds_a_pool(monkeypatch):
    groups = list(semantics._candidate_groups((((1, 2), 1), ((10, 11, 12), 2)), None))
    assert groups == [((a,), b) for a in (1, 2) for b in ((10, 11), (10, 12), (11, 12))]
    analysis = weighted_input()
    state = replace(analysis.state, base=replace(analysis.state.base, tokens=()))
    counts = install_sentinel(monkeypatch, maximum=0)
    result = semantics.enabled_bindings(analysis, state)
    assert result.complete and not result.bindings and not result.candidates_examined
    assert counts["yielded"] == 0


def test_internal_deadline_prefix_replays_exact_candidate_count(monkeypatch):
    import cpn.rpnh.pn_validation.explorer as explorer
    clock = {"now": 0.0}
    monkeypatch.setattr(explorer, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(semantics, "monotonic", lambda: clock["now"])
    analysis = weighted_input()
    counts = install_sentinel(monkeypatch, maximum=2,
        on_yield=lambda number: clock.update(now=100.0) if number == 1 else None)
    report = analyze(analysis)
    assert report.graph.initial_bindings.candidates_examined == 1
    assert report.graph.initial_bindings.reasons == ("max_seconds",)
    assert report.graph.frontier and not report.graph.complete
    assert counts["yielded"] == 1
    # Changing verifier speed must not change the stored prefix or UNKNOWN.
    clock["now"] = 0.0
    assert verify_report(report, analysis)
    assert counts["yielded"] == 2
    conclusions = {item.property_id: item.verdict for item in report.properties}
    assert conclusions["safety"] == "UNKNOWN" and conclusions["enabledness"] == "HOLDS"


def test_untouched_timeout_frontier_does_not_restart_enabledness(monkeypatch):
    import cpn.rpnh.pn_validation.explorer as explorer
    ticks = iter((0.0, 100.0, 100.0))
    monkeypatch.setattr(explorer, "monotonic", lambda: next(ticks))
    analysis = weighted_input()
    counts = install_sentinel(monkeypatch, maximum=0)
    report = analyze(analysis)
    assert report.graph.initial_bindings is None and counts["yielded"] == 0
    conclusions = {item.property_id: item.verdict for item in report.properties}
    for name in ("enabledness", "safety", "possible_successful_completion",
                 "allowed_completion_from_every_state", "inevitable_completion_without_fairness"):
        assert conclusions[name] == "UNKNOWN"
    assert conclusions["terminal_classification"] == "HOLDS"  # exact present NONTERMINAL classification
    assert verify_report(report, analysis) and counts["yielded"] == 0


def test_initial_terminal_keeps_bounded_enabledness_point_query():
    from test_pn_validation_properties import conclusions
    from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration, policy_allows
    analysis = make_input(ordinary_read=True, policy=AnalysisPolicy(max_states=5))
    analysis = replace(analysis, terminal_contract=replace(analysis.terminal_contract,
        persistent_places=("step.request",), stop_on_terminal=True))
    terminal = analyze(analysis).graph.nodes[-1].state
    initial_terminal = replace(analysis, state=terminal,
        policy=AnalysisPolicy(mode="strict", properties=("enabledness",),
            required_properties=("enabledness",)))
    report = analyze(initial_terminal)
    assert report.graph.complete and not report.graph.edges
    assert report.graph.initial_bindings.bindings
    assert conclusions(report)["enabledness"].verdict == "HOLDS"
    assert policy_allows(ValidationConfiguration(policy=initial_terminal.policy), report.to_dict())
    assert verify_report(report, initial_terminal)
