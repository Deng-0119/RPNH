"""Private fixed-scope producer-execution proof over one authorized frozen cut.

This is not the public product_origin query or a partial profile response.
The include evaluator shares this non-owning Core; pages/cursors remain absent.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..registry.identities import TypedId
from ..registry.models import EventEnvelope, VersionRef
from ..registry.resources import ResourceVersionRef
from ..registry.schema_catalog import canonical_json
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef, _ENTITY_TYPE
from .registry_read_contracts import (IndexQuery, TypedIndexClause, TypedPredicate,
    RegistryReadSessionError)
from .registry_typed_readers import (prepared_at, descriptor_at, qualify,
    _checkpoint_commit_ordinal)
from ._origin_core_contract import (CORE_RECORD_FIELDS, CORE_INDEX_FIELDS,
    RESOURCE_ROOT_FIELDS, RESOURCE_OUTPUT_FIELDS, CORE_FIELD_KINDS, INCLUDE_FIELD_KINDS,
    START_FIELDS, record_fields, core_contract)
from ._origin_query_context import _OriginQueryContext, _bytes_bound
from ._snapshot_producer_proof import _canonical_snapshot_producer


_SAFE_ERROR_CODES = frozenset({
    'INTEGRITY_FAILED', 'NOT_PRESENT_AT_CUT', 'ROOT_ORIGIN_UNSUPPORTED',
    'UNSUPPORTED_SETTLEMENT_SHAPE', 'LIMIT_EXCEEDED', 'ACCESS_CHANGED', 'SOURCE_UNAVAILABLE',
    'NOT_DISCLOSED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'READ_FAILED', 'UNSUPPORTED_ENTRY_TYPE',
    'UNSUPPORTED_READER_VERSION', 'AUTHORITY_NOT_CONFIGURED', 'CURSOR_MISMATCH', 'INVALID_CUT'})
# Static contract-only data. Hold both the safe mapping and encoded buffer's
# bound before protected reads; a later exhausted query never borrows live
# descriptor/cache reservations to serialize its error. This is not a new cap.
_ERROR_BUFFER_BYTES = 2 * max(_bytes_bound(RegistryReadSessionError(code).to_dict())
    for code in _SAFE_ERROR_CODES)


def _require(condition):
    if not condition:
        raise RegistryReadSessionError('INTEGRITY_FAILED')


def _ref(value, expected=None, *, nullable=False):
    if value is None and nullable:
        return None
    _require(isinstance(value, Mapping) and set(value) == {'entity_type', 'logical_id', 'version_id'})
    kind = value['entity_type']
    _require(type(kind) is str and _ENTITY_TYPE.fullmatch(kind) is not None
        and (expected is None or kind == expected))
    if expected is None:
        TypedId.parse(value['logical_id']); TypedId.parse(value['version_id'])
        return value
    stem = kind.split('/')[0]
    logical, version = {'resource_version': ('resource', 'resource_version'),
        'node_declaration': ('node', 'node_declaration_version'),
        'workspace_write_intent': ('write_intent', 'write_intent_version'),
        'plan_version': ('plan', 'plan_version')}.get(stem, (stem, stem + '_version'))
    TypedId.parse(value['logical_id'], expected=logical)
    TypedId.parse(value['version_id'], expected=version)
    return value


def _key(ref):
    return ref['entity_type'], ref['logical_id'], ref['version_id']


def _typed(ref):
    return VersionRef(ref['entity_type'], TypedId.parse(ref['logical_id']), TypedId.parse(ref['version_id']))


def _preauthorize(session, source, resource_root, include=('producer_execution',)):
    records = record_fields(resource_root, include)
    fixed_kinds = dict(CORE_FIELD_KINDS + INCLUDE_FIELD_KINDS)
    for operation, table in (('record', records), ('index', CORE_INDEX_FIELDS)):
        for kind, fixed in table:
            fields = fixed
            if len(fields) > session.limits.max_projection_fields:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            allowed = getattr(source.authority.scope, operation + '_fields').get(kind)
            if allowed is None or set(fields) - set(allowed):
                raise RegistryReadSessionError('NOT_DISCLOSED')
            try:
                actual = session._catalog.fields(kind)
                if set(fields) - set(actual):
                    raise RegistryReadSessionError('UNSUPPORTED_ENTRY_TYPE')
                if any(actual[field] != fixed_kinds[field] for field in fields):
                    raise RegistryReadSessionError('UNSUPPORTED_READER_VERSION')
                definition = source.resolved.core.catalog.require(kind, category='object')
                if definition.schema_ref != 'registry_v1/' + kind:
                    raise RegistryReadSessionError('UNSUPPORTED_READER_VERSION')
            except RegistryReadSessionError:
                raise
            except Exception as exc:
                raise RegistryReadSessionError(getattr(exc, 'code', 'UNSUPPORTED_ENTRY_TYPE')) from None
    dependencies = ('task/v1', 'task_round/v1', 'net_instance/v1', 'operation_binding/v1',
        'operation_execution_lease/v1', 'marking_delta/v1')
    if resource_root:
        dependencies += ('output_binding/v1', 'workspace_write_intent/v1')
    if 'start_inputs' in include or 'claims' in include:
        dependencies += ('petri_token/v1',)
    if 'start_inputs' in include:
        dependencies += ('resource_version/v1',)
    try:
        for kind in dependencies:
            _require(source.resolved.core.catalog.require(kind, category='object').schema_ref == 'registry_v1/' + kind)
    except Exception:
        raise RegistryReadSessionError('UNSUPPORTED_READER_VERSION') from None


class _CoreProof:
    def __init__(self, session, cut, source, context):
        self.session, self.cut, self.source, self.context = session, cut, source, context
        self.snapshot, self.core = context.snapshot, source.resolved.core
        self.task = source.selection.source_ref.to_dict()['ref']

    def event(self, event, contract, validator):
        def validate(_event):
            with self.context.scratch(event.payload):
                self._event_envelope(event)
                validator()
            return event
        return self.context.validate_event(event, contract, validate)

    def _event_envelope(self, event):
        # Reuse the original fact-envelope schema without recursively copying
        # its payload. IDs/parent positions and the shallow wire mapping are
        # bounded before construction; no parent endpoint is dereferenced.
        def size(value):
            if isinstance(value, TypedId):
                return 12 * (len(value.kind) + len(value.value) + 1) + 2
            if isinstance(value, tuple):
                return 2 + sum(size(item) + 1 for item in value)
            return self.context.bytes_bound(value)
        bound = 512 + sum(size(getattr(event, name)) + self.context.bytes_bound(name)
            for name in EventEnvelope.__dataclass_fields__ if name != 'ordinal')
        self.context.reserve('A', rows=len(event.parent_event_ids))
        with self.context.scratch(size=bound):
            envelope = {}
            for name in EventEnvelope.__dataclass_fields__:
                if name == 'ordinal':
                    continue
                value = getattr(event, name)
                envelope[name] = ([str(item) for item in value] if name == 'parent_event_ids'
                    else str(value) if isinstance(value, TypedId) else value)
            self.core.catalog.validate_fact_envelope(envelope)
            del envelope
        TypedId.parse(str(event.event_id), expected='event')
        TypedId.parse(str(event.transaction_id), expected='transaction')
        _require(event.envelope_version == 'v1' and event.criticality == 'authoritative'
            and event.payload_schema_ref == 'registry_v1/' + event.event_type
            and event.event_schema_version == event.event_type.rsplit('/', 1)[-1]
            and str(event.task_id) == self.task['logical_id']
            and type(event.ordinal) is int and 0 < event.ordinal <= self.snapshot.head.ordinal
            and type(event.stream_sequence) is int and event.stream_sequence > 0
            and type(event.aggregate_version) is int and event.aggregate_version > 0
            and type(event.writer_fencing_epoch) is int and event.writer_fencing_epoch >= 0)
        _require(len(self.context.events_by_id.get(str(event.event_id), ())) == 1)
        for value, kind in ((event.task_id, 'task'), (event.task_round_id, 'task_round'),
                            (event.net_instance_id, 'net_instance'), (event.producer_invocation_id, 'invocation')):
            if value is not None:
                TypedId.parse(str(value), expected=kind)
        self.core.catalog.validate_event_payload(event.event_type, event.payload)

    def commit(self, transaction):
        candidates = self.context.commits_by_transaction.get(transaction, ())
        # Charge rejected duplicates too; successful results are actually cached.
        for event in candidates:
            self.event(event, 'core-transaction/v3', lambda event=event: _require(
                event.event_type == 'transaction_committed/v1'
                and event.aggregate_type == 'transaction' and event.aggregate_id == transaction
                and event.stream_id == 'transaction:' + transaction
                and event.producer_invocation_id is None))
        _require(len(candidates) == 1)
        return candidates[0]

    def exact(self, ref, expected, *, root=False):
        ref = _ref(ref, expected)
        key = (self.cut.source_id, *_key(ref))
        def read():
            row = self.snapshot.objects.get(ref['version_id'])
            if row is None:
                raise RegistryReadSessionError('NOT_PRESENT_AT_CUT' if root else 'INTEGRITY_FAILED')
            _require(row['object_type'] == ref['entity_type'] and row['logical_id'] == ref['logical_id']
                and row['version_id'] == ref['version_id'] and row['schema_ref'] == 'registry_v1/' + ref['entity_type'])
            qualified = qualify(self.cut.source_id, {'resource_id': ref['logical_id'],
                'resource_version_id': ref['version_id']} if expected == 'resource_version/v1' else ref)
            body = (prepared_at(self.core, qualified, self.snapshot).metadata if expected == 'resource_version/v1'
                    else descriptor_at(self.core, qualified, self.snapshot))
            if expected == 'workspace_write_intent/v1':
                _require(body['write_intent_id'] == ref['logical_id'] and body['write_intent_version_id'] == ref['version_id'])
            elif expected == 'output_binding/v1':
                _require(body['output_binding_id'] == ref['logical_id'] and body['output_binding_version_id'] == ref['version_id'])
            elif expected not in ('task/v1', 'task_round/v1'):
                _require(body[expected.split('/')[0] + '_ref'] == ref) if expected != 'resource_version/v1' else None
            witnesses = self.context.events_by_id.get(row['published_event_id'], ())
            for witness in witnesses:
                def publication(witness=witness):
                    _require(witness.event_type == 'object_version_published/v1'
                        and witness.aggregate_type == row['object_type'] and witness.aggregate_id == row['logical_id']
                        and witness.stream_id == 'object:' + row['logical_id']
                        and str(witness.transaction_id) == row['transaction_id']
                        and (str(witness.producer_invocation_id) if witness.producer_invocation_id is not None else None)
                            == row['producer_invocation_id'])
                    with self.context.scratch(body):
                        _require(witness.payload == {name: row[name] for name in ('logical_id', 'version_id',
                            'object_type', 'size', 'media_type', 'schema_ref', 'storage_locator')} | {'metadata': body})
                    commit = self.commit(row['transaction_id'])
                    _require(witness.ordinal < commit.ordinal <= self.snapshot.publication_ordinals[ref['version_id']]
                        <= self.snapshot.head.ordinal)
                self.event(witness, ('core-object-publication/v3', *key), publication)
            _require(len(witnesses) == 1)
            return body
        return self.context.cached('origin-exact', key, 'core-exact-publication/v3', read)

    def public(self, ref, expected, fields, *, root=False):
        # P remains an explicit original record access. V is never a substitute.
        ref = _ref(ref, expected)
        qualified = qualify(self.cut.source_id, {'resource_id': ref['logical_id'],
            'resource_version_id': ref['version_id']} if expected == 'resource_version/v1' else ref)
        self.session._authorize(self.cut.source_id, qualified, 'record', fields)
        projection = self.session._catalog.read_exact(self.core, qualified, snapshot=self.snapshot, projection=fields)
        body = self.exact(ref, expected, root=root)
        derived = expected == 'transition_firing/v1' and any(field in START_FIELDS for field in fields)
        if derived:
            from ._product_origin_includes import _verify_start_projection
            _verify_start_projection(self.context, ref, projection, fields)
        with self.context.scratch(projection):
            _require(set(projection) == set(fields))
            for field in fields:
                if derived and field in START_FIELDS:
                    continue
                value = (body['reference_provenance'].get(field.removeprefix('provenance_'))
                    if field.startswith('provenance_') else qualify(self.cut.source_id, body['net_instance_ref'])
                    if field == 'net_ref' and expected == 'marking_checkpoint/v1' else body[field])
                _require(projection[field] == value)
        return body

    def producer(self, ref, producer, *, transaction=None):
        row = self.snapshot.objects[ref['version_id']]
        _require(row['producer_invocation_id'] == producer)
        if transaction is not None:
            _require(row['transaction_id'] == transaction)

    def checkpoint(self, ref, *, producer=None, execution=None, transaction=None):
        body = self.exact(ref, 'marking_checkpoint/v1')
        # Check the exact net's own identity/publication even on changed-net
        # shapes, without expanding its plan, bindings or token arrays.
        self.exact(body['net_instance_ref'], 'net_instance/v1')
        # Preserve the reader's unconditional witness predicate even for V.
        _checkpoint_commit_ordinal(body, self.snapshot)
        key = (self.cut.source_id, *_key(ref))
        def validate():
            witnesses = self.context.checkpoint_events(ref)
            for event in witnesses:
                def witness(event=event):
                    _require(event.event_type == 'marking_checkpoint_committed/v1'
                        and event.aggregate_type == 'marking_checkpoint'
                        and event.aggregate_id == body['net_instance_ref']['logical_id']
                        and event.stream_id == 'marking:' + body['net_instance_ref']['logical_id']
                        and str(event.transaction_id) == self.snapshot.objects[ref['version_id']]['transaction_id'])
                    for field in ('transition_firing_refs', 'workspace_revision_refs'):
                        self.context.reserve('A', rows=len(body.get(field, ())))
                    _require(all(event.payload.get(field) == body.get(field) for field in (
                        'net_instance_ref', 'team_design_root_ref', 'previous_checkpoint_ref', 'settlement_delta_ref',
                        'transition_firing_refs', 'workspace_revision_refs', 'settled')))
                    _require(event.ordinal < self.commit(str(event.transaction_id)).ordinal)
                self.event(event, ('core-checkpoint-witness/v3', *key), witness)
            _require(len(witnesses) == 1)
            return witnesses[0]
        witness = self.context.cached('origin-checkpoint', key, 'core-checkpoint/v3', validate, category=None)
        if execution is not None:
            self.execution_event(witness, execution)
        if producer is not None:
            _require(str(witness.producer_invocation_id) == producer)
        if transaction is not None:
            _require(str(witness.transaction_id) == transaction)
        return body

    @staticmethod
    def execution_event(event, invocation):
        _require(str(event.task_id) == invocation['task_ref']['logical_id']
            and str(event.task_round_id) == invocation['task_round_ref']['logical_id']
            and str(event.net_instance_id) == invocation['net_instance_ref']['logical_id']
            and str(event.producer_invocation_id) == invocation['invocation_ref']['logical_id']
            and event.producer_principal == invocation['principal_ref']['logical_id'])

    def resource(self, ref, body, invocation):
        ctx = self.context
        direct, origin = body['reference_provenance'], body['origin']
        _require(set(direct) == {'schema_version', 'producer_invocation_ref', 'operation_binding_ref',
            'derived_from_refs', 'tool_evidence_refs', 'contributor_refs', 'supersedes_ref', 'intended_consumer', 'publication'}
            and direct['schema_version'] == 'resource_reference_provenance/v1')
        for field, expected in (('derived_from_refs', 'resource_version/v1'), ('tool_evidence_refs', 'agent_action/v2'),
                                ('contributor_refs', None)):
            values = direct[field]
            _require(type(values) is list)
            ctx.reserve('A', rows=len(values))
            with ctx.scratch(values):
                encoded = []
                for value in values:
                    _ref(value, expected)
                    encoded.append(canonical_json(value))
                _require(encoded == sorted(encoded) and len(encoded) == len(set(encoded)))
                del encoded
        _require(direct['tool_evidence_refs'] == [])
        _ref(direct['supersedes_ref'], 'resource_version/v1', nullable=True)
        _ref(body['lifetime_ref'])
        schema_authority = body['content_schema_authority_ref']
        if schema_authority is not None:
            if set(schema_authority) == {'resource_id', 'resource_version_id'}:
                TypedId.parse(schema_authority['resource_id'], expected='resource')
                TypedId.parse(schema_authority['resource_version_id'], expected='resource_version')
            else:
                _ref(schema_authority)
        I, B = invocation['invocation_ref'], invocation['operation_binding_ref']
        _require(body['producer_ref'] == direct['producer_invocation_ref'] == I
            and direct['operation_binding_ref'] == B and body['task_ref'] == invocation['task_ref']
            and body['round_ref'] == invocation['task_round_ref'] and body['net_ref'] == invocation['net_instance_ref']
            and origin.get('kind') == body['origin_kind'])
        with ctx.scratch(body):
            _require(direct['publication'] == {'origin_kind': body['origin_kind'], 'primary_ref': origin.get('primary_ref'),
                'secondary_ref': origin.get('secondary_ref'), 'lifetime_ref': body['lifetime_ref'], 'size': body['size'],
                'media_type': body['media_type'], 'content_schema_ref': body['content_schema_ref'],
                'content_schema_authority_ref': body['content_schema_authority_ref']})
        if body['origin_kind'] == 'petri_output':
            output = self.exact(origin['primary_ref'], 'output_binding/v1')
            secondary = _ref(origin['secondary_ref'], nullable=True)
            _require(output['task_round_ref'] == invocation['task_round_ref']
                and output['net_ref'] == invocation['net_instance_ref'] and output['node_ref'] == invocation['own_node_ref']
                and invocation['activation_ref'] == secondary
                and direct['intended_consumer'] == {'boundary': 'petri_input', 'consumer_ref': origin['primary_ref']})
        else:
            intent = self.exact(origin['secondary_ref'], 'workspace_write_intent/v1')
            _require(origin['primary_ref'] == B and intent['operation_binding_ref'] == B
                and direct['intended_consumer'] == {'boundary': 'tool_result', 'consumer_ref': origin['secondary_ref']})
        _canonical_snapshot_producer(self.snapshot, ResourceVersionRef(TypedId.parse(ref['logical_id']),
            TypedId.parse(ref['version_id'])), _typed(I), ctx.reservation)

    def run(self, root, resource_root):
        fields = dict(CORE_RECORD_FIELDS)
        root_ref = ({'entity_type': 'resource_version/v1', 'logical_id': str(root.ref.resource_id),
            'version_id': str(root.ref.resource_version_id)} if resource_root else root.to_dict()['ref'])
        P = R = None
        if resource_root:
            P = self.public(root_ref, 'resource_version/v1', RESOURCE_ROOT_FIELDS, root=True)
            if P['origin_kind'] not in ('petri_output', 'workspace_write') or P['producer_ref']['entity_type'] != 'invocation/v1':
                raise RegistryReadSessionError('ROOT_ORIGIN_UNSUPPORTED')
            Iref = P['producer_ref']
        else:
            R = self.public(root_ref, 'operation_result/v1', fields['operation_result/v1'], root=True)
            Iref = R['invocation_ref']
        I = self.public(Iref, 'invocation/v1', fields['invocation/v1'])
        Fref = _ref(I['own_transition_firing_ref'], 'transition_firing/v1')
        F = self.public(Fref, 'transition_firing/v1', fields['transition_firing/v1'])
        T, Q, N, B, L, Ka = (I[key] for key in ('task_ref', 'task_round_ref', 'net_instance_ref',
            'operation_binding_ref', 'operation_execution_lease_ref', 'admission_marking_checkpoint_ref'))
        _require(T == self.task and F['task_ref'] == T and F['task_round_ref'] == Q and F['net_instance_ref'] == N
            and F['operation_binding_ref'] == B and F['node_ref'] == I['own_node_ref']
            and F['admission_marking_checkpoint_ref'] == Ka)
        _ref(I['own_node_ref'], 'node_declaration/v1'); _ref(I['principal_ref'], 'principal/v1')
        for ref, kind in ((T, 'task/v1'), (Q, 'task_round/v1'), (N, 'net_instance/v1'), (B, 'operation_binding/v1')):
            self.exact(ref, kind)
        Aref, Dc = F['firing_admission_ref'], _ref(F['claim_marking_delta_ref'], 'marking_delta/v1')
        A = self.public(Aref, 'firing_admission/v1', fields['firing_admission/v1'])
        lease = self.exact(L, 'operation_execution_lease/v1')
        _require(A['transition_firing_ref'] == Fref and A['invocation_ref'] == Iref
            and A['operation_execution_lease_ref'] == L and A['claim_marking_delta_ref'] == Dc
            and A['admission_marking_checkpoint_ref'] == Ka and lease['invocation_ref'] == Iref)
        admission_tx = self.snapshot.objects[Iref['version_id']]['transaction_id']
        self.producer(Iref, None)
        for ref in (Fref, Aref, L):
            self.producer(ref, Iref['logical_id'], transaction=admission_tx)
        _require(self.checkpoint(Ka)['net_instance_ref'] == N)
        if P is not None:
            self.resource(root_ref, P, I)
        self.context.reserve(size=self.context.bytes_bound(Fref) * 4 + 8192)
        spec = IndexQuery((self.cut.source_id,), (TypedIndexClause('firing_completion/v2',
            (TypedPredicate('transition_firing_ref', 'eq', Fref),), dict(CORE_INDEX_FIELDS)['firing_completion/v2']),),
            page_size=1, cuts={self.cut.source_id: self.cut})
        collected = self.session._collect_index(spec, _origin_context=self.context)
        if collected.failures:
            raise RegistryReadSessionError(collected.failures.get(self.cut.source_id, 'READ_FAILED'))
        _require(collected.cuts == {self.cut.source_id: self.cut} and len(collected.entries) == 1)
        Cref = collected.entries[0]['entry_ref']['ref']
        C = self.public(Cref, 'firing_completion/v2', fields['firing_completion/v2'])
        Rref = _ref(C['operation_result_ref'], 'operation_result/v1')
        if R is None:
            R = self.public(Rref, 'operation_result/v1', fields['operation_result/v1'] + RESOURCE_OUTPUT_FIELDS)
        else:
            _require(Rref == root_ref)
        _require(C['transition_firing_ref'] == R['transition_firing_ref'] == Fref
            and C['invocation_ref'] == R['invocation_ref'] == Iref
            and C['business_outcome'] == R['business_outcome'] == 'completed')
        Dsref, Ksref = C['marking_delta_ref'], C['successor_checkpoint_ref']
        Ds = self.exact(Dsref, 'marking_delta/v1')
        Ks = self.public(Ksref, 'marking_checkpoint/v1', fields['marking_checkpoint/v1'])
        for array in (Ds['transition_firing_refs'], Ds['operation_binding_refs'], Ks['transition_firing_refs']):
            self.context.reserve('A', rows=len(array))
        _require(Ds['phase'] == 'settlement' and Ds['net_instance_ref'] == N
            and Ds['transition_firing_refs'] == [Fref] and Ds['operation_binding_refs'] == [B]
            and Ks['settled'] is True and Ks['settlement_delta_ref'] == Dsref and Ks['transition_firing_refs'] == [Fref])
        refs = (Fref, Cref, Rref, Dsref, Ksref)
        relevant = []
        for event in self.context.events_labelled('transition_firing_settled/v1'):
            touches = event.aggregate_id == Fref['logical_id']
            if not touches:
                for key, ref in zip(('transition_firing_ref', 'firing_completion_ref', 'operation_result_ref',
                        'marking_delta_ref', 'successor_checkpoint_ref'), refs):
                    value = event.payload.get(key)
                    if isinstance(value, Mapping) and value.get('logical_id') == ref['logical_id']:
                        touches = True
                        break
            if touches:
                self.context.reserve(size=256)
                relevant.append(event)
        def settled_candidate(event):
            with self.context.scratch(event.payload):
                self._event_envelope(event)
                _require(event.event_type == 'transition_firing_settled/v1'
                    and event.aggregate_type == 'transition_firing' and event.aggregate_id == Fref['logical_id']
                    and event.stream_id == 'firing-claims:' + N['version_id'])
                self.execution_event(event, I)
                payload = event.payload
                _require(all(payload[key] == ref for key, ref in zip(('transition_firing_ref', 'firing_completion_ref',
                    'operation_result_ref', 'marking_delta_ref', 'successor_checkpoint_ref'), refs))
                    and payload['business_outcome'] == 'completed')
                _require(_ref(payload['transaction_ref'], 'transaction/v1')['logical_id'] == str(event.transaction_id))
                _ref(payload['event_ref'], 'fact_event/v1')
                _require(payload['workspace_access_set_ref'] == C['workspace_access_set_ref'] == R['workspace_access_set_ref']
                    and payload['workspace_revision_ref'] == C['workspace_revision_ref'])
                _ref(payload['workspace_access_set_ref'], 'workspace_access_set/v1', nullable=True)
                _ref(payload['workspace_revision_ref'], 'workspace_revision/v1', nullable=True)
                _require(event.ordinal < self.commit(str(event.transaction_id)).ordinal)
                for ref in (Cref, Dsref, Ksref):
                    self.producer(ref, Iref['logical_id'], transaction=str(event.transaction_id))
            return event
        self.context.validate_events(relevant, ('core-settled/v3', *_key(Fref)), settled_candidate)
        _require(len(relevant) == 1)
        self.producer(Rref, Iref['logical_id'])
        if resource_root:
            self.producer(root_ref, Iref['logical_id'])
        self.checkpoint(Ksref, producer=Iref['logical_id'], execution=I, transaction=str(relevant[0].transaction_id))
        Kp = self.checkpoint(_ref(Ks['previous_checkpoint_ref'], 'marking_checkpoint/v1'))
        if Ks['net_instance_ref'] != N:
            raise RegistryReadSessionError('UNSUPPORTED_SETTLEMENT_SHAPE')
        _require(Kp['net_instance_ref'] == N)
        role = 'operation_result'
        if resource_root:
            outputs = R['output_resource_refs']
            self.context.reserve('O', rows=len(outputs))
            member = False
            with self.context.scratch(outputs):
                identities = set()
                for value in outputs:
                    _ref(value, 'resource_version/v1')
                    identity = _key(value)
                    _require(identity not in identities)
                    identities.add(identity)
                    member = member or value == root_ref
                del identities
            role = 'registered_output' if member else 'invocation_produced_resource'
        self.context.reserve(size=sum(self.context.bytes_bound(ref) for ref in (Iref, Fref, Cref, Rref, B))
            + 5 * self.context.bytes_bound(self.cut.source_id) + 4096)
        return {key: qualify(self.cut.source_id, ref) for key, ref in (
            ('producer_invocation_ref', Iref), ('transition_firing_ref', Fref), ('firing_completion_ref', Cref),
            ('operation_result_ref', Rref), ('operation_binding_ref', B))} | {'root_role': role}


def _evaluate_origin_core(verifier, root, resource_root):
    """Non-owning evaluation seam; the caller retains and closes its owner."""
    return verifier.run(root, resource_root)


def _verify_origin_core(session, *, root, at_cut):
    """Compatible private six-field Core facade; never a page."""
    return _owned_origin_evaluation(session, root=root, at_cut=at_cut, include=('producer_execution',),
        with_relations=False)


def _clear_exception_frames(error):
    """Release completed protected frames only after final safe delivery checks."""
    from traceback import clear_frames
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for linked in (current.__context__, current.__cause__):
            if linked is not None:
                pending.append(linked)
        clear_frames(current.__traceback__)
        current.__traceback__ = None
        current.__context__ = None
        current.__cause__ = None


def _owned_origin_evaluation(session, *, root, at_cut, include, with_relations):
    # The inner owning frame has completed (including close and final checks)
    # before it is cleared. Raise outside except so the returned safe exception
    # does not retain original private candidate/row frames in its context.
    try:
        return _owned_origin_evaluation_impl(session, root=root, at_cut=at_cut,
            include=include, with_relations=with_relations)
    except Exception as exc:
        code = getattr(exc, 'code', 'INTEGRITY_FAILED')
        if code not in _SAFE_ERROR_CODES | {'INVALID_ROOT', 'INVALID_QUERY', 'UNSUPPORTED_RELATION'}:
            code = 'INTEGRITY_FAILED'
        _clear_exception_frames(exc)
    raise RegistryReadSessionError(code) from None


def _owned_origin_evaluation_impl(session, *, root, at_cut, include, with_relations):
    resource_root = type(root) is SourceQualifiedResourceRef
    if not resource_root and (type(root) is not SourceQualifiedVersionRef
            or root.ref.entity_type != 'operation_result/v1'):
        raise RegistryReadSessionError('INVALID_ROOT')
    try:
        if resource_root:
            _require(type(root.ref) is ResourceVersionRef)
            TypedId.parse(str(root.ref.resource_id), expected='resource')
            TypedId.parse(str(root.ref.resource_version_id), expected='resource_version')
        else:
            _ref(root.to_dict()['ref'], 'operation_result/v1')
    except Exception:
        raise RegistryReadSessionError('INVALID_ROOT') from None
    cut = session.validate_cut(at_cut)
    if root.source_id != cut.source_id:
        raise RegistryReadSessionError('INVALID_ROOT')
    source = session._sources[cut.source_id]
    _preauthorize(session, source, resource_root, include)
    context = None
    error_reserved = False
    try:
        try:
            context = _OriginQueryContext(session, session._cuts[cut.cut_id][1])
            context.reserve(size=_ERROR_BUFFER_BYTES)
            error_reserved = True
            # Contract/request copies and their canonical buffer are bounded
            # before construction. This slice retains no continuation state.
            size = 2 * (context.bytes_bound(root.to_dict()) + context.bytes_bound(cut.to_dict())
                + context.bytes_bound(core_contract()) + context.bytes_bound(include))
            with context.scratch(size=size):
                request_bytes = canonical_json({'root': root.to_dict(), 'cut': cut.to_dict(), 'contract': core_contract(), 'include': include})
                if len(request_bytes) > session.limits.max_query_bytes:
                    raise RegistryReadSessionError('LIMIT_EXCEEDED')
                del request_bytes
            verifier = _CoreProof(session, cut, source, context)
            if with_relations:
                from ._product_origin_includes import _evaluate_origin_includes
                result = _evaluate_origin_includes(verifier, root, resource_root, include)
            else:
                result = _evaluate_origin_core(verifier, root, resource_root)
            with context.scratch(result):
                encoded = canonical_json(result)
                if not with_relations and len(encoded) > session.limits.max_response_bytes:
                    raise RegistryReadSessionError('LIMIT_EXCEEDED')
                del encoded
        except Exception as exc:
            code = getattr(exc, 'code', 'INTEGRITY_FAILED')
            if code == 'NOT_PRESENT_AT_CUT':
                root_version = str(root.ref.resource_version_id if resource_root else root.ref.version_id)
                code = code if context is not None and root_version not in context.snapshot.objects else 'INTEGRITY_FAILED'
            if code not in _SAFE_ERROR_CODES:
                code = 'INTEGRITY_FAILED'
            error = RegistryReadSessionError(code)
            if error_reserved:
                if len(canonical_json(error.to_dict())) > session.limits.max_response_bytes:
                    error = RegistryReadSessionError('LIMIT_EXCEEDED')
                    canonical_json(error.to_dict())
            # Construction/first-reservation failure precedes protected reads.
            # Later failures serialize within the pre-owned safe error buffer.
            _final_delivery_check(session, cut, context if error_reserved else None)
            raise error from None
        _final_delivery_check(session, cut, context)
        return result
    finally:
        if context is not None:
            context.close()


def _final_delivery_check(session, cut, context):
    session.final_recheck((cut.source_id,))
    session._alive()
    if context is not None:
        try:
            # The final trusted callback can retain another cut or cursor.
            # Refresh the baseline even when no further projection is built.
            context.reserve()
        except RegistryReadSessionError:
            error = RegistryReadSessionError('LIMIT_EXCEEDED')
            canonical_json(error.to_dict())  # held fixed error-buffer allowance
            session.final_recheck((cut.source_id,))
            session._alive()
            raise error from None
    session._alive()
