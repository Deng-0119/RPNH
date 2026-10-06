"""Fixed first/replay gate for the explicit v2 inert candidate plan domain.

This validates static preparation data, never HOST callables. It runs inside
the owning EventStore's continuous writer transaction and opens no connection.
"""
from __future__ import annotations

import json
import uuid

from .accounting import _CANONICAL_EVENT_SQL
from .._candidate_read_context import _CandidateReadContext
from ..event_store import RegistryConflict
from ..models import PreparedObject, VersionRef
from ..preserved_binding_contracts import PLAN_V2_TYPE
from ..runtime_binding_contracts import PLAN_TYPE, freeze_candidate_document
from ..strict_contracts import ref_payload


def _plan_family(kind):
    return isinstance(kind, str) and str.startswith(kind, 'collaboration_candidate_plan/')


def validate_preserved_plan_commit(event_store, db, *, task_id, branch_id,
        task_round_id, net_instance_id, transaction_id, idempotency_key,
        objects, events, relations, command_material, existing, extra_commands):
    """Finite built-in dispatch, including raw Core publication and replay."""
    # Dispatch on the string value, not application overrides of its methods.
    reserved = (str.startswith(idempotency_key, 'collaboration-candidate:')
        and str.endswith(idempotency_key, ':plan'))
    kinds = [item.object_type for item in objects if _plan_family(item.object_type)]
    published_kinds = [event.payload.get('object_type') for event in events
        if event.event_type == 'object_version_published/v1' and _plan_family(event.payload.get('object_type'))]
    previous = (() if existing is None else tuple(row[0] for row in db.execute(
        'SELECT object_type FROM objects WHERE transaction_id=?', (existing['transaction_id'],))))
    if not reserved and not kinds and not published_kinds and not any(_plan_family(kind) for kind in previous):
        return
    if any(kind not in (PLAN_TYPE, PLAN_V2_TYPE) for kind in (*kinds, *published_kinds, *previous) if _plan_family(kind)):
        raise RegistryConflict('unsupported candidate plan record version')
    if not reserved and PLAN_V2_TYPE not in (*kinds, *published_kinds, *previous):
        # Ordinary generic v1 transactions retain their existing behavior,
        # including unrelated facts/objects. Only the explicit plan namespace
        # and the new version participate in this fixed admission contract.
        return
    if any(type(kind) is not str for kind in (*kinds, *published_kinds)):
        raise RegistryConflict('candidate plan command requires standard string types')
    if (len(objects) != 1 or len(kinds) != 1 or relations or len(events) != 2
            or [event.event_type for event in events] != ['object_version_published/v1', 'transaction_committed/v1']
            or extra_commands or task_round_id is not None or net_instance_id is not None):
        raise RegistryConflict('candidate plan command requires one isolated static plan')
    if published_kinds != kinds:
        raise RegistryConflict('candidate plan publication kind differs from its prepared object')
    # The established v1 producer/reader keep their original contracts. A v2
    # transaction cannot be replayed by replacing its object with an old type.
    if kinds == [PLAN_TYPE] and PLAN_V2_TYPE not in previous:
        return
    if kinds != [PLAN_V2_TYPE]:
        raise RegistryConflict('candidate command cannot replace its stored plan version')
    context = _CandidateReadContext.from_event_store(event_store, db, task_id=task_id, branch_id=branch_id)
    prepared = objects[0]
    if type(prepared) is not PreparedObject or prepared.producer_invocation_id is not None:
        raise RegistryConflict('preserved plan must be a static prepared object')
    # Prospective data is verified directly, never through committed-only lookup.
    payload = context.object_store.read_verified(prepared)
    document = json.loads(freeze_candidate_document(dict(prepared.metadata)))
    if prepared.media_type != 'application/json' or payload != freeze_candidate_document(document).encode('utf-8'):
        raise RegistryConflict('preserved plan requires exact canonical prospective bytes/metadata')
    context.catalog.validate_instance(PLAN_V2_TYPE, category='object', instance=document)
    from ...collaboration.candidate_plans import _command_key, _existing_plan_ref_at
    command_key = _command_key(task_id, document['source_id'], document['command_id'])
    key = command_key + ':plan'
    expected_transaction = uuid.uuid5(uuid.NAMESPACE_URL, f'd1-c:transaction:{task_id}:{key}').hex
    ref = VersionRef(PLAN_V2_TYPE, prepared.logical_id, prepared.version_id)
    if (idempotency_key != key or transaction_id.value != expected_transaction
            or document['plan_ref'] != ref_payload(ref)):
        raise RegistryConflict('preserved plan differs from its exact command and object identity')
    publication = {'logical_id': str(prepared.logical_id), 'version_id': str(prepared.version_id),
        'object_type': prepared.object_type, 'size': prepared.size, 'media_type': prepared.media_type,
        'schema_ref': prepared.schema_ref, 'storage_locator': prepared.storage_locator, 'metadata': document}
    for event, stream, aggregate, kind, body in (
            (events[0], f'object:{prepared.logical_id}', str(prepared.logical_id), PLAN_V2_TYPE, publication),
            (events[1], f'transaction:{transaction_id}', str(transaction_id), 'transaction',
                {'object_count': 1, 'relation_count': 0, 'fact_count': 1})):
        if (event.stream_id != stream or event.aggregate_id != aggregate or event.aggregate_type != kind
                or event.criticality != 'authoritative' or event.idempotency_key != key or event.command_id != key
                or event.payload_schema_ref != 'registry_v1/' + event.event_type
                or type(event.task_control) is not bool or event.task_control or event.producer_invocation_id is not None
                or event.producer_principal != 'framework' or event.causation_event_id is not None
                or event.parent_event_ids or event.occurred_at is not None
                or freeze_candidate_document(dict(event.payload)) != freeze_candidate_document(body)):
            raise RegistryConflict('preserved plan lacks its exact isolated publication/terminal facts')
    from ...collaboration.preserved_candidate_plans import (
        _collect_preserved_plan_dependencies, _read_preserved_candidate_plan_at,
    )
    if existing is not None:
        if (existing['status'] != 'committed' or existing['transaction_id'] != str(transaction_id)
                or freeze_candidate_document(json.loads(existing['command_json'])) != freeze_candidate_document(command_material)):
            raise RegistryConflict('preserved plan replay differs from its complete exact command')
        saved = _read_preserved_candidate_plan_at(context, ref)
        if saved._plan_json.encode('utf-8') != payload:
            raise RegistryConflict('preserved plan replay differs from its frozen bytes')
        return
    # A historical object under another command must not be carried through
    # generic commit's immutable-object reuse branch as a fresh v2 plan.
    if _existing_plan_ref_at(context, command_key) is not None:
        raise RegistryConflict('preserved first commit already has a registered plan')
    evidence = _collect_preserved_plan_dependencies(context, document)
    if freeze_candidate_document({'evidence': evidence}) != freeze_candidate_document({'evidence': document['dependency_evidence']}):
        raise RegistryConflict('preserved plan dependencies differ at first commit')
    basis = document['preserved_basis']
    if basis is not None:
        latest = db.execute("SELECT e.event_id FROM events e WHERE e.task_id=? AND e.event_type='net_adopted/v1' AND "
            + _CANONICAL_EVENT_SQL + ' ORDER BY e.task_control_sequence DESC LIMIT 1', (str(task_id),)).fetchone()
        # The collector already checked this event's complete canonical identity,
        # digest and prefix. Equality of a net alone cannot establish currency.
        if latest is None or latest['event_id'] != basis['adoption_event']['event_id']:
            raise RegistryConflict('preserved basis is stale at first plan commit')
