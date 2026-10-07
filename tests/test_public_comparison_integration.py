"""Independent ordinary public comparison integration regressions.

Synthetic owner fixtures create inputs; all reading goes through the canonical
observer issuer and delivered read-only public opener. No sockets or providers.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid

from cpn.frontend.comparison_context import ComparisonProvider, comparison_context
from cpn.rpnh.collaboration import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.registry_read_contracts import ReadSessionRequest, ExplicitSources, SourceSelection
from cpn.rpnh.collaboration.registry_read_session import RegistryReadHostBinding, ExistingReadAuthorityProvider, open_registry_session, open_readonly_source
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access, observer_access_schema_data
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.module import ModuleDeclaration
from test_comparison_context import world, chosen, request, counts



def public_reader(*worlds, scopes=None, export=False, material_filter=None):
    cat = TypedReaderCatalog()
    grants = {}
    for w in worlds:
        fields = {kind: tuple(cat.fields(kind)) for kind in cat.entry_types}
        if scopes is not None:
            index = scopes[w['source']]
        else:
            index = fields
        # Fixture owner chooses already-created synthetic material refs. Actual
        # read authority is issued by the delivered canonical issuer below.
        materials = tuple(SourceQualifiedResourceRef(w['source'], ResourceVersionRef(
            TypedId.parse(r['logical_id']), TypedId.parse(r['version_id'])))
            for r in w['core'].event_store.object_rows_by_type('resource_version/v1'))
        revisions = tuple(SourceQualifiedVersionRef.from_dict(json.loads(r['metadata_json'])['revision_ref'], catalog=w['core'].catalog)
            for r in w['core'].event_store.object_rows_by_type('collaboration_net_revision/v1'))
        grant = issue_observer_access(w['gateway'], principal_ref=w['principal'],
            scope=ObserverReadScope(index, fields, tuple(r for r in materials if material_filter(r)) if material_filter else materials, (*materials, *revisions) if export else (), ('review-local',) if export else ()),
            purpose='functional-review', expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='review:' + uuid.uuid4().hex)
        grants[('review:verified', w['source'], 'local')] = grant
    def resolver(source_id, access_path):
        assert access_path == 'local'
        w = next(w for w in worlds if w['source'] == source_id)
        return open_readonly_source(w['core'].run_dir, catalog=w['core'].catalog)
    return open_registry_session(ReadSessionRequest(ExplicitSources(tuple(SourceSelection(
        SourceQualifiedVersionRef(w['source'], w['owner'].task_ref), 'local') for w in worlds)), 'functional-review'),
        host=RegistryReadHostBinding('review:verified', resolver, ExistingReadAuthorityProvider(grants), cat))


def test_heterogeneous_source_selector_paging_uses_each_scope(tmp_path):
    a, b = world(tmp_path / 'a', 'review-a'), world(tmp_path / 'b', 'review-b')
    scopes = {'review-a': {'collaboration_net_revision/v1': ('definition_kind',)},
              'review-b': {'collaboration_net_revision/v1': ('revision_ref',)}}
    with public_reader(a, b, scopes=scopes) as reader:
        provider = ComparisonProvider(reader)
        before = (counts(a), counts(b))
        first = provider.comparison_selection()
        assert first['next_cursor']
        second = provider.comparison_selection(cursor=first['next_cursor'])
        assert second == provider.comparison_selection(cursor=first['next_cursor'])
        assert second['next_cursor'] is None
        assert {item['target']['source_id'] for item in first['targets'] + second['targets']} == {'review-a', 'review-b'}
        assert first['source_cuts'] == second['source_cuts']
        assert (counts(a), counts(b)) == before
        context = comparison_context(reader, request(reader, first['targets'][0]['target'], second['targets'][0]['target']))
        assert context['presentation']['recommended_mode'] == 'full_pair'
        assert context['mapping']['explicit_presence_changes'] == []


def test_scoped_copy_comparison_retains_fixed_cut_after_append(tmp_path):
    w = world(tmp_path / 'w')
    doc = w['module'].to_dict(); doc['name'] = 'Renamed'
    second = w['author'].publish(module=ModuleDeclaration.from_dict(doc), element_ids=w['ids'],
        parent_ref=w['first'].revision.revision_ref, command_id='second')
    with public_reader(w) as reader:
        req = request(reader, chosen(w['first']), chosen(second))
        pair = comparison_context(reader, req)
        req['source_cuts'] = pair['request_echo']['source_cuts']
        req['axes'] = ['definition']
        req['scope'] = {'kind': 'selected_pair', **{side: {'kind': 'nodes', 'node_ids': [next(n['id'] for n in pair[side]['graph']['nodes'] if n['kind'] == 'transition')]} for side in ('left', 'right')}}
        scoped = comparison_context(reader, req)
        assert scoped['presentation']['recommended_mode'] == 'reliable_diff'
        assert all(scoped['axes'][axis]['coverage'] == 'not_requested' and scoped['axes'][axis]['counts']['known_same'] is None for axis in ('configuration', 'materials', 'runtime'))
        w['author'].publish(module=w['module'], element_ids=w['ids'], parent_ref=second.revision.revision_ref, command_id='third')
        full = deepcopy(req); full['scope'] = scoped['presentation']['full_pair_navigation']['scope']
        full['source_cuts'] = scoped['presentation']['full_pair_navigation']['source_cuts']
        result = comparison_context(reader, full)
        assert result['request_echo']['source_cuts'] == pair['request_echo']['source_cuts']
        assert result['left']['selected_target'] == req['left'] and result['right']['selected_target'] == req['right']


def assembly_world(tmp_path, monkeypatch):
    import test_typed_assembly_v9 as support
    original = support.typed_assembly_schema_data
    def all_schemas():
        a, at, ap = original(); b, bt, bp = observer_access_schema_data()
        return {**a, **b}, tuple({t.name: t for t in (*at, *bt)}.values()), {**ap, **bp}
    monkeypatch.setattr(support, 'typed_assembly_schema_data', all_schemas)
    core, gateway, registration, producer, author, composer = support.world(tmp_path / 'assembly', context=True)
    item = support.publish_member(author, registration, 'member', plain=True, budget=('left', 5))
    value = composer.publish(**support.request(item, claims=(support.PLAIN, support.PLAIN)))
    from types import SimpleNamespace
    w = {'core': core, 'gateway': gateway, 'principal': producer.ref, 'source': producer.source_id,
         'owner': SimpleNamespace(task_ref=gateway._task_ref), 'author': author, 'first': item[0]}
    return w, value


def test_actual_assembly_v9_is_selectable_with_member_hierarchy(tmp_path, monkeypatch):
    w, value = assembly_world(tmp_path, monkeypatch)
    with public_reader(w) as reader:
        ref = value.revision.revision_ref
        projection = reader.read_public_projection(entry_ref=ref, at_cut=reader.capture_cut(w['source']))['record']
        assert len(projection['hierarchy']) == 3
        targets = []
        provider = ComparisonProvider(reader)
        page = provider.comparison_selection(); targets.extend(item['target'] for item in page['targets'])
        while page['next_cursor']:
            page = provider.comparison_selection(cursor=page['next_cursor']); targets.extend(item['target'] for item in page['targets'])
        # Contract V-A05/J11: actual declared Assembly members must be reachable
        # from the public exact-target selector, not only a private projection.
        assert any(target.get('revision_ref') == ref.to_dict() for target in targets), {'expected_assembly': ref.to_dict(), 'targets': targets}
        req = request(reader, chosen(value), chosen(value))
        result = comparison_context(reader, req)
        scopes = result['left']['hierarchy_navigation']['scopes']
        assert len(scopes) == 3
        member = next(row for row in scopes if row['scope']['kind'] == 'subnet')
        req['source_cuts'] = result['request_echo']['source_cuts']
        req['scope'] = {'kind': 'selected_pair', 'left': member['scope'], 'right': member['scope']}
        scoped = comparison_context(reader, req)
        assert scoped['left']['scope_resolution']['member_node_ids'] == sorted(member['node_ids'])
        assert scoped['left']['graph']['boundary_edges']
        assert scoped['presentation']['recommended_mode'] == 'reliable_diff'
        full = deepcopy(req); full['scope'] = scoped['presentation']['full_pair_navigation']['scope']
        restored = comparison_context(reader, full)
        assert restored['request_echo']['source_cuts'] == result['request_echo']['source_cuts']
        assert restored['left']['graph'] == result['left']['graph']
