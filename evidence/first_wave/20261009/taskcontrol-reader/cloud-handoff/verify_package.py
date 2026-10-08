#!/usr/bin/env python3
"""Read-only package verification plus disposable patch application; no tests."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def blob(p):
    data = p.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--state', choices=('baseline', 'final'), default='baseline')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'file-manifest.json').read_text())
    report = {'package_checksums': 0, 'junit_parseable': 0, 'source_paths': [], 'repo_state': args.state if args.repo else None}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert sha(root / name) == digest, ('package checksum', name)
        report['package_checksums'] += 1
    for path in root.rglob('*.xml'):
        ET.parse(path)
        report['junit_parseable'] += 1
    assert sha(root / 'taskcontrol-reader-convergence.patch') == '62f914d41d953e9badd4eb06eec43273f9d52bd5bd9b948ad6e10137d3ed2b24'
    for row in manifest['files']:
        p = root / 'source' / row['path']
        assert sha(p) == row['source_sha256']
        assert blob(p) == row['source_git_blob']
        if row['baseline_sha256']:
            p = root / 'baseline' / row['path']
            assert sha(p) == row['baseline_sha256']
            assert blob(p) == row['baseline_git_blob']
        if args.repo:
            p = args.repo / row['path']
            expected = row['baseline_sha256'] if args.state == 'baseline' else row['source_sha256']
            assert (sha(p) == expected) if expected else not p.exists(), ('checkout mismatch', row['path'], args.state)
        report['source_paths'].append(row['path'])
    with tempfile.TemporaryDirectory(prefix='rpnh-h2a-apply-') as temp:
        work = Path(temp)
        shutil.copytree(root / 'baseline', work, dirs_exist_ok=True)
        patch = str(root / 'taskcontrol-reader-convergence.patch')
        for command in (['git', 'apply', '--check', patch], ['git', 'apply', patch]):
            process = subprocess.run(command, cwd=work, text=True, capture_output=True)
            assert process.returncode == 0, process.stderr
        for row in manifest['files']:
            assert sha(work / row['path']) == row['source_sha256']
    report['temporary_apply'] = 'PASS; five final paths match'
    report['product_tests_run'] = False
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
