"""Trusted local HOST handles; JSON is evidence, never an actor credential.

Handles are deliberately bound to one EventStore instance. Canonical facts,
not this module, hold membership and policy. This is a local HOST boundary,
not authentication against code executing as the same operating-system user.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import uuid

from .identities import TypedId
from .models import VersionRef
from .schema_catalog import canonical_text
from .strict_contracts import ref_payload
from .event_store import RegistryConflict

KINDS = ('session', 'project', 'participant', 'proposal', 'review', 'decision', 'receipt', 'delivery', 'execution')
TYPES = tuple('user_collaboration_' + kind + '/v1' for kind in KINDS)
SESSION, PROJECT, PARTICIPANT, PROPOSAL, REVIEW, DECISION, RECEIPT, DELIVERY, EXECUTION = TYPES
_SEAL = object()
_route = ContextVar('user_collaboration_exact_command', default=None)


def fact_ref(task, kind, command):
    material = canonical_text({'task': str(task), 'kind': kind, 'command': command})
    return VersionRef('user_collaboration_' + kind + '/v1',
        TypedId('resource', uuid.uuid5(uuid.NAMESPACE_URL, 'uc:id:' + material).hex),
        TypedId('resource_version', uuid.uuid5(uuid.NAMESPACE_URL, 'uc:version:' + material).hex))


def command_key(kind, command):
    return 'user-collaboration:' + canonical_text({'kind': kind, 'command': command})


@dataclass(frozen=True, slots=True)
class _SessionBinding:
    issuer: object
    store: object
    ref: VersionRef
    actor: str


@dataclass(frozen=True, slots=True)
class _ActionBinding:
    issuer: object
    store: object
    session_ref: VersionRef | None
    actor: str | None
    kind: str
    reference: VersionRef | None
    creation: bytes | None


@dataclass(frozen=True, slots=True)
class HostSession:
    _seal: object
    _store: object
    ref: VersionRef
    actor: str
    _observer: object = None


def mint_session(store, ref, actor):
    return HostSession(_SessionBinding(_SEAL, store, ref, actor), store, ref, actor)


def check_session(store, session):
    if (type(session) is not HostSession or type(session._seal) is not _SessionBinding
            or session._seal.issuer is not _SEAL or session._store is not store
            or session._seal.store is not store or session._seal.ref != session.ref
            or session._seal.actor != session.actor):
        raise RegistryConflict('untrusted or altered HOST actor/session binding')
    return session


@dataclass(frozen=True, slots=True)
class CollaborationAction:
    _seal: object
    _store: object
    session: HostSession | None
    kind: str
    reference: VersionRef | None
    # Creation evidence is held by the trusted HOST, not read from JSON.
    creation: bytes | None = None
    observer: object = None

    def material(self):
        return {'session_ref': None if self.session is None else ref_payload(self.session.ref),
                'actor': None if self.session is None else self.session.actor,
                'kind': self.kind,
                'action_ref': None if self.reference is None else ref_payload(self.reference)}


def mint_action(store, session, kind, reference, creation=None, observer=None):
    if session is not None:
        check_session(store, session)
    seal = _ActionBinding(_SEAL, store, None if session is None else session.ref,
        None if session is None else session.actor, kind, reference, creation)
    return CollaborationAction(seal, store, session, kind, reference, creation, observer)


def check_handle(store, action):
    if (type(action) is not CollaborationAction or type(action._seal) is not _ActionBinding
            or action._seal.issuer is not _SEAL or action._store is not store
            or action._seal.store is not store or action._seal.kind != action.kind
            or action._seal.reference != action.reference or action._seal.creation != action.creation):
        raise RegistryConflict('collaboration requires a trusted HOST action for this Registry')
    session = action.session
    if session is not None:
        check_session(store, session)
    if (action._seal.session_ref != (None if session is None else session.ref)
            or action._seal.actor != (None if session is None else session.actor)):
        raise RegistryConflict('collaboration action cannot change its original actor/session')
    return action


def routed_action(store, command):
    route = _route.get()
    return route[2] if route is not None and route[:2] == (store, command) else None


@contextmanager
def exact_command(store, command, action):
    """HOST-only routing for an original facade lacking an action parameter.

    Only this exact command on this exact store is eligible; shared schema and
    HOST registration transactions never acquire ambient actor material.
    """
    check_handle(store, action)
    token = _route.set((store, command, action))
    try:
        yield
    finally:
        _route.reset(token)


def early_replay_fence(core, command, action=None):
    """Original facades with early returns use the same immutable actor fence."""
    from ._event_store.user_collaboration import check_replay
    action = action or routed_action(core.event_store, command)
    with core.event_store.connect() as db:
        row = db.execute('SELECT * FROM transactions WHERE task_id=? AND idempotency_key=?',
                         (str(core.task_id), command)).fetchone()
        if row is not None:
            check_replay(core.event_store, db, core.task_id, row, action)


def stage_action_receipt(tx):
    """Stage only apply receipts; the original final validator proves outputs."""
    action = tx._collaboration_action
    if action is None or action.kind not in ('advance', 'adopt'):
        return
    check_handle(tx.event_store, action)
    from ._event_store.user_collaboration import exact
    from .schema_catalog import canonical_json
    with tx.event_store.connect() as db:
        decision = exact(tx.event_store, db, tx.task_id, ref_payload(action.reference), DECISION)
    if decision['session_ref'] != ref_payload(action.session.ref):
        raise RegistryConflict('collaboration decision actor differs from action')
    data = decision['data']
    if action.kind == 'advance':
        branches = [o for o in tx._objects if o.object_type.startswith('collaboration_branch/')]
        if len(branches) != 1:
            raise RegistryConflict('collaboration advance requires one Branch successor')
        result_ref = branches[0].metadata['branch_ref']['ref']
    else:
        adoptions = [e for e in tx._events if e.event_type == 'net_adopted/v1']
        if len(adoptions) != 1:
            raise RegistryConflict('collaboration adoption needs its original net event')
        result_ref = adoptions[0].payload['net_instance_ref']
    reference = fact_ref(tx.task_id, 'receipt', tx.idempotency_key)
    document = {k: decision[k] for k in ('source_id', 'owner_task_ref', 'publisher_bootstrap_ref', 'project_ref')}
    document.update(schema_version='registry_v1/' + RECEIPT, self_ref=ref_payload(reference),
        command_id=tx.idempotency_key, session_ref=ref_payload(action.session.ref),
        data={'decision_ref': ref_payload(action.reference), 'operation': action.kind,
            'result_revision_ref': data['result_revision_ref'], 'result_ref': result_ref,
            'transaction_id': str(tx.transaction_id)})
    tx.prewrite(object_type=RECEIPT, logical_id=reference.entity_id, version_id=reference.version_id,
        payload=canonical_json(document), metadata=document, media_type='application/json',
        schema_ref='registry_v1/' + RECEIPT)


def owner_edit_action(core, command, action=None, *, operation='submit'):
    """Fence facade early returns and capture the immutable pending action."""
    from ._event_store.user_collaboration import (facts, exact, session_document,
        require_owner, check_replay, _decision)
    action = action or routed_action(core.event_store, command)
    with core.event_store.connect() as db:
        projects = facts(core.event_store, db, core.task_id, PROJECT)
        if not projects:
            return action
        existing = db.execute('SELECT * FROM transactions WHERE task_id=? AND idempotency_key=?',
                              (str(core.task_id), command)).fetchone()
        if existing is not None:
            check_replay(core.event_store, db, core.task_id, existing, action)
            return action
        check_handle(core.event_store, action)
        if operation != 'submit' or action.kind != 'adopt':
            raise RegistryConflict('protected correction/retraction is unsupported; submit an explicit new owner decision')
        session = session_document(core.event_store, db, core.task_id, action)
        decision = exact(core.event_store, db, core.task_id, ref_payload(action.reference), DECISION)
        project = exact(core.event_store, db, core.task_id, decision['project_ref'], PROJECT)
        require_owner(core.event_store, db, core.task_id, project, session)
        _decision(core.event_store, db, core.task_id, project, decision['data'])
    return action


__all__ = ('HostSession', 'CollaborationAction')
