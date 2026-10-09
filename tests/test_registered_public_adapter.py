"""Real public adapter/Spec branches against synthetic, non-authority wire.

These are offline parser and adapter contract tests, not registered-material
root acceptance. No provider, native worker, subprocess, or model is called.
"""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from cpn.llm_adapters import external_provider as external
from cpn.llm_adapters import factory
from cpn.llm_adapters.config import RegisteredLLMExecutionSelection
from cpn.llm_adapters.public_credentials import (
    PublicCredentialCapability, bearer_secret_slot,
)
from cpn.rpnh import public_material_contracts as wire
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputPortFailure
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from registered_public_adapter_fixtures import public_wire
from test_external_provider_health_recovery import (
    _FakeHTTPSFactory, _provider_response,
)


def selection(document=None):
    return RegisteredLLMExecutionSelection(**(public_wire() if document is None else document))


def capabilities(selected, calls, *, secret='synthetic-secret-value', renderer=bearer_secret_slot):
    identity = selected.public_identity
    def resolve(binding):
        calls.append(binding)
        return secret
    return tuple(PublicCredentialCapability(
        wire.canonical(identity[role]['identity']),
        wire.canonical(identity[role]['observation']), callback,
    ) for role, callback in (('resolver', resolve), ('renderer', renderer)))


def attempt(selected, *, model=None):
    target = selected.input_target
    model = target.model_condition if model is None else model
    return LLMCallAttempt(
        invocation_ref=VersionRef('llm_invocation_spec/v1', new_id('llm_invocation'), new_id('llm_invocation_version')),
        attempt_ref=VersionRef('llm_invocation_attempt/v1', new_id('llm_invocation_attempt'), new_id('llm_invocation_attempt_version')),
        attempt_ordinal=0, model_condition=model,
        canonical_request_bytes=json.dumps({
            'protocol': 'llm_request_envelope/v1', 'model_condition': model,
            'max_output_tokens': target.max_output_tokens,
            'messages': [{'role': 'user', 'content': 'Synthetic offline adapter contract.'}],
            'tools': [], 'tool_choice': 'none',
        }, sort_keys=True, separators=(',', ':')).encode(),
        max_response_bytes=target.max_response_bytes,
    )


def fake_transport(monkeypatch, count=1):
    transport = _FakeHTTPSFactory([(200, _provider_response('offline', 'synthetic-id'), 'synthetic-id')] * count)
    transport.constructions = 0
    def construct(*args, **kwargs):
        transport.constructions += 1
        return transport(*args, **kwargs)
    monkeypatch.setattr(external.http.client, 'HTTPSConnection', construct)
    monkeypatch.setattr(external.http.client, 'HTTPConnection', lambda *a, **k: pytest.fail('unexpected HTTP route'))
    monkeypatch.setattr(external.ssl, 'create_default_context', lambda: object())
    return transport


def test_public_selection_copies_full_binding_and_observations_without_paths():
    document = public_wire()
    selected = selection(document)
    identity = selected.public_identity
    assert identity['binding'] == document['bindings'][0]
    assert identity['adapter_observation'] == document['observations']['u.adapter']
    for role in ('resolver', 'renderer'):
        assert identity[role]['observation'] == document['observations']['u.credentials']
        assert identity[role]['identity']['role'] == role
    assert not hasattr(selected, 'adapter_config_path')
    document['bindings'][0]['binding_revision'] = 'changed'
    document['policy']['runtime']['workspace']['timeout_seconds'] = 999
    identity['policy']['model_condition'] = 'changed'
    selected.as_registry_policy()['route_provenance'][0]['endpoint'] = 'https://other.invalid/'
    assert selected.public_identity == selection().public_identity
    with pytest.raises(FrozenInstanceError):
        selected._identity_bytes = b'{}'


def test_original_target_runtime_recovery_reasoning_are_fully_preserved(tmp_path):
    selected = selection()
    calls = []
    resolver, renderer = capabilities(selected, calls)
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)
    port = bound._port
    assert type(port) is external.ExternalProviderInputPort
    assert type(bound) is factory.BoundLLMInputPort
    assert selected.input_target.context_window_tokens == 32768
    assert selected.input_target.context_compaction_retained_tokens == 4096
    target = selected.input_target.as_registry_document()
    assert target['context_window_tokens'] == 32768 and target['context_compaction_retained_tokens'] == 4096
    assert selected.runtime_policy.context_pressure_trigger_ratio == 0.73
    assert wire.canonical(selected.runtime_policy.as_document()) == wire.canonical(selected.as_registry_policy()['runtime'])
    assert port._recovery.as_document() == selected.as_registry_policy()['adapter_profile']['recovery']
    assert port._reasoning_effort == 'high'
    assert port._timeout_seconds == selected.timeout_seconds == 120
    assert port._model_condition == port._routes[0].outbound_model == selected.input_target.model_condition
    assert bound.public_identity == selected.public_identity
    assert bound.execution_policy == selected.as_registry_policy()
    assert calls == []
    bound.close()


def test_fresh_public_port_never_reads_private_configuration_or_old_audit(tmp_path, monkeypatch):
    private = tmp_path / 'nonempty-private-config.json'
    private.write_text('{"routes":[{"secret":"synthetic-only","model":"wrong"}]}')
    original_read, original_text, original_stat = Path.read_bytes, Path.read_text, Path.stat
    def guarded(original):
        def call(path, *a, **k):
            if path == private:
                pytest.fail('public adapter touched private configuration')
            return original(path, *a, **k)
        return call
    monkeypatch.setattr(Path, 'read_bytes', guarded(original_read))
    monkeypatch.setattr(Path, 'read_text', guarded(original_text))
    monkeypatch.setattr(Path, 'stat', guarded(original_stat))
    def forbidden(*a, **k):
        pytest.fail('public adapter invoked legacy private or audit route loader')
    monkeypatch.setattr(factory, '_private_kind', forbidden)
    monkeypatch.setattr(external, '_load_config', forbidden)
    monkeypatch.setattr(external.ExternalProviderInputPort, '__init__', forbidden)
    monkeypatch.setattr(external.PrivateAttemptAudit, 'latest_selected_external_route_id', forbidden)
    selected = selection()
    calls = []
    resolver, renderer = capabilities(selected, calls)
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path / 'run', resolver=resolver, renderer=renderer)
    assert bound._port._active_route_index == 0
    assert calls == []
    bound.close()


@pytest.mark.parametrize('damage', ['model', 'timeout_bool', 'ratio_int', 'missing_runtime', 'missing_context', 'bad_context', 'half_auth', 'private_header', 'unknown_adapter'])
def test_public_policy_bad_shapes_are_rejected_before_port_construction(damage):
    doc = public_wire()
    policy = doc['policy']
    if damage == 'model': policy['route_provenance'][0]['outbound_model'] = 'another-model'
    elif damage == 'timeout_bool': policy['timeout_seconds'] = True
    elif damage == 'ratio_int': policy['runtime']['context_pressure_trigger_ratio'] = 1
    elif damage == 'missing_runtime': del policy['runtime']['workspace']['timeout_seconds']
    elif damage == 'missing_context': del policy['context_window_tokens']
    elif damage == 'bad_context': policy['context_compaction_retained_tokens'] = policy['context_window_tokens']
    elif damage == 'half_auth': policy['route_provenance'][0]['account_binding_id'] = None
    elif damage == 'private_header': policy['route_provenance'][0]['headers'] = {'Authorization': 'synthetic'}
    else: policy['adapter_kind'] = 'local_process'
    with pytest.raises(Exception): selection(doc)


@pytest.mark.parametrize('damage', ['missing_observation', 'observation_body', 'adapter_hash', 'binding_hash', 'binding_account', 'binding_service', 'resolver_revision', 'extra_binding'])
def test_public_binding_and_complete_observation_joins_fail_closed(damage):
    doc = public_wire()
    binding = doc['bindings'][0]
    if damage == 'missing_observation': del doc['observations']['u.credentials']
    elif damage == 'observation_body': doc['observations']['u.credentials']['runtime']['version'] = '3.99.0'
    elif damage == 'adapter_hash': doc['policy']['implementation_digest'] = '0' * 64
    elif damage == 'binding_hash': binding['renderer_implementation_sha256'] = '0' * 64
    elif damage == 'binding_account': binding['account_binding_id'] = 'other-account'
    elif damage == 'binding_service': binding['service_id'] = 'other-service'
    elif damage == 'resolver_revision': binding['resolver_revision'] = 'r2'
    else: doc['bindings'].append(dict(binding, binding_id='unused'))
    with pytest.raises(Exception): selection(doc)


def test_binding_revision_changes_actual_port_identity_with_identical_policy(tmp_path):
    doc = public_wire()
    first = selection(doc)
    doc['bindings'][0]['binding_revision'] = 'r2'
    second = selection(doc)
    assert wire.canonical(first.as_registry_policy()) == wire.canonical(second.as_registry_policy())
    ports = []
    for index, selected in enumerate((first, second)):
        caps = capabilities(selected, [])
        ports.append(factory.build_llm_input_port(selected, destination_run_root=tmp_path / str(index), resolver=caps[0], renderer=caps[1]))
    assert ports[0].public_identity['binding'] != ports[1].public_identity['binding']
    for port in ports: port.close()


@pytest.mark.parametrize('damage', ['identity_role', 'identity_revision', 'observation_bytes', 'plain_callable'])
def test_from_public_requires_exact_original_capability_association(tmp_path, damage):
    selected = selection()
    resolver, renderer = capabilities(selected, [])
    if damage.startswith('identity_'):
        identity = wire.decode(resolver.identity_bytes)
        identity['role' if damage == 'identity_role' else 'capability_revision'] = 'renderer' if damage == 'identity_role' else 'r2'
        resolver = replace(resolver, identity_bytes=wire.canonical(identity))
    elif damage == 'observation_bytes':
        observation = wire.decode(resolver.observation_bytes)
        observation['runtime']['version'] = '3.99.0'
        resolver = replace(resolver, observation_bytes=wire.canonical(observation))
    else: resolver = lambda binding: 'synthetic'
    with pytest.raises((TypeError, ValueError)):
        external.ExternalProviderInputPort.from_public(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)


@pytest.mark.parametrize('field', ['_public_resolver', '_public_renderer', '_public_identity_bytes', '_public_association', '_routes', '_model_condition', '_reasoning_effort'])
def test_provider_identity_is_frozen_after_public_construction(tmp_path, field):
    selected = selection()
    caps = capabilities(selected, [])
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=caps[0], renderer=caps[1])
    with pytest.raises(AttributeError): setattr(bound._port, field, None)
    with pytest.raises(AttributeError): bound._port = object()
    with pytest.raises(FrozenInstanceError): caps[0].callback = lambda binding: 'other'
    bound.close()


def test_public_identity_rechecks_capability_association_on_access(tmp_path):
    selected = selection()
    resolver, renderer = capabilities(selected, [])
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)
    original = resolver.callback
    # Deliberate fault injection; this is a HOST consistency boundary, not a sandbox.
    object.__setattr__(resolver, 'callback', lambda binding: 'replacement')
    try:
        with pytest.raises(ValueError, match='association'): _ = bound.public_identity
    finally:
        object.__setattr__(resolver, 'callback', original)
        bound.close()


def test_original_request_injects_secret_only_at_transport_time(tmp_path, monkeypatch):
    selected = selection()
    resolved = []
    resolver, renderer = capabilities(selected, resolved)
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)
    transport = fake_transport(monkeypatch)
    assert resolved == [] and transport.requests == []
    bound.request_once(attempt(selected))
    assert resolved == [selected.public_identity['binding']]
    assert len(transport.requests) == 1
    assert transport.constructions == 1
    sent = transport.requests[0]
    assert sent['headers']['Authorization'] == 'Bearer synthetic-secret-value'
    body = json.loads(sent['body'])
    assert body['model'] == selected.input_target.model_condition
    assert body['reasoning_effort'] == 'high'
    assert b'synthetic-secret-value' not in wire.canonical(bound.public_identity)
    audit = (tmp_path / 'adapter-private' / 'llm-attempts.jsonl').read_bytes()
    assert b'synthetic-secret-value' not in audit
    bound.close()


@pytest.mark.parametrize('secret', [None, {}, '', 'bad\nsecret', 'nonascii-\u00e9'])
def test_bad_secret_never_reaches_even_fake_transport(tmp_path, monkeypatch, secret):
    selected = selection()
    resolved = []
    resolver, renderer = capabilities(selected, resolved, secret=secret)
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)
    transport = fake_transport(monkeypatch, count=0)
    with pytest.raises(LLMInputPortFailure): bound.request_once(attempt(selected))
    assert resolved and transport.requests == [] and transport.constructions == 0
    bound.close()


def test_actual_call_wrong_model_stops_before_resolver_and_transport(tmp_path, monkeypatch):
    selected = selection()
    resolved = []
    resolver, renderer = capabilities(selected, resolved)
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)
    transport = fake_transport(monkeypatch, count=0)
    with pytest.raises((ValueError, LLMInputPortFailure)): bound.request_once(attempt(selected, model='wrong-model'))
    assert resolved == [] and transport.requests == [] and transport.constructions == 0
    bound.close()


def test_unauthenticated_public_port_has_no_credential_capability(tmp_path, monkeypatch):
    doc = public_wire()
    for field in ('secret_binding_id', 'account_binding_id', 'credential_renderer_id'):
        doc['policy']['route_provenance'][0][field] = None
    doc['bindings'] = []
    selected = selection(doc)
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path)
    assert bound.public_identity['binding'] is None
    assert bound.public_identity['resolver'] is None and bound.public_identity['renderer'] is None
    transport = fake_transport(monkeypatch)
    bound.request_once(attempt(selected))
    assert 'Authorization' not in transport.requests[0]['headers']
    with pytest.raises(ValueError):
        external.ExternalProviderInputPort.from_public(selected, destination_run_root=tmp_path, resolver=lambda: None)
    bound.close()


def test_null_context_uses_original_target_omission_and_full_backend_policy():
    from cpn.rpnh.agent_tasks import _execution_route
    document = public_wire()
    document['policy']['context_window_tokens'] = None
    document['policy']['context_compaction_retained_tokens'] = None
    selected = selection(document)
    target = selected.input_target.as_registry_document()
    assert 'context_window_tokens' not in target and 'context_compaction_retained_tokens' not in target
    backend = _execution_route(selected)
    assert backend['model'] == selected.input_target.model_condition
    assert backend['selection']['context_window_tokens'] is None
    assert backend['selection']['context_compaction_retained_tokens'] is None
    assert wire.canonical(backend['selection']) == wire.canonical(document['policy'])
    assert backend['selection']['runtime']['context_pressure_trigger_ratio'] == 0.73


def test_from_public_independently_rejects_model_mismatch_before_audit(tmp_path, monkeypatch):
    selected = selection()
    resolver, renderer = capabilities(selected, [])
    identity = selected.public_identity
    identity['policy']['route_provenance'][0]['outbound_model'] = 'wrong-physical-model'
    # Deliberately bypass the earlier parser to exercise from_public's own check.
    object.__setattr__(selected, '_identity_bytes', wire.canonical(identity))
    def forbidden(*a, **k): pytest.fail('model mismatch reached audit constructor')
    monkeypatch.setattr(external, 'PrivateAttemptAudit', forbidden)
    with pytest.raises(external.AdapterConfigError, match='exact model'):
        external.ExternalProviderInputPort.from_public(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)


@pytest.mark.parametrize('kind', ['resolver_failure', 'renderer_failure', 'renderer_wrong_header', 'renderer_extra_headers'])
def test_credential_capability_failure_precedes_transport_constructor(tmp_path, monkeypatch, kind):
    selected = selection()
    resolved = []
    resolver, renderer = capabilities(selected, resolved)
    def failed(*a): raise RuntimeError('synthetic credential veto')
    if kind == 'resolver_failure': resolver = replace(resolver, callback=failed)
    elif kind == 'renderer_failure': renderer = replace(renderer, callback=failed)
    elif kind == 'renderer_wrong_header': renderer = replace(renderer, callback=lambda secret: ('X-Secret', secret))
    else: renderer = replace(renderer, callback=lambda secret: {'Authorization': 'Bearer ' + secret, 'X-Other': 'wrong'})
    bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path, resolver=resolver, renderer=renderer)
    transport = fake_transport(monkeypatch, count=0)
    with pytest.raises(LLMInputPortFailure): bound.request_once(attempt(selected))
    assert transport.constructions == 0 and transport.requests == []
    bound.close()


def test_secret_rotation_does_not_change_public_adapter_identity(tmp_path, monkeypatch):
    selected = selection()
    transport = fake_transport(monkeypatch, count=2)
    identities = []
    for index, secret in enumerate(('synthetic-first', 'synthetic-rotated')):
        resolver, renderer = capabilities(selected, [], secret=secret)
        bound = factory.build_llm_input_port(selected, destination_run_root=tmp_path / str(index), resolver=resolver, renderer=renderer)
        identities.append(wire.canonical(bound.public_identity))
        bound.request_once(attempt(selected))
        bound.close()
    assert identities[0] == identities[1]
    assert [request['headers']['Authorization'] for request in transport.requests] == ['Bearer synthetic-first', 'Bearer synthetic-rotated']
    assert transport.constructions == 2


def test_bound_public_wrapper_rejects_self_reported_non_adapter():
    selected = selection()
    class PretendPort:
        public_identity = selected.public_identity
        execution_policy = selected.as_registry_policy()
    with pytest.raises(TypeError, match='original provider'):
        factory.BoundLLMInputPort(PretendPort(), selected.as_registry_policy())


def public_spec(tmp_path):
    run = tmp_path / 'runs' / 'sealed' / 'run'
    return AgentTaskSpec(run_dir=run, prompt='Synthetic public task.',
        stages=(AgentStage('answer', 'Return the registered result.'),),
        execution_config_path=None, max_parallel_nodes=1,
        owner_socket_path=run / 'owner.sock',
        registered_execution_sources=(('default', 'policy-default'),))


def test_spec_v13_final_root_roundtrip_preserves_legal_sibling(tmp_path, monkeypatch):
    spec = public_spec(tmp_path)
    root = tmp_path / 'control' / 'bound_tasks' / 'sealed'
    doc = spec.as_worker_document(document_root=root)
    assert doc['schema_version'] == 'rpnh/agent_task_spec/v13'
    assert doc['run_relative_path'].startswith('../')
    assert doc['owner_socket_relative_path'] == 'owner.sock'
    assert 'execution_config_relative_path' not in doc and 'execution_config_path' not in doc
    assert AgentTaskSpec.from_worker_document(doc, document_root=root) == spec
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert AgentTaskSpec.from_worker_document(doc, document_root=root) == spec
    assert not spec.run_dir.exists()
    with pytest.raises(ValueError): spec.as_worker_document()
    with pytest.raises(ValueError): AgentTaskSpec.from_worker_document(doc)


@pytest.mark.parametrize('field,value', [
    ('execution_config_path', '/private/profile.json'),
    ('execution_config_relative_path', '../private/profile.json'),
    ('execution_profiles', {}), ('plugin_configuration', {}),
    ('managed_bindings', {}), ('tool_program_policy', {}),
    ('owner_socket_relative_path', None), ('owner_socket_relative_path', '../escape.sock'),
    ('workflow_graph', {}), ('max_parallel_nodes', 2), ('max_parallel_nodes', True),
    ('max_parallel_nodes', 1.0), ('registered_execution_sources', {'other': 'policy-default'}),
])
def test_spec_v13_rejects_legacy_mixed_and_unsupported_fields(tmp_path, field, value):
    root = tmp_path / 'control'
    doc = public_spec(tmp_path).as_worker_document(document_root=root)
    doc[field] = value
    with pytest.raises(Exception): AgentTaskSpec.from_worker_document(doc, document_root=root)


@pytest.mark.parametrize('kwargs', [
    {'execution_config_path': Path('/private/profile.json')},
    {'execution_profiles': (('other', Path('/private/profile.json')),)},
    {'plugin_configuration': {}, 'plugin_catalog_digest': '0' * 64},
    {'max_parallel_nodes': 2}, {'max_parallel_nodes': True}, {'max_parallel_nodes': 1.0},
    {'registered_execution_sources': (('other', 'policy-default'),)},
])
def test_spec_public_object_rejects_legacy_source_and_wrong_types(tmp_path, kwargs):
    with pytest.raises((TypeError, ValueError)):
        replace(public_spec(tmp_path), **kwargs)


@pytest.mark.parametrize('method', ['run_agent_task', 'resume_agent_task', 'reopen_agent_task'])
def test_public_spec_cannot_enter_unbound_legacy_execution(tmp_path, monkeypatch, method):
    from cpn.rpnh import agent_tasks
    def forbidden(*a, **k): pytest.fail('unbound public Spec reached private selection loader')
    monkeypatch.setattr(agent_tasks, 'load_llm_execution_selection', forbidden)
    kwargs = {'checkpoint_version_id': 'checkpoint:synthetic', 'command_id': 'synthetic', 'reason': 'offline'} if method == 'reopen_agent_task' else {}
    with pytest.raises((ValueError, RuntimeError)):
        getattr(agent_tasks, method)(public_spec(tmp_path), **kwargs)


def test_legacy_spec_none_socket_and_bound_port_api_remain_compatible(tmp_path):
    legacy = AgentTaskSpec(run_dir=tmp_path / 'legacy', prompt='Legacy task.',
        stages=(AgentStage('main', 'Complete legacy request.'),),
        execution_config_path=tmp_path / 'private-unread.json', owner_socket_path=None)
    for root in (None, tmp_path / 'control'):
        document = legacy.as_worker_document(document_root=root)
        assert document['schema_version'] == ('rpnh/agent_task_spec/v5' if root is None else 'rpnh/agent_task_spec/v6')
        assert AgentTaskSpec.from_worker_document(document, document_root=root) == legacy
    class LegacyPort:
        def request_once(self, value): return value
        def close(self): pass
    port = factory.BoundLLMInputPort(LegacyPort(), {'adapter_kind': 'external_provider', 'timeout_seconds': 17})
    assert port.execution_policy['timeout_seconds'] == 17
    assert port.request_once('legacy') == 'legacy'
    port.close()



def test_new_public_containers_cannot_be_mutable_or_custom_legacy_bridges(tmp_path):
    from collections import UserDict
    document=public_wire()
    with pytest.raises(TypeError):
        RegisteredLLMExecutionSelection(**{**document,'bindings':tuple(document['bindings'])})
    with pytest.raises(TypeError):
        RegisteredLLMExecutionSelection(**{**document,'observations':UserDict(document['observations'])})
    spec=public_spec(tmp_path)
    with pytest.raises(TypeError): replace(spec,registered_execution_sources=[])
    wire_spec=spec.as_worker_document(document_root=tmp_path)
    with pytest.raises(TypeError): AgentTaskSpec.from_worker_document(UserDict(wire_spec),document_root=tmp_path)
