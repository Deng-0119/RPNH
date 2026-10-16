"""Read-only current execution-PN navigation beneath an exact business firing.

The existing Registry closure reader verifies original attachments and producer
membership. No payload resources, scheduler, or runtime recovery are exposed.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from . import checkpoint_view as checkpoints
from .firing_activity import _json
from cpn.rpnh.registry._event_store.views import _activity_head
from cpn.rpnh.registry.execution_child_closure import ClosureSnapshot, CHILD_OBJECTS
from cpn.rpnh.registry.execution_net import ExecutionParentAuthority
from cpn.rpnh.registry.execution_runtime import ExecutionRuntime, _ref, _from_ref

SCHEMA = 'rpnh/execution_nets/v1'


class ExecutionQueryError(ValueError):
    pass


class ExecutionStale(RuntimeError):
    pass


class ExecutionAccessChanged(RuntimeError):
    pass


def parse_query(query):
    try:
        q = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        required = {'net_ref', 'checkpoint_ref', 'cut', 'firing_ref', 'expected_source', 'expected_capture'}
        if not required <= set(q) or set(q) - required - {'instance_ref'} or any(len(v) != 1 for v in q.values()):
            raise ValueError('query keys differ')
        return {key: int(values[0]) if key == 'cut' else _json(values[0]) for key, values in q.items()}
    except (ValueError, TypeError, KeyError) as exc:
        raise ExecutionQueryError('invalid execution net query') from exc


class _ExecutionReads(ExecutionRuntime):
    """Reuse existing topology/checkpoint validators with snapshot-bound reads."""

    def __init__(self, core, snapshot, parent):
        super().__init__(core)
        self.snapshot, self.parent = snapshot, parent

    def _load(self, ref, expected_type, *, parent_invocation_id=None):
        scoped = expected_type in CHILD_OBJECTS | {'invocation/v1', 'transition_firing/v1'}
        return self.snapshot.read(_ref(ref), expected_type, parent=self.parent if scoped else None)


def _bound(provider):
    try:
        provider._bound()
    except (ValueError, RuntimeError, OSError, PermissionError) as exc:
        raise ExecutionAccessChanged('execution source access changed') from exc


def execution_nets(provider, *, net_ref, checkpoint_ref, cut, firing_ref,
                   expected_source, expected_capture, instance_ref=None):
    try:
        selected = checkpoints.selector(net_ref, checkpoint_ref, cut)
        firing_ref = checkpoints.reference(firing_ref, 'transition_firing/v1')
        if instance_ref is not None:
            instance_ref = checkpoints.reference(instance_ref, 'execution_instance/v1')
        if (type(expected_source) is not dict or set(expected_source) != {'task_id', 'run_dir'}
                or type(expected_capture) is not dict or set(expected_capture) != {'head_ordinal', 'writer_fencing_epoch'}
                or not checkpoints._safe_integer(expected_capture['head_ordinal'], 1)
                or not checkpoints._safe_integer(expected_capture['writer_fencing_epoch'])):
            raise ValueError('source/capture required')
    except (ValueError, TypeError, KeyError) as exc:
        raise ExecutionQueryError('invalid execution selection') from exc
    _bound(provider)
    try:
        core, anchor = checkpoints._capture_anchor(provider)
    except RuntimeError as exc:
        raise ExecutionStale('execution observation changed') from exc
    source = {key: anchor['source'][key] for key in ('task_id', 'run_dir')}
    capture = {'head_ordinal': anchor['source']['verified_head_ordinal'],
               'writer_fencing_epoch': anchor['source']['writer_fencing_epoch']}
    if source != expected_source:
        raise ExecutionQueryError('execution source differs')
    if capture != expected_capture:
        raise ExecutionStale('execution observation changed')
    if net_ref != anchor['source']['net_ref'] or checkpoint_ref != anchor['net']['marking']['checkpoint_ref']:
        raise ExecutionQueryError('execution navigation requires the current net/checkpoint')
    reads = checkpoints._BoundedReads(core, cut)
    checkpoint, commit = reads.checkpoint(checkpoint_ref)
    if commit.ordinal != cut:
        raise ExecutionQueryError('business checkpoint cut differs')
    _, _, identity = checkpoints._identity(reads, net_ref, checkpoint)
    _, bindings, _ = checkpoints._inventory(reads, selected, checkpoint, identity)

    with core.event_store.connect() as db:
        db.execute('BEGIN')
        try:
            if _activity_head(db) != (capture['head_ordinal'], capture['writer_fencing_epoch']):
                raise ExecutionStale('execution observation changed')
            gate = db.execute('SELECT * FROM firing_publications WHERE firing_version_id=?',
                              (firing_ref['version_id'],)).fetchone()
            if gate is None or gate['state'] not in {'PROVISIONAL', 'PUBLISHED', 'SUPERSEDED'} or gate['net_version_id'] != net_ref['version_id']:
                raise ExecutionQueryError('firing is not visible in this net')
            parent = {'parent_business_firing_ref': firing_ref, 'parent_business_net_ref': net_ref,
                      'parent_invocation_ref': {'entity_type': 'invocation/v1',
                          'logical_id': gate['invocation_logical_id'], 'version_id': gate['invocation_version_id']}}
            snapshot = ClosureSnapshot(core.event_store, db, core.task_id, cut=capture['head_ordinal'],
                provisional_parent=parent if gate['state'] != 'PUBLISHED' else None)
            firing = snapshot.read(firing_ref, 'transition_firing/v1', parent=parent)
            parent['parent_business_checkpoint_ref'] = firing['admission_marking_checkpoint_ref']
            authority = ExecutionParentAuthority(*(_from_ref(parent[k]) for k in (
                'parent_invocation_ref', 'parent_business_firing_ref', 'parent_business_net_ref', 'parent_business_checkpoint_ref')))
            runtime = _ExecutionReads(core, snapshot, parent)
            runtime._validate_parent(authority, require_open=False)
            binding = next((b for b in bindings if b['transition_id'] == firing['transition_id']), None)
            if (firing['task_ref'] != identity['task_ref'] or binding is None
                    or any(firing[k] != binding[k] for k in ('node_ref', 'operation_binding_ref'))):
                raise ExecutionQueryError('firing binding differs')
            children = _children(db, snapshot, runtime, authority, parent, instance_ref)
        finally:
            db.execute('ROLLBACK')
    _bound(provider)
    try:
        provider._stable(core, anchor)
    except RuntimeError as exc:
        raise ExecutionStale('execution observation changed') from exc
    return {'schema_version': SCHEMA, 'selector': selected, 'source': source,
            'capture': capture, 'firing_ref': firing_ref, 'parent': parent,
            'publication_state': gate['state'], 'instance_ref': instance_ref,
            'children': children, 'read_only': True, 'coverage': 'current_attached_execution_nets'}


def _children(db, snapshot, runtime, authority, parent, requested):
    firing = parent['parent_business_firing_ref']
    stream = 'execution-children:' + firing['version_id']
    events = db.execute('SELECT * FROM events WHERE stream_id=? ORDER BY ordinal', (stream,)).fetchall()
    attachments, seal = [], None
    for event in events:
        data = snapshot.event(event, parent=parent)
        if (event['aggregate_id'] != firing['logical_id'] or event['aggregate_type'] != 'execution_child_set'
                or event['producer_invocation_id'] != parent['parent_invocation_ref']['logical_id']
                or data['parent_business_firing_ref'] != firing
                or data['parent_invocation_ref'] != parent['parent_invocation_ref']):
            raise ValueError('execution attachment parent differs')
        if event['event_type'] == 'execution_instance_attached/v1' and seal is None:
            attachments.append((event, data))
        elif event['event_type'] == 'execution_children_sealed/v1' and seal is None:
            seal = (event, data)
        else:
            raise ValueError('unexpected execution child stream fact')
    refs = [data['execution_instance_ref'] for _, data in attachments]
    if len({r['version_id'] for r in refs}) != len(refs):
        raise ValueError('duplicate attachment')
    if requested is not None and requested not in refs:
        raise ExecutionQueryError('instance is not attached to this firing')
    if seal and seal[1]['execution_instance_refs'] != refs:
        # Seal order need not match attachment order.
        if sorted(seal[1]['execution_instance_refs'], key=lambda r: r['version_id']) != sorted(refs, key=lambda r: r['version_id']):
            raise ValueError('sealed child inventory differs')
    children = []
    for event, attached in attachments:
        ref = attached['execution_instance_ref']
        if requested is not None and ref != requested:
            continue
        instance, _, definition_ref, definition = runtime._instance(_from_ref(ref), authority)
        if (instance['initial_checkpoint_ref'] != attached['initial_checkpoint_ref']
                or snapshot.rows[ref['version_id']]['transaction_id'] != event['transaction_id']):
            raise ValueError('original attachment differs')
        rows = db.execute('SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id '
                          'WHERE o.logical_id=? ORDER BY e.ordinal',
                          (instance['initial_checkpoint_ref']['logical_id'],)).fetchall()
        previous = None
        for row in rows:
            cp_ref = {'entity_type': row['object_type'], 'logical_id': row['logical_id'], 'version_id': row['version_id']}
            cp = runtime._checkpoint(instance_ref=_from_ref(ref), definition_ref=definition_ref,
                definition=definition, parent=authority, checkpoint_ref=_from_ref(cp_ref))
            if ((previous is None and (cp_ref != instance['initial_checkpoint_ref'] or cp.sequence != 0))
                    or (previous is not None and (cp.previous_checkpoint_ref != previous.checkpoint_ref or cp.sequence != previous.sequence + 1))):
                raise ValueError('execution checkpoint chain differs')
            previous = cp
        if previous is None:
            raise ValueError('attached execution has no checkpoint')
        cp = previous
        active = [runtime._active_firing(instance_ref=_from_ref(ref), definition_ref=definition_ref,
                  parent=authority, firing_ref=f) for f in cp.active_firing_refs]
        firing_fields = ('execution_transition_firing_ref', 'transition_id', 'status', 'recovery_mode',
                         'predecessor_checkpoint_ref', 'input_token_refs')
        child = {**instance, 'definition': runtime._definition_metadata(definition_ref, definition),
                 'attachment': {'event_id': event['event_id'], 'ordinal': event['ordinal']},
                 'checkpoint_ref': _ref(cp.checkpoint_ref), 'sequence': cp.sequence,
                 'status': cp.status, 'map_ready': cp.map_ready, 'sealed': seal is not None,
                 'map_ready_checkpoint_ref': _ref(cp.checkpoint_ref) if cp.map_ready else None,
                 'tokens': [{'token_ref': _ref(t.token_ref), 'place': t.place, 'ordinal': t.ordinal,
                             'produced_by_firing_ref': _ref(t.produced_by_firing_ref)} for t in cp.tokens],
                 'active_firings': [{k: f[k] for k in firing_fields} for f in active],
                 'transition_firing_ref': _ref(cp.transition_firing_ref),
                 'evidence_refs': [_ref(r) for r in cp.evidence_refs], 'terminal_mapping_ref': None}
        if seal:
            index = seal[1]['execution_instance_refs'].index(ref)
            if seal[1]['execution_checkpoint_refs'][index] != child['checkpoint_ref'] or not cp.map_ready:
                raise ValueError('seal checkpoint differs')
            mapping_ref = seal[1]['execution_terminal_mapping_refs'][index]
            mapping = snapshot.read(mapping_ref, 'execution_terminal_mapping/v1', parent=parent)
            if (mapping['execution_instance_ref'] != ref or mapping['execution_checkpoint_ref'] != child['checkpoint_ref']
                    or mapping['parent_invocation_ref'] != parent['parent_invocation_ref']
                    or mapping['parent_business_firing_ref'] != firing or mapping['evidence_refs'] != child['evidence_refs']):
                raise ValueError('terminal mapping differs')
            child.update(terminal_mapping_ref=mapping_ref,
                         seal_event={'event_id': seal[0]['event_id'], 'ordinal': seal[0]['ordinal']})
        children.append(child)
    return children
