"""Bounded full-byte HOST configuration witnesses; no provider or listener."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from cpn.rpnh.collaboration import read_host_config as config
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
from test_shared_public_entrypoints import document, reference, write


def _same_stat_samples(monkeypatch, path, original):
    """Hold this file's stat samples fixed, but still perform actual file reads.

    This isolates the content term deterministically. It is not a claim that a
    filesystem permits an attacker to preserve its ctime or other OS checks.
    """
    real_fstat, real_stat = os.fstat, Path.stat

    def fstat(fd):
        actual = real_fstat(fd)
        return original if (actual.st_dev, actual.st_ino) == (original.st_dev, original.st_ino) else actual

    def stat(path_value, *args, **kwargs):
        actual = real_stat(path_value, *args, **kwargs)
        return original if path_value == path else actual

    monkeypatch.setattr(os, 'fstat', fstat)
    monkeypatch.setattr(Path, 'stat', stat)


@pytest.mark.parametrize('size', [0, config.MAX_CONFIG_BYTES])
def test_private_witness_shares_exact_bounded_payload_without_hash(tmp_path, monkeypatch, size):
    path = tmp_path / 'owned.json'
    path.write_bytes(b'x' * size)
    path.chmod(0o600)
    original = path.stat()
    with monkeypatch.context() as patch:
        patch.setattr(hashlib, 'sha256', lambda *_args, **_kwargs: pytest.fail('configuration witness still hashes'))
        payload, witness = config._read_owned_file(path)
    assert type(payload) is bytes and payload == b'x' * size
    assert witness[:-1] == (original.st_dev, original.st_ino, original.st_size,
                            original.st_mtime_ns, original.st_ctime_ns, original.st_mode, original.st_uid)
    assert witness[-1] is payload


@pytest.mark.parametrize('version', ['v1', 'v2'])
@pytest.mark.parametrize('resolver_kind', ['source', 'source_set'])
def test_each_resolver_rejects_changed_full_bytes_even_with_same_stat(tmp_path, monkeypatch, resolver_kind, version):
    value = document(tmp_path)
    if version == 'v2':
        value.update(schema_version=config.HIERARCHY_CONFIG_SCHEMA, hierarchy={
            'roots': ['root'], 'nodes': {'root': {'sources': ['isolated-source'], 'children': []}}})
    manifest_ref = {'schema_version': 'rpnh/collaboration/source_version_ref/v1',
                    'source_id': 'manifest',
                    'ref': reference('collaboration_source_set/v1', 'resource', 'resource_version')}
    value['source_set'] = {'source_set_ref': manifest_ref,
                          'registry_root': str(tmp_path / 'manifest'), 'binding_generation': 'manifest-1'}
    path = write(tmp_path, value)
    original_bytes = path.read_bytes() + b' \n'
    path.write_bytes(original_bytes)
    loaded = config.load_read_host_config(path)
    stat = path.stat()
    opened = []
    sentinel = object()

    def open_source(root, *, catalog, binding_generation):
        opened.append((root, binding_generation))
        return sentinel

    monkeypatch.setattr(config, 'open_readonly_source', open_source)
    if resolver_kind == 'source':
        resolve = lambda: loaded.binding.source_resolver('isolated-source', 'local-observer')
        expected = (tmp_path / 'registry', '1')
    else:
        ref = loaded.request.selection.source_set_ref
        resolve = lambda: loaded.binding.source_set_resolver(ref)
        expected = (tmp_path / 'manifest', 'manifest-1')
    assert resolve() is sentinel and opened == [expected]
    changed = original_bytes[:-2] + b'\n '
    assert len(changed) == len(original_bytes) and json.loads(changed) == json.loads(original_bytes)
    path.write_bytes(changed)
    with monkeypatch.context() as patch:
        _same_stat_samples(patch, path, stat)
        with pytest.raises(RegistryReadSessionError) as error:
            resolve()
    assert error.value.code == 'ACCESS_CHANGED'
    assert opened == [expected]


@pytest.mark.parametrize('change', ['replace_inode', 'allowed_mode'])
def test_unchanged_bytes_do_not_remove_stat_identity_binding(tmp_path, monkeypatch, change):
    path = write(tmp_path)
    loaded = config.load_read_host_config(path)
    raw = path.read_bytes()
    if change == 'replace_inode':
        replacement = tmp_path / 'replacement.json'
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        replacement.replace(path)
    else:
        path.chmod(0o400)
    assert path.read_bytes() == raw
    monkeypatch.setattr(config, 'open_readonly_source', lambda *_a, **_k: pytest.fail('opened changed identity'))
    with pytest.raises(RegistryReadSessionError) as error:
        loaded.binding.source_resolver('isolated-source', 'local-observer')
    assert error.value.code == 'ACCESS_CHANGED'


def test_read_closes_descriptor_when_stat_changes_during_read(tmp_path, monkeypatch):
    path = write(tmp_path)
    real_fstat, real_close = os.fstat, os.close
    samples, closed = [], []

    def fstat(fd):
        actual = real_fstat(fd)
        samples.append(fd)
        if len(samples) == 2:
            from types import SimpleNamespace
            return SimpleNamespace(**{name: getattr(actual, name) + (1 if name == 'st_size' else 0)
                for name in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid')})
        return actual

    def close(fd):
        closed.append(fd)
        return real_close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'fstat', fstat)
        patch.setattr(os, 'close', close)
        with pytest.raises(RegistryReadSessionError) as error:
            config._read_owned_file(path)
    assert error.value.code == 'INVALID_HOST_CONFIGURATION'
    assert len(samples) == 2 and closed == [samples[0]]


def test_project_configuration_consumer_still_uses_original_payload(tmp_path):
    from cpn.rpnh.collaboration.project_local import load_configuration
    value = {'format': 'controlled-project-launcher/v1', 'project': {},
             'routes': {'owner': {
                 'session': reference('user_collaboration_session/v1', 'resource', 'resource_version'),
                 'principal': reference('principal/v1', 'principal', 'principal_version')}},
             'execution_profile': None, 'read_closure_limit': 1024}
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)
    assert load_configuration(tmp_path) == value
    path.write_text(json.dumps({**value, 'unexpected': True}))
    with pytest.raises(ValueError, match='unsupported local project configuration'):
        load_configuration(tmp_path)


def test_actual_v1_session_rejects_representation_change_without_registry_writes(tmp_path, monkeypatch):
    from test_reader_preflight import configured
    from test_registry_read_session import counts
    from cpn.rpnh.collaboration.registry_read_contracts import IndexQuery, TypedIndexClause
    world, _, path = configured(tmp_path)
    raw = path.read_bytes() + b' \n'
    path.write_bytes(raw)
    loaded = config.load_read_host_config(path)
    stat = path.stat()
    before = counts(world)
    with loaded.open_session() as reader:
        query = IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1'),), 100)
        assert reader.query_index(query)['entries']
        path.write_bytes(raw[:-2] + b'\n ')
        with monkeypatch.context() as patch:
            _same_stat_samples(patch, path, stat)
            with pytest.raises(RegistryReadSessionError) as error:
                reader.final_recheck()
            assert error.value.code == 'ACCESS_CHANGED'
            assert not reader.query_index(query)['entries']
    assert counts(world) == before


def test_read_rejects_overflow_even_when_initial_stat_understates_size(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / 'owned.json'
    path.write_bytes(b'x' * (config.MAX_CONFIG_BYTES + 1))
    path.chmod(0o600)
    real_fstat, real_read, real_close = os.fstat, os.read, os.close
    read_sizes, closed = [], []

    def fstat(fd):
        actual = real_fstat(fd)
        return SimpleNamespace(**{name: config.MAX_CONFIG_BYTES if name == 'st_size' else getattr(actual, name)
            for name in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid')})

    def read(fd, amount):
        chunk = real_read(fd, amount)
        read_sizes.append(len(chunk))
        return chunk

    def close(fd):
        closed.append(fd)
        return real_close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'fstat', fstat)
        patch.setattr(os, 'read', read)
        patch.setattr(os, 'close', close)
        with pytest.raises(RegistryReadSessionError) as error:
            config._read_owned_file(path)
    assert error.value.code == 'INVALID_HOST_CONFIGURATION'
    assert sum(read_sizes) == config.MAX_CONFIG_BYTES + 1 and len(closed) == 1


@pytest.mark.parametrize('invalid', ['owner', 'nonregular', 'current_identity'])
def test_owned_file_rejects_remaining_stat_boundaries(tmp_path, monkeypatch, invalid):
    import stat
    from types import SimpleNamespace
    path = write(tmp_path)
    real_fstat, real_stat = os.fstat, Path.stat
    names = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid')

    def altered(actual):
        values = {name: getattr(actual, name) for name in names}
        if invalid == 'owner': values['st_uid'] += 1
        elif invalid == 'nonregular': values['st_mode'] = stat.S_IFDIR | 0o600
        else: values['st_ino'] += 1
        return SimpleNamespace(**values)

    def path_stat(value, *args, **kwargs):
        actual = real_stat(value, *args, **kwargs)
        return altered(actual) if value == path else actual

    with monkeypatch.context() as patch:
        if invalid == 'current_identity':
            patch.setattr(Path, 'stat', path_stat)
        else:
            patch.setattr(os, 'fstat', lambda fd: altered(real_fstat(fd)))
        with pytest.raises(RegistryReadSessionError) as error:
            config._read_owned_file(path)
    assert error.value.code == 'INVALID_HOST_CONFIGURATION'


def test_unsupported_platform_remains_explicit(tmp_path, monkeypatch):
    path = write(tmp_path)
    with monkeypatch.context() as patch:
        patch.delattr(os, 'O_NOFOLLOW')
        with pytest.raises(RegistryReadSessionError) as error:
            config._read_owned_file(path)
    assert error.value.code == 'UNSUPPORTED_HOST_PLATFORM'
