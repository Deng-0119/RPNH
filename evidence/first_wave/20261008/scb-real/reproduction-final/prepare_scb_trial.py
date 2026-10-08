"""Prepare/run the single explicitly authorized SCB development prefix.

All writes are task/workspace local. Raw CLI output is private until reviewed.
No automatic retry, resume, prompt retuning, or grader feedback is added.
"""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import subprocess
import sys

ROOT = Path('/home/deng123/RPNH').resolve()
TASK = ROOT / 'task-first-wave-examples-20261008'
ERP = ROOT / 'task-erp-first-wave-20261008'
SHORT = ROOT / '.s26'
PLAN = TASK / 'work/scb-real-plan01'


def checked(path):
    path = Path(path)
    assert path.resolve().is_relative_to(ROOT) and not path.is_symlink(), path
    if path.exists() and path.is_file():
        assert path.stat().st_nlink == 1, path
    return path


def write(path, content):
    checked(path)
    with path.open('x') as stream:
        stream.write(content)


os.umask(0o077)
assert (TASK / 'private/MODEL_AUTHORIZATION_SCB.json').is_file()
for directory in (SHORT, SHORT / 't', PLAN):
    checked(directory).mkdir(mode=0o700, exist_ok=True)

environment_path = PLAN / 'docker-python3.12-uv-eval-host.yaml'
condition_path = PLAN / 'condition.json'
if sys.argv[1] == 'prepare':
    original = (TASK / 'work/upstream-runner/configs/environments/docker-python3.12-uv.yaml').read_text()
    assert original.count('docker:\n') == 1 and '  network:' not in original
    write(environment_path, original.replace('docker:\n', 'docker:\n  network: host\n', 1))
    write(condition_path, (ROOT / 'RPNH-main/examples/slopcodebench/condition.example.json').read_text())
    write(PLAN / 'adaptation.json', json.dumps({
        'mode': 'adapted_development_prefix', 'solver_network': 'none',
        'evaluation_network': 'host', 'image_build_network': 'host',
        'reason': 'task-owned WSL Docker daemon has no container NAT; ordinary dependency setup needs network',
        'upstream_setup_commands_changed': False, 'upstream_tests_changed': False,
        'upstream_grader_changed': False, 'official_agent_runner': 'not_run',
        'model': 'codex/gpt-5.6-terra', 'prefix': 3,
        'max_model_calls_per_checkpoint': 48, 'owner_wait_seconds_per_checkpoint': 7200,
        'pass_policy': 'any-case', 'automatic_replay': False,
        'authorization': str(TASK / 'private/MODEL_AUTHORIZATION_SCB.json')
    }, ensure_ascii=False, indent=2) + '\n')
    print('SCB_REAL_PLAN_PREPARED; no model calls')
    raise SystemExit(0)

assert sys.argv[1] == 'run'
output = checked(SHORT / 'scb01')
assert not output.exists(), 'Fresh output required; no automatic replay'
python = TASK / 'upstream-venv/bin/python'
binary = Path('/home/deng123/.nvm/versions/node/v22.22.1/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')
assert binary.is_file() and os.access(binary, os.X_OK)
command = [str(python), '-B', '-m', 'rpnh_scb.run',
    '--runner-source', str(TASK / 'work/upstream-runner'),
    '--problems-source', str(TASK / 'work/upstream-problems'),
    '--environment', str(environment_path),
    '--template', str(TASK / 'work/upstream-runner/configs/prompts/just-solve.jinja'),
    '--execution', str(ROOT / 'task-benchmark-20261002/work/automationbench-local/codex-terra-selection.json'),
    '--condition', str(condition_path), '--output', str(output), '--prefix', '3',
    '--pass-policy', 'any-case', '--codex-binary', str(binary),
    '--acknowledge-development-model-run']
environment = dict(os.environ)
environment.pop('PYTHONPATH', None)
environment.update(DOCKER_HOST='unix:///home/deng123/RPNH/.e26/s',
    DOCKER_CONFIG=str(ERP / 'config/docker'), TMPDIR=str(SHORT / 't'),
    PYTHONDONTWRITEBYTECODE='1', UV_CACHE_DIR=str(TASK / 'work/upstream-uv-cache'),
    PIP_CACHE_DIR=str(TASK / 'work/pip-cache'))
environment['PATH'] = str(ERP / 'work/tools/docker') + ':' + str(TASK / 'upstream-venv/bin') + ':' + environment.get('PATH', '')
start = datetime.now(timezone.utc)
log = checked(TASK / 'private/scb-real01.log')
with log.open('x') as stream:
    process = subprocess.Popen(command, cwd=TASK, env=environment, stdin=subprocess.DEVNULL,
                               stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    write(TASK / 'private/scb-real01-owner.json', json.dumps({
        'pid': process.pid, 'command': command, 'started_at': start.isoformat(),
        'output': str(output), 'log': str(log), 'retry': False
    }, indent=2) + '\n')
    code = process.wait()
finish = datetime.now(timezone.utc)
write(TASK / 'evidence/scb-real01-exit.json', json.dumps({
    'exit_code': code, 'started_at': start.isoformat(), 'ended_at': finish.isoformat(),
    'elapsed_seconds': (finish - start).total_seconds(), 'output': str(output),
    'condition': 'adapted_development_prefix', 'original_agent_runner': 'not_run',
    'raw_log_visibility': 'private_unreviewed', 'retry': False
}, indent=2) + '\n')
print(json.dumps({'status': 'SCB_REAL_PREFIX_PROCESS_EXITED', 'exit_code': code,
                  'output': str(output), 'elapsed_seconds': (finish - start).total_seconds()}))
raise SystemExit(code)
