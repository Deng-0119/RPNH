"""Summarize completed combined-source gates without rerunning tests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-h2a-validation-20261008'
REPO = ROOT / '.t26/s'
PACKET = TASK / 'rpnh-taskcontrol-reader-convergence'
H1 = ROOT / 'task-owner-entry-validation-20261008/RPNH_Owner_Entry_Local_Validation_20261008'
PY = ROOT / '.p26/v/bin/python'
BATCHES = {'h2-focused': 47, 'h2-compatibility': 44, 'h2-shared': 15,
           'h2-independent': 12, 'h2-native-status': 7,
           'h2-native-callbacks': 2, 'h1-classic': 3, 'h1-focused': 36}


def read(name):
    return json.loads((TASK / name).read_text())


def main():
    windows, executed, product, external = [], [], set(), set()
    for label, count in BATCHES.items():
        command = read('logs/' + label + '.json')
        assert command['exit_code'] == 0, label
        parsed = ET.parse(TASK / 'logs' / (label + '.xml'))
        suite = parsed.find('testsuite')
        assert int(suite.attrib['tests']) == count
        assert all(int(suite.attrib[k]) == 0 for k in ('failures', 'errors', 'skipped'))
        ids = []
        for case in parsed.iter('testcase'):
            classname = case.attrib['classname']
            if label == 'h2-independent':
                path = 'package/independent-review/' + classname.rsplit('.', 1)[-1] + '.py'
            else:
                path = classname.replace('.', '/') + '.py'
            ident = path + '::' + case.attrib['name']
            ids.append(ident)
            (external if label == 'h2-independent' else product).add(ident)
        assert len(ids) == count
        executed.extend(ids)
        windows.append({'id': label, 'status': 'PASS', 'tests': count,
                        'time_seconds': float(suite.attrib['time']),
                        'junit': 'logs/' + label + '.xml', 'test_ids': ids})
    assert len(executed) == len(set(executed)) == 166
    assert len(product) == 154 and len(external) == 12
    h1_expected = json.loads((H1 / 'review/repo-relative-test-inventory.json').read_text())
    h1_ids = {i for w in windows if w['id'] in ('h1-focused', 'h1-classic') for i in w['test_ids']}
    assert h1_ids == set(h1_expected['final_focused_product_suite']) | set(h1_expected['additional_existing_product_tests'])
    for label in ('preflight', 'native-cli', 'readback-before', 'native-readback',
                  'readback-after', 'cli-readback-audit', 'agent-task-ledgers'):
        assert read('logs/' + label + '.json')['exit_code'] == 0, label
    before = read('source-before.json')
    after = read('source-after.json')
    assert before == after, 'Tested checkout changed during execution'
    sockets = [str(p.relative_to(ROOT)) for name in ('f', 'a', 'c', 'n', 'k')
               for p in (ROOT / '.t26' / name).rglob('*') if stat.S_ISSOCK(p.lstat().st_mode)]
    assert not sockets, sockets
    ledgers = read('logs/agent-task-ledgers.stdout.log')
    assert [r['scripted_logical_ledger'] for r in ledgers['runs']] == [[2, 0], [3, 0], [3, 0]]
    audit = read('native-cli-readback-audit.json')
    assert audit['status'] == 'PASS' and audit['stable_fields_deep_equal']
    summary = {
        'schema_version': 'rpnh/h2a_combined_local_validation/v1',
        'status': 'PASS_NATIVE_FINITE', 'validated_at_utc': datetime.now(timezone.utc).isoformat(),
        'historical_h2a_base': 'ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4',
        'actual_combined_head': before['head'], 'head_git_tree': before['head_git_tree'],
        'tested_worktree_manifest_sha256': before['worktree_manifest_sha256'],
        'tested_manifest_entries': len(before['entries']), 'source_before_equals_after': True,
        'h1_frozen_seven_verified': True, 'h2a_frozen_five_verified': True,
        'product_repairs': [], 'unique_test_count': 166, 'test_executions': 166,
        'repository_test_count': 154, 'package_independent_test_count': 12,
        'deduplication': 'Full repo-relative nodeid; package probes use package-relative paths. Three classic AgentTask tests run once and satisfy both H1 and H2a gates.',
        'h1_unique': 39, 'h2a_unique': 130, 'shared_classic_test_ids': 3,
        'windows': windows, 'real_provider_requests': 0, 'pipeline_counts': [0, 0],
        'classic_post_resume_scripted_ledgers': [[2, 0], [3, 0], [3, 0]],
        'residual_native_sockets': sockets,
        'cloud_history': {
            'status_attempt': '6 PASS / 1 BLOCKED_ENV at AF_UNIX construction',
            'resume_attempt': 'BLOCKED_ENV before provider dispatch and resume assertions',
            'old_red': '20 expected baseline failures; not rerun',
            'initial_fixture_failures': 'Retained unchanged relative to received ZIP',
            'distribution_transformations': 'Prior cloud sanitization is documented by the received packaging/DISTRIBUTION_PROVENANCE.json; received bytes are preserved.'},
        'scope_limits': ['Finite focused validation, not a whole-repository run',
                         'Status batch includes pure tests; not all 166 are socket integrations',
                         'No pipe or alternative transport; no install or security changes',
                         'Synthetic imported/spy counts and scripted calls are not real model usage',
                         'No extra OS handler identity probe; classic SIGINT/resume and existing lifecycle assertions only',
                         'Earlier A1 environment block remains historical and was not retested',
                         'No Actions, Docker, external providers or business benchmark'],
        'delivery_authority': 'Standing owner authorization: commit and push accepted code and reviewed evidence to origin/main; no return ZIP requested.'}
    (TASK / 'LAYERED_MANIFEST.json').write_text(json.dumps(summary, indent=2) + '\n')
    (TASK / 'test-inventory.json').write_text(json.dumps({
        'status': summary['status'], 'unique_count': 166, 'execution_count': 166,
        'repository_ids': sorted(product), 'package_ids': sorted(external),
        'windows': windows, 'deduplication': summary['deduplication']}, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in ('windows', 'cloud_history', 'scope_limits')}))


if __name__ == '__main__':
    main()
