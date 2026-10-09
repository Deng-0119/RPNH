"""Exact finite START/SETTLE relation delegated to production marking semantics."""
from __future__ import annotations

from collections import Counter
from dataclasses import fields, replace
from itertools import combinations, product
import uuid

from ..marking import MarkingStateError
from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.normal_root_token_allocation import NORMAL_ROOT_TOKEN_SCHEME
from .contracts import (ActiveOccurrence, ActionBinding, ActionEvidence, AnalysisState,
                        BindingEnumeration, Successor, SupportResult, canonical_json)
from .projection import restore_base, restore_state, runtime_net


def _refs(state):
    return tuple(t.token_ref for t in state.base.tokens)


def _model(analysis_input, transition_id):
    operation = next(t.operation for t in analysis_input.compiled.symbolic.transitions if t.name == transition_id)
    return next(m for m in analysis_input.operation_models if m.operation_name == operation)


def check_supported_semantics(analysis_input):
    compiled = analysis_input.compiled
    symbolic = compiled.symbolic
    reasons = []
    policy = analysis_input.policy
    for name in ("max_states", "max_edges", "max_depth", "max_bindings"):
        value = getattr(policy, name)
        if type(value) is not int or value < 1:
            reasons.append("invalid_budget:" + name)
    if type(policy.max_seconds) not in (float, int) or policy.max_seconds <= 0:
        reasons.append("invalid_budget:max_seconds")
    if analysis_input.ordinary_token_ref_scheme not in (None, NORMAL_ROOT_TOKEN_SCHEME):
        reasons.append("unsupported_token_allocation_scheme")
    if symbolic.reset_arcs:
        reasons.append("reset_arcs")
    if symbolic.variable_resource_arcs or symbolic.logical_slots:
        reasons.append("variable_lease_or_logical_updates")
    places = {place.name: place for place in symbolic.places}
    for arc in symbolic.arcs:
        # A null proposal is meaningful only for a declared control output.
        # Ordinary read/lease returns are handled by production from exact inputs.
        # Data forwarding requires a separate exact-resource model; v1 declines it.
        place = places[arc.place]
        if (arc.direction == "output"
                and place.token_kind not in {"agent_resource", "resource_lease"}
                and place.channel != "control"):
            reasons.append("unmodeled_data_product:" + arc.transition + ":" + arc.place)
        if (arc.effect_selector is not None or arc.emit in {"route_selected", "lease_mint"}
                or arc.lease_claim_set is not None or arc.lease_claims or arc.lease_claim_exclusions):
            reasons.append("selected_effect_or_dynamic_lease:" + arc.transition)
    models = {m.operation_name: m for m in analysis_input.operation_models}
    if len(models) != len(analysis_input.operation_models):
        reasons.append("duplicate_operation_model")
    for op in symbolic.operations:
        if any(outcome.effects for outcome in op.outcomes):
            reasons.append("host_effects:" + op.name)
        model = models.get(op.name)
        if model is None:
            reasons.append("missing_operation_model:" + op.name)
            continue
        if not model.revision or not model.fidelity_assumption:
            reasons.append("unversioned_or_unassumed_model:" + op.name)
        cases = model.cases
        if len({c.case_id for c in cases}) != len(cases) or any(not c.case_id for c in cases):
            reasons.append("duplicate_or_empty_case:" + op.name)
        if {c.outcome_id for c in cases} != {o.name for o in op.outcomes}:
            reasons.append("incomplete_outcome_coverage:" + op.name)
        for case in cases:
            if len({p.place for p in case.produced}) != len(case.produced):
                reasons.append("multiple_proposals_per_place:" + case.case_id)
            if any(p.resource_ref is not None or p.work_resource_ref is not None or p.kind is not None
                   or p.output_port_id is not None or p.output_place_ref is not None
                   or p.output_binding_ref is not None for p in case.produced):
                reasons.append("unverified_product_authority:" + case.case_id)
    if set(models) - {o.name for o in symbolic.operations}:
        reasons.append("model_for_unknown_operation")
    env, scheduler = analysis_input.environment_contract, analysis_input.scheduler_contract
    if not env.closed or env.external_wait:
        reasons.append("open_environment_or_external_wait")
    if scheduler.selection != "any-exact-binding":
        reasons.append("scheduler_requires_any-exact-binding")
    if scheduler.fairness != "none":
        reasons.append("fairness_not_supported")
    if any(t.continuation is not None or t.override_warning is not None or t.lease_claims
           for t in analysis_input.state.base.tokens):
        reasons.append("continuation_override_or_variable_claims")
    return SupportResult(not reasons, tuple(dict.fromkeys(reasons)),
        tuple(m.fidelity_assumption + " [" + m.operation_name + "@" + m.revision + "]"
              for m in analysis_input.operation_models),
        ("fixed-net", "exact-token-identity", "weighted-consume", "ordinary-read-return",
         "static-lease-read-borrow-return", "bool-string-colours", "reader-all-counts",
         "finite-outcomes", "concurrent-occurrences", "analytical-unpublished-refs"))


def check_state_safety(analysis_input, state):
    reasons = []
    try:
        restore_state(analysis_input, state)
    except (TypeError, ValueError, KeyError, MarkingStateError) as exc:
        reasons.append("invalid_typed_marking_or_active_claim:" + str(exc))
    counts = Counter(t.place for t in state.base.tokens if t.consumed_by is None and t.epoch == state.base.epoch)
    for place in analysis_input.compiled.symbolic.places:
        if place.capacity is not None and counts[place.name] > place.capacity:
            reasons.append(f"capacity_exceeded:{place.name}:{counts[place.name]}>{place.capacity}")
    return tuple(reasons)


def enabled_bindings(analysis_input, state):
    support = check_supported_semantics(analysis_input)
    if not support.supported:
        return BindingEnumeration((), False, support.reasons)
    local = restore_state(analysis_input, state)
    actions = []
    # Every occurrence and every finite case remains a distinct choice.
    for occurrence in state.active:
        for case in _model(analysis_input, occurrence.transition_id).cases:
            actions.append(ActionBinding("SETTLE", occurrence.transition_id,
                firing_ref=occurrence.firing_ref, case_id=case.case_id))
    examined = 0
    for transition_id in sorted(local._net.transitions):
        required = Counter()
        for place, _, weight in local._claim_input_arcs(transition_id):
            required[place] += weight
        for place, target, weight in local._net.lease_reference_arcs:
            if target == transition_id:
                required[place] += weight
        pools = [combinations(tuple(t.token_id for t in local._fresh_on(place, transition_id)), weight)
                 for place, weight in sorted(required.items())]
        for groups in product(*pools):
            if examined >= analysis_input.policy.max_bindings:
                return BindingEnumeration(tuple(actions), False, ("max_bindings",), examined)
            examined += 1
            selected_ids = set(i for group in groups for i in group)
            # Never filter by unrestricted is_enabled: a later exact binding can work.
            candidate = local._try_reserve(transition_id, set(), allowed_token_ids=selected_ids)
            if candidate is None or set((*candidate.token_ids, *candidate.reference_token_ids)) != selected_ids:
                continue
            if not local._is_enabled_locked(transition_id, allowed_token_ids=selected_ids):
                continue
            refs = tuple(sorted((t.token_ref for t in local._tokens if t.token_id in selected_ids),
                                key=lambda r: (r.entity_type, str(r.entity_id), str(r.version_id))))
            actions.append(ActionBinding("START", transition_id, refs))
    return BindingEnumeration(tuple(actions), True, (), examined)


def _proposed_firing(analysis_input, state, binding):
    # Identity material changes behavior: no history/identity quotient is used.
    material = "pn-analysis-proposed:" + canonical_json((analysis_input.net_ref, state, binding))
    return VersionRef("transition_firing/v1",
        TypedId("transition_firing", uuid.uuid5(uuid.NAMESPACE_URL, material).hex),
        TypedId("transition_firing_version", uuid.uuid5(uuid.NAMESPACE_URL, material + ":version").hex))


def successors(analysis_input, state, binding):
    support = check_supported_semantics(analysis_input)
    if not support.supported:
        raise ValueError("unsupported semantics: " + ",".join(support.reasons))
    local = restore_state(analysis_input, state)
    if binding.kind == "START":
        if binding.firing_ref is not None or binding.case_id is not None:
            raise ValueError("START cannot select a settlement")
        claim_id = state.next_claim_id
        refs = local._require_exact_registered_claim_refs(binding.transition_id, binding.token_refs)
        local._install_exact_registered_claim_locked(binding.transition_id, refs,
            verify_enabled=True, allow_siblings=True)
        claim = local._active_claims[claim_id]
        firing_ref = _proposed_firing(analysis_input, state, binding)
        local.bind_active_claim(claim_id, firing_ref)
        attempt = max([local.attempt(binding.transition_id),
            *(a.attempt_index for a in state.active if a.transition_id == binding.transition_id)]) + 1
        occurrence = ActiveOccurrence(firing_ref, claim_id, binding.transition_id, claim.epoch,
            attempt, claim.token_refs, claim.reference_token_refs, claim.lease_accesses)
        after = AnalysisState(state.base, (*state.active, occurrence), local._next_claim_id)
        return (Successor(after, ActionEvidence(binding, _refs(state), _refs(after), occurrence)),)
    if binding.kind != "SETTLE" or binding.token_refs:
        raise ValueError("invalid settlement binding")
    occurrence = next((a for a in state.active if a.firing_ref == binding.firing_ref
                       and a.transition_id == binding.transition_id), None)
    if occurrence is None:
        raise ValueError("settlement firing is not active")
    case = next((c for c in _model(analysis_input, occurrence.transition_id).cases if c.case_id == binding.case_id), None)
    if case is None:
        raise ValueError("settlement case is not modeled")
    view = local.singleton_settlement_view(occurrence.firing_ref)
    candidate_json = None
    evidence = ActionEvidence(binding, _refs(state), (), occurrence, case.outcome_id)
    try:
        operation_tokens = view.claimed_operation_tokens(occurrence.transition_id, occurrence.claim_epoch,
                                                       instance_key=occurrence.firing_ref)
        selected = local._net.for_outcome(occurrence.transition_id, case.outcome_id).for_claimed_inputs(
            occurrence.transition_id, tuple((t.place, t.verdict) for t in operation_tokens))
        view._net = selected
        expected = {p for p in selected.outputs_of(occurrence.transition_id)
                    if selected.place_kind(p) not in {"agent_resource", "resource_lease"}}
        supplied = {p.place for p in case.produced}
        if expected != supplied:
            raise ValueError(f"modeled_output_coverage:expected={sorted(expected)} supplied={sorted(supplied)}")
        produced = [{**{f.name: getattr(spec, f.name) for f in fields(spec)}, "continuation": None}
                    for spec in case.produced]
        _, off_arc = view.deposit_outputs(occurrence.transition_id, produced, claim_epoch=occurrence.claim_epoch)
        if off_arc:
            raise ValueError("modeled_output_off_arc")
        view.record_settled_attempt(occurrence.transition_id, occurrence.attempt_index)
        candidate_json = canonical_json(view._validated_candidate_mapping())
        snapshot = view.pure_typed_snapshot(analysis_input.executable,
            proposed_net_ref=(analysis_input.net_ref if analysis_input.executable is None else None),
            ordinary_token_ref_scheme=analysis_input.ordinary_token_ref_scheme,
            allocation_firing_ref=(occurrence.firing_ref if analysis_input.ordinary_token_ref_scheme else None))
        successor_local = restore_base(local._net, snapshot)
        local.carry_active_claims_to(successor_local, settled_instance_key=occurrence.firing_ref)
        after = AnalysisState(snapshot, tuple(a for a in state.active if a.firing_ref != occurrence.firing_ref),
                              successor_local._next_claim_id)
        evidence = replace(evidence, after_refs=_refs(after))
        errors = check_state_safety(analysis_input, after)
        if errors:
            return (Successor(None, evidence, errors, candidate_json=candidate_json),)
        return (Successor(after, evidence),)
    except (TypeError, ValueError, KeyError, MarkingStateError) as exc:
        if candidate_json is None:
            candidate_json = canonical_json({"case": case, "state": state})
        return (Successor(None, evidence, ("invalid_successor:" + str(exc),), candidate_json=candidate_json),)
