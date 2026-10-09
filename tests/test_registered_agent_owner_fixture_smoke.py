"""Fixture sentinels only; no public-material/native completion claim."""
import json

import pytest

from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.registry.operations import OperationAuthorityError
from cpn.rpnh.harness import OperationDispatch
from registered_agent_owner_fixtures import (
    prepare_neutral_attempt, registered_host_request,
    start_optional_owner, start_registered_host_owner,
)


class NeverRequestedPort:
    """Legacy fixture port, deliberately not public Bound-port evidence."""
    def __init__(self, policy):
        self.execution_policy = policy
        self.calls = 0

    def request_once(self, attempt):
        self.calls += 1
        raise AssertionError('fixture smoke may not request a model')

    def close(self):
        pass


def _inputs():
    target = LLMInputTarget('offline-owner-fixture', 32, 65536,
                            32768, 4096)
    policy = {
        'adapter_kind': 'offline-test', 'timeout_seconds': 30,
        'max_output_tokens': target.max_output_tokens,
        'max_response_bytes': target.max_response_bytes,
        'route_provenance': [{
            'route_id': 'offline-test', 'provider': 'offline-test',
            'backend': 'offline-test', 'transport': 'offline-test',
            'outbound_model': target.model_condition,
        }],
        'adapter_profile': {'config_schema_version': 'offline-test/v1'},
    }
    return target, policy, NeverRequestedPort(policy)


def test_original_owner_loop_constructor_and_gateway_use_memory_transport(
        tmp_path, monkeypatch):
    target, policy, port = _inputs()
    case = start_optional_owner(tmp_path / 'owner', monkeypatch,
                                target=target, policy=policy, port=port)
    try:
        assert case.event_loop.socket_path.is_file()  # inert ordinary marker
        dispatch = case.prepare_dispatcher()
        assert type(dispatch) is OperationDispatch
        assert dispatch.execution is case.execution
        assert case.gateway_call('operation_interruption_requested',
                                 case.execution) is False
        counts = case.boundaries.counts
        assert counts['memory_socket'] == 1
        assert counts['memory_socketpair'] == 1
        assert counts['memory_selector'] == 1
        assert counts['memory_bind'] == 1
        assert counts['memory_register'] == 2
        assert counts['memory_send'] == 1
        assert counts['memory_recv'] == 1
        assert counts['synthetic_inventory_capture'] == 1
        assert port.calls == 0
        print('optional_owner_transport_counts=' + json.dumps(dict(counts), sort_keys=True))
    finally:
        case.close()
    assert not case.event_loop.socket_path.exists()
    assert all(channel.closed for channel in case.boundaries.sockets)


def test_real_neutral_attempt_stops_before_materialization(tmp_path, monkeypatch):
    target, policy, port = _inputs()
    case = start_optional_owner(tmp_path / 'owner', monkeypatch,
                                target=target, policy=policy, port=port)
    try:
        attempt = prepare_neutral_attempt(case)
        assert attempt.invocation_ref.entity_type == 'llm_invocation_spec/v1'
        assert attempt.attempt_ref.entity_type == 'llm_invocation_attempt/v1'
        verified_execution, provider = case.services._optional_input_authority(
            case.execution, attempt)
        assert verified_execution is case.execution
        assert provider.call.ref.entity_type == 'llm_call_spec/v2'
        events = case.owner._core.event_store.list_events()
        assert not [event for event in events if event.event_type in {
            'provider_payload_materialization_recorded/v1',
            'provider_attempt_dispatch_started/v2',
            'provider_attempt_submission_permitted/v1',
        }]
        assert port.calls == 0
        print('neutral_attempt_pre_materialization_counts=' + json.dumps(
            dict(case.boundaries.counts), sort_keys=True))
    finally:
        case.close()


def test_real_registered_host_reader_is_independent_of_neutral_loop(
        tmp_path, monkeypatch):
    target, policy, port = _inputs()
    case = start_registered_host_owner(tmp_path / 'owner', monkeypatch,
                                      target=target, policy=policy, port=port)
    try:
        service = case.services._registered_host_llm_service
        authority = service._request_authority(case.execution,
                                               registered_host_request(), policy)
        assert authority.target_document == target.as_registry_document()
        assert authority.route['schema_version'] == 'registered_host_execution_provenance/v1'
        with pytest.raises(OperationAuthorityError, match='differs from firing bindings'):
            service.prepare(case.execution, registered_host_request(),
                            {**policy, 'adapter_kind': 'mismatched-route'})
        assert not case.owner._core.event_store.object_rows_by_type('agent_loop/v1')
        assert not case.owner._core.event_store.object_rows_by_type('llm_call_spec/v3')
        assert case.boundaries.counts['synthetic_inventory_capture'] == 0
        assert port.calls == 0
        print('registered_host_reader_counts=' + json.dumps(
            dict(case.boundaries.counts), sort_keys=True))
    finally:
        case.close()
