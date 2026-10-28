"""Small local HTTP surface for a read-only Petri-net projection.

The provider is deliberately supplied by the caller.  This module neither
knows about nor imports Registry code, and the request helper is usable in
tests without binding a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
import socket
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, parse_qs
import webbrowser


ProjectionProvider = Callable[[], Mapping[str, Any]]
_STATIC_ROOT = Path(__file__).with_name("static")
_SCHEMA_VERSION = "rpnh/net_view/v1"
_REQUEST_TIMEOUT_SECONDS = 5.0
_SESSION_TIMEOUT_SECONDS = 3600.0
_MAX_CONNECTIONS = 16
_MAX_TARGET_BYTES = 8192
_MAX_HEADER_BYTES = 32768
_MAX_HEADER_LINES = 64
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_GRAPH_NODES = 100000
_MAX_GRAPH_EDGES = 200000
_ASSETS = {
    "/help-catalog.json": ("help-catalog.json", "application/json; charset=utf-8"),
    "/user-contributions.mjs": ("user-contributions.mjs", "text/javascript; charset=utf-8"),
    "/source-observation.mjs": ("source-observation.mjs", "text/javascript; charset=utf-8"),
    "/worksets.mjs": ("worksets.mjs", "text/javascript; charset=utf-8"),
    "/firing-activity.mjs": ("firing-activity.mjs", "text/javascript; charset=utf-8"),

    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    **{f"/{name}.mjs": (f"{name}.mjs", "application/javascript; charset=utf-8")
       for name in ("model", "layout", "renderer", "panels", "dashboard-model", "checkpoint-view", "comparison-view", "comparison-context", "agent-members", "observation-panel", "i18n", "messages", "canvas-text", "overview", "wire-geometry", "helper", "viewer-runtime")},
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


class _ResponseLimitError(Exception):
    """A projection exceeds the viewer's response budget, not its authority."""


class _ViewerSessionExpired(Exception):
    """Internal signal to leave serve_forever without same-thread shutdown."""


_JSON_ESCAPE = re.compile(r'["\\\x00-\x1f]')


def _preflight_json(value: Any, *, compact: bool = False) -> int:
    """Count exact JSON bytes before C encoding, without copying containers.

    Strings needing escaping are measured in 16 KiB character chunks. The
    request-local short-string cache is bounded. This limits serialization
    output, not Python's process RSS or concurrent mutation by a provider.
    """
    remaining = _MAX_RESPONSE_BYTES
    separator_bytes = 1 if compact else 2
    ancestors: set[int] = set()
    short_strings: dict[str, int] = {}
    string_encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False)

    def reserve(size: int) -> None:
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise _ResponseLimitError

    def string_size(item: str) -> int:
        # Exact built-in strings are immutable. A cached size still passes
        # through reserve() against this request's current remaining budget.
        # Keep subclass operations on the original path.
        if type(item) is str:
            length = len(item)
            cached = short_strings.get(item) if length <= 128 else None
            if cached is not None:
                return cached
            if length + 2 > remaining:
                raise _ResponseLimitError
        else:
            if len(item) + 2 > remaining:
                raise _ResponseLimitError
            cached = short_strings.get(item) if len(item) <= 128 else None
            if cached is not None:
                return cached
        if item.isascii() and _JSON_ESCAPE.search(item) is None:
            size = len(item) + 2
        else:
            size = 2
            for start in range(0, len(item), 16384):
                part = string_encoder.encode(item[start:start + 16384])
                size += len(part.encode("utf-8")) - 2
                if size > remaining:
                    raise _ResponseLimitError
        if len(item) <= 128 and len(short_strings) < 1024:
            short_strings[item] = size
        return size

    def key_string(key: Any) -> str:
        if isinstance(key, str):
            return key
        if key is True:
            return "true"
        if key is False:
            return "false"
        if key is None:
            return "null"
        if isinstance(key, int):
            return int.__repr__(key)
        if isinstance(key, float) and math.isfinite(key):
            return float.__repr__(key)
        raise ValueError("unsupported JSON key")

    def visit(item: Any, depth: int = 0) -> None:
        if depth > 128:
            raise _ResponseLimitError
        if isinstance(item, str):
            reserve(string_size(item))
        elif item is None or item is True:
            reserve(4)
        elif item is False:
            reserve(5)
        elif isinstance(item, int):
            reserve(len(int.__repr__(item)))
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite JSON number")
            reserve(len(float.__repr__(item)))
        elif isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in ancestors:
                raise ValueError("circular JSON value")
            ancestors.add(identity)
            reserve(2)
            try:
                if isinstance(item, dict):
                    for index, (key, child) in enumerate(item.items()):
                        if index:
                            reserve(separator_bytes)  # comma, optional space
                        # Projection dictionaries repeat a small set of keys.
                        # Reuse only this request's exact encoded string size,
                        # never a projection, authorization or mutable value.
                        key_size = (short_strings.get(key)
                                    if type(key) is str and len(key) <= 128 else None)
                        if key_size is None:
                            key_size = string_size(key_string(key))
                        reserve(key_size)
                        reserve(separator_bytes)  # colon, optional space
                        visit(child, depth + 1)
                else:
                    for index, child in enumerate(item):
                        if index:
                            reserve(separator_bytes)
                        visit(child, depth + 1)
            finally:
                ancestors.remove(identity)
        else:
            raise TypeError("unsupported JSON value")

    visit(value)
    return _MAX_RESPONSE_BYTES - remaining


def _json_body(value: Any, *, canonical: bool = False) -> bytes:
    """Preflight before efficient C encoding; verify wire bytes afterwards."""
    _preflight_json(value, compact=canonical)
    options = {"sort_keys": True, "separators": (",", ":")} if canonical else {}
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, **options).encode("utf-8")
    # A caller is expected to supply a stable snapshot. Even if it mutates
    # between passes, oversized bytes are never sent; this is not an RSS cap.
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise _ResponseLimitError
    return encoded


def _error_response(status: int, code: str) -> RequestResponse:
    # Codes are application-owned constants: never interpolate exception text,
    # selectors, paths, SQL, or credentials into a browser-facing error.
    return _response(status, "application/json; charset=utf-8",
                     json.dumps({"error": code}).encode("utf-8"))


def _static_bytes(filename: str) -> bytes:
    root = _STATIC_ROOT.resolve()
    asset = (root / filename).resolve(strict=True)
    if not asset.is_relative_to(root):
        raise OSError("static asset outside packaged root")
    # Limit the read as well as the declared size, covering concurrent growth.
    with asset.open("rb") as stream:
        body = stream.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise _ResponseLimitError
    return body


def _valid_target(target: str) -> bool:
    if (not target.startswith("/") or target.startswith("//")
            or "#" in target
            or len(target.encode("utf-8")) > _MAX_TARGET_BYTES
            or any(ord(char) < 32 or ord(char) == 127 for char in target)):
        return False
    try:
        parsed = urlsplit(target)
    except ValueError:
        return False
    return not (parsed.scheme or parsed.netloc or parsed.fragment)


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
    if type(projection) is not dict and not isinstance(projection, Mapping):
        raise _projection_error("根对象必须是映射")
    if projection.get("schema_version") != _SCHEMA_VERSION:
        raise _projection_error(f"schema_version 必须为 {_SCHEMA_VERSION}")
    source = projection.get("source")
    if (type(source) is not dict and not isinstance(source, Mapping)) or source.get("mode") not in {
        "initial_configured", "registry_current",
    }:
        raise _projection_error("source.mode 必须为 initial_configured 或 registry_current")
    summary = projection.get("summary")
    if type(summary) is not dict and not isinstance(summary, Mapping):
        raise _projection_error("summary 必须是映射")
    nodes = projection.get("nodes")
    edges = projection.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise _projection_error("nodes 和 edges 必须是列表")
    if len(nodes) > _MAX_GRAPH_NODES or len(edges) > _MAX_GRAPH_EDGES:
        raise _ResponseLimitError

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if type(node) is not dict and not isinstance(node, Mapping):
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
        if type(edge) is not dict and not isinstance(edge, Mapping):
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
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; worker-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'"),
    }
    if status == 405:
        headers["Allow"] = "GET, HEAD"
    return RequestResponse(status, headers, body)


def handle_request(
    provider: ProjectionProvider,
    method: str,
    target: str,
    *,
    show_resources: bool = False,
    body: bytes = b"",
) -> RequestResponse:
    """Bound and sanitize every read projection, including POST selection."""
    try:
        if not isinstance(target, str) or not _valid_target(target):
            response = _error_response(400, "invalid_target")
        else:
            response = _handle_request(provider, method, target,
                                       show_resources=show_resources, body=body)
    except _ResponseLimitError:
        response = _error_response(413, "scope_limit")
    except Exception:
        response = _error_response(503, "read_failed")
    if method.upper() == "HEAD":
        return RequestResponse(response.status, response.headers, b"")
    return response


def _handle_request(
    provider: ProjectionProvider,
    method: str,
    target: str,
    *,
    show_resources: bool = False,
    body: bytes = b"",
) -> RequestResponse:
    """Handle one request synchronously without opening a socket.

    The callable serves v1; optional dashboard/history methods are read-only.
    GET and HEAD use the same validated response without opening execution hosts.
    """
    method = method.upper()
    path = urlsplit(target).path
    if method not in {"GET", "HEAD"} and not (method == "POST" and path == "/api/v2/comparison-context"):
        return _response(
            405, "text/plain; charset=utf-8", "只支持 GET 和 HEAD".encode("utf-8"))
    if path == "/":
        body = _static_bytes("index.html").replace(
            b"__NET_VIEW_BOOTSTRAP__",
            json.dumps({"showResources": show_resources, "comparisonOnly": bool(getattr(provider, "comparison_only", False))}).encode("utf-8"),
        )
        if len(body) > _MAX_RESPONSE_BYTES:
            raise _ResponseLimitError
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
            response = _response(200, content_type, _static_bytes(filename))
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
            response = _response(200, 'application/json; charset=utf-8', _json_body(payload))
        except NotImplementedError:
            response = _response(501, 'application/json; charset=utf-8', b'{"error":"not_provided"}')
        except ObservationNotSelected:
            response = _response(403, 'application/json; charset=utf-8', b'{"error":"not_selected"}')
        except (ValueError, TypeError, KeyError):
            response = _response(400, 'application/json; charset=utf-8', b'{"error":"invalid_query"}')
        except (RuntimeError, OSError, sqlite3.Error):
            response = _response(503, 'application/json; charset=utf-8', b'{"error":"read_failed"}')
    elif path == "/api/v2/user-contributions":
        from .user_contributions import (parse_query, SCHEMA, ContributionInvalid,
            ContributionAccessChanged, ContributionStale, ContributionLimit)
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        import sqlite3
        try:
            selected = parse_query(urlsplit(target).query)
            action = getattr(provider, 'user_contributions', None)
            if not callable(action):
                raise NotImplementedError('not_configured')
            payload = action(**selected)
            if payload.get('schema_version') != SCHEMA or payload.get('selector') != selected:
                raise ValueError('contribution response selector differs')
            response = _response(200, 'application/json; charset=utf-8', _json_body(payload))
        except NotImplementedError:
            response = _response(501, 'application/json; charset=utf-8', b'{"error":"not_configured_or_unsupported"}')
        except ContributionInvalid:
            response = _response(400, 'application/json; charset=utf-8', b'{"error":"invalid_query"}')
        except ContributionAccessChanged:
            response = _response(403, 'application/json; charset=utf-8', b'{"error":"access_changed"}')
        except ContributionStale:
            response = _response(409, 'application/json; charset=utf-8', b'{"error":"stale_observation"}')
        except ContributionLimit:
            response = _response(413, 'application/json; charset=utf-8', b'{"error":"scope_limit"}')
        except RegistryReadSessionError as error:
            status = 413 if error.code == 'LIMIT_EXCEEDED' else 422 if error.code == 'PROJECTION_UNAVAILABLE' else 403
            response = _response(status, 'application/json; charset=utf-8', json.dumps({'error': 'scope_limit' if status == 413 else 'projection_unavailable' if status == 422 else 'access_changed'}).encode())
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error):
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
                response = _response(200, "application/json; charset=utf-8", _json_body(payload))
            except (ValueError, RuntimeError):
                response = _response(409, "application/json; charset=utf-8", b'{"error":"workset_unavailable"}')
    elif path == "/api/v2/execution-nets":
        from .execution_nets import parse_query, SCHEMA, ExecutionQueryError, ExecutionStale, ExecutionAccessChanged
        import sqlite3
        try:
            params = parse_query(urlsplit(target).query)
            action = getattr(provider, 'execution_nets', None)
            if not callable(action):
                raise NotImplementedError('unsupported')
            payload = action(**params)
            if (payload.get('schema_version') != SCHEMA
                    or payload.get('selector') != {k: params[k] for k in ('net_ref', 'checkpoint_ref', 'cut')}
                    or payload.get('firing_ref') != params['firing_ref']
                    or payload.get('capture') != params['expected_capture']
                    or payload.get('source') != params['expected_source']
                    or payload.get('instance_ref') != params.get('instance_ref')):
                raise ValueError('execution response identity differs')
            response = _response(200, 'application/json; charset=utf-8', _json_body(payload))
        except NotImplementedError:
            response = _response(501, 'application/json; charset=utf-8', b'{"error":"unsupported"}')
        except ExecutionQueryError:
            response = _response(400, 'application/json; charset=utf-8', b'{"error":"invalid_query"}')
        except (ExecutionAccessChanged, PermissionError):
            response = _response(403, 'application/json; charset=utf-8', b'{"error":"access_changed"}')
        except ExecutionStale:
            response = _response(409, 'application/json; charset=utf-8', b'{"error":"stale_observation"}')
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error):
            response = _response(503, 'application/json; charset=utf-8', b'{"error":"read_failed"}')
    elif path == "/api/v2/managed-tools":
        from .managed_tools import parse_query, SCHEMA, ManagedQueryError, ManagedStale
        import sqlite3
        try:
            params = parse_query(urlsplit(target).query)
            action = getattr(provider, 'managed_tools', None)
            if not callable(action):
                raise NotImplementedError('unsupported')
            payload = action(**params)
            if (payload.get('schema_version') != SCHEMA
                    or payload.get('selector') != {k: params[k] for k in ('net_ref', 'checkpoint_ref', 'cut')}
                    or payload.get('firing_ref') != params['firing_ref']):
                raise ValueError('managed response identity differs')
            response = _response(200, 'application/json; charset=utf-8', _json_body(payload))
        except NotImplementedError:
            response = _response(501, 'application/json; charset=utf-8', b'{"error":"unsupported"}')
        except ManagedQueryError:
            response = _response(400, 'application/json; charset=utf-8', b'{"error":"invalid_query"}')
        except ManagedStale:
            response = _response(409, 'application/json; charset=utf-8', b'{"error":"stale_observation"}')
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error):
            response = _response(503, 'application/json; charset=utf-8', b'{"error":"read_failed"}')
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
                response = _response(200, "application/json; charset=utf-8", _json_body(payload))
        except (ActivityQueryError, ActivityCursorError):
            response = _response(400, "application/json; charset=utf-8", b'{"error":"invalid_query"}')
        except ActivityStaleError:
            response = _response(409, "application/json; charset=utf-8", b'{"error":"stale_observation"}')
        except ActivityAccessChanged:
            response = _response(403, "application/json; charset=utf-8", b'{"error":"access_changed"}')
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error):
            response = _response(503, "application/json; charset=utf-8", b'{"error":"read_failed"}')
    elif path in {"/api/v2/comparison-context", "/api/v2/comparison-selection"}:
        from .comparison_context import (parse_query as parse_context_query, parse_body, _json,
            validate_comparison_context, ComparisonContextError)
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        from .comparison_context import _session_error
        try:
            if path.endswith("comparison-context"):
                host_limits = getattr(provider, "host_limits", None)
                if method == "POST":
                    if urlsplit(target).query: raise ComparisonContextError("invalid_query")
                    selected = parse_body(body, host_limits=host_limits)
                else:
                    selected = parse_context_query(urlsplit(target).query, host_limits=host_limits)
                action = getattr(provider, "comparison_context", None)
                if not callable(action): raise ComparisonContextError("unsupported")
                payload = action(selected)
                validate_comparison_context(payload, selected, host_limits=host_limits)
            else:
                params = parse_qs(urlsplit(target).query, keep_blank_values=True, strict_parsing=True)
                if set(params) - {"cursor"} or any(len(v) != 1 or not v[0] for v in params.values()):
                    raise ComparisonContextError("invalid_query")
                action = getattr(provider, "comparison_selection", None)
                if not callable(action): raise ComparisonContextError("unsupported")
                payload = action(cursor=params.get("cursor", [None])[0])
                if payload.get("schema_version") != "rpnh/comparison_selection/v1":
                    raise ComparisonContextError("read_failed")
            response = _response(200, "application/json; charset=utf-8", _json_body(payload, canonical=True))
        except (ComparisonContextError, RegistryReadSessionError) as error:
            code = error.code if isinstance(error, ComparisonContextError) else _session_error(error).code
            status = {"invalid_query": 400, "access_changed": 403, "stale_observation": 409,
                      "unsupported": 501, "scope_limit": 413, "projection_unavailable": 422}.get(code, 503)
            response = _response(status, "application/json; charset=utf-8", json.dumps({"error": code}).encode())
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError):
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
                response = _response(200, "application/json; charset=utf-8", _json_body(payload))
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
                response = _response(200, "application/json; charset=utf-8", _json_body(payload))
            except TokenResourceStale:
                response = _response(409, "application/json; charset=utf-8", b'{"error":"stale_observation"}')
            except TokenResourceAccessChanged:
                response = _response(403, "application/json; charset=utf-8", b'{"error":"access_changed"}')
            except (TypeError, ValueError, KeyError) as exc:
                response = _response(400, "application/json; charset=utf-8", b'{"error":"invalid_target"}')
            except (RuntimeError, OSError, sqlite3.Error) as exc:
                response = _response(503, "application/json; charset=utf-8", b'{"error":"read_failed"}')
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
                response = _response(200, "application/json; charset=utf-8", _json_body(payload))
            except (TypeError, ValueError, KeyError) as exc:
                response = _response(400, "application/json; charset=utf-8", b'{"error":"read_failed"}')
            except (RuntimeError, OSError) as exc:
                response = _response(503, "application/json; charset=utf-8", b'{"error":"read_failed"}')
    elif path == "/api/v1/net" and not callable(provider):
        response = _response(501, "application/json; charset=utf-8", b'{"error":"unsupported"}')
    elif path == "/api/v1/net":
        try:
            projection = _validate_projection(provider())
            body = _json_body(projection)
            response = _response(200, "application/json; charset=utf-8", body)
        except (TypeError, ValueError, RuntimeError) as exc:
            response = _response(500, "application/json; charset=utf-8", b'{"error":"projection_unavailable"}')
    else:
        response = _response(404, "text/plain; charset=utf-8", "未找到资源".encode("utf-8"))
    if method == "HEAD":
        return RequestResponse(response.status, response.headers, b"")
    return response


class _HeaderReader:
    """Limit headers before stdlib's parser builds its message object."""

    def __init__(self, reader: Any) -> None:
        self.reader = reader
        self.size = 0
        self.lines = 0

    def readline(self, size: int = -1) -> bytes:
        remaining = _MAX_HEADER_BYTES - self.size
        line = self.reader.readline(min(size, remaining + 1) if size >= 0 else remaining + 1)
        self.size += len(line)
        self.lines += 1
        if self.size > _MAX_HEADER_BYTES or self.lines > _MAX_HEADER_LINES:
            raise http.client.HTTPException("viewer header budget exceeded")
        return line


class _ProjectionHandler(BaseHTTPRequestHandler):
    server: "_ProjectionHTTPServer"
    server_version = "RPNHViewer"
    sys_version = ""

    def parse_request(self) -> bool:
        reader = self.rfile
        self.rfile = _HeaderReader(reader)
        try:
            return super().parse_request()
        finally:
            self.rfile = reader

    def send_error(self, code: int, message: str | None = None,
                   explain: str | None = None) -> None:
        self.close_connection = True
        self._send_response(_error_response(code, "invalid_http_request"))

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionError, OSError):
            # Client disconnect / absolute socket deadline: no stack or input
            # is logged, and the server's worker finally releases its slot.
            self.close_connection = True

    def _send_response(self, response: RequestResponse) -> None:
        self.close_connection = True
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
        if not _valid_target(raw_target):
            return (
                _response(
                    400, "text/plain; charset=utf-8",
                    "Viewer 只接受 origin-form request target".encode("utf-8")),
                None,
            )

        lengths = self.headers.get_all("Content-Length", [])
        comparison_post = (self.command == "POST"
                           and urlsplit(raw_target).path == "/api/v2/comparison-context")
        if (self.headers.get_all("Transfer-Encoding", []) or len(lengths) > 1
                or (not comparison_post and lengths and lengths[0] != "0")):
            return _error_response(400, "request_body_not_allowed"), None

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

    def _read_comparison_body(self) -> bytes | RequestResponse:
        # Reject framing/size before allocating or reading the declared body.
        from .comparison_context import _host_limits
        lengths = self.headers.get_all("Content-Length", [])
        content_types = self.headers.get_all("Content-Type", [])
        if (self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1
                or not lengths[0].isascii() or not lengths[0].isdecimal()
                or len(content_types) != 1 or self.headers.get_content_type() != "application/json"):
            return _response(400, "application/json; charset=utf-8", b'{"error":"invalid_query"}')
        try:
            maximum = _host_limits(getattr(self.server.provider, "host_limits", None))["max_request_bytes"]
            # Comparing decimal lengths also avoids allocating an unbounded integer.
            length_text = lengths[0].lstrip("0") or "0"
            bound = str(maximum)
            if len(length_text) > len(bound) or (len(length_text) == len(bound) and length_text > bound):
                return _response(413, "application/json; charset=utf-8", b'{"error":"scope_limit"}')
            length = int(length_text)
            body = self.rfile.read(length)
            if len(body) != length: raise ValueError("incomplete body")
            return body
        except (ValueError, OSError):
            return _response(400, "application/json; charset=utf-8", b'{"error":"invalid_query"}')

    def _send(self) -> None:
        rejection, target = self._request_boundary()
        if rejection is not None:
            self._send_response(rejection)
            return
        assert target is not None
        body = b""
        if self.command == "POST" and urlsplit(target).path == "/api/v2/comparison-context":
            body = self._read_comparison_body()
            if isinstance(body, RequestResponse):
                self.close_connection = True
                self._send_response(body)
                return
        response = handle_request(
            self.server.provider,
            self.command,
            target,
            show_resources=self.server.show_resources,
            body=body,
        )
        self._send_response(response)

    do_GET = _send
    do_HEAD = _send

    def do_POST(self) -> None: self._send()
    def do_PUT(self) -> None: self._send()
    def do_PATCH(self) -> None: self._send()
    def do_DELETE(self) -> None: self._send()
    def do_OPTIONS(self) -> None: self._send()
    def do_TRACE(self) -> None: self._send()
    def do_CONNECT(self) -> None: self._send()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _ProjectionHTTPServer(ThreadingHTTPServer):
    """Bounded-request loopback server for the read-only Viewer."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        provider: ProjectionProvider,
        host: str,
        port: int,
        show_resources: bool,
        *,
        max_connections: int = _MAX_CONNECTIONS,
        request_timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
        session_timeout_seconds: float = _SESSION_TIMEOUT_SECONDS,
    ) -> None:
        _validate_loopback(host)
        if type(max_connections) is not int or not 1 <= max_connections <= 256:
            raise ValueError("max_connections must be within 1..256")
        if (isinstance(request_timeout_seconds, bool)
                or not math.isfinite(request_timeout_seconds)
                or request_timeout_seconds <= 0):
            raise ValueError("request_timeout_seconds must be positive and finite")
        if (isinstance(session_timeout_seconds, bool)
                or not math.isfinite(session_timeout_seconds)
                or session_timeout_seconds < 0):
            raise ValueError("session_timeout_seconds must be finite and nonnegative")
        address = ip_address(host)
        self.address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self.provider = provider
        self.show_resources = show_resources
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.session_timeout_seconds = float(session_timeout_seconds)
        self._slots = threading.BoundedSemaphore(max_connections)
        self._requests_lock = threading.Lock()
        self._requests: dict[socket.socket, float] = {}
        self._started_at = time.monotonic()
        self.expired = False
        self.max_connections = max_connections
        # Method is not parsed when the worker gate rejects a connection.
        # An empty response therefore preserves HEAD semantics as well as GET.
        busy = _response(503, "application/json; charset=utf-8")
        self._busy_response = (
            "HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\n"
            + "".join(f"{key}: {value}\r\n" for key, value in busy.headers.items())
            + "\r\n").encode("ascii") + busy.body
        super().__init__((address.compressed, port), _ProjectionHandler)
        self.bound_address = ip_address(self.server_address[0])
        self.bound_port = int(self.server_address[1])
        self.authority = _format_authority(self.bound_address, self.bound_port)
        self.origin = f"http://{self.authority}"

    @property
    def active_connections(self) -> int:
        with self._requests_lock:
            return len(self._requests)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        # Bound workers before ThreadingMixIn creates a thread, not inside it.
        if not self._slots.acquire(blocking=False):
            try:
                request.setblocking(False)
                request.send(self._busy_response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        with self._requests_lock:
            self._requests[request] = time.monotonic()
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._requests_lock:
                self._requests.pop(request, None)
            self._slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request: socket.socket,
                               client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._requests_lock:
                self._requests.pop(request, None)
            self._slots.release()

    def handle_error(self, request: socket.socket, client_address: Any) -> None:
        # Unexpected transport failures must not print provider internals.
        return

    @staticmethod
    def _close_socket(request: socket.socket) -> None:
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        request.close()

    def service_actions(self) -> None:
        now = time.monotonic()
        with self._requests_lock:
            overdue = [request for request, started in self._requests.items()
                       if now - started >= self.request_timeout_seconds]
        for request in overdue:
            self._close_socket(request)
        if (self.session_timeout_seconds
                and now - self._started_at >= self.session_timeout_seconds):
            self.expired = True
            raise _ViewerSessionExpired

    def serve_forever(self, poll_interval: float = 0.1) -> None:
        try:
            super().serve_forever(poll_interval=min(poll_interval, 0.1))
        except _ViewerSessionExpired:
            # BaseServer's finally has signalled shutdown completion already.
            # Calling shutdown() inside service_actions would deadlock.
            self.server_close()

    def server_close(self) -> None:
        with self._requests_lock:
            requests = list(self._requests)
        for request in requests:
            self._close_socket(request)
        super().server_close()

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
    *,
    session_timeout_seconds: float = _SESSION_TIMEOUT_SECONDS,
    max_connections: int = _MAX_CONNECTIONS,
) -> int:
    """Serve on loopback for a finite session (0 explicitly stays open)."""
    _validate_loopback(host)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port 必须在 0 到 65535 之间")

    with _ProjectionHTTPServer(provider, host, port, show_resources,
                               session_timeout_seconds=session_timeout_seconds,
                               max_connections=max_connections) as server:
        url = f"{server.origin}/"
        print(f"PetriNet 只读查看器：{url}")
        if session_timeout_seconds:
            print(f"仅本机 / loopback only · {session_timeout_seconds:g}s 后关闭端口 / closes after session limit · Ctrl-C 停止")
        else:
            print("持续监听 / persistent listener explicitly enabled · 请用 Ctrl-C 关闭端口 / stop to close port")
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        if server.expired:
            print("Viewer 会话已到期，端口已关闭 / Session expired; listener closed.")
    return server.bound_port
