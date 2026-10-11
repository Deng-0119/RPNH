"""Minimal trusted local HOST frontdoor over original Registry transactions.

The caller owns the existing registration gateway. Do not expose this object
or origin Core handles to model tools/plugins. Model JSON supplies content;
the registered HOST derives source/execution references from consumed inputs.
"""
from contextlib import contextmanager
import hashlib
import uuid
from dataclasses import replace

from ..registry.event_store import RegistryConflict
from ..registry.models import VersionRef
from ..registry.schema_catalog import canonical_json, canonical_text
from ..registry.strict_contracts import ref_payload
from ..registry.publication import _version_from_payload
from ..registry.user_collaboration import (HostSession, CollaborationAction,
    TYPES, SESSION, PROJECT, PARTICIPANT, PROPOSAL, DECISION, RECEIPT, DELIVERY, EXECUTION,
    fact_ref, command_key, check_handle, exact_command, check_session, mint_session, mint_action)
from ..registry._event_store.user_collaboration import (exact, facts, participant,
    read_fence, require_owner)
from ..registry._event_store.branch_publication import _local_binding
from ..registry._event_store.collaboration_descriptors import exact_prepared


def _ref(value):
    if isinstance(value, VersionRef):
        return ref_payload(value)
    if hasattr(value, 'ref') and isinstance(value.ref, VersionRef):
        return ref_payload(value.ref)
    if hasattr(value, 'as_version_ref'):
        return ref_payload(value.as_version_ref())
    raise TypeError('HOST requires typed exact references, not request JSON')


class UserCollaborationHost:
    def __init__(self, core, gateway):
        from ..registry.registration_gateway import RegistryRegistrationGateway
        if type(gateway) is not RegistryRegistrationGateway or gateway._core is not core:
            raise TypeError('UserCollaborationHost requires this Core original owner gateway')
        self._core, self._gateway = core, gateway
        with core.event_store.connect() as db:
            self._binding = _local_binding(db, core.catalog, core.task_id, core.object_store)

    def _session(self, session):
        return check_session(self._core.event_store, session)

    def _get(self, reference):
        with self._core.event_store.connect() as db:
            return exact(self._core.event_store, db, self._core.task_id, _ref(reference))

    def _prior(self, kind, command_id):
        ref = fact_ref(self._core.task_id, kind, command_id)
        with self._core.event_store.connect() as db:
            exists = db.execute('SELECT 1 FROM objects WHERE version_id=?', (str(ref.version_id),)).fetchone()
            return None if exists is None else exact(self._core.event_store, db, self._core.task_id, _ref(ref))

    def _publish(self, kind, session, project_ref, data, command_id, *, creation=None):
        if session is not None:
            self._session(session)
        reference = fact_ref(self._core.task_id, kind, command_id)
        doc = {'schema_version': 'registry_v1/' + reference.entity_type,
            'self_ref': _ref(reference), 'source_id': self._binding['source_id'],
            'owner_task_ref': self._binding['task_ref'],
            'publisher_bootstrap_ref': self._binding['bootstrap_command_ref'],
            'command_id': command_id, 'session_ref': None if session is None else _ref(session.ref),
            'project_ref': None if project_ref is None else _ref(project_ref), 'data': data}
        action = mint_action(self._core.event_store, session, kind, reference, creation,
                                     None if session is None else session._observer)
        tx = self._core.begin(idempotency_key=command_key(kind, command_id))
        tx.bind_collaboration_action(action)
        tx.prewrite(object_type=reference.entity_type, logical_id=reference.entity_id,
            version_id=reference.version_id, payload=canonical_json(doc), metadata=doc,
            media_type='application/json', schema_ref=doc['schema_version'])
        tx.commit()
        return reference

    def _origin(self, origin_core, principal_ref):
        from ..registry._registry import _RegistryCore
        if type(origin_core) is not _RegistryCore or origin_core.read_only:
            raise TypeError('session must be routed by its owned original Registry Core')
        with origin_core.event_store.connect() as db:
            binding = _local_binding(db, origin_core.catalog, origin_core.task_id, origin_core.object_store)
            value = exact(origin_core.event_store, db, origin_core.task_id, _ref(principal_ref), 'principal/v1')
        if (value['principal_id'] != str(principal_ref.entity_id)
                or value['principal_version_id'] != str(principal_ref.version_id)):
            raise RegistryConflict('origin principal self identity differs')
        actor = 'actor:' + uuid.uuid5(uuid.NAMESPACE_URL, canonical_text({
            'source': binding['source_id'], 'task': binding['task_ref'], 'principal': _ref(principal_ref)})).hex
        from ..registry.identities import TypedId
        target_principal = (principal_ref if origin_core is self._core else VersionRef('principal/v1',
            TypedId('principal', uuid.uuid5(uuid.NAMESPACE_URL, 'uc-principal:' + actor).hex),
            TypedId('principal_version', uuid.uuid5(uuid.NAMESPACE_URL, 'uc-principal-version:' + actor).hex)))
        return {'actor': actor, 'origin_source': binding['source_id'], 'origin_task_ref': binding['task_ref'],
            'origin_principal_ref': _ref(principal_ref), 'target_principal_ref': _ref(target_principal),
            'owner': origin_core is self._core}

    def create_session(self, *, origin_core, principal_ref, command_id):
        data = self._origin(origin_core, principal_ref)
        target = _version_from_payload(data['target_principal_ref'])
        if origin_core is not self._core:
            body = {'principal_id': str(target.entity_id), 'principal_version_id': str(target.version_id),
                    'display_name': data['actor']}
            self._core.publish_bytes(object_type=target.entity_type, logical_id=target.entity_id,
                version_id=target.version_id, payload=canonical_json(body), metadata=body,
                media_type='application/json', schema_ref='registry_v1/principal/v1',
                idempotency_key='user-collaboration-actor:' + data['actor'])
        reference = self._publish('session', None, None, data, command_id, creation=canonical_json(data))
        return mint_session(self._core.event_store, reference, data['actor'])

    def bind_read_authority(self, session, observer):
        """Attach an original observer context without changing actor identity."""
        return replace(self._session(session), _observer=observer)

    def session_principal(self, session):
        return _version_from_payload(self._get(self._session(session).ref)['data']['target_principal_ref'])

    def resume_session(self, session_ref, *, origin_core, principal_ref):
        data = self._origin(origin_core, principal_ref)
        if self._get(session_ref)['data'] != data or session_ref.entity_type != SESSION:
            raise RegistryConflict('resume must retain exact original actor/owned Registry binding')
        return mint_session(self._core.event_store, session_ref, data['actor'])

    def protect(self, session, *, branch_ref, runtime_net_ref=None, command_id):
        return self._publish('project', session, None, {
            'owner_session_ref': _ref(session.ref), 'branch_ref': _ref(branch_ref),
            'runtime_net_ref': None if runtime_net_ref is None else _ref(runtime_net_ref),
            'protect_task_runtime': True}, command_id)

    def _participant(self, project_ref, subject_session_ref):
        with self._core.event_store.connect() as db:
            project = exact(self._core.event_store, db, self._core.task_id, _ref(project_ref), PROJECT)
            return participant(self._core.event_store, db, self._core.task_id, project, _ref(subject_session_ref))

    def invite(self, session, project_ref, *, subject_session_ref, role='propose', command_id):
        current = self._participant(project_ref, subject_session_ref)
        prior = self._prior('participant', command_id)
        return self._publish('participant', session, project_ref, {
            'subject_session_ref': _ref(subject_session_ref), 'role': role, 'status': 'invited',
            'predecessor_ref': prior['data']['predecessor_ref'] if prior is not None else
                None if current is None else current['self_ref']}, command_id)

    def join(self, session, invitation_ref, *, command_id):
        invitation = self._get(invitation_ref)
        return self._publish('participant', session, _version_from_payload(invitation['project_ref']), {
            **invitation['data'], 'status': 'joined', 'predecessor_ref': _ref(invitation_ref)}, command_id)

    def revoke(self, session, project_ref, *, subject_session_ref, command_id):
        current = self._participant(project_ref, subject_session_ref)
        prior = self._prior('participant', command_id)
        return self._publish('participant', session, project_ref, {
            'subject_session_ref': _ref(subject_session_ref), 'role': prior['data']['role'] if prior is not None else
                'propose' if current is None else current['data']['role'],
            'status': 'revoked', 'predecessor_ref': prior['data']['predecessor_ref'] if prior is not None else
                None if current is None else current['self_ref']}, command_id)

    def submit_proposal(self, session, project_ref, *, source_text_ref, origin_text,
                        execution_refs, payload, base_revision_ref, expected_branch_ref,
                        expected_head_ref, candidate_revision_ref, previous_proposal_ref=None,
                        reason='', command_id, delivery_ref=None, synthetic_fixture=False):
        with self._core.event_store.connect() as db:
            prepared = exact_prepared(db, self._core.object_store, self._core.task_id, _ref(source_text_ref))
            raw = self._core.object_store.read_registered(prepared, max_bytes=6 * 262144 + 2)
        if delivery_ref is not None:
            import json
            text = json.loads(raw)
            if type(text) is not str:
                raise RegistryConflict('delivered stock TEXT must retain its original JSON string codec')
            raw = text.encode('utf-8')
        return self._publish('proposal', session, project_ref, {
            'source_text_ref': _ref(source_text_ref), 'origin_text': origin_text,
            'execution_refs': {k: _ref(v) for k, v in execution_refs.items()},
            'text_digest': hashlib.sha256(raw).hexdigest(), 'payload': payload,
            'base_revision_ref': _ref(base_revision_ref), 'expected_branch_ref': _ref(expected_branch_ref),
            'expected_head_ref': _ref(expected_head_ref), 'candidate_revision_ref': _ref(candidate_revision_ref),
            'previous_proposal_ref': None if previous_proposal_ref is None else _ref(previous_proposal_ref),
            'reason': reason, 'delivery_ref': None if delivery_ref is None else _ref(delivery_ref),
            'synthetic_fixture': synthetic_fixture}, command_id)

    def bind_execution(self, session, *, origin_core, execution_core, command_id):
        """Record an explicit user launch before any invocation in a new run."""
        import json
        self._session(session)
        original = self._get(session.ref)['data']
        if self._origin(origin_core, _version_from_payload(original['origin_principal_ref'])) != original:
            raise RegistryConflict('execution launch requires the original personal owner route')
        if execution_core.read_only or execution_core.writer_epoch != execution_core.event_store.writer_epoch:
            raise RegistryConflict('execution launch requires the current original run owner')
        prior = self._prior('execution', command_id)
        if prior is None and execution_core.event_store.object_rows_by_type('invocation/v1'):
            raise RegistryConflict('cannot retrospectively assign an already-started execution to a user')
        with execution_core.event_store.connect() as db:
            binding = _local_binding(db, execution_core.catalog, execution_core.task_id, execution_core.object_store)
            run_ref = json.loads(execution_core.event_store.get_meta('native_run_ref'))
            run = exact(execution_core.event_store, db, execution_core.task_id, run_ref, 'native_run_identity/v1')
            roots = facts(execution_core.event_store, db, execution_core.task_id, 'team_design_root/v1')
            principals = {canonical_text(r['owner_principal_ref']) for r in roots}
            if run['task_ref'] != binding['task_ref'] or len(principals) != 1:
                raise RegistryConflict('execution launch needs one original canonical run owner')
            principal = json.loads(next(iter(principals)))
            exact(execution_core.event_store, db, execution_core.task_id, principal, 'principal/v1')
        data = {'author_session_ref': _ref(session.ref), 'source_id': binding['source_id'],
            'source_task_ref': binding['task_ref'], 'run_ref': run_ref, 'principal_ref': principal,
            'launch_cut': execution_core.event_store.max_ordinal() if prior is None else prior['data']['launch_cut']}
        return self._publish('execution', session, None, data, command_id, creation=canonical_json(data))

    def ingest_completed_text(self, session, *, origin_core, command_id, completed=None,
                              execution_ref=None, firing_ref=None):
        """Explicit personal-owner delivery; no source capability is transferred.

        The received Resource has the target bootstrap as copy publisher. The
        separate immutable delivery fact retains original invocation/product,
        run, author, stock codec and successful firing provenance.
        """
        from ..registry.user_collaboration_source import completed_text
        from ..registry.resources import PublishResource, PrivateSystemOrigin
        from ..registry.resource_service import _publish_private_system
        import json
        self._session(session)
        original = self._get(session.ref)['data']
        launch = None if execution_ref is None else self._get(execution_ref)
        if launch is not None and (execution_ref.entity_type != EXECUTION or launch['session_ref'] != _ref(session.ref)):
            raise RegistryConflict('execution launch belongs to another stable user')
        source_id = original['origin_source'] if launch is None else launch['data']['source_id']
        source_task = original['origin_task_ref'] if launch is None else launch['data']['source_task_ref']
        source_principal = original['origin_principal_ref'] if launch is None else launch['data']['principal_ref']
        with origin_core.event_store.connect() as db:
            binding = _local_binding(db, origin_core.catalog, origin_core.task_id, origin_core.object_store)
        if binding['source_id'] != source_id or binding['task_ref'] != source_task:
            raise RegistryConflict('TEXT delivery must originate from this user personal Registry')
        raw, payload, witness = completed_text(origin_core, firing_ref=firing_ref)
        if witness['principal_ref'] != source_principal or (launch is not None and witness['run_ref'] != launch['data']['run_ref']):
            raise RegistryConflict('TEXT producer is not bound to the original personal owner')
        if completed is not None:
            if (completed.payload != payload or any(getattr(completed, key) != witness[key]
                    for key in ('run_ref', 'task_ref', 'product_ref', 'invocation_ref', 'firing_ref', 'principal_ref', 'producer'))):
                raise RegistryConflict('caller CompletedText differs from re-derived original facts')
        prior = self._prior('delivery', command_id)
        if prior is not None:
            original_witness = prior['data']['witness']
            if any(value != original_witness.get(key) for key, value in witness.items() if key != 'source_cut'):
                raise RegistryConflict('delivery retry changed its original successful source output')
            witness = original_witness
        copied = _publish_private_system(self._core, self._gateway._task_ref, PublishResource(
            origin=PrivateSystemOrigin(self._gateway._bootstrap_ref), payload=raw,
            media_type='application/json', content_schema_ref=None,
            summary='Received original stock TEXT; target owner copy, original producer in delivery fact',
            lifetime_ref=self._gateway._bootstrap_ref,
            descriptors={'user_collaboration_source': binding['source_id'],
                'original_product_version': witness['product_ref']['version_id'], 'original_stored_digest': witness['stored_digest']},
            idempotency_key='user-collaboration-delivery-copy:' + command_id))
        data = {'source_id': binding['source_id'], 'source_task_ref': binding['task_ref'],
            'received_text_ref': _ref(copied), 'author_session_ref': _ref(session.ref), 'witness': witness,
            'execution_ref': None if execution_ref is None else _ref(execution_ref)}
        return self._publish('delivery', session, None, data, command_id, creation=canonical_json(data))

    def delivered_proposal_inputs(self, session, delivery_ref):
        """Trusted HOST ref-only adapter inputs; original source payload is not returned."""
        self._session(session)
        delivery = self._get(delivery_ref)
        if delivery_ref.entity_type != DELIVERY or delivery['session_ref'] != _ref(session.ref):
            raise RegistryConflict('delivery belongs to another actor')
        data = delivery['data']; witness = data['witness']
        return {'source_text_ref': _version_from_payload(data['received_text_ref']),
            'origin_text': {'source_id': data['source_id'], 'ref': witness['product_ref']},
            'execution_refs': {k: _version_from_payload(witness[k]) for k in (
                'run_ref', 'task_ref', 'product_ref', 'invocation_ref', 'firing_ref', 'principal_ref',
                'terminal_ref', 'operation_result_ref') if witness[k] is not None} | {'producer_ref': _version_from_payload(witness['invocation_ref'])},
            'delivery_ref': delivery_ref}

    def review(self, session, proposal_ref, *, disposition, reason='', command_id):
        proposal = self._get(proposal_ref)
        return self._publish('review', session, _version_from_payload(proposal['project_ref']), {
            'proposal_ref': _ref(proposal_ref), 'disposition': disposition,
            'exact_proposal': proposal['data'], 'reason': reason}, command_id)

    def decide(self, session, project_ref, *, operation, proposal_refs, review_refs,
               result_revision_ref, expected_branch_ref, expected_head_ref,
               expected_runtime_ref=None, result_net_ref=None, accepted_receipt_ref=None,
               choices=None, command_id):
        return self._publish('decision', session, project_ref, {
            'operation': operation, 'proposal_refs': [_ref(r) for r in proposal_refs],
            'review_refs': [_ref(r) for r in review_refs], 'result_revision_ref': _ref(result_revision_ref),
            'expected_branch_ref': _ref(expected_branch_ref), 'expected_head_ref': _ref(expected_head_ref),
            'expected_runtime_ref': None if expected_runtime_ref is None else _ref(expected_runtime_ref),
            'result_net_ref': None if result_net_ref is None else _ref(result_net_ref),
            'accepted_receipt_ref': None if accepted_receipt_ref is None else _ref(accepted_receipt_ref),
            'choices': {} if choices is None else choices}, command_id)

    def action(self, session, decision_ref, *, observer=None):
        self._session(session)
        decision = self._get(decision_ref)
        if decision_ref.entity_type != DECISION or decision['session_ref'] != _ref(session.ref):
            raise RegistryConflict('action must retain the original decision actor')
        return mint_action(self._core.event_store, session,
            decision['data']['operation'], decision_ref, observer=observer)

    def bind_transaction(self, tx, action):
        check_handle(self._core.event_store, action)
        tx.bind_collaboration_action(action)

    def command(self, action, idempotency_key):
        return exact_command(self._core.event_store, idempotency_key, action)

    def receipt_ref(self, idempotency_key):
        return fact_ref(self._core.task_id, 'receipt', idempotency_key)

    def read_fact(self, session, reference, *, observer=None):
        if reference.entity_type not in TYPES:
            raise RegistryConflict('HOST fact view only exposes collaboration facts; use the original scoped reader for PN material')
        action = mint_action(self._core.event_store, self._session(session), 'read', reference,
                                     observer=observer or session._observer)
        with self._core.event_store.connect() as db:
            db.execute('BEGIN')
            read_fence(self._core.event_store, db, self._core.task_id, action)
            value = exact(self._core.event_store, db, self._core.task_id, _ref(reference))
        with self._core.event_store.connect() as db:
            read_fence(self._core.event_store, db, self._core.task_id, action)
        return value

    def read_view(self, session, *, observer=None):
        action = mint_action(self._core.event_store, self._session(session), 'read', None,
                                     observer=observer or session._observer)
        with self._core.event_store.connect() as db:
            db.execute('BEGIN')
            read_fence(self._core.event_store, db, self._core.task_id, action)
            result = {kind: facts(self._core.event_store, db, self._core.task_id, kind) for kind in TYPES}
        with self._core.event_store.connect() as db:
            read_fence(self._core.event_store, db, self._core.task_id, action)
        return result

    def issue_read_access(self, session, **kwargs):
        from ..registry.observer_access import issue_observer_access, _key
        action = mint_action(self._core.event_store, self._session(session), 'source_read', None)
        with self.command(action, _key(kwargs['command_id'])):
            return issue_observer_access(self._gateway, **kwargs)

    def revoke_read_access(self, session, **kwargs):
        from ..registry.observer_access import revoke_observer_access, _key
        action = mint_action(self._core.event_store, self._session(session), 'source_read', None)
        with self.command(action, _key(kwargs['command_id'])):
            return revoke_observer_access(self._gateway, **kwargs)


__all__ = ('UserCollaborationHost',)
