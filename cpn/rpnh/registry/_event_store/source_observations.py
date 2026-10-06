"""Observation staging in an exact firing; only original Success makes it canonical."""
from __future__ import annotations
import json

from .branch_publication import _local_binding
from .collaboration_descriptors import descriptor_store, exact_descriptor, readable_descriptor
from .source_sets import exact_source_set
from ..event_store import RegistryConflict
from ..models import PreparedObject
from ..identities import TypedId


def _contract():
    from ...collaboration import source_observations
    return source_observations


def _load(db, catalog, task_id, reference, store, *, temporary_root=None):
    """Canonical descriptor or exact explicit same-root temporary descriptor."""
    ref = reference['ref']
    if ref['entity_type'] != _contract().OBSERVATION_TYPE:
        raise RegistryConflict('unsupported Observation version')
    roots = db.execute("SELECT p.* FROM firing_temporary_members m JOIN firing_publications p "
        "ON p.firing_version_id=m.firing_version_id WHERE m.member_kind='object' AND m.member_identity=?",
        (ref['version_id'],)).fetchall()
    if len(roots) != 1:
        raise RegistryConflict('Observation requires one exact originating firing')
    root = roots[0]
    if root['state'] == 'PROVISIONAL':
        if temporary_root != root['firing_version_id']:
            raise RegistryConflict('Observation is not canonically visible')
        row = db.execute("SELECT o.*,t.status,e.task_id,e.transaction_id AS event_transaction,e.payload_json,"
            "e.event_type,e.criticality,e.producer_invocation_id AS event_producer FROM objects o "
            "JOIN transactions t ON t.transaction_id=o.transaction_id JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.object_type=? AND o.logical_id=? AND o.version_id=?",
            (ref['entity_type'], ref['logical_id'], ref['version_id'])).fetchone()
        if (row is None or row['status'] != 'committed' or row['task_id'] != str(task_id)
                or row['event_transaction'] != row['transaction_id'] or row['event_type'] != 'object_version_published/v1'
                or row['criticality'] != 'authoritative' or row['producer_invocation_id'] != root['invocation_logical_id']
                or row['event_producer'] != row['producer_invocation_id']):
            raise RegistryConflict('temporary Observation lacks its exact publication')
        obj = PreparedObject(row['object_type'], TypedId.parse(row['logical_id']), TypedId.parse(row['version_id']),
            row['size'], row['media_type'], row['schema_ref'], TypedId.parse(row['producer_invocation_id']),
            row['storage_locator'], json.loads(row['metadata_json']))
        document = readable_descriptor(store, obj)
        if json.loads(row['payload_json']) != {'logical_id': row['logical_id'], 'version_id': row['version_id'],
                'object_type': row['object_type'], 'size': row['size'], 'media_type': row['media_type'],
                'schema_ref': row['schema_ref'], 'storage_locator': row['storage_locator'], 'metadata': document}:
            raise RegistryConflict('temporary Observation publication differs from immutable bytes')
    else:
        document = exact_descriptor(db, store, task_id, ref)
    if document.get('observation_ref') != reference:
        raise RegistryConflict('Observation exact self reference differs')
    return document


def _validate(db, catalog, task_id, document, store, *, temporary_root=None):
    contract = _contract()
    from ...collaboration.source_sets import SourceSetVersion
    from ...collaboration.source_query import _unqualified
    catalog.validate_instance(contract.OBSERVATION_TYPE, category='object', instance=document)
    binding = _local_binding(db, catalog, task_id, store)
    ref = document['observation_ref']
    if (any(document[k]['source_id'] != binding['source_id'] for k in
            ('observation_ref', 'owner_task_ref', 'producer_invocation_ref', 'producer_firing_ref'))
            or document['owner_task_ref']['ref'] != binding['task_ref']
            or ref['ref']['logical_id'] != str(contract.observation_identity(task_id, document['command_id'], 'resource'))
            or ref['ref']['version_id'] != str(contract.observation_identity(task_id, document['command_id'], 'resource_version'))):
        raise RegistryConflict('Observation identity differs from exact local producer command')
    invocation, firing = document['producer_invocation_ref']['ref'], document['producer_firing_ref']['ref']
    root = db.execute('SELECT * FROM firing_publications WHERE firing_version_id=?', (firing['version_id'],)).fetchone()
    if (root is None or root['invocation_version_id'] != invocation['version_id']
            or root['invocation_logical_id'] != invocation['logical_id'] or root['firing_logical_id'] != firing['logical_id']):
        raise RegistryConflict('Observation producer is not one exact registered firing')
    if root['state'] == 'PROVISIONAL' and root['firing_version_id'] != temporary_root:
        raise RegistryConflict('Observation is outside the exact same-firing read/write scope')
    manifest = SourceSetVersion.from_dict(exact_source_set(db, catalog, task_id, document['observation']['source_set_ref'], store), catalog)
    observation = document['observation']
    contract.validate_observation(observation, manifest, catalog)
    for row in observation['sources']:
        if row['source_id'] == binding['source_id'] and row['authority'] is not None:
            if _unqualified(row['authority'], binding['source_id']).get('invocation_ref') != invocation:
                raise RegistryConflict('local capture qualification must be the Observation producer')
    parent_ref = observation['previous_observation_ref'] or observation['supplement_of']
    if parent_ref is not None:
        parent_record = _load(db, catalog, task_id, parent_ref, store, temporary_root=temporary_root)
        if parent_record['owner_task_ref'] != document['owner_task_ref']:
            raise RegistryConflict('Observation parent uses another owner')
        parent = parent_record['observation']
        if parent['source_set_ref'] != observation['source_set_ref']:
            raise RegistryConflict('Observation parent uses another SourceSet version')
        old = {r['source_id']: r for r in parent['sources']}
        if observation['previous_observation_ref'] is not None:
            if (parent['kind'] != 'query' or parent['query'] != observation['query']
                    or parent_record['producer_invocation_ref'] != document['producer_invocation_ref']):
                raise RegistryConflict('Observation continuation changed query or Invocation')
            for row in observation['sources']:
                previous = old[row['source_id']]
                if row['access_path'] != previous['access_path']:
                    raise RegistryConflict('Observation continuation changed its access path')
                if row['coverage']['state'] in ('complete', 'partial') and (
                        row['cut'] != previous['cut'] or row['authority'] != previous['authority']
                        or row['authority_head'] != previous['authority_head']
                        or row['headers'][:len(previous['headers'])] != previous['headers']):
                    raise RegistryConflict('Observation continuation changed retained capture evidence')
        elif observation['sources'][0]['access_path'] != old[observation['sources'][0]['source_id']]['access_path']:
            raise RegistryConflict('supplement changed the original selected path')
    return root


def read_observation(core, reference, *, capture_context=None):
    from ..resource_service import _ResourceServiceKernel
    root = None
    canonical = core.event_store.canonical_view()
    row = core.event_store.object_row_for_view(canonical, reference.ref.version_id)
    if row is None:
        if capture_context is None:
            raise RegistryConflict('Observation is not canonically visible')
        _ResourceServiceKernel(core)._revalidate_invocation(capture_context, boundary='source-observation-read')
        view = core.event_store.firing_view(firing_version_id=capture_context.own_transition_firing_ref.version_id,
                                            invocation_version_id=capture_context.invocation_ref.version_id)
        row = core.event_store.object_row_for_view(view, reference.ref.version_id)
        if row is None:
            raise RegistryConflict('Observation is outside the exact same-firing view')
        root = str(capture_context.own_transition_firing_ref.version_id)
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        document = _load(db, core.catalog, core.task_id, reference.to_dict(), core.object_store, temporary_root=root)
        _validate(db, core.catalog, core.task_id, document, core.object_store, temporary_root=root)
    if root is not None:
        if document['producer_invocation_ref']['ref']['version_id'] != str(capture_context.invocation_ref.version_id):
            raise RegistryConflict('temporary Observation belongs to another Invocation')
        _ResourceServiceKernel(core)._revalidate_invocation(capture_context, boundary='source-observation-read-complete')
    return document


def validate_observation_publication(context):
    contract = _contract()
    objects = [o for o in context.objects if o.object_type == contract.OBSERVATION_TYPE]
    identities = {str(o.logical_id) for o in context.objects}
    identities.update(e.stream_id.removeprefix('object:') for e in context.events if e.stream_id.startswith('object:'))
    uses = bool(objects) or any(e.payload.get('object_type') == contract.OBSERVATION_TYPE for e in context.events)
    if not uses and identities:
        marks = ','.join('?' for _ in identities)
        uses = context.db.execute(f'SELECT 1 FROM objects WHERE object_type=? AND logical_id IN ({marks}) LIMIT 1',
                                  (contract.OBSERVATION_TYPE, *sorted(identities))).fetchone() is not None
    if not uses:
        return
    if len(objects) != 1 or len(context.objects) != 1 or context.relations or len(context.events) != 2:
        raise RegistryConflict('Observation staging requires one object and its exact commit')
    obj = objects[0]
    store = descriptor_store(context.event_store)
    document = readable_descriptor(store, obj)
    firing = document['producer_firing_ref']['ref']['version_id']
    root = _validate(context.db, context.event_store.catalog, context.task_id, document, store, temporary_root=firing)
    publications = [e for e in context.events if e.event_type == 'object_version_published/v1']
    terminals = [e for e in context.events if e.event_type == 'transaction_committed/v1']
    if len(publications) != 1 or len(terminals) != 1:
        raise RegistryConflict('Observation requires one publication and one commit')
    event, terminal = publications[0], terminals[0]
    key = contract.observation_key(document['command_id'])
    ref = document['observation_ref']['ref']
    invocation = document['producer_invocation_ref']['ref']
    metadata = json.loads(context.db.execute('SELECT metadata_json FROM objects WHERE version_id=?', (invocation['version_id'],)).fetchone()[0])
    if (root['state'] != 'PROVISIONAL' or context.proposal_owner_roots != {firing}
            or str(obj.producer_invocation_id) != invocation['logical_id'] or event.producer_invocation_id != obj.producer_invocation_id
            or str(context.task_round_id) != metadata['task_round_ref']['logical_id']
            or str(context.net_instance_id) != metadata['net_instance_ref']['logical_id']
            or str(obj.logical_id) != ref['logical_id'] or str(obj.version_id) != ref['version_id']
            or obj.schema_ref != contract.RECORD_SCHEMA or context.idempotency_key != key
            or event.stream_id != f'object:{obj.logical_id}' or event.aggregate_id != str(obj.logical_id)
            or event.aggregate_type != obj.object_type or event.producer_principal != 'framework'
            or event.task_control or event.criticality != 'authoritative' or event.command_id != key or event.idempotency_key != key
            or event.payload_schema_ref != 'registry_v1/object_version_published/v1'
            or dict(event.payload) != {'logical_id': str(obj.logical_id), 'version_id': str(obj.version_id),
                'object_type': obj.object_type, 'size': obj.size, 'media_type': obj.media_type,
                'schema_ref': obj.schema_ref, 'storage_locator': obj.storage_locator, 'metadata': dict(obj.metadata)}
            or terminal.stream_id != f'transaction:{context.transaction_id}'
            or terminal.aggregate_id != str(context.transaction_id) or terminal.aggregate_type != 'transaction'
            or terminal.criticality != 'authoritative' or terminal.task_control or terminal.producer_principal != 'framework'
            or terminal.producer_invocation_id is not None or terminal.command_id != key or terminal.idempotency_key != key
            or terminal.payload_schema_ref != 'registry_v1/transaction_committed/v1'
            or dict(terminal.payload) != {'object_count': 1, 'relation_count': 0, 'fact_count': 1}
            or context.db.execute('SELECT 1 FROM stream_heads WHERE stream_id=?', (event.stream_id,)).fetchone() is not None):
        raise RegistryConflict('Observation producer/command/publication differs')
