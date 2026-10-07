"""Independent ordinary public comparison integration regressions.

Synthetic owner fixtures create inputs; all reading goes through the canonical
observer issuer and delivered read-only public opener. No sockets or providers.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid
import pytest

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


def native_author_world(path):
    """Synthetic regression owner only; never business acceptance evidence."""
    from types import SimpleNamespace
    from test_registry_typed_exchange import world as owner_world
    from test_native_plugins import catalog
    from cpn.plugins.runtime import plugin_registration, build_plugin_module
    from cpn.rpnh.collaboration import ClosedModuleAuthor
    core, original = owner_world(path, 'native-regression', observer=True)
    author = ClosedModuleAuthor(original.gateway, plugin_registration(catalog()), original.producer)
    module = build_plugin_module(catalog(), 'test_plugin/add')
    from cpn.rpnh.collaboration.materials import _elements
    ids = {locator: 'element:' + uuid.uuid4().hex for locator in _elements(module)}
    first = author.publish(module=module, element_ids=ids, command_id='native:first')
    return {'core': core, 'gateway': author.gateway, 'principal': author.producer.ref,
        'source': 'native-regression', 'owner': SimpleNamespace(task_ref=author.gateway._task_ref),
        'author': author, 'first': first, 'module': module, 'ids': ids}


def native_copy(w, module=None, *, lineage_only=False, rename=False, author=None):
    module = module or w['module']
    from cpn.rpnh.collaboration.materials import _elements
    ids = {locator: 'element:' + uuid.uuid4().hex for locator in _elements(module)}
    def prior(locator):
        return locator.replace('/renamed', '/plugin').replace('/execute', '/run') if rename else locator
    copies = {} if lineage_only else {identity: w['ids'][prior(locator)] for locator, identity in ids.items()}
    return (author or w['author']).publish(module=module, element_ids=ids,
        copy_sources=copies, parent_ref=w['first'].revision.revision_ref, command_id='native:copy')


def test_native_generated_correspondence_verifies_exact_recipe_without_lowering(tmp_path, monkeypatch):
    w = native_author_world(tmp_path / 'native')
    copied = native_copy(w)
    def forbidden(*args, **kwargs):
        raise AssertionError('public reader must not invoke compiler or lowerer')
    monkeypatch.setattr('cpn.rpnh.compiler.compile_module', forbidden)
    monkeypatch.setattr('cpn.plugins.host.lower_plugin_operation', forbidden)
    with public_reader(w) as reader:
        before = counts(w)
        projection = reader.read_public_projection(entry_ref=copied.revision.revision_ref,
            at_cut=reader.capture_cut(w['source']))['record']
        rows = projection['generated_correspondences']
        assert {row['subject_kind'] for row in rows} == {'node', 'edge'}
        assert all(row['semantic_claim'] == 'author_correspondence' and row['relation_kind'] == 'copied_from' for row in rows)
        assert all(row['source_revision_ref'] == w['first'].revision.revision_ref.to_dict() for row in rows)
        assert all(len(row['evidence_refs']) == 4 for row in rows)
        assert counts(w) == before


@pytest.mark.parametrize('change', ['config', 'operation', 'plugin', 'lineage_only'])
def test_native_generated_correspondence_never_guesses_changed_semantics(tmp_path, change):
    from dataclasses import replace
    from test_native_plugins import catalog, fixture_plugin
    from cpn.plugins import PluginCatalog, BoundPlugin
    from cpn.plugins.runtime import plugin_registration, build_plugin_module
    from cpn.rpnh.collaboration import ClosedModuleAuthor
    w = native_author_world(tmp_path / change)
    selected = catalog()
    selector = 'test_plugin/add'
    if change == 'config':
        selected = PluginCatalog((BoundPlugin(fixture_plugin(), {'offset': 3}),))
    elif change == 'plugin':
        selected = PluginCatalog((BoundPlugin(replace(fixture_plugin(), version='2.0'), {'offset': 2}),))
    elif change == 'operation':
        selector = 'test_plugin/wrong'
    registration = plugin_registration(selected)
    known = {(row['kind'], row['key']) for row in registration.declarations()}
    # Both immutable versions must remain available to validate the parent.
    for row in w['author'].registration.declarations():
        kind, key = row['kind'], row['key']
        if (kind, key) in known:
            assert registration.declaration(kind, key) == row
        elif kind == 'schema':
            registration.register_schema(key, row['schema'])
        else:
            getattr(registration, 'register_' + kind)(key, w['author'].registration.resolve(kind, key),
                identity=row['identity'], contracts=row['contracts'])
    author = ClosedModuleAuthor(w['gateway'], registration, w['author'].producer)
    copied = native_copy(w, build_plugin_module(selected, selector), author=author,
        lineage_only=change == 'lineage_only')
    with public_reader(w) as reader:
        projection = reader.read_public_projection(entry_ref=copied.revision.revision_ref,
            at_cut=reader.capture_cut(w['source']))['record']
        assert projection['generated_correspondences'] == []
        pair = comparison_context(reader, request(reader, chosen(w['first']), chosen(copied)))
        assert pair['presentation']['recommended_mode'] != 'reliable_diff'
        assert any(ep['subject_id'] == 'plugin.capability' for ep in pair['mapping']['unmapped']['right']['subjects'])
        assert not any(evidence['verification_contract'] == 'rpnh/native_capability_derivation/v1'
            for relation in pair['mapping']['relations'] for evidence in relation['evidence'])
        assert any(row['classification'] == 'unknown' and 'plugin.capability' in row['field_path']
            for row in pair['axes']['definition']['rows'])


def test_native_generated_correspondence_uses_declarations_across_renaming(tmp_path):
    w = native_author_world(tmp_path / 'rename')
    document = w['module'].to_dict()
    document['components'][0]['name'] = 'renamed'
    document['components'][0]['operations'][0]['name'] = 'execute'
    for endpoint in (*document['entry'].values(), *document['exit'].values(), document['terminal']['source']):
        endpoint['component'] = 'renamed'
    document['terminal']['operation'] = 'execute'
    copied = native_copy(w, ModuleDeclaration.from_dict(document), rename=True)
    with public_reader(w) as reader:
        projection = reader.read_public_projection(entry_ref=copied.revision.revision_ref,
            at_cut=reader.capture_cut(w['source']))['record']
        rows = projection['generated_correspondences']
        assert len(rows) == 2
        assert all(row['source_subject_id'] != row['target_subject_id'] for row in rows)


def test_native_parent_projection_denial_keeps_generated_subjects_unknown(tmp_path):
    w = native_author_world(tmp_path / 'denied')
    copied = native_copy(w)
    with public_reader(w) as reader:
        original = reader.read_public_projection(entry_ref=w['first'].revision.revision_ref,
            at_cut=reader.capture_cut(w['source']))['record']
    with public_reader(w, material_filter=lambda ref: ref.to_dict() != original['projection_ref']) as reader:
        projection = reader.read_public_projection(entry_ref=copied.revision.revision_ref,
            at_cut=reader.capture_cut(w['source']))['record']
        assert projection['mapping_coverage'] == 'verified'
        assert projection['generated_correspondences'] == []


@pytest.mark.parametrize('reverse', [False, True], ids=['direct', 'reverse'])
def test_native_consumer_copy_closes_all_subjects_with_author_direction(tmp_path, reverse):
    w = native_author_world(tmp_path / 'consumer')
    copied = native_copy(w)
    left, right = (copied, w['first']) if reverse else (w['first'], copied)
    with public_reader(w) as reader:
        before = counts(w)
        pair = comparison_context(reader, request(reader, chosen(left), chosen(right)))
        assert pair['presentation']['recommended_mode'] == 'reliable_diff'
        assert pair['mapping']['coverage'] == 'complete_in_declared_scope'
        relations = pair['mapping']['relations']
        assert len(relations) == 7
        assert all(row['validation'] == 'verified' and row['semantic_claim'] == 'author_correspondence'
            and row['relation_kind'] == 'copied_from'
            and row['author_direction'] == ('right_to_left' if reverse else 'left_to_right') for row in relations)
        generated = [row for row in relations if any(e['verification_contract'] ==
            'rpnh/native_capability_derivation/v1' for e in row['evidence'])]
        assert len(generated) == 2
        for side in ('left', 'right'):
            assert not pair['mapping']['unmapped'][side]['subjects']
            assert {(row[side][0]['subject_kind'], row[side][0]['subject_id']) for row in generated} == {
                ('node', 'plugin.capability'), ('edge', 'edge:arc:plugin.capability:plugin.run:read:-:1')}
        assert pair['axes']['definition']['counts']['unknown'] == 0
        # The configuration axis compares the newly published exact refs;
        # closed PN correspondence does not make these refs equal or claim
        # equality of their underlying configuration bodies.
        configuration = pair['axes']['configuration']
        assert configuration['coverage'] == 'partial'
        assert configuration['counts']['known_same'] == 0
        assert {row['field_path'] for row in configuration['rows']} == {
            'configuration.host_requirements_ref', 'configuration.declared_configuration_ref'}
        assert all(row['classification'] == 'known_changed' for row in configuration['rows'])
        assert pair['axes']['runtime']['coverage'] == 'not_provided'
        assert counts(w) == before


def test_native_consumer_copied_revision_self_comparison_is_exact_identity(tmp_path):
    w = native_author_world(tmp_path / 'self')
    copied = native_copy(w)
    with public_reader(w) as reader:
        pair = comparison_context(reader, request(reader, chosen(copied), chosen(copied)))
        assert pair['presentation']['recommended_mode'] == 'reliable_diff'
        relations = pair['mapping']['relations']
        assert len(relations) == 7
        assert all(row['relation_kind'] == 'same_exact_subject' and row['semantic_claim'] == 'identity'
            and row['author_direction'] is None for row in relations)
        assert not any(e['verification_contract'] == 'rpnh/native_capability_derivation/v1'
            for row in relations for e in row['evidence'])
        assert all(not pair['mapping']['unmapped'][side]['subjects'] for side in ('left', 'right'))


def test_native_consumer_capability_scope_returns_full_pair_at_original_cut(tmp_path):
    w = native_author_world(tmp_path / 'scope')
    copied = native_copy(w)
    with public_reader(w) as reader:
        req = request(reader, chosen(w['first']), chosen(copied))
        full = comparison_context(reader, req)
        scoped_request = deepcopy(req)
        scoped_request['source_cuts'] = full['request_echo']['source_cuts']
        scoped_request['scope'] = {'kind': 'selected_pair', **{side: {
            'kind': 'nodes', 'node_ids': ['plugin.capability']} for side in ('left', 'right')}}
        scoped = comparison_context(reader, scoped_request)
        assert scoped['presentation']['recommended_mode'] == 'reliable_diff'
        assert len(scoped['mapping']['relations']) == 1
        relation = scoped['mapping']['relations'][0]
        assert relation['semantic_claim'] == 'author_correspondence'
        for side in ('left', 'right'):
            assert len(relation[side]) == 1 and relation[side][0]['subject_kind'] == 'node'
            assert relation[side][0]['subject_id'] == 'plugin.capability'
            assert scoped[side]['scope_resolution']['member_node_ids'] == ['plugin.capability']
            assert scoped[side]['graph']['edges'] == []
            assert len(scoped[side]['graph']['boundary_edges']) == 1
            assert not scoped['mapping']['unmapped'][side]['subjects']
        # The full return must retain the observation even after a genuine
        # authorized append. It must not silently capture the newer head.
        w['author'].publish(module=w['module'], element_ids=w['ids'],
            parent_ref=w['first'].revision.revision_ref, command_id='native:after-scope')
        navigation = scoped['presentation']['full_pair_navigation']
        restored_request = deepcopy(req)
        restored_request['source_cuts'] = navigation['source_cuts']
        restored_request['scope'] = navigation['scope']
        restored = comparison_context(reader, restored_request)
        assert restored['request_echo']['source_cuts'] == full['request_echo']['source_cuts']
        assert restored['presentation']['recommended_mode'] == 'reliable_diff'
        assert restored['mapping'] == full['mapping']
        for side in ('left', 'right'):
            assert restored[side]['selected_target'] == full[side]['selected_target']
            assert restored[side]['graph'] == full[side]['graph']
