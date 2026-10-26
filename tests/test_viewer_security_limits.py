"""Isolated loopback attack simulations; never connect to external services."""
from contextlib import contextmanager
import json
import socket
import threading
import time
import tracemalloc

import pytest

from cpn.frontend import server
from test_net_viewer import _projection, _raw_request


@contextmanager
def running(provider=_projection, **limits):
    httpd = server._ProjectionHTTPServer(provider, "127.0.0.1", 0, False, **limits)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, thread
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=1)
        assert not thread.is_alive()


def await_condition(predicate, seconds=1):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        time.sleep(0.005)
    assert predicate()


@pytest.mark.parametrize("route", ["/api/v1/net", "/api/v1/dashboard", "/api/v1/history", "/api/v2/worksets"])
def test_provider_failures_never_disclose_exception_text(route):
    secret = "secret-token /private/registry.db SELECT private_column"

    class Broken:
        def __call__(self):
            raise RuntimeError(secret)
        dashboard = history = worksets = __call__

    response = server.handle_request(Broken(), "GET", route)
    assert response.status >= 400
    assert secret.encode() not in response.body
    assert json.loads(response.body)["error"] in {"projection_unavailable", "read_failed", "workset_unavailable"}
    assert server.handle_request(Broken(), "HEAD", route).body == b""


def test_response_and_graph_budgets_are_explicit(monkeypatch):
    monkeypatch.setattr(server, "_MAX_RESPONSE_BYTES", 256)
    response = server.handle_request(_projection, "GET", "/api/v1/net")
    assert response.status == 413
    assert json.loads(response.body) == {"error": "scope_limit"}
    assert server.handle_request(_projection, "HEAD", "/api/v1/net").body == b""
    monkeypatch.setattr(server, "_MAX_RESPONSE_BYTES", 100000)
    monkeypatch.setattr(server, "_MAX_GRAPH_NODES", 2)
    assert server.handle_request(_projection, "GET", "/api/v1/net").status == 413


def test_oversized_escape_is_rejected_before_large_encoder_allocation(monkeypatch):
    value = "\x01" * (2 * 1024 * 1024)
    monkeypatch.setattr(server, "_MAX_RESPONSE_BYTES", 256)
    tracemalloc.start()
    try:
        with pytest.raises(server._ResponseLimitError):
            server._json_body({"value": value})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 65536


@pytest.mark.parametrize("value", [
    {True: (None, False, 3, 0.5), None: "value", 12: "integer key"},
    {2.5: ["中文", "🙂", "\x01\n\t\b\r\f\\\"", ""]},
    [{"same": [1, 2]}, {"same": [1, 2]}],
    [], {}, (), 123, -5.3e-15, "plain ASCII", "é" * 17000,
])
def test_preflight_matches_json_byte_count_and_exact_budget(monkeypatch, value):
    expected = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    assert server._preflight_json(value) == len(expected)
    monkeypatch.setattr(server, "_MAX_RESPONSE_BYTES", len(expected))
    assert server._json_body(value) == expected
    monkeypatch.setattr(server, "_MAX_RESPONSE_BYTES", len(expected) - 1)
    with pytest.raises(server._ResponseLimitError):
        server._json_body(value)


def test_preflight_allows_shared_values_but_rejects_cycles_and_nonfinite():
    child = {"name": "shared"}
    value = [child, child]
    assert server._preflight_json(value) == len(json.dumps(value).encode())
    child["cycle"] = child
    with pytest.raises(ValueError):
        server._json_body(child)
    for value in (float("nan"), {float("inf"): 1}, {"v": float("-inf")}):
        with pytest.raises(ValueError):
            server._json_body(value)


def test_static_symlink_cannot_escape_packaged_root(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    sentinel = tmp_path / "private.txt"
    sentinel.write_text("DO_NOT_DISCLOSE")
    (static / "app.js").symlink_to(sentinel)
    monkeypatch.setattr(server, "_STATIC_ROOT", static)
    result = server.handle_request(_projection, "GET", "/app.js")
    assert result.status == 503 and b"DO_NOT_DISCLOSE" not in result.body


def test_concurrency_cap_precedes_thread_creation_and_recovers():
    with running(max_connections=2, request_timeout_seconds=0.3) as (httpd, _):
        clients = []
        try:
            for _ in range(2):
                client = socket.create_connection(("127.0.0.1", httpd.bound_port), timeout=1)
                client.sendall(b"GET /api/v1/net HTTP/1.1\r\n")
                clients.append(client)
            await_condition(lambda: httpd.active_connections == 2)
            status, _ = _raw_request(httpd, headers=(("Host", httpd.authority),))
            assert status == 503
            status, body = _raw_request(httpd, method="HEAD", headers=(("Host", httpd.authority),))
            assert status == 503 and body == b""
            assert httpd.active_connections <= 2
        finally:
            for client in clients:
                client.close()
        await_condition(lambda: httpd.active_connections == 0)
        assert _raw_request(httpd, headers=(("Host", httpd.authority),))[0] == 200


def test_thread_start_failure_releases_the_slot(monkeypatch):
    httpd = server._ProjectionHTTPServer(_projection, "127.0.0.1", 0, False, max_connections=1)
    one, two = socket.socketpair()
    def failed_start(*_args):
        raise RuntimeError("thread unavailable")
    try:
        monkeypatch.setattr(server.ThreadingHTTPServer, "process_request", failed_start)
        with pytest.raises(RuntimeError):
            httpd.process_request(one, ("127.0.0.1", 1))
        assert httpd.active_connections == 0
        assert httpd._slots.acquire(blocking=False)
        httpd._slots.release()
    finally:
        one.close()
        two.close()
        httpd.server_close()


def test_absolute_deadline_closes_a_trickling_header():
    with running(request_timeout_seconds=0.12) as (httpd, _):
        client = socket.create_connection(("127.0.0.1", httpd.bound_port), timeout=1)
        started = time.monotonic()
        try:
            client.sendall(b"GET /api/v1/net HTTP/1.1\r\nX-Slow: ")
            for _ in range(8):
                time.sleep(0.035)
                try:
                    client.sendall(b"x")
                except OSError:
                    break
            assert client.recv(1) == b""
            assert time.monotonic() - started < 0.6
            await_condition(lambda: httpd.active_connections == 0)
        finally:
            client.close()


def test_finite_lifetime_ends_even_with_continuous_valid_refresh():
    with running(session_timeout_seconds=0.16) as (httpd, thread):
        deadline = time.monotonic() + 1
        while thread.is_alive() and time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", httpd.bound_port), timeout=0.3) as client:
                    client.sendall(("GET /api/v1/net HTTP/1.1\r\nHost: "
                                    + httpd.authority + "\r\n\r\n").encode())
                    # The listener may expire during a request, legitimately
                    # closing the socket before any status line is produced.
                    client.recv(65536)
            except OSError:
                break
            time.sleep(0.015)
        thread.join(timeout=1)
        assert httpd.expired and not thread.is_alive()
        assert httpd.socket.fileno() == -1
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", httpd.bound_port), timeout=0.2)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_invalid_lifetimes_never_bind(value):
    with pytest.raises(ValueError):
        server._ProjectionHTTPServer(_projection, "127.0.0.1", 0, False,
                                     session_timeout_seconds=value)


@pytest.mark.parametrize("headers", [
    (("Content-Length", "2"),),
    (("Content-Length", "0"), ("Content-Length", "0")),
    (("Transfer-Encoding", "chunked"),),
])
def test_read_only_routes_reject_request_body_framing(headers):
    with running() as (httpd, _):
        status, body = _raw_request(httpd, headers=(("Host", httpd.authority),) + headers)
        assert status == 400
        assert json.loads(body) == {"error": "request_body_not_allowed"}


def test_header_budget_and_parser_errors_are_sanitized():
    with running() as (httpd, _):
        status, body = _raw_request(httpd, headers=(("Host", httpd.authority), ("X-Large", "s" * 33000)))
        assert status == 431 and b"ssss" not in body
        status, body = _raw_request(httpd, method="PRIVATE_SECRET_METHOD", headers=(("Host", httpd.authority),))
        assert status == 501 and b"PRIVATE_SECRET" not in body
        status, _ = _raw_request(httpd, path="/" + "q" * 8192, headers=(("Host", httpd.authority),))
        assert status == 400
        status, _ = _raw_request(httpd, path="/api/v1/net#", headers=(("Host", httpd.authority),))
        assert status == 400


def test_security_headers_apply_to_success_and_errors():
    for path in ("/", "/does-not-exist", "/api/v1/net"):
        response = server.handle_request(_projection, "GET", path)
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "'unsafe-eval'" not in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("seconds", ["0", "30"])
def test_cpn_view_cli_forwards_explicit_session_lifetime(monkeypatch, seconds):
    from cpn import cli
    from cpn.frontend import dashboard
    seen = {}
    monkeypatch.setattr(dashboard, "RegistryDashboard", lambda *_args, **_kwargs: _projection)
    monkeypatch.setattr(server, "serve_projection", lambda _provider, **kwargs: seen.update(kwargs))
    assert cli.main(["net", "view", "--run", "unused", "--no-open",
                     "--session-timeout", seconds]) == 0
    assert seen["session_timeout_seconds"] == float(seconds)


@pytest.mark.parametrize("seconds", ["nan", "inf", "-1"])
def test_cpn_view_cli_rejects_invalid_lifetime_before_read(monkeypatch, seconds):
    from cpn import cli
    from cpn.frontend import dashboard
    monkeypatch.setattr(dashboard, "RegistryDashboard", lambda *_args, **_kwargs: pytest.fail("unexpected Registry read"))
    with pytest.raises(SystemExit) as result:
        cli.main(["net", "view", "--run", "unused", "--session-timeout", seconds])
    assert result.value.code == 2


def test_adapter_view_cli_forwards_lifetime_without_native_runtime(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from cpn.frontend.net_view_adapters import _cli
    from cpn.frontend import dashboard
    seen = {}
    binding = SimpleNamespace(run_dir=tmp_path, catalog=object())
    monkeypatch.setattr(dashboard, "RegistryDashboard", lambda *_args, **_kwargs: _projection)
    monkeypatch.setattr(dashboard, "load_presentation", lambda _path: None)
    monkeypatch.setattr(_cli, "serve_projection", lambda _provider, **kwargs: seen.update(kwargs) or 0)
    assert _cli.run_cli("codex", lambda *_args, **_kwargs: binding,
                        ["--root", str(tmp_path), "--thread-id", "thread",
                         "--view", "--no-open", "--session-timeout", "42"]) == 0
    assert seen["session_timeout_seconds"] == 42
