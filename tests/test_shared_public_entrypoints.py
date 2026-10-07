"""Shared integration unit checks; no business, browser or authority claims."""
from pathlib import Path
import json
import os
from types import SimpleNamespace
import sys

import pytest

from cpn.rpnh.collaboration import read_host_config as config
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError


def reference(kind, identity, version):
    return {'entity_type': kind, 'logical_id': identity + ':' + 'a' * 32,
            'version_id': version + ':' + 'b' * 32}


def document(tmp_path):
    task = reference('task/v1', 'task', 'task_version')
    return {'schema_version': config.CONFIG_SCHEMA, 'purpose': 'comparison', 'sources': [{
        'source_ref': {'schema_version': 'rpnh/collaboration/source_version_ref/v1',
                       'source_id': 'isolated-source', 'ref': task},
        'access_path': 'local-observer', 'registry_root': str(tmp_path / 'registry'),
        'binding_generation': '1', 'observer_context': {
            'observer_principal_ref': reference('principal/v1', 'principal', 'principal_version'),
            'observer_profile_ref': reference('registry_observer_profile/v2', 'resource', 'resource_version'),
            'grant_ref': reference('registry_observer_grant/v2', 'resource', 'resource_version'),
            'task_ref': task, 'issued_writer_fencing_epoch': 1,
            'issued_task_control_sequence': 0, 'reader_fence': 'test-fence',
            'expires_at': '2099-01-01T00:00:00Z', 'purpose': 'comparison'}}]}


def write(tmp_path, value=None):
    path = tmp_path / 'read-host.json'
    path.write_text(json.dumps(document(tmp_path) if value is None else value))
    path.chmod(0o600)
    return path


def test_config_has_os_verified_caller_but_does_not_manufacture_authority(tmp_path):
    loaded = config.load_read_host_config(write(tmp_path))
    assert loaded.binding.verified_caller == 'uid:' + str(os.geteuid())
    with pytest.raises(RegistryReadSessionError) as exc:
        loaded.open_session()
    assert exc.value.code == 'SOURCE_UNAVAILABLE'
    assert not (tmp_path / 'registry').exists()


@pytest.mark.parametrize('mode', [0o644, 0o660, 0o700, 0o666])
def test_read_config_rejects_nonprivate_mode(tmp_path, mode):
    path = write(tmp_path); path.chmod(mode)
    with pytest.raises(RegistryReadSessionError):
        config.load_read_host_config(path)


def test_read_config_rejects_symlink_and_untrusted_parent(tmp_path):
    path = write(tmp_path)
    link = tmp_path / 'link.json'; link.symlink_to(path)
    with pytest.raises(RegistryReadSessionError):
        config.load_read_host_config(link)
    tmp_path.chmod(0o777)
    try:
        with pytest.raises(RegistryReadSessionError):
            config.load_read_host_config(path)
    finally:
        tmp_path.chmod(0o700)


@pytest.mark.parametrize('key,value', [('verified_caller', 'other-user'),
    ('module', 'arbitrary.code'), ('issue_grant', True), ('callback', 'run')])
def test_config_rejects_authority_or_executable_fields(tmp_path, key, value):
    doc = document(tmp_path); doc[key] = value
    with pytest.raises(RegistryReadSessionError):
        config.load_read_host_config(write(tmp_path, doc))


def test_duplicate_json_and_oversized_input_rejected(tmp_path):
    path = write(tmp_path)
    path.write_text('{"purpose":"comparison","purpose":"different"}')
    with pytest.raises(RegistryReadSessionError):
        config.load_read_host_config(path)
    path.write_bytes(b' ' * (config.MAX_CONFIG_BYTES + 1))
    with pytest.raises(RegistryReadSessionError):
        config.load_read_host_config(path)


def test_config_change_or_replacement_fails_before_source_open(tmp_path, monkeypatch):
    path = write(tmp_path); loaded = config.load_read_host_config(path)
    monkeypatch.setattr(config, 'open_readonly_source', lambda *a, **k: pytest.fail('opened changed source'))
    value = json.loads(path.read_text()); value['purpose'] = 'changed'
    path.write_text(json.dumps(value))
    with pytest.raises(RegistryReadSessionError) as exc:
        loaded.binding.source_resolver('isolated-source', 'local-observer')
    assert exc.value.code == 'ACCESS_CHANGED'


def test_known_catalog_composes_without_changing_mechanical_defaults():
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    from cpn.rpnh.collaboration.schema_catalog import registry_read_schema_data
    docs, types, paths = registry_read_schema_data()
    assert 'registry_v1/registry_observer_grant/v2' in docs
    SchemaCatalog(schemas=docs, types=types, schema_paths=paths)
    assert 'registry_observer_grant/v2' not in {x.name for x in SchemaCatalog().definitions()}


def test_package_environment_routing_bypasses_session_start(monkeypatch):
    from cpn.rpnh_cli import main
    monkeypatch.setitem(sys.modules, 'cpn.rpnh.collaboration.environment_cli',
        SimpleNamespace(main=lambda argv: 7 if argv == ['check-environment', 'test.zip'] else 9))
    assert main(['package', 'check-environment', 'test.zip']) == 7


def test_selected_host_routing_uses_fixed_installed_module(monkeypatch):
    from cpn.rpnh_cli import main
    monkeypatch.setitem(sys.modules, 'cpn.rpnh.collaboration.environment_host',
        SimpleNamespace(main=lambda argv: 6 if argv == [] else 8))
    assert main(['_environment-host']) == 6


def test_read_host_cli_is_independent_and_closes_session(monkeypatch, tmp_path):
    from cpn import rpnh_cli
    from cpn.frontend import comparison_context, server
    events = []
    class Session:
        def __enter__(self): events.append('open'); return self
        def __exit__(self, *args): events.append('close')
    session = Session(); provider = object()
    monkeypatch.setattr(config, 'open_read_host_session', lambda p: session)
    monkeypatch.setattr(comparison_context, 'ComparisonProvider', lambda s: provider)
    monkeypatch.setattr(server, 'serve_projection', lambda p, **kwargs: events.append((p, kwargs)))
    assert rpnh_cli._net_command(['--read-host-config', str(tmp_path/'read.json'), '--view', '--no-open']) == 0
    assert events[0] == 'open' and events[-1] == 'close'
    assert events[1][0] is provider and events[1][1]['open_browser'] is False
    with pytest.raises(ValueError, match='requires --view'):
        rpnh_cli._net_command(['--read-host-config', str(tmp_path/'read.json')])
    with pytest.raises(SystemExit):
        rpnh_cli._net_parser().parse_args(['--run', str(tmp_path), '--read-host-config', str(tmp_path/'read.json')])
