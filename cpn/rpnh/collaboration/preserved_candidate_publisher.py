"""Explicit trusted v2 inert-plan producer with fixed first/replay admission.

The actual Registration compiles outside the committing write transaction.
Static Core admission proves material/wire compatibility, not HOST provenance
or permission to publish an executable graph.
"""
from __future__ import annotations

import json

from ..executable_net import _load_compiled_net_offline
from ..registry._candidate_plan_reads import PlanReadClosure, same
from ..registry._candidate_read_context import _CandidateReadContext
from ..registry._preserved_basis_reads import _copy_basis
from ..registry._preserved_slot_selection import _selections
from ..registry.bootstrap import NativeRunIdentity
from ..registry.event_store import RegistryConflict, StaleWriterError
from ..registry.object_store import ObjectIntegrityError
from ..registry.preserved_binding_contracts import PLAN_V2_TYPE, PLAN_V2_SCHEMA, MANIFEST_V2_TYPE
from ..registry.runtime_binding_contracts import PLAN_TYPE, freeze_candidate_document
from ..registry.schema_catalog import canonical_json
from ..registry.strict_contracts import ref_payload
from .candidate_plans import (
    CandidatePlanPublisher, _command_key, _copy_qualified, _copy_ref, _empty_hosts,
    _existing_plan_ref_at, _operation_refs, _pure_author_preflight, _read_candidate_plan_at,
    _record_ref, _resource_pair, _resource_selections,
)
from .materials import validate_closed_revision
from .preserved_candidate_plans import (
    _collect_preserved_plan_dependencies, _read_preserved_candidate_plan_at, read_preserved_candidate_plan,
)


def _selected_schema_projection(document):
    compiled = _load_compiled_net_offline(document['compiled'])
    declarations = {item.name: item for item in compiled.symbolic.logical_slots}
    if not set(document['preserved_slot_refs']) <= set(declarations):
        raise RegistryConflict('selected symbols differ from the frozen candidate inventory')
    return {declarations[name].schema: document['schema_refs'][declarations[name].schema]
        for name in document['preserved_slot_refs']}


def _registration_preflight(context, author, registration):
    _, declarations = _pure_author_preflight(context.db, context, PlanReadClosure(context.db, context), author)
    for (category, name), expected in declarations.declarations.items():
        if canonical_json(registration.declaration(category, name)) != expected:
            raise RegistryConflict('candidate HOST Registration differs from exact author material closure')


class PreservedCandidatePlanPublisher(CandidatePlanPublisher):
    """Opt-in v2 entry; the existing v1/default publisher is unchanged."""

    def publish(self, *, author_ref, identity, task_round_ref, authority_decision_ref, command_id,
            preserved_basis=None, selected_slot_refs=None, selected_schema_refs=None, command_context=None,
            entry_inputs=None, owner_resource_inputs=None, owner_input_resources=None):
        if self.core.writer_epoch != self.core.event_store.writer_epoch:
            raise StaleWriterError('candidate plan publisher uses a stale owner writer')
        author = _copy_qualified(author_ref, 'collaboration_net_revision/v1')
        principal = _copy_qualified(self.producer, 'principal/v1')
        slots, schemas = _selections({} if selected_slot_refs is None else selected_slot_refs,
            {} if selected_schema_refs is None else selected_schema_refs)
        basis = None if preserved_basis is None else _copy_basis(preserved_basis)
        if bool(slots) != (basis is not None) or (not slots and schemas):
            raise ValueError('nonempty preserved slots require one basis; empty slots require no basis/schemas')
        if type(identity) is not NativeRunIdentity or type(identity.branch_id) is not str or type(identity.protocol_versions) is not tuple:
            raise TypeError('candidate plans require a standard exact NativeRunIdentity')
        native = {field: ref_payload(_copy_ref(getattr(identity, field), kind)) for field, kind in (
            ('task_ref', 'task/v1'), ('run_ref', 'native_run_identity/v1'),
            ('task_branch_ref', 'task_branch/v1'), ('genesis_manifest_ref', 'native_genesis_manifest/v1'))}
        native.update(branch_id=identity.branch_id, protocol_versions=list(identity.protocol_versions))
        key = _command_key(self.core.task_id, principal.source_id, command_id)
        request = json.loads(freeze_candidate_document({
            'source_id': principal.source_id, 'command_id': command_id, 'run_identity': native,
            'author_ref': author.to_dict(), 'producer_principal_ref': principal.to_dict(),
            'principal_ref': ref_payload(principal.ref),
            'owner_task_ref': ref_payload(_copy_ref(self.gateway._task_ref, 'task/v1')),
            'bootstrap_ref': ref_payload(_copy_ref(self.gateway._bootstrap_ref, 'bootstrap_command/v1')),
            'task_round_ref': ref_payload(_copy_ref(task_round_ref, 'task_round/v1')),
            'authority_decision_ref': ref_payload(_copy_ref(authority_decision_ref, 'user_authority_decision/v1')),
            'command_context': {} if command_context is None else command_context,
            'preserved_slot_refs': {name: ref_payload(ref) for name, ref in slots.items()},
            'preserved_basis': None if basis is None else basis.to_dict(),
            **_resource_selections(entry_inputs, owner_resource_inputs, owner_input_resources)}))
        selected_schemas = {name: _resource_pair(ref) for name, ref in schemas.items()}
        # Discover the actual persisted version before compiling/prewriting. A
        # successful v1 request retains its original format and numeric IDs.
        with self.core.event_store.connect() as db:
            db.execute('BEGIN')
            context = _CandidateReadContext.from_core(self.core, db)
            existing = _existing_plan_ref_at(context, key)
            if existing is not None:
                saved = (_read_candidate_plan_at(context, existing) if existing.entity_type == PLAN_TYPE
                    else _read_preserved_candidate_plan_at(context, existing))
                document = saved.plan
                compared = {field: document.get(field, None) for field in request}
                if (not same(compared, request) or not same(selected_schemas, _selected_schema_projection(document))):
                    raise RegistryConflict('candidate command conflicts with its complete frozen request')
                _registration_preflight(context, author, self.registration)
                return saved
            _registration_preflight(context, author, self.registration)
        material = validate_closed_revision(self.core, author, self.registration)
        compiled = material.compiled
        plan_ref, manifest_ref = _record_ref(PLAN_V2_TYPE, key + ':plan'), _record_ref(MANIFEST_V2_TYPE, key + ':manifest')
        document = json.loads(freeze_candidate_document({
            'schema_version': PLAN_V2_SCHEMA, 'plan_ref': ref_payload(plan_ref), 'manifest_ref': ref_payload(manifest_ref),
            'graph_command_key': key + ':graph', 'operation_command_key': key + ':operations',
            'schema_refs': {item['key']: item['resource_ref']['ref'] for item in material.host_requirements['declaration_refs']
                if item['kind'] == 'schema' and item['key'] in compiled.source.required_schemas},
            'operation_refs': _operation_refs(compiled, key + ':operations'),
            'compiled': compiled.to_dict(), 'host_requirements': material.host_requirements,
            'host_bindings': _empty_hosts(compiled),
            'runtime_dependencies': {item.name: {} for item in compiled.symbolic.transitions},
            'host_inventory': {'resource_refs': [], 'artifact_refs': []}, 'dependency_evidence': [], **request}))
        if not same(selected_schemas, _selected_schema_projection(document)):
            raise RegistryConflict('selected schemas must be the exact frozen-author projection')
        with self.core.event_store.connect() as db:
            db.execute('BEGIN')
            document['dependency_evidence'] = _collect_preserved_plan_dependencies(_CandidateReadContext.from_core(self.core, db), document)
        frozen = freeze_candidate_document(document)
        try:
            self.core.publish_bytes(object_type=PLAN_V2_TYPE, logical_id=plan_ref.entity_id, version_id=plan_ref.version_id,
                payload=frozen.encode('utf-8'), metadata=document, media_type='application/json', schema_ref=PLAN_V2_SCHEMA,
                idempotency_key=key + ':plan')
        except ObjectIntegrityError as exc:
            # An unregistered prewrite is never successful replay authority.
            # Exact bytes may retry first admission; divergent bytes cannot
            # overwrite the stable command's immutable version path.
            raise RegistryConflict('candidate command conflicts with its first immutable complete plan') from exc
        saved = read_preserved_candidate_plan(self.core, plan_ref)
        if saved._plan_json != frozen:
            raise RegistryConflict('candidate command conflicts with its first immutable complete plan')
        return saved
