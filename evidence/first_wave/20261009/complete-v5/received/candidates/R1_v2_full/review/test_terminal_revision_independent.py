"""Successor mapping checks; no Registry, callback, socket or model execution."""
from itertools import product
import json
from pathlib import Path
import pytest
from cpn.rpnh.iteration_profile import IterationProfile, compile_iteration_profile
from cpn.rpnh.executable_net import load_compiled_net
from test_inert_profile_independent import document, registration


@pytest.mark.parametrize('rounds', [1, 2, 32])
@pytest.mark.parametrize('values', list(product(('complete', 'failed'), repeat=3)))
def test_all_explicit_classification_combinations(rounds, values):
    data = document(rounds)
    mapping = dict(zip(('stop', 'final_select', 'final_retain'), values))
    data['terminal_outcomes'] = mapping.copy()
    prepared = compile_iteration_profile(IterationProfile.from_dict(data), registration=registration(data))
    terminals = (prepared.module.terminal, *prepared.module.terminal_alternatives)
    assert len(terminals) == rounds + 2
    for terminal in terminals:
        key = 'stop' if terminal.outcome == 'stop' else 'final_' + terminal.outcome
        assert terminal.config == {'run_outcome': mapping[key]}
    assert next(t for t in terminals if t.source.component == f'round_{rounds}_selector' and t.outcome == 'stop').config == {'run_outcome': mapping['stop']}
    assert load_compiled_net(prepared.compiled.to_json()).to_dict() == prepared.compiled.to_dict()
    data['terminal_outcomes']['stop'] = 'invalid-after-construction'
    assert prepared.profile.to_dict()['terminal_outcomes'] == mapping


@pytest.mark.parametrize('mapping', [None, {}, {'stop': 'complete'},
    {'stop': 'complete', 'final_select': 'complete', 'final_retain': 'unknown'},
    {'stop': 'complete', 'final_select': 'complete', 'final_retain': True},
    {'stop': 'complete', 'final_select': 'complete', 'final_retain': 'complete', 'owner_stop': 'complete'},
])
def test_missing_or_ambiguous_classification_is_rejected(mapping):
    data = document()
    if mapping is None: data.pop('terminal_outcomes')
    else: data['terminal_outcomes'] = mapping
    with pytest.raises((ValueError, RuntimeError)):
        IterationProfile.from_dict(data)


@pytest.mark.parametrize('rounds', [1, 2, 3])
def test_successor_topology_preserves_exact_frozen_source_except_declared_terminal_config(rounds):
    data = document(rounds)
    prepared = compile_iteration_profile(IterationProfile.from_dict(data), registration=registration(data))
    old = json.loads((Path(__file__).parent / f'frozen-module-rounds-{rounds}.json').read_text())
    updated = prepared.module.to_dict()
    for terminal in (updated['terminal'], *updated['terminal_alternatives']):
        terminal['config'] = {}
    assert updated == old
