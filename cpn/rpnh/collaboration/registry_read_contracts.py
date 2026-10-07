"""Versioned, inert values for independent Registry observation.

None of these values conveys authority. HOST configuration and source-side
canonical verification are required even when a value was returned earlier.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Mapping

from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef, _require_source_id
from ..registry.schema_catalog import canonical_json

READER_CONTRACT_VERSION = 'rpnh/registry_read/v1'


class RegistryReadSessionError(ValueError):
    """A stable public failure; messages never contain storage locators."""
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code.replace('_', ' ').lower())

    def to_dict(self):
        return {'code': self.code, 'message': str(self),
                'reopen_session': self.code in {'SESSION_EXPIRED', 'SESSION_CLOSED', 'ACCESS_CHANGED'}}


@dataclass(frozen=True, slots=True)
class ReadLimits:
    max_sources: int = 64
    max_clauses: int = 64
    max_predicates: int = 32
    max_in_values: int = 64
    max_projection_fields: int = 32
    max_page_size: int = 1000
    max_query_bytes: int = 65536
    max_response_bytes: int = 4 * 1024 * 1024
    max_scan_rows: int = 100000
    max_scan_bytes: int = 64 * 1024 * 1024
    max_material_bytes: int = 4 * 1024 * 1024
    max_cuts: int = 64
    max_cursors: int = 128
    session_seconds: int = 600

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise ValueError('read limits require positive integers')

    def tightened(self, other):
        return ReadLimits(**{key: min(value, getattr(other, key)) for key, value in asdict(self).items()})

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceSelection:
    source_ref: SourceQualifiedVersionRef
    access_path: str

    def __post_init__(self):
        if (type(self.source_ref) is not SourceQualifiedVersionRef
                or self.source_ref.ref.entity_type != 'task/v1'
                or self.source_ref.ref.entity_id.kind != 'task'
                or self.source_ref.ref.version_id.kind != 'task_version'):
            raise TypeError('source selection requires an exact source-qualified task')
        _require_source_id(self.access_path)

    def to_dict(self):
        return {'source_ref': self.source_ref.to_dict(), 'access_path': self.access_path}


@dataclass(frozen=True, slots=True)
class ExplicitSources:
    sources: tuple[SourceSelection, ...]

    def __post_init__(self):
        _selections(self.sources)

    def to_dict(self):
        return {'kind': 'explicit', 'sources': [item.to_dict() for item in self.sources]}


@dataclass(frozen=True, slots=True)
class SelectedSourceSet:
    source_set_ref: SourceQualifiedVersionRef
    sources: tuple[SourceSelection, ...]

    def __post_init__(self):
        _selections(self.sources)
        if (type(self.source_set_ref) is not SourceQualifiedVersionRef
                or self.source_set_ref.ref.entity_type != 'collaboration_source_set/v1'):
            raise TypeError('selection requires an exact SourceSet')

    def to_dict(self):
        return {'kind': 'source_set', 'source_set_ref': self.source_set_ref.to_dict(),
                'sources': [item.to_dict() for item in self.sources]}


def _selections(values):
    if (type(values) is not tuple or not values or any(type(x) is not SourceSelection for x in values)
            or len({x.source_ref.source_id for x in values}) != len(values)):
        raise TypeError('sources must be a nonempty unique tuple of exact selections')


@dataclass(frozen=True, slots=True)
class ReadSessionRequest:
    selection: ExplicitSources | SelectedSourceSet
    purpose: str
    limits: ReadLimits = field(default_factory=ReadLimits)
    schema_version: str = 'rpnh/registry_read_session_request/v1'

    def __post_init__(self):
        if type(self.selection) not in (ExplicitSources, SelectedSourceSet) or type(self.limits) is not ReadLimits:
            raise TypeError('unsupported read session request')
        _require_source_id(self.purpose)
        if self.schema_version != 'rpnh/registry_read_session_request/v1':
            raise ValueError('unsupported read request version')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'selection': self.selection.to_dict(),
                'purpose': self.purpose, 'limits': self.limits.to_dict()}


@dataclass(frozen=True, slots=True)
class PublicRegistryHead:
    ordinal: int
    writer_fencing_epoch: int

    def __post_init__(self):
        if any(type(x) is not int or x < 0 for x in (self.ordinal, self.writer_fencing_epoch)):
            raise ValueError('invalid public Registry head')

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceCut:
    source_id: str
    cut_id: str
    head: PublicRegistryHead
    reader_contract_version: str = READER_CONTRACT_VERSION

    def __post_init__(self):
        _require_source_id(self.source_id)
        _require_source_id(self.cut_id)
        if type(self.head) is not PublicRegistryHead or self.reader_contract_version != READER_CONTRACT_VERSION:
            raise ValueError('unsupported source cut')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {'source_id', 'cut_id', 'head', 'reader_contract_version'}:
            raise RegistryReadSessionError('CURSOR_MISMATCH')
        try:
            return cls(value['source_id'], value['cut_id'], PublicRegistryHead(**value['head']),
                       value['reader_contract_version'])
        except (TypeError, ValueError) as exc:
            raise RegistryReadSessionError('CURSOR_MISMATCH') from exc


@dataclass(frozen=True, slots=True)
class HistoricalCutRequest:
    source_id: str
    ordinal: int

    def __post_init__(self):
        _require_source_id(self.source_id)
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError('historical cut ordinal must be nonnegative')

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TypedPredicate:
    field: str
    operator: str
    value: object

    def __post_init__(self):
        _require_source_id(self.field)
        if self.operator not in {'eq', 'in', 'lt', 'le', 'gt', 'ge'}:
            raise RegistryReadSessionError('UNSUPPORTED_PREDICATE')
        # Snapshot supplied values: immutable canonical bytes are used by all
        # consumers and fingerprints, so later mutation cannot change a query.
        try:
            encoded = json.dumps(self.value, allow_nan=False, sort_keys=True, separators=(',', ':'))
        except (ValueError, TypeError) as exc:
            raise RegistryReadSessionError('INVALID_QUERY') from exc
        object.__setattr__(self, 'value', json.loads(encoded))

    def to_dict(self):
        return {'field': self.field, 'operator': self.operator, 'value': self.value}


@dataclass(frozen=True, slots=True)
class TypedIndexClause:
    entry_type: str
    predicates: tuple[TypedPredicate, ...] = ()
    projection: tuple[str, ...] = ()

    def __post_init__(self):
        _require_source_id(self.entry_type)
        if (type(self.predicates) is not tuple or any(type(x) is not TypedPredicate for x in self.predicates)
                or type(self.projection) is not tuple or any(type(x) is not str for x in self.projection)
                or len(set(self.projection)) != len(self.projection)):
            raise RegistryReadSessionError('INVALID_QUERY')

    def to_dict(self):
        return {'entry_type': self.entry_type, 'predicates': [x.to_dict() for x in self.predicates],
                'projection': list(self.projection)}


@dataclass(frozen=True, slots=True)
class IndexQuery:
    source_ids: tuple[str, ...]
    clauses: tuple[TypedIndexClause, ...]
    page_size: int = 100
    cuts: Mapping[str, SourceCut | HistoricalCutRequest] | None = None
    sort: str = 'source_type_identity_version'
    schema_version: str = 'rpnh/registry_index_query/v1'

    def __post_init__(self):
        if (type(self.source_ids) is not tuple or not self.source_ids
                or any(type(x) is not str for x in self.source_ids)
                or len(set(self.source_ids)) != len(self.source_ids)
                or type(self.clauses) is not tuple or not self.clauses
                or any(type(x) is not TypedIndexClause for x in self.clauses)
                or type(self.page_size) is not int or self.page_size < 1
                or self.sort != 'source_type_identity_version'
                or self.schema_version != 'rpnh/registry_index_query/v1'
                or self.cuts is not None and (not isinstance(self.cuts, Mapping)
                    or set(self.cuts) != set(self.source_ids)
                    or any(type(v) not in (SourceCut, HistoricalCutRequest) for v in self.cuts.values()))):
            raise RegistryReadSessionError('INVALID_QUERY')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'source_ids': list(self.source_ids),
                'clauses': [x.to_dict() for x in self.clauses], 'page_size': self.page_size,
                'cuts': None if self.cuts is None else {k: v.to_dict() for k, v in self.cuts.items()},
                'sort': self.sort}


def qualified_ref(value):
    if type(value) not in (SourceQualifiedResourceRef, SourceQualifiedVersionRef):
        raise TypeError('read requires a typed exact source-qualified reference')
    return value.ref.as_version_ref() if type(value) is SourceQualifiedResourceRef else value.ref


def document_digest(value):
    import hashlib
    return hashlib.sha256(canonical_json(value)).hexdigest()


def registry_read_contract_schema_data():
    """Inert request/query/cut content schemas; no Registry object types."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'schemas' / 'rpnh'
    names = ('registry_source_cut', 'registry_read_session_request', 'registry_index_query')
    paths = {'rpnh/' + name + '/v1': root / (name + '.v1.schema.json') for name in names}
    return {schema: json.loads(path.read_text(encoding='utf-8')) for schema, path in paths.items()}, (), paths
