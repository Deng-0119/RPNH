"""Inspect executable names and public source tooling; never probe unknown clients."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys

ROOT = Path('<WORKSPACE>')
patterns = ['node', 'opencode', 'opencode-*', 'pnpm', 'pnpm-lock.yaml',
            '*opencode*provenance*', '*opencode*sha*']
command = ['rg', '--files', '--hidden', '--no-ignore']
for pattern in patterns:
    command += ['-g', pattern]
for excluded in ['**/.git/**', '**/.rpnh/**', '**/raw/**', '**/stages/**']:
    command += ['-g', '!' + excluded]
command += [str(ROOT)]
result = subprocess.run(command, capture_output=True, text=True)
assert result.returncode in (0, 1, 2), result.stderr
found = []
for name in result.stdout.splitlines():
    p = Path(name)
    if p.is_symlink() or not p.resolve().is_relative_to(ROOT):
        continue
    row = {'path': name, 'bytes': p.stat().st_size}
    if p.name in ('node', 'opencode', 'pnpm') or p.name.startswith('opencode-'):
        with p.open('rb') as f:
            row['elf'] = f.read(4) == b'\x7fELF'
        row['executable'] = os.access(p, os.X_OK)
        if row['elf'] and row['executable']:
            row['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    found.append(row)
print(json.dumps({'python': sys.executable, 'python_version': sys.version,
    'dependencies': {name: importlib.util.find_spec(name) is not None for name in ('pytest', 'jsonschema', 'numpy', 'scipy', 'websockets')},
    'node_path': shutil.which('node'),
    'node_version': subprocess.check_output([shutil.which('node'), '--version'], text=True).strip(),
    'nvm_exact_entries': [str(p) for p in Path('<USER_HOME>/.nvm/versions/node').iterdir()],
    'pnpm_path': shutil.which('pnpm'), 'opencode_path': shutil.which('opencode'),
    'workspace_search_command': command, 'workspace_search_exit': result.returncode,
    'workspace_search_stderr': result.stderr, 'workspace_search_complete': result.returncode != 2,
    'workspace_candidates': found,
    'unknown_binaries_executed': False}, indent=2))
