"""The actual local HTTP transport boundary for an explicit human read.

Only an original native Viewer handler can start this observation. A body
write, explicit flush, closed reader/writer and closed native socket are all
required. No receipt is minted from JSON, a memory writer, an expiry or a
request to close. The evidence describes this adapter, not remote retention.
"""
from __future__ import annotations

from io import BufferedReader, BufferedWriter
from ipaddress import ip_address
import json
import socket
from socketserver import _SocketWriter
from threading import Lock

from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json


_ISSUER = object()
_PROTOCOL = "rpnh/human_viewer_transport/v1"
_ADAPTER = "rpnh.viewer.http/v1"
_BINDING_KEYS = frozenset({"protocol", "command_ref", "firing_ref", "invocation_ref",
    "actor", "session_ref", "resource_ref", "delivery_ref", "witness_ref",
    "broker_channel_id", "byte_count"})


def _binding_bytes(binding):
    if (type(binding) is not dict or set(binding) != _BINDING_KEYS
            or binding.get("protocol") != _PROTOCOL
            or type(binding.get("byte_count")) is not int or binding["byte_count"] < 0):
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: exact transport binding required")
    return canonical_json(binding)


class HumanViewerHTTPBody:
    """Immutable original channel bytes, with no caller-supplied payload."""
    __slots__ = ("__issuer", "__binding_bytes", "__media_type", "__payload", "__on_closed",
        "__byte_count", "__claim_lock", "__claimed")

    def __init__(self, delivery, on_closed, *, _issuer=None):
        if _issuer is not _ISSUER or not callable(on_closed):
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original HTTP body factory required")
        from cpn.rpnh.human_viewer import (
            transport_binding, delivery_media_type, consume_human_viewer_delivery,
        )
        binding = transport_binding(delivery)
        media_type = delivery_media_type(delivery)
        payload = consume_human_viewer_delivery(delivery)
        if type(payload) is not bytes or len(payload) != binding["byte_count"]:
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original channel body is incomplete")
        for key, value in (("__issuer", _ISSUER), ("__binding_bytes", _binding_bytes(binding)),
                ("__media_type", media_type), ("__payload", payload), ("__on_closed", on_closed),
                ("__byte_count", len(payload)), ("__claim_lock", Lock()), ("__claimed", False)):
            object.__setattr__(self, "_HumanViewerHTTPBody" + key, value)

    def __setattr__(self, name, value):
        raise AttributeError("original HTTP body is immutable")

    @property
    def binding_bytes(self):
        return self.__binding_bytes

    @property
    def media_type(self):
        return self.__media_type

    @property
    def byte_count(self):
        return self.__byte_count

    @property
    def on_closed(self):
        return self.__on_closed

    def __claim_payload(self):
        with self.__claim_lock:
            if self.__claimed:
                raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original HTTP body is already bound")
            object.__setattr__(self, "_HumanViewerHTTPBody__claimed", True)
            payload = self.__payload
            object.__setattr__(self, "_HumanViewerHTTPBody__payload", None)
            return payload


def human_viewer_http_body(delivery, on_closed):
    """Consume once on the original Owner thread and seal those exact bytes."""
    return HumanViewerHTTPBody(delivery, on_closed, _issuer=_ISSUER)


def _socket_stream(stream, connection, *, writing):
    if writing and type(stream) is _SocketWriter:
        return stream._sock is connection
    expected = BufferedWriter if writing else BufferedReader
    return (type(stream) is expected and type(stream.raw) is socket.SocketIO
            and stream.raw._sock is connection)


def require_native_http_handler(handler):
    """Reject synthetic adapters before they can request an original read."""
    from .server import _ProjectionHandler, _ProjectionHTTPServer
    if type(handler) is not _ProjectionHandler or type(handler.server) is not _ProjectionHTTPServer:
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: native Viewer HTTP handler required")
    connection = handler.request
    if (type(connection) is not socket.socket or handler.connection is not connection
            or connection.family not in (socket.AF_INET, socket.AF_INET6)
            or connection.fileno() < 0 or handler.command != "POST"
            or not _socket_stream(handler.rfile, connection, writing=False)
            or not _socket_stream(handler.wfile, connection, writing=True)
            or handler.rfile.closed or handler.wfile.closed):
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: exact live native HTTP streams required")
    with handler.server._requests_lock:
        if connection not in handler.server._requests or handler.server.expired:
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original active server request required")
    try:
        local, peer = connection.getsockname(), connection.getpeername()
        if (not ip_address(peer[0]).is_loopback
                or ip_address(local[0]) != handler.server.bound_address
                or local[1] != handler.server.bound_port):
            raise ValueError("connection differs from the original loopback server")
    except (OSError, ValueError) as exc:
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: native connection is not established") from exc


class _HTTPTransport:
    __slots__ = ("issuer", "binding_bytes", "connection", "reader", "writer",
        "on_closed", "_payload", "write_started", "written", "streams_finished", "failure", "closed", "lock")

    def __init__(self, handler, body):
        require_native_http_handler(handler)
        if (type(body) is not HumanViewerHTTPBody
                or body._HumanViewerHTTPBody__issuer is not _ISSUER):
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original HOST body required")
        binding = json.loads(body.binding_bytes)
        payload = body._HumanViewerHTTPBody__claim_payload()
        if (_binding_bytes(binding) != body.binding_bytes or type(payload) is not bytes
                or len(payload) != binding["byte_count"]):
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original body size changed")
        self.issuer = _ISSUER
        self.binding_bytes = body.binding_bytes
        self.connection, self.reader, self.writer = handler.request, handler.rfile, handler.wfile
        self.on_closed = body.on_closed
        self._payload = payload
        self.write_started = self.written = False
        self.streams_finished = False
        self.failure = None
        self.closed = False
        self.lock = Lock()


class _HTTPProof:
    __slots__ = ("issuer", "binding_bytes", "claimed", "lock")

    def __init__(self, transport):
        if (type(transport) is not _HTTPTransport or transport.issuer is not _ISSUER
                or not transport.closed or not transport.written or not transport.streams_finished
                or not transport.reader.closed or not transport.writer.closed
                or type(transport.connection) is not socket.socket
                or transport.connection.fileno() != -1 or transport.failure is not None):
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original HTTP transport is not complete")
        self.issuer, self.binding_bytes = _ISSUER, transport.binding_bytes
        self.claimed, self.lock = False, Lock()


def _transport(value):
    if type(value) is not _HTTPTransport or value.issuer is not _ISSUER:
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: original HTTP observation required")
    return value


def begin_human_viewer_http(handler, body):
    return _HTTPTransport(handler, body)


def fail_human_viewer_http(transport, reason):
    value = _transport(transport)
    if reason not in {"HTTP_WRITE_FAILED", "HTTP_STREAM_CLOSE_FAILED", "HTTP_SOCKET_CLOSE_FAILED",
            "HTTP_DEADLINE", "HTTP_SERVER_CLOSED", "HTTP_HANDLER_FAILED"}:
        raise ValueError("unsupported HTTP transport failure code")
    with value.lock:
        if not value.closed and value.failure is None:
            value.failure = reason


def write_human_viewer_http(transport):
    value = _transport(transport)
    with value.lock:
        if value.closed or value.write_started or value.failure is not None:
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: HTTP body may be written once")
        payload = value._payload
        binding = json.loads(value.binding_bytes)
        if type(payload) is not bytes or len(payload) != binding["byte_count"]:
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: exact HTTP body required")
        value.write_started = True
    try:
        count = value.writer.write(payload)
        if count != len(payload):
            raise OSError("native HTTP body write was incomplete")
        value.writer.flush()
    except BaseException:
        fail_human_viewer_http(value, "HTTP_WRITE_FAILED")
        raise
    with value.lock:
        value.written = True
        value._payload = None


def finish_human_viewer_streams(transport):
    """Close actual file objects; the standard helper hides flush failures."""
    value = _transport(transport)
    try:
        if not value.writer.closed:
            value.writer.flush()
    except (OSError, ValueError):
        fail_human_viewer_http(value, "HTTP_STREAM_CLOSE_FAILED")
    finally:
        for stream in (value.writer, value.reader):
            try:
                stream.close()
            except (OSError, ValueError):
                fail_human_viewer_http(value, "HTTP_STREAM_CLOSE_FAILED")
    with value.lock:
        value.streams_finished = value.writer.closed and value.reader.closed
        if not value.streams_finished and value.failure is None:
            value.failure = "HTTP_STREAM_CLOSE_FAILED"


def finish_human_viewer_socket(transport):
    """Called after the actual server shutdown/close, never on close intent."""
    value = _transport(transport)
    with value.lock:
        if value.closed:
            return
        success = (value.written and value.streams_finished and value.reader.closed
            and value.writer.closed and value.connection.fileno() == -1 and value.failure is None)
        value.closed = True
        proof = _HTTPProof(value) if success else None
        reason = None if success else value.failure or "HTTP_SOCKET_CLOSE_FAILED"
    # The callback crosses back to the existing Owner thread. It may record
    # unknown or leave original recovery responsibility; it cannot resend.
    value.on_closed(proof, reason)


def verify_human_viewer_transport_evidence(proof, binding):
    """One original closed HTTP observation, exact to the live read lease."""
    if type(proof) is not _HTTPProof or proof.issuer is not _ISSUER:
        raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: no native HTTP completion proof")
    exact = _binding_bytes(binding)
    with proof.lock:
        if proof.claimed or proof.binding_bytes != exact:
            raise RegistryConflict("HUMAN_VIEWER_TRANSPORT_REQUIRED: proof is used or belongs to another delivery")
        proof.claimed = True
    return {**json.loads(exact), "adapter": _ADAPTER, "completion": "completed_and_closed"}


__all__ = ("human_viewer_http_body", "require_native_http_handler", "begin_human_viewer_http",
    "write_human_viewer_http", "finish_human_viewer_streams", "finish_human_viewer_socket",
    "fail_human_viewer_http", "verify_human_viewer_transport_evidence")
