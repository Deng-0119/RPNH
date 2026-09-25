"""Small local HTTP surface for a read-only Petri-net projection.

The provider is deliberately supplied by the caller.  This module neither
knows about nor imports Registry code, and the request helper is usable in
tests without binding a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, parse_qs
import webbrowser


ProjectionProvider = Callable[[], Mapping[str, Any]]
_STATIC_ROOT = Path(__file__).with_name("static")
_SCHEMA_VERSION = "rpnh/net_view/v1"
_ASSETS = {
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    **{f"/{name}.mjs": (f"{name}.mjs", "application/javascript; charset=utf-8")
       for name in ("model", "layout", "renderer", "panels", "dashboard-model", "i18n", "messages", "canvas-text", "overview", "wire-geometry")},
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
    if not address.is_loopback:
        raise ValueError("只允许绑定回环地址")


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

    class ProjectionHandler(BaseHTTPRequestHandler):
        def _send(self) -> None:
            response = handle_request(provider, self.command, self.path,
                                      show_resources=show_resources)
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        do_GET = _send
        do_HEAD = _send

        def do_POST(self) -> None: self._send()
        def do_PUT(self) -> None: self._send()
        def do_PATCH(self) -> None: self._send()
        def do_DELETE(self) -> None: self._send()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    with HTTPServer((host, port), ProjectionHandler) as server:
        bound_port = int(server.server_port)
        url = f"http://{host}:{bound_port}/"
        print(f"PetriNet 只读查看器：{url}")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return bound_port
