"""Verify included bytes and clean six-file application; no native/client/model run."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((ROOT / 'file-manifest.json').read_text())
    assert digest(ROOT / 'codex-0161-candidate.patch') == manifest['patch_sha256']
    assert digest(ROOT / 'BASE_SOURCE_LOCK.json') == manifest['base_lock_sha256']
    for row in manifest['source_files']:
        path = ROOT / 'source' / row['path']
        assert digest(path) == row['sha256'] and path.stat().st_size == row['bytes'], row['path']
    fingerprint = hashlib.sha256(json.dumps(manifest['source_files'], sort_keys=True,
                               separators=(',', ':')).encode()).hexdigest()
    assert fingerprint == manifest['source_fingerprint']
    with tempfile.TemporaryDirectory(prefix='codex-0161-local-verify-') as raw:
        check = Path(raw)
        for row in manifest['changed_files']:
            if row['base_sha256'] is not None:
                source = ROOT / 'baseline' / row['path']
                assert digest(source) == row['base_sha256']
                target = check / row['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        subprocess.run(['git', 'apply', '--check', str(ROOT / 'codex-0161-candidate.patch')], cwd=check, check=True)
        subprocess.run(['git', 'apply', str(ROOT / 'codex-0161-candidate.patch')], cwd=check, check=True)
        for row in manifest['changed_files']:
            assert digest(check / row['path']) == row['sha256']
    print(json.dumps({'verified_source_files': len(manifest['source_files']),
                      'clean_increment_files': len(manifest['changed_files']),
                      'patch_sha256': manifest['patch_sha256'], 'native_certification': 'NOT_ESTABLISHED'}))


if __name__ == '__main__':
    main()
