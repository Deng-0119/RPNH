"""Current-v2 logical provider-call validation and publication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..content_schemas import hydrate_registered_content_schema
from ..identities import TypedId
from ..invocations import InvocationContext, InvocationLifecycle, _stable_id
from ..models import TypedRelation, VersionRef
from ..publication import (
    _NO_CONTENT_SCHEMA_INSTANCE,
    _content_schema_instance,
    _content_schema_source_from_payload,
    _version_from_payload,
)
from ..resource_service import _ResourceServiceKernel
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
RefPayload = Callable[[VersionRef], dict[str, str]]
ResourcePayload = Callable[[ResourceVersionRef], dict[str, str]]


@dataclass(frozen=True, slots=True)
class CallV2Publication:
    """Transaction-free object material for one v2 call."""

    call_ref: VersionRef
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CallV3Publication:
    """Transaction-free object material for one registered-HOST call."""

    call_ref: VersionRef
    metadata: Mapping[str, Any]


def materialize_call_v2_publication(
        *, call_id: TypedId, version_id: TypedId,
        invocation_ref: VersionRef, operation_binding_ref: VersionRef,
        loop_payload: Mapping[str, str], turn_sequence: int,
        request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        semantic_prompt_resource_ref: ResourceVersionRef,
        llm_execution_target_ref: ResourceVersionRef,
        backend: str, model: str,
        transport_contract_ref: ResourceVersionRef,
        interaction_protocol_ref: str, response_adapter_ref: str,
        tool_catalog_ref: ResourceVersionRef,
        prior_turn_payloads: tuple[Mapping[str, str], ...],
        timeout_seconds: int, max_response_bytes: int,
        ref_payload: RefPayload,
        resource_payload: ResourcePayload,
) -> CallV2Publication:
    """Build the exact durable object payload without Registry access."""
    call_ref = VersionRef("llm_call_spec/v2", call_id, version_id)
    metadata = {
        "llm_call_id": str(call_id),
        "llm_call_version_id": str(version_id),
        "llm_call_ref": ref_payload(call_ref),
        "invocation_ref": ref_payload(invocation_ref),
        "operation_binding_ref": ref_payload(operation_binding_ref),
        "agent_loop_ref": loop_payload,
        "turn_sequence": turn_sequence,
        "request_resource_ref": resource_payload(request_resource_ref),
        "terminal_delivery_ref": ref_payload(terminal_delivery_ref),
        "semantic_prompt_resource_ref": resource_payload(
            semantic_prompt_resource_ref),
        "llm_execution_target_ref": resource_payload(
            llm_execution_target_ref),
        "backend": backend,
        "model": model,
        "transport_contract_ref": resource_payload(transport_contract_ref),
        "interaction_protocol_ref": interaction_protocol_ref,
        "response_adapter_ref": response_adapter_ref,
        "tool_catalog_ref": resource_payload(tool_catalog_ref),
        "prior_turn_refs": list(prior_turn_payloads),
        "timeout_seconds": timeout_seconds,
        "max_response_bytes": max_response_bytes,
    }
    return CallV2Publication(call_ref, metadata)


def materialize_call_v3_publication(
        *, call_id: TypedId, version_id: TypedId,
        invocation_ref: VersionRef, operation_binding_ref: VersionRef,
        request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        semantic_prompt_resource_ref: ResourceVersionRef,
        llm_input_target_ref: ResourceVersionRef,
        llm_execution_target_ref: ResourceVersionRef,
        backend: str, model: str,
        transport_contract_ref: ResourceVersionRef,
        interaction_protocol_ref: str, response_adapter_ref: str,
        tool_catalog_ref: ResourceVersionRef,
        timeout_seconds: int, max_response_bytes: int,
        ref_payload: RefPayload,
        resource_payload: ResourcePayload,
) -> CallV3Publication:
    call_ref = VersionRef("llm_call_spec/v3", call_id, version_id)
    metadata = {
        "llm_call_id": str(call_id),
        "llm_call_version_id": str(version_id),
        "llm_call_ref": ref_payload(call_ref),
        "invocation_kind": "registered_host",
        "invocation_ref": ref_payload(invocation_ref),
        "operation_binding_ref": ref_payload(operation_binding_ref),
        "request_resource_ref": resource_payload(request_resource_ref),
        "terminal_delivery_ref": ref_payload(terminal_delivery_ref),
        "semantic_prompt_resource_ref": resource_payload(
            semantic_prompt_resource_ref),
        "llm_input_target_ref": resource_payload(llm_input_target_ref),
        "llm_execution_target_ref": resource_payload(
            llm_execution_target_ref),
        "backend": backend,
        "model": model,
        "transport_contract_ref": resource_payload(transport_contract_ref),
        "interaction_protocol_ref": interaction_protocol_ref,
        "response_adapter_ref": response_adapter_ref,
        "tool_catalog_ref": resource_payload(tool_catalog_ref),
        "timeout_seconds": timeout_seconds,
        "max_response_bytes": max_response_bytes,
    }
    return CallV3Publication(call_ref, metadata)


def create_call_v3(
        self, *, context: InvocationContext,
        request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        semantic_prompt_resource_ref: ResourceVersionRef,
        llm_input_target_ref: ResourceVersionRef,
        llm_execution_target_ref: ResourceVersionRef, backend: str,
        model: str, transport_contract_ref: ResourceVersionRef,
        interaction_protocol_ref: str, response_adapter_ref: str,
        tool_catalog_ref: ResourceVersionRef, timeout_seconds: int,
        max_response_bytes: int, call_id: TypedId | None = None,
        idempotency_key: str = ""):
    """Publish a loop-free call from exact registered-HOST resources."""
    from ..provider_calls import (
        LLMCallV3, ProviderAdmissionError, _ref_payload, _resource_payload,
    )

    InvocationLifecycle(self.service).revalidate_io(
        context, boundary="registered-host-llm-call-creation")
    refs = (
        request_resource_ref, semantic_prompt_resource_ref,
        llm_input_target_ref, llm_execution_target_ref,
        transport_contract_ref, tool_catalog_ref,
    )
    if (any(not isinstance(ref, ResourceVersionRef) for ref in refs)
            or not isinstance(terminal_delivery_ref, VersionRef)
            or terminal_delivery_ref.entity_type != "resource_delivery/v1"):
        raise ProviderAdmissionError(
            "v3 call requires exact request/prompt/config/contract/catalog refs")
    if (isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int) or timeout_seconds < 1
            or isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes < 1):
        raise ProviderAdmissionError("v3 call limits are invalid")
    for value in (
            backend, model, interaction_protocol_ref,
            response_adapter_ref, idempotency_key):
        if not isinstance(value, str) or not value or value != value.strip():
            raise ProviderAdmissionError("v3 call identity is not exact")

    kernel = _ResourceServiceKernel(self.service)
    binding = kernel._exact_object(
        context.operation_binding_ref,
        expected_type="operation_binding/v1").metadata
    spec = kernel._exact_object(
        _version_from_payload(binding["operation_spec_ref"]),
        expected_type="operation_spec/v1").metadata
    contracts = spec.get("implementation_contracts", {})
    if (contracts.get("host_protocols") != ["registered_llm/v1"]
            or contracts.get("provider_request_schema")
            != "runtime/llm_request_envelope/v1"):
        raise ProviderAdmissionError(
            "v3 call requires the exact registered HOST LLM protocol")
    if (binding.get("llm_input_target_ref")
            != _resource_payload(llm_input_target_ref)):
        raise ProviderAdmissionError(
            "v3 call target differs from the firing host binding")
    readable = tuple(binding.get("readable_resource_refs", ()))
    for ref in refs[1:]:
        if (_ref_payload(ref.as_version_ref()) not in readable
                or not kernel._binding_allows(
                    context, context.operation_binding_ref, ref,
                    metadata_only=False)):
            raise ProviderAdmissionError(
                "v3 call resource lacks its exact firing read grant")
    if not kernel._binding_allows(
            context, context.operation_binding_ref, request_resource_ref,
            metadata_only=False):
        raise ProviderAdmissionError(
            "v3 request resource lacks its firing read contract")

    request = kernel._firing_prepared(context, request_resource_ref)
    prompt = kernel._firing_prepared(context, semantic_prompt_resource_ref)
    target = kernel._firing_prepared(context, llm_input_target_ref)
    route = kernel._firing_prepared(context, llm_execution_target_ref)
    transport = kernel._firing_prepared(context, transport_contract_ref)
    catalog = kernel._firing_prepared(context, tool_catalog_ref)
    delivery = kernel._exact_object(
        terminal_delivery_ref, expected_type="resource_delivery/v1")
    request_document = json.loads(
        self.service.object_store.read_registered(request))
    target_document = json.loads(
        self.service.object_store.read_registered(target))
    route_document = json.loads(
        self.service.object_store.read_registered(route))
    transport_document = json.loads(
        self.service.object_store.read_registered(transport))
    catalog_document = json.loads(
        self.service.object_store.read_registered(catalog))
    request_schema = self._provider_request_schema(context)
    self.service.catalog.validate_schema_ref(request_schema, request_document)
    self.service.catalog.validate_schema_ref(
        "registry_v1/llm_input_target/v1", target_document)
    if (request.metadata.get("content_schema_ref") != request_schema
            or target.metadata.get("content_schema_ref")
            != "registry_v1/llm_input_target/v1"
            or request_document.get("model_condition") != model
            or request_document.get("max_output_tokens")
            != target_document.get("max_output_tokens")
            or request_document.get("source_prompt_ref")
            != _resource_payload(semantic_prompt_resource_ref)
            or request_document.get("tool_catalog_ref")
            != _resource_payload(tool_catalog_ref)
            or request_document.get("tools")
            != catalog_document.get("tools")
            or target_document.get("model_condition") != model
            or max_response_bytes != target_document.get(
                "max_response_bytes")
            or route_document.get("backend") != backend
            or route_document.get("model") != model
            or route_document.get("timeout_seconds") != timeout_seconds
            or transport_document != {
                "interaction_protocol_ref": interaction_protocol_ref,
                "response_adapter_ref": response_adapter_ref,
            }
            or delivery.metadata.get("state") != "acknowledged"
            or delivery.metadata.get("boundary") != "llm_prompt"
            or delivery.metadata.get("resource_ref")
            != _resource_payload(request_resource_ref)
            or delivery.metadata.get("context_ref")
            != _ref_payload(context.invocation_ref)
            or delivery.metadata.get("authorization_ref")
            != _ref_payload(context.operation_binding_ref)):
        raise ProviderAdmissionError(
            "v3 route/model/request/delivery closure is not exact")
    del prompt

    call_id = call_id or _stable_id("llm_call", idempotency_key)
    version_id = _stable_id("llm_call_version", idempotency_key)
    publication = materialize_call_v3_publication(
        call_id=call_id, version_id=version_id,
        invocation_ref=context.invocation_ref,
        operation_binding_ref=context.operation_binding_ref,
        request_resource_ref=request_resource_ref,
        terminal_delivery_ref=terminal_delivery_ref,
        semantic_prompt_resource_ref=semantic_prompt_resource_ref,
        llm_input_target_ref=llm_input_target_ref,
        llm_execution_target_ref=llm_execution_target_ref,
        backend=backend, model=model,
        transport_contract_ref=transport_contract_ref,
        interaction_protocol_ref=interaction_protocol_ref,
        response_adapter_ref=response_adapter_ref,
        tool_catalog_ref=tool_catalog_ref,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        ref_payload=_ref_payload, resource_payload=_resource_payload)
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.prewrite(
        object_type=publication.call_ref.entity_type,
        logical_id=call_id, version_id=version_id,
        payload=canonical_json(publication.metadata),
        metadata=publication.metadata, media_type="application/json",
        schema_ref="registry_v1/llm_call_spec/v3",
        producer_invocation_id=context.invocation_ref.entity_id)
    relations = (
        ("call_of_invocation", context.invocation_ref,
         "call-of-invocation"),
        ("bound_to_backend", llm_execution_target_ref.as_version_ref(),
         "uses-backend"),
        ("uses_transport_contract", transport_contract_ref.as_version_ref(),
         "uses-transport"),
        ("uses_tool_catalog", tool_catalog_ref.as_version_ref(),
         "uses-tool-catalog"),
        ("uses_semantic_prompt", semantic_prompt_resource_ref.as_version_ref(),
         "uses-prompt"),
    )
    for relation_type, target_ref, suffix in relations:
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:{suffix}"),
            relation_type, publication.call_ref, target_ref),
            producer_invocation_id=context.invocation_ref.entity_id)
    tx.commit()
    return LLMCallV3(
        call_id, version_id, context.invocation_ref,
        context.operation_binding_ref, request_resource_ref,
        terminal_delivery_ref, semantic_prompt_resource_ref,
        llm_input_target_ref, llm_execution_target_ref, backend, model,
        transport_contract_ref, interaction_protocol_ref,
        response_adapter_ref, tool_catalog_ref,
        timeout_seconds, max_response_bytes)

def create_call_v2(
        self, *, context: InvocationContext,
        loop_ref: Mapping[str, str], turn_sequence: int,
        request_resource_ref: ResourceVersionRef,
        terminal_delivery_ref: VersionRef,
        semantic_prompt_resource_ref: ResourceVersionRef,
        llm_execution_target_ref: ResourceVersionRef, backend: str, model: str,
        transport_contract_ref: ResourceVersionRef,
        interaction_protocol_ref: str,
        response_adapter_ref: str,
        tool_catalog_ref: ResourceVersionRef,
        prior_turn_refs: tuple[VersionRef, ...],
        timeout_seconds: int, max_response_bytes: int,
        call_id: TypedId | None = None,
        idempotency_key: str) -> LLMCallV2:
    """Publish a v2 logical call without any application output schema.

    Catalog registration is intentionally supplied by the serialized shared
    owner.  Once registered, this method publishes the exact provider/model,
    transport, interaction, catalog, prompt and prior-turn lineage.  It does
    not normalize a response or name a response contract.
    """
    from ..provider_calls import (
        LLMCallV2, ProviderAdmissionError, _ref_payload, _resource_payload,
    )
    InvocationLifecycle(self.service).revalidate_io(
        context, boundary="agent-llm-call-creation")
    request_schema = self._provider_request_schema(context)
    if (not isinstance(request_resource_ref, ResourceVersionRef)
            or not isinstance(terminal_delivery_ref, VersionRef)
            or terminal_delivery_ref.entity_type != "resource_delivery/v1"
            or not isinstance(
                semantic_prompt_resource_ref, ResourceVersionRef)
            or not isinstance(llm_execution_target_ref, ResourceVersionRef)
            or not isinstance(transport_contract_ref, ResourceVersionRef)
            or not isinstance(tool_catalog_ref, ResourceVersionRef)):
        raise ProviderAdmissionError(
            "v2 call requires exact request/prompt/config/contract/catalog refs")
    if (isinstance(turn_sequence, bool)
            or not isinstance(turn_sequence, int) or turn_sequence < 0
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int) or timeout_seconds < 1
            or isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or max_response_bytes < 1):
        raise ProviderAdmissionError("v2 call limits or turn are invalid")
    for value in (backend, model, interaction_protocol_ref,
                  response_adapter_ref,
                  idempotency_key):
        if not isinstance(value, str) or not value or value != value.strip():
            raise ProviderAdmissionError("v2 call identity is not exact")
    from ..resource_service import _ResourceServiceKernel
    from ..publication import _version_from_payload
    kernel = _ResourceServiceKernel(self.service)
    firing_ref = context.own_transition_firing_ref
    view = self.service.event_store.firing_view(
        firing_version_id=firing_ref.version_id,
        invocation_version_id=context.invocation_ref.version_id)
    if (not isinstance(loop_ref, Mapping)
            or set(loop_ref) != {"entity_type", "logical_id", "version_id"}):
        raise ProviderAdmissionError("provider session requires one closed exact ref")
    session_ref = _version_from_payload(loop_ref)
    loop_payload = _ref_payload(session_ref)

    def member_authority(ref):
        exact = kernel._exact_object_for_view(view, ref)
        facts = exact.metadata
        if ref.entity_type == "resource_version/v1":
            resource = ResourceVersionRef(ref.entity_id, ref.version_id)
            kernel._firing_prepared(context, resource, view=view)
            if not kernel._binding_allows(context, context.operation_binding_ref,
                                          resource, metadata_only=False):
                raise ProviderAdmissionError("provider session resource has no exact read grant")
            provenance = facts.get("reference_provenance", {})
            invocation = facts.get("producer_ref")
            binding = provenance.get("operation_binding_ref")
        elif isinstance(facts.get("llm_call_ref"), Mapping):
            parent = kernel._exact_object_for_view(
                view, _version_from_payload(facts["llm_call_ref"]),
                expected_type="llm_call_spec/v2").metadata
            invocation, binding = parent["invocation_ref"], parent["operation_binding_ref"]
        else:
            invocation, binding = facts.get("invocation_ref"), facts.get("operation_binding_ref")
        if (invocation != _ref_payload(context.invocation_ref)
                or binding != _ref_payload(context.operation_binding_ref)):
            raise ProviderAdmissionError("provider session/prior belongs to another invocation or binding")
        return facts

    member_authority(session_ref)
    prior_refs = tuple(prior_turn_refs)
    if any(not isinstance(ref, VersionRef) for ref in prior_refs):
        raise ProviderAdmissionError(
            "v2 call prior entries require exact registered refs")
    if len(prior_refs) != turn_sequence:
        raise ProviderAdmissionError(
            "v2 call prior turns must be one contiguous complete prefix")
    for index, ref in enumerate(prior_refs):
        facts = member_authority(ref)
        if "sequence" in facts and facts["sequence"] != index:
            raise ProviderAdmissionError("provider prior prefix differs from its declared sequence")
        parent_session = facts.get("agent_loop_ref")
        if isinstance(parent_session, Mapping):
            prior_session = _version_from_payload(parent_session)
            member_authority(prior_session)
            if (prior_session.entity_type != session_ref.entity_type
                    or prior_session.entity_id != session_ref.entity_id):
                raise ProviderAdmissionError("provider prior prefix belongs to another exact session")
    prior_payloads = tuple(_ref_payload(ref) for ref in prior_refs)
    request_row = self.service.event_store.object_row(
        request_resource_ref.resource_version_id)
    delivery_row = self.service.event_store.object_row(
        terminal_delivery_ref.version_id)
    if request_row is None or delivery_row is None:
        raise ProviderAdmissionError(
            "v2 call prompt resource/delivery is not registered")
    request = json.loads(str(request_row["metadata_json"]))
    delivery = json.loads(str(delivery_row["metadata_json"]))
    if (request_row["object_type"] != "resource_version/v1"
            or request_row["logical_id"] != str(request_resource_ref.resource_id)
            or request.get("content_schema_ref")
            != request_schema
            or delivery_row["object_type"] != "resource_delivery/v1"
            or delivery_row["logical_id"]
            != str(terminal_delivery_ref.entity_id)
            or delivery.get("state") != "acknowledged"
            or delivery.get("boundary") != "llm_prompt"
            or delivery.get("resource_ref")
            != _resource_payload(request_resource_ref)
            or delivery.get("context_ref")
            != _ref_payload(context.invocation_ref)
            or delivery.get("authorization_ref")
            != _ref_payload(context.operation_binding_ref)):
        raise ProviderAdmissionError(
            "v2 call prompt differs from exact acknowledged delivery")
    config_row = self.service.event_store.object_row(
        llm_execution_target_ref.resource_version_id)
    contract_row = self.service.event_store.object_row(
        transport_contract_ref.resource_version_id)
    catalog_row = self.service.event_store.object_row(
        tool_catalog_ref.resource_version_id)
    prompt_row = self.service.event_store.object_row(
        semantic_prompt_resource_ref.resource_version_id)
    if any(row is None for row in (
            config_row, contract_row, catalog_row, prompt_row)):
        raise ProviderAdmissionError(
            "v2 call prompt/config/contract/catalog is not registered")
    exact_resources = (
        (config_row, llm_execution_target_ref),
        (contract_row, transport_contract_ref),
        (catalog_row, tool_catalog_ref),
        (prompt_row, semantic_prompt_resource_ref),
    )
    if any(row["object_type"] != "resource_version/v1"
           or row["logical_id"] != str(ref.resource_id)
           for row, ref in exact_resources):
        raise ProviderAdmissionError(
            "v2 call exact resource reference differs from Registry")
    from ..resource_service import _ResourceServiceKernel
    from ..publication import _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE
    from ..publication import _content_schema_source_from_payload
    from ..content_schemas import hydrate_registered_content_schema
    prepared_request = kernel._firing_prepared(context, request_resource_ref)
    schema_source = _content_schema_source_from_payload(
        prepared_request.metadata["content_schema_authority_ref"])
    if isinstance(schema_source, ResourceVersionRef):
        authority = self.service.verify_registered_content_schema_ref(
            schema_source, schema_document_ref=schema_source.as_version_ref())
    elif isinstance(schema_source, VersionRef):
        authority = hydrate_registered_content_schema(
            self.service, schema_source, schema_id=request_schema)
    else:
        raise ProviderAdmissionError("provider request has no exact registered schema source")
    if authority.schema_id != request_schema:
        raise ProviderAdmissionError("provider request schema source differs from executor contract")
    request_instance = _content_schema_instance(
        self.service.object_store.read_registered(prepared_request),
        media_type=prepared_request.media_type)
    if request_instance is not _NO_CONTENT_SCHEMA_INSTANCE:
        self.service.catalog.validate_schema_ref(request_schema, request_instance)
    invocation_ref = context.invocation_ref
    call_id = call_id or _stable_id("llm_call", idempotency_key)
    version_id = _stable_id("llm_call_version", idempotency_key)
    publication = materialize_call_v2_publication(
        call_id=call_id, version_id=version_id,
        invocation_ref=invocation_ref,
        operation_binding_ref=context.operation_binding_ref,
        loop_payload=loop_payload, turn_sequence=turn_sequence,
        request_resource_ref=request_resource_ref,
        terminal_delivery_ref=terminal_delivery_ref,
        semantic_prompt_resource_ref=semantic_prompt_resource_ref,
        llm_execution_target_ref=llm_execution_target_ref,
        backend=backend, model=model,
        transport_contract_ref=transport_contract_ref,
        interaction_protocol_ref=interaction_protocol_ref,
        response_adapter_ref=response_adapter_ref,
        tool_catalog_ref=tool_catalog_ref,
        prior_turn_payloads=prior_payloads,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        ref_payload=_ref_payload,
        resource_payload=_resource_payload,
    )
    tx = self.service.begin(idempotency_key=idempotency_key)
    tx.prewrite(
        object_type="llm_call_spec/v2", logical_id=call_id,
        version_id=version_id, payload=canonical_json(publication.metadata),
        metadata=publication.metadata, media_type="application/json",
        schema_ref="registry_v1/llm_call_spec/v2",
        producer_invocation_id=invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:call-of-invocation"),
        "call_of_invocation", publication.call_ref, invocation_ref),
        producer_invocation_id=invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:uses-backend"),
        "bound_to_backend", publication.call_ref,
        llm_execution_target_ref.as_version_ref()),
        producer_invocation_id=invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:uses-transport"),
        "uses_transport_contract", publication.call_ref,
        transport_contract_ref.as_version_ref()),
        producer_invocation_id=invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:uses-tool-catalog"),
        "uses_tool_catalog", publication.call_ref,
        tool_catalog_ref.as_version_ref()),
        producer_invocation_id=invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:uses-prompt"),
        "uses_semantic_prompt", publication.call_ref,
        semantic_prompt_resource_ref.as_version_ref()),
        producer_invocation_id=invocation_ref.entity_id)
    tx.commit()
    return LLMCallV2(
        call_id, version_id, invocation_ref,
        context.operation_binding_ref, loop_payload, turn_sequence,
        request_resource_ref, terminal_delivery_ref,
        semantic_prompt_resource_ref, llm_execution_target_ref, backend, model,
        transport_contract_ref, interaction_protocol_ref,
        response_adapter_ref, tool_catalog_ref, prior_refs,
        timeout_seconds, max_response_bytes)
