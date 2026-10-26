"""Offline opt-out coverage; fake transport never establishes a real connection.

Public-wire fixtures establish parser/adapter identity behavior only, not
registered-material root acceptance or a global AgentLoop call budget.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest
from jsonschema import Draft7Validator

from cpn.llm_adapters import build_llm_input_port, load_llm_execution_selection
from cpn.llm_adapters import external_provider as external
from cpn.llm_adapters._external_provider_recovery import (
    ExternalProviderRecoveryConfigError,
    recovery_policy_from_document,
)
from cpn.rpnh import public_material_contracts as wire
from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputPortInterrupted
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh.user_config import discover_profiles
from registered_public_adapter_fixtures import public_wire
from test_external_provider_health_recovery import (
    _FakeHTTPSConnection, _FakeHTTPSFactory, _audit, _port_and_attempt,
    _provider_response, _recovery, _started_attempts,
)
from test_reasoning_effort_configuration import _external_catalog
from test_registered_public_adapter import attempt as public_attempt, selection


NO_RETRY = {"strategy": "no_automatic_retry/v1"}
SCHEMAS = Path(__file__).resolve().parents[1] / "cpn" / "schemas"


def _recovery_validators():
    for kind, key in (
            ("external_provider_adapter_config", "recovery"),
            ("provider_model_catalog", "externalRecovery")):
        for version in ("v2", "v3"):
            schema = json.loads((SCHEMAS / "runtime" /
                f"{kind}.{version}.schema.json").read_text())
            Draft7Validator.check_schema(schema)
            yield Draft7Validator({
                "definitions": schema["definitions"],
                "allOf": [schema["definitions"][key]],
            })
    schema = json.loads((SCHEMAS / "rpnh" /
        "public_execution_policy.v1.schema.json").read_text())
    Draft7Validator.check_schema(schema)
    yield Draft7Validator(schema["properties"]["adapter_profile"][
        "properties"]["recovery"])


@pytest.mark.parametrize("document", [NO_RETRY, _recovery()])
def test_recovery_documents_round_trip_in_parser_and_all_schemas(document):
    assert recovery_policy_from_document(document).as_document() == document
    for validator in _recovery_validators():
        validator.validate(document)


@pytest.mark.parametrize("document", [
    None, {}, {"strategy": False}, {"strategy": "no_automatic_retry/v2"},
    {**NO_RETRY, "max_probe_attempts": 0},
    {**NO_RETRY, "probe_timeout_budget_seconds": 1},
    {**NO_RETRY, "max_probe_success_formal_failure_cycles": None},
    {**NO_RETRY, "extra": False},
    {"strategy": "bounded_same_route_health_probe/v1"},
    _recovery(max_probe_attempts=0),
    _recovery(max_probe_attempts=True),
    _recovery(max_probe_success_formal_failure_cycles=4),
    _recovery(probe_timeout_budget_seconds=0),
])
def test_recovery_parser_and_all_schemas_reject_non_closed_shapes(document):
    with pytest.raises(ExternalProviderRecoveryConfigError):
        recovery_policy_from_document(document)
    assert all(not validator.is_valid(document)
               for validator in _recovery_validators())


def _assert_no_recovery(tmp_path, *, outcome, failed=False):
    started = _started_attempts(tmp_path)
    assert [item["call_kind"] for item in started] == ["real_model_call"]
    assert started[0]["call_ordinal"] == 0
    assert "prior_provider_attempt_id" not in started[0]
    finished = [item for item in _audit(tmp_path)
                if item["lifecycle"] == "external_provider_invocation_finished"]
    assert len(finished) == 1 and finished[0]["outcome"] == outcome
    detail = finished[0]["detail"]
    assert detail["formal_call_count"] == detail["physical_call_count"] == 1
    assert detail["probe_call_count"] == detail["recovery_cycle_count"] == 0
    assert detail["route_recovery"] == []
    assert detail["recovery_strategy"] == NO_RETRY["strategy"]
    if failed:
        assert detail["recovery_reason"] == "automatic_recovery_disabled"
        assert detail["last_provider_attempt"] == detail["last_formal_provider_attempt"]


@pytest.mark.parametrize("reasoning_effort", [None, "high"])
def test_no_retry_success_and_duplicate_guard(tmp_path, monkeypatch, reasoning_effort):
    unused = (200, _provider_response("unexpected", "second"), "second")
    port, attempt, transport = _port_and_attempt(tmp_path, monkeypatch, [
        (200, _provider_response("answer", "first"), "first"), unused,
    ], recovery=NO_RETRY, reasoning_effort=reasoning_effort)
    result = port.request_once(attempt)
    assert result.status_code == 200 and result.external_request_id == "first"
    assert len(transport.requests) == 1 and transport.actions == [unused]
    body = json.loads(transport.requests[0]["body"])
    assert body["messages"] == [{"role": "user", "content": "original prompt"}]
    assert body.get("reasoning_effort") == reasoning_effort
    _assert_no_recovery(tmp_path, outcome="response_returned")
    with pytest.raises(LLMInputPortFailure) as caught:
        port.request_once(attempt)
    assert caught.value.failure_code == "external_duplicate_submission_prevented"
    assert len(transport.requests) == 1 and transport.actions == [unused]
    port.close()


@pytest.mark.parametrize("action,disposition,state,code", [
    ((429, b'{"error":"rate limited"}', "first"),
     "provider_retryable", "response_observed", "http_status_429"),
    ((503, b'{"error":"unavailable"}', "first"),
     "provider_retryable", "response_observed", "http_status_503"),
    ((400, b'{"error":"invalid"}', "first"),
     "provider_failure", "response_observed", "http_status_400"),
    (socket.timeout("response headers timeout"),
     "submission_unknown", "submission_unknown", "response_headers_timeout"),
    ((200, b'not json', "first"), "protocol_rejected", "response_observed",
     "response_normalization_response_protocol_invalid"),
])
def test_no_retry_returns_original_failure_without_probe_or_replay(
        tmp_path, monkeypatch, action, disposition, state, code):
    unused = (200, _provider_response("unexpected probe", "second"), "second")
    port, attempt, transport = _port_and_attempt(
        tmp_path, monkeypatch, [action, unused], recovery=NO_RETRY)
    with pytest.raises(LLMInputPortFailure) as caught:
        port.request_once_interruptible(attempt, interruption_requested=lambda: False)
    assert (caught.value.disposition, caught.value.submission_state,
            caught.value.failure_code) == (disposition, state, code)
    assert len(transport.requests) == 1 and transport.actions == [unused]
    _assert_no_recovery(tmp_path, outcome="no_result", failed=True)
    port.close()


def test_no_retry_pre_submission_failure_keeps_original_classification(tmp_path, monkeypatch):
    unused = (200, _provider_response("unused", "first"), "first")
    port, attempt, transport = _port_and_attempt(
        tmp_path, monkeypatch, [unused, unused], recovery=NO_RETRY)
    connections = []
    def failed_connect(connection):
        connections.append(connection)
        raise socket.gaierror("synthetic name resolution failure")
    monkeypatch.setattr(_FakeHTTPSConnection, "connect", failed_connect)
    with pytest.raises(LLMInputPortFailure) as caught:
        port.request_once(attempt)
    assert (caught.value.disposition, caught.value.submission_state,
            caught.value.failure_code) == (
                "provider_retryable", "not_submitted", "connection_name_resolution_failure")
    assert len(connections) == 1 and transport.requests == []
    assert transport.actions == [unused]
    _assert_no_recovery(tmp_path, outcome="no_result", failed=True)
    port.close()


def test_no_retry_preserves_owner_interruption_before_submission(tmp_path, monkeypatch):
    port, attempt, transport = _port_and_attempt(
        tmp_path, monkeypatch, [], recovery=NO_RETRY)
    with pytest.raises(LLMInputPortInterrupted) as caught:
        port.request_once_interruptible(attempt, interruption_requested=lambda: True)
    assert caught.value.submission_state == "not_submitted"
    assert transport.requests == []
    _assert_no_recovery(tmp_path, outcome="owner_interrupted")
    port.close()


@pytest.mark.parametrize("version", ["v2", "v3"])
def test_catalog_builder_preserves_explicit_opt_out_and_selection_metadata(tmp_path, version):
    document = _external_catalog()
    document["schema_version"] = f"rpnh/provider_model_catalog/{version}"
    model = document["providers"][0]["models"][0]
    model["adapter"]["recovery"] = dict(NO_RETRY)
    if version == "v2":
        del model["reasoning_efforts"]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(document))
    output = tmp_path / "generated"
    result = build_provider_catalog(catalog, output)
    profiles = discover_profiles(output / "execution")
    assert result["profile_count"] == len(profiles) == (1 if version == "v2" else 5)
    for profile in profiles:
        selected = load_llm_execution_selection(profile.path)
        adapter = json.loads(selected.adapter_config_path.read_text())
        Draft7Validator(json.loads((SCHEMAS / "runtime" /
            f"external_provider_adapter_config.{version}.schema.json").read_text())).validate(adapter)
        assert adapter["recovery"] == NO_RETRY
        parsed = external._load_config(selected.adapter_config_path,
            selected.input_target.model_condition, selected.reasoning_effort)
        assert parsed.recovery.as_document() == NO_RETRY
        policy = selected.as_registry_policy()
        assert policy["adapter_profile"]["recovery"] == NO_RETRY
        assert selected.input_target.model_condition == model["model_condition"]
        assert policy["timeout_seconds"] == model["timeout_seconds"]
        assert policy["max_output_tokens"] == model["max_output_tokens"]
        assert policy["max_response_bytes"] == model["max_response_bytes"]
        if version == "v3":
            assert selected.logical_selection_id == model["profile"]
            assert selected.supported_reasoning_efforts == tuple(model["reasoning_efforts"]["supported"])
            assert selected.default_reasoning_effort == "medium"
            assert adapter["reasoning_effort"] == selected.reasoning_effort
        bound = build_llm_input_port(selected, destination_run_root=tmp_path / profile.name)
        assert bound.execution_policy == policy
        assert bound._port._recovery.as_document() == NO_RETRY
        bound.close()
    assert build_provider_catalog(catalog, output, check=True)["status"] == "in_sync"


def test_public_policy_opt_out_is_identity_bound_and_uses_same_adapter(tmp_path, monkeypatch):
    document = public_wire()
    for field in ("secret_binding_id", "account_binding_id", "credential_renderer_id"):
        document["policy"]["route_provenance"][0][field] = None
    document["bindings"] = []
    original = selection(document)
    document["policy"]["adapter_profile"]["recovery"] = dict(NO_RETRY)
    selected = selection(document)
    expected_identity = deepcopy(original.public_identity)
    expected_identity["policy"]["adapter_profile"]["recovery"] = dict(NO_RETRY)
    assert selected.public_identity == expected_identity
    assert wire.canonical(selected.public_identity) != wire.canonical(original.public_identity)
    bound = build_llm_input_port(selected, destination_run_root=tmp_path / "run")
    assert type(bound._port) is external.ExternalProviderInputPort
    assert bound.public_identity == selected.public_identity
    assert bound.execution_policy == selected.as_registry_policy()
    assert bound._port._recovery.as_document() == NO_RETRY
    with pytest.raises(AttributeError):
        bound._port._recovery = recovery_policy_from_document(_recovery())
    transport = _FakeHTTPSFactory([(503, b'{}', "first")])
    monkeypatch.setattr(external.http.client, "HTTPSConnection", transport)
    with pytest.raises(LLMInputPortFailure) as caught:
        bound.request_once(public_attempt(selected))
    assert caught.value.failure_code == "http_status_503"
    assert len(transport.requests) == 1
    _assert_no_recovery(tmp_path, outcome="no_result", failed=True)
    bound.close()


def test_bounded_recovery_still_never_resends_unknown_formal_submission(tmp_path, monkeypatch):
    """The opt-out must not regress the newer bounded-policy reconciliation fix."""
    unused = (200, _provider_response("unexpected resend", "third"), "third")
    port, attempt, transport = _port_and_attempt(tmp_path, monkeypatch, [
        socket.timeout("response headers timeout"),
        (200, _provider_response("healthy", "probe"), "probe"),
        unused,
    ], recovery=_recovery())
    with pytest.raises(LLMInputPortFailure) as caught:
        port.request_once(attempt)
    assert caught.value.disposition == "submission_unknown"
    assert caught.value.submission_state == "submission_unknown"
    assert caught.value.failure_code == "external_submission_unknown_requires_reconciliation"
    assert len(transport.requests) == 2 and transport.actions == [unused]
    assert [item["call_kind"] for item in _started_attempts(tmp_path)] == [
        "real_model_call", "health_probe"]
    port.close()
