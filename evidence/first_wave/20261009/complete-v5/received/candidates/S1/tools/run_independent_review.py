"""Run packaged independent checks against an explicitly selected checkout."""
import argparse
import os
from pathlib import Path
import runpy
import socket
import ssl
import sys
import urllib.request
import pytest

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--mode', choices=('pytest', 'lowlevel', 'indices', 'empty-consume', 'read-edit'), default='pytest')
args, extra = parser.parse_known_args()
source = args.source.resolve()
review = Path(__file__).resolve().parents[1] / 'independent_review'
os.environ['RPNH_SOURCE'] = str(source)
sys.path[:0] = [str(source), str(source / 'tests'), str(review)]

def forbidden(*args, **kwargs):
    raise AssertionError('independent offline review forbids sockets and URL retrieval')

class OfflineTransport:
    @pytest.fixture(autouse=True)
    def no_external_transport(self, monkeypatch):
        monkeypatch.setattr(socket, 'socket', forbidden)
        monkeypatch.setattr(urllib.request, 'urlopen', forbidden)

if args.mode == 'pytest':
    raise SystemExit(pytest.main(['-p', 'no:cacheprovider', '-q', str(review / 'test_independent_static_lease.py'), *extra], plugins=[OfflineTransport()]))
if extra:
    parser.error('extra pytest options are only accepted with --mode pytest')
socket.socket = forbidden
urllib.request.urlopen = forbidden
scripts = {'lowlevel': 'probe_lowlevel.py', 'indices': 'probe_indices.py',
    'empty-consume': 'probe_empty_consume.py', 'read-edit': 'probe_read_edit.py'}
runpy.run_path(str(review / scripts[args.mode]), run_name='__main__')
