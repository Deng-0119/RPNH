"""Public-session comparison regressions; deterministic, offline, no sockets.

Synthetic owner fixtures use the canonical observer issuer and stored author
producer. These tests do not claim OS isolation or actual-browser acceptance.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid
from urllib.parse import urlencode

import pytest

from cpn.frontend.comparison_context import (ComparisonProvider, comparison_context, validate_request,
    validate_comparison_context, parse_query, ComparisonContextError, LIMITS, _key, _relations,
    _scope, _axes)
from cpn.frontend.server import handle_request
from cpn.rpnh.collaboration import ClosedModuleAuthor, author_material_schema_data
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits, ReadSessionRequest, ExplicitSources, SourceSelection
from cpn.rpnh.collaboration.registry_read_session import (RegistryReadHostBinding, ExistingReadAuthorityProvider,
    open_registry_session, open_readonly_source)
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.identities import new_id, TypedId
from cpn.rpnh.registry.observer_access import (observer_access_schema_data, ObserverReadScope,
    issue_observer_access, revoke_observer_access)
from cpn.rpnh.module import ModuleDeclaration
from test_native_net_operations import _registration, _simple_module


def world(path, source='source-a', *, declaration=None, registration=None, schema_data=None):
    a, at, ap = (schema_data or author_material_schema_data)(); b, bt, bp = observer_access_schema_data()
    types = {t.name: t for t in (*at, *bt)}
    catalog = SchemaCatalog(schemas={**a, **b}, types=tuple(types.values()), schema_paths={**ap, **bp})
    core = _RegistryCore(path, create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(('comparison-offline-fixture/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id=source, command_id='source')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    body = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Comparison fixture'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='principal')
    author = ClosedModuleAuthor(gateway, registration or _registration(), SourceQualifiedVersionRef(source, principal))
    module = declaration or _simple_module()
    ids = {path: 'element:' + uuid.uuid4().hex for path in _elements(module)}
    first = author.publish(module=module, element_ids=ids, command_id='author:first')
    return {'core': core, 'gateway': gateway, 'owner': owner, 'principal': principal, 'source': source,
            'author': author, 'module': module, 'ids': ids, 'first': first}


def reader(*worlds, materials=True):
    catalog = TypedReaderCatalog()
    fields = {kind: tuple(catalog.fields(kind)) for kind in catalog.entry_types}
    grants = {}
    for w in worlds:
        resources = tuple(SourceQualifiedResourceRef(w['source'], ResourceVersionRef(
            TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))
            for row in w['core'].event_store.object_rows_by_type('resource_version/v1'))
        grant = issue_observer_access(w['gateway'], principal_ref=w['principal'],
            scope=ObserverReadScope(fields, w.get('record_fields', fields), tuple(r for r in resources if materials(r)) if callable(materials) else resources if materials else ()), purpose='compare',
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='read-grant:' + str(uuid.uuid4()))
        w['grant'] = grant
        grants[('verified:comparison', w['source'], 'local')] = grant
    def resolve(source, path):
        w = next(w for w in worlds if w['source'] == source)
        return open_readonly_source(w['core'].run_dir, catalog=w['core'].catalog, binding_generation=w.get('generation', '1'))
    host = RegistryReadHostBinding('verified:comparison', resolve, ExistingReadAuthorityProvider(grants), catalog)
    request = ReadSessionRequest(ExplicitSources(tuple(SourceSelection(SourceQualifiedVersionRef(w['source'], w['owner'].task_ref), 'local') for w in worlds)), 'compare')
    return open_registry_session(request, host=host)


def chosen(revision):
    ref = revision.revision.revision_ref.to_dict()
    return {'kind': 'author_revision', 'source_id': ref['source_id'], 'revision_ref': ref}


def request(session, left, right):
    return {'schema_version': 'rpnh/comparison_request/v1', 'session_id': session.describe()['session_id'],
        'client_request_id': 'test-exact-pair', 'left': left, 'right': right, 'source_cuts': None,
        'scope': {'kind': 'full_pair', 'left': {'kind': 'full_net'}, 'right': {'kind': 'full_net'}},
        'axes': ['definition', 'configuration', 'materials', 'runtime'], 'view_preference': 'auto',
        'visual_pairs': [], 'limits': deepcopy(LIMITS)}


def counts(w):
    with w['core'].event_store.connect() as db:
        return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in ('objects', 'events', 'transactions', 'relations'))


def test_actual_author_public_session_pair_scopes_and_no_writes(tmp_path):
    w = world(tmp_path / 'source')
    session = reader(w)
    req = request(session, chosen(w['first']), chosen(w['first']))
    before = counts(w)
    result = comparison_context(session, req)
    assert result['presentation']['recommended_mode'] == 'reliable_diff'
    assert result['axes']['definition']['counts']['known_same'] > 0
    assert result['axes']['runtime']['coverage'] == 'not_provided'
    assert result['global_atomic_snapshot'] is False
    assert counts(w) == before
    for side in ('left', 'right'):
        req['scope'][side] = {'kind': 'nodes', 'node_ids': [result[side]['graph']['nodes'][0]['id']]}
    req['scope']['kind'] = 'selected_pair'
    req['source_cuts'] = result['request_echo']['source_cuts']
    scoped = comparison_context(session, req)
    assert all(len(scoped[side]['graph']['nodes']) == 1 for side in ('left', 'right'))
    assert scoped['presentation']['recommended_mode'] == 'reliable_diff'
    assert scoped['presentation']['full_pair_navigation']['source_cuts'] == req['source_cuts']
    assert all(scoped[side]['graph']['boundary_edges'] for side in ('left', 'right'))
    assert counts(w) == before


def test_unrelated_sources_same_names_no_identity_or_false_absence(tmp_path):
    a, b = world(tmp_path / 'a', 'source-a'), world(tmp_path / 'b', 'source-b')
    session = reader(a, b); req = request(session, chosen(a['first']), chosen(b['first']))
    result = comparison_context(session, req)
    assert result['presentation']['display_mode'] == 'full_pair'
    assert not result['mapping']['relations'] and not result['mapping']['explicit_presence_changes']
    assert result['axes']['definition']['counts']['known_changed'] == 0
    assert result['axes']['definition']['counts']['unknown'] > 0
    left_node, right_node = result['left']['graph']['nodes'][0], result['right']['graph']['nodes'][0]
    req['source_cuts'] = result['request_echo']['source_cuts']
    req['visual_pairs'] = [{'pair_id': 'local-only', **{side: [{'side': side, 'target_key': result[side]['graph']['target_key'],
        'subject_kind': 'node', 'subject_id': node['id'], 'occurrence_path': []}] for side, node in (('left', left_node), ('right', right_node))}}]
    visual = comparison_context(session, req)
    assert visual['presentation']['display_mode'] == 'full_pair'
    assert visual['mapping'] == result['mapping']


def test_scope_limit_and_nested_query_fields_fail_closed(tmp_path):
    w = world(tmp_path / 'a'); session = reader(w); req = request(session, chosen(w['first']), chosen(w['first']))
    for mutation in (lambda x: x.update(grant='fake'), lambda x: x['left'].update(latest=True),
                     lambda x: x['limits'].update(max_nodes_per_side=True), lambda x: x['axes'].append('definition')):
        bad = deepcopy(req); mutation(bad)
        with pytest.raises(ComparisonContextError): validate_request(bad)
    raw = json.dumps(req).replace('"client_request_id": "test-exact-pair"', '"client_request_id": "a", "client_request_id": "b"')
    with pytest.raises(ComparisonContextError): parse_query(urlencode({'request': raw}))
    req['limits']['max_nodes_per_side'] = 1
    with pytest.raises(ComparisonContextError, match='scope_limit'): comparison_context(session, req)


def test_whole_pair_rechecks_revoke_after_read_and_never_serves_raw(tmp_path, monkeypatch):
    w = world(tmp_path / 'a'); session = reader(w); provider = ComparisonProvider(session)
    req = request(session, chosen(w['first']), chosen(w['first']))
    assert handle_request(provider, 'GET', '/api/v1/net').status == 501
    original = session.read_public_projection; calls = []
    def read(**kwargs):
        result = original(**kwargs); calls.append(kwargs)
        if len(calls) == 2: revoke_observer_access(w['gateway'], grant_ref=w['grant'].grant_ref, command_id='revoke')
        return result
    monkeypatch.setattr(session, 'read_public_projection', read)
    response = handle_request(provider, 'GET', '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)}))
    assert response.status == 403 and json.loads(response.body) == {'error': 'access_changed'}


def test_response_extra_keys_counts_and_facts_rejected(tmp_path):
    w = world(tmp_path / 'a'); session = reader(w); req = request(session, chosen(w['first']), chosen(w['first']))
    result = comparison_context(session, req)
    for mutation in (lambda x: x.update(secret='bad'), lambda x: x['left']['graph']['nodes'][0].update(runtime={}),
                     lambda x: x['axes']['definition']['counts'].update(known_same=0),
                     lambda x: x['axes']['definition']['rows'][0]['left_fact'].update(value='wrong')):
        bad = deepcopy(result); mutation(bad)
        with pytest.raises(ComparisonContextError): validate_comparison_context(bad, req)


def test_real_declared_copy_correspondence_and_reversal(tmp_path):
    w = world(tmp_path / 'a')
    document = w['module'].to_dict()
    document['name'] = 'Copied'
    copied_ids = {key: 'element:' + uuid.uuid4().hex for key in w['ids']}
    copied = w['author'].publish(module=ModuleDeclaration.from_dict(document), element_ids=copied_ids,
        parent_ref=w['first'].revision.revision_ref, copy_sources={copied_ids[k]: v for k, v in w['ids'].items()}, command_id='copy')
    session = reader(w); req = request(session, chosen(w['first']), chosen(copied))
    result = comparison_context(session, req)
    assert result['presentation']['recommended_mode'] == 'reliable_diff'
    assert all(r['relation_kind'] == 'copied_from' and r['author_direction'] == 'left_to_right'
               and r['semantic_claim'] == 'author_correspondence' for r in result['mapping']['relations'])
    assert any(r['left'][0]['subject_kind'] == 'edge' for r in result['mapping']['relations'])
    req['left'], req['right'] = req['right'], req['left']
    reverse = comparison_context(session, req)
    assert all(r['author_direction'] == 'right_to_left' for r in reverse['mapping']['relations'])
    assert reverse['axes']['runtime']['coverage'] == 'not_provided'


def runtime_world(path):
    from cpn.rpnh.run import start_run, OwnerInput
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from test_native_net_operations import TEXT
    a, at, ap = author_material_schema_data(); b, bt, bp = observer_access_schema_data()
    catalog = SchemaCatalog(schemas={**a, **b}, types=tuple({t.name: t for t in (*at, *bt)}.values()), schema_paths={**ap, **bp})
    module = _simple_module(); task = OwnerInput(TEXT, canonical_json('offline task'), 'Task')
    owner = start_run(module, _registration(), run_dir=path, task_input=task, entry_inputs={'request': task},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']), (TEXT,), 3, 0, 3, 0),
        model_condition='offline-comparison', owner_statement='Offline comparison fixture', command_id='fixture:runtime', catalog=catalog)
    owner.schema_gateway.bind_source_identity(source_id='runtime-a', command_id='source')
    return {'core': owner._core, 'gateway': owner.schema_gateway, 'owner': owner.identity,
            'principal': owner.principal_ref, 'source': 'runtime-a', 'runtime_owner': owner}


def test_actual_runtime_targets_from_index_and_checkpoint_material_headers(tmp_path):
    w = runtime_world(tmp_path / 'run'); session = reader(w); provider = ComparisonProvider(session)
    before = counts(w); page = provider.comparison_selection()
    checkpoints = [item['target'] for item in page['targets'] if item['target']['kind'] == 'checkpoint']
    nets = [item['target'] for item in page['targets'] if item['target']['kind'] == 'net_instance']
    assert checkpoints and nets
    req = request(session, checkpoints[0], checkpoints[0]); req['source_cuts'] = page['source_cuts']
    result = comparison_context(session, req)
    assert result['presentation']['recommended_mode'] == 'reliable_diff'
    assert result['axes']['runtime']['coverage'] == 'partial'
    assert result['axes']['runtime']['counts']['known_same'] > 0
    assert any(r['left_fact']['state'] == 'provided' for r in result['axes']['materials']['rows'] if r['field_path'].endswith('.headers'))
    assert any(r['classification'] == 'unknown' for r in result['axes']['runtime']['rows'])
    assert counts(w) == before
    # Selecting a definition alone must not borrow the current checkpoint.
    req = request(session, nets[0], nets[0]); result = comparison_context(session, req)
    assert result['axes']['runtime']['coverage'] == 'not_provided'


def test_graph_and_material_headers_survive_initial_token_body_denial(tmp_path):
    w = runtime_world(tmp_path / 'run')
    bodies = {json.dumps(json.loads(row['metadata_json']).get('resource_ref'), sort_keys=True)
              for row in w['core'].event_store.object_rows_by_type('petri_token/v1')}
    session = reader(w, materials=lambda r: json.dumps(r.to_dict()['ref'], sort_keys=True) not in bodies)
    page = ComparisonProvider(session).comparison_selection()
    checkpoint = next(item['target'] for item in page['targets'] if item['target']['kind'] == 'checkpoint')
    result = comparison_context(session, request(session, checkpoint, checkpoint))
    rows = result['axes']['materials']['rows']
    headers = [r for r in rows if r['field_path'].endswith('.headers')]
    body = [r for r in rows if r['field_path'].endswith('.body_digest')]
    assert headers and all(r['left_fact']['state'] == 'provided' for r in headers)
    assert body and all(r['classification'] == 'unknown' and r['left_fact']['reason_code'] == 'not_disclosed' for r in body)
    assert result['presentation']['recommended_mode'] == 'reliable_diff'
    assert result['left']['hierarchy_navigation']['coverage'] == 'not_provided'


def test_real_retained_origin_can_correspond_to_two_distinct_copies(tmp_path):
    from test_collaboration_plain_merge import module, registration
    w = world(tmp_path / 'a', declaration=module(), registration=registration())
    document = w['module'].to_dict()
    for name in ('copied1', 'copied2'):
        component = deepcopy(document['components'][0]); component['name'] = name
        document['components'].append(component)
        document['entry'][name] = {'component': name, 'port': 'request'}
    changed = ModuleDeclaration.from_dict(document)
    ids = {path: w['ids'].get(path, 'element:' + uuid.uuid4().hex) for path in _elements(changed)}
    copies = {ids[path]: w['ids'][path.replace('/components/' + name, '/components/a')]
              for name in ('copied1', 'copied2') for path in ids if path.startswith('/components/' + name)}
    copied = w['author'].publish(module=changed, element_ids=ids, parent_ref=w['first'].revision.revision_ref,
        copy_sources=copies, command_id='two-copies')
    session = reader(w); result = comparison_context(session, request(session, chosen(w['first']), chosen(copied)))
    refs = [r for r in result['mapping']['relations'] if r['left'][0]['subject_kind'] == 'node' and r['left'][0]['subject_id'] == 'a.run']
    assert len(refs) == 3
    assert {r['relation_kind'] for r in refs} == {'retained_author_element', 'copied_from'}
    assert len({r['right'][0]['subject_id'] for r in refs}) == 3
    assert result['presentation']['recommended_mode'] == 'reliable_diff'


def test_actual_split_fusion_groups_preserve_edges_and_reversed_direction(tmp_path):
    from cpn.rpnh.collaboration import PlainModuleTransformAuthor
    from test_collaboration_plain_transform import catalog_data, split_request, fusion_request
    from test_collaboration_plain_merge import module, registration
    w = world(tmp_path / 'a', declaration=module(), registration=registration(), schema_data=catalog_data)
    author = PlainModuleTransformAuthor(w['gateway'], w['author'].registration, w['author'].producer)
    split = author.publish(**split_request(w['first']))
    fused = author.publish(**fusion_request(split))
    session = reader(w)
    result = comparison_context(session, request(session, chosen(w['first']), chosen(split)))
    groups = [r for r in result['mapping']['relations'] if r['relation_kind'] == 'split']
    assert groups and any(len(r['left']) < len(r['right']) for r in groups)
    assert any(any(ep['subject_kind'] == 'edge' for ep in r['left'] + r['right']) for r in groups)
    grouped_ids = {r['relation_id'] for r in groups if len(r['left']) > 1 or len(r['right']) > 1}
    assert all(r['classification'] == 'unknown' for r in result['axes']['definition']['rows'] if r['subject_relation_id'] in grouped_ids)
    reversed_result = comparison_context(session, request(session, chosen(split), chosen(w['first'])))
    assert any(r['relation_kind'] == 'fusion' and r['author_direction'] == 'right_to_left' for r in reversed_result['mapping']['relations'])
    fused_result = comparison_context(session, request(session, chosen(split), chosen(fused)))
    assert any(r['relation_kind'] == 'fusion' and r['author_direction'] == 'left_to_right' for r in fused_result['mapping']['relations'])
    assert fused_result['axes']['runtime']['coverage'] == 'not_provided'


def test_actual_binding_replacement_after_side_read_invalidates_whole_pair(tmp_path, monkeypatch):
    w = world(tmp_path / 'a'); session = reader(w)
    req = request(session, chosen(w['first']), chosen(w['first']))
    original = session.read_public_projection
    def shifted(**kwargs):
        result = original(**kwargs); w['generation'] = '2'; return result
    monkeypatch.setattr(session, 'read_public_projection', shifted)
    response = handle_request(ComparisonProvider(session), 'GET', '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)}))
    assert response.status == 403 and json.loads(response.body) == {'error': 'access_changed'}
    assert 'left' not in response.body.decode() and 'count' not in response.body.decode()


def test_actual_author_partition_proves_local_presence_without_matching_names(tmp_path):
    w = world(tmp_path / 'a')
    ids = dict(w['ids']); ids['/components/step/ports/request'] = 'element:' + uuid.uuid4().hex
    newer = w['author'].publish(module=w['module'], element_ids=ids, parent_ref=w['first'].revision.revision_ref, command_id='fresh-port-identity')
    session = reader(w); result = comparison_context(session, request(session, chosen(w['first']), chosen(newer)))
    changes = result['mapping']['explicit_presence_changes']
    assert {c['state'] for c in changes} == {'present_left_only', 'present_right_only'}
    assert any(c['subject']['subject_kind'] == 'node' and c['subject']['subject_id'] == 'step.request' for c in changes)
    rows = [r for r in result['axes']['definition']['rows'] if r['field_path'].startswith('definition.presence.')]
    assert rows and all(r['classification'] == 'known_changed' for r in rows)
    assert all(any(r[side]['state'] == 'absent_in_complete_scope' for side in ('left_fact', 'right_fact')) for r in rows)
    assert result['presentation']['recommended_mode'] == 'reliable_diff'


def test_unrequested_axes_do_not_become_same_or_trigger_optional_body_reader(tmp_path, monkeypatch):
    w = runtime_world(tmp_path / 'run'); session = reader(w)
    page = ComparisonProvider(session).comparison_selection()
    selected = next(item['target'] for item in page['targets'] if item['target']['kind'] == 'checkpoint')
    req = request(session, selected, selected); req['axes'] = ['definition']
    monkeypatch.setattr(session, 'read_material', lambda **kwargs: pytest.fail('unrequested optional body read'))
    result = comparison_context(session, req)
    for axis in ('configuration', 'materials', 'runtime'):
        assert result['axes'][axis]['coverage'] == 'not_requested'
        assert result['axes'][axis]['rows'] == []
        assert result['axes'][axis]['counts']['loaded_count'] is None


def test_pure_missing_public_field_is_unknown_not_null_or_absence(tmp_path):
    from cpn.frontend.comparison_context import _definition_rows
    w = world(tmp_path / 'a'); session = reader(w); req = request(session, chosen(w['first']), chosen(w['first']))
    result = comparison_context(session, req)
    sides = {side: deepcopy(result[side]) for side in ('left', 'right')}
    node = next(n for n in sides['left']['graph']['nodes'] if n['kind'] == 'place')
    node['capacity'] = None
    next(n for n in sides['right']['graph']['nodes'] if n['id'] == node['id']).pop('capacity')
    rows = _definition_rows(sides, result['mapping'], result['axes']['definition']['scope_key'])
    row = next(r for r in rows if r['field_path'] == 'definition.capacity' and r['right_fact']['state'] == 'unknown')
    assert row['left_fact']['value'] is None and row['classification'] == 'unknown'


def test_unsupported_many_to_many_projection_stays_a_group_and_does_not_raise_mode(tmp_path):
    w = world(tmp_path / 'a')
    newer = w['author'].publish(module=w['module'], element_ids=w['ids'], parent_ref=w['first'].revision.revision_ref, command_id='next')
    session = reader(w); req = request(session, chosen(w['first']), chosen(newer)); baseline = comparison_context(session, req)
    cut = session.validate_cut(baseline['request_echo']['source_cuts'][w['source']])
    projections = {side: session.read_public_projection(entry_ref=SourceQualifiedVersionRef.from_dict(req[side]['revision_ref'], catalog=w['core'].catalog), at_cut=cut)['record'] for side in ('left', 'right')}
    # Pure support-matrix mutation, not an accepted author producer or grant.
    ports = [row['element_id'] for row in projections['left']['lowering'] if row['kind'] == 'port']
    evidence = projections['right']['mapping_groups'][0]['evidence_refs']
    projections['right']['mapping_groups'] = [{'source_revision_ref': req['left']['revision_ref'], 'target_revision_ref': req['right']['revision_ref'],
        'relation_kind': 'many_to_many', 'source_element_ids': ports, 'target_element_ids': ports,
        'semantic_claim': 'author_correspondence', 'evidence_refs': evidence}]
    result = _relations({side: baseline[side] for side in ('left', 'right')}, projections,
                        {s['source_state']['source_id']: s['source_state'] for s in baseline['sources']}, LIMITS)
    assert result['coverage'] == 'none'
    assert len(result['relations']) == 1 and result['relations'][0]['validation'] == 'unsupported'
    assert len(result['relations'][0]['left']) == len(result['relations'][0]['right']) == 2


def test_pure_same_resource_ref_never_establishes_token_occurrence_identity(tmp_path):
    w = runtime_world(tmp_path / 'run'); session = reader(w); page = ComparisonProvider(session).comparison_selection()
    selected = next(item['target'] for item in page['targets'] if item['target']['kind'] == 'checkpoint')
    req = request(session, selected, selected); baseline = comparison_context(session, req)
    cut = session.validate_cut(baseline['request_echo']['source_cuts'][w['source']])
    projection = session.read_public_projection(entry_ref=SourceQualifiedVersionRef.from_dict(selected['checkpoint_ref'], catalog=w['core'].catalog), at_cut=cut)['record']
    left, right = deepcopy(projection), deepcopy(projection)
    # Pure fact comparator mutation: keep the material but change occurrence.
    assert right['runtime']['tokens']
    for token in right['runtime']['tokens']:
        token['token_ref']['ref']['logical_id'] = str(new_id('petri_token'))
        token['token_ref']['ref']['version_id'] = str(new_id('petri_token_version'))
    axes = _axes({side: baseline[side] for side in ('left', 'right')}, {'left': left, 'right': right},
                 baseline['mapping'], ['runtime'], {'left': {}, 'right': {}}, LIMITS)
    rows = [row for row in axes['runtime']['rows'] if row['field_path'].startswith('runtime.token.')]
    assert rows and all(row['classification'] == 'unknown' for row in rows)


def test_forged_or_foreign_cut_handle_is_rejected_and_full_limit_never_returns_old_scope(tmp_path):
    w = world(tmp_path / 'a'); session = reader(w); req = request(session, chosen(w['first']), chosen(w['first']))
    req['scope'] = {'kind': 'selected_pair', 'left': {'kind': 'nodes', 'node_ids': ['step.run']}, 'right': {'kind': 'nodes', 'node_ids': ['step.run']}}
    scoped = comparison_context(session, req)
    req['source_cuts'] = deepcopy(scoped['request_echo']['source_cuts'])
    req['source_cuts'][w['source']]['head']['ordinal'] += 1
    with pytest.raises(ComparisonContextError, match='stale_observation'): comparison_context(session, req)
    req['source_cuts'] = scoped['request_echo']['source_cuts']; req['scope'] = scoped['presentation']['full_pair_navigation']['scope']
    req['limits']['max_nodes_per_side'] = 1
    response = handle_request(ComparisonProvider(session), 'GET', '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)}))
    assert response.status == 413 and json.loads(response.body) == {'error': 'scope_limit'}


def test_initial_pn_index_visible_but_record_denied_clears_complete_pair(tmp_path, monkeypatch):
    """V-A06: actual PN index visibility never confers public graph payload access."""
    left = world(tmp_path / 'left', 'source-left')
    right = runtime_world(tmp_path / 'right')
    right['record_fields'] = {}
    session = reader(left, right); provider = ComparisonProvider(session)
    before = (counts(left), counts(right))
    page = provider.comparison_selection(); targets = list(page['targets'])
    cuts = page['source_cuts']
    while page['next_cursor']:
        page = provider.comparison_selection(cursor=page['next_cursor']); targets.extend(page['targets'])
        assert page['source_cuts'] == cuts
    selected = next(item['target'] for item in targets if item['target']['source_id'] == right['source'] and item['target']['kind'] == 'net_instance')
    assert selected['net_ref']['ref']['entity_type'] == 'net_instance/v1'
    assert all(item['read_state'] == 'not_read' for item in targets)
    calls = []; original = session.read_public_projection
    def actual_read(**kwargs):
        calls.append(kwargs['entry_ref'].source_id)
        return original(**kwargs)
    monkeypatch.setattr(session, 'read_public_projection', actual_read)
    req = request(session, chosen(left['first']), selected); req['source_cuts'] = cuts
    response = handle_request(provider, 'GET', '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)}))
    assert calls == [left['source'], right['source']]
    assert response.status == 422 and json.loads(response.body) == {'error': 'projection_unavailable'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert not any(name in response.body.decode() for name in ('left', 'right', 'nodes', 'counts', 'graph'))
    assert (counts(left), counts(right)) == before
    # The denied record did not invalidate independent, still-authorized index
    # visibility or manufacture an empty public graph.
    page = provider.comparison_selection(); available = list(page['targets'])
    while page['next_cursor']:
        page = provider.comparison_selection(cursor=page['next_cursor']); available.extend(page['targets'])
    assert any(item['target'] == selected for item in available)


def test_third_selected_source_observation_revocation_is_part_of_final_pair_guard(tmp_path, monkeypatch):
    """Three actual sources, without inventing a cross-source evidence producer.

    This covers the public third SourceCut observation guard. Supported current
    author/material producers keep their proof closure local to a source, so it
    does not claim an actual foreign mapping/material body was read.
    """
    left, right, extra = (world(tmp_path / name, name) for name in ('source-left', 'source-right', 'source-extra'))
    session = reader(left, right, extra); provider = ComparisonProvider(session)
    req = request(session, chosen(left['first']), chosen(right['first']))
    req['source_cuts'] = {w['source']: session.capture_cut(w['source']).to_dict() for w in (left, right, extra)}
    observed = []; observe = session.source_state
    def public_observation(*args, **kwargs):
        value = observe(*args, **kwargs); observed.append(value['source_id']); return value
    monkeypatch.setattr(session, 'source_state', public_observation)
    reads = []; read = session.read_public_projection
    def actual_read(**kwargs):
        result = read(**kwargs); reads.append(kwargs['entry_ref'].source_id)
        if len(reads) == 2:
            revoke_observer_access(extra['gateway'], grant_ref=extra['grant'].grant_ref, command_id='revoke-third-observation')
        return result
    monkeypatch.setattr(session, 'read_public_projection', actual_read)
    before = (counts(left), counts(right))
    response = handle_request(provider, 'GET', '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)}))
    assert extra['source'] in observed and reads == [left['source'], right['source']]
    assert response.status == 403 and json.loads(response.body) == {'error': 'access_changed'}
    assert not any(name in response.body.decode() for name in ('left', 'right', 'nodes', 'counts', 'graph'))
    assert (counts(left), counts(right)) == before



@pytest.fixture
def policy_session():
    """Public-session double for policy tests; no new Registry or business run."""
    from pathlib import Path
    from cpn.rpnh.collaboration.registry_read_contracts import SourceCut
    fixture = json.loads((Path(__file__).resolve().parents[1] / 'frontend/net-viewer/tests/comparison-context-fixture.json').read_text())
    selected = fixture['request']['left']
    state = fixture['context']['sources'][0]['source_state']
    class Session:
        limits = ReadLimits()
        def describe(self):
            return {'session_id': fixture['request']['session_id'], 'effective_limits': self.limits.to_dict(),
                    'sources': [{'source_id': selected['source_id'], 'capabilities': {'index_fields': {'collaboration_net_revision/v1': ['name']}}}]}
        def capture_cut(self, source):
            return SourceCut.from_dict(state['cut'])
        def source_state(self, cut): return deepcopy(state)
        def final_recheck(self, *args): pass
        def query_index(self, *args, **kwargs): return {'entries': [], 'continuation': None}
        def read_public_projection(self, *, entry_ref, at_cut):
            return {'status': 'ok', 'entry_ref': entry_ref.to_dict(), 'source_cut': at_cut.to_dict(),
                    'record': {'schema_version': 'rpnh/public_pn_projection/v1', 'target_ref': entry_ref.to_dict(), 'runtime': {'coverage': 'not_provided'},
                               'nodes': deepcopy(fixture['context']['left']['graph']['nodes']),
                               'edges': deepcopy(fixture['context']['left']['graph']['edges'])}}
    return Session(), selected


@pytest.mark.parametrize('key', LIMITS)
def test_host_budget_above_default_python_http_and_client_escalation(policy_session, key):
    session, selected = policy_session
    session.limits = ReadLimits(max_response_bytes=32_000_000)
    policy = {key: LIMITS[key] + 1}
    provider = ComparisonProvider(session, host_limits=policy)
    page = provider.comparison_selection()
    assert page['limits'][key] == LIMITS[key] + 1
    assert set(page['limits']) == set(LIMITS)
    req = request(session, selected, selected)
    req['limits'] = page['limits']
    assert parse_query(urlencode({'request': json.dumps(req)}), host_limits=policy) == req
    result = comparison_context(session, req, host_limits=policy)
    assert validate_comparison_context(result, req, host_limits=policy) == result
    url = '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)})
    assert handle_request(provider, 'GET', url).status == 200
    assert handle_request(provider, 'HEAD', url).status == 200
    req['limits'][key] += 1
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        comparison_context(session, req, host_limits=policy)
    response = handle_request(provider, 'GET', '/api/v2/comparison-context?' + urlencode({'request': json.dumps(req)}))
    assert response.status == 413


@pytest.mark.parametrize('key', LIMITS)
def test_smaller_host_budget_advertised_and_enforced(policy_session, key):
    session, selected = policy_session
    policy = {key: 1}
    provider = ComparisonProvider(session, host_limits=policy)
    assert provider.comparison_selection()['limits'][key] == 1
    req = request(session, selected, selected)
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        provider.comparison_context(req)
    req['limits'][key] = 1
    validate_request(req, host_limits=policy)
    if key in {'max_nodes_per_side', 'max_edges_per_side', 'max_mapping_relations', 'max_field_rows', 'max_response_bytes'}:
        with pytest.raises(ComparisonContextError, match='scope_limit'):
            provider.comparison_context(req)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '2', None, 9007199254740992])
def test_invalid_host_budgets_rejected(value):
    from cpn.frontend.comparison_context import HOST_LIMITS
    for key in HOST_LIMITS:
        with pytest.raises(ValueError):
            ComparisonProvider(None, host_limits={key: value})
    with pytest.raises(ValueError): ComparisonProvider(None, host_limits={'unknown': 1})
    with pytest.raises(ValueError): ComparisonProvider(None, host_limits=[])


def test_underlying_session_ceiling_and_defaults_remain_effective(policy_session):
    session, selected = policy_session
    session.limits = ReadLimits(max_response_bytes=1024)
    provider = ComparisonProvider(session, host_limits={'max_response_bytes': 32_000_000})
    assert provider.comparison_selection()['limits']['max_response_bytes'] == 1024
    req = request(session, selected, selected)
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        provider.comparison_context(req)
    ordinary = session
    ordinary.limits = ReadLimits()
    req = request(ordinary, selected, selected)
    assert comparison_context(ordinary, req)['publication'] == 'complete_pair'
    assert ComparisonProvider(ordinary).comparison_selection()['limits']['max_response_bytes'] == 4 * 1024 * 1024


def test_large_graph_has_no_hidden_array_default_ceiling():
    nodes = [{'id': str(i), 'label': 'n', 'kind': 'place', 'category': 'place', 'hidden_by_default': False} for i in range(30001)]
    edges = [{'id': str(i), 'source': '0', 'target': '1', 'kind': 'arc', 'mode': 'read', 'weight': 1,
              'outcome': None, 'hidden_by_default': False} for i in range(30001)]
    projection = {'nodes': nodes, 'edges': edges}
    limits = {**LIMITS, 'max_nodes_per_side': 30001, 'max_edges_per_side': 30001}
    graph, _, _ = _scope(projection, {}, {'kind': 'full_net'}, limits)
    assert len(graph['nodes']) == len(graph['edges']) == 30001
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        _scope(projection, {}, {'kind': 'full_net'}, LIMITS)


def test_material_budget_above_default_smaller_and_session_ceiling():
    from cpn.frontend.comparison_context import _material_facts, _effective_host_limits
    from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
    resource = {'schema_version': 'rpnh/collaboration/source_resource_ref/v1', 'source_id': 'a',
                'ref': {'resource_id': 'resource:' + 'a' * 32, 'resource_version_id': 'resource_version:' + 'b' * 32}}
    class Cut:
        def to_dict(self): return {'cut': 'fixed'}
    class Session:
        def read_exact(self, **kwargs): return {'status': 'not_disclosed'}
        def read_material(self, **kwargs):
            self.received = kwargs['max_bytes']
            if self.received < 1048577: raise RegistryReadSessionError('MATERIAL_TOO_LARGE')
            return {'status': 'ok', 'resource_ref': resource, 'source_cut': Cut().to_dict(), 'byte_count': 1048577, 'sha256': 'a' * 64}
    session = Session()
    for requested, underlying, state in [(2097152, 4194304, 'provided'), (100, 4194304, 'unknown'), (2097152, 100, 'unknown'), (1048576, 4194304, 'unknown')]:
        policy = _effective_host_limits({'max_material_bytes': requested}, {'effective_limits': {'max_material_bytes': underlying}})
        budget = {'reads': 2, 'bytes': 4 * 1024 * 1024, 'cache': {}, 'max_material_bytes': policy['max_material_bytes']}
        facts = _material_facts(session, {'runtime': {'tokens': [{'resource_ref': resource}]}}, {'a': Cut()}, {}, budget)
        assert session.received == min(requested, underlying)
        assert next(iter(facts.values()))['body_digest']['state'] == state


@pytest.mark.parametrize('host_max,session_max,allowed', [(130, 130, 130), (1, 130, 1), (130, 1, 1)])
def test_selection_cursor_budget_can_exceed_128_and_respects_session(policy_session, monkeypatch, host_max, session_max, allowed):
    session, selected = policy_session
    session.limits = ReadLimits(max_cursors=session_max)
    monkeypatch.setattr(session, 'query_index', lambda *args, **kwargs: {'entries': [], 'continuation': 'next'})
    provider = ComparisonProvider(session, host_limits={'max_selection_cursors': host_max})
    cursor = None
    for _ in range(allowed):
        page = provider.comparison_selection(cursor=cursor)
        cursor = page['next_cursor']
        assert cursor is not None
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        provider.comparison_selection(cursor=cursor)


def test_request_byte_budget_is_host_only_and_configurable(policy_session):
    session, selected = policy_session
    req = request(session, selected, selected)
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        parse_query(urlencode({'request': json.dumps(req)}), host_limits={'max_request_bytes': 1})
    req['limits']['max_request_bytes'] = 2_000_000
    with pytest.raises(ComparisonContextError): validate_request(req)


def test_browser_validator_uses_request_budget_above_old_array_cap():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {normalizeComparisonContext} from './cpn/frontend/static/comparison-context.mjs';
const {request,context} = JSON.parse(readFileSync('./frontend/net-viewer/tests/comparison-context-fixture.json','utf8'));
const axis = context.axes.configuration;
const row = context.axes.definition.rows[0];
assert.ok(row);
axis.rows = Array.from({length:200001}, (_,i)=>({...row,evidence_refs:[],row_id:'row-'+i,field_path:'configuration.field-'+i,subject_relation_id:null,left_fact:{state:'unknown',reason_code:'not_provided'},right_fact:{state:'unknown',reason_code:'not_provided'},classification:'unknown'}));
axis.coverage='partial';
axis.counts={...axis.counts,loaded_count:axis.rows.length,total_count:null,known_same:0,known_changed:0,unknown:axis.rows.length,coverage:'partial'};
request.limits.max_field_rows=210000;
request.limits.max_response_bytes=256000000;
assert.equal((await normalizeComparisonContext(context,request)).axes.configuration.rows.length,200001);
request.limits.max_field_rows=200000;
await assert.rejects(normalizeComparisonContext(context,request));
console.log('browser resource budget checks passed');
"""
    result = subprocess.run(['node', '--max-old-space-size=700', '--input-type=module', '-e', script], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr



def test_comparison_large_post_loopback_and_pre_read_host_limit(policy_session, monkeypatch):
    """Actual HTTP, using a public-session double; creates no Registry."""
    from http.client import HTTPConnection
    from threading import Thread
    from cpn.frontend.server import _ProjectionHTTPServer
    from cpn.frontend.comparison_context import _json
    session, selected = policy_session
    req = request(session, selected, selected)
    endpoints = {side: [{'side': side, 'target_key': _key(selected), 'subject_kind': 'node',
                         'subject_id': 'step.run', 'occurrence_path': []}] for side in ('left', 'right')}
    req['visual_pairs'] = [{'pair_id': str(i), 'label': 'é' * 3500, **deepcopy(endpoints)} for i in range(10)]
    body = _json(req).encode('utf-8')
    assert len(body) > 65536
    provider = ComparisonProvider(session, host_limits={'max_request_bytes': len(body)})
    reads = []
    original = session.read_public_projection
    def read(**kwargs):
        reads.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(session, 'read_public_projection', read)
    with _ProjectionHTTPServer(provider, '127.0.0.1', 0, False) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
        thread.start()
        try:
            conn = HTTPConnection('127.0.0.1', server.bound_port, timeout=2)
            conn.request('POST', '/api/v2/comparison-context', body=body, headers={'Content-Type': 'application/json'})
            response = conn.getresponse(); wire = response.read()
            assert response.status == 200
            assert int(response.getheader('Content-Length')) == len(wire)
            assert wire == _json(json.loads(wire)).encode('utf-8')
            assert json.loads(wire)['request_echo']['visual_pairs'] == req['visual_pairs']
            conn.close()
            assert len(reads) == 2
            # Send headers only. A read-before-size-check would hang until timeout.
            conn = HTTPConnection('127.0.0.1', server.bound_port, timeout=2)
            conn.putrequest('POST', '/api/v2/comparison-context')
            conn.putheader('Content-Type', 'application/json')
            conn.putheader('Content-Length', str(len(body) + 1))
            conn.endheaders()
            response = conn.getresponse()
            assert response.status == 413 and json.loads(response.read()) == {'error': 'scope_limit'}
            conn.close()
            assert len(reads) == 2
            small = request(session, selected, selected)
            conn = HTTPConnection('127.0.0.1', server.bound_port, timeout=2)
            conn.request('GET', '/api/v2/comparison-context?' + urlencode({'request': _json(small)}))
            response = conn.getresponse()
            assert response.status == 200 and json.loads(response.read())['publication'] == 'complete_pair'
            conn.close()
            print('loopback accepted POST bytes:', len(body), '; over-budget headers-only rejection: 413; legacy GET: 200')
        finally:
            server.shutdown(); thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'POST'])
def test_comparison_exact_response_wire_budget(policy_session, method):
    from cpn.frontend.comparison_context import _json
    session, selected = policy_session
    req = request(session, selected, selected)
    req['client_request_id'] = '字节预算'
    wire = _json(comparison_context(session, req)).encode('utf-8')
    req['limits']['max_response_bytes'] = len(wire)
    provider = ComparisonProvider(session, host_limits={'max_response_bytes': len(wire)})
    def call():
        target = '/api/v2/comparison-context'
        if method != 'POST': target += '?' + urlencode({'request': _json(req)})
        return handle_request(provider, method, target, body=_json(req).encode('utf-8') if method == 'POST' else b'')
    response = call()
    assert response.status == 200
    assert int(response.headers['Content-Length']) == len(wire)
    assert response.body == (b'' if method == 'HEAD' else wire)
    req['limits']['max_response_bytes'] -= 1
    assert call().status == 413


def test_comparison_post_parser_closed_body_and_legacy_methods(policy_session):
    from cpn.frontend.comparison_context import _json, parse_body
    session, selected = policy_session
    req = request(session, selected, selected); raw = _json(req).encode('utf-8')
    assert parse_body(raw, host_limits={'max_request_bytes': len(raw)}) == req
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        parse_body(raw, host_limits={'max_request_bytes': len(raw) - 1})
    with pytest.raises(ComparisonContextError, match='scope_limit'):
        parse_body(raw + b' ', host_limits={'max_request_bytes': len(raw)})
    for invalid in (b'{"schema_version":1,"schema_version":2}', b'NaN', b'\xff', raw[:-1]):
        with pytest.raises(ComparisonContextError): parse_body(invalid)
    provider = ComparisonProvider(session)
    assert handle_request(provider, 'POST', '/api/v2/comparison-context?request=bad', body=raw).status == 400
    assert handle_request(provider, 'POST', '/api/v2/comparison-selection', body=raw).status == 405
    assert handle_request(provider, 'PUT', '/api/v2/comparison-context', body=raw).status == 405
    assert handle_request(provider, 'POST', '/api/v1/net', body=raw).status == 405


def test_comparison_frontend_large_post_options_reach_fetch():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {comparisonContextTransport} from './cpn/frontend/static/comparison-context.mjs';
import {checkpointApp,response} from './frontend/net-viewer/tests/checkpoint-test-harness.mjs';
const {request,context}=JSON.parse(readFileSync('./frontend/net-viewer/tests/comparison-context-fixture.json','utf8'));
assert.match(comparisonContextTransport(request).path,/comparison-context\?request=/);
assert.deepEqual(comparisonContextTransport(request).options,{});
const large=structuredClone(request), relation=context.mapping.relations[0];
large.visual_pairs=Array.from({length:20},(_,i)=>({pair_id:String(i),label:'x'.repeat(4000),left:relation.left,right:relation.right}));
const transport=comparisonContextTransport(large);
assert.equal(transport.path,'/api/v2/comparison-context');assert.equal(transport.options.method,'POST');
assert.ok(new TextEncoder().encode(transport.options.body).length>65536);
assert.deepEqual(JSON.parse(transport.options.body),large);
const calls=[];
const app=checkpointApp(async(url,options)=>{
    calls.push({url,options});
    return url.startsWith('/api/v2/comparison-selection')?response({schema_version:'rpnh/comparison_selection/v1',session_id:request.session_id,targets:[],source_cuts:{},next_cursor:null,limits:request.limits}):response({error:'scope_limit'},413);
});
app.api.crossComparison.open();await new Promise(resolve=>setTimeout(resolve,0));
const state=app.api.crossComparison.state;
state.selection={left:large.left,right:large.right};state.visualPairs=large.visual_pairs;
app.api.crossComparison.refreshLanguage();
const button=app.document.getElementById('comparison-context-panel').querySelectorAll('button').find(b=>b.textContent==='Read comparison');
assert.ok(button);button.onclick();await new Promise(resolve=>setTimeout(resolve,0));
const actual=calls.at(-1);assert.equal(actual.url,'/api/v2/comparison-context');
assert.equal(actual.options.method,'POST');assert.equal(actual.options.headers['Content-Type'],'application/json');
assert.equal(actual.options.cache,'no-store');assert.ok(actual.options.signal);
assert.deepEqual(JSON.parse(actual.options.body).visual_pairs,large.visual_pairs);
assert.equal(state.error,'scope_limit');app.api.crossComparison.close();
console.log('actual app request helper forwarded bounded Comparison POST options');
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
