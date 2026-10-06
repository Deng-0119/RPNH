"""Small local HTTP surface for a read-only Petri-net projection.

The provider is deliberately supplied by the caller.  This module neither
knows about nor imports Registry code, and the request helper is usable in
tests without binding a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
import socket
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, parse_qs
import webbrowser


ProjectionProvider = Callable[[], Mapping[str, Any]]
_STATIC_ROOT = Path(__file__).with_name("static")
_SCHEMA_VERSION = "rpnh/net_view/v1"
_REQUEST_TIMEOUT_SECONDS = 5.0
_ASSETS = {
    "/source-observation.mjs": ("source-observation.mjs", "text/javascript; charset=utf-8"),
    "/worksets.mjs": ("worksets.mjs", "text/javascript; charset=utf-8"),
    "/firing-activity.mjs": ("firing-activity.mjs", "text/javascript; charset=utf-8"),

    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    **{f"/{name}.mjs": (f"{name}.mjs", "application/javascript; charset=utf-8")
       for name in ("model", "layout", "renderer", "panels", "dashboard-model", "checkpoint-view", "comparison-view", "agent-members", "observation-panel", "i18n", "messages", "canvas-text", "overview", "wire-geometry")},
    **{f"/assets/{name}.js": (f"assets/{name}.js", "application/javascript; charset=utf-8")
       for name in ("joint", "elk-api", "elk-worker")},
    "/assets/manifest.json": ("assets/manifest.json", "application/json; charset=utf-8"),
    **{f"/assets/{name}-LICENSE.txt": (f"assets/{name}-LICENSE.txt", "text/plain; charset=utf-8")
       for name in ("joint", "elk")},
}


@dataclass(frozen=True, slots=True)
class RequestResponse:
    """Pure request result used by the HTTP adapter and focused tests."""

    status: int
    headers: Mapping[str, str]
    body: bytes


def _validate_loopback(host: str) -> None:
    try:
        address = ip_address(host)
    except ValueError as exc:
        raise ValueError("host 必须是明确的回环 IP 地址") from exc
    if not address.is_loopback or getattr(address, "scope_id", None) is not None:
        raise ValueError("只允许绑定回环地址")


def _format_authority(address: IPv4Address | IPv6Address, port: int) -> str:
    host = (
        f"[{address.compressed}]" if address.version == 6
        else address.compressed)
    return host if port == 80 else f"{host}:{port}"


def _parse_ip_authority(
        value: str,
) -> tuple[IPv4Address | IPv6Address, int | None]:
    """Parse the deliberately narrow authority accepted by the Viewer."""
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("invalid authority")
    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1:
            raise ValueError("invalid IPv6 authority")
        host = value[1:closing]
        remainder = value[closing + 1:]
        if "]" in value[closing + 1:] or "%" in host:
            raise ValueError("invalid IPv6 authority")
        address = ip_address(host)
        if address.version != 6:
            raise ValueError("brackets require IPv6")
        if remainder and not remainder.startswith(":"):
            raise ValueError("invalid IPv6 authority")
        port_text = remainder[1:] if remainder else None
    else:
        if value.count(":") > 1:
            raise ValueError("invalid IPv4 authority")
        host, separator, exact_port = value.partition(":")
        port_text = exact_port if separator else None
        address = ip_address(host)
        if address.version != 4:
            raise ValueError("IPv6 requires brackets")
    if port_text is None:
        return address, None
    if (not port_text or not port_text.isascii() or not port_text.isdecimal()
            or (len(port_text) > 1 and port_text.startswith("0"))):
        raise ValueError("invalid port")
    port = int(port_text)
    if not 0 <= port <= 65535:
        raise ValueError("invalid port")
    return address, port


def _authority_matches(
    value: str,
    expected_address: IPv4Address | IPv6Address,
    expected_port: int,
) -> bool:
    try:
        address, port = _parse_ip_authority(value)
    except ValueError:
        return False
    normalized_port = 80 if port is None else port
    return address == expected_address and normalized_port == expected_port


def _origin_matches(
    value: str,
    expected_address: IPv4Address | IPv6Address,
    expected_port: int,
) -> bool:
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        return False
    prefix = "http://"
    if value[:len(prefix)].lower() != prefix:
        return False
    # Origin is a serialized origin, not a URL with a path, query, fragment,
    # credentials, or an inferred/default port. The authority parser rejects
    # all of those extra forms, including userinfo.
    return _authority_matches(value[len(prefix):], expected_address, expected_port)


def _projection_error(message: str) -> ValueError:
    return ValueError(f"PetriNet 投影无效：{message}")


def _validate_projection(projection: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(projection, Mapping):
        raise _projection_error("根对象必须是映射")
    if projection.get("schema_version") != _SCHEMA_VERSION:
        raise _projection_error(f"schema_version 必须为 {_SCHEMA_VERSION}")
    source = projection.get("source")
    if not isinstance(source, Mapping) or source.get("mode") not in {
        "initial_configured", "registry_current",
    }:
        raise _projection_error("source.mode 必须为 initial_configured 或 registry_current")
    if not isinstance(projection.get("summary"), Mapping):
        raise _projection_error("summary 必须是映射")
    nodes = projection.get("nodes")
    edges = projection.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise _projection_error("nodes 和 edges 必须是列表")

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            raise _projection_error(f"nodes[{index}] 必须是映射")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise _projection_error(f"nodes[{index}].id 必须是非空字符串")
        if node_id in node_ids:
            raise _projection_error(f"nodes[{index}].id 重复：{node_id}")
        node_ids.add(node_id)
        if not isinstance(node.get("label"), str):
            raise _projection_error(f"nodes[{index}].label 必须是字符串")
        if node.get("kind") not in {"transition", "place"}:
            raise _projection_error(f"nodes[{index}].kind 必须为 transition 或 place")
        if node.get("category") not in {"execution", "place", "resource"}:
            raise _projection_error(f"nodes[{index}].category 无效")
        if not isinstance(node.get("hidden_by_default"), bool):
            raise _projection_error(f"nodes[{index}].hidden_by_default 必须是布尔值")

    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            raise _projection_error(f"edges[{index}] 必须是映射")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id or edge_id in edge_ids:
            raise _projection_error(f"edges[{index}].id 必须是唯一非空字符串")
        edge_ids.add(edge_id)
        if edge.get("source") not in node_ids or edge.get("target") not in node_ids:
            raise _projection_error(f"edges[{index}] 引用了未知节点")
        if not isinstance(edge.get("kind"), str):
            raise _projection_error(f"edges[{index}].kind 必须是字符串")
        if type(edge.get("weight")) is not int:
            raise _projection_error(f"edges[{index}].weight 必须是整数")
        if edge.get("outcome") is not None and not isinstance(edge.get("outcome"), str):
            raise _projection_error(f"edges[{index}].outcome 必须是字符串或 null")
        if not isinstance(edge.get("hidden_by_default"), bool):
            raise _projection_error(f"edges[{index}].hidden_by_default 必须是布尔值")
    return projection


def _response(status: int, content_type: str, body: bytes = b"") -> RequestResponse:
    return RequestResponse(status, {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; worker-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'self'; form-action 'none'"),
    }, body)


def handle_request(
    provider: ProjectionProvider,
    method: str,
    target: str,
    *,
    show_resources: bool = False,
) -> RequestResponse:
    """Handle one request synchronously without opening a socket.

    The callable serves v1; optional dashboard/history methods are read-only.
    GET and HEAD use the same validated response without opening execution hosts.
    """
    method = method.upper()
    if method not in {"GET", "HEAD"}:
        return _response(
            405, "text/plain; charset=utf-8", "只支持 GET 和 HEAD".encode("utf-8"))
    path = urlsplit(target).path
    if path == "/":
        body = (_STATIC_ROOT / "index.html").read_bytes().replace(
            b"__NET_VIEW_BOOTSTRAP__",
            json.dumps({"showResources": show_resources}).encode("utf-8"),
        )
        response = _response(200, "text/html; charset=utf-8", body)
    elif path in _ASSETS:
        filename, content_type = _ASSETS[path]
        asset = _STATIC_ROOT / filename
        if not asset.is_file():
            response = _response(503, "text/plain; charset=utf-8", (
                "Viewer build assets are missing. Build frontend/net-viewer with "
                "npm ci && npm run build, or install an asset-complete wheel."
            ).encode("utf-8"))
        else:
            response = _response(200, content_type, asset.read_bytes())
    elif path == "/api/v2/source-observation":
        from .source_observation import parse_query, ObservationNotSelected
        import sqlite3
        try:
            params = parse_query(urlsplit(target).query)
            action = getattr(provider, 'source_observation', None)
            if not callable(action):
                raise NotImplementedError('unsupported')
            payload = action(**params)
            if payload.get('schema_version') != 'rpnh/source_observation/v1':
                raise RuntimeError('source observation response version differs')
            response = _response(200, 'application/json; charset=utf-8', json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf8'))
        except NotImplementedError:
            response = _response(501, 'application/json; charset=utf-8', b'{"error":"not_provided"}')
        except ObservationNotSelected:
            response = _response(403, 'application/json; charset=utf-8', b'{"error":"not_selected"}')
        except (ValueError, TypeError, KeyError):
            response = _response(400, 'application/json; charset=utf-8', b'{"error":"invalid_query"}')
        except (RuntimeError, OSError, sqlite3.Error):
            response = _response(503, 'application/json; charset=utf-8', b'{"error":"read_failed"}')
    elif path == "/api/v2/worksets":
        if urlsplit(target).query:
            response = _response(400, "application/json; charset=utf-8", b'{"error":"invalid_query"}')
        elif not callable(getattr(provider, "worksets", None)):
            response = _response(501, "application/json; charset=utf-8", b'{"error":"unsupported"}')
        else:
            try:
                payload = provider.worksets()
                if payload.get("schema_version") not in {"rpnh/workset_view/v1", "rpnh/workset_view/v2"}:
                    raise ValueError("Workset projection schema differs")
                response = _response(200, "application/json; charset=utf-8", json.dumps(payload, allow_nan=False).encode("utf8"))
            except (ValueError, RuntimeError):
                response = _response(409, "application/json; charset=utf-8", b'{"error":"workset_unavailable"}')
    elif path == "/api/v2/firing-activity":
        from .firing_activity import parse_query, ActivityQueryError, ActivityStaleError, ActivityAccessChanged
        from cpn.rpnh.registry._event_store.views import ActivityCursorError
        import sqlite3
        try:
            params = parse_query(urlsplit(target).query)
            action = getattr(provider, "firing_activity", None)
            if not callable(action):
                response = _response(501, "application/json; charset=utf-8", b'{"error":"unsupported"}')
            else:
                payload = action(**params)
                selected = {k: params[k] for k in ("net_ref", "checkpoint_ref", "cut")}
                if payload.get("schema_version") != "rpnh/firing_activity/v1" or payload.get("selector") != selected:
                    raise RuntimeError("activity response identity differs")
                response = _response(200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf8"))
        except (ActivityQueryError, ActivityCursorError):
            response = _response(400, "application/json; charset=utf-8", b'{"error":"invalid_query"}')
        except ActivityStaleError:
            response = _response(409, "application/json; charset=utf-8", b'{"error":"stale_observation"}')
        except ActivityAccessChanged:
            response = _response(403, "application/json; charset=utf-8", b'{"error":"access_changed"}')
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error):
            response = _response(503, "application/json; charset=utf-8", b'{"error":"read_failed"}')
    elif path == "/api/v2/comparison-view":
        from .comparison_view import (parse_query, validate_comparison_response,
            ComparisonInvalid, ComparisonStale, ComparisonAccessChanged)
        import sqlite3
        try:
            params = parse_query(urlsplit(target).query)
            action = getattr(provider, "comparison_view", None)
            if not callable(action):
                response = _response(501, "application/json; charset=utf-8", b'{"error":"unsupported"}')
            else:
                payload = action(**params)
                validate_comparison_response(payload, **params)
                response = _response(200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf8"))
        except ComparisonInvalid:
            response = _response(400, "application/json; charset=utf-8", b'{"error":"invalid_query"}')
        except ComparisonStale:
            response = _response(409, "application/json; charset=utf-8", b'{"error":"stale_observation"}')
        except ComparisonAccessChanged:
            response = _response(403, "application/json; charset=utf-8", b'{"error":"access_changed"}')
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError, sqlite3.Error):
            response = _response(503, "application/json; charset=utf-8", b'{"error":"read_failed"}')
    elif path == "/api/v2/checkpoint-view":
        from .checkpoint_view import (token_resource_target, validate_token_resource_response,
            TokenResourceInvalid, TokenResourceStale, TokenResourceAccessChanged)
        import sqlite3
        extended = "token_resource" in parse_qs(urlsplit(target).query, keep_blank_values=True)
        action = getattr(provider, "checkpoint_view", None)
        if not callable(action):
            response = _response(501, "application/json; charset=utf-8", b'{"error":"Saved checkpoint views are unsupported"}')
        else:
            try:
                query = parse_qs(urlsplit(target).query, keep_blank_values=True, strict_parsing=True)
                if set(query) != ({"net_ref", "checkpoint_ref", "cut", "token_resource"} if extended else {"net_ref", "checkpoint_ref", "cut"}) or any(len(v) != 1 for v in query.values()):
                    raise ValueError("checkpoint-view requires exactly net_ref, checkpoint_ref and cut")
                text = query["cut"][0]
                if not text.isascii() or not text.isdecimal() or text.startswith("0"):
                    raise ValueError("cut must be a positive canonical decimal ordinal")
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate reference field")
                        result[key] = value
                    return result
                from .checkpoint_view import selector
                selected = selector(json.loads(query["net_ref"][0], object_pairs_hook=unique_object),
                                    json.loads(query["checkpoint_ref"][0], object_pairs_hook=unique_object), int(text))
                resource_target = token_resource_target(json.loads(query['token_resource'][0], object_pairs_hook=unique_object)) if extended else None
                payload = action(**selected, token_resource=resource_target) if extended else action(**selected)
                if extended and type(payload) is not dict:
                    raise ValueError("invalid metadata response container")
                if payload.get("schema_version") != "rpnh/checkpoint_view/v1" or payload.get("selector") != selected:
                    raise ValueError("checkpoint-view returned a different selector")
                if extended:
                    validate_token_resource_response(payload, resource_target)
                _validate_projection(payload["frame"]["net"])
                response = _response(200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf8"))
            except TokenResourceStale:
                response = _response(409, "application/json; charset=utf-8", b'{"error":"stale_observation"}')
            except TokenResourceAccessChanged:
                response = _response(403, "application/json; charset=utf-8", b'{"error":"access_changed"}')
            except (TypeError, ValueError, KeyError) as exc:
                response = _response(400, "application/json; charset=utf-8", json.dumps({"error": "invalid_target" if extended else str(exc)}, ensure_ascii=False).encode("utf8"))
            except (RuntimeError, OSError, sqlite3.Error) as exc:
                response = _response(503, "application/json; charset=utf-8", json.dumps({"error": "read_failed" if extended else str(exc)}, ensure_ascii=False).encode("utf8"))
    elif path in {"/api/v1/dashboard", "/api/v1/history"}:
        action = getattr(provider, "dashboard" if path.endswith("dashboard") else "history", None)
        if not callable(action):
            response = _response(501, "application/json; charset=utf-8", b'{"error":"This provider supplies only a current net view"}')
        else:
            try:
                query = parse_qs(urlsplit(target).query, keep_blank_values=True)
                allowed = {"cursor"} if path.endswith("dashboard") else {"before", "limit"}
                if set(query) - allowed or any(len(v) != 1 or not v[0].isascii() or not v[0].isdecimal() for v in query.values()):
                    raise ValueError("invalid dashboard query")
                params = {k: int(v[0]) for k, v in query.items()}
                payload = action(**params)
                if path.endswith("dashboard"):
                    _validate_projection(payload["net"])
                response = _response(200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf8"))
            except (TypeError, ValueError, KeyError) as exc:
                response = _response(400, "application/json; charset=utf-8", json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf8"))
            except (RuntimeError, OSError) as exc:
                response = _response(503, "application/json; charset=utf-8", json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf8"))
    elif path == "/api/v1/net":
        try:
            projection = _validate_projection(provider())
            body = json.dumps(projection, ensure_ascii=False, allow_nan=False).encode("utf-8")
            response = _response(200, "application/json; charset=utf-8", body)
        except (TypeError, ValueError, RuntimeError) as exc:
            response = _response(500, "application/json; charset=utf-8", json.dumps({
                "error": str(exc),
            }, ensure_ascii=False).encode("utf-8"))
    else:
        response = _response(404, "text/plain; charset=utf-8", "未找到资源".encode("utf-8"))
    if method == "HEAD":
        return RequestResponse(response.status, response.headers, b"")
    return response


class _ProjectionHandler(BaseHTTPRequestHandler):
    server: "_ProjectionHTTPServer"

    def _send_response(self, response: RequestResponse) -> None:
        self.send_response(response.status)
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _request_boundary(
            self,
    ) -> tuple[RequestResponse | None, str | None]:
        request_parts = self.requestline.split()
        raw_target = request_parts[1] if len(request_parts) == 3 else ""
        target = urlsplit(raw_target)
        if (not raw_target.startswith("/") or raw_target.startswith("//")
                or target.scheme or target.netloc or target.fragment):
            return (
                _response(
                    400, "text/plain; charset=utf-8",
                    "Viewer 只接受 origin-form request target".encode("utf-8")),
                None,
            )

        host_values = self.headers.get_all("Host", [])
        if (len(host_values) != 1
                or not _authority_matches(
                    host_values[0], self.server.bound_address, self.server.bound_port)):
            return (
                _response(
                    400, "text/plain; charset=utf-8",
                    "Host 与 Viewer 监听地址不匹配".encode("utf-8")),
                None,
            )

        origin_values = self.headers.get_all("Origin", [])
        if len(origin_values) > 1 or (
            origin_values and not _origin_matches(
                origin_values[0], self.server.bound_address, self.server.bound_port)
        ):
            return (
                _response(
                    400, "text/plain; charset=utf-8",
                    "Origin 与 Viewer 不同源".encode("utf-8")),
                None,
            )
        return None, raw_target

    def _send(self) -> None:
        rejection, target = self._request_boundary()
        if rejection is not None:
            self._send_response(rejection)
            return
        assert target is not None
        response = handle_request(
            self.server.provider,
            self.command,
            target,
            show_resources=self.server.show_resources,
        )
        self._send_response(response)

    do_GET = _send
    do_HEAD = _send

    def do_POST(self) -> None: self._send()
    def do_PUT(self) -> None: self._send()
    def do_PATCH(self) -> None: self._send()
    def do_DELETE(self) -> None: self._send()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ProjectionHTTPServer(ThreadingHTTPServer):
    """Bounded-request loopback server for the read-only Viewer."""

    daemon_threads = True

    def __init__(
        self,
        provider: ProjectionProvider,
        host: str,
        port: int,
        show_resources: bool,
    ) -> None:
        _validate_loopback(host)
        address = ip_address(host)
        self.address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self.provider = provider
        self.show_resources = show_resources
        self.request_timeout_seconds = _REQUEST_TIMEOUT_SECONDS
        super().__init__((address.compressed, port), _ProjectionHandler)
        self.bound_address = ip_address(self.server_address[0])
        self.bound_port = int(self.server_address[1])
        self.authority = _format_authority(self.bound_address, self.bound_port)
        self.origin = f"http://{self.authority}"

    def get_request(self) -> tuple[socket.socket, Any]:
        request, client_address = super().get_request()
        request.settimeout(self.request_timeout_seconds)
        return request, client_address


def serve_projection(
    provider: ProjectionProvider,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    show_resources: bool = False,
) -> int:
    """Serve a projection on loopback until interrupted, then return its port."""
    _validate_loopback(host)
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port 必须在 0 到 65535 之间")

    with _ProjectionHTTPServer(provider, host, port, show_resources) as server:
        url = f"{server.origin}/"
        print(f"PetriNet 只读查看器：{url}")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return server.bound_port
