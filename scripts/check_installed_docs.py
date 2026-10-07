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
            "examples/adapter_task/opencode/evidence.json",
            "examples/catalog.json",
            "examples/gallery/examples/native_plugin/rpnh_demo.py",
            "examples/gallery/examples/hybrid_summary/run.py",
            "examples/gallery/examples/_support/profile.py",
            "examples/gallery/examples/net_operations/compose_serial.py",
            "examples/gallery/examples/package_reuse/native-add-v2.zip"]
for item in required:
    assert (package / item).is_file(), "Missing packaged resource: " + item
assert any(p.is_file() for p in (package / "frontend/static").rglob("*")), "Missing static assets"
print(json.dumps({"package": str(package), "version": dist.version}))
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--expected-version',
                        help='require this exact installed distribution identity')
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
            if (args.expected_version is not None
                    and provenance['version'] != args.expected_version):
                raise ValueError('Installed version differs from --expected-version: '
                                 + provenance['version'])
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
            # A user edits both the copied task and its contract. This is a
            # deterministic verifier check, not evidence of a provider run.
            custom = {'count': 3, 'total': 60, 'mean': 20,
                      'minimum': 10, 'maximum': 30}
            (example / 'task.txt').write_text(
                'Summarize 10, 20, 30 using count, total, mean, minimum, maximum.',
                encoding='utf-8')
            (example / 'expected.json').write_text(json.dumps(custom), encoding='utf-8')
            answer = example / 'custom-answer.json'
            answer.write_text(json.dumps(custom), encoding='utf-8')
            result = run([str(entry), 'examples', 'verify', '--result', str(answer),
                          '--expected', str(example / 'expected.json')])
            if result.returncode or json.loads(result.stdout).get('status') != 'PASS':
                print('FAIL: user-owned custom example contract verification')
                print(result.stderr)
                return 1
            result = run([str(entry), 'examples', 'verify', '--result', str(answer)])
            if result.returncode == 0:
                raise ValueError('Custom result unexpectedly passed the unchanged default contract')
            named = ('native_plugin', 'hybrid_summary', 'compose_serial', 'package_reuse')
            for name in named:
                destination = work / ('user copy 中文 ' + name)
                result = run([str(entry), 'examples', 'export', '--example', name,
                              '--output', str(destination)])
                if result.returncode or not (destination / 'manifest.json').is_file():
                    raise ValueError('Installed named example export failed: ' + name)
                (destination / 'user-note.txt').write_text('keep', encoding='utf-8')
                repeated = run([str(entry), 'examples', 'export', '--example', name,
                                '--output', str(destination)])
                if repeated.returncode != 2 or (destination / 'user-note.txt').read_text() != 'keep':
                    raise ValueError('Named export did not preserve existing user directory: ' + name)
            print(json.dumps({'installed_version': provenance['version'],
                              'outside_source': True, 'zero_model_commands_passed': len(commands),
                              'installed_example_exported': True,
                              'custom_expected_contract_verified': True,
                              'named_examples_exported_without_execution': list(named),
                              'existing_user_directories_preserved': True,
                              'guarded_against_runtime_effects': True,
                              'live_calls': 0}, indent=2))
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print('FAIL:', exc)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
