"""OpenAI-compatible HTTPS/loopback-HTTP input port with private lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from cpn.rpnh.llm_contracts import (
    LLMCallAttempt,
    LLMInputPortFailure,
    LLMInputPortInterrupted,
    LLMInputResponseBytes,
)

from ._audit import PrivateAttemptAudit
from ._common import AdapterConfigError, ResponseEnvelopeError
from ._external_provider_credentials import (
    CredentialResolutionError,
    ProviderCredentialBinding,
    credential_binding_from_document,
    resolved_provider_headers,
)
from ._external_provider_protocol import (
    normalize_openai_compatible_response,
    provider_request_from_envelope,
)
from ._external_provider_recovery import (
    ExternalProviderRecoveryConfigError,
    ExternalProviderRecoveryPolicy,
    recovery_policy_from_document,
)


SCHEMA_VERSION = "external_provider_adapter_config/v3"
LEGACY_SCHEMA_VERSION = "external_provider_adapter_config/v2"
_READ_CHUNK_BYTES = 64 * 1024
_ROUTE_FIELDS = {
    "route_id", "provider", "backend", "protocol", "endpoint",
    "outbound_model", "credential", "headers",
}
_LEGACY_CONFIG_FIELDS = {
    "schema_version", "adapter_kind", "model_condition", "recovery", "routes",
}
_CONFIG_FIELDS = _LEGACY_CONFIG_FIELDS | {"reasoning_effort"}
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*")
_FORBIDDEN_EXTRA_HEADERS = {
    "authorization", "connection", "content-length", "content-type", "host",
    "proxy-authorization", "transfer-encoding",
}


def _http_failure_metadata(status: int) -> tuple[str, bool, str]:
    """Classify only the owner-approved HTTP retry statuses as transient."""
    if status in {408, 429} or 500 <= status <= 599:
        return "provider_response_transient", True, "provider_retry"
    return "provider_response_failure", False, "block_no_retry"


@dataclass(frozen=True, slots=True)
class _ExternalRoute:
    route_id: str
    provider: str
    backend: str
    protocol: str
    scheme: str
    hostname: str
    port: int | None
    request_target: str
    outbound_model: str
    credential: ProviderCredentialBinding | None
    headers: tuple[tuple[str, str], ...]

    def audit_identity(self) -> dict[str, object]:
        return {
            "route_id": self.route_id,
            "provider": self.provider,
            "backend": self.backend,
            "protocol": self.protocol,
            "transport": self.scheme,
            "outbound_model": self.outbound_model,
        }


@dataclass(frozen=True, slots=True)
class _ExternalProviderConfig:
    routes: tuple[_ExternalRoute, ...]
    recovery: ExternalProviderRecoveryPolicy
    reasoning_effort: str | None


def _text(value: object, *, label: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)):
        raise AdapterConfigError(f"{label} must be nonempty trimmed text")
    return value


def _headers(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise AdapterConfigError("route headers must be an object")
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_name, raw_value in value.items():
        name = _text(raw_name, label="route header name")
        header_value = _text(raw_value, label=f"route header {name}")
        lower = name.lower()
        if (lower in seen or lower in _FORBIDDEN_EXTRA_HEADERS
                or any(character in name for character in "\r\n:")
                or any(character in header_value for character in "\r\n")):
            raise AdapterConfigError("route headers contain a forbidden field")
        seen.add(lower)
        result.append((name, header_value))
    return tuple(result)


def _route(value: object) -> _ExternalRoute:
    if not isinstance(value, Mapping) or set(value) != _ROUTE_FIELDS:
        raise AdapterConfigError(
            "external route must contain exactly the current fields")
    endpoint = _text(value.get("endpoint"), label="route endpoint")
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as exc:
        raise AdapterConfigError("route endpoint port is invalid") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise AdapterConfigError(
            "route endpoint must be HTTPS, or loopback HTTP, without "
            "credentials/query/fragment")
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if not loopback:
            raise AdapterConfigError(
                "plaintext HTTP routes are allowed only on loopback")
    provider = _text(value.get("provider"), label="route provider")
    backend = _text(value.get("backend"), label="route backend")
    protocol = _text(value.get("protocol"), label="route protocol")
    if protocol != "openai_chat_completions/v1":
        raise AdapterConfigError(
            "external route protocol is not supported by this adapter")
    try:
        credential = credential_binding_from_document(value.get("credential"))
    except (TypeError, ValueError) as exc:
        raise AdapterConfigError("route credential binding is invalid") from exc
    headers = _headers(value.get("headers"))
    if (credential is not None
            and credential.header.lower() in {
                name.lower() for name, _value in headers}):
        raise AdapterConfigError(
            "route credential header duplicates one static header")
    return _ExternalRoute(
        route_id=_text(value.get("route_id"), label="route_id"),
        provider=provider,
        backend=backend,
        protocol=protocol,
        scheme=parsed.scheme,
        hostname=parsed.hostname,
        port=port,
        request_target=parsed.path or "/",
        outbound_model=_text(
            value.get("outbound_model"), label="route outbound_model"),
        credential=credential,
        headers=headers,
    )


def _load_config(
        path: Path, model_condition: str,
        reasoning_effort: str | None = None,
) -> _ExternalProviderConfig:
    if (not isinstance(model_condition, str) or not model_condition
            or model_condition != model_condition.strip()):
        raise AdapterConfigError(
            "external adapter model condition is invalid")
    try:
        document: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterConfigError(
            "external adapter config is unavailable or invalid") from exc
    if not isinstance(document, Mapping):
        raise AdapterConfigError("external adapter config fields are not current")
    version = document.get("schema_version")
    expected_fields = (
        _CONFIG_FIELDS if version == SCHEMA_VERSION
        else _LEGACY_CONFIG_FIELDS)
    if set(document) != expected_fields:
        raise AdapterConfigError("external adapter config fields are not current")
    if (version not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}
            or document.get("adapter_kind") != "external_provider"
            or document.get("model_condition") != model_condition):
        raise AdapterConfigError("external adapter identity differs from selection")
    configured_effort = document.get("reasoning_effort")
    if version == SCHEMA_VERSION:
        if (configured_effort is not None
                and (not isinstance(configured_effort, str)
                     or _IDENTIFIER.fullmatch(configured_effort) is None)):
            raise AdapterConfigError(
                "external adapter reasoning_effort is invalid")
        if configured_effort != reasoning_effort:
            raise AdapterConfigError(
                "external adapter reasoning effort differs from selection")
    elif reasoning_effort is not None:
        raise AdapterConfigError(
            "legacy external adapter cannot select a reasoning effort")
    values = document.get("routes")
    if not isinstance(values, list) or len(values) != 1:
        raise AdapterConfigError(
            "external adapter requires exactly one user-selected route")
    routes = tuple(_route(value) for value in values)
    if len({route.route_id for route in routes}) != len(routes):
        raise AdapterConfigError("external route_id values must be unique")
    if any(route.outbound_model != model_condition for route in routes):
        raise AdapterConfigError(
            "external route exact model differs from the selected condition")
    try:
        recovery = recovery_policy_from_document(document.get("recovery"))
    except ExternalProviderRecoveryConfigError as exc:
        raise AdapterConfigError(str(exc)) from exc
    return _ExternalProviderConfig(
        routes=routes, recovery=recovery,
        reasoning_effort=(
            configured_effort if isinstance(configured_effort, str) else None),
    )


def _probe_request(
        route: _ExternalRoute, reasoning_effort: str | None = None,
) -> bytes:
    document: dict[str, object] = {
        "model": route.outbound_model,
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply with READY."}],
        "stream": False,
    }
    if reasoning_effort is not None:
        document["reasoning_effort"] = reasoning_effort
    return json.dumps(document, ensure_ascii=False, allow_nan=False,
        separators=(",", ":")).encode("utf-8")


def _credential_headers(route: _ExternalRoute):
    return resolved_provider_headers(route.credential)


def _read_bounded(
        response: http.client.HTTPResponse,
        connection: http.client.HTTPConnection, *, max_bytes: int,
        deadline: float, transport_socket: socket.socket | None = None,
) -> bytes:
    payload = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        active_socket = transport_socket if transport_socket is not None else connection.sock
        if active_socket is not None:
            active_socket.settimeout(remaining)
        room = max_bytes + 1 - len(payload)
        if room <= 0:
            raise OverflowError
        # read(n) may wait for n bytes while a peer keeps resetting the socket's
        # inactivity timeout. read1 returns available body data so the absolute
        # request deadline is rechecked between reads, including detached sockets.
        read = getattr(response, "read1", response.read)
        chunk = read(min(_READ_CHUNK_BYTES, room))
        if time.monotonic() >= deadline:
            raise TimeoutError
        if not chunk:
            remaining_body = getattr(response, "length", None)
            if isinstance(remaining_body, int) and remaining_body > 0:
                # HTTPResponse.read(n)/read1 deliberately allow early EOF. A
                # syntactically complete JSON prefix is not a complete response.
                raise http.client.IncompleteRead(bytes(payload), remaining_body)
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise OverflowError
        if getattr(response, "length", None) == 0:
            return bytes(payload)


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _exception_category(exc: BaseException) -> str:
    if isinstance(exc, CredentialResolutionError):
        return exc.code
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, socket.gaierror):
        return "name_resolution_failure"
    if isinstance(exc, ssl.SSLError):
        return "tls_failure"
    if isinstance(exc, http.client.HTTPException):
        return "http_protocol_failure"
    if isinstance(exc, ConnectionError):
        return "connection_failure"
    if isinstance(exc, OSError):
        return "operating_system_transport_failure"
    return "adapter_failure"


@dataclass(frozen=True, slots=True)
class _ProviderCallResult:
    response_bytes: bytes | None
    outcome: str
    provider_attempt_id: str
    provider_request_id: str
    detail: Mapping[str, object]


def _neutral_failure(
        result: _ProviderCallResult, *, failure_code: str | None = None,
) -> LLMInputPortFailure:
    """Translate one provider result to the sole framework failure contract."""
    detail = result.detail
    raw_state = detail.get("submission_state")
    if raw_state == "response_complete":
        submission_state = "response_observed"
    elif raw_state == "not_submitted":
        submission_state = "not_submitted"
    elif isinstance(raw_state, str) and raw_state:
        submission_state = "submission_unknown"
    else:
        submission_state = "submission_unknown"
    if bool(detail.get("retry_eligible")):
        disposition = "provider_retryable"
    elif result.outcome == "submission_unknown":
        disposition = "submission_unknown"
        submission_state = "submission_unknown"
    elif result.outcome == "protocol_failure":
        disposition = "protocol_rejected"
    else:
        disposition = "provider_failure"
    raw_code = detail.get("failure_code")
    selected_failure_code = failure_code or (
        raw_code if isinstance(raw_code, str) and raw_code
        else result.outcome)
    return LLMInputPortFailure(
        disposition, submission_state=submission_state,
        failure_code=selected_failure_code)


def _health_probe_eligible(result: _ProviderCallResult) -> bool:
    """Return whether one failed formal call warrants same-route diagnosis."""
    return (
        result.response_bytes is None
        and (result.outcome == "submission_unknown"
             or bool(result.detail.get("retry_eligible")))
    )


class ExternalProviderInputPort:
    """Provider-local exact-model retries behind one neutral invocation."""

    def __init__(
            self, *, model_condition: str, max_output_tokens: int,
            timeout_seconds: int, max_response_bytes: int,
            config_path: Path,
            destination_run_root: Path,
            reasoning_effort: str | None = None,
    ) -> None:
        values = (
            max_output_tokens, timeout_seconds, max_response_bytes,
        )
        if (not isinstance(model_condition, str) or not model_condition
                or any(isinstance(value, bool) or not isinstance(value, int)
                       or value < 1 for value in values)):
            raise TypeError("external input port configuration is invalid")
        self._model_condition = model_condition
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        config = _load_config(
            config_path, model_condition, reasoning_effort)
        self._routes = config.routes
        self._recovery = config.recovery
        self._reasoning_effort = config.reasoning_effort
        self._audit = PrivateAttemptAudit(destination_run_root)
        active_route_id = self._audit.latest_selected_external_route_id(
            model_condition=model_condition)
        if active_route_id is None:
            self._active_route_index = 0
        else:
            active_route_indexes = [
                index for index, route in enumerate(self._routes)
                if route.route_id == active_route_id]
            if len(active_route_indexes) != 1:
                raise AdapterConfigError(
                    "last selected external route is absent from current config")
            self._active_route_index = active_route_indexes[0]
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def from_public(cls, selection, *, destination_run_root: Path,
                    resolver=None, renderer=None):
        from .config import RegisteredLLMExecutionSelection
        from .public_credentials import check_capability
        from cpn.rpnh.public_material_contracts import canonical
        if cls is not ExternalProviderInputPort or type(selection) is not RegisteredLLMExecutionSelection:
            raise TypeError('public factory requires the original adapter and public selection')
        identity = selection.public_identity
        policy = identity['policy']
        route = policy['route_provenance'][0]
        if route['outbound_model'] != selection.input_target.model_condition:
            raise AdapterConfigError('public route exact model differs from target')
        check_capability(resolver, identity['resolver'])
        check_capability(renderer, identity['renderer'])
        # Reuse original pure route parser; never invoke private loader/constructor.
        parsed = _route({key: route[key] for key in (
            'route_id', 'provider', 'backend', 'protocol', 'endpoint', 'outbound_model')}
            | {'credential': None, 'headers': {}})
        self = cls.__new__(cls)
        self._model_condition = policy['model_condition']
        self._max_output_tokens = policy['max_output_tokens']
        self._timeout_seconds = policy['timeout_seconds']
        self._max_response_bytes = policy['max_response_bytes']
        self._routes = (parsed,)
        self._recovery = recovery_policy_from_document(policy['adapter_profile']['recovery'])
        self._reasoning_effort = policy['reasoning_effort']
        self._audit = PrivateAttemptAudit(destination_run_root)
        # Fresh public mode never reads historical private audit route selection.
        self._active_route_index = 0
        self._lock = threading.Lock()
        self._closed = False
        self._public_identity_bytes = canonical(identity)
        self._public_resolver = resolver
        self._public_renderer = renderer
        self._public_association = (resolver, renderer,
            None if resolver is None else resolver.callback,
            None if renderer is None else renderer.callback)
        return self

    def __setattr__(self, name, value):
        frozen = {'_public_identity_bytes', '_public_resolver', '_public_renderer',
                  '_public_association', '_model_condition', '_max_output_tokens',
                  '_timeout_seconds', '_max_response_bytes', '_routes', '_recovery',
                  '_reasoning_effort'}
        if hasattr(self, '_public_association') and name in frozen:
            raise AttributeError('registered provider construction identity is immutable')
        object.__setattr__(self, name, value)

    @property
    def public_identity(self):
        from cpn.rpnh.public_material_contracts import decode
        raw = getattr(self, '_public_identity_bytes', None)
        if raw is None: raise ValueError('legacy provider has no registered public identity')
        self.assert_public_association()
        return decode(raw, canonical_required=True)

    def assert_public_association(self):
        from .public_credentials import check_capability
        from cpn.rpnh.public_material_contracts import decode
        identity = decode(self._public_identity_bytes, canonical_required=True)
        resolver, renderer, resolve, render = self._public_association
        if (self._public_resolver is not resolver or self._public_renderer is not renderer
                or resolver is not None and resolver.callback is not resolve
                or renderer is not None and renderer.callback is not render):
            raise ValueError('registered provider capability association changed')
        check_capability(resolver, identity['resolver'])
        check_capability(renderer, identity['renderer'])

    def _resolved_headers(self, route):
        if hasattr(self, '_public_identity_bytes'):
            from .public_credentials import resolved_public_headers
            if route is not self._routes[0]: raise ValueError('public route changed')
            return resolved_public_headers(self.public_identity,
                self._public_resolver, self._public_renderer)
        return _credential_headers(route)

    @staticmethod
    def _private_identity(
            attempt: LLMCallAttempt, *, call_ordinal: int, label: str,
    ) -> str:
        return (
            f"{attempt.invocation_ref.version_id}:external-{label}:"
            f"{call_ordinal}")

    def _perform_request(
            self, attempt: LLMCallAttempt, *, route: _ExternalRoute,
            method: str, body: bytes | None, call_kind: str,
            call_ordinal: int, prior_provider_attempt_id: str | None,
            deadline: float, max_response_bytes: int,
            interruption_requested: Callable[[], bool] | None = None,
    ) -> _ProviderCallResult:
        route_audit = route.audit_identity()
        provider_attempt_id = self._private_identity(
            attempt, call_ordinal=call_ordinal, label="provider-attempt")
        provider_request_id = self._private_identity(
            attempt, call_ordinal=call_ordinal, label="provider-request")
        self._audit.provider_attempt_started(
            attempt, route=route_audit,
            provider_attempt_id=provider_attempt_id,
            provider_request_id=provider_request_id,
            call_kind=call_kind, call_ordinal=call_ordinal,
            prior_provider_attempt_id=prior_provider_attempt_id)

        connection: http.client.HTTPConnection | None = None
        # Keep the established socket even when getresponse() detaches it for
        # a Connection: close response. HTTPResponse owns a makefile reference;
        # connection.close() alone does not wake its blocking read.
        transport_socket: socket.socket | None = None
        request_write_started = False
        request_write_completed = False
        response_headers_received = False
        response_body_completed = False
        request_id: str | None = None
        transport_phase = "credential_resolution"
        detail: dict[str, object] = {"http_method": method}
        watch_stop = threading.Event()
        watch_interrupted = threading.Event()

        def monitor_interruption() -> None:
            if interruption_requested is None:
                return
            while not watch_stop.wait(0.05):
                if not interruption_requested():
                    continue
                watch_interrupted.set()
                active = connection
                current_socket = transport_socket
                if current_socket is None and active is not None:
                    current_socket = active.sock
                if current_socket is not None:
                    try:
                        current_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass  # Already disconnected; the request thread owns cleanup.
                # Do not close HTTPResponse's buffered reader on this thread:
                # close() can wait for the blocked read's internal lock.
                return

        watcher = (
            threading.Thread(
                target=monitor_interruption,
                name="rpnh-provider-owner-stop",
                daemon=True)
            if interruption_requested is not None else None)
        if watcher is not None:
            watcher.start()

        def progress(
                phase: str, phase_detail: Mapping[str, object] | None = None,
        ) -> None:
            self._audit.provider_progress(
                attempt, route=route_audit,
                provider_attempt_id=provider_attempt_id,
                provider_request_id=provider_request_id,
                call_kind=call_kind, phase=phase, detail=phase_detail)

        try:
            if (interruption_requested is not None
                    and interruption_requested()):
                watch_interrupted.set()
                raise LLMInputPortInterrupted(
                    submission_state="not_submitted")
            with self._resolved_headers(route) as headers:
                headers.update(route.headers)
                progress("credential_resolution_complete")
                transport_phase = "connection"
                if route.scheme == "https":
                    connection = http.client.HTTPSConnection(
                        route.hostname, route.port,
                        timeout=_remaining_seconds(deadline),
                        context=ssl.create_default_context())
                else:
                    connection = http.client.HTTPConnection(
                        route.hostname, route.port,
                        timeout=_remaining_seconds(deadline))
                connection.connect()
                transport_socket = connection.sock
                progress("connection_complete")
                if (watch_interrupted.is_set()
                        or (interruption_requested is not None
                            and interruption_requested())):
                    watch_interrupted.set()
                    raise LLMInputPortInterrupted(submission_state="not_submitted")

                transport_phase = "request_write"
                request_write_started = True
                progress("request_write_start")
                if connection.sock is not None:
                    connection.sock.settimeout(_remaining_seconds(deadline))
                connection.request(
                    method, route.request_target, body=body, headers=headers)
                request_write_completed = True
                progress("request_write_return")

                transport_phase = "response_headers"
                if connection.sock is not None:
                    connection.sock.settimeout(_remaining_seconds(deadline))
                response = connection.getresponse()
                response_headers_received = True
                detail["http_status"] = response.status
                candidate_id = response.getheader("x-request-id")
                if (candidate_id and len(candidate_id) <= 1024
                        and "\r" not in candidate_id
                        and "\n" not in candidate_id):
                    request_id = candidate_id
                progress(
                    "response_headers_received",
                    {"http_status": response.status})

                transport_phase = "response_body"
                vendor_bytes = _read_bounded(
                    response, connection, max_bytes=max_response_bytes,
                    deadline=deadline, transport_socket=transport_socket)
                response_body_completed = True
                progress("body_complete", {"byte_count": len(vendor_bytes)})

            if (watch_interrupted.is_set()
                    or (interruption_requested is not None
                        and interruption_requested())):
                watch_interrupted.set()
                raise LLMInputPortInterrupted(
                    submission_state="response_complete")

            if request_id is None and body is not None:
                try:
                    vendor_document = json.loads(vendor_bytes)
                    body_request_id = (
                        vendor_document.get("id")
                        if isinstance(vendor_document, Mapping) else None)
                    if (isinstance(body_request_id, str)
                            and 0 < len(body_request_id) <= 1024):
                        request_id = body_request_id
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass

            if response.status < 200 or response.status >= 300:
                failure_category, retry_eligible, disposition = (
                    _http_failure_metadata(response.status))
                outcome = "http_failure"
                detail.update({
                    "transport_phase": "response_status_evaluation",
                    "submission_state": "response_complete",
                    "failure_category": failure_category,
                    "failure_code": f"http_status_{response.status}",
                    "retry_eligible": retry_eligible,
                    "recovery_disposition": disposition,
                })
                self._audit.provider_attempt_finished(
                    attempt, route=route_audit,
                    provider_attempt_id=provider_attempt_id,
                    provider_request_id=provider_request_id,
                    call_kind=call_kind, outcome=outcome,
                    external_request_id=request_id, detail=detail)
                return _ProviderCallResult(
                    None, outcome, provider_attempt_id, provider_request_id,
                    dict(detail))

            transport_phase = "response_normalization"
            try:
                response_bytes = normalize_openai_compatible_response(
                    vendor_bytes)
            except ResponseEnvelopeError as exc:
                detail.update({
                    "transport_phase": transport_phase,
                    "exception_category": "response_protocol_invalid",
                    "exception_type": type(exc).__name__,
                    "submission_state": "response_complete",
                    "failure_category": "response_protocol_invalid",
                    "failure_code": (
                        "response_normalization_response_protocol_invalid"),
                    "retry_eligible": False,
                    "recovery_disposition": "block_no_retry",
                })
                outcome = "protocol_failure"
                self._audit.provider_attempt_finished(
                    attempt, route=route_audit,
                    provider_attempt_id=provider_attempt_id,
                    provider_request_id=provider_request_id,
                    call_kind=call_kind, outcome=outcome,
                    external_request_id=request_id, detail=detail)
                return _ProviderCallResult(
                    None, outcome, provider_attempt_id, provider_request_id,
                    dict(detail))
            if len(response_bytes) > max_response_bytes:
                detail.update({
                    "transport_phase": "response_normalization",
                    "submission_state": "response_complete",
                    "failure_category": "response_size_limit",
                    "failure_code": (
                        "response_normalization_response_size_limit"),
                    "retry_eligible": False,
                    "recovery_disposition": "block_no_retry",
                })
                outcome = "response_too_large"
                self._audit.provider_attempt_finished(
                    attempt, route=route_audit,
                    provider_attempt_id=provider_attempt_id,
                    provider_request_id=provider_request_id,
                    call_kind=call_kind, outcome=outcome,
                    external_request_id=request_id, detail=detail)
                return _ProviderCallResult(
                    None, outcome, provider_attempt_id, provider_request_id,
                    dict(detail))
            detail.update({
                "submission_state": "response_complete",
                "recovery_disposition": "return_response",
            })
            if request_id is not None:
                detail["external_request_id"] = request_id
            self._audit.provider_attempt_finished(
                attempt, route=route_audit,
                provider_attempt_id=provider_attempt_id,
                provider_request_id=provider_request_id,
                call_kind=call_kind, outcome="response_returned",
                external_request_id=request_id, detail=detail)
            return _ProviderCallResult(
                response_bytes, "response_returned", provider_attempt_id,
                provider_request_id, dict(detail))
        except OverflowError as caught:
            failure_exception: BaseException = caught
            outcome = "response_too_large"
            category = "response_size_limit"
        except LLMInputPortInterrupted as caught:
            failure_exception = caught
            outcome = "owner_interrupted"
            category = "owner_interrupted"
        except Exception as caught:
            failure_exception = caught
            if watch_interrupted.is_set():
                category = "owner_interrupted"
                outcome = "owner_interrupted"
            else:
                category = _exception_category(caught)
                if (call_kind == "real_model_call"
                        and request_write_started
                        and not response_headers_received):
                    outcome = "submission_unknown"
                elif category == "timeout":
                    outcome = "timeout"
                elif isinstance(caught, CredentialResolutionError):
                    outcome = "credential_failure"
                else:
                    outcome = "transport_failure"
        finally:
            watch_stop.set()
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            if watcher is not None:
                watcher.join(timeout=0.2)
        if response_body_completed:
            submission_state = "response_complete"
            recovery_disposition = "block_no_retry"
        elif response_headers_received:
            submission_state = "response_headers_received_body_incomplete"
            recovery_disposition = "block_no_retry"
        elif request_write_completed:
            submission_state = "request_write_completed_no_response_headers"
            recovery_disposition = "block_no_retry"
        elif request_write_started:
            submission_state = "request_write_started_completion_unknown"
            recovery_disposition = "block_no_retry"
        else:
            submission_state = "not_submitted"
            recovery_disposition = "block_no_retry"
        retry_eligible = (
            not request_write_started
            and category in {
                "timeout", "name_resolution_failure", "tls_failure",
                "connection_failure", "http_protocol_failure",
            })
        failure_category = category
        if outcome == "submission_unknown":
            failure_category = "submission_unknown"
        detail.update({
            "transport_phase": transport_phase,
            "exception_category": category,
            "exception_type": type(failure_exception).__name__,
            "submission_state": submission_state,
            "failure_category": failure_category,
            "failure_code": f"{transport_phase}_{category}",
            "retry_eligible": retry_eligible,
            "recovery_disposition": recovery_disposition,
        })
        self._audit.provider_attempt_finished(
            attempt, route=route_audit,
            provider_attempt_id=provider_attempt_id,
            provider_request_id=provider_request_id,
            call_kind=call_kind, outcome=outcome,
            external_request_id=request_id, detail=detail)
        if outcome == "owner_interrupted":
            raise LLMInputPortInterrupted(
                submission_state=submission_state)
        return _ProviderCallResult(
            None, outcome, provider_attempt_id, provider_request_id,
            dict(detail))

    def request_once(self, attempt: LLMCallAttempt) -> LLMInputResponseBytes:
        return self._request_once(attempt, interruption_requested=None)

    def request_once_interruptible(
            self, attempt: LLMCallAttempt, *,
            interruption_requested: Callable[[], bool],
    ) -> LLMInputResponseBytes:
        if not callable(interruption_requested):
            raise TypeError("external interruption probe must be callable")
        return self._request_once(
            attempt, interruption_requested=interruption_requested)

    def _request_once(
            self, attempt: LLMCallAttempt, *,
            interruption_requested: Callable[[], bool] | None,
    ) -> LLMInputResponseBytes:
        if not isinstance(attempt, LLMCallAttempt):
            raise TypeError("external input port requires LLMCallAttempt")
        with self._lock:
            if self._closed or attempt.model_condition != self._model_condition:
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="external_adapter_closed_or_model_mismatch")
            try:
                provider_request_from_envelope(
                    attempt.canonical_request_bytes,
                    expected_model_condition=attempt.model_condition,
                    expected_max_output_tokens=self._max_output_tokens,
                    outbound_model=self._routes[0].outbound_model,
                    reasoning_effort=self._reasoning_effort)
            except ResponseEnvelopeError:
                self._audit.finish_external_invocation(
                    attempt, outcome="adapter_not_submitted",
                    detail={
                        "failure_category": "adapter_not_submitted",
                        "retry_eligible": False,
                        "recovery_disposition": "block_no_retry",
                    })
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="external_request_protocol_invalid")
            if not self._audit.reserve_external_invocation(attempt):
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="external_duplicate_submission_prevented")
            call_ordinal = 0
            formal_call_count = 0
            probe_call_count = 0
            recovery_cycle_count = 0
            prior_provider_attempt_id: str | None = None
            last_result: _ProviderCallResult | None = None
            last_formal_result: _ProviderCallResult | None = None
            last_route: _ExternalRoute | None = None
            route_recovery: list[dict[str, object]] = []

            def invocation_detail(
                    *, recovery_reason: str | None = None,
            ) -> dict[str, object]:
                value: dict[str, object] = {
                    "formal_call_count": formal_call_count,
                    "probe_call_count": probe_call_count,
                    "physical_call_count": call_ordinal,
                    "recovery_cycle_count": recovery_cycle_count,
                    "recovery_strategy": self._recovery.strategy,
                    "route_recovery": list(route_recovery),
                }
                if recovery_reason is not None:
                    value["recovery_reason"] = recovery_reason
                if last_result is not None and last_route is not None:
                    value["last_provider_attempt"] = {
                        "provider_attempt_id": last_result.provider_attempt_id,
                        "provider_request_id": last_result.provider_request_id,
                        "outcome": last_result.outcome,
                        "route": last_route.audit_identity(),
                        "diagnostic": dict(last_result.detail),
                    }
                if last_formal_result is not None and last_route is not None:
                    value["last_formal_provider_attempt"] = {
                        "provider_attempt_id": (
                            last_formal_result.provider_attempt_id),
                        "provider_request_id": (
                            last_formal_result.provider_request_id),
                        "outcome": last_formal_result.outcome,
                        "route": last_route.audit_identity(),
                        "diagnostic": dict(last_formal_result.detail),
                    }
                return value

            route_index = self._active_route_index
            route = self._routes[route_index]
            last_route = route
            try:
                request_bytes = provider_request_from_envelope(
                    attempt.canonical_request_bytes,
                    expected_model_condition=attempt.model_condition,
                    expected_max_output_tokens=self._max_output_tokens,
                    outbound_model=route.outbound_model,
                    reasoning_effort=self._reasoning_effort)
            except ResponseEnvelopeError:
                self._audit.finish_external_invocation(
                    attempt, outcome="request_protocol_invalid",
                    detail=invocation_detail(
                        recovery_reason="request_protocol_invalid"))
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="external_route_request_protocol_invalid")

            def perform(
                    *, body: bytes, call_kind: str, deadline: float,
            ) -> _ProviderCallResult:
                nonlocal call_ordinal, formal_call_count, probe_call_count
                nonlocal prior_provider_attempt_id, last_result
                current_call_ordinal = call_ordinal
                call_ordinal += 1
                if call_kind == "health_probe":
                    probe_call_count += 1
                else:
                    formal_call_count += 1
                try:
                    result = self._perform_request(
                        attempt, route=route, method="POST", body=body,
                        call_kind=call_kind,
                        call_ordinal=current_call_ordinal,
                        prior_provider_attempt_id=prior_provider_attempt_id,
                        deadline=deadline,
                        max_response_bytes=min(
                            self._max_response_bytes,
                            attempt.max_response_bytes),
                        interruption_requested=interruption_requested)
                except LLMInputPortInterrupted:
                    self._audit.finish_external_invocation(
                        attempt, outcome="owner_interrupted",
                        detail=invocation_detail(
                            recovery_reason="owner_interrupted"))
                    raise
                prior_provider_attempt_id = result.provider_attempt_id
                last_result = result
                return result

            def response(
                    result: _ProviderCallResult,
            ) -> LLMInputResponseBytes:
                response_bytes = result.response_bytes
                if response_bytes is None:
                    raise RuntimeError(
                        "external provider response result is unavailable")
                self._audit.finish_external_invocation(
                    attempt, outcome="response_returned",
                    detail={
                        **invocation_detail(),
                        "route": route.audit_identity()})
                self._active_route_index = route_index
                status_code = result.detail.get("http_status")
                external_request_id = result.detail.get(
                    "external_request_id")
                return LLMInputResponseBytes(
                    response_bytes,
                    status_code=(
                        status_code if isinstance(status_code, int)
                        and not isinstance(status_code, bool) else None),
                    external_request_id=(
                        external_request_id
                        if isinstance(external_request_id, str) else None),
                )

            result = perform(
                body=request_bytes, call_kind="real_model_call",
                deadline=time.monotonic() + self._timeout_seconds)
            last_formal_result = result
            final_recovery_reason = "failure_blocked"
            final_failure_code: str | None = None

            while True:
                if result.response_bytes is not None:
                    return response(result)
                if not _health_probe_eligible(result):
                    final_recovery_reason = "failure_not_probe_eligible"
                    break
                if (recovery_cycle_count >=
                        self._recovery.max_probe_success_formal_failure_cycles):
                    final_recovery_reason = "recovery_cycle_limit_exhausted"
                    final_failure_code = (
                        "external_recovery_cycle_limit_exhausted")
                    break

                probe_deadline = (
                    time.monotonic()
                    + self._recovery.probe_timeout_budget_seconds)
                probe_attempts = 0
                probe_succeeded = False
                probe_budget_exhausted = False
                for _probe_ordinal in range(
                        self._recovery.max_probe_attempts):
                    if time.monotonic() >= probe_deadline:
                        probe_budget_exhausted = True
                        break
                    probe_result = perform(
                        body=_probe_request(
                            route, self._reasoning_effort),
                        call_kind="health_probe",
                        deadline=probe_deadline)
                    probe_attempts += 1
                    if probe_result.response_bytes is not None:
                        probe_succeeded = True
                        break
                route_recovery.append({
                    "route": route.audit_identity(),
                    "probe_attempts": probe_attempts,
                    "probe_succeeded": probe_succeeded,
                    "probe_budget_exhausted": probe_budget_exhausted,
                })
                if not probe_succeeded:
                    if probe_budget_exhausted:
                        final_recovery_reason = "health_probe_budget_exhausted"
                        final_failure_code = (
                            "external_health_probe_budget_exhausted")
                    else:
                        final_recovery_reason = "health_probe_attempts_exhausted"
                        final_failure_code = (
                            "external_health_probe_attempts_exhausted")
                    break

                recovery_cycle_count += 1
                result = perform(
                    body=request_bytes, call_kind="real_model_call",
                    deadline=time.monotonic() + self._timeout_seconds)
                last_formal_result = result

            self._audit.finish_external_invocation(
                attempt, outcome="no_result",
                detail=invocation_detail(
                    recovery_reason=final_recovery_reason))
            if last_formal_result is None:
                raise LLMInputPortFailure(
                    "provider_failure", submission_state="not_submitted",
                    failure_code="external_provider_no_route_result")
            raise _neutral_failure(
                last_formal_result, failure_code=final_failure_code)

    def close(self) -> None:
        with self._lock:
            self._closed = True


__all__ = [
    "AdapterConfigError", "ExternalProviderInputPort",
    "LEGACY_SCHEMA_VERSION", "SCHEMA_VERSION",
]
