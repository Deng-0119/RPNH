"""Task-local command recorder. Raw stdout and stderr are retained separately."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-shared-harness-validation-20261008'
REPO = ROOT / '.h26/s'

def main():
    label, *command = sys.argv[1:]
    assert label.replace('-', '').replace('_', '').isalnum()
    assert command
    logs = TASK / 'logs'
    paths = {name: logs / (label + '.' + name) for name in ('stdout.log', 'stderr.log', 'json')}
    for path in paths.values():
        assert path.resolve().is_relative_to(ROOT) and not path.exists(), path
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               PYTHONPATH=str(REPO), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               TMPDIR=str(ROOT / '.h26/tmp'))
    started = datetime.now(timezone.utc).isoformat()
    tick = time.monotonic()
    with paths['stdout.log'].open('xb') as out, paths['stderr.log'].open('xb') as err:
        process = subprocess.Popen(command, cwd=REPO, env=env, stdout=out, stderr=err)
        print(json.dumps({'label': label, 'pid': process.pid, 'started_at': started}), flush=True)
        code = process.wait()
    record = {'label': label, 'command': command, 'cwd': str(REPO), 'pid': process.pid,
              'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': time.monotonic() - tick, 'exit_code': code,
              'tested_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
              'environment_overrides': {key: env[key] for key in
                 ('PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED', 'PYTHONPATH',
                  'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'TMPDIR')},
              'stdout': str(paths['stdout.log']), 'stderr': str(paths['stderr.log'])}
    paths['json'].write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record), flush=True)
    return code

if __name__ == '__main__':
    raise SystemExit(main())
