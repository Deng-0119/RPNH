from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from rpnh_rrsi.method import (
    CandidateEvaluation,
    DataSplit,
    History,
    HistoryEntry,
    Incumbent,
    SelectionParameters,
    TaskPlan,
    TraceTask,
    TraceTrial,
    TrialObservation,
    aggregate_trials,
    build_representative_traces,
    calibrate_noise_band,
    project_trial_rows,
    relative_cost_change,
    select_candidate,
)


def test_m01_representative_traces_use_stable_extremes_and_threshold() -> None:
    result = build_representative_traces(
        (
            TraceTask("low", 0.1, (
                TraceTrial(0.2, "low-first"), TraceTrial(0.2, "low-second"))),
            TraceTask("middle", 0.5, (TraceTrial(0.5, "middle"),)),
            TraceTask("high", 0.9, (
                TraceTrial(1.0, "high-first"), TraceTrial(1.0, "high-second"))),
        ),
        planned_task_ids=("low", "middle", "high"),
        n_fail_traces=1,
        n_success_traces=1,
    )
    assert [(row.task_id, row.trial_index, row.trace_ref, row.group)
            for row in result.traces] == [
        ("low", 0, "low-first", "failure"),
        ("high", 0, "high-first", "success"),
    ]
    assert result.minimum_available == 1.0
    assert result.sufficient

    unavailable = build_representative_traces(
        (TraceTask("only", 0.1, (TraceTrial(0.0, None),)),),
        planned_task_ids=("only",), n_fail_traces=1, n_success_traces=1,
    )
    assert unavailable.traces == ()
    assert not unavailable.sufficient


def test_m03_full_slots_retain_missing_weight_and_original_aggregate() -> None:
    rows = project_trial_rows(
        (TaskPlan("a", (1.0, 1.0)), TaskPlan("b", (1.0, 1.0))),
        (
            TrialObservation("a", 0, 1.0, 100),
            TrialObservation("a", 1, 0.0, None),
            TrialObservation("b", 0, 0.5, 300),
            TrialObservation("b", 1, 0.0, 0),
        ),
    )
    result = aggregate_trials(rows)
    assert result.score == 0.375
    assert result.cost == 200
    assert result.expected_slots == 4
    assert result.known_token_total == 400
    assert result.token_coverage == 0.75
    assert not result.cost_comparable

    missing = project_trial_rows(
        (TaskPlan("x", (2.0, 3.0)),),
        (TrialObservation("x", 0, 1.0, 10),),
    )
    assert (missing[1].reward, missing[1].weight, missing[1].missing) == (
        0.0, 3.0, True)
    assert aggregate_trials(missing).score == 0.4


def test_m04_unknown_or_zero_cost_has_frozen_relative_behavior() -> None:
    assert relative_cost_change(None, 100) == 0
    assert relative_cost_change(100, None) == 0
    assert relative_cost_change(0, 100) == 0
    assert relative_cost_change(100, 0) == 0
    assert relative_cost_change(80, 100) == -0.2


def test_m05_ties_keep_first_candidate_even_with_unknown_cost() -> None:
    incumbent = Incumbent("h0", 0.5, 200, True)
    candidates = (
        CandidateEvaluation("v0", 0.6, None, False, True, 1),
        CandidateEvaluation("v1", 0.6, 100, True, True, 1),
    )
    decision = select_candidate(
        incumbent, candidates, delta=0.1, score_star=0.5,
        parameters=SelectionParameters(0.1, 1.0, 1.0, 1.0, 0.5))
    assert decision.selected_id == "v0"
    assert not decision.selected_cost_comparable
    assert decision.eligible_candidate_ids == ("v0", "v1")


def test_m06_floor_is_inclusive_and_cost_and_guards_are_required() -> None:
    incumbent = Incumbent("h0", 1.0, 100, True)
    decision = select_candidate(incumbent, (
        CandidateEvaluation("below", 0.799999, 90, True, True),
        CandidateEvaluation("cost-fail", 2.0, 500, True, True),
        CandidateEvaluation("guard-fail", 2.0, 90, True, False),
        CandidateEvaluation("floor", 0.8, 90, True, True),
    ), delta=0.2, score_star=1.0,
        parameters=SelectionParameters(0.0, 0.0, 0.0, 1.0, 0.0))
    assert decision.selected_id == "floor"
    assert decision.eligible_candidate_ids == ("floor",)


def test_m07_history_rounding_measured_tried_and_accepted_projection() -> None:
    unmeasured = HistoryEntry(
        0, "gate", "routing", "gate edit", "critic_reject", False,
        None, None, None, None,
    )
    measured = HistoryEntry(
        0, "loser", "routing", "measured edit", "lost", False,
        0.12345678, -0.23456789, 0.98765432, 123.456,
    )
    accepted = HistoryEntry(
        1, "winner", "memory", "accepted edit", "selected", True,
        0.2, 0.1, 1.2, 101.05,
    )
    history = History().append(unmeasured, measured, accepted)
    assert measured.delta_score == 0.123457
    assert measured.delta_cost == -0.234568
    assert measured.score == 0.987654
    assert measured.cost == 123.5
    assert history.measured({"routing"}) == (measured,)
    assert history.tried({"routing", "memory"}) == frozenset({"routing", "memory"})
    assert [row.edit for row in history.accepted_edits()] == ["accepted edit"]
    assert history.incumbent_component_counts() == {"memory": 1}
    with pytest.raises(FrozenInstanceError):
        measured.score = 0.0  # type: ignore[misc]


def test_m07_render_uses_recent_40_and_at_most_four_unmeasured() -> None:
    entries = tuple(
        HistoryEntry(
            index, f"c{index}", "x", None, "gate", False,
            None if index % 2 == 0 else float(index), None, None, None,
        )
        for index in range(50)
    )
    rendered = History(entries).render_window()
    assert all(row.round_index >= 10 for row in rendered)
    assert [row.round_index for row in rendered if row.delta_score is None] == [
        42, 44, 46, 48,
    ]


def test_m11_no_evaluated_or_no_eligible_candidate_keeps_incumbent() -> None:
    incumbent = Incumbent("h0", 1.0, None, False)
    parameters = SelectionParameters(0.1, 1.0, 1.0, 1.0, 0.0)
    assert not select_candidate(
        incumbent, (), delta=0.1, score_star=1.0,
        parameters=parameters).adopted
    decision = select_candidate(incumbent, (
        CandidateEvaluation("v0", 0.0, None, False, True),
    ), delta=0.1, score_star=1.0, parameters=parameters)
    assert decision.selected_id == "h0"
    assert not decision.adopted


def test_m12_m13_acceptance_is_explicit_and_old_history_is_unchanged() -> None:
    old = History().append(HistoryEntry(
        0, "v0", "routing", "edit-0", "lost", False,
        -0.1, 0.0, 0.9, 100,
    ))
    revised = old.append(HistoryEntry(
        1, "v1", "routing", "edit-1", "selected", True,
        0.1, 0.0, 1.1, 100,
    ))
    assert len(old.entries) == 1
    assert [row.candidate_id for row in revised.accepted_edits()] == ["v1"]


def test_heldout_rows_cannot_enter_evolution_or_history_projection() -> None:
    with pytest.raises(ValueError, match="heldout"):
        project_trial_rows(
            (TaskPlan("secret", (1.0,)),), (), split=DataSplit.HELDOUT)
    with pytest.raises(ValueError, match="heldout"):
        HistoryEntry(
            0, "secret", None, None, "selected", True,
            1.0, 0.0, 1.0, 1.0, DataSplit.HELDOUT,
        )
    with pytest.raises(ValueError, match="heldout"):
        project_trial_rows(
            (TaskPlan("secret", (1.0,)),), (), split="heldout")
    with pytest.raises(ValueError, match="heldout"):
        HistoryEntry(
            0, "secret", None, None, "selected", True,
            1.0, 0.0, 1.0, 1.0, "heldout",
        )


def test_string_evolve_split_is_normalized() -> None:
    rows = project_trial_rows(
        (TaskPlan("task", (1.0,)),),
        (TrialObservation("task", 0, 1.0, 5),),
        split="evolve",
    )
    assert rows[0].split is DataSplit.EVOLVE
    entry = HistoryEntry(
        0, "candidate", None, None, "lost", False,
        0.0, 0.0, 1.0, 5.0, "evolve",
    )
    assert entry.split is DataSplit.EVOLVE


def test_selection_uses_historical_score_star_and_public_cost_rule() -> None:
    parameters = SelectionParameters(0.1, 1.0, 1.0, 1.0, 0.0)
    incumbent = Incumbent("incumbent", 0.8, 100, True)

    below_historical_floor = select_candidate(
        incumbent,
        (CandidateEvaluation("candidate", 0.84, 80, True, True),),
        delta=0.05, score_star=0.9, parameters=parameters)
    assert below_historical_floor.selected_id == "incumbent"

    cheaper_tie = select_candidate(
        incumbent,
        (CandidateEvaluation("candidate", 0.8, 90, True, True),),
        delta=0.05, score_star=0.8, parameters=parameters)
    assert cheaper_tie.selected_id == "candidate"


def test_calibration_uses_public_seeded_bootstrap_when_repeats_are_degenerate() -> None:
    result = calibrate_noise_band(
        (1.0, 1.0), ((1.0, 1.0), (1.0, 1.0)))

    assert result["delta"] == 0.0
    assert result["method"] == "bootstrap over pooled trials"
    assert result["bootstrap_seed"] == 7
    assert result["bootstrap_repetitions"] == 2000


def test_calibration_selects_repeated_evaluations_when_nondegenerate() -> None:
    result = calibrate_noise_band(
        (0.0, 1.0), ((0.0, 1.0), (1.0, 1.0)))

    assert result["delta"] > 0
    assert result["method"] == "repeated base evaluations"
