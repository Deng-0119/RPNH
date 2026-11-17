"""Real bootstrap/publication/commit/canonical reads; query authority is isolated.

The fixture bypasses caller admission only, not Registry writes or canonical
selection. These are query contract tests, not an end-to-end authority test.
"""
from types import SimpleNamespace
import pytest
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import ObjectRef, TypedRelation, VersionRef
from cpn.rpnh.registry.resources import RelationFilter, RelationQuery, ResourceQuery
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from test_local_registry_browser import make_run, publish

class QueryKernel(_ResourceServiceKernel):
    def _query_authority(self, context, *args, **kwargs):
        return context.task_ref, context.task_ref
    def _authority_facts(self, context):
        return {}
    def _visible_refs(self, context, *, through_ordinal):
        from cpn.rpnh.registry.resources import ResourceVersionRef
        from cpn.rpnh.registry.identities import TypedId
        return {ResourceVersionRef(TypedId.parse(r['logical_id']), TypedId.parse(r['version_id']))
                for r in self._ResourceServiceKernel__core.event_store.canonical_object_rows(
                    object_type='resource_version/v1', through_ordinal=through_ordinal)}

def exact(ref):
    return VersionRef('resource_version/v1', ref.resource_id, ref.resource_version_id)

def relate(world, source, target, key):
    tx = world[0].begin(idempotency_key=key)
    relation = TypedRelation(new_id('relation'), 'reviewed_by', source, target, system_owned=True)
    tx.relate(relation)
    tx.commit()
    return relation

@pytest.fixture
def graph(tmp_path):
    world = make_run(tmp_path / 'registry')
    a, b, c = [publish(world, key=key) for key in ('a','b','c')]
    ab = relate(world, exact(a), exact(b), 'ab')
    bc = relate(world, exact(b), exact(c), 'bc')
    return world, QueryKernel(world[0]), SimpleNamespace(task_ref=world[1].task_ref), (a,b,c), (ab,bc)

def test_object_reference_is_published_canonically(graph):
    world,kernel,context,(a,b,c),edges = graph
    edge = relate(world, ObjectRef('resource_version/v1',a.resource_id), exact(c), 'logical')
    rows=world[0].event_store.canonical_relation_rows(relation_type='reviewed_by')
    assert str(edge.relation_id) in {r['relation_id'] for r in rows}
    # Exact resource query must not manufacture a version for logical endpoints.
    result=kernel.relation_query(context, RelationQuery(context.task_ref,(b,), (RelationFilter('reviewed_by'),)))
    assert {r.relation_id for r in result.relations} == {r.relation_id for r in edges}

@pytest.mark.parametrize('direction,expected', [('forward',(0,1)),('reverse',(1,2)),('either',(0,1,2))])
def test_resource_filter_direction(graph,direction,expected):
    world,kernel,context,refs,edges=graph
    result=kernel.query(context, ResourceQuery(context.task_ref,(),relation_filters=(RelationFilter('reviewed_by',direction),)))
    assert {h.ref for h in result.headers} == {refs[i] for i in expected}

@pytest.mark.parametrize('direction,expected', [('forward',(1,)),('reverse',(0,)),('either',(0,1))])
def test_exact_relation_direction_keeps_existing_contract(graph,direction,expected):
    world,kernel,context,(a,b,c),edges=graph
    result=kernel.relation_query(context,RelationQuery(context.task_ref,(b,), (RelationFilter('reviewed_by',direction),)))
    assert {r.relation_id for r in result.relations} == {edges[i].relation_id for i in expected}

@pytest.mark.parametrize('logical_side',['source','target'])
def test_logical_endpoint_preserved_in_store_but_not_exact_api(graph,logical_side):
    world,kernel,context,(a,b,c),edges=graph
    logical=ObjectRef('task/v1',world[1].task_ref.entity_id)
    source,target=(logical,exact(b)) if logical_side=='source' else (exact(b),logical)
    edge=relate(world,source,target,'logical-'+logical_side)
    before=world[0].event_store.max_ordinal()
    result=kernel.relation_query(context,RelationQuery(context.task_ref,(b,), (RelationFilter('reviewed_by'),)))
    assert {r.relation_id for r in result.relations} == {e.relation_id for e in edges}
    assert any(r['relation_id']==str(edge.relation_id) for r in world[0].relations.forward(str(source.entity_id)))
    assert world[0].event_store.max_ordinal()==before


def test_direction_filter_honors_history_and_cursor(graph):
    world,kernel,context,(a,b,c),edges=graph
    head=kernel._canonical_head()
    later=relate(world,exact(c),exact(a),'later')
    query=ResourceQuery(context.task_ref,(),relation_filters=(RelationFilter('reviewed_by','forward'),),through_head=head,limit=1)
    first=kernel.query(context,query)
    from dataclasses import replace
    second=kernel.query(context,replace(query,cursor=first.cursor))
    assert {h.ref for h in first.headers+second.headers}=={a,b}
    assert second.cursor is None
    fresh=kernel.query(context,replace(query,through_head=None,limit=100))
    assert {h.ref for h in fresh.headers}=={a,b,c}
    old_edges=kernel.relation_query(context,RelationQuery(context.task_ref,(a,), (RelationFilter('reviewed_by','reverse'),),through_head=head))
    assert old_edges.relations==()


def test_resource_duplicate_kind_direction_filters_remain_and(graph):
    world,kernel,context,(a,b,c),edges=graph
    filters=(RelationFilter('reviewed_by','forward'),RelationFilter('reviewed_by','reverse'))
    for ordered in (filters,tuple(reversed(filters))):
        result=kernel.query(context,ResourceQuery(context.task_ref,(),relation_filters=ordered))
        assert {h.ref for h in result.headers}=={b}


def test_relation_duplicate_kind_keeps_legacy_last_filter(graph):
    world,kernel,context,(a,b,c),edges=graph
    filters=(RelationFilter('reviewed_by','forward'),RelationFilter('reviewed_by','reverse'))
    for ordered,expected in ((filters,edges[0]),(tuple(reversed(filters)),edges[1])):
        result=kernel.relation_query(context,RelationQuery(context.task_ref,(b,),ordered))
        assert [r.relation_id for r in result.relations]==[expected.relation_id]


def test_direction_reuses_existing_header_relation_reads(graph,monkeypatch):
    world,kernel,context,refs,edges=graph
    store=world[0].event_store
    original=store.canonical_relation_rows
    calls=[]
    def observed(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(store,'canonical_relation_rows',observed)
    kernel.query(context,ResourceQuery(context.task_ref,(),relation_filters=(RelationFilter('reviewed_by'),)))
    either_calls=list(calls);calls.clear()
    kernel.query(context,ResourceQuery(context.task_ref,(),relation_filters=(RelationFilter('reviewed_by','forward'),RelationFilter('reviewed_by','reverse'))))
    assert calls==either_calls


def test_direction_projection_is_call_local_and_consumed(graph):
    from cpn.rpnh.registry._resource_service.queries import _headers
    world,kernel,context,refs,edges=graph
    directions={}
    for header in _headers(kernel,refs,through_head=kernel._canonical_head(),relation_directions=directions):
        assert len(directions)<=128
        endpoint_kinds=directions.pop(header.ref)
        assert set(endpoint_kinds)=={'forward','reverse'}
    assert directions=={}
