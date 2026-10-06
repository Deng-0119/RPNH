"""Bounded, read-only comparison of two exact checkpoints in one Registry.

Composes the existing saved-checkpoint projection; it is not a new reader,
execution authority, cross-net identity mapper, or evaluation/scoring service.
The two historical cuts are independent. Both must remain disclosable through
one unchanged HOST-selected provider when the complete pair is returned.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs
import json

from .checkpoint_view import checkpoint_view, selector, reference
from cpn.rpnh.registry.schema_catalog import canonical_json

SCHEMA = 'rpnh/checkpoint_comparison/v1'
UNKNOWN_AXES = ('definition_details', 'effective_execution', 'activity', 'resource_contents', 'native_scores', 'terminal_evidence')
NODE_FIELDS = ('label', 'kind', 'category', 'hidden_by_default', 'operation', 'operation_id',
               'executor', 'inputs', 'outputs', 'token_kind', 'capacity', 'schema')
EDGE_FIELDS = ('source', 'target', 'kind', 'mode', 'weight', 'outcome', 'hidden_by_default',
               'resource', 'direction', 'emit', 'forward_source')
BINDING_FIELDS = ('node_ref', 'operation_binding_ref', 'executable_binding_ref', 'operation',
                  'operation_id', 'input_ports', 'output_ports')
TOKEN_FIELDS = ('place', 'kind', 'resource_ref', 'active_in_checkpoint')


class ComparisonInvalid(ValueError):
    """Invalid exact pair; no inferred latest checkpoint or cross-net matching."""


class ComparisonStale(RuntimeError):
    """The shared current capture moved during the paired read."""


class ComparisonAccessChanged(RuntimeError):
    """The HOST binding or physical source changed before disclosure."""


def exact_selector(value):
    if type(value) is not dict or set(value) != {'net_ref', 'checkpoint_ref', 'cut'}:
        raise ComparisonInvalid('invalid_query')
    try:
        result = selector(**value)
    except (ValueError, TypeError, KeyError) as exc:
        raise ComparisonInvalid('invalid_query') from exc
    if result['cut'] > 9007199254740991:
        raise ComparisonInvalid('invalid_query')
    return result


def selectors(left_selector, right_selector):
    left, right = exact_selector(left_selector), exact_selector(right_selector)
    if left['net_ref'] != right['net_ref']:
        raise ComparisonInvalid('same_exact_net_required')
    return left, right


def parse_query(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ComparisonInvalid('invalid_query')
            result[key] = value
        return result
    try:
        values = parse_qs(raw, keep_blank_values=True, strict_parsing=True)
        if set(values) != {'left_selector', 'right_selector'} or any(len(v) != 1 for v in values.values()):
            raise ComparisonInvalid('invalid_query')
        left, right = selectors(*(json.loads(values[key][0], object_pairs_hook=unique)
                                  for key in ('left_selector', 'right_selector')))
        return {'left_selector': left, 'right_selector': right}
    except (TypeError, ValueError, KeyError) as exc:
        raise ComparisonInvalid('invalid_query') from exc


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 9007199254740991


def _frame(value, selected):
    """Reject contradictory source/cut facts before deriving or disclosing rows."""
    from .server import _validate_projection
    if type(value) is not dict or value.get('schema_version') != 'rpnh/checkpoint_view/v1' or value.get('selector') != selected:
        raise ValueError('invalid comparison side')
    frame, capture = value['frame'], value['capture']
    _validate_projection(frame['net'])
    if (type(capture) is not dict or set(capture) != {'head_ordinal', 'writer_fencing_epoch'}
            or not _integer(capture['head_ordinal'], selected['cut'])
            or not _integer(capture['writer_fencing_epoch'])
            or frame['schema_version'] != 'rpnh/dashboard/v1'
            or frame['position']['mode'] != 'history' or not _integer(frame['position']['cursor'], 1)
            or frame['position']['cursor'] != selected['cut']
            or frame['position']['latest_head'] != capture['head_ordinal']
            or frame['net']['marking']['checkpoint_ref'] != selected['checkpoint_ref']):
        raise ValueError('invalid comparison capture')
    source = frame['source']
    for origin in (source, frame['net']['source']):
        if (origin['mode'] != 'registry_current' or origin['net_ref'] != selected['net_ref']
                or not _integer(origin['verified_head_ordinal'], 1) or origin['verified_head_ordinal'] != selected['cut']
                or not _integer(origin['writer_fencing_epoch']) or origin['writer_fencing_epoch'] != capture['writer_fencing_epoch']
                or origin['task_id'] != source['task_id'] or origin['run_dir'] != source['run_dir']
                or type(origin['run_dir']) is not str or type(origin['task_id']) is not str):
            raise ValueError('invalid comparison source')
    if (value['coverage'].get('topology') != 'selected_declaration'
            or value['coverage'].get('bindings') != 'selected_net_exact_closure'
            or value['coverage'].get('marking') != 'selected_checkpoint'
            or any(value['coverage'].get(k) != 'not_provided' for k in ('firings', 'activity', 'cross_net_delta'))
            or frame['coverage'].get('history') != 'selected_saved_checkpoint'
            or frame['coverage'].get('firings') != 'not_provided'
            or any('runtime' in n for n in frame['net']['nodes'])):
        raise ValueError('invalid comparison coverage')
    return frame


def _records(items, id_key, fields):
    result = {}
    for item in items:
        if type(item) is not dict or id_key not in item:
            raise ValueError('invalid comparison record')
        key = canonical_json(item[id_key]).decode('utf8')
        if key in result:
            raise ValueError('duplicate comparison identity')
        result[key] = (item[id_key], {k: deepcopy(item[k]) for k in fields if k in item})
    return result


def _facts(frame):
    net = frame['net']
    definitions = {}
    for group, fields in (('nodes', NODE_FIELDS), ('edges', EDGE_FIELDS)):
        for key, (identity, value) in _records(net[group], 'id', fields).items():
            definitions[group + ':' + key] = ({'kind': group, 'id': identity}, value)
    definitions['boundaries'] = ({'kind': 'boundaries'},
                                 {k: deepcopy(frame['boundaries'][k]) for k in ('entry', 'exit', 'terminal_rules') if k in frame['boundaries']})
    bindings = _records(frame['transition_bindings'], 'transition_id', BINDING_FIELDS)
    transitions = {n['id'] for n in net['nodes'] if n['kind'] == 'transition'}
    if {identity for identity, _ in bindings.values()} != transitions:
        raise ValueError('incomplete comparison binding closure')
    tokens = []
    for node in net['nodes']:
        if node['kind'] != 'place':
            continue
        if type(node.get('tokens')) is not list:
            raise ValueError('checkpoint tokens not provided')
        for token in node['tokens']:
            reference(token['token_ref'], 'petri_token/v1')
            if token['place'] != node['id'] or type(token['active_in_checkpoint']) is not bool:
                raise ValueError('invalid checkpoint token occurrence')
            tokens.append(token)
        if not _integer(node['active_token_count']) or node['active_token_count'] != sum(t['active_in_checkpoint'] for t in node['tokens']):
            raise ValueError('inconsistent place marking')
    marking = net['marking']
    if (not _integer(marking['epoch']) or marking['token_count'] != len(tokens)
            or marking['active_token_count'] != sum(t['active_in_checkpoint'] for t in tokens)
            or not _integer(marking['token_count']) or not _integer(marking['active_token_count'])):
        raise ValueError('inconsistent checkpoint marking')
    return {'definition': definitions, 'bindings': bindings,
            'tokens': _records(tokens, 'token_ref', TOKEN_FIELDS),
            'marking': {'marking': ({'kind': 'marking'}, {k: marking[k] for k in ('epoch', 'token_count', 'active_token_count')})}}


def _rows(left, right):
    """Absence is only relative to a complete disclosed collection at that cut.

    A missing *field* is unknown, not null, deleted, or evidence of equality.
    Token rows match exact occurrence refs, never resource bytes or display names.
    """
    rows = []
    for key in sorted(left.keys() | right.keys()):
        l, r = left.get(key), right.get(key)
        subject = (l or r)[0]
        fields = sorted((l[1] if l else {}).keys() | (r[1] if r else {}).keys())
        for field in fields:
            def fact(record):
                if record is None:
                    return {'status': 'absent_at_selected_cut', 'value': None}
                return {'status': 'provided', 'value': record[1][field]} if field in record[1] else {'status': 'not_provided', 'value': None}
            a, b = fact(l), fact(r)
            if 'not_provided' in (a['status'], b['status']):
                classification = 'unavailable'
            elif l is None:
                classification = 'present_right_only'
            elif r is None:
                classification = 'present_left_only'
            else:
                classification = 'unchanged' if canonical_json(a['value']) == canonical_json(b['value']) else 'content_change'
            rows.append({'subject': deepcopy(subject), 'field': field, 'classification': classification,
                         'left_fact': a, 'right_fact': b, 'reason_source': 'current_analysis'})
    return rows


def _public_side(value):
    """Narrow side DTO: never forward opaque/custom-provider extensions.

    Presentation annotations, change records and agent annotations are not used
    in this comparison. Public structural records are closed, including nested
    token refs and binding ports; an added config/body field fails closed.
    """
    def take(obj, allowed):
        if type(obj) is not dict or set(obj) - set(allowed):
            raise ValueError('unknown comparison side field')
        return {k: deepcopy(v) for k, v in obj.items() if k in allowed}
    def scalars(obj):
        if any(type(v) not in (str, int, bool, type(None)) for v in obj.values()):
            raise ValueError('opaque comparison field')
        return obj
    def refs(value, kind):
        return reference(value, kind)
    result = take(value, ('schema_version', 'selector', 'frame', 'navigation', 'capture', 'adoption_evidence', 'coverage'))
    raw = take(result['frame'], ('schema_version', 'source', 'net', 'boundaries', 'transition_bindings',
                               'presentation', 'agent_nodes', 'change', 'position', 'coverage', 'observed_at'))
    origin = take(raw['source'], ('mode', 'run_dir', 'task_id', 'net_ref', 'verified_head_ordinal', 'writer_fencing_epoch'))
    refs(origin['net_ref'], 'net_instance/v1')
    scalars({k:v for k,v in origin.items() if k != 'net_ref'})
    net = take(raw['net'], ('schema_version', 'source', 'summary', 'nodes', 'edges', 'marking'))
    if net['source'] != raw['source']:
        raise ValueError('comparison source differs')
    nodes = []
    for record in net['nodes']:
        node = take(record, ('id', *NODE_FIELDS, 'active_token_count', 'tokens'))
        scalars({k:v for k,v in node.items() if k not in ('inputs', 'outputs', 'tokens')})
        for key in ('inputs', 'outputs'):
            if key in node and (type(node[key]) is not list or any(type(v) is not str for v in node[key])):
                raise ValueError('invalid public node ports')
        for token in node.get('tokens', []):
            take(token, ('token_ref', *TOKEN_FIELDS))
            refs(token['token_ref'], 'petri_token/v1')
            scalars({k:v for k,v in token.items() if k not in ('token_ref', 'resource_ref')})
            if token['resource_ref'] is not None:
                pair = take(token['resource_ref'], ('resource_id', 'resource_version_id'))
                refs({'entity_type':'resource_version/v1', 'logical_id':pair['resource_id'], 'version_id':pair['resource_version_id']}, 'resource_version/v1')
        nodes.append(node)
    edges = [scalars(take(edge, ('id', *EDGE_FIELDS))) for edge in net['edges']]
    marking = take(net['marking'], ('checkpoint_ref', 'epoch', 'token_count', 'active_token_count'))
    refs(marking['checkpoint_ref'], 'marking_checkpoint/v1')
    scalars({k:v for k,v in marking.items() if k != 'checkpoint_ref'})
    boundaries = take(raw['boundaries'], ('entry', 'exit', 'terminal_rules', 'terminal_evidence'))
    if boundaries.get('terminal_evidence', 'not_provided') != 'not_provided':
        raise ValueError('unsupported terminal evidence')
    for direction in ('entry', 'exit'):
        for port in boundaries.get(direction, []):
            scalars(take(port, ('name', 'port', 'place')))
    for rule in boundaries.get('terminal_rules', []):
        take(rule, ('key', 'operation', 'transition_ids', 'outcome', 'port', 'place'))
        scalars({k:v for k,v in rule.items() if k != 'transition_ids'})
        if type(rule['transition_ids']) is not list or any(type(v) is not str for v in rule['transition_ids']):
            raise ValueError('invalid boundary transitions')
    bindings = []
    for record in raw['transition_bindings']:
        binding = take(record, ('transition_id', *BINDING_FIELDS))
        for key, kind in (('node_ref', 'node_declaration/v1'), ('operation_binding_ref', 'operation_binding/v1'),
                          ('executable_binding_ref', 'executable_transition_binding/v1')):
            refs(binding[key], kind)
        scalars({k:v for k,v in binding.items() if k in ('transition_id', 'operation', 'operation_id')})
        for key in ('input_ports', 'output_ports'):
            for port in binding[key]:
                scalars(take(port, ('name', 'port_id', 'place')))
        bindings.append(binding)
    # Summary and host presentation are deliberately not part of this DTO.
    result['frame'] = {'schema_version': raw['schema_version'], 'source': origin,
        'net': {'schema_version': net['schema_version'], 'source': deepcopy(origin), 'summary': {},
                'nodes': nodes, 'edges': edges, 'marking': marking},
        'boundaries': boundaries, 'transition_bindings': bindings,
        'position': scalars(take(raw['position'], ('mode', 'cursor', 'at', 'latest_head'))),
        'coverage': scalars(take(raw['coverage'], ('history', 'firings', 'agent_nodes', 'provisional_history', 'terminal_evidence', 'end_reason')))}
    navigation = take(result['navigation'], ('previous_net_segment', 'coverage', 'end_reason', 'max_chain', 'loaded_checkpoints'))
    if navigation['previous_net_segment'] is not None:
        exact_selector(navigation['previous_net_segment'])
    scalars({k:v for k,v in navigation.items() if k != 'previous_net_segment'})
    evidence = take(result['adoption_evidence'], ('status', 'coverage', 'cut', 'scope', 'current_net_ref', 'records'))
    if evidence['current_net_ref'] is not None:
        refs(evidence['current_net_ref'], 'net_instance/v1')
    scalars({k:v for k,v in evidence.items() if k not in ('current_net_ref', 'records')})
    for record in evidence['records']:
        take(record, ('event_id', 'recorded_ordinal', 'visible_at_commit', 'net_ref'))
        refs(record['net_ref'], 'net_instance/v1')
        scalars({k:v for k,v in record.items() if k != 'net_ref'})
    result['coverage'] = scalars(take(result['coverage'], ('topology', 'marking', 'bindings', 'firings', 'activity', 'cross_net_delta')))
    return result


def project_comparison(left, right, left_selector, right_selector):
    """Pure projection of already authorized exact checkpoint responses."""
    ls, rs = selectors(left_selector, right_selector)
    left, right = _public_side(left), _public_side(right)
    lf, rf = _frame(left, ls), _frame(right, rs)
    source = {k: lf['source'][k] for k in ('run_dir', 'task_id', 'net_ref')}
    if source != {k: rf['source'][k] for k in source}:
        raise ComparisonAccessChanged('access_changed')
    if left['capture'] != right['capture']:
        raise ComparisonStale('stale_observation')
    lfacts, rfacts = _facts(lf), _facts(rf)
    axes = {axis: {'coverage': 'complete_in_declared_scope', 'rows': _rows(lfacts[axis], rfacts[axis])}
            for axis in lfacts}
    axes.update({axis: {'coverage': 'not_provided', 'rows': []} for axis in UNKNOWN_AXES})
    return {'schema_version': SCHEMA, 'comparison_mode': 'descriptive', 'comparability': 'not_established',
            'global_atomic_snapshot': False, 'source': source, 'left_selector': ls, 'right_selector': rs,
            'capture': deepcopy(left['capture']), 'left': deepcopy(left), 'right': deepcopy(right),
            'coverage': {'status': 'partial', 'scope': 'public_checkpoint_projection', 'unknown_axes': list(UNKNOWN_AXES)},
            'axes': axes}


def validate_comparison_response(value, left_selector, right_selector):
    if type(value) is not dict:
        raise ValueError('invalid comparison response')
    expected = project_comparison(value['left'], value['right'], left_selector, right_selector)
    if canonical_json(value) != canonical_json(expected):
        raise ValueError('invalid comparison response')


def _source_pin(provider):
    """Local identity guard; no path or authority supplied by the client."""
    try:
        provider._bound()
        core = provider._open()
        if str(core.task_id) != provider.task_id:
            raise ValueError('task identity changed')
        run_dir = Path(provider.run_dir).resolve(strict=True)
        db_path = core.event_store.path.resolve(strict=True)
        files = [(path.stat().st_dev, path.stat().st_ino) for path in (run_dir, db_path)]
        source = (str(run_dir), str(db_path), str(core.task_id), tuple(files), id(provider.binding), id(provider.catalog))
        capture = {'head_ordinal': core.event_store.max_ordinal(), 'writer_fencing_epoch': core.event_store.writer_epoch}
        return source, capture
    except (ValueError, RuntimeError, OSError) as exc:
        raise ComparisonAccessChanged('access_changed') from exc


def comparison_view(provider, left_selector, right_selector):
    ls, rs = selectors(left_selector, right_selector)
    pin, capture = _source_pin(provider)
    # Each exact read independently validates the bounded checkpoint/net closure.
    # Nothing from the first read is disclosed if the second or final guard fails.
    left = checkpoint_view(provider, **ls)
    right = checkpoint_view(provider, **rs)
    result = project_comparison(left, right, ls, rs)
    final_pin, final_capture = _source_pin(provider)
    if pin != final_pin:
        raise ComparisonAccessChanged('access_changed')
    if capture != final_capture or result['capture'] != capture:
        raise ComparisonStale('stale_observation')
    if result['source']['run_dir'] != pin[0] or result['source']['task_id'] != pin[2]:
        raise ComparisonAccessChanged('access_changed')
    return result
