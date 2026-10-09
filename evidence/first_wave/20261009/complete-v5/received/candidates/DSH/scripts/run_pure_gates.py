#!/usr/bin/env python3
"""Run only already-prepared offline pure gates; never install or start an owner."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'source'


def source_hashes() -> dict[str, str]:
    expected = json.loads((ROOT / 'evidence/freeze/source-before-tests.json').read_text())
    actual = {e['path']: hashlib.sha256((SOURCE / e['path']).read_bytes()).hexdigest()
              for e in expected}
    if any(actual[e['path']] != e['sha256'] for e in expected):
        raise SystemExit('Candidate source differs from the frozen source manifest.')
    return actual


def factory_checks() -> list[dict]:
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(
        'detached_dsh_factory', SOURCE / 'integrations/dsh/patch_upstream.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for label in ('pin', 'latest'):
        source = (ROOT / 'upstream' / label / module.FACTORY_PATH).read_text()
        assert all(source.count(old) == 1 for old, _ in module.replacements)
        updated, changed = module.prepared_source(source)
        assert changed
        assert module.prepared_source(updated) == (updated, False)
        old, new = module.replacements[0]
        try:
            module.prepared_source(source.replace(old, new, 1))
        except ValueError:
            pass
        else:
            raise AssertionError('partial patch was not rejected')
        results.append({'revision_label': label, 'unique_anchors': 6,
                        'transforms': True, 'idempotent': True, 'partial_rejected': True})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='new or empty results directory outside the frozen bundle')
    args = parser.parse_args()
    output = args.output.resolve()
    for package in (ROOT, ROOT.parent / 'rpnh-dsh-codec-independent-review'):
        if output.is_relative_to(package):
            parser.error('Output must be outside the frozen bundle directories.')
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be new or empty; existing evidence is preserved.')
    subprocess.run([sys.executable, str(ROOT / 'scripts/verify_bundle.py')], check=True)
    node = shutil.which('node')
    if node is None:
        parser.error('Node24 must already be on PATH; this runner does not install it.')
    version = subprocess.check_output([node, '--version'], text=True).strip()
    if int(version.removeprefix('v').split('.')[0]) != 24:
        parser.error('This frozen gate was tested on Node24. Use an existing Node24 environment.')
    for dependency in ('pytest', 'jsonschema'):
        if importlib.util.find_spec(dependency) is None:
            parser.error(f'Existing interpreter is missing {dependency}; no installation attempted.')
    before = source_hashes()
    output.mkdir(parents=True, exist_ok=True)
    factories = factory_checks()
    (output / 'factory-checks.json').write_text(json.dumps(factories, indent=2) + '\n')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    commands = [
        ('node-codec', [node, '--test', '--test-reporter=tap',
                        'integrations/dsh/src/message-codec.test.ts']),
        ('python-codec', [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                          'tests/test_dsh_message_codec.py',
                          f'--junitxml={output / "python-codec.xml"}']),
        ('launcher-source', [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                             'tests/test_dsh_distribution.py', '-k',
                             'not distribution_contains_runtime_and_public_source_assets and '
                             'not console_help_works_from_installed_distribution',
                             f'--junitxml={output / "launcher-source.xml"}']),
    ]
    gates = []
    for name, command in commands:
        completed = subprocess.run(command, cwd=SOURCE, env=env,
                                   text=True, capture_output=True)
        (output / f'{name}.log').write_text(completed.stdout + completed.stderr)
        gates.append({'name': name, 'exit_code': completed.returncode,
                      'log': f'{name}.log', 'command': command})
        print(f'{name}: exit {completed.returncode}')
    same = before == source_hashes()
    expected = {'node-codec': 55, 'python-codec': 39, 'launcher-source': 9}
    for gate in gates:
        if gate['name'] == 'node-codec':
            match = re.search(r'^# tests (\d+)$', (output / gate['log']).read_text(), re.M)
            count = int(match[1]) if match else None
        else:
            xml = output / f'{gate["name"]}.xml'
            count = len(list(ET.parse(xml).iter('testcase'))) if xml.exists() else None
        gate['cases'] = count
        gate['expected_cases'] = expected[gate['name']]
    passed = same and all(g['exit_code'] == 0 and g['cases'] == g['expected_cases'] for g in gates)
    report = {'ended_at_utc': datetime.now(timezone.utc).isoformat(),
              'status': 'passed' if passed else 'failed', 'node_version': version,
              'python_version': sys.version, 'source_unchanged': same,
              'gates': gates, 'factory_source_checks': factories,
              'excluded': ['owner/socket', 'native DSH', 'TypeScript typecheck',
                           'install', 'provider/model', 'login', 'Actions', 'push'],
              'counting': 'Repeated runs are not additional cases; factory assertions are separate.'}
    (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
