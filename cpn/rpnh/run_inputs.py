"""Trusted HOST external inputs at quiescent original-run boundaries.

Only the server owner holds this facade. Client JSON supplies opaque data, never
sessions or authority. No active firing may coexist with opening/accepting a
wait. A deadline without expiry_input rejects late input; expire() requires an
explicit expiry_input to produce a continuation token.
"""
import base64
import json
from functools import wraps

from .run import RunOwner, OwnerInput
from .registry.run_inputs import (InputWaitSpec, InputReceipt, WAIT, RECEIPT,
    command, command_key, reference, exact_ref, resource_ref, input_material, spec_material)
from .registry.models import VersionRef, PendingEvent
from .registry.publication import _version_from_payload, _stable_id
from .registry.resources import ResourceVersionRef, PublishResource, PrivateSystemOrigin
from .registry.resource_service import _ResourceServiceKernel, _publish_private_system
from .registry.schema_catalog import canonical_json, PROTECTED_SCHEMA_REFS
from .registry.strict_contracts import ref_payload, content_schema_ref_payload
from .registry.run_authority import stage_run_execution_checkpoint
from .registry._event_store import run_inputs as guard


def _serialized(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self.core.event_store._lock:
            return method(self, *args, **kwargs)
    return invoke


class RunInputHost:
    def __init__(self, owner: RunOwner):
        if not isinstance(owner, RunOwner) or owner._core.read_only:
            raise TypeError('RunInputHost requires the original RunOwner')
        self.owner, self.core = owner, owner._core

    def _replay(self, action, key):
        store = self.core.event_store
        with store._lock, store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            guard.writer(db, self.core.writer_epoch)
            row = db.execute('SELECT * FROM transactions WHERE task_id=? AND idempotency_key=?',
                             (str(self.core.task_id), key)).fetchone()
            if row is None:
                return None
            guard.check_replay(store, db, self.core.task_id, row, action)
            body = action.material()['envelope']
            kind = WAIT if body['operation'] == 'open' else RECEIPT
            return guard.exact(store, db, self.core.task_id,
                               ref_payload(reference(self.core.task_id, kind, key)), kind)

    @staticmethod
    def _receipt(doc, replayed=False):
        resource = _version_from_payload(doc['resource_ref'])
        return InputReceipt(_version_from_payload(doc['self_ref']),
            ResourceVersionRef(resource.entity_id, resource.version_id),
            _version_from_payload(doc['token_ref']), _version_from_payload(doc['checkpoint_ref']),
            _version_from_payload(doc['wait_ref']), replayed)

    @_serialized
    def open_wait(self, owner_session, *, spec: InputWaitSpec, command_id: str) -> VersionRef:
        key = command_key(command_id)
        material = spec_material(spec)
        action = command(self.core.event_store, owner_session, dict(operation='open', spec=material))
        prior = self._replay(action, key)
        if prior is not None:
            return _version_from_payload(prior['self_ref'])
        store = self.core.event_store
        with store.connect() as db:
            current, checkpoint, net, _compiled = guard.boundary(store, db, self.core.task_id)
            guard.check_open(store, db, self.core.task_id, action, current, checkpoint, net)
        wait_ref = reference(self.core.task_id, WAIT, key)
        cp_ref = self._checkpoint_ref(key)
        doc = dict(self_ref=ref_payload(wait_ref), command_id=key,
            owner_session_ref=ref_payload(owner_session.ref), task_ref=current['task_ref'],
            run_ref=current['run_ref'], net_ref=checkpoint['net_instance_ref'],
            source_ref=current['declaration_ref'], predecessor_checkpoint_ref=checkpoint['marking_checkpoint_ref'],
            checkpoint_ref=ref_payload(cp_ref), spec=material)
        tx = self.core.begin(idempotency_key=key,
                             net_instance_id=_version_from_payload(doc['net_ref']).entity_id)
        tx.bind_run_input_command(action)
        self._stage(tx, wait_ref, doc)
        self._checkpoint(tx, checkpoint, cp_ref, wait_ref, None, None, key)
        tx.commit()
        return wait_ref

    def submit(self, session, *, wait_ref: VersionRef, expected_checkpoint_ref: VersionRef,
               binding_ref: ResourceVersionRef, declaration: OwnerInput, command_id: str) -> InputReceipt:
        body = dict(operation='submit', wait_ref=exact_ref(wait_ref, WAIT),
            expected_checkpoint_ref=exact_ref(expected_checkpoint_ref, 'marking_checkpoint/v1'),
            binding_ref=resource_ref(binding_ref), declaration=input_material(declaration))
        return self._accept(session, body, command_id)

    def expire(self, owner_session, *, wait_ref: VersionRef, expected_checkpoint_ref: VersionRef,
               command_id: str) -> InputReceipt:
        body = dict(operation='expire', wait_ref=exact_ref(wait_ref, WAIT),
                    expected_checkpoint_ref=exact_ref(expected_checkpoint_ref, 'marking_checkpoint/v1'))
        return self._accept(owner_session, body, command_id)

    @_serialized
    def _accept(self, session, body, command_id):
        key = command_key(command_id)
        action = command(self.core.event_store, session, body)
        prior = self._replay(action, key)
        if prior is not None:
            return self._receipt(prior, True)
        store = self.core.event_store
        with store.connect() as db:
            current, checkpoint, net, compiled = guard.boundary(store, db, self.core.task_id)
            wait, declaration, principal = guard.check_accept(store, db, self.core.task_id,
                action, current, checkpoint, net, compiled)
            previous_state, previous_receipt = guard.status(store, db, self.core.task_id, wait)
            replacing = body['operation'] == 'expire' and previous_state == 'accepted'
        tx = self.core.begin(idempotency_key=key)
        tx.bind_run_input_command(action)
        binding = _version_from_payload(wait['spec']['binding_ref'])
        schema = declaration['schema_id']
        resource = _publish_private_system(self.core, self.owner.identity.task_ref, PublishResource(
            origin=PrivateSystemOrigin(self.owner.bootstrap_ref), payload=base64.b64decode(declaration['payload']),
            media_type='application/json', content_schema_ref=schema,
            content_schema_authority_ref=(None if schema in PROTECTED_SCHEMA_REFS else
                                          self.owner.schema_gateway.schema_refs[schema]),
            summary=declaration['summary'], lifetime_ref=self.owner.bootstrap_ref,
            derived_from=(ResourceVersionRef(binding.entity_id, binding.version_id),), descriptors={'run_input_command_id': key},
            idempotency_key=key), transaction=tx)
        receipt_ref = reference(self.core.task_id, RECEIPT, key)
        token_ref = VersionRef('petri_token/v1', _stable_id('petri_token', key, self.core.task_id),
                              _stable_id('petri_token_version', key, self.core.task_id))
        cp_ref = self._checkpoint_ref(key)
        port = next(p for p in compiled.ports if p.name == wait['spec']['input_port'])
        token = dict(petri_token_ref=ref_payload(token_ref), net_instance_ref=wait['net_ref'],
            token_id=checkpoint['next_token_id'], place=port.place, epoch=checkpoint['epoch'],
            producer=None, consumer=None, resource_ref=content_schema_ref_payload(resource),
            work_resource_ref=None, kind=None, consumed_by=None, override_warning=None,
            verdict=None, continuation=None, lease_identity_ref=None, lease_claims=[],
            input_receipt_ref=ref_payload(receipt_ref))
        doc = dict(self_ref=ref_payload(receipt_ref), command_id=key, wait_ref=wait['self_ref'],
            session_ref=ref_payload(session.ref), principal_ref=principal,
            disposition='expired' if body['operation'] == 'expire' else 'accepted',
            resource_ref=ref_payload(resource.as_version_ref()), token_ref=ref_payload(token_ref),
            checkpoint_ref=ref_payload(cp_ref), predecessor_checkpoint_ref=checkpoint['marking_checkpoint_ref'],
            binding_ref=wait['spec']['binding_ref'], declaration=declaration,
            supersedes_receipt_ref=previous_receipt['self_ref'] if replacing else None)
        self._stage(tx, token_ref, token)
        self._stage(tx, receipt_ref, doc)
        self._checkpoint(tx, checkpoint, cp_ref, _version_from_payload(wait['self_ref']),
                         receipt_ref, token_ref, key,
                         None if not replacing else previous_receipt['token_ref'])
        tx.commit()
        return self._receipt(doc)

    def _checkpoint_ref(self, key):
        return VersionRef('marking_checkpoint/v1', _stable_id('marking_checkpoint', self.core.task_id, key),
                          _stable_id('marking_checkpoint_version', self.core.task_id, key))

    @staticmethod
    def _stage(tx, ref, doc):
        tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(doc), metadata=doc, media_type='application/json',
            schema_ref='registry_v1/' + ref.entity_type)

    def _checkpoint(self, tx, previous, ref, wait_ref, receipt_ref, token_ref, key,
                    replaced_token_ref=None):
        fields = ('net_instance_ref', 'team_design_root_ref', 'epoch', 'next_token_id',
                  'attempts', 'token_refs', 'workspace_revision_refs')
        cp = {k: previous[k] for k in fields}
        cp.update(marking_checkpoint_ref=ref_payload(ref), settled=True,
            previous_checkpoint_ref=previous['marking_checkpoint_ref'], settlement_delta_ref=None,
            transition_firing_refs=[], input_wait_ref=ref_payload(wait_ref),
            input_receipt_ref=None if receipt_ref is None else ref_payload(receipt_ref))
        if token_ref is not None:
            retained = [r for r in cp['token_refs'] if r != replaced_token_ref]
            cp['token_refs'] = sorted([*retained, ref_payload(token_ref)], key=canonical_json)
            cp['next_token_id'] += 1
        self._stage(tx, ref, cp)
        payload = {k: v for k, v in cp.items() if k not in {
            'marking_checkpoint_ref', 'epoch', 'next_token_id', 'attempts', 'token_refs'}}
        payload['checkpoint_ref'] = ref_payload(ref)
        net = _version_from_payload(cp['net_instance_ref'])
        tx.expect_snapshot_predecessor(f'marking:{net.entity_id}', previous['marking_checkpoint_ref'])
        tx.append(PendingEvent(event_type='marking_checkpoint_committed/v1', criticality='authoritative',
            stream_id=f'marking:{net.entity_id}', aggregate_id=str(net.entity_id), aggregate_type='marking_checkpoint',
            idempotency_key=key, command_id=key, payload=payload,
            payload_schema_ref='registry_v1/marking_checkpoint_committed/v1', task_control=True))
        stage_run_execution_checkpoint(self.core, _ResourceServiceKernel(self.core), tx, ref,
                                       idempotency_key=key)

    def inspect(self, wait_ref: VersionRef):
        """Owner-internal projection; not a client material-reading endpoint."""
        with self.core.event_store.connect() as db:
            doc = guard.exact(self.core.event_store, db, self.core.task_id, exact_ref(wait_ref, WAIT), WAIT)
            status, receipt = guard.status(self.core.event_store, db, self.core.task_id, doc)
        raw = doc['spec']
        binding = _version_from_payload(raw['binding_ref'])
        expiry = raw['expiry_input']
        spec = InputWaitSpec(transition_id=raw['transition_id'], input_port=raw['input_port'],
            anchor_token_ref=_version_from_payload(raw['anchor_token_ref']),
            binding_ref=ResourceVersionRef(binding.entity_id, binding.version_id),
            allowed_principal_refs=tuple(_version_from_payload(r) for r in raw['allowed_principal_refs']),
            expires_at_utc=raw['expires_at_utc'],
            expiry_input=None if expiry is None else OwnerInput(expiry['schema_id'],
                base64.b64decode(expiry['payload']), expiry['summary']),
            supersedes_wait_ref=None if raw['supersedes_wait_ref'] is None else _version_from_payload(raw['supersedes_wait_ref']))
        result = dict(wait_ref=wait_ref, status=status, spec=spec)
        if receipt is not None:
            result['receipt'] = self._receipt(receipt)
        return result

    def waits(self):
        with self.core.event_store.connect() as db:
            refs = [d['self_ref'] for d in guard.facts(self.core.event_store, db, self.core.task_id, WAIT)]
        return tuple(self.inspect(_version_from_payload(r)) for r in refs)


__all__ = ('RunInputHost', 'InputWaitSpec', 'InputReceipt')
