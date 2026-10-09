import os
import sys
from pathlib import Path
source = Path(__file__).resolve().parents[1] / 'source'
os.chdir(source)
sys.path.insert(0, str(source))
sys.path.insert(0, str(source / 'tests'))
def guard(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.spawn'}:
        raise RuntimeError(f'Independent review forbids external execution: {event}')
sys.addaudithook(guard)
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))
