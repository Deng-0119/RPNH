from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time

import pytest

from cpn.llm_adapters import external_provider as external_provider_module
from cpn.llm_adapters.external_provider import ExternalProviderInputPort
from cpn.rpnh.llm_contracts import (
    LLMCallAttempt,
    LLMInputPortFailure,
    LLMInputPortInterrupted,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef


MODEL = "user/exact-model:v1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _provider_response(text: str, request_id: str) -> bytes:
    return json.dumps({
        "id": request_id,
        "choices": [{
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }, separators=(",", ":")).encode("utf-8")


class _FakeSocket:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)


class _FakeResponse:
    def __init__(self, status: int, payload: bytes, request_id: str) -> None:
        self.status = status
        self._payload = payload
        self._request_id = request_id

    def getheader(self, name: str) -> str | None:
        return self._request_id if name.lower() == "x-request-id" else None

    def read(self, _size: int) -> bytes:
        payload, self._payload = self._payload, b""
        return payload


class _FakeHTTPSConnection:
    def __init__(
            self, owner: "_FakeHTTPSFactory", action: object,
            hostname: str, port: int | None,
    ) -> None:
        self._owner = owner
        self._action = action
        self.hostname = hostname
        self.port = port
        self.sock = _FakeSocket()

    def connect(self) -> None:
        return None

    def request(
            self, method: str, target: str, *, body: bytes | None,
            headers: dict[str, str],
    ) -> None:
        self._owner.requests.append({
            "method": method,
            "target": target,
            "body": body,
            "headers": dict(headers),
            "hostname": self.hostname,
            "port": self.port,
        })

    def getresponse(self) -> _FakeResponse:
        if isinstance(self._action, BaseException):
            raise self._action
        status, payload, request_id = self._action
        return _FakeResponse(status, payload, request_id)

    def close(self) -> None:
        return None


class _FakeHTTPSFactory:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)
        self.requests: list[dict[str, object]] = []

    def __call__(
            self, hostname: str, port: int | None, *, timeout: float,
            context: object,
    ) -> _FakeHTTPSConnection:
        del timeout, context
        if not self.actions:
            raise AssertionError("unexpected external-provider physical call")
        return _FakeHTTPSConnection(
            self, self.actions.pop(0), hostname, port)


def _recovery(**overrides: int) -> dict[str, object]:
    value: dict[str, object] = {
        "strategy": "bounded_same_route_health_probe/v1",
        "max_probe_attempts": 3,
        "probe_timeout_budget_seconds": 300,
        "max_probe_success_formal_failure_cycles": 3,
    }
    value.update(overrides)
    return value


def _port_and_attempt(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        actions: list[object], *, recovery: dict[str, object] | None = None,
        reasoning_effort: str | None = None,
) -> tuple[ExternalProviderInputPort, LLMCallAttempt, _FakeHTTPSFactory]:
    config = tmp_path / "adapter.json"
    config_document = {
        "schema_version": (
            "external_provider_adapter_config/v3"
            if reasoning_effort is not None
            else "external_provider_adapter_config/v2"),
        "adapter_kind": "external_provider",
        "model_condition": MODEL,
        "recovery": recovery or _recovery(),
        "routes": [{
            "route_id": "primary",
            "provider": "user provider",
            "backend": "user backend",
            "protocol": "openai_chat_completions/v1",
            "endpoint": "https://api.example.invalid/v1/chat/completions",
            "outbound_model": MODEL,
            "credential": None,
            "headers": {"X-Test-Route": "primary"},
        }],
    }
    if reasoning_effort is not None:
        config_document["reasoning_effort"] = reasoning_effort
    config.write_text(json.dumps(config_document), encoding="utf-8")
    factory = _FakeHTTPSFactory(actions)
    monkeypatch.setattr(
        external_provider_module.http.client, "HTTPSConnection", factory)
    request = _canonical({
        "protocol": "llm_request_envelope/v1",
        "model_condition": MODEL,
        "max_output_tokens": 16,
        "messages": [{"role": "user", "content": "original prompt"}],
        "tools": [],
        "tool_choice": "none",
    })
    attempt = LLMCallAttempt(
        invocation_ref=VersionRef(
            "llm_invocation_spec/v1", new_id("llm_invocation"),
            new_id("llm_invocation_version")),
        attempt_ref=VersionRef(
            "llm_invocation_attempt/v1", new_id("llm_invocation_attempt"),
            new_id("llm_invocation_attempt_version")),
        attempt_ordinal=0,
        model_condition=MODEL,
        canonical_request_bytes=request,
        max_response_bytes=1024 * 1024,
    )
    return ExternalProviderInputPort(
        model_condition=MODEL,
        max_output_tokens=16,
        timeout_seconds=10,
        max_response_bytes=1024 * 1024,
        config_path=config,
        destination_run_root=tmp_path / "run",
        reasoning_effort=reasoning_effort,
    ), attempt, factory


def _request_documents(
        factory: _FakeHTTPSFactory,
) -> list[dict[str, object]]:
    return [json.loads(request["body"]) for request in factory.requests]


def _audit(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "run" / "adapter-private" / "llm-attempts.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _started_attempts(tmp_path: Path) -> list[dict[str, object]]:
    return [
        item for item in _audit(tmp_path)
        if item["lifecycle"] == "provider_attempt_started"
    ]


def test_first_probe_success_stops_probe_sequence_and_retries_formal_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, attempt, factory = _port_and_attempt(tmp_path, monkeypatch, [
        socket.timeout("formal response headers timeout"),
        (200, _provider_response("READY.", "probe-1"), "probe-1"),
        (200, _provider_response("formal result", "formal-2"), "formal-2"),
    ])

    result = port.request_once(attempt)

    documents = _request_documents(factory)
    assert [document["messages"][0]["content"] for document in documents] == [
        "original prompt", "Reply with READY.", "original prompt",
    ]
    assert documents[1]["model"] == MODEL
    assert documents[1]["max_tokens"] == 8
    assert {document["model"] for document in documents} == {MODEL}
    assert {request["hostname"] for request in factory.requests} == {
        "api.example.invalid"}
    assert {request["target"] for request in factory.requests} == {
        "/v1/chat/completions"}
    assert {request["headers"]["X-Test-Route"]
            for request in factory.requests} == {"primary"}
    assert result.status_code == 200
    assert result.external_request_id == "formal-2"
    assert factory.actions == []

    started = _started_attempts(tmp_path)
    assert [item["call_kind"] for item in started] == [
        "real_model_call", "health_probe", "real_model_call",
    ]
    assert [item["call_ordinal"] for item in started] == [0, 1, 2]
    assert len({item["provider_attempt_id"] for item in started}) == 3
    assert len({item["provider_request_id"] for item in started}) == 3
    assert started[1]["prior_provider_attempt_id"] == (
        started[0]["provider_attempt_id"])
    assert started[2]["prior_provider_attempt_id"] == (
        started[1]["provider_attempt_id"])
    assert {(item["provider"], item["backend"], item["outbound_model"])
            for item in started} == {
                ("user provider", "user backend", MODEL)}

    audit = _audit(tmp_path)
    finished = [
        item for item in audit
        if item["lifecycle"] == "provider_attempt_finished"
    ]
    assert {item["provider_attempt_id"] for item in finished} == {
        item["provider_attempt_id"] for item in started}
    first_finished = next(
        item for item in audit
        if item["lifecycle"] == "provider_attempt_finished"
        and item["provider_attempt_id"] == started[0]["provider_attempt_id"])
    assert first_finished["outcome"] == "submission_unknown"
    assert first_finished["detail"]["failure_code"] == (
        "response_headers_timeout")
    invocation_finished = [
        item for item in audit
        if item["lifecycle"] == "external_provider_invocation_finished"
    ]
    assert len(invocation_finished) == 1
    assert invocation_finished[0]["outcome"] == "response_returned"
    assert invocation_finished[0]["detail"]["formal_call_count"] == 2
    assert invocation_finished[0]["detail"]["probe_call_count"] == 1
    assert invocation_finished[0]["detail"]["physical_call_count"] == 3
    assert {path.relative_to(tmp_path / "run").parts[0]
            for path in (tmp_path / "run").rglob("*") if path.is_file()} == {
                "adapter-private"}


def test_selected_reasoning_effort_reaches_formal_and_probe_requests(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, attempt, factory = _port_and_attempt(
        tmp_path, monkeypatch, [
            socket.timeout("formal response headers timeout"),
            (200, _provider_response("READY.", "probe-1"), "probe-1"),
            (200, _provider_response("formal result", "formal-2"), "formal-2"),
        ],
        reasoning_effort="user-defined-xhigh",
    )

    port.request_once(attempt)

    documents = _request_documents(factory)
    assert len(documents) == 3
    assert {document["reasoning_effort"] for document in documents} == {
        "user-defined-xhigh"}
    assert documents[1]["messages"] == [{
        "role": "user", "content": "Reply with READY."}]


def test_second_probe_success_skips_third_probe(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, attempt, factory = _port_and_attempt(tmp_path, monkeypatch, [
        socket.timeout("formal response headers timeout"),
        (503, b'{"error":"down"}', "probe-1"),
        (200, _provider_response("READY.", "probe-2"), "probe-2"),
        (200, _provider_response("formal result", "formal-2"), "formal-2"),
    ])

    port.request_once(attempt)

    documents = _request_documents(factory)
    assert [document["messages"][0]["content"] for document in documents] == [
        "original prompt", "Reply with READY.", "Reply with READY.",
        "original prompt",
    ]
    assert factory.actions == []


def test_three_probe_failures_return_one_final_provider_interruption(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, attempt, factory = _port_and_attempt(tmp_path, monkeypatch, [
        socket.timeout("formal response headers timeout"),
        (503, b'{"error":"down"}', "probe-1"),
        (503, b'{"error":"down"}', "probe-2"),
        (503, b'{"error":"down"}', "probe-3"),
    ])

    with pytest.raises(LLMInputPortFailure) as exc_info:
        port.request_once(attempt)

    assert exc_info.value.disposition == "submission_unknown"
    assert exc_info.value.submission_state == "submission_unknown"
    assert exc_info.value.failure_code == (
        "external_health_probe_attempts_exhausted")
    assert len(factory.requests) == 4
    assert [item["call_kind"] for item in _started_attempts(tmp_path)] == [
        "real_model_call", "health_probe", "health_probe", "health_probe",
    ]
    assert factory.actions == []


def test_probe_success_formal_failure_cycles_are_strictly_bounded(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[object] = [
        socket.timeout("formal-1"),
        (200, _provider_response("READY.", "probe-1"), "probe-1"),
        socket.timeout("formal-2"),
        (200, _provider_response("READY.", "probe-2"), "probe-2"),
        socket.timeout("formal-3"),
        (200, _provider_response("READY.", "probe-3"), "probe-3"),
        socket.timeout("formal-4"),
    ]
    port, attempt, factory = _port_and_attempt(
        tmp_path, monkeypatch, actions)

    with pytest.raises(LLMInputPortFailure) as exc_info:
        port.request_once(attempt)

    assert exc_info.value.failure_code == (
        "external_recovery_cycle_limit_exhausted")
    assert [item["call_kind"] for item in _started_attempts(tmp_path)] == [
        "real_model_call", "health_probe", "real_model_call",
        "health_probe", "real_model_call", "health_probe",
        "real_model_call",
    ]
    assert len(factory.requests) == 7
    assert factory.actions == []


def test_probe_timeout_budget_is_shared_by_one_probe_sequence(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"value": 0.0}

    class _AdvancingFactory(_FakeHTTPSFactory):
        def __call__(self, *args, **kwargs):
            connection = super().__call__(*args, **kwargs)
            original = connection.getresponse

            def getresponse() -> _FakeResponse:
                response = original()
                clock["value"] = 2.0
                return response

            if len(self.requests) == 1:
                connection.getresponse = getresponse  # type: ignore[method-assign]
            return connection

    config_recovery = _recovery(probe_timeout_budget_seconds=1)
    port, attempt, _unused = _port_and_attempt(tmp_path, monkeypatch, [
        socket.timeout("formal response headers timeout"),
        (503, b'{"error":"down"}', "probe-1"),
    ], recovery=config_recovery)
    factory = _AdvancingFactory([
        socket.timeout("formal response headers timeout"),
        (503, b'{"error":"down"}', "probe-1"),
    ])
    monkeypatch.setattr(
        external_provider_module.http.client, "HTTPSConnection", factory)
    monkeypatch.setattr(
        external_provider_module.time, "monotonic",
        lambda: clock["value"])

    with pytest.raises(LLMInputPortFailure) as exc_info:
        port.request_once(attempt)

    assert exc_info.value.failure_code == (
        "external_health_probe_budget_exhausted")
    assert len(factory.requests) == 2


def test_owner_stop_closes_inflight_response_wait_without_probe_or_retry(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, attempt, _unused = _port_and_attempt(
        tmp_path, monkeypatch,
        [(200, _provider_response("unused", "unused"), "unused")])
    requests: list[dict[str, object]] = []

    class _BlockingConnection:
        def __init__(self, *_args, **_kwargs) -> None:
            self.sock = _FakeSocket()
            self.closed = threading.Event()
            # Real HTTPResponse may retain a makefile socket reference. The
            # interrupt now shuts down the transport instead of only closing
            # the connection wrapper.
            self.sock.shutdown = lambda _how: self.closed.set()

        def connect(self) -> None:
            return None

        def request(self, method, target, *, body, headers) -> None:
            requests.append({
                "method": method, "target": target,
                "body": body, "headers": dict(headers),
            })

        def getresponse(self):
            if not self.closed.wait(3):
                raise AssertionError("owner stop did not close HTTPS wait")
            raise ConnectionAbortedError("closed by owner stop")

        def close(self) -> None:
            self.closed.set()

    monkeypatch.setattr(
        external_provider_module.http.client,
        "HTTPSConnection", _BlockingConnection)
    interrupted = threading.Event()
    timer = threading.Timer(0.2, interrupted.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(LLMInputPortInterrupted) as exc_info:
            port.request_once_interruptible(
                attempt, interruption_requested=interrupted.is_set)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 3
    assert exc_info.value.submission_state == (
        "request_write_completed_no_response_headers")
    assert len(requests) == 1
    assert json.loads(requests[0]["body"])["messages"][0]["content"] == (
        "original prompt")
    started_attempts = _started_attempts(tmp_path)
    assert [item["call_kind"] for item in started_attempts] == [
        "real_model_call"]
    finished = [
        item for item in _audit(tmp_path)
        if item["lifecycle"] == "provider_attempt_finished"]
    assert [item["outcome"] for item in finished] == ["owner_interrupted"]
    invocation = [
        item for item in _audit(tmp_path)
        if item["lifecycle"] == "external_provider_invocation_finished"]
    assert len(invocation) == 1
    assert invocation[0]["outcome"] == "owner_interrupted"
    assert invocation[0]["detail"]["physical_call_count"] == 1
    assert invocation[0]["detail"]["probe_call_count"] == 0
