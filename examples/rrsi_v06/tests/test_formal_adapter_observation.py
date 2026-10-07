"""A real adapter with a fake HTTPS transport, never a provider connection."""
from __future__ import annotations

from dataclasses import replace
import json
import socket

import pytest

from cpn.llm_adapters.external_provider import ExternalProviderInputPort
from cpn.llm_adapters.factory import BoundLLMInputPort
from cpn.rpnh.llm_contracts import LLMInputPortFailure
from cpn.rpnh.registry.schema_catalog import canonical_json
from rpnh_rrsi.formal_observation import ObservedInputPort, usage_summary

from test_formal_observation import _attempt


def _response(text, usage):
    return (200, json.dumps({
        "id": "fake-request", "choices": [{
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop"}], "usage": usage,
    }).encode())


class FakeResponse:
    def __init__(self, action):
        self.status, self.payload = action

    def getheader(self, name):
        return "fake-request" if name.lower() == "x-request-id" else None

    def read(self, _size):
        result, self.payload = self.payload, b""
        return result


class FakeConnection:
    def __init__(self, action, requests):
        self.action = action
        self.requests = requests
        self.sock = self

    def settimeout(self, _timeout):
        pass

    def connect(self):
        pass

    def request(self, method, target, *, body, headers):
        self.requests.append(json.loads(body))

    def getresponse(self):
        if isinstance(self.action, BaseException):
            raise self.action
        return FakeResponse(self.action)

    def close(self):
        pass


def _port(tmp_path, monkeypatch, actions):
    import cpn.llm_adapters.external_provider as adapter
    requests = []
    pending = list(actions)

    def factory(*_args, **_kwargs):
        assert pending, "unexpected extra transport request"
        return FakeConnection(pending.pop(0), requests)

    monkeypatch.setattr(adapter.http.client, "HTTPSConnection", factory)
    config = tmp_path / "adapter.json"
    config.write_text(json.dumps({
        "schema_version": "external_provider_adapter_config/v2",
        "adapter_kind": "external_provider", "model_condition": "test-model",
        "recovery": {"strategy": "bounded_same_route_health_probe/v1",
                     "max_probe_attempts": 1,
                     "probe_timeout_budget_seconds": 30,
                     "max_probe_success_formal_failure_cycles": 1},
        "routes": [{"route_id": "primary", "provider": "offline",
                    "backend": "fake", "protocol": "openai_chat_completions/v1",
                    "endpoint": "https://offline.example.invalid/v1/chat/completions",
                    "outbound_model": "test-model", "credential": None,
                    "headers": {}}],
    }))
    adapter_port = ExternalProviderInputPort(
        model_condition="test-model", max_output_tokens=16,
        timeout_seconds=10, max_response_bytes=65536, config_path=config,
        destination_run_root=tmp_path / "audit")
    port = ObservedInputPort(BoundLLMInputPort(adapter_port, {"adapter_kind": "external_provider"}))
    attempt = replace(_attempt(), canonical_request_bytes=canonical_json({
        "protocol": "llm_request_envelope/v1", "model_condition": "test-model",
        "max_output_tokens": 16, "messages": [{"role": "user", "content": "offline"}],
        "tools": [], "tool_choice": "none",
    }), max_response_bytes=65536)
    return port, attempt, requests


def _started(tmp_path):
    rows = [json.loads(line) for line in (
        tmp_path / "audit" / "adapter-private" / "llm-attempts.jsonl").read_text().splitlines()]
    return [row for row in rows if row.get("lifecycle") == "provider_attempt_started"]


@pytest.mark.parametrize("usage, expected", [
    ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, 2),
    ({}, None),
])
def test_recovered_adapter_counts_one_invocation_not_three_physical_calls(
        tmp_path, monkeypatch, usage, expected):
    port, attempt, requests = _port(tmp_path, monkeypatch, [
        socket.timeout("private transport detail"),
        _response("READY.", {"total_tokens": 2}),
        _response("final", usage),
    ])
    try:
        port.request_once(attempt)
    finally:
        port.close()
    summary = usage_summary(port.observations())
    assert len(requests) == 3
    started = _started(tmp_path)
    assert [row["call_kind"] for row in started] == [
        "real_model_call", "health_probe", "real_model_call"]
    assert summary["input_port_invocations"] == 1
    assert summary["physical_attempts"] == 1  # Deprecated legacy alias only.
    assert summary["total_tokens"] == expected
    assert summary["provider_physical_calls"] is None
    assert summary["provider_physical_total_tokens"] is None
    assert summary["provider_physical_cost_complete"] is False
    assert summary["usage_scope"] == "visible_final_responses_only"


def test_unrecovered_adapter_keeps_one_unknown_invocation(tmp_path, monkeypatch):
    port, attempt, requests = _port(tmp_path, monkeypatch, [
        socket.timeout("private transport detail"),
        (503, b'{"error":"offline"}'),
    ])
    try:
        with pytest.raises(LLMInputPortFailure):
            port.request_once(attempt)
    finally:
        port.close()
    summary = usage_summary(port.observations())
    assert len(requests) == len(_started(tmp_path)) == 2
    assert summary["input_port_invocations"] == 1
    assert summary["total_tokens"] is None
    assert summary["provider_physical_cost_complete"] is False
