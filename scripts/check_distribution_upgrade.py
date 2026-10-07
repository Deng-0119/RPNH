#!/usr/bin/env python3
"""Offline clean install and ordinary upgrade with exact user-supplied wheels.

This invokes only pip and data-only RPNH commands. It creates temporary virtual
environments and requires a complete local wheelhouse for declared dependencies.
No provider, Registry owner, package execution, upload or release is started.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
from zipfile import ZipFile


PROBE = '''import importlib.metadata, json
from pathlib import Path
import cpn
print(json.dumps({"version":importlib.metadata.version("rpnh-harness"),
    "package":str(Path(cpn.__file__).resolve().parent)}))
'''


def wheel_version(path):
    with ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(names) != 1:
            raise ValueError('Expected exactly one wheel metadata document')
        metadata = BytesParser().parsebytes(archive.read(names[0]))
        if metadata['Name'] != 'rpnh-harness':
            raise ValueError('Both wheels must be rpnh-harness distributions')
        return metadata['Version']


def verify_payload(wheel, package):
    site = Path(package).parent
    checked = 0
    expected = set()
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith('/') or not name.startswith(('cpn/', 'integrations/')):
                continue
            target = site / name
            if not target.is_file() or target.read_bytes() != archive.read(name):
                raise ValueError('Installed payload differs from candidate wheel: ' + name)
            expected.add(name)
            checked += 1
    if not checked:
        raise ValueError('Candidate has no runtime payload')
    actual = {path.relative_to(site).as_posix()
        for root in ('cpn', 'integrations') for path in (site / root).rglob('*')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
    if actual != expected:
        raise ValueError('Installed tree retains unexpected runtime files: '
                         + ', '.join(sorted(actual - expected)))
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-wheel', required=True, type=Path)
    parser.add_argument('--old-wheel-sha256', required=True)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--wheelhouse', required=True, type=Path)
    parser.add_argument('--source-root', required=True, type=Path)
    args = parser.parse_args()
    old, candidate = args.old_wheel.resolve(), args.wheel.resolve()
    wheelhouse, source = args.wheelhouse.resolve(), args.source_root.resolve()
    if hashlib.sha256(old.read_bytes()).hexdigest() != args.old_wheel_sha256:
        raise ValueError('Historical wheel SHA-256 differs from the selected baseline')
    old_version, version = wheel_version(old), wheel_version(candidate)
    if old_version == version:
        raise ValueError('Candidate must have a distinct upgrade identity')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PYTHON', 'RPNH_', 'PIP_'))}
    env.update(PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
               PYTHONDONTWRITEBYTECODE='1')
    commands = []
    with tempfile.TemporaryDirectory(prefix='rpnh-distribution-upgrade-') as temp:
        work = Path(temp)
        env['HOME'] = str(work / 'home')

        def run(command):
            result = subprocess.run([str(x) for x in command], cwd=work, env=env,
                text=True, capture_output=True, timeout=180)
            commands.append({'command': [str(x) for x in command],
                'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
            if result.returncode:
                raise RuntimeError(json.dumps(commands[-1], ensure_ascii=False))
            return result.stdout

        def environment(name):
            root = work / name
            venv.EnvBuilder(with_pip=True).create(root)
            return root / ('Scripts' if os.name == 'nt' else 'bin') / 'python'

        def install(python, wheel, upgrade=False):
            return run([python, '-m', 'pip', 'install', '--no-index',
                '--find-links', wheelhouse, *(['--upgrade'] if upgrade else []), wheel])

        def inspect(python):
            value = json.loads(run([python, '-I', '-c', PROBE]))
            if Path(value['package']).is_relative_to(source):
                raise ValueError('Imported checkout rather than installed candidate')
            return value

        fresh = environment('clean')
        install(fresh, candidate)
        fresh_provenance = inspect(fresh)
        if fresh_provenance['version'] != version:
            raise ValueError('Clean install did not install the selected version')
        fresh_files = verify_payload(candidate, fresh_provenance['package'])
        upgraded = environment('upgrade')
        install(upgraded, old)
        before = inspect(upgraded)
        if before['version'] != old_version:
            raise ValueError('Historical baseline did not install')
        install(upgraded, candidate, upgrade=True)
        after = inspect(upgraded)
        if after['version'] != version:
            raise ValueError('Ordinary pip --upgrade retained the old distribution')
        upgraded_files = verify_payload(candidate, after['package'])
        for python in (fresh, upgraded):
            run([python, '-m', 'pip', 'check'])
            run([python, '-I', '-m', 'cpn.rpnh_cli', 'package', '--help'])
            catalog = json.loads(run([python, '-I', '-m', 'cpn.rpnh_cli', 'examples', 'list']))
            if not catalog.get('examples'):
                raise ValueError('Installed candidate has no example gallery')
        print(json.dumps({'status': 'PASS', 'old_version': old_version,
            'candidate_version': version, 'ordinary_upgrade': True,
            'clean_installed_payload_files': fresh_files,
            'upgraded_payload_files': upgraded_files, 'outside_source': True,
            'old_wheel_sha256': args.old_wheel_sha256,
            'candidate_wheel_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
            'provider_calls': 0, 'commands': commands}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
