"""Preflight reuses consumed facts, never authority or mutable markings."""
from dataclasses import replace

import pytest

from cpn.rpnh.firing_preflight import preflight_module_firing, preflight_module_firing_set
from cpn.rpnh.marking import TeamNetMarking, MarkingStateError
from cpn.rpnh.pn_validation.projection import runtime_net
from cpn.rpnh.registry.resources import TypedMarkingAuthority, PetriTokenAuthority, RegistryHead
from test_pn_validation_semantics import make_input, ref


def preflight_fixture(n=2, **kwargs):
    # Explicit finite-test DTO only, not a published Registry authority.
    analysis = make_input(occurrences=n, **kwargs)
    state = analysis.state.base
    head = RegistryHead(0, 0, 0, {})
    authority = TypedMarkingAuthority(ref('marking_checkpoint', 300), analysis.net_ref,
        ref('team_design_root', 301), state.epoch, state.next_token_id, state.attempts,
        tuple(t.token_ref for t in state.tokens),
        tuple(PetriTokenAuthority(t.token_ref, t, head) for t in state.tokens),
        None, None, (), head)
    leases = tuple(t.token_ref for t in state.tokens if t.place == 'step.lease')
    occurrences = tuple(('step.run', tuple(sorted((t.token_ref, *leases),
        key=lambda r: (r.entity_type, str(r.entity_id), str(r.version_id)))))
        for t in state.tokens if t.place == 'step.request')
    return runtime_net(analysis), authority, occurrences


@pytest.mark.parametrize('n', [1, 2, 4, 8])
def test_one_full_restore_per_occurrence_no_repeated_claim(n, monkeypatch):
    structure, authority, occurrences = preflight_fixture(n)
    original = TeamNetMarking.from_authority.__func__
    calls = []
    def counted(cls, *args, **kwargs):
        local = original(cls, *args, **kwargs)
        calls.append(local)
        return local
    monkeypatch.setattr(TeamNetMarking, 'from_authority', classmethod(counted))
    result = preflight_module_firing_set(structure, authority, occurrences=occurrences)
    assert len(calls) == n and len({id(local) for local in calls}) == n
    assert len(result.firings) == n
    assert result.checkpoint_ref == authority.checkpoint_ref
    assert all(t.state.consumed_by is None for t in authority.tokens)


@pytest.mark.parametrize('kwargs', [{}, {'ordinary_read': True}, {'lease': True}, {'outcomes': ('complete', 'failure')}])
def test_batch_permits_equal_independent_public_single_firings(kwargs):
    structure, authority, occurrences = preflight_fixture(**kwargs)
    expected = tuple(preflight_module_firing(structure, authority,
        transition_id=t, claimed_token_refs=refs) for t, refs in occurrences)
    actual = preflight_module_firing_set(structure, authority, occurrences=occurrences)
    assert actual.firings == expected


def test_all_single_permit_failures_precede_batch_duplicate_check():
    structure, authority, occurrences = preflight_fixture()
    first = occurrences[0]
    invalid = ('step.run', (ref('petri_token', 999),))
    with pytest.raises(MarkingStateError, match='claims are absent from the predecessor'):
        preflight_module_firing_set(structure, authority, occurrences=(first, first, invalid))
    with pytest.raises(MarkingStateError, match='repeats one consumed occurrence'):
        preflight_module_firing_set(structure, authority, occurrences=(first, first))


def test_mismatched_predecessor_does_not_reuse_an_earlier_call():
    structure, authority, occurrences = preflight_fixture()
    preflight_module_firing_set(structure, authority, occurrences=occurrences)
    with pytest.raises(MarkingStateError, match='differs from the adopted net/marking'):
        preflight_module_firing_set(structure, replace(authority, net_ref=ref('net_instance', 999)), occurrences=occurrences)
