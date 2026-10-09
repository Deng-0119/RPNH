"""Read original H7 acceptance history without minting or redelivering permission.

The only durable authority is the supplied original Registry. The private SQL
cut is a SELECT-only projection of its existing tables in one read transaction;
it creates no database, view, ledger, marker, cache or recovery command. A valid
result says nothing about OS authentication, in-memory stop state or receipt
delivery. Those boundaries remain unimplemented in this offline core.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from jsonschema.exceptions import ValidationError
from typing import Literal

from .event_store import EventStore, RegistryConflict, RegistryCorruptError, fact_event_envelope
from .identities import TypedId
from .models import PendingEvent, TypedRelation, VersionRef
from .object_store import ObjectIntegrityError
from .publication import _ref_payload, _version_from_payload
from .schema_catalog import SchemaGovernanceError, canonical_json
from ._event_store.collaboration_descriptors import descriptor_store, readable_descriptor
from ._event_store.source_identity import read_source_binding
from . import parent_child as h7

VALID = 'HISTORICAL_ACCEPTANCE_VALID'
INVALID = 'HISTORICAL_ACCEPTANCE_INVALID'
UNAVAILABLE = 'HISTORICAL_ACCEPTANCE_UNAVAILABLE'


@dataclass(frozen=True, slots=True)
class ChildAcceptanceHistoryAssertion:
    """Expected external origin identity and digests, never authority itself.

    Compute these two JSON digests from the exact copies in a child origin.
    The classifier reads and verifies the original bytes independently. Neither
    these assertions nor a successful classification can create native evidence.
    """
    source_id: str
    binding_ref: VersionRef
    run_ref: VersionRef
    task_ref: VersionRef
    slot_id: str
    acceptance_ref: VersionRef
    transaction_id: TypedId
    event_id: TypedId
    commit_ordinal: int
    acceptance_sha256: str
    intent_sha256: str
    initial_declaration_digest: str


@dataclass(frozen=True, slots=True)
class ChildAcceptanceHistoryProof:
    source_id: str
    binding_ref: VersionRef
    run_ref: VersionRef
    task_ref: VersionRef
    acceptance_ref: VersionRef
    intent_ref: VersionRef
    dispatch_ref: VersionRef
    worker_ref: VersionRef
    firing_ref: VersionRef
    invocation_ref: VersionRef
    lease_ref: VersionRef
    start_event_id: TypedId
    transaction_id: TypedId
    event_id: TypedId
    commit_ordinal: int
    observed_registry_ordinal: int
    writer_epoch_at_acceptance: int
    execution_generation_at_acceptance: int
    acceptance_sha256: str
    intent_sha256: str
    request_digest: str
    public_material_digest: str
    envelope_digest: str
    initial_declaration_digest: str


@dataclass(frozen=True, slots=True)
class ChildAcceptanceHistoryResult:
    status: Literal['HISTORICAL_ACCEPTANCE_VALID', 'HISTORICAL_ACCEPTANCE_INVALID',
                    'HISTORICAL_ACCEPTANCE_UNAVAILABLE']
    reason: str
    proof: ChildAcceptanceHistoryProof | None = None


class _Unavailable(Exception):
    pass


class _CommittedCut:
    """Internal SELECT-only relation projection, always on the original snapshot.

    A cut ends at one complete committed transaction. Publication state is
    derived from its real promotion transaction, never an ABANDONED enum.
    registry_meta's epoch is a historical transaction fact, not today's fence.
    Every non-cut Registry metadata value is immutable identity and independently
    checked against the current source binding before this adapter is used.
    """
    def __init__(self, db, ordinal, epoch):
        if type(ordinal) is not int or ordinal < 1 or type(epoch) is not int or epoch < 0:
            raise RegistryConflict('invalid historical cut')
        self._db = db
        closed = ("SELECT t.* FROM main.transactions t WHERE t.status='committed' "
            "AND EXISTS (SELECT 1 FROM main.events e WHERE e.transaction_id=t.transaction_id "
            f"AND e.event_type='transaction_committed/v1' AND e.ordinal<={ordinal})")
        promoted = "p.published_transaction_id IN (SELECT transaction_id FROM transactions)"
        self._prefix = f"""WITH
        transactions AS ({closed}),
        events AS (SELECT e.* FROM main.events e JOIN transactions t USING(transaction_id) WHERE e.ordinal<={ordinal}),
        objects AS (SELECT o.* FROM main.objects o JOIN transactions t USING(transaction_id)),
        relations AS (SELECT r.* FROM main.relations r JOIN transactions t USING(transaction_id)),
        outbox AS (SELECT o.* FROM main.outbox o JOIN transactions t USING(transaction_id)),
        firing_publications AS (SELECT p.firing_version_id,p.firing_logical_id,p.invocation_version_id,
            p.invocation_logical_id,p.net_version_id,p.operation_binding_version_id,p.admission_checkpoint_version_id,
            CASE WHEN {promoted} THEN p.state ELSE 'PROVISIONAL' END AS state,p.opened_transaction_id,
            CASE WHEN {promoted} THEN p.published_transaction_id END AS published_transaction_id,
            CASE WHEN {promoted} THEN p.operation_result_version_id END AS operation_result_version_id,
            CASE WHEN {promoted} THEN p.workspace_lineage_id END AS workspace_lineage_id,
            CASE WHEN {promoted} THEN p.workspace_revision_version_id END AS workspace_revision_version_id,
            CASE WHEN {promoted} THEN p.marking_checkpoint_version_id END AS marking_checkpoint_version_id
            FROM main.firing_publications p JOIN transactions t ON t.transaction_id=p.opened_transaction_id),
        firing_temporary_members AS (SELECT m.* FROM main.firing_temporary_members m JOIN transactions t USING(transaction_id)),
        registry_meta AS (SELECT key,CASE WHEN key='writer_epoch' THEN '{epoch}' ELSE value END AS value FROM main.registry_meta),
        stream_heads AS (SELECT stream_id,MAX(stream_sequence) AS sequence,NULL AS event_hash FROM events GROUP BY stream_id),
        task_control_heads AS (SELECT task_id,COALESCE(MAX(task_control_sequence),0) AS sequence FROM events GROUP BY task_id)
        """

    def execute(self, sql, parameters=()):
        if not sql.lstrip().upper().startswith('SELECT '):
            raise RegistryConflict('historical validation is SELECT-only')
        return self._db.execute(self._prefix + sql, parameters)


def _pending(row):
    # Persisted occurrence time is generated during commit, not command input.
    return PendingEvent(row['event_type'], row['criticality'], row['stream_id'],
        row['aggregate_id'], row['aggregate_type'], row['idempotency_key'], row['command_id'],
        json.loads(row['payload_json']), row['payload_schema_ref'],
        task_control=row['task_control_sequence'] is not None,
        causation_event_id=TypedId.parse(row['causation_event_id']) if row['causation_event_id'] else None,
        parent_event_ids=tuple(TypedId.parse(v) for v in json.loads(row['parent_event_ids_json'])),
        producer_principal=row['producer_principal'],
        producer_invocation_id=TypedId.parse(row['producer_invocation_id']) if row['producer_invocation_id'] else None)


def _relation(row, event):
    from .models import ObjectRef
    def ref(value):
        value = json.loads(value)
        if set(value) == {'entity_type', 'entity_id', 'version_id'}:
            return VersionRef(value['entity_type'], TypedId.parse(value['entity_id']), TypedId.parse(value['version_id']))
        if set(value) == {'entity_type', 'entity_id'}:
            return ObjectRef(value['entity_type'], TypedId.parse(value['entity_id']))
        raise RegistryConflict('malformed exact relation endpoint')
    payload = json.loads(event['payload_json'])
    return TypedRelation(TypedId.parse(row['relation_id']), row['relation_type'],
        ref(row['source_json']), ref(row['target_json']), row['strength'], json.loads(row['metadata_json']),
        TypedId.parse(event['producer_invocation_id']) if event['producer_invocation_id'] else None,
        event['producer_invocation_id'] is None)


class _HistoryReads:
    """Ephemeral, integrity-checked dependency reads; never an exported raw reader."""
    def __init__(self, store, db, original_db, task_id, firing):
        self.store, self.db, self.task_id, self.firing = store, db, task_id, firing
        self.original_db = original_db
        self.objects = descriptor_store(store)
        self.checked_transactions = set()
        self.checked_objects = {}
        self.pending_objects = []

    def member(self, kind, identity, transaction, required=None):
        rows = self.db.execute('SELECT * FROM firing_temporary_members WHERE member_kind=? AND member_identity=?',
            (kind, identity)).fetchall()
        expected = [] if required is None else [(required, transaction)]
        actual = [(r['firing_version_id'], r['transaction_id']) for r in rows]
        if sorted(actual) != sorted(expected):
            raise RegistryConflict('history member ownership differs from original transaction')

    def transaction(self, transaction):
        if transaction in self.checked_transactions:
            return
        tx = self.db.execute('SELECT * FROM transactions WHERE transaction_id=?', (transaction,)).fetchone()
        rows = self.db.execute('SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal', (transaction,)).fetchall()
        terminals = [r for r in rows if r['event_type'] == 'transaction_committed/v1']
        if (tx is None or tx['status'] != 'committed' or tx['task_id'] != str(self.task_id)
                or len(terminals) != 1 or terminals[0] != rows[-1]):
            raise RegistryConflict('history requires a complete original committed batch')
        # Verify real current heads/outbox as well as projected historical heads.
        # Do not let a cut hide an extra event appended after its claimed commit.
        original_rows = self.original_db.execute(
            'SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal', (transaction,)).fetchall()
        if [tuple(r) for r in original_rows] != [tuple(r) for r in rows]:
            raise RegistryConflict('history cut truncates its original transaction')
        if [r['ordinal'] for r in rows] != list(range(rows[0]['ordinal'], rows[-1]['ordinal'] + 1)):
            raise RegistryConflict('history transaction event positions are not contiguous')
        self.store._verified_persisted_event_record(self.original_db, original_rows[-1])
        self.store._verified_persisted_event_record(self.db, terminals[0])
        pending = []
        for row in rows:
            event = self.store._row_to_envelope(row)
            envelope = fact_event_envelope(event)
            for field in ('stream_sequence','aggregate_version','task_control_sequence','writer_fencing_epoch'):
                envelope[field] = row[field]
            self.store.catalog.validate_fact_envelope(envelope)
            definition = self.store.catalog.require(event.event_type, category='event', criticality=event.criticality)
            self.store.catalog.validate_event_payload(event.event_type, event.payload, criticality=event.criticality)
            if event.payload_schema_ref != definition.schema_ref:
                raise RegistryConflict('history event declared schema differs')
            pending.append(_pending(row))
        objects = []
        for row in rows:
            if row['event_type'] != 'object_version_published/v1':
                continue
            value = json.loads(row['payload_json'])
            obj = self.db.execute('SELECT * FROM objects WHERE published_event_id=? AND transaction_id=?',
                (row['event_id'], transaction)).fetchone()
            if obj is None:
                raise RegistryConflict('history publication has no same-transaction object')
            prepared = h7._prepared(obj)
            expected = {key: obj[key] for key in ('logical_id','version_id','object_type','size','media_type','schema_ref','storage_locator')}
            expected['metadata'] = dict(prepared.metadata)
            if value != expected:
                raise RegistryConflict('history object publication differs from registered envelope')
            objects.append(prepared)
        relations = []
        for row in rows:
            if row['event_type'] != 'relation_published/v1':
                continue
            rel = self.db.execute('SELECT * FROM relations WHERE published_event_id=? AND transaction_id=?',
                (row['event_id'], transaction)).fetchone()
            if rel is None:
                raise RegistryConflict('history relation is not in its publication transaction')
            relation = _relation(rel, row)
            from .event_store import _ref_json
            expected = {'relation_id': str(relation.relation_id), 'relation_type': relation.relation_type,
                'source': _ref_json(relation.source), 'target': _ref_json(relation.target),
                'strength': relation.strength, 'metadata': dict(relation.metadata)}
            if json.loads(row['payload_json']) != expected:
                raise RegistryConflict('history relation differs from its publication')
            self.store.catalog.require(relation.relation_type, category='relation')
            if relation.strength == 'strong':
                for endpoint in (relation.source, relation.target):
                    if type(endpoint) is VersionRef:
                        self.pending_objects.append(_ref_payload(endpoint))
                    elif self.db.execute('SELECT 1 FROM objects WHERE object_type=? AND logical_id=?',
                            (endpoint.entity_type, str(endpoint.entity_id))).fetchone() is None:
                        raise RegistryConflict('history strong relation endpoint is absent')
            relations.append(relation)
        if json.loads(terminals[0]['payload_json']) != {
                'object_count':len(objects), 'relation_count':len(relations), 'fact_count':len(rows)-1}:
            raise RegistryConflict('history committed batch counts differ')
        command = json.loads(tx['command_json'])
        from .event_store import _transaction_command_material, _ref_json
        material = _transaction_command_material(branch_id=rows[0]['branch_id'],
            task_round_id=rows[0]['task_round_id'], net_instance_id=rows[0]['net_instance_id'],
            objects=[{'object_type':o.object_type,'logical_id':str(o.logical_id),'version_id':str(o.version_id),
                'metadata':o.metadata,'producer_invocation_id':str(o.producer_invocation_id) if o.producer_invocation_id else None} for o in objects],
            events=[{'event_type':e.event_type,'stream_id':e.stream_id,'aggregate_id':e.aggregate_id,
                'aggregate_type':e.aggregate_type,'criticality':e.criticality,'payload':e.payload,
                'payload_schema_ref':e.payload_schema_ref,'task_control':e.task_control,
                'producer_principal':e.producer_principal,'producer_invocation_id':str(e.producer_invocation_id) if e.producer_invocation_id else None} for e in pending],
            relations=[{'relation_id':str(r.relation_id),'relation_type':r.relation_type,'source':_ref_json(r.source),
                'target':_ref_json(r.target),'strength':r.strength,'metadata':r.metadata,
                'producer_invocation_id':str(r.producer_invocation_id) if r.producer_invocation_id else None,
                'system_owned':r.system_owned} for r in relations],
            expected_snapshot_predecessors=command.get('expected_snapshot_predecessors', {}),
            expected_dependency_root_predecessor=command.get('expected_dependency_root_predecessor'),
            workspace_head_advances=command.get('workspace_head_advances', ()))
        if material != command or any(r['branch_id'] != command['branch_id'] or r['task_round_id'] != command['task_round_id']
                or r['net_instance_id'] != command['net_instance_id'] for r in rows):
            raise RegistryConflict('history command differs from original transaction material')
        roots = set()
        producers = {str(v.producer_invocation_id) for v in (*objects, *pending, *relations) if v.producer_invocation_id is not None}
        for producer in producers:
            owned = self.db.execute('SELECT * FROM firing_publications WHERE invocation_logical_id=?', (producer,)).fetchall()
            if len(owned) > 1:
                raise RegistryConflict('history producer belongs to multiple roots')
            if owned:
                roots.add(owned[0]['firing_version_id'])
        # Opening transaction includes its firing root even though owner-produced.
        roots.update(r['firing_version_id'] for r in self.db.execute(
            'SELECT firing_version_id FROM firing_publications WHERE opened_transaction_id=?', (transaction,)))
        if len(roots) > 1:
            raise RegistryConflict('history transaction has multiple firing roots')
        root = next(iter(roots), None)
        if root is not None and root != self.firing:
            # Ordinary CanonicalView includes properly published dependencies.
            # This never canonicalizes the acceptance's own provisional root.
            from ._event_store.collaboration_descriptors import _canonical_closure
            if not _canonical_closure(self.db, self.task_id, transaction_id=transaction,
                    members=(('transaction', transaction),)):
                raise RegistryConflict('history dependency crosses provisional firing roots')
        self.member('transaction', transaction, transaction, root)
        for obj in objects:
            self.member('object', str(obj.version_id), transaction, root)
            self.pending_objects.append(_ref_payload(VersionRef(obj.object_type,obj.logical_id,obj.version_id)))
        for row in rows:
            self.member('event', row['event_id'], transaction, root)
        for rel in relations:
            self.member('relation', str(rel.relation_id), transaction, root)
        # Membership is an exact inventory, not merely a list of required
        # entries. An orphan retained member must not survive this cut.
        expected_members = set() if root is None else {
            (root, 'transaction', transaction, transaction),
            *((root, 'object', str(o.version_id), transaction) for o in objects),
            *((root, 'event', r['event_id'], transaction) for r in rows),
            *((root, 'relation', str(r.relation_id), transaction) for r in relations),
        }
        actual_members = {tuple(r) for r in self.db.execute(
            'SELECT firing_version_id,member_kind,member_identity,transaction_id '
            'FROM firing_temporary_members WHERE transaction_id=?', (transaction,))}
        if actual_members != expected_members:
            raise RegistryConflict('history transaction member inventory is not exact')
        self.checked_transactions.add(transaction)

    def object(self, ref):
        if type(ref) is not dict or set(ref) != {'entity_type','logical_id','version_id'}:
            raise RegistryConflict('history requires exact native references')
        key = tuple(ref[k] for k in ('entity_type','logical_id','version_id'))
        if key in self.checked_objects:
            return self.checked_objects[key]
        row = self.db.execute('SELECT * FROM objects WHERE object_type=? AND logical_id=? AND version_id=?', key).fetchone()
        if row is None:
            raise RegistryConflict('history exact dependency is absent at acceptance cut')
        item = h7._prepared(row)
        if item.object_type == 'resource_version/v1':
            raw = self.objects.read_registered(item)
            body = dict(item.metadata)
            self.store.catalog.validate_instance(item.object_type, category='object', instance=body)
            if body['resource_id'] != ref['logical_id'] or body['resource_version_id'] != ref['version_id'] or body['size'] != item.size or body['media_type'] != item.media_type:
                raise RegistryConflict('history resource identity/envelope differs')
        else:
            body = readable_descriptor(self.objects, item, strict=True)
            raw = self.objects.read_registered(item)
        self.checked_objects[key] = (body, raw)
        self.transaction(row['transaction_id'])
        definition = self.store.catalog.require(item.object_type, category='object')
        schema = self.store.catalog._validator(definition.schema_ref).schema
        self._refs(body, schema, schema)
        if item.object_type == 'resource_version/v1':
            # These existing Registry authority contracts are deliberately
            # generic objects in the resource JSON schema. Follow only their
            # named authority fields, never descriptors/extensions/user data.
            for field in ('primary_ref', 'secondary_ref'):
                if body['origin'].get(field) is not None:
                    self.pending_objects.append(body['origin'][field])
            provenance = body['reference_provenance']
            for field in ('producer_invocation_ref', 'operation_binding_ref', 'agent_loop_ref', 'supersedes_ref'):
                if provenance.get(field) is not None:
                    self.pending_objects.append(provenance[field])
            for field in ('derived_from_refs', 'tool_evidence_refs', 'contributor_refs', 'input_resource_refs'):
                self.pending_objects.extend(provenance.get(field, ()))
            consumer = provenance.get('intended_consumer', {}).get('consumer_ref')
            if consumer is not None:
                self.pending_objects.append(consumer)
        return body, raw

    def _refs(self, value, schema, root):
        """Follow references declared by the original registered schema.

        A reference-shaped business value is ordinary data. Unconstrained
        object/array schemas provide no authority to inspect their children.
        This is an integrity walk, not a replacement authorization validator.
        """
        if not isinstance(schema, dict):
            return
        if '$ref' in schema:
            pointer = schema['$ref']
            if not pointer.startswith('#/'):
                raise RegistryConflict('history dependency schema requires a local exact reference')
            target = root
            for part in pointer[2:].split('/'):
                target = target[part.replace('~1', '/').replace('~0', '~')]
            self._refs(value, target, root)
            return
        for keyword in ('allOf', 'anyOf', 'oneOf'):
            for branch in schema.get(keyword, ()):
                self._refs(value, branch, root)
        properties = schema.get('properties', {})
        if isinstance(value, dict):
            if (set(properties) == set(schema.get('required', ()))
                    == {'entity_type','logical_id','version_id'}
                    and set(value) == set(properties)):
                self.pending_objects.append(value)
            elif (set(properties) == set(schema.get('required', ()))
                    == {'resource_id','resource_version_id'} and set(value) == set(properties)):
                self.pending_objects.append({'entity_type':'resource_version/v1','logical_id':value['resource_id'],
                    'version_id':value['resource_version_id']})
            else:
                for name, nested in value.items():
                    matched = False
                    if name in properties:
                        self._refs(nested, properties[name], root)
                        matched = True
                    for pattern, child in schema.get('patternProperties', {}).items():
                        if re.search(pattern, name):
                            self._refs(nested, child, root)
                            matched = True
                    if not matched and isinstance(schema.get('additionalProperties'), dict):
                        self._refs(nested, schema['additionalProperties'], root)
        elif isinstance(value, list):
            items = schema.get('items', {})
            for index, nested in enumerate(value):
                child = items if not isinstance(items, list) else (items[index] if index < len(items) else schema.get('additionalItems', {}))
                self._refs(nested, child, root)

    def finish(self):
        while self.pending_objects:
            self.object(self.pending_objects.pop())
        # Resource contents use their original registered schema authority,
        # including schema bytes; the offline validator cannot retrieve URLs.
        from .content_schemas import _validate_schema_bytes
        from ._candidate_plan_reads import _validate_resource_instance
        from .publication import _content_schema_instance, _NO_CONTENT_SCHEMA_INSTANCE
        for key, (body, raw) in tuple(self.checked_objects.items()):
            if key[0] != 'resource_version/v1' or body['content_schema_ref'] is None:
                continue
            authority = body['content_schema_authority_ref']
            if set(authority) == {'resource_id','resource_version_id'}:
                schema_key = ('resource_version/v1',authority['resource_id'],authority['resource_version_id'])
                schema_bytes = self.checked_objects[schema_key][1]
            elif authority['entity_type'] == 'registry_type_catalog/v1':
                schema_key = ('registry_type_catalog/v1',authority['logical_id'],authority['version_id'])
                schema_bytes = self.checked_objects[schema_key][0]['schemas'][body['content_schema_ref']]['source'].encode('utf-8')
            else:
                raise RegistryConflict('unsupported resource schema authority')
            schema_id, schema = _validate_schema_bytes(schema_bytes)
            if schema_id != body['content_schema_ref']:
                raise RegistryConflict('history resource schema identity differs')
            instance = _content_schema_instance(raw, media_type=body['media_type'])
            if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
                _validate_resource_instance(schema, instance)


def _assertion(value):
    if type(value) is not ChildAcceptanceHistoryAssertion:
        raise RegistryConflict('history accepts only exact typed origin assertions')
    types = {'binding_ref':'collaboration_source_binding/v1','run_ref':'native_run_identity/v1',
        'task_ref':'task/v1','acceptance_ref':h7.PARENT_KINDS[3]}
    for name, kind in types.items():
        ref = getattr(value, name)
        if type(ref) is not VersionRef or ref.entity_type != kind or type(ref.entity_id) is not TypedId or type(ref.version_id) is not TypedId:
            raise RegistryConflict('history assertion has an inexact native ref')
    if (type(value.commit_ordinal) is not int or value.commit_ordinal < 1
            or type(value.transaction_id) is not TypedId or value.transaction_id.kind != 'transaction'
            or type(value.event_id) is not TypedId or value.event_id.kind != 'event'
            or not isinstance(value.source_id, str) or not value.source_id
            or not isinstance(value.slot_id, str) or not value.slot_id):
        raise RegistryConflict('history assertion identity/cut is malformed')
    for text in (value.acceptance_sha256,value.intent_sha256,value.initial_declaration_digest):
        if type(text) is not str or len(text) != 64 or any(c not in '0123456789abcdef' for c in text):
            raise RegistryConflict('history assertion requires exact SHA-256 digests')


def classify_child_acceptance_history(store, assertion):
    """Classify a specified original acceptance; never opens a writer or repairs.

    It is deliberately not a receipt parser and is not called by bootstrap,
    dispatch, resume or normal CanonicalView. Missing evidence fails closed.
    SQLite read-only access can create SHM/WAL support files; this API promises
    no canonical database/event mutation, not zero directory side effects.
    """
    try:
        _assertion(assertion)
        if type(store) is not EventStore:
            raise RegistryConflict('history requires the original Registry EventStore')
        if not store.path.is_file():
            raise _Unavailable()
        # A separate read-only handle to the SAME original source keeps ordinary
        # live reader memos untouched and gives this call fresh local memos.
        reader = EventStore(store.path, store.catalog, read_only=True)
        with reader.connect() as db:
            db.execute('BEGIN')
            observed = db.execute('SELECT COALESCE(MAX(ordinal),0) FROM events').fetchone()[0]
            task_id = assertion.task_ref.entity_id
            binding = read_source_binding(db, reader.catalog, task_id)
            if binding is None:
                raise _Unavailable()
            parent = json.loads(binding['binding_metadata_json'])
            if (parent['source_id'] != assertion.source_id or parent['binding_ref'] != _ref_payload(assertion.binding_ref)
                    or parent['native_run_ref'] != _ref_payload(assertion.run_ref) or parent['task_ref'] != _ref_payload(assertion.task_ref)):
                raise RegistryConflict('history source binding differs')
            row = db.execute('SELECT * FROM objects WHERE object_type=? AND logical_id=? AND version_id=?',
                (assertion.acceptance_ref.entity_type,str(assertion.acceptance_ref.entity_id),str(assertion.acceptance_ref.version_id))).fetchone()
            if row is None:
                raise _Unavailable()
            tx = db.execute('SELECT * FROM transactions WHERE transaction_id=?', (row['transaction_id'],)).fetchone()
            terminals = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (row['transaction_id'],)).fetchall()
            facts = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='parent_child_recorded/v1'", (row['transaction_id'],)).fetchall()
            if (tx is None or tx['status'] != 'committed' or len(terminals) != 1 or len(facts) != 1
                    or row['transaction_id'] != str(assertion.transaction_id) or facts[0]['event_id'] != str(assertion.event_id)
                    or terminals[0]['ordinal'] != assertion.commit_ordinal or assertion.commit_ordinal > observed):
                raise RegistryConflict('history acceptance transaction/cut differs')
            # Validate the current full stream structure before projecting a cut;
            # projection must not conceal a damaged current source head/outbox.
            reader._verified_persisted_event_record(db, terminals[0])
            cut = _CommittedCut(db, assertion.commit_ordinal, tx['writer_epoch'])
            body = readable_descriptor(descriptor_store(reader), h7._prepared(row), strict=True)
            scope = body['execution']
            root = db.execute('SELECT * FROM firing_publications WHERE firing_version_id=?', (scope['firing_ref']['version_id'],)).fetchone()
            if root is None:
                raise RegistryConflict('history acceptance root is absent')
            unsupported_promotion = root['state'] == 'PUBLISHED'
            if root['state'] not in ('PROVISIONAL', 'PUBLISHED'):
                raise RegistryConflict('history root has an unknown lifecycle state')
            reads = _HistoryReads(reader, cut, db, task_id, scope['firing_ref']['version_id'])
            acceptance, acceptance_bytes = reads.object(_ref_payload(assertion.acceptance_ref))
            intent, intent_bytes = reads.object(acceptance['intent_ref'])
            if (acceptance['slot_id'] != assertion.slot_id or h7.digest(acceptance_bytes) != assertion.acceptance_sha256
                    or h7.digest(intent_bytes) != assertion.intent_sha256
                    or intent['materials']['initial_declaration_digest'] != assertion.initial_declaration_digest):
                raise RegistryConflict('history origin assertions differ from original bytes')
            # Exact whole-run history uniqueness uses the current snapshot, not
            # just the old cut. A later conflict can never create a fresh slot.
            all_records = db.execute('SELECT * FROM objects WHERE object_type IN ('+','.join('?' for _ in h7.ALL_PARENT_KINDS)+')', h7.ALL_PARENT_KINDS).fetchall()
            refs = (acceptance['intent_ref'],acceptance['dispatch_ref'],acceptance['worker_ref'],acceptance['record_ref'])
            if len(all_records) != 4 or {r['version_id'] for r in all_records} != {r['version_id'] for r in refs}:
                raise RegistryConflict('history whole-run H7 slot uniqueness differs')
            intent_kind = intent['record_ref']['entity_type']
            if intent_kind not in h7.INTENT_KINDS:
                raise RegistryConflict('unknown historical intent version')
            phase_kinds = (intent_kind, *h7.PARENT_KINDS[1:])
            expected_refs = dict(zip(phase_kinds, refs))
            last_commit = 0
            for kind in phase_kinds:
                current_ref = expected_refs[kind]
                record, raw = reads.object(current_ref)
                if raw != h7._json(record):
                    raise RegistryConflict('history H7 record bytes are not exact canonical material')
                original = cut.execute('SELECT * FROM objects WHERE version_id=?', (current_ref['version_id'],)).fetchone()
                transaction = cut.execute('SELECT * FROM transactions WHERE transaction_id=?', (original['transaction_id'],)).fetchone()
                events = cut.execute('SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal', (original['transaction_id'],)).fetchall()
                commit = events[-1]['ordinal']
                if commit <= last_commit:
                    raise RegistryConflict('history H7 phase order differs')
                last_commit = commit
                if (transaction['writer_epoch'] != scope['original_writer_epoch']
                        or any(r['idempotency_key'] != transaction['idempotency_key']
                            or r['command_id'] != transaction['idempotency_key']
                            or r['correlation_id'] != transaction['transaction_id']
                            or r['causation_event_id'] is not None or json.loads(r['parent_event_ids_json'])
                            or r['producer_principal'] != 'framework'
                            or r['criticality'] != 'authoritative' or r['task_control_sequence'] is not None
                            for r in events)):
                    raise RegistryConflict('history H7 transaction event contract/epoch differs')
                native, _ = reads.object(record['parent']['run_ref'])
                if any(r['branch_id'] != native['branch_id'] for r in events):
                    raise RegistryConflict('history H7 transaction is outside its native branch')
                phase_cut = _CommittedCut(db, commit, transaction['writer_epoch'])
                typed_relations = [_relation(r, cut.execute('SELECT * FROM events WHERE event_id=?', (r['published_event_id'],)).fetchone())
                    for r in cut.execute('SELECT * FROM relations WHERE transaction_id=? ORDER BY relation_id', (original['transaction_id'],))]
                # Shared live mechanical matcher expects the original edge order.
                key = transaction['idempotency_key']
                relation_order = {str(h7._stable_id('relation',key,n)):n for n in range(len(typed_relations))}
                typed_relations.sort(key=lambda r: relation_order.get(str(r.relation_id), -1))
                h7._validate_parent_record_transaction(reader, phase_cut, task_id=task_id,
                    task_round_id=TypedId.parse(scope['round_ref']['logical_id']), net_instance_id=TypedId.parse(scope['net_ref']['logical_id']),
                    transaction_id=TypedId.parse(original['transaction_id']), idempotency_key=key,
                    objects=(h7._prepared(original),), events=tuple(_pending(r) for r in events), relations=typed_relations,
                    existing={'transaction_id': original['transaction_id']})
            firing, _ = reads.object(scope['firing_ref'])
            invocation, _ = reads.object(scope['invocation_ref'])
            if (root['firing_logical_id'] != scope['firing_ref']['logical_id']
                    or root['invocation_version_id'] != scope['invocation_ref']['version_id']
                    or root['invocation_logical_id'] != scope['invocation_ref']['logical_id']
                    or root['net_version_id'] != scope['net_ref']['version_id']
                    or root['operation_binding_version_id'] != firing['operation_binding_ref']['version_id']
                    or root['admission_checkpoint_version_id'] != firing['admission_marking_checkpoint_ref']['version_id']
                    or any(cut.execute('SELECT transaction_id FROM objects WHERE version_id=?', (ref['version_id'],)).fetchone()[0]
                        != root['opened_transaction_id'] for ref in (scope['firing_ref'],scope['invocation_ref']))):
                raise RegistryConflict('history root opening differs from exact admission')
            # Admission-authority relation must be exact and strong, including
            # both endpoint identities and its original invocation producer.
            links = cut.execute("SELECT r.*,e.producer_invocation_id FROM relations r JOIN events e ON e.event_id=r.published_event_id "
                "WHERE r.relation_type='derived_from' AND json_extract(r.source_json,'$.version_id')=? "
                "AND json_extract(r.target_json,'$.version_id')=?", (scope['invocation_ref']['version_id'],scope['run_authority_ref']['version_id'])).fetchall()
            from .event_store import _ref_json
            if (len(links) != 1 or links[0]['strength'] != 'strong'
                    or json.loads(links[0]['source_json']) != _ref_json(_version_from_payload(scope['invocation_ref']))
                    or json.loads(links[0]['target_json']) != _ref_json(_version_from_payload(scope['run_authority_ref']))
                    or links[0]['transaction_id'] != root['opened_transaction_id']
                    or links[0]['producer_invocation_id'] != scope['invocation_ref']['logical_id']
                    or json.loads(links[0]['metadata_json']) != {}):
                raise RegistryConflict('history admission authority edge is not exact strong same-transaction')
            reads.object(parent['binding_ref'])
            start = cut.execute('SELECT * FROM events WHERE event_id=?', (scope['start_event_id'],)).fetchone()
            if start is None:
                raise RegistryConflict('history original Start is absent')
            reads.transaction(start['transaction_id'])
            start_tx = cut.execute('SELECT * FROM transactions WHERE transaction_id=?',
                (start['transaction_id'],)).fetchone()
            start_rows = cut.execute('SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal',
                (start['transaction_id'],)).fetchall()
            # The original native Start writer emits one Start plus its commit.
            # Revalidating its payload does not revalidate these persisted
            # command/causality fields, so compare the original envelope too.
            if (len(start_rows) != 2 or start_rows[0]['event_id'] != start['event_id']
                    or start['stream_id'] != 'operation-lease:' + scope['lease_ref']['logical_id']
                    or start['aggregate_type'] != 'operation_execution_lease'
                    or start['task_control_sequence'] is None
                    or start['producer_principal'] != invocation['principal_ref']['logical_id']
                    or start['branch_id'] != native['branch_id']
                    or start['task_round_id'] != scope['round_ref']['logical_id']
                    or start['net_instance_id'] != scope['net_ref']['logical_id']
                    or start_tx['writer_epoch'] != scope['original_writer_epoch']
                    or any(r['command_id'] != start_tx['idempotency_key']
                        or r['idempotency_key'] != start_tx['idempotency_key']
                        or r['correlation_id'] != start_tx['transaction_id']
                        or r['causation_event_id'] is not None or json.loads(r['parent_event_ids_json'])
                        or r['criticality'] != 'authoritative' for r in start_rows)):
                raise RegistryConflict('history original Start event contract differs')
            # Explicitly walk latest-at-cut authority, which need not equal the
            # admission checkpoint after unrelated valid sibling settlements.
            current_ref, _ = h7._Snapshot(reader, cut, task_id, firing=scope['firing_ref']['version_id']).canonical_latest('run_execution_authority/v1')
            reads.object(current_ref)
            reads.finish()
            if unsupported_promotion:
                # Validate original acceptance first. A mere PUBLISHED label
                # cannot hide damaged evidence behind an UNAVAILABLE result.
                from ._event_store.collaboration_descriptors import _canonical_closure
                if not _canonical_closure(db, task_id, transaction_id=str(assertion.transaction_id),
                        members=(('object', str(assertion.acceptance_ref.version_id)),)):
                    raise RegistryConflict('history purported promotion lacks committed closure')
                promotion = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                    (root['published_transaction_id'],)).fetchone()
                if promotion is None or promotion['ordinal'] <= assertion.commit_ordinal:
                    raise RegistryConflict('history purported promotion has invalid ordering')
                reader._verified_persisted_event_record(db, promotion)
                raise _Unavailable()
            return ChildAcceptanceHistoryResult(VALID, 'original_committed_mechanical_acceptance',
                ChildAcceptanceHistoryProof(assertion.source_id, assertion.binding_ref, assertion.run_ref, assertion.task_ref,
                    assertion.acceptance_ref, _version_from_payload(acceptance['intent_ref']), _version_from_payload(acceptance['dispatch_ref']),
                    _version_from_payload(acceptance['worker_ref']), _version_from_payload(scope['firing_ref']),
                    _version_from_payload(scope['invocation_ref']), _version_from_payload(scope['lease_ref']),
                    TypedId.parse(scope['start_event_id']), assertion.transaction_id, assertion.event_id, assertion.commit_ordinal,
                    observed, tx['writer_epoch'], scope['execution_generation'], assertion.acceptance_sha256,
                    assertion.intent_sha256, acceptance['request_digest'], acceptance['public_material_digest'],
                    acceptance['envelope_digest'], assertion.initial_declaration_digest))
    except _Unavailable:
        return ChildAcceptanceHistoryResult(UNAVAILABLE, 'original_evidence_or_supported_lifecycle_unavailable')
    except (OSError, sqlite3.OperationalError):
        return ChildAcceptanceHistoryResult(UNAVAILABLE, 'original_source_unavailable')
    except (RegistryConflict, RegistryCorruptError, ObjectIntegrityError, SchemaGovernanceError,
            ValueError, TypeError, KeyError, IndexError, AttributeError, ValidationError, sqlite3.DatabaseError):
        return ChildAcceptanceHistoryResult(INVALID, 'original_evidence_or_assertion_invalid')
