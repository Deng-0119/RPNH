from __future__ import annotations

from contextlib import contextmanager
import json
import socket
import threading

import pytest

from cpn.frontend import server as viewer_server
from cpn.frontend.server import handle_request, serve_projection


def _projection():
    return {
        "schema_version": "rpnh/net_view/v1",
        "source": {"mode": "initial_configured"},
        "summary": {"title": "test net"},
        "nodes": [
            {
                "id": "p0", "label": "start", "kind": "place",
                "category": "place", "hidden_by_default": False,
            },
            {
                "id": "t0", "label": "run", "kind": "transition",
                "category": "execution", "hidden_by_default": False,
            },
            {
                "id": "r0", "label": "capacity", "kind": "place",
                "category": "resource", "hidden_by_default": True,
            },
        ],
        "edges": [
            {
                "id": "e0", "source": "p0", "target": "t0",
                "kind": "arc", "weight": 1, "outcome": None,
                "hidden_by_default": False,
            },
            {
                "id": "e1", "source": "r0", "target": "t0",
                "kind": "arc", "weight": 1, "outcome": None,
                "hidden_by_default": True,
            },
        ],
    }


@contextmanager
def _running_viewer(provider, host="127.0.0.1"):
    try:
        httpd = viewer_server._ProjectionHTTPServer(provider, host, 0, False)
    except OSError as exc:
        if host == "::1":
            pytest.skip(f"IPv6 loopback unavailable: {exc}")
        raise
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _raw_request(httpd, *, version="HTTP/1.1", headers=(), method="GET", path="/api/v1/net"):
    lines = [f"{method} {path} {version}", *(f"{name}: {value}" for name, value in headers),
             "Connection: close", "", ""]
    request = "\r\n".join(lines).encode("ascii")
    with socket.socket(httpd.address_family, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect((httpd.bound_address.compressed, httpd.bound_port))
        client.sendall(request)
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = b"".join(chunks)
    status = int(response.split(b"\r\n", 1)[0].split()[1])
    return status, response.partition(b"\r\n\r\n")[2]


def test_viewer_api_and_static_assets_are_read_only() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    get = handle_request(provider, "GET", "/api/v1/net")
    assert get.status == 200
    assert json.loads(get.body)["schema_version"] == "rpnh/net_view/v1"
    assert calls == 1
    head = handle_request(provider, "HEAD", "/api/v1/net")
    assert head.status == 200 and head.body == b"" and calls == 2
    rejected = handle_request(provider, "POST", "/api/v1/net")
    assert rejected.status == 405 and calls == 2
    index = handle_request(provider, "GET", "/", show_resources=True)
    assert index.status == 200
    assert b'"showResources": true' in index.body


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::1%lo"])
def test_viewer_rejects_non_literal_or_non_loopback_bind(host) -> None:
    with pytest.raises(ValueError, match="回环"):
        serve_projection(_projection, host=host, open_browser=False)


def test_viewer_reports_a_moving_registry_head_without_partial_graph() -> None:
    def changed():
        raise RuntimeError("Registry changed during inspection; refresh")
    response = handle_request(changed, "GET", "/api/v1/net")
    assert response.status == 500
    value = json.loads(response.body)
    assert set(value) == {"error"} and "Registry changed" in value["error"]


def test_ipv4_wire_boundary_accepts_only_the_bound_authority_and_same_origin() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    with _running_viewer(provider) as httpd:
        authority = httpd.authority
        assert httpd.address_family == socket.AF_INET
        assert authority == f"127.0.0.1:{httpd.bound_port}"

        status, body = _raw_request(httpd, headers=(("Host", authority),))
        assert status == 200 and json.loads(body)["schema_version"] == "rpnh/net_view/v1"
        status, _ = _raw_request(httpd, headers=(
            ("Host", authority), ("Origin", httpd.origin)))
        assert status == 200
        status, body = _raw_request(
            httpd, method="HEAD", headers=(("Host", authority),))
        assert status == 200 and body == b""
        status, _ = _raw_request(
            httpd, method="POST", headers=(("Host", authority),))
        assert status == 405
        status, _ = _raw_request(
            httpd, path="/style.css", headers=(("Host", authority),))
        assert status == 200
        assert calls == 3


def test_incomplete_client_does_not_block_other_viewer_requests() -> None:
    with _running_viewer(_projection) as httpd:
        httpd.request_timeout_seconds = 0.2
        slow = socket.socket(httpd.address_family, socket.SOCK_STREAM)
        slow.settimeout(1)
        try:
            slow.connect((
                httpd.bound_address.compressed, httpd.bound_port))
            slow.sendall(b"GET /api/v1/net HTTP/1.1\r\n")

            status, body = _raw_request(
                httpd, headers=(("Host", httpd.authority),))
            assert status == 200
            assert json.loads(body)["schema_version"] == "rpnh/net_view/v1"
            assert slow.recv(1) == b""
        finally:
            slow.close()


def test_wire_boundary_rejects_invalid_host_before_provider() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    with _running_viewer(provider) as httpd:
        authority = httpd.authority
        wrong_port = 1 if httpd.bound_port != 1 else 2
        rejected_headers = [
            (),
            (("Host", authority), ("Host", authority)),
            (("Host", f"127.0.0.2:{httpd.bound_port}"),),
            (("Host", f"127.0.0.1:{wrong_port}"),),
            (("Host", f"user@{authority}"),),
            (("Host", f"http://{authority}"),),
            (("Host", f"{authority}:9"),),
            (("Host", f"127.0.0.1:0{httpd.bound_port}"),),
        ]
        for headers in rejected_headers:
            status, _ = _raw_request(httpd, headers=headers)
            assert status == 400
        assert calls == 0


def test_wire_boundary_rejects_invalid_origin_before_provider() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    with _running_viewer(provider) as httpd:
        host = (("Host", httpd.authority),)
        wrong_port = 1 if httpd.bound_port != 1 else 2
        rejected_origins = [
            (("Origin", httpd.origin), ("Origin", httpd.origin)),
            (("Origin", f"http://127.0.0.2:{httpd.bound_port}"),),
            (("Origin", f"http://127.0.0.1:{wrong_port}"),),
            (("Origin", f"http://user@{httpd.authority}"),),
            (("Origin", f"{httpd.origin}/"),),
            (("Origin", f"{httpd.origin}?"),),
            (("Origin", f"{httpd.origin}#"),),
            (("Origin", f"https://{httpd.authority}"),),
            (("Origin", "null"),),
        ]
        for origin_headers in rejected_origins:
            status, _ = _raw_request(httpd, headers=host + origin_headers)
            assert status == 400
        assert calls == 0


@pytest.mark.parametrize("target", (
    "http://attacker.invalid/api/v1/net",
    "//api/v1/net",
    "api/v1/net",
    "/api/v1/net#fragment",
))
def test_wire_boundary_rejects_non_origin_form_target_before_provider(
        target: str,
) -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    with _running_viewer(provider) as httpd:
        status, _ = _raw_request(
            httpd, path=target, headers=(("Host", httpd.authority),))
        assert status == 400 and calls == 0


@pytest.mark.parametrize("authority,origin,address", (
    ("127.0.0.1", "http://127.0.0.1", "127.0.0.1"),
    ("[::1]", "http://[::1]", "::1"),
))
def test_default_http_port_accepts_browser_canonical_authority(
        authority: str, origin: str, address: str,
) -> None:
    expected = viewer_server.ip_address(address)
    assert viewer_server._format_authority(expected, 80) == authority
    assert viewer_server._authority_matches(authority, expected, 80)
    assert viewer_server._authority_matches(f"{authority}:80", expected, 80)
    assert viewer_server._origin_matches(origin, expected, 80)
    assert viewer_server._origin_matches(f"{origin}:80", expected, 80)


def test_http_1_0_also_requires_host_fail_closed() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    with _running_viewer(provider) as httpd:
        # Although HTTP/1.0 did not require Host, the Viewer cannot validate the
        # browser-facing authority without it. Browsers use HTTP/1.1 or newer.
        status, _ = _raw_request(httpd, version="HTTP/1.0")
        assert status == 400 and calls == 0
        status, _ = _raw_request(
            httpd, version="HTTP/1.0", headers=(("Host", httpd.authority),))
        assert status == 200 and calls == 1


def test_ipv6_loopback_uses_bracketed_authority_and_ipv6_socket() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    with _running_viewer(provider, "::1") as httpd:
        assert httpd.address_family == socket.AF_INET6
        assert httpd.authority == f"[::1]:{httpd.bound_port}"
        assert httpd.origin == f"http://[::1]:{httpd.bound_port}"
        status, _ = _raw_request(httpd, headers=(
            ("Host", httpd.authority), ("Origin", httpd.origin)))
        assert status == 200 and calls == 1

        status, _ = _raw_request(
            httpd, headers=(("Host", f"::1:{httpd.bound_port}"),))
        assert status == 400 and calls == 1
