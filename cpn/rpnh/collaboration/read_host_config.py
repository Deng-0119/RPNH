"""Trusted local JSON adapter for the independent, read-only installed HOST.

This file is owner-controlled configuration, never an HTTP request. It selects
existing grants only; requests cannot supply principals, import paths, callbacks
or issuance instructions. The OS user and canonical source grants are checked
independently. This same-user boundary is not remote/multi-tenant authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from ..registry.observer_access import RegistryReadObserverContext
from ..registry.schema_catalog import SchemaCatalog
from .references import SourceQualifiedVersionRef
from .registry_read_contracts import (
    ExplicitSources, ReadLimits, ReadSessionRequest, RegistryReadSessionError,
    SelectedSourceSet, SourceSelection,
)
from .registry_read_session import (
    ExistingReadAuthorityProvider, RegistryReadHostBinding, RegistryReadSession,
    open_readonly_source,
)
from .registry_typed_readers import DEFAULT_TYPED_READER_CATALOG
from .schema_catalog import registry_read_schema_data
from .share_packages import strict_json

CONFIG_SCHEMA = 'rpnh/registry_read_host_config/v1'
MAX_CONFIG_BYTES = 1024 * 1024


def _invalid():
    return RegistryReadSessionError('INVALID_HOST_CONFIGURATION')


def _object(value, required, optional=()):
    if (type(value) is not dict or not set(required) <= set(value)
            or set(value) - set(required) - set(optional)):
        raise _invalid()
    return value


def _local_path(value):
    if type(value) is not str or not value or '\x00' in value:
        raise _invalid()
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise _invalid()
    # Never normalize a symlink away before checking trusted local selectors.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise _invalid()
    return path


def _read_owned_file(path):
    """Read one bounded inode without following a swapped final symlink."""
    if not hasattr(os, 'geteuid') or not hasattr(os, 'O_NOFOLLOW'):
        raise RegistryReadSessionError('UNSUPPORTED_HOST_PLATFORM')
    path = _local_path(str(path))
    uid = os.geteuid()
    for parent in path.parents:
        info = parent.stat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, uid}
                or info.st_mode & 0o022 and not sticky_root):
            raise _invalid()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid
                or stat.S_IMODE(before.st_mode) not in {0o400, 0o600}
                or before.st_size > MAX_CONFIG_BYTES):
            raise _invalid()
        data = bytearray()
        while len(data) <= MAX_CONFIG_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_CONFIG_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_uid)
        if (len(data) > MAX_CONFIG_BYTES or identity(before) != identity(after)
                or identity(after) != identity(current)):
            raise _invalid()
        payload = bytes(data)
        return payload, (*identity(after), hashlib.sha256(payload).hexdigest())
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class ReadHostConfiguration:
    """Loaded local configuration; not serializable read authority."""
    request: ReadSessionRequest
    binding: RegistryReadHostBinding

    def open_session(self):
        return RegistryReadSession(self.request, self.binding)


def load_read_host_config(path: Path | str) -> ReadHostConfiguration:
    """Validate existing owner-issued contexts against a fixed builtin catalog."""
    try:
        path = Path(path).absolute()
        payload, file_identity = _read_owned_file(path)
        document = strict_json(payload, path='read-host-configuration')
        _object(document, ('schema_version', 'purpose', 'sources'), ('limits', 'source_set'))
        if document['schema_version'] != CONFIG_SCHEMA:
            raise _invalid()
        if type(document['sources']) is not list or not 1 <= len(document['sources']) <= 64:
            raise _invalid()
        limits_data = document.get('limits', {})
        if type(limits_data) is not dict:
            raise _invalid()
        limits = ReadLimits(**limits_data)
        schemas, types, paths = registry_read_schema_data()
        catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
        caller = 'uid:' + str(os.geteuid())
        sources, authorities, selections = {}, {}, []
        for row in document['sources']:
            _object(row, ('source_ref', 'access_path', 'registry_root',
                          'binding_generation', 'observer_context'))
            ref = SourceQualifiedVersionRef.from_dict(row['source_ref'], catalog=catalog)
            selection = SourceSelection(ref, row['access_path'])
            key = (ref.source_id, row['access_path'])
            if key in sources or type(row['binding_generation']) is not str or not row['binding_generation']:
                raise _invalid()
            root = _local_path(row['registry_root'])
            context = RegistryReadObserverContext.from_dict(row['observer_context'])
            if context.task_ref != ref.ref or context.purpose != document['purpose']:
                raise _invalid()
            sources[key] = (root, row['binding_generation'])
            authorities[(caller, *key)] = context
            selections.append(selection)
        def revalidate():
            if _read_owned_file(path)[1] != file_identity:
                raise RegistryReadSessionError('ACCESS_CHANGED')
        def resolve(source_id, access_path):
            revalidate()
            item = sources.get((source_id, access_path))
            if item is None:
                raise RegistryReadSessionError('SOURCE_UNAVAILABLE')
            root, generation = item
            _local_path(str(root))
            return open_readonly_source(root, catalog=catalog, binding_generation=generation)
        selection = ExplicitSources(tuple(selections))
        source_set_resolver = None
        if 'source_set' in document:
            item = _object(document['source_set'], ('source_set_ref', 'registry_root', 'binding_generation'))
            manifest_ref = SourceQualifiedVersionRef.from_dict(item['source_set_ref'], catalog=catalog)
            manifest_root = _local_path(item['registry_root'])
            generation = item['binding_generation']
            if type(generation) is not str or not generation:
                raise _invalid()
            selection = SelectedSourceSet(manifest_ref, tuple(selections))
            def source_set_resolver(ref):
                revalidate()
                if ref != manifest_ref:
                    raise RegistryReadSessionError('NOT_DISCLOSED')
                _local_path(str(manifest_root))
                return open_readonly_source(manifest_root, catalog=catalog, binding_generation=generation)
        request = ReadSessionRequest(selection, document['purpose'], limits)
        binding = RegistryReadHostBinding(caller, resolve, ExistingReadAuthorityProvider(authorities),
            DEFAULT_TYPED_READER_CATALOG, limits, source_set_resolver)
        return ReadHostConfiguration(request, binding)
    except RegistryReadSessionError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _invalid() from exc


def open_read_host_session(path: Path | str) -> RegistryReadSession:
    return load_read_host_config(path).open_session()
