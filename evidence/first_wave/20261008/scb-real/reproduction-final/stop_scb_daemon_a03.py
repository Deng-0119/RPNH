"""Stop only the recorded A03 daemon after the authorized trial has exited."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import signal
import subprocess
import time

ROOT = Path('/home/deng123/RPNH').resolve()
TASK = ROOT / 'task-first-wave-examples-20261008'
ERP = ROOT / 'task-erp-first-wave-20261008'
assert (TASK / 'evidence/scb-real01-exit.json').exists()
owner = json.loads((TASK / 'private/daemon-owner-a03.json').read_bytes())
pid = owner['pid']
proc = Path('/proc') / str(pid)
actual = [part.decode() for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
assert actual == owner['command']
env = dict(os.environ, DOCKER_HOST='unix://' + str(ROOT / '.e26/s'),
           DOCKER_CONFIG=str(ERP / 'config/docker'))
check = subprocess.run([str(ERP / 'work/tools/docker/docker'), 'ps', '-aq'],
    env=env, capture_output=True, text=True, check=True, timeout=15)
assert not check.stdout.strip(), 'containers remain; no signal sent'
os.kill(pid, signal.SIGTERM)
for _ in range(100):
    if not proc.exists() and not (ROOT / '.e26/s').exists():
        break
    time.sleep(0.2)
assert not proc.exists() and not (ROOT / '.e26/s').exists()
receipt = {'status': 'TASK_OWNED_DAEMON_STOPPED',
    'verified_at': datetime.now(timezone.utc).isoformat(), 'daemon_attempt': 'a03',
    'recorded_pid': pid, 'owner_identity_verified': True, 'containers_before_stop': 0,
    'process_absent': True, 'socket_absent': True, 'cache_preserved': True,
    'unrelated_daemons_modified': False,
    'model_counts_source': 'terminal per-checkpoint Registry evidence; not inferred from cleanup'}
target = TASK / 'evidence/daemon-cleanup-scb-real01.json'
assert target.resolve().is_relative_to(ROOT) and not target.exists() and not target.is_symlink()
with target.open('x') as stream:
    json.dump(receipt, stream, indent=2)
    stream.write('\n')
os.chown(target, 1000, 1000)
print(json.dumps(receipt))
