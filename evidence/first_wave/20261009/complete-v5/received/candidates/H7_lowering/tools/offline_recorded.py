"""Independent pure-D0 launcher. Sentinel precedes pytest and product imports."""
import os
import sys
import json
from pathlib import Path

os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
forbidden_events = {'subprocess.Popen', 'os.system', 'os.fork', 'os.forkpty', 'os.exec',
                    'os.posix_spawn', 'os.spawn', 'pty.spawn', 'pty.openpty',
                    'socket.__new__', 'socket.connect', 'socket.bind', 'socket.getaddrinfo',
                    'socket.gethostbyname', 'urllib.Request'}
blocked = []
def guard(event, args):
    if event in forbidden_events:
        blocked.append(event)
        raise AssertionError('Independent D0 sentinel forbids ' + event)
sys.addaudithook(guard)
# os.openpty does not emit a portable CPython audit event, so block it directly.
import pty

def block_direct(name):
    def forbidden(*args, **kwargs):
        blocked.append('direct.' + name)
        raise AssertionError('Independent D0 sentinel forbids ' + name)
    return forbidden
for module, name in ((os,'openpty'), (os,'forkpty'), (pty,'openpty'), (pty,'spawn')):
    if hasattr(module,name):
        setattr(module,name,block_direct(module.__name__+'.'+name))

source = Path(sys.argv[1]).resolve()
result_path = Path(sys.argv[2]).resolve()
sys.path[:0] = [str(source), str(source / 'tests')]
import pytest
class Results:
    def __init__(self):
        self.outcomes = []
        self.collected = []
    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]
    def pytest_runtest_logreport(self, report):
        if report.when == 'call' or report.failed:
            self.outcomes.append({'nodeid':report.nodeid,'when':report.when,
                                  'outcome':report.outcome,'duration':report.duration})
recorder = Results()
status = pytest.main(sys.argv[3:], plugins=[recorder])
result_path.write_text(json.dumps({'source':str(source),'exit_status':status,
    'collected':recorder.collected,'outcomes':recorder.outcomes,
    'sentinel_blocked_events':blocked}, indent=2) + '\n')
raise SystemExit(status)
