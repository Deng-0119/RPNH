"""SourceSet compare-and-append inside the existing Registry writer transaction."""
from __future__ import annotations
import json

from .branch_publication import _local_binding
from .collaboration_descriptors import descriptor_store, exact_descriptor, readable_descriptor
from .source_identity import _canonical_commit_event
from ..event_store import RegistryConflict


def _contract():
    from ...collaboration import source_sets
    return source_sets


def exact_source_set(db, catalog, task_id, reference, store):
    contract = _contract()
    ref = reference['ref']
    if ref['entity_type'] != contract.SOURCE_SET_TYPE:
        raise RegistryConflict('unsupported SourceSet version')
    document = exact_descriptor(db, store, task_id, ref)
    record = contract.SourceSetVersion.from_dict(document, catalog)
    binding = _local_binding(db, catalog, task_id, store)
    row = db.execute('SELECT e.*, o.producer_invocation_id AS object_invocation FROM objects o '
        'JOIN events e ON e.event_id=o.published_event_id WHERE o.version_id=?', (ref['version_id'],)).fetchone()
    if (record.source_set_ref.to_dict() != reference or record.source_set_ref.source_id != binding['source_id']
            or record.owner_task_ref.to_dict()['ref'] != binding['task_ref']
            or record.publisher_bootstrap_ref.to_dict()['ref'] != binding['bootstrap_command_ref']
            or row['producer_principal'] != 'framework' or row['producer_invocation_id'] is not None
            or row['object_invocation'] is not None or row['net_instance_id'] is not None or row['task_round_id'] is not None
            or row['stream_sequence'] != record.sequence
            or row['command_id'] != contract.command_key(record.command_id) or row['idempotency_key'] != row['command_id']
            or ref['version_id'] != str(contract.version_id(task_id, record.command_id))
            or _canonical_commit_event(db, row['transaction_id'], task_id) is None
            or db.execute("SELECT 1 FROM firing_temporary_members WHERE (member_kind='object' AND member_identity=?) "
                          "OR (member_kind='event' AND member_identity=?)", (ref['version_id'], row['event_id'])).fetchone()):
        raise RegistryConflict('SourceSet lacks exact canonical owner publication')
    return document


def current_source_set_document(db, catalog, task_id, identity, store):
    contract = _contract()
    head = db.execute('SELECT sequence FROM stream_heads WHERE stream_id=?', ('object:' + identity,)).fetchone()
    if head is None:
        return None
    rows = db.execute("SELECT * FROM events WHERE stream_id=? AND stream_sequence=?", ('object:' + identity, head['sequence'])).fetchall()
    if len(rows) != 1 or rows[0]['event_type'] != 'object_version_published/v1':
        raise RegistryConflict('SourceSet stream lacks its current version')
    publication = json.loads(rows[0]['payload_json'])
    document = publication.get('metadata', {})
    reference = document.get('source_set_ref')
    if publication.get('object_type') != contract.SOURCE_SET_TYPE or not isinstance(reference, dict):
        raise RegistryConflict('SourceSet stream was used by another object type')
    return exact_source_set(db, catalog, task_id, reference, store)


def validate_source_set_publication(context):
    contract = _contract()
    identities = {str(item.logical_id) for item in context.objects}
    identities.update(event.stream_id.removeprefix('object:') for event in context.events if event.stream_id.startswith('object:'))
    selected = [item for item in context.objects if item.object_type == contract.SOURCE_SET_TYPE]
    uses = bool(selected) or any(event.payload.get('object_type') == contract.SOURCE_SET_TYPE for event in context.events)
    if not uses and identities:
        marks = ','.join('?' for _ in identities)
        uses = context.db.execute(f'SELECT 1 FROM objects WHERE object_type=? AND logical_id IN ({marks}) LIMIT 1',
                                  (contract.SOURCE_SET_TYPE, *sorted(identities))).fetchone() is not None
    if not uses:
        return
    if len(selected) != 1 or len(context.objects) != 1 or context.relations or len(context.events) != 2:
        raise RegistryConflict('SourceSet requires one immutable version and its exact commit')
    obj = selected[0]
    store = descriptor_store(context.event_store)
    record = contract.SourceSetVersion.from_dict(readable_descriptor(store, obj), context.event_store.catalog)
    binding = _local_binding(context.db, context.event_store.catalog, context.task_id, store)
    publications = [e for e in context.events if e.event_type == 'object_version_published/v1']
    terminals = [e for e in context.events if e.event_type == 'transaction_committed/v1']
    if len(publications) != 1 or len(terminals) != 1:
        raise RegistryConflict('SourceSet requires one publication and one commit')
    event, terminal = publications[0], terminals[0]
    key = contract.command_key(record.command_id)
    if (context.task_round_id is not None or context.net_instance_id is not None
            or obj.producer_invocation_id is not None or event.producer_invocation_id is not None
            or record.source_set_ref.source_id != binding['source_id']
            or record.owner_task_ref.to_dict()['ref'] != binding['task_ref']
            or record.publisher_bootstrap_ref.to_dict()['ref'] != binding['bootstrap_command_ref']
            or obj.logical_id != record.source_set_ref.ref.entity_id or obj.version_id != record.source_set_ref.ref.version_id
            or obj.version_id != contract.version_id(context.task_id, record.command_id)
            or obj.schema_ref != contract.SOURCE_SET_SCHEMA or context.idempotency_key != key
            or event.stream_id != f'object:{obj.logical_id}' or event.aggregate_id != str(obj.logical_id)
            or event.aggregate_type != obj.object_type or event.producer_principal != 'framework'
            or event.task_control or event.criticality != 'authoritative'
            or event.command_id != key or event.idempotency_key != key
            or event.payload_schema_ref != 'registry_v1/object_version_published/v1'
            or dict(event.payload) != {'logical_id': str(obj.logical_id), 'version_id': str(obj.version_id),
                'object_type': obj.object_type, 'size': obj.size, 'media_type': obj.media_type,
                'schema_ref': obj.schema_ref, 'storage_locator': obj.storage_locator, 'metadata': dict(obj.metadata)}
            or terminal.stream_id != f'transaction:{context.transaction_id}'
            or terminal.aggregate_id != str(context.transaction_id) or terminal.aggregate_type != 'transaction'
            or terminal.criticality != 'authoritative' or terminal.task_control or terminal.producer_principal != 'framework'
            or terminal.producer_invocation_id is not None or terminal.command_id != key or terminal.idempotency_key != key
            or terminal.payload_schema_ref != 'registry_v1/transaction_committed/v1'
            or dict(terminal.payload) != {'object_count': 1, 'relation_count': 0, 'fact_count': 1}):
        raise RegistryConflict('SourceSet owner/command/publication differs')
    prior = current_source_set_document(context.db, context.event_store.catalog, context.task_id, str(obj.logical_id), store)
    if record.sequence == 1:
        if prior is not None or obj.logical_id != contract.set_id(context.task_id, record.command_id):
            raise RegistryConflict('SourceSet creation conflicts with existing identity')
    elif (prior is None or prior['source_set_ref'] != record.predecessor_ref.to_dict()
          or prior['sequence'] + 1 != record.sequence
          or any(prior[k] != record.to_dict()[k] for k in ('owner_task_ref', 'publisher_bootstrap_ref'))):
        raise RegistryConflict('stale SourceSet version or owner')
