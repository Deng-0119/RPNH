"""Pure exact-material proof for the plain-merge projection producer.

Inventories are derived evidence, never new author facts. The supplied reader
must return only canonical fixed-cut objects and authorize material bodies.
No Registry query, writer, Registration or component lowerer is used here.
"""
from __future__ import annotations

import hashlib
import json

from ..executable_net import _load_compiled_net_offline
from ..registry.publication import _fresh_bootstrap_reference_resource_metadata, _version_from_payload
from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS, canonical_json, canonical_text
from .assembly_v2 import _material_ref
from .authoring import NetRevision, NET_REVISION_TYPE
from .materials import (ValidatedClosedRevision, MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA,
    _element_map, _boundaries, _command_material, _author_revision_ref)
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .registry_typed_readers import qualify, wire_ref
from ._plain_merge_model import ALGORITHM, NORMALIZATION, normalize, compare
from ._plain_merge_resolution import RESOLUTION_ALGORITHM, resolve_atoms, rebuild_module

PRODUCER = 'rpnh/plain_merge_public_pn/v1'
STORED_SCHEMA = 'rpnh/stored_plain_merge_public_pn/v1'
PROOF_SCHEMA = 'rpnh/plain_merge_projection_proof/v1'
MATERIAL_FIELDS = ('definition_ref', 'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref')


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def verify(core, record, compiled_document, elements, proof, read):
    """Reconstruct all source/decision proofs using an explicit immutable read.

    ``read(qualified_ref) -> (bytes, PreparedObject)`` also records or checks the
    exact dependency set. The target descriptor is passed separately because the
    producer calls this before committing it; all ancestors must already exist.
    """
    from .plain_merge import ANALYSIS_SCHEMA, COMMAND_SCHEMA, ANALYSIS_MARKER, COMMAND_MARKER, _command_key, _nearest, _pins
    from .plain_merge_result import (RESULT_COMMAND_SCHEMA, RESULT_MARKER, RESOLUTION_SCHEMA,
        SCHEMAS, ROLES, _key, _result_ref, _metadata)
    from ..registry._event_store.source_identity import source_binding_id, source_binding_version_id

    if set(proof) != {'schema_version', 'binding_ref', 'inventories'} or proof['schema_version'] != PROOF_SCHEMA:
        raise ValueError('unknown plain merge proof envelope')
    if not isinstance(proof['inventories'], list):
        raise ValueError('plain merge inventories must be a finite list')
    source = record['revision_ref']['source_id']
    cache = {}

    def document(reference):
        ref = reference.to_dict() if hasattr(reference, 'to_dict') else reference
        if wire_ref(ref)[0] != source:
            raise ValueError('cross-source merge proof')
        key = canonical_text(ref)
        if key not in cache:
            raw, prepared = read(ref)
            value = json.loads(raw)
            if prepared.producer_invocation_id is not None:
                raise ValueError('merge proof provenance differs')
            if prepared.object_type != 'resource_version/v1':
                core.catalog.validate_instance(prepared.object_type, category='object', instance=value)
                if not _same(value, prepared.metadata):
                    raise ValueError('merge proof descriptor metadata differs')
            cache[key] = value, prepared, raw
        return cache[key]

    binding, _, _ = document(proof['binding_ref'])
    if (binding['source_id'] != source or qualify(source, binding['binding_ref']) != proof['binding_ref']
            or binding['binding_ref'] != {'entity_type': 'collaboration_source_binding/v1',
                'logical_id': str(source_binding_id(core.task_id)),
                'version_id': str(source_binding_version_id(core.task_id, binding['command_id']))}):
        raise ValueError('merge source binding identity differs')
    for field, kind in (('task_ref', 'task'), ('native_run_ref', 'run'), ('bootstrap_command_ref', 'bootstrap_command')):
        body, _, _ = document(qualify(source, binding[field]))
        if (body[kind + '_id'] != binding[field]['logical_id']
                or body[kind + '_version_id'] != binding[field]['version_id']):
            raise ValueError('merge source authority identity differs')
        if field == 'native_run_ref' and body['task_ref'] != binding['task_ref']:
            raise ValueError('merge native owner differs')
    if binding['task_ref']['logical_id'] != str(core.task_id):
        raise ValueError('merge owner is outside the selected task')
    owner = qualify(source, binding['task_ref'])
    catalog_ref = None

    def schema_authority(schema, authority):
        nonlocal catalog_ref
        reference = qualify(source, authority)
        body, prepared, raw = document(reference)
        if schema in PROTECTED_SCHEMA_REFS:
            if wire_ref(reference)[1]['entity_type'] != 'registry_type_catalog/v1':
                raise ValueError('protected merge schema lacks exact catalog')
            if catalog_ref is not None and reference != catalog_ref:
                raise ValueError('merge schema catalog selection differs')
            catalog_ref = reference
            entry = body['schemas'][schema]
            if entry['schema_id'] != schema:
                raise ValueError('merge catalog schema identity differs')
            actual = json.loads(entry['source'])
        else:
            # Existing result commands allow any exact local private schema
            # resource with these bytes, not only gateway allocation names.
            private(reference, media_type='application/schema+json')
            actual = body
        expected = json.loads(core.catalog.schema_path(schema).read_text(encoding='utf-8'))
        if not _same(actual, expected):
            raise ValueError('merge schema authority bytes differ')
        return hashlib.sha256(raw).hexdigest()

    def private(reference, *, schema=None, media_type='application/json'):
        reference = reference.to_dict() if hasattr(reference, 'to_dict') else reference
        typed = SourceQualifiedResourceRef.from_dict(reference, catalog=core.catalog)
        body, prepared, raw = document(reference)
        meta = prepared.metadata
        expected = _fresh_bootstrap_reference_resource_metadata(core, ref=typed.ref,
            task_ref=_version_from_payload(binding['task_ref']),
            bootstrap_ref=_version_from_payload(binding['bootstrap_command_ref']),
            lifetime_ref=_version_from_payload(binding['bootstrap_command_ref']),
            payload_size=len(raw), media_type=media_type, content_schema_ref=meta['content_schema_ref'],
            content_schema_authority_ref=meta['content_schema_authority_ref'], summary=meta['summary'],
            descriptors=meta['descriptors'], extensions=meta['extensions'], derived_from=())
        if not _same(meta, expected):
            raise ValueError('merge material bootstrap metadata differs')
        if schema is not None:
            if meta['content_schema_ref'] != schema:
                raise ValueError('merge material schema differs')
            schema_authority(schema, meta['content_schema_authority_ref'])
            core.catalog.validate_schema_ref(schema, body)
        return body, meta

    def principal(reference):
        if reference.source_id != source or reference.ref.entity_type != 'principal/v1':
            raise ValueError('merge producer must be an exact local principal')
        body, _, _ = document(reference)
        if (body['principal_id'] != str(reference.ref.entity_id)
                or body['principal_version_id'] != str(reference.ref.version_id)):
            raise ValueError('merge producer self-identity differs')

    def host_requirements(host, compiled, *, merge_result):
        if not _same(host['registrations'], compiled.to_dict()['registrations']):
            raise ValueError('merge HOST inventory differs')
        wanted = {(kind, key) for kind, entries in host['registrations'].items() for key in entries}
        actual = [(row['kind'], row['key']) for row in host['declaration_refs']]
        if len(set(actual)) != len(actual) or set(actual) != wanted:
            raise ValueError('merge HOST declaration set differs')
        for row in host['declaration_refs']:
            kind, key = row['kind'], row['key']
            body, meta = private(row['resource_ref'], media_type='application/schema+json' if kind == 'schema' else 'application/json')
            declaration = host['registrations'][kind][key]
            if not _same(body, declaration['schema'] if kind == 'schema' else declaration):
                raise ValueError('merge HOST exact declaration differs')
            if not merge_result:
                continue  # Ordinary ancestors retain their original selected refs/order.
            builtin = kind == 'schema' and key in PROTECTED_SCHEMA_REFS
            command = 'builtin-schema-source:' + key if builtin else f'host-registration:{kind}:{key}'
            descriptors = {'host_registration_kind': kind, 'registered_key': key}
            if builtin:
                descriptors['mechanical_schema'] = True
            if (row['resource_ref'] != _material_ref(core, binding, command).to_dict()
                    or meta['descriptors'] != descriptors):
                raise ValueError('merge result HOST selection differs')
            if kind == 'schema':
                if meta['content_schema_ref'] != 'registry_v1/registry_type_catalog/v1':
                    raise ValueError('merge HOST schema contract differs')
                schema_authority(meta['content_schema_ref'], meta['content_schema_authority_ref'])
            elif meta['content_schema_ref'] is not None or meta['content_schema_authority_ref'] is not None:
                raise ValueError('merge HOST declaration unexpectedly typed')
        if merge_result and actual != sorted(wanted):
            raise ValueError('merge result HOST selection order differs')

    inventories = {}
    for row in proof['inventories']:
        if set(row) != {'revision_ref', 'compiled'}:
            raise ValueError('invalid merge inventory entry')
        ref = SourceQualifiedVersionRef.from_dict(row['revision_ref'], catalog=core.catalog)
        if ref in inventories or ref.source_id != source or ref.ref.entity_type != NET_REVISION_TYPE:
            raise ValueError('duplicate/cross-source merge inventory')
        inventories[ref] = row['compiled']
    target = NetRevision.from_dict(record, catalog=core.catalog)
    if target.revision_ref in inventories:
        raise ValueError('merge target repeated in ancestry inventory')
    values, active, declarations = {}, set(), {}

    def visit(reference):
        if reference in active:
            raise ValueError('merge ancestry cycle')
        if reference in values:
            return values[reference]
        active.add(reference)
        if reference == target.revision_ref:
            revision, inventory = target, compiled_document
        else:
            if reference not in inventories:
                raise ValueError('missing exact ancestor inventory')
            body, _, _ = document(reference)
            revision, inventory = NetRevision.from_dict(body, catalog=core.catalog), inventories[reference]
        if (revision.revision_ref != reference or revision.owner_task_ref.to_dict() != owner
                or revision.definition_kind != 'closed_module' or revision.open_region_contract_ref is not None
                or revision.selected_change_refs):
            raise ValueError('plain merge revision identity/domain differs')
        principal(revision.producer_principal_ref)
        parents = [visit(ref) for ref in revision.parent_revision_refs]
        compiled = _load_compiled_net_offline(inventory)
        module = compiled.source
        if module.designer_constraints or any(c.key in {f'rpnh/agent-workflow-graph/v{n}' for n in range(1, 5)} for c in module.components):
            raise ValueError('unsupported plain merge ancestor domain')
        materials = [private(getattr(revision, field), schema=schema)
            for field, schema in zip(MATERIAL_FIELDS, (MODULE_SCHEMA, ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA), strict=True)]
        (definition, metadata), (mapping, _), (boundary, _), (host, _) = materials
        if not _same(definition, module.to_dict()) or not _same(boundary, _boundaries(module, mapping)):
            raise ValueError('merge source/boundary inventory differs')
        for kind, entries in host['registrations'].items():
            for name, declaration in entries.items():
                key, body = (kind, name), canonical_json(declaration)
                if key in declarations and declarations[key] != body:
                    raise ValueError('merge ancestry cannot use incompatible shared HOST declarations')
                declarations[key] = body
        host_requirements(host, compiled, merge_result=set(metadata['descriptors']) == {RESULT_MARKER})
        value = ValidatedClosedRevision(revision, module, compiled, mapping, boundary, host)
        ids = {row['locator']: row['element_id'] for row in mapping['elements']}
        marker = metadata['descriptors']
        markers = {key for key in marker if '_author_command_' in key or '_author_transform_command_' in key}
        if markers == {RESULT_MARKER} and set(marker) == markers:
            merge(value, marker, ids)
        elif markers == {'closed_author_command_v1'}:
            if len(parents) > 1:
                raise ValueError('ordinary merge ancestor has multiple parents')
            parent = parents[0] if parents else None
            copies = {row['element_id']: row['copied_from']['element_id'] for row in mapping['elements'] if row['copied_from'] is not None}
            if not _same(mapping, _element_map(module, ids, parent, copies)):
                raise ValueError('merge ancestor element map differs')
            command = _command_material(source_id=source, owner=revision.owner_task_ref,
                producer=revision.producer_principal_ref, command_id=revision.command_id,
                parents=revision.parent_revision_refs, documents=(definition, mapping, boundary, host))
            if marker['closed_author_command_v1'] != command or reference != _author_revision_ref(core, source, revision.command_id, parent):
                raise ValueError('merge ancestor complete command/identity differs')

        else:
            raise ValueError('unsupported merge ancestor producer')
        values[reference] = value
        active.remove(reference)
        return value

    def merge(value, marker, ids):
        revision = value.revision
        if len(revision.parent_revision_refs) != 2:
            raise ValueError('merge requires two ordered parents')
        left, right = revision.parent_revision_refs
        command_ref = SourceQualifiedResourceRef.from_dict(json.loads(marker[RESULT_MARKER]), catalog=core.catalog)
        command, command_meta = private(command_ref, schema=RESULT_COMMAND_SCHEMA)
        key = _key(revision.command_id)
        if (command_ref != _material_ref(core, binding, key + ':command')
                or revision.revision_ref != _result_ref(core, binding, revision.command_id, left, right)):
            raise ValueError('merge result complete command identity differs')
        analysis_ref = SourceQualifiedResourceRef.from_dict(command['analysis_ref'], catalog=core.catalog)
        analysis, analysis_meta = private(analysis_ref, schema=ANALYSIS_SCHEMA)
        analysis_command_ref = _material_ref(core, binding, _command_key(analysis['command_id']) + ':command')
        analysis_command, analysis_command_meta = private(analysis_command_ref, schema=COMMAND_SCHEMA)
        refs, pending = set(), [left, right]
        while pending:
            current = pending.pop()
            if current in refs:
                continue
            refs.add(current)
            pending.extend(values[current].revision.parent_revision_refs)
        records = {ref: values[ref].revision for ref in refs}
        bases = _nearest(records, left, right)
        if len(bases) != 1:
            raise ValueError('merge lacks one unique nearest common base')
        base = bases[0]
        request = analysis['request']
        if (request['local_revision_ref'] != left.to_dict() or request['incoming_revision_ref'] != right.to_dict()
                or request['asserted_base_revision_ref'] not in (None, base.to_dict())):
            raise ValueError('merge analysis ordered parents/base differs')
        analysis_producer = SourceQualifiedVersionRef.from_dict(analysis['producer_principal_ref'], catalog=core.catalog)
        principal(analysis_producer)
        models = {role: normalize(values[ref]) for role, ref in (('base', base), ('local', left), ('incoming', right))}
        differences, conflicts = compare(models['base'], models['local'], models['incoming'])
        expected_analysis = {'schema_version': ANALYSIS_SCHEMA, 'algorithm': ALGORITHM, 'normalization': NORMALIZATION,
            'source_id': source, 'owner_task_ref': owner, 'producer_principal_ref': analysis_producer.to_dict(),
            'command_id': analysis['command_id'], 'command_ref': analysis_command_ref.to_dict(), 'request': request,
            'status': 'analyzed', 'base_revision_ref': base.to_dict(), 'nearest_common_bases': [base.to_dict()],
            'inputs': {role: _pins(values[ref]) for role, ref in (('base', base), ('local', left), ('incoming', right))},
            'ancestry': [_pins(values[ref]) for ref in sorted(refs, key=lambda r: canonical_json(r.to_dict()))],
            'normalized': models, 'differences': differences, 'conflicts': conflicts}
        if (analysis_ref != _material_ref(core, binding, _command_key(analysis['command_id']) + ':analysis')
                or not _same(analysis, expected_analysis)
                or not _same(analysis_command, {'schema_version': COMMAND_SCHEMA, 'analysis': expected_analysis})
                or analysis_meta['descriptors'] != {ANALYSIS_MARKER: canonical_text(analysis_command_ref.to_dict())}
                or analysis_command_meta['descriptors'] != {COMMAND_MARKER: COMMAND_SCHEMA}):
            raise ValueError('merge analysis differs from exact source reconstruction')
        atoms, choices, deleted = resolve_atoms(expected_analysis, command['requested_choices'])
        module, rebuilt_ids = rebuild_module(atoms)
        host = value.host_requirements
        chosen_host = atoms['host/selection']
        selected = {(row['kind'], row['key']): row['resource_ref'] for row in chosen_host['declaration_refs']}
        for row in host['declaration_refs']:
            kind, name = row['kind'], row['key']
            if (row['resource_ref'] != selected.get((kind, name))
                    or not _same(host['registrations'][kind][name], chosen_host['registrations'].get(kind, {}).get(name))):
                raise ValueError('merge HOST outside selected exact contract')
        if (not _same(module.to_dict(), value.module.to_dict()) or rebuilt_ids != ids
                or not _same(value.element_map, _element_map(module, ids, None, {}))
                or not _same(normalize(value)['atoms'], {**atoms, 'host/selection': host})):
            raise ValueError('merge result differs from resolved author atoms')
        resolution = {'schema_version': RESOLUTION_SCHEMA, 'algorithm': RESOLUTION_ALGORITHM,
            'analysis_ref': analysis_ref.to_dict(), 'choices': choices, 'deleted_element_ids': deleted}
        authorities = command['schema_authorities']
        if set(authorities) != set((*SCHEMAS, RESULT_COMMAND_SCHEMA)):
            raise ValueError('merge result schema authorities incomplete')
        digests = {schema: schema_authority(schema, authority) for schema, authority in authorities.items()}
        specs = []
        for role, schema, body in zip(ROLES, SCHEMAS,
                (resolution, module.to_dict(), value.element_map, value.boundary_map, host), strict=True):
            reference = _material_ref(core, binding, key + ':' + role)
            expected_meta = _metadata(core, binding, reference, schema, authorities[schema], body,
                'Plain merge ' + role, {RESULT_MARKER: canonical_text(command_ref.to_dict())})
            actual, meta = private(reference, schema=schema)
            if (document(reference)[2] != canonical_json(body)
                    or not _same(actual, body) or not _same(meta, expected_meta)):
                raise ValueError('merge prepared material differs')
            specs.append({'role': role, 'schema': schema, 'resource_ref': reference.to_dict(), 'document': body,
                'metadata': expected_meta, 'sha256': hashlib.sha256(canonical_json(body)).hexdigest()})
        expected = {'schema_version': RESULT_COMMAND_SCHEMA, 'algorithm': RESOLUTION_ALGORITHM,
            'source_id': source, 'owner_task_ref': owner, 'producer_principal_ref': revision.producer_principal_ref.to_dict(),
            'command_id': revision.command_id, 'analysis_ref': analysis_ref.to_dict(),
            'analysis_command_ref': analysis_command_ref.to_dict(), 'requested_choices': command['requested_choices'],
            'schema_authorities': authorities, 'schema_authority_sha256': digests,
            'result_revision_ref': revision.revision_ref.to_dict(), 'parent_revision_refs': [left.to_dict(), right.to_dict()],
            'selected_change_refs': [], 'prepared_materials': specs}
        if (document(command_ref)[2] != canonical_json(expected)
                or not _same(command, expected) or not _same(command_meta,
                _metadata(core, binding, command_ref, RESULT_COMMAND_SCHEMA, authorities[RESULT_COMMAND_SCHEMA],
                    expected, 'Plain merge complete command', {RESULT_MARKER: RESULT_COMMAND_SCHEMA}))
                or any(getattr(revision, field).to_dict() != spec['resource_ref']
                    for field, spec in zip(MATERIAL_FIELDS, specs[1:], strict=True))):
            raise ValueError('merge immutable complete command differs')

    result = visit(target.revision_ref)
    if (set(values) != {*inventories, target.revision_ref} or not _same(elements, result.element_map)
            or len(target.parent_revision_refs) != 2
            or set(private(target.definition_ref, schema=MODULE_SCHEMA)[1]['descriptors']) != {RESULT_MARKER}):
        raise ValueError('merge projection has unused ancestry or wrong exact target')
    return result


class MaterialClosure:
    """Exact bootstrap resource relations from the existing canonical snapshot."""
    def __init__(self, snapshot, task_id):
        self.snapshot, self.task_id = snapshot, task_id
        self.events = {str(event.event_id): event for event in snapshot.events}
        self.relations = {}
        for row in snapshot.relations:
            source = json.loads(row['source_json'])
            self.relations.setdefault(source['version_id'], []).append(row)

    def check(self, reference, prepared):
        _, local = wire_ref(reference)
        if local['entity_type'] != 'resource_version/v1':
            return
        row = self.snapshot.objects[local['version_id']]
        bootstrap = prepared.metadata['origin']['primary_ref']
        source = {'entity_type': 'resource_version/v1', 'entity_id': local['logical_id'], 'version_id': local['version_id']}
        target = {'entity_type': bootstrap['entity_type'], 'entity_id': bootstrap['logical_id'], 'version_id': bootstrap['version_id']}
        actual = []
        for relation in self.relations.get(local['version_id'], []):
            event = self.events.get(str(relation['published_event_id']))
            left, right = json.loads(relation['source_json']), json.loads(relation['target_json'])
            payload = {'relation_id': relation['relation_id'], 'relation_type': relation['relation_type'],
                'source': left, 'target': right, 'strength': 'strong', 'metadata': {}}
            if (relation['strength'] != 'strong' or relation['transaction_id'] != row['transaction_id']
                    or event is None or str(event.transaction_id) != row['transaction_id']
                    or event.criticality != 'authoritative' or event.event_type != 'relation_published/v1'
                    or event.stream_id != 'relation:' + relation['relation_id']
                    or event.aggregate_id != relation['relation_id'] or event.aggregate_type != 'typed_relation/v1'
                    or event.producer_invocation_id is not None or event.producer_principal != 'framework'
                    or event.task_id != self.task_id or event.payload_schema_ref != 'registry_v1/relation_published/v1'
                    or not _same(left, source) or json.loads(relation['metadata_json'])
                    or not _same(event.payload, payload)):
                raise ValueError('merge material relation publication differs')
            actual.append((relation['relation_type'], right))
        if not _same(actual, [('produced_by', target)]):
            raise ValueError('merge material exact bootstrap relations differ')
