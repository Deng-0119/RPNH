"""Source-neutral contracts for the framework's external-input boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cpn.rpnh.registry.models import VersionRef


@dataclass(frozen=True, slots=True)
class LLMCallAttempt:
    """One generic framework attempt, with no transport or route semantics."""

    invocation_ref: VersionRef
    attempt_ref: VersionRef
    attempt_ordinal: int
    model_condition: str
    canonical_request_bytes: bytes
    max_response_bytes: int

    def __post_init__(self) -> None:
        # Import only when an attempt is constructed.  Importing a Registry
        # submodule while the neutral LLM package itself is being imported
        # would otherwise execute registry_v1.__init__ and form a cycle back
        # through the launch contract.
        from cpn.rpnh.registry.models import VersionRef as RegistryVersionRef

        if (not isinstance(self.invocation_ref, RegistryVersionRef)
                or self.invocation_ref.entity_type != "llm_invocation_spec/v1"
                or not isinstance(self.attempt_ref, RegistryVersionRef)
                or self.attempt_ref.entity_type
                != "llm_invocation_attempt/v1"
                or isinstance(self.attempt_ordinal, bool)
                or not isinstance(self.attempt_ordinal, int)
                or self.attempt_ordinal < 0
                or not isinstance(self.model_condition, str)
                or not self.model_condition.strip()
                or self.model_condition != self.model_condition.strip()
                or not isinstance(self.canonical_request_bytes, bytes)
                or not self.canonical_request_bytes
                or isinstance(self.max_response_bytes, bool)
                or not isinstance(self.max_response_bytes, int)
                or self.max_response_bytes < 1):
            raise TypeError("LLM call attempt authority is invalid")


class LLMInputResponseBytes(bytes):
    """Canonical response bytes with exact transport observation metadata.

    The bytes surface remains compatible with response-envelope parsers while
    the Registry boundary can record the HTTP status and provider request ID
    that accompanied those exact bytes. Subprocess transports use ``None``
    for both fields.
    """

    status_code: int | None
    external_request_id: str | None

    def __new__(
            cls, payload: bytes, *, status_code: int | None,
            external_request_id: str | None,
    ) -> "LLMInputResponseBytes":
        if (not isinstance(payload, bytes)
                or (status_code is not None and (
                    isinstance(status_code, bool)
                    or not isinstance(status_code, int)
                    or not 100 <= status_code <= 599))
                or (external_request_id is not None and (
                    not isinstance(external_request_id, str)
                    or not external_request_id
                    or len(external_request_id) > 1024
                    or "\r" in external_request_id
                    or "\n" in external_request_id))):
            raise TypeError("LLM input response metadata is invalid")
        value = super().__new__(cls, payload)
        value.status_code = status_code
        value.external_request_id = external_request_id
        return value


@runtime_checkable
class LLMInputPort(Protocol):
    """The complete framework-visible LLM input surface."""

    def request_once(self, attempt: LLMCallAttempt) -> LLMInputResponseBytes: ...

    def close(self) -> None: ...


class LLMInputPortFailure(RuntimeError):
    """A classified provider/adapter failure at the neutral input boundary.

    The adapter owns transport classification.  AgentLoop owns only the
    existing Registry retry accounting and execution-block handoff.  A
    failure is therefore explicit instead of being collapsed into ``None``.
    """

    _DISPOSITIONS = frozenset({
        "provider_retryable",
        "provider_failure",
        "submission_unknown",
        "adapter_not_submitted",
        "protocol_rejected",
    })
    _SUBMISSION_STATES = frozenset({
        "not_submitted",
        "response_observed",
        "submission_unknown",
    })

    def __init__(
            self, disposition: str, *, submission_state: str,
            failure_code: str,
    ) -> None:
        if (not isinstance(disposition, str)
                or disposition not in self._DISPOSITIONS
                or not isinstance(submission_state, str)
                or submission_state not in self._SUBMISSION_STATES
                or (disposition == "submission_unknown"
                    and submission_state != "submission_unknown")
                or not isinstance(failure_code, str)
                or not failure_code or failure_code != failure_code.strip()):
            raise TypeError("LLM input failure authority is invalid")
        super().__init__(failure_code)
        self.disposition = disposition
        self.submission_state = submission_state
        self.failure_code = failure_code


class LLMInputPortInterrupted(RuntimeError):
    """Owner stop cancelled one in-flight physical input operation.

    This is control flow, not a provider failure or a successful model return.
    Registry records the immutable submission state and closes the interrupted
    attempt before a later explicit continuation reserves a new identity.
    """

    _SUBMISSION_STATES = frozenset({
        "not_submitted",
        "request_write_started_completion_unknown",
        "request_write_completed_no_response_headers",
        "response_headers_received_body_incomplete",
        "response_complete",
        "submission_unknown",
    })

    def __init__(self, *, submission_state: str) -> None:
        if (not isinstance(submission_state, str)
                or submission_state not in self._SUBMISSION_STATES):
            raise TypeError("LLM input interruption state is invalid")
        super().__init__("owner_interrupted")
        self.submission_state = submission_state


@dataclass(frozen=True, slots=True)
class LLMInputTarget:
    """Source-neutral constraints registered for one LLM input condition."""

    model_condition: str
    max_output_tokens: int
    max_response_bytes: int
    context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.model_condition, str)
                or not self.model_condition.strip()
                or self.model_condition != self.model_condition.strip()):
            raise TypeError("LLM input target identity is invalid")
        for value, label in (
                (self.max_output_tokens, "max_output_tokens"),
                (self.max_response_bytes, "max_response_bytes")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise TypeError(f"LLM input target {label} must be positive")
        if (self.context_window_tokens is not None
                and (isinstance(self.context_window_tokens, bool)
                     or not isinstance(self.context_window_tokens, int)
                     or self.context_window_tokens < 1)):
            raise TypeError(
                "LLM input target context_window_tokens must be positive")

    def as_registry_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "schema_version": "llm_input_target/v1",
            "model_condition": self.model_condition,
            "max_output_tokens": self.max_output_tokens,
            "max_response_bytes": self.max_response_bytes,
        }
        if self.context_window_tokens is not None:
            document["context_window_tokens"] = self.context_window_tokens
        return document


__all__ = [
    "LLMCallAttempt", "LLMInputPort", "LLMInputPortFailure",
    "LLMInputPortInterrupted",
    "LLMInputResponseBytes",
    "LLMInputTarget",
]
