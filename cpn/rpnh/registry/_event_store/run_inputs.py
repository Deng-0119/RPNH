"""External input admission in the original Registry write snapshot.

Waits and receipts are immutable Registry facts, not a second pending ledger.
All checks here use the caller's SQLite transaction and registered bytes.
"""
import base64
import json

from ..event_store import RegistryConflict, StaleWriterError
from ..run_inputs import WAIT, RECEIPT, InputCommand, reference, now, timestamp
from ..user_collaboration import SESSION, check_session
from ..strict_contracts import ref_payload
from ..schema_catalog import canonical_json
from .user_collaboration import exact, facts
from .collaboration_descriptors import descriptor_store, exact_prepared, readable_descriptor


def writer(db, epoch):
    current = int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
    if epoch != current:
        raise StaleWriterError('run input writer is stale')


def session(store, db, task, action):
    if type(action) is not InputCommand or action.store is not store:
        raise RegistryConflict('input requires a native command for this store')
    check_session(store, action.session)
    doc = exact(store, db, task, ref_payload(action.session.ref), SESSION)
    if doc['data']['actor'] != action.session.actor:
        raise RegistryConflict('input session actor differs')
    exact(store, db, task, doc['data']['target_principal_ref'], 'principal/v1')
    return doc


def boundary(store, db, task):
    runs = facts(store, db, task, 'run_execution_authority/v1')
    if not runs or len({r['run_execution_authority_ref']['logical_id'] for r in runs}) != 1:
        raise RegistryConflict('input needs one original run authority')
    current = runs[-1]
    if current['status'] not in {'ready', 'running'}:
        raise RegistryConflict('run is not available for external input')
    cp = exact(store, db, task, current['latest_checkpoint_ref'], 'marking_checkpoint/v1')
    # An admitted operation must retain its own completion/unknown recovery.
    if db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' LIMIT 1").fetchone():
        raise RegistryConflict('input requires a quiescent run with no active firing')
    from ..event_store import verified_adoption_head, verified_checkpoint_head
    adopted = verified_adoption_head(store, store.catalog, task, _db=db)
    head = verified_checkpoint_head(store, store.catalog, task, adopted, _db=db)
    if ref_payload(head) != cp['marking_checkpoint_ref']:
        raise RegistryConflict('input checkpoint differs from current marking head')
    if ref_payload(adopted) != cp['net_instance_ref']:
        raise RegistryConflict('input net is no longer adopted')
    net = exact(store, db, task, cp['net_instance_ref'], 'net_instance/v1')
    source = net['team_net_declaration_resource_ref']
    source_ref = dict(entity_type='resource_version/v1', logical_id=source['resource_id'], version_id=source['resource_version_id'])
    if source_ref != current['declaration_ref']:
        raise RegistryConflict('input source differs from original run')
    prepared = exact_prepared(db, descriptor_store(store), task, source_ref)
    from ...executable_net import load_compiled_net
    compiled = load_compiled_net(json.loads(descriptor_store(store).read_registered(prepared)))
    return current, cp, net, compiled


def status(store, db, task, wait):
    successors = [w for w in facts(store, db, task, WAIT)
                  if w['spec']['supersedes_wait_ref'] == wait['self_ref']]
    receipts = [r for r in facts(store, db, task, RECEIPT) if r['wait_ref'] == wait['self_ref']]
    if len(successors) > 1 or len(receipts) > 2:
        raise RegistryConflict('input wait has competing canonical decisions')
    if len(receipts) == 2 and (receipts[0]['disposition'] != 'accepted'
            or receipts[1]['disposition'] != 'expired'
            or receipts[1].get('supersedes_receipt_ref') != receipts[0]['self_ref']):
        raise RegistryConflict('input wait has no exact acceptance-to-expiry chain')
    if successors:
        return 'superseded', None
    if receipts:
        return receipts[-1]['disposition'], receipts[-1]
    return 'waiting', None


def owner_session(store, db, task, doc):
    if not doc['data']['owner'] or doc['data']['origin_task_ref'] != doc['owner_task_ref']:
        raise RegistryConflict('opening or expiring input needs original owner session')
    roots = facts(store, db, task, 'team_design_root/v1')
    if not roots or doc['data']['target_principal_ref'] != roots[-1]['owner_principal_ref']:
        raise RegistryConflict('input owner principal is not current')


def live_anchor(store, db, task, spec, cp):
    ref = spec['anchor_token_ref']
    if ref not in cp['token_refs']:
        raise RegistryConflict('input anchor is not in current marking')
    token = exact(store, db, task, ref, 'petri_token/v1')
    if (token['consumed_by'] is not None or token['epoch'] != cp['epoch']
            or token['net_instance_ref'] != cp['net_instance_ref']):
        raise RegistryConflict('input anchor is no longer live')
    resource = token['resource_ref']
    if resource != {'resource_id':spec['binding_ref']['logical_id'], 'resource_version_id':spec['binding_ref']['version_id']}:
        raise RegistryConflict('input binding must be exact anchor resource')
    exact_prepared(db, descriptor_store(store), task, spec['binding_ref'])
    return token


def input_port(compiled, spec, anchor):
    ports = [p for p in compiled.ports if p.name == spec['input_port']]
    if len(ports) != 1:
        raise RegistryConflict('input port is absent')
    port = ports[0]
    place = next(p for p in compiled.symbolic.places if p.name == port.place)
    arcs = compiled.symbolic.arcs
    consumes = lambda place: any(a.place == place and a.transition == spec['transition_id']
                                and a.direction == 'input' and a.mode == 'consume' and a.weight == 1 for a in arcs)
    if (port.direction != 'input' or port.public or port.minimum != 1 or port.maximum != 1
            or place.initial_tokens or port.place == anchor['place']
            or any(a.place == port.place and a.direction == 'output' for a in arcs)
            or not consumes(port.place) or not consumes(anchor['place'])):
        raise RegistryConflict('input requires a private required-one producer-free port and consumed anchor')
    return port


def check_open(store, db, task, action, current, cp, net, compiled=None):
    doc = session(store, db, task, action)
    owner_session(store, db, task, doc)
    spec = action.material()['envelope']['spec']
    if compiled is None:
        _, _, _, compiled = boundary(store, db, task)
    anchor = live_anchor(store, db, task, spec, cp)
    port = input_port(compiled, spec, anchor)
    for ref in spec['allowed_principal_refs']:
        exact(store, db, task, ref, 'principal/v1')
    if spec['expires_at_utc'] is not None and timestamp(spec['expires_at_utc']) <= now():
        raise RegistryConflict('input wait deadline already elapsed')
    for ref in cp['token_refs']:
        token = exact(store, db, task, ref, 'petri_token/v1')
        if token['place'] == port.place and token['consumed_by'] is None and token['epoch'] == cp['epoch']:
            raise RegistryConflict('input port already contains a live token')
    same = [w for w in facts(store, db, task, WAIT) if w['net_ref'] == cp['net_instance_ref']
            and w['spec']['anchor_token_ref'] == spec['anchor_token_ref']]
    predecessor = spec['supersedes_wait_ref']
    if same:
        latest = same[-1]
        if predecessor != latest['self_ref'] or status(store, db, task, latest)[0] != 'waiting':
            raise RegistryConflict('same anchor requires exact unaccepted wait supersession')
    elif predecessor is not None:
        raise RegistryConflict('superseded wait belongs to another anchor')
    if spec['expiry_input'] is not None:
        validate_input(compiled, port, spec['expiry_input'])


def validate_input(compiled, port, declaration):
    if declaration['schema_id'] != port.schema:
        raise RegistryConflict('input schema differs from exact port')
    from jsonschema import Draft7Validator
    value = json.loads(base64.b64decode(declaration['payload'], validate=True))
    Draft7Validator(compiled.registrations['schema'][port.schema]['schema']).validate(value)


def check_accept(store, db, task, action, current, cp, net, compiled):
    actor = session(store, db, task, action)
    body = action.material()['envelope']
    wait = exact(store, db, task, body['wait_ref'], WAIT)
    state, previous_receipt = status(store, db, task, wait)
    replacing = body['operation'] == 'expire' and state == 'accepted'
    if (body['expected_checkpoint_ref'] != cp['marking_checkpoint_ref']
            or wait['run_ref'] != current['run_ref'] or wait['task_ref'] != current['task_ref']
            or wait['net_ref'] != cp['net_instance_ref'] or wait['source_ref'] != current['declaration_ref']
            or (state != 'waiting' and not replacing)):
        raise RegistryConflict('input target/checkpoint/wait is stale')
    anchor = live_anchor(store, db, task, wait['spec'], cp)
    port = input_port(compiled, wait['spec'], anchor)
    replaced_token = None if not replacing else previous_receipt['token_ref']
    if replacing and replaced_token not in cp['token_refs']:
        raise RegistryConflict('accepted input is no longer replaceable')
    occupancy = 0
    for ref in cp['token_refs']:
        token = exact(store, db, task, ref, 'petri_token/v1')
        if ref == replaced_token:
            if token['consumed_by'] is not None:
                raise RegistryConflict('accepted input was already consumed')
            continue
        if token['place'] == port.place and token['consumed_by'] is None and token['epoch'] == cp['epoch']:
            occupancy += 1
    place = next(p for p in compiled.symbolic.places if p.name == port.place)
    if place.capacity is not None and occupancy + 1 > place.capacity:
        raise RegistryConflict('input destination capacity would be exceeded')
    deadline = wait['spec']['expires_at_utc']
    if body['operation'] == 'expire':
        owner_session(store, db, task, actor)
        if actor['self_ref'] != wait['owner_session_ref'] or deadline is None or timestamp(deadline) > now():
            raise RegistryConflict('input wait is not eligible for owner expiry')
        declaration = wait['spec']['expiry_input']
        if declaration is None:
            raise RegistryConflict('no explicit expiry input configured')
    else:
        if (actor['data']['target_principal_ref'] not in wait['spec']['allowed_principal_refs']
                or body['binding_ref'] != wait['spec']['binding_ref']
                or (deadline is not None and timestamp(deadline) <= now())):
            raise RegistryConflict('input actor/binding/deadline rejected')
        declaration = body['declaration']
    validate_input(compiled, port, declaration)
    return wait, declaration, actor['data']['target_principal_ref']


def check_replay(store, db, task, existing, action):
    recorded = json.loads(existing['command_json']).get('run_input')
    if recorded is None and action is None:
        return
    if (existing['status'] != 'committed' or action is None
            or recorded != action.material()):
        raise RegistryConflict('input replay changed actor or exact body')
    session(store, db, task, action)


def resumable(store, db, task):
    """Prove live input waits in the current marking, without a writer.

    An unrelated firing may have advanced the checkpoint since a wait opened.
    Its settlement need not repeat input provenance; the original immutable
    wait, live anchor and effective receipt remain the recovery witnesses.
    """
    current, cp, net, compiled = boundary(store, db, task)
    if current['status'] != 'running':
        return None
    found = False
    for wait in facts(store, db, task, WAIT):
        if (wait['run_ref'] != current['run_ref'] or wait['task_ref'] != current['task_ref']
                or wait['net_ref'] != cp['net_instance_ref'] or wait['source_ref'] != current['declaration_ref']
                or wait['spec']['anchor_token_ref'] not in cp['token_refs']):
            continue
        state, receipt = status(store, db, task, wait)
        if state == 'superseded':
            continue
        anchor = exact(store, db, task, wait['spec']['anchor_token_ref'], 'petri_token/v1')
        if anchor['consumed_by'] is not None or anchor['epoch'] != cp['epoch']:
            continue
        anchor = live_anchor(store, db, task, wait['spec'], cp)
        port = input_port(compiled, wait['spec'], anchor)
        if state in {'accepted','expired'}:
            if receipt['token_ref'] not in cp['token_refs']:
                raise RegistryConflict('accepted checkpoint lacks exact persistent receipt')
            token=exact(store,db,task,receipt['token_ref'],'petri_token/v1')
            if (token.get('input_receipt_ref') != receipt['self_ref'] or token['consumed_by'] is not None
                    or token['epoch'] != cp['epoch'] or token['net_instance_ref'] != wait['net_ref']
                    or token['place'] != port.place
                    or token['resource_ref'] != {'resource_id':receipt['resource_ref']['logical_id'],
                                                'resource_version_id':receipt['resource_ref']['version_id']}):
                raise RegistryConflict('accepted input is not available for continuation')
        elif state != 'waiting':
            raise RegistryConflict('input wait has no recoverable disposition')
        found = True
    return cp['marking_checkpoint_ref'] if found else None


def validate_commit(store, db, *, task_id, idempotency_key, objects, events, action, existing):
    protected = [o for o in objects if o.object_type in (WAIT, RECEIPT)
                 or (o.object_type in ('petri_token/v1','marking_checkpoint/v1')
                     and ('input_receipt_ref' in o.metadata or 'input_wait_ref' in o.metadata))]
    if existing is not None:
        check_replay(store, db, task_id, existing, action)
        return
    if protected and action is None:
        raise RegistryConflict('external input facts require native HOST command')
    if action is not None:
        current, cp, net, compiled = boundary(store, db, task_id)
        actor = session(store, db, task_id, action)
        body = action.material()['envelope']
        docs = {}
        for obj in objects:
            if obj.object_type in (WAIT, RECEIPT, 'petri_token/v1','marking_checkpoint/v1','run_execution_authority/v1'):
                if obj.object_type in docs or obj.producer_invocation_id is not None:
                    raise RegistryConflict('input command has repeated or invocation-owned facts')
                docs[obj.object_type] = readable_descriptor(descriptor_store(store), obj, strict=True)
        opening = body['operation'] == 'open'
        expected_types = {WAIT,'marking_checkpoint/v1','run_execution_authority/v1'} if opening else {RECEIPT,'petri_token/v1','marking_checkpoint/v1','run_execution_authority/v1'}
        if set(docs) != expected_types:
            raise RegistryConflict('input command lacks its atomic fact closure')
        successor = docs['marking_checkpoint/v1']
        if opening:
            check_open(store, db, task_id, action, current, cp, net, compiled)
            wait = docs[WAIT]
            expected = dict(self_ref=ref_payload(reference(task_id,WAIT,idempotency_key)),command_id=idempotency_key,
                owner_session_ref=actor['self_ref'],task_ref=current['task_ref'],run_ref=current['run_ref'],
                net_ref=cp['net_instance_ref'],source_ref=current['declaration_ref'],
                predecessor_checkpoint_ref=cp['marking_checkpoint_ref'],checkpoint_ref=successor['marking_checkpoint_ref'],spec=body['spec'])
            if wait != expected:
                raise RegistryConflict('input wait differs from complete native command')
            token_ref = receipt_ref = None
        else:
            wait, declaration, principal = check_accept(store,db,task_id,action,current,cp,net,compiled)
            previous_state, previous_receipt = status(store,db,task_id,wait)
            replacing = body['operation']=='expire' and previous_state=='accepted'
            receipt = docs[RECEIPT]
            token = docs['petri_token/v1']
            token_ref, receipt_ref = token['petri_token_ref'], receipt['self_ref']
            expected = dict(self_ref=ref_payload(reference(task_id,RECEIPT,idempotency_key)),command_id=idempotency_key,
                wait_ref=wait['self_ref'],session_ref=actor['self_ref'],principal_ref=principal,
                disposition='expired' if body['operation']=='expire' else 'accepted',resource_ref=receipt['resource_ref'],
                token_ref=token_ref,checkpoint_ref=successor['marking_checkpoint_ref'],
                predecessor_checkpoint_ref=cp['marking_checkpoint_ref'],binding_ref=wait['spec']['binding_ref'],declaration=declaration,
                supersedes_receipt_ref=previous_receipt['self_ref'] if replacing else None)
            if receipt != expected:
                raise RegistryConflict('input receipt differs from complete native command')
            resources = [o for o in objects if o.object_type=='resource_version/v1' and str(o.version_id)==receipt['resource_ref']['version_id']]
            if len(resources)!=1:
                raise RegistryConflict('input resource is not atomically published')
            resource = resources[0]
            if (descriptor_store(store).read_registered(resource)!=base64.b64decode(declaration['payload'])
                    or resource.metadata['content_schema_ref']!=declaration['schema_id']
                    or resource.metadata['task_ref']!=current['task_ref']):
                raise RegistryConflict('input resource differs from submitted exact bytes/schema/task')
            port = next(p for p in compiled.ports if p.name==wait['spec']['input_port'])
            expected_token = dict(petri_token_ref=token_ref,net_instance_ref=cp['net_instance_ref'],token_id=cp['next_token_id'],
                place=port.place,epoch=cp['epoch'],producer=None,consumer=None,
                resource_ref={'resource_id':receipt['resource_ref']['logical_id'],'resource_version_id':receipt['resource_ref']['version_id']},
                work_resource_ref=None,kind=None,consumed_by=None,override_warning=None,verdict=None,continuation=None,
                lease_identity_ref=None,lease_claims=[],input_receipt_ref=receipt_ref)
            if token != expected_token:
                raise RegistryConflict('input token differs from exact receipt/port')
        expected_cp = {k:cp[k] for k in ('net_instance_ref','team_design_root_ref','epoch','next_token_id','attempts','token_refs','workspace_revision_refs')}
        expected_cp.update(marking_checkpoint_ref=successor['marking_checkpoint_ref'],settled=True,
            previous_checkpoint_ref=cp['marking_checkpoint_ref'],settlement_delta_ref=None,transition_firing_refs=[],
            input_wait_ref=wait['self_ref'],input_receipt_ref=receipt_ref)
        if token_ref is not None:
            retained = cp['token_refs']
            if replacing:
                retained = [r for r in retained if r != previous_receipt['token_ref']]
            expected_cp['token_refs']=sorted([*retained,token_ref],key=canonical_json)
            expected_cp['next_token_id']+=1
        if successor != expected_cp:
            raise RegistryConflict('input checkpoint changes unrelated marking authority')
        run = docs['run_execution_authority/v1']
        expected_run = dict(current,run_execution_authority_ref=run['run_execution_authority_ref'],latest_checkpoint_ref=successor['marking_checkpoint_ref'])
        if run != expected_run or run['run_execution_authority_ref']['logical_id']!=current['run_execution_authority_ref']['logical_id']:
            raise RegistryConflict('input run successor differs from original authority')
        checkpoints=[e for e in events if e.event_type=='marking_checkpoint_committed/v1']
        if len(checkpoints)!=1 or checkpoints[0].payload.get('checkpoint_ref')!=successor['marking_checkpoint_ref']:
            raise RegistryConflict('input command lacks unique checkpoint publication')
    firing_gate(store, db, task_id, objects)


def firing_gate(store, db, task, objects):
    firings=[o for o in objects if o.object_type=='transition_firing/v1']
    if not firings:
        return
    waits=facts(store,db,task,WAIT)
    if not waits:
        return
    for obj in firings:
        firing=obj.metadata
        claimed=firing['claimed_input_refs']
        for wait in waits:
            if wait['net_ref']!=firing['net_instance_ref']:
                continue
            if wait['spec']['anchor_token_ref'] not in claimed:
                continue
            state, receipt=status(store,db,task,wait)
            if state=='superseded':
                continue
            if receipt is None or receipt['token_ref'] not in claimed or wait['spec']['anchor_token_ref'] not in claimed:
                raise RegistryConflict('firing lacks exact accepted input and original anchor')
            if wait['spec']['transition_id']!=firing['transition_id']:
                raise RegistryConflict('input cannot authorize another transition')
            token=exact(store,db,task,receipt['token_ref'],'petri_token/v1')
            if token.get('input_receipt_ref')!=receipt['self_ref'] or receipt['binding_ref']!=wait['spec']['binding_ref']:
                raise RegistryConflict('input token/receipt/binding closure differs')
            actor=exact(store,db,task,receipt['session_ref'],SESSION)
            if receipt['disposition']=='accepted':
                if actor['data']['target_principal_ref'] not in wait['spec']['allowed_principal_refs']:
                    raise RegistryConflict('accepted input principal is no longer eligible')
                deadline=wait['spec']['expires_at_utc']
                if deadline is not None and timestamp(deadline) <= now():
                    raise RegistryConflict('accepted input expired before firing admission')
            elif actor['self_ref']!=wait['owner_session_ref']:
                raise RegistryConflict('expiry receipt owner differs')
        for ref in claimed:
            token=exact(store,db,task,ref,'petri_token/v1')
            if token.get('input_receipt_ref') is not None:
                receipt=exact(store,db,task,token['input_receipt_ref'],RECEIPT)
                wait=exact(store,db,task,receipt['wait_ref'],WAIT)
                if (receipt['token_ref']!=ref or wait['spec']['transition_id']!=firing['transition_id']
                        or wait['spec']['anchor_token_ref'] not in claimed
                        or status(store,db,task,wait)[1] != receipt):
                    raise RegistryConflict('claimed external input does not belong to this firing')
            else:
                # A reused external-input place remains protected even when a
                # later occurrence has a different anchor and wait generation.
                for wait in waits:
                    if wait['net_ref'] != firing['net_instance_ref']:
                        continue
                    source=exact_prepared(db,descriptor_store(store),task,wait['source_ref'])
                    from ...executable_net import load_compiled_net
                    compiled=load_compiled_net(json.loads(descriptor_store(store).read_registered(source)))
                    port=next(p for p in compiled.ports if p.name==wait['spec']['input_port'])
                    if token['place']==port.place:
                        raise RegistryConflict('external input place requires exact accepted receipt')
