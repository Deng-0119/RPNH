"""Core-bound provider execution command facade.

The exact reservation and raw-return command bodies live in
``_provider_calls.attempts``.  This object holds only the shared Registry core;
it owns no transport, retry policy, or independent lifecycle state.
"""

from __future__ import annotations

from ._provider_calls import attempts as _attempts
from ._provider_calls.attempts import materialize_attempt_v2_reservation
from .invocations import InvocationContext
from .provider_calls import (
    LLMCallV2, LLMCallV3, ProviderAttemptV2, ProviderAttemptV3,
)


class ProviderExecution:
    """Dispatch provider ledger commands against one existing Registry core."""

    def __init__(self, core):
        self.__core = core

    def reserve_provider_attempt_v2(
            self, *, context: InvocationContext, call: LLMCallV2,
            prior_attempt: ProviderAttemptV2 | None,
            idempotency_key: str) -> ProviderAttemptV2:
        return _attempts.reserve_provider_attempt_v2(
            self, context=context, call=call,
            prior_attempt=prior_attempt, idempotency_key=idempotency_key)

    def commit_provider_raw_response_v2(
            self, *, attempt, payload, status_code,
            external_request_id, idempotency_key):
        return _attempts.commit_provider_raw_response_v2(
            self, attempt=attempt, payload=payload,
            status_code=status_code, external_request_id=external_request_id,
            idempotency_key=idempotency_key)

    def reserve_provider_attempt_v3(
            self, *, context: InvocationContext, call: LLMCallV3,
            prior_attempt: None, idempotency_key: str) -> ProviderAttemptV3:
        if prior_attempt is not None:
            raise TypeError("registered-HOST calls do not retry")
        return _attempts.reserve_provider_attempt_v3(
            self, context=context, call=call,
            prior_attempt=None, idempotency_key=idempotency_key)

    def commit_provider_raw_response_v3(
            self, *, attempt, payload, status_code,
            external_request_id, idempotency_key):
        return _attempts.commit_provider_raw_response_v3(
            self, attempt=attempt, payload=payload,
            status_code=status_code, external_request_id=external_request_id,
            idempotency_key=idempotency_key)
