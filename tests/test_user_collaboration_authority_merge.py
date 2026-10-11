"""Exact original two-parent M acceptance and complete required input review."""
from copy import deepcopy
import pytest

import test_user_collaboration_authority as support
from cpn.rpnh.collaboration import PlainModuleMergeAuthor, plain_merge_result_schema_data
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.user_collaboration import RECEIPT
from cpn.rpnh.registry._event_store.branch_publication import branch_command_key


def test_genuine_M_requires_all_inputs_and_accepts_from_original_common_base(tmp_path,monkeypatch):
    monkeypatch.setattr(support,'_plain_inventory',plain_merge_result_schema_data)
    w=support.world.__wrapped__(tmp_path,monkeypatch);h=w['host'];support.join(w);support.join(w,'c')
    left_doc=deepcopy(w['base'].module.to_dict());left_doc['components'][0]['operations'][0]['config']={'value':'B'}
    right_doc=deepcopy(w['base'].module.to_dict());right_doc['components'][1]['operations'][0]['config']={'value':'C'}
    pb,left,_=support.proposal(w,key='PB',document=left_doc)
    pc,right,_=support.proposal(w,'c','PC',document=right_doc)
    rb=h.review(w['a'],pb,disposition='approve',command_id='RB')
    # This real ordinary child is neither the reviewed exact candidate nor M.
    x=support.plain.publish(w['fixture'],left_doc,'unreviewed-child',parent=left.revision.revision_ref)
    args=dict(operation='advance',expected_branch_ref=w['branch'].branch_ref,
        expected_head_ref=w['branch'].head_revision_ref)
    with pytest.raises(RegistryConflict,match='exact candidate'):
        h.decide(w['a'],w['project'],proposal_refs=(pb,),review_refs=(rb,),
            result_revision_ref=x.revision.revision_ref,command_id='reject-child',**args)
    analyzer=w['fixture'][4]
    analysis=analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,
        base_ref=w['base'].revision.revision_ref,command_id='M-analysis')
    author=PlainModuleMergeAuthor(w['gateway'],w['fixture'][2],analyzer.producer)
    merged=author.publish(analysis_ref=analysis.analysis_ref,choices=[],command_id='M-original')
    assert merged.revision.parent_revision_refs==(left.revision.revision_ref,right.revision.revision_ref)
    assert w['branch'].head_revision_ref not in merged.revision.parent_revision_refs
    with pytest.raises(RegistryConflict,match='complete exact proposal'):
        h.decide(w['a'],w['project'],proposal_refs=(pb,),review_refs=(rb,),
            result_revision_ref=merged.revision.revision_ref,command_id='reject-omitted-C',**args)
    h.revoke(w['a'],w['project'],subject_session_ref=w['c'].ref,command_id='C-leaves')
    with pytest.raises(RegistryConflict,match='complete exact proposal'):
        h.decide(w['a'],w['project'],proposal_refs=(pb,),review_refs=(rb,),
            result_revision_ref=merged.revision.revision_ref,command_id='reject-revoked-omitted-C',**args)
    invitation=h.invite(w['a'],w['project'],subject_session_ref=w['c'].ref,command_id='C-new-invite')
    h.join(w['c'],invitation,command_id='C-new-join')
    rc=h.review(w['a'],pc,disposition='approve',command_id='RC')
    full=h.decide(w['a'],w['project'],proposal_refs=(pb,pc),review_refs=(rb,rc),
        result_revision_ref=merged.revision.revision_ref,command_id='M-full-decision',**args)
    action=h.action(w['a'],full)
    h.revoke(w['a'],w['project'],subject_session_ref=w['c'].ref,command_id='C-leaves-before-first-acceptance')
    with pytest.raises(RegistryConflict,match='eligibility'):
        support.advance(w,merged,action,key='M-revoked-at-commit')
    assert w['core'].event_store.object_rows_by_type(RECEIPT)==()
    invitation=h.invite(w['a'],w['project'],subject_session_ref=w['c'].ref,command_id='C-third-invite')
    h.join(w['c'],invitation,command_id='C-third-join')
    accepted=support.advance(w,merged,action,key='M-accepted')
    assert accepted.head_revision_ref==merged.revision.revision_ref
    receipt=h.read_fact(w['a'],h.receipt_ref(branch_command_key('M-accepted')))
    assert receipt['data']['result_revision_ref']==merged.revision.revision_ref.to_dict()['ref']
    assert receipt['data']['result_ref']==accepted.branch_ref.to_dict()['ref']
