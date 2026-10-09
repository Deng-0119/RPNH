"""Read-only source reconciliation for the candidate-gate review."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'baseline'
SOURCE = ROOT / 'source'

def digest(path):
    raw = path.read_bytes()
    return {'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}

def files(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob('*')
            if p.is_file() and not {'__pycache__','.pytest_cache','.git'} & set(p.parts)}

old, new = files(BASELINE), files(SOURCE)
main_tree = json.loads((ROOT / 'evidence/main-tree.json').read_text())
upstream_blobs = {item['path']: item['sha'] for item in main_tree['tree'] if item['type'] == 'blob'}
assert not main_tree['truncated']
assert all(digest(path)['git_blob'] == upstream_blobs[name] for name, path in old.items())
changed = sorted(k for k in old.keys() & new.keys() if old[k].read_bytes() != new[k].read_bytes())
added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
allowed_product = ['cpn/frontend/opencode_compatibility.v1.json',
                   'cpn/frontend/opencode_launcher.py', 'cpn/frontend/opencode_protocol.py']
assert [p for p in changed if p.startswith('cpn/')] == allowed_product
assert not [p for p in added+removed if p.startswith('cpn/')]
assert not removed
baseline_manifest = json.loads((BASELINE / allowed_product[0]).read_text())
source_manifest = json.loads((SOURCE / allowed_product[0]).read_text())
assert source_manifest.pop('certification_candidates')
assert source_manifest == baseline_manifest
frozen_paths = sorted(p for p in old if p.startswith('cpn/') and p not in allowed_product)
assert all(old[p].read_bytes() == new[p].read_bytes() for p in frozen_paths)
for p in ['tests/test_opencode_pty.py','tests/test_opencode_registry_integration.py','tests/test_opencode_frontend.py','tests/test_opencode_cli.py','tests/test_opencode_application_boundary.py']:
    assert old[p].read_bytes() == new[p].read_bytes()
report = {'rpnh_commit':'8dd360e4848912a998dbd83220c3f0ce0a1caa86',
          'baseline_blob_verifications':len(old),
          'changed':changed, 'added':added, 'removed':removed,
          'product_changed':allowed_product,
          'all_existing_manifest_content_unchanged':True,
          'frozen_product_files':{p:digest(new[p]) for p in frozen_paths},
          'frozen_source_files':{p:digest(new[p]) for p in sorted(new)},
          'native_g2':'NOT_RUN','native_g3':'NOT_RUN'}
(ROOT / 'independent-review/frozen-scope.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'changed':changed,'added':added,'removed':removed,
                  'frozen_product_count':len(frozen_paths),'source_files':len(new)},indent=2))
