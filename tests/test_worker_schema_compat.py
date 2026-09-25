"""Read both historical v4 shapes without weakening the v5 writer contract."""
from pathlib import Path

import pytest

from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec


def spec(*, plugins=False):
    return AgentTaskSpec(
        run_dir=Path('/tmp/schema-compat/run'), prompt='Preserve this task.',
        stages=(AgentStage("main", "Complete the request."),), execution_config_path=Path('/tmp/schema-compat/profile.json'),
        owner_socket_path=Path('/tmp/schema-compat/owner.sock'),
        plugin_configuration={} if plugins else None,
        plugin_catalog_digest='0' * 64 if plugins else None,
    )


@pytest.mark.parametrize('socket_path', [None, '/tmp/legacy-main.sock'])
def test_main_v4_imports_without_dropping_socket_or_profile(socket_path):
    document = spec().as_worker_document()
    document['schema_version'] = 'rpnh/agent_task_spec/v4'
    document['owner_socket_path'] = socket_path
    restored = AgentTaskSpec.from_worker_document(document)
    current = restored.as_worker_document()
    assert current == {**document, 'schema_version': 'rpnh/agent_task_spec/v5'}


def test_plugin_v4_keeps_pinned_catalog_and_migrates_to_v5():
    document = spec(plugins=True).as_worker_document()
    document['schema_version'] = 'rpnh/agent_task_spec/v4'
    del document['owner_socket_path']
    restored = AgentTaskSpec.from_worker_document(document)
    assert restored.plugin_catalog_digest == '0' * 64
    assert dict(restored.plugin_configuration) == {}
    assert restored.as_worker_document() == {
        **document, 'schema_version': 'rpnh/agent_task_spec/v5',
        'owner_socket_path': None,
    }


def test_v4_hybrid_cannot_guess_between_independent_legacy_formats():
    document = spec(plugins=True).as_worker_document()
    document['schema_version'] = 'rpnh/agent_task_spec/v4'
    with pytest.raises(ValueError, match='worker document'):
        AgentTaskSpec.from_worker_document(document)


@pytest.mark.parametrize('version', ['v4', 'v5'])
@pytest.mark.parametrize('invalid', [1, True, {}])
def test_both_socket_formats_reject_non_string_paths(version, invalid):
    document = spec().as_worker_document()
    document['schema_version'] = f'rpnh/agent_task_spec/{version}'
    document['owner_socket_path'] = invalid
    with pytest.raises(ValueError, match='worker document'):
        AgentTaskSpec.from_worker_document(document)


@pytest.mark.parametrize('version', ['v4', 'v5'])
def test_incomplete_plugin_authority_is_rejected(version):
    document = spec(plugins=True).as_worker_document()
    document['schema_version'] = f'rpnh/agent_task_spec/{version}'
    del document['plugin_catalog_digest']
    if version == 'v4':
        del document['owner_socket_path']
    with pytest.raises(ValueError, match='worker document'):
        AgentTaskSpec.from_worker_document(document)


def test_unknown_main_v4_field_is_not_silently_dropped():
    document = spec().as_worker_document()
    document.update(schema_version='rpnh/agent_task_spec/v4', unknown=True)
    with pytest.raises(ValueError, match='worker document'):
        AgentTaskSpec.from_worker_document(document)


def test_v5_roundtrip_keeps_plugins_and_owner_identity_together():
    original = spec(plugins=True)
    assert AgentTaskSpec.from_worker_document(original.as_worker_document()) == original
