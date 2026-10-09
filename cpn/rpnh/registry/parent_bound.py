"""H7 protected child provenance in the original Registry/PN boundaries.

This module provides transaction staging and integrity checks, not a production
bound-create entry. Until authenticated receipt + physical reservation adapters
are installed there is deliberately no normal constructor of native evidence.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from .event_store import RegistryConflict
from .models import TypedRelation, VersionRef
from .identities import TypedId
from .publication import _ref_payload, _version_from_payload, _stable_id
from .parent_child import (BOUND_KINDS,BOUND_PROTOCOL,CAPABILITY_SCHEMA,PARENT_KINDS,
    _Snapshot,_ref,_json,digest,_require_evidence,_native_boundary,ParentChildUnsupported)


def _candidate_signal(kind, body):
    return (kind in BOUND_KINDS or kind=='resource_version/v1' and body.get('content_schema_ref')==CAPABILITY_SCHEMA
            or kind=='native_run_identity/v1' and BOUND_PROTOCOL in body.get('protocol_versions',()))


def _bound_signal(db):
    rows=db.execute("SELECT object_type,metadata_json FROM objects WHERE object_type IN "
        "('native_run_identity/v1','parent_bound_bootstrap/v1','parent_bound_origin/v1','resource_version/v1')")
    return any(_candidate_signal(row['object_type'],json.loads(row['metadata_json'])) for row in rows)


def preflight_bound_writer(path):
    """Read-only rejection before Core/EventStore writable construction."""
    path=Path(path)
    if not path.is_file():return
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='objects'").fetchone() is None:
            return  # Preserve ordinary fresh empty-store initialization.
        if _bound_signal(db):
            raise ParentChildUnsupported('H7 bound child cannot reopen or acquire a new writer')


def reject_bound_writer_at(db):
    if _bound_signal(db):
        raise ParentChildUnsupported('H7 bound child writer entry is immutable')


def _one(snap,kind):
    rows=snap.db.execute('SELECT logical_id,version_id FROM objects WHERE object_type=?',(kind,)).fetchall()
    if len(rows)!=1:raise RegistryConflict('H7 bound identity is incomplete or duplicated: '+kind)
    ref={'entity_type':kind,'logical_id':rows[0]['logical_id'],'version_id':rows[0]['version_id']}
    return ref,snap.obj(ref)


def _marker(snap):
    ref,marker=_one(snap,BOUND_KINDS[0])
    _validate_acceptance_copy(snap.store,marker['acceptance'])
    run=snap.obj(marker['child_run_ref']);genesis=snap.obj(marker['genesis_ref'])
    run_row=snap.row(marker['child_run_ref']);mark_row=snap.row(ref);gen_row=snap.row(marker['genesis_ref'])
    if (BOUND_PROTOCOL not in run['protocol_versions'] or marker['record_ref']!=ref
            or run['task_ref']!=marker['child_task_ref'] or run['task_branch_ref']!=marker['child_branch_ref']
            or genesis['run_identity_ref']!=marker['child_run_ref']
            or run_row['transaction_id']!=mark_row['transaction_id'] or gen_row['transaction_id']!=mark_row['transaction_id']
            or genesis['bootstrap_transaction_id']!=mark_row['transaction_id']):
        raise RegistryConflict('H7 bound marker/protocol/native genesis closure differs')
    expected=[('derived_from',marker['genesis_ref']),('derived_from',marker['child_run_ref']),
              ('produced_by',marker['bootstrap_ref'])]
    _edges_at(snap,ref,expected,mark_row['transaction_id'])
    return ref,marker


def _edges_at(snap,ref,expected,transaction):
    rows=snap.db.execute("SELECT * FROM relations WHERE json_extract(source_json,'$.version_id')=?",
                         (ref['version_id'],)).fetchall()
    actual=[]
    for row in rows:
        target=json.loads(row['target_json'])
        target['logical_id']=target.pop('entity_id')
        actual.append((row['relation_type'],target))
    if (sorted(_json(v) for v in actual)!=sorted(_json(v) for v in expected)
            or any(r['transaction_id']!=transaction or r['strength']!='strong' for r in rows)):
        raise RegistryConflict('H7 protected relation closure differs')


def bound_integrity_at(store,db,task_id,writer_epoch,*,require_origin=True,require_net=True,claim=None,for_execution=False):
    if not _bound_signal(db):return None
    snap=_Snapshot(store,db,task_id)
    marker_ref,marker=_marker(snap)
    epoch=int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
    if writer_epoch!=marker['initial_writer_epoch'] or epoch!=marker['initial_writer_epoch']:
        raise RegistryConflict('H7 bound execution requires its original child writer entry')
    if for_execution:
        authority_ref,authority=snap.canonical_latest('run_execution_authority/v1')
        if (authority['run_ref']!=marker['child_run_ref'] or authority['task_ref']!=marker['child_task_ref']
                or authority.get('execution_generation',0)!=0 or authority['status'] in ('stopped_by_owner','terminal')
                or authority.get('terminal_evidence_ref') is not None):
            raise RegistryConflict('H7 bound execution authority is stopped, terminal, or foreign')
    if not require_origin:return marker
    origin_ref,origin=_one(snap,BOUND_KINDS[1]);cap_ref=origin['capability_ref'];cap=snap.obj(cap_ref)
    body=json.loads(snap.payload(cap_ref))
    binding=__import__('cpn.rpnh.registry._event_store.source_identity',fromlist=['read_source_binding']).read_source_binding(db,store.catalog,task_id)
    if (origin['record_ref']!=origin_ref or origin['marker_ref']!=marker_ref
            or origin['child_run_ref']!=marker['child_run_ref'] or origin['child_task_ref']!=marker['child_task_ref']
            or origin['initial_writer_epoch']!=marker['initial_writer_epoch']
            or origin['initial_declaration_digest']!=marker['initial_declaration_digest']
            or binding is None or json.loads(binding['binding_metadata_json'])['binding_ref']!=origin['child_source_binding_ref']
            or cap.get('content_schema_ref')!=CAPABILITY_SCHEMA or cap['task_ref']!=marker['child_task_ref']
            or cap['origin_kind']!='private_system' or cap['producer_ref']!=marker['bootstrap_ref']
            or cap['lifetime_ref']!=marker['bootstrap_ref']
            or body!={'schema_version':CAPABILITY_SCHEMA,'origin_ref':origin_ref,'marker_ref':marker_ref,
                'child_run_ref':marker['child_run_ref'],'child_task_ref':marker['child_task_ref'],
                'parent_acceptance':marker['acceptance'],'initial_writer_epoch':marker['initial_writer_epoch'],
                'initial_declaration_digest':marker['initial_declaration_digest']}
            or snap.row(origin_ref)['transaction_id']!=snap.row(cap_ref)['transaction_id']):
        raise RegistryConflict('H7 origin/capability is not its exact protected native provenance')
    _edges_at(snap,origin_ref,[('produced_by',marker['bootstrap_ref']),('derived_from',marker_ref),
        ('derived_from',origin['child_source_binding_ref']),('derived_from',cap_ref)],snap.row(origin_ref)['transaction_id'])
    genesis=snap.obj(marker['genesis_ref'])
    catalog_ref=genesis['type_catalog_ref']
    if cap['content_schema_authority_ref']!=catalog_ref:
        raise RegistryConflict('H7 capability requires its original exact native catalog source')
    catalog=snap.obj(catalog_ref)
    from .schema_catalog import SchemaCatalog
    if (catalog.get('catalog_identity')!='rpnh-v1'
            or json.loads(catalog['schemas'][CAPABILITY_SCHEMA]['source']) !=
                json.loads(SchemaCatalog._mechanical_schema_path(CAPABILITY_SCHEMA).read_bytes())):
        raise RegistryConflict('H7 capability catalog source differs from installed protected schema')
    if not require_net:return origin
    adoptions=db.execute("SELECT * FROM events WHERE event_type='net_adopted/v1' ORDER BY ordinal").fetchall()
    if len(adoptions)!=1:raise RegistryConflict('H7 child requires its sole initial adoption')
    net_ref=json.loads(adoptions[0]['payload_json'])['net_instance_ref']
    place=_check_bound_net(snap,net_ref,marker,cap_ref)
    tokens=[]
    for row in db.execute("SELECT logical_id,version_id,metadata_json FROM objects WHERE object_type='petri_token/v1'"):
        value=json.loads(row['metadata_json'])
        if value['place']==place:
            tokens.append(value)
    if len(tokens)!=1:
        raise RegistryConflict('H7 origin token is absent, duplicated, or reminted')
    token=tokens[0]
    resource={'resource_id':cap_ref['logical_id'],'resource_version_id':cap_ref['version_id']}
    if (token['net_instance_ref']!=net_ref or token['resource_ref']!=resource or token['lease_identity_ref']!=cap_ref
            or token['consumed_by'] is not None or token['epoch']!=0 or token['producer'] is not None):
        raise RegistryConflict('H7 origin token lacks exact resource/lease provenance')
    if claim is not None:
        refs=claim.get('claimed_input_refs',[])
        if refs.count(token['petri_token_ref'])!=1 or claim.get('net_instance_ref')!=net_ref:
            raise RegistryConflict('H7 admission omits the exact protected origin reference')
        if token['petri_token_ref'] in claim.get('consumed_input_refs',[]):
            raise RegistryConflict('H7 origin reference cannot be consumed')
    return origin


def _check_bound_net(snap,net_ref,marker,cap_ref):
    from ..executable_net import load_compiled_net
    net=snap.obj(net_ref);rr=net['team_net_declaration_resource_ref']
    ref={'entity_type':'resource_version/v1','logical_id':rr['resource_id'],'version_id':rr['resource_version_id']}
    raw=snap.payload(ref)
    if digest(raw)!=marker['initial_declaration_digest']:
        raise RegistryConflict('H7 initial declaration differs from accepted frozen bytes')
    compiled=load_compiled_net(json.loads(raw));pn=compiled.symbolic
    from .parent_child import NATIVE_LAUNCH_EXECUTOR
    if any(op.executor==NATIVE_LAUNCH_EXECUTOR for op in pn.operations):
        raise ParentChildUnsupported('H7a child does not support recursive native launch')
    places=[p for p in pn.places if p.schema==CAPABILITY_SCHEMA]
    if len(places)!=1:raise RegistryConflict('H7 bound net requires one reserved origin place')
    place=places[0]
    if any(p.place==place.name for p in compiled.ports):
        raise RegistryConflict('H7 origin is an internal structural place, not a public operation port')
    pools=[p for p in pn.lease_pools if p.place==place.name]
    arcs=[a for a in pn.arcs if a.place==place.name]
    if (place.token_kind!='resource_lease' or not place.reusable or place.capacity!=1 or place.initial_tokens
            or len(pools)!=1 or len(pools[0].initial_resources)!=1 or pools[0].initial_slots
            or len(arcs)!=len(pn.transitions)
            or {(a.transition,a.direction,a.mode,a.weight) for a in arcs} !=
                {(t.name,'input','read',1) for t in pn.transitions}
            or any(a.lease_pool==pools[0].name for a in pn.variable_resource_arcs)
            or any(getattr(a,'place',None)==place.name for a in pn.reset_arcs)):
        raise RegistryConflict('H7 protected origin structure is not one non-consuming read on every transition')
    root=snap.obj(net['team_design_root_ref'])
    from .static_lease_claims import _resource_plan
    # The ordinary registered net resource plan must bind the protected exact ref.
    plan=_resource_plan(net,root,compiled,lambda ref,kind=None:snap.obj(ref))
    selected=[p for p in plan.lease_pools if p.lease_pool_place==place.name]
    if len(selected)!=1 or len(selected[0].initial_resource_refs)!=1:
        raise RegistryConflict('H7 origin M0 binding differs')
    exact=selected[0].initial_resource_refs[0]
    if {'entity_type':exact.entity_type,'logical_id':exact.logical_id,'version_id':exact.version_id}!=cap_ref:
        raise RegistryConflict('H7 initial pool does not bind the exact protected origin capability')
    return place.name


def assert_bound_integrity(core,*,claim=None,require_net=True,for_execution=False):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return bound_integrity_at(core.event_store,db,core.task_id,core.writer_epoch,
                                 require_net=require_net,claim=claim,for_execution=for_execution)


def validate_bound_commit(store,db,*,task_id,writer_epoch,objects,events,relations,existing,transaction_id):
    if not _bound_signal(db):return
    snap=_Snapshot(store,db,task_id,proposal=objects)
    _,marker=_marker(snap)
    if any(o.object_type in ('native_run_identity/v1','native_genesis_manifest/v1','bootstrap_command/v1') for o in objects):
        raise RegistryConflict('H7 bound native genesis cannot be relabelled or replayed')
    if marker['initial_writer_epoch']!=writer_epoch:
        raise RegistryConflict('H7 bound write belongs to another writer entry')
    forbidden={'native_resume_refenced/v1','run_reopened/v1','run_reopen_authorization/v1',
               'transition_firing_superseded_by_native_resume/v1'}
    if any(e.event_type in forbidden for e in events) or any(o.object_type=='run_reopen_authorization/v1' for o in objects):
        raise ParentChildUnsupported('H7 bound child restart is outside this contract')
    if any(o.object_type=='run_execution_authority/v1' and o.metadata.get('execution_generation',0)!=0 for o in objects):
        raise ParentChildUnsupported('H7 bound child generation cannot change')
    authority_rows=db.execute("SELECT o.metadata_json FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE o.object_type='run_execution_authority/v1' ORDER BY e.ordinal DESC").fetchall()
    if authority_rows:
        current_authority=json.loads(authority_rows[0][0])
        for item in objects:
            if (item.object_type=='run_execution_authority/v1'
                    and current_authority['status'] in ('stopped_by_owner','terminal')
                    and item.metadata['status']!=current_authority['status']):
                raise ParentChildUnsupported('H7 bound child cannot revive closed execution authority')
    for event in events:
        if event.event_type=='net_adopted/v1':
            previous=db.execute("SELECT transaction_id,payload_json FROM events WHERE event_type='net_adopted/v1'").fetchall()
            if previous and (len(previous)!=1 or existing is None or previous[0]['transaction_id']!=existing['transaction_id']
                              or json.loads(previous[0]['payload_json'])!=dict(event.payload)):
                raise RegistryConflict('H7 bound child cannot adopt a successor net')
            if event.payload.get('supersedes_net_ref') is not None:
                raise RegistryConflict('H7 initial adoption cannot supersede another net')
            origin=bound_integrity_at(store,db,task_id,writer_epoch,require_net=False)
            _check_bound_net(snap,event.payload['net_instance_ref'],marker,origin['capability_ref'])
        if event.event_type in ('firing_admitted/v1','operation_execution_started/v1'):
            firing_ref=event.payload['transition_firing_ref']
            firing_snap=_Snapshot(store,db,task_id,firing=firing_ref['version_id'],proposal=objects)
            firing=firing_snap.obj(firing_ref)
            delta=firing_snap.obj(firing['claim_marking_delta_ref'])
            bound_integrity_at(store,db,task_id,writer_epoch,claim={**firing,'consumed_input_refs':delta['consumed_refs']},for_execution=True)
    # Settlement/effects cannot replace, consume or reset the protected token.
    origin_rows=db.execute("SELECT metadata_json FROM objects WHERE object_type='parent_bound_origin/v1'").fetchall()
    if origin_rows:
        origin=json.loads(origin_rows[0][0]);cap=origin['capability_ref']
        protected=[]
        for row in db.execute("SELECT metadata_json FROM objects WHERE object_type='petri_token/v1'"):
            token=json.loads(row[0])
            if token.get('lease_identity_ref')==cap:protected.append(token['petri_token_ref'])
        origin_resource={'resource_id':cap['logical_id'],'resource_version_id':cap['version_id']}
        proposed_origin=[]
        for item in objects:
            if item.object_type=='petri_token/v1':
                token=item.metadata
                # Check the adopted/frozen native origin place as well as refs;
                # a caller cannot evade protection by changing lease_identity.
                place=_check_bound_net(snap,token['net_instance_ref'],marker,cap)
                if (token['place']==place or token.get('lease_identity_ref')==cap
                        or token.get('resource_ref')==origin_resource):
                    proposed_origin.append(token)
                    if (token['place']!=place or token['resource_ref']!=origin_resource
                            or token['lease_identity_ref']!=cap or token['epoch']!=0
                            or token['consumed_by'] is not None or token['producer'] is not None):
                        raise RegistryConflict('H7 prospective origin token lacks exact native provenance')
            if item.object_type=='petri_token/v1' and item.metadata.get('lease_identity_ref')==cap and protected:
                if item.metadata.get('petri_token_ref') not in protected:
                    raise RegistryConflict('H7 origin token cannot be cloned or reminted')
            if item.object_type=='marking_delta/v1' and any(ref in protected for ref in item.metadata.get('consumed_refs',())):
                raise RegistryConflict('H7 origin token cannot be consumed or reset')
            if item.object_type=='marking_checkpoint/v1' and protected and not all(ref in item.metadata['token_refs'] for ref in protected):
                raise RegistryConflict('H7 checkpoint cannot drop protected origin reference')
        if len(proposed_origin)>1:
            raise RegistryConflict('H7 M0 cannot duplicate the protected origin token')


def stage_bound_bootstrap(core,tx,*,run_ref,task_ref,branch_ref,genesis_ref,bootstrap_ref,acceptance):
    """Internal staging only; authenticated create/reservation issuer is absent."""
    ref=_ref(BOUND_KINDS[0],str(run_ref.version_id))
    body={'record_ref':_ref_payload(ref),'child_run_ref':_ref_payload(run_ref),'child_task_ref':_ref_payload(task_ref),
        'child_branch_ref':_ref_payload(branch_ref),'genesis_ref':_ref_payload(genesis_ref),
        'bootstrap_ref':_ref_payload(bootstrap_ref),'initial_writer_epoch':core.writer_epoch,
        'acceptance':acceptance,'initial_declaration_digest':acceptance['initial_declaration_digest']}
    _require_evidence(core.event_store,'bound_bootstrap',acceptance)
    _validate_acceptance_copy(core.event_store,acceptance)
    tx.prewrite(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,payload=_json(body),
        metadata=body,media_type='application/json',schema_ref='registry_v1/'+ref.entity_type)
    for index,(relation,target) in enumerate((('derived_from',genesis_ref),('derived_from',run_ref),('produced_by',bootstrap_ref))):
        tx.relate(TypedRelation(_stable_id('relation',str(ref.version_id),index),relation,ref,target),system_owned=True)
    return ref


def close_bound_origin(core,*,evidence):
    """Protected child-local origin + capability in one original transaction."""
    from .resource_service import _publish_private_system
    from .resources import PrivateSystemOrigin,PublishResource
    from ._event_store.source_identity import read_source_binding
    with core.event_store.connect() as db:
        db.execute('BEGIN');snap=_Snapshot(core.event_store,db,core.task_id)
        marker_ref,marker=_marker(snap)
        binding=read_source_binding(db,core.catalog,core.task_id)
        if binding is None:raise RegistryConflict('H7 origin requires exact native child source binding')
        binding_ref=json.loads(binding['binding_metadata_json'])['binding_ref']
    ref=_ref(BOUND_KINDS[1],marker_ref['version_id'])
    cap_body={'schema_version':CAPABILITY_SCHEMA,'origin_ref':_ref_payload(ref),'marker_ref':marker_ref,
        'child_run_ref':marker['child_run_ref'],'child_task_ref':marker['child_task_ref'],
        'parent_acceptance':marker['acceptance'],'initial_writer_epoch':marker['initial_writer_epoch'],
        'initial_declaration_digest':marker['initial_declaration_digest']}
    key='h7-origin:'+str(ref.version_id);tx=core.begin(idempotency_key=key)
    with _native_boundary(evidence,core.event_store,'bound_origin',marker):
        bootstrap=_version_from_payload(marker['bootstrap_ref'])
        capability=_publish_private_system(core,_version_from_payload(marker['child_task_ref']),PublishResource(
            origin=PrivateSystemOrigin(bootstrap),payload=_json(cap_body),media_type='application/json',
            content_schema_ref=CAPABILITY_SCHEMA,summary='Protected parent-bound origin',lifetime_ref=bootstrap,
            idempotency_key=key),transaction=tx)
        body={'record_ref':_ref_payload(ref),'marker_ref':marker_ref,'child_run_ref':marker['child_run_ref'],
            'child_task_ref':marker['child_task_ref'],'child_source_binding_ref':binding_ref,
            'capability_ref':_ref_payload(capability.as_version_ref()),'initial_writer_epoch':marker['initial_writer_epoch'],
            'initial_declaration_digest':marker['initial_declaration_digest']}
        tx.prewrite(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,payload=_json(body),
            metadata=body,media_type='application/json',schema_ref='registry_v1/'+ref.entity_type)
        for index,(relation,target) in enumerate((('produced_by',marker['bootstrap_ref']),('derived_from',marker_ref),
            ('derived_from',binding_ref),('derived_from',body['capability_ref']))):
            tx.relate(TypedRelation(_stable_id('relation',str(ref.version_id),index),relation,ref,_version_from_payload(target)),system_owned=True)
        tx.commit()
    return capability


def validate_bound_publication(store,db,*,task_id,writer_epoch,objects,events,relations,transaction_id,idempotency_key,existing):
    snap=_Snapshot(store,db,task_id,proposal=objects)
    if (any(o.producer_invocation_id is not None for o in objects)
            or any(e.producer_invocation_id is not None or e.producer_principal!='framework' or e.task_control
                   or e.event_type not in ('object_version_published/v1','relation_published/v1','transaction_committed/v1')
                   for e in events)
            or len(events)!=len(objects)+len(relations)+1
            or any(r.strength!='strong' or not r.system_owned or r.producer_invocation_id is not None or r.metadata
                   for r in relations)):
        raise RegistryConflict('H7 protected bootstrap/origin requires exact strong system-owned transaction closure')
    markers=[o for o in objects if o.object_type==BOUND_KINDS[0]]
    origins=[o for o in objects if o.object_type==BOUND_KINDS[1]]
    capabilities=[o for o in objects if o.object_type=='resource_version/v1' and o.metadata.get('content_schema_ref')==CAPABILITY_SCHEMA]
    if markers:
        if len(markers)!=1 or origins or capabilities or existing is not None:
            raise RegistryConflict('H7 bound bootstrap is one fresh native identity transaction')
        actual_marker=_ref_payload(VersionRef(markers[0].object_type,markers[0].logical_id,markers[0].version_id))
        body=snap.obj(actual_marker)
        expected_marker=_ref(BOUND_KINDS[0],body['child_run_ref']['version_id'])
        if body['record_ref']!=actual_marker or actual_marker!=_ref_payload(expected_marker):
            raise RegistryConflict('H7 marker self identity is not its unique native bootstrap identity')
        _require_evidence(store,'bound_bootstrap',body['acceptance'])
        _validate_acceptance_copy(store,body['acceptance'])
        expected_types={'task/v1','task_branch/v1','bootstrap_command/v1','native_run_identity/v1','native_genesis_manifest/v1',BOUND_KINDS[0]}
        if len(objects)!=6 or {o.object_type for o in objects}!=expected_types:
            raise RegistryConflict('H7 bound marker must share exact fresh native identity/genesis transaction')
        # Every protected native identity must be the exact object in this
        # bootstrap transaction, not a locally valid object from another cut.
        native_fields={
            'task/v1':('child_task_ref','task_id','task_version_id'),
            'task_branch/v1':('child_branch_ref','task_branch_id','task_branch_version_id'),
            'bootstrap_command/v1':('bootstrap_ref','bootstrap_command_id','bootstrap_command_version_id'),
            'native_run_identity/v1':('child_run_ref','run_id','run_version_id'),
            'native_genesis_manifest/v1':('genesis_ref','genesis_id','genesis_version_id')}
        for item in objects:
            if item.object_type not in native_fields:continue
            field,logical,version=native_fields[item.object_type]
            exact=_ref_payload(VersionRef(item.object_type,item.logical_id,item.version_id))
            if (body[field]!=exact or item.metadata.get(logical)!=str(item.logical_id)
                    or item.metadata.get(version)!=str(item.version_id)):
                raise RegistryConflict('H7 marker native self/exact identities differ')
        run=snap.obj(body['child_run_ref']);genesis=snap.obj(body['genesis_ref'])
        branch=snap.obj(body['child_branch_ref'])
        catalog_ref,_=_one(snap,'registry_type_catalog/v1')
        if (body['child_task_ref']['logical_id']!=str(task_id)
                or branch['task_ref']!=body['child_task_ref'] or branch['branch_name']!=run['branch_id']
                or genesis['type_catalog_ref']!=catalog_ref
                or BOUND_PROTOCOL not in run['protocol_versions'] or run['task_ref']!=body['child_task_ref']
                or run['task_branch_ref']!=body['child_branch_ref'] or genesis['run_identity_ref']!=body['child_run_ref']
                or genesis['bootstrap_transaction_id']!=str(transaction_id) or body['initial_writer_epoch']!=writer_epoch
                or body['initial_declaration_digest']!=body['acceptance']['initial_declaration_digest']):
            raise RegistryConflict('H7 bootstrap marker differs from native transaction identities')
        if db.execute("SELECT 1 FROM objects WHERE object_type='native_run_identity/v1'").fetchone():
            raise RegistryConflict('H7 cannot relabel an existing standalone run')
        expected=[('derived_from',body['genesis_ref']),('derived_from',body['child_run_ref']),('produced_by',body['bootstrap_ref'])]
        if len(relations)!=3 or sorted(_json((r.relation_type,_ref_payload(r.target))) for r in relations)!=sorted(_json(v) for v in expected):
            raise RegistryConflict('H7 marker causal edges missing')
        if any(_ref_payload(r.source)!=body['record_ref'] or not r.system_owned or r.producer_invocation_id is not None for r in relations):
            raise RegistryConflict('H7 marker producer scope differs')
        return
    if len(origins)!=1 or len(capabilities)!=1 or len(objects)!=2:
        raise RegistryConflict('H7 origin capability can only be minted by its one atomic protected origin producer')
    _,marker=_marker(snap);_require_evidence(store,'bound_origin',marker)
    if db.execute("SELECT 1 FROM objects WHERE object_type='parent_bound_origin/v1'").fetchone():
        raise RegistryConflict('H7 protected origin is one-time, never reminted/replayed')
    body=dict(origins[0].metadata);cap=capabilities[0]
    actual_origin=_ref_payload(VersionRef(origins[0].object_type,origins[0].logical_id,origins[0].version_id))
    if body['record_ref']!=actual_origin or actual_origin!=_ref_payload(_ref(BOUND_KINDS[1],marker['record_ref']['version_id'])):
        raise RegistryConflict('H7 origin self identity differs from its unique marker')
    genesis=snap.obj(marker['genesis_ref'])
    if cap.metadata.get('content_schema_authority_ref')!=genesis['type_catalog_ref']:
        raise RegistryConflict('H7 prospective capability lacks original native catalog authority')
    catalog=snap.obj(genesis['type_catalog_ref'])
    from .schema_catalog import SchemaCatalog
    if (catalog.get('catalog_identity')!='rpnh-v1' or
            json.loads(catalog['schemas'][CAPABILITY_SCHEMA]['source']) !=
                json.loads(SchemaCatalog._mechanical_schema_path(CAPABILITY_SCHEMA).read_bytes())):
        raise RegistryConflict('H7 prospective capability catalog schema differs')
    cap_ref=_ref_payload(VersionRef(cap.object_type,cap.logical_id,cap.version_id))
    expected_cap={'schema_version':CAPABILITY_SCHEMA,'origin_ref':body['record_ref'],'marker_ref':body['marker_ref'],
        'child_run_ref':marker['child_run_ref'],'child_task_ref':marker['child_task_ref'],
        'parent_acceptance':marker['acceptance'],'initial_writer_epoch':marker['initial_writer_epoch'],
        'initial_declaration_digest':marker['initial_declaration_digest']}
    if (body['marker_ref']!=marker['record_ref'] or body['capability_ref']!=cap_ref
            or body['child_run_ref']!=marker['child_run_ref'] or body['child_task_ref']!=marker['child_task_ref']
            or body['initial_writer_epoch']!=writer_epoch or body['initial_declaration_digest']!=marker['initial_declaration_digest']
            or json.loads(snap.object_store.read_verified(cap))!=expected_cap
            or cap.metadata.get('origin_kind')!='private_system'
            or cap.metadata.get('producer_ref')!=marker['bootstrap_ref']
            or cap.metadata.get('lifetime_ref')!=marker['bootstrap_ref']
            or cap.metadata.get('task_ref')!=marker['child_task_ref']):
        raise RegistryConflict('H7 origin/capability atomic material differs')
    binding=__import__('cpn.rpnh.registry._event_store.source_identity',fromlist=['read_source_binding']).read_source_binding(db,store.catalog,task_id)
    if binding is None or body['child_source_binding_ref']!=json.loads(binding['binding_metadata_json'])['binding_ref']:
        raise RegistryConflict('H7 origin child native source differs')
    expected=[('produced_by',marker['bootstrap_ref']),('derived_from',marker['record_ref']),
              ('derived_from',body['child_source_binding_ref']),('derived_from',cap_ref)]
    expected=[(body['record_ref'],kind,target) for kind,target in expected]
    expected.append((cap_ref,'produced_by',marker['bootstrap_ref']))
    actual=[(_ref_payload(r.source),r.relation_type,_ref_payload(r.target)) for r in relations]
    if sorted(_json(v) for v in actual)!=sorted(_json(v) for v in expected):
        raise RegistryConflict('H7 origin/capability complete causal relations differ')


def reject_bound_reentry(core):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        if _bound_signal(db):
            raise ParentChildUnsupported('H7 bound child cannot resume, reopen, or reenter')


def assert_bound_adoption(core,net_ref,key):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        if not _bound_signal(db):return
        rows=db.execute("SELECT * FROM events WHERE event_type='net_adopted/v1'").fetchall()
        if rows and (len(rows)!=1 or rows[0]['idempotency_key']!=key or json.loads(rows[0]['payload_json'])['net_instance_ref']!=_ref_payload(net_ref)):
            raise RegistryConflict('H7 bound child cannot adopt a successor net')
        assert_origin=bound_integrity_at(core.event_store,db,core.task_id,core.writer_epoch,require_net=False)
        snap=_Snapshot(core.event_store,db,core.task_id);_,marker=_marker(snap)
        _check_bound_net(snap,_ref_payload(net_ref),marker,assert_origin['capability_ref'])


def _validate_acceptance_copy(store, value):
    """Check a detached source-qualified receipt copy, never authenticate it.

    Authentication/delivery are exclusively the missing native evidence issuer.
    JSON copies alone never enter bound bootstrap. External native refs stay in
    canonical serialized records, explicitly scoped by parent_source_id; no
    parent ref is inserted as a child-local strong relation.
    """
    expected={'parent_source_id','acceptance_json','intent_json','acceptance_transaction_id',
              'acceptance_event_id','acceptance_commit_ordinal','initial_declaration_digest'}
    if type(value) is not dict or set(value)!=expected:
        raise RegistryConflict('H7 external acceptance evidence shape differs')
    acceptance=json.loads(value['acceptance_json']);intent=json.loads(value['intent_json'])
    if h7_json(acceptance)!=value['acceptance_json'] or h7_json(intent)!=value['intent_json']:
        raise RegistryConflict('H7 external evidence must preserve canonical exact bytes')
    store.catalog.validate_instance(PARENT_KINDS[3],category='object',instance=acceptance)
    from .parent_child import INTENT_V2
    if intent['record_ref']['entity_type'] != INTENT_V2:
        raise ParentChildUnsupported('legacy intent cannot bootstrap a fresh bound child')
    store.catalog.validate_instance(INTENT_V2,category='object',instance=intent)
    from ..public_material_contracts import canonical as public_canonical
    from .parent_child import bootstrap_request_material
    if (value['parent_source_id']!=acceptance['parent']['source_id']
            or acceptance['parent']!=intent['parent'] or acceptance['slot_id']!=intent['slot_id']
            or acceptance['execution']!=intent['execution'] or acceptance['intent_ref']!=intent['record_ref']
            or acceptance['target']!=intent['target'] or acceptance['envelope_digest']!=digest(public_canonical(intent['envelope']))
            or acceptance['envelope_digest']!=intent['envelope_digest']
            or acceptance['public_material_digest']!=intent['materials']['public_material_digest']
            or value['initial_declaration_digest']!=intent['materials']['initial_declaration_digest']
            or acceptance['request_digest']!=digest(_json(bootstrap_request_material(intent,acceptance['dispatch_ref'])))
            or acceptance['allowed_action']!='fresh_bound_bootstrap_once'):
        raise RegistryConflict('H7 external acceptance/intent/material source closure differs')
    TypedId.parse(value['acceptance_transaction_id'],expected='transaction')
    TypedId.parse(value['acceptance_event_id'],expected='event')
    if type(value['acceptance_commit_ordinal']) is not int or value['acceptance_commit_ordinal']<1:
        raise RegistryConflict('H7 external acceptance commit cut differs')
    return acceptance


def h7_json(value):
    return _json(value).decode('ascii')
