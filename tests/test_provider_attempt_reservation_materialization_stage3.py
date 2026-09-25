from __future__ import annotations

from cpn.rpnh.registry._provider_calls.attempts import (
    materialize_attempt_v2_reservation,
)
from cpn.rpnh.registry.identities import IdKind, TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.provider_calls import _ref_payload, _resource_payload
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


def _materialize(
        *, prior_attempt_ref: VersionRef | None,
        prior_failure_class: str | None,
        finalization_scope: str | None,
):
    return materialize_attempt_v2_reservation(
        attempt_id=_id("provider_attempt", "1"),
        version_id=_id("provider_attempt_version", "2"),
        call_ref=_version(
            "llm_call_spec/v2", "llm_call", "llm_call_version", "3", "4"),
        invocation_ref=_version(
            "invocation/v1", "invocation", "invocation_version", "5", "6"),
        operation_binding_ref=_version(
            "operation_binding/v1", "operation_binding",
            "operation_binding_version", "7", "8"),
        llm_execution_target_ref=_resource("9", "a"),
        request_resource_ref=_resource("b", "c"),
        terminal_delivery_ref=_version(
            "resource_delivery/v1", "resource_delivery",
            "resource_delivery_version", "d", "e"),
        budget_witness_ref=_version(
            "task_recovery_manifest/v1", "task", "resource_version", "f", "0"),
        prior_attempt_ref=prior_attempt_ref,
        activation_ref=_version(
            "a2c_activation/v1", "a2c_activation",
            "a2c_activation_version", "1", "3"),
        origin="agent-loop",
        accounting_parent_invocation_ref=_version(
            "invocation/v1", "invocation", "invocation_version", "4", "5"),
        backend="registered-backend",
        model="exact-model",
        transport_kind="subprocess",
        response_protocol="response/v1",
        timeout_seconds=31,
        prior_failure_class=prior_failure_class,
        reservation_class="model_call",
        finalization_scope=finalization_scope,
        writer_fencing_epoch=17,
        ref_payload=_ref_payload,
        resource_payload=_resource_payload,
    )


def test_v2_attempt_reservation_materializes_exact_no_prior_payloads(
) -> None:
    publication = _materialize(
        prior_attempt_ref=None,
        prior_failure_class=None,
        finalization_scope=None,
    )
    attempt_ref = _version(
        "provider_attempt_spec/v1", "provider_attempt",
        "provider_attempt_version", "1", "2")
    call_ref = _version(
        "llm_call_spec/v2", "llm_call", "llm_call_version", "3", "4")
    invocation_ref = _version(
        "invocation/v1", "invocation", "invocation_version", "5", "6")
    binding_ref = _version(
        "operation_binding/v1", "operation_binding",
        "operation_binding_version", "7", "8")
    target_ref = _resource("9", "a")
    request_ref = _resource("b", "c")
    delivery_ref = _version(
        "resource_delivery/v1", "resource_delivery",
        "resource_delivery_version", "d", "e")
    budget_ref = _version(
        "task_recovery_manifest/v1", "task", "resource_version", "f", "0")

    assert publication.attempt_ref == attempt_ref
    assert publication.metadata == {
        "provider_attempt_id": str(attempt_ref.entity_id),
        "provider_attempt_version_id": str(attempt_ref.version_id),
        "provider_attempt_ref": _ref_payload(attempt_ref),
        "llm_call_ref": _ref_payload(call_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_binding_ref": _ref_payload(binding_ref),
        "llm_execution_target_ref": _resource_payload(target_ref),
        "request_resource_ref": _resource_payload(request_ref),
        "terminal_delivery_ref": _ref_payload(delivery_ref),
        "budget_witness_ref": _ref_payload(budget_ref),
        "prior_attempt_ref": None,
        "llm_call_id": str(call_ref.entity_id),
        "llm_call_version_id": str(call_ref.version_id),
        "invocation_id": str(invocation_ref.entity_id),
        "invocation_version_id": str(invocation_ref.version_id),
        "activation_id": str(_id("a2c_activation", "1")),
        "origin": "agent-loop",
        "accounting_parent_invocation_id": str(_id("invocation", "4")),
        "accounting_parent_invocation_version_id": str(
            _id("invocation_version", "5")),
        "backend_config_version_id": str(
            target_ref.resource_version_id),
        "budget_witness_version_id": str(budget_ref.version_id),
        "resource_contract_version_id": None,
        "backend": "registered-backend",
        "model": "exact-model",
        "transport_kind": "subprocess",
        "response_protocol": "response/v1",
        "timeout_seconds": 31,
        "retry_cause": None,
        "prior_attempt_version_id": None,
        "reservation_class": "model_call",
        "finalization_scope": None,
        "writer_fencing_epoch": 17,
    }
    assert publication.event_payload == {
        "provider_attempt_id": str(attempt_ref.entity_id),
        "provider_attempt_version_id": str(attempt_ref.version_id),
        "provider_attempt_ref": _ref_payload(attempt_ref),
        "llm_call_ref": _ref_payload(call_ref),
        "invocation_ref": _ref_payload(invocation_ref),
        "operation_binding_ref": _ref_payload(binding_ref),
        "llm_execution_target_ref": _resource_payload(target_ref),
        "request_resource_ref": _resource_payload(request_ref),
        "terminal_delivery_ref": _ref_payload(delivery_ref),
        "budget_witness_ref": _ref_payload(budget_ref),
        "prior_attempt_ref": None,
        "reservation_class": "model_call",
        "finalization_scope": False,
    }


def test_v2_attempt_reservation_materializes_exact_retry_predecessor(
) -> None:
    prior_ref = _version(
        "provider_attempt_spec/v1", "provider_attempt",
        "provider_attempt_version", "6", "7")

    publication = _materialize(
        prior_attempt_ref=prior_ref,
        prior_failure_class="http_status",
        finalization_scope="invocation",
    )

    assert publication.metadata["prior_attempt_ref"] == _ref_payload(prior_ref)
    assert publication.metadata["prior_attempt_version_id"] == str(
        prior_ref.version_id)
    assert publication.metadata["retry_cause"] == "http_status"
    assert publication.metadata["finalization_scope"] == "invocation"
    assert publication.event_payload["prior_attempt_ref"] == _ref_payload(
        prior_ref)
    assert publication.event_payload["finalization_scope"] is True
    assert set(publication.event_payload) == {
        "provider_attempt_id",
        "provider_attempt_version_id",
        "provider_attempt_ref",
        "llm_call_ref",
        "invocation_ref",
        "operation_binding_ref",
        "llm_execution_target_ref",
        "request_resource_ref",
        "terminal_delivery_ref",
        "budget_witness_ref",
        "prior_attempt_ref",
        "reservation_class",
        "finalization_scope",
    }
