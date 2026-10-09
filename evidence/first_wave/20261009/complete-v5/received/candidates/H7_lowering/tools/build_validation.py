import json
from pathlib import Path
root = Path(__file__).resolve().parent.parent
identity = json.loads((root / 'SOURCE_IDENTITY.json').read_text())
runs = []; distinct = set()
for name in ('final-author', 'compatibility', 'final-core'):
    evidence = root / 'evidence'
    data = json.loads((evidence / f'{name}-results.json').read_text())
    before = json.loads((evidence / f'{name}-before.json').read_text())
    after = json.loads((evidence / f'{name}-after.json').read_text())
    assert before == after and before['manifest_sha256'] == identity['source_manifest_sha256']
    assert data['exit_status'] == 0 and not data['sentinel_blocked_events']
    passed = {r['nodeid'] for r in data['outcomes'] if r['when'] == 'call' and r['outcome'] == 'passed'}
    assert passed == set(data['collected']) and all(r['outcome'] == 'passed' for r in data['outcomes'])
    overlap = distinct & passed; distinct |= passed
    runs.append({'name': name, 'passed': len(passed), 'overlap_with_prior': sorted(overlap),
                 'source_manifest_sha256': before['manifest_sha256']})
result = {'source_manifest_sha256': identity['source_manifest_sha256'], 'runs': runs,
          'unique_passed_count': len(distinct), 'unique_passed_nodeids': sorted(distinct),
          'native': 'NOT_RUN', 'full_execution_inventory': 'UNSUPPORTED',
          'independent_review': 'reported separately, not included in this author count'}
(root / 'evidence/FINAL_TEST_INVENTORY.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k:v for k,v in result.items() if k != 'unique_passed_nodeids'}, indent=2))
