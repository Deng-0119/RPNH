"""Deterministic projections of the frozen RRSI method.

This module contains no provider or harness calls.  Selection implements the
public RRSI score floor and cost rule; domain-specific non-compensatory guards
remain explicit booleans supplied by the application adapter.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import math
import random
import statistics
from typing import Iterable


class DataSplit(str, Enum):
    EVOLVE = "evolve"
    CALIBRATION = "calibration"
    HELDOUT = "heldout"


@dataclass(frozen=True, slots=True)
class TaskPlan:
    task_id: str
    weights: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TrialObservation:
    task_id: str
    trial_index: int
    reward: float
    tokens: int | float | None
    trace_ref: str | None = None


@dataclass(frozen=True, slots=True)
class TrialSlot:
    task_id: str
    trial_index: int
    reward: float
    weight: float
    tokens: int | float | None
    missing: bool
    trace_ref: str | None
    split: DataSplit


def project_trial_rows(
    plans: Iterable[TaskPlan],
    observations: Iterable[TrialObservation],
    *,
    split: DataSplit | str = DataSplit.EVOLVE,
) -> tuple[TrialSlot, ...]:
    """Fill every planned task/trial slot, retaining weight on missing slots."""
    try:
        normalized_split = DataSplit(split)
    except ValueError as exc:
        raise ValueError("unknown data split") from exc
    if normalized_split == DataSplit.HELDOUT:
        raise ValueError("heldout rows cannot enter the evolution projection")

    plan_rows = tuple(plans)
    if len({plan.task_id for plan in plan_rows}) != len(plan_rows):
        raise ValueError("task plans must have unique task ids")

    planned_keys = {
        (plan.task_id, trial_index)
        for plan in plan_rows
        for trial_index in range(len(plan.weights))
    }
    observed: dict[tuple[str, int], TrialObservation] = {}
    for item in observations:
        key = (item.task_id, item.trial_index)
        if key not in planned_keys:
            raise ValueError(f"observation is not a planned slot: {key!r}")
        if key in observed:
            raise ValueError(f"duplicate observation for planned slot: {key!r}")
        observed[key] = item

    result: list[TrialSlot] = []
    for plan in plan_rows:
        for trial_index, weight in enumerate(plan.weights):
            key = (plan.task_id, trial_index)
            item = observed.get(key)
            result.append(TrialSlot(
                task_id=plan.task_id,
                trial_index=trial_index,
                reward=0.0 if item is None else item.reward,
                weight=weight,
                tokens=None if item is None else item.tokens,
                missing=item is None,
                trace_ref=None if item is None else item.trace_ref,
                split=normalized_split,
            ))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class AggregateResult:
    score: float | None
    cost: float | None
    expected_slots: int
    missing_slots: int
    known_token_slots: int
    positive_token_slots: int
    token_coverage: float
    known_token_total: float
    cost_comparable: bool


def aggregate_trials(slots: Iterable[TrialSlot]) -> AggregateResult:
    """Apply upstream S/C formulas and add non-selector coverage metadata."""
    rows = tuple(slots)
    denominator = sum(row.weight for row in rows)
    score = (
        sum(row.reward * row.weight for row in rows) / denominator
        if rows and denominator > 0
        else None
    )
    known_tokens = tuple(row.tokens for row in rows if row.tokens is not None)
    positive_tokens = tuple(value for value in known_tokens if value > 0)
    cost = (
        sum(positive_tokens) / len(positive_tokens)
        if positive_tokens
        else None
    )
    expected = len(rows)
    known_count = len(known_tokens)
    return AggregateResult(
        score=score,
        cost=cost,
        expected_slots=expected,
        missing_slots=sum(row.missing for row in rows),
        known_token_slots=known_count,
        positive_token_slots=len(positive_tokens),
        token_coverage=known_count / expected if expected else 0.0,
        known_token_total=float(sum(known_tokens)),
        cost_comparable=(
            expected > 0 and known_count == expected and cost is not None
        ),
    )


def relative_cost_change(
    candidate_cost: int | float | None,
    incumbent_cost: int | float | None,
) -> float:
    """Frozen upstream behavior: absent or zero cost on either side means 0."""
    if candidate_cost is None or incumbent_cost is None:
        return 0.0
    if candidate_cost == 0 or incumbent_cost == 0:
        return 0.0
    return (candidate_cost - incumbent_cost) / incumbent_cost


def calibrate_noise_band(
    scores: Iterable[float],
    pooled_trials: Iterable[tuple[float, float]],
    *,
    z: float = 2.0,
    repetitions: int = 2000,
    seed: int = 7,
) -> dict[str, float | int | str | list[float]]:
    """Apply the public repeated-evaluation/bootstrap calibration rule."""
    score_rows = tuple(float(item) for item in scores)
    trials = tuple((float(reward), float(weight))
                   for reward, weight in pooled_trials)
    if not score_rows or not trials or repetitions < 2:
        raise ValueError("calibration requires scores, trials, and repetitions")
    if z < 0 or any(weight <= 0 for _, weight in trials):
        raise ValueError("calibration weights and z must be positive")

    direct = (statistics.stdev(score_rows) * math.sqrt(2)
              if len(score_rows) >= 2 else 0.0)
    rng = random.Random(seed)
    boot: list[float] = []
    for _ in range(repetitions):
        sampled = [trials[rng.randrange(len(trials))]
                   for _ in range(len(trials))]
        denominator = sum(weight for _, weight in sampled)
        boot.append(sum(reward * weight for reward, weight in sampled)
                    / denominator)
    standard_error = statistics.pstdev(boot)
    bootstrap_null = math.sqrt(2) * standard_error
    fallback_exercised = direct <= 0
    selected = bootstrap_null if fallback_exercised else direct
    return {
        "delta": round(z * selected, 6),
        "z": z,
        "sd_null": round(selected, 6),
        "sd_null_bootstrap": round(bootstrap_null, 6),
        "se_bootstrap": round(standard_error, 6),
        "method": ("bootstrap over pooled trials"
                   if fallback_exercised else "repeated base evaluations"),
        "n_evals": len(score_rows),
        "bootstrap_repetitions": repetitions,
        "bootstrap_seed": seed,
        "S_per_eval": list(score_rows),
    }


@dataclass(frozen=True, slots=True)
class TraceTrial:
    reward: float
    trace_ref: str | None


@dataclass(frozen=True, slots=True)
class TraceTask:
    task_id: str
    mean: float
    trials: tuple[TraceTrial, ...]


@dataclass(frozen=True, slots=True)
class RepresentativeTrace:
    task_id: str
    trial_index: int
    reward: float
    trace_ref: str
    group: str


@dataclass(frozen=True, slots=True)
class TraceSelection:
    traces: tuple[RepresentativeTrace, ...]
    minimum_available: float
    sufficient: bool


def build_representative_traces(
    per_task: Iterable[TraceTask],
    *,
    planned_task_ids: Iterable[str],
    n_fail_traces: int,
    n_success_traces: int,
) -> TraceSelection:
    """Project ``Run.build_traces`` ordering and first min/max trial ties."""
    if n_fail_traces < 0 or n_success_traces < 0:
        raise ValueError("trace counts must be non-negative")
    tasks = tuple(per_task)
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("per-task rows must have unique task ids")
    task_ids = tuple(planned_task_ids)
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("planned task ids must be unique")

    ordered = sorted(tasks, key=lambda task: task.mean)
    low_tasks = ordered[:n_fail_traces]
    low_ids = {task.task_id for task in low_tasks}
    high_tasks = [
        task for task in ordered[-n_success_traces:]
        if task.task_id not in low_ids
    ] if n_success_traces else []

    selected: list[RepresentativeTrace] = []
    for group, chosen_tasks, choose_reward in (
        ("failure", low_tasks, min),
        ("success", high_tasks, max),
    ):
        for task in chosen_tasks:
            if not task.trials:
                continue
            target_reward = choose_reward(trial.reward for trial in task.trials)
            trial_index = next(
                index
                for index, trial in enumerate(task.trials)
                if trial.reward == target_reward
            )
            trial = task.trials[trial_index]
            if trial.trace_ref is None:
                continue
            selected.append(RepresentativeTrace(
                task_id=task.task_id,
                trial_index=trial_index,
                reward=trial.reward,
                trace_ref=trial.trace_ref,
                group=group,
            ))

    minimum = 0.5 * min(
        len(task_ids), n_fail_traces + n_success_traces,
    )
    traces = tuple(selected)
    return TraceSelection(
        traces=traces,
        minimum_available=minimum,
        sufficient=len(traces) >= minimum,
    )


@dataclass(frozen=True, slots=True)
class Incumbent:
    candidate_id: str
    score: float
    cost: float | None
    cost_comparable: bool


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: str
    score: float
    cost: float | None
    cost_comparable: bool
    domain_guards_passed: bool
    novelty: int = 0


@dataclass(frozen=True, slots=True)
class SelectionParameters:
    beta0: float
    beta1: float
    w_s: float
    w_c: float
    w_n: float


def cost_rule_passes(
    incumbent: Incumbent,
    candidate: CandidateEvaluation,
    *,
    delta: float,
    parameters: SelectionParameters,
) -> bool:
    delta_score = candidate.score - incumbent.score
    delta_cost = relative_cost_change(candidate.cost, incumbent.cost)
    if delta_score > delta:
        return delta_cost <= parameters.beta0 + parameters.beta1 * delta_score
    shaped = (
        parameters.w_s * delta_score
        - parameters.w_c * delta_cost
        + parameters.w_n * candidate.novelty)
    return shaped > 0


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    selected_id: str
    selected_score: float
    selected_cost: float | None
    selected_cost_comparable: bool
    adopted: bool
    eligible_candidate_ids: tuple[str, ...]


def select_candidate(
    incumbent: Incumbent,
    candidates: Iterable[CandidateEvaluation],
    *,
    delta: float,
    score_star: float,
    parameters: SelectionParameters,
) -> SelectionDecision:
    """Apply public RRSI Algorithm 2 and select the first highest-score row."""
    if delta < 0 or score_star < incumbent.score:
        raise ValueError("selection floor inputs are inconsistent")
    eligible: list[CandidateEvaluation] = []
    floor = score_star - delta
    for candidate in candidates:
        if (candidate.score >= floor and cost_rule_passes(
                incumbent, candidate, delta=delta, parameters=parameters)
                and candidate.domain_guards_passed):
            eligible.append(candidate)

    best: CandidateEvaluation | None = None
    for candidate in eligible:
        if best is None or candidate.score > best.score:
            best = candidate
    if best is None:
        return SelectionDecision(
            selected_id=incumbent.candidate_id,
            selected_score=incumbent.score,
            selected_cost=incumbent.cost,
            selected_cost_comparable=incumbent.cost_comparable,
            adopted=False,
            eligible_candidate_ids=(),
        )
    return SelectionDecision(
        selected_id=best.candidate_id,
        selected_score=best.score,
        selected_cost=best.cost,
        selected_cost_comparable=best.cost_comparable,
        adopted=True,
        eligible_candidate_ids=tuple(item.candidate_id for item in eligible),
    )


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    round_index: int
    candidate_id: str
    component: str | None
    edit: str | None
    outcome: str
    accepted: bool
    delta_score: float | None
    delta_cost: float | None
    score: float | None
    cost: float | None
    split: DataSplit | str = DataSplit.EVOLVE

    def __post_init__(self) -> None:
        try:
            normalized_split = DataSplit(self.split)
        except ValueError as exc:
            raise ValueError("unknown data split") from exc
        object.__setattr__(self, "split", normalized_split)
        if normalized_split == DataSplit.HELDOUT:
            raise ValueError("heldout rows cannot enter method history")
        for name in ("delta_score", "delta_cost", "score"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, round(value, 6))
        if self.cost is not None:
            object.__setattr__(self, "cost", round(self.cost, 1))


@dataclass(frozen=True, slots=True)
class AcceptedEdit:
    round_index: int
    candidate_id: str
    component: str
    edit: str
    delta_score: float | None
    delta_cost: float | None
    score: float | None
    cost: float | None


@dataclass(frozen=True, slots=True)
class History:
    entries: tuple[HistoryEntry, ...] = ()

    def append(self, *entries: HistoryEntry) -> "History":
        """Return a new history; earlier evidence is never overwritten."""
        return History(self.entries + tuple(entries))

    def measured(self, components: Iterable[str]) -> tuple[HistoryEntry, ...]:
        allowed = frozenset(components)
        return tuple(
            entry for entry in self.entries
            if entry.delta_score is not None and entry.component in allowed
        )

    def tried(self, components: Iterable[str]) -> frozenset[str]:
        return frozenset(
            entry.component
            for entry in self.measured(components)
            if entry.component is not None
        )

    def accepted_edits(self) -> tuple[AcceptedEdit, ...]:
        return tuple(
            AcceptedEdit(
                round_index=entry.round_index,
                candidate_id=entry.candidate_id,
                component=entry.component,
                edit=entry.edit,
                delta_score=entry.delta_score,
                delta_cost=entry.delta_cost,
                score=entry.score,
                cost=entry.cost,
            )
            for entry in self.entries
            if entry.accepted
            and entry.component is not None
            and entry.edit is not None
        )

    def incumbent_component_counts(self) -> dict[str, int]:
        return dict(Counter(edit.component for edit in self.accepted_edits()))

    def render_window(self) -> tuple[HistoryEntry, ...]:
        """Return the recent 40 entries, retaining at most 4 unmeasured rows."""
        recent = self.entries[-40:]
        unmeasured_indexes = [
            index for index, entry in enumerate(recent)
            if entry.delta_score is None
        ]
        keep_unmeasured = frozenset(unmeasured_indexes[-4:])
        return tuple(
            entry for index, entry in enumerate(recent)
            if entry.delta_score is not None or index in keep_unmeasured
        )
