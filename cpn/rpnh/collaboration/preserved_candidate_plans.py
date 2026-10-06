"""Exact historical v2 inert-plan reading, separate from first admission.

Static author material and mechanically consistent wire are not evidence that
HOST callables generated the fragments. Future executable preparation must
recompile with the actual Registration and compare the complete result.
"""
from __future__ import annotations

import json

from ..executable_net import _load_compiled_net_offline
from ..registry._candidate_plan_reads import PlanReadClosure, same
from ..registry._candidate_read_context import _CandidateReadContext
from ..registry._event_store.adoption_reads import AdoptionPrefixReads, _BASIS_DESCRIPTOR_TYPES
from ..registry._event_store.collaboration_descriptors import readable_descriptor
from ..registry._event_store.source_identity import _canonical_commit_event
from ..registry._preserved_basis_reads import _basis_read_context
from ..registry._preserved_slot_selection import _check_slot_selection_at, _selections
from ..registry.event_store import RegistryConflict
from ..registry.preserved_binding_contracts import (
    PLAN_V2_TYPE, MANIFEST_V2_TYPE, PreservedBasis, RuntimeBindingManifestDraftV2,
)
from ..registry.publication import _resource_from_payload, _version_from_payload
from ..registry.runtime_binding_contracts import freeze_candidate_document
from ..registry.strict_contracts import ref_payload
from .candidate_plans import (
    _command_key, _copy_ref, _empty_hosts, _operation_refs, _pure_author_preflight, _record_ref,
)
from .materials import _match_author_compiled, _requires_graph_source_rebuild
from .references import SourceQualifiedVersionRef


# These objects must first have been consumed successfully by one of the
# finite owner/author/business/basis readers below. Type membership alone is
# not semantic authority and the RecordingStore is never a public input.
_PLAN_DESCRIPTOR_TYPES = _BASIS_DESCRIPTOR_TYPES | {'collaboration_net_revision/v1'}


def _supported_preserved_plan(plan, compiled):
    if _requires_graph_source_rebuild(compiled.source):
        raise RegistryConflict('graph-authoritative preparation requires a single-source rebuild')
    if any(item.declaration.tools or item.executor_declaration['contracts'].get('transport') != 'deterministic'
            for item in compiled.operations):
        raise RegistryConflict('preserved inert plans support only deterministic operations without tools')
    empty = {'host_bindings': _empty_hosts(compiled),
        'runtime_dependencies': {item.name: {} for item in compiled.symbolic.transitions},
        'host_inventory': {'resource_refs': [], 'artifact_refs': []}}
    if any(not same(plan[key], value) for key, value in empty.items()):
        raise RegistryConflict('additional HOST selections are unsupported in this plan slice')


def _finish_plan_dependencies(closure, reads, validated):
    """Reclose only completed typed reads plus their explicit schema edges."""
    before = set(closure.store.records)
    closure.finish_resources()
    for key in set(closure.store.records) - before:
        # resource() follows only a resource schema pair or a frozen catalog;
        # it validates the selected actual schema and any applicable instance.
        kind = closure.store.records[key][0].object_type
        if kind not in ('resource_version/v1', 'registry_type_catalog/v1'):
            raise RegistryConflict('unsupported indirect candidate dependency role')
        validated.add(key)
    if set(closure.store.records) != validated:
        raise RegistryConflict('candidate dependency lacks a completed typed read role')
    for key in sorted(validated):
        prepared, _, evidence = closure.store.records[key]
        if prepared.object_type != 'resource_version/v1' and prepared.object_type not in _PLAN_DESCRIPTOR_TYPES:
            raise RegistryConflict('unsupported candidate dependency object role')
        exact = reads._canonical_prepared(_version_from_payload(evidence['ref']))
        # One shared RecordingStore checks repeated refs, metadata and bytes.
        closure.store.read_registered(exact)
    return closure.store.evidence()


def _collect_preserved_plan_dependencies(context, plan):
    """One continuous cut, one recorder, and finite static dependency roles."""
    if type(context) is not _CandidateReadContext:
        raise TypeError('preserved plan reading requires its fixed context')
    context.require_cut()
    context.catalog.validate_instance(PLAN_V2_TYPE, category='object', instance=plan)
    RuntimeBindingManifestDraftV2.from_document(_version_from_payload(plan['plan_ref']),
        _version_from_payload(plan['manifest_ref']), plan)
    db = context.db
    closure = PlanReadClosure(db, context)
    closure.owner(plan)
    author = SourceQualifiedVersionRef.from_dict(plan['author_ref'], catalog=context.catalog)
    material, _ = _pure_author_preflight(db, context, closure, author)
    # Bind raw source/schema inventory before any wire instance validation.
    if (not same(plan['compiled']['source'], material.module.to_dict())
            or not same(plan['compiled']['registrations'], material.host_requirements['registrations'])):
        raise RegistryConflict('candidate raw wire differs from exact author source/HOST materials')
    compiled = _load_compiled_net_offline(plan['compiled'])
    _supported_preserved_plan(plan, compiled)
    material = _match_author_compiled(material, compiled)
    if not same(material.host_requirements, plan['host_requirements']):
        raise RegistryConflict('candidate plan differs from complete author HOST materials')
    schemas = {item['key']: item['resource_ref']['ref'] for item in material.host_requirements['declaration_refs']
        if item['kind'] == 'schema' and item['key'] in compiled.source.required_schemas}
    if set(schemas) != set(compiled.source.required_schemas) or not same(schemas, plan['schema_refs']):
        raise RegistryConflict('candidate schema selection differs from exact author declarations')
    key = _command_key(context.task_id, closure.binding['source_id'], plan['command_id'])
    if (not same(plan['plan_ref'], ref_payload(_record_ref(PLAN_V2_TYPE, key + ':plan')))
            or not same(plan['manifest_ref'], ref_payload(_record_ref(MANIFEST_V2_TYPE, key + ':manifest')))
            or plan['graph_command_key'] != key + ':graph' or plan['operation_command_key'] != key + ':operations'
            or not same(plan['operation_refs'], _operation_refs(compiled, key + ':operations'))):
        raise RegistryConflict("candidate plan differs from its complete command's allocated identities")
    closure.business_inputs(plan, compiled)
    # These complete fixed readers have verified the roles of all current reads.
    validated = set(closure.store.records)
    reads = AdoptionPrefixReads(context.event_store, context.catalog, db, context.task_id, _plan_reads=closure)
    if plan['preserved_slot_refs']:
        declarations = {item.name: item for item in compiled.symbolic.logical_slots}
        if not set(plan['preserved_slot_refs']) <= set(declarations):
            raise RegistryConflict('selected symbols differ from the candidate slot inventory')
        selected_schema_ids = {declarations[name].schema for name in plan['preserved_slot_refs']}
        # The only supported selected-schema input is this unique frozen-author
        # projection. Equal-content alternative authority is not substituted.
        slots, selected_schemas = _selections(
            {name: _version_from_payload(value) for name, value in plan['preserved_slot_refs'].items()},
            {schema: _resource_from_payload(schemas[schema]) for schema in selected_schema_ids})
        basis = PreservedBasis.from_document(plan['preserved_basis'])
        with _basis_read_context(context, basis, _db=db, _plan_reads=closure) as basis_context:
            _check_slot_selection_at(basis_context, compiled, slots, selected_schemas, declarations)
            reads = basis_context.reads
        validated.update(closure.store.records)
    return _finish_plan_dependencies(closure, reads, validated)


def _read_preserved_candidate_plan_at(context, plan_ref):
    if type(context) is not _CandidateReadContext:
        raise TypeError('preserved plan reading requires its fixed context')
    context.require_cut()
    reference = _copy_ref(plan_ref, PLAN_V2_TYPE)
    # Read the plan before constructing its dependency recorder. Its bytes must
    # never become evidence for themselves.
    reads = AdoptionPrefixReads(context.event_store, context.catalog, context.db, context.task_id)
    prepared = reads._canonical_prepared(reference)
    document = readable_descriptor(context.object_store, prepared)
    row = context.db.execute('SELECT o.published_event_id,o.transaction_id,t.idempotency_key FROM objects o '
        'JOIN transactions t ON o.transaction_id=t.transaction_id WHERE o.version_id=?',
        (str(reference.version_id),)).fetchone()
    key = _command_key(context.task_id, document['source_id'], document['command_id'])
    if (prepared.producer_invocation_id is not None or row['idempotency_key'] != key + ':plan'
            or _canonical_commit_event(context.db, row['transaction_id'], context.task_id) is None
            or context.db.execute('SELECT 1 FROM firing_temporary_members WHERE '
                "(member_kind='object' AND member_identity=?) OR (member_kind='event' AND member_identity=?)",
                (str(reference.version_id), row['published_event_id'])).fetchone() is not None
            or context.object_store.read_registered(prepared) != freeze_candidate_document(document).encode('utf-8')
            or not same(document['plan_ref'], ref_payload(reference))):
        raise RegistryConflict('preserved plan lacks exact static canonical bytes/identity')
    evidence = _collect_preserved_plan_dependencies(context, document)
    if not same(evidence, document['dependency_evidence']):
        raise RegistryConflict('candidate dependency bytes differ from the frozen exact evidence')
    return RuntimeBindingManifestDraftV2.from_document(reference,
        _version_from_payload(document['manifest_ref']), document)


def read_preserved_candidate_plan(core, plan_ref):
    """Read a canonical historical inert plan, without current-head selection.

    This reader does not prove first-current admission or HOST lowering. A v2
    publication uses the separate producer and fixed first/replay commit gate.
    """
    reference = _copy_ref(plan_ref, PLAN_V2_TYPE)
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return _read_preserved_candidate_plan_at(_CandidateReadContext.from_core(core, db), reference)
