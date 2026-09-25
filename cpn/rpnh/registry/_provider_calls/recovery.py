"""Interrupted current-v2 provider-attempt recovery without retransmission."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from ..identities import TypedId
from ..models import EventEnvelope, VersionRef
from ..provider_calls import (
    LLMCallV2,
    ProviderAdmissionError,
    ProviderAttemptV2,
    _ref_payload,
)
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
def reconcile_interrupted_attempts_on_resume(
        self, *,
        publish_permitted_attempt_diagnostic: Callable[
            [ProviderAttemptV2, EventEnvelope], ResourceVersionRef],
) -> tuple[str, ...]:
    """Close transport crash windows without retrying or guessing submission."""
    if not callable(publish_permitted_attempt_diagnostic):
        raise TypeError(
            "provider recovery requires one diagnostic publisher")
    events = self.service.event_store.list_events()
    latest: dict[str, EventEnvelope] = {}
    for event in events:
        if event.event_type.startswith("provider_attempt_"):
            latest[event.aggregate_id] = event
    reconciled: list[str] = []
    for attempt_id, state in sorted(latest.items()):
        if state.event_type not in {
                "provider_attempt_reserved/v1",
                "provider_attempt_dispatch_started/v2",
                "provider_attempt_submission_permitted/v2"}:
            continue
        rows = [row for row in self.service.event_store.object_rows()
                if row["logical_id"] == attempt_id
                and row["object_type"] == "provider_attempt_spec/v1"]
        if len(rows) != 1:
            raise ProviderAdmissionError(
                f"interrupted provider attempt has no exact spec: {attempt_id}")
        metadata = dict(self.service.get_version(
            TypedId.parse(rows[0]["version_id"],
                          expected="provider_attempt_version")).metadata)
        attempt_ref = VersionRef(
            "provider_attempt_spec/v1",
            TypedId.parse(attempt_id, expected="provider_attempt"),
            TypedId.parse(str(metadata["provider_attempt_version_id"]),
                          expected="provider_attempt_version"))
        key = f"provider-resume-reconcile:{attempt_id}:{state.event_type}"
        interrupted_attempt = self._hydrate_recovery_attempt(
            attempt_ref, metadata)
        if state.event_type == "provider_attempt_submission_permitted/v2":
            diagnostic_ref = publish_permitted_attempt_diagnostic(
                interrupted_attempt, state)
            if not isinstance(diagnostic_ref, ResourceVersionRef):
                raise ProviderAdmissionError(
                    "provider recovery diagnostic publisher returned no exact ref")
            prepared = self.service.get_version(
                diagnostic_ref.resource_version_id)
            diagnostic_payload = self.service.object_store.read_verified(
                prepared)
            try:
                diagnostic = json.loads(
                    diagnostic_payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderAdmissionError(
                    "provider recovery diagnostic is not JSON") from exc
            self.service.catalog.validate_schema_ref(
                "registry_v1/provider_submission_unknown_diagnostic/v1",
                diagnostic)
            if (prepared.object_type != "resource_version/v1"
                    or prepared.logical_id != diagnostic_ref.resource_id
                    or prepared.version_id
                    != diagnostic_ref.resource_version_id
                    or prepared.metadata.get("content_schema_ref")
                    != "registry_v1/provider_submission_unknown_diagnostic/v1"
                    or canonical_json(diagnostic) != diagnostic_payload
                    or diagnostic.get("provider_attempt_ref")
                    != _ref_payload(interrupted_attempt.ref)
                    or diagnostic.get("llm_call_ref")
                    != _ref_payload(interrupted_attempt.call.ref)
                    or diagnostic.get("dispatch_event_id")
                    != str(state.payload["dispatch_event_id"])
                    or diagnostic.get("submission_permitted_event_id")
                    != str(state.event_id)):
                raise ProviderAdmissionError(
                    "provider recovery diagnostic differs from its permit")
            self.submission_unknown(
                interrupted_attempt,
                dispatch_event_id=TypedId.parse(
                    str(state.payload["dispatch_event_id"]),
                    expected="event"),
                diagnostic_resource_ref=diagnostic_ref,
                require_current_writer=False,
                idempotency_key=key,
            )
            reconciled.append(attempt_id)
            continue
        if state.event_type == "provider_attempt_reserved/v1":
            self.close_before_dispatch(
                interrupted_attempt,
                closed_reason="pre_dispatch_runtime_unavailable",
                require_current_writer=False,
                idempotency_key=key)
        elif state.event_type == "provider_attempt_dispatch_started/v2":
            self.submission_not_permitted(
                interrupted_attempt,
                dispatch_event_id=state.event_id,
                closed_reason="submission_permit_commit_failed",
                idempotency_key=key)
        else:
            raise ProviderAdmissionError(
                "interrupted provider state is outside recovery closure")
        reconciled.append(attempt_id)
    return tuple(reconciled)

def _hydrate_recovery_attempt(
        self, attempt_ref: VersionRef, metadata: Mapping[str, Any],
) -> ProviderAttemptV2:
    """Rebuild the exact typed call solely to close an interrupted attempt."""
    call_ref_raw = metadata["llm_call_ref"]
    call_ref = VersionRef(
        str(call_ref_raw["entity_type"]),
        TypedId.parse(str(call_ref_raw["logical_id"])),
        TypedId.parse(str(call_ref_raw["version_id"])))
    call_metadata = dict(self.service.get_version(
        call_ref.version_id).metadata)

    def version_ref(name: str) -> VersionRef:
        raw = call_metadata[name]
        return VersionRef(
            str(raw["entity_type"]),
            TypedId.parse(str(raw["logical_id"])),
            TypedId.parse(str(raw["version_id"])))

    def resource_ref(name: str) -> ResourceVersionRef:
        raw = call_metadata[name]
        return ResourceVersionRef(
            TypedId.parse(str(raw["resource_id"]), expected="resource"),
            TypedId.parse(
                str(raw["resource_version_id"]),
                expected="resource_version"))

    if call_ref.entity_type != "llm_call_spec/v2":
        raise ProviderAdmissionError(
            "current interrupted provider attempt must name llm_call_spec/v2")
    call = LLMCallV2(
        call_id=call_ref.entity_id,
        version_id=call_ref.version_id,
        invocation_ref=version_ref("invocation_ref"),
        operation_binding_ref=version_ref("operation_binding_ref"),
        loop_ref=dict(call_metadata["agent_loop_ref"]),
        turn_sequence=int(call_metadata["turn_sequence"]),
        request_resource_ref=resource_ref("request_resource_ref"),
        terminal_delivery_ref=version_ref("terminal_delivery_ref"),
        semantic_prompt_resource_ref=resource_ref(
            "semantic_prompt_resource_ref"),
        llm_execution_target_ref=resource_ref("llm_execution_target_ref"),
        backend=str(call_metadata["backend"]),
        model=str(call_metadata["model"]),
        transport_contract_ref=resource_ref(
            "transport_contract_ref"),
        interaction_protocol_ref=str(
            call_metadata["interaction_protocol_ref"]),
        response_adapter_ref=str(
            call_metadata["response_adapter_ref"]),
        tool_catalog_ref=resource_ref("tool_catalog_ref"),
        prior_turn_refs=tuple(
            VersionRef(
                str(value["entity_type"]),
                TypedId.parse(str(value["logical_id"])),
                TypedId.parse(str(value["version_id"])))
            for value in call_metadata["prior_turn_refs"]),
        timeout_seconds=int(call_metadata["timeout_seconds"]),
        max_response_bytes=int(call_metadata["max_response_bytes"]),
    )
    return ProviderAttemptV2(
        attempt_ref.entity_id, attempt_ref.version_id, call,
        str(metadata["reservation_class"]),
        (str(metadata["finalization_scope"])
         if metadata["finalization_scope"] is not None else None))
