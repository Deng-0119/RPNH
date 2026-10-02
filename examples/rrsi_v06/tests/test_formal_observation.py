from __future__ import annotations

import json

import pytest

from cpn.components.registered_host_llm import RegisteredHostLLMCallAttempt
from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputResponseBytes
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json

from rpnh_rrsi.formal_observation import ObservedInputPort, usage_summary


def _ref(entity_type: str, kind: str, value: str) -> VersionRef:
    del value
    return VersionRef(
        entity_type,
        TypedId(kind, "1" * 32),
        TypedId(kind + "_version", "2" * 32),
    )


def _attempt() -> RegisteredHostLLMCallAttempt:
    return RegisteredHostLLMCallAttempt(
        invocation_ref=_ref("llm_call_spec/v3", "llm_call", "call"),
        attempt_ref=_ref("registered_host_llm_attempt/v1",
                         "registered_host_llm_attempt", "host-attempt"),
        provider_attempt_ref=_ref("provider_attempt_spec/v1",
                                  "provider_attempt", "provider-attempt"),
        attempt_ordinal=0,
        model_condition="test-model",
        canonical_request_bytes=canonical_json({
            "protocol": "llm_request_envelope/v1", "messages": [],
        }),
        max_response_bytes=4096,
    )


class _SuccessPort:
    execution_policy = {"adapter_kind": "test"}

    def request_once(self, _attempt):
        return LLMInputResponseBytes(canonical_json({
            "protocol": "llm_response_envelope/v1", "text": "ok",
            "tool_calls": [], "finish_reason": "stop",
            "usage": {"input_tokens": 3, "output_tokens": 2,
                      "total_tokens": 5},
        }), status_code=None, external_request_id="request-1")

    def close(self):
        pass


class _FailurePort(_SuccessPort):
    def request_once(self, _attempt):
        raise LLMInputPortFailure(
            "submission_unknown", submission_state="submission_unknown",
            failure_code="connection_lost")


class _SplitUsagePort(_SuccessPort):
    def request_once(self, _attempt):
        return LLMInputResponseBytes(canonical_json({
            "protocol": "llm_response_envelope/v1", "text": "ok",
            "tool_calls": [], "finish_reason": "stop",
            "usage": {"input_tokens": 8, "output_tokens": 3},
        }), status_code=None, external_request_id=None)


class _InvalidTotalPort(_SuccessPort):
    def __init__(self, total):
        self.total = total

    def request_once(self, _attempt):
        return LLMInputResponseBytes(canonical_json({
            "protocol": "llm_response_envelope/v1", "text": "ok",
            "tool_calls": [], "finish_reason": "stop",
            "usage": {"total_tokens": self.total},
        }), status_code=None, external_request_id=None)


def test_observation_uses_exact_public_attempt_and_response() -> None:
    port = ObservedInputPort(_SuccessPort())
    response = port.request_once(_attempt())
    assert json.loads(response)["text"] == "ok"
    rows = port.observations()
    assert rows[0]["provider_attempt_ref"]["logical_id"] == (
        "provider_attempt:" + "1" * 32)
    assert rows[0]["submission_state"] == "response_observed"
    assert usage_summary(rows) == {
        "physical_attempts": 1,
        "response_observed_attempts": 1,
        "known_usage_attempts": 1,
        "unknown_usage_attempts": 0,
        "total_tokens": 5,
        "known_total_tokens_lower_bound": 5,
    }


def test_submission_unknown_is_not_counted_as_zero_usage() -> None:
    port = ObservedInputPort(_FailurePort())
    try:
        port.request_once(_attempt())
    except LLMInputPortFailure:
        pass
    rows = port.observations()
    assert rows[0]["submission_state"] == "submission_unknown"
    assert usage_summary(rows)["total_tokens"] is None
    assert usage_summary(rows)["physical_attempts"] == 1


def test_usage_derives_public_total_from_input_and_output_counts() -> None:
    port = ObservedInputPort(_SplitUsagePort())
    port.request_once(_attempt())

    rows = port.observations()
    assert rows[0]["usage"] == {
        "input_tokens": 8, "output_tokens": 3, "total_tokens": 11,
    }
    assert usage_summary(rows)["total_tokens"] == 11


@pytest.mark.parametrize("invalid_total", [True, -1, "5"])
def test_invalid_total_tokens_are_removed_and_remain_unknown(
        invalid_total) -> None:
    port = ObservedInputPort(_InvalidTotalPort(invalid_total))
    port.request_once(_attempt())

    rows = port.observations()
    assert rows[0]["usage"] == {}
    assert usage_summary(rows)["known_usage_attempts"] == 0
    assert usage_summary(rows)["total_tokens"] is None


@pytest.mark.parametrize("invalid_total", [True, -1])
def test_usage_summary_does_not_treat_invalid_raw_total_as_known(
        invalid_total) -> None:
    summary = usage_summary([{
        "submission_state": "response_observed",
        "usage": {"total_tokens": invalid_total},
    }])
    assert summary["known_usage_attempts"] == 0
    assert summary["total_tokens"] is None
