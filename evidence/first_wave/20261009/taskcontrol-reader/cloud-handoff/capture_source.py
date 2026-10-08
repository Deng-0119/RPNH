#!/usr/bin/env python3
"""Capture tested checkout identity without staging, committing, or executing tests."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

def git(repo, *args):
    p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace'))
    return p.stdout

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('--h1-package', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root/'file-manifest.json').read_text())['files']
    named = set(row['path'] for row in manifest)
    if args.h1_package:
        h1 = json.loads((args.h1_package/'file-manifest.json').read_text())
        for row in h1:
            named.add(row['path'])
            assert hashlib.sha256((args.repo/row['path']).read_bytes()).hexdigest() == row['final_sha256'], ('H1 mismatch', row['path'])
    for row in manifest:
        assert hashlib.sha256((args.repo/row['path']).read_bytes()).hexdigest() == row['source_sha256'], ('H2a mismatch', row['path'])
    tracked = set(p.decode() for p in git(args.repo, 'ls-files', '-z').split(b'\0') if p)
    entries = []
    for name in sorted(tracked | named):
        p = args.repo/name
        if p.is_symlink():
            data = str(p.readlink()).encode()
            kind = 'symlink'
        elif p.is_file():
            data = p.read_bytes()
            kind = 'file'
        elif not p.exists():
            data = b''
            kind = 'missing'
        else:
            data = b''
            kind = 'directory_or_submodule'
        entries.append({'path': name, 'kind': kind, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'executable': bool(p.stat().st_mode & 0o111) if p.exists() else None})
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    result = {
        'head': git(args.repo, 'rev-parse', 'HEAD').decode().strip(),
        'head_git_tree': git(args.repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
        'branch': git(args.repo, 'branch', '--show-current').decode().strip(),
        'status_porcelain': git(args.repo, 'status', '--porcelain=v1', '--untracked-files=all').decode(),
        'h2a_patch_sha256': hashlib.sha256((root/'taskcontrol-reader-convergence.patch').read_bytes()).hexdigest(),
        'h1_final_verified': bool(args.h1_package),
        'worktree_manifest_sha256': hashlib.sha256(canonical).hexdigest(),
        'worktree_manifest_scope': 'All git-tracked paths plus H2a paths and optional H1 paths; other untracked paths remain visible in status. This SHA256 is not a Git tree ID.',
        'entries': entries,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
