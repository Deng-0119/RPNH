"""Workspace-local command recorder; retain stdout, stderr and actual exit."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('<WORKSPACE>').resolve()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--pythonpath')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    assert command and args.cwd.resolve().is_relative_to(ROOT)
    paths = {k: Path(str(args.out) + '.' + k) for k in ('stdout.log', 'stderr.log', 'json')}
    for p in paths.values():
        assert p.resolve().is_relative_to(ROOT) and not p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', TMPDIR=str(ROOT / '.v26/tmp'))
    if args.pythonpath:
        env['PYTHONPATH'] = args.pythonpath
    tick = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    with paths['stdout.log'].open('xb') as out, paths['stderr.log'].open('xb') as err:
        p = subprocess.Popen(command, cwd=args.cwd, env=env, stdout=out, stderr=err)
        print(json.dumps({'pid': p.pid, 'out': str(args.out), 'started_at_utc': started}), flush=True)
        code = p.wait()
    result = {'command': command, 'cwd': str(args.cwd), 'pid': p.pid,
              'started_at_utc': started, 'finished_at_utc': datetime.now(timezone.utc).isoformat(),
              'elapsed_seconds': time.monotonic() - tick, 'exit_code': code,
              'environment': {k: env.get(k) for k in ('PYTHONPATH', 'PYTHONDONTWRITEBYTECODE',
                    'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'TMPDIR', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS')}}
    paths['json'].write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
