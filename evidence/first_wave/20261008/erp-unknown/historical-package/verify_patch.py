#!/usr/bin/env python3
"""Read-only before/after byte verification for the five ERP patch paths."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('checkout', type=Path)
parser.add_argument('--state', choices=('before', 'after'), required=True)
args = parser.parse_args()
package = Path(__file__).resolve().parent
manifest = json.loads((package / 'evidence/changed-files.json').read_text())
patch = package / 'erp-unknown-convergence.patch'
if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest['patch_sha256']:
    raise SystemExit('Patch hash mismatch; do not apply.')
failures = []
for row in manifest['files']:
    target = args.checkout / row['path']
    if args.state == 'before' and row['old_git_blob_sha'] is None:
        valid = not target.exists()
    elif not target.is_file():
        valid = False
    else:
        data = target.read_bytes()
        digest = (hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                  if args.state == 'before' else hashlib.sha256(data).hexdigest())
        valid = digest == row['old_git_blob_sha' if args.state == 'before' else 'sha256']
    print(('PASS' if valid else 'FAIL') + ' ' + row['path'])
    if not valid:
        failures.append(row['path'])
if failures:
    raise SystemExit('Byte mismatch: preserve local changes and review before proceeding.')
print('Verified all five paths in ' + args.state + ' state. No files were changed.')
