"""Pure exact DTO projection; no Registry writer or fabricated checkpoint."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from types import MappingProxyType

from ..executable_net import CompiledPetriNet
from ..marking import TeamNetMarking, Token, validate_typed_marking_state
from ..registry.resources import TypedMarkingAuthority, TypedMarkingSnapshot
from ..runtime_net import RuntimeNet
from .contracts import (AnalysisInput, AnalysisState, canonical_json)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return replace(value, **{f.name: _freeze(getattr(value, f.name)) for f in fields(value)})
    return value


def runtime_net(analysis_input):
    return RuntimeNet(analysis_input.compiled, net_ref=analysis_input.net_ref,
                      resource_plan=analysis_input.resource_plan)


def restore_base(net, snapshot):
    """Restore only local state using production validation and payload codecs."""
    validate_typed_marking_state(net, epoch=snapshot.epoch,
        next_token_id=snapshot.next_token_id, attempts=snapshot.attempts,
        tokens=snapshot.tokens, require_token_refs=True)
    if any(t.consumed_by is not None or t.epoch != snapshot.epoch for t in snapshot.tokens):
        raise ValueError("analysis base must retain fresh reserved tokens without overlay stamps")
    marking = TeamNetMarking(net)
    tokens = []
    for state in snapshot.tokens:
        values = {f.name: getattr(state, f.name) for f in fields(state)}
        c = state.continuation
        values["continuation"] = None if c is None else {"round": c.round, "source_ref": c.source_ref}
        w = state.override_warning
        values["override_warning"] = None if w is None else {
            "reason": w.reason, "gate": [w.gate_transition_id, w.gate_peer_transition_id],
            "overridden_attempt": w.overridden_attempt,
            "escalation_provenance": w.escalation_provenance, "gate_output_place": w.gate_output_place}
        values["lease_claims"] = tuple({
            "lease_identity_ref": c.lease_identity_ref, "expected_resource_ref": c.expected_resource_ref,
            "access_mode": c.access_mode, **({"staging_place": c.staging_place} if c.staging_place is not None else {})
        } for c in state.lease_claims)
        tokens.append(Token(**values))
    marking._restore_candidate_mapping({"epoch": snapshot.epoch, "next_id": snapshot.next_token_id,
        "attempts": {a.transition_id: a.highest_issued for a in snapshot.attempts},
        "tokens": [marking._token_payload(t) for t in tokens]})
    return marking


def restore_state(analysis_input, state):
    local = restore_base(runtime_net(analysis_input), state.base)
    ids = [a.claim_id for a in state.active]
    if (type(state.next_claim_id) is not int or state.next_claim_id < 1
            or len(set(ids)) != len(ids) or any(type(i) is not int or i < 1 or i >= state.next_claim_id for i in ids)
            or len({a.firing_ref for a in state.active}) != len(state.active)):
        raise ValueError("invalid active occurrence counters or duplicate identities")
    for occurrence in state.active:
        if (occurrence.claim_epoch != state.base.epoch or type(occurrence.attempt_index) is not int
                or occurrence.attempt_index < 1):
            raise ValueError("invalid active occurrence epoch/attempt")
        local._next_claim_id = occurrence.claim_id
        refs = local._require_exact_registered_claim_refs(occurrence.transition_id,
            (*occurrence.consumed_refs, *occurrence.reference_refs))
        local._install_exact_registered_claim_locked(occurrence.transition_id, refs,
            verify_enabled=False, allow_siblings=True)
        actual = local._active_claims[occurrence.claim_id]
        if (actual.token_refs != occurrence.consumed_refs
                or actual.reference_token_refs != occurrence.reference_refs
                or actual.lease_accesses != occurrence.lease_accesses):
            raise ValueError("active occurrence differs from exact consume/reference/lease split")
        local.bind_active_claim(occurrence.claim_id, occurrence.firing_ref)
    local._next_claim_id = state.next_claim_id
    return local


def build_analysis_input(compiled, marking_preview, resource_plan, operation_models,
        terminal_contract, environment_contract, scheduler_contract, policy, **source_context):
    if not isinstance(compiled, CompiledPetriNet):
        raise TypeError("analysis requires exact CompiledPetriNet")
    # Store every caller source binding, rejecting data that cannot be bound canonically.
    context = dict(source_context)
    executable = context.pop("executable", None)
    net_ref = context.pop("net_ref", None) or (executable.net_ref if executable is not None else None)
    scheme = context.pop("ordinary_token_ref_scheme", None)
    if isinstance(marking_preview, TypedMarkingAuthority):
        if net_ref is not None and marking_preview.net_ref != net_ref:
            raise ValueError("marking/net binding mismatch")
        net_ref = marking_preview.net_ref
        context.setdefault("checkpoint_ref", marking_preview.checkpoint_ref)
        context.setdefault("previous_checkpoint_ref", marking_preview.previous_checkpoint_ref)
        context.setdefault("marking_authority", marking_preview)
        marking_preview = TypedMarkingSnapshot(marking_preview.epoch, marking_preview.next_token_id,
            marking_preview.attempts, tuple(t.state for t in marking_preview.tokens))
    state = marking_preview if isinstance(marking_preview, AnalysisState) else AnalysisState(marking_preview)
    if not isinstance(state.base, TypedMarkingSnapshot):
        raise TypeError("analysis marking preview must be a typed snapshot/state/authority")
    if net_ref is None:
        raise ValueError("analysis requires exact net_ref or executable")
    if executable is not None and executable.net_ref != net_ref:
        raise ValueError("executable/net binding mismatch")
    result = AnalysisInput(canonical_json(compiled.to_dict()), net_ref, _freeze(resource_plan),
        _freeze(state), tuple(_freeze(m) for m in operation_models), _freeze(terminal_contract),
        _freeze(environment_contract), _freeze(scheduler_contract), _freeze(policy),
        canonical_json(context), _freeze(executable), scheme)
    canonical_json(result)
    restore_state(result, result.state)
    return result
