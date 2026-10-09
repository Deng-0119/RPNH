"""Staged R2 regressions: original legacy call shapes and optional HOST.

This file is intentionally outside the frozen R1 source. The real-owner test
uses original constructors and gates with only the declared in-memory OS
boundary. No socket, subprocess, numerical worker, or model is executed.
"""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

# Temporary staging only; unnecessary once moved into source/tests for R2.
if Path(__file__).resolve().parent.name == 'tools':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source' / 'tests'))

from cpn.components.registered_host_llm import (
    RegisteredHostLLM, RegisteredHostLLMRegistryService,
)
from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.registry.schema_catalog import canonical_json
from registered_agent_owner_fixtures import (
    registered_host_request, start_optional_owner,
)


def legacy_policy():
    return {
        'adapter_kind': 'external_provider', 'timeout_seconds': 30,
        'max_output_tokens': 64, 'max_response_bytes': 65536,
        'route_provenance': [{
            'route_id': 'legacy', 'provider': 'offline-provider',
            'backend': 'offline-backend', 'transport': 'https',
            'protocol': 'openai_chat_completions/v1',
            'endpoint': 'https://synthetic.example.invalid/v1/chat/completions',
            'outbound_model': 'legacy-model',
        }],
        'adapter_profile': {
            'config_schema_version': 'external_provider_adapter_config/v2',
            'route_count': 1,
        },
    }


def test_legacy_registered_host_request_keeps_three_argument_gateway():
    calls = []
    class LegacyGateway:
        def reconcile_registered_host_llm(self, execution, payload, policy):
            calls.append((execution, payload, policy))
            return SimpleNamespace(classification='semantic_success', response=b'previously-registered-response')
    class LegacyPort:
        execution_policy = legacy_policy()
        def request_once(self, _attempt):
            pytest.fail('recovered semantic success must not issue a request')
    capability = RegisteredHostLLM(
        execution='legacy-execution', gateway=LegacyGateway(), input_port=LegacyPort(),
        interruption_requested=lambda: False)
    request = registered_host_request()
    assert capability.request(request) == b'previously-registered-response'
    assert len(calls) == 1
    assert calls[0][:2] == ('legacy-execution', request)


def test_legacy_reconcile_keeps_three_argument_classify_override():
    plan = SimpleNamespace(classification='semantic_success', response=b'preserved')
    calls = []
    class LegacyService(RegisteredHostLLMRegistryService):
        def __init__(self):
            pass  # Signature-only test; no Registry action is requested.
        def classify_resume(self, execution, request_bytes, execution_policy):
            calls.append((execution, request_bytes, execution_policy))
            return plan
    policy = legacy_policy()
    request = registered_host_request()
    assert LegacyService().reconcile('execution', request, policy) is plan
    assert calls == [('execution', request, policy)]


def test_legacy_reconcile_keeps_three_argument_prepare_override():
    class ReachedLegacyPrepare(Exception):
        pass
    class LegacyService(RegisteredHostLLMRegistryService):
        def __init__(self):
            pass
        def classify_resume(self, execution, request_bytes, execution_policy, **kwargs):
            return SimpleNamespace(classification='fresh')
        def prepare(self, execution, request_bytes, execution_policy):
            raise ReachedLegacyPrepare('original prepare signature reached')
    with pytest.raises(ReachedLegacyPrepare, match='original prepare signature'):
        LegacyService().reconcile('execution', registered_host_request(), legacy_policy())


def test_legacy_optional_host_without_backend_can_prepare_actual_dispatcher(tmp_path, monkeypatch):
    from cpn.components.agent_loop import optional_host_bindings
    original = optional_host_bindings.make_optional_agent_host_bindings
    factory_calls = []
    def target_only(target, **_ignored_configuration):
        # Exercise the original documented optional-backend default. This
        # changes fixture input only; it does not replace any Registry gate.
        factory_calls.append(target)
        return original(target)
    monkeypatch.setattr(optional_host_bindings, 'make_optional_agent_host_bindings', target_only)
    policy = legacy_policy()
    class LegacyPort:
        execution_policy = policy
        def request_once(self, _attempt):
            pytest.fail('dispatcher preparation must not execute a model')
    target = LLMInputTarget('legacy-model', 64, 65536)
    case = start_optional_owner(tmp_path / 'owner', monkeypatch,
        target=target, policy=policy, port=LegacyPort())
    try:
        assert factory_calls == [target]
        # Check this was really the target-only HOST publication, rather than
        # a test that silently rebuilt the two absent provenance documents.
        with case.owner._core.event_store.connect() as db:
            roles = {row[0] for row in db.execute(
                "SELECT json_extract(metadata_json,'$.descriptors.content_role') "
                "FROM objects WHERE object_type='resource_version/v1'")}
        assert 'optional_agent_backend' not in roles
        assert 'optional_agent_transport' not in roles
        dispatcher = case.prepare_dispatcher()
        assert dispatcher is not None
        case.boundaries.assert_no_external_calls()
    finally:
        case.close()
