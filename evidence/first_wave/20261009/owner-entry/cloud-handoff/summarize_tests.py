from pathlib import Path
import json
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent
names = ['focused-tests.xml', 'agent-task-tests.xml', 'boundary-final-tests.xml',
         'stop-final-tests.xml', 'review/independent-focused.xml',
         'review/independent-stop-compat.xml']
batches = []
unique = {}
for name in names:
    tree = ET.parse(root / name).getroot()
    cases = []
    for case in tree.iter('testcase'):
        state = ('failed' if case.find('failure') is not None else
                 'error' if case.find('error') is not None else
                 'skipped' if case.find('skipped') is not None else 'passed')
        key = (case.get('classname'), case.get('name'))
        cases.append({'classname': key[0], 'name': key[1], 'outcome': state})
        unique.setdefault(key, []).append({'batch': name, 'outcome': state})
    batches.append({'xml': name, 'tests': len(cases),
        'passed': sum(c['outcome'] == 'passed' for c in cases),
        'failed': sum(c['outcome'] == 'failed' for c in cases),
        'errors': sum(c['outcome'] == 'error' for c in cases),
        'skipped': sum(c['outcome'] == 'skipped' for c in cases),
        'suite_attributes': [s.attrib for s in tree.iter('testsuite')], 'cases': cases})
expected = set()
for line in (root / 'final-collection.log').read_text().splitlines():
    if '.py::' in line:
        path, name = line.split('::', 1)
        expected.add((path[:-3].replace('/', '.'), name))
missing = expected - unique.keys()
unexpected = unique.keys() - expected
assert not missing and not unexpected, (missing, unexpected)
assert all(record['outcome'] == 'passed' for records in unique.values() for record in records)
result = {'source_base_commit': '674252feb836f631c162979f177d1fe91f22559f',
    'verdict': 'PASS_FOCUSED_PIPE_SEMANTICS_NATIVE_BLOCKED',
    'distinct_testcases': len(unique), 'passed_distinct_testcases': len(unique),
    'batch_testcase_executions': sum(b['tests'] for b in batches),
    'deduplication_key': ['classname', 'name'], 'batches': batches,
    'cases': [{'classname': key[0], 'name': key[1], 'runs': records}
              for key, records in sorted(unique.items())],
    'collection_timing': 'Main focused batch collected 32 tests before final early-stop/factory additions. Final supplements and independent runs cover the final added or revised tests; all 39 final collected identities are covered.',
    'native_transport': {'status': 'BLOCKED_ENV', 'log': 'native-attempt.log',
        'failure': 'AF_UNIX socket creation PermissionError: [Errno 1] Operation not permitted'},
    'preserved_nonfinal_errors': [
        {'log': 'agent-task-plugin-setup-error.log', 'stage': 'plugin setup before tests'},
        {'xml': 'stop-test-collection-error.xml', 'stage': 'collection before tests'},
        {'xml': 'stop-fixture-error.xml', 'tests': 2, 'passed': 1, 'failed': 1,
         'reason': 'The test double was unhashable; fixed to HostPort and both tests rerun.'}],
    'real_provider_model_calls': 0, 'full_repository_suite_run': False,
    'docker_actions_push_merge': False}
(root / 'test-results.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({key: result[key] for key in ['verdict', 'distinct_testcases', 'batch_testcase_executions']}))
