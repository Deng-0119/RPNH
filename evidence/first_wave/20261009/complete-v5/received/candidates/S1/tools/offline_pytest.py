"""Run selected tests with fail-closed socket and URL-retrieval sentinels."""
import sys
import socket
import urllib.request
from pathlib import Path
import pytest

sys.path.insert(0, str(Path.cwd()))

class OfflineTransport:
    @pytest.fixture(autouse=True)
    def no_external_transport(self, monkeypatch):
        def forbidden(*args, **kwargs):
            raise AssertionError('offline acceptance forbids sockets and URL retrieval')
        monkeypatch.setattr(socket, 'socket', forbidden)
        monkeypatch.setattr(urllib.request, 'urlopen', forbidden)

raise SystemExit(pytest.main(sys.argv[1:], plugins=[OfflineTransport()]))
