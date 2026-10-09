"""Marking snapshot, typed checkpoint, validation, and recovery mechanics."""
from __future__ import annotations

import itertools
import json
import math
import random
import uuid
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.petri_runtime_interfaces import (
    MarkingNetStructure,
    RegistryTokenAllocator,
)
from cpn.rpnh.marking import (
    ActiveFiringClaim,
    BindingEnumeration,
    BindingQueueOverflow,
    BoundInputToken,
    FiringAllocationEvidence,
    FiringClaimOccurrence,
    MarkingResourceRefError,
    MarkingStateError,
    PetriFiringResourceAccess,
    ResourceDemand,
    SelectedRouteEpochEffectResult,
    TeamNetMarking,
    TimedBinding,
    TimedMarkingCheckpointAuthority,
    TimedMarkingDeltaMaterial,
    TimedTerminalOutput,
    TimedTerminalProposal,
    TimedWaitGuardState,
    Token,
    VERDICT_GUARD_KIND,
    _ActiveClaim,
    _PendingClaim,
    _canonical_marking_material,
    _canonical_timed_claim_rows,
    _nonnegative_integer,
    _verdict_color_matches,
    _version_ref_key,
    continuation_from_tokens,
    count_guard_holds,
    exact_token_identity,
)

if TYPE_CHECKING:
    from cpn.rpnh.registry.resources import (
        AttemptCounterAuthority,
        ExecutableNetAuthority,
        PetriTokenState,
        TypedMarkingAuthority,
        TypedMarkingSnapshot,
    )

def marking_snapshot(self) -> dict:
    """A records-only snapshot of the live marking (observability / resume).

        ``{place: <fresh token count>}`` over places that currently hold a fresh
        token, plus the epoch and the total live token count. Pure read; no LLM,
        no judgment.

        """
    with self._lock:
        counts: dict[str, int] = {}
        for tok in self._tokens:
            if tok.epoch == self._epoch and tok.consumed_by is None:
                counts[tok.place] = counts.get(tok.place, 0) + 1
        return {'epoch': self._epoch, 'fresh_by_place': counts, 'tokens_total': len(self._tokens)}

def _state_int(value: object, path: str, *, minimum: int=0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise MarkingStateError(f'{path} must be an integer >= {minimum}, got {value!r}')
    return value

def _optional_string(value: object, path: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise MarkingStateError(f'{path} must be null or a non-empty canonical string, got {value!r}')
    return value

def _token_ref_payload(value: Optional[VersionRef], path: str) -> Optional[dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, VersionRef) or value.entity_type != 'petri_token/v1' or value.entity_id.kind != 'petri_token' or (value.version_id.kind != 'petri_token_version'):
        raise MarkingStateError(f'{path} must be an exact petri_token/v1 VersionRef')
    return {'entity_type': value.entity_type, 'logical_id': str(value.entity_id), 'version_id': str(value.version_id)}

def _token_ref_state(cls, value: object, path: str, *, required: bool=False) -> Optional[VersionRef]:
    if value is None:
        if required:
            raise MarkingStateError(f'{path} requires an exact petri_token/v1 VersionRef')
        return None
    if not isinstance(value, dict):
        raise MarkingStateError(f'{path} must be null or an exact-ref object')
    expected = {'entity_type', 'logical_id', 'version_id'}
    if set(value) != expected:
        raise MarkingStateError(f'{path} fields must be exactly {sorted(expected)!r}')
    try:
        ref = VersionRef(str(value['entity_type']), TypedId.parse(str(value['logical_id']), expected='petri_token'), TypedId.parse(str(value['version_id']), expected='petri_token_version'))
    except (TypeError, ValueError) as exc:
        raise MarkingStateError(f'{path} is not an exact Petri token identity') from exc
    cls._token_ref_payload(ref, path)
    if ref.entity_type != value['entity_type'] or str(ref.entity_id) != value['logical_id'] or str(ref.version_id) != value['version_id']:
        raise MarkingStateError(f'{path} exact-ref fields are not canonical')
    return ref

def _resource_ref_payload(value: Optional[ResourceVersionRef], path: str) -> Optional[dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, ResourceVersionRef):
        raise MarkingResourceRefError(f'{path} must be an exact ResourceVersionRef')
    return {'resource_id': str(value.resource_id), 'resource_version_id': str(value.resource_version_id)}

def _resource_ref_state(cls, value: object, path: str, *, required: bool=False) -> Optional[ResourceVersionRef]:
    if value is None:
        if required:
            raise MarkingResourceRefError(f'{path} requires an exact ResourceVersionRef')
        return None
    if not isinstance(value, dict):
        raise MarkingResourceRefError(f'{path} must be null or a typed-id string object')
    expected = {'resource_id', 'resource_version_id'}
    if set(value) != expected:
        raise MarkingResourceRefError(f'{path} fields must be exactly {sorted(expected)!r}, got {sorted(value, key=repr)!r}')
    resource_id = value['resource_id']
    version_id = value['resource_version_id']
    if not isinstance(resource_id, str) or not isinstance(version_id, str):
        raise MarkingResourceRefError(f'{path} typed identities must be canonical strings')
    try:
        parsed_resource = TypedId.parse(resource_id, expected='resource')
        parsed_version = TypedId.parse(version_id, expected='resource_version')
        result = ResourceVersionRef(parsed_resource, parsed_version)
    except (TypeError, ValueError) as exc:
        raise MarkingResourceRefError(f'{path} is not an exact resource-version identity: {exc}') from exc
    if str(result.resource_id) != resource_id or str(result.resource_version_id) != version_id:
        raise MarkingResourceRefError(f'{path} typed identities are not canonical')
    return result

def _lease_identity_ref_payload(value: Optional[VersionRef], path: str) -> Optional[dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, VersionRef) or value.entity_type not in {'resource_version/v1', 'logical_artifact_slot/v1'}:
        raise MarkingResourceRefError(f'{path} must be an exact resource or logical-slot VersionRef')
    return {'entity_type': value.entity_type, 'logical_id': str(value.entity_id), 'version_id': str(value.version_id)}

def _lease_identity_ref_state(cls, value: object, path: str, *, required: bool=False) -> Optional[VersionRef]:
    if value is None:
        if required:
            raise MarkingResourceRefError(f'{path} requires an exact lease identity')
        return None
    if not isinstance(value, dict) or set(value) != {'entity_type', 'logical_id', 'version_id'}:
        raise MarkingResourceRefError(f'{path} must be null or one closed exact ref')
    try:
        ref = VersionRef(str(value['entity_type']), TypedId.parse(str(value['logical_id'])), TypedId.parse(str(value['version_id'])))
    except (TypeError, ValueError) as exc:
        raise MarkingResourceRefError(f'{path} is not a canonical exact ref') from exc
    if cls._lease_identity_ref_payload(ref, path) != value:
        raise MarkingResourceRefError(f'{path} exact-ref fields are not canonical')
    return ref

def _lease_claims_state(cls, value: object, path: str) -> tuple[dict, ...]:
    if not isinstance(value, list):
        raise MarkingStateError(f'{path} must be an array')
    claims: list[dict] = []
    identities: list[VersionRef] = []
    for index, item in enumerate(value):
        item_path = f'{path}[{index}]'
        required = {'lease_identity_ref', 'expected_resource_ref', 'access_mode'}
        if not isinstance(item, dict) or not required.issubset(item) or set(item) - required != ({'staging_place'} if 'staging_place' in item else set()):
            raise MarkingStateError(f'{item_path} must be one closed lease claim')
        identity = cls._lease_identity_ref_state(item['lease_identity_ref'], f'{item_path}.lease_identity_ref', required=True)
        assert identity is not None
        expected = cls._resource_ref_state(item['expected_resource_ref'], f'{item_path}.expected_resource_ref')
        access_mode = item['access_mode']
        if access_mode not in {'read', 'edit', 'produce'}:
            raise MarkingStateError(f'{item_path}.access_mode is outside the closed enum')
        staging_place = item.get('staging_place')
        if staging_place is not None and (not isinstance(staging_place, str) or not staging_place):
            raise MarkingStateError(f'{item_path}.staging_place must be a nonempty string')
        identities.append(identity)
        claims.append({'lease_identity_ref': identity, 'expected_resource_ref': expected, 'access_mode': access_mode, **({'staging_place': staging_place} if staging_place is not None else {})})
    if len(identities) != len(set(identities)):
        raise MarkingStateError(f'{path} repeats one lease identity')
    return tuple(claims)

def _runtime_lease_claims(cls, value: object, path: str) -> tuple[dict, ...]:
    if not isinstance(value, tuple):
        raise MarkingStateError(f'{path} must be a tuple')
    payload: list[dict] = []
    for index, item in enumerate(value):
        required = {'lease_identity_ref', 'expected_resource_ref', 'access_mode'}
        if not isinstance(item, dict) or not required.issubset(item) or set(item) - required != ({'staging_place'} if 'staging_place' in item else set()):
            raise MarkingStateError(f'{path}[{index}] must be one closed runtime lease claim')
        payload.append({'lease_identity_ref': cls._lease_identity_ref_payload(item['lease_identity_ref'], f'{path}[{index}].lease_identity_ref'), 'expected_resource_ref': cls._resource_ref_payload(item['expected_resource_ref'], f'{path}[{index}].expected_resource_ref'), 'access_mode': item['access_mode'], **({'staging_place': item['staging_place']} if 'staging_place' in item else {})})
    return cls._lease_claims_state(payload, path)

def _continuation_state(cls, value: object, path: str) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MarkingStateError(f'{path} must be null or an object')
    expected = {'round', 'source_ref'}
    if set(value) != expected:
        raise MarkingStateError(f'{path} fields must be exactly {sorted(expected)!r}, got {sorted(value, key=repr)!r}')
    round_ = cls._state_int(value['round'], f'{path}.round')
    source_ref = cls._resource_ref_state(value['source_ref'], f'{path}.source_ref', required=True)
    return {'round': round_, 'source_ref': source_ref}

def _runtime_continuation(value: object, path: str) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'round', 'source_ref'} or (not isinstance(value.get('round'), int)) or isinstance(value.get('round'), bool) or (value['round'] < 0) or (not isinstance(value.get('source_ref'), ResourceVersionRef)):
        raise MarkingResourceRefError(f'{path} requires a non-negative round and exact ResourceVersionRef')
    return {'round': value['round'], 'source_ref': value['source_ref']}

def _validate_runtime_token_refs(cls, tokens: list[Token], path: str, *, require_token_ref: bool=False) -> None:
    """Reject an in-memory token that could smuggle a selector into a ref flow."""
    for index, token in enumerate(tokens):
        token_path = f'{path}[{index}]'
        if token.token_ref is None:
            if require_token_ref:
                raise MarkingStateError(f'{token_path}.token_ref lacks committed petri_token/v1 authority')
        else:
            cls._token_ref_payload(token.token_ref, f'{token_path}.token_ref')
        if token.resource_ref is not None and (not isinstance(token.resource_ref, ResourceVersionRef)):
            raise MarkingResourceRefError(f'{token_path}.resource_ref must be an exact ResourceVersionRef')
        if token.work_resource_ref is not None and (not isinstance(token.work_resource_ref, ResourceVersionRef)):
            raise MarkingResourceRefError(f'{token_path}.work_resource_ref must be an exact ResourceVersionRef')
        if token.work_resource_ref is not None and token.resource_ref is None:
            raise MarkingResourceRefError(f'{token_path}.work_resource_ref requires a primary resource_ref')
        if token.kind is not None and token.resource_ref is None:
            raise MarkingResourceRefError(f'{token_path} semantic token requires an exact resource_ref')
        cls._runtime_continuation(token.continuation, f'{token_path}.continuation')
        cls._lease_identity_ref_payload(token.lease_identity_ref, f'{token_path}.lease_identity_ref')
        cls._runtime_lease_claims(token.lease_claims, f'{token_path}.lease_claims')

def _token_payload(self, token: Token) -> dict:
    payload: dict = {}
    for field_name in self._TOKEN_FIELDS:
        value = getattr(token, field_name)
        if field_name == 'token_ref':
            payload[field_name] = self._token_ref_payload(value, 'token.token_ref')
        elif field_name in {'resource_ref', 'work_resource_ref'}:
            payload[field_name] = self._resource_ref_payload(value, f'token.{field_name}')
        elif field_name == 'continuation' and value is not None:
            if not isinstance(value, dict) or set(value) != {'round', 'source_ref'}:
                raise MarkingResourceRefError('token.continuation is not a closed continuation object')
            payload[field_name] = {'round': value['round'], 'source_ref': self._resource_ref_payload(value['source_ref'], 'token.continuation.source_ref')}
        elif field_name == 'lease_identity_ref':
            payload[field_name] = self._lease_identity_ref_payload(value, 'token.lease_identity_ref')
        elif field_name == 'lease_claims':
            claims = self._runtime_lease_claims(value, 'token.lease_claims')
            payload[field_name] = [{'lease_identity_ref': self._lease_identity_ref_payload(claim['lease_identity_ref'], 'token.lease_claims[].lease_identity_ref'), 'expected_resource_ref': self._resource_ref_payload(claim['expected_resource_ref'], 'token.lease_claims[].expected_resource_ref'), 'access_mode': claim['access_mode'], **({'staging_place': claim['staging_place']} if 'staging_place' in claim else {})} for claim in claims]
        else:
            payload[field_name] = deepcopy(value)
    return payload

def _override_warning_state(self, value: object, path: str, *, place: str) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MarkingStateError(f'{path} must be null or an object')
    expected = {'reason', 'gate', 'overridden_attempt', 'escalation_provenance', 'gate_output_place'}
    if set(value) != expected:
        raise MarkingStateError(f'{path} fields must be exactly {sorted(expected)!r}, got {sorted(value, key=repr)!r}')
    reason = self._optional_string(value['reason'], f'{path}.reason')
    provenance = self._optional_string(value['escalation_provenance'], f'{path}.escalation_provenance')
    if reason is None or provenance is None:
        raise MarkingStateError(f'{path} reason and escalation_provenance must not be null')
    gate = value['gate']
    if not isinstance(gate, list) or len(gate) != 2:
        raise MarkingStateError(f'{path}.gate must be a two-item array')
    reviewer = self._optional_string(gate[0], f'{path}.gate[0]')
    author = self._optional_string(gate[1], f'{path}.gate[1]')
    transitions = set(self._net.transitions)
    if reviewer not in transitions:
        raise MarkingStateError(f'{path}.gate[0] is not a declared transition')
    if author is not None and author not in transitions:
        raise MarkingStateError(f'{path}.gate[1] is not a declared transition')
    overridden = self._optional_string(value['overridden_attempt'], f'{path}.overridden_attempt')
    output_place = self._optional_string(value['gate_output_place'], f'{path}.gate_output_place')
    if output_place != place:
        raise MarkingStateError(f'{path}.gate_output_place must equal the token place {place!r}')
    return {'reason': reason, 'gate': [reviewer, author], 'overridden_attempt': overridden, 'escalation_provenance': provenance, 'gate_output_place': output_place}

def _validated_state(self, data: object) -> tuple[int, int, dict[str, int], list[Token]]:
    """Validate a complete checkpoint without mutating this marking."""
    if not isinstance(data, dict):
        raise MarkingStateError(f'marking checkpoint must be an object, got {type(data).__name__}')
    if set(data) != self._STATE_FIELDS:
        raise MarkingStateError(f'marking checkpoint fields must be exactly {sorted(self._STATE_FIELDS)!r}, got {sorted(data, key=repr)!r}')
    epoch = self._state_int(data['epoch'], 'epoch')
    next_id = self._state_int(data['next_id'], 'next_id')
    raw_attempts = data['attempts']
    if not isinstance(raw_attempts, dict):
        raise MarkingStateError('attempts must be an object')
    business_transitions = set(self._net.transitions)
    transitions = business_transitions | set(self._net.registered_fault_transition_routes)
    attempts: dict[str, int] = {}
    for transition_id, value in raw_attempts.items():
        if not isinstance(transition_id, str) or transition_id not in transitions:
            raise MarkingStateError(f'attempts key {transition_id!r} is not a declared transition')
        attempts[transition_id] = self._state_int(value, f'attempts[{transition_id!r}]', minimum=1)
    raw_tokens = data['tokens']
    if not isinstance(raw_tokens, list):
        raise MarkingStateError('tokens must be an array')
    ignition_consumers = {place: transition_id for place, transition_id, _weight in self._net.synthetic_ignition_arcs}
    declared_places = set(self._net.places)
    allowed_places = declared_places | set(self._net.registered_fault_places) | set(ignition_consumers)
    required_token_fields = set(self._TOKEN_FIELDS)
    tokens: list[Token] = []
    token_ids: list[int] = []
    for index, row in enumerate(raw_tokens):
        path = f'tokens[{index}]'
        if not isinstance(row, dict):
            raise MarkingStateError(f'{path} must be an object')
        if set(row) != required_token_fields:
            raise MarkingStateError(f'{path} fields must be exactly {sorted(required_token_fields)!r}, got {sorted(row, key=repr)!r}')
        token_ref = self._token_ref_state(row['token_ref'], f'{path}.token_ref')
        token_id = self._state_int(row['token_id'], f'{path}.token_id')
        place = self._optional_string(row['place'], f'{path}.place')
        if place is None or place not in allowed_places:
            raise MarkingStateError(f'{path}.place {place!r} is not declared by the current net')
        token_epoch = self._state_int(row['epoch'], f'{path}.epoch')
        if token_epoch > epoch:
            raise MarkingStateError(f'{path}.epoch {token_epoch} exceeds marking epoch {epoch}')
        producer = self._optional_string(row['producer'], f'{path}.producer')
        consumer = self._optional_string(row['consumer'], f'{path}.consumer')
        resource_ref = self._resource_ref_state(row['resource_ref'], f'{path}.resource_ref')
        work_resource_ref = self._resource_ref_state(row['work_resource_ref'], f'{path}.work_resource_ref')
        if work_resource_ref is not None and resource_ref is None:
            raise MarkingResourceRefError(f'{path}.work_resource_ref requires a primary resource_ref')
        kind = self._optional_string(row['kind'], f'{path}.kind')
        consumed_by = self._optional_string(row['consumed_by'], f'{path}.consumed_by')
        is_lease_pool_place = any((place == pool.lease_pool_place for pool in self._net.resource_lease_pool_bindings))
        if producer is not None and producer not in transitions:
            raise MarkingStateError(f'{path}.producer is not a declared transition')
        if consumer is not None and consumer not in transitions:
            raise MarkingStateError(f'{path}.consumer is not a declared transition')
        if consumed_by is not None and consumed_by != self.INVALIDATED:
            if consumed_by not in transitions:
                raise MarkingStateError(f'{path}.consumed_by is not a declared transition or {self.INVALIDATED!r}')
            if consumer is not None and consumed_by != consumer:
                raise MarkingStateError(f'{path}.consumed_by does not match its addressed consumer')
        if place in ignition_consumers:
            ignition_consumer = ignition_consumers[place]
            if producer is not None or consumer != ignition_consumer:
                raise MarkingStateError(f'{path} is not the declared ignition token for {ignition_consumer!r}')
            if consumed_by not in (None, self.INVALIDATED, ignition_consumer):
                raise MarkingStateError(f'{path}.consumed_by is inconsistent with its ignition place')
        else:
            place_producers = set(self._net.producers_of(place)) | {transition_id for transition_id in self._net.registered_fault_transition_routes if place in self._net.registered_fault_outputs_of(transition_id)}
            place_consumers = set(self._net.consumers_of(place)) | set(self._net.registered_fault_consumers_of(place))
            if is_lease_pool_place:
                variable_users = {arc.transition_id for arc in self._net.variable_resource_arcs}
                place_producers |= variable_users
                place_consumers |= variable_users
            if producer is not None and producer not in place_producers:
                raise MarkingStateError(f'{path}.producer does not declare an output arc to {place!r}')
            if consumer is not None and consumer not in place_consumers:
                raise MarkingStateError(f'{path}.consumer does not declare an input arc from {place!r}')
            if consumed_by not in (None, self.INVALIDATED) and consumed_by not in place_consumers:
                raise MarkingStateError(f'{path}.consumed_by does not declare an input arc from {place!r}')
        if kind is not None and resource_ref is None:
            raise MarkingResourceRefError(f'{path} semantic token kind {kind!r} requires an exact resource_ref')
        verdict = row['verdict']
        if not (verdict is None or type(verdict) is bool or (isinstance(verdict, str) and verdict and (verdict == verdict.strip()))):
            raise MarkingStateError(f'{path}.verdict must be null, a boolean, or a non-empty canonical string')
        self._require_concrete_place_colour(place, verdict, path=path)
        continuation = self._continuation_state(row['continuation'], f'{path}.continuation')
        lease_identity_ref = self._lease_identity_ref_state(row['lease_identity_ref'], f'{path}.lease_identity_ref')
        lease_claims = self._lease_claims_state(row['lease_claims'], f'{path}.lease_claims')
        override_warning = self._override_warning_state(row['override_warning'], f'{path}.override_warning', place=place)
        if is_lease_pool_place != (lease_identity_ref is not None):
            raise MarkingStateError(f'{path} lease identity must occur exactly on the v6 lease pool')
        producer_staging_outputs = {output_place for output_place in self._net.outputs_of(producer) if self._net.output_emit_for(producer, output_place) in {None, 'forward'}} if lease_claims and producer in self._net.transitions else set()
        formal_claim_carrier = producer in self._net.transitions and place in self._net.outputs_of(producer) and (self._net.output_lease_claim_set_for(producer, place) is not None or bool(self._net.output_lease_claims_for(producer, place)))
        if lease_claims and (not any((arc.claim_token_place == place for arc in self._net.variable_resource_arcs))) and (not formal_claim_carrier) and (not all((claim.get('staging_place') == place for claim in lease_claims))) and (not (producer in self._net.transitions and place in self._net.outputs_of(producer) and all((claim.get('staging_place') in producer_staging_outputs for claim in lease_claims)))):
            raise MarkingStateError(f'{path} lease claims occur outside a declared claim-token place, exact staging place, or registered produced carrier')
        if is_lease_pool_place and (consumer is not None or lease_claims):
            raise MarkingStateError(f'{path} lease-pool token must be shared and carry no claim set')
        if lease_identity_ref is not None and lease_identity_ref.entity_type == 'resource_version/v1' and (resource_ref is None or lease_identity_ref.entity_id != resource_ref.resource_id or lease_identity_ref.version_id != resource_ref.resource_version_id):
            raise MarkingResourceRefError(f'{path} resource lease identity/current version must be exact')
        tokens.append(Token(token_id=token_id, token_ref=token_ref, place=place, epoch=token_epoch, producer=producer, consumer=consumer, resource_ref=resource_ref, work_resource_ref=work_resource_ref, kind=kind, consumed_by=consumed_by, override_warning=override_warning, verdict=verdict, continuation=continuation, lease_identity_ref=lease_identity_ref, lease_claims=lease_claims))
        token_ids.append(token_id)
    live_lease_identities = [token.lease_identity_ref for token in tokens if token.epoch == epoch and token.consumed_by is None and (token.lease_identity_ref is not None)]
    if len(live_lease_identities) != len(set(live_lease_identities)):
        raise MarkingStateError('live resource-lease marking violates exact M=1 identity')
    if len(token_ids) != len(set(token_ids)):
        raise MarkingStateError('tokens contain duplicate token_id values')
    if token_ids != sorted(token_ids):
        raise MarkingStateError('tokens must be ordered by strictly increasing token_id')
    if token_ids and next_id <= token_ids[-1]:
        raise MarkingStateError(f'next_id {next_id} must be greater than maximum token_id {token_ids[-1]}')
    return (epoch, next_id, attempts, tokens)

def _validated_candidate_mapping(self) -> dict:
    """Build the private closed mapping consumed only by the local validator.

        This is deliberately not a serialization API.  The mapping never leaves this
        object: it exists solely to reuse one strict structural validator while producing
        Registry's typed snapshot or consuming Registry's typed authority.
        """
    with self._lock:
        candidate = {'epoch': self._epoch, 'next_id': self._next_id, 'attempts': {key: self._attempts[key] for key in sorted(self._attempts)}, 'tokens': [self._token_payload(tok) for tok in sorted(self._tokens, key=lambda item: item.token_id)]}
        epoch, next_id, attempts, tokens = self._validated_state(candidate)
        return {'epoch': epoch, 'next_id': next_id, 'attempts': {key: attempts[key] for key in sorted(attempts)}, 'tokens': [self._token_payload(token) for token in tokens]}

def _restore_candidate_mapping(self, data: object) -> int:
    """Atomically adopt one already-authorized private candidate mapping.

        Callers must first cross a typed Registry authority boundary.  This helper has no
        public recovery semantics and never accepts a checkpoint from outside the object.
        """
    with self._lock:
        epoch, next_id, attempts, tokens = self._validated_state(data)
        self._tokens = tokens
        self._epoch = epoch
        self._next_id = next_id
        self._attempts = attempts
        self._active_claims = {}
        self._next_claim_id = 0
        self._timed_active_claims = {}
        return len(tokens)

def carry_to(self, net: 'MarkingNetStructure') -> 'TeamNetMarking':
    """Return a typed in-process copy validated against ``net``.

        Runtime growth uses this only while assembling an uncommitted Registry proposal.
        No mapping, token dictionary, checkpoint bytes, or selector crosses the call.
        Crossing to an unregistered or structurally different net clears every token's
        authority ref: the candidate cannot enable until Registry derives/publishes the new
        per-token versions and returns a typed checkpoint authority.
        """
    with self._lock:
        if self._timed_active_claims:
            raise MarkingStateError('cannot carry a marking with unsettled firing-keyed claims')
    candidate = self._validated_candidate_mapping()
    carried = type(self)(net)
    carried._restore_candidate_mapping(candidate)
    same_registered_net = isinstance(self._net.registry_net_ref, VersionRef) and self._net.registry_net_ref == net.registry_net_ref
    if not same_registered_net:
        with carried._lock:
            carried._tokens = [replace(token, token_ref=None) for token in carried._tokens]
    return carried

def from_authority(cls, net: 'MarkingNetStructure', authority: TypedMarkingAuthority) -> 'TeamNetMarking':
    """Restore only from Registry's typed, verified marking authority.

        The executor never reconstructs a legacy monolithic state mapping.  Every
        token is a separately registered ``petri_token/v1`` record, attempts are a
        canonical sorted tuple, and the loaded net must have been stamped by
        :func:`team_net_loader.load_executable_team_net` with the same adopted net ref.
        """
    from cpn.rpnh.registry.resources import AttemptCounterAuthority, PetriContinuation, PetriLeaseClaim, PetriOverrideWarning, PetriTokenState, PetriTokenAuthority, TypedMarkingAuthority
    if not isinstance(authority, TypedMarkingAuthority):
        raise MarkingStateError('from_authority requires a TypedMarkingAuthority')
    checkpoint_ref = authority.checkpoint_ref
    if not isinstance(checkpoint_ref, VersionRef) or checkpoint_ref.entity_type != 'marking_checkpoint/v1' or checkpoint_ref.entity_id.kind != 'marking_checkpoint' or (checkpoint_ref.version_id.kind != 'marking_checkpoint_version'):
        raise MarkingStateError('typed marking authority lacks an exact marking checkpoint ref')
    if not isinstance(net.registry_net_ref, VersionRef) or net.registry_net_ref != authority.net_ref:
        raise MarkingStateError('typed marking authority belongs to a different or unbound TeamNet')
    attempts = authority.attempts
    if not isinstance(attempts, tuple) or any((not isinstance(item, AttemptCounterAuthority) for item in attempts)):
        raise MarkingStateError('typed marking attempts are not AttemptCounterAuthority records')
    attempt_keys = tuple((item.transition_id for item in attempts))
    if attempt_keys != tuple(sorted(attempt_keys)) or len(set(attempt_keys)) != len(attempt_keys):
        raise MarkingStateError('typed marking attempts must be sorted and unique')
    tokens = authority.tokens
    if not isinstance(tokens, tuple) or any((not isinstance(item, PetriTokenAuthority) for item in tokens)):
        raise MarkingStateError('typed marking tokens are not PetriTokenAuthority records')
    if not isinstance(authority.token_refs, tuple) or any((not isinstance(ref, VersionRef) for ref in authority.token_refs)):
        raise MarkingStateError('typed marking token refs are not an exact-ref tuple')
    token_refs = tuple((item.token_ref for item in tokens))
    if any((not isinstance(ref, VersionRef) for ref in token_refs)):
        raise MarkingStateError('typed marking token authorities lack exact refs')
    canonical_refs = tuple(sorted(token_refs, key=lambda ref: (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    if authority.token_refs != canonical_refs or len(set(token_refs)) != len(token_refs):
        raise MarkingStateError('typed marking token refs differ from their canonical token records')
    restored_tokens: list[Token] = []
    authority_token_ids: list[int] = []
    for index, item in enumerate(tokens):
        cls._token_ref_payload(item.token_ref, f'tokens[{index}].token_ref')
        state = item.state
        if not isinstance(state, PetriTokenState):
            raise MarkingStateError(f'tokens[{index}].state is not a PetriTokenState')
        if state.token_ref != item.token_ref:
            raise MarkingStateError(f'tokens[{index}] authority ref differs from its typed state')
        if state.continuation is not None and (not isinstance(state.continuation, PetriContinuation)):
            raise MarkingStateError(f'tokens[{index}].continuation is not typed')
        if state.override_warning is not None and (not isinstance(state.override_warning, PetriOverrideWarning)):
            raise MarkingStateError(f'tokens[{index}].override_warning is not typed')
        if not isinstance(state.lease_claims, tuple) or any((not isinstance(claim, PetriLeaseClaim) for claim in state.lease_claims)):
            raise MarkingStateError(f'tokens[{index}].lease_claims are not typed')
        if any((claim.staging_place is not None and (not isinstance(claim.staging_place, str) or not claim.staging_place) for claim in state.lease_claims)):
            raise MarkingStateError(f'tokens[{index}].lease_claims have an invalid staging place')
        authority_token_ids.append(state.token_id)
        continuation = None if state.continuation is None else {'round': state.continuation.round, 'source_ref': state.continuation.source_ref}
        warning = None if state.override_warning is None else {'reason': state.override_warning.reason, 'gate': [state.override_warning.gate_transition_id, state.override_warning.gate_peer_transition_id], 'overridden_attempt': state.override_warning.overridden_attempt, 'escalation_provenance': state.override_warning.escalation_provenance, 'gate_output_place': state.override_warning.gate_output_place}
        lease_claims = tuple(({'lease_identity_ref': claim.lease_identity_ref, 'expected_resource_ref': claim.expected_resource_ref, 'access_mode': claim.access_mode, **({'staging_place': claim.staging_place} if claim.staging_place is not None else {})} for claim in state.lease_claims))
        restored_tokens.append(Token(token_ref=item.token_ref, token_id=state.token_id, place=state.place, epoch=state.epoch, producer=state.producer, consumer=state.consumer, resource_ref=state.resource_ref, work_resource_ref=state.work_resource_ref, kind=state.kind, consumed_by=state.consumed_by, override_warning=warning, verdict=state.verdict, continuation=continuation, lease_identity_ref=state.lease_identity_ref, lease_claims=lease_claims))
    if authority_token_ids != sorted(authority_token_ids) or len(set(authority_token_ids)) != len(authority_token_ids):
        raise MarkingStateError('typed marking token authorities must be unique and token-id sorted')
    marking = cls(net)
    candidate = {'epoch': authority.epoch, 'next_id': authority.next_token_id, 'attempts': {item.transition_id: item.highest_issued for item in attempts}, 'tokens': [marking._token_payload(item) for item in sorted(restored_tokens, key=lambda tok: tok.token_id)]}
    marking._restore_candidate_mapping(candidate)
    marking._validate_runtime_token_refs(marking._tokens, 'typed_marking.tokens', require_token_ref=True)
    return marking

def typed_snapshot(self, executable: ExecutableNetAuthority, registry: RegistryTokenAllocator) -> TypedMarkingSnapshot:
    """Build Registry's normalized typed snapshot for one atomic settlement.

        Existing token refs are never copied blindly. Registry derives each exact
        ``petri_token/v1`` version from the net, token id, and epoch; an unchanged
        occurrence retains its ref and a later epoch obtains a new version ref.
        Registry validates and publishes those provisional refs atomically with the
        checkpoint.  No raw marking mapping or process-local token map crosses out.
        """
    from cpn.rpnh.registry.resources import AttemptCounterAuthority, ExecutableNetAuthority, PetriContinuation, PetriLeaseClaim, PetriOverrideWarning, PetriTokenState, TypedMarkingSnapshot
    if not isinstance(executable, ExecutableNetAuthority):
        raise MarkingStateError('typed_snapshot requires an ExecutableNetAuthority')
    if not isinstance(registry, RegistryTokenAllocator):
        raise MarkingStateError('typed_snapshot requires a RegistryTokenAllocator')
    if not isinstance(self._net.registry_net_ref, VersionRef) or self._net.registry_net_ref != executable.net_ref:
        raise MarkingStateError('typed_snapshot net differs from its executable Registry closure')
    with self._lock:
        if self._timed_active_claims:
            raise MarkingStateError('Registry projection of active timed firing claims is not yet wired')
        candidate = self._validated_candidate_mapping()
        self._validated_state(candidate)
        states: list[PetriTokenState] = []
        live_tokens = tuple((token for token in sorted(self._tokens, key=lambda item: item.token_id) if token.epoch == self._epoch and token.consumed_by is None))
        for index, token in enumerate(live_tokens):
            continuation = None
            if token.continuation is not None:
                value = self._runtime_continuation(token.continuation, f'tokens[{index}].continuation')
                assert value is not None
                continuation = PetriContinuation(round=value['round'], source_ref=value['source_ref'])
            warning = None
            if token.override_warning is not None:
                value = self._override_warning_state(token.override_warning, f'tokens[{index}].override_warning', place=token.place)
                assert value is not None
                reviewer, author = value['gate']
                overridden = value['overridden_attempt']
                if author is None or overridden is None:
                    raise MarkingStateError('typed override warning requires exact author and attempt ids')
                warning = PetriOverrideWarning(reason=value['reason'], gate_transition_id=reviewer, gate_peer_transition_id=author, overridden_attempt=overridden, escalation_provenance=value['escalation_provenance'], gate_output_place=value['gate_output_place'])
            lease_claims = tuple((PetriLeaseClaim(lease_identity_ref=claim['lease_identity_ref'], expected_resource_ref=claim['expected_resource_ref'], access_mode=claim['access_mode'], staging_place=claim.get('staging_place')) for claim in self._runtime_lease_claims(token.lease_claims, f'tokens[{index}].lease_claims')))
            draft = PetriTokenState(token_ref=None, token_id=token.token_id, place=token.place, epoch=token.epoch, producer=token.producer, consumer=token.consumer, resource_ref=token.resource_ref, work_resource_ref=token.work_resource_ref, kind=token.kind, consumed_by=token.consumed_by, override_warning=warning, verdict=token.verdict, continuation=continuation, lease_identity_ref=token.lease_identity_ref, lease_claims=lease_claims)
            token_ref = token.token_ref or registry.petri_token_ref(executable, token_id=token.token_id)
            self._token_ref_payload(token_ref, f'tokens[{index}].token_ref')
            states.append(replace(draft, token_ref=token_ref))
        attempts = tuple((AttemptCounterAuthority(transition_id=transition_id, highest_issued=self._attempts[transition_id]) for transition_id in sorted(self._attempts)))
        snapshot = TypedMarkingSnapshot(epoch=self._epoch, next_token_id=self._next_id, attempts=attempts, tokens=tuple(states))
        validate_typed_marking_state(self._net, epoch=snapshot.epoch, next_token_id=snapshot.next_token_id, attempts=snapshot.attempts, tokens=snapshot.tokens, require_token_refs=True)
        return snapshot

def pure_typed_snapshot(self, executable: 'ExecutableNetAuthority' = None, *,
        proposed_net_ref: VersionRef | None = None,
        ordinary_token_ref_scheme: str | None = None,
        allocation_firing_ref: VersionRef | None = None) -> 'TypedMarkingSnapshot':
    """Build a typed snapshot without Registry calls or mutable clocks.

    proposed_net_ref names an analytical allocation scope only. Returned future
    token addresses remain unregistered and confer no execution authority.
    """
    from cpn.rpnh.registry.resources import AttemptCounterAuthority, ExecutableNetAuthority, PetriContinuation, PetriLeaseClaim, PetriOverrideWarning, PetriTokenState, TypedMarkingSnapshot
    if proposed_net_ref is None:
        if not isinstance(executable, ExecutableNetAuthority):
            raise MarkingStateError('pure_typed_snapshot requires an ExecutableNetAuthority')
        net_ref = executable.net_ref
    else:
        if (executable is not None or not isinstance(proposed_net_ref, VersionRef)
                or proposed_net_ref.entity_type != 'net_instance/v1'):
            raise MarkingStateError('analytical allocation requires only an explicit proposed net ref')
        net_ref = proposed_net_ref
    if not isinstance(self._net.registry_net_ref, VersionRef) or self._net.registry_net_ref != net_ref:
        raise MarkingStateError('pure typed snapshot differs from its exact net scope')
    from cpn.rpnh.registry.normal_root_token_allocation import (
        NORMAL_ROOT_TOKEN_SCHEME, normal_root_token_ref,
    )
    if ordinary_token_ref_scheme is None:
        if allocation_firing_ref is not None:
            raise MarkingStateError('legacy token allocation cannot carry a firing selector')
    elif (ordinary_token_ref_scheme != NORMAL_ROOT_TOKEN_SCHEME
            or not isinstance(allocation_firing_ref, VersionRef)
            or allocation_firing_ref.entity_type != 'transition_firing/v1'):
        raise MarkingStateError('unknown or incomplete normal-root token allocation')

    def token_ref(token_id: int) -> VersionRef:
        if ordinary_token_ref_scheme is not None:
            return normal_root_token_ref(net_ref, allocation_firing_ref, token_id)
        material = str(net_ref.version_id)
        logical = TypedId('petri_token', uuid.uuid5(uuid.NAMESPACE_URL, f'd1-c:petri_token:{material}:{token_id}').hex)
        version = TypedId('petri_token_version', uuid.uuid5(uuid.NAMESPACE_URL, f'd1-c:petri_token_version:{material}:{token_id}').hex)
        return VersionRef('petri_token/v1', logical, version)
    with self._lock:
        if self._timed_active_claims:
            raise MarkingStateError('pure projection cannot carry active timed firing claims')
        candidate = self._validated_candidate_mapping()
        self._validated_state(candidate)
        states: list[PetriTokenState] = []
        live_tokens = tuple((token for token in sorted(self._tokens, key=lambda item: item.token_id) if token.epoch == self._epoch and token.consumed_by is None))
        for index, token in enumerate(live_tokens):
            continuation = None
            if token.continuation is not None:
                value = self._runtime_continuation(token.continuation, f'tokens[{index}].continuation')
                assert value is not None
                continuation = PetriContinuation(round=value['round'], source_ref=value['source_ref'])
            warning = None
            if token.override_warning is not None:
                value = self._override_warning_state(token.override_warning, f'tokens[{index}].override_warning', place=token.place)
                assert value is not None
                reviewer, author = value['gate']
                overridden = value['overridden_attempt']
                if author is None or overridden is None:
                    raise MarkingStateError('typed override warning requires exact author and attempt ids')
                warning = PetriOverrideWarning(reason=value['reason'], gate_transition_id=reviewer, gate_peer_transition_id=author, overridden_attempt=overridden, escalation_provenance=value['escalation_provenance'], gate_output_place=value['gate_output_place'])
            lease_claims = tuple((PetriLeaseClaim(lease_identity_ref=claim['lease_identity_ref'], expected_resource_ref=claim['expected_resource_ref'], access_mode=claim['access_mode'], staging_place=claim.get('staging_place')) for claim in self._runtime_lease_claims(token.lease_claims, f'tokens[{index}].lease_claims')))
            exact_ref = token.token_ref or token_ref(token.token_id)
            self._token_ref_payload(exact_ref, f'tokens[{index}].token_ref')
            states.append(PetriTokenState(token_ref=exact_ref, token_id=token.token_id, place=token.place, epoch=token.epoch, producer=token.producer, consumer=token.consumer, resource_ref=token.resource_ref, work_resource_ref=token.work_resource_ref, kind=token.kind, consumed_by=token.consumed_by, override_warning=warning, verdict=token.verdict, continuation=continuation, lease_identity_ref=token.lease_identity_ref, lease_claims=lease_claims))
        attempts = tuple((AttemptCounterAuthority(transition_id=transition_id, highest_issued=self._attempts[transition_id]) for transition_id in sorted(self._attempts)))
        snapshot = TypedMarkingSnapshot(epoch=self._epoch, next_token_id=self._next_id, attempts=attempts, tokens=tuple(states))
        validate_typed_marking_state(self._net, epoch=snapshot.epoch, next_token_id=snapshot.next_token_id, attempts=snapshot.attempts, tokens=snapshot.tokens, require_token_refs=True)
        return snapshot

def _typed_snapshot_with_active_timed_claims(self, executable: ExecutableNetAuthority, registry: RegistryTokenAllocator) -> TypedMarkingSnapshot:
    """Snapshot a timed successor while keeping claims as separate authority.

        Active timed claims are represented by ``successor_claims`` rather than by
        rewriting their held token records.  A state-only clone therefore clears
        ``consumed_by`` solely for tokens still named by active timed claims before
        invoking the unchanged public :meth:`typed_snapshot` contract.
        """
    with self._lock:
        candidate = self._validated_candidate_mapping()
        claims = self.active_timed_claims()
    snapshot_source = type(self)(self._net)
    snapshot_source._restore_candidate_mapping(candidate)
    with snapshot_source._lock:
        by_id = {token.token_id: token for token in snapshot_source._tokens}
        occupied: set[int] = set()
        for claim in claims:
            for item in (*claim.inputs, *claim.resource_tokens):
                token = by_id.get(item.token_id)
                if token is None or token.token_ref != item.token_ref or token.consumed_by != claim.transition_id or (token.token_id in occupied):
                    raise MarkingStateError('active timed claim differs from successor token state')
                occupied.add(token.token_id)
                token.consumed_by = None
    return snapshot_source.typed_snapshot(executable, registry)

def _adopt_snapshot_token_refs(self, snapshot: object) -> None:
    """Install deterministic provisional refs on an isolated proposal clone."""
    from cpn.rpnh.registry.resources import PetriTokenState, TypedMarkingSnapshot
    if not isinstance(snapshot, TypedMarkingSnapshot) or any((not isinstance(state, PetriTokenState) or not isinstance(state.token_ref, VersionRef) for state in snapshot.tokens)):
        raise MarkingStateError('timed successor snapshot lacks exact provisional token refs')
    with self._lock:
        refs = {state.token_id: state.token_ref for state in snapshot.tokens}
        if len(refs) != len(snapshot.tokens) or set(refs) != {token.token_id for token in self._tokens}:
            raise MarkingStateError('timed successor snapshot differs from cloned token ids')
        self._tokens = [replace(token, token_ref=refs[token.token_id]) for token in self._tokens]

def validate_typed_marking_state(net: 'MarkingNetStructure', *, epoch: int, next_token_id: int, attempts: tuple['AttemptCounterAuthority', ...], tokens: tuple['PetriTokenState', ...], require_token_refs: bool) -> None:
    """Purely validate one Registry DTO marking against ``net`` structure.

    Registry calls this same SSOT before publishing a checkpoint and while hydrating
    one. It performs no Registry lookup and mutates neither ``net`` nor caller DTOs.
    """
    from cpn.rpnh.registry.resources import AttemptCounterAuthority, PetriContinuation, PetriLeaseClaim, PetriOverrideWarning, PetriTokenState
    if not isinstance(net, MarkingNetStructure):
        raise MarkingStateError('typed marking structural validation requires a MarkingNetStructure')
    if type(require_token_refs) is not bool:
        raise MarkingStateError('require_token_refs must be a boolean')
    if not isinstance(attempts, tuple) or any((not isinstance(item, AttemptCounterAuthority) for item in attempts)):
        raise MarkingStateError('typed marking attempts must be AttemptCounterAuthority records')
    transition_ids = tuple((item.transition_id for item in attempts))
    if transition_ids != tuple(sorted(transition_ids)) or len(set(transition_ids)) != len(transition_ids):
        raise MarkingStateError('typed marking attempts must be transition-id sorted and unique')
    if not isinstance(tokens, tuple) or any((not isinstance(state, PetriTokenState) for state in tokens)):
        raise MarkingStateError('typed marking tokens must be PetriTokenState records')
    token_ids = tuple((state.token_id for state in tokens))
    if token_ids != tuple(sorted(token_ids)) or len(set(token_ids)) != len(token_ids):
        raise MarkingStateError('typed marking tokens must be token-id sorted and unique')
    marking = TeamNetMarking(net)
    runtime_tokens: list[Token] = []
    for index, state in enumerate(tokens):
        if require_token_refs and state.token_ref is None:
            raise MarkingStateError(f'tokens[{index}].token_ref lacks committed petri_token/v1 authority')
        if state.continuation is not None and (not isinstance(state.continuation, PetriContinuation)):
            raise MarkingStateError(f'tokens[{index}].continuation is not typed')
        if state.override_warning is not None and (not isinstance(state.override_warning, PetriOverrideWarning)):
            raise MarkingStateError(f'tokens[{index}].override_warning is not typed')
        if not isinstance(state.lease_claims, tuple) or any((not isinstance(claim, PetriLeaseClaim) for claim in state.lease_claims)):
            raise MarkingStateError(f'tokens[{index}].lease_claims are not typed')
        if any((claim.staging_place is not None and (not isinstance(claim.staging_place, str) or not claim.staging_place) for claim in state.lease_claims)):
            raise MarkingStateError(f'tokens[{index}].lease_claims have an invalid staging place')
        continuation = None if state.continuation is None else {'round': state.continuation.round, 'source_ref': state.continuation.source_ref}
        warning = None if state.override_warning is None else {'reason': state.override_warning.reason, 'gate': [state.override_warning.gate_transition_id, state.override_warning.gate_peer_transition_id], 'overridden_attempt': state.override_warning.overridden_attempt, 'escalation_provenance': state.override_warning.escalation_provenance, 'gate_output_place': state.override_warning.gate_output_place}
        lease_claims = tuple(({'lease_identity_ref': claim.lease_identity_ref, 'expected_resource_ref': claim.expected_resource_ref, 'access_mode': claim.access_mode, **({'staging_place': claim.staging_place} if claim.staging_place is not None else {})} for claim in state.lease_claims))
        runtime_tokens.append(Token(token_ref=state.token_ref, token_id=state.token_id, place=state.place, epoch=state.epoch, producer=state.producer, consumer=state.consumer, resource_ref=state.resource_ref, work_resource_ref=state.work_resource_ref, kind=state.kind, consumed_by=state.consumed_by, override_warning=warning, verdict=state.verdict, continuation=continuation, lease_identity_ref=state.lease_identity_ref, lease_claims=lease_claims))
    candidate = {'epoch': epoch, 'next_id': next_token_id, 'attempts': {item.transition_id: item.highest_issued for item in attempts}, 'tokens': [marking._token_payload(token) for token in runtime_tokens]}
    _validated_epoch, _validated_next, _validated_attempts, validated_tokens = marking._validated_state(candidate)
    marking._validate_runtime_token_refs(validated_tokens, 'typed_marking.tokens', require_token_ref=require_token_refs)
