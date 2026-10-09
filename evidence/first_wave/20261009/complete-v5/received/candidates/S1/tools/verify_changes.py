"""Read-only verification of the patch's exact changed files."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--manifest', type=Path, required=True)
args = parser.parse_args()
identity = json.loads(args.manifest.read_text())
errors = []
for record in identity['changed_files']:
    path = args.source / record['path']
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record['candidate_sha256']:
        errors.append(record['path'])
print(json.dumps({'verified_changed_files': len(identity['changed_files']), 'mismatches': errors}, indent=2))
raise SystemExit(bool(errors))
