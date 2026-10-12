"""Rebuild the four identity exports from their source declarations, offline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from cpn.frontend.dashboard import public_net
from examples._support.declaration_identity import compile_example_declaration, export_declaration_identity
from examples.view_declaration import DeclarationFixture

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ('erp_bench', 'automationbench', 'harnessaudit_office', 'slopcodebench')
VIEW_NAMES = ('erp-bench', 'automationbench', 'harnessaudit-office', 'slopcodebench')


def read(path):
    return json.loads(path.read_text())


@pytest.fixture(scope='module', params=EXAMPLES)
def declaration(request):
    name = request.param
    figures = ROOT/'examples'/name/'figures'
    public_input = read(figures/'identity-declaration-input.json') if name == 'harnessaudit_office' else None
    compiled = compile_example_declaration(name, public_task=public_input)
    return name, figures, compiled


def test_export_reproduces_bundled_identity_without_changing_original_net(declaration):
    name, figures, compiled = declaration
    historical = read(figures/'historical/pre-identity-export/declaration-frame.json')
    before = deepcopy(historical)
    rebuilt = export_declaration_identity(historical, compiled)
    current = read(figures/'declaration-frame.json')
    assert historical == before
    assert historical['agent_nodes'] == []
    assert current['agent_nodes'] == rebuilt['agent_nodes']
    assert current['coverage'] == rebuilt['coverage']
    expected = 4 if name == 'harnessaudit_office' else 1
    assert len(current['agent_nodes']) == expected
    for agent in current['agent_nodes']:
        assert agent['source'] == 'declared_llm_executor'
        assert agent['agent_ref'] is agent['executable_binding_ref'] is None
        assert agent['semantic_group']['component'] == 'team'
    assert current['net']['nodes'] == rebuilt['net']['nodes']
    assert current['source']['identity_export']['runtime_evidence'] is False
    assert current['source']['identity_export']['scope'] == 'declaration_only'
    assert public_net(rebuilt['net']) == historical['net']
    assert rebuilt['boundaries'] == historical['boundaries']
    assert rebuilt['transition_bindings'] == []
    assert rebuilt['coverage']['firings'] == 'not_provided'
    assert rebuilt['coverage']['history'] == 'unsupported'
    assert all('runtime' not in node for node in current['net']['nodes'])
    for node in current['net']['nodes']:
        if node['kind'] == 'transition':
            assert set(node['executor_declaration']) == {'contracts'}
            assert node['executor_declaration']['contracts'] == {'transport':'llm'}
            assert set(node['config']) == {'semantic_node_id'}
    fixture = DeclarationFixture(VIEW_NAMES[EXAMPLES.index(name)])
    assert fixture.dashboard() == current
    assert fixture() == current['net']


def test_historical_bytes_and_old_figure_provenance_remain_verifiable(declaration):
    _, figures, _ = declaration
    original = figures/'historical/pre-identity-export'
    old_provenance = read(original/'source-provenance.json')
    viewer = read(figures/'viewer-export-provenance.json')
    assert hashlib.sha256((original/'declaration-frame.json').read_bytes()).hexdigest() == old_provenance['frame_sha256'] == viewer['source_frame_sha256']
    current_provenance = read(figures/'source-provenance.json')
    assert hashlib.sha256((figures/'declaration-frame.json').read_bytes()).hexdigest() == current_provenance['frame_sha256']
    assert current_provenance['official_viewer_export']['source_frame'] == 'historical/pre-identity-export/declaration-frame.json'
    for source in old_provenance['sources']:
        assert hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest() == source['sha256']


def test_export_rejects_unmatched_paths_boundaries_and_runtime_frames(declaration):
    _, figures, compiled = declaration
    historical = read(figures/'historical/pre-identity-export/declaration-frame.json')
    for field in ('source', 'mode', 'weight', 'outcome'):
        changed = deepcopy(historical)
        changed['net']['edges'][0][field] = 'mismatch'
        with pytest.raises(ValueError, match='differs'):
            export_declaration_identity(changed, compiled)
    changed = deepcopy(historical)
    changed['boundaries']['terminal_rules'][0]['outcome'] = 'mismatch'
    with pytest.raises(ValueError, match='differs'):
        export_declaration_identity(changed, compiled)
    changed = deepcopy(historical)
    changed['source']['mode'] = 'registry_current'
    with pytest.raises(ValueError, match='declaration-only'):
        export_declaration_identity(changed, compiled)
    changed = deepcopy(historical)
    changed['transition_bindings'] = [{'transition_id':'some-binding'}]
    with pytest.raises(ValueError, match='declaration-only'):
        export_declaration_identity(changed, compiled)


def test_harnessaudit_requires_explicit_public_input_and_rejects_grader_fields():
    with pytest.raises(ValueError, match='public task input'):
        compile_example_declaration('harnessaudit_office')
    with pytest.raises(ValueError, match='grader fields'):
        compile_example_declaration('harnessaudit_office', public_task={'ground_truth_tool_paths':[]})
