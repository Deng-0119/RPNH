"""Freeze only this adapter delta against its explicit inherited base."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
SOURCE = ROOT / 'source'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def clean_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and
                  '__pycache__' not in p.parts and '.pytest_cache' not in p.parts and p.suffix != '.pyc')


def main():
    base = json.loads((ROOT / 'BASE_SOURCE_LOCK.json').read_text())
    lookup = {row['path']: row for row in base['files']}
    def inherited(path):
        entry = lookup[path]
        parent = (WORKSPACE / 'rpnh-h2-return-review/remote/cloud-handoff/source'
                  if entry.get('inherited_overlay') else WORKSPACE / base['base_source'])
        data = (parent / path).read_bytes()
        assert sha(data) == entry['sha256'] and len(data) == entry['bytes'], path
        return data
    source_files, changes, patches = [], [], []
    for path in clean_files(SOURCE):
        name = path.relative_to(SOURCE).as_posix()
        after = path.read_bytes()
        source_files.append({'path': name, 'bytes': len(after), 'sha256': sha(after)})
        before = inherited(name) if name in lookup else b''
        if name in lookup and before == after:
            continue
        if name in lookup:
            copy = ROOT / 'baseline' / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(before)
        changes.append({'path': name, 'base_sha256': sha(before) if name in lookup else None,
                        'sha256': sha(after), 'bytes': len(after), 'status': 'modified' if name in lookup else 'added'})
        patches.append('diff --git a/' + name + ' b/' + name + '\n')
        if name not in lookup:
            patches.append('new file mode 100644\n')
        patches.extend(difflib.unified_diff(before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True),
                       fromfile='a/' + name if name in lookup else '/dev/null', tofile='b/' + name))
    assert len(changes) == 6, changes
    assert {c['path'] for c in changes if c['path'].startswith('cpn/')} == {
        'cpn/frontend/codex_app_server.py', 'cpn/frontend/codex_history.py', 'cpn/frontend/codex_compatibility.v1.json'}
    patch = ''.join(patches).encode()
    (ROOT / 'codex-0161-candidate.patch').write_bytes(patch)
    fingerprint = sha(json.dumps(source_files, sort_keys=True, separators=(',', ':')).encode())
    manifest = {'schema': 'rpnh/codex_0161_candidate_package/v1',
                'status': 'SOURCE_AND_JSON_REGISTRY_VALIDATED_NATIVE_PENDING',
                'base_composition': base['source'], 'base_lock_sha256': sha((ROOT / 'BASE_SOURCE_LOCK.json').read_bytes()),
                'patch_sha256': sha(patch), 'source_fingerprint': fingerprint,
                'changed_files': changes, 'source_files': source_files,
                'native_certification': 'NOT_ESTABLISHED',
                'note': 'This patch is only the candidate increment. Reader, effort, owner history and d92 H2 are inherited prerequisites, not re-delivered changes.'}
    (ROOT / 'file-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with tempfile.TemporaryDirectory(prefix='codex-0161-apply-') as raw:
        check = Path(raw)
        for name in lookup:
            destination = check / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(inherited(name))
        checked = subprocess.run(['git', 'apply', '--check', str(ROOT / 'codex-0161-candidate.patch')], cwd=check, capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
        applied = subprocess.run(['git', 'apply', str(ROOT / 'codex-0161-candidate.patch')], cwd=check, capture_output=True, text=True)
        assert applied.returncode == 0, applied.stderr
        for row in source_files:
            assert sha((check / row['path']).read_bytes()) == row['sha256'], row['path']
    tests = []
    for name in ('composed-history-junit.xml', 'default-regression-junit.xml', 'handoff-fake-junit.xml'):
        file = ROOT / 'reports' / name
        doc = ET.parse(file)
        cases = doc.findall('.//testcase')
        tests.append({'file': 'reports/' + name, 'sha256': sha(file.read_bytes()), 'count': len(cases),
                      'failures': len(doc.findall('.//failure')), 'errors': len(doc.findall('.//error')),
                      'skipped': len(doc.findall('.//skipped')),
                      'classes': {key: sum(c.attrib.get('classname') == key for c in cases)
                                  for key in sorted({c.attrib.get('classname') for c in cases})}})
    report = {'schema': 'rpnh/codex_0161_candidate_verification/v1', 'patch_sha256': sha(patch),
              'source_fingerprint': fingerprint, 'changed_files': len(changes), 'source_files': len(source_files),
              'clean_patch_check': True, 'clean_patch_apply': True, 'all_reconstructed_source_bytes_match': True,
              'tests': tests, 'native_runs': 0, 'model_calls': 0, 'pushes': 0, 'installations': 0,
              'prior_69_pre_d92_tests': 'Historical exploratory run only; not added to final counts',
              'independent_tests': 'Separate overlapping review; never add to author count'}
    (ROOT / 'reports/VERIFICATION.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('patch_sha256', 'source_fingerprint', 'changed_files', 'source_files', 'tests')}, indent=2))


if __name__ == '__main__':
    main()
