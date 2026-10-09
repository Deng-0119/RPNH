"""H7's Registry-native, single-slot causal records (offline core slice).

No scheduler, transport, worker launcher, receipt issuer or child constructor is
provided here. Native evidence is an internal non-serializable boundary whose
production issuer is intentionally absent until the original transport is wired.
Tests may allocate that boundary explicitly; this never certifies OS identity.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath

from .event_store import RegistryConflict
from .identities import TypedId
from .models import PreparedObject, PendingEvent, TypedRelation, VersionRef
from .publication import _ref_payload, _version_from_payload, _stable_id
from .schema_catalog import canonical_json
from ._event_store.collaboration_descriptors import descriptor_store, readable_descriptor
from ._event_store.source_identity import read_source_binding
from ._event_store.proposal import TransactionValidationContext
from ._event_store.accounting import _CANONICAL_EVENT_SQL

REQUEST_SCHEMA = 'rpnh/parent_child_request/v1'
CAPABILITY_SCHEMA = 'rpnh/parent_origin_capability/v1'
BOUND_PROTOCOL = 'rpnh/parent-bound-child/v1'
PARENT_KINDS = tuple('parent_child_' + name + '/v1' for name in (
    'intent', 'dispatch', 'worker', 'acceptance'))
BOUND_KINDS = ('parent_bound_bootstrap/v1', 'parent_bound_origin/v1')
INTENT_V2 = 'parent_child_intent/v2'
INTENT_KINDS = (PARENT_KINDS[0], INTENT_V2)
ALL_PARENT_KINDS = (*PARENT_KINDS, INTENT_V2)
KINDS = ALL_PARENT_KINDS + BOUND_KINDS
MATERIAL_SECTIONS = frozenset(('normalized_request', 'lowered_net', 'registration',
    'host', 'implementation', 'inputs', 'budgets'))


class ParentChildUnsupported(RegistryConflict):
    """The required original native transport boundary is not installed."""


def _json(value):
    # Unlike generic canonical_json, never coerce arbitrary Python objects.
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def digest(value: bytes) -> str:
    if type(value) is not bytes:
        raise TypeError('H7 digests require frozen bytes')
    return hashlib.sha256(value).hexdigest()


def _ref(kind, *scope):
    return VersionRef(kind, _stable_id('resource', 'h7', kind, *scope),
                      _stable_id('resource_version', 'h7', kind, *scope))


def _target(parent, slot_id, root_binding, control_root):
    if (not isinstance(root_binding, str) or not root_binding
            or not isinstance(control_root, str) or not PurePosixPath(control_root).is_absolute()
            or '..' in PurePosixPath(control_root).parts):
        raise ValueError('H7 preparation requires an exact trusted root binding/control root')
    key = {'protocol': 'rpnh/parent-bound-target/v1', 'parent_source': parent['source_id'],
        'parent_run_ref': parent['run_ref'], 'parent_task_ref': parent['task_ref'],
        'slot_id': slot_id, 'root_binding': root_binding}
    key_digest = digest(_json(key))
    task_id = 'task-h7-' + key_digest
    return {'key_digest': key_digest, 'root_binding': root_binding,
        'relative_path': 'h7-runs/' + key_digest + '/run', 'transport_task_id': task_id,
        'document_root': str(PurePosixPath(control_root) / 'bound_tasks' / task_id)}


@dataclass(frozen=True, slots=True)
class UnregisteredChildMaterialDraft:
    """Legacy supplied-byte diagnostic draft; not registered or production complete."""
    request_ref: VersionRef
    request_bytes: bytes
    parent_bytes: bytes
    target_bytes: bytes
    payloads: tuple[tuple[str, bytes], ...]

    @property
    def materials(self):
        payloads = dict(self.payloads)
        inventory = [{'name': name, 'size': len(payload), 'sha256': digest(payload)}
                     for name, payload in self.payloads]
        return {'request_sha256': digest(self.request_bytes),
            'normalized_request': json.loads(payloads['normalized_request']),
            'inventory': inventory, 'public_material_digest': digest(_json(inventory)),
            'initial_declaration_digest': digest(payloads['lowered_net'])}


def prepare_child_materials(*, parent, request_ref, request_bytes, root_binding,
                            control_root, public_payloads):
    """Pure K -> T -> normalized D. Does not read files, write, or select PN work.

    Full installed HOST inventory collection remains the native composition's
    responsibility; this function only freezes exact supplied public bytes.
    """
    if type(request_ref) is not VersionRef or request_ref.entity_type != 'resource_version/v1':
        raise TypeError('H7 request must be an exact native resource ref')
    if type(request_bytes) is not bytes:
        raise TypeError('H7 request must have immutable exact bytes')
    request = json.loads(request_bytes)
    from .schema_catalog import SchemaCatalog
    SchemaCatalog().validate_schema_ref(REQUEST_SCHEMA, request)
    if _json(request) != request_bytes:
        raise ValueError('H7 request must use exact canonical JSON bytes')
    target = _target(parent, request['slot_id'], root_binding, control_root)
    normalized = {'request': request, 'target': target}
    payloads = dict(public_payloads)
    if set(payloads) != MATERIAL_SECTIONS - {'normalized_request'}:
        raise ValueError('H7 material inventory is incomplete or has unknown sections')
    if any(type(value) is not bytes for value in payloads.values()):
        raise TypeError('H7 public materials must be immutable bytes')
    payloads['normalized_request'] = _json(normalized)
    return UnregisteredChildMaterialDraft(request_ref, request_bytes, _json(parent), _json(target),
                                  tuple(sorted(payloads.items())))


@dataclass(frozen=True, slots=True)
class PreparedChildMaterials:
    """Registered Module facts; this DTO itself never establishes authority."""
    request_ref: VersionRef
    request_bytes: bytes
    parent_bytes: bytes
    target_bytes: bytes
    registered_inventory_ref: object
    root_bytes: bytes

    @property
    def materials(self):
        from ..public_material_contracts import decode
        from .public_materials import pair
        root = decode(self.root_bytes, canonical_required=True)
        return {'request_sha256': digest(self.request_bytes),
            'normalized_request': {'request': json.loads(self.request_bytes), 'target': json.loads(self.target_bytes)},
            'registered_inventory_ref': pair(self.registered_inventory_ref),
            'public_material_digest': root['public_material_digest'],
            'initial_declaration_digest': root['initial_declaration_digest']}


def prepare_registered_child_materials(core, *, request_ref, request_bytes, registered_inventory_ref, root_binding, control_root):
    from .public_materials import read_inventory
    from ..public_material_contracts import decode
    inventory = read_inventory(core, registered_inventory_ref)
    parent = parent_identity(core)
    request = decode(request_bytes)
    if type(request_ref) is not VersionRef or request_ref.entity_type != 'resource_version/v1' or _json(request) != request_bytes:
        raise TypeError('registered preparation needs exact original request bytes/ref')
    actual = core.get_version(request_ref.version_id)
    if (actual.object_type != request_ref.entity_type or actual.logical_id != request_ref.entity_id
            or actual.metadata.get('content_schema_ref') != REQUEST_SCHEMA
            or actual.metadata.get('task_ref') != parent['task_ref']
            or core.object_store.read_registered(actual) != request_bytes):
        raise RegistryConflict('registered preparation request is not the exact original resource')
    target = _target(parent, request['slot_id'], root_binding, control_root)
    root = inventory.root
    refs = {v['node_id']: v['resource_ref'] for v in root['node_refs']}
    from .publication import _resource_from_payload
    normalized = _resource_from_payload(refs[root['roots']['normalized_request']])
    raw = core.object_store.read_registered(core.get_version(normalized.resource_version_id))
    if _json(decode(raw)) != _json({'request': request, 'target': target}):
        raise RegistryConflict('registered inventory normalized request/target differs')
    return PreparedChildMaterials(request_ref, request_bytes, _json(parent), _json(target), registered_inventory_ref, inventory.root_bytes)


class _NativeBoundaryEvidence:
    """Opaque internal evidence, with NO production issuer in this slice.

    It is not a JSON DTO or caller flag. It records a transport observation,
    not PN authority. Python-private boundaries are not hostile-code sandboxes.
    """
    __slots__ = ('store', 'action', 'body', 'consumed')
    def __new__(cls, *args, **kwargs):
        raise ParentChildUnsupported('H7 native peer/receipt/reservation adapter is not wired')


_EVIDENCE = ContextVar('h7_native_boundary_evidence', default=None)


@contextmanager
def _native_boundary(evidence, store, action, body):
    if (type(evidence) is not _NativeBoundaryEvidence or evidence.store is not store
            or evidence.action != action or evidence.body != _json(body) or evidence.consumed):
        raise ParentChildUnsupported('H7 needs exact original native boundary evidence')
    evidence.consumed = True
    token = _EVIDENCE.set(evidence)
    try:
        yield
    finally:
        _EVIDENCE.reset(token)


def _require_evidence(store, action, body):
    value = _EVIDENCE.get()
    if (type(value) is not _NativeBoundaryEvidence or value.store is not store
            or value.action != action or value.body != _json(body) or not value.consumed):
        raise ParentChildUnsupported('H7 native evidence is absent; JSON is not transport authority')


def _prepared(row):
    return PreparedObject(row['object_type'], TypedId.parse(row['logical_id']),
        TypedId.parse(row['version_id']), row['size'], row['media_type'], row['schema_ref'],
        TypedId.parse(row['producer_invocation_id']) if row['producer_invocation_id'] else None,
        row['storage_locator'], json.loads(row['metadata_json']))


class _Snapshot:
    """View-aware evidence inside the existing transaction, no second reader."""
    def __init__(self, store, db, task_id, *, firing=None, proposal=()):
        self.store, self.db, self.task_id = store, db, task_id
        self.firing = firing
        self.object_store = descriptor_store(store)
        self.proposal = {str(item.version_id): item for item in proposal}

    def member(self, kind, identity):
        roots = self.db.execute('SELECT p.* FROM firing_temporary_members m '
            'JOIN firing_publications p ON p.firing_version_id=m.firing_version_id '
            'WHERE m.member_kind=? AND m.member_identity=?', (kind, identity)).fetchall()
        if any(row['state'] != 'PUBLISHED' and row['firing_version_id'] != self.firing for row in roots):
            raise RegistryConflict('H7 evidence is outside its exact firing view')
        return roots

    def row(self, ref):
        if type(ref) is not dict or set(ref) != {'entity_type', 'logical_id', 'version_id'}:
            raise RegistryConflict('H7 requires exact native refs, never transport paths')
        row = self.db.execute('SELECT o.*,e.task_id AS publication_task,e.payload_json AS publication,'
            'e.ordinal AS publication_ordinal,t.status AS tx_status FROM objects o '
            'JOIN events e ON e.event_id=o.published_event_id '
            'JOIN transactions t ON t.transaction_id=o.transaction_id '
            'WHERE o.object_type=? AND o.logical_id=? AND o.version_id=?',
            (ref['entity_type'], ref['logical_id'], ref['version_id'])).fetchone()
        if row is None or row['tx_status'] != 'committed' or row['publication_task'] != str(self.task_id):
            raise RegistryConflict('H7 exact object lacks committed local authority')
        self.member('object', row['version_id'])
        self.member('event', row['published_event_id'])
        publication = json.loads(row['publication'])
        if (publication.get('metadata') != json.loads(row['metadata_json'])
                or publication.get('logical_id') != row['logical_id']
                or publication.get('version_id') != row['version_id']):
            raise RegistryConflict('H7 registered publication differs from object')
        commits = self.db.execute("SELECT event_id FROM events WHERE transaction_id=? "
            "AND event_type='transaction_committed/v1'", (row['transaction_id'],)).fetchall()
        if len(commits) != 1:
            raise RegistryConflict('H7 object lacks unique committed transaction')
        self.member('event', commits[0]['event_id'])
        return row

    def obj(self, ref):
        item = self.proposal.get(ref['version_id'])
        if item is None:
            item = _prepared(self.row(ref))
        if _ref_payload(VersionRef(item.object_type, item.logical_id, item.version_id)) != ref:
            raise RegistryConflict('H7 object exact ref differs')
        if item.object_type == 'resource_version/v1':
            self.object_store.read_verified(item)
            return dict(item.metadata)
        return readable_descriptor(self.object_store, item, strict=True)

    def payload(self, ref):
        return self.object_store.read_verified(_prepared(self.row(ref)))

    def events(self, aggregate, types):
        result=[]
        for row in self.db.execute('SELECT * FROM events WHERE aggregate_id=? ORDER BY ordinal', (aggregate,)):
            if row['event_type'] in types:
                self.member('event', row['event_id']);result.append(row)
        return result

    def canonical_latest(self, kind):
        rows=self.db.execute('SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id '
            'WHERE o.object_type=? AND e.task_id=? AND '+_CANONICAL_EVENT_SQL+' ORDER BY e.ordinal DESC',
            (kind,str(self.task_id))).fetchall()
        if not rows or len({r['logical_id'] for r in rows}) != 1:
            raise RegistryConflict('H7 lacks unique current ' + kind)
        ref={'entity_type':kind,'logical_id':rows[0]['logical_id'],'version_id':rows[0]['version_id']}
        return ref,self.obj(ref)


def _parent_at(snap):
    binding=read_source_binding(snap.db,snap.store.catalog,snap.task_id)
    if binding is None:
        raise RegistryConflict('H7 requires an existing exact local source binding')
    body=json.loads(binding['binding_metadata_json'])
    run=snap.obj(body['native_run_ref'])
    return {'source_id':body['source_id'],'binding_ref':body['binding_ref'],
        'run_ref':body['native_run_ref'],'task_ref':body['task_ref'],'branch_ref':run['task_branch_ref']}


def parent_identity(core):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return _parent_at(_Snapshot(core.event_store,db,core.task_id))


def _execution_at(snap, locator, parent):
    """Original PN claim/ordinary Start/current authority at the same view cut."""
    db=snap.db
    firing=snap.obj(locator['firing_ref']); invocation=snap.obj(locator['invocation_ref'])
    lease=snap.obj(locator['lease_ref'])
    root=db.execute('SELECT * FROM firing_publications WHERE firing_version_id=?',
                    (locator['firing_ref']['version_id'],)).fetchone()
    if (root is None or root['state']!='PROVISIONAL'
            or root['invocation_version_id']!=locator['invocation_ref']['version_id']
            or snap.firing!=root['firing_version_id']):
        raise RegistryConflict('H7 requires its exact open provisional firing')
    pairs={'task_ref':parent['task_ref'],'task_branch_ref':parent['branch_ref'],
        'net_instance_ref':locator['net_ref'],'task_round_ref':locator['round_ref']}
    if (any(firing.get(k)!=v or invocation.get(k)!=v for k,v in pairs.items())
            or invocation.get('own_transition_firing_ref')!=locator['firing_ref']
            or invocation.get('operation_execution_lease_ref')!=locator['lease_ref']
            or lease.get('invocation_ref')!=locator['invocation_ref']):
        raise RegistryConflict('H7 cross-firing execution closure')
    epoch=int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
    native_row=snap.row(parent['run_ref'])
    original=db.execute('SELECT writer_fencing_epoch FROM events WHERE event_id=?',
                        (native_row['published_event_id'],)).fetchone()[0]
    if epoch!=original or epoch!=locator['original_writer_epoch'] or lease['writer_fencing_epoch']!=epoch:
        raise RegistryConflict('H7 fresh entry requires original native writer, not generation alone')
    current_ref,current=snap.canonical_latest('run_execution_authority/v1')
    admitted=snap.obj(locator['run_authority_ref'])
    if (current['run_ref']!=parent['run_ref'] or current['task_ref']!=parent['task_ref']
            or current.get('execution_generation',0)!=0 or locator['execution_generation']!=0
            or admitted.get('execution_generation',0)!=0 or current_ref['logical_id']!=locator['run_authority_ref']['logical_id']
            or current['status'] in ('terminal','stopped_by_owner')
            or admitted['run_ref']!=parent['run_ref']):
        raise RegistryConflict('H7 run authority is closed, foreign, or not fresh')
    relations=db.execute("SELECT * FROM relations WHERE relation_type='derived_from' "
        "AND json_extract(source_json,'$.version_id')=? AND json_extract(target_json,'$.version_id')=?",
        (locator['invocation_ref']['version_id'],locator['run_authority_ref']['version_id'])).fetchall()
    if len(relations)!=1:
        raise RegistryConflict('H7 invocation lacks exact admission run authority')
    snap.member('relation',relations[0]['relation_id'])
    starts=snap.events(locator['lease_ref']['logical_id'],('operation_execution_started/v1',))
    if len(starts)!=1 or starts[0]['event_id']!=locator['start_event_id']:
        raise RegistryConflict('H7 requires ordinary operation Start, not invocation-start')
    start=json.loads(starts[0]['payload_json'])
    from .operation_execution import revalidate_started_operation_at
    revalidate_started_operation_at(snap.store,db,task_id=snap.task_id,start_event_id=locator['start_event_id'])
    required={'invocation_ref':locator['invocation_ref'],
        'operation_execution_lease_ref':locator['lease_ref'],
        'transition_firing_ref':locator['firing_ref'],'claimed_input_refs':firing['claimed_input_refs'],
        'operation_binding_ref':invocation['operation_binding_ref'],
        'admission_writer_fencing_epoch':epoch}
    if any(start.get(k)!=v for k,v in required.items()) or starts[0]['producer_invocation_id']!=locator['invocation_ref']['logical_id']:
        raise RegistryConflict('H7 Start differs from exact admitted operation')
    terminal=snap.events(locator['invocation_ref']['logical_id'],('operation_terminal_ready/v1',))
    settled=snap.events(locator['firing_ref']['logical_id'],('transition_firing_settled/v1',))
    if terminal or settled:
        raise RegistryConflict('H7 execution is closed')
    adoption=db.execute("SELECT e.payload_json FROM events e WHERE e.task_id=? "
        "AND e.event_type='net_adopted/v1' AND "+_CANONICAL_EVENT_SQL+' ORDER BY e.ordinal DESC LIMIT 1',
        (str(snap.task_id),)).fetchone()
    if adoption is None or json.loads(adoption[0])['net_instance_ref']!=locator['net_ref']:
        raise RegistryConflict('H7 execution net is no longer adopted')
    checkpoint=snap.obj(current['latest_checkpoint_ref'])
    present={v['version_id'] for v in checkpoint['token_refs']}
    if not set(firing['claimed_input_version_ids'])<=present:
        raise RegistryConflict('H7 current marking no longer contains exact claims')
    return firing,invocation,start


def _locator(core, execution):
    from .operations import OperationExecutionAuthority
    if type(execution) is not OperationExecutionAuthority:
        raise TypeError('H7 needs a real ordinary operation execution authority')
    ctx=execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        rows=db.execute("SELECT json_extract(r.target_json,'$.version_id') FROM relations r JOIN objects o ON o.version_id=json_extract(r.target_json,'$.version_id') "
            "WHERE r.relation_type='derived_from' AND json_extract(r.source_json,'$.version_id')=? AND o.object_type='run_execution_authority/v1'",
            (str(ctx.invocation_ref.version_id),)).fetchall()
        if len(rows)!=1:raise RegistryConflict('H7 lacks exact admission authority relation')
        row=db.execute('SELECT * FROM objects WHERE version_id=?',(rows[0][0],)).fetchone()
        authority={'entity_type':row['object_type'],'logical_id':row['logical_id'],'version_id':row['version_id']}
    return {'firing_ref':_ref_payload(ctx.own_transition_firing_ref),'invocation_ref':_ref_payload(ctx.invocation_ref),
        'lease_ref':_ref_payload(ctx.operation_execution_lease_ref),'start_event_id':str(execution.start_event_id),
        'net_ref':_ref_payload(ctx.net_instance_ref),'round_ref':_ref_payload(ctx.task_round_ref),
        'run_authority_ref':authority,'execution_generation':0,'original_writer_epoch':core.writer_epoch}


def _record(core, kind, body, *, predecessors, evidence=None):
    ref=_version_from_payload(body['record_ref']); scope=body['execution']; producer=TypedId.parse(scope['invocation_ref']['logical_id'])
    key='h7:'+str(ref.version_id)
    tx=core.begin(idempotency_key=key,task_round_id=TypedId.parse(scope['round_ref']['logical_id']),
                  net_instance_id=TypedId.parse(scope['net_ref']['logical_id']))
    tx.expect_registry_ordinal(core.event_store.max_ordinal())
    tx.prewrite(object_type=kind,logical_id=ref.entity_id,version_id=ref.version_id,payload=_json(body),metadata=body,
        media_type='application/json',schema_ref='registry_v1/'+kind,producer_invocation_id=producer)
    edges=[('produced_by',scope['invocation_ref']), *[('derived_from',value) for value in predecessors]]
    for index,(relation,target) in enumerate(edges):
        tx.relate(TypedRelation(_stable_id('relation',key,index),relation,ref,_version_from_payload(target)),
                  producer_invocation_id=producer)
    tx.append(PendingEvent('parent_child_recorded/v1','authoritative','h7:'+str(ref.entity_id),str(ref.entity_id),kind,
        key,key,{'record_ref':body['record_ref'],'predecessor_refs':list(predecessors)},
        'registry_v1/parent_child_recorded/v1',producer_invocation_id=producer))
    if kind in INTENT_KINDS:
        tx.commit()
    else:
        with _native_boundary(evidence,core.event_store,kind,body):tx.commit()
    return ref


def register_child_intent(core, execution, prepared):
    """Register frozen preparation in the existing started firing; no launch."""
    if type(prepared) is not PreparedChildMaterials:
        raise TypeError('H7 intent requires immutable prepared materials')
    parent=json.loads(prepared.parent_bytes);request=json.loads(prepared.request_bytes)
    target=json.loads(prepared.target_bytes);scope=_locator(core,execution)
    ref=_ref(INTENT_V2,canonical_json(parent['run_ref']).decode(),request['slot_id'])
    envelope={'protocol':'rpnh/parent-bound-child-envelope/v2','intent_ref':_ref_payload(ref),
        'parent':parent,'slot_id':request['slot_id'],'target':target,
        'public_material_digest':prepared.materials['public_material_digest'],
        'registered_inventory_ref':prepared.materials['registered_inventory_ref']}
    body={'record_ref':_ref_payload(ref),'parent':parent,'slot_id':request['slot_id'],'execution':scope,
        'request_ref':_ref_payload(prepared.request_ref),'request':request,'materials':prepared.materials,
        'target':target,'envelope':envelope,'envelope_digest':digest(__import__('cpn.rpnh.public_material_contracts',fromlist=['canonical']).canonical(envelope))}
    return _record(core,INTENT_V2,body,predecessors=(body['request_ref'],scope['firing_ref'],scope['lease_ref']))


def _advance_record(core, kind, previous_ref, *, details, evidence):
    """Internal original-transport seam; no public evidence issuer is wired."""
    if kind not in PARENT_KINDS[1:]:raise TypeError('unsupported H7 phase')
    prior=core.get_version(previous_ref.version_id)
    prior_body=json.loads(core.object_store.read_verified(prior))
    base={key:prior_body[key] for key in ('parent','slot_id','execution')}
    ref=_ref(kind,canonical_json(base['parent']['run_ref']).decode(),base['slot_id'])
    body={'record_ref':_ref_payload(ref),**base,**details}
    if kind==PARENT_KINDS[1]:body['intent_ref']=_ref_payload(previous_ref)
    elif kind==PARENT_KINDS[2]:
        body['intent_ref']=prior_body['intent_ref'];body['dispatch_ref']=_ref_payload(previous_ref)
    else:
        intent=core.get_version(_version_from_payload(prior_body['intent_ref']).version_id).metadata
        body.update(intent_ref=prior_body['intent_ref'],dispatch_ref=prior_body['dispatch_ref'],
            worker_ref=_ref_payload(previous_ref),worker=prior_body['worker'],target=intent['target'],
            public_material_digest=intent['materials']['public_material_digest'],
            envelope_digest=intent['envelope_digest'],allowed_action='fresh_bound_bootstrap_once')
    return _record(core,kind,body,predecessors=(_ref_payload(previous_ref),),evidence=evidence)


def _validate_intent(snap, body, start):
    request_ref=body['request_ref']; request_meta=snap.obj(request_ref);raw=snap.payload(request_ref)
    if (request_meta.get('content_schema_ref')!=REQUEST_SCHEMA or _json(json.loads(raw))!=_json(body['request'])
            or digest(raw)!=body['materials']['request_sha256']
            or request_ref not in [{'entity_type':'resource_version/v1','logical_id':v['resource_id'],
                'version_id':v['resource_version_id']} for v in start['input_resource_refs']]):
        raise RegistryConflict('H7 request is not an exact claimed registered operation input')
    if body['slot_id']!=body['request']['slot_id']:
        raise RegistryConflict('H7 slot differs from registered request')
    target=body['target'];path=PurePosixPath(target['document_root'])
    if path.name!=target['transport_task_id'] or path.parent.name!='bound_tasks':
        raise RegistryConflict('H7 sealed document root differs')
    wanted=_target(body['parent'],body['slot_id'],target['root_binding'],str(path.parent.parent))
    materials=body['materials']
    if body['record_ref']['entity_type'] == INTENT_V2:
        from ..public_material_contracts import canonical, decode
        from .public_materials import read_inventory_at
        from ._candidate_read_context import _CandidateReadContext
        from .publication import _resource_from_payload
        branch_id = snap.db.execute("SELECT value FROM registry_meta WHERE key='branch_id'").fetchone()[0]
        from types import SimpleNamespace
        context = SimpleNamespace(event_store=snap.store, catalog=snap.store.catalog, object_store=snap.object_store, task_id=snap.task_id, branch_id=branch_id, db=snap.db)
        inventory = read_inventory_at(context, _resource_from_payload(materials['registered_inventory_ref']))
        root = inventory.root
        refs = {r['node_id']:r['resource_ref'] for r in root['node_refs']}
        normalized_ref = _resource_from_payload(refs[root['roots']['normalized_request']])
        normalized = decode(snap.payload(_ref_payload(normalized_ref.as_version_ref())))
        if (target != wanted or _json(normalized) != _json({'request':body['request'],'target':target})
                or _json(materials['normalized_request']) != _json(normalized)
                or root['public_material_digest'] != materials['public_material_digest']
                or root['initial_declaration_digest'] != materials['initial_declaration_digest']):
            raise RegistryConflict('H7 registered material target/digest differs')
        expected={'protocol':'rpnh/parent-bound-child-envelope/v2','intent_ref':body['record_ref'],
            'parent':body['parent'],'slot_id':body['slot_id'],'target':target,
            'registered_inventory_ref':materials['registered_inventory_ref'],
            'public_material_digest':materials['public_material_digest']}
        if body['envelope'] != expected or body['envelope_digest'] != digest(canonical(expected)):
            raise RegistryConflict('H7 registered envelope differs')
        return
    if body['record_ref']['entity_type'] != PARENT_KINDS[0]:
        raise RegistryConflict('unknown intent version')
    inventory=materials['inventory']
    if (target!=wanted or materials['normalized_request']!={'request':body['request'],'target':target}
            or [v['name'] for v in inventory]!=sorted(MATERIAL_SECTIONS)
            or digest(_json(inventory))!=materials['public_material_digest']
            or next(v for v in inventory if v['name']=='normalized_request')!={
                'name':'normalized_request','size':len(_json(materials['normalized_request'])),
                'sha256':digest(_json(materials['normalized_request']))}
            or next(v for v in inventory if v['name']=='lowered_net')['sha256']!=materials['initial_declaration_digest']):
        raise RegistryConflict('H7 target/normalized material/inventory digest differs')
    expected={'protocol':'rpnh/parent-bound-child-envelope/v1','intent_ref':body['record_ref'],
        'parent':body['parent'],'slot_id':body['slot_id'],'target':target,
        'public_material_digest':materials['public_material_digest']}
    if body['envelope']!=expected or body['envelope_digest']!=digest(_json(expected)):
        raise RegistryConflict('H7 envelope is not acyclic exact frozen material')


def validate_parent_child_commit(store,db,*,task_id,branch_id,task_round_id,net_instance_id,
        transaction_id,idempotency_key,writer_epoch,objects,events,relations,existing,extra_commands):
    """First AND replay guard under the original BEGIN IMMEDIATE snapshot."""
    previous=() if existing is None else tuple(db.execute('SELECT object_type,metadata_json FROM objects WHERE transaction_id=?',
                                                         (existing['transaction_id'],)))
    def protected(kind,body):return kind in KINDS or kind=='resource_version/v1' and body.get('content_schema_ref')==CAPABILITY_SCHEMA
    selected=[o for o in objects if protected(o.object_type,o.metadata)]
    intercepted=bool(selected or str.startswith(idempotency_key,'h7:')
        or any(protected(r['object_type'],json.loads(r['metadata_json'])) for r in previous)
        or any(o.object_type=='native_run_identity/v1' and BOUND_PROTOCOL in o.metadata.get('protocol_versions',()) for o in objects)
        or any(e.event_type=='parent_child_recorded/v1' or e.event_type=='object_version_published/v1'
            and protected(e.payload.get('object_type'),e.payload.get('metadata',{})) for e in events))
    from .parent_bound import validate_bound_commit
    validate_bound_commit(store,db,task_id=task_id,writer_epoch=writer_epoch,objects=objects,events=events,
        relations=relations,existing=existing,transaction_id=transaction_id)
    # Native observation/completion is outside this slice; do not release a
    # parent slot via an unrelated generic Success before that adapter exists.
    for event in events:
        if event.event_type == 'transition_firing_settled/v1':
            firing_ref=event.payload['transition_firing_ref']
            check=_Snapshot(store,db,task_id,firing=firing_ref['version_id'],proposal=objects)
            firing=check.obj(firing_ref)
            binding=check.obj(firing['operation_binding_ref'])
            spec=check.obj(binding['operation_spec_ref'])
            if spec.get('executor_key')==NATIVE_LAUNCH_EXECUTOR:
                raise ParentChildUnsupported('H7 native child observation/completion adapter is not wired')
    if not intercepted:return
    if not selected or any(o.object_type not in ALL_PARENT_KINDS for o in selected):
        # Child producer validation is a separate native bootstrap/origin phase.
        from .parent_bound import validate_bound_publication
        return validate_bound_publication(store,db,task_id=task_id,writer_epoch=writer_epoch,objects=objects,
            events=events,relations=relations,transaction_id=transaction_id,idempotency_key=idempotency_key,existing=existing)
    if len(objects)!=1 or len(selected)!=1 or extra_commands:
        raise RegistryConflict('H7 parent record must be one isolated causal transaction')
    item=selected[0]
    if item.object_type == PARENT_KINDS[0]:
        raise ParentChildUnsupported('intent/v1 is historical diagnostic evidence only')
    if item.object_type != INTENT_V2:
        intent_ref = item.metadata.get('intent_ref')
        if not intent_ref or intent_ref['entity_type'] != INTENT_V2:
            raise ParentChildUnsupported('legacy intent cannot create fresh dispatch')
        _require_evidence(store, item.object_type, dict(item.metadata))
    _validate_parent_record_transaction(store, db, task_id=task_id,
        task_round_id=task_round_id, net_instance_id=net_instance_id,
        transaction_id=transaction_id, idempotency_key=idempotency_key,
        objects=objects, events=events, relations=relations, existing=existing)


def _validate_parent_record_transaction(store, db, *, task_id, task_round_id,
        net_instance_id, transaction_id, idempotency_key, objects, events,
        relations, existing):
    """Shared mechanical closure only; never issues native boundary evidence.

    The live commit caller separately requires its original native boundary.
    The history caller supplies verified original rows at a committed cut.
    """
    selected=list(objects)
    item=selected[0];kind=item.object_type
    from .parent_bound import _bound_signal
    if _bound_signal(db):raise ParentChildUnsupported('H7a bound child cannot recursively launch another child')
    body=readable_descriptor(descriptor_store(store),item,strict=True)
    producer=body['execution']['invocation_ref']['logical_id'];scope=body['execution']
    snap=_Snapshot(store,db,task_id,firing=scope['firing_ref']['version_id'])
    if (body['record_ref']!=_ref_payload(VersionRef(kind,item.logical_id,item.version_id))
            or item.producer_invocation_id!=TypedId.parse(producer)
            or body['parent']!=_parent_at(snap) or task_round_id!=TypedId.parse(scope['round_ref']['logical_id'])
            or net_instance_id!=TypedId.parse(scope['net_ref']['logical_id'])
            or idempotency_key!='h7:'+str(item.version_id)):
        raise RegistryConflict('H7 record is outside original native identity/producer scope')
    firing,invocation,start=_execution_at(snap,scope,body['parent'])
    expected_ref=_ref(kind,canonical_json(body['parent']['run_ref']).decode(),body['slot_id'])
    if body['record_ref']!=_ref_payload(expected_ref):raise RegistryConflict('H7 whole-run slot key differs')
    # A single static slot for the complete native run, across all provisional/history roots.
    for row in db.execute('SELECT * FROM objects WHERE object_type IN ('+','.join('?' for _ in ALL_PARENT_KINDS)+')',ALL_PARENT_KINDS):
        prior=json.loads(row['metadata_json'])
        if (kind in INTENT_KINDS and row['object_type'] in INTENT_KINDS and row['version_id'] != str(item.version_id)
                or prior['parent']!=body['parent'] or prior['slot_id']!=body['slot_id']
                or prior['execution']!=scope):
            raise RegistryConflict('H7 whole-run slot is already used by another material/execution/slot')
    if kind in INTENT_KINDS:
        _validate_intent(snap,body,start)
        _validate_launcher_pn(snap,firing,start,body["request"])
        predecessors=(body['request_ref'],scope['firing_ref'],scope['lease_ref'])
    else:
        intent_for_pn=snap.obj(body['intent_ref'])
        _validate_launcher_pn(snap,firing,start,intent_for_pn['request'])
        field={PARENT_KINDS[1]:'intent_ref',PARENT_KINDS[2]:'dispatch_ref',PARENT_KINDS[3]:'worker_ref'}[kind]
        prior=snap.obj(body[field]);predecessors=(body[field],)
        if any(prior[k]!=body[k] for k in ('parent','slot_id','execution')):
            raise RegistryConflict('H7 predecessor comes from another execution')
        if kind==PARENT_KINDS[2] and body['intent_ref']!=prior['intent_ref']:
            raise RegistryConflict('H7 worker registration differs from exact dispatch')
        if kind==PARENT_KINDS[3]:
            intent=snap.obj(body['intent_ref']);dispatch=snap.obj(body['dispatch_ref'])
            if (prior['intent_ref']!=body['intent_ref'] or prior['dispatch_ref']!=body['dispatch_ref']
                    or prior['worker']!=body['worker'] or dispatch['intent_ref']!=body['intent_ref']
                    or body['target']!=intent['target'] or body['envelope_digest']!=intent['envelope_digest']
                    or body['public_material_digest']!=intent['materials']['public_material_digest']
                    or body['request_digest']!=digest(_json(bootstrap_request_material(intent,body['dispatch_ref'])))):
                raise RegistryConflict('H7 acceptance is not its exact intent/dispatch/worker/material closure')
    expected_edges=[('produced_by',scope['invocation_ref']),*[('derived_from',v) for v in predecessors]]
    if len(relations)!=len(expected_edges):raise RegistryConflict('H7 record causal edges incomplete')
    for index,(relation,(edge,target)) in enumerate(zip(relations,expected_edges)):
        if (relation.relation_id!=_stable_id('relation',idempotency_key,index) or relation.relation_type!=edge
                or _ref_payload(relation.source)!=body['record_ref'] or _ref_payload(relation.target)!=target
                or relation.strength!='strong' or relation.producer_invocation_id!=item.producer_invocation_id
                or relation.system_owned or relation.metadata):
            raise RegistryConflict('H7 record causal edge/producer differs')
    records=[e for e in events if e.event_type=='parent_child_recorded/v1']
    if (len(records)!=1 or records[0].payload!={'record_ref':body['record_ref'],'predecessor_refs':list(predecessors)}
            or records[0].producer_invocation_id!=item.producer_invocation_id
            or len(events)!=len(relations)+3
            or any(e.event_type not in ('parent_child_recorded/v1','object_version_published/v1',
                        'relation_published/v1','transaction_committed/v1') for e in events)):
        raise RegistryConflict('H7 record lacks exact same-transaction fact closure')
    record=records[0]
    if (record.stream_id!='h7:'+str(item.logical_id) or record.aggregate_id!=str(item.logical_id)
            or record.aggregate_type!=kind or record.idempotency_key!=idempotency_key
            or record.command_id!=idempotency_key or record.criticality!='authoritative'
            or record.payload_schema_ref!='registry_v1/parent_child_recorded/v1'
            or record.task_control or record.producer_principal!='framework'
            or record.causation_event_id is not None or record.parent_event_ids or record.occurred_at is not None):
        raise RegistryConflict('H7 record event identity/producer contract differs')
    commits=[e for e in events if e.event_type=='transaction_committed/v1']
    publications=[e for e in events if e.event_type=='object_version_published/v1']
    if (len(commits)!=1 or len(publications)!=1 or commits[0].producer_invocation_id is not None
            or commits[0].payload!={'object_count':1,'relation_count':len(relations),'fact_count':len(events)-1}
            or publications[0].producer_invocation_id!=item.producer_invocation_id
            or publications[0].payload.get('metadata')!=body
            or publications[0].payload.get('version_id')!=str(item.version_id)):
        raise RegistryConflict('H7 publication/commit envelope differs from its exact producer')
    prior=db.execute('SELECT transaction_id FROM objects WHERE version_id=?',(str(item.version_id),)).fetchone()
    if prior is not None and (existing is None or prior['transaction_id']!=existing['transaction_id']):
        raise RegistryConflict('H7 historical record cannot be reused as a new command')


NATIVE_LAUNCH_EXECUTOR='rpnh/parent_child_launch/v1'


def _validate_launcher_pn(snap,firing,start,request):
    """No host Boolean may supply the request/launch/one-slot PN prerequisites."""
    from ..executable_net import load_compiled_net
    net=snap.obj(firing['net_instance_ref']);rr=net['team_net_declaration_resource_ref']
    ref={'entity_type':'resource_version/v1','logical_id':rr['resource_id'],'version_id':rr['resource_version_id']}
    compiled=load_compiled_net(json.loads(snap.payload(ref)));pn=compiled.symbolic
    launchers=[op for op in pn.operations if op.executor==NATIVE_LAUNCH_EXECUTOR]
    if len(launchers)!=1 or launchers[0].name!=firing['transition_id']:
        raise RegistryConflict('H7a requires exactly one declared native launcher operation')
    op=launchers[0]
    if {o.name for o in op.outcomes}!={'complete','failed'}:
        raise RegistryConflict('H7 launcher has unsupported stopped/interrupted/unknown outcome')
    prefix=firing['transition_id'].rsplit('.',1)[0]
    capacity=prefix+'.h7capacity';launch=prefix+'.h7launch'
    places={p.name:p for p in pn.places}
    if (capacity not in places or launch not in places or places[capacity].capacity!=1
            or not places[capacity].reusable or places[capacity].token_kind=='resource_lease'
            or places[launch].token_kind!='resource_lease' or not places[launch].reusable):
        raise RegistryConflict('H7 launcher lacks single reusable capacity and exact launch lease')
    arcs=[a for a in pn.arcs if a.transition==op.name]
    if (len([a for a in arcs if a.place==capacity and a.direction=='input' and a.mode=='borrow' and a.weight==1])!=1
            or len([a for a in arcs if a.place==launch and a.direction=='input' and a.mode=='read' and a.weight==1])!=1
            or any(len([a for a in arcs if a.place==capacity and a.direction=='output' and a.mode=='return'
                        and a.weight==1 and a.outcome==outcome])!=1 for outcome in ('complete','failed'))):
        raise RegistryConflict('H7 launcher PN capacity/launch arcs differ')
    claims=[snap.obj(ref) for ref in firing['claimed_input_refs']]
    if any(sum(token['place']==place for token in claims)!=1 for place in (capacity,launch)):
        raise RegistryConflict('H7 exact firing claim lacks capacity or launch capability')
    delta=snap.obj(firing['claim_marking_delta_ref'])
    for token in claims:
        if token['place']==capacity and token['petri_token_ref'] not in delta['consumed_refs']:
            raise RegistryConflict('H7 capacity must stay claimed until original Success')
        if token['place']==launch and token['petri_token_ref'] in delta['consumed_refs']:
            raise RegistryConflict('H7 launch lease must use S1 reference semantics')

def bootstrap_request_material(intent,dispatch_ref):
    """Pure exact request material, with no peer identity or permission."""
    return {'protocol':'rpnh/parent-child-bootstrap/v1','parent':intent['parent'],
        'slot_id':intent['slot_id'],'intent_ref':intent['record_ref'],'dispatch_ref':dispatch_ref,
        'execution':intent['execution'],'target':intent['target'],
        'envelope_digest':intent['envelope_digest'],'public_material_digest':intent['materials']['public_material_digest']}
