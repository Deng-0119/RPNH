"""Private fixed Start/Claims closure; no public query, paging or new authority.

All helpers require the existing bounded live query owner. Descriptors and
validated summaries stay within its captured source/cut and die with the owner.
"""
from collections.abc import Mapping

from ..registry.identities import TypedId
from ..registry.schema_catalog import canonical_json
from ._origin_core_contract import INCLUDE_ORDER, START_FIELDS, INCLUDE_RECORD_FIELDS
from ._product_origin_core import (_CoreProof, _require, _ref, _key,
    _evaluate_origin_core, _owned_origin_evaluation)
from .registry_typed_readers import prepared_at, qualify
from .registry_read_contracts import RegistryReadSessionError


def _normalize_include(include):
    if include is None:
        return INCLUDE_ORDER
    if type(include) not in (tuple, list) or not include or any(type(item) is not str for item in include):
        raise RegistryReadSessionError('INVALID_QUERY')
    if any(item not in INCLUDE_ORDER for item in include):
        raise RegistryReadSessionError('UNSUPPORTED_RELATION')
    if len(include) > len(INCLUDE_ORDER) or len(include) != len(set(include)) or 'producer_execution' not in include:
        raise RegistryReadSessionError('INVALID_QUERY')
    return tuple(item for item in INCLUDE_ORDER if item in include)


def _verify_origin_includes(session, *, root, at_cut, include=None):
    """Private whole-closure proof and rows for public query delivery."""
    return _owned_origin_evaluation(session, root=root, at_cut=at_cut,
        include=_normalize_include(include), with_relations=True)


def _owner_verifier(context):
    _require(context is not None)
    session, snapshot = context._session, context.snapshot
    # Match the actual captured arrays, never create a fake session or discover
    # current state. This bounded list is the session's existing cut registry.
    for cut, captured in session._cuts.values():
        if (cut.source_id == snapshot.source_id and captured.objects is snapshot.objects
                and captured.events is snapshot.events and captured.head == snapshot.head):
            return _CoreProof(session, cut, session._sources[cut.source_id], context)
    raise RegistryReadSessionError('INVALID_CUT')


def _resource_ref(value, *, nullable=False):
    if value is None and nullable:
        return None
    _require(isinstance(value, Mapping) and set(value) == {'resource_id', 'resource_version_id'})
    TypedId.parse(value['resource_id'], expected='resource')
    TypedId.parse(value['resource_version_id'], expected='resource_version')
    return value


def _start_anchors(verifier, firing_ref):
    ctx = verifier.context
    firing_ref = _ref(firing_ref, 'transition_firing/v1')
    def validate():
        F = verifier.exact(firing_ref, 'transition_firing/v1')
        A = verifier.exact(F['firing_admission_ref'], 'firing_admission/v1')
        I = verifier.exact(A['invocation_ref'], 'invocation/v1')
        Iref, Lref, B = I['invocation_ref'], I['operation_execution_lease_ref'], F['operation_binding_ref']
        L = verifier.exact(Lref, 'operation_execution_lease/v1')
        binding = verifier.exact(B, 'operation_binding/v1')
        T, Q, N = (I[name] for name in ('task_ref', 'task_round_ref', 'net_instance_ref'))
        _require(T == verifier.task and F['task_ref'] == T and F['task_round_ref'] == Q
            and F['net_instance_ref'] == N and I['own_transition_firing_ref'] == firing_ref
            and I['operation_binding_ref'] == B and F['node_ref'] == I['own_node_ref']
            and A['transition_firing_ref'] == firing_ref and A['operation_execution_lease_ref'] == Lref
            and L['invocation_ref'] == Iref and A['claim_marking_delta_ref'] == F['claim_marking_delta_ref']
            and A['admission_marking_checkpoint_ref'] == F['admission_marking_checkpoint_ref']
                == I['admission_marking_checkpoint_ref'])
        _ref(I['own_node_ref'], 'node_declaration/v1'); _ref(I['principal_ref'], 'principal/v1')
        _ref(F['claim_marking_delta_ref'], 'marking_delta/v1')
        _ref(F['admission_marking_checkpoint_ref'], 'marking_checkpoint/v1')
        for ref, kind in ((T, 'task/v1'), (Q, 'task_round/v1'), (N, 'net_instance/v1')):
            verifier.exact(ref, kind)
        transaction = verifier.snapshot.objects[Iref['version_id']]['transaction_id']
        verifier.producer(Iref, None)
        for ref in (firing_ref, A['firing_admission_ref'], Lref):
            verifier.producer(ref, Iref['logical_id'], transaction=transaction)
        ctx.reserve(size=1024)
        return I, F, A, L, binding
    return ctx.cached('start-anchors', _key(firing_ref), 'start-anchor/v1', validate, category=None)


def _claimed_summary(verifier, firing):
    ctx = verifier.context
    values, versions = firing['claimed_input_refs'], firing['claimed_input_version_ids']
    _require(type(values) is list and type(versions) is list)
    def validate(items):
        # Whole-array C is reserved by semantic_array. All set/tuple/sort
        # buffers and the separate version-summary work are prepaid here.
        ctx.reserve('A', rows=len(versions), size=3 * ctx.bytes_bound(items)
            + 3 * ctx.bytes_bound(versions) + 256 * len(items) + 512)
        identities, version_ids = set(), set()
        for item in items:
            _ref(item, 'petri_token/v1')
            key = _key(item)
            _require(key not in identities)
            identities.add(key)
            version_ids.add(item['version_id'])
        for version in versions:
            TypedId.parse(version, expected='petri_token_version')
        _require(versions == sorted(version_ids))
        return items, frozenset(identities)
    return ctx.semantic_array(values, 'C', ('whole-firing-claims/v1', *_key(firing['transition_firing_ref'])), validate)


def _token_identity(verifier, token_ref, net):
    ctx = verifier.context
    def validate():
        body = verifier.exact(token_ref, 'petri_token/v1')
        _require(body['net_instance_ref'] == net)
        _resource_ref(body['resource_ref'], nullable=True)
        return body
    return ctx.cached('start-claim-token', (_key(token_ref), _key(net)), 'token-exact-net-resource/v1', validate)


def _input_resource(verifier, ref):
    ctx = verifier.context
    _resource_ref(ref)
    ctx.reserve(size=ctx.bytes_bound(ref) + 256)
    generic = {'entity_type': 'resource_version/v1', 'logical_id': ref['resource_id'],
        'version_id': ref['resource_version_id']}
    def validate():
        body = verifier.exact(generic, 'resource_version/v1')
        with ctx.scratch(size=ctx.bytes_bound(body) + ctx.bytes_bound(ref) + 1024):
            item = prepared_at(verifier.core, qualify(verifier.cut.source_id, ref), verifier.snapshot)
            verifier.core.object_store.validate_envelope(item)
        return body
    return ctx.cached('start-input-resource', _key(generic), 'metadata-publication-envelope/v1', validate)


def _binding_summary(verifier, binding):
    values = binding['input_binding_refs']
    _require(type(values) is list)
    ctx = verifier.context
    def validate(items):
        ctx.reserve(size=2 * ctx.bytes_bound(items) + 256 * len(items) + 256)
        identities = set()
        for item in items:
            # Static operation inputs have generic resource VersionRefs. The
            # other declaration lists are deliberately not expanded or read.
            _ref(item, 'resource_version/v1')
            key = _key(item)
            _require(key not in identities)
            identities.add(key)
        return frozenset(identities)
    return ctx.semantic_array(values, 'A', 'static-input-binding-refs/v1', validate)


def _validated_start_fields(context, firing_ref):
    """Exactly five raw derived fields, from one fully validated cached Start."""
    verifier = _owner_verifier(context)
    firing_ref = _ref(firing_ref, 'transition_firing/v1')
    def validate():
        I, F, A, L, B = _start_anchors(verifier, firing_ref)
        claims, claimed = _claimed_summary(verifier, F)
        Iref, Lref = I['invocation_ref'], L['operation_execution_lease_ref']
        context.reserve(size=256)
        candidates = []
        for event in context.events_labelled('operation_execution_started/v1'):
            touches = (event.aggregate_id == Lref['logical_id']
                or event.stream_id == 'operation-lease:' + Lref['logical_id'])
            if not touches:
                for name, ref in (('invocation_ref', Iref), ('transition_firing_ref', firing_ref),
                        ('operation_execution_lease_ref', Lref)):
                    value = event.payload.get(name)
                    if isinstance(value, Mapping) and value.get('logical_id') == ref['logical_id']:
                        touches = True
                        break
            if touches:
                context.reserve(size=256)
                candidates.append(event)
        def candidate(event):
            payload = event.payload
            # Reserve full logical array costs before schema validation can
            # perform uniqueness comparisons, and before building pair sets.
            arrays = tuple(payload.get(key) for key in ('input_binding_refs', 'input_resource_refs', 'claimed_input_refs'))
            _require(all(type(array) is list for array in arrays))
            bindings, resources, event_claims = arrays
            context.reserve('S', rows=max(len(bindings), len(resources)), size=4 * context.bytes_bound(payload)
                + 1024 * (len(bindings) + len(resources)) + 2048)
            context.reserve('A', rows=len(event_claims))
            with context.scratch(payload):
                verifier._event_envelope(event)
                _require(event.event_type == 'operation_execution_started/v1'
                    and event.aggregate_type == 'operation_execution_lease'
                    and event.aggregate_id == Lref['logical_id']
                    and event.stream_id == 'operation-lease:' + Lref['logical_id'])
                verifier.execution_event(event, I)
                for name, ref in (('invocation_ref', Iref), ('transition_firing_ref', firing_ref),
                        ('operation_execution_lease_ref', Lref), ('operation_binding_ref', B['operation_binding_ref'])):
                    _require(payload[name] == ref)
                for name, kind, nullable in (
                        ('operation_spec_ref', 'operation_spec/v1', False),
                        ('executable_transition_binding_ref', 'executable_transition_binding/v1', False),
                        ('principal_ref', 'principal/v1', False),
                        ('authority_decision_ref', 'user_authority_decision/v1', False),
                        ('agent_ref', 'agent/v1', True),
                        ('declaration_terminal_delivery_ref', 'resource_delivery/v1', True)):
                    _ref(payload[name], kind, nullable=nullable)
                for name in ('admission_registry_ordinal', 'admission_writer_fencing_epoch', 'admission_task_control_sequence'):
                    _require(type(payload[name]) is int and payload[name] >= 0)
                _require(event_claims == claims and len(bindings) == len(resources))
                static = _binding_summary(verifier, B)
                seen = set()
                for binding, resource in zip(bindings, resources):
                    _require(isinstance(binding, Mapping))
                    kind = binding.get('entity_type')
                    _require(kind in ('resource_version/v1', 'petri_token/v1'))
                    _ref(binding, kind)
                    identity = _key(binding)
                    _require(identity not in seen)
                    seen.add(identity)
                    _resource_ref(resource)
                    if kind == 'resource_version/v1':
                        _require(identity in static and binding['logical_id'] == resource['resource_id']
                            and binding['version_id'] == resource['resource_version_id'])
                    else:
                        _require(identity in claimed)
                        _token_identity(verifier, binding, I['net_instance_ref'])
                    _input_resource(verifier, resource)
                _require(event.ordinal < verifier.commit(str(event.transaction_id)).ordinal)
            return event
        validated = context.validate_events(candidates, ('start-candidate/v1', *_key(firing_ref)), candidate)
        _require(len(validated) == 1)
        event = validated[0]
        context.reserve(size=2048 + context.bytes_bound(event.payload['input_binding_refs'])
            + context.bytes_bound(event.payload['input_resource_refs']))
        return {'start_event_id': str(event.event_id), 'start_transaction_id': str(event.transaction_id),
            'start_ordinal': event.ordinal, 'start_input_binding_refs': event.payload['input_binding_refs'],
            'start_input_resource_refs': event.payload['input_resource_refs']}
    return context.cached('validated-start-fields', _key(firing_ref), 'paired-start/v1', validate, category=None)


def _verify_start_projection(context, firing_ref, projection, selected):
    """Check only the newly opt-in Start facts of an original HOST callback.

    Stored/legacy callback fields keep their existing HOST contract. Derived
    facts cannot be omitted, forged, or widened to unselected Start fields.
    """
    chosen = tuple(field for field in selected if field in START_FIELDS)
    if not chosen:
        return
    derived = _validated_start_fields(context, firing_ref)
    with context.scratch(size=context.bytes_bound(projection) + context.bytes_bound(derived) + 512):
        _require(isinstance(projection, Mapping)
            and set(projection).intersection(START_FIELDS) == set(chosen))
        for field in chosen:
            if field in START_FIELDS[3:]:
                # Both independently validated expected positions and returned
                # projection positions are traversed by canonical comparison.
                actual = projection[field]
                context.reserve('A', rows=len(derived[field])
                    + (len(actual) if type(actual) in (list, tuple) else 0))
            _require(canonical_json(projection[field]) == canonical_json(derived[field]))


def _validated_claims(verifier, firing):
    ctx = verifier.context
    refs, claimed = _claimed_summary(verifier, firing)
    I, F, A, L, B = _start_anchors(verifier, firing['transition_firing_ref'])
    # The anchor helper does not inspect Start events or claim classifications.
    fields = dict(dict(INCLUDE_RECORD_FIELDS)['claims'])
    verifier.public(F['transition_firing_ref'], 'transition_firing/v1', fields['transition_firing/v1'])
    delta_ref = F['claim_marking_delta_ref']
    delta = verifier.public(delta_ref, 'marking_delta/v1', fields['marking_delta/v1'])
    arrays = (delta['transition_firing_refs'], delta['operation_binding_refs'], delta['deposited_refs'])
    _require(all(type(value) is list for value in arrays))
    ctx.reserve('A', rows=sum(len(value) for value in arrays))
    _require(delta['net_instance_ref'] == I['net_instance_ref'] and delta['phase'] == 'claim'
        and delta['transition_firing_refs'] == [F['transition_firing_ref']]
        and delta['operation_binding_refs'] == [B['operation_binding_ref']] and delta['deposited_refs'] == [])
    verifier.producer(delta_ref, I['invocation_ref']['logical_id'],
        transaction=verifier.snapshot.objects[A['firing_admission_ref']['version_id']]['transaction_id'])
    consumed = delta['consumed_refs']
    _require(type(consumed) is list)
    def validate(values):
        ctx.reserve(size=2 * ctx.bytes_bound(values) + 256 * len(values) + 256)
        identities = set()
        for value in values:
            _ref(value, 'petri_token/v1')
            identity = _key(value)
            _require(identity not in identities and identity in claimed)
            identities.add(identity)
        return frozenset(identities)
    consumed_ids = ctx.semantic_array(consumed, 'C', ('claim-consumed-subset/v1', *_key(delta_ref)), validate)
    ctx.reserve(size=ctx.bytes_bound(refs) + 256 * len(refs) + 256)
    tokens = []
    for ref in refs:
        token = _token_identity(verifier, ref, I['net_instance_ref'])
        verifier.public(ref, 'petri_token/v1', fields['petri_token/v1'])
        tokens.append(token)
    return tokens, consumed_ids, delta_ref


def _evaluate_origin_includes(verifier, root, resource_root, include):
    """Non-owning complete evaluator; future pages must use this live owner."""
    proof = _evaluate_origin_core(verifier, root, resource_root)
    ctx, source = verifier.context, verifier.cut.source_id
    firing_ref = proof['transition_firing_ref']['ref']
    firing = verifier.exact(firing_ref, 'transition_firing/v1')
    if 'start_inputs' in include or 'claims' in include:
        _claimed_summary(verifier, firing)
    start = None
    if 'start_inputs' in include:
        start = _validated_start_fields(ctx, firing_ref)
        # A configured HOST catalog stays authoritative for public access, but
        # cannot replace validated facts with forged/missing/extra projection.
        verifier.public(firing_ref, 'transition_firing/v1', START_FIELDS)
    claims = _validated_claims(verifier, firing) if 'claims' in include else None
    # All closure validation is complete before any output row is constructed.
    # Conservative bounds include qualified refs, maps, sorted copies and the
    # full private result serialization, without imposing a future page cap.
    count = (0 if start is None else len(start['start_input_binding_refs'])) + (0 if claims is None else len(claims[0]))
    ctx.reserve(size=3 * ctx.bytes_bound(start) + (0 if claims is None else
        3 * sum(ctx.bytes_bound(token['petri_token_ref']) + ctx.bytes_bound(token['resource_ref']) for token in claims[0]))
        + count * (8192 + 6 * ctx.bytes_bound(source)) + 2048)
    rows = []
    if start is not None:
        for position, (binding, resource) in enumerate(zip(start['start_input_binding_refs'], start['start_input_resource_refs'])):
            rows.append({'role': 'start_input', 'position': position, 'input_binding_ref': qualify(source, binding),
                'resource_ref': qualify(source, resource), 'evidence': {key: start[key] for key in START_FIELDS[:3]},
                'verification': {'binding_identity': 'exact_at_cut', 'resource_identity': 'exact_at_cut',
                    'target_record': 'not_requested', 'material': 'not_read'}})
    if claims is not None:
        tokens, consumed, delta = claims
        for token in sorted(tokens, key=lambda token: _key(token['petri_token_ref'])):
            rows.append({'role': 'claim', 'token_ref': qualify(source, token['petri_token_ref']),
                'resource_ref': None if token['resource_ref'] is None else qualify(source, token['resource_ref']),
                'classification': 'consumed_claim' if _key(token['petri_token_ref']) in consumed else 'non_consuming_claim',
                'evidence': {'transition_firing_ref': qualify(source, firing_ref), 'claim_marking_delta_ref': qualify(source, delta)},
                'verification': {'token_record': 'verified_at_cut', 'resource_target': 'not_requested', 'material': 'not_read'}})
    return {'root_proof': proof, 'rows': rows}
