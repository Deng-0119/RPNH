"""Verify bundled source and exact S1 dependency without product imports or I/O beyond files."""
from pathlib import Path
import hashlib
import json
import sys

package = Path(__file__).resolve().parents[1]
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def rows(root):
    return [{'path': p.relative_to(root).as_posix(), 'size': p.stat().st_size,
             'sha256': sha(p)} for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts]
def aggregate(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
identity = json.loads((package / 'SOURCE_IDENTITY.json').read_text())
frozen = json.loads((package / identity['source_manifest']).read_text())
candidate = rows(package / 'source')
assert candidate == frozen['files'], 'Candidate file content/size/path mismatch'
assert aggregate(candidate) == identity['source_manifest_sha256'] == frozen['manifest_sha256']
assert len(candidate) == identity['source_file_count'] == 992
assert sha(package / 'H7-core.patch') == identity['H7_patch_sha256']
assert sha(package / 'inputs/S1-source-identity.json') == identity['S1_source_identity_sha256']
assert sha(package / 'inputs/S1-static-lease-reads.patch') == identity['S1_patch_sha256']
s1_identity = json.loads((package / 'inputs/S1-source-identity.json').read_text())
s1_root = package / 'inputs/S1-source'
if len(sys.argv) > 1:
    s1_root = Path(sys.argv[1]).resolve()
s1 = rows(s1_root)
expected_s1 = sorted(s1_identity['candidate_files'], key=lambda x: x['path'])
assert s1 == expected_s1, 'S1 file content/size/path mismatch'
assert len(s1) == identity['S1_file_count'] == 979
print(json.dumps({'status': 'PASS', 'candidate_files': len(candidate),
    'candidate_aggregate_sha256': aggregate(candidate), 'S1_files': len(s1),
    'S1_aggregate_sha256': aggregate(s1), 'H7_patch_sha256': identity['H7_patch_sha256']}, indent=2))
