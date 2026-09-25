"""Current-v2 provider-attempt ledger transitions and execution commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ...executable_net import load_compiled_net
from ..content_schemas import hydrate_registered_content_schema
from ..errors import ResourceIntegrityFault, StaleAuthorityHead
from ..identities import TypedId
from ..invocations import InvocationContext, InvocationLifecycle, _stable_id
from ..models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from ..provider_calls import (
    LLMCallV2, LLMCallV3,
    ProviderAdmissionError,
    ProviderAttemptUnknown,
    ProviderAttemptV2, ProviderAttemptV3,
    _resource_payload,
)
from ..publication import (
    _NO_CONTENT_SCHEMA_INSTANCE,
    _append_direct_resource_version_publication,
    _content_schema_instance,
    _content_schema_source_from_payload,
    _direct_resource_metadata,
    _ref_payload,
    _resource_from_payload,
    _stable_id as _publication_id,
    _version_from_payload,
)
from ..resource_service import _ResourceServiceKernel
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
RefPayload = Callable[[VersionRef], dict[str, str]]
ResourcePayload = Callable[[ResourceVersionRef], dict[str, str]]


@dataclass(frozen=True, slots=True)
class AttemptV2ReservationPublication:
    """Transaction-free material for one reserved v2 provider attempt."""

    attempt_ref: VersionRef
    metadata: Mapping[str, Any]
    event_payload: Mapping[str, Any]


def materialize_attempt_v2_reservation(
        *, attempt_id: TypedId, version_id: TypedId,
        call_ref: VersionRef, invocation_ref: VersionRef,
        operation_binding_ref: VersionRef,
        llm_execution_target_ref: ResourceVersionRef,
        request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        budget_witness_ref: VersionRef,
        prior_attempt_ref: VersionRef | None,
        activation_ref: VersionRef | None,
        origin: str,
        accounting_parent_invocation_ref: VersionRef,
        backend: str, model: str,
        transport_kind: str, response_protocol: str,
        timeout_seconds: int,
        prior_failure_class: str | None,
        reservation_class: str,
        finalization_scope: str | None,
        writer_fencing_epoch: int,
        ref_payload: RefPayload,
        resource_payload: ResourcePayload,
) -> AttemptV2ReservationPublication:
    """Build exact object/event payloads from already-resolved authority."""
    attempt_ref = VersionRef(
        "provider_attempt_spec/v1", attempt_id, version_id)
    metadata = {
        "provider_attempt_id": str(attempt_id),
        "provider_attempt_version_id": str(version_id),
        "provider_attempt_ref": ref_payload(attempt_ref),
        "llm_call_ref": ref_payload(call_ref),
        "invocation_ref": ref_payload(invocation_ref),
        "operation_binding_ref": ref_payload(operation_binding_ref),
        "llm_execution_target_ref": resource_payload(
            llm_execution_target_ref),
        "request_resource_ref": resource_payload(request_resource_ref),
        "terminal_delivery_ref": ref_payload(terminal_delivery_ref),
        "budget_witness_ref": ref_payload(budget_witness_ref),
        "prior_attempt_ref": (
            ref_payload(prior_attempt_ref)
            if prior_attempt_ref is not None else None),
        "llm_call_id": str(call_ref.entity_id),
        "llm_call_version_id": str(call_ref.version_id),
        "invocation_id": str(invocation_ref.entity_id),
        "invocation_version_id": str(invocation_ref.version_id),
        "activation_id": (
            str(activation_ref.entity_id)
            if activation_ref is not None else None),
        "origin": origin,
        "accounting_parent_invocation_id": str(
            accounting_parent_invocation_ref.entity_id),
        "accounting_parent_invocation_version_id": str(
            accounting_parent_invocation_ref.version_id),
        "backend_config_version_id": str(
            llm_execution_target_ref.resource_version_id),
        "budget_witness_version_id": str(budget_witness_ref.version_id),
        "resource_contract_version_id": None,
        "backend": backend,
        "model": model,
        "transport_kind": transport_kind,
        "response_protocol": response_protocol,
        "timeout_seconds": timeout_seconds,
        "retry_cause": prior_failure_class,
        "prior_attempt_version_id": (
            str(prior_attempt_ref.version_id)
            if prior_attempt_ref is not None else None),
        "reservation_class": reservation_class,
        "finalization_scope": finalization_scope,
        "writer_fencing_epoch": writer_fencing_epoch,
    }
    event_payload = {
        "provider_attempt_id": str(attempt_id),
        "provider_attempt_version_id": str(version_id),
        "provider_attempt_ref": ref_payload(attempt_ref),
        "llm_call_ref": ref_payload(call_ref),
        "invocation_ref": ref_payload(invocation_ref),
        "operation_binding_ref": ref_payload(operation_binding_ref),
        "llm_execution_target_ref": resource_payload(
            llm_execution_target_ref),
        "request_resource_ref": resource_payload(request_resource_ref),
        "terminal_delivery_ref": ref_payload(terminal_delivery_ref),
        "budget_witness_ref": ref_payload(budget_witness_ref),
        "prior_attempt_ref": (
            ref_payload(prior_attempt_ref)
            if prior_attempt_ref is not None else None),
        "reservation_class": reservation_class,
        "finalization_scope": finalization_scope is not None,
    }
    return AttemptV2ReservationPublication(
        attempt_ref, metadata, event_payload)

def reserve_v2(
        self, *, context: InvocationContext, call: LLMCallV2,
        prior_attempt: ProviderAttemptV2 | None,
        idempotency_key: str) -> ProviderAttemptV2:
    """Reserve one exact v2 attempt through the shared Registry owner."""
    if not isinstance(call, LLMCallV2):
        raise TypeError("v2 reservation requires LLMCallV2")
    hydrated = self._revalidate_call_v2_context(
        call, boundary="agent-provider-attempt-reservation")
    if hydrated != context:
        raise ProviderAdmissionError(
            "v2 reservation context differs from exact call")
    if (prior_attempt is not None
            and (not isinstance(prior_attempt, ProviderAttemptV2)
                 or prior_attempt.call.call_id != call.call_id)):
        raise ProviderAdmissionError(
            "v2 retry predecessor belongs to another logical call")
    attempt = self.service.reserve_provider_attempt_v2(
        context=context, call=call, prior_attempt=prior_attempt,
        idempotency_key=idempotency_key)
    if (not isinstance(attempt, ProviderAttemptV2)
            or attempt.call != call):
        raise ProviderAdmissionError(
            "Registry returned a different v2 provider attempt")
    return attempt


def reserve_v3(
        self, *, context: InvocationContext, call: LLMCallV3,
        idempotency_key: str) -> ProviderAttemptV3:
    """Reserve exactly one provider attempt for a registered-HOST call."""
    if not isinstance(call, LLMCallV3):
        raise TypeError("v3 reservation requires LLMCallV3")
    hydrated = self._revalidate_call_v3_context(
        call, boundary="registered-host-provider-attempt-reservation")
    if hydrated != context:
        raise ProviderAdmissionError(
            "v3 reservation context differs from exact call")
    attempt = self.service.reserve_provider_attempt_v3(
        context=context, call=call, prior_attempt=None,
        idempotency_key=idempotency_key)
    if (not isinstance(attempt, ProviderAttemptV3)
            or attempt.call != call):
        raise ProviderAdmissionError(
            "Registry returned a different v3 provider attempt")
    return attempt

def complete_v2(
        self, attempt: ProviderAttemptV2, *, response: bytes,
        status_code: int | None, external_request_id: str | None,
        idempotency_key: str):
    """Publish returned bytes; Core closes status against the registered route.

    None represents no HTTP status on a subprocess return, not HTTP 200.
    Syntax observation is deliberately later.
    """
    if not isinstance(attempt, ProviderAttemptV2):
        raise TypeError("v2 completion requires ProviderAttemptV2")
    self._revalidate_call_v2_context(
        attempt.call, boundary="agent-provider-response-publication")
    if (not isinstance(response, bytes)
            or len(response) > attempt.call.max_response_bytes
            or (status_code is not None and (
                isinstance(status_code, bool)
                or not isinstance(status_code, int)
                or status_code < 100 or status_code > 599))):
        raise ProviderAdmissionError(
            "v2 completion requires raw bytes and an HTTP status or None")
    publication = self.service.commit_provider_raw_response_v2(
        attempt=attempt, payload=response, status_code=status_code,
        external_request_id=external_request_id,
        idempotency_key=idempotency_key)
    from ...response_protocol import PublishedRawLLMResponse
    if (not isinstance(publication, PublishedRawLLMResponse)
            or publication.provider_attempt_ref != attempt.ref
            or publication.llm_call_ref != attempt.call.ref
            or publication.backend != attempt.call.backend
            or publication.model != attempt.call.model
            or publication.interaction_protocol_ref
            != attempt.call.interaction_protocol_ref
            or publication.response_adapter_ref
            != attempt.call.response_adapter_ref
            or publication.size != len(response)):
        raise ProviderAdmissionError(
            "Registry raw v2 publication differs from response bytes")
    return publication


def complete_v3(
        self, attempt: ProviderAttemptV3, *, response: bytes,
        status_code: int | None, external_request_id: str | None,
        idempotency_key: str):
    if not isinstance(attempt, ProviderAttemptV3):
        raise TypeError("v3 completion requires ProviderAttemptV3")
    self._revalidate_call_v3_context(
        attempt.call, boundary="registered-host-provider-response-publication")
    if (not isinstance(response, bytes)
            or len(response) > attempt.call.max_response_bytes
            or (status_code is not None and (
                isinstance(status_code, bool)
                or not isinstance(status_code, int)
                or not 100 <= status_code <= 599))):
        raise ProviderAdmissionError(
            "v3 completion requires bounded raw bytes and status or None")
    publication = self.service.commit_provider_raw_response_v3(
        attempt=attempt, payload=response, status_code=status_code,
        external_request_id=external_request_id,
        idempotency_key=idempotency_key)
    from ..provider_calls import RegisteredHostTransportResponse
    if (not isinstance(publication, RegisteredHostTransportResponse)
            or publication.provider_attempt_ref != attempt.ref
            or publication.llm_call_ref != attempt.call.ref
            or publication.size != len(response)):
        raise ProviderAdmissionError(
            "Registry raw v3 publication differs from response bytes")
    return publication

def record_materialization(
        self, *, context: InvocationContext,
        attempt: ProviderAttemptV2 | ProviderAttemptV3,
        request_payload: bytes, idempotency_key: str):
    """Record exact request lineage for a registered HOST wire adapter.

    Wire bytes remain process-local. This publishes neither a response nor
    settlement; dispatch/permit and raw-return authority stay separate.
    """
    from ..resource_service import _ResourceServiceKernel
    from ..resources import (
        ProviderPayloadMaterializationReceipt, ProviderRequestMaterialization,
    )
    if not isinstance(attempt, (ProviderAttemptV2, ProviderAttemptV3)):
        raise TypeError("materialization requires a registered attempt")
    hydrated = (
        self._revalidate_call_v3_context(
            attempt.call, boundary="provider-request-materialization")
        if isinstance(attempt, ProviderAttemptV3) else
        self._revalidate_call_v2_context(
            attempt.call, boundary="provider-request-materialization"))
    if (hydrated != context or not isinstance(request_payload, bytes)
            or not request_payload):
        raise ProviderAdmissionError("materialization requires exact context and positive bytes")
    kernel = _ResourceServiceKernel(self.service)
    exact_attempt = kernel._exact_object(attempt.ref, expected_type="provider_attempt_spec/v1")
    if (exact_attempt.metadata["llm_call_ref"] != _ref_payload(attempt.call.ref)
            or exact_attempt.metadata["invocation_ref"] != _ref_payload(context.invocation_ref)):
        raise ProviderAdmissionError("materialization differs from the registered attempt")
    receipt_ref = VersionRef("provider_payload_materialization_receipt/v1",
        _stable_id("provider_payload_materialization_receipt", f"{attempt.version_id}:{idempotency_key}"),
        _stable_id("provider_payload_materialization_receipt_version", f"{attempt.version_id}:{idempotency_key}"))
    document = {
        "provider_payload_materialization_receipt_ref": _ref_payload(receipt_ref),
        "provider_attempt_ref": _ref_payload(attempt.ref),
        "llm_call_ref": _ref_payload(attempt.call.ref),
        "request_recipe_ref": _resource_payload(attempt.call.request_resource_ref),
        "terminal_delivery_ref": _ref_payload(attempt.call.terminal_delivery_ref),
        "byte_count": len(request_payload),
    }
    if self.service.event_store.object_row(receipt_ref.version_id) is None:
        tx = self.service.begin(idempotency_key=idempotency_key)
        tx.prewrite(object_type=receipt_ref.entity_type, logical_id=receipt_ref.entity_id,
            version_id=receipt_ref.version_id, payload=canonical_json(document), metadata=document,
            media_type="application/json",
            schema_ref="registry_v1/provider_payload_materialization_receipt/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.append(PendingEvent("provider_payload_materialization_recorded/v1", "authoritative",
            f"provider-attempt:{attempt.attempt_id}", str(attempt.attempt_id), "provider_attempt",
            idempotency_key, idempotency_key, document,
            "registry_v1/provider_payload_materialization_recorded/v1", task_control=True,
            producer_invocation_id=context.invocation_ref.entity_id))
        tx.commit()
    receipt_object = kernel._exact_object(receipt_ref, expected_type=receipt_ref.entity_type)
    recorded = [event for event in self.service.event_store.list_events_by_aggregate(
        str(attempt.attempt_id), event_types=("provider_payload_materialization_recorded/v1",))
        if event.idempotency_key == idempotency_key]
    if (dict(receipt_object.metadata) != document or len(recorded) != 1
            or dict(recorded[0].payload) != document):
        raise ProviderAdmissionError("materialization differs from its durable receipt")
    receipt = ProviderPayloadMaterializationReceipt(receipt_ref, attempt.ref, attempt.call.ref,
        attempt.call.request_resource_ref, attempt.call.terminal_delivery_ref,
        len(request_payload), kernel._head())
    return ProviderRequestMaterialization(receipt, request_payload)

def reconcile_v2(
        self, attempt: ProviderAttemptV2, *, proof_ref: VersionRef,
        idempotency_key: str) -> ProviderAttemptV2:
    """Reconcile the same attempt; this API never creates a blind retry."""
    if (not isinstance(attempt, ProviderAttemptV2)
            or not isinstance(proof_ref, VersionRef)):
        raise TypeError("v2 reconciliation requires exact attempt/proof refs")
    try:
        reconciled = self.service.reconcile_provider_attempt_v2(
            attempt=attempt, proof_ref=proof_ref,
            idempotency_key=idempotency_key)
    except AttributeError as exc:
        raise ProviderAttemptUnknown(
            "shared Registry v2 reconciliation is not integrated") from exc
    if not isinstance(reconciled, ProviderAttemptV2) or reconciled.ref != attempt.ref:
        raise ProviderAdmissionError(
            "v2 reconciliation changed the provider attempt")
    return reconciled

def _revalidate_call_v2_context(
        self, call: LLMCallV2, *, boundary: str,
        require_current_writer: bool = True) -> InvocationContext:
    if not isinstance(call, LLMCallV2):
        raise TypeError("expected LLMCallV2")
    context = InvocationLifecycle(self.service).hydrate_context(
        call.invocation_ref,
        require_current_writer=require_current_writer)
    if context.operation_binding_ref != call.operation_binding_ref:
        raise ProviderAdmissionError(
            "v2 call context differs from canonical invocation")
    if require_current_writer:
        InvocationLifecycle(self.service).revalidate_io(
            context, boundary=boundary)
    row = self.service.event_store.object_row(call.version_id)
    if (row is None or row["object_type"] != "llm_call_spec/v2"
            or row["logical_id"] != str(call.call_id)):
        raise ProviderAdmissionError(
            "v2 provider call is not an exact registered version")
    return context


def _revalidate_call_v3_context(
        self, call: LLMCallV3, *, boundary: str,
        require_current_writer: bool = True) -> InvocationContext:
    if not isinstance(call, LLMCallV3):
        raise TypeError("expected LLMCallV3")
    context = InvocationLifecycle(self.service).hydrate_context(
        call.invocation_ref,
        require_current_writer=require_current_writer)
    if context.operation_binding_ref != call.operation_binding_ref:
        raise ProviderAdmissionError(
            "v3 call context differs from canonical invocation")
    if require_current_writer:
        InvocationLifecycle(self.service).revalidate_io(
            context, boundary=boundary)
    row = self.service.event_store.object_row(call.version_id)
    if (row is None or row["object_type"] != "llm_call_spec/v3"
            or row["logical_id"] != str(call.call_id)):
        raise ProviderAdmissionError(
            "v3 provider call is not an exact registered version")
    return context

def _provider_request_schema(self, context: InvocationContext) -> str:
    """Read the exact executor's registered request contract, not a recipe."""
    from ..publication import _version_from_payload
    binding = self.service.get_version(context.operation_binding_ref.version_id)
    if (binding.object_type != "operation_binding/v1"
            or binding.logical_id != context.operation_binding_ref.entity_id):
        raise ProviderAdmissionError("provider request binding differs from exact Registry ref")
    spec_ref = _version_from_payload(binding.metadata["operation_spec_ref"])
    spec = self.service.get_version(spec_ref.version_id)
    if spec.object_type != "operation_spec/v1" or spec.logical_id != spec_ref.entity_id:
        raise ProviderAdmissionError("provider request spec differs from exact Registry ref")
    schema = spec.metadata["implementation_contracts"].get("provider_request_schema")
    if not isinstance(schema, str) or not schema:
        raise ProviderAdmissionError("executor requires explicit registered provider_request_schema")
    return schema

def _canonical_budget_scope(
        self, context: InvocationContext) -> tuple[str, str | None]:
    lifecycle = InvocationLifecycle(self.service)
    lifecycle.revalidate_io(context, boundary="provider-attempt-reservation")
    from ...budgets import BudgetContractError, validate_budget_binding
    binding = self.service.get_version(context.operation_binding_ref.version_id)
    manifest_ref = self.recovery_manifest_ref()
    manifest = self.service.get_version(manifest_ref.version_id)
    if (binding.object_type != "operation_binding/v1"
            or binding.logical_id != context.operation_binding_ref.entity_id
            or manifest.object_type != manifest_ref.entity_type
            or manifest.logical_id != manifest_ref.entity_id):
        raise ProviderAdmissionError("provider budget refs differ from Registry")
    try:
        bucket = validate_budget_binding(manifest.metadata, binding.metadata)
    except BudgetContractError as exc:
        raise ProviderAdmissionError("provider budget binding is not registered") from exc
    reservation_class = bucket["budget_scope"]
    if (context.budget_scope != reservation_class
            or context.finalization_scope != bucket["finalization_scope"]):
        raise ProviderAdmissionError("provider scope differs from its exact registered bucket")
    if (context.origin != "petri_operation"
            or context.parent_invocation_ref is not None
            or context.accounting_parent_invocation_ref
            != context.invocation_ref):
        raise ProviderAdmissionError(
            "provider accounting requires one exact Petri invocation identity")
    return reservation_class, context.finalization_scope


def _append(self, attempt: ProviderAttemptV2 | ProviderAttemptV3,
            event_type: str,
            payload: Mapping[str, Any], *, idempotency_key: str,
            relations: tuple[TypedRelation, ...] = ()) -> None:
    if not isinstance(attempt, (ProviderAttemptV2, ProviderAttemptV3)):
        raise ProviderAdmissionError(
            "current provider lifecycle requires a registered attempt")
    if isinstance(attempt, ProviderAttemptV3):
        self._revalidate_call_v3_context(
            attempt.call, boundary=event_type.rsplit("/", 1)[0])
    else:
        self._revalidate_call_v2_context(
            attempt.call, boundary=event_type.rsplit("/", 1)[0])
    tx = self.service.begin(idempotency_key=idempotency_key)
    for relation in relations:
        tx.relate(
            relation,
            producer_invocation_id=attempt.call.invocation_id)
    tx.append(PendingEvent(
        event_type=event_type, criticality="authoritative",
        stream_id=f"provider-attempt:{attempt.attempt_id}",
        aggregate_id=str(attempt.attempt_id), aggregate_type="provider_attempt",
        idempotency_key=idempotency_key, command_id=idempotency_key,
        payload={"provider_attempt_id": str(attempt.attempt_id), **dict(payload)},
        payload_schema_ref=f"registry_v1/{event_type}", task_control=True,
        producer_invocation_id=attempt.call.invocation_id))
    tx.commit()

def dispatch_started(
        self, attempt: ProviderAttemptV2 | ProviderAttemptV3, *,
        materialization_receipt_ref: VersionRef,
        request_byte_count: int,
        idempotency_key: str) -> None:
    metadata = self.service.get_version(attempt.version_id).metadata
    if (isinstance(request_byte_count, bool)
            or not isinstance(request_byte_count, int)
            or request_byte_count < 1
            or not isinstance(materialization_receipt_ref, VersionRef)
            or materialization_receipt_ref.entity_type
            != "provider_payload_materialization_receipt/v1"
            or metadata.get("request_resource_ref")
            != _resource_payload(attempt.call.request_resource_ref)
            or metadata.get("terminal_delivery_ref")
            != _ref_payload(attempt.call.terminal_delivery_ref)):
        raise ProviderAdmissionError(
            "provider dispatch differs from exact request authority")
    self._append(
        attempt, "provider_attempt_dispatch_started/v2",
        {"provider_attempt_version_id": str(attempt.version_id),
         "provider_attempt_ref": _ref_payload(attempt.ref),
         "llm_call_ref": metadata["llm_call_ref"],
         "invocation_ref": metadata["invocation_ref"],
         "operation_binding_ref": metadata["operation_binding_ref"],
         "llm_execution_target_ref": metadata["llm_execution_target_ref"],
         "request_resource_ref": metadata["request_resource_ref"],
         "terminal_delivery_ref": metadata["terminal_delivery_ref"],
         "transport_kind": str(metadata["transport_kind"]),
         "response_protocol": str(metadata["response_protocol"]),
         "provider_payload_materialization_receipt_ref": _ref_payload(
             materialization_receipt_ref),
         "request_payload_byte_count": request_byte_count},
        idempotency_key=idempotency_key)

def submission_permitted(
        self, attempt: ProviderAttemptV2 | ProviderAttemptV3, *,
        dispatch_event_id: TypedId,
        operation_execution_lease_ref: VersionRef,
        operation_start_event_id: TypedId,
        lifecycle_observation_event_id: TypedId,
        evidence_receipt_id: str | None,
        idempotency_key: str,
) -> EventEnvelope:
    metadata = self.service.get_version(attempt.version_id).metadata
    dispatches = tuple(
        event for event in self.service.event_store.list_events_by_aggregate(
            str(attempt.attempt_id),
            event_types=("provider_attempt_dispatch_started/v2",))
        if event.event_id == dispatch_event_id)
    if (len(dispatches) != 1
            or not isinstance(lifecycle_observation_event_id, TypedId)
            or lifecycle_observation_event_id != dispatch_event_id
            or (evidence_receipt_id is not None
                and not isinstance(evidence_receipt_id, str))):
        raise ProviderAdmissionError(
            "provider permit requires exact dispatch/observation authority")
    self._append(
        attempt, "provider_attempt_submission_permitted/v2",
        {
            "provider_attempt_version_id": str(attempt.version_id),
            "provider_attempt_ref": _ref_payload(attempt.ref),
            "llm_call_ref": metadata["llm_call_ref"],
            "invocation_ref": metadata["invocation_ref"],
            "operation_execution_lease_ref": _ref_payload(
                operation_execution_lease_ref),
            "operation_start_event_id": str(operation_start_event_id),
            "dispatch_event_id": str(dispatch_event_id),
            "lifecycle_observation_event_id": str(
                lifecycle_observation_event_id),
            "evidence_receipt_id": evidence_receipt_id,
            "request_payload_byte_count": dispatches[0].payload[
                "request_payload_byte_count"],
            "response_protocol": str(metadata["response_protocol"]),
            "state": "submission_permitted",
        },
        idempotency_key=idempotency_key,
    )
    matches = tuple(
        event for event in self.service.event_store.list_events_by_aggregate(
            str(attempt.attempt_id),
            event_types=("provider_attempt_submission_permitted/v2",))
        if event.idempotency_key == idempotency_key)
    if len(matches) != 1:
        raise ProviderAdmissionError(
            "provider permit did not commit one exact lifecycle fact")
    return matches[0]

def submission_not_permitted(
        self, attempt: ProviderAttemptV2, *,
        dispatch_event_id: TypedId,
        closed_reason: str, idempotency_key: str,
) -> tuple[EventEnvelope, EventEnvelope]:
    if closed_reason not in {
            "lifecycle_observer_failed",
            "lifecycle_receipt_invalid",
            "submission_permit_commit_failed"}:
        raise ProviderAdmissionError(
            "provider non-permit reason is outside the closed set")
    metadata = self.service.get_version(attempt.version_id).metadata
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.append(PendingEvent(
        event_type="provider_attempt_submission_not_permitted/v1",
        criticality="authoritative",
        stream_id=f"provider-attempt:{attempt.attempt_id}",
        aggregate_id=str(attempt.attempt_id),
        aggregate_type="provider_attempt",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={
            "provider_attempt_id": str(attempt.attempt_id),
            "provider_attempt_version_id": str(attempt.version_id),
            "provider_attempt_ref": _ref_payload(attempt.ref),
            "llm_call_ref": metadata["llm_call_ref"],
            "invocation_ref": metadata["invocation_ref"],
            "dispatch_event_id": str(dispatch_event_id),
            "closed_reason": closed_reason,
            "state": "submission_not_permitted",
        },
        payload_schema_ref=(
            "registry_v1/provider_attempt_submission_not_permitted/v1"),
        task_control=True,
        producer_invocation_id=attempt.call.invocation_id,
    ))
    tx.append(PendingEvent(
        event_type="llm_call_failed/v1",
        criticality="authoritative",
        stream_id=f"llm-call:{attempt.call.call_id}",
        aggregate_id=str(attempt.call.call_id),
        aggregate_type="llm_call",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={
            "llm_call_id": str(attempt.call.call_id),
            "llm_call_version_id": str(attempt.call.version_id),
            "reason": "provider_submission_not_permitted",
        },
        payload_schema_ref="registry_v1/llm_call_failed/v1",
        task_control=True,
        producer_invocation_id=attempt.call.invocation_id,
    ))
    committed = tx.commit()
    not_permitted = tuple(
        event for event in committed
        if event.event_type
        == "provider_attempt_submission_not_permitted/v1")
    call_failed = tuple(
        event for event in committed
        if event.event_type == "llm_call_failed/v1")
    if (len(not_permitted) != 1 or len(call_failed) != 1
            or not_permitted[0].transaction_id
            != call_failed[0].transaction_id):
        raise ProviderAdmissionError(
            "provider non-permit did not atomically close attempt and call")
    return not_permitted[0], call_failed[0]

def submission_unknown(
        self, attempt: ProviderAttemptV2, *,
        dispatch_event_id: TypedId | None = None,
        diagnostic_resource_ref: ResourceVersionRef,
        require_current_writer: bool = True,
        idempotency_key: str,
) -> tuple[EventEnvelope, EventEnvelope]:
    if not isinstance(attempt, ProviderAttemptV2):
        raise ProviderAdmissionError(
            "submission unknown requires the current v2 attempt")
    self._revalidate_call_v2_context(
        attempt.call, boundary="provider-submission-unknown",
        require_current_writer=require_current_writer)
    metadata = dict(self.service.get_version(attempt.version_id).metadata)
    if dispatch_event_id is None:
        starts = [
            event for event in
            self.service.event_store.list_events_by_aggregate(
                str(attempt.attempt_id),
                event_types=("provider_attempt_dispatch_started/v2",))
        ]
        if len(starts) != 1:
            raise ProviderAdmissionError(
                "submission unknown requires one exact dispatch-start fact")
        dispatch_event_id = starts[0].event_id
    if (dispatch_event_id.kind != "event"
            or not isinstance(
                diagnostic_resource_ref, ResourceVersionRef)):
        raise TypeError(
            "submission unknown requires exact dispatch/diagnostic refs")
    uncertainty_material = {
        "provider_attempt_ref": metadata["provider_attempt_ref"],
        "llm_call_ref": metadata["llm_call_ref"],
        "invocation_ref": metadata["invocation_ref"],
        "operation_binding_ref": metadata["operation_binding_ref"],
        "llm_execution_target_ref": metadata["llm_execution_target_ref"],
        "request_resource_ref": metadata["request_resource_ref"],
        "terminal_delivery_ref": metadata["terminal_delivery_ref"],
        "dispatch_event_id": str(dispatch_event_id),
        "diagnostic_resource_ref": (
            _resource_payload(diagnostic_resource_ref)),
        "uncertainty_kind": "possibly_submitted_response_unknown",
    }
    unknown_ref = VersionRef(
        "provider_submission_unknown/v1",
        _stable_id("provider_submission_unknown", idempotency_key),
        _stable_id("provider_submission_unknown_version", idempotency_key),
    )
    unknown_metadata = {
        "provider_submission_unknown_id": str(unknown_ref.entity_id),
        "provider_submission_unknown_version_id": str(
            unknown_ref.version_id),
        "provider_submission_unknown_ref": _ref_payload(unknown_ref),
        **uncertainty_material,
    }
    payload = {
        "provider_attempt_id": str(attempt.attempt_id),
        "provider_attempt_version_id": str(attempt.version_id),
        "provider_submission_unknown_ref": _ref_payload(unknown_ref),
        **uncertainty_material,
    }
    budget_ref = self.recovery_manifest_ref()
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.prewrite(
        object_type="provider_submission_unknown/v1",
        logical_id=unknown_ref.entity_id,
        version_id=unknown_ref.version_id,
        payload=canonical_json(unknown_metadata),
        metadata=unknown_metadata,
        media_type="application/json",
        schema_ref="registry_v1/provider_submission_unknown/v1",
        producer_invocation_id=attempt.call.invocation_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:unknown-escrow"),
        "submission_unknown_escrowed_by_budget",
        attempt.ref, budget_ref,
        metadata={"partition": attempt.reservation_class,
                  "finalization_scope": attempt.finalization_scope}),
        producer_invocation_id=attempt.call.invocation_id)
    for event_type, aggregate_id, stream_id, aggregate_type in (
            (
                "provider_attempt_submission_unknown/v1",
                str(attempt.attempt_id),
                f"provider-attempt:{attempt.attempt_id}",
                "provider_attempt",
            ),
            (
                "llm_call_submission_unknown/v1",
                str(attempt.call.call_id),
                f"llm-call:{attempt.call.call_id}",
                "llm_call",
            )):
        tx.append(PendingEvent(
            event_type=event_type,
            criticality="authoritative",
            stream_id=stream_id,
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            idempotency_key=idempotency_key,
            command_id=idempotency_key,
            payload=payload,
            payload_schema_ref=f"registry_v1/{event_type}",
            task_control=True,
            producer_invocation_id=attempt.call.invocation_id))
    committed = tx.commit()
    unknowns = tuple(
        event for event in committed
        if event.event_type in {
            "provider_attempt_submission_unknown/v1",
            "llm_call_submission_unknown/v1",
        })
    if len(unknowns) != 2:
        raise ProviderAdmissionError(
            "submission unknown did not commit attempt/call closure")
    return (
        next(event for event in unknowns if event.event_type.startswith(
            "provider_attempt_")),
        next(event for event in unknowns if event.event_type.startswith(
            "llm_call_")),
    )

def close_before_dispatch(
        self, attempt: ProviderAttemptV2, *,
        closed_reason: str,
        require_current_writer: bool = True,
        idempotency_key: str) -> tuple[EventEnvelope, EventEnvelope]:
    """Atomically close one reserved attempt and its call with zero dispatch."""
    if closed_reason not in {
            "credential_binding_unavailable",
            "credential_worker_unavailable",
            "pre_dispatch_runtime_unavailable"}:
        raise ProviderAdmissionError(
            "pre-dispatch closure reason is outside the closed set")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ProviderAdmissionError(
            "pre-dispatch closure requires an idempotency key")
    existing = tuple(
        event for event in
        self.service.event_store.list_events_by_idempotency_key(
            idempotency_key)
        if event.event_type in {
            "provider_attempt_cancelled_before_submission/v2",
            "llm_call_failed/v1",
        })
    if existing:
        attempts = tuple(event for event in existing if event.event_type.endswith(
            "cancelled_before_submission/v2"))
        calls = tuple(event for event in existing
                      if event.event_type == "llm_call_failed/v1")
        if (len(attempts) == 1 and len(calls) == 1
                and attempts[0].aggregate_id == str(attempt.attempt_id)
                and calls[0].aggregate_id == str(attempt.call.call_id)
                and attempts[0].transaction_id == calls[0].transaction_id
                and attempts[0].payload.get("closed_reason") == closed_reason
                and calls[0].payload.get("llm_call_version_id")
                == str(attempt.call.version_id)):
            return attempts[0], calls[0]
        raise ProviderAdmissionError(
            "pre-dispatch closure idempotency key names another command")
    if not isinstance(attempt, ProviderAttemptV2):
        raise ProviderAdmissionError(
            "current pre-dispatch closure requires a v2 attempt")
    self._revalidate_call_v2_context(
        attempt.call,
        boundary="provider-attempt-close-before-dispatch",
        require_current_writer=require_current_writer)
    lifecycle = tuple(
        event for event in
        self.service.event_store.list_events_by_aggregate(
            str(attempt.attempt_id))
        if event.event_type.startswith("provider_attempt_"))
    if (not lifecycle
            or lifecycle[-1].event_type != "provider_attempt_reserved/v1"):
        raise ProviderAdmissionError(
            "pre-dispatch closure requires the current reserved state")
    metadata = dict(self.service.get_version(attempt.version_id).metadata)
    payload = {
        "provider_attempt_id": str(attempt.attempt_id),
        "provider_attempt_version_id": str(attempt.version_id),
        "provider_attempt_ref": metadata["provider_attempt_ref"],
        "llm_call_id": str(attempt.call.call_id),
        "llm_call_version_id": str(attempt.call.version_id),
        "llm_call_ref": metadata["llm_call_ref"],
        "invocation_ref": metadata["invocation_ref"],
        "closed_reason": closed_reason,
        "state": "cancelled_before_submission",
        "dispatch_count": 0,
        "permit_count": 0,
        "request_count": 0,
    }
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.append(PendingEvent(
        event_type="provider_attempt_cancelled_before_submission/v2",
        criticality="authoritative",
        stream_id=f"provider-attempt:{attempt.attempt_id}",
        aggregate_id=str(attempt.attempt_id),
        aggregate_type="provider_attempt",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload=payload,
        payload_schema_ref=(
            "registry_v1/provider_attempt_cancelled_before_submission/v2"),
        task_control=True,
        producer_invocation_id=attempt.call.invocation_id,
    ))
    tx.append(PendingEvent(
        event_type="llm_call_failed/v1",
        criticality="authoritative",
        stream_id=f"llm-call:{attempt.call.call_id}",
        aggregate_id=str(attempt.call.call_id),
        aggregate_type="llm_call",
        idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={
            "llm_call_id": str(attempt.call.call_id),
            "llm_call_version_id": str(attempt.call.version_id),
            "reason": "provider_attempt_cancelled_before_submission",
        },
        payload_schema_ref="registry_v1/llm_call_failed/v1",
        task_control=True,
        producer_invocation_id=attempt.call.invocation_id,
    ))
    committed = tx.commit()
    attempts = tuple(event for event in committed if event.event_type.endswith(
        "cancelled_before_submission/v2"))
    calls = tuple(event for event in committed
                  if event.event_type == "llm_call_failed/v1")
    if (len(attempts) != 1 or len(calls) != 1
            or attempts[0].transaction_id != calls[0].transaction_id):
        raise ProviderAdmissionError(
            "pre-dispatch closure did not atomically close attempt and call")
    return attempts[0], calls[0]

@dataclass(frozen=True, slots=True)
class _RegisteredProviderRoute:
    """Common provenance only; transport/credential interpretation stays outside Core."""

    resource_ref: ResourceVersionRef
    backend: str
    model: str
    transport_kind: str
    response_protocol: str
    timeout_seconds: int


class _ProviderExecutionCommands:
    """Use the owner's existing ledger, event store, object store and CAS."""

    def __init__(self, core):
        self.__core = core
        self.__kernel = _ResourceServiceKernel(core)

    def __registered_attempt(self, attempt):
        if not isinstance(attempt, (ProviderAttemptV2, ProviderAttemptV3)):
            raise TypeError("provider authority requires a current typed attempt")
        context = (
            self.__core.provider_attempts._revalidate_call_v3_context(
                attempt.call, boundary="provider-v3-attempt-verification")
            if isinstance(attempt, ProviderAttemptV3) else
            self.__core.provider_attempts._revalidate_call_v2_context(
                attempt.call, boundary="provider-v2-attempt-verification"))
        self.__validate_call(attempt.call, context)
        scope = self.__core.provider_attempts._canonical_budget_scope(context)
        if scope != (attempt.reservation_class, attempt.finalization_scope):
            raise ResourceIntegrityFault("attempt differs from its registered budget")
        return self.__verify_provider_attempt_closure(attempt, context)

    @staticmethod
    def __call_metadata(call):
        common = {
            "llm_call_id": str(call.call_id),
            "llm_call_version_id": str(call.version_id),
            "llm_call_ref": _ref_payload(call.ref),
            "invocation_ref": _ref_payload(call.invocation_ref),
            "operation_binding_ref": _ref_payload(call.operation_binding_ref),
            "request_resource_ref": _resource_payload(
                call.request_resource_ref),
            "terminal_delivery_ref": _ref_payload(call.terminal_delivery_ref),
            "semantic_prompt_resource_ref": _resource_payload(
                call.semantic_prompt_resource_ref),
            "llm_execution_target_ref": _resource_payload(call.llm_execution_target_ref),
            "backend": call.backend, "model": call.model,
            "transport_contract_ref": _resource_payload(
                call.transport_contract_ref),
            "interaction_protocol_ref": call.interaction_protocol_ref,
            "response_adapter_ref": call.response_adapter_ref,
            "tool_catalog_ref": _resource_payload(call.tool_catalog_ref),
            "timeout_seconds": call.timeout_seconds,
            "max_response_bytes": call.max_response_bytes,
        }
        if isinstance(call, LLMCallV3):
            return {
                **common,
                "invocation_kind": "registered_host",
                "llm_input_target_ref": _resource_payload(
                    call.llm_input_target_ref),
            }
        return {
            **common,
            "agent_loop_ref": dict(call.loop_ref),
            "turn_sequence": call.turn_sequence,
            "prior_turn_refs": [
                _ref_payload(ref) for ref in call.prior_turn_refs],
        }

    def __validate_call(self, call, context):
        """Close current neutral model authority and exact registered route refs."""
        exact_call = self.__kernel._exact_object(
            call.ref, expected_type=call.ref.entity_type)
        if dict(exact_call.metadata) != self.__call_metadata(call):
            raise ResourceIntegrityFault(
                "provider call DTO differs from exact Registry bytes")
        binding = self.__kernel._exact_object(
            context.operation_binding_ref, expected_type="operation_binding/v1").metadata
        target_ref = _resource_from_payload(binding["llm_input_target_ref"])
        if (isinstance(call, LLMCallV3)
                and call.llm_input_target_ref != target_ref):
            raise ProviderAdmissionError(
                "registered-HOST call changed its exact model target")
        target = self.__kernel._firing_prepared(context, target_ref)
        if target.metadata.get("content_schema_ref") != "registry_v1/llm_input_target/v1":
            raise ProviderAdmissionError("operation lacks its exact neutral model target")
        target_data = json.loads(self.__core.object_store.read_registered(target))
        self.__core.catalog.validate_schema_ref("registry_v1/llm_input_target/v1", target_data)
        backend = self.__provider_backend_resource(call.llm_execution_target_ref)
        backend_version = _ref_payload(call.llm_execution_target_ref.as_version_ref())
        request = self.__kernel._firing_prepared(context, call.request_resource_ref)
        request_schema = self.__core.provider_attempts._provider_request_schema(context)
        request_data = _content_schema_instance(
            self.__core.object_store.read_registered(request), media_type=request.media_type)
        if request_data is not _NO_CONTENT_SCHEMA_INSTANCE:
            self.__core.catalog.validate_schema_ref(request_schema, request_data)
        delivery = self.__kernel._exact_object(
            call.terminal_delivery_ref, expected_type="resource_delivery/v1")
        if (call.invocation_ref != context.invocation_ref
                or call.operation_binding_ref != context.operation_binding_ref
                or backend_version not in binding["input_binding_refs"]
                or backend_version not in binding["readable_resource_refs"]
                or backend.backend != call.backend or backend.model != call.model
                or backend.timeout_seconds != call.timeout_seconds
                or target_data["model_condition"] != call.model
                or call.max_response_bytes > target_data["max_response_bytes"]
                or request.metadata.get("content_schema_ref")
                != request_schema
                or not self.__kernel._binding_allows(context, context.operation_binding_ref,
                    call.request_resource_ref, metadata_only=False)
                or delivery.metadata.get("state") != "acknowledged"
                or delivery.metadata.get("boundary") != "llm_prompt"
                or delivery.metadata.get("resource_ref") != _resource_payload(call.request_resource_ref)
                or delivery.metadata.get("context_ref") != _ref_payload(context.invocation_ref)
                or delivery.metadata.get("authorization_ref") != _ref_payload(context.operation_binding_ref)):
            raise ProviderAdmissionError(
                "provider backend/model/request/delivery closure is not exact")
        return backend

    def __require_provider_attempt_slot_v2(self, tx, call, context, prior_attempt):
        binding = self.__kernel._exact_object(
            context.operation_binding_ref, expected_type="operation_binding/v1").metadata
        spec = self.__kernel._exact_object(
            _version_from_payload(binding["operation_spec_ref"]), expected_type="operation_spec/v1")
        node = self.__kernel._exact_object(
            _version_from_payload(binding["node_ref"]), expected_type="node_declaration/v1")
        net = self.__kernel._exact_object(context.net_instance_ref, expected_type="net_instance/v1")
        declaration_ref = _resource_from_payload(net.metadata["team_net_declaration_resource_ref"])
        declaration = self.__kernel._firing_prepared(context, declaration_ref)
        compiled = load_compiled_net(json.loads(self.__core.object_store.read_registered(declaration)))
        transition = next((item for item in compiled.symbolic.transitions
                           if item.name == node.metadata["transition_id"]), None)
        operation = next((item for item in compiled.operations
                          if transition is not None
                          and item.declaration.name == transition.operation), None)
        if (operation is None or operation.operation_id != spec.metadata["operation_id"]
                or operation.executor_key != spec.metadata["executor_key"]
                or compiled.registrations["executor"][operation.executor_key]["contracts"]
                != spec.metadata["implementation_contracts"]):
            raise ResourceIntegrityFault("provider limit lacks exact Module/spec contract closure")
        # This explicit per-call declaration is NOT the actor's turn bound or
        # the manifest's bucket-wide bound. Current producers supply their
        # immutable policy value here; Core never interprets that policy key.
        limit = operation.declaration.config.get("provider_attempt_limit")
        if (spec.metadata["implementation_contracts"]["transport"] != "llm" or isinstance(limit, bool)
                or not isinstance(limit, int) or limit < 1):
            raise ProviderAdmissionError("operation requires explicit positive provider_attempt_limit")
        stream = f"provider-call-attempt:{call.call_id}:{call.version_id}"
        sequence = tx.next_stream_sequence(stream)
        if sequence < 1 or sequence > limit:
            raise ProviderAdmissionError(
                "exact logical call has no available provider-attempt slot")
        rows = self.__core.event_store.provider_attempt_rows_for_call(call.call_id, call.version_id)
        if ((sequence == 1 and prior_attempt is not None)
                or (sequence > 1 and (not rows or prior_attempt is None))):
            raise ProviderAdmissionError("attempt slot requires its exact predecessor")
        return stream

    def commit_provider_raw_response_v2(self, *, attempt, payload, status_code,
                                       external_request_id, idempotency_key):
        """Commit a returned payload, not a transport invocation.

        HTTP routes supply their actual HTTP status. A subprocess route has no
        HTTP status and must supply None; successful returned bytes consume the
        ordinary model-call budget. Transport failures remain separate ledger
        facts and must not enter this returned-payload boundary.
        """
        return self.__commit_provider_raw_response(
            attempt, payload=payload, status_code=status_code,
            external_request_id=external_request_id, idempotency_key=idempotency_key)

    def commit_provider_raw_response_v3(self, *, attempt, payload, status_code,
                                       external_request_id, idempotency_key):
        return self.__commit_provider_raw_response(
            attempt, payload=payload, status_code=status_code,
            external_request_id=external_request_id,
            idempotency_key=idempotency_key)

    def __provider_backend_resource(
            self, backend_ref: ResourceVersionRef, *,
            reference_only: bool = False,
            prepared: Any = None,
    ) -> _RegisteredProviderRoute:
        """Validate the exact route's registered schema, not an HTTP config type."""
        backend_prepared = prepared
        if backend_prepared is None:
            backend_prepared = (
                self.__kernel._prepared_reference(backend_ref)
                if reference_only else self.__kernel._prepared(backend_ref))
        backend_payload = (
            self.__kernel._read_registered(backend_ref)
            if reference_only else
            self.__core.object_store.read_registered(backend_prepared))
        schema_ref = backend_prepared.metadata.get("content_schema_ref")
        source = _content_schema_source_from_payload(
            backend_prepared.metadata.get("content_schema_authority_ref"))
        if not isinstance(schema_ref, str) or source is None:
            raise ProviderAdmissionError("provider route requires its exact registered schema")
        authority = hydrate_registered_content_schema(
            self.__core, source, schema_id=schema_ref,
            fresh_reader=(self.__kernel._read_registered
                          if isinstance(source, ResourceVersionRef) else None))
        backend_data = _content_schema_instance(
            backend_payload, media_type=backend_prepared.media_type)
        self.__core.catalog.validate_schema_ref(
            authority.schema_id, backend_data)
        names = ("backend", "model", "transport_kind", "response_protocol")
        if (not isinstance(backend_data, Mapping)
                or any(not isinstance(backend_data.get(name), str)
                       or not backend_data[name]
                       or backend_data[name] != backend_data[name].strip()
                       for name in names)):
            raise ProviderAdmissionError("provider route lacks explicit common provenance")
        timeout = backend_data.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ProviderAdmissionError("provider route lacks its explicit timeout")
        return _RegisteredProviderRoute(backend_ref, *(backend_data[name] for name in names), timeout)

    def __provider_attempt_state(
            self, attempt: ProviderAttemptV2,
    ) -> tuple[str, Any]:
        events = self.__core.event_store.list_events_by_aggregate(
            str(attempt.attempt_id),
            event_types=(
                "provider_attempt_reserved/v1",
                "provider_attempt_dispatch_started/v1",
                "provider_attempt_dispatch_started/v2",
                "provider_attempt_cancelled_before_submission/v1",
                "provider_attempt_cancelled_before_submission/v2",
                "provider_attempt_submission_permitted/v1",
                "provider_attempt_submission_permitted/v2",
                "provider_attempt_submission_not_permitted/v1",
                "provider_attempt_submission_observed/v1",
                "provider_attempt_submission_unknown/v1",
                "provider_attempt_proven_not_submitted/v1",
                "provider_attempt_proven_submitted/v1",
                "provider_attempt_completed/v1",
                "provider_attempt_failed/v1",
                "provider_attempt_cancelled_after_submission/v1",
                "provider_attempt_outcome_unknown/v1",
                "provider_attempt_reconciled_completed/v1",
                "provider_attempt_reconciled_failed/v1",
                "provider_attempt_reconciled_cancelled_after_submission/v1",
            ))
        if not events:
            raise ResourceIntegrityFault(
                "provider attempt has no durable lifecycle")
        return events[-1].event_type, events[-1]

    def __require_provider_attempt_state(
            self, attempt: ProviderAttemptV2, expected: str,
    ) -> Any:
        state, event = self.__provider_attempt_state(attempt)
        if state != expected:
            raise StaleAuthorityHead(
                "provider lifecycle authority is not fresh for this transition")
        return event

    def __provider_retry_predecessor_cause_v1(
            self, prior_ref: VersionRef, call_ref: VersionRef) -> str:
        """Read the one current retry cause from the predecessor's Registry tail."""
        prior = self.__kernel._exact_object(
            prior_ref, expected_type="provider_attempt_spec/v1")
        history = tuple(
            event for event in
            self.__core.event_store.list_events_by_aggregate(
                str(prior_ref.entity_id))
            if event.event_type.startswith("provider_attempt_"))
        terminal = history[-1] if history else None
        if prior.metadata.get("llm_call_ref") != _ref_payload(call_ref):
            raise ResourceIntegrityFault(
                "provider retry predecessor belongs to another call")
        if (terminal is not None
                and terminal.event_type == "provider_attempt_failed/v1"
                and terminal.payload.get("retry_allowed") is True
                and terminal.payload.get("failure_class") == "http_status"):
            return "http_status"
        if (terminal is not None
                and terminal.event_type
                == "provider_attempt_submission_unknown/v1"):
            return "submission_unknown"
        raise ResourceIntegrityFault(
            "provider retry predecessor has no qualifying current tail")

    def __verify_provider_attempt_closure(
            self, attempt: ProviderAttemptV2 | ProviderAttemptV3,
            context: InvocationContext,
    ) -> tuple[InvocationContext, ProviderAttemptV2 | ProviderAttemptV3,
               _RegisteredProviderRoute]:
        """Verify exact immutable call, attempt, backend, and ref closure."""
        call_exact = self.__kernel._exact_object(
            attempt.call.ref, expected_type=attempt.call.ref.entity_type)
        attempt_exact = self.__kernel._exact_object(
            attempt.ref, expected_type="provider_attempt_spec/v1")
        call = attempt.call
        backend = self.__provider_backend_resource(call.llm_execution_target_ref)
        expected_call = self.__call_metadata(call)
        prior_payload = attempt_exact.metadata.get("prior_attempt_ref")
        prior_valid = prior_payload is None
        if isinstance(prior_payload, Mapping):
            try:
                prior_ref = _version_from_payload(prior_payload)
                retry_cause = self.__provider_retry_predecessor_cause_v1(
                    prior_ref, call.ref)
                prior_valid = (
                    attempt_exact.metadata.get("prior_attempt_version_id")
                    == str(prior_ref.version_id)
                    and attempt_exact.metadata.get("retry_cause")
                    == retry_cause)
            except Exception:
                prior_valid = False
        elif (attempt_exact.metadata.get("prior_attempt_version_id") is not None
              or attempt_exact.metadata.get("retry_cause") is not None):
            prior_valid = False
        expected_attempt = {
            "provider_attempt_ref": _ref_payload(attempt.ref),
            "llm_call_ref": _ref_payload(call.ref),
            "invocation_ref": _ref_payload(context.invocation_ref),
            "operation_binding_ref": _ref_payload(
                context.operation_binding_ref),
            "llm_execution_target_ref": _resource_payload(call.llm_execution_target_ref),
            "request_resource_ref": _resource_payload(
                call.request_resource_ref),
            "terminal_delivery_ref": _ref_payload(call.terminal_delivery_ref),
            "backend": call.backend, "model": call.model,
            "transport_kind": backend.transport_kind,
            "response_protocol": backend.response_protocol,
            "timeout_seconds": call.timeout_seconds,
            "reservation_class": attempt.reservation_class,
            "finalization_scope": attempt.finalization_scope,
            "prior_attempt_ref": prior_payload,
        }
        if (context.operation_binding_ref != call.operation_binding_ref
                or backend.resource_ref != call.llm_execution_target_ref
                or backend.backend != call.backend
                or backend.model != call.model
                or not prior_valid
                or any(call_exact.metadata.get(name) != expected
                       for name, expected in expected_call.items())
                or any(attempt_exact.metadata.get(name) != expected
                       for name, expected in expected_attempt.items())):
            raise ResourceIntegrityFault(
                "provider attempt differs from exact call/backend authority")
        return context, attempt, backend

    def reserve_provider_attempt_v2(
            self, *, context: InvocationContext,
            call: LLMCallV2 | LLMCallV3,
            prior_attempt: ProviderAttemptV2 | None,
            idempotency_key: str) -> ProviderAttemptV2 | ProviderAttemptV3:
        if (not isinstance(context, InvocationContext)
                or not isinstance(call, (LLMCallV2, LLMCallV3))
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise TypeError("provider reservation requires context/call/key")
        if isinstance(call, LLMCallV3) and prior_attempt is not None:
            raise ProviderAdmissionError("registered-HOST calls do not retry")
        canonical = (
            self.__core.provider_attempts._revalidate_call_v3_context(
                call, boundary="provider-v3-attempt-reservation")
            if isinstance(call, LLMCallV3) else
            self.__core.provider_attempts._revalidate_call_v2_context(
                call, boundary="provider-v2-attempt-reservation"))
        if canonical != context:
            raise ProviderAdmissionError(
                "provider reservation context differs from Registry")
        attempt_type = (
            ProviderAttemptV3 if isinstance(call, LLMCallV3)
            else ProviderAttemptV2)
        existing_version = _stable_id(
            "provider_attempt_version", idempotency_key)
        if self.__core.event_store.object_row(existing_version) is not None:
            replay = attempt_type(
                _stable_id("provider_attempt", idempotency_key),
                existing_version, call,
                str(self.__kernel._exact_object(VersionRef(
                    "provider_attempt_spec/v1",
                    _stable_id("provider_attempt", idempotency_key),
                    existing_version)).metadata["reservation_class"]),
                self.__kernel._exact_object(VersionRef(
                    "provider_attempt_spec/v1",
                    _stable_id("provider_attempt", idempotency_key),
                    existing_version)).metadata["finalization_scope"],
            )
            stored = self.__kernel._exact_object(replay.ref).metadata
            if stored["prior_attempt_ref"] != (
                    _ref_payload(prior_attempt.ref) if prior_attempt is not None else None):
                raise ProviderAdmissionError(
                    "provider reservation replay changed its predecessor")
            self.__registered_attempt(replay)
            return replay
        tx = self.__core.begin(idempotency_key=idempotency_key)
        attempt_slot_stream = self.__require_provider_attempt_slot_v2(
            tx, call, context, prior_attempt)
        call_exact = self.__kernel._exact_object(
            call.ref, expected_type=call.ref.entity_type)
        if (call.invocation_ref != context.invocation_ref
                or call.operation_binding_ref
                != context.operation_binding_ref
                or call_exact.metadata.get("llm_call_ref")
                != _ref_payload(call.ref)):
            raise ProviderAdmissionError(
                "provider reservation call differs from invocation authority")
        prior_failure_class = None
        if prior_attempt is not None:
            if (not isinstance(prior_attempt, ProviderAttemptV2)
                    or prior_attempt.call.ref != call.ref):
                raise ProviderAdmissionError(
                    "v2 retry predecessor belongs to another logical call")
            try:
                prior_failure_class = (
                    self.__provider_retry_predecessor_cause_v1(
                        prior_attempt.ref, call.ref))
            except ResourceIntegrityFault as exc:
                raise ProviderAdmissionError(
                    "v2 retry requires one qualifying committed predecessor") \
                    from exc
        backend = self.__validate_call(call, context)
        reservation_class, finalization_scope = (
            self.__core.provider_attempts._canonical_budget_scope(context))
        budget_ref = self.__core.recovery_manifest_ref()
        attempt_id = _stable_id("provider_attempt", idempotency_key)
        version_id = existing_version
        activation = (context.activation_ref
                      or context.authorization_lifetime_activation_ref)
        publication = materialize_attempt_v2_reservation(
            attempt_id=attempt_id, version_id=version_id,
            call_ref=call.ref,
            invocation_ref=context.invocation_ref,
            operation_binding_ref=context.operation_binding_ref,
            llm_execution_target_ref=call.llm_execution_target_ref,
            request_resource_ref=call.request_resource_ref,
            terminal_delivery_ref=call.terminal_delivery_ref,
            budget_witness_ref=budget_ref,
            prior_attempt_ref=(
                prior_attempt.ref if prior_attempt is not None else None),
            activation_ref=activation,
            origin=context.origin,
            accounting_parent_invocation_ref=(
                context.accounting_parent_invocation_ref),
            backend=call.backend, model=call.model,
            transport_kind=backend.transport_kind,
            response_protocol=backend.response_protocol,
            timeout_seconds=call.timeout_seconds,
            prior_failure_class=prior_failure_class,
            reservation_class=reservation_class,
            finalization_scope=finalization_scope,
            writer_fencing_epoch=self.__core.writer_epoch,
            ref_payload=_ref_payload,
            resource_payload=_resource_payload,
        )
        attempt = attempt_type(
            attempt_id, version_id, call, reservation_class,
            finalization_scope)
        tx.prewrite(
            object_type="provider_attempt_spec/v1", logical_id=attempt_id,
            version_id=version_id,
            payload=canonical_json(publication.metadata),
            metadata=publication.metadata, media_type="application/json",
            schema_ref="registry_v1/provider_attempt_spec/v1",
            producer_invocation_id=call.invocation_id)
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:attempt-of-call"),
            "attempt_of_call", attempt.ref, call.ref),
            producer_invocation_id=call.invocation_id)
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:bound-to-backend"),
            "bound_to_backend", attempt.ref,
            call.llm_execution_target_ref.as_version_ref()),
            producer_invocation_id=call.invocation_id)
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:consumes-resource"),
            "attempt_consumes_resource", attempt.ref,
            call.request_resource_ref.as_version_ref()),
            producer_invocation_id=call.invocation_id)
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:uses-delivery"),
            "attempt_uses_delivery", attempt.ref, call.terminal_delivery_ref),
            producer_invocation_id=call.invocation_id)
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:call-policy"),
            "governed_by_call_policy", attempt.ref, budget_ref,
            metadata={"partition": reservation_class,
                      "finalization_scope": finalization_scope}),
            producer_invocation_id=call.invocation_id)
        if prior_attempt is not None:
            tx.relate(TypedRelation(
                _stable_id("relation", f"{idempotency_key}:retry-of-attempt"),
                "retry_of_attempt", attempt.ref, prior_attempt.ref,
                metadata={"failure_class": prior_failure_class}),
                producer_invocation_id=call.invocation_id)
        tx.append(PendingEvent(
            event_type="provider_attempt_reserved/v1",
            criticality="authoritative",
            stream_id=attempt_slot_stream,
            aggregate_id=str(attempt_id), aggregate_type="provider_attempt",
            idempotency_key=idempotency_key, command_id=idempotency_key,
            payload=publication.event_payload,
            payload_schema_ref="registry_v1/provider_attempt_reserved/v1",
            task_control=True,
            producer_invocation_id=call.invocation_id))
        tx.commit()
        self.__registered_attempt(attempt)
        return attempt

    def reserve_provider_attempt_v3(
            self, *, context: InvocationContext, call: LLMCallV3,
            prior_attempt: None, idempotency_key: str) -> ProviderAttemptV3:
        attempt = self.reserve_provider_attempt_v2(
            context=context, call=call, prior_attempt=prior_attempt,
            idempotency_key=idempotency_key)
        if not isinstance(attempt, ProviderAttemptV3):
            raise ProviderAdmissionError(
                "v3 reservation returned another provider attempt type")
        return attempt

    def __commit_provider_raw_response(
            self, attempt: ProviderAttemptV2 | ProviderAttemptV3, *,
            payload: bytes,
            status_code: int | None,
            external_request_id: str | None,
            idempotency_key: str):
        from cpn.rpnh.response_protocol import PublishedRawLLMResponse
        from ..provider_calls import RegisteredHostTransportResponse

        response_type = (
            RegisteredHostTransportResponse
            if isinstance(attempt, ProviderAttemptV3)
            else PublishedRawLLMResponse)
        if (not isinstance(payload, bytes)
                or (status_code is not None and (
                    isinstance(status_code, bool)
                    or not isinstance(status_code, int)
                    or not 100 <= status_code <= 599))
                or (external_request_id is not None
                    and not isinstance(external_request_id, str))
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise TypeError("raw publication requires bytes/status/key")
        context, attempt, backend = self.__registered_attempt(attempt)
        if ((backend.transport_kind == "subprocess" and status_code is not None)
                or (backend.transport_kind != "subprocess" and status_code is None)):
            raise ProviderAdmissionError("raw status must match the exact registered transport")
        if len(payload) > attempt.call.max_response_bytes:
            raise ProviderAdmissionError(
                "raw response exceeds the exact call byte limit")
        existing = tuple(
            event for event in self.__core.event_store.list_events_by_aggregate(
                str(attempt.attempt_id),
                event_types=("provider_attempt_submission_observed/v1",))
            if event.idempotency_key == idempotency_key)
        if existing:
            if len(existing) != 1:
                raise ResourceIntegrityFault(
                    "raw v2 replay has duplicate observation facts")
            response_ref = _resource_from_payload(
                existing[0].payload["response_resource_ref"])
            prepared = self.__prepared_fresh_agent_raw_response_v1(
                response_ref, call_ref=attempt.call.ref,
                attempt_ref=attempt.ref)
            stored = self.__core.object_store.read_registered(prepared)
            extensions = prepared.metadata.get("extensions")
            response_facts = (
                extensions.get(
                    "registry.provider_raw_response/v3"
                    if isinstance(attempt, ProviderAttemptV3)
                    else "registry.provider_raw_response/v2")
                if isinstance(extensions, Mapping) else None)
            if (stored != payload
                    or not isinstance(response_facts, Mapping)
                    or response_facts.get("status_code") != status_code
                    or response_facts.get("external_request_id")
                    != external_request_id):
                raise ResourceIntegrityFault(
                    "raw v2 replay bytes differ from Registry")
            return response_type(
                response_resource_ref=response_ref,
                provider_attempt_ref=attempt.ref,
                llm_call_ref=attempt.call.ref,
                size=prepared.size,
                backend=attempt.call.backend, model=attempt.call.model,
                interaction_protocol_ref=(
                    attempt.call.interaction_protocol_ref),
                response_adapter_ref=attempt.call.response_adapter_ref)
        permit = self.__require_provider_attempt_state(
            attempt, "provider_attempt_submission_permitted/v2")
        dispatch_event_id = TypedId.parse(
            str(permit.payload["dispatch_event_id"]), expected="event")
        lifetime_ref = context.operation_execution_lease_ref
        consumer_ref = (
            attempt.call.ref if isinstance(attempt, ProviderAttemptV3)
            else _version_from_payload(attempt.call.loop_ref))
        resource_id = _publication_id(
            "resource", self.__core.task_id,
            context.task_round_ref.entity_id,
            context.net_instance_ref.entity_id,
            "provider_raw_response", attempt.version_id,
            attempt.call.version_id)
        version_id = _publication_id(
            "resource_version", self.__core.task_id, idempotency_key,
            attempt.version_id, attempt.call.version_id,
            context.invocation_ref.version_id)
        response_ref = ResourceVersionRef(resource_id, version_id)
        descriptors = {
            "content_role": "provider_raw_response_evidence"}
        extensions = {
            ("registry.provider_raw_response/v3"
             if isinstance(attempt, ProviderAttemptV3)
             else "registry.provider_raw_response/v2"): {
                "transport_kind": backend.transport_kind,
                "status_code": status_code,
                "external_request_id": (
                    external_request_id),
            }}

        def metadata_factory(payload_size: int):
            return _direct_resource_metadata(
                self.__core, ref=response_ref,
                origin_kind="provider_raw_response",
                primary=attempt.ref, secondary=attempt.call.ref,
                task_ref=context.task_ref,
                round_ref=context.task_round_ref,
                net_ref=context.net_instance_ref,
                producer_ref=context.invocation_ref,
                lifetime_ref=lifetime_ref,
                operation_binding_ref=context.operation_binding_ref,
                agent_loop_ref=consumer_ref,
                payload_size=payload_size,
                media_type="application/octet-stream",
                content_schema_ref=None,
                content_schema_authority_ref=None,
                summary="Raw provider response",
                descriptors=descriptors, extensions=extensions,
                input_resource_refs=(),
                intended_boundary=(
                    "registered_host_response"
                    if isinstance(attempt, ProviderAttemptV3)
                    else "agent_turn_decode"))

        budget_ref = _version_from_payload(
            self.__kernel._exact_object(
                attempt.ref,
                expected_type="provider_attempt_spec/v1").metadata[
                    "budget_witness_ref"])
        tx = self.__core.begin(
            idempotency_key=idempotency_key,
            task_round_id=context.task_round_ref.entity_id,
            net_instance_id=context.net_instance_ref.entity_id)
        prepared = _append_direct_resource_version_publication(
            tx, ref=response_ref, payload=payload,
            metadata_factory=metadata_factory,
            media_type="application/octet-stream",
            producer_ref=context.invocation_ref,
            producer_invocation_id=context.invocation_ref.entity_id,
            relation_key=idempotency_key)
        tx.relate(TypedRelation(
            _publication_id(
                "relation", idempotency_key, "attempt-response"),
            "attempt_produced_response", attempt.ref,
            response_ref.as_version_ref()),
            producer_invocation_id=context.invocation_ref.entity_id)
        if status_code is None or 200 <= status_code < 300:
            tx.relate(TypedRelation(
                _publication_id(
                    "relation", idempotency_key, "actual-model-call"),
                "actual_model_call_recorded_against_limit",
                attempt.ref, budget_ref,
                metadata={"partition": attempt.reservation_class,
                          "finalization_scope": attempt.finalization_scope}),
                producer_invocation_id=context.invocation_ref.entity_id)
        material = {
            "provider_attempt_ref": _ref_payload(attempt.ref),
            "llm_call_ref": _ref_payload(attempt.call.ref),
            "invocation_ref": _ref_payload(context.invocation_ref),
            "dispatch_event_id": str(dispatch_event_id),
            "response_resource_ref": _resource_payload(response_ref),
            "response_size": prepared.size,
            "finish_reason": None,
            "external_request_id": external_request_id,
        }
        tx.append(PendingEvent(
            event_type="provider_attempt_submission_observed/v1",
            criticality="authoritative",
            stream_id=f"provider-attempt:{attempt.attempt_id}",
            aggregate_id=str(attempt.attempt_id),
            aggregate_type="provider_attempt",
            idempotency_key=idempotency_key,
            command_id=idempotency_key,
            payload={
                "provider_attempt_id": str(attempt.attempt_id),
                "provider_attempt_version_id": str(attempt.version_id),
                **material,
            },
            payload_schema_ref=(
                "registry_v1/provider_attempt_submission_observed/v1"),
            task_control=True,
            producer_invocation_id=context.invocation_ref.entity_id))
        tx.commit()
        prepared = self.__prepared_fresh_agent_raw_response_v1(
            response_ref, call_ref=attempt.call.ref,
            attempt_ref=attempt.ref)
        stored = self.__core.object_store.read_registered(prepared)
        if stored != payload:
            raise ResourceIntegrityFault(
                "fresh raw provider response bytes changed after commit")
        return response_type(
            response_resource_ref=response_ref,
            provider_attempt_ref=attempt.ref,
            llm_call_ref=attempt.call.ref,
            size=prepared.size, backend=backend.backend,
            model=backend.model,
            interaction_protocol_ref=(
                attempt.call.interaction_protocol_ref),
            response_adapter_ref=attempt.call.response_adapter_ref)

    def __prepared_fresh_agent_raw_response_v1(
            self, response_ref: ResourceVersionRef, *, call_ref: VersionRef,
            attempt_ref: VersionRef):
        """Prepare one fresh raw response from exact direct Registry refs."""
        if (not isinstance(response_ref, ResourceVersionRef)
                or not isinstance(call_ref, VersionRef)
                or call_ref.entity_type not in {
                    "llm_call_spec/v2", "llm_call_spec/v3"}
                or not isinstance(attempt_ref, VersionRef)
                or attempt_ref.entity_type != "provider_attempt_spec/v1"):
            raise TypeError(
                "fresh agent raw response requires exact response/call/attempt refs")
        call = self.__kernel._exact_object(
            call_ref, expected_type=call_ref.entity_type)
        attempt = self.__kernel._exact_object(
            attempt_ref, expected_type="provider_attempt_spec/v1")
        invocation_payload = call.metadata.get("invocation_ref")
        if not isinstance(invocation_payload, Mapping):
            raise ResourceIntegrityFault(
                "fresh agent raw response call lacks invocation authority")
        response_context = InvocationLifecycle(self.__core).hydrate_context(
            _version_from_payload(invocation_payload))
        if response_context.own_transition_firing_ref is None:
            raise ResourceIntegrityFault(
                "fresh agent raw response invocation lacks firing authority")
        prepared = self.__kernel._firing_prepared(response_context, response_ref)
        origin = prepared.metadata.get("origin")
        if ("reference_provenance" not in prepared.metadata
                or not isinstance(
                    prepared.metadata.get("reference_provenance"), Mapping)
                or not isinstance(origin, Mapping)
                or origin.get("kind") != "provider_raw_response"
                or origin.get("primary_ref") != _ref_payload(attempt_ref)
                or origin.get("secondary_ref") != _ref_payload(call_ref)
                or attempt.metadata.get("llm_call_ref")
                != _ref_payload(call_ref)
                or attempt.metadata.get("request_resource_ref")
                != call.metadata.get("request_resource_ref")):
            raise ResourceIntegrityFault(
                "fresh agent raw response differs from its exact call/attempt chain")
        return prepared


def reserve_provider_attempt_v2(
        self, *, context: InvocationContext, call: LLMCallV2,
        prior_attempt: ProviderAttemptV2 | None,
        idempotency_key: str) -> ProviderAttemptV2:
    core = self._ProviderExecution__core
    return _ProviderExecutionCommands(core).reserve_provider_attempt_v2(
        context=context, call=call, prior_attempt=prior_attempt,
        idempotency_key=idempotency_key)


def commit_provider_raw_response_v2(
        self, *, attempt, payload, status_code,
        external_request_id, idempotency_key):
    core = self._ProviderExecution__core
    return _ProviderExecutionCommands(core).commit_provider_raw_response_v2(
        attempt=attempt, payload=payload, status_code=status_code,
        external_request_id=external_request_id,
        idempotency_key=idempotency_key)


def reserve_provider_attempt_v3(
        self, *, context: InvocationContext, call: LLMCallV3,
        prior_attempt: None, idempotency_key: str) -> ProviderAttemptV3:
    core = self._ProviderExecution__core
    return _ProviderExecutionCommands(core).reserve_provider_attempt_v3(
        context=context, call=call, prior_attempt=prior_attempt,
        idempotency_key=idempotency_key)


def commit_provider_raw_response_v3(
        self, *, attempt, payload, status_code,
        external_request_id, idempotency_key):
    core = self._ProviderExecution__core
    return _ProviderExecutionCommands(core).commit_provider_raw_response_v3(
        attempt=attempt, payload=payload, status_code=status_code,
        external_request_id=external_request_id,
        idempotency_key=idempotency_key)
