"""Parent byte fidelity and in-memory known-secret rescan; no data printed."""
from pathlib import Path
from collections import Counter
import hashlib
import importlib.util
import json
import os
import sys

ROOT = Path('/home/deng123/RPNH').resolve()
TASK = ROOT / 'task-first-wave-examples-20261008'
RUN = ROOT / '.s26/scb01'
BASE = TASK / 'work/scb-publication'
CANDIDATE = BASE / 'candidate'
spec = importlib.util.spec_from_file_location('scb_collector_review', BASE / 'collect.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
redactor = module.Redactor()
for name in ('selection.json', 'adapter.json', 'original-adapter.json'):
    path = RUN / 'profile' / name
    if path.exists():
        redactor.inspect_profile(json.loads(path.read_bytes()))
redactor.inspect_proxies(os.environ)
manifest = json.loads((CANDIDATE / 'MANIFEST.json').read_bytes())
assert manifest['originals_unchanged'] and not manifest['collection_errors'] and not manifest['exclusions']
for path in CANDIDATE.rglob('*'):
    assert path.resolve().is_relative_to(ROOT) and not path.is_symlink()
    if path.is_file():
        redactor.inspect_headers(path.read_bytes())
unchanged_originals = 0
metadata = 0
for row in manifest['records']:
    data = (CANDIDATE / row['path']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == row['distributed_sha256']
    assert len(data) == row['distributed_bytes']
    assert not row['changed_bytes'] and not row['redactions']
    if row['representation_scope'].startswith('byte_original_'):
        origin = row['origin']
        source = {'task': TASK, 'run': RUN}[origin['root']] / origin['relative_path']
        assert source.resolve().is_relative_to(ROOT) and not source.is_symlink()
        assert data == source.read_bytes(), row['path']
        unchanged_originals += 1
    else:
        assert row['representation_scope'].startswith('metadata_')
        metadata += 1
residual = 0
uncertain_files = 0
for path in CANDIDATE.rglob('*'):
    if path.is_file():
        data = path.read_bytes()
        residual += sum(atom in data for atom in redactor.atoms)
        _, _, uncertain = redactor.redact(data)
        uncertain_files += bool(uncertain)
assert residual == 0 and uncertain_files == 0
assert len(manifest['records']) == unchanged_originals + metadata == 475
roles = Counter(role for checkpoint in manifest['registry_verification'].values()
    for resource in checkpoint['resources'] for role in resource['roles'])
result = {'status': 'PARENT_BYTES_AND_KNOWN_SECRET_SCAN_PASS',
    'payload_files': len(manifest['records']), 'original_byte_files_compared_directly': unchanged_originals,
    'explicit_metadata_serializations': metadata, 'known_secret_residual_matches': residual,
    'unresolved_credential_literal_files': uncertain_files, 'ambient_auth_files_read': False,
    'SCB_profile_fields_and_proxy_values_used_in_memory_only': True,
    'reference_validation_failures': len(manifest['reference_validation_failures']),
    'resource_roles': dict(roles), 'source_originals_modified': False,
    'limits': 'Known-secret scan and direct retained-byte comparison; independent finite content review is separate.'}
suffix = '-final' if sys.argv[1:] == ['--final'] else ''
assert not sys.argv[1:] or suffix
target = TASK / f'evidence/scb-parent-byte-secret-review{suffix}.json'
assert target.resolve().is_relative_to(ROOT) and not target.exists()
with target.open('x') as stream:
    json.dump(result, stream, indent=2)
    stream.write('\n')
print(json.dumps(result))
