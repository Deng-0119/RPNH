"""Published-result documentation checks; no model or harness execution."""
from pathlib import Path
import csv
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def read_rows(name):
    with (ROOT / 'comparison' / name).open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def test_public_results_are_the_reference_not_a_pending_local_run():
    value = json.loads((ROOT / 'comparison/manifest.json').read_text())
    assert value['status'] == 'published_reference_comparison_complete'
    assert value['reference_source']['paper'] == 'arXiv:2605.14271v2'
    assert value['reference_source']['table'] == 'Table 2'
    assert value['planned_reference_runs'] == 0
    assert not value['local_reference_implementation_required']
    assert not value['publication_requires_new_experiments']
    assert value['new_executor_calls'] == value['new_judge_calls'] == 0


def test_public_table_preserves_ten_distinct_configurations():
    rows = read_rows('public_reference_results.csv')
    assert len(rows) == len({(r['harness'], r['model']) for r in rows}) == 10
    assert {r['harness'] for r in rows} == {'OpenClaw', 'Claude Code', 'Codex'}
    for row in rows:
        assert row['source_table'] == 'Table 2'
        assert row['source_url'].startswith('https://arxiv.org/html/2605.14271v2')
        assert 'not the five-task' in row['scope']
        for key in ('sar_tool', 'sar_resource', 'sar_flow', 'sar_avg', 'avs', 'tcr'):
            assert 0 <= float(row[key]) <= 1


def test_transcribed_selected_reference_values():
    rows = {(r['harness'], r['model']): r for r in read_rows('public_reference_results.csv')}
    expected = {
        ('OpenClaw', 'ChatGPT-5.4'): ('0.66', '0.50', '0.53'),
        ('Codex', 'ChatGPT-5.4'): ('0.76', '0.50', '0.34'),
        ('Claude Code', 'Claude Opus 4.6'): ('0.82', '0.51', '0.43'),
        ('OpenClaw', 'Gemini 3.1 Pro'): ('0.56', '0.56', '0.77'),
    }
    for key, scores in expected.items():
        assert tuple(rows[key][m] for m in ('tcr', 'avs', 'sar_avg')) == scores


def test_historical_groups_are_separate_and_unchanged_in_scope():
    rows = read_rows('rpnh_historical_groups.csv')
    assert len(rows) == 6
    assert sum(int(r['rpnh_n']) for r in rows) == 15
    assert len({r['rpnh_condition_id'] for r in rows}) == 2
    assert len({r['task_id'] for r in rows}) == 5
    assert not any('delta' in key or 'reference' in key for key in rows[0])
    assert not (ROOT / 'comparison/historical_vs_original.csv').exists()


def test_retired_plan_cannot_be_read_as_execution_instructions():
    assert 'withdrawn' in (ROOT / 'BASELINE_PROTOCOL.md').read_text()
    assert '全部撤销' in (ROOT / 'BASELINE_PROTOCOL_ZH.md').read_text()
    for name in ('COMPARISON.md', 'COMPARISON_ZH.md', 'README.md', 'README_ZH.md'):
        assert 'oai-reference-unmetered-v1' not in (ROOT / name).read_text()


def test_comparison_pages_have_working_local_links():
    names = ['COMPARISON.md', 'COMPARISON_ZH.md', 'IMPLEMENTATION_COMPARISON.md',
             'IMPLEMENTATION_COMPARISON_ZH.md', 'BASELINE_PROTOCOL.md', 'BASELINE_PROTOCOL_ZH.md']
    for name in names:
        text = (ROOT / name).read_text()
        for link in re.findall(r'\]\(([^)]+)\)', text):
            if '://' in link or link.startswith('#'):
                continue
            assert (ROOT / link.split('#')[0]).exists(), (name, link)
    for name in ('README', 'DESIGN', 'RESULTS'):
        for lang in ('', '_ZH'):
            text = (ROOT / f'{name}{lang}.md').read_text()
            assert 'COMPARISON' in text
            assert 'BASELINE_PROTOCOL' not in text


def test_no_claim_of_matched_superiority_or_invented_l3():
    value = json.loads((ROOT / 'comparison/manifest.json').read_text())
    assert value['performance_superiority_established'] is False
    assert value['matched_score_deltas_provided'] is False
    rows = read_rows('rpnh_historical_groups.csv')
    assert not any(k in rows[0] for k in ('pb', 'overall', 'relative_gain'))


def test_sources_preserve_paper_version_and_prior_code_provenance():
    data = json.loads((ROOT / 'comparison/source_index.json').read_text())
    assert len(data['external_sources']) == 13
    assert len(data['public_result_sources']) == 3
    assert data['public_result_sources'][0]['version'] == '2605.14271v2'
    for row in data['external_sources']:
        assert ('/blob/' + data['harnessaudit_commit'] + '/' in row['url']
                or '/blob/' + data['rpnh_commit'] + '/' in row['url'])
        assert row['symbols']
