"""Freeze exact candidate/input identity and a reconstructible text overlay."""
import hashlib, json, difflib
from pathlib import Path
root = Path(__file__).resolve().parent.parent

def snapshot(path):
    rows = [{'path': p.relative_to(path).as_posix(), 'size': p.stat().st_size,
             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(path.rglob('*')) if p.is_file() and not {'__pycache__', '.pytest_cache'}.intersection(p.parts)]
    return {'files': rows, 'file_count': len(rows), 'manifest_sha256': hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}

def write(name, value):
    (root / name).write_text(json.dumps(value, indent=2) + '\n')

before = snapshot(root / 'inputs/acceptance-history-source')
after = snapshot(root / 'source')
assert before['file_count'] == 994 and before['manifest_sha256'] == 'a3af1d85ffbc8acac72e719fed316ed3c848229fce92026c62e571cb77a913a2'
write('evidence/FINAL_INPUT_MANIFEST.json', before)
write('evidence/FINAL_SOURCE_MANIFEST.json', after)
a = {r['path']: r for r in before['files']}; b = {r['path']: r for r in after['files']}
changed = [name for name in sorted(a.keys() | b.keys()) if a.get(name) != b.get(name)]
chunks = []
for name in changed:
    old = (root / 'inputs/acceptance-history-source' / name).read_text().splitlines(keepends=True) if name in a else []
    new = (root / 'source' / name).read_text().splitlines(keepends=True) if name in b else []
    chunks.append(f'diff --git a/{name} b/{name}\n')
    if name not in a: chunks.append('new file mode 100644\n')
    if name not in b: chunks.append('deleted file mode 100644\n')
    chunks.extend(difflib.unified_diff(old, new, fromfile='a/' + name if name in a else '/dev/null',
                                    tofile='b/' + name if name in b else '/dev/null'))
patch = ''.join(chunks).encode()
(root / 'bound-child-declarations.patch').write_bytes(patch)
identity = {'repository': 'Deng-0119/RPNH', 'source_kind': 'frozen acceptance-history overlay; no current remote HEAD assertion',
    'input_file_count': before['file_count'], 'input_manifest_sha256': before['manifest_sha256'],
    'source_file_count': after['file_count'], 'source_manifest_sha256': after['manifest_sha256'],
    'overlay_patch_sha256': hashlib.sha256(patch).hexdigest(),
    'changed_files': [{'path': name, 'before_sha256': a.get(name, {}).get('sha256'),
                       'after_sha256': b.get(name, {}).get('sha256')} for name in changed]}
write('SOURCE_IDENTITY.json', identity)
print(json.dumps(identity, indent=2))
