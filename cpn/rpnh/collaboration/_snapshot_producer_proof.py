"""Private exact-producer foundation; not a product-origin query or closure.

The caller must authorize the root and all required fields before invoking this
pure frozen-snapshot adapter. No material, live authority, or public projection
is accessed here. PO-E01-20261009 uses the original publication transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
import json
from collections.abc import Mapping

from ..registry.identities import TypedId
from ..registry.models import VersionRef
from ..registry.resources import ResourceVersionRef
from ..registry._event_store.provenance import canonical_producer_record, CanonicalProducerViolation
from .registry_read_contracts import ReadLimits, RegistryReadSessionError


@dataclass(slots=True)
class _ProofReservation:
    """Independent W ledger and scratch reservation against existing limits.

    retained_bytes includes all session snapshots/cursors. Scratch is charged
    conservatively before parsing/copying, in canonical-byte units, not RSS.
    A future query owns this ledger for its entire requested closure.
    """
    limits: ReadLimits
    retained_bytes: int
    work: int = 0
    scratch_bytes: int = 0
    relations: int = 0

    def reserve(self, category=None, *, rows=0, size=0):
        if (self.work + rows > self.limits.max_scan_rows
                or self.retained_bytes + self.scratch_bytes + size > self.limits.max_scan_bytes):
            raise RegistryReadSessionError('LIMIT_EXCEEDED')
        self.work += rows
        self.scratch_bytes += size
        if category == 'Rel':
            self.relations += rows

    def release(self, size):
        self.scratch_bytes -= size


def _text_bound(value):
    # JSON can escape each Unicode code point as two six-byte escapes. This
    # conservative bound is computed without first encoding/serializing it.
    return 12 * len(value) + 256


def _canonical_snapshot_producer(snapshot, resource_ref, producer_ref, reservation):
    """Validate one exact producer; return no private proof material.

    Other registered exact sibling sources stop at identity validation. This is
    intentionally not canonical_producer_logical_set or a completion proof.
    """
    context = getattr(snapshot, '_origin_context', None)
    if context is not None:
        # The query retains this shared index beyond the foundation's scratch
        # lifetime. Establish its ownership before recording our release point.
        context.events_by_id
        context.reserve()
    initial_scratch = reservation.scratch_bytes
    completed = False
    try:
        _check(snapshot, resource_ref, producer_ref, reservation)
        completed = True
    except RegistryReadSessionError:
        raise
    except (CanonicalProducerViolation, ValueError, TypeError, KeyError, AttributeError):
        raise RegistryReadSessionError('INTEGRITY_FAILED') from None
    finally:
        if context is None or completed:
            reservation.release(reservation.scratch_bytes - initial_scratch)


def _check(snapshot, resource_ref, producer_ref, budget):
    def require(condition):
        if not condition:
            raise RegistryReadSessionError('INTEGRITY_FAILED')

    require(type(resource_ref) is ResourceVersionRef and type(producer_ref) is VersionRef)
    resource_id, version = str(resource_ref.resource_id), str(resource_ref.resource_version_id)
    producer_id, producer_version = str(producer_ref.entity_id), str(producer_ref.version_id)
    TypedId.parse(resource_id, expected='resource')
    TypedId.parse(version, expected='resource_version')
    TypedId.parse(producer_id, expected='invocation')
    TypedId.parse(producer_version, expected='invocation_version')
    require(producer_ref.entity_type == 'invocation/v1')
    budget.reserve('D', rows=2, size=sum(_text_bound(x) for x in (resource_id, version, producer_id, producer_version)))
    root = snapshot.objects.get(version)
    producer = snapshot.objects.get(producer_version)
    require(root is not None and producer is not None)
    require(root['object_type'] == 'resource_version/v1' and root['logical_id'] == resource_id
            and root['version_id'] == version and root['schema_ref'] == 'registry_v1/resource_version/v1'
            and producer['object_type'] == producer_ref.entity_type and producer['logical_id'] == producer_id
            and producer['version_id'] == producer_version and producer['schema_ref'] == 'registry_v1/invocation/v1')
    budget.reserve(size=_text_bound(root['metadata_json']) * 3)
    metadata = json.loads(root['metadata_json'])
    expected_producer = dict(entity_type=producer_ref.entity_type, logical_id=producer_id, version_id=producer_version)
    require(metadata['resource_id'] == resource_id and metadata['resource_version_id'] == version
            and metadata['producer_ref'] == expected_producer
            and metadata['reference_provenance']['producer_invocation_ref'] == expected_producer
            and root['producer_invocation_id'] == producer_id
            and metadata['size'] == root['size'] and metadata['media_type'] == root['media_type'])
    transaction = root['transaction_id']
    TypedId.parse(transaction, expected='transaction')
    key = snapshot.publication_transaction_keys.get(transaction)
    require(isinstance(key, str) and bool(key))
    budget.reserve(size=_text_bound(key) + 1024)
    candidates = []
    for row in snapshot.relations:
        if row['relation_type'] != 'produced_by':
            continue
        source_size = _text_bound(row['source_json'])
        budget.reserve(size=source_size)
        try:
            source = json.loads(row['source_json'])
        except (ValueError, TypeError):
            budget.reserve('Rel', rows=1)
            raise RegistryReadSessionError('INTEGRITY_FAILED') from None
        if not isinstance(source, Mapping):
            budget.reserve('Rel', rows=1)
            raise RegistryReadSessionError('INTEGRITY_FAILED')
        if source.get('entity_id', source.get('logical_id')) != resource_id:
            budget.release(source_size)
            continue
        # Charge before inspecting strength, target, completeness or uniqueness.
        budget.reserve('Rel', rows=1)
        require(set(source) == {'entity_type', 'entity_id', 'version_id'}
                and source['entity_type'] == 'resource_version/v1')
        sibling_version = source['version_id']
        TypedId.parse(sibling_version, expected='resource_version')
        budget.reserve('D', rows=1, size=_text_bound(sibling_version))
        sibling = snapshot.objects.get(sibling_version)
        require(sibling is not None and sibling['logical_id'] == resource_id
                and sibling['object_type'] == 'resource_version/v1' and sibling['version_id'] == sibling_version)
        if sibling_version != version:
            # No sibling target/metadata parsing or producer/business closure.
            budget.release(source_size)
            continue
        budget.reserve(size=sum(_text_bound(row[name]) for name in
            ('target_json', 'metadata_json', 'relation_id', 'transaction_id')) * 3 + 1024)
        candidates.append(row)
    # All relevant candidates have been charged before strict uniqueness checks.
    canonical = canonical_producer_record(
        dict(entity_type='resource_version/v1', logical_id=resource_id, version_id=version),
        expected_producer, publication_transaction=transaction, publication_key=key,
        relation_records=candidates, valid_resource_versions=(version,),
        registered_exact_refs=((producer_ref.entity_type, producer_id, producer_version),))
    relation = candidates[0]
    TypedId.parse(relation['relation_id'], expected='relation')
    witnesses = {}
    context = getattr(snapshot, '_origin_context', None)
    if context is None:
        events = snapshot.events
    else:
        events = chain(context.events_by_id.get(root['published_event_id'], ()),
            context.events_by_id.get(relation['published_event_id'], ()),
            context.commits_by_transaction.get(transaction, ()))
    selected_events = set()
    for event in events:
        if context is not None:
            if id(event) in selected_events:
                continue
            budget.reserve(size=128)
            selected_events.add(id(event))
        event_id = str(event.event_id)
        is_commit = event.event_type == 'transaction_committed/v1' and str(event.transaction_id) == transaction
        if event_id not in (root['published_event_id'], relation['published_event_id']) and not is_commit:
            continue
        budget.reserve('E', rows=1, size=512)
        require(event_id not in witnesses)
        witnesses[event_id] = event
        TypedId.parse(event_id, expected='event')
        require(event.criticality == 'authoritative' and event.ordinal is not None
                and 0 < event.ordinal <= snapshot.head.ordinal and str(event.transaction_id) == transaction
                and event.payload_schema_ref == 'registry_v1/' + event.event_type
                and str(event.task_id) == metadata['task_ref']['logical_id']
                and event.branch_id == metadata['branch_id']
                and str(event.task_round_id) == metadata['round_ref']['logical_id']
                and str(event.net_instance_id) == metadata['net_ref']['logical_id'])
    publication = witnesses.get(root['published_event_id'])
    relation_event = witnesses.get(relation['published_event_id'])
    commits = [event for event in witnesses.values() if event.event_type == 'transaction_committed/v1']
    require(publication is not None and relation_event is not None and len(commits) == 1)
    require(publication.event_type == 'object_version_published/v1'
            and publication.aggregate_type == 'resource_version/v1'
            and publication.aggregate_id == resource_id and publication.stream_id == 'object:' + resource_id
            and str(publication.producer_invocation_id) == producer_id
            and publication.payload == {name: root[name] for name in
                ('logical_id', 'version_id', 'object_type', 'size', 'media_type', 'schema_ref', 'storage_locator')}
                | {'metadata': metadata})
    require(relation_event.event_type == 'relation_published/v1'
            and relation_event.aggregate_type == 'typed_relation/v1'
            and relation_event.aggregate_id == relation['relation_id']
            and relation_event.stream_id == 'relation:' + relation['relation_id']
            and str(relation_event.producer_invocation_id) == producer_id
            and relation_event.payload == {name: canonical[name] for name in
                ('relation_id', 'source', 'target', 'strength', 'metadata')} | {'relation_type': 'produced_by'})
    commit = commits[0]
    require(commit.aggregate_type == 'transaction' and commit.aggregate_id == transaction
            and commit.stream_id == 'transaction:' + transaction
            and publication.ordinal < commit.ordinal and relation_event.ordinal < commit.ordinal)
