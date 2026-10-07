#!/usr/bin/env python3
"""Guarded zero-model smoke of an already installed wheel, outside its source tree."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

GUARD = '''import sys

def reject_runtime_effects(event, args):
    if event in {"socket.connect", "socket.bind", "socket.__new__", "socket.getaddrinfo",
                 "subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.exec",
                 "os.posix_spawn", "sqlite3.connect"}:
        raise RuntimeError("Installed documentation smoke forbids runtime effects: " + event)
sys.addaudithook(reject_runtime_effects)
'''
PROVENANCE = '''import importlib.metadata as md, json
from pathlib import Path
import cpn
package = Path(cpn.__file__).resolve().parent
dist = md.distribution("rpnh-harness")
assert Path(dist.locate_file("cpn/__init__.py")).resolve() == Path(cpn.__file__).resolve()
assert any(ep.name == "rpnh" and ep.value == "cpn.rpnh_cli:main" for ep in dist.entry_points)
required = ["config/provider_models.json", "schemas/rpnh/module_declaration.v1.schema.json",
            "schemas/rpnh/share_package.v2.schema.json",
            "schemas/rpnh/package_preview.v2.schema.json",
            "schemas/rpnh/package_resolution_lock.v2.schema.json",
            "schemas/rpnh/environment_requirements.v1.schema.json",
            "schemas/rpnh/package_target.v1.schema.json",
            "schemas/rpnh/registry_read_session_request.v1.schema.json",
            "schemas/rpnh/registry_source_cut.v1.schema.json",
            "schemas/rpnh/registry_index_query.v1.schema.json",
            "schemas/registry_v1/registry_observer_grant.v2.schema.json",
            "frontend/static/comparison-context.mjs",
            "schemas/runtime/provider_model_catalog.v2.schema.json",
            "examples/adapter_task/manifest.json", "examples/adapter_task/task.txt",
            "examples/adapter_task/expected.json",
            "examples/adapter_task/opencode/evidence.json"]
for item in required:
    assert (package / item).is_file(), "Missing packaged resource: " + item
assert any(p.is_file() for p in (package / "frontend/static").rglob("*")), "Missing static assets"
print(json.dumps({"package": str(package), "version": dist.version}))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    args = parser.parse_args()
    # Do not resolve the venv interpreter symlink to its base interpreter.
    python = args.python.absolute()
    source = args.source_root.resolve()
    if not python.is_file():
        print('BLOCKED: supplied wheel-environment Python does not exist')
        return 2
    with tempfile.TemporaryDirectory(prefix='rpnh-installed-docs-') as temp:
        work = Path(temp)
        guard = work / 'guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text(GUARD, encoding='utf-8')
        env = {'PATH': str(python.parent) + ':/usr/bin:/bin', 'HOME': str(work / 'home'),
               'LANG': 'C.UTF-8', 'PYTHONPATH': str(guard), 'PYTHONDONTWRITEBYTECODE': '1',
               'RPNH_CONFIG': str(work / 'config/config.json'),
               'RPNH_PROVIDER_CATALOG': str(work / 'config/provider_models.json'),
               'RPNH_PROFILE_DIR': str(work / 'config/profiles/execution')}
        def run(command: list[str]):
            return subprocess.run(command, cwd=work, env=env, text=True,
                                  capture_output=True, timeout=30)
        try:
            result = run([str(python), '-c', PROVENANCE])
            if result.returncode:
                print('BLOCKED: installed RPNH distribution/resources could not be verified')
                print(result.stderr)
                return 2
            provenance = json.loads(result.stdout)
            if Path(provenance['package']).is_relative_to(source):
                print('FAIL: imported package is in the source tree, not an isolated wheel install')
                return 1
            entry = python.parent / 'rpnh'
            if not entry.is_file():
                print('BLOCKED: installed rpnh console entry point not found')
                return 2
            commands = [['--help'], ['config', '--help'], ['config', 'init'],
                        ['config', 'build'], ['config', 'build', '--check'], ['config', 'list'],
                        ['examples', 'list'], ['package', '--help'],
                        ['package', 'check-environment', '--help'],
                        ['package', 'resolve-environment', '--help'],
                        ['package', 'prepare-environment', '--help'],
                        ['package', 'setup-instructions', '--help'],
                        ['package', 'run', '--help'], ['net', '--help']]
            for arguments in commands:
                result = run([str(entry), *arguments])
                if result.returncode:
                    print('FAIL:', 'rpnh ' + ' '.join(arguments), 'exit', result.returncode)
                    print(result.stderr)
                    return 1
                if arguments == ['config', 'list'] and json.loads(result.stdout) != []:
                    raise ValueError('A fresh empty catalog must expose no profiles')
            example = work / 'adapter-task'
            result = run([str(entry), 'examples', 'export', '--output', str(example)])
            if result.returncode:
                print('FAIL: installed example export exit', result.returncode)
                print(result.stderr)
                return 1
            result = run([str(entry), 'examples', 'verify',
                          '--result', str(example / 'expected.json')])
            if result.returncode or json.loads(result.stdout).get('status') != 'PASS':
                print('FAIL: installed example verification')
                print(result.stderr)
                return 1
            print(json.dumps({'installed_version': provenance['version'],
                              'outside_source': True, 'zero_model_commands_passed': len(commands),
                              'installed_example_exported': True,
                              'guarded_against_runtime_effects': True,
                              'live_calls': 0}, indent=2))
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print('FAIL:', exc)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
