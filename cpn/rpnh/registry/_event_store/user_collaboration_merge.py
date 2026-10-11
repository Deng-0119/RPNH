"""Reclose a genuine M using original pure proof code and the writer snapshot."""
import json
import hashlib
from types import SimpleNamespace

from ..event_store import RegistryConflict
from ..schema_catalog import canonical_json, canonical_text
from .branch_publication import _local_binding
from .collaboration_descriptors import descriptor_store, exact_prepared


def merge_base(store, db, task, record):
    """Return the proven original analysis base, never descriptor-only ancestry.

    The stored inventory is only reconstruction input. The accepted original
    pure verifier re-reads and checks every source/analysis/command/resolution/
    HOST/schema dependency from this same pretransaction canonical snapshot.
    No observer-view material limit is promoted into author semantics.
    """
    from ...collaboration._plain_merge_projection import verify, MaterialClosure, STORED_SCHEMA, PRODUCER
    from ...collaboration.plain_merge_result import RESULT_MARKER
    from ...collaboration.public_projections import projection_resource_ref
    from ...collaboration.references import SourceQualifiedVersionRef
    from ...collaboration.registry_typed_readers import wire_ref
    objects = descriptor_store(store)
    binding = _local_binding(db, store.catalog, task, objects)
    branch = db.execute("SELECT value FROM registry_meta WHERE key='branch_id'").fetchone()[0]
    core = SimpleNamespace(task_id=task, branch_id=branch, catalog=store.catalog, object_store=objects)
    snapshot = SimpleNamespace(objects={}, events=(), relations=())
    closure = MaterialClosure(snapshot, task)

    def read(reference):
        source, ref = wire_ref(reference)
        if source != binding['source_id']:
            raise RegistryConflict('merge authority crosses source identity')
        prepared = exact_prepared(db, objects, task, ref)
        raw = objects.read_registered(prepared)
        if ref['entity_type'] == 'resource_version/v1':
            row = db.execute('SELECT * FROM objects WHERE version_id=?', (ref['version_id'],)).fetchone()
            snapshot.objects[ref['version_id']] = row
            relations = db.execute("SELECT * FROM relations WHERE json_extract(source_json,'$.version_id')=?",
                                   (ref['version_id'],)).fetchall()
            closure.relations[ref['version_id']] = relations
            for relation in relations:
                event = db.execute('SELECT * FROM events WHERE event_id=?', (relation['published_event_id'],)).fetchone()
                if event is None:
                    raise RegistryConflict('merge material relation has no original publication')
                closure.events[str(event['event_id'])] = store._row_to_envelope(event)
            closure.check(reference, prepared)
        return raw, prepared

    try:
        reference = SourceQualifiedVersionRef.from_dict(record['revision_ref'], catalog=store.catalog)
        projection_ref = projection_resource_ref(core, reference, binding=binding)
        raw, prepared = read(projection_ref.to_dict())
        stored = json.loads(raw)
        # The original publisher deliberately uses content_schema_ref=None;
        # its exact stored schema and producer contract live in this envelope.
        if (stored['schema_version'] != STORED_SCHEMA or prepared.media_type != 'application/json'
                or prepared.metadata['content_schema_ref'] is not None
                or prepared.metadata['task_ref'] != binding['task_ref']
                or prepared.metadata['origin']['primary_ref'] != binding['bootstrap_command_ref']
                or prepared.metadata['descriptors'] != {
                    'public_pn_revision_ref': canonical_text(record['revision_ref']),
                    'public_pn_contract': PRODUCER, 'content_sha256': hashlib.sha256(raw).hexdigest()}):
            raise ValueError('exact M lacks the original complete stored proof')
        result = verify(core, record, stored['compiled'], stored['elements'], stored['merge_proof'], read)
        if canonical_json(result.revision.to_dict()) != canonical_json(record):
            raise ValueError('reconstructed M descriptor differs')
        _, definition = read(record['definition_ref'])
        command_ref = json.loads(definition.metadata['descriptors'][RESULT_MARKER])
        command = json.loads(read(command_ref)[0])
        analysis = json.loads(read(command['analysis_ref'])[0])
        return analysis['base_revision_ref']['ref']
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RegistryConflict('collaboration result is not a fully proven original plain M') from exc
