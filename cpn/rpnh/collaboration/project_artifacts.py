"""Bounded owner suggestions and scoped bytes from settled workspace heads only."""
import base64
import hashlib
import io
import tarfile
from ..registry.publication import _version_from_payload
from ..registry.schema_catalog import canonical_json
from ..registry.event_store import RegistryConflict
from .references import SourceQualifiedResourceRef
from .registry_typed_readers import qualify


def artifact_plan(endpoint):
    endpoint._authorize(owner=True)
    core = endpoint.core
    cut = core.event_store.max_ordinal()
    epoch = core.event_store.writer_epoch
    snapshot = endpoint.owner.snapshot()
    from cpn.frontend.checkpoint_view import _BoundedReads
    from ..workspace_settlement import _workspace_resource_state, _archive_files
    checkpoint, _ = _BoundedReads(core, cut).checkpoint(snapshot['checkpoint_ref'])
    files, seen, metadata_bytes = [], set(), 0
    archive_bytes = payload_bytes = 0
    source = endpoint.host._binding['source_id']
    for selected in checkpoint['workspace_revision_refs']:
        cursor = selected
        while cursor is not None:
            ref = _version_from_payload(cursor)
            if ref in seen:
                break
            if len(seen) >= 128:
                raise ValueError('artifact ancestry exceeds 128 exact revisions')
            seen.add(ref)
            row = core.event_store.canonical_object_row(ref.version_id, through_ordinal=cut)
            prepared = core.get_version(ref.version_id)
            if (row is None or prepared.object_type != 'workspace_revision/v1'
                    or prepared.logical_id != ref.entity_id or prepared.size > 16 * 1024 * 1024):
                raise ValueError('artifact revision is absent, inexact or exceeds archive limit')
            core.object_store.validate_envelope(prepared)
            core.catalog.validate_instance('workspace_revision/v1', category='object', instance=prepared.metadata)
            metadata_bytes += len(canonical_json(prepared.metadata))
            if metadata_bytes > 4 * 1024 * 1024:
                raise ValueError('artifact descriptors exceed bound')
            if (prepared.metadata['workspace_revision_ref'] != cursor
                    or prepared.metadata['task_ref'] != snapshot['task_ref']
                    or prepared.metadata['run_ref'] != snapshot['run_ref']
                    or prepared.metadata['settled'] is not True):
                raise ValueError('artifact lineage differs from current task/run or is unsettled')
            cursor = prepared.metadata.get('parent_revision_ref')
        ref = _version_from_payload(selected)
        resources = _workspace_resource_state(core, ref)
        head = core.get_version(ref.version_id)
        archive_bytes += head.size
        if archive_bytes > 16 * 1024 * 1024:
            raise ValueError('selected archives exceed total byte bound')
        # Bound header expansion before the original full-archive parser.
        raw = core.object_store.read_registered(head, max_bytes=16 * 1024 * 1024)
        members = declared_bytes = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as source_archive:
            for member in source_archive:
                members += 1
                declared_bytes += member.size
                if (members > 2048 or member.sparse is not None or member.size < 0
                        or member.size > 4 * 1024 * 1024 or declared_bytes > 16 * 1024 * 1024
                        or not (member.isdir() or member.isfile())):
                    raise ValueError('archive header exceeds bounded regular-file surface')
        archive = _archive_files(core, ref)
        if len(archive) > 2048 or set(archive) != set(resources):
            raise ValueError('settled archive lacks complete exact path resources or exceeds file limit')
        for path, resource in sorted(resources.items()):
            payload, mode = archive[path]
            payload_bytes += len(payload)
            if payload_bytes > 16 * 1024 * 1024 or len(payload) > 4 * 1024 * 1024 or len(files) >= 2048:
                raise ValueError('artifact exceeds bounded body/file limits')
            files.append({'workspace_revision_ref': selected, 'path': path,
                'resource_ref': qualify(source, {'resource_id': str(resource.resource_id),
                    'resource_version_id': str(resource.resource_version_id)}),
                'sha256': hashlib.sha256(payload).hexdigest(), 'byte_count': len(payload), 'mode': mode})
    if sum(row['byte_count'] for row in files) > 16 * 1024 * 1024:
        raise ValueError('settled artifacts exceed total body limit')
    result = {'status': 'settled_artifact_scope_suggestion_not_grant_or_verifier_result',
        'task_ref': snapshot['task_ref'], 'net_ref': snapshot['net_ref'],
        'checkpoint_ref': snapshot['checkpoint_ref'], 'transition_firing_refs': checkpoint['transition_firing_refs'],
        'files': files, 'record_types': [], 'material_refs': [row['resource_ref'] for row in files],
        'export_refs': [], 'read_cut': cut}
    endpoint._authorize(owner=True)
    if (cut, epoch) != (core.event_store.max_ordinal(), core.event_store.writer_epoch):
        raise RegistryConflict('artifact selection became stale')
    return result


def read_artifact(endpoint, request):
    plan = artifact_plan(endpoint)
    if request['checkpoint'] != plan['checkpoint_ref']:
        raise RegistryConflict('artifact checkpoint changed')
    rows = [row for row in plan['files'] if row['workspace_revision_ref'] == request['workspace'] and row['path'] == request['path']]
    if len(rows) != 1 or endpoint.reader is None:
        raise ValueError('exact settled artifact and existing scoped reader required')
    row = rows[0]
    result = _read_scoped_body(endpoint, row)
    endpoint._authorize(owner=True)
    if endpoint.owner.snapshot()['checkpoint_ref'] != plan['checkpoint_ref']:
        raise RegistryConflict('artifact checkpoint changed during read')
    return {**row, 'task_ref': plan['task_ref'], 'checkpoint_ref': plan['checkpoint_ref'],
        'transition_firing_refs': plan['transition_firing_refs'],
        'content_base64': base64.b64encode(result['bytes']).decode('ascii')}


def _read_scoped_body(endpoint, row):
    reference = SourceQualifiedResourceRef.from_dict(row['resource_ref'], catalog=endpoint.core.catalog)
    cut = endpoint.reader.capture_cut(reference.source_id)
    result = endpoint.reader.read_material(resource_ref=reference, at_cut=cut)
    if result['sha256'] != row['sha256'] or result['byte_count'] != row['byte_count']:
        raise RegistryConflict('registered artifact differs from exact settled archive')
    return result
