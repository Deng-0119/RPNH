"""Canonical producer and provider provenance closure."""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable, Mapping

from ..event_store import (
    CanonicalAttemptResponseViolation,
    CanonicalProducerViolation,
    CanonicalProviderLineageViolation,
    RegistryAuthorityView,
    RegistryCorruptError,
    _ref_json,
)
from ..identities import TypedId
from ..schema_catalog import canonical_text

_CANONICAL_PROVIDER_LINEAGES = {
    "attempt_of_call": (
        "provider_attempt_spec/v1", "llm_call_spec/v1", "attempt-of-call"),
    "call_of_invocation": (
        "llm_call_spec/v1", "invocation/v1", "call-of-invocation"),
}

_CANONICAL_PROVIDER_LINEAGE_ENDPOINT_TYPES = {
    "attempt_of_call": (
        frozenset({"provider_attempt_spec/v1", "provider_attempt_spec/v2"}),
        frozenset({
            "llm_call_spec/v1", "llm_call_spec/v2", "llm_call_spec/v3"}),
    ),
    "call_of_invocation": (
        frozenset({
            "llm_call_spec/v1", "llm_call_spec/v2", "llm_call_spec/v3"}),
        frozenset({"invocation/v1"}),
    ),
}

def _canonical_provider_lineage_endpoint_types(
        relation_type: str, source_ref: Mapping[str, Any],
        target_ref: Mapping[str, Any]) -> tuple[str, str, str]:
    """Resolve only supported exact endpoint types named by typed refs."""

    contract = _CANONICAL_PROVIDER_LINEAGES.get(relation_type)
    allowed = _CANONICAL_PROVIDER_LINEAGE_ENDPOINT_TYPES.get(relation_type)
    if contract is None or allowed is None:
        raise CanonicalProviderLineageViolation(
            f"unsupported canonical provider lineage: {relation_type}")
    source_type = str(source_ref.get("entity_type", ""))
    target_type = str(target_ref.get("entity_type", ""))
    if source_type not in allowed[0] or target_type not in allowed[1]:
        raise CanonicalProviderLineageViolation(
            f"{relation_type} names unsupported endpoint types")
    return source_type, target_type, contract[2]

def _stable_id_text(kind: str, *parts: object) -> str:
    material = ":".join(str(part) for part in parts)
    return str(TypedId(
        kind,
        uuid.uuid5(uuid.NAMESPACE_URL, f"d1-c:{kind}:{material}").hex,
    ))

def canonical_provider_lineage_record(
        relation_type: str, source_ref: Mapping[str, Any],
        target_ref: Mapping[str, Any], *, publication_transaction: str,
        publication_key: str, source_metadata: Mapping[str, Any],
        target_metadata: Mapping[str, Any],
        relation_records: Iterable[Mapping[str, Any]],
        registered_exact_refs: Iterable[tuple[str, str, str]],
        ) -> Mapping[str, Any]:
    """Return the canonical Registry authority for one provider lineage edge.

    The generic typed-relation schema intentionally supports weaker relation
    contracts.  P-A's attempt/call chain is narrower: its two endpoints are
    registered exact versions and the source publication transaction owns one
    strong, empty-metadata relation with the deterministic relation identity.
    """

    source_type, target_type, relation_id_suffix = (
        _canonical_provider_lineage_endpoint_types(
            relation_type, source_ref, target_ref))

    def normalize(ref: Mapping[str, Any]) -> dict[str, str]:
        return {
            "entity_type": str(ref.get("entity_type", "")),
            "entity_id": str(ref.get("logical_id", ref.get("entity_id", ""))),
            "version_id": str(ref.get("version_id", "")),
        }

    source = normalize(source_ref)
    target = normalize(target_ref)

    def ref_tuple(ref: Mapping[str, Any]) -> tuple[str, str, str]:
        return (str(ref.get("entity_type", "")),
                str(ref.get("entity_id", "")),
                str(ref.get("version_id", "")))
    registered = {
        (str(entity_type), str(entity_id), str(version_id))
        for entity_type, entity_id, version_id in registered_exact_refs
    }
    if (source["entity_type"] != source_type
            or target["entity_type"] != target_type
            or not source["entity_id"] or not source["version_id"]
            or not target["entity_id"] or not target["version_id"]
            or ref_tuple(source) not in registered
            or ref_tuple(target) not in registered
            or not publication_transaction or not publication_key):
        raise CanonicalProviderLineageViolation(
            f"{relation_type} lacks exact registered endpoint authority")
    if relation_type == "attempt_of_call":
        metadata_matches = (
            source_metadata.get("provider_attempt_id") == source["entity_id"]
            and source_metadata.get("provider_attempt_version_id")
            == source["version_id"]
            and source_metadata.get("llm_call_id") == target["entity_id"]
            and source_metadata.get("llm_call_version_id")
            == target["version_id"]
            and target_metadata.get("llm_call_id") == target["entity_id"]
            and target_metadata.get("llm_call_version_id")
            == target["version_id"])
    else:
        invocation_ref = target_metadata.get("invocation_ref")
        source_invocation_ref = source_metadata.get("invocation_ref")
        source_invocation_id = source_metadata.get("invocation_id")
        source_invocation_version = source_metadata.get(
            "invocation_version_id")
        if isinstance(source_invocation_ref, Mapping):
            source_invocation_id = source_invocation_ref.get("logical_id")
            source_invocation_version = source_invocation_ref.get("version_id")
        metadata_matches = (
            source_metadata.get("llm_call_id") == source["entity_id"]
            and source_metadata.get("llm_call_version_id")
            == source["version_id"]
            and source_invocation_id == target["entity_id"]
            and source_invocation_version == target["version_id"]
            and isinstance(invocation_ref, Mapping)
            and normalize(invocation_ref) == target)
    if not metadata_matches:
        raise CanonicalProviderLineageViolation(
            f"{relation_type} endpoint metadata is not exact")

    matching: list[dict[str, Any]] = []
    for raw_record in relation_records:
        if str(raw_record.get("relation_type", "")) != relation_type:
            continue
        try:
            source_value = raw_record.get("source", raw_record.get("source_json"))
            target_value = raw_record.get("target", raw_record.get("target_json"))
            metadata_value = raw_record.get(
                "metadata", raw_record.get("metadata_json"))
            relation_source = (json.loads(source_value)
                               if isinstance(source_value, str) else source_value)
            relation_target = (json.loads(target_value)
                               if isinstance(target_value, str) else target_value)
            relation_metadata = (json.loads(metadata_value)
                                 if isinstance(metadata_value, str)
                                 else metadata_value)
            publication_payload_value = raw_record.get(
                "publication_payload", raw_record.get("publication_payload_json"))
            publication_payload = (
                json.loads(publication_payload_value)
                if isinstance(publication_payload_value, str)
                else publication_payload_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalProviderLineageViolation(
                f"{relation_type} relation record is malformed") from exc
        if not isinstance(relation_source, Mapping):
            continue
        same_logical = (
            relation_source.get("entity_type") == source_type
            and str(relation_source.get("entity_id", ""))
            == source["entity_id"])
        same_version = (
            str(relation_source.get("version_id", ""))
            == source["version_id"])
        if not same_logical and not same_version:
            continue
        if (set(relation_source) != {"entity_type", "entity_id", "version_id"}
                or not isinstance(relation_source.get("version_id"), str)
                or (source_type, str(relation_source.get("entity_id", "")),
                    str(relation_source.get("version_id", ""))) not in registered):
            raise CanonicalProviderLineageViolation(
                f"{relation_type} source is not an exact registered version")
        if same_version and not same_logical:
            raise CanonicalProviderLineageViolation(
                f"{relation_type} source version has a conflicting typed identity")
        if relation_source.get("version_id") != source["version_id"]:
            continue
        if (not isinstance(relation_target, Mapping)
                or not isinstance(relation_metadata, Mapping)):
            raise CanonicalProviderLineageViolation(
                f"{relation_type} target or metadata is malformed")
        matching.append({
            "relation_id": str(raw_record.get("relation_id", "")),
            "source": dict(relation_source),
            "target": dict(relation_target),
            "strength": str(raw_record.get("strength", "")),
            "metadata": dict(relation_metadata),
            "transaction_id": str(raw_record.get("transaction_id", "")),
            "publication_transaction_id": str(raw_record.get(
                "publication_transaction_id",
                raw_record.get("transaction_id", ""))),
            "publication_event_type": str(raw_record.get(
                "publication_event_type", "")),
            "publication_payload": publication_payload,
        })

    expected_relation_id = _stable_id_text(
        "relation", publication_key, relation_id_suffix)
    expected_publication_payload = ({
        "relation_id": matching[0]["relation_id"],
        "relation_type": relation_type,
        "source": matching[0]["source"],
        "target": matching[0]["target"],
        "strength": matching[0]["strength"],
        "metadata": matching[0]["metadata"],
    } if len(matching) == 1 else None)
    if (len(matching) != 1
            or matching[0]["source"] != source
            or matching[0]["target"] != target
            or ref_tuple(matching[0]["target"]) not in registered
            or matching[0]["strength"] != "strong"
            or matching[0]["metadata"]
            or matching[0]["transaction_id"] != publication_transaction
            or matching[0]["publication_transaction_id"]
            != publication_transaction
            or matching[0]["publication_event_type"]
            != "relation_published/v1"
            or matching[0]["publication_payload"]
            != expected_publication_payload
            or matching[0]["relation_id"] != expected_relation_id):
        raise CanonicalProviderLineageViolation(
            f"{relation_type} requires one canonical strong lineage authority")
    return matching[0]

def canonical_attempt_response_record(
        attempt_ref: Mapping[str, Any], response_ref: Mapping[str, Any], *,
        publication_transaction: str, publication_key: str,
        attempt_metadata: Mapping[str, Any],
        response_metadata: Mapping[str, Any],
        relation_records: Iterable[Mapping[str, Any]],
        completion_records: Iterable[Mapping[str, Any]],
        registered_exact_refs: Iterable[tuple[str, str, str]],
        ) -> Mapping[str, Any]:
    """Return one canonical attempt-to-provider-response authority record."""

    def normalize(ref: Mapping[str, Any]) -> dict[str, str]:
        return {
            "entity_type": str(ref.get("entity_type", "")),
            "entity_id": str(ref.get("logical_id", ref.get("entity_id", ""))),
            "version_id": str(ref.get("version_id", "")),
        }

    attempt = normalize(attempt_ref)
    response = normalize(response_ref)
    registered = {
        (str(entity_type), str(entity_id), str(version_id))
        for entity_type, entity_id, version_id in registered_exact_refs
    }
    origin = response_metadata.get("origin")
    primary = origin.get("primary_ref") if isinstance(origin, Mapping) else None
    secondary = origin.get("secondary_ref") if isinstance(origin, Mapping) else None
    secondary_exact = normalize(secondary) if isinstance(secondary, Mapping) else None
    if (attempt["entity_type"] != "provider_attempt_spec/v1"
            or response["entity_type"] != "resource_version/v1"
            or (attempt["entity_type"], attempt["entity_id"],
                attempt["version_id"]) not in registered
            or (response["entity_type"], response["entity_id"],
                response["version_id"]) not in registered
            or not publication_transaction or not publication_key
            or attempt_metadata.get("provider_attempt_id") != attempt["entity_id"]
            or attempt_metadata.get("provider_attempt_version_id")
            != attempt["version_id"]
            or response_metadata.get("resource_id") != response["entity_id"]
            or response_metadata.get("resource_version_id") != response["version_id"]
            or response_metadata.get("origin_kind") != "provider_response"
            or not isinstance(origin, Mapping)
            or origin.get("kind") != "provider_response"
            or set(origin) != {"kind", "primary_ref", "secondary_ref"}
            or not isinstance(primary, Mapping)
            or set(primary) != {"entity_type", "logical_id", "version_id"}
            or normalize(primary) != attempt
            or not isinstance(secondary, Mapping)
            or set(secondary) != {"entity_type", "logical_id", "version_id"}
            or secondary.get("entity_type") != "llm_call_spec/v1"
            or secondary.get("logical_id") != attempt_metadata.get("llm_call_id")
            or secondary.get("version_id")
            != attempt_metadata.get("llm_call_version_id")
            or secondary_exact is None
            or (secondary_exact["entity_type"], secondary_exact["entity_id"],
                secondary_exact["version_id"]) not in registered):
        raise CanonicalAttemptResponseViolation(
            "attempt response lacks exact registered endpoint authority")

    matching: list[dict[str, Any]] = []
    for raw_record in relation_records:
        if str(raw_record.get("relation_type", "")) != "attempt_produced_response":
            continue
        try:
            source_value = raw_record.get("source", raw_record.get("source_json"))
            target_value = raw_record.get("target", raw_record.get("target_json"))
            metadata_value = raw_record.get(
                "metadata", raw_record.get("metadata_json"))
            source = (json.loads(source_value)
                      if isinstance(source_value, str) else source_value)
            target = (json.loads(target_value)
                      if isinstance(target_value, str) else target_value)
            metadata = (json.loads(metadata_value)
                        if isinstance(metadata_value, str) else metadata_value)
            publication_value = raw_record.get(
                "publication_payload", raw_record.get("publication_payload_json"))
            publication_payload = (
                json.loads(publication_value)
                if isinstance(publication_value, str) else publication_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalAttemptResponseViolation(
                "attempt response relation record is malformed") from exc
        if not isinstance(source, Mapping) or not isinstance(target, Mapping):
            continue
        source_version = str(source.get("version_id", ""))
        same_source_version = source_version == attempt["version_id"]
        same_source_logical = (
            source.get("entity_type") == "provider_attempt_spec/v1"
            and str(source.get("entity_id", "")) == attempt["entity_id"])
        target_version = str(target.get("version_id", ""))
        same_target_version = target_version == response["version_id"]
        same_target_logical = (
            target.get("entity_type") == "resource_version/v1"
            and str(target.get("entity_id", "")) == response["entity_id"])
        if not (same_source_version or same_source_logical
                or same_target_version or same_target_logical):
            continue
        exact_source = (
            set(source) == {"entity_type", "entity_id", "version_id"}
            and (str(source.get("entity_type", "")),
                 str(source.get("entity_id", "")), source_version) in registered)
        exact_target = (
            set(target) == {"entity_type", "entity_id", "version_id"}
            and (str(target.get("entity_type", "")),
                 str(target.get("entity_id", "")), target_version) in registered)
        if (not exact_source or not exact_target
                or (same_source_version and not same_source_logical)
                or (same_target_version and not same_target_logical)
                or not isinstance(metadata, Mapping)):
            raise CanonicalAttemptResponseViolation(
                "attempt response relation has an inexact registered endpoint")
        if (same_target_version
                and (not same_source_logical
                     or source_version != attempt["version_id"])):
            raise CanonicalAttemptResponseViolation(
                "provider response has a competing attempt origin")
        if source_version != attempt["version_id"]:
            continue
        matching.append({
            "relation_id": str(raw_record.get("relation_id", "")),
            "relation_type": "attempt_produced_response",
            "source": dict(source),
            "target": dict(target),
            "strength": str(raw_record.get("strength", "")),
            "metadata": dict(metadata),
            "transaction_id": str(raw_record.get("transaction_id", "")),
            "publication_transaction_id": str(raw_record.get(
                "publication_transaction_id", raw_record.get("transaction_id", ""))),
            "publication_event_type": str(raw_record.get(
                "publication_event_type", "")),
            "publication_payload": publication_payload,
        })

    expected_relation_id = _stable_id_text(
        "relation", publication_key, "attempt-response")
    expected_relation_payload = ({
        "relation_id": matching[0]["relation_id"],
        "relation_type": "attempt_produced_response",
        "source": matching[0]["source"],
        "target": matching[0]["target"],
        "strength": matching[0]["strength"],
        "metadata": matching[0]["metadata"],
    } if len(matching) == 1 else None)
    if (len(matching) != 1
            or matching[0]["source"] != attempt
            or matching[0]["target"] != response
            or matching[0]["strength"] != "strong"
            or matching[0]["metadata"]
            or matching[0]["relation_id"] != expected_relation_id
            or matching[0]["transaction_id"] != publication_transaction
            or matching[0]["publication_transaction_id"]
            != publication_transaction
            or matching[0]["publication_event_type"] != "relation_published/v1"
            or matching[0]["publication_payload"] != expected_relation_payload):
        raise CanonicalAttemptResponseViolation(
            "attempt response requires one canonical strong provenance authority")

    extensions = response_metadata.get("extensions")
    facts = (extensions.get("registry.provider_response/v1")
             if isinstance(extensions, Mapping) else None)
    if (not isinstance(facts, Mapping)
            or set(facts) != {
                "finish_reason", "external_request_id",
                "reconciliation_proof_ref"}):
        raise CanonicalAttemptResponseViolation(
            "attempt response metadata lacks terminal facts")
    proof_ref = facts.get("reconciliation_proof_ref")
    if proof_ref is not None:
        raise CanonicalAttemptResponseViolation(
            "v1 response observation cannot claim reconciliation completion")
    task_ref = response_metadata.get("task_ref")
    round_ref = response_metadata.get("round_ref")
    net_ref = response_metadata.get("net_ref")
    expected_task_id = (str(task_ref.get("logical_id", ""))
                        if isinstance(task_ref, Mapping) else "")
    expected_round_id = (str(round_ref.get("logical_id", ""))
                         if isinstance(round_ref, Mapping) else "")
    expected_net_id = (str(net_ref.get("logical_id", ""))
                       if isinstance(net_ref, Mapping) else "")
    expected_branch_id = str(response_metadata.get("branch_id", ""))
    lifecycle_matches = []
    for raw_record in completion_records:
        if str(raw_record.get("event_type", "")) not in {
                "provider_attempt_submission_observed/v1",
                "provider_attempt_completed/v1",
                "provider_attempt_reconciled_completed/v1"}:
            continue
        payload_value = raw_record.get("payload", raw_record.get("payload_json"))
        try:
            payload = (json.loads(payload_value)
                       if isinstance(payload_value, str) else payload_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalAttemptResponseViolation(
                "attempt response completion fact is malformed") from exc
        if not isinstance(payload, Mapping):
            continue
        response_value = payload.get("response_resource_ref")
        observed_response_version = (
            str(response_value.get("resource_version_id", ""))
            if isinstance(response_value, Mapping) else "")
        if (str(raw_record.get("aggregate_id", "")) == attempt["entity_id"]
                or str(payload.get("response_version_id", ""))
                == response["version_id"]
                or observed_response_version == response["version_id"]):
            parent_ids_value = raw_record.get(
                "parent_event_ids", raw_record.get("parent_event_ids_json"))
            try:
                parent_ids = (json.loads(parent_ids_value)
                              if isinstance(parent_ids_value, str)
                              else parent_ids_value)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise CanonicalAttemptResponseViolation(
                    "attempt response completion fact is malformed") from exc
            producer = response_metadata.get("producer_ref")
            expected_producer_id = (
                str(producer.get("logical_id", ""))
                if isinstance(producer, Mapping) else "")
            is_proposal = bool(raw_record.get("proposal_record", False))
            if is_proposal:
                task_control = raw_record.get("task_control") is True
                time_authority_matches = raw_record.get("occurred_at") is None
            else:
                task_control_sequence = raw_record.get("task_control_sequence")
                task_control = (
                    isinstance(task_control_sequence, int)
                    and not isinstance(task_control_sequence, bool)
                    and task_control_sequence >= 1)
                time_authority_matches = (
                    bool(str(raw_record.get("occurred_at", "")))
                    and raw_record.get("occurred_at")
                    == raw_record.get("recorded_at"))
            lifecycle_matches.append({
                "event_id": str(raw_record.get("event_id", "")),
                "event_type": str(raw_record.get("event_type", "")),
                "event_schema_version": str(raw_record.get(
                    "event_schema_version", "")),
                "criticality": str(raw_record.get("criticality", "")),
                "task_id": str(raw_record.get("task_id", "")),
                "branch_id": str(raw_record.get("branch_id", "")),
                "task_round_id": str(raw_record.get("task_round_id", "")),
                "net_instance_id": str(raw_record.get("net_instance_id", "")),
                "stream_id": str(raw_record.get("stream_id", "")),
                "aggregate_id": str(raw_record.get("aggregate_id", "")),
                "aggregate_type": str(raw_record.get("aggregate_type", "")),
                "task_control": task_control,
                "idempotency_key": str(raw_record.get("idempotency_key", "")),
                "command_id": str(raw_record.get("command_id", "")),
                "correlation_id": str(raw_record.get("correlation_id", "")),
                "causation_event_id": raw_record.get("causation_event_id"),
                "parent_event_ids": parent_ids,
                "producer_principal": str(raw_record.get(
                    "producer_principal", "")),
                "producer_invocation_id": str(raw_record.get(
                    "producer_invocation_id", "")),
                "transaction_id": str(raw_record.get("transaction_id", "")),
                "time_authority_matches": time_authority_matches,
                "payload_schema_ref": str(raw_record.get(
                    "payload_schema_ref", "")),
                "payload_digest": str(raw_record.get("payload_digest", "")),
                "payload": dict(payload),
                "expected_producer_invocation_id": expected_producer_id,
            })
    observations = tuple(
        record for record in lifecycle_matches
        if record["event_type"]
        == "provider_attempt_submission_observed/v1")
    completions = tuple(
        record for record in lifecycle_matches
        if record["event_type"] in {
            "provider_attempt_completed/v1",
            "provider_attempt_reconciled_completed/v1"})
    if len(observations) != 1:
        raise CanonicalAttemptResponseViolation(
            "attempt response lacks one atomic durable observation")
    observation = observations[0]
    observed_material = {
        "provider_attempt_ref": {
            "entity_type": attempt["entity_type"],
            "logical_id": attempt["entity_id"],
            "version_id": attempt["version_id"],
        },
        "llm_call_ref": dict(secondary),
        "invocation_ref": response_metadata.get("producer_ref"),
        "dispatch_event_id": observation["payload"].get("dispatch_event_id"),
        "response_resource_ref": {
            "resource_id": response["entity_id"],
            "resource_version_id": response["version_id"],
        },
        "response_payload_digest": response_metadata.get("payload_digest"),
        "finish_reason": facts.get("finish_reason"),
        "external_request_id": facts.get(
            "external_request_id"),
    }
    expected_observation_payload = {
        "provider_attempt_id": attempt["entity_id"],
        "provider_attempt_version_id": attempt["version_id"],
        **observed_material,
    }
    if (observation["event_schema_version"] != "v1"
            or observation["criticality"] != "authoritative"
            or not expected_task_id
            or observation["task_id"] != expected_task_id
            or not expected_branch_id
            or observation["branch_id"] != expected_branch_id
            or not expected_round_id
            or observation["task_round_id"] != expected_round_id
            or not expected_net_id
            or observation["net_instance_id"] != expected_net_id
            or observation["stream_id"]
            != f"provider-attempt:{attempt['entity_id']}"
            or observation["aggregate_id"] != attempt["entity_id"]
            or observation["aggregate_type"] != "provider_attempt"
            or not observation["task_control"]
            or observation["idempotency_key"] != publication_key
            or observation["command_id"] != publication_key
            or observation["correlation_id"]
            != publication_transaction
            or observation["causation_event_id"] is not None
            or observation["parent_event_ids"] != []
            or observation["producer_principal"] != "framework"
            or not observation["expected_producer_invocation_id"]
            or observation["producer_invocation_id"]
            != observation["expected_producer_invocation_id"]
            or observation["transaction_id"] != publication_transaction
            or not observation["time_authority_matches"]
            or observation["payload_schema_ref"]
            != "registry_v1/provider_attempt_submission_observed/v1"
            or observation["payload_digest"]
            != canonical_text(expected_observation_payload)
            or observation["payload"] != expected_observation_payload):
        raise CanonicalAttemptResponseViolation(
            "attempt response observation differs from its publication closure")
    if len(completions) > 1:
        raise CanonicalAttemptResponseViolation(
            "attempt response has multiple terminal completion facts")
    if completions:
        completion = completions[0]
        expected_completion_payload = {
            "provider_attempt_id": attempt["entity_id"],
            "response_version_id": response["version_id"],
            "response_observed_event_id": observation["event_id"],
            "finish_reason": facts.get("finish_reason"),
            "external_request_id": facts.get(
                "external_request_id"),
        }
        if (not observation["event_id"]
                or completion["event_type"]
                != "provider_attempt_completed/v1"
                or completion["payload"] != expected_completion_payload
                or completion["payload_digest"]
                != canonical_text(expected_completion_payload)
                or completion["transaction_id"] == publication_transaction
                or completion["idempotency_key"] == publication_key
                or completion["aggregate_id"] != attempt["entity_id"]
                or completion["stream_id"]
                != f"provider-attempt:{attempt['entity_id']}"
                or completion["producer_invocation_id"]
                != observation["producer_invocation_id"]):
            raise CanonicalAttemptResponseViolation(
                "attempt completion does not uniquely consume its observation")
    return matching[0]

def canonical_producer_record(
        resource_ref: Mapping[str, Any], producer_ref: Mapping[str, Any], *,
        publication_transaction: str, publication_key: str,
        relation_records: Iterable[Mapping[str, Any]],
        valid_resource_versions: Iterable[str],
        registered_exact_refs: Iterable[tuple[str, str, str]],
        ) -> Mapping[str, Any]:
    """Return the one canonical ``produced_by`` record for an exact resource.

    ``typed_relation/v1`` intentionally permits logical endpoints for relation
    types whose contracts use them.  The narrower ``produced_by`` contract is
    enforced here: every row naming this registered resource logical identity
    must name one of its exact versions, while the requested version must have
    exactly one strong, empty-metadata, stable-ID relation in its publication
    transaction.  Exact sibling versions are validated by their own call.
    """

    resource_id = str(
        resource_ref.get("logical_id", resource_ref.get("entity_id", "")))
    resource_version = str(resource_ref.get("version_id", ""))
    producer_id = str(
        producer_ref.get("logical_id", producer_ref.get("entity_id", "")))
    producer_version = str(producer_ref.get("version_id", ""))
    producer_type = str(producer_ref.get("entity_type", ""))
    valid_versions = {str(value) for value in valid_resource_versions}
    registered_refs = {
        (str(entity_type), str(entity_id), str(version_id))
        for entity_type, entity_id, version_id in registered_exact_refs
    }
    if (not resource_id or not resource_version
            or resource_version not in valid_versions
            or not producer_type or not producer_id or not producer_version
            or (producer_type, producer_id, producer_version)
            not in registered_refs
            or not publication_transaction or not publication_key):
        raise CanonicalProducerViolation(
            "resource produced_by authority lacks exact publication identity")

    expected_source = {
        "entity_type": "resource_version/v1",
        "entity_id": resource_id,
        "version_id": resource_version,
    }
    expected_target = {
        "entity_type": producer_type,
        "entity_id": producer_id,
        "version_id": producer_version,
    }
    matching: list[dict[str, Any]] = []
    for raw_record in relation_records:
        try:
            if str(raw_record.get("relation_type", "")) != "produced_by":
                continue
            source_value = raw_record.get("source", raw_record.get("source_json"))
            target_value = raw_record.get("target", raw_record.get("target_json"))
            metadata_value = raw_record.get(
                "metadata", raw_record.get("metadata_json"))
            source = (json.loads(source_value)
                      if isinstance(source_value, str) else source_value)
            target = (json.loads(target_value)
                      if isinstance(target_value, str) else target_value)
            metadata = (json.loads(metadata_value)
                        if isinstance(metadata_value, str) else metadata_value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalProducerViolation(
                "resource produced_by relation record is malformed") from exc
        if not isinstance(source, Mapping):
            continue
        if (source.get("entity_type") != "resource_version/v1"
                or str(source.get("entity_id", "")) != resource_id):
            continue
        source_version = source.get("version_id")
        if (set(source) != {"entity_type", "entity_id", "version_id"}
                or not isinstance(source_version, str)
                or not source_version
                or source_version not in valid_versions):
            raise CanonicalProducerViolation(
                "resource produced_by source is not an exact registered version")
        if source_version != resource_version:
            continue
        if not isinstance(target, Mapping) or not isinstance(metadata, Mapping):
            raise CanonicalProducerViolation(
                "resource produced_by target or metadata is malformed")
        matching.append({
            "relation_id": str(raw_record.get("relation_id", "")),
            "source": dict(source),
            "target": dict(target),
            "strength": str(raw_record.get("strength", "")),
            "metadata": dict(metadata),
            "transaction_id": str(raw_record.get("transaction_id", "")),
        })

    expected_relation_id = _stable_id_text(
        "relation", publication_key, resource_version, "produced_by")
    if (len(matching) != 1
            or matching[0]["source"] != expected_source
            or matching[0]["target"] != expected_target
            or matching[0]["strength"] != "strong"
            or matching[0]["metadata"]
            or matching[0]["transaction_id"] != publication_transaction
            or matching[0]["relation_id"] != expected_relation_id):
        raise CanonicalProducerViolation(
            "resource requires one canonical strong produced_by authority")
    return matching[0]

def canonical_producer_logical_set(
        resource_records: Iterable[Mapping[str, Any]], *,
        relation_records: Iterable[Mapping[str, Any]],
        transaction_keys: Mapping[str, str],
        registered_exact_refs: Iterable[tuple[str, str, str]],
        ) -> Mapping[str, Mapping[str, Any]]:
    """Validate every exact sibling in one resource logical identity.

    The exact-record predicate deliberately ignores a different valid sibling
    after proving its source is exact.  This logical-set wrapper closes that
    scope by invoking the same canonical predicate once for every registered
    sibling with its own authoritative metadata and publication transaction.
    """

    resources = [dict(record) for record in resource_records]
    if not resources:
        raise CanonicalProducerViolation(
            "resource produced_by logical set is empty")
    logical_ids = {str(record.get("logical_id", "")) for record in resources}
    versions = [str(record.get("version_id", "")) for record in resources]
    if (len(logical_ids) != 1 or "" in logical_ids or "" in versions
            or len(set(versions)) != len(versions)):
        raise CanonicalProducerViolation(
            "resource produced_by logical set is not one exact sibling set")
    logical_id = next(iter(logical_ids))
    valid_versions = set(versions)
    relations = tuple(relation_records)
    registered = tuple(registered_exact_refs)
    canonical: dict[str, Mapping[str, Any]] = {}
    for record, version_id in zip(resources, versions):
        metadata = record.get("metadata")
        producer_ref = (metadata.get("producer_ref")
                        if isinstance(metadata, Mapping) else None)
        publication_transaction = str(record.get("transaction_id", ""))
        publication_key = transaction_keys.get(publication_transaction)
        canonical[version_id] = canonical_producer_record(
            {
                "entity_type": "resource_version/v1",
                "logical_id": logical_id,
                "version_id": version_id,
            },
            producer_ref if isinstance(producer_ref, Mapping) else {},
            publication_transaction=publication_transaction,
            publication_key=str(publication_key or ""),
            relation_records=relations,
            valid_resource_versions=valid_versions,
            registered_exact_refs=registered,
        )
    return canonical

def require_canonical_provider_lineage(
        store, relation_type: str, source_ref: Any,
        target_ref: Any, *,
        through_ordinal: int | None = None,
        view: RegistryAuthorityView | None = None) -> Mapping[str, Any]:
    """Read one persisted attempt/call lineage with canonical authority."""

    if view is not None and through_ordinal is not None:
        raise TypeError(
            "provider lineage accepts one Registry read authority")
    authority = view or store.canonical_view(
        through_ordinal=through_ordinal)

    source = (dict(source_ref) if isinstance(source_ref, Mapping)
              else _ref_json(source_ref))
    target = (dict(target_ref) if isinstance(target_ref, Mapping)
              else _ref_json(target_ref))
    try:
        source_type, target_type, _suffix = (
            _canonical_provider_lineage_endpoint_types(
                relation_type, source, target))
    except CanonicalProviderLineageViolation as exc:
        raise RegistryCorruptError(
            f"persisted {relation_type} names unsupported endpoint types"
        ) from exc
    source_id = str(source.get(
        "logical_id", source.get("entity_id", "")))
    source_version = str(source.get("version_id", ""))
    target_id = str(target.get(
        "logical_id", target.get("entity_id", "")))
    target_version = str(target.get("version_id", ""))
    source_visible = store.object_row_for_view(authority, source_version)
    target_visible = store.object_row_for_view(authority, target_version)
    if source_visible is None or target_visible is None:
        raise RegistryCorruptError(
            f"persisted {relation_type} is outside Registry read authority")
    visible_relation_ids = {
        str(row["relation_id"])
        for row in store.relation_rows_for_view(
            authority, version_id=source_version,
            relation_type=relation_type)
    }
    with store.connect() as db:
        source_row = db.execute(
            "SELECT o.logical_id,o.metadata_json,o.transaction_id,"
            "t.idempotency_key FROM objects o JOIN transactions t "
            "ON t.transaction_id=o.transaction_id WHERE o.version_id=? "
            "AND o.object_type=?", (source_version, source_type),
        ).fetchone()
        target_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type=?", (target_version, target_type),
        ).fetchone()
        relation_records = [dict(row) for row in db.execute(
            "SELECT r.relation_id,r.relation_type,r.source_json,r.target_json,"
            "r.strength,r.metadata_json,r.transaction_id,"
            "COALESCE(e.transaction_id,'') AS publication_transaction_id,"
            "COALESCE(e.event_type,'') AS publication_event_type,"
            "e.payload_json AS publication_payload_json "
            "FROM relations r LEFT JOIN events e "
            "ON e.event_id=r.published_event_id WHERE r.relation_type=? "
            "AND (json_extract(r.source_json,'$.entity_id')=? OR "
            "json_extract(r.source_json,'$.version_id')=?)",
            (relation_type, source_id, source_version),
        ).fetchall()
            if str(row["relation_id"]) in visible_relation_ids]
        registered_versions = {source_version, target_version}
        for record in relation_records:
            for field in ("source_json", "target_json"):
                value = record.get(field)
                try:
                    ref = (json.loads(value)
                           if isinstance(value, str) else value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(ref, Mapping):
                    registered_versions.add(str(ref.get("version_id", "")))
        versions = tuple(sorted(
            value for value in registered_versions if value))
        placeholders = ",".join("?" for _value in versions)
        registered_exact_refs = {
            (str(row["object_type"]), str(row["logical_id"]),
             str(row["version_id"]))
            for row in db.execute(
                "SELECT object_type,logical_id,version_id FROM objects "
                f"WHERE version_id IN ({placeholders})", versions)
            if store.object_row_for_view(
                authority, str(row["version_id"])) is not None
        }
    if (source_row is None or str(source_row["logical_id"]) != source_id
            or target_row is None or str(target_row["logical_id"]) != target_id):
        raise RegistryCorruptError(
            f"persisted {relation_type} has no exact endpoint objects")
    try:
        return canonical_provider_lineage_record(
            relation_type, source, target,
            publication_transaction=str(source_row["transaction_id"]),
            publication_key=str(source_row["idempotency_key"]),
            source_metadata=json.loads(source_row["metadata_json"]),
            target_metadata=json.loads(target_row["metadata_json"]),
            relation_records=relation_records,
            registered_exact_refs=registered_exact_refs,
        )
    except (CanonicalProviderLineageViolation, json.JSONDecodeError,
            TypeError, ValueError) as exc:
        raise RegistryCorruptError(
            f"persisted {relation_type} authority is corrupt") from exc

def require_canonical_attempt_response(
        store, attempt_ref: Any, response_ref: Any) -> Mapping[str, Any]:
    """Read one persisted provider-response provenance authority."""

    attempt = (dict(attempt_ref) if isinstance(attempt_ref, Mapping)
               else _ref_json(attempt_ref))
    response = (dict(response_ref) if isinstance(response_ref, Mapping)
                else _ref_json(response_ref))
    attempt_id = str(attempt.get(
        "logical_id", attempt.get("entity_id", "")))
    attempt_version = str(attempt.get("version_id", ""))
    response_id = str(response.get(
        "logical_id", response.get("entity_id", "")))
    response_version = str(response.get("version_id", ""))
    with store.connect() as db:
        attempt_row = db.execute(
            "SELECT logical_id,metadata_json FROM objects WHERE version_id=? "
            "AND object_type='provider_attempt_spec/v1'", (attempt_version,),
        ).fetchone()
        response_row = db.execute(
            "SELECT o.logical_id,o.metadata_json,o.transaction_id,"
            "t.idempotency_key FROM objects o JOIN transactions t "
            "ON t.transaction_id=o.transaction_id WHERE o.version_id=? "
            "AND o.object_type='resource_version/v1'", (response_version,),
        ).fetchone()
        relation_records = [dict(row) for row in db.execute(
            "SELECT r.relation_id,r.relation_type,r.source_json,r.target_json,"
            "r.strength,r.metadata_json,r.transaction_id,"
            "COALESCE(e.transaction_id,'') AS publication_transaction_id,"
            "COALESCE(e.event_type,'') AS publication_event_type,"
            "e.payload_json AS publication_payload_json "
            "FROM relations r LEFT JOIN events e "
            "ON e.event_id=r.published_event_id "
            "WHERE r.relation_type='attempt_produced_response' AND ("
            "json_extract(r.source_json,'$.entity_id')=? OR "
            "json_extract(r.source_json,'$.version_id')=? OR "
            "json_extract(r.target_json,'$.entity_id')=? OR "
            "json_extract(r.target_json,'$.version_id')=?)",
            (attempt_id, attempt_version, response_id, response_version),
        ).fetchall()]
        completion_rows = db.execute(
            "SELECT * "
            "FROM events WHERE event_type IN "
            "('provider_attempt_submission_observed/v1',"
            "'provider_attempt_completed/v1',"
            "'provider_attempt_reconciled_completed/v1') AND ("
            "aggregate_id=? OR "
            "json_extract(payload_json,'$.response_version_id')=? OR "
            "json_extract(payload_json,"
            "'$.response_resource_ref.resource_version_id')=?)",
            (attempt_id, response_version, response_version),
        ).fetchall()
        try:
            completion_records = [
                store._verified_persisted_event_record(db, row)
                for row in completion_rows]
        except RegistryCorruptError as exc:
            raise RegistryCorruptError(
                "persisted attempt response authority is corrupt") from exc
        try:
            attempt_metadata = json.loads(attempt_row["metadata_json"])
            response_metadata = json.loads(response_row["metadata_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RegistryCorruptError(
                "persisted attempt response metadata is corrupt") from exc
        registered_versions = {
            attempt_version,
            response_version,
            str(attempt_metadata.get("llm_call_version_id", "")),
        }
        origin = response_metadata.get("origin")
        secondary = (origin.get("secondary_ref")
                     if isinstance(origin, Mapping) else None)
        if isinstance(secondary, Mapping):
            registered_versions.add(str(secondary.get("version_id", "")))
        for record in relation_records:
            for field in ("source_json", "target_json"):
                value = record.get(field)
                try:
                    ref = (json.loads(value)
                           if isinstance(value, str) else value)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(ref, Mapping):
                    registered_versions.add(str(ref.get("version_id", "")))
        versions = tuple(sorted(
            value for value in registered_versions if value))
        placeholders = ",".join("?" for _value in versions)
        registered_refs = {
            (str(row["object_type"]), str(row["logical_id"]),
             str(row["version_id"]))
            for row in db.execute(
                "SELECT object_type,logical_id,version_id FROM objects "
                f"WHERE version_id IN ({placeholders})", versions)
        }
    if (attempt_row is None or response_row is None
            or str(attempt_row["logical_id"]) != attempt_id
            or str(response_row["logical_id"]) != response_id):
        raise RegistryCorruptError(
            "persisted attempt response has no exact endpoint objects")
    try:
        return canonical_attempt_response_record(
            attempt, response,
            publication_transaction=str(response_row["transaction_id"]),
            publication_key=str(response_row["idempotency_key"]),
            attempt_metadata=attempt_metadata,
            response_metadata=response_metadata,
            relation_records=relation_records,
            completion_records=completion_records,
            registered_exact_refs=registered_refs,
        )
    except (CanonicalAttemptResponseViolation, json.JSONDecodeError,
            TypeError, ValueError) as exc:
        raise RegistryCorruptError(
            "persisted attempt response authority is corrupt") from exc

def require_canonical_producer(
        store, resource_ref: Any, producer_ref: Any) -> Mapping[str, Any]:
    """Read and validate one persisted resource producer authority.

    Readers share the commit/recovery predicate and report malformed legacy
    rows as registry corruption instead of attempting partial endpoint
    decoding.
    """
    resource = (dict(resource_ref) if isinstance(resource_ref, Mapping)
                else _ref_json(resource_ref))
    producer = (dict(producer_ref) if isinstance(producer_ref, Mapping)
                else _ref_json(producer_ref))
    resource_id = str(
        resource.get("logical_id", resource.get("entity_id", "")))
    resource_version = str(resource.get("version_id", ""))
    with store.connect() as db:
        row = db.execute(
            "SELECT logical_id,object_type,transaction_id FROM objects "
            "WHERE version_id=?", (resource_version,),
        ).fetchone()
        if (row is None or row["object_type"] != "resource_version/v1"
                or str(row["logical_id"]) != resource_id):
            raise RegistryCorruptError(
                f"resource produced_by lookup has no exact object: {resource_version}")
        resource_records: list[dict[str, Any]] = []
        transaction_keys: dict[str, str] = {}
        for item in db.execute(
                "SELECT o.logical_id,o.version_id,o.metadata_json,"
                "o.transaction_id,t.idempotency_key FROM objects o "
                "JOIN transactions t ON t.transaction_id=o.transaction_id "
                "WHERE o.logical_id=? "
                "AND o.object_type='resource_version/v1'", (resource_id,)):
            try:
                metadata = json.loads(str(item["metadata_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RegistryCorruptError(
                    f"persisted resource metadata is corrupt: {item['version_id']}") from exc
            resource_records.append({
                "logical_id": str(item["logical_id"]),
                "version_id": str(item["version_id"]),
                "metadata": metadata,
                "transaction_id": str(item["transaction_id"]),
            })
            transaction_keys[str(item["transaction_id"])] = str(
                item["idempotency_key"])
        relation_records = [dict(item) for item in db.execute(
            "SELECT relation_id,relation_type,source_json,target_json,strength,"
            "metadata_json,transaction_id FROM relations "
            "WHERE relation_type='produced_by' "
            "AND json_extract(source_json,'$.entity_type')="
            "'resource_version/v1' "
            "AND json_extract(source_json,'$.entity_id')=?",
            (resource_id,),
        ).fetchall()]
        registered_exact_refs: set[tuple[str, str, str]] = {
            ("resource_version/v1", resource_id,
             str(item["version_id"])) for item in resource_records}
        producer_versions: set[str] = set()
        for record in resource_records:
            value = record["metadata"].get("producer_ref")
            if not isinstance(value, Mapping):
                continue
            producer_versions.add(str(value.get("version_id", "")))
        versions = tuple(sorted(
            value for value in producer_versions if value))
        if versions:
            placeholders = ",".join("?" for _value in versions)
            registered_exact_refs.update({
                (str(producer_row["object_type"]),
                 str(producer_row["logical_id"]),
                 str(producer_row["version_id"]))
                for producer_row in db.execute(
                    "SELECT object_type,logical_id,version_id FROM objects "
                    f"WHERE version_id IN ({placeholders})", versions)
            })
    try:
        canonical = canonical_producer_logical_set(
            resource_records,
            relation_records=relation_records,
            transaction_keys=transaction_keys,
            registered_exact_refs=registered_exact_refs,
        )
    except CanonicalProducerViolation as exc:
        raise RegistryCorruptError(
            f"resource produced_by authority is corrupt: {resource_id}") from exc
    expected_target = {
        "entity_type": str(producer.get("entity_type", "")),
        "entity_id": str(producer.get(
            "logical_id", producer.get("entity_id", ""))),
        "version_id": str(producer.get("version_id", "")),
    }
    selected = canonical.get(resource_version)
    if selected is None or selected.get("target") != expected_target:
        raise RegistryCorruptError(
            f"resource produced_by authority differs from metadata: {resource_version}")
    return selected
