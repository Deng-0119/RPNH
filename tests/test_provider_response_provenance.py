"""Pure native response provenance fixtures; no Registry or provider execution."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from cpn.rpnh.registry._event_store import provenance
from cpn.rpnh.registry.event_store import CanonicalAttemptResponseViolation
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _direct_resource_metadata, _ref_payload, _stable_id
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_text
from test_provider_attempt_reservation_materialization_stage3 import _materialize


def ref(kind, entity_type=None):
    return VersionRef(entity_type or kind + "/v1", TypedId(kind, "a" * 32),
                      TypedId(kind + "_version", "b" * 32))


def fixture(version):
    # Use the native transaction-free attempt materializer and raw-resource factory.
    reserved = _materialize(prior_attempt_ref=None, prior_failure_class=None, finalization_scope=None)
    attempt = deepcopy(reserved.metadata)
    call_ref = dict(attempt["llm_call_ref"], entity_type="llm_call_spec/v" + str(version))
    attempt["llm_call_ref"] = call_ref
    invocation = VersionRef("invocation/v1", TypedId.parse(attempt["invocation_id"]),
                            TypedId.parse(attempt["invocation_version_id"]))
    binding = ref("operation_binding")
    attempt["operation_binding_ref"] = _ref_payload(binding)
    call = VersionRef(call_ref["entity_type"], TypedId.parse(call_ref["logical_id"]), TypedId.parse(call_ref["version_id"]))
    response = ResourceVersionRef(TypedId("resource", "c" * 32), TypedId("resource_version", "d" * 32))
    raw = version != 1
    kind = "provider_raw_response" if raw else "provider_response"
    extension = ({"transport_kind": "subprocess", "status_code": None, "external_request_id": None}
                 if raw else {"finish_reason": "stop", "external_request_id": None, "reconciliation_proof_ref": None})
    metadata = _direct_resource_metadata(SimpleNamespace(branch_id="main"), ref=response,
        origin_kind=kind, primary=reserved.attempt_ref, secondary=call,
        task_ref=ref("task"), round_ref=ref("task_round"), net_ref=ref("net_instance"),
        producer_ref=invocation, lifetime_ref=ref("operation_execution_lease"),
        operation_binding_ref=binding, agent_loop_ref=call if version == 3 else ref("agent_loop"),
        payload_size=316, media_type="application/octet-stream", content_schema_ref=None,
        content_schema_authority_ref=None, summary="Native response fixture",
        descriptors={"content_role": "provider_raw_response_evidence"},
        extensions={("registry.provider_raw_response/v" + str(version) if raw else "registry.provider_response/v1"): extension},
        input_resource_refs=(), intended_boundary="registered_host_response" if version == 3 else "agent_turn_decode")
    if not raw:
        metadata["payload_digest"] = "legacy-response-payload-material"
    attempt_ref, response_ref = _ref_payload(reserved.attempt_ref), _ref_payload(response.as_version_ref())
    tx, key = "raw-response-transaction", "native:raw-return"
    def endpoint(r):
        return {"entity_type": r["entity_type"], "entity_id": r["logical_id"], "version_id": r["version_id"]}
    relation = {"relation_id": str(_stable_id("relation", key, "attempt-response")),
                "relation_type": "attempt_produced_response", "source": endpoint(attempt_ref),
                "target": endpoint(response_ref), "strength": "strong", "metadata": {}}
    record = dict(relation, transaction_id=tx, publication_transaction_id=tx,
                  publication_event_type="relation_published/v1", publication_payload=deepcopy(relation))
    observed = {"provider_attempt_id": attempt_ref["logical_id"], "provider_attempt_version_id": attempt_ref["version_id"],
                "provider_attempt_ref": attempt_ref, "llm_call_ref": call_ref,
                "invocation_ref": _ref_payload(invocation), "dispatch_event_id": "event:" + "e" * 32,
                "response_resource_ref": {"resource_id": response_ref["logical_id"], "resource_version_id": response_ref["version_id"]},
                "finish_reason": None if raw else "stop", "external_request_id": None}
    observed.update({"response_size": 316} if raw else {"response_payload_digest": metadata["payload_digest"]})
    completed = {"provider_attempt_id": attempt_ref["logical_id"], "response_version_id": response_ref["version_id"],
                 "response_observed_event_id": "event:" + "f" * 32,
                 "finish_reason": observed["finish_reason"], "external_request_id": None}
    def event(name, payload, transaction, command, event_id):
        return {"event_id": event_id, "event_type": name + "/v1", "event_schema_version": "v1",
                "criticality": "authoritative", "task_id": metadata["task_ref"]["logical_id"],
                "branch_id": "main", "task_round_id": metadata["round_ref"]["logical_id"],
                "net_instance_id": metadata["net_ref"]["logical_id"],
                "stream_id": "provider-attempt:" + attempt_ref["logical_id"],
                "aggregate_id": attempt_ref["logical_id"], "aggregate_type": "provider_attempt",
                "task_control_sequence": 1, "idempotency_key": command, "command_id": command,
                "correlation_id": transaction, "causation_event_id": None, "parent_event_ids_json": "[]",
                "producer_principal": "framework", "producer_invocation_id": str(invocation.entity_id),
                "transaction_id": transaction, "occurred_at": "2026-01-01T00:00:00Z", "recorded_at": "2026-01-01T00:00:00Z",
                "payload_schema_ref": "registry_v1/" + name + "/v1",
                "payload_json": canonical_text(payload), "payload_digest": canonical_text(payload)}
    return dict(attempt_ref=attempt_ref, response_ref=response_ref, publication_transaction=tx, publication_key=key,
                attempt_metadata=attempt, response_metadata=metadata, relation_records=[record],
                completion_records=[event("provider_attempt_submission_observed", observed, tx, key, completed["response_observed_event_id"]),
                    event("provider_attempt_completed", completed, "completion-transaction", "native:complete", "event:" + "0" * 32)],
                registered_exact_refs=[(r["entity_type"], r["logical_id"], r["version_id"]) for r in (attempt_ref, response_ref, call_ref)])


@pytest.mark.parametrize("version", [1, 2, 3])
def test_native_exact_response_closes_without_changing_inputs(version):
    data = fixture(version)
    original = deepcopy(data)
    result = provenance.canonical_attempt_response_record(**data)
    assert result["relation_type"] == "attempt_produced_response"
    assert result["source"]["version_id"] == data["attempt_ref"]["version_id"]
    assert result["target"]["version_id"] == data["response_ref"]["version_id"]
    assert data == original


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("mutation", ["typed_call", "primary", "unregistered_call", "weak_relation", "relation_transaction",
                                     "missing_observation", "observation_size", "observation_digest", "duplicate_completion",
                                     "completion_response", "wrong_extension", "producer"])
def test_native_response_rejects_misbound_or_incomplete_authority(version, mutation):
    data = fixture(version)
    if mutation == "typed_call":
        data["attempt_metadata"]["llm_call_ref"]["entity_type"] = "llm_call_spec/v1"
    elif mutation == "primary":
        data["response_metadata"]["origin"]["primary_ref"]["version_id"] = "provider_attempt_version:" + "0" * 32
    elif mutation == "unregistered_call":
        data["registered_exact_refs"].pop()
    elif mutation == "weak_relation":
        data["relation_records"][0]["strength"] = "weak"
    elif mutation == "relation_transaction":
        data["relation_records"][0]["publication_transaction_id"] = "unrelated"
    elif mutation == "missing_observation":
        data["completion_records"].pop(0)
    elif mutation == "observation_size":
        row = data["completion_records"][0]
        value = json.loads(row["payload_json"])
        value["response_size"] += 1
        row["payload_json"] = row["payload_digest"] = canonical_text(value)
    elif mutation == "observation_digest":
        data["completion_records"][0]["payload_digest"] = "invalid"
    elif mutation == "duplicate_completion":
        data["completion_records"].append(deepcopy(data["completion_records"][-1]))
    elif mutation == "completion_response":
        row = data["completion_records"][-1]
        value = json.loads(row["payload_json"])
        value["response_version_id"] = "resource_version:" + "0" * 32
        row["payload_json"] = row["payload_digest"] = canonical_text(value)
    elif mutation == "wrong_extension":
        value = data["response_metadata"]["extensions"].pop("registry.provider_raw_response/v" + str(version))
        data["response_metadata"]["extensions"]["registry.provider_response/v1"] = value
    else:
        data["response_metadata"]["producer_ref"]["version_id"] = "invocation_version:" + "0" * 32
    with pytest.raises(CanonicalAttemptResponseViolation):
        provenance.canonical_attempt_response_record(**data)
