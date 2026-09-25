from __future__ import annotations

from cpn.rpnh.registry._provider_calls.calls import (
    materialize_call_v2_publication,
)
from cpn.rpnh.registry.identities import IdKind, TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.provider_calls import (
    LLMCallV2,
    ProviderAttemptLedger,
    ProviderAttemptV2,
    _ref_payload,
    _resource_payload,
)
from cpn.rpnh.registry.resources import ResourceVersionRef


def _id(kind: IdKind, digit: str) -> TypedId:
    return TypedId(kind, digit * 32)


def _version(
        entity_type: str, logical_kind: IdKind, version_kind: IdKind,
        logical_digit: str, version_digit: str,
) -> VersionRef:
    return VersionRef(
        entity_type,
        _id(logical_kind, logical_digit),
        _id(version_kind, version_digit),
    )


def _resource(logical_digit: str, version_digit: str) -> ResourceVersionRef:
    return ResourceVersionRef(
        _id("resource", logical_digit),
        _id("resource_version", version_digit),
    )


def test_v2_call_publication_materializes_exact_ledger_payload(
) -> None:
    call_id = _id("llm_call", "1")
    call_version_id = _id("llm_call_version", "2")
    invocation_ref = _version(
        "invocation/v1", "invocation", "invocation_version", "3", "4")
    binding_ref = _version(
        "operation_binding/v1", "operation_binding",
        "operation_binding_version", "5", "6")
    loop_ref = _version(
        "agent_loop/v1", "agent_loop", "agent_loop_version", "7", "8")
    delivery_ref = _version(
        "resource_delivery/v1", "resource_delivery",
        "resource_delivery_version", "9", "a")
    prior_ref = _version(
        "agent_turn/v1", "agent_turn", "agent_turn_version", "b", "c")
    request_ref = _resource("d", "e")
    semantic_prompt_ref = _resource("0", "1")
    execution_target_ref = _resource("2", "3")
    transport_contract_ref = _resource("4", "5")
    tool_catalog_ref = _resource("6", "7")

    publication = materialize_call_v2_publication(
        call_id=call_id,
        version_id=call_version_id,
        invocation_ref=invocation_ref,
        operation_binding_ref=binding_ref,
        loop_payload=_ref_payload(loop_ref),
        turn_sequence=1,
        request_resource_ref=request_ref,
        terminal_delivery_ref=delivery_ref,
        semantic_prompt_resource_ref=semantic_prompt_ref,
        llm_execution_target_ref=execution_target_ref,
        backend="registered-backend",
        model="exact-model",
        transport_contract_ref=transport_contract_ref,
        interaction_protocol_ref="interaction/v1",
        response_adapter_ref="adapter/v1",
        tool_catalog_ref=tool_catalog_ref,
        prior_turn_payloads=(_ref_payload(prior_ref),),
        timeout_seconds=31,
        max_response_bytes=4096,
        ref_payload=_ref_payload,
        resource_payload=_resource_payload,
    )

    call_ref = VersionRef("llm_call_spec/v2", call_id, call_version_id)
    assert publication.call_ref == call_ref
    assert publication.metadata == {
        "llm_call_id": str(call_id),
        "llm_call_version_id": str(call_version_id),
        "llm_call_ref": _ref_payload(call_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_binding_ref": _ref_payload(binding_ref),
        "agent_loop_ref": _ref_payload(loop_ref),
        "turn_sequence": 1,
        "request_resource_ref": _resource_payload(request_ref),
        "terminal_delivery_ref": _ref_payload(delivery_ref),
        "semantic_prompt_resource_ref": _resource_payload(semantic_prompt_ref),
        "llm_execution_target_ref": _resource_payload(execution_target_ref),
        "backend": "registered-backend",
        "model": "exact-model",
        "transport_contract_ref": _resource_payload(transport_contract_ref),
        "interaction_protocol_ref": "interaction/v1",
        "response_adapter_ref": "adapter/v1",
        "tool_catalog_ref": _resource_payload(tool_catalog_ref),
        "prior_turn_refs": [_ref_payload(prior_ref)],
        "timeout_seconds": 31,
        "max_response_bytes": 4096,
    }
    assert _resource_payload(request_ref) == {
        "resource_id": str(request_ref.resource_id),
        "resource_version_id": str(request_ref.resource_version_id),
    }
    assert LLMCallV2.__module__ == "cpn.rpnh.registry.provider_calls"
    assert ProviderAttemptV2.__module__ == "cpn.rpnh.registry.provider_calls"
    assert ProviderAttemptLedger.__module__ == "cpn.rpnh.registry.provider_calls"
