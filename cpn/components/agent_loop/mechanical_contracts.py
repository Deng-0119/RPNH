"""Dependency-leaf contracts shared by AgentLoop lifecycle mechanics."""
from __future__ import annotations


class AgentLoopMechanicalLifecycleError(RuntimeError):
    """A prepared adapter command violates the shared lifecycle contract."""


_RETRYABLE_LLM_FAILURE_DISPOSITIONS = frozenset({
    "no_input", "protocol_rejected", "provider_retryable",
})
_LLM_FAILURE_DISPOSITIONS = frozenset({
    *_RETRYABLE_LLM_FAILURE_DISPOSITIONS,
    "provider_failure", "submission_unknown", "adapter_not_submitted",
})
_LLM_FAILURES_REQUIRING_TRANSPORT_DETAIL = frozenset({
    "provider_retryable", "provider_failure", "submission_unknown",
    "adapter_not_submitted",
})
_LLM_SUBMISSION_STATES = frozenset({
    "not_submitted", "response_observed", "submission_unknown",
})
_OWNER_INTERRUPTION_SUBMISSION_STATES = frozenset({
    "not_submitted",
    "request_write_started_completion_unknown",
    "request_write_completed_no_response_headers",
    "response_headers_received_body_incomplete",
    "response_complete",
    "submission_unknown",
})
