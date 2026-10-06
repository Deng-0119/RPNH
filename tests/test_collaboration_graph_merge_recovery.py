"""Actual graph analysis/M/E durable cuts, fresh closure and immutable bytes."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cpn.rpnh.collaboration import GraphMergeAuthor, GraphMergeAnalyzer, validate_closed_revision, read_graph_merge_analysis
from cpn.rpnh.collaboration import graph_merge, graph_merge_author
from cpn.rpnh.collaboration.graph_authoring import GRAPH_REF_FIELDS
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, StaleWriterError
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError, canonical_json
from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
from cpn.rpnh.registry.transaction import RegistryTransaction
from test_collaboration_graph_materials import registration, assert_author_only
from test_collaboration_graph_merge import fixture, make_sides, choices, merged, counts


ERRORS = (ValueError, RegistryConflict, ObjectIntegrityError, SchemaGovernanceError)


class DurableCut(RuntimeError):
    pass


def reopen(core, gateway, producer):
    next_core = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(next_core, gateway._task_ref, gateway._bootstrap_ref)
    author = GraphMergeAuthor(next_gateway, registration(), producer)
    return next_core, next_gateway, author


def test_every_analysis_and_result_material_cut_reopens_and_freezes_complete_command(fixture, monkeypatch):
    core, gateway, _, _, analyzer, author, _, _ = fixture
    left, right = make_sides(fixture)
    request = {"local_ref":left.revision.revision_ref,"incoming_ref":right.revision.revision_ref}
    original_analysis = RegistryTransaction.commit
    for cut in (1,2):
        durable = []
        command_id="cut:analysis:"+str(cut)
        keys={graph_merge._key(command_id)+":"+role:graph_merge._material_ref(core,author.binding,graph_merge._key(command_id)+":"+role).ref for role in ("command","analysis")}
        def interrupt(tx):
            result=original_analysis(tx)
            if tx.idempotency_key in keys:
                reference=keys[tx.idempotency_key]
                assert core.event_store.object_row(reference.resource_version_id) is not None
                durable.append(reference)
                if len(durable)==cut:raise DurableCut("real analysis resource committed")
            return result
        monkeypatch.setattr(RegistryTransaction,"commit",interrupt)
        with pytest.raises(DurableCut): analyzer.analyze(**request,command_id="cut:analysis:"+str(cut))
        monkeypatch.setattr(RegistryTransaction,"commit",original_analysis)
        core,gateway,author = reopen(core,gateway,author.producer);analyzer=author.analyzer
        before=counts(core)
        with pytest.raises(RegistryConflict):
            analyzer.analyze(local_ref=right.revision.revision_ref,incoming_ref=left.revision.revision_ref,command_id="cut:analysis:"+str(cut))
        assert counts(core)==before
        analysis=analyzer.analyze(**request,command_id="cut:analysis:"+str(cut))
        assert analysis.command_ref.ref==durable[0]
        print("GRAPH_ANALYSIS_DURABLE_CUT",cut)
    original=graph_merge_author._publish_private_system
    for cut in range(1,10):
        durable=[]; command="cut:merge:"+str(cut)
        def interrupt(*args,**kwargs):
            ref=original(*args,**kwargs);durable.append(ref)
            if len(durable)==cut:raise DurableCut("real result resource committed")
            return ref
        before_revisions=len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))
        monkeypatch.setattr(graph_merge_author,"_publish_private_system",interrupt)
        with pytest.raises(DurableCut): author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id=command)
        monkeypatch.setattr(graph_merge_author,"_publish_private_system",original)
        assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))==before_revisions
        core,gateway,author=reopen(core,gateway,author.producer)
        changed=choices(analysis);changed[0]["reason"]="A different caller rationale is a different complete command."
        before=counts(core)
        with pytest.raises(RegistryConflict):author.publish(analysis_ref=analysis.analysis_ref,choices=changed,command_id=command)
        assert counts(core)==before
        result=author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id=command)
        refs=[result.command_ref.ref,result.resolution_ref.ref,*[getattr(result.revision,key).ref for key in GRAPH_REF_FIELDS]]
        assert refs[:cut]==durable
        assert validate_closed_revision(core,result.revision.revision_ref,registration())==result
        print("GRAPH_MERGE_DURABLE_CUT",cut)
    commit=RegistryTransaction.commit
    def lost_reply(tx):
        result=commit(tx)
        if tx.idempotency_key==graph_merge_author._key("cut:merge-reply"):
            assert tx._closed
            raise DurableCut("merged descriptor committed before lost reply")
        return result
    monkeypatch.setattr(RegistryTransaction,"commit",lost_reply)
    with pytest.raises(DurableCut):author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id="cut:merge-reply")
    monkeypatch.setattr(RegistryTransaction,"commit",commit)
    core,gateway,author=reopen(core,gateway,author.producer)
    before=counts(core)
    result=author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id="cut:merge-reply")
    assert counts(core)==before
    print("GRAPH_MERGE_DESCRIPTOR_LOST_REPLY",str(result.revision.revision_ref.ref.version_id))
    assert_author_only(core)


def test_every_descendant_material_cut_and_descriptor_failure_recover_exactly(fixture, monkeypatch):
    core,gateway,_,_,_,author,_,_=fixture
    _,_,_,parent=merged(fixture)
    ids={row["locator"]:row["element_id"] for row in parent.source_map["elements"]}
    request={"source":parent.source,"recipe":parent.recipe,"source_ids":ids,"parent_ref":parent.revision.revision_ref}
    original=graph_merge_author._publish_private_system
    for cut in range(1,10):
        durable=[];command="cut:edit:"+str(cut)
        def interrupt(*args,**kwargs):
            ref=original(*args,**kwargs);durable.append(ref)
            if len(durable)==cut:raise DurableCut("real edit resource committed")
            return ref
        before_revisions=len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))
        monkeypatch.setattr(graph_merge_author,"_publish_private_system",interrupt)
        with pytest.raises(DurableCut):author.publish_edit(**request,command_id=command)
        monkeypatch.setattr(graph_merge_author,"_publish_private_system",original)
        assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))==before_revisions
        core,gateway,author=reopen(core,gateway,author.producer)
        result=author.publish_edit(**request,command_id=command)
        refs=[result.command_ref.ref,result.resolution_ref.ref,*[getattr(result.revision,key).ref for key in GRAPH_REF_FIELDS]]
        assert refs[:cut]==durable
        print("GRAPH_EDIT_DURABLE_CUT",cut)
    insert=core.event_store._insert_event
    def fail(db,event):
        insert(db,event)
        if event.payload.get("object_type")=="collaboration_net_revision/v3":raise DurableCut("real final descriptor transaction rollback")
    monkeypatch.setattr(core.event_store,"_insert_event",fail)
    before_revisions=len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))
    with pytest.raises(DurableCut):author.publish_edit(**request,command_id="cut:descriptor")
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))==before_revisions
    monkeypatch.setattr(core.event_store,"_insert_event",insert)
    result=author.publish_edit(**request,command_id="cut:descriptor")
    before=counts(core)
    assert author.publish_edit(**request,command_id="cut:descriptor").revision==result.revision
    assert counts(core)==before
    commit=RegistryTransaction.commit
    def lost_reply(tx):
        result=commit(tx)
        if tx.idempotency_key==graph_merge_author._key("cut:edit-reply"):
            assert tx._closed
            raise DurableCut("edit descriptor committed before lost reply")
        return result
    monkeypatch.setattr(RegistryTransaction,"commit",lost_reply)
    with pytest.raises(DurableCut):author.publish_edit(**request,command_id="cut:edit-reply")
    monkeypatch.setattr(RegistryTransaction,"commit",commit)
    core,gateway,author=reopen(core,gateway,author.producer)
    before=counts(core)
    result=author.publish_edit(**request,command_id="cut:edit-reply")
    assert counts(core)==before
    print("GRAPH_EDIT_DESCRIPTOR_LOST_REPLY",str(result.revision.revision_ref.ref.version_id))


def test_fresh_final_proof_rejects_actual_damage_after_command_then_exact_restore(fixture,monkeypatch):
    core,_,_,_,analyzer,author,base,_=fixture
    left,right=make_sides(fixture)
    original=graph_merge._publish_private_system
    path=core.object_store.path_for_version(base.revision.graph_recipe_ref.ref.resource_version_id)
    saved=path.read_bytes();called=[]
    def damage(*args,**kwargs):
        ref=original(*args,**kwargs);called.append(ref)
        if len(called)==1:path.write_bytes(b"{}")
        return ref
    monkeypatch.setattr(graph_merge,"_publish_private_system",damage)
    with pytest.raises(ERRORS):analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id="fresh:analysis")
    assert len(called)==2
    analysis_ref=graph_merge._material_ref(core,author.binding,graph_merge._key("fresh:analysis")+":analysis")
    assert core.event_store.object_row(analysis_ref.ref.resource_version_id) is None
    path.write_bytes(saved);monkeypatch.setattr(graph_merge,"_publish_private_system",original)
    analysis=analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id="fresh:analysis")
    assert analysis.command_ref.ref==called[0]
    # Corrupt only after the real analysis object has been prewritten, before
    # its actual commit. The existing transaction hook must prevent success.
    late_path=core.object_store.path_for_version(base.revision.graph_source_ref.ref.resource_version_id)
    late_saved=late_path.read_bytes();late_calls=[]
    def late_damage(*args,**kwargs):
        reference=original(*args,**kwargs)
        if kwargs.get("transaction") is not None:
            late_calls.append(reference);late_path.write_bytes(b"{}")
        return reference
    monkeypatch.setattr(graph_merge,"_publish_private_system",late_damage)
    with pytest.raises(ERRORS):analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id="fresh:late-analysis")
    assert len(late_calls)==1 and core.event_store.object_row(late_calls[0].resource_version_id) is None
    late_path.write_bytes(late_saved);monkeypatch.setattr(graph_merge,"_publish_private_system",original)
    late=analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id="fresh:late-analysis")
    assert late.analysis_ref.ref==late_calls[0]
    targets=(base.revision.revision_ref.ref.version_id,base.revision.graph_source_ref.ref.resource_version_id,
        left.revision.graph_recipe_ref.ref.resource_version_id,right.revision.host_requirements_ref.ref.resource_version_id)
    original=graph_merge_author._publish_private_system
    for index,target in enumerate(targets):
        path=core.object_store.path_for_version(target);saved=path.read_bytes();called=[]
        def damage(*args,**kwargs):
            ref=original(*args,**kwargs);called.append(ref)
            if len(called)==1:path.write_bytes(b"{}")
            return ref
        before_revisions=len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))
        monkeypatch.setattr(graph_merge_author,"_publish_private_system",damage)
        with pytest.raises(ERRORS):author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id="fresh:merge:"+str(index))
        assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v3"))==before_revisions
        path.write_bytes(saved);monkeypatch.setattr(graph_merge_author,"_publish_private_system",original)
        result=author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id="fresh:merge:"+str(index))
        assert result.command_ref.ref==called[0]
        print("GRAPH_FRESH_FINAL_DEPENDENCY",index)


def test_full_consumer_rejects_all_material_byte_substitutions_and_changed_command(fixture,monkeypatch):
    core,gateway,_,_,_,author,_,_=fixture
    left,right,analysis,result=merged(fixture)
    refs=(result.revision.revision_ref.ref.version_id,result.command_ref.ref.resource_version_id,result.resolution_ref.ref.resource_version_id,
        *(getattr(result.revision,key).ref.resource_version_id for key in GRAPH_REF_FIELDS),analysis.analysis_ref.ref.resource_version_id,
        analysis.command_ref.ref.resource_version_id)
    before=counts(core)
    for index,reference in enumerate(refs):
        path=core.object_store.path_for_version(reference);saved=path.read_bytes();path.write_bytes(saved+b" ")
        try:
            with pytest.raises(ERRORS):validate_closed_revision(core,result.revision.revision_ref,registration())
            assert counts(core)==before
        finally:path.write_bytes(saved)
        assert validate_closed_revision(core,result.revision.revision_ref,registration())==result
        print("GRAPH_FULL_PROOF_MATERIAL_REJECT",index)
    changed=choices(analysis);changed[0]["reason"]="Changed decision explanation."
    with pytest.raises(RegistryConflict):author.publish(analysis_ref=analysis.analysis_ref,choices=changed,command_id="graph:merge")
    assert counts(core)==before
    # A real alternative schema resource with identical supported schema bytes
    # cannot replace the exact authority locked by the first analysis command.
    original=graph_merge._publish_private_system
    schema_body=json.loads(core.catalog.schema_path(graph_merge.ANALYSIS_SCHEMA).read_text())
    alternate=original(core,gateway._task_ref,PublishResource(origin=PrivateSystemOrigin(gateway._bootstrap_ref),
        payload=canonical_json(schema_body),media_type="application/schema+json",content_schema_ref=None,
        summary="Independent same-body graph analysis schema",lifetime_ref=gateway._bootstrap_ref,
        idempotency_key="integrity:alternate-analysis-schema"))
    assert alternate!=author.analyzer.schemas[graph_merge.ANALYSIS_SCHEMA]
    for change in ("authority","summary","extensions"):
        staged=[];command_id="integrity:analysis:"+change
        def change_analysis(core_arg,owner,spec,**kwargs):
            if spec.content_schema_ref==graph_merge.ANALYSIS_SCHEMA:
                if change=="authority":spec=replace(spec,content_schema_authority_ref=alternate)
                elif change=="summary":spec=replace(spec,summary="Changed after the complete command")
                else:spec=replace(spec,extensions={"caller_note":"Changed after the complete command"})
            reference=original(core_arg,owner,spec,**kwargs)
            if spec.content_schema_ref==graph_merge.ANALYSIS_SCHEMA:
                assert kwargs.get("transaction") is not None
                staged.append(reference)
            return reference
        with monkeypatch.context() as patch:
            patch.setattr(graph_merge,"_publish_private_system",change_analysis)
            with pytest.raises(RegistryConflict,match="frozen actual material"):
                author.analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id=command_id)
        assert len(staged)==1 and core.event_store.object_row(staged[0].resource_version_id) is None
        restored=author.analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id=command_id)
        assert restored.analysis_ref.ref==staged[0]
        assert read_graph_merge_analysis(core,restored.analysis_ref,registration()).document==restored.document
        print("GRAPH_ANALYSIS_FROZEN_METADATA_REJECT",change)
    captured=[]
    def freeze_command(core_arg,owner,spec,**kwargs):
        reference=original(core_arg,owner,spec,**kwargs)
        if spec.content_schema_ref==graph_merge.ANALYSIS_COMMAND_SCHEMA:
            captured.append(json.loads(spec.payload));raise DurableCut("complete analysis command committed")
        return reference
    with monkeypatch.context() as patch:
        patch.setattr(graph_merge,"_publish_private_system",freeze_command)
        with pytest.raises(DurableCut):author.analyzer.analyze(local_ref=left.revision.revision_ref,
            incoming_ref=right.revision.revision_ref,command_id="integrity:canonical-alternate")
    body=captured[0]["analysis"]
    bad=original(core,gateway._task_ref,PublishResource(origin=PrivateSystemOrigin(gateway._bootstrap_ref),
        payload=canonical_json(body),media_type="application/json",content_schema_ref=graph_merge.ANALYSIS_SCHEMA,
        content_schema_authority_ref=alternate,summary="Graph merge analysis",lifetime_ref=gateway._bootstrap_ref,
        descriptors={graph_merge.ANALYSIS_MARKER:json.dumps(body["command_ref"],sort_keys=True,separators=(",",":"),ensure_ascii=False)},
        idempotency_key=graph_merge._key("integrity:canonical-alternate")+":analysis"))
    assert core.event_store.object_row(bad.resource_version_id) is not None
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    with pytest.raises(RegistryConflict,match="immutable command"):
        read_graph_merge_analysis(core,SourceQualifiedResourceRef(author.binding["source_id"],bad),registration())
    print("GRAPH_ANALYSIS_CANONICAL_SAME_BODY_AUTHORITY_REJECT")


def test_actual_final_registry_epoch_fences_stale_owner_after_preparation(fixture,monkeypatch):
    core,_,_,_,_,author,_,_=fixture
    left,right=make_sides(fixture)
    analysis=author.analyzer.analyze(local_ref=left.revision.revision_ref,incoming_ref=right.revision.revision_ref,command_id="epoch:analysis")
    commit=RegistryTransaction.commit;rotated=[]
    def fence(tx):
        if tx.idempotency_key.startswith("collaboration-graph-merge-author:") and tx.idempotency_key.endswith('"epoch:merge"}'):
            rotated.append(_RegistryCore(core.run_dir,create=False,catalog=core.catalog))
        return commit(tx)
    monkeypatch.setattr(RegistryTransaction,"commit",fence)
    with pytest.raises(StaleWriterError):author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id="epoch:merge")
    assert rotated and not core.event_store.object_rows_by_type("collaboration_net_revision/v3")
    monkeypatch.setattr(RegistryTransaction,"commit",commit)
    next_core,gateway,next_author=reopen(core,fixture[1],author.producer)
    result=next_author.publish(analysis_ref=analysis.analysis_ref,choices=choices(analysis),command_id="epoch:merge")
    assert validate_closed_revision(next_core,result.revision.revision_ref,registration())==result
