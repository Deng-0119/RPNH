"""Summarize frozen attempt evidence; no Registry imports, writes or test runs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>')
OUT = Path(__file__).resolve().parent
FINAL = OUT / 'a004'


def load(path):
    return json.loads(path.read_text())


def main():
    result = load(FINAL / 'result.json')
    assert result['status'] != 'RUNNING' and len(result['scenes']) == 10
    result['final_attempt'] = 'a004'
    result['distinct_final_test_cases'] = 10
    result['acceptance_gate_count'] = 7
    result['history'] = []
    monitors = []
    pids = []
    for attempt in sorted(OUT.glob('a[0-9][0-9][0-9]')):
        r = load(attempt / 'result.json')
        cases = [c for p in attempt.glob('g*.xml') for c in ET.parse(p).findall('.//testcase')]
        actual = [c for c in cases if c.get('name', '').startswith('test_g')]
        result['history'].append(dict(attempt=attempt.name, status=r['status'],
            case_status_counts=r['case_status_counts'], pytest_commands=len(r['scenes']),
            actual_test_cases=len(actual), collection_errors=len(cases) - len(actual),
            result_ref=str(attempt / 'result.json'), frozen_probe=str(attempt / 'test_native_s1.py')))
        monitors.extend(json.loads(line) for line in (attempt / 'resource-monitor.jsonl').read_text().splitlines())
        pids.extend(row['pid'] for row in r['scenes'] if 'pid' in row)
    result['historical_failures_reclassified_as_pass'] = False
    result['history_counts'] = dict(
        pytest_commands=sum(r['pytest_commands'] for r in result['history']),
        actual_test_cases=sum(r['actual_test_cases'] for r in result['history']),
        collection_errors=sum(r['collection_errors'] for r in result['history']))
    metrics = ('label', 'head', 'active', 'lease_count', 'lease_minted_count',
               'token_minted_count', 'firing_count', 'settlements', 'completions',
               'model_counts', 'deterministic_host_body_count', 'marking_checkpoint')
    for row in result['scenes']:
        row['observation_summary'] = []
        for path in sorted(FINAL.glob(row['scene'] + '*.json')):
            data = load(path)
            if not isinstance(data, list):
                continue
            for observed in data:
                item = {key: observed[key] for key in metrics}
                item['source_ref'] = str(path)
                item['lease_refs_and_states'] = observed['leases']
                item['active_claims'] = observed['active_claims']
                item['net_ref'] = observed['af_unix_response']['result']['net_ref']
                item['pending_edits'] = observed['af_unix_response']['result']['pending_edits']
                row['observation_summary'].append(item)
        if row['scene'].startswith('g5-'):
            first, last = row['observation_summary'][0], row['observation_summary'][-1]
            row['read_head_advanced'] = last['head'] > first['head']
            row['read_head_delta'] = last['head'] - first['head']
        row['setup_classification'] = (
            'direct Registry seed Success; native callback count excludes seed' if row['scene'] in ('g5-union', 'g5-produce', 'g7')
            else 'direct Registry bad/good carrier Successes and exact admission; native settlement separate' if row['scene'] == 'g6'
            else 'direct Registry fixture setup; runtime via original Harness/OwnerEventLoop')
    observed_models = [row['model_counts'] for scene in result['scenes'] for row in scene['observation_summary']]
    assert observed_models and all(counts == [0, 0] for counts in observed_models)
    result['model_counts'] = [0, 0]
    result['model_count_scope'] = 'Every final observed Registry; counts not summed across repeated snapshots.'
    cleanup = [load(p) for p in FINAL.glob('g*-cleanup.json')]
    result['deterministic_host_body_count'] = sum(row['body_count'] for row in cleanup)
    dirs = sorted((ROOT / '.v26').glob('unS1-*'))
    sockets = [str(p) for d in dirs for p in d.rglob('*') if p.is_socket()]
    result['cleanup'] = dict(all_final_socket_closes_confirmed=all(c['socket_removed'] for c in cleanup),
        socket_cleanup_receipts=len(cleanup), residual_sockets=sockets,
        pytest_pids_still_present=[pid for pid in pids if Path('/proc', str(pid)).exists()],
        worker_processes_started=0, parallel_host_threads_proven=False,
        retained_runtime_folders=[str(d.relative_to(ROOT)) for d in dirs],
        all_original_databases_retained=True, compression_owner='parent after evidence extraction')
    result['resource_monitor_bounds'] = dict(
        windows_D_free_min_bytes=min(m['windows_D_free_bytes'] for m in monitors),
        memory_available_min_bytes=min(m['memory_available_bytes'] for m in monitors),
        task_plus_v26_max_bytes=max(m['task_plus_v26_bytes'] for m in monitors),
        native_runtime_max_bytes=max(m['native_runtime_bytes'] for m in monitors),
        all_samples_within_limits=all(m['allowed'] for m in monitors))
    result['g7_failure_analysis'] = load(OUT / 'g7-provenance-readback.json')
    result['g7_gate_parts'] = dict(reset='PASS_DIRECT_COMPILER',
        ordinary_retirement='PASS_NATIVE_HOST_REJECTION', active_replacement='PASS_DRAINING',
        complete_native_adoption_lifecycle='FAIL_TERMINAL_PROVENANCE',
        remaining_post_release_probe_assertions='NOT_RUN: original Harness completion_error stopped dependent checks',
        readback='direct read-only provenance inspection, not a substitute native pass')
    result['probe_correction_records'] = sorted(str(p) for p in OUT.glob('fixture-correction-*.json'))
    result['slot_free'] = not result['cleanup']['pytest_pids_still_present']
    result['extracted_utc'] = datetime.now(timezone.utc).isoformat()
    path = OUT / 'result.json'
    assert path.resolve().is_relative_to(OUT) and not path.is_symlink() and path.stat().st_nlink == 1
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'distinct_final_test_cases', 'case_status_counts',
        'history_counts', 'model_counts', 'deterministic_host_body_count', 'slot_free', 'resource_monitor_bounds')}, indent=2))


if __name__ == '__main__':
    main()
