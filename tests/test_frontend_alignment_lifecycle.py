"""Current frontend lifetime integration; no model, Registry, or native host."""
from types import SimpleNamespace
from threading import get_ident
import pytest
from cpn.rpnh.collaboration.project_local import LocalProjectApplication
from cpn.frontend import server


@pytest.mark.parametrize('expired,fileno', [(True, -1), (True, 3), (False, -1)])
def test_project_viewer_reopens_expired_or_closed_listener(monkeypatch, expired, fileno):
    events = []
    old = SimpleNamespace(expired=expired, socket=SimpleNamespace(fileno=lambda: fileno),
        shutdown=lambda: events.append('shutdown-old'), server_close=lambda: events.append('close-old'))
    created = []
    def create(*args):
        item = SimpleNamespace(expired=False, socket=SimpleNamespace(fileno=lambda: 5),
            origin='http://127.0.0.1:2345', serve_forever=lambda: None,
            shutdown=lambda: events.append('shutdown-new'), server_close=lambda: events.append('close-new'))
        created.append(item)
        return item
    monkeypatch.setattr(server, '_ProjectionHTTPServer', create)
    monkeypatch.setattr('threading.Thread', lambda **kw: SimpleNamespace(start=lambda: events.append('started')))
    app = object.__new__(LocalProjectApplication)
    app.owner_thread = get_ident()
    app.endpoints = {'owner': SimpleNamespace(viewer=object(), _authorize=lambda **kw: events.append('authorize'))}
    app.viewer_servers = {'owner': old}
    app.readers = {}
    first = app.process('owner', {'op': 'viewer-open'})
    assert first['url'] == 'http://127.0.0.1:2345/'
    assert app.process('owner', {'op': 'viewer-open'}) == first
    assert len(created) == 1
    assert events[:4] == ['authorize', 'shutdown-old', 'close-old', 'started']
    app.close_readers()
    app.close_readers()
    assert events.count('close-new') == 1
    assert app.viewer_servers == {}


def test_comparison_post_keeps_precise_body_boundary():
    from email.message import Message
    from cpn.frontend.server import _ProjectionHandler
    handler = object.__new__(_ProjectionHandler)
    handler.command = 'POST'
    handler.requestline = 'POST /api/v2/comparison-context HTTP/1.1'
    handler.headers = Message()
    handler.headers['Host'] = '127.0.0.1:2345'
    handler.headers['Origin'] = 'http://127.0.0.1:2345'
    handler.headers['Content-Length'] = '2'
    handler.headers['Content-Type'] = 'application/json'
    handler.server = SimpleNamespace(bound_address=server.ip_address('127.0.0.1'), bound_port=2345)
    rejection, target = handler._request_boundary()
    assert rejection is None and target == '/api/v2/comparison-context'
    handler.requestline = 'POST /api/v1/net HTTP/1.1'
    rejection, _ = handler._request_boundary()
    assert rejection.status == 400
    handler.requestline = 'POST /api/v2/comparison-context HTTP/1.1'
    handler.headers['Transfer-Encoding'] = 'chunked'
    assert handler._request_boundary()[0].status == 400


@pytest.mark.parametrize('value', [{"z": [1, "é", "\x01"], "a": {"next": True}}, []])
def test_canonical_comparison_encoding_has_exact_budget(value, monkeypatch):
    from cpn.frontend.comparison_context import _json
    expected = _json(value).encode('utf-8')
    assert server._preflight_json(value, compact=True) == len(expected)
    monkeypatch.setattr(server, '_MAX_RESPONSE_BYTES', len(expected))
    assert server._json_body(value, canonical=True) == expected
    monkeypatch.setattr(server, '_MAX_RESPONSE_BYTES', len(expected) - 1)
    with pytest.raises(server._ResponseLimitError):
        server._json_body(value, canonical=True)


def test_bootstrap_expansion_still_obeys_response_budget(tmp_path, monkeypatch):
    marker = b"__NET_VIEW_BOOTSTRAP__"
    raw = marker * 8
    (tmp_path / 'index.html').write_bytes(raw)
    monkeypatch.setattr(server, '_STATIC_ROOT', tmp_path)
    monkeypatch.setattr(server, '_MAX_RESPONSE_BYTES', len(raw))
    response = server.handle_request(lambda: {}, 'GET', '/')
    assert response.status == 413
    assert response.body == b'{"error": "scope_limit"}'
    assert server.handle_request(lambda: {}, 'HEAD', '/').body == b''
