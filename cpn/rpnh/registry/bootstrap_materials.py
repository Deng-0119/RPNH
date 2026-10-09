"""Exact private-system byte/strong-relation closure in an existing read cut.

Shared by collaboration author materials and registered public inventories.
Does not open a reader, run a producer, import an installation or grant access.
"""
from __future__ import annotations
import json
from ._event_store.collaboration_descriptors import exact_prepared, exact_descriptor, readable_payload, _canonical_closure
from .event_store import RegistryConflict
from .publication import _fresh_bootstrap_reference_resource_metadata, _version_from_payload
from .schema_catalog import canonical_json


def read_bootstrap_material(db, core, ref, binding, *, media_type, derived_from=(), proposal=None, relations=(), events=(), strict_events=False, transaction_id=None, command_key=None):
    pair = {'entity_type':'resource_version/v1','logical_id':str(ref.resource_id),'version_id':str(ref.resource_version_id)}
    prepared = proposal if proposal is not None else exact_prepared(db,core.object_store,core.task_id,pair)
    if (prepared.object_type != pair['entity_type'] or str(prepared.logical_id)!=pair['logical_id'] or str(prepared.version_id)!=pair['version_id']):
        raise RegistryConflict('material exact reference differs')
    body=prepared.metadata
    task, bootstrap=(_version_from_payload(binding[k]) for k in ('task_ref','bootstrap_command_ref'))
    for authority in (binding['task_ref'],binding['bootstrap_command_ref'],binding['binding_ref']):
        exact_descriptor(db,core.object_store,core.task_id,authority)
    expected=_fresh_bootstrap_reference_resource_metadata(core,ref=ref,task_ref=task,bootstrap_ref=bootstrap,lifetime_ref=bootstrap,
        payload_size=prepared.size,media_type=media_type,content_schema_ref=body['content_schema_ref'],
        content_schema_authority_ref=body['content_schema_authority_ref'],summary=body['summary'],descriptors=body['descriptors'],extensions=body['extensions'],derived_from=derived_from)
    same=lambda a,b:canonical_json(a)==canonical_json(b)
    if not same(body,expected) or prepared.producer_invocation_id is not None:
        raise RegistryConflict('material bootstrap task/lifetime/provenance differs')
    endpoint=lambda v:{'entity_type':v.entity_type,'entity_id':str(v.entity_id),'version_id':str(v.version_id)}
    wanted=[('produced_by',endpoint(bootstrap)),*[('derived_from',endpoint(r.as_version_ref())) for r in derived_from]]
    wanted=sorted(canonical_json([kind,target]) for kind,target in wanted)
    if proposal is not None:
        actual=[]
        for relation in relations:
            if (relation.source != ref.as_version_ref() or relation.strength!='strong' or not relation.system_owned
                or relation.producer_invocation_id is not None or relation.metadata):
                raise RegistryConflict('material prospective relation differs')
            actual.append(canonical_json([relation.relation_type,endpoint(relation.target)]))
        if sorted(actual)!=wanted: raise RegistryConflict('material prospective exact relations differ')
        if strict_events: _check_proposed_events(prepared, relations, events, transaction_id=transaction_id, command_key=command_key)
        pubs=[e for e in events if e.event_type=='object_version_published/v1']
        if len(pubs)!=1 or pubs[0].payload.get('metadata')!=body or pubs[0].payload.get('version_id')!=str(ref.resource_version_id):
            raise RegistryConflict('material prospective publication differs')
    else:
        rows=db.execute("SELECT r.*,e.event_type,e.transaction_id AS event_transaction,e.payload_json,"
            "e.criticality AS event_criticality,e.task_id AS event_task,e.payload_schema_ref AS event_schema,"
            "e.stream_id AS event_stream,e.aggregate_id AS event_aggregate,e.aggregate_type AS event_aggregate_type,"
            "e.producer_invocation_id AS event_invocation,e.producer_principal AS event_principal "
            "FROM relations r LEFT JOIN events e ON e.event_id=r.published_event_id WHERE json_extract(r.source_json,'$.version_id')=?",(str(ref.resource_version_id),)).fetchall()
        tx=db.execute('SELECT transaction_id FROM objects WHERE version_id=?',(str(ref.resource_version_id),)).fetchone()[0]
        if strict_events:
            all_events = db.execute('SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal',(tx,)).fetchall()
            key=db.execute('SELECT idempotency_key FROM transactions WHERE transaction_id=?',(tx,)).fetchone()[0]
            object_events = [e for e in all_events if e['event_type']=='object_version_published/v1']
            relation_events = [e for e in all_events if e['event_type']=='relation_published/v1']
            terminals = [e for e in all_events if e['event_type']=='transaction_committed/v1']
            if (len(object_events)!=1 or len(terminals)!=1 or len(all_events)!=len(rows)+2
                or {e['event_id'] for e in relation_events}!={r['published_event_id'] for r in rows}
                or len(relation_events)!=len(rows)
                or json.loads(terminals[0]['payload_json'])!={'object_count':1,'relation_count':len(rows),'fact_count':len(rows)+1}
                or any(e['idempotency_key']!=key or e['command_id']!=key or e['producer_principal']!='framework' or e['producer_invocation_id'] is not None or e['criticality']!='authoritative'
                    or e['causation_event_id'] is not None or json.loads(e['parent_event_ids_json']) or e['task_control_sequence'] is not None for e in all_events)):
                raise RegistryConflict('material transaction has extra/malformed publication facts')
        actual=[]
        for row in rows:
            source,target=json.loads(row['source_json']),json.loads(row['target_json'])
            payload={'relation_id':row['relation_id'],'relation_type':row['relation_type'],'source':source,'target':target,'strength':'strong','metadata':{}}
            if (row['strength']!='strong' or row['transaction_id']!=tx or row['event_transaction']!=tx
                or row['event_criticality']!='authoritative' or row['event_type']!='relation_published/v1'
                or row['event_stream']!='relation:'+row['relation_id'] or row['event_aggregate']!=row['relation_id']
                or row['event_aggregate_type']!='typed_relation/v1' or row['event_invocation'] is not None or row['event_principal']!='framework'
                or row['event_task']!=str(core.task_id) or row['event_schema']!='registry_v1/relation_published/v1'
                or not same(source,endpoint(ref.as_version_ref())) or not same(json.loads(row['metadata_json']),{})
                or not same(json.loads(row['payload_json']),payload)
                or not _canonical_closure(db,core.task_id,transaction_id=tx,members=(('relation',row['relation_id']),('event',row['published_event_id'])))):
                raise RegistryConflict('material relation publication lacks exact canonical authority')
            actual.append(canonical_json([row['relation_type'],target]))
        if sorted(actual)!=wanted: raise RegistryConflict('material exact producer/dependency relations differ')
    return readable_payload(core.object_store,prepared,media_type=media_type), body


def _check_proposed_events(prepared, relations, events, *, transaction_id, command_key):
    expected_object={'logical_id':str(prepared.logical_id),'version_id':str(prepared.version_id),'object_type':prepared.object_type,
        'size':prepared.size,'media_type':prepared.media_type,'schema_ref':prepared.schema_ref,'storage_locator':prepared.storage_locator,'metadata':dict(prepared.metadata)}
    endpoint=lambda v:{'entity_type':v.entity_type,'entity_id':str(v.entity_id),'version_id':str(v.version_id)}
    expected_relations={str(r.relation_id):{'relation_id':str(r.relation_id),'relation_type':r.relation_type,'source':endpoint(r.source),
        'target':endpoint(r.target),'strength':r.strength,'metadata':dict(r.metadata)} for r in relations}
    objects=[e for e in events if e.event_type=='object_version_published/v1']
    links=[e for e in events if e.event_type=='relation_published/v1']
    terminals=[e for e in events if e.event_type=='transaction_committed/v1']
    if (transaction_id is None or command_key is None or len(objects)!=1 or len(terminals)!=1 or len(events)!=len(relations)+2 or len(links)!=len(relations)
        or canonical_json(objects[0].payload)!=canonical_json(expected_object)
        or objects[0].stream_id!='object:'+str(prepared.logical_id) or objects[0].aggregate_id!=str(prepared.logical_id)
        or objects[0].aggregate_type!=prepared.object_type or objects[0].payload_schema_ref!='registry_v1/object_version_published/v1'
        or {e.payload.get('relation_id') for e in links}!=set(expected_relations)
        or terminals[0].payload!={'object_count':1,'relation_count':len(relations),'fact_count':len(relations)+1}
        or any(e.idempotency_key!=command_key or e.command_id!=command_key or e.producer_principal!='framework' or e.producer_invocation_id is not None or e.criticality!='authoritative'
            or e.task_control or e.causation_event_id is not None or e.parent_event_ids for e in events)):
        raise RegistryConflict('material prospective publication event set differs')
    terminal=terminals[0]
    if (terminal.stream_id!='transaction:'+str(transaction_id) or terminal.aggregate_id!=str(transaction_id)
        or terminal.aggregate_type!='transaction' or terminal.payload_schema_ref!='registry_v1/transaction_committed/v1'):
        raise RegistryConflict('material prospective terminal identity differs')
    for e in links:
        identity=e.payload['relation_id']
        if (canonical_json(e.payload)!=canonical_json(expected_relations[identity]) or e.stream_id!='relation:'+identity
            or e.aggregate_id!=identity or e.aggregate_type!='typed_relation/v1' or e.payload_schema_ref!='registry_v1/relation_published/v1'):
            raise RegistryConflict('material prospective relation event differs')
