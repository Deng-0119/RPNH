"""Bounded exact graph/property/evidence distinctions, without runtime execution."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.rpnh.pn_validation import (
    AnalysisPolicy, EnvironmentContract, SchedulerContract, analyze,
    classify_terminal, input_digest, replay_witness, state_key,
    verify_graph, verify_report, verify_report_binding,
)
from test_pn_validation_semantics import make_input, start, settle, token


def conclusions(report):
    return {item.property_id: item for item in report.properties}


def test_one_step_success_and_allowed_failure_are_distinct_clean_terminals():
    analysis = make_input(outcomes=("complete", "failure"))
    report = analyze(analysis)
    results = conclusions(report)
    assert report.graph.complete
    assert {classify_terminal(analysis, node.state).classification for node in report.graph.nodes} == {
        "NONTERMINAL", "SUCCESS", "PERMITTED_FAILURE"}
    for name in ("safety", "proper_completion", "possible_successful_completion",
                 "allowed_completion_from_every_state", "inevitable_completion_without_fairness",
                 "dead_transition"):
        assert results[name].verdict == "HOLDS", (name, results[name].reason)
    success = results["possible_successful_completion"]
    assert len(success.witness) == 2
    assert replay_witness(analysis, success.witness).valid
    assert verify_graph(analysis, report.graph)
    assert results["local_progress"].verdict == "UNKNOWN"


@pytest.mark.parametrize("leftover", ["work", "active", "lease"])
def test_terminal_marker_does_not_hide_unfinished_work_claim_or_lease(leftover):
    analysis = make_input(lease=leftover == "lease")
    if leftover == "lease":
        state = settle(analysis, start(analysis)).state
    else:
        state = start(analysis) if leftover == "active" else analysis.state
        state = replace(state, base=replace(state.base,
            tokens=(*state.base.tokens, token(2, "step.result")), next_token_id=3))
    analysis = replace(analysis, state=state,
                       policy=AnalysisPolicy(max_states=1, properties=("proper_completion",)))
    classification = classify_terminal(analysis, state)
    assert classification.terminal_marked and not classification.allowed
    report = analyze(analysis)
    result = conclusions(report)["proper_completion"]
    assert result.verdict == "VIOLATED", result.reason
    assert replay_witness(analysis, result.witness).valid
    if leftover == "lease":
        released = replace(analysis, terminal_contract=replace(
            analysis.terminal_contract, released_lease_places=("step.lease",)))
        assert classify_terminal(released, state).classification == "SUCCESS"


def test_partial_graph_keeps_success_witness_but_global_properties_unknown():
    analysis = make_input(outcomes=("complete", "retry"), retry=True,
                          policy=AnalysisPolicy(max_states=5, max_depth=6))
    report = analyze(analysis)
    results = conclusions(report)
    assert not report.graph.complete and report.graph.frontier
    assert results["possible_successful_completion"].verdict == "HOLDS"
    assert replay_witness(analysis, results["possible_successful_completion"].witness).valid
    for name in ("safety", "proper_completion", "allowed_completion_from_every_state",
                 "dead_transition", "inevitable_completion_without_fairness"):
        assert results[name].verdict == "UNKNOWN", (name, results[name].reason)
    keys = [state_key(node.state) for node in report.graph.nodes]
    assert len(keys) == len(set(keys))
    retries = [node.state for node in report.graph.nodes
               if node.state.base.tokens and node.state.base.tokens[0].place == "step.request"
               and not node.state.active]
    assert len(retries) >= 2
    assert retries[0].base.tokens[0].token_ref != retries[1].base.tokens[0].token_ref
    assert retries[0].base.attempts != retries[1].base.attempts


@pytest.mark.parametrize("budget,expected", [
    ({"max_states": 1}, "max_states"),
    ({"max_edges": 1}, "max_edges"),
    ({"max_depth": 1}, "max_depth"),
    ({"max_bindings": 1}, "max_bindings"),
])
def test_cutoff_frontier_is_not_a_proven_dead_end(budget, expected):
    analysis = make_input(occurrences=2 if expected == "max_bindings" else 1,
                          policy=AnalysisPolicy(**budget))
    report = analyze(analysis)
    assert not report.graph.complete
    assert any(expected in item.reasons for item in report.graph.frontier)
    assert conclusions(report)["allowed_completion_from_every_state"].verdict == "UNKNOWN"
    assert conclusions(report)["inevitable_completion_without_fairness"].verdict == "UNKNOWN"


def test_time_cutoff_retains_initial_frontier(monkeypatch):
    import cpn.rpnh.pn_validation.explorer as explorer
    analysis = make_input(policy=AnalysisPolicy(max_seconds=0.5))
    ticks = iter((0.0, 1.0, 1.0))
    monkeypatch.setattr(explorer, "monotonic", lambda: next(ticks))
    report = analyze(analysis)
    assert not report.graph.complete and len(report.graph.nodes) == 1
    assert report.graph.frontier[0].reasons == ("max_seconds",)
    assert conclusions(report)["possible_successful_completion"].verdict == "UNKNOWN"


@pytest.mark.parametrize("missing", ["model", "external", "fairness", "terminal"])
def test_unmodeled_contracts_do_not_become_global_success(missing):
    analysis = make_input()
    if missing == "model":
        analysis = replace(analysis, operation_models=())
    elif missing == "external":
        analysis = replace(analysis, environment_contract=EnvironmentContract(closed=False, external_wait=True))
    elif missing == "fairness":
        analysis = replace(analysis, scheduler_contract=SchedulerContract(fairness="weak"))
    else:
        analysis = replace(analysis, terminal_contract=replace(analysis.terminal_contract, success_places=()))
    report = analyze(analysis)
    for name in ("possible_successful_completion", "allowed_completion_from_every_state",
                 "inevitable_completion_without_fairness", "local_progress"):
        assert conclusions(report)[name].verdict == "UNKNOWN"
    if missing == "external":
        assert classify_terminal(analysis, analysis.state).classification == "WAITING_EXTERNAL"


def test_input_binding_graph_replay_and_gate_reject_tampered_claims():
    analysis = make_input()
    report = analyze(analysis)
    encoded = json.loads(json.dumps(report.to_dict()))
    assert verify_report_binding(report, analysis)
    assert verify_report_binding(encoded, analysis)
    assert verify_report(encoded, analysis)
    before = deepcopy(encoded)
    assert verify_report(encoded, analysis) and encoded == before
    assert "input" not in report.to_public_dict()
    for changed in (
        replace(analysis, source_context_json='{"predecessor":"changed"}'),
        replace(analysis, terminal_contract=replace(analysis.terminal_contract, revision="v2")),
        replace(analysis, policy=replace(analysis.policy, max_states=9)),
        replace(analysis, state=replace(analysis.state, next_claim_id=2)),
    ):
        assert not verify_report_binding(encoded, changed)
    forged = deepcopy(encoded)
    forged["properties"][0]["verdict"] = "VIOLATED"
    assert verify_report_binding(forged, analysis)  # binding is deliberately not proof checking
    assert not verify_report(forged, analysis)
    forged = deepcopy(encoded)
    forged["graph"]["edges"] = []
    assert not verify_report(forged, analysis)
    bad_edge = replace(report.graph.edges[0], successor=replace(report.graph.edges[0].successor,
                       action=replace(report.graph.edges[0].successor.action, after_refs=())))
    assert not verify_graph(analysis, replace(report.graph, edges=(bad_edge, *report.graph.edges[1:])))
    witness = conclusions(report)["possible_successful_completion"].witness
    stale = replace(witness[0], token_refs=())
    assert not replay_witness(analysis, (stale, *witness[1:])).valid


def test_typed_colors_preserve_state_and_report_identity():
    analysis = make_input(occurrences=3, colours=(True, "true", "True"))
    states = [replace(analysis.state, base=replace(analysis.state.base,
              tokens=(replace(analysis.state.base.tokens[0], verdict=value),)))
              for value in (True, "true", "True")]
    assert len({state_key(state) for state in states}) == 3
    inputs = [replace(analysis, state=state) for state in states]
    assert len({input_digest(item) for item in inputs}) == 3
    report = analyze(inputs[0])
    assert verify_report_binding(report, inputs[0])
    assert not verify_report_binding(report, inputs[1])


def test_explicit_terminal_stop_and_persistent_data_are_contract_bound():
    analysis = make_input(ordinary_read=True, policy=AnalysisPolicy(max_states=5))
    analysis = replace(analysis, terminal_contract=replace(analysis.terminal_contract,
        persistent_places=("step.request",), stop_on_terminal=True))
    report = analyze(analysis)
    assert report.graph.complete and len(report.graph.nodes) == 3
    assert conclusions(report)["proper_completion"].verdict == "HOLDS"
    assert conclusions(report)["inevitable_completion_without_fairness"].verdict == "HOLDS"
    terminal = report.graph.nodes[-1].state
    from cpn.rpnh.pn_validation.semantics import enabled_bindings
    extra = enabled_bindings(analysis, terminal).bindings[0]
    assert not replay_witness(analysis, report.graph.path_to(2) + (extra,)).valid


def test_unsafe_outcome_keeps_safety_witness_and_prevents_global_completion():
    from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration, policy_allows

    def capacity_bad_alternate(fragment):
        return replace(fragment,
            places=tuple(replace(place, capacity=1) if place.name == "failure" else place
                         for place in fragment.places))

    required = "inevitable_completion_without_fairness"
    analysis = make_input(outcomes=("complete", "failure"),
        fragment_transform=capacity_bad_alternate,
        policy=AnalysisPolicy(mode="strict", required_properties=(required,)))
    # One retained control token is legal initial occupancy. The failure case
    # adds another token to capacity one; complete reaches clean SUCCESS while
    # retaining the explicitly persistent control token.
    analysis = replace(analysis,
        state=replace(analysis.state, base=replace(analysis.state.base,
            tokens=(*analysis.state.base.tokens, token(2, "step.failure")), next_token_id=3)),
        terminal_contract=replace(analysis.terminal_contract,
            permitted_failure_places=(), persistent_places=("step.failure",)))
    report = analyze(analysis)
    results = conclusions(report)
    assert results["safety"].verdict == "VIOLATED"
    assert "capacity_exceeded" in results["safety"].reason
    replay = replay_witness(analysis, results["safety"].witness)
    assert replay.valid and replay.safety_violations
    assert results["possible_successful_completion"].verdict == "HOLDS"
    assert not report.graph.complete
    assert any("unsafe successor unresolved" in item.reasons for item in report.graph.frontier)
    assert results[required].verdict == "UNKNOWN"
    assert results["allowed_completion_from_every_state"].verdict == "UNKNOWN"
    assert not policy_allows(ValidationConfiguration(policy=analysis.policy), report.to_dict())
    assert verify_report(report, analysis)


def test_report_verifies_stored_time_prefix_without_new_bfs_and_rejects_fakecomplete(monkeypatch):
    import cpn.rpnh.pn_validation.explorer as explorer
    import cpn.rpnh.pn_validation.evidence as evidence
    from cpn.rpnh.pn_validation.runtime_gate import ValidationConfiguration, policy_allows

    analysis = make_input(policy=AnalysisPolicy(max_seconds=0.5))
    complete = analyze(analysis)
    ticks = iter((0.0, 0.0, 0.0, 1.0, 1.0))
    monkeypatch.setattr(explorer, "monotonic", lambda: next(ticks))
    report = analyze(analysis)
    encoded = report.to_dict()
    assert len(report.graph.nodes) == 2 and len(report.graph.edges) == 1
    assert not report.graph.complete and report.graph.frontier[0].reasons == ("max_seconds",)
    assert conclusions(report)["possible_successful_completion"].verdict == "UNKNOWN"

    def forbidden(*args, **kwargs):
        raise AssertionError("proof checking must not start BFS or read its clock")

    monkeypatch.setattr(evidence, "analyze", forbidden)
    monkeypatch.setattr(explorer, "monotonic", forbidden)
    before = deepcopy(encoded)
    assert verify_report(report, analysis) and verify_report(encoded, analysis)
    assert encoded == before and verify_report(complete, analysis)
    assert policy_allows(ValidationConfiguration(policy=analysis.policy), encoded)
    encoded["graph"]["elapsed_seconds"] = 123.0
    assert verify_report(encoded, analysis)
    forged = deepcopy(encoded)
    forged["graph"]["complete"] = True
    forged["graph"]["expanded"] = list(range(len(forged["graph"]["nodes"])))
    forged["graph"]["frontier"] = []
    assert verify_report_binding(forged, analysis) and not verify_report(forged, analysis)
    forged = deepcopy(encoded)
    forged["properties"][0]["verdict"] = "HOLDS"
    assert not verify_report(forged, analysis)
