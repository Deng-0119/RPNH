"""Local evidence utilities for the two assigned frozen adapter lanes."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from datetime import datetime, timezone

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-complete-v5-20261009'
INDEX = json.loads((TASK / 'rpnh-complete-local-bundle-v5/CANDIDATE_INDEX.json').read_text())

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def safe(p):
    assert p.resolve().is_relative_to(ROOT), str(p)
    assert not p.is_symlink(), str(p)
    if p.is_file():
        assert p.stat().st_nlink == 1, str(p)
    return p

def write(p, data):
    safe(p)
    assert not p.exists(), str(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + '\n')

def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True)

def snapshot(repo):
    names = sorted(set(git(repo, 'ls-files', '-z').split('\0')[:-1]) |
                   set(git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')[:-1]))
    files = {}
    for name in names:
        p = repo / name
        safe(p)
        files[name] = {'sha256': digest(p), 'mode': oct(stat.S_IMODE(p.stat().st_mode))} if p.exists() else None
    return {'at_utc': datetime.now(timezone.utc).isoformat(), 'root': str(repo),
            'head': git(repo, 'rev-parse', 'HEAD').strip(),
            'status': git(repo, 'status', '--porcelain=v1', '--untracked-files=all'),
            'file_count': len(files), 'files': files}

def settings(lane):
    cid, short = ('DSH', 'd') if lane == 'dsh' else ('OpenCode', 'o')
    item = next(c for c in INDEX['candidates'] if c['id'] == cid)
    stage = safe(TASK / 'stages' / lane)
    repo = safe(ROOT / '.v26' / short)
    raw = TASK / 'raw' / Path(item['archive_path']).stem
    package = stage / 'package' / item['root_member']
    return item, stage, repo, raw, package

def manifest_check(lane, phase, label):
    item, stage, repo, raw, package = settings(lane)
    original = raw / item['root_member']
    patch = original / Path(item['selected_final_patch_member']).name
    errors, rows, protected = [], [], []
    if digest(patch) != item['patch_sha256']:
        errors.append('patch SHA mismatch')
    patch_paths = [line.removeprefix('+++ b/') for line in patch.read_text().splitlines() if line.startswith('+++ b/')]
    if sorted(patch_paths) != sorted(item['changed_paths']):
        errors.append('patch path whitelist mismatch')
    if lane == 'dsh':
        manifest = json.loads((original / 'file-manifest.json').read_text())
        entries = manifest['changed_files']
        protected = manifest['protected_unchanged']
    else:
        manifest = json.loads((original / 'FILE_MANIFEST.json').read_text())
        assert sorted(manifest['changed_files']) == sorted(item['changed_paths'])
        entries = []
        for name in item['changed_paths']:
            old, new = original / 'baseline' / name, original / 'source' / name
            entries.append({'path': name, 'baseline_sha256': digest(old) if old.exists() else None,
                            'candidate_sha256': digest(new), 'mode': '100755' if new.stat().st_mode & 0o111 else '100644'})
        for old in (original / 'baseline').rglob('*'):
            if old.is_file() and old.relative_to(original / 'baseline').as_posix() not in item['changed_paths']:
                protected.append({'path': old.relative_to(original / 'baseline').as_posix(), 'sha256': digest(old)})
    for e in entries:
        p = safe(repo / e['path'])
        actual = digest(p) if p.exists() else None
        expected = e['baseline_sha256'] if phase == 'pre' else e['candidate_sha256']
        row = dict(e, actual_sha256=actual, expected_sha256=expected, match=actual == expected)
        if not row['match']:
            errors.append('changed path mismatch: ' + e['path'])
        if phase == 'post' and p.exists():
            row['actual_mode'] = '100755' if p.stat().st_mode & 0o111 else '100644'
            if row['actual_mode'] != e['mode']:
                errors.append('mode mismatch: ' + e['path'])
        rows.append(row)
    for e in protected:
        p = safe(repo / e['path'])
        actual = digest(p) if p.exists() else None
        e['actual_sha256'] = actual
        e['match'] = actual == e['sha256']
        if not e['match']:
            errors.append('protected mismatch: ' + e['path'])
    report = {'status': 'BLOCKED' if errors else 'PASS', 'phase': phase, 'worktree': str(repo),
              'patch': str(patch), 'actual_patch_sha256': digest(patch), 'expected_patch_sha256': item['patch_sha256'],
              'changed': rows, 'protected': protected, 'errors': errors}
    write(stage / (phase + ('-' + label if label != 'after-tests' else '') + '-manifest.json'), report)
    print(json.dumps({'status': report['status'], 'changed_paths': len(rows), 'protected_paths': len(protected), 'errors': errors}))
    assert not errors

def copy_package(lane):
    item, stage, repo, raw, package = settings(lane)
    assert git(repo, 'rev-parse', 'HEAD').strip() == INDEX['baseline_main']
    assert not git(repo, 'status', '--porcelain=v1', '--untracked-files=all')
    write(stage / 'source-before.json', snapshot(repo))
    original_files = {}
    for p in raw.rglob('*'):
        assert not p.is_symlink(), str(p)
        if p.is_file():
            original_files[p.relative_to(raw).as_posix()] = digest(p)
    write(stage / 'raw-before.json', original_files)
    target = safe(stage / 'package')
    assert not target.exists()
    shutil.copytree(raw, target, copy_function=shutil.copy2)
    for name, expected in original_files.items():
        assert digest(safe(target / name)) == expected, name
    print(json.dumps({'status': 'PASS', 'copied_files': len(original_files), 'package': str(package)}))

def finish_snapshot(lane, label):
    item, stage, repo, raw, package = settings(lane)
    data = snapshot(repo)
    write(stage / ('source-' + label + '.json'), data)
    raw_before = json.loads((stage / 'raw-before.json').read_text())
    raw_now = {p.relative_to(raw).as_posix(): digest(p) for p in raw.rglob('*') if p.is_file()}
    assert raw_now == raw_before, 'raw candidate changed'
    print(json.dumps({'status': 'PASS', 'file_count': data['file_count'], 'raw_unchanged': True, 'label': label}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('lane', choices=['dsh', 'opencode'])
    parser.add_argument('action', choices=['copy', 'pre', 'post', 'snapshot'])
    parser.add_argument('--label', default='after-tests')
    args = parser.parse_args()
    if args.action == 'copy':
        copy_package(args.lane)
    elif args.action == 'snapshot':
        finish_snapshot(args.lane, args.label)
    else:
        manifest_check(args.lane, args.action, args.label)
