"""Explicit recording of finite SourceSet query results, separate from reading.

These are owner-recorded observations of independently authorized source cuts,
not remote grants, receipts of payload delivery, or global causal snapshots.
"""
from __future__ import annotations
import json
import uuid

from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.event_store import RegistryConflict
from ..registry.object_store import ObjectIntegrityError
from ..registry.schema_catalog import canonical_json, canonical_text
from .references import SourceQualifiedVersionRef
from .source_sets import read_source_set
from .sources import get_local_source_identity
from .source_query import SourceObservationDraft, OBSERVATION_SCHEMA, _coverage, _head, _cursor, _unqualified, wire

OBSERVATION_TYPE = 'collaboration_source_observation/v1'
RECORD_SCHEMA = 'registry_v1/' + OBSERVATION_TYPE


def observation_identity(task_id, command_id, kind):
    return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
        'kind': 'source-observation-' + kind, 'task': str(task_id), 'command': command_id})).hex)


def observation_key(command_id):
    from .references import _require_source_id
    _require_source_id(command_id)
    return 'collaboration-source-observation:' + canonical_text({'command_id': command_id})


def validate_observation(document, manifest, catalog):
    expected = {'schema_version', 'kind', 'source_set_ref', 'previous_observation_ref', 'supplement_of',
                'dependencies', 'query', 'sources', 'coverage', 'causal_completeness', 'global_atomic_snapshot'}
    if (type(document) is not dict or set(document) != expected or document['schema_version'] != OBSERVATION_SCHEMA
            or document['source_set_ref'] != manifest.source_set_ref.to_dict()
            or document['global_atomic_snapshot'] is not False or document['causal_completeness'] != 'not_established'):
        raise ValueError('unsupported or conflicting source observation')
    members = {m.source_ref.source_id: m for m in manifest.members}
    if type(document['sources']) is not list:
        raise ValueError('observation sources must be an array')
    ids = [r['source_id'] for r in document['sources']]
    if len(set(ids)) != len(ids) or set(ids) - set(members):
        raise ValueError('observation repeats or adds an undeclared source')
    if document['kind'] == 'query':
        query = document['query']
        if (set(ids) != set(members) or type(query) is not dict or set(query) != {'limit', 'media_types'}
                or type(query['limit']) is not int or not 1 <= query['limit'] <= 1000
                or type(query['media_types']) is not list or any(type(v) is not str for v in query['media_types'])
                or document['supplement_of'] is not None or document['dependencies']):
            raise ValueError('invalid complete expected query scope')
    elif document['kind'] == 'supplement':
        if (document['query'] is not None or document['previous_observation_ref'] is not None
                or document['supplement_of'] is None or len(ids) != 1 or len(document['dependencies']) != 1):
            raise ValueError('supplement requires a separate exact parent and dependency')
        from .references import SourceQualifiedResourceRef
        dependency = SourceQualifiedResourceRef.from_dict(document['dependencies'][0], catalog=catalog)
        if dependency.source_id != ids[0]:
            raise ValueError('supplement dependency source differs')
    else:
        raise ValueError('unsupported source observation kind')
    row_keys = {'source_id', 'source_ref', 'access_path', 'aliases', 'availability', 'cut', 'authority', 'authority_head', 'headers', 'cursor', 'coverage'}
    for row in document['sources']:
        member = members[row['source_id']]
        if (set(row) != row_keys or row['source_ref'] != member.source_ref.to_dict()
                or row['access_path'] not in member.access_paths or row['aliases'] != list(member.aliases)
                or type(row['headers']) is not list or set(row['coverage']) != {'state', 'loaded_count', 'total_count'}):
            raise ValueError('source observation identity/path/coverage differs')
        head, cursor = _head(row['cut']), _cursor(row['cursor'], row['source_id'])
        qualification_head = _head(row['authority_head'])
        coverage = row['coverage']
        if coverage['state'] in ('complete', 'partial'):
            if (row['availability'] != 'readable' or head is None or qualification_head is None or type(row['authority']) is not dict
                    or coverage['loaded_count'] != len(row['headers'])
                    or (coverage['state'] == 'complete') != (cursor is None)
                    or coverage['total_count'] != (len(row['headers']) if cursor is None else None)
                    or cursor is not None and cursor.through_head != head):
                raise ValueError('source page lacks its fixed authority/cut/coverage')
        elif (coverage['state'] not in ('not_loaded', 'not_established', 'unavailable', 'denied', 'access_changed', 'read_failed', 'missing_dependency')
              or row['headers'] or cursor is not None or coverage['loaded_count'] is not None or coverage['total_count'] is not None):
            raise ValueError('missing source must not report content or zero')
        for header in row['headers']:
            if header['task_ref'] != row['source_ref']:
                raise ValueError('source result header belongs to another task')
            if _head(header['published_at_head']).ordinal > head.ordinal:
                raise ValueError('source result is later than its captured cut')
            if document['kind'] == 'supplement' and header['ref'] != document['dependencies'][0]:
                raise ValueError('supplement returned a different exact dependency')
    if document['coverage'] != _coverage(document['sources']):
        raise ValueError('observation coverage differs from its source pages')


def _read_source_observation(core, reference, *, capture_context=None):
    from ..registry._event_store.source_observations import read_observation
    return read_observation(core, reference, capture_context=capture_context)


def observation_publication(core, reference):
    """Public capture visibility is the real original Success promotion position."""
    document = _read_source_observation(core, reference)
    with core.event_store.connect() as db:
        row = db.execute("SELECT p.state,p.published_transaction_id,e.ordinal FROM firing_publications p "
            "JOIN events e ON e.transaction_id=p.published_transaction_id "
            "AND e.event_type='transaction_committed/v1' WHERE p.firing_version_id=?",
            (document['producer_firing_ref']['ref']['version_id'],)).fetchone()
    if row is None or row['state'] != 'PUBLISHED':
        raise RegistryConflict('Observation is not canonically visible through Success')
    return {'state': row['state'], 'visible_ordinal': row['ordinal'],
            'published_transaction_id': row['published_transaction_id']}


def _record_source_observation(gateway, *, query, draft, command_id):
    from .source_query import SourceSetQuery
    from ..registry.resource_service import _ResourceServiceKernel
    if not isinstance(draft, SourceObservationDraft) or type(query) is not SourceSetQuery:
        raise TypeError('recording requires the real query validator and an explicit draft')
    core, context = gateway._core, query.capture_context
    binding = get_local_source_identity(core)
    if binding is None or query.core.task_id != core.task_id or context is None:
        raise RegistryConflict('recording requires the exact local capture Invocation')
    document = draft.to_dict()
    manifest_ref = SourceQualifiedVersionRef.from_dict(document['source_set_ref'], catalog=core.catalog)
    manifest = read_source_set(core, manifest_ref)
    validate_observation(document, manifest, core.catalog)
    ref = SourceQualifiedVersionRef(binding.source_id, VersionRef(OBSERVATION_TYPE,
        observation_identity(core.task_id, command_id, 'resource'), observation_identity(core.task_id, command_id, 'resource_version')))
    record = {'schema_version': RECORD_SCHEMA, 'observation_ref': ref.to_dict(),
              'owner_task_ref': SourceQualifiedVersionRef(binding.source_id, gateway._task_ref).to_dict(),
              'producer_invocation_ref': SourceQualifiedVersionRef(binding.source_id, context.invocation_ref).to_dict(),
              'producer_firing_ref': SourceQualifiedVersionRef(binding.source_id, context.own_transition_firing_ref).to_dict(),
              'command_id': command_id, 'observation': document}
    # Exact immutable retries after Success are historical reads, never a new
    # query or an attempt to reopen the closed capture Invocation's I/O.
    if core.event_store.object_row(ref.ref.version_id) is not None:
        prior = _read_source_observation(core, ref, capture_context=context)
        if prior != record:
            raise RegistryConflict('observation command conflicts with immutable prior content')
        return ref
    _ResourceServiceKernel(core)._revalidate_invocation(context, boundary='source-observation-record')
    query.validate_draft(draft)
    for row in document['sources']:
        if row['source_id'] == binding.source_id and row['authority'] is not None:
            authority = _unqualified(row['authority'], binding.source_id)
            if authority.get('invocation_ref') != wire(context.invocation_ref):
                raise RegistryConflict('local source capture belongs to another provisional Invocation')
    tx = core.begin(idempotency_key=observation_key(command_id), task_round_id=context.task_round_ref.entity_id,
                    net_instance_id=context.net_instance_ref.entity_id)
    try:
        tx.prewrite(object_type=OBSERVATION_TYPE, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
                    payload=canonical_json(record), metadata=record, media_type='application/json', schema_ref=RECORD_SCHEMA,
                    producer_invocation_id=context.invocation_ref.entity_id)
    except ObjectIntegrityError as exc:
        raise RegistryConflict('observation command conflicts with immutable prior content') from exc
    tx.commit()
    _read_source_observation(core, ref, capture_context=context)
    return ref
