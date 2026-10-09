"""Selected D0 only: import-time fail-closed network/process/PTY sentinel."""
import os
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))

def audit(event,args):
    if (event.startswith(('socket.','subprocess.','os.exec','os.spawn'))
            or event in ('os.system','os.fork','os.forkpty','pty.spawn','pty.openpty','urllib.Request')):
        raise AssertionError('offline sentinel blocked '+event)
sys.addaudithook(audit)
import socket,urllib.request,subprocess,pty

def forbidden(*args,**kwargs):
    raise AssertionError('offline acceptance forbids sockets, URLs, subprocesses and PTYs')
socket.socket=forbidden
urllib.request.urlopen=forbidden
subprocess.Popen=forbidden
os.openpty=forbidden
os.forkpty=forbidden
pty.openpty=forbidden
pty.spawn=forbidden
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
