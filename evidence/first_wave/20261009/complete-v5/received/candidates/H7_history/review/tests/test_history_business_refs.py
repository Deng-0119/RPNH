"""Real API payloads may contain reference-shaped user data without authority."""
import json
import pytest
import parent_child_fixtures as fixtures
from test_acceptance_history import assertion_for, database_contents
from cpn.rpnh.registry.acceptance_history import VALID
from cpn.rpnh.registry.identities import new_id


@pytest.mark.parametrize('shape', ['version_ref', 'resource_ref'])
def test_user_configuration_reference_shape_does_not_create_dependency(tmp_path, monkeypatch, shape):
    original = fixtures.canonical_json
    value = ({'entity_type':'task/v1', 'logical_id':str(new_id('task')), 'version_id':str(new_id('task_version'))}
        if shape == 'version_ref' else {'resource_id':str(new_id('resource')), 'resource_version_id':str(new_id('resource_version'))})
    def encoded(body):
        if isinstance(body, dict) and body.get('schema_version') == fixtures.h7.REQUEST_SCHEMA:
            body = {**body, 'public_configuration':{**body['public_configuration'], 'opaque_example':value}}
        return original(body)
    monkeypatch.setattr(fixtures, 'canonical_json', encoded)
    owner,*_,acceptance = fixtures.accepted_parent(tmp_path / 'parent')
    store = owner._core.event_store
    query = assertion_for(owner, acceptance)
    body = owner._core.get_version(acceptance.version_id).metadata
    intent = owner._core.get_version(fixtures._version_from_payload(body['intent_ref']).version_id).metadata
    assert intent['request']['public_configuration']['opaque_example'] == value
    before = database_contents(store)
    result = store.classify_child_acceptance_history(query)
    assert result.status == VALID, result
    assert database_contents(store) == before


def test_original_operation_config_is_opaque_business_data(tmp_path, monkeypatch):
    from cpn.rpnh.module import ModuleDeclaration
    original = fixtures._simple_module
    # Allowed by the real registered config schema: each input mode is read/consume.
    example = {'entity_type':'read', 'logical_id':'consume', 'version_id':'read'}
    def module(*args, **kwargs):
        body = original(*args, **kwargs).to_dict()
        body['components'][0]['operations'][0]['config'] = {'input_modes':example}
        return ModuleDeclaration.from_dict(body)
    monkeypatch.setattr(fixtures, '_simple_module', module)
    owner,*_,acceptance = fixtures.accepted_parent(tmp_path / 'parent')
    store = owner._core.event_store
    query = assertion_for(owner, acceptance)
    before = database_contents(store)
    result = store.classify_child_acceptance_history(query)
    assert result.status == VALID, result
    assert database_contents(store) == before
