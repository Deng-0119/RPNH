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
        "input_port_invocations": 1,
        "physical_attempts": 1,
        "physical_attempts_semantics": "deprecated_alias_of_input_port_invocations",
        "usage_scope": "visible_final_responses_only",
        "provider_physical_calls": None,
        "provider_physical_total_tokens": None,
        "provider_physical_cost_complete": False,
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


@pytest.mark.parametrize("error", [KeyboardInterrupt("private detail"),
                                   SystemExit("private detail"),
                                   TimeoutError("private detail")])
def test_observation_retains_aborted_invocation_without_exception_payload(error):
    class Port(_SuccessPort):
        def request_once(self, _attempt):
            raise error

    port = ObservedInputPort(Port())
    with pytest.raises(type(error)) as caught:
        port.request_once(_attempt())
    assert caught.value is error
    rows = port.observations()
    assert len(rows) == 1
    assert rows[0]["submission_state"] == "unknown"
    assert rows[0]["error_type"] == type(error).__name__
    assert "private detail" not in json.dumps(rows)


def test_malformed_response_is_observed_and_classified_without_retry():
    class Port(_SuccessPort):
        def request_once(self, _attempt):
            return b"private non-JSON body"

    port = ObservedInputPort(Port())
    with pytest.raises(ValueError):
        port.request_once(_attempt())
    row, = port.observations()
    assert row["submission_state"] == "response_observed"
    assert row["outcome"] == "unclassified_exception"
    assert row["canonical_response"] is None
    assert "private non-JSON" not in json.dumps(row)


def test_interruptible_method_receives_exact_callback_and_preserves_typed_stop():
    from cpn.rpnh.llm_contracts import LLMInputPortInterrupted
    callback = lambda: True
    error = LLMInputPortInterrupted(submission_state="submission_unknown")

    class Port(_SuccessPort):
        def request_once(self, _attempt):
            raise AssertionError("ordinary request must not be called")

        def request_once_interruptible(self, attempt, *, interruption_requested):
            assert interruption_requested is callback
            raise error

    port = ObservedInputPort(Port())
    with pytest.raises(LLMInputPortInterrupted) as caught:
        port.request_once_interruptible(_attempt(), interruption_requested=callback)
    assert caught.value is error
    row, = port.observations()
    assert row["outcome"] == "owner_interrupted"
    assert row["submission_state"] == "submission_unknown"


def test_pre_stopped_legacy_port_does_not_dispatch():
    from cpn.rpnh.llm_contracts import LLMInputPortInterrupted

    class Port(_SuccessPort):
        def request_once(self, _attempt):
            raise AssertionError("legacy request must not be called")

    port = ObservedInputPort(Port())
    with pytest.raises(LLMInputPortInterrupted):
        port.request_once_interruptible(_attempt(), interruption_requested=lambda: True)
    assert port.observations()[0]["submission_state"] == "not_submitted"


@pytest.mark.parametrize("code, expected", [
    ("connection_lost", "connection_lost"),
    ("response_headers_timeout", "response_headers_timeout"),
    ("secret=provider-private-token", "input_port_failure"),
])
def test_only_bounded_public_failure_codes_are_exported(code, expected):
    class Port(_SuccessPort):
        def request_once(self, _attempt):
            raise LLMInputPortFailure("submission_unknown",
                                     submission_state="submission_unknown",
                                     failure_code=code)

    port = ObservedInputPort(Port())
    with pytest.raises(LLMInputPortFailure):
        port.request_once(_attempt())
    assert port.observations()[0]["failure_code"] == expected
