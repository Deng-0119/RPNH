"""Deterministic offline HTTP overlap, drain, cancellation and duplicate guards.

Every HTTP connection is an in-process fake. These are contract tests, not a
provider throughput benchmark or a change to Registry/PN capacity policy.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import threading

import pytest

from cpn.llm_adapters import external_provider as external
from cpn.llm_adapters.external_provider import ExternalProviderInputPort
from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputPortInterrupted
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from test_external_provider_health_recovery import (
    MODEL, _audit, _port_and_attempt, _provider_response,
)


_WAIT = 5


def _other_attempt(attempt, text="second"):
    document = json.loads(attempt.canonical_request_bytes)
    document["messages"][0]["content"] = text
    return replace(
        attempt,
        invocation_ref=VersionRef("llm_invocation_spec/v1", new_id("llm_invocation"),
                                  new_id("llm_invocation_version")),
        attempt_ref=VersionRef("llm_invocation_attempt/v1", new_id("llm_invocation_attempt"),
                               new_id("llm_invocation_attempt_version")),
        canonical_request_bytes=json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),
    )


class _Socket:
    def __init__(self):
        self.shutdown_called = threading.Event()

    def settimeout(self, timeout):
        assert timeout > 0

    def shutdown(self, _how):
        self.shutdown_called.set()


class _Response:
    status = 200

    def __init__(self, connection, *, status=200, read_hook=None):
        self.status = status
        self.connection = connection
        self.payload = _provider_response(connection.prompt, connection.prompt)
        self.read_hook = read_hook

    def getheader(self, name):
        return self.connection.prompt if name == "x-request-id" else None

    def read(self, _size):
        if self.read_hook is not None:
            self.read_hook(self.connection)
        result, self.payload = self.payload, b""
        return result


class _Connection:
    def __init__(self, factory, hostname, port):
        self.factory = factory
        self.hostname = hostname
        self.port = port
        self.sock = _Socket()
        self.transport_socket = self.sock
        self.closed = False
        self.prompt = None

    def connect(self):
        if self.factory.connect_hook is not None:
            self.factory.connect_hook(self)

    def request(self, method, target, *, body, headers):
        self.document = json.loads(body)
        self.prompt = self.document["messages"][0]["content"]
        self.headers = dict(headers)
        self.target = target
        with self.factory.lock:
            self.factory.requests.append(self)
        if self.factory.request_hook is not None:
            self.factory.request_hook(self)

    def getresponse(self):
        if self.factory.response_hook is not None:
            return self.factory.response_hook(self)
        return _Response(self)

    def close(self):
        self.closed = True


class _Factory:
    def __init__(self, *, connect_hook=None, request_hook=None, response_hook=None):
        self.connect_hook = connect_hook
        self.request_hook = request_hook
        self.response_hook = response_hook
        self.lock = threading.Lock()
        self.connections = []
        self.requests = []

    def __call__(self, hostname, port, **_kwargs):
        connection = _Connection(self, hostname, port)
        with self.lock:
            self.connections.append(connection)
        return connection


def _install(monkeypatch, factory):
    monkeypatch.setattr(external.http.client, "HTTPSConnection", factory)
    monkeypatch.setattr(external.http.client, "HTTPConnection", factory)
    monkeypatch.setattr(external.ssl, "create_default_context", lambda: object())


def _reopen(tmp_path):
    return ExternalProviderInputPort(
        model_condition=MODEL, max_output_tokens=16, timeout_seconds=10,
        max_response_bytes=1024 * 1024, config_path=tmp_path / "adapter.json",
        destination_run_root=tmp_path / "run")


def _finished(tmp_path, attempt):
    return [item for item in _audit(tmp_path)
            if item["lifecycle"] == "external_provider_invocation_finished"
            and item["attempt_ref"] == str(attempt.attempt_ref.version_id)]


@pytest.mark.parametrize("scheme", ["https", "http"])
def test_distinct_attempts_overlap_and_keep_response_identity(tmp_path, monkeypatch, scheme):
    port, first, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    if scheme == "http":
        document = json.loads((tmp_path / "adapter.json").read_text())
        document["routes"][0]["endpoint"] = "http://127.0.0.1/v1/chat/completions"
        (tmp_path / "adapter.json").write_text(json.dumps(document))
        port = _reopen(tmp_path)
    second = _other_attempt(first)
    entered = threading.Barrier(3)
    release = threading.Event()
    def response(connection):
        entered.wait(_WAIT)
        assert release.wait(_WAIT)
        return _Response(connection)
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(port.request_once, item) for item in (first, second)]
        try:
            entered.wait(_WAIT)
            assert len(factory.requests) == 2
        finally:
            release.set()
        results = [future.result(_WAIT) for future in futures]
    assert [result.external_request_id for result in results] == ["original prompt", "second"]
    assert all(connection.closed for connection in factory.connections)
    for attempt in (first, second):
        final, = _finished(tmp_path, attempt)
        assert final["detail"]["formal_call_count"] == 1
        assert final["detail"]["probe_call_count"] == 0
        assert final["detail"]["physical_call_count"] == 1
    port.close()


@pytest.mark.parametrize("separate_port", [False, True])
def test_durable_duplicate_guard_shared_ports_and_reopen(tmp_path, monkeypatch, separate_port):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    other = _reopen(tmp_path) if separate_port else port
    entered, release = threading.Event(), threading.Event()
    def response(connection):
        entered.set()
        assert release.wait(_WAIT)
        return _Response(connection)
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(port.request_once, attempt)
        assert entered.wait(_WAIT)
        duplicate = pool.submit(other.request_once, attempt)
        try:
            with pytest.raises(LLMInputPortFailure, match="external_duplicate_submission_prevented"):
                duplicate.result(_WAIT)
        finally:
            release.set()
        first.result(_WAIT)
    port.close()
    other.close()
    reopened = _reopen(tmp_path)
    with pytest.raises(LLMInputPortFailure, match="external_duplicate_submission_prevented"):
        reopened.request_once(attempt)
    assert len(factory.requests) == 1
    assert len(_finished(tmp_path, attempt)) == 1
    reopened.close()


@pytest.mark.parametrize("phase", ["before_write", "partial_write", "headers", "body"])
def test_cancel_one_attempt_does_not_touch_other_connection(tmp_path, monkeypatch, phase):
    port, first, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    second = _other_attempt(first)
    first_entered, second_entered = threading.Event(), threading.Event()
    release_first, release_second = threading.Event(), threading.Event()
    cancel = threading.Event()
    first_connection = []

    def interrupted_io(connection):
        first_entered.set()
        assert connection.transport_socket.shutdown_called.wait(_WAIT)
        raise ConnectionAbortedError("synthetic owner cancellation")

    def connect(connection):
        # Start the second request only after the first reaches its phase gate.
        if phase == "before_write" and not first_connection:
            first_connection.append(connection)
            first_entered.set()
            assert release_first.wait(_WAIT)

    def request(connection):
        if connection.prompt == "original prompt" and phase == "partial_write":
            interrupted_io(connection)

    def response(connection):
        if connection.prompt == "second":
            second_entered.set()
            assert release_second.wait(_WAIT)
            return _Response(connection)
        if phase == "headers":
            interrupted_io(connection)
        if phase == "body":
            # Preserve the established socket after HTTPResponse detaches it.
            connection.sock = None
            return _Response(connection, read_hook=interrupted_io)
        return _Response(connection)

    factory = _Factory(connect_hook=connect, request_hook=request, response_hook=response)
    _install(monkeypatch, factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(port.request_once_interruptible, first,
                          interruption_requested=cancel.is_set)
        assert first_entered.wait(_WAIT)
        two = pool.submit(port.request_once, second)
        try:
            assert second_entered.wait(_WAIT)
            cancel.set()
            release_first.set()
            with pytest.raises(LLMInputPortInterrupted) as interrupted:
                one.result(_WAIT)
            expected = {
                "before_write": "not_submitted",
                "partial_write": "request_write_started_completion_unknown",
                "headers": "request_write_completed_no_response_headers",
                "body": "response_headers_received_body_incomplete",
            }
            assert interrupted.value.submission_state == expected[phase]
            second_transport, = [c for c in factory.connections if c.prompt == "second"]
            assert not second_transport.closed
            assert not second_transport.transport_socket.shutdown_called.is_set()
        finally:
            release_first.set()
            release_second.set()
        assert two.result(_WAIT).external_request_id == "second"
    first_final, = _finished(tmp_path, first)
    assert first_final["outcome"] == "owner_interrupted"
    assert first_final["detail"]["formal_call_count"] == 1
    assert first_final["detail"]["probe_call_count"] == 0
    second_final, = _finished(tmp_path, second)
    assert second_final["detail"]["physical_call_count"] == 1
    assert len(factory.requests) == (1 if phase == "before_write" else 2)
    assert port._inflight == set()
    port.close()


def _observe_drain(monkeypatch, port):
    waiting = threading.Event()
    original = port._state.wait
    def wait(timeout=None):
        waiting.set()
        return original(timeout)
    monkeypatch.setattr(port._state, "wait", wait)
    return waiting


@pytest.mark.parametrize("phase", ["reserve", "transport", "recovery", "final_audit"])
def test_close_rejects_new_calls_and_drains_every_accepted_phase(tmp_path, monkeypatch, phase):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    entered, release = threading.Event(), threading.Event()
    waiting = _observe_drain(monkeypatch, port)
    formal_count = 0
    def gate():
        entered.set()
        assert release.wait(_WAIT)
    def response(connection):
        nonlocal formal_count
        if connection.prompt == "original prompt":
            formal_count += 1
            if phase == "recovery" and formal_count == 1:
                return _Response(connection, status=503)
            if phase == "transport":
                gate()
        elif phase == "recovery":
            assert connection.prompt == "Reply with READY."
            gate()
        return _Response(connection)
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    if phase in {"reserve", "final_audit"}:
        name = "reserve_external_invocation" if phase == "reserve" else "finish_external_invocation"
        original = getattr(port._audit, name)
        def delayed(*args, **kwargs):
            gate()
            return original(*args, **kwargs)
        monkeypatch.setattr(port._audit, name, delayed)
    with ThreadPoolExecutor(max_workers=3) as pool:
        request = pool.submit(port.request_once, attempt)
        assert entered.wait(_WAIT)
        close = pool.submit(port.close)
        try:
            assert waiting.wait(_WAIT)
            repeated_close = pool.submit(port.close)
            assert not close.done()
            with pytest.raises(LLMInputPortFailure, match="external_adapter_closed_or_model_mismatch"):
                port.request_once(_other_attempt(attempt))
        finally:
            release.set()
        request.result(_WAIT)
        close.result(_WAIT)
        repeated_close.result(_WAIT)
    final, = _finished(tmp_path, attempt)
    assert final["outcome"] == "response_returned"
    expected_physical = 3 if phase == "recovery" else 1
    assert final["detail"]["physical_call_count"] == expected_physical
    assert len(factory.requests) == expected_physical
    assert port._inflight == set()
    port.close()


@pytest.mark.parametrize("method", ["reserve_external_invocation", "provider_attempt_started",
                                    "provider_attempt_finished", "finish_external_invocation"])
def test_audit_failure_releases_inflight_without_replaying_attempt(tmp_path, monkeypatch, method):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    factory = _Factory()
    _install(monkeypatch, factory)
    original = getattr(port._audit, method)
    def fail(*_args, **_kwargs):
        raise OSError("synthetic durable audit failure")
    monkeypatch.setattr(port._audit, method, fail)
    with pytest.raises(OSError, match="synthetic durable audit failure"):
        port.request_once(attempt)
    assert port._inflight == set()
    monkeypatch.setattr(port._audit, method, original)
    # This failed transport must not consume a private capacity token. Admission
    # of a distinct attempt still uses the unchanged caller/Registry authority.
    assert port.request_once(_other_attempt(attempt)).status_code == 200
    port.close()
    if method != "reserve_external_invocation":
        reopened = _reopen(tmp_path)
        with pytest.raises(LLMInputPortFailure, match="external_duplicate_submission_prevented"):
            reopened.request_once(attempt)
        reopened.close()


@pytest.mark.parametrize("observer_thread", [False, True])
def test_reentrant_close_from_request_or_observer_callback_fails_without_deadlock(
        tmp_path, monkeypatch, observer_thread):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    entered = threading.Event()
    close_rejected = threading.Event()
    caller = threading.get_ident()
    def response(connection):
        entered.set()
        assert connection.transport_socket.shutdown_called.wait(_WAIT)
        raise ConnectionAbortedError("synthetic callback stop")
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    def interruption():
        if observer_thread and threading.get_ident() == caller:
            return False
        if observer_thread:
            assert entered.wait(_WAIT)
        with pytest.raises(RuntimeError, match="cannot drain from a request callback"):
            port.close()
        close_rejected.set()
        return True
    with pytest.raises(LLMInputPortInterrupted):
        port.request_once_interruptible(attempt, interruption_requested=interruption)
    assert close_rejected.is_set()
    assert port._inflight == set()
    port.close()
    with pytest.raises(LLMInputPortFailure, match="external_adapter_closed_or_model_mismatch"):
        port.request_once(_other_attempt(attempt))


def test_recovery_chain_and_successful_peer_keep_route_and_counts(tmp_path, monkeypatch):
    port, first, _ = _port_and_attempt(tmp_path, monkeypatch, [], reasoning_effort="selected-effort")
    second = _other_attempt(first)
    probe_entered, peer_entered, release = threading.Event(), threading.Event(), threading.Event()
    formal_count = 0
    def response(connection):
        nonlocal formal_count
        if connection.prompt == "original prompt":
            formal_count += 1
            return _Response(connection, status=503 if formal_count == 1 else 200)
        if connection.prompt == "Reply with READY.":
            probe_entered.set()
        else:
            assert connection.prompt == "second"
            peer_entered.set()
        assert release.wait(_WAIT)
        return _Response(connection)
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(port.request_once, first)
        assert probe_entered.wait(_WAIT)
        two = pool.submit(port.request_once, second)
        try:
            assert peer_entered.wait(_WAIT)
        finally:
            release.set()
        assert one.result(_WAIT).external_request_id == "original prompt"
        assert two.result(_WAIT).external_request_id == "second"
    for attempt, formal, probe in ((first, 2, 1), (second, 1, 0)):
        final, = _finished(tmp_path, attempt)
        assert final["detail"]["formal_call_count"] == formal
        assert final["detail"]["probe_call_count"] == probe
        assert final["detail"]["physical_call_count"] == formal + probe
        started = [item for item in _audit(tmp_path)
                   if item["attempt_ref"] == str(attempt.attempt_ref.version_id)
                   and item["lifecycle"] == "provider_attempt_started"]
        assert [item["call_ordinal"] for item in started] == list(range(formal + probe))
        for previous, current in zip(started, started[1:]):
            assert current["prior_provider_attempt_id"] == previous["provider_attempt_id"]
    assert {c.hostname for c in factory.requests} == {"api.example.invalid"}
    assert {c.target for c in factory.requests} == {"/v1/chat/completions"}
    assert {c.document["model"] for c in factory.requests} == {MODEL}
    assert {c.document["reasoning_effort"] for c in factory.requests} == {"selected-effort"}
    assert {c.headers["X-Test-Route"] for c in factory.requests} == {"primary"}
    port.close()


def test_public_host_callbacks_stay_serial_without_serializing_transport(tmp_path, monkeypatch):
    from cpn.llm_adapters.public_credentials import PublicCredentialCapability
    from cpn.rpnh.public_material_contracts import canonical
    from test_registered_public_adapter import attempt, selection

    selected = selection()
    identity = selected.public_identity
    entered, release = threading.Event(), threading.Event()
    resolving = threading.Event()
    second_preparation = threading.Event()
    callbacks = []
    callback_active = 0
    peak_callbacks = 0
    callback_lock = threading.Lock()
    def resolve(_binding):
        nonlocal callback_active, peak_callbacks
        with callback_lock:
            callback_active += 1
            peak_callbacks = max(peak_callbacks, callback_active)
            callbacks.append("resolve")
            ordinal = len(callbacks)
        if ordinal == 1:
            resolving.set()
            assert release.wait(_WAIT)
        with callback_lock:
            callback_active -= 1
        return "synthetic-secret"
    def render(secret):
        nonlocal callback_active, peak_callbacks
        with callback_lock:
            callback_active += 1
            peak_callbacks = max(peak_callbacks, callback_active)
            callbacks.append("render")
            callback_active -= 1
        return ("Authorization", "Bearer " + secret)
    resolver, renderer = [PublicCredentialCapability(
        canonical(identity[role]["identity"]), canonical(identity[role]["observation"]), callback)
        for role, callback in (("resolver", resolve), ("renderer", render))]
    port = ExternalProviderInputPort.from_public(
        selected, destination_run_root=tmp_path / "run", resolver=resolver, renderer=renderer)
    first = attempt(selected)
    second = _other_attempt(first)
    original = port._resolved_headers
    def preparing(route):
        if resolving.is_set():
            second_preparation.set()
        return original(route)
    monkeypatch.setattr(port, "_resolved_headers", preparing)
    barrier = threading.Barrier(3)
    def response(connection):
        barrier.wait(_WAIT)
        return _Response(connection)
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(port.request_once, first)
        assert resolving.wait(_WAIT)
        two = pool.submit(port.request_once, second)
        try:
            assert second_preparation.wait(_WAIT)
            assert callbacks == ["resolve"]
            release.set()
            barrier.wait(_WAIT)
        finally:
            release.set()
        one.result(_WAIT)
        two.result(_WAIT)
    assert callbacks == ["resolve", "render", "resolve", "render"]
    assert peak_callbacks == 1
    assert all(c.headers["Authorization"] == "Bearer synthetic-secret" for c in factory.requests)
    assert len(factory.requests) == 2
    port.close()
