"""DSH applies main's short endpoint helper without moving registered data."""
from pathlib import Path

from cpn.dsh.backend import DshBackend
from test_dsh_backend import request


def test_long_state_root_does_not_prevent_registered_denial(tmp_path):
    root = tmp_path / ('long-' * 30)
    effects = []
    backend = DshBackend(root, 'longroot', effects.append, create=True)
    assert len(str(root / 'longroot' / 'owner.sock').encode()) >= 108
    result = backend.turn(request(session='longroot', allow=False))
    assert result['answer']['status'] == 'denied'
    assert effects == []
    assert (root / 'longroot' / 'main' / '.registry_v1' / 'registry.sqlite3').is_file()
    reopened = DshBackend(root, 'longroot', effects.append)
    assert reopened.history()['committed_history'][0]['answer'] == result['answer']
    assert effects == []
