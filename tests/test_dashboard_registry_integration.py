"""Local Viewer entrypoint wiring, not a managed-installation certification."""
from contextlib import contextmanager

import pytest

from cpn.frontend import dashboard
from cpn.rpnh import update_install


@pytest.mark.parametrize('fails', [False, True])
def test_dashboard_main_keeps_existing_lifecycle_lease_for_server(monkeypatch, fails):
    events = []

    @contextmanager
    def lease():
        events.append('enter')
        try:
            yield
        finally:
            events.append('exit')

    def serve():
        assert events == ['enter']
        events.append('serve')
        if fails:
            raise RuntimeError('server stopped')
        return 'done'

    monkeypatch.setattr(update_install, 'lifecycle_lease', lease)
    monkeypatch.setattr(dashboard, '_main', serve)
    if fails:
        with pytest.raises(RuntimeError, match='server stopped'):
            dashboard.main()
    else:
        assert dashboard.main() == 'done'
    assert events == ['enter', 'serve', 'exit']
