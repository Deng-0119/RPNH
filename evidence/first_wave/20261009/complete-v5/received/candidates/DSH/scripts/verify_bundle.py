#!/usr/bin/env python3
"""Read-only verification of the frozen transport and optional target worktree."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path,
                        help='read-only check of affected baseline files before patch application')
    args = parser.parse_args()
    errors: list[str] = []
    count = 0
    for line in (ROOT / 'BUNDLE_SHA256SUMS').read_text().splitlines():
        expected, rel = line.split('  ', 1)
        path = Path(rel)
        if (path.is_absolute() or '..' in path.parts or not path.parts
                or path.parts[0] not in {'rpnh-dsh-session-codec',
                                         'rpnh-dsh-codec-independent-review'}):
            raise ValueError(f'unsafe manifest path: {rel}')
        target = ROOT.parent / path
        if target.is_symlink() or not target.is_file() or sha256(target) != expected:
            errors.append(f'archive hash mismatch or missing file: {rel}')
        count += 1
    if args.checkout is not None:
        target = args.checkout.resolve(strict=True)
        manifest = json.loads((ROOT / 'file-manifest.json').read_text())
        for entry in manifest['changed_files']:
            path = target / entry['path']
            if entry['change'] == 'add':
                if path.exists() or path.is_symlink():
                    errors.append(f'new candidate path already exists: {entry["path"]}')
            elif (path.is_symlink() or not path.is_file()
                  or sha256(path) != entry['baseline_sha256']):
                errors.append(f'affected baseline differs: {entry["path"]}')
        for entry in manifest['protected_unchanged']:
            path = target / entry['path']
            if (path.is_symlink() or not path.is_file()
                    or sha256(path) != entry['sha256']):
                errors.append(f'protected baseline differs: {entry["path"]}')
    result = {'status': 'failed' if errors else 'passed',
              'archive_files_checked': count,
              'checkout_checked': args.checkout is not None,
              'errors': errors,
              'scope': 'byte integrity only; no native/typecheck or support admission'}
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
