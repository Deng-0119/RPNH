import json
from pathlib import Path
root = Path(__file__).resolve().parent.parent
author = json.loads((root / 'evidence/FINAL_TEST_INVENTORY.json').read_text())
review = json.loads((root / 'review/TEST_COUNTS.json').read_text())
def normalized(nodeid):
    path, *rest = nodeid.split('::')
    if path.endswith('.py'): path = path[:-3]
    return '::'.join([path.replace('/', '.'), *rest])
a = {normalized(v) for v in author['unique_passed_nodeids']}
r = {row['id'] for run in review['runs'] for row in run['cases'] if row['outcome'] == 'passed'}
assert a <= r and len(a) == 721 and len(r) == 768
result = {'source_manifest_sha256': author['source_manifest_sha256'],
    'author_unique_passed': len(a), 'reviewer_unique_passed': len(r),
    'author_reviewer_overlap': len(a & r), 'joint_unique_passed': len(a | r),
    'new_author_cases': 28, 'new_independent_cases': 46, 'additional_ordinary_revision_regression': 1,
    'prior_298_input_cases_added': False, 'early_executions_added': False,
    'author_reviewer_common_cases': sorted(a & r), 'review_only_cases': sorted(r - a),
    'deselected_native_subprocess_cases': 1, 'extra_cases_not_collected_missing_input': 6,
    'full_execution_inventory': 'UNSUPPORTED', 'native': 'NOT_RUN',
    'decision': 'ACCEPT_LIMITED_OFFLINE_CANDIDATE'}
(root / 'JOINT_TEST_COUNTS.json').write_text(json.dumps(result, indent=2) + '\n')
print({k:v for k,v in result.items() if not isinstance(v,list)})
