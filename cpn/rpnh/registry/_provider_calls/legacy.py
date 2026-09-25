"""Historical provider-call v1 command bodies.

These commands remain available through the compatibility facade but are not
the current-v2 provider path.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..identities import TypedId
from ..invocations import InvocationContext, InvocationLifecycle, _stable_id
from ..models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from ..provider_calls import (
    ExternallyValidatedProviderRequestAuthority,
    LLMCall,
    ProviderAdmissionError,
    ProviderAttempt,
    ProviderAttemptUnknown,
    _ref_payload,
    _resource_payload,
)
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
def _historical_create_call(
        self, *, context: InvocationContext,
        request_authority: ExternallyValidatedProviderRequestAuthority,
        tool_turn_sequence: int, request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef, request_payload_digest: str,
        tool_catalog_digest: str, response_contract_digest: str,
        model_requirement: str, timeout_seconds: int, retry_limit: int,
        revision_reason: str = "initial",
        previous_call_version_id: TypedId | None = None,
        caused_by_response_ref: VersionRef | None = None,
        call_id: TypedId | None = None,
        idempotency_key: str) -> LLMCall:
    InvocationLifecycle(self.service).revalidate_io(
        context, boundary="llm-call-creation")
    if (not isinstance(
                request_authority,
                ExternallyValidatedProviderRequestAuthority)
            or not request_authority.matches(
                request_resource_ref=request_resource_ref,
                context=context)
            or not isinstance(request_resource_ref, ResourceVersionRef)
            or not isinstance(terminal_delivery_ref, VersionRef)
            or terminal_delivery_ref.entity_type != "resource_delivery/v1"
            or not isinstance(request_payload_digest, str)
            or len(request_payload_digest) != 64
            or any(character not in "0123456789abcdef"
                   for character in request_payload_digest)):
        raise ProviderAdmissionError(
            "logical call requires exact request file/delivery/digest")
    request_row = self.service.event_store.object_row(
        request_resource_ref.resource_version_id)
    delivery_row = self.service.event_store.object_row(
        terminal_delivery_ref.version_id)
    if request_row is None or delivery_row is None:
        raise ProviderAdmissionError(
            "logical call request file/delivery is not registered")
    request = json.loads(str(request_row["metadata_json"]))
    delivery = json.loads(str(delivery_row["metadata_json"]))
    acknowledged = tuple(
        event for event in self.service.event_store.list_events_by_aggregate(
            str(terminal_delivery_ref.entity_id),
            event_types=("resource_delivery_acknowledged/v1",))
        if (event.payload.get("terminal_delivery_ref")
            == _ref_payload(terminal_delivery_ref)))
    if (request_row["object_type"] != "resource_version/v1"
            or request_row["logical_id"]
            != str(request_resource_ref.resource_id)
            or delivery_row["object_type"] != "resource_delivery/v1"
            or delivery_row["logical_id"]
            != str(terminal_delivery_ref.entity_id)
            # v1 binds the direct application prompt digest.  v2's
            # logical-recipe digest is admitted only by create_call_v2.
            or request.get("payload_digest") != request_payload_digest
            or delivery.get("state") != "acknowledged"
            or delivery.get("boundary") != "llm_prompt"
            or delivery.get("resource_ref")
            != _resource_payload(request_resource_ref)
            or delivery.get("resource_digest") != request_payload_digest
            or delivery.get("context_ref") != _ref_payload(
                context.invocation_ref)
            or delivery.get("authorization_ref") != _ref_payload(
                context.operation_binding_ref)
            or len(acknowledged) != 1):
        raise ProviderAdmissionError(
            "logical call request differs from exact acknowledged prompt")
    invocation_ref = context.invocation_ref
    activation_ref = (context.activation_ref
                      or context.authorization_lifetime_activation_ref)
    call_id = call_id or _stable_id("llm_call", idempotency_key)
    version_id = _stable_id("llm_call_version", idempotency_key)
    metadata = {
        "llm_call_id": str(call_id), "llm_call_version_id": str(version_id),
        "llm_call_ref": _ref_payload(VersionRef(
            "llm_call_spec/v1", call_id, version_id)),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_binding_ref": _ref_payload(context.operation_binding_ref),
        "request_resource_ref": _resource_payload(request_resource_ref),
        "terminal_delivery_ref": _ref_payload(terminal_delivery_ref),
        "invocation_id": str(invocation_ref.entity_id),
        "invocation_version_id": str(invocation_ref.version_id),
        "activation_id": (str(activation_ref.entity_id)
                          if activation_ref is not None else None),
        "context_digest": context.context_digest,
        "origin": context.origin,
        "accounting_parent_invocation_id": str(
            context.accounting_parent_invocation_ref.entity_id),
        "accounting_parent_invocation_version_id": str(
            context.accounting_parent_invocation_ref.version_id),
        "budget_scope": context.budget_scope,
        "finalization_scope": context.finalization_scope,
        "tool_turn_sequence": int(tool_turn_sequence),
        "request_digest": request_payload_digest,
        "tool_catalog_digest": tool_catalog_digest,
        "response_contract_digest": response_contract_digest,
        "model_requirement": model_requirement,
        "timeout_seconds": int(timeout_seconds), "retry_limit": int(retry_limit),
        "revision_reason": revision_reason,
        "previous_call_version_id": (
            str(previous_call_version_id) if previous_call_version_id else None),
    }
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.prewrite(
        object_type="llm_call_spec/v1", logical_id=call_id, version_id=version_id,
        payload=canonical_json(metadata), metadata=metadata,
        media_type="application/json", schema_ref="registry_v1/llm_call_spec/v1",
        producer_invocation_id=invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:call-of-invocation"),
        "call_of_invocation",
        VersionRef("llm_call_spec/v1", call_id, version_id), invocation_ref),
        producer_invocation_id=invocation_ref.entity_id)
    if previous_call_version_id is not None:
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:supersedes"),
            "supersedes",
            VersionRef("llm_call_spec/v1", call_id, version_id),
            VersionRef("llm_call_spec/v1", call_id, previous_call_version_id)),
            producer_invocation_id=invocation_ref.entity_id)
    if caused_by_response_ref is not None:
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:caused-by-response"),
            "caused_by_tool_result",
            VersionRef("llm_call_spec/v1", call_id, version_id),
            caused_by_response_ref,
            metadata={"reason": revision_reason}),
            producer_invocation_id=invocation_ref.entity_id)
    tx.commit()
    return LLMCall(
        call_id, version_id, invocation_ref,
        activation_ref.entity_id if activation_ref is not None else None,
        context.context_digest, context.operation_binding_ref,
        request_resource_ref, terminal_delivery_ref)


def _historical_revalidate_call_context(
        self, call: LLMCall, *, boundary: str,
        require_current_writer: bool = True) -> InvocationContext:
    context = InvocationLifecycle(self.service).hydrate_context(
        call.invocation_ref,
        require_current_writer=require_current_writer)
    if context.context_digest != call.context_digest:
        raise ProviderAdmissionError(
            "provider call context digest differs from its canonical invocation")
    if require_current_writer:
        InvocationLifecycle(self.service).revalidate_io(
            context, boundary=boundary)
    return context

def _historical_reserve(
        self, *, context: InvocationContext, call: LLMCall,
        request_authority: ExternallyValidatedProviderRequestAuthority,
        operation_binding_metadata: Mapping[str, Any],
        llm_execution_target_ref: VersionRef,
        request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        backend: str, model: str, transport_kind: str, timeout_seconds: int,
        response_protocol: str,
        request_payload_digest: str, retry_cause: str | None = None,
        prior_attempt: ProviderAttempt | None = None,
        resource_contract_ref: VersionRef | None = None,
        idempotency_key: str) -> ProviderAttempt:
    reservation_class, finalization_scope = (
        self._canonical_budget_scope(context))
    if (llm_execution_target_ref.entity_type != "resource_version/v1"
            or llm_execution_target_ref.entity_id.kind != "resource"
            or llm_execution_target_ref.version_id.kind != "resource_version"
            or not isinstance(operation_binding_metadata, Mapping)):
        raise TypeError("backend binding must be an exact registered resource version")
    backend_resource_ref = ResourceVersionRef(
        llm_execution_target_ref.entity_id, llm_execution_target_ref.version_id)
    binding = dict(operation_binding_metadata)
    backend_payload = _resource_payload(backend_resource_ref)
    backend_version_payload = _ref_payload(llm_execution_target_ref)
    if binding.get("llm_execution_target_ref") != backend_payload:
        raise ProviderAdmissionError(
            "provider backend differs from the exact operation binding")
    if (backend_version_payload not in binding.get("input_binding_refs", [])
            or backend_version_payload
            not in binding.get("readable_resource_refs", [])):
        raise ProviderAdmissionError(
            "provider backend is not an exact readable operation input")
    if (not isinstance(
                request_authority,
                ExternallyValidatedProviderRequestAuthority)
            or not request_authority.matches(
                request_resource_ref=request_resource_ref,
                context=context)):
        raise ProviderAdmissionError(
            "provider request lacks exact workflow validation authority")
    request_object = self.service.get_version(
        request_resource_ref.resource_version_id)
    try:
        backend_object = self.service.get_version(
            llm_execution_target_ref.version_id)
        terminal = self.service.get_version(terminal_delivery_ref.version_id)
    except Exception as exc:
        raise ProviderAdmissionError(
            "provider attempt refs are not exact registered objects") from exc
    if (backend_object.object_type != "resource_version/v1"
            or backend_object.logical_id != llm_execution_target_ref.entity_id
            or backend_object.metadata.get("content_schema_ref")
            != "registry_v1/provider_backend_config/v1"
            or request_object.object_type != "resource_version/v1"
            or request_object.logical_id != request_resource_ref.resource_id
            or terminal.object_type != "resource_delivery/v1"
            or terminal.logical_id != terminal_delivery_ref.entity_id
            or terminal.metadata.get("state") != "acknowledged"
            or terminal.metadata.get("boundary") != "llm_prompt"
            or terminal.metadata.get("resource_ref")
            != _resource_payload(request_resource_ref)):
        raise ProviderAdmissionError(
            "provider attempt backend/request/delivery closure is not exact")
    if (str(backend_object.metadata.get("payload_digest"))
            != backend_object.payload_digest
            or str(request_object.metadata.get("payload_digest"))
            != request_object.payload_digest
            or terminal.metadata.get("resource_digest")
            != request_object.payload_digest
            or request_payload_digest != request_object.payload_digest):
        raise ProviderAdmissionError(
            "provider attempt resource or delivery digest differs")
    if resource_contract_ref is not None:
        raise ProviderAdmissionError(
            "resource contract extension is inactive until G-PROFILE is approved")
    call_row = self.service.event_store.object_row(call.version_id)
    if (call_row is None or call_row["object_type"] != "llm_call_spec/v1"
            or call_row["logical_id"] != str(call.call_id)):
        raise ProviderAdmissionError("provider attempt requires an exact registered call version")
    call_metadata = self.service.get_version(call.version_id).metadata
    if (call.invocation_ref != context.invocation_ref
            or call.context_digest != context.context_digest
            or call.operation_binding_ref != context.operation_binding_ref
            or call.request_resource_ref != request_resource_ref
            or call.terminal_delivery_ref != terminal_delivery_ref
            or call_metadata.get("invocation_version_id")
            != str(context.invocation_ref.version_id)
            or call_metadata.get("context_digest") != context.context_digest):
        raise ProviderAdmissionError(
            "provider call does not belong to the canonical invocation context")
    if prior_attempt is not None:
        if prior_attempt.call.call_id != call.call_id:
            raise ProviderAdmissionError("retry predecessor belongs to another logical call")
        prior_events = [
            event for event in
            self.service.event_store.list_events_by_aggregate(
                str(prior_attempt.attempt_id))
            if event.event_type.startswith("provider_attempt_")
        ]
        prior_state = prior_events[-1] if prior_events else None
        if prior_state is None:
            raise ProviderAdmissionError("retry predecessor has no canonical lifecycle")
        retryable = (
            prior_state.event_type in {
                "provider_attempt_cancelled_before_submission/v1",
            }
            or (prior_state.event_type in {
                "provider_attempt_failed/v1",
                "provider_attempt_reconciled_failed/v1",
                "provider_attempt_reconciled_cancelled_after_submission/v1",
                "provider_attempt_cancelled_after_submission/v1",
            } and bool(prior_state.payload.get("retry_allowed")))
        )
        if not retryable:
            if prior_state.event_type in {
                "provider_attempt_submission_unknown/v1",
                "provider_attempt_outcome_unknown/v1",
                "provider_attempt_proven_submitted/v1",
            }:
                raise ProviderAttemptUnknown(
                    "provider attempt uncertainty must be reconciled before retry")
            raise ProviderAdmissionError(
                f"provider attempt state is not retryable: {prior_state.event_type}")
    attempt_id = _stable_id("provider_attempt", idempotency_key)
    version_id = _stable_id("provider_attempt_version", idempotency_key)
    existing_attempt = self.service.event_store.object_row(version_id)
    node_ref_payload = binding.get("node_ref")
    if not isinstance(node_ref_payload, Mapping):
        raise ProviderAdmissionError(
            "provider attempt operation binding has no exact node limits")
    try:
        node_ref = VersionRef(
            str(node_ref_payload["entity_type"]),
            TypedId.parse(str(node_ref_payload["logical_id"])),
            TypedId.parse(str(node_ref_payload["version_id"])),
        )
        node = self.service.get_version(node_ref.version_id)
        limits = node.metadata["resource_bounds"]
        max_llm_attempts = limits["max_llm_attempts"]
    except Exception as exc:
        raise ProviderAdmissionError(
            "provider attempt cannot hydrate exact operation limits") from exc
    if (node.object_type != "node_declaration/v1"
            or node.logical_id != node_ref.entity_id
            or isinstance(max_llm_attempts, bool)
            or not isinstance(max_llm_attempts, int)
            or max_llm_attempts < 0):
        raise ProviderAdmissionError(
            "provider attempt operation limits are malformed")
    call_attempts = tuple(
        row for row in
        self.service.event_store.provider_attempt_rows_for_call(
            call.call_id, call.version_id)
        if json.loads(row["metadata_json"]).get("llm_call_ref")
        == _ref_payload(call.ref))
    if (existing_attempt is None
            and len(call_attempts) >= max_llm_attempts):
        raise ProviderAdmissionError(
            "logical provider call exhausted max_llm_attempts")
    if (existing_attempt is None and call_attempts
            and prior_attempt is None):
        raise ProviderAdmissionError(
            "additional logical-call attempt requires its retry predecessor")
    if (prior_attempt is not None
            and all(row["version_id"] != str(prior_attempt.version_id)
                    for row in call_attempts)):
        raise ProviderAdmissionError(
            "retry predecessor is outside the logical call lineage")
    budget_ref = self.recovery_manifest_ref()
    metadata = {
        "provider_attempt_id": str(attempt_id),
        "provider_attempt_version_id": str(version_id),
        "provider_attempt_ref": _ref_payload(VersionRef(
            "provider_attempt_spec/v1", attempt_id, version_id)),
        "llm_call_ref": _ref_payload(call.ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "operation_binding_ref": _ref_payload(context.operation_binding_ref),
        "llm_execution_target_ref": _resource_payload(ResourceVersionRef(
            llm_execution_target_ref.entity_id, llm_execution_target_ref.version_id)),
        "request_resource_ref": _resource_payload(request_resource_ref),
        "terminal_delivery_ref": _ref_payload(terminal_delivery_ref),
        "budget_witness_ref": _ref_payload(budget_ref),
        "prior_attempt_ref": (
            _ref_payload(prior_attempt.ref) if prior_attempt else None),
        "llm_call_id": str(call.call_id),
        "llm_call_version_id": str(call.version_id),
        "invocation_id": str(call.invocation_id),
        "invocation_version_id": str(context.invocation_ref.version_id),
        "activation_id": (str(call.activation_id)
                          if call.activation_id is not None else None),
        "origin": context.origin,
        "accounting_parent_invocation_id": str(
            context.accounting_parent_invocation_ref.entity_id),
        "accounting_parent_invocation_version_id": str(
            context.accounting_parent_invocation_ref.version_id),
        "backend_config_version_id": str(llm_execution_target_ref.version_id),
        "budget_witness_version_id": str(budget_ref.version_id),
        "resource_contract_version_id": None,
        "backend": backend, "model": model,
        "transport_kind": transport_kind,
        "response_protocol": response_protocol,
        "timeout_seconds": int(timeout_seconds),
        "retry_cause": retry_cause,
        "prior_attempt_version_id": (
            str(prior_attempt.version_id) if prior_attempt else None),
        "reservation_class": reservation_class,
        "finalization_scope": finalization_scope,
        "writer_fencing_epoch": self.service.writer_epoch,
    }
    attempt = ProviderAttempt(
        attempt_id, version_id, call, reservation_class, finalization_scope)
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.prewrite(
        object_type="provider_attempt_spec/v1", logical_id=attempt_id,
        version_id=version_id, payload=canonical_json(metadata), metadata=metadata,
        media_type="application/json",
        schema_ref="registry_v1/provider_attempt_spec/v1",
        producer_invocation_id=call.invocation_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:attempt-of-call"),
        "attempt_of_call", attempt.ref, call.ref),
        producer_invocation_id=call.invocation_id)
    if prior_attempt is not None:
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:retry-of-attempt"),
            "retry_of_attempt", attempt.ref, prior_attempt.ref),
            producer_invocation_id=call.invocation_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:bound-to-backend"),
        "bound_to_backend", attempt.ref, llm_execution_target_ref),
        producer_invocation_id=call.invocation_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:consumes-resource"),
        "attempt_consumes_resource", attempt.ref,
        request_resource_ref.as_version_ref()),
        producer_invocation_id=call.invocation_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:uses-delivery"),
        "attempt_uses_delivery", attempt.ref, terminal_delivery_ref),
        producer_invocation_id=call.invocation_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:call-policy"),
        "governed_by_call_policy", attempt.ref, budget_ref,
        metadata={"partition": reservation_class,
                  "finalization_scope": finalization_scope}),
        producer_invocation_id=call.invocation_id)
    tx.append(PendingEvent(
        event_type="provider_attempt_reserved/v1", criticality="authoritative",
        stream_id=f"llm-call:{call.call_id}", aggregate_id=str(attempt_id),
        aggregate_type="provider_attempt", idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={"provider_attempt_id": str(attempt_id),
                 "provider_attempt_version_id": str(version_id),
                 "provider_attempt_ref": _ref_payload(attempt.ref),
                 "llm_call_ref": _ref_payload(call.ref),
                 "invocation_ref": _ref_payload(context.invocation_ref),
                 "operation_binding_ref": _ref_payload(
                     context.operation_binding_ref),
                 "llm_execution_target_ref": _resource_payload(ResourceVersionRef(
                     llm_execution_target_ref.entity_id,
                     llm_execution_target_ref.version_id)),
                 "request_resource_ref": _resource_payload(
                     request_resource_ref),
                 "terminal_delivery_ref": _ref_payload(
                     terminal_delivery_ref),
                 "budget_witness_ref": _ref_payload(budget_ref),
                 "prior_attempt_ref": (
                     _ref_payload(prior_attempt.ref)
                     if prior_attempt else None),
                 "reservation_class": reservation_class,
                 "finalization_scope": finalization_scope is not None},
        payload_schema_ref="registry_v1/provider_attempt_reserved/v1",
        task_control=True, producer_invocation_id=call.invocation_id))
    tx.commit()
    return attempt

def _historical_cancelled_before_submission(self, attempt: ProviderAttempt, *, reason: str,
                                idempotency_key: str) -> None:
    self._append(attempt, "provider_attempt_cancelled_before_submission/v1",
                 {"reason": reason}, idempotency_key=idempotency_key)

def _historical_failed(self, attempt: ProviderAttempt, *, failure: BaseException,
           retry_allowed: bool, idempotency_key: str) -> None:
    self._append(
        attempt, "provider_attempt_failed/v1",
        {"failure_class": type(failure).__name__,
         "retry_allowed": bool(retry_allowed)}, idempotency_key=idempotency_key)

def _historical_outcome_unknown(self, attempt: ProviderAttempt, *, fault: BaseException,
                    idempotency_key: str) -> None:
    del fault
    self._append(
        attempt, "provider_attempt_outcome_unknown/v1",
        {"state": "provider_response_outcome_indeterminate"},
        idempotency_key=idempotency_key)

def _historical_prove_not_submitted(self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
                        idempotency_key: str) -> None:
    self._require_proof(proof_ref)
    self._append(
        attempt, "provider_attempt_proven_not_submitted/v1",
        {"proof_resource_version_id": str(proof_ref.version_id)},
        idempotency_key=idempotency_key)

def _historical_prove_submitted(self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
                    idempotency_key: str) -> None:
    self._require_proof(proof_ref)
    self._append(
        attempt, "provider_attempt_proven_submitted/v1",
        {"proof_resource_version_id": str(proof_ref.version_id)},
        idempotency_key=idempotency_key)

def _historical_cancelled_after_submission(
        self, attempt: ProviderAttempt, *, reason: str, retry_allowed: bool,
        idempotency_key: str) -> None:
    self._append(
        attempt, "provider_attempt_cancelled_after_submission/v1",
        {"reason": reason, "retry_allowed": bool(retry_allowed)},
        idempotency_key=idempotency_key)

def _historical_reconcile_failed(
        self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
        retry_allowed: bool, idempotency_key: str) -> None:
    self._require_proof(proof_ref)
    self._append(
        attempt, "provider_attempt_reconciled_failed/v1",
        {"proof_resource_version_id": str(proof_ref.version_id),
         "retry_allowed": bool(retry_allowed)}, idempotency_key=idempotency_key)

def _historical_reconcile_cancelled_after_submission(
        self, attempt: ProviderAttempt, *, proof_ref: VersionRef,
        retry_allowed: bool, idempotency_key: str) -> None:
    self._require_proof(proof_ref)
    self._append(
        attempt, "provider_attempt_reconciled_cancelled_after_submission/v1",
        {"proof_resource_version_id": str(proof_ref.version_id),
         "retry_allowed": bool(retry_allowed)}, idempotency_key=idempotency_key)


def _require_proof(self, proof_ref: VersionRef) -> None:
    if proof_ref.entity_type != "resource_version/v1":
        raise ProviderAdmissionError("provider reconciliation proof must be a resource version")
    row = self.service.event_store.object_row(proof_ref.version_id)
    if (row is None or row["logical_id"] != str(proof_ref.entity_id)
            or row["object_type"] != proof_ref.entity_type):
        raise ProviderAdmissionError("provider reconciliation proof is not registered")

def _historical_adoption_event(
        self, attempt: ProviderAttempt, response_ref: VersionRef, *,
        selection_authority_ref: VersionRef,
        idempotency_key: str) -> PendingEvent:
    self._historical_revalidate_call_context(
        attempt.call, boundary="provider-candidate-adoption")
    if (not isinstance(selection_authority_ref, VersionRef)
            or selection_authority_ref.entity_type != "output_binding/v1"):
        raise ProviderAdmissionError(
            "provider adoption requires exact output-binding authority")
    selection_row = self.service.event_store.object_row(
        selection_authority_ref.version_id)
    call_row = self.service.event_store.object_row(attempt.call.version_id)
    if (selection_row is None
            or selection_row["object_type"] != "output_binding/v1"
            or selection_row["logical_id"]
            != str(selection_authority_ref.entity_id)
            or call_row is None
            or call_row["object_type"] != "llm_call_spec/v1"):
        raise ProviderAdmissionError(
            "provider selection authority is not an exact registered binding")
    selection = json.loads(selection_row["metadata_json"])
    call = json.loads(call_row["metadata_json"])
    invocation_row = self.service.event_store.object_row(
        attempt.call.invocation_ref.version_id)
    binding_row = self.service.event_store.object_row(
        attempt.call.operation_binding_ref.version_id)
    invocation = (json.loads(invocation_row["metadata_json"])
                  if invocation_row is not None else None)
    binding = (json.loads(binding_row["metadata_json"])
               if binding_row is not None else None)
    if (not isinstance(invocation, Mapping)
            or not isinstance(binding, Mapping)
            or selection.get("node_ref")
            != invocation.get("own_node_ref")
            or selection.get("net_ref")
            != invocation.get("net_instance_ref")
            or _ref_payload(selection_authority_ref)
            not in binding.get("output_binding_refs", [])
            or call.get("operation_binding_ref")
            != _ref_payload(attempt.call.operation_binding_ref)):
        raise ProviderAdmissionError(
            "provider selection binding differs from call/invocation authority")
    return PendingEvent(
        event_type="llm_call_result_adopted/v1", criticality="authoritative",
        stream_id=f"llm-call:{attempt.call.call_id}",
        aggregate_id=str(attempt.call.call_id), aggregate_type="llm_call",
        idempotency_key=idempotency_key, command_id=idempotency_key,
        payload={"llm_call_id": str(attempt.call.call_id),
                 "llm_call_version_id": str(attempt.call.version_id),
                 "provider_attempt_version_id": str(attempt.version_id),
                 "response_version_id": str(response_ref.version_id),
                 "selection_authority_ref": _ref_payload(
                     selection_authority_ref)},
        payload_schema_ref="registry_v1/llm_call_result_adopted/v1",
        task_control=True, producer_invocation_id=attempt.call.invocation_id)

def _historical_append_adoption(
        self, transaction: Any, attempt: ProviderAttempt,
        response_ref: VersionRef, *,
        selection_authority_ref: VersionRef,
        idempotency_key: str) -> None:
    """Validate and append one disposition to an owning transaction."""
    event = self._historical_adoption_event(
        attempt, response_ref,
        selection_authority_ref=selection_authority_ref,
        idempotency_key=idempotency_key)
    dispositions = tuple(
        self.service.event_store.list_events_by_aggregate(
            str(attempt.call.call_id),
            event_types=(
                "llm_call_result_adopted/v1",
                "llm_call_candidate_not_adopted/v1",
                "llm_call_failed/v1",
            )))
    if dispositions:
        raise ProviderAdmissionError(
            "provider call already has a conflicting disposition")
    transaction.append(event)

def _historical_adopt(self, attempt: ProviderAttempt, response_ref: VersionRef, *,
          selection_authority_ref: VersionRef,
          idempotency_key: str) -> EventEnvelope:
    event = self._historical_adoption_event(
        attempt, response_ref,
        selection_authority_ref=selection_authority_ref,
        idempotency_key=idempotency_key)
    payload = dict(event.payload)
    dispositions = tuple(
        self.service.event_store.list_events_by_aggregate(
            str(attempt.call.call_id),
            event_types=(
                "llm_call_result_adopted/v1",
                "llm_call_candidate_not_adopted/v1",
                "llm_call_failed/v1",
            )))
    if dispositions:
        if (len(dispositions) == 1
                and dispositions[0].event_type == event.event_type
                and dispositions[0].idempotency_key == idempotency_key
                and dict(dispositions[0].payload) == payload):
            return dispositions[0]
        raise ProviderAdmissionError(
            "provider call already has a conflicting disposition")
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.append(event)
    events = tx.commit()
    adopted = [event for event in events
               if event.event_type == "llm_call_result_adopted/v1"]
    if len(adopted) != 1:
        raise ProviderAdmissionError(
            "candidate adoption did not return one canonical disposition fact")
    return adopted[0]

def _historical_candidate_not_adopted_event(
        self, attempt: ProviderAttempt, response_ref: VersionRef, *, reason: str,
        idempotency_key: str) -> PendingEvent:
    if (not isinstance(attempt, ProviderAttempt)
            or not isinstance(response_ref, VersionRef)
            or response_ref.entity_type != "resource_version/v1"
            or reason not in {
                "superseded", "truncated", "hedge_loser",
                "transport_contract_rejected",
            }
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise ProviderAdmissionError(
            "candidate non-adoption requires exact closed inputs")
    self._historical_revalidate_call_context(
        attempt.call, boundary="provider-candidate-disposition")
    payload = {
        "llm_call_id": str(attempt.call.call_id),
        "llm_call_version_id": str(attempt.call.version_id),
        "provider_attempt_version_id": str(attempt.version_id),
        "response_version_id": str(response_ref.version_id),
        "reason": reason,
    }
    return PendingEvent(
        event_type="llm_call_candidate_not_adopted/v1",
        criticality="authoritative",
        stream_id=f"llm-call:{attempt.call.call_id}",
        aggregate_id=str(attempt.call.call_id), aggregate_type="llm_call",
        idempotency_key=idempotency_key, command_id=idempotency_key,
        payload=payload,
        payload_schema_ref="registry_v1/llm_call_candidate_not_adopted/v1",
        task_control=True, producer_invocation_id=attempt.call.invocation_id)

def _historical_append_candidate_not_adopted(
        self, transaction: Any, attempt: ProviderAttempt,
        response_ref: VersionRef, *, reason: str,
        idempotency_key: str) -> None:
    """Validate and append one rejection to an owning transaction."""
    event = self._historical_candidate_not_adopted_event(
        attempt, response_ref, reason=reason,
        idempotency_key=idempotency_key)
    dispositions = tuple(
        self.service.event_store.list_events_by_aggregate(
            str(attempt.call.call_id),
            event_types=(
                "llm_call_result_adopted/v1",
                "llm_call_candidate_not_adopted/v1",
                "llm_call_failed/v1",
            )))
    if dispositions:
        raise ProviderAdmissionError(
            "provider call already has a conflicting disposition")
    transaction.append(event)

def _historical_candidate_not_adopted(
        self, attempt: ProviderAttempt, response_ref: VersionRef, *, reason: str,
        idempotency_key: str) -> EventEnvelope:
    event = self._historical_candidate_not_adopted_event(
        attempt, response_ref, reason=reason,
        idempotency_key=idempotency_key)
    payload = dict(event.payload)
    dispositions = tuple(
        self.service.event_store.list_events_by_aggregate(
            str(attempt.call.call_id),
            event_types=(
                "llm_call_result_adopted/v1",
                "llm_call_candidate_not_adopted/v1",
                "llm_call_failed/v1",
            )))
    if dispositions:
        if (len(dispositions) == 1
                and dispositions[0].event_type == event.event_type
                and dispositions[0].idempotency_key == idempotency_key
                and dict(dispositions[0].payload) == payload):
            return dispositions[0]
        raise ProviderAdmissionError(
            "provider call already has a conflicting disposition")
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.append(event)
    events = tx.commit()
    rejected = tuple(
        event for event in events
        if event.event_type == "llm_call_candidate_not_adopted/v1")
    if (len(rejected) != 1
            or rejected[0].idempotency_key != idempotency_key
            or dict(rejected[0].payload) != payload):
        raise ProviderAdmissionError(
            "candidate non-adoption did not return one canonical fact")
    return rejected[0]

def _historical_fail_call(
        self, call: LLMCall, *, reason: str,
        idempotency_key: str) -> None:
    self._historical_revalidate_call_context(
        call, boundary="llm-call-failure")
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.append(PendingEvent(
        event_type="llm_call_failed/v1", criticality="authoritative",
        stream_id=f"llm-call:{call.call_id}", aggregate_id=str(call.call_id),
        aggregate_type="llm_call", idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={"llm_call_id": str(call.call_id),
                 "llm_call_version_id": str(call.version_id), "reason": reason},
        payload_schema_ref="registry_v1/llm_call_failed/v1", task_control=True,
        producer_invocation_id=call.invocation_id))
    tx.commit()
