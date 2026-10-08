"""Capture exact native pytest commands and originals in the owned A scope."""
from pathlib import Path
import datetime
import json
import os
import subprocess
import sys
import time

root = Path('<WORKSPACE>').resolve()
out = Path(__file__).resolve().parent
runtime = root / '.h26/an'
source = root / '.h26/s'
assert out.is_relative_to(root) and runtime.resolve().is_relative_to(root)
name = sys.argv[1]
assert name.isidentifier()
assert not (runtime / name).exists()
env = os.environ.copy()
env.update(PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
           TMPDIR=str(root / '.h26/tmp'),
           PYTHONPATH=':'.join(map(str, (source, source / 'tests', source / 'examples/harnessaudit_office/src', out))))
command = [str(root / '.p26/v/bin/python'), '-m', 'pytest', '-q', *sys.argv[2:],
           '-p', 'no:cacheprovider', '--basetemp=' + str(runtime / name),
           '--junitxml=' + str(out / (name + '.xml'))]
record = {'command': command, 'cwd': str(source),
          'environment': {k: env[k] for k in ('PYTHONDONTWRITEBYTECODE', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'TMPDIR', 'PYTHONPATH')},
          'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
receipt = out / (name + '.command.json')
assert not receipt.exists()
receipt.write_text(json.dumps(record, indent=2))
start = time.monotonic()
with (out / (name + '.stdout')).open('wb') as so, (out / (name + '.stderr')).open('wb') as se:
    result = subprocess.run(command, cwd=source, env=env, stdout=so, stderr=se)
record.update(exit_code=result.returncode, elapsed_seconds=time.monotonic() - start,
              ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
receipt.write_text(json.dumps(record, indent=2))
print(json.dumps(record, indent=2))
print((out / (name + '.stdout')).read_text())
print((out / (name + '.stderr')).read_text())
sys.exit(result.returncode)
