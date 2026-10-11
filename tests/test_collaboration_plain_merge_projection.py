"""Exact M public projection, immutable prefix recovery and pure observation."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_result import fixture
from test_collaboration_plain_merge_resolution import conflicting, choices_for
from test_registry_typed_exchange import snapshot as _snapshot
from cpn.rpnh.collaboration import PlainModuleMergeAuthor, plain_merge_result_schema_data
from cpn.rpnh.collaboration import plain_merge_result as implementation
from cpn.rpnh.collaboration import public_projections as public
from cpn.rpnh.collaboration._plain_merge_projection import PRODUCER, STORED_SCHEMA, verify
from cpn.rpnh.collaboration.registry_typed_readers import TypedReadError, payload_at, wire_ref
from cpn.rpnh.registry.event_store import StaleWriterError
from cpn.rpnh.registry.schema_catalog import canonical_json, canonical_text


def snapshot(core, source, denied=()):
    value = _snapshot(core, source, denied)
    with core.event_store.connect() as db:
        value.relations = tuple(dict(row) for row in db.execute('SELECT * FROM relations'))
    return value


def merged(fixture):
    inputs, author = fixture
    analysis, left, right = conflicting(inputs)
    args = {'analysis_ref': analysis.analysis_ref, 'choices': choices_for(analysis, 'local'), 'command_id': 'public-M'}
    return inputs, author, author.publish(**args), left, right, args


def stored(core, value, binding):
    ref = public.projection_resource_ref(core, value.revision.revision_ref, binding=binding)
    raw = core.object_store.path_for_version(ref.ref.resource_version_id).read_bytes()
    return ref, raw, json.loads(raw)


def test_actual_merge_reads_without_host_compilation_queries_or_writes(fixture, monkeypatch):
    inputs, author, value, left, right, args = merged(fixture)
    core = inputs[0]
    ref, raw, document = stored(core, value, author.binding)
    assert document['schema_version'] == STORED_SCHEMA
    snap = snapshot(core, 'plain-source')
    before = support.counts(core)
    with monkeypatch.context() as patch:
        for path in ('cpn.rpnh.compiler.compile_module', 'cpn.rpnh.collaboration.materials.compile_module',
                'cpn.rpnh.collaboration.materials.validate_closed_revision',
                'cpn.rpnh.collaboration.plain_merge_result.compile_module',
                'cpn.rpnh.collaboration.plain_merge_result._validate_merge_at'):
            patch.setattr(path, support.forbidden)
        patch.setattr(author.registration, 'resolve', support.forbidden)
        patch.setattr(type(core.event_store), 'connect', support.forbidden)
        patch.setattr(core, 'begin', support.forbidden)
        graph = public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    assert graph['producer_contract'] == PRODUCER
    assert graph['target_ref'] == graph['author_revision_ref'] == value.revision.revision_ref.to_dict()
    assert graph['nodes'] and graph['edges']
    assert graph['mapping_groups'] == [] and graph['mapping_closure'] is None
    assert graph['mapping_coverage'] == 'not_provided' and graph['generated_correspondences'] == []
    assert graph['runtime'] == {'coverage': 'not_provided'}
    assert 'merge_proof' not in graph and 'compiled' not in graph
    assert graph['configuration'] == {'host_requirements_ref': value.revision.host_requirements_ref.to_dict(),
        'declared_configuration_ref': value.revision.definition_ref.to_dict()}
    mapping = public.read_public_mapping(core, left.revision.revision_ref, value.revision.revision_ref, snapshot=snap)
    assert mapping['groups'] == mapping['generated_correspondences'] == []
    assert support.counts(core) == before
    assert author.publish(**args).revision == value.revision
    assert stored(core, value, author.binding)[1] == raw
    assert support.counts(core) == before


def test_required_merge_proof_denials_revocation_cut_and_limits_never_fallback(fixture, monkeypatch):
    inputs, author, value, left, right, _ = merged(fixture)
    core = inputs[0]
    ref, raw, document = stored(core, value, author.binding)
    monkeypatch.setattr(public, '_read_topology_only', support.forbidden)
    snap = snapshot(core, 'plain-source', {canonical_json(value.revision.revision_ref.to_dict())})
    with pytest.raises(TypedReadError, match='NOT_DISCLOSED'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    # Every original material body is required, even an ancestor's schema resource.
    for pin in document['dependencies']:
        if wire_ref(pin['ref'])[1]['entity_type'] not in ('resource_version/v1', 'collaboration_net_revision/v1'):
            continue
        snap = snapshot(core, 'plain-source', {canonical_json(pin['ref'])})
        with pytest.raises(TypedReadError) as error:
            public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
        assert error.value.code in {'MATERIAL_ACCESS_NOT_GRANTED', 'NOT_DISCLOSED'}
    snap = snapshot(core, 'plain-source')
    snap.check_authorized = lambda *args: (_ for _ in ()).throw(TypedReadError('ACCESS_CHANGED'))
    with pytest.raises(TypedReadError, match='ACCESS_CHANGED'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    snap = snapshot(core, 'plain-source')
    snap.objects.pop(str(left.revision.revision_ref.ref.version_id))
    with pytest.raises(TypedReadError, match='NOT_PRESENT_AT_CUT'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    snap = snapshot(core, 'plain-source')
    snap.max_material_bytes = len(raw) + 1
    with pytest.raises(TypedReadError, match='LIMIT_EXCEEDED'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)


def test_historical_completed_merge_is_not_backfilled_or_repaired(fixture, monkeypatch):
    inputs, author = fixture
    analysis, left, right = conflicting(inputs)
    args = {'analysis_ref': analysis.analysis_ref, 'choices': choices_for(analysis, 'local'), 'command_id': 'historical-M'}
    with monkeypatch.context() as patch:
        patch.setattr(public, 'publish_public_projection', lambda *a, **k: None)
        value = author.publish(**args)
    core = inputs[0]
    before = support.counts(core)
    assert author.publish(**args).revision == value.revision
    with pytest.raises(TypedReadError, match='PROJECTION_UNAVAILABLE'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snapshot(core, 'plain-source'))
    assert support.counts(core) == before


def test_every_pinned_original_body_same_size_reordering_is_rejected(fixture):
    inputs, author, value, _, _, _ = merged(fixture)
    core = inputs[0]
    _, _, document = stored(core, value, author.binding)
    snap = snapshot(core, 'plain-source')
    before = support.counts(core)
    for pin in document['dependencies']:
        _, local = wire_ref(pin['ref'])
        from cpn.rpnh.registry.identities import TypedId
        path = core.object_store.path_for_version(TypedId.parse(local['version_id']))
        raw = path.read_bytes()
        body = json.loads(raw)
        changed = json.dumps(dict(reversed(list(body.items()))), ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()
        if changed == raw:
            continue
        assert len(changed) == len(raw) and canonical_json(json.loads(changed)) == raw
        try:
            path.write_bytes(changed)
            with pytest.raises(TypedReadError, match='INTEGRITY_FAILED'):
                public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
        finally:
            path.write_bytes(raw)
    assert support.counts(core) == before


def test_semantic_proof_tampering_rejected_beyond_self_consistent_pins(fixture):
    inputs, author, value, left, right, _ = merged(fixture)
    core = inputs[0]
    _, _, envelope = stored(core, value, author.binding)
    snap = snapshot(core, 'plain-source')
    records = {canonical_text(pin['ref']): payload_at(core, pin['ref'], snap,
        material=wire_ref(pin['ref'])[1]['entity_type'] == 'resource_version/v1') for pin in envelope['dependencies']}
    target_record = value.revision.to_dict()
    command_key = canonical_text(value.command_ref.to_dict())
    command = json.loads(records[command_key][0])
    analysis_key = canonical_text(command['analysis_ref'])
    host_key = canonical_text(left.revision.host_requirements_ref.to_dict())
    changes = ('parents', 'owner', 'producer', 'base', 'normalized', 'choices', 'host',
        'schema_ref', 'metadata', 'inventory', 'inventory_source', 'duplicate_inventory', 'extra_inventory', 'binding',
        'cycle', 'ancestry', 'designer_constraints')
    for damage in changes:
        record, document, incoming = deepcopy(target_record), deepcopy(envelope), dict(records)
        def edit(key, change):
            raw, prepared = incoming[key]
            body = json.loads(raw); change(body)
            raw = canonical_json(body)
            meta = deepcopy(prepared.metadata)
            if prepared.object_type == 'resource_version/v1':
                meta['size'] = len(raw)
            else:
                meta = body
            incoming[key] = raw, replace(prepared, size=len(raw), metadata=meta)
        if damage == 'parents': record['parent_revision_refs'].reverse()
        elif damage == 'owner': record['owner_task_ref']['ref']['version_id'] = 'task_version:' + '0' * 32
        elif damage == 'producer': record['producer_principal_ref']['ref']['version_id'] = 'principal_version:' + '0' * 32
        elif damage == 'base': edit(analysis_key, lambda d: d.update(base_revision_ref=left.revision.revision_ref.to_dict()))
        elif damage == 'normalized': edit(analysis_key, lambda d: d['normalized']['local']['atoms'].update({'host/selection': {}}))
        elif damage == 'choices': edit(command_key, lambda d: d['requested_choices'][0].update(choice='incoming'))
        elif damage == 'host': edit(host_key, lambda d: d['declaration_refs'].reverse())
        elif damage == 'schema_ref': edit(command_key, lambda d: d['schema_authorities'].update({implementation.RESOLUTION_SCHEMA: d['schema_authorities'][implementation.RESULT_COMMAND_SCHEMA]}))
        elif damage == 'metadata':
            raw, prepared = incoming[command_key]; meta = deepcopy(prepared.metadata); meta['summary'] += ' changed'
            incoming[command_key] = raw, replace(prepared, metadata=meta)
        elif damage == 'inventory': document['compiled']['fragments'].pop(next(iter(document['compiled']['fragments'])))
        elif damage == 'inventory_source': document['merge_proof']['inventories'][0]['compiled']['source']['name'] += ' changed'
        elif damage == 'duplicate_inventory': document['merge_proof']['inventories'].append(deepcopy(document['merge_proof']['inventories'][0]))
        elif damage == 'extra_inventory':
            document['merge_proof']['inventories'].append({'revision_ref': value.revision.revision_ref.to_dict(), 'compiled': document['compiled']})
        elif damage == 'binding': document['merge_proof']['binding_ref']['source_id'] = 'other-source'
        elif damage == 'cycle': edit(canonical_text(left.revision.revision_ref.to_dict()),
            lambda d: d.update(parent_revision_refs=[left.revision.revision_ref.to_dict()]))
        elif damage == 'ancestry': edit(analysis_key, lambda d: d['ancestry'].pop())
        elif damage == 'designer_constraints': document['compiled']['source']['designer_constraints'] = {'opaque': True}
        with pytest.raises((ValueError, KeyError)):
            verify(core, record, document['compiled'], document['elements'], document['merge_proof'], lambda ref: incoming[canonical_text(ref)])


def test_stored_pin_omission_extra_duplicate_metadata_and_cross_source_rejected(fixture):
    inputs, author, value, _, _, _ = merged(fixture)
    core = inputs[0]
    ref, raw, original = stored(core, value, author.binding)
    snap = snapshot(core, 'plain-source')
    for damage in ('omitted', 'extra', 'duplicate', 'metadata', 'source', 'dto', 'schema'):
        doc = deepcopy(original)
        if damage == 'omitted': doc['dependencies'].pop()
        elif damage in ('duplicate', 'extra'):
            extra = deepcopy(doc['dependencies'][0])
            if damage == 'extra': extra['ref'] = ref.to_dict()
            doc['dependencies'].append(extra)
        elif damage == 'metadata': doc['dependencies'][0]['metadata']['fixture_damage'] = True
        elif damage == 'source': doc['dependencies'][0]['ref']['source_id'] = 'other-source'
        elif damage == 'dto': doc['projection']['mapping_groups'].append({'unproved': True})
        else: doc['schema_version'] = public.STORED_SCHEMA
        with pytest.raises((ValueError, TypedReadError)):
            public._read_merge_projection(core, value.revision.revision_ref.to_dict(), value.revision.to_dict(),
                ref.to_dict(), canonical_json(doc), doc, snap)


def test_actual_merge_uses_existing_authorized_comparison_full_pair(tmp_path, monkeypatch):
    from cpn.frontend.comparison_context import comparison_context
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registry.observer_access import revoke_observer_access
    from test_comparison_context import world, reader, chosen, request, counts
    monkeypatch.setattr(support.socket, 'socket', support.forbidden)
    monkeypatch.setattr(support.subprocess, 'Popen', support.forbidden)
    w = world(tmp_path / 'comparison', 'merge-public', declaration=support.module(),
        registration=support.registration(), schema_data=plain_merge_result_schema_data)
    merger = PlainModuleMergeAuthor(w['gateway'], w['author'].registration, w['author'].producer)
    left_doc, right_doc = w['module'].to_dict(), w['module'].to_dict()
    left_doc['components'][0]['operations'][0]['config'] = {'value': 'left'}
    right_doc['components'][1]['operations'][0]['config'] = {'value': 'right'}
    def publish(doc, command):
        return w['author'].publish(module=ModuleDeclaration.from_dict(doc), element_ids=w['ids'],
            parent_ref=w['first'].revision.revision_ref, command_id=command)
    left, right = publish(left_doc, 'left'), publish(right_doc, 'right')
    analysis = merger.analyzer.analyze(local_ref=left.revision.revision_ref,
        incoming_ref=right.revision.revision_ref, command_id='analysis')
    result = merger.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id='M')
    with reader(w) as session:
        before = counts(w)
        context = comparison_context(session, request(session, chosen(left), chosen(result)))
        assert context['presentation']['recommended_mode'] == 'full_pair'
        assert context['mapping']['explicit_presence_changes'] == []
        assert context['right']['graph']['nodes'] and context['right']['graph']['edges']
        assert counts(w) == before
        revoke_observer_access(w['gateway'], grant_ref=w['grant'].grant_ref, command_id='revoke-merge')
        with pytest.raises(Exception) as error:
            comparison_context(session, request(session, chosen(left), chosen(result)))
        assert getattr(error.value, 'code', None) == 'ACCESS_CHANGED'
    with reader(w, materials=lambda ref: ref != analysis.analysis_ref) as session:
        with pytest.raises(Exception) as error:
            comparison_context(session, request(session, chosen(left), chosen(result)))
        assert getattr(error.value, 'code', None) == 'projection_unavailable'
        assert getattr(error.value.__cause__, 'code', None) == 'MATERIAL_ACCESS_NOT_GRANTED'


def test_nested_merge_and_ordinary_child_ancestry_use_frozen_inventory(fixture, monkeypatch):
    inputs, author, first, left, right, _ = merged(fixture)
    core = inputs[0]
    doc = first.module.to_dict(); doc['name'] = 'AfterFirstM'
    child = support.publish(inputs, doc, 'child', parent=first.revision.revision_ref)
    analysis = author.analyzer.analyze(local_ref=child.revision.revision_ref,
        incoming_ref=right.revision.revision_ref, command_id='second-analysis')
    assert analysis.document['conflicts'] == []
    second = author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id='second-M')
    snap = snapshot(core, 'plain-source')
    # Ancestor projections are neither a requirement nor a new truth store.
    for row in list(snap.objects.values()):
        meta = json.loads(row['metadata_json'])
        if meta.get('descriptors', {}).get('public_pn_revision_ref') not in (None, canonical_text(second.revision.revision_ref.to_dict())):
            del snap.objects[row['version_id']]
    monkeypatch.setattr(implementation, '_validate_merge_at', support.forbidden)
    graph = public.read_public_projection(core, second.revision.revision_ref, snapshot=snap)
    assert graph['author_revision_ref'] == second.revision.revision_ref.to_dict()
    assert graph['mapping_coverage'] == 'not_provided'


def test_original_noncanonical_ancestor_and_selected_schema_stay_compatible(fixture, monkeypatch):
    from cpn.rpnh.collaboration import materials
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.event_store import RegistryConflict
    inputs, author = fixture
    core, gateway, _, ordinary, analyzer, base, ids = inputs
    original_publish = materials._publish_private_system
    original_command = materials._command_material
    def noncanonical(core, owner, resource):
        if resource.content_schema_ref == materials.MODULE_SCHEMA:
            body = json.loads(resource.payload)
            raw = json.dumps(dict(reversed(list(body.items()))), ensure_ascii=True, separators=(',', ':')).encode()
            assert raw != canonical_json(body)
            resource = replace(resource, payload=raw)
        return original_publish(core, owner, resource)
    def unordered_host(**kwargs):
        if kwargs['command_id'] != 'legacy-left':
            return original_command(**kwargs)
        kwargs['documents'][3]['declaration_refs'].sort(key=lambda row: (row['kind'], row['key']), reverse=True)
        return original_command(**kwargs)
    doc = base.module.to_dict(); doc['name'] = 'LegacyNoncanonical'
    with monkeypatch.context() as patch:
        patch.setattr(materials, '_publish_private_system', noncanonical)
        patch.setattr(materials, '_command_material', unordered_host)
        left = support.publish(inputs, doc, 'legacy-left')
    # Choose canonical right-side HOST when the two legacy selections differ.
    right_doc = base.module.to_dict(); right_doc['components'][1]['operations'][0]['config'] = {'value': 'right'}
    right = support.publish(inputs, right_doc, 'right')
    analysis = analyzer.analyze(**support.request(inputs, left, right))
    choices = choices_for(analysis, 'incoming')
    schema = implementation.RESOLUTION_SCHEMA
    original = author.schemas[schema]
    body = json.loads(core.object_store.path_for_version(original.resource_version_id).read_bytes())
    schema_raw = json.dumps(dict(reversed(list(body.items()))), ensure_ascii=True, separators=(',', ':')).encode()
    alternative = implementation._publish_private_system(core, gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(gateway._bootstrap_ref), payload=schema_raw, media_type='application/schema+json',
        content_schema_ref=None, summary='Original exact alternate schema fixture', descriptors={},
        lifetime_ref=gateway._bootstrap_ref, idempotency_key='original-alternate-schema'))
    author.schemas[schema] = alternative
    args = {'analysis_ref': analysis.analysis_ref, 'choices': choices, 'command_id': 'compatible-M'}
    value = author.publish(**args)
    graph = public.read_public_projection(core, value.revision.revision_ref, snapshot=snapshot(core, 'plain-source'))
    assert graph['target_ref'] == value.revision.revision_ref.to_dict()
    assert author.publish(**args).revision == value.revision
    # Same schema semantics at another exact reference are not replay authority.
    author.schemas[schema] = original
    with pytest.raises(RegistryConflict, match='conflict'):
        author.publish(**args)


def test_missing_swapped_extra_material_relations_fail_closed(fixture):
    inputs, author, value, _, _, _ = merged(fixture)
    core = inputs[0]
    frozen = snapshot(core, 'plain-source')
    resource = value.resolution_ref.ref.resource_version_id
    selected = next(row for row in frozen.relations if json.loads(row['source_json'])['version_id'] == str(resource))
    for damage in ('missing', 'swapped', 'extra', 'event'):
        snap = snapshot(core, 'plain-source')
        relations = [dict(row) for row in snap.relations]
        index = next(i for i, row in enumerate(relations) if row['relation_id'] == selected['relation_id'])
        if damage == 'missing': relations.pop(index)
        elif damage == 'extra': relations.append(deepcopy(relations[index]))
        elif damage == 'swapped':
            target = json.loads(relations[index]['target_json'])
            target['version_id'] = 'bootstrap_command_version:' + '0' * 32
            relations[index]['target_json'] = canonical_text(target)
            event = next(e for e in snap.events if str(e.event_id) == selected['published_event_id'])
            snap.events = tuple(replace(e, payload={**e.payload, 'target': target}) if e == event else e for e in snap.events)
        else:
            snap.events = tuple(e for e in snap.events if str(e.event_id) != selected['published_event_id'])
        snap.relations = tuple(relations)
        with pytest.raises(TypedReadError, match='INTEGRITY_FAILED'):
            public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    # Missing actual registered material relation is rejected by the original
    # full merge proof too, rather than only this derived projection checker.
    with core.event_store.connect() as db:
        db.execute('DELETE FROM relations WHERE relation_id=?', (selected['relation_id'],))
        db.commit()
    from cpn.rpnh.registry.event_store import RegistryConflict
    with pytest.raises(RegistryConflict):
        from cpn.rpnh.collaboration import validate_closed_revision
        validate_closed_revision(core, value.revision.revision_ref, support.registration())
