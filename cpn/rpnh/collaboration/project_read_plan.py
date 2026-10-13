"""Owner-only, inert scope suggestions from original validated project proofs.

This is neither a reader nor a grant. The normal scoped reader and original
export planner still revalidate the requested closure after explicit issuance.
"""
from types import SimpleNamespace
import hashlib
import json

from ..registry.schema_catalog import canonical_json, canonical_text
from ..registry.strict_contracts import ref_payload
from ..registry.event_store import RegistryConflict
from .materials import validate_closed_revision
from .public_projections import projection_resource_ref, STORED_SCHEMA
from .plain_merge_result import ValidatedPlainMergeRevision, _projection_proof_at
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from .registry_typed_readers import TypedReaderCatalog, wire_ref, qualify


def suggest_read_scope(endpoint, root, *, include_runtime=False):
    endpoint._authorize(owner=True)
    core, source = endpoint.core, endpoint.host._binding['source_id']
    before = (core.event_store.max_ordinal(), core.event_store.writer_epoch)
    material_refs, export_refs, record_types = {}, {}, {'collaboration_net_revision/v1'}
    total_bytes = 0

    def add(reference, *, export=True):
        nonlocal total_bytes
        value = reference.to_dict() if hasattr(reference, 'to_dict') else reference
        selected_source, local = wire_ref(value)
        if selected_source != source:
            raise ValueError('cross-source read-scope planning is unsupported')
        if local['entity_type'] == 'resource_version/v1':
            value = qualify(source, {'resource_id': local['logical_id'], 'resource_version_id': local['version_id']})
            key = canonical_text(value)
            if key not in material_refs:
                prepared = core.get_version(local['version_id'])
                if str(prepared.logical_id) != local['logical_id']:
                    raise RegistryConflict('scope material identity differs')
                total_bytes += prepared.size
                if total_bytes > 16 * 1024 * 1024 or len(material_refs) >= 2048:
                    raise ValueError('scope suggestion exceeds bounded closure')
                material_refs[key] = value
        else:
            record_types.add(local['entity_type'])
        if export:
            export_refs[canonical_text(value)] = value
        return value

    seen, pending = set(), [root]
    while pending:
        ref = pending.pop()
        if ref in seen:
            continue
        if len(seen) >= 128:
            raise ValueError('scope history exceeds 128 exact revisions')
        seen.add(ref)
        value = validate_closed_revision(core, ref, endpoint.registration)
        if value.module.designer_constraints or value.revision.selected_change_refs:
            raise ValueError('only ordinary closed Module history is supported')
        add(ref)
        expected = [getattr(value.revision, field).to_dict() for field in
                    ('definition_ref', 'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref')]
        expected.extend(row['resource_ref'] for row in value.host_requirements['declaration_refs'])
        if value.revision.parent_revision_refs and not isinstance(value, ValidatedPlainMergeRevision):
            if len(value.revision.parent_revision_refs) != 1:
                raise ValueError('unsupported producer history')
            parent = validate_closed_revision(core, value.revision.parent_revision_refs[0], endpoint.registration)
            expected.extend([parent.revision.definition_ref.to_dict(), parent.revision.element_mapping_ref.to_dict()])
        projection = projection_resource_ref(core, ref, binding=endpoint.host._binding)
        prepared = core.get_version(projection.ref.resource_version_id)
        raw = core.object_store.read_registered(prepared, max_bytes=4 * 1024 * 1024)
        stored = json.loads(raw)
        descriptor = prepared.metadata.get('descriptors', {})
        if (descriptor.get('public_pn_revision_ref') != canonical_text(ref.to_dict())
                or descriptor.get('content_sha256') != hashlib.sha256(raw).hexdigest()
                or stored['projection']['target_ref'] != ref.to_dict()
                or canonical_json(stored['compiled']) != canonical_json(value.compiled.to_dict())
                or canonical_json(stored['elements']) != canonical_json(value.element_map)):
            raise RegistryConflict('stored projection differs from original author proof')
        if isinstance(value, ValidatedPlainMergeRevision):
            with core.event_store.connect() as db:
                db.execute('BEGIN')
                proof, dependencies = _projection_proof_at(db,
                    SimpleNamespace(core=core, registration=endpoint.registration), value, endpoint.host._binding)
            if canonical_json(stored.get('merge_proof')) != canonical_json(proof) or canonical_json(stored['dependencies']) != canonical_json(dependencies):
                raise RegistryConflict('stored merge dependencies differ from original exact proof')
        else:
            if stored['schema_version'] != STORED_SCHEMA or {canonical_text(row['ref']) for row in stored['dependencies']} != {canonical_text(r) for r in expected}:
                raise ValueError('unsupported or incomplete ordinary projection closure')
        add(projection)
        for row in stored['dependencies']:
            _, local = wire_ref(row['ref'])
            body = core.object_store.read_registered(core.get_version(local['version_id']), max_bytes=4 * 1024 * 1024)
            if len(body) != row['bytes'] or hashlib.sha256(body).hexdigest() != row['sha256']:
                raise RegistryConflict('projection dependency bytes differ')
            add(row['ref'])
        # The importer publishes provenance as a separate resource. Match the
        # same exact revision marker exposed by the fixed-cut typed reader.
        with core.event_store.connect() as db:
            db.execute('BEGIN')
            provenance = db.execute(
                "SELECT logical_id, version_id FROM objects "
                "WHERE object_type='resource_version/v1' AND "
                "json_extract(metadata_json, '$.descriptors.subnet_import_revision_ref')=?",
                (canonical_text(ref.to_dict()),)).fetchall()
        if len(provenance) > 1:
            raise RegistryConflict('import provenance is ambiguous for exact revision')
        if provenance:
            record_types.add('resource_version/v1')
            add(qualify(source, {'resource_id': provenance[0]['logical_id'],
                                 'resource_version_id': provenance[0]['version_id']}))
        pending.extend(value.revision.parent_revision_refs)

    runtime = None
    if include_runtime:
        from cpn.frontend.checkpoint_view import _BoundedReads, _inventory, _tokens
        snapshot = endpoint.owner.snapshot()
        reads = _BoundedReads(core, before[0])
        checkpoint, _ = reads.checkpoint(snapshot['checkpoint_ref'])
        compiled, _, runtime_root = _inventory(reads, {'net_ref': snapshot['net_ref']}, checkpoint, None)
        _tokens(reads, checkpoint, snapshot['net_ref'], {p.name: p.capacity for p in compiled.symbolic.places}, runtime_root['task_ref'])
        # Only compiler wire and actual port schemas need body permission.
        # Other business resources visited by the validator remain metadata-only.
        net = reads.metadata(snapshot['net_ref'], 'net_instance/v1')
        declaration = net['team_net_declaration_resource_ref']
        if 'resource_id' not in declaration:
            declaration = {'resource_id': declaration['logical_id'], 'resource_version_id': declaration['version_id']}
        add(qualify(source, declaration), export=False)
        for encoded in reads.schema_reads:
            add(qualify(source, json.loads(encoded)), export=False)
        record_types.update(json.loads(key)['entity_type'] for key in reads.cache)
        runtime = {'net_ref': snapshot['net_ref'], 'checkpoint_ref': snapshot['checkpoint_ref']}
    known = TypedReaderCatalog().entry_types
    # Infrastructure proof descriptors may be implicit dependency reads in the
    # original reader; its supported typed surface defines grant record names.
    unique_bodies = {wire_ref(value)[1]["version_id"] for value in export_refs.values()}
    body_sizes = [core.get_version(version).size for version in unique_bodies]
    result = {'selected_body_count': len(unique_bodies), 'material_ref_count': len(material_refs), 'revision_count': len(seen), 'selected_body_bytes': sum(body_sizes), 'max_selected_body_bytes': max(body_sizes, default=0),
        'root_revision_ref': root.to_dict(), 'runtime': runtime,
        'record_types': sorted(record_types & set(known)),
        'material_refs': [material_refs[k] for k in sorted(material_refs)],
        'export_refs': [export_refs[k] for k in sorted(export_refs)],
        'status': 'scope_suggestion_not_a_grant_or_export_plan', 'read_cut': before[0]}
    endpoint._authorize(owner=True)
    if before != (core.event_store.max_ordinal(), core.event_store.writer_epoch):
        raise RegistryConflict('scope suggestion became stale')
    return result
