"""Independent D0 runner: fail closed and count every forbidden boundary attempt."""
import os
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
blocked = []
def forbid(label):
    blocked.append(label)
    raise AssertionError('OFFLINE_SENTINEL_BLOCKED: ' + label)
def audit(event, args):
    if event.startswith(('socket.', 'subprocess.', 'os.exec', 'os.spawn')) or event in (
            'os.system', 'os.fork', 'os.forkpty', 'pty.spawn', 'pty.openpty', 'urllib.Request'):
        forbid(event)
sys.addaudithook(audit)
import socket, urllib.request, subprocess, pty

def forbidden(*args, **kwargs):
    forbid('socket/URL/subprocess/PTY direct call')
socket.socket = forbidden
urllib.request.urlopen = forbidden
subprocess.Popen = forbidden
os.openpty = forbidden
os.forkpty = forbidden
pty.openpty = forbidden
pty.spawn = forbidden
import pytest
code = pytest.main(sys.argv[1:])
print('OFFLINE_SENTINEL_ATTEMPTS=' + str(len(blocked)), flush=True)
if blocked:
    print('OFFLINE_SENTINEL_EVENTS=' + repr(blocked), flush=True)
    raise SystemExit(91)
raise SystemExit(code)
