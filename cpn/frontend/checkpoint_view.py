"""Opt-in display-only projection of an exact saved checkpoint.

No current lifecycle reconstruction, runtime authority, arbitrary content endpoint,
HOST loading or writer hooks. All historical inputs use a fixed canonical cut.
The current stable observation is used only as a reachability/capture anchor.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from cpn.rpnh.executable_net import _load_compiled_net_offline
from cpn.rpnh.inspection import project_compiled_net, project_compiled_boundaries
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import PreparedObject
from cpn.rpnh.registry.event_store import fact_event_envelope
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.module_binding_authority import validate_module_bindings
from cpn.rpnh.registry.schema_catalog import canonical_json

SCHEMA = 'rpnh/checkpoint_view/v1'


def reference(value, kind):
    if (type(value) is not dict or set(value) != {'entity_type', 'logical_id', 'version_id'}
            or value['entity_type'] != kind or any(type(v) is not str for v in value.values())):
        raise ValueError('selector requires an exact canonical reference')
    parsed = _version_from_payload(value)
    expected = {'net_instance/v1': ('net_instance', 'net_instance_version'),
                'marking_checkpoint/v1': ('marking_checkpoint', 'marking_checkpoint_version')}.get(kind)
    if expected and (parsed.entity_id.kind, parsed.version_id.kind) != expected:
        raise ValueError('selector reference has incorrect typed identities')
    return dict(value)


def selector(net_ref, checkpoint_ref, cut):
    if type(cut) is not int or cut < 1:
        raise ValueError('cut must be a positive complete checkpoint commit ordinal')
    return {'net_ref': reference(net_ref, 'net_instance/v1'),
            'checkpoint_ref': reference(checkpoint_ref, 'marking_checkpoint/v1'), 'cut': cut}


def _resource_ref(value):
    if type(value) is not dict or set(value) != {'resource_id', 'resource_version_id'}:
        raise ValueError('invalid exact resource pair')
    return reference({'entity_type': 'resource_version/v1', 'logical_id': value['resource_id'],
                      'version_id': value['resource_version_id']}, 'resource_version/v1')


class TokenResourceInvalid(ValueError):
    """The explicit target is malformed or is not this checkpoint occurrence."""


class TokenResourceStale(RuntimeError):
    """The requested observation is no longer the captured observation."""


class TokenResourceAccessChanged(RuntimeError):
    """The provider's original binding check no longer permits disclosure."""


def _safe_integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 9007199254740991


def _target_id(value, kind):
    if type(value) is not str or re.fullmatch(kind + r':[a-f0-9]{32}', value) is None:
        raise TokenResourceInvalid('invalid_target')
    return value


def token_resource_target(value):
    """No inferred IDs, work refs, paths, or latest-capture defaults."""
    if type(value) is not dict or set(value) != {'token_ref', 'resource_ref', 'expected_task_id', 'expected_capture'}:
        raise TokenResourceInvalid('invalid_target')
    try:
        token = reference(value['token_ref'], 'petri_token/v1')
        _target_id(token['logical_id'], 'petri_token')
        _target_id(token['version_id'], 'petri_token_version')
        pair = value['resource_ref']
        _resource_ref(pair)
        _target_id(pair['resource_id'], 'resource')
        _target_id(pair['resource_version_id'], 'resource_version')
        _target_id(value['expected_task_id'], 'task')
        capture = value['expected_capture']
        if (type(capture) is not dict or set(capture) != {'head_ordinal', 'writer_fencing_epoch'}
                or not _safe_integer(capture['head_ordinal'], 1) or not _safe_integer(capture['writer_fencing_epoch'])):
            raise ValueError('capture')
    except (ValueError, TypeError, KeyError) as exc:
        raise TokenResourceInvalid('invalid_target') from exc
    return {'token_ref': token, 'resource_ref': dict(pair), 'expected_task_id': value['expected_task_id'],
            'expected_capture': dict(capture)}


def _token_resource_metadata(reads, tokens, identity, selected, capture, target):
    matches = [t for t in tokens if t['token_ref'] == target['token_ref']]
    if len(matches) != 1 or matches[0]['resource_ref'] != target['resource_ref']:
        raise TokenResourceInvalid('invalid_target')
    token = matches[0]
    # _tokens has already populated this exact cache through canonical-at-C,
    # publication/transaction, envelope and task-ownership checks. Never read anew.
    row, metadata = reads.cache[canonical_json(_resource_ref(target['resource_ref']))]
    publication = next(e for e in reads.events if str(e.event_id) == row['published_event_id'])
    commit = reads.transaction_commit_event(publication.transaction_id)
    if (row['object_type'] != 'resource_version/v1'
            or row['logical_id'] != target['resource_ref']['resource_id']
            or row['version_id'] != target['resource_ref']['resource_version_id']
            or metadata['resource_id'] != row['logical_id'] or metadata['resource_version_id'] != row['version_id']
            or metadata['task_ref'] != identity['task_ref'] or row['size'] != metadata['size']
            or row['media_type'] != metadata['media_type']
            or str(publication.transaction_id) not in reads.verified_transactions
            or not 1 <= publication.ordinal <= commit.ordinal <= selected['cut']):
        raise ValueError('inconsistent registered metadata')
    return {'schema_version': 'rpnh/token_resource_metadata/v1',
        'scope': {**identity, **selected, 'capture': dict(capture),
                  'token_ref': target['token_ref'], 'resource_ref': target['resource_ref']},
        'occurrence': {k: token[k] for k in ('token_ref', 'resource_ref', 'place', 'active_in_checkpoint')},
        'registered_metadata': {'byte_size': row['size'], 'media_type': row['media_type'],
                                'content_schema_ref': metadata.get('content_schema_ref')},
        'registration': {'published_event_id': row['published_event_id'],
            'publication_recorded_ordinal': publication.ordinal,
            'publication_transaction_commit_ordinal': commit.ordinal},
        'coverage': {'status': 'complete', 'scope': 'one_checkpoint_token_resource'},
        'verification': {'metadata_scope': 'canonical_at_selected_checkpoint',
            'body_read_by_metadata_projection': False, 'content_validation': 'not_performed',
            'actual_verified_byte_size': None}}


def validate_token_resource_response(payload, target):
    """Strict opt-in response boundary, including custom provider implementations."""
    value = payload.get('token_resource_metadata')
    def exact(value, fields):
        if type(value) is not dict or set(value) != set(fields.split()):
            raise ValueError('invalid metadata response')
    exact(value, 'schema_version scope occurrence registered_metadata registration coverage verification')
    if value['schema_version'] != 'rpnh/token_resource_metadata/v1':
        raise ValueError('invalid metadata response')
    scope, occurrence = value['scope'], value['occurrence']
    exact(scope, 'task_ref run_ref net_ref checkpoint_ref cut capture token_ref resource_ref')
    exact(occurrence, 'token_ref resource_ref place active_in_checkpoint')
    for key, kind, stem in [('task_ref', 'task/v1', 'task'), ('run_ref', 'native_run_identity/v1', 'run')]:
        ref = reference(scope[key], kind)
        _target_id(ref['logical_id'], stem); _target_id(ref['version_id'], stem + '_version')
    if (any(scope[k] != payload['selector'][k] for k in ('net_ref', 'checkpoint_ref', 'cut'))
            or not _safe_integer(scope['cut'], 1) or scope['cut'] > target['expected_capture']['head_ordinal']
            or scope['capture'] != target['expected_capture'] or payload['capture'] != scope['capture']
            or scope['task_ref']['logical_id'] != target['expected_task_id']
            or any(scope[k] != target[k] or occurrence[k] != target[k] for k in ('token_ref', 'resource_ref'))):
        raise ValueError('invalid metadata response scope')
    # Only the opt-in extension is checked here. Cross-check the exact frame
    # being disclosed rather than relying on a later client normalizer to reject it.
    frame = payload['frame']
    if (type(frame) is not dict
            or any(type(frame.get(key)) is not dict for key in ('source', 'net', 'position'))):
        raise ValueError('invalid metadata frame shape')
    net = frame['net']
    if (type(net.get('source')) is not dict or type(net.get('marking')) is not dict
            or type(net.get('nodes')) is not list):
        raise ValueError('invalid metadata net shape')
    for node in net['nodes']:
        if (type(node) is not dict or type(node.get('tokens', [])) is not list
                or any(type(token) is not dict for token in node.get('tokens', []))):
            raise ValueError('invalid metadata token container')
    source, nested, position = frame['source'], net['source'], frame['position']
    for captured in (scope['capture'], payload['capture']):
        exact(captured, 'head_ordinal writer_fencing_epoch')
        if (not _safe_integer(captured['head_ordinal'], 1)
                or not _safe_integer(captured['writer_fencing_epoch'])):
            raise ValueError('invalid metadata capture')
    if (frame.get('schema_version') != 'rpnh/dashboard/v1'
            or frame['net'].get('schema_version') != 'rpnh/net_view/v1'
            or position.get('mode') != 'history'
            or not _safe_integer(position['cursor'], 1) or position['cursor'] != scope['cut']
            or not _safe_integer(position['latest_head'], 1) or position['latest_head'] != scope['capture']['head_ordinal']
            or frame['net']['marking']['checkpoint_ref'] != scope['checkpoint_ref']
            or type(source.get('run_dir')) is not str or nested.get('run_dir') != source['run_dir']):
        raise ValueError('invalid metadata frame boundary')
    for origin in (source, nested):
        if (origin.get('mode') != 'registry_current' or origin.get('task_id') != target['expected_task_id']
                or origin.get('net_ref') != scope['net_ref']
                or not _safe_integer(origin.get('verified_head_ordinal'), 1) or origin['verified_head_ordinal'] != scope['cut']
                or not _safe_integer(origin.get('writer_fencing_epoch'))
                or origin['writer_fencing_epoch'] != scope['capture']['writer_fencing_epoch']):
            raise ValueError('invalid metadata frame source')
    matching = [(n, t) for n in frame['net']['nodes'] for t in n.get('tokens', [])
                if t['token_ref'] == target['token_ref']]
    if (len(matching) != 1 or any(occurrence[k] != matching[0][1][k] for k in occurrence)
            or matching[0][0].get('kind') != 'place' or matching[0][0].get('id') != occurrence['place']
            or type(matching[0][1]['active_in_checkpoint']) is not bool
            or type(occurrence['place']) is not str or type(occurrence['active_in_checkpoint']) is not bool):
        raise ValueError('invalid metadata occurrence')
    registered, registration = value['registered_metadata'], value['registration']
    exact(registered, 'byte_size media_type content_schema_ref')
    exact(registration, 'published_event_id publication_recorded_ordinal publication_transaction_commit_ordinal')
    if (not _safe_integer(registered['byte_size']) or type(registered['media_type']) is not str
            or (registered['content_schema_ref'] is not None and type(registered['content_schema_ref']) is not str)
            or not _safe_integer(registration['publication_recorded_ordinal'], 1)
            or not _safe_integer(registration['publication_transaction_commit_ordinal'], 1)
            or not registration['publication_recorded_ordinal'] <= registration['publication_transaction_commit_ordinal'] <= scope['cut']):
        raise ValueError('invalid metadata registration')
    _target_id(registration['published_event_id'], 'event')
    if (value['coverage'] != {'status': 'complete', 'scope': 'one_checkpoint_token_resource'}
            or value['verification'] != {'metadata_scope': 'canonical_at_selected_checkpoint',
                'body_read_by_metadata_projection': False, 'content_validation': 'not_performed',
                'actual_verified_byte_size': None}):
        raise ValueError('invalid metadata coverage or verification')


class _BoundedReads:
    """A private fixed-cut reader, not an externally supplied read callback."""
    def __init__(self, core, cut):
        self.core, self.cut = core, cut
        self.view = core.event_store.canonical_view(through_ordinal=cut)
        self.events = core.event_store.canonical_events(through_ordinal=cut)
        self.commits = {}
        for event in self.events:
            if event.event_type == 'transaction_committed/v1':
                key = str(event.transaction_id)
                if key in self.commits:
                    raise ValueError('ambiguous canonical transaction commit')
                self.commits[key] = event
        self.cache = {}
        self.verified_transactions = set()
        self.kernel = _ResourceServiceKernel(core)
        self.schema_reads = {}

    def transaction_commit_event(self, transaction_id):
        value = self.commits.get(str(transaction_id))
        if value is None:
            raise ValueError('material is not visible in a complete transaction at cut')
        return value

    def verify_transaction(self, transaction_id):
        """Integrity of one complete transaction, never a later stream scan."""
        key = str(transaction_id)
        if key in self.verified_transactions:
            return
        terminal = self.transaction_commit_event(key)
        store = self.core.event_store
        with store.connect() as db:
            transaction = db.execute('SELECT * FROM transactions WHERE transaction_id=?', (key,)).fetchone()
            outbox = db.execute('SELECT * FROM outbox WHERE transaction_id=?', (key,)).fetchone()
            rows = db.execute('SELECT * FROM events WHERE transaction_id=? AND ordinal<=? ORDER BY ordinal',
                              (key, self.cut)).fetchall()
        events = [store._row_to_envelope(row) for row in rows]
        if (transaction is None or transaction['status'] != 'committed' or outbox is None
                or not events or events[-1].event_id != terminal.event_id
                or json.loads(outbox['event_ids_json']) != [str(e.event_id) for e in events]
                or transaction['task_id'] != str(terminal.task_id) or outbox['task_id'] != str(terminal.task_id)
                or int(transaction['writer_epoch']) != terminal.writer_fencing_epoch
                or int(outbox['writer_epoch']) != terminal.writer_fencing_epoch
                or sum(e.event_type == 'transaction_committed/v1' for e in events) != 1
                or any(e.event_type == 'transaction_aborted/v1' for e in events)):
            raise ValueError('historical terminal/outbox/transaction evidence differs')
        for event in events:
            if (event.transaction_id != terminal.transaction_id or event.task_id != terminal.task_id
                    or event.writer_fencing_epoch != terminal.writer_fencing_epoch):
                raise ValueError('historical event transaction envelope differs')
            self.core.catalog.validate_fact_envelope(fact_event_envelope(event))
            self.core.catalog.validate_event_payload(event.event_type, event.payload, criticality=event.criticality)
        self.verified_transactions.add(key)

    @staticmethod
    def prepared(row, metadata):
        return PreparedObject(object_type=row['object_type'], logical_id=TypedId.parse(row['logical_id']),
            version_id=TypedId.parse(row['version_id']), size=row['size'], media_type=row['media_type'],
            schema_ref=row['schema_ref'], storage_locator=row['storage_locator'], metadata=metadata,
            producer_invocation_id=TypedId.parse(row['producer_invocation_id']) if row['producer_invocation_id'] else None)

    def row(self, ref, kind):
        ref = reference(ref, kind)
        key = canonical_json(ref)
        if key not in self.cache:
            row = self.core.event_store.object_row_for_view(self.view, ref['version_id'])
            if (row is None or row['object_type'] != kind or row['logical_id'] != ref['logical_id']
                    or row['version_id'] != ref['version_id']):
                raise ValueError('exact historical object is not canonical at cut')
            self.verify_transaction(row['transaction_id'])
            value = json.loads(row['metadata_json'])
            publications = [e for e in self.events if str(e.event_id) == row['published_event_id']]
            if len(publications) != 1:
                raise ValueError('historical object has no canonical publication envelope')
            publication = publications[0]
            if (publication.event_type != 'object_version_published/v1'
                    or str(publication.transaction_id) != row['transaction_id']
                    or (str(publication.producer_invocation_id) if publication.producer_invocation_id else None) != row['producer_invocation_id']
                    or canonical_json(publication.payload.get('metadata')) != canonical_json(value)
                    or any(publication.payload.get(k) != row[k] for k in
                           ('object_type', 'logical_id', 'version_id', 'size', 'media_type', 'schema_ref', 'storage_locator'))):
                raise ValueError('historical object publication differs from exact row')
            self.core.catalog.validate_instance(kind, category='object', instance=value)
            # Object schemas have either one self reference or scalar self IDs.
            stem = kind.split('/')[0]
            self_key = {'node_declaration': 'node_ref', 'user_authority_decision': 'decision_ref'}.get(stem, stem + '_ref')
            if self_key in value and value[self_key] != ref:
                raise ValueError('historical object self reference differs')
            scalar_stem = {'native_run_identity': 'run', 'node_declaration': 'node', 'resource_version': 'resource', 'plan_version': 'plan', 'user_authority_decision': 'decision'}.get(stem, stem)
            for suffix, expected in (('_id', ref['logical_id']), ('_version_id', ref['version_id'])):
                if scalar_stem + suffix in value and value[scalar_stem + suffix] != expected:
                    raise ValueError('historical object scalar self identity differs')
            prepared = self.prepared(row, value)
            self.core.object_store.validate_envelope(prepared)
            if kind == 'resource_version/v1':
                # Existing strict resource/provenance reader is explicitly cut-gated.
                checked = self.kernel._canonical_prepared(ResourceVersionRef(prepared.logical_id, prepared.version_id),
                                                           through_ordinal=self.cut)
                if checked != prepared:
                    raise ValueError('bounded resource read disagrees with publication')
            elif canonical_json(json.loads(self.core.object_store.read_registered(prepared))) != canonical_json(value):
                raise ValueError('historical descriptor bytes differ from registered metadata')
            self.cache[key] = row, value
        return self.cache[key][0]

    def metadata(self, ref, kind):
        self.row(ref, kind)
        return self.cache[canonical_json(ref)][1]

    def checkpoint(self, ref):
        value = self.metadata(ref, 'marking_checkpoint/v1')
        if value.get('marking_checkpoint_ref') != ref or value.get('settled') is not True:
            raise ValueError('selected checkpoint lacks exact settled identity')
        ordinal = self.core.event_store.canonical_object_publication_ordinal(
            _version_from_payload(ref).version_id, through_ordinal=self.cut)
        events = [e for e in self.events if e.ordinal == ordinal]
        if len(events) != 1:
            raise ValueError('checkpoint publication is unavailable at cut')
        commit = self.transaction_commit_event(events[0].transaction_id)
        self.verify_transaction(events[0].transaction_id)
        witnesses = [e for e in self.events if e.event_type == 'marking_checkpoint_committed/v1'
                     and e.payload.get('checkpoint_ref') == ref]
        if (len(witnesses) != 1 or self.transaction_commit_event(witnesses[0].transaction_id).ordinal != commit.ordinal
                or str(witnesses[0].task_id) != str(self.core.task_id)):
            raise ValueError('checkpoint has no exact complete publication witness')
        witness = witnesses[0]
        self.verify_transaction(witness.transaction_id)
        if (str(witness.net_instance_id) != value['net_instance_ref']['logical_id']
                or any(witness.payload.get(field) != value.get(field) for field in
                       ('net_instance_ref', 'team_design_root_ref', 'previous_checkpoint_ref', 'settlement_delta_ref',
                        'transition_firing_refs', 'workspace_revision_refs', 'settled'))):
            raise ValueError('checkpoint publication witness differs from descriptor')
        return value, commit

    def declaration(self, resource_ref, task_ref):
        # Business resource bodies are never read. This is the selected compiler wire.
        ref = _resource_ref(resource_ref)
        row = self.row(ref, 'resource_version/v1')
        meta = self.metadata(ref, 'resource_version/v1')
        if meta.get('task_ref') != task_ref or meta.get('content_schema_ref') != 'rpnh/executable_net/v1':
            raise ValueError('declaration differs from selected task/compiler wire')
        prepared = self.prepared(row, meta)
        return _load_compiled_net_offline(json.loads(self.core.object_store.read_registered(prepared)))


    def port_schema(self, ref, root, schema_id, expected):
        """Only an actual selected-wire port schema, never an arbitrary body."""
        _resources(self, root, [ref])
        if type(expected) is not dict or expected.get('$id') != schema_id:
            raise ValueError('selected wire schema has no matching exact schema ID')
        row = self.row(ref, 'resource_version/v1')
        meta = self.metadata(ref, 'resource_version/v1')
        prepared = self.prepared(row, meta)
        if prepared.media_type != 'application/schema+json':
            raise ValueError('selected port schema has the wrong registered content type')
        key = canonical_json(ref)
        if key not in self.schema_reads:
            payload = self.core.object_store.read_registered(prepared)
            if not payload:
                raise ValueError('selected port schema body is empty')
            def nonfinite(value):
                raise ValueError('selected port schema contains non-finite JSON')
            document = json.loads(payload, parse_constant=nonfinite)
            if type(document) is not dict or document.get('$id') != schema_id:
                raise ValueError('selected port schema body identifies another schema')
            self.schema_reads[key] = document
        # JSON serialization keeps booleans/numbers and arrays/objects distinct.
        if canonical_json(self.schema_reads[key]) != canonical_json(expected):
            raise ValueError('selected port schema body differs from compiler wire')


def _identity(reads, net_ref, checkpoint, expected_identity=None):
    net = reads.metadata(net_ref, 'net_instance/v1')
    root_ref = net['team_design_root_ref']
    root = reads.metadata(root_ref, 'team_design_root/v1')
    reads.metadata(root['owner_principal_ref'], 'principal/v1')
    task_ref = reference(root['task_ref'], 'task/v1')
    reads.metadata(task_ref, 'task/v1')
    run_ref = reference(root['run_ref'], 'native_run_identity/v1')
    run = reads.metadata(run_ref, 'native_run_identity/v1')
    round_value = reads.metadata(root['task_round_ref'], 'task_round/v1')
    if (net.get('net_instance_ref') != net_ref or root.get('team_design_root_ref') != root_ref
            or checkpoint['net_instance_ref'] != net_ref or checkpoint['team_design_root_ref'] != root_ref
            or task_ref['logical_id'] != str(reads.core.task_id) or run['task_ref'] != task_ref
            or round_value['task_id'] != task_ref['logical_id']
            or round_value['task_branch_ref'] != run['task_branch_ref']):
        raise ValueError('checkpoint/net/root/task/run identity differs')
    identity = {'task_ref': task_ref, 'run_ref': run_ref}
    if expected_identity is not None and identity != expected_identity:
        raise ValueError('checkpoint predecessor escaped the selected task/run')
    for field in ('llm_macro_net_ref', 'node_refs', 'operation_binding_refs', 'output_binding_refs',
                  'executable_transition_binding_refs', 'task_round_ref', 'team_net_declaration_resource_ref'):
        if net.get(field) != root.get(field):
            raise ValueError('net and selected root closure disagree: ' + field)
    return net, root, identity



def _capture_anchor(provider, *, resource_target=False):
    """Capture only current canonical metadata; never invoke the legacy loader."""
    try:
        provider._bound()
    except (ValueError, RuntimeError, OSError) as exc:
        if resource_target:
            raise TokenResourceAccessChanged('access_changed') from exc
        raise
    core = provider._open()
    if str(core.task_id) != provider.task_id:
        if resource_target:
            raise TokenResourceAccessChanged('access_changed')
        raise ValueError('bound task identity changed')
    head, epoch = core.event_store.max_ordinal(), core.event_store.writer_epoch
    reads = _BoundedReads(core, head)
    adoption = _adoption(reads, None)
    net_ref = adoption['current_net_ref']
    if net_ref is None:
        raise ValueError('current canonical adoption anchor is unavailable')
    events = [e for e in reads.events if e.event_type == 'marking_checkpoint_committed/v1'
              and str(e.task_id) == str(core.task_id) and e.payload.get('net_instance_ref') == net_ref]
    if not events:
        raise ValueError('current canonical checkpoint anchor is unavailable')
    previous = None
    for event in events:
        checkpoint, _ = reads.checkpoint(event.payload['checkpoint_ref'])
        if checkpoint['net_instance_ref'] != net_ref or checkpoint.get('previous_checkpoint_ref') != event.payload.get('previous_checkpoint_ref'):
            raise ValueError('checkpoint anchor event identity differs')
        if previous is not None and checkpoint.get('previous_checkpoint_ref') != previous:
            raise ValueError('current checkpoint events are not one predecessor chain')
        previous = event.payload['checkpoint_ref']
    checkpoint, _ = reads.checkpoint(previous)
    _, _, identity = _identity(reads, net_ref, checkpoint)
    _inventory(reads, selector(net_ref, previous, reads.checkpoint(previous)[1].ordinal), checkpoint, identity)
    observation = {'source': {'mode': 'registry_current', 'run_dir': str(provider.run_dir),
        'task_id': str(core.task_id), 'net_ref': net_ref, 'verified_head_ordinal': head,
        'writer_fencing_epoch': epoch}, 'net': {'marking': {'checkpoint_ref': previous}}}
    try:
        provider._stable(core, observation)
    except RuntimeError as exc:
        if resource_target:
            raise TokenResourceStale('stale_observation') from exc
        raise
    return core, observation


def _chain(provider, core, observation, requested):
    reads = _BoundedReads(core, observation['source']['verified_head_ordinal'])
    ref = observation['net']['marking']['checkpoint_ref']
    # Current observation refs can be dataclass-shaped; normalize only the anchor.
    from .dashboard import exact
    ref = exact(ref)
    seen, items, identity = set(), [], None
    while ref is not None and len(items) < provider.max_checkpoints:
        ref = reference(ref, 'marking_checkpoint/v1')
        key = canonical_json(ref)
        if key in seen:
            raise ValueError('checkpoint predecessor cycle')
        seen.add(key)
        checkpoint, commit = reads.checkpoint(ref)
        net_ref = reference(checkpoint['net_instance_ref'], 'net_instance/v1')
        _, _, identity = _identity(reads, net_ref, checkpoint, identity)
        if items and commit.ordinal >= items[-1]['selector']['cut']:
            raise ValueError('checkpoint predecessor commits are not strictly ordered')
        items.append({'selector': selector(net_ref, ref, commit.ordinal), 'at': commit.recorded_at})
        ref = checkpoint.get('previous_checkpoint_ref')
    end = 'reader_limit' if ref is not None else 'initial_checkpoint'
    index = next((i for i, item in enumerate(items) if item['selector'] == requested), None)
    if index is None:
        raise ValueError('selector is not in the captured predecessor chain' +
                         ('; coverage incomplete: reader_limit' if ref is not None else ''))
    previous = next((item['selector'] for item in items[index + 1:]
                     if item['selector']['net_ref'] != requested['net_ref']), None)
    navigation = {'previous_net_segment': previous,
                  'coverage': 'partial' if end == 'reader_limit' else 'complete',
                  'end_reason': 'net_version_boundary' if previous else end,
                  'max_chain': provider.max_checkpoints, 'loaded_checkpoints': len(items)}
    return items[index], navigation, identity



def _ref_keys(values, kind=None):
    if type(values) is not list:
        raise ValueError('selected material inventory must be an exact-ref array')
    keys = [canonical_json(reference(value, kind or value['entity_type'])) for value in values]
    if len(keys) != len(set(keys)):
        raise ValueError('selected material inventory contains duplicate exact refs')
    return set(keys)


def _member(reads, root, ref, kind, collection):
    reference(ref, kind)
    if canonical_json(ref) not in _ref_keys(root[collection]):
        raise ValueError('selected material is outside root ' + collection)
    return reads.metadata(ref, kind)


def _resources(reads, root, values):
    keys = _ref_keys(values, 'resource_version/v1')
    for ref in values:
        value = _member(reads, root, ref, 'resource_version/v1', 'resource_refs')
        if value.get('task_ref') != root['task_ref']:
            raise ValueError('selected resource/schema belongs to another task')
    return keys



def _operation_ports(reads, root, compiled, item, spec, schema_refs):
    """Compare the persisted operation ABI with the fixed compiler data only."""
    ports = {port.name: port for port in compiled.ports}
    operation, host = item.declaration, item.executor_declaration
    if (spec['operation_id'] != item.operation_id or spec['executor_key'] != item.executor_key
            or canonical_json(spec['implementation_identity']) != canonical_json(host['identity'])
            or canonical_json(spec['implementation_contracts']) != canonical_json(host['contracts'])
            or spec['allowed_tool_ids'] != sorted(operation.tools)
            or spec['llm_prompt_port_id'] != (ports[operation.request_port].port_id if operation.request_port else None)):
        raise ValueError('registered operation ABI differs from persisted compiler declaration')
    direction_refs = {}
    for direction, names in (('input', operation.inputs), ('output', operation.outputs)):
        declared = spec[direction + '_ports']
        if len(declared) != len(names):
            raise ValueError('registered port inventory differs from compiled operation')
        expected, refs = [], []
        for name, actual in zip(names, declared):
            port = ports[name]
            schema_ref = reference(actual['schema_ref'], 'resource_version/v1')
            _resources(reads, root, [schema_ref])
            reads.port_schema(schema_ref, root, port.schema, compiled.registrations['schema'][port.schema]['schema'])
            if port.schema in schema_refs and schema_refs[port.schema] != schema_ref:
                raise ValueError('one compiled schema resolves to different exact schema resources')
            schema_refs[port.schema] = schema_ref
            refs.append(schema_ref)
            if direction == 'input':
                minimum, maximum = port.minimum, port.maximum
            else:
                products = [next((p for p in outcome.products if p.port == name), None)
                            for outcome in operation.outcomes]
                minimum = min(p.minimum if p else 0 for p in products)
                maximum = max(p.maximum if p else 0 for p in products)
            expected.append({'port_id': port.port_id, 'place': port.place, 'schema_ref': schema_ref,
                'content_schema_ref': {'resource_id': schema_ref['logical_id'], 'resource_version_id': schema_ref['version_id']},
                'cardinality': {'minimum': minimum, 'maximum': maximum}, 'lease_identity_ref': None})
        if canonical_json(declared) != canonical_json(expected):
            raise ValueError('registered port schema/place/cardinality differs from compiler wire')
        direction_refs[direction] = {canonical_json(ref) for ref in refs}
    return direction_refs


def _inventory(reads, selected, checkpoint, identity):
    net, root, _ = _identity(reads, selected['net_ref'], checkpoint, identity)
    declaration_ref = _resource_ref(net['team_net_declaration_resource_ref'])
    if declaration_ref != net['llm_macro_net_ref'] or declaration_ref not in root['resource_refs']:
        raise ValueError('declaration is outside selected root')
    compiled = reads.declaration(net['team_net_declaration_resource_ref'], root['task_ref'])
    root_ref = net['team_design_root_ref']
    plan = _member(reads, root, net['plan_ref'], 'plan_version/v1', 'artifact_refs')
    if (len(plan['node_ids']) != len(set(plan['node_ids']))
            or set(plan['node_ids']) != {ref['logical_id'] for ref in net['node_refs']}):
        raise ValueError('selected plan node inventory differs from net')
    records = {}
    for field, kind in (('node_refs', 'node_declaration/v1'), ('operation_binding_refs', 'operation_binding/v1'),
                        ('output_binding_refs', 'output_binding/v1'),
                        ('executable_transition_binding_refs', 'executable_transition_binding/v1')):
        refs = net[field]
        if len({canonical_json(r) for r in refs}) != len(refs):
            raise ValueError('duplicate selected closure member')
        records[field] = {canonical_json(r): reads.metadata(r, kind) for r in refs}
    nodes, operations, outputs = (records[k] for k in ('node_refs', 'operation_binding_refs', 'output_binding_refs'))
    for node in nodes.values():
        operation = operations.get(canonical_json(node['producer_operation_binding_ref']))
        if (node['team_design_root_ref'] != root_ref or node['plan_ref'] != net['plan_ref']
                or operation is None or operation['node_ref'] != node['node_ref']
                or any(canonical_json(r) not in outputs or outputs[canonical_json(r)]['node_ref'] != node['node_ref']
                       for r in node['offered_output_binding_refs'])):
            raise ValueError('node has foreign root/producer/output identity')
    for operation in operations.values():
        node = nodes.get(canonical_json(operation['node_ref']))
        if (operation['team_design_root_ref'] != root_ref or node is None
                or node['producer_operation_binding_ref'] != operation['operation_binding_ref']
                or any(canonical_json(r) not in outputs or outputs[canonical_json(r)]['node_ref'] != node['node_ref']
                       for r in operation['output_binding_refs'])):
            raise ValueError('operation binding escaped selected closure')
        if node['opaque_role_artifact_ref'] != operation['operation_spec_ref']:
            raise ValueError('node role differs from selected operation specification')
        _member(reads, root, node['opaque_role_artifact_ref'], 'operation_spec/v1', 'artifact_refs')
        reads.metadata(operation['principal_ref'], 'principal/v1')
        authority = _member(reads, root, operation['authority_decision_ref'], 'user_authority_decision/v1', 'artifact_refs')
        reads.metadata(authority['user_principal_ref'], 'principal/v1')
        if (authority['status'] != 'effective' or authority['user_principal_ref'] != root['owner_principal_ref']
                or root['task_ref'] not in authority['governed_artifact_refs']):
            raise ValueError('selected binding authority differs from effective task owner')
        # These arrays have distinct roles. The Module contract defines binding
        # inputs as the union of actual input resources and input schema refs.
        resources = _resources(reads, root, node['input_resource_refs'])
        inputs = _resources(reads, root, node['input_schema_refs'])
        out_schemas = _resources(reads, root, node['output_schema_refs'])
        if (_resources(reads, root, operation['input_binding_refs']) != resources | inputs
                or _resources(reads, root, operation['input_schema_refs']) != inputs
                or _resources(reads, root, operation['output_schema_refs']) != out_schemas):
            raise ValueError('selected node and operation input/schema inventories disagree')
        for field in ('discoverable_resource_refs', 'readable_resource_refs'):
            if not _resources(reads, root, operation[field]) <= resources:
                raise ValueError('binding resource visibility escapes its declared node inputs')
        if (_ref_keys(operation['output_binding_refs'], 'output_binding/v1')
                != _ref_keys(node['offered_output_binding_refs'], 'output_binding/v1')):
            raise ValueError('selected node and operation output inventories disagree')
    for output in outputs.values():
        if (output['team_design_root_ref'] != root_ref or output['net_ref'] != selected['net_ref']
                or output['task_round_ref'] != net['task_round_ref'] or canonical_json(output['node_ref']) not in nodes):
            raise ValueError('output binding escaped selected closure')
        spec = reads.metadata(output['opaque_action_ref'], 'operation_spec/v1')
        _resources(reads, root, [output['place_ref']])
        node = nodes[canonical_json(output['node_ref'])]
        operation = operations[canonical_json(node['producer_operation_binding_ref'])]
        if output['opaque_action_ref'] != operation['operation_spec_ref']:
            raise ValueError('output action differs from selected node operation spec')
        ports = [p for p in spec['output_ports'] if p['port_id'] == output['output_port_id']]
        if (len(ports) != 1 or ports[0]['content_schema_ref'] != output['content_schema_ref']
                or ports[0]['cardinality'] != output['normal_output_cardinality']):
            raise ValueError('output binding differs from selected port')
    validate_module_bindings(net, root, outputs, nodes, reads.metadata, ValueError)
    transitions = {t.name: t for t in compiled.symbolic.transitions}
    compiled_ops = {o.declaration.name: o for o in compiled.operations}
    compiled_ports = {p.name: p for p in compiled.ports}
    bindings, schema_refs = [], {}
    for ref in net['executable_transition_binding_refs']:
        value = reads.metadata(ref, 'executable_transition_binding/v1')
        tid = value['transition_id']
        node = nodes.get(canonical_json(value['node_ref']))
        operation = operations.get(canonical_json(value['operation_binding_ref']))
        if (tid not in transitions or value['net_instance_ref'] != selected['net_ref'] or node is None
                or node['transition_id'] != tid or operation is None or operation['node_ref'] != value['node_ref']
                or node['producer_operation_binding_ref'] != value['operation_binding_ref']
                or value['declaration_resource_ref'] != net['team_net_declaration_resource_ref']
                or value['declaration_schema_ref'] != compiled.schema_version
                or value['principal_ref'] != operation['principal_ref']
                or value['activation_ref'] != node['activation_ref']):
            raise ValueError('executable binding differs from selected exact compiled net')
        reads.metadata(value['principal_ref'], 'principal/v1')
        op = compiled_ops[transitions[tid].operation]
        spec = reads.metadata(operation['operation_spec_ref'], 'operation_spec/v1')
        port_schemas = _operation_ports(reads, root, compiled, op, spec, schema_refs)
        if (_ref_keys(node['input_schema_refs']) != port_schemas['input']
                or _ref_keys(node['output_schema_refs']) != port_schemas['output']):
            raise ValueError('node schema refs differ from its exact compiled port ABI')
        by_port = {p['port_id']: p for p in spec['output_ports']}
        node_outputs = [output for output in outputs.values() if output['node_ref'] == node['node_ref']]
        if (len(node_outputs) != len(by_port)
                or {output['output_port_id'] for output in node_outputs} != set(by_port)):
            raise ValueError('output bindings do not cover exact compiled ports once')
        for output in node_outputs:
            registered = by_port[output['output_port_id']]
            port = next(p for p in compiled.ports if p.port_id == output['output_port_id'])
            outcomes = [outcome.name for outcome in op.declaration.outcomes
                        if any(product.port == port.name for product in outcome.products)]
            if (output['place'] != port.place or output['place_ref'] != registered['schema_ref']
                    or output['content_schema_id'] != port.schema
                    or (len(outcomes) == 1 and output.get('declared_outcome_id') != outcomes[0])
                    or (len(outcomes) != 1 and 'declared_outcome_id' in output)):
                raise ValueError('output place/schema/outcome differs from selected compiler wire')
        def ports(names):
            return [{'name': name, 'port_id': compiled_ports[name].port_id, 'place': compiled_ports[name].place}
                    for name in names]
        inputs, out = ports(op.declaration.inputs), ports(op.declaration.outputs)
        arcs = [a for a in compiled.symbolic.arcs if a.transition == tid]
        if (value['input_place_ids'] != sorted({a.place for a in arcs if a.direction == 'input'})
                or value['output_place_ids'] != sorted({a.place for a in arcs if a.direction == 'output'})):
            raise ValueError('executable places differ from selected compiler inventory')
        bindings.append({'transition_id': tid, 'node_ref': value['node_ref'],
                         'operation_binding_ref': value['operation_binding_ref'], 'executable_binding_ref': ref,
                         'operation': op.declaration.name, 'operation_id': op.operation_id,
                         'input_ports': inputs, 'output_ports': out})
    if len(bindings) != len(transitions) or {b['transition_id'] for b in bindings} != set(transitions):
        raise ValueError('executable transition inventory is incomplete or duplicated')
    return compiled, bindings, root


def _tokens(reads, checkpoint, net_ref, places, task_ref):
    tokens, ids, refs = [], set(), set()
    for ref in checkpoint['token_refs']:
        key = canonical_json(ref)
        value = reads.metadata(ref, 'petri_token/v1')
        if (key in refs or value['token_id'] in ids or value['petri_token_ref'] != ref
                or value['net_instance_ref'] != net_ref or value['place'] not in places
                or value['token_id'] >= checkpoint['next_token_id']):
            raise ValueError('checkpoint token has foreign or duplicate identity/place')
        refs.add(key); ids.add(value['token_id'])
        for field in ('resource_ref', 'work_resource_ref'):
            if value[field] is not None:
                resource = reads.metadata(_resource_ref(value[field]), 'resource_version/v1')
                if resource.get('task_ref') != task_ref:
                    raise ValueError('token resource belongs to another task')
        tokens.append({'token_ref': ref, 'place': value['place'], 'kind': value['kind'],
                       'resource_ref': value['resource_ref'],
                       'active_in_checkpoint': value['epoch'] == checkpoint['epoch'] and value['consumed_by'] is None})
    for place, capacity in places.items():
        if capacity is not None and sum(t['active_in_checkpoint'] and t['place'] == place for t in tokens) > capacity:
            raise ValueError('saved checkpoint exceeds selected place capacity')
    return tokens


def _adoption(reads, net_ref):
    events = [e for e in reads.events if e.event_type == 'net_adopted/v1' and str(e.task_id) == str(reads.core.task_id)]
    records = []
    previous = None
    for event in events:
        commit = reads.transaction_commit_event(event.transaction_id)
        reads.verify_transaction(event.transaction_id)
        ref = reference(event.payload['net_instance_ref'], 'net_instance/v1')
        net = reads.metadata(ref, 'net_instance/v1')
        for field in ('team_design_root_ref', 'llm_macro_net_ref', 'node_refs', 'operation_binding_refs', 'output_binding_refs'):
            if event.payload.get(field) != net.get(field):
                raise ValueError('adoption evidence differs from exact net closure')
        if event.payload.get('supersedes_net_ref') != previous or ref == previous:
            raise ValueError('bounded adoption evidence has a broken exact predecessor')
        records.append({'event_id': str(event.event_id), 'recorded_ordinal': event.ordinal,
                        'visible_at_commit': commit.ordinal, 'net_ref': ref})
        previous = ref
    matching = [r for r in records if r['net_ref'] == net_ref]
    status = ('current_at_cut' if previous == net_ref else 'previously_adopted' if matching else 'no_evidence_at_cut')
    return {'status': status, 'coverage': 'complete', 'cut': reads.cut,
            'scope': 'task_canonical_adoption_events_through_cut', 'current_net_ref': previous, 'records': matching}


def checkpoint_view(provider, *, net_ref, checkpoint_ref, cut, token_resource=None):
    from .dashboard import public_net, presentation_for
    selected = selector(net_ref, checkpoint_ref, cut)
    target = token_resource_target(token_resource) if token_resource is not None else None
    if target:
        if not _safe_integer(cut, 1):
            raise TokenResourceInvalid('invalid_target')
    core, observation = _capture_anchor(provider, resource_target=True) if target else _capture_anchor(provider)
    capture = {'head_ordinal': observation['source']['verified_head_ordinal'],
               'writer_fencing_epoch': observation['source']['writer_fencing_epoch']}
    if target and (target['expected_capture'] != capture):
        raise TokenResourceStale('stale_observation')
    if target and target['expected_task_id'] != str(core.task_id):
        raise TokenResourceInvalid('invalid_target')
    item, navigation, identity = _chain(provider, core, observation, selected)
    reads = _BoundedReads(core, cut)
    checkpoint, commit = reads.checkpoint(selected['checkpoint_ref'])
    if commit.ordinal != cut:
        raise ValueError('cut must equal the selected checkpoint complete commit')
    compiled, bindings, root = _inventory(reads, selected, checkpoint, identity)
    source = {**observation['source'], 'net_ref': selected['net_ref'], 'verified_head_ordinal': cut}
    net = project_compiled_net(compiled, source=source)
    tokens = _tokens(reads, checkpoint, net_ref, {p.name: p.capacity for p in compiled.symbolic.places}, root['task_ref'])
    for node in net['nodes']:
        if node['kind'] == 'place':
            node['tokens'] = [t for t in tokens if t['place'] == node['id']]
            node['active_token_count'] = sum(t['active_in_checkpoint'] for t in node['tokens'])
    net['marking'] = {'checkpoint_ref': checkpoint_ref, 'epoch': checkpoint['epoch'], 'token_count': len(tokens),
                      'active_token_count': sum(t['active_in_checkpoint'] for t in tokens)}
    presentation = presentation_for(provider.presentation, net)
    agents = []
    by_id = {n['id']: n for n in net['nodes']}
    for binding in bindings:
        value = reads.metadata(binding['executable_binding_ref'], 'executable_transition_binding/v1')
        node = by_id[binding['transition_id']]
        origin = ('registered_agent_binding' if value.get('agent_ref') is not None else
                  'declared_llm_executor' if node.get('executor_declaration', {}).get('contracts', {}).get('transport') == 'llm' else
                  'explicit_host_annotation' if node['id'] in presentation.get('agent_nodes', []) else None)
        if origin:
            semantic = node.get('config', {}).get('semantic_node_id')
            group = {'component': node.get('operation', node['id']).rsplit('.', 1)[0], 'semantic_node_id': semantic} if isinstance(semantic, str) and semantic else None
            agents.append({'transition_id': node['id'], 'source': origin, 'semantic_group': group,
                           'agent_ref': value.get('agent_ref'), 'executable_binding_ref': binding['executable_binding_ref']})
    frame = {'schema_version': 'rpnh/dashboard/v1', 'source': source, 'net': public_net(net),
        'boundaries': project_compiled_boundaries(compiled), 'transition_bindings': bindings,
        'presentation': presentation, 'agent_nodes': agents,
        'change': {'coverage': 'not_provided', 'consumed': [], 'deposited': [],
                   'firing_refs': checkpoint['transition_firing_refs'], 'previous_checkpoint_ref': checkpoint.get('previous_checkpoint_ref')},
        'position': {'mode': 'history', 'cursor': cut, 'at': item['at'], 'latest_head': observation['source']['verified_head_ordinal']},
        'coverage': {'history': 'selected_saved_checkpoint', 'firings': 'not_provided',
                     'agent_nodes': 'declared_or_explicit_host_annotation', 'provisional_history': 'unsupported',
                     'terminal_evidence': 'not_provided', 'end_reason': navigation['end_reason']},
        'observed_at': datetime.now(timezone.utc).isoformat()}
    result = {'schema_version': SCHEMA, 'selector': selected, 'frame': frame, 'navigation': navigation,
              'capture': {'head_ordinal': observation['source']['verified_head_ordinal'],
                          'writer_fencing_epoch': observation['source']['writer_fencing_epoch']},
              'adoption_evidence': _adoption(reads, net_ref),
              'coverage': {'topology': 'selected_declaration', 'marking': 'selected_checkpoint',
                           'bindings': 'selected_net_exact_closure', 'firings': 'not_provided',
                           'activity': 'not_provided', 'cross_net_delta': 'not_provided'}}
    if target:
        result['token_resource_metadata'] = _token_resource_metadata(reads, tokens, identity, selected, capture, target)
        validate_token_resource_response(result, target)
        try:
            provider._bound()
        except (ValueError, RuntimeError, OSError) as exc:
            raise TokenResourceAccessChanged('access_changed') from exc
        try:
            provider._stable(core, observation)
        except RuntimeError as exc:
            raise TokenResourceStale('stale_observation') from exc
    else:
        provider._stable(core, observation)
    return result
