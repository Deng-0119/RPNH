"""Offline original Registry tests; full plain author material, no providers."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

import test_collaboration_plain_merge as plain
from test_collaboration_branches import _stage_branch, _direct_batch
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PublishResource, PrivateSystemOrigin
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.user_collaboration import TYPES, PROJECT, RECEIPT, command_key
from cpn.rpnh.registry._event_store.branch_publication import branch_command_key


def _successor(first, target, task_id, command):
    from cpn.rpnh.collaboration import SourceQualifiedVersionRef
    from cpn.rpnh.registry._event_store.branch_publication import branch_version_id
    return replace(first, branch_ref=SourceQualifiedVersionRef(first.branch_ref.source_id,
        VersionRef(first.branch_ref.ref.entity_type, first.branch_ref.ref.entity_id, branch_version_id(task_id, command))),
        head_revision_ref=target.revision_ref, predecessor_branch_ref=first.branch_ref,
        expected_head_revision_ref=first.head_revision_ref, expected_stream_head=first.sequence,
        sequence=first.sequence+1, command_id=command)

_plain_inventory = plain.plain_merge_schema_data


def fixture_inventory():
    s, t, p = user_collaboration_schema_data()
    other, extra, paths = _plain_inventory()
    s.update(other); p.update(paths)
    return s, tuple({d.name: d for d in (*t, *extra)}.values()), p


def catalog():
    s, t, p = user_collaboration_schema_data()
    return SchemaCatalog(schemas=s, types=t, schema_paths=p)


def personal(path, source):
    core = _RegistryCore(path, create=True, catalog=catalog())
    identity = _bootstrap_identity(core, NativeBootstrapManifest(('user-collaboration-fixture/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id=source, command_id='source')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    data = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': source}
    core.publish_bytes(object_type='principal/v1', logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(data), metadata=data, media_type='application/json', schema_ref='registry_v1/principal/v1',
        idempotency_key='principal')
    return core, principal


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(plain, 'plain_merge_schema_data', fixture_inventory)
    fixture = plain.fixture.__wrapped__(tmp_path, monkeypatch)
    core, gateway, _, author, _, base, _ = fixture
    host = UserCollaborationHost(core, gateway)
    principal = author.producer_principal_ref.ref if hasattr(author, 'producer_principal_ref') else None
    row = core.event_store.object_rows_by_type('principal/v1')[0]
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version')) if row is None else _version_from_payload({
        'entity_type': 'principal/v1', 'logical_id': row['logical_id'], 'version_id': row['version_id']})
    a = host.create_session(origin_core=core, principal_ref=principal, command_id='A')
    bcore, bp = personal(tmp_path/'B', 'B-origin')
    ccore, cp = personal(tmp_path/'C', 'C-origin')
    b = host.create_session(origin_core=bcore, principal_ref=bp, command_id='B')
    c = host.create_session(origin_core=ccore, principal_ref=cp, command_id='C')
    branch = gateway.create_author_branch(head_revision_ref=base.revision.revision_ref, command_id='official')
    project = host.protect(a, branch_ref=branch.branch_ref, command_id='protect')
    return dict(fixture=fixture, core=core, gateway=gateway, host=host, a=a,b=b,c=c,
        bcore=bcore, bp=bp, principal=principal, branch=branch, project=project, base=base)


def join(w, who='b', role='propose'):
    h = w['host']; session = w[who]
    invitation = h.invite(w['a'], w['project'], subject_session_ref=session.ref, role=role, command_id='invite-'+who)
    return h.join(session, invitation, command_id='join-'+who)


def proposal(w, who='b', key='P1', *, branch=None, parent=None, document=None):
    branch = branch or w['branch']
    doc = deepcopy(w['base'].module.to_dict() if document is None else document)
    if document is None:
        doc['components'][-1]['operations'][0]['config'] = {'value': key}
    candidate = plain.publish(w['fixture'], doc, 'candidate-'+key, parent=parent)
    h = w['host']; core = w['core']; g = w['gateway']
    text = _publish_private_system(core, g._task_ref, PublishResource(origin=PrivateSystemOrigin(g._bootstrap_ref),
        payload=canonical_json(doc), media_type='text/plain', content_schema_ref=None, summary='Explicit synthetic proposal',
        lifetime_ref=g._bootstrap_ref, idempotency_key='TEXT-'+key))
    args = dict(source_text_ref=text, origin_text={'source_id': 'plain-source', 'ref': {
        'entity_type': 'resource_version/v1', 'logical_id': str(text.resource_id), 'version_id': str(text.resource_version_id)}},
        execution_refs={'producer_ref': g._bootstrap_ref}, payload=doc, base_revision_ref=branch.head_revision_ref,
        expected_branch_ref=branch.branch_ref, expected_head_ref=branch.head_revision_ref,
        candidate_revision_ref=candidate.revision.revision_ref, command_id=key, synthetic_fixture=True)
    return h.submit_proposal(w[who], w['project'], **args), candidate, args


def decision(w, p, candidate, key='D1'):
    h=w['host']; branch=w['branch']
    review=h.review(w['a'], p, disposition='approve', command_id='review-'+key)
    d=h.decide(w['a'], w['project'], operation='advance', proposal_refs=(p,), review_refs=(review,),
        result_revision_ref=candidate.revision.revision_ref, expected_branch_ref=branch.branch_ref,
        expected_head_ref=branch.head_revision_ref, command_id=key)
    return d, review


def advance(w, candidate, action=None, key='advance'):
    branch=w['branch']
    args=dict(expected_branch_version_ref=branch.branch_ref, expected_head_revision_ref=branch.head_revision_ref,
        expected_stream_head=branch.sequence, next_revision_ref=candidate.revision.revision_ref, command_id=key)
    if action is None:
        return w['gateway'].advance_author_branch(**args)
    with w['host'].command(action, branch_command_key(key)):
        return w['gateway'].advance_author_branch(**args)


def test_real_branch_acceptance_and_original_exact_replay(world):
    w=world; join(w); p,c,_=proposal(w); d,_=decision(w,p,c)
    action=w['host'].action(w['a'],d)
    result=advance(w,c,action)
    receipt=w['host'].read_fact(w['a'], w['host'].receipt_ref(branch_command_key('advance')))
    assert receipt['data']['result_ref']==result.branch_ref.to_dict()['ref']
    before=plain.counts(w['core'])
    w['host'].revoke(w['a'],w['project'],subject_session_ref=w['b'].ref,command_id='leave')
    revoked=plain.counts(w['core'])
    assert advance(w,c,action)==result
    assert plain.counts(w['core'])==revoked and revoked[0]==before[0]+1
    with pytest.raises(RegistryConflict):
        advance(w,c,replace(action, session=w['b']))


@pytest.mark.parametrize('role', ['read', None])
def test_readonly_or_uninvited_cannot_propose(world,role):
    if role: join(world,role=role)
    with pytest.raises(RegistryConflict,match='eligibility'):
        proposal(world)


@pytest.mark.parametrize('route',['gateway','transaction','batch','alias'])
def test_no_envelope_cannot_bypass_actual_branch_boundary(world,route):
    w=world; join(w); p,c,_=proposal(w)
    before=plain.counts(w['core'])
    with pytest.raises(RegistryConflict,match='trusted HOST'):
        if route=='gateway': advance(w,c)
        elif route=='alias': w['gateway'].create_author_branch(head_revision_ref=c.revision.revision_ref,command_id='alias')
        else:
            successor=_successor(w['branch'],c.revision,w['core'].task_id,'direct')
            tx,obj=_stage_branch(w['core'],successor)
            tx.commit() if route=='transaction' else _direct_batch(w['core'],tx,obj)
    assert plain.counts(w['core'])==before


def test_p1_approval_never_authorizes_p2(world):
    w=world; join(w); p1,c1,_=proposal(w); d,r=decision(w,p1,c1)
    p2,c2,_=proposal(w,key='P2')
    with pytest.raises(RegistryConflict,match='approval'):
        w['host'].decide(w['a'],w['project'],operation='advance',proposal_refs=(p2,),review_refs=(r,),
            result_revision_ref=c2.revision.revision_ref,expected_branch_ref=w['branch'].branch_ref,
            expected_head_ref=w['branch'].head_revision_ref,command_id='bad-P2')
    with pytest.raises(RegistryConflict,match='differs'):
        advance(w,c2,w['host'].action(w['a'],d))
    w['host'].review(w['a'],p1,disposition='request_changes',reason='Exact pending revision requires a change',command_id='withdraw-P1')
    with pytest.raises(RegistryConflict,match='current required review'):
        advance(w,c1,w['host'].action(w['a'],d),key='withdrawn-approval')


def test_prepared_transaction_rechecks_author_after_revoke(world):
    w=world; join(w); p,c,_=proposal(w); d,_=decision(w,p,c)
    successor=_successor(w['branch'],c.revision,w['core'].task_id,'prepared')
    tx,_=_stage_branch(w['core'],successor)
    w['host'].bind_transaction(tx,w['host'].action(w['a'],d))
    w['host'].revoke(w['a'],w['project'],subject_session_ref=w['b'].ref,command_id='revoke')
    before=plain.counts(w['core'])
    with pytest.raises(RegistryConflict,match='eligibility'): tx.commit()
    assert plain.counts(w['core'])==before
    assert w['core'].event_store.object_rows_by_type(RECEIPT)==()


def test_owner_revoke_and_stale_competing_head(world):
    w=world;join(w);join(w,'c');p,c,_=proposal(w);d,_=decision(w,p,c)
    p2,c2,_=proposal(w,'c','C1');d2,_=decision(w,p2,c2,'D2')
    advance(w,c,w['host'].action(w['a'],d))
    with pytest.raises(RegistryConflict,match='stale'):
        advance(w,c2,w['host'].action(w['a'],d2),key='stale')
    w['host'].revoke(w['a'],w['project'],subject_session_ref=w['a'].ref,command_id='owner-revoke')
    with pytest.raises(RegistryConflict,match='owner authority'):
        advance(w,c2,w['host'].action(w['a'],d2),key='revoked-owner')


def test_sessions_resume_models_do_not_change_actor_and_no_context_leak(world):
    w=world;join(w)
    resumed=w['host'].resume_session(w['b'].ref,origin_core=w['bcore'],principal_ref=w['bp'])
    assert resumed==w['b']
    with pytest.raises(RegistryConflict):
        w['host'].resume_session(w['a'].ref,origin_core=w['bcore'],principal_ref=w['bp'])
    p,c,_=proposal(w);d,_=decision(w,p,c);a=w['host'].action(w['a'],d)
    with w['host'].command(a,branch_command_key('one-command')):
        assert w['core'].begin(idempotency_key='host-registration:tool:shared')._collaboration_action is None
        assert w['bcore'].begin(idempotency_key=branch_command_key('one-command'))._collaboration_action is None
    assert w['core'].begin(idempotency_key=branch_command_key('one-command'))._collaboration_action is None


def test_participant_cannot_grant_or_protect_or_forge_owner(world):
    w=world;join(w)
    with pytest.raises(RegistryConflict):
        w['host'].invite(w['b'],w['project'],subject_session_ref=w['c'].ref,command_id='bootstrap')
    with pytest.raises(RegistryConflict):
        w['host'].protect(w['b'],branch_ref=w['branch'].branch_ref,command_id='unprotect')
    with pytest.raises(RegistryConflict):
        w['host'].invite(replace(w['b'],actor=w['a'].actor),w['project'],subject_session_ref=w['c'].ref,command_id='spoof')
    with pytest.raises(RegistryConflict,match='altered'):
        w['host'].invite(replace(w['b'],ref=w['a'].ref,actor=w['a'].actor),w['project'],
            subject_session_ref=w['c'].ref,command_id='spoof-both')


def test_fake_fact_without_protection_is_not_authority(tmp_path):
    core,p=personal(tmp_path/'fresh','fresh')
    from cpn.rpnh.registry.user_collaboration import fact_ref
    ref=fact_ref(core.task_id,'session','fake')
    tx=core.begin(idempotency_key='fake')
    from cpn.rpnh.registry._event_store.branch_publication import _local_binding
    from cpn.rpnh.registry.strict_contracts import ref_payload
    with core.event_store.connect() as db:
        binding=_local_binding(db,core.catalog,core.task_id,core.object_store)
    doc={'schema_version':'registry_v1/'+ref.entity_type,'self_ref':ref_payload(ref),'source_id':'fresh',
        'owner_task_ref':binding['task_ref'],'publisher_bootstrap_ref':binding['bootstrap_command_ref'],
        'command_id':'fake','session_ref':None,'project_ref':None,'data':{'actor':'fake-owner',
            'origin_source':'fresh','origin_task_ref':binding['task_ref'],'origin_principal_ref':ref_payload(p),
            'target_principal_ref':ref_payload(p),'owner':True}}
    with pytest.raises(RegistryConflict,match='trusted HOST'):
        tx.prewrite(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,
            payload=canonical_json(doc),metadata=doc,media_type='application/json',schema_ref='registry_v1/'+ref.entity_type)
        tx.commit()


def test_same_batch_unprotect_cannot_hide_pretransaction_scope(world):
    w=world;join(w);p,c,_=proposal(w)
    successor=_successor(w['branch'],c.revision,w['core'].task_id,'unprotect')
    tx,_=_stage_branch(w['core'],successor)
    original=w['host']._get(w['project']);original['data']['protect_task_runtime']=False
    with pytest.raises(Exception):
        tx.prewrite(object_type=PROJECT,logical_id=w['project'].entity_id,version_id=new_id('resource_version'),
            payload=canonical_json(original),metadata=original,media_type='application/json',schema_ref='registry_v1/'+PROJECT)
        tx.commit()


def test_optional_inventory_not_default_and_protected_reopen_fails_closed(world):
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
    with pytest.raises(SchemaGovernanceError): SchemaCatalog().require(PROJECT,category='object')
    w=world
    # Reopening with a legacy inventory may itself reject the catalog. If it
    # opens, the always-on commit gate must still see canonical protection.
    try:
        reopened=_RegistryCore(w['core'].run_dir,create=False)
    except (SchemaGovernanceError,RegistryConflict):
        return
    with pytest.raises((SchemaGovernanceError,RegistryConflict)):
        reopened.begin(idempotency_key='no-catalog').commit()


def test_original_observer_revoke_fences_actual_proposal_replay_not_membership(world):
    from datetime import datetime,timedelta,timezone
    from cpn.rpnh.registry.observer_access import ObserverReadScope,issue_observer_access
    w=world;h=w['host'];join(w)
    kwargs=dict(principal_ref=h.session_principal(w['b']),
        scope=ObserverReadScope({}, {kind:('*',) for kind in TYPES}),purpose='collaboration-view',
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),command_id='read-grant')
    with pytest.raises(RegistryConflict,match='trusted HOST'):
        issue_observer_access(w['gateway'],**kwargs)
    observer=h.issue_read_access(w['a'],**kwargs)
    w['b']=h.bind_read_authority(w['b'],observer)
    p,c,args=proposal(w)
    count=w['core'].event_store.max_ordinal()
    assert h.submit_proposal(w['b'],w['project'],**args)==p
    assert w['core'].event_store.max_ordinal()==count
    assert h.read_view(w['b'])
    h.revoke_read_access(w['a'],grant_ref=observer.grant_ref,command_id='revoke-read')
    count=w['core'].event_store.max_ordinal()
    with pytest.raises(RegistryConflict,match='revoked'):
        h.submit_proposal(w['b'],w['project'],**args)
    with pytest.raises(RegistryConflict,match='revoked'): h.read_view(w['b'])
    assert w['core'].event_store.max_ordinal()==count
    # Original source read revocation neither revokes target propose nor
    # erases an immutable already-delivered/canonical contribution.
    p2,_,_=proposal(w,key='after-read-revoke')
    assert p2!=p and h.read_fact(w['a'],p)['data']['candidate_revision_ref']==c.revision.revision_ref.to_dict()['ref']


@pytest.mark.parametrize('raw',[b'{"x":1e400}',b'{"x":NaN}',b'{"x":1,"x":2}',b'['*65+b']'*65])
def test_native_proposal_codec_rejects_nonfinite_duplicate_or_excess_depth(raw):
    from cpn.rpnh.registry.user_collaboration_source import strict_proposal_json
    with pytest.raises(RegistryConflict): strict_proposal_json(raw)
