"""Whole-pair, read-only comparison over a HOST-established public read session.

No Registry internals, compiler, grant creation or current-run provider are used.
All facts are collected at one validated cut per source and disclosed only after
final public session guards pass. This does not modify checkpoint_comparison/v1.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import parse_qs

SCHEMA = 'rpnh/comparison_context/v1'
REQUEST_SCHEMA = 'rpnh/comparison_request/v1'
AXES = ('definition', 'configuration', 'materials', 'runtime')
LIMITS = {'max_nodes_per_side': 10000, 'max_edges_per_side': 30000,
          'max_mapping_relations': 20000, 'max_field_rows': 200000,
          'max_evidence_reads': 20000, 'max_response_bytes': 16000000}
NODE_FIELDS = ('id', 'label', 'kind', 'category', 'hidden_by_default', 'operation',
               'operation_id', 'executor', 'inputs', 'outputs', 'token_kind', 'capacity', 'schema')
EDGE_FIELDS = ('id', 'source', 'target', 'kind', 'mode', 'weight', 'outcome',
               'hidden_by_default', 'resource', 'direction', 'emit', 'forward_source')
REASONS = {'exact_identity', 'verified_author_mapping', 'explicit_presence', 'not_requested',
           'not_provided', 'not_disclosed', 'not_captured', 'incomplete_scope', 'mapping_missing',
           'mapping_unsupported', 'mapping_conflicting', 'comparator_unsupported',
           'projection_unavailable', 'scope_limit'}
COVERAGES = {'complete_in_declared_scope', 'partial', 'not_provided', 'not_requested'}
MODES = {'reliable_diff', 'partial_mapping', 'full_pair'}


class ComparisonContextError(ValueError):
    """Only this fixed code crosses the HTTP boundary."""
    def __init__(self, code='invalid_query'):
        if code not in {'invalid_query', 'access_changed', 'stale_observation', 'unsupported',
                        'scope_limit', 'projection_unavailable', 'read_failed', 'invalid_response'}:
            code = 'read_failed'
        self.code = code
        super().__init__(code)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _key(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _object(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ComparisonContextError()
    return value


def _text(value, max_length=4096):
    if type(value) is not str or not value or len(value) > max_length or any(ord(c) < 32 for c in value):
        raise ComparisonContextError()
    return value


def _int(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 9007199254740991:
        raise ComparisonContextError()
    return value


def _array(value, limit=30000):
    if type(value) is not list or len(value) > limit:
        raise ComparisonContextError()
    return value


def _unique(values, *, strings=False):
    _array(values)
    if strings:
        for item in values:
            _text(item)
    if len({_json(v) for v in values}) != len(values):
        raise ComparisonContextError()
    return values


def reference(value, *, source_id=None, entity_type=None):
    _object(value, ('schema_version', 'source_id', 'ref'))
    _text(value['source_id'])
    if source_id is not None and value['source_id'] != source_id:
        raise ComparisonContextError()
    resource = value['schema_version'] == 'rpnh/collaboration/source_resource_ref/v1'
    if not resource and value['schema_version'] != 'rpnh/collaboration/source_version_ref/v1':
        raise ComparisonContextError()
    inner = _object(value['ref'], ('resource_id', 'resource_version_id') if resource else ('entity_type', 'logical_id', 'version_id'))
    for field in (('resource_id', 'resource_version_id') if resource else ('logical_id', 'version_id')):
        if not re.fullmatch(r'[a-z][a-z0-9_]*:[a-f0-9]{32}', _text(inner[field])):
            raise ComparisonContextError()
    if resource:
        if not inner['resource_id'].startswith('resource:') or not inner['resource_version_id'].startswith('resource_version:') or entity_type:
            raise ComparisonContextError()
    elif not re.fullmatch(r'[a-z][a-z0-9_]*(?:/[a-z][a-z0-9_]*)*/v[1-9][0-9]*', _text(inner['entity_type'])) or (entity_type and inner['entity_type'] != entity_type):
        raise ComparisonContextError()
    return value


def target(value):
    if type(value) is not dict:
        raise ComparisonContextError()
    kind = value.get('kind')
    fields = {'net_instance': ('net_ref',), 'author_revision': ('revision_ref',),
              'checkpoint': ('net_ref', 'checkpoint_ref', 'checkpoint_commit_ordinal')}.get(kind)
    if fields is None:
        raise ComparisonContextError()
    _object(value, ('kind', 'source_id', *fields))
    _text(value['source_id'])
    for field in fields:
        if field.endswith('_ref'):
            reference(value[field], source_id=value['source_id'], entity_type={
                'net_ref': 'net_instance/v1', 'checkpoint_ref': 'marking_checkpoint/v1',
                'revision_ref': None}[field])
            if field == 'revision_ref' and value[field]['ref'].get('entity_type') not in {'collaboration_net_revision/v1', 'collaboration_assembly_revision/v9'}:
                raise ComparisonContextError()
    if kind == 'checkpoint':
        _int(value['checkpoint_commit_ordinal'], 1)
    return value


def occurrence(value):
    for step in _array(value, 128):
        _object(step, ('declaration_ref', 'member_id'))
        reference(step['declaration_ref'])
        _text(step['member_id'])
    return value


def side_scope(value):
    if type(value) is not dict:
        raise ComparisonContextError()
    kind = value.get('kind')
    if kind == 'full_net':
        _object(value, ('kind',))
    elif kind == 'nodes':
        _object(value, ('kind', 'node_ids'))
        _unique(value['node_ids'], strings=True)
        if not value['node_ids']:
            raise ComparisonContextError()
    elif kind in {'module', 'subnet'}:
        _object(value, ('kind', 'selector'))
        selector = _object(value['selector'], ('declaration_ref', 'occurrence_path', 'element_id' if kind == 'module' else 'subnet_id'))
        reference(selector['declaration_ref'])
        occurrence(selector['occurrence_path'])
        _text(selector['element_id' if kind == 'module' else 'subnet_id'])
    else:
        raise ComparisonContextError()
    return value


def source_cut(value):
    _object(value, ('source_id', 'cut_id', 'head', 'reader_contract_version'))
    for key in ('source_id', 'cut_id', 'reader_contract_version'):
        _text(value[key])
    _object(value['head'], ('ordinal', 'writer_fencing_epoch'))
    _int(value['head']['ordinal'])
    _int(value['head']['writer_fencing_epoch'])
    return value


def endpoint(value):
    _object(value, ('side', 'target_key', 'subject_kind', 'subject_id', 'occurrence_path'))
    if value['side'] not in {'left', 'right'} or value['subject_kind'] not in {'node', 'edge', 'boundary', 'author_element'}:
        raise ComparisonContextError()
    _text(value['target_key'])
    _text(value['subject_id'])
    occurrence(value['occurrence_path'])
    return value


def validate_request(value):
    _object(value, ('schema_version', 'session_id', 'client_request_id', 'left', 'right', 'source_cuts',
                    'scope', 'axes', 'view_preference', 'visual_pairs', 'limits'))
    if value['schema_version'] != REQUEST_SCHEMA or value['view_preference'] not in {'auto', 'full_pair'}:
        raise ComparisonContextError()
    _text(value['session_id'], 256)
    _text(value['client_request_id'], 256)
    target(value['left']); target(value['right'])
    if value['source_cuts'] is not None:
        if type(value['source_cuts']) is not dict or len(value['source_cuts']) > 128:
            raise ComparisonContextError()
        for sid, cut in value['source_cuts'].items():
            if source_cut(cut)['source_id'] != sid:
                raise ComparisonContextError()
        if not {value[side]['source_id'] for side in ('left', 'right')} <= set(value['source_cuts']):
            raise ComparisonContextError()
    _object(value['scope'], ('kind', 'left', 'right'))
    if value['scope']['kind'] not in {'full_pair', 'selected_pair'}:
        raise ComparisonContextError()
    for side in ('left', 'right'):
        side_scope(value['scope'][side])
        if value['scope']['kind'] == 'full_pair' and value['scope'][side]['kind'] != 'full_net':
            raise ComparisonContextError()
    _unique(value['axes'], strings=True)
    if not set(value['axes']) <= set(AXES):
        raise ComparisonContextError()
    _unique(value['visual_pairs'])
    ids = set()
    for pair in value['visual_pairs']:
        _object(pair, ('pair_id', 'left', 'right'), ('label',))
        _text(pair['pair_id'], 256)
        if pair['pair_id'] in ids:
            raise ComparisonContextError()
        ids.add(pair['pair_id'])
        if 'label' in pair: _text(pair['label'])
        for side in ('left', 'right'):
            if not _unique(pair[side]): raise ComparisonContextError()
            for item in pair[side]:
                endpoint(item)
                if item['side'] != side or item['target_key'] != _key(value[side]):
                    raise ComparisonContextError()
    _object(value['limits'], LIMITS)
    for key, maximum in LIMITS.items():
        if _int(value['limits'][key], 1) > maximum: raise ComparisonContextError('scope_limit')
    if len(_json(value).encode()) > 1000000:
        raise ComparisonContextError('scope_limit')
    return deepcopy(value)


def parse_query(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ComparisonContextError()
            result[key] = value
        return result
    try:
        values = parse_qs(raw, keep_blank_values=True, strict_parsing=True)
        if set(values) != {'request'} or len(values['request']) != 1:
            raise ComparisonContextError()
        return validate_request(json.loads(values['request'][0], object_pairs_hook=unique,
                                           parse_constant=lambda _: (_ for _ in ()).throw(ComparisonContextError())))
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        if isinstance(exc, ComparisonContextError): raise
        raise ComparisonContextError() from exc


def _projection_graph(value):
    """Reject unknown fields; do not silently repair or infer graph topology."""
    nodes, edges = deepcopy(value['nodes']), deepcopy(value['edges'])
    node_ids, edge_ids = set(), set()
    for node in _array(nodes):
        _object(node, NODE_FIELDS[:5], NODE_FIELDS[5:])
        _text(node['id']); _text(node['label'])
        if node['id'] in node_ids or node['kind'] not in {'place', 'transition'} or node['category'] not in {'execution', 'place', 'resource'} or type(node['hidden_by_default']) is not bool:
            raise ComparisonContextError('read_failed')
        node_ids.add(node['id'])
        for field in NODE_FIELDS[5:]:
            if field not in node: continue
            v = node[field]
            if field in {'inputs', 'outputs'}:
                _unique(v, strings=True)
            elif field == 'capacity':
                if v is not None: _int(v)
            elif v is not None and type(v) is not str:
                raise ComparisonContextError('read_failed')
    for edge in _array(edges):
        _object(edge, EDGE_FIELDS[:8], EDGE_FIELDS[8:])
        _text(edge['id']); _text(edge['kind']); _text(edge['mode'])
        if edge['id'] in edge_ids or edge['source'] not in node_ids or edge['target'] not in node_ids or type(edge['hidden_by_default']) is not bool:
            raise ComparisonContextError('read_failed')
        edge_ids.add(edge['id']); _int(edge['weight'], 1)
        if edge['outcome'] is not None: _text(edge['outcome'])
        if 'resource' in edge and type(edge['resource']) is not bool:
            raise ComparisonContextError('read_failed')
        for field in ('direction', 'emit', 'forward_source'):
            if field in edge and edge[field] is not None: _text(edge[field])
    return nodes, edges


def _hierarchy(projection, nodes, edges):
    raw = projection.get('hierarchy')
    if raw is None:
        return {'coverage': 'not_provided', 'scopes': []}
    node_ids, edge_ids = {n['id'] for n in nodes}, {e['id'] for e in edges}
    scopes = []
    for item in _array(raw, 10000):
        _object(item, ('scope', 'parent_scope', 'label', 'node_ids', 'edge_ids', 'evidence_refs'))
        side_scope(item['scope'])
        if item['scope']['kind'] not in {'module', 'subnet'}: raise ComparisonContextError('read_failed')
        if item['parent_scope'] is not None: side_scope(item['parent_scope'])
        _text(item['label']); _unique(item['node_ids'], strings=True); _unique(item['edge_ids'], strings=True)
        if not set(item['node_ids']) <= node_ids or not set(item['edge_ids']) <= edge_ids:
            raise ComparisonContextError('read_failed')
        for ref in _unique(item['evidence_refs']): reference(ref)
        scopes.append(deepcopy(item))
    _unique([item['scope'] for item in scopes])
    known = {_json(item['scope']) for item in scopes} | {_json({'kind': 'full_net'})}
    for item in scopes:
        if item['parent_scope'] is not None and _json(item['parent_scope']) not in known:
            raise ComparisonContextError('read_failed')
        seen = {_json(item['scope'])}; parent = item['parent_scope']
        while parent is not None and parent['kind'] != 'full_net':
            key = _json(parent)
            if key in seen: raise ComparisonContextError('read_failed')
            seen.add(key)
            ancestor = next(x for x in scopes if x['scope'] == parent)
            if not set(item['node_ids']) <= set(ancestor['node_ids']): raise ComparisonContextError('read_failed')
            parent = ancestor['parent_scope']
    return {'coverage': 'provided', 'scopes': scopes}


def _scope(projection, selected, requested, limits):
    nodes, edges = _projection_graph(projection)
    hierarchy = _hierarchy(projection, nodes, edges)
    members = {n['id'] for n in nodes}
    evidence = []
    if requested['kind'] == 'nodes':
        chosen = set(requested['node_ids'])
        if not chosen <= members: raise ComparisonContextError()
        members = chosen
    elif requested['kind'] != 'full_net':
        declaration = next((item for item in hierarchy['scopes'] if item['scope'] == requested), None)
        if declaration is None: raise ComparisonContextError('projection_unavailable')
        members = set(declaration['node_ids']); evidence = declaration['evidence_refs']
    public_nodes = [n for n in nodes if n['id'] in members]
    public_edges = [e for e in edges if e['source'] in members and e['target'] in members]
    if len(public_nodes) > limits['max_nodes_per_side'] or len(public_edges) > limits['max_edges_per_side']:
        raise ComparisonContextError('scope_limit')
    boundary = [{'edge_id': e['id'], 'inside_node_id': e['source'] if e['source'] in members else e['target'],
                 'outside_endpoint': {'state': 'provided', 'node_id': e['target'] if e['source'] in members else e['source']},
                 'direction': 'outgoing' if e['source'] in members else 'incoming'} for e in edges
                if (e['source'] in members) != (e['target'] in members)]
    scope_key = _key({'target': selected, 'scope': requested})
    resolution = {'scope_key': scope_key, 'requested_scope': deepcopy(requested), 'resolved_scope': deepcopy(requested),
                  'member_node_ids': sorted(members), 'member_edge_ids': sorted(e['id'] for e in public_edges),
                  'boundary_edges': boundary, 'membership_evidence_refs': deepcopy(evidence),
                  'coverage': 'complete_in_declared_scope'}
    graph = {'schema_version': 'rpnh/comparison_pn/v1', 'target_key': _key(selected), 'scope_key': scope_key,
             'nodes': public_nodes, 'edges': public_edges, 'boundary_edges': deepcopy(boundary),
             'topology_coverage': 'complete_in_declared_scope'}
    return graph, resolution, hierarchy


def _endpoint(side, graph, kind, identity, path=None):
    return {'side': side, 'target_key': graph['target_key'], 'subject_kind': kind,
            'subject_id': identity, 'occurrence_path': deepcopy(path or [])}


def _subjects(side, graph):
    return [_endpoint(side, graph, kind, item['id']) for kind, collection in (('node', 'nodes'), ('edge', 'edges'))
            for item in graph[collection]]


def _target_ref(selected):
    return selected['revision_ref'] if selected['kind'] == 'author_revision' else selected['net_ref']


def _same_definition(left, right):
    return _target_ref(left) == _target_ref(right)


def _relations(sides, projections, observations, limits):
    relations, presence = [], []
    graphs = {side: sides[side]['graph'] for side in ('left', 'right')}
    all_subjects = {side: _subjects(side, graphs[side]) for side in graphs}
    subject_keys = {side: {(e['subject_kind'], e['subject_id']) for e in values} for side, values in all_subjects.items()}
    def evidence(refs, endpoints, contract):
        rows = []
        for ref in refs:
            reference(ref)
            state = observations.get(ref['source_id'])
            if state is None: raise ComparisonContextError('stale_observation')
            rows.append({'source_id': ref['source_id'], 'evidence_ref': deepcopy(ref),
                         'evidence_kind': 'exact_public_projection', 'selected_endpoints': deepcopy(endpoints),
                         'source_cut': deepcopy(state['cut']), 'access_revision': state['access_revision'],
                         'verification_contract': contract, 'validation': 'verified'})
        return rows
    def append(kind, left, right, refs, claim, contract='rpnh/public_pn_projection/v1', validation='verified'):
        if not left or not right: return
        identity = {'kind': kind, 'left': left, 'right': right, 'refs': refs}
        relations.append({'relation_id': _key(identity), 'relation_kind': kind, 'left': left, 'right': right,
                          'validation': validation, 'semantic_claim': claim,
                          'evidence': evidence(refs, left + right, contract),
                          'coverage': 'complete_relation', 'author_direction': None,
                          'reason_code': 'exact_identity' if claim == 'identity' else
                              'mapping_unsupported' if validation != 'verified' else 'verified_author_mapping'})
    if _same_definition(sides['left']['selected_target'], sides['right']['selected_target']):
        ref = _target_ref(sides['left']['selected_target'])
        for kind, identity in sorted(subject_keys['left'] & subject_keys['right']):
            append('same_exact_subject', [_endpoint('left', graphs['left'], kind, identity)],
                   [_endpoint('right', graphs['right'], kind, identity)], [ref], 'identity')
    else:
        revisions = {side: projections[side].get('author_revision_ref') for side in sides}
        if all(revisions.values()):
            # Only producer-verified direct history. Shared names, common ancestors
            # and multi-generation composition establish no PN correspondence.
            groups = []
            for projection in projections.values():
                for group in projection.get('mapping_groups', []):
                    if _json(group) not in {_json(g) for g in groups}: groups.append(group)
            for group in groups:
                required = ('source_revision_ref', 'target_revision_ref', 'relation_kind', 'source_element_ids',
                            'target_element_ids', 'semantic_claim', 'evidence_refs')
                _object(group, required, ('validation',))
                direct = group['source_revision_ref'] == revisions['left'] and group['target_revision_ref'] == revisions['right']
                reverse = group['source_revision_ref'] == revisions['right'] and group['target_revision_ref'] == revisions['left']
                if not direct and not reverse: continue
                kind = group['relation_kind']
                if kind not in {'retained_author_element', 'copied_from', 'split', 'fusion', 'many_to_many'} or group['semantic_claim'] != 'author_correspondence':
                    raise ComparisonContextError('read_failed')
                selected_ids = {'left': group['source_element_ids'] if direct else group['target_element_ids'],
                                'right': group['target_element_ids'] if direct else group['source_element_ids']}
                regular = kind in {'retained_author_element', 'copied_from'}
                if regular and any(row['kind'] not in {'operation', 'port'}
                    for side in ('left', 'right') for row in projections[side].get('lowering', [])
                    if row['element_id'] in selected_ids[side]):
                    continue
                ends = {}
                for side in ('left', 'right'):
                    _unique(selected_ids[side], strings=True)
                    items = []
                    lowering = projections[side].get('lowering', [])
                    for element_id in selected_ids[side]:
                        lowered = [row for row in lowering if row['element_id'] == element_id]
                        for row in lowered:
                            occurrence(row['occurrence_path'])
                            for subject_kind, collection in ((('node', 'nodes'),) if regular else (('node', 'nodes'), ('edge', 'edges'))):
                                for identity in row[collection]:
                                    if (subject_kind, identity) in subject_keys[side]:
                                        ep = _endpoint(side, graphs[side], subject_kind, identity, row['occurrence_path'])
                                        if ep not in items: items.append(ep)
                    ends[side] = sorted(items, key=_json)
                # A partly visible group never changes its cardinality/meaning.
                # Scope endpoints may omit members only if no group member lowered
                # outside that selected side; otherwise keep the region unmapped.
                complete = True
                for side in ('left', 'right'):
                    for row in projections[side].get('lowering', []):
                        if row['element_id'] in selected_ids[side]:
                            complete &= all((k, i) in subject_keys[side] for k, col in ((('node', 'nodes'),) if regular else (('node', 'nodes'), ('edge', 'edges'))) for i in row[col])
                if not complete: continue
                if reverse and kind in {'split', 'fusion'}:
                    kind = {'split': 'fusion', 'fusion': 'split'}[kind]
                # copied_from retains provenance direction in its evidence refs;
                # relation_kind describes author correspondence, never identity.
                validation = 'unsupported' if kind == 'many_to_many' else group.get('validation', 'verified')
                append(kind, ends['left'], ends['right'], group['evidence_refs'], 'author_correspondence', validation=validation)
                if ends['left'] and ends['right']: relations[-1]['author_direction'] = 'left_to_right' if direct else 'right_to_left'
    # Edge roles are producer-persisted and reader-recomputed declaration
    # ownership. Relate unique roles only through verified operation and port
    # element maps; local names, topology proximity and layouts are irrelevant.
    revisions = {side: projections[side].get('author_revision_ref') for side in sides}
    if all(revisions.values()) and revisions['left'] != revisions['right']:
        element_maps = []
        for projection in projections.values():
            for group in projection.get('mapping_groups', []):
                if group['relation_kind'] not in {'retained_author_element', 'copied_from'}:
                    continue
                direct = group['source_revision_ref'] == revisions['left'] and group['target_revision_ref'] == revisions['right']
                reverse = group['source_revision_ref'] == revisions['right'] and group['target_revision_ref'] == revisions['left']
                if not (direct or reverse) or len(group['source_element_ids']) != 1 or len(group['target_element_ids']) != 1:
                    continue
                a, b = (group['source_element_ids'][0], group['target_element_ids'][0]) if direct else (group['target_element_ids'][0], group['source_element_ids'][0])
                item = (a, b, group['relation_kind'], group['evidence_refs'], 'left_to_right' if direct else 'right_to_left')
                if item not in element_maps: element_maps.append(item)
        by_element = {}
        for item in element_maps: by_element.setdefault(item[0], []).append(item)
        roles = {side: {} for side in sides}
        for side in sides:
            for role in projections[side].get('edge_roles', []):
                _object(role, ('edge_id', 'operation_element_id', 'place_element_ids', 'role'))
                _object(role['role'], ('kind', 'direction', 'mode', 'outcome', 'emit', 'forward_source'))
                _unique(role['place_element_ids'], strings=True)
                key = (role['operation_element_id'], _json(role['role']))
                roles[side].setdefault(key, []).append(role)
        for (operation, role_key), left_roles in roles['left'].items():
            for _, other_operation, kind, proof, direction in by_element.get(operation, []):
                for a in left_roles:
                    candidates = []
                    for b in roles['right'].get((other_operation, role_key), []):
                        matched = {(old, new) for old in a['place_element_ids'] for _, new, *_ in by_element.get(old, []) if new in b['place_element_ids']}
                        if ({old for old, _ in matched} == set(a['place_element_ids'])
                                and {new for _, new in matched} == set(b['place_element_ids'])):
                            candidates.append(b)
                    if len(candidates) != 1: continue
                    b = candidates[0]
                    reverse_candidates = [x for x in left_roles if all(any(item[1] in b['place_element_ids'] for item in by_element.get(old, [])) for old in x['place_element_ids'])]
                    if len(reverse_candidates) != 1: continue
                    if ('edge', a['edge_id']) not in subject_keys['left'] or ('edge', b['edge_id']) not in subject_keys['right']: continue
                    refs = list({_json(ref): ref for ref in proof}.values())
                    append(kind, [_endpoint('left', graphs['left'], 'edge', a['edge_id'])],
                           [_endpoint('right', graphs['right'], 'edge', b['edge_id'])], refs, 'author_correspondence')
                    relations[-1]['author_direction'] = direction
    # Copied origins may legitimately participate in retained + several copy
    # relations. Do not impose one-to-one uniqueness or form a Cartesian product.
    unique = {}
    for relation in relations: unique[relation['relation_id']] = relation
    relations = sorted(unique.values(), key=lambda item: item['relation_id'])
    if len(relations) > limits['max_mapping_relations']: raise ComparisonContextError('scope_limit')
    covered = {side: {(ep['subject_kind'], ep['subject_id']) for r in relations if r['validation'] == 'verified'
                      for ep in r[side]} for side in sides}
    # Explicit author partitions can prove local PN presence only when every
    # relevant declaring owner of that PN subject belongs to the created/removed
    # set. Shared/fused places with any surviving owner remain unknown.
    for projection in projections.values():
        closure = projection.get('mapping_closure')
        if closure is None: continue
        _object(closure, ('source_revision_ref', 'target_revision_ref', 'created_element_ids', 'removed_element_ids', 'coverage', 'evidence_refs'))
        direct = closure['source_revision_ref'] == revisions.get('left') and closure['target_revision_ref'] == revisions.get('right')
        reverse = closure['source_revision_ref'] == revisions.get('right') and closure['target_revision_ref'] == revisions.get('left')
        if not (direct or reverse) or closure['coverage'] != 'complete_author_element_partition': continue
        for ref in closure['evidence_refs']:
            reference(ref)
            if ref['source_id'] not in observations: raise ComparisonContextError('stale_observation')
        if not closure['evidence_refs']: continue
        for side in sides:
            field = 'removed_element_ids' if (side == 'left') == direct else 'created_element_ids'
            changed = set(_unique(closure[field], strings=True))
            if not changed: continue
            lowering = projections[side].get('lowering', [])
            absent_keys = set()
            for node in graphs[side]['nodes']:
                kind = 'operation' if node['kind'] == 'transition' else 'port'
                owners = {row['element_id'] for row in lowering if row['kind'] == kind and node['id'] in row['nodes']}
                if owners and owners <= changed: absent_keys.add(('node', node['id']))
            for role in projections[side].get('edge_roles', []):
                if role['operation_element_id'] in changed or set(role['place_element_ids']) and set(role['place_element_ids']) <= changed:
                    absent_keys.add(('edge', role['edge_id']))
            for kind, identity in sorted((absent_keys & subject_keys[side]) - covered[side]):
                subject = _endpoint(side, graphs[side], kind, identity)
                change = {'change_id': _key({'subject': subject, 'proof': closure['evidence_refs']}), 'subject': subject,
                    'state': 'present_left_only' if side == 'left' else 'present_right_only', 'reason_code': 'explicit_presence',
                    'absence_evidence_refs': deepcopy(closure['evidence_refs']), 'validation': 'verified'}
                if change not in presence: presence.append(change)
                covered[side].add((kind, identity))
    unmapped = {side: {'scope_key': graphs[side]['scope_key'],
                      'subjects': [ep for ep in all_subjects[side] if (ep['subject_kind'], ep['subject_id']) not in covered[side]],
                      'coverage': 'complete_in_declared_scope'} for side in sides}
    complete = all(covered[side] == subject_keys[side] for side in sides)
    any_verified = any(r['validation'] == 'verified' for r in relations)
    # Empty unrelated graphs have no correspondence proof; exact empty nets do.
    complete &= not any(r['validation'] == 'conflicting' for r in relations)
    complete &= any_verified or bool(presence) or _same_definition(sides['left']['selected_target'], sides['right']['selected_target'])
    coverage = 'complete_in_declared_scope' if complete else 'partial' if any_verified or presence else 'none'
    return {'contract_version': 'rpnh/comparison_mapping/v1', 'coverage': coverage,
            'relations': relations, 'explicit_presence_changes': presence, 'unmapped': unmapped,
            'reason_codes': ['exact_identity' if complete and all(r['semantic_claim'] == 'identity' for r in relations)
                             else 'verified_author_mapping' if any_verified else 'explicit_presence' if presence else 'mapping_missing']}


def _provided(value, refs=()):
    return {'state': 'provided', 'value': deepcopy(value), 'evidence_refs': deepcopy(list(refs))}


def _unknown(reason='not_provided'):
    return {'state': 'unknown', 'reason_code': reason}


def _row(scope_key, relation_id, path, left, right, *, applicable=True, reason='exact_identity', comparator='rpnh/json_equal/v1'):
    classification = 'unknown'
    if applicable and left['state'] == right['state'] == 'provided':
        classification = 'known_same' if _json(left['value']) == _json(right['value']) else 'known_changed'
    elif applicable and {left['state'], right['state']} == {'provided', 'absent_in_complete_scope'}:
        classification = 'known_changed'
    if classification == 'unknown':
        reason = next((f['reason_code'] for f in (left, right) if f['state'] == 'unknown'),
                      'comparator_unsupported' if not applicable else 'not_provided')
    return {'row_id': _key({'scope': scope_key, 'relation': relation_id, 'path': path}),
            'subject_relation_id': relation_id, 'subject_scope': scope_key, 'field_path': path,
            'comparator_contract': comparator, 'left_fact': left, 'right_fact': right,
            'classification': classification, 'reason_code': reason,
            'evidence_refs': list({ _json(ref): ref for fact in (left, right) for ref in fact.get('evidence_refs', fact.get('absence_evidence_refs', []))}.values()),
            'interpretation': 'current_analysis'}


def _axis(axis, scope_key, rows, coverage, reasons):
    visible = coverage not in {'not_provided', 'not_requested'}
    counts = {'scope_key': scope_key, 'unit': 'field_rows', 'loaded_count': len(rows) if visible else None,
              'total_count': len(rows) if coverage == 'complete_in_declared_scope' else None,
              **{key: sum(row['classification'] == key for row in rows) if visible else None
                 for key in ('known_changed', 'known_same', 'unknown')},
              'coverage': coverage if coverage != 'not_requested' else 'not_provided'}
    return {'axis': axis, 'coverage': coverage, 'scope_key': scope_key, 'rows': rows,
            'counts': counts, 'unknown_reasons': sorted(set(reasons))}


def _definition_rows(sides, mapping, scope_key):
    rows = []
    def lookup(side, ep):
        collection = 'nodes' if ep['subject_kind'] == 'node' else 'edges'
        return next(item for item in sides[side]['graph'][collection] if item['id'] == ep['subject_id'])
    for relation in mapping['relations']:
        applicable = relation['validation'] == 'verified' and len(relation['left']) == len(relation['right']) == 1
        if not applicable:
            rows.append(_row(scope_key, relation['relation_id'], 'definition.group',
                             _provided(relation['left']), _provided(relation['right']), applicable=False))
            continue
        l, r = lookup('left', relation['left'][0]), lookup('right', relation['right'][0])
        refs = [e['evidence_ref'] for e in relation['evidence']]
        for field in sorted((l.keys() | r.keys()) - {'id'}):
            # Arc endpoint local IDs are not cross-net identity. Compare through
            # verified node correspondence, retaining direction.
            a = _provided(l[field], refs) if field in l else _unknown()
            b = _provided(r[field], refs) if field in r else _unknown()
            if field in {'source', 'target'} and relation['semantic_claim'] != 'identity':
                linked = any(x['validation'] == 'verified' and len(x['left']) == len(x['right']) == 1
                    and x['left'][0]['subject_kind'] == x['right'][0]['subject_kind'] == 'node'
                    and x['left'][0]['subject_id'] == l.get(field) and x['right'][0]['subject_id'] == r.get(field)
                    for x in mapping['relations'])
                if linked: a = _provided('verified_corresponding_endpoint', refs); b = _provided('verified_corresponding_endpoint', refs)
                else: a = _unknown('mapping_missing'); b = _unknown('mapping_missing')
            rows.append(_row(scope_key, relation['relation_id'], 'definition.' + field, a, b,
                             reason=relation['reason_code']))
    for change in mapping['explicit_presence_changes']:
        side, ep = change['subject']['side'], change['subject']
        item = lookup(side, ep)
        for field, value in sorted(item.items()):
            if field == 'id': continue
            facts = {side: _provided(value, change['absence_evidence_refs']),
                'right' if side == 'left' else 'left': {'state': 'absent_in_complete_scope', 'absence_evidence_refs': deepcopy(change['absence_evidence_refs'])}}
            rows.append(_row(scope_key, None, 'definition.presence.' + change['change_id'] + '.' + field,
                             facts['left'], facts['right'], reason='explicit_presence'))
    for side in ('left', 'right'):
        for ep in mapping['unmapped'][side]['subjects']:
            item = lookup(side, ep)
            for field, value in sorted(item.items()):
                if field == 'id': continue
                facts = {side: _provided(value), 'right' if side == 'left' else 'left': _unknown('mapping_missing')}
                rows.append(_row(scope_key, None, 'definition.unmapped.' + side + '.' + ep['subject_kind'] + '.' + ep['subject_id'] + '.' + field,
                                 facts['left'], facts['right'], applicable=False))
    return rows


def _axes(sides, projections, mapping, requested, materials, limits):
    scope_key = _key({side: sides[side]['graph']['scope_key'] for side in sides})
    axes = {}
    for axis in AXES:
        if axis not in requested:
            axes[axis] = _axis(axis, scope_key, [], 'not_requested', ['not_requested']); continue
        rows, coverage = [], 'not_provided'
        if axis == 'definition':
            rows = _definition_rows(sides, mapping, scope_key)
            coverage = 'complete_in_declared_scope' if mapping['coverage'] == 'complete_in_declared_scope' else 'partial'
        elif axis == 'configuration':
            # Explicit scalar configuration whitelist. No host paths, credential
            # references, binding digests or arbitrary provider config objects.
            for field in ('host_requirements_ref', 'environment_requirements_ref', 'declared_configuration_ref'):
                facts = {}
                for side in sides:
                    config = projections[side].get('configuration', {})
                    if field in config:
                        reference(config[field]); facts[side] = _provided(config[field])
                    else: facts[side] = _unknown()
                if any(f['state'] == 'provided' for f in facts.values()):
                    rows.append(_row(scope_key, None, 'configuration.' + field, facts['left'], facts['right'],
                                     applicable=mapping['coverage'] == 'complete_in_declared_scope'))
            if rows: coverage = 'partial'
        elif axis == 'materials':
            keys = sorted(set(materials['left']) | set(materials['right']))
            for key in keys:
                for field in ('resource_ref', 'headers', 'body_digest'):
                    facts = {side: materials[side].get(key, {}).get(field, _unknown('mapping_missing')) for side in sides}
                    rows.append(_row(scope_key, None, 'materials.' + _key(key) + '.' + field, facts['left'], facts['right']))
            if rows: coverage = 'partial'
        elif axis == 'runtime':
            same_net = _same_definition(sides['left']['selected_target'], sides['right']['selected_target'])
            same_scope = same_net and sides['left']['scope_resolution']['member_node_ids'] == sides['right']['scope_resolution']['member_node_ids']
            runtime = {side: projections[side].get('runtime') for side in sides}
            if any(runtime.values()):
                coverage = 'partial'
                for field in ('epoch', 'token_count', 'active_token_count'):
                    facts = {side: _provided(runtime[side]['marking'][field]) if runtime[side] and field in runtime[side].get('marking', {})
                             else _unknown('not_captured') for side in sides}
                    rows.append(_row(scope_key, None, 'runtime.marking.' + field, facts['left'], facts['right'], applicable=same_scope))
                tokens = {side: {_json(t['token_ref']): t for t in runtime[side].get('tokens', [])} if runtime[side] else {} for side in sides}
                for key in sorted(set(tokens['left']) | set(tokens['right'])):
                    for field in ('place', 'kind', 'resource_ref', 'active_in_checkpoint'):
                        facts = {side: _provided(tokens[side][key][field]) if key in tokens[side] and field in tokens[side][key]
                                 else _unknown('not_captured' if not runtime[side] else 'mapping_missing') for side in sides}
                        rows.append(_row(scope_key, None, 'runtime.token.' + _key(key) + '.' + field,
                                         facts['left'], facts['right'], applicable=same_net))
                rows.append(_row(scope_key, None, 'runtime.firing_activity_completion',
                                 _unknown('not_captured'), _unknown('not_captured')))
        reasons = [row['reason_code'] for row in rows if row['classification'] == 'unknown']
        if not rows: reasons = ['not_provided']
        if axis == 'runtime' and coverage == 'partial': reasons.append('not_captured')
        axes[axis] = _axis(axis, scope_key, rows, coverage, reasons)
    if sum(len(a['rows']) for a in axes.values()) > limits['max_field_rows']:
        raise ComparisonContextError('scope_limit')
    return axes



def _validate_runtime(runtime, chosen, all_nodes):
    if runtime.get('coverage') == 'not_provided':
        _object(runtime, ('coverage',))
        if chosen['kind'] == 'checkpoint': raise ComparisonContextError('projection_unavailable')
        return
    if chosen['kind'] != 'checkpoint' or runtime.get('coverage') != 'selected_checkpoint_marking':
        raise ComparisonContextError('read_failed')
    _object(runtime, ('coverage', 'marking', 'tokens'))
    marking = _object(runtime['marking'], ('epoch', 'token_count', 'active_token_count'))
    for count in marking.values(): _int(count)
    identities = set()
    for token in _array(runtime['tokens']):
        _object(token, ('token_ref', 'place', 'kind', 'resource_ref', 'active_in_checkpoint'))
        reference(token['token_ref'], source_id=chosen['source_id'], entity_type='petri_token/v1')
        key = _json(token['token_ref'])
        if key in identities or token['place'] not in all_nodes or type(token['active_in_checkpoint']) is not bool:
            raise ComparisonContextError('read_failed')
        identities.add(key)
        if token['kind'] is not None: _text(token['kind'])
        if token['resource_ref'] is not None: reference(token['resource_ref'])
    if marking['token_count'] != len(identities) or marking['active_token_count'] != sum(t['active_in_checkpoint'] for t in runtime['tokens']):
        raise ComparisonContextError('read_failed')


def _qualified(value):
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.identities import TypedId
    reference(value)
    ref = value['ref']
    if 'entity_type' in ref:
        return SourceQualifiedVersionRef(value['source_id'], VersionRef(ref['entity_type'],
                TypedId.parse(ref['logical_id']), TypedId.parse(ref['version_id'])))
    return SourceQualifiedResourceRef(value['source_id'], ResourceVersionRef(
        TypedId.parse(ref['resource_id'], expected='resource'), TypedId.parse(ref['resource_version_id'], expected='resource_version')))


def _session_error(error):
    code = getattr(error, 'code', '')
    if code in {'ACCESS_CHANGED', 'SESSION_EXPIRED', 'SESSION_CLOSED', 'NOT_SELECTED', 'NOT_AUTHORIZED'}:
        return ComparisonContextError('access_changed')
    if code in {'CURSOR_MISMATCH', 'STALE_OBSERVATION', 'CUT_UNAVAILABLE'}:
        return ComparisonContextError('stale_observation')
    if code in {'PROJECTION_UNAVAILABLE', 'NOT_DISCLOSED', 'PAYLOAD_NOT_DISCLOSED', 'MATERIAL_ACCESS_NOT_GRANTED', 'GOVERNED_DELIVERY_REQUIRED'}:
        return ComparisonContextError('projection_unavailable')
    if code in {'UNSUPPORTED_READER_VERSION', 'UNSUPPORTED_ENTRY_TYPE', 'UNSUPPORTED_PROJECTION'}:
        return ComparisonContextError('unsupported')
    if 'LIMIT' in code: return ComparisonContextError('scope_limit')
    return ComparisonContextError('read_failed')


def _material_facts(session, projection, cuts, observations, budget):
    facts = {}
    runtime = projection.get('runtime') or {}
    for token in runtime.get('tokens', []):
        resource = token.get('resource_ref')
        if resource is None: continue
        # Only exact token material refs, never graph edge.resource (a Boolean).
        reference(resource)
        key = _json(resource)
        if key in facts: continue
        if key in budget['cache']:
            facts[key] = deepcopy(budget['cache'][key]); continue
        if budget['reads'] < 1: raise ComparisonContextError('scope_limit')
        budget['reads'] -= 1
        sid = resource['source_id']
        if sid not in cuts: raise ComparisonContextError('stale_observation')
        row = {'resource_ref': _provided(resource, [resource]), 'headers': _unknown('not_disclosed'),
               'body_digest': _unknown('not_disclosed')}
        try:
            envelope = session.read_exact(entry_ref=_qualified(resource), at_cut=cuts[sid],
                projection=('media_type', 'content_schema_ref', 'byte_count', 'summary'))
        except Exception as exc:
            from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
            if not isinstance(exc, RegistryReadSessionError) or exc.code != 'NOT_DISCLOSED': raise
            envelope = {'status': 'not_disclosed'}
        if envelope.get('status') == 'ok':
            if envelope.get('entry_ref') != resource or envelope.get('source_cut') != cuts[sid].to_dict(): raise ComparisonContextError('read_failed')
            record = envelope['record']
            headers = {key: deepcopy(record[key]) for key in ('media_type', 'content_schema_ref', 'byte_count', 'summary') if key in record}
            row['headers'] = _provided(headers, [resource])
        elif envelope.get('status') not in {'not_disclosed', 'unsupported', 'not_provided'}:
            raise ComparisonContextError('read_failed')
        # Read permission is separately evaluated; an initially denied optional
        # body is unknown. A guard change is never swallowed as optional denial.
        try:
            if budget['reads'] < 1 or budget['bytes'] < 1:
                body = {'status': 'scope_limit'}
            else:
                budget['reads'] -= 1
                body = session.read_material(resource_ref=_qualified(resource), at_cut=cuts[sid], max_bytes=min(1048576, budget['bytes']))
        except Exception as exc:
            from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
            if not isinstance(exc, RegistryReadSessionError) or exc.code not in {'MATERIAL_ACCESS_NOT_GRANTED', 'GOVERNED_DELIVERY_REQUIRED', 'MATERIAL_TOO_LARGE'}:
                raise
            body = {'status': 'not_disclosed'}
        if body.get('status') == 'ok':
            if body.get('resource_ref') != resource or body.get('source_cut') != cuts[sid].to_dict(): raise ComparisonContextError('read_failed')
            budget['bytes'] -= body['byte_count']
            if budget['bytes'] < 0: raise ComparisonContextError('scope_limit')
            digest = body.get('sha256')
            if type(digest) is str:
                row['body_digest'] = _provided(digest, [resource])
        elif body.get('status') not in {'not_disclosed', 'unsupported', 'not_provided', 'scope_limit'}:
            raise ComparisonContextError('read_failed')
        if body.get('status') == 'scope_limit': row['body_digest'] = _unknown('scope_limit')
        facts[key] = row
        budget['cache'][key] = deepcopy(row)
    return facts


def comparison_context(session, request):
    """Read both selected sides and all evidence through one public session."""
    selected = validate_request(request)
    try:
        description = session.describe()
        if selected['session_id'] != description['session_id']:
            raise ComparisonContextError('access_changed')
        limits = dict(selected['limits'])
        configured = description.get('effective_limits', {})
        for key in limits:
            if key in configured: limits[key] = min(limits[key], configured[key])
        sources = {selected[side]['source_id'] for side in ('left', 'right')}
        cuts = {}
        if selected['source_cuts'] is None:
            for sid in sorted(sources): cuts[sid] = session.capture_cut(sid)
        else:
            for sid, value in selected['source_cuts'].items():
                cuts[sid] = session.validate_cut(value)
        observations = {}
        for sid, cut in cuts.items():
            raw_state = session.source_state(cut)
            observations[sid] = {key: deepcopy(raw_state[key]) for key in ('source_id', 'source_ref', 'cut', 'access_revision', 'access_state', 'coverage')}
            observations[sid]['coverage'] = {'state': 'selected_exact_projection', 'loaded_count': None, 'total_count': None}
        projections, sides = {}, {}
        for side in ('left', 'right'):
            chosen = selected[side]; sid = chosen['source_id']
            if chosen['kind'] == 'checkpoint' and chosen['checkpoint_commit_ordinal'] > cuts[sid].head.ordinal:
                raise ComparisonContextError()
            read_ref = chosen['checkpoint_ref'] if chosen['kind'] == 'checkpoint' else _target_ref(chosen)
            envelope = session.read_public_projection(entry_ref=_qualified(read_ref), at_cut=cuts[sid])
            if envelope.get('status') != 'ok': raise ComparisonContextError('projection_unavailable')
            if envelope.get('entry_ref') != read_ref or envelope.get('source_cut') != cuts[sid].to_dict():
                raise ComparisonContextError('read_failed')
            projection = envelope['record']
            if projection.get('schema_version') != 'rpnh/public_pn_projection/v1' or projection.get('target_ref') != read_ref:
                raise ComparisonContextError('read_failed')
            if chosen['kind'] == 'checkpoint':
                if projection.get('net_ref') != chosen['net_ref'] or projection.get('checkpoint_commit_ordinal') != chosen['checkpoint_commit_ordinal']:
                    raise ComparisonContextError('read_failed')
            graph, resolution, hierarchy = _scope(projection, chosen, selected['scope'][side], limits)
            # Runtime counts are local to the exact selected member nodes.
            projection = deepcopy(projection)
            runtime = projection.get('runtime') or {}
            _validate_runtime(runtime, chosen, {n['id'] for n in projection['nodes']})
            if runtime.get('coverage') == 'not_provided': projection['runtime'] = None
            elif runtime:
                members = set(resolution['member_node_ids'])
                tokens = [t for t in runtime.get('tokens', []) if t['place'] in members]
                projection['runtime']['tokens'] = tokens
                if 'marking' in runtime:
                    projection['runtime']['marking']['token_count'] = len(tokens)
                    projection['runtime']['marking']['active_token_count'] = sum(t['active_in_checkpoint'] for t in tokens)
            provenance = [read_ref]
            mapping_refs = [projection['projection_ref']] if projection.get('projection_ref') else []
            sides[side] = {'selected_target': deepcopy(chosen), 'source_observation_key': _key(observations[sid]),
                'scope_resolution': resolution, 'graph': graph, 'hierarchy_navigation': hierarchy,
                'public_definition_provenance': {'descriptor_refs': provenance, 'mapping_refs': mapping_refs, 'status': 'verified'},
                'axis_input_coverage': {axis: {'coverage': 'complete_in_declared_scope' if axis == 'definition'
                    else 'partial' if axis == 'runtime' and projection.get('runtime') or axis == 'configuration' and projection.get('configuration') else 'not_provided',
                    'reason_codes': [] if axis == 'definition' else ['not_captured'] if axis == 'runtime' else ['not_provided']}
                    for axis in AXES}}
            projections[side] = projection
        for pair in selected['visual_pairs']:
            for side in ('left', 'right'):
                allowed = {(ep['subject_kind'], ep['subject_id']) for ep in _subjects(side, sides[side]['graph'])}
                for ep in pair[side]:
                    if (ep['subject_kind'], ep['subject_id']) not in allowed:
                        raise ComparisonContextError()
                    paths = { _json(row['occurrence_path']) for row in projections[side].get('lowering', [])
                              if ep['subject_id'] in row['nodes' if ep['subject_kind'] == 'node' else 'edges']}
                    if ep['occurrence_path'] and _json(ep['occurrence_path']) not in paths:
                        raise ComparisonContextError()
        mapping = _relations(sides, projections, observations, limits)
        evidence_refs = {_json(e['evidence_ref']) for relation in mapping['relations'] for e in relation['evidence']}
        evidence_refs.update(_json(ref) for change in mapping['explicit_presence_changes'] for ref in change['absence_evidence_refs'])
        if len(evidence_refs) > limits['max_evidence_reads']: raise ComparisonContextError('scope_limit')
        budget = {'reads': limits['max_evidence_reads'] - len(evidence_refs), 'bytes': limits['max_response_bytes'], 'cache': {}}
        materials = {side: _material_facts(session, projections[side], cuts, observations, budget)
                     if 'materials' in selected['axes'] else {} for side in sides}
        for side in sides:
            if materials[side]: sides[side]['axis_input_coverage']['materials'] = {'coverage': 'partial', 'reason_codes': ['not_disclosed'] if any(f['body_digest']['state'] == 'unknown' for f in materials[side].values()) else []}
        axes = _axes(sides, projections, mapping, selected['axes'], materials, limits)
        cuts_wire = {sid: cut.to_dict() for sid, cut in cuts.items()}
        recommended = {'complete_in_declared_scope': 'reliable_diff', 'partial': 'partial_mapping', 'none': 'full_pair'}[mapping['coverage']]
        echo = {key: deepcopy(selected[key]) for key in ('left', 'right', 'scope', 'axes', 'view_preference', 'visual_pairs')}
        echo['source_cuts'] = cuts_wire
        result = {'schema_version': SCHEMA, 'client_request_id': selected['client_request_id'],
            'context_id': _key({'request': echo, 'observations': observations}), 'purpose': 'descriptive_read_only',
            'request_echo': echo, 'global_atomic_snapshot': False, 'publication': 'complete_pair',
            'sources': [{'observation_key': _key(state), 'source_state': state, 'final_recheck': 'passed'} for state in observations.values()],
            'left': sides['left'], 'right': sides['right'], 'mapping': mapping,
            'presentation': {'recommended_mode': recommended,
                'display_mode': 'full_pair' if selected['view_preference'] == 'full_pair' else recommended,
                'full_pair_navigation': {'availability': 'available' if selected['scope']['kind'] == 'full_pair' else 'requires_read',
                    'reason_code': 'exact_identity', 'targets': {side: deepcopy(selected[side]) for side in sides},
                    'source_cuts': cuts_wire, 'scope': {'kind': 'full_pair', 'left': {'kind': 'full_net'}, 'right': {'kind': 'full_net'}},
                    'view_preference': 'full_pair'}},
            'axes': axes, 'comparability': 'not_established', 'authority': 'display_only'}
        validate_comparison_context(result, selected)
        if len(_json(result).encode()) > limits['max_response_bytes']: raise ComparisonContextError('scope_limit')
        session.final_recheck(tuple(cuts))
        return result
    except ComparisonContextError:
        raise
    except Exception as exc:
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        if isinstance(exc, RegistryReadSessionError): raise _session_error(exc) from exc
        raise ComparisonContextError('read_failed') from exc


class ComparisonProvider:
    """Independent read-only viewer; deliberately has no raw-net __call__."""
    comparison_only = True

    def __init__(self, session):
        from threading import RLock
        self.session = session
        self._selection_pages = {}
        self._lock = RLock()

    def comparison_context(self, request):
        with self._lock:
            return comparison_context(self.session, request)

    def comparison_selection(self, *, cursor=None):
        from cpn.rpnh.collaboration.registry_read_contracts import IndexQuery, TypedIndexClause
        import secrets
        with self._lock:
            description = self.session.describe()
            if cursor is None:
                cuts, specs = {}, []
                for state in description['sources']:
                    sid = state['source_id']
                    allowed = state.get('capabilities', {}).get('index_fields', {})
                    clauses = []
                    for kind in ('net_instance/v1', 'marking_checkpoint/v1', 'collaboration_net_revision/v1', 'collaboration_assembly_revision/v9'):
                        fields = allowed.get(kind, [])
                        if not fields: continue
                        if kind == 'marking_checkpoint/v1':
                            net_field = next((f for f in ('net_ref', 'net_instance_ref') if f in fields), None)
                            ordinal_field = next((f for f in ('checkpoint_commit_ordinal', 'commit_ordinal') if f in fields), None)
                            if net_field is None or ordinal_field is None: continue
                            selected_fields = (net_field, ordinal_field)
                        else:
                            selected_fields = (fields[0],)
                        clauses.append(TypedIndexClause(kind, projection=selected_fields))
                    if not clauses: continue
                    cut = self.session.capture_cut(sid)
                    cuts[sid] = cut
                    specs.append(IndexQuery((sid,), tuple(clauses), page_size=min(100, description['effective_limits']['max_page_size']), cuts={sid: cut}))
                state = {'specs': specs, 'index': 0, 'cursor': None, 'cuts': cuts, 'response': None}
            else:
                _text(cursor, 256)
                state = self._selection_pages.get(cursor)
                if state is None: raise ComparisonContextError('stale_observation')
            self.session.final_recheck(tuple(state['cuts']))
            if state['response'] is not None: return deepcopy(state['response'])
            entries, next_state = [], None
            if state['specs']:
                page = self.session.query_index(state['specs'][state['index']], cursor=state['cursor'])
                entries = page['entries']
                if page['continuation'] is not None:
                    next_state = {**state, 'cursor': page['continuation'], 'response': None}
                elif state['index'] + 1 < len(state['specs']):
                    next_state = {**state, 'index': state['index'] + 1, 'cursor': None, 'response': None}
            targets = []
            for row in entries:
                ref, kind = row['entry_ref'], row['entry_type']
                chosen = {'kind': 'net_instance', 'source_id': ref['source_id'], 'net_ref': ref} if kind == 'net_instance/v1' else \
                    {'kind': 'author_revision', 'source_id': ref['source_id'], 'revision_ref': ref} if kind in {'collaboration_net_revision/v1', 'collaboration_assembly_revision/v9'} else None
                if kind == 'marking_checkpoint/v1':
                    record = row['fields']
                    net = record.get('net_ref', record.get('net_instance_ref'))
                    ordinal = record.get('checkpoint_commit_ordinal', record.get('commit_ordinal'))
                    if net is not None and ordinal is not None:
                        if 'source_id' not in net:
                            net = {'schema_version': 'rpnh/collaboration/source_version_ref/v1', 'source_id': ref['source_id'], 'ref': net}
                        chosen = {'kind': 'checkpoint', 'source_id': ref['source_id'], 'net_ref': net,
                                 'checkpoint_ref': ref, 'checkpoint_commit_ordinal': ordinal}
                if chosen is not None:
                    target(chosen)
                    targets.append({'target': chosen, 'label': f"{chosen['source_id']} · {chosen['kind']} · {ref['ref']['version_id']}", 'read_state': 'not_read'})
            continuation = None
            if next_state:
                if len(self._selection_pages) >= 128: raise ComparisonContextError('scope_limit')
                continuation = 'selection_' + secrets.token_urlsafe(24)
                self._selection_pages[continuation] = next_state
            response = {'schema_version': 'rpnh/comparison_selection/v1', 'session_id': description['session_id'],
                'targets': targets, 'source_cuts': {sid: cut.to_dict() for sid, cut in state['cuts'].items()},
                'next_cursor': continuation,
                'limits': {key: min(value, description.get('effective_limits', {}).get(key, value)) for key, value in LIMITS.items()}}
            self.session.final_recheck(tuple(state['cuts']))
            state['response'] = deepcopy(response)
            return response


def _fact(value):
    if type(value) is not dict: raise ComparisonContextError('invalid_response')
    state = value.get('state')
    if state == 'provided':
        _object(value, ('state', 'value', 'evidence_refs'))
        _json(value['value'])
        for ref in _unique(value['evidence_refs']): reference(ref)
    elif state == 'unknown':
        _object(value, ('state', 'reason_code'))
        if value['reason_code'] not in REASONS: raise ComparisonContextError('invalid_response')
    elif state == 'absent_in_complete_scope':
        _object(value, ('state', 'absence_evidence_refs'))
        if not _unique(value['absence_evidence_refs']): raise ComparisonContextError('invalid_response')
        for ref in value['absence_evidence_refs']: reference(ref)
    else: raise ComparisonContextError('invalid_response')


def validate_comparison_context(value, request):
    """Closed response validation with recomputed scope, modes and row counts."""
    request = validate_request(request)
    _object(value, ('schema_version', 'client_request_id', 'context_id', 'purpose', 'request_echo',
                    'global_atomic_snapshot', 'publication', 'sources', 'left', 'right', 'mapping',
                    'presentation', 'axes', 'comparability', 'authority'))
    if (value['schema_version'] != SCHEMA or value['client_request_id'] != request['client_request_id']
        or value['purpose'] != 'descriptive_read_only' or value['global_atomic_snapshot'] is not False
        or value['publication'] != 'complete_pair' or value['comparability'] != 'not_established'
        or value['authority'] != 'display_only'):
        raise ComparisonContextError('invalid_response')
    _text(value['context_id'])
    echo = _object(value['request_echo'], ('left', 'right', 'source_cuts', 'scope', 'axes', 'view_preference', 'visual_pairs'))
    for key in ('left', 'right', 'scope', 'axes', 'view_preference', 'visual_pairs'):
        if echo[key] != request[key]: raise ComparisonContextError('invalid_response')
    if request['source_cuts'] is not None and echo['source_cuts'] != request['source_cuts']:
        raise ComparisonContextError('invalid_response')
    observations = {}
    for observed in _array(value['sources'], 128):
        _object(observed, ('observation_key', 'source_state', 'final_recheck'))
        if observed['final_recheck'] != 'passed': raise ComparisonContextError('invalid_response')
        state = _object(observed['source_state'], ('source_id', 'source_ref', 'cut', 'access_revision', 'access_state', 'coverage'))
        sid = state['source_id']; reference(state['source_ref'], source_id=sid, entity_type='task/v1')
        source_cut(state['cut']); _text(state['access_revision']); _text(state['access_state'])
        _object(state['coverage'], ('state', 'loaded_count', 'total_count'))
        _text(state['coverage']['state'])
        for count in ('loaded_count', 'total_count'):
            if state['coverage'][count] is not None: _int(state['coverage'][count])
        if sid in observations or sid != state['cut']['source_id'] or echo['source_cuts'].get(sid) != state['cut'] or observed['observation_key'] != _key(state):
            raise ComparisonContextError('invalid_response')
        observations[sid] = state
    if set(observations) != set(echo['source_cuts']): raise ComparisonContextError('invalid_response')
    subjects = {}
    for side in ('left', 'right'):
        current = _object(value[side], ('selected_target', 'source_observation_key', 'scope_resolution', 'graph',
            'public_definition_provenance', 'axis_input_coverage', 'hierarchy_navigation'))
        if current['selected_target'] != request[side] or current['source_observation_key'] != _key(observations[request[side]['source_id']]):
            raise ComparisonContextError('invalid_response')
        graph = _object(current['graph'], ('schema_version', 'target_key', 'scope_key', 'nodes', 'edges', 'boundary_edges', 'topology_coverage'))
        scope_key = _key({'target': request[side], 'scope': request['scope'][side]})
        if (graph['schema_version'] != 'rpnh/comparison_pn/v1' or graph['target_key'] != _key(request[side])
            or graph['scope_key'] != scope_key or graph['topology_coverage'] != 'complete_in_declared_scope'):
            raise ComparisonContextError('invalid_response')
        _projection_graph(graph)
        resolution = _object(current['scope_resolution'], ('scope_key', 'requested_scope', 'resolved_scope',
            'member_node_ids', 'member_edge_ids', 'boundary_edges', 'membership_evidence_refs', 'coverage'))
        if (resolution['scope_key'] != scope_key or resolution['requested_scope'] != request['scope'][side]
            or resolution['resolved_scope'] != request['scope'][side]
            or resolution['member_node_ids'] != sorted(n['id'] for n in graph['nodes'])
            or resolution['member_edge_ids'] != sorted(e['id'] for e in graph['edges'])
            or resolution['coverage'] != 'complete_in_declared_scope' or resolution['boundary_edges'] != graph['boundary_edges']):
            raise ComparisonContextError('invalid_response')
        for ref in _unique(resolution['membership_evidence_refs']): reference(ref)
        if request['scope'][side]['kind'] == 'nodes' and set(resolution['member_node_ids']) != set(request['scope'][side]['node_ids']):
            raise ComparisonContextError('invalid_response')
        boundary_ids = set()
        for boundary in _array(graph['boundary_edges']):
            _object(boundary, ('edge_id', 'inside_node_id', 'outside_endpoint', 'direction'))
            _text(boundary['edge_id'])
            if boundary['edge_id'] in boundary_ids or boundary['edge_id'] in resolution['member_edge_ids'] or boundary['inside_node_id'] not in resolution['member_node_ids'] or boundary['direction'] not in {'incoming', 'outgoing'}:
                raise ComparisonContextError('invalid_response')
            boundary_ids.add(boundary['edge_id'])
            outside = boundary['outside_endpoint']
            if outside.get('state') == 'provided':
                _object(outside, ('state', 'node_id')); _text(outside['node_id'])
                if outside['node_id'] in resolution['member_node_ids']: raise ComparisonContextError('invalid_response')
            else:
                _object(outside, ('state', 'reason_code'))
                if outside['state'] != 'unknown' or outside['reason_code'] not in REASONS: raise ComparisonContextError('invalid_response')
        if request['scope'][side]['kind'] == 'full_net' and boundary_ids:
            raise ComparisonContextError('invalid_response')
        hierarchy = _object(current['hierarchy_navigation'], ('coverage', 'scopes'))
        if hierarchy['coverage'] not in {'provided', 'not_provided'} or hierarchy['coverage'] == 'not_provided' and hierarchy['scopes']:
            raise ComparisonContextError('invalid_response')
        for h in _array(hierarchy['scopes']):
            _object(h, ('scope', 'parent_scope', 'label', 'node_ids', 'edge_ids', 'evidence_refs'))
            side_scope(h['scope']); _text(h['label']); _unique(h['node_ids'], strings=True); _unique(h['edge_ids'], strings=True)
            if h['parent_scope'] is not None: side_scope(h['parent_scope'])
            for ref in _unique(h['evidence_refs']): reference(ref)
        provenance = _object(current['public_definition_provenance'], ('descriptor_refs', 'mapping_refs', 'status'))
        if provenance['status'] not in {'verified', 'partial', 'not_provided'}: raise ComparisonContextError('invalid_response')
        for field in ('descriptor_refs', 'mapping_refs'):
            for ref in _unique(provenance[field]): reference(ref)
        _object(current['axis_input_coverage'], AXES)
        for item in current['axis_input_coverage'].values():
            _object(item, ('coverage', 'reason_codes'))
            if item['coverage'] not in COVERAGES or not set(_unique(item['reason_codes'])) <= REASONS: raise ComparisonContextError('invalid_response')
        subjects[side] = {(ep['subject_kind'], ep['subject_id']) for ep in _subjects(side, graph)}
    mapping = _object(value['mapping'], ('contract_version', 'coverage', 'relations', 'explicit_presence_changes', 'unmapped', 'reason_codes'))
    if mapping['contract_version'] != 'rpnh/comparison_mapping/v1' or mapping['coverage'] not in {'complete_in_declared_scope', 'partial', 'none'}:
        raise ComparisonContextError('invalid_response')
    if not set(_unique(mapping['reason_codes'])) <= REASONS: raise ComparisonContextError('invalid_response')
    relation_ids, covered = set(), {'left': set(), 'right': set()}
    for relation in _array(mapping['relations']):
        _object(relation, ('relation_id', 'relation_kind', 'left', 'right', 'validation', 'semantic_claim', 'evidence', 'coverage', 'reason_code', 'author_direction'))
        _text(relation['relation_id'])
        if relation['author_direction'] not in {None, 'left_to_right', 'right_to_left'}: raise ComparisonContextError('invalid_response')
        if relation['relation_id'] in relation_ids: raise ComparisonContextError('invalid_response')
        relation_ids.add(relation['relation_id'])
        if relation['relation_kind'] not in {'same_exact_subject', 'retained_author_element', 'copied_from', 'split', 'fusion', 'many_to_many'} or relation['validation'] not in {'verified', 'unverified', 'unsupported', 'conflicting'} or relation['semantic_claim'] not in {'identity', 'author_correspondence'} or relation['coverage'] not in {'complete_relation', 'incomplete_relation'} or relation['reason_code'] not in REASONS:
            raise ComparisonContextError('invalid_response')
        if relation['relation_kind'] == 'many_to_many' and relation['validation'] == 'verified': raise ComparisonContextError('invalid_response')
        if relation['semantic_claim'] == 'identity' and (len(relation['left']) != 1 or len(relation['right']) != 1 or any(relation['left'][0][k] != relation['right'][0][k] for k in ('subject_kind', 'subject_id', 'occurrence_path'))): raise ComparisonContextError('invalid_response')
        if relation['semantic_claim'] == 'identity' and (relation['relation_kind'] != 'same_exact_subject' or not _same_definition(request['left'], request['right'])):
            raise ComparisonContextError('invalid_response')
        for side in ('left', 'right'):
            if not _unique(relation[side]): raise ComparisonContextError('invalid_response')
            for ep in relation[side]:
                endpoint(ep)
                if ep['side'] != side or ep['target_key'] != value[side]['graph']['target_key'] or (ep['subject_kind'], ep['subject_id']) not in subjects[side]:
                    raise ComparisonContextError('invalid_response')
                if ep['occurrence_path'] and not any(
                    h['scope'].get('selector', {}).get('occurrence_path') == ep['occurrence_path']
                    and ep['subject_id'] in h['node_ids' if ep['subject_kind'] == 'node' else 'edge_ids']
                    for h in value[side]['hierarchy_navigation']['scopes']):
                    raise ComparisonContextError('invalid_response')
                if relation['validation'] == 'verified': covered[side].add((ep['subject_kind'], ep['subject_id']))
        if not relation['evidence']: raise ComparisonContextError('invalid_response')
        for e in _array(relation['evidence']):
            _object(e, ('source_id', 'evidence_ref', 'evidence_kind', 'selected_endpoints', 'source_cut', 'access_revision', 'verification_contract', 'validation'))
            reference(e['evidence_ref'], source_id=e['source_id'])
            state = observations[e['source_id']]
            if (e['source_cut'] != state['cut'] or e['access_revision'] != state['access_revision']
                or e['evidence_kind'] != 'exact_public_projection' or e['verification_contract'] != 'rpnh/public_pn_projection/v1'
                or e['validation'] != 'verified' or e['selected_endpoints'] != relation['left'] + relation['right']):
                raise ComparisonContextError('invalid_response')
    presence_ids = set()
    for change in _array(mapping['explicit_presence_changes']):
        _object(change, ('change_id', 'subject', 'state', 'reason_code', 'absence_evidence_refs', 'validation'))
        ep = endpoint(change['subject']); side = ep['side']; identity = (ep['subject_kind'], ep['subject_id'])
        if (change['change_id'] in presence_ids or ep['target_key'] != value[side]['graph']['target_key']
                or identity not in subjects[side] or identity in covered[side]
                or change['state'] != ('present_left_only' if side == 'left' else 'present_right_only')
                or change['reason_code'] != 'explicit_presence' or change['validation'] != 'verified'
                or not _unique(change['absence_evidence_refs'])):
            raise ComparisonContextError('invalid_response')
        presence_ids.add(change['change_id'])
        for ref in change['absence_evidence_refs']:
            reference(ref)
            if ref['source_id'] not in observations: raise ComparisonContextError('invalid_response')
        covered[side].add(identity)
    _object(mapping['unmapped'], ('left', 'right'))
    for side in ('left', 'right'):
        unknown = _object(mapping['unmapped'][side], ('scope_key', 'subjects', 'coverage'))
        if unknown['scope_key'] != value[side]['graph']['scope_key'] or unknown['coverage'] != 'complete_in_declared_scope':
            raise ComparisonContextError('invalid_response')
        endpoints = _unique(unknown['subjects'])
        for ep in endpoints:
            endpoint(ep)
            if ep['side'] != side or ep['target_key'] != value[side]['graph']['target_key']: raise ComparisonContextError('invalid_response')
        if {(ep['subject_kind'], ep['subject_id']) for ep in endpoints} != subjects[side] - covered[side]: raise ComparisonContextError('invalid_response')
    any_verified = any(r['validation'] == 'verified' for r in mapping['relations'])
    complete = all(covered[s] == subjects[s] for s in subjects) and (any_verified or bool(presence_ids) or _same_definition(request['left'], request['right'])) and not any(r['validation'] == 'conflicting' for r in mapping['relations'])
    expected_coverage = 'complete_in_declared_scope' if complete else 'partial' if any_verified or presence_ids else 'none'
    if mapping['coverage'] != expected_coverage: raise ComparisonContextError('invalid_response')
    presentation = _object(value['presentation'], ('recommended_mode', 'display_mode', 'full_pair_navigation'))
    expected_mode = {'complete_in_declared_scope': 'reliable_diff', 'partial': 'partial_mapping', 'none': 'full_pair'}[mapping['coverage']]
    if presentation['recommended_mode'] != expected_mode or presentation['display_mode'] != ('full_pair' if request['view_preference'] == 'full_pair' else expected_mode):
        raise ComparisonContextError('invalid_response')
    navigation = _object(presentation['full_pair_navigation'], ('availability', 'reason_code', 'targets', 'source_cuts', 'scope', 'view_preference'))
    if (navigation['availability'] not in {'available', 'requires_read', 'unavailable'} or navigation['reason_code'] not in REASONS
        or navigation['targets'] != {s: request[s] for s in ('left', 'right')} or navigation['source_cuts'] != echo['source_cuts']
        or navigation['scope'] != {'kind': 'full_pair', 'left': {'kind': 'full_net'}, 'right': {'kind': 'full_net'}}
        or navigation['view_preference'] != 'full_pair'):
        raise ComparisonContextError('invalid_response')
    _object(value['axes'], AXES)
    scope_key = _key({side: value[side]['graph']['scope_key'] for side in ('left', 'right')})
    for axis, result in value['axes'].items():
        _object(result, ('axis', 'coverage', 'scope_key', 'rows', 'counts', 'unknown_reasons'))
        if result['axis'] != axis or result['scope_key'] != scope_key or result['coverage'] not in COVERAGES:
            raise ComparisonContextError('invalid_response')
        if axis not in request['axes'] and (result['coverage'] != 'not_requested' or result['rows']): raise ComparisonContextError('invalid_response')
        if not set(_unique(result['unknown_reasons'])) <= REASONS: raise ComparisonContextError('invalid_response')
        ids = set()
        for row in _array(result['rows'], LIMITS['max_field_rows']):
            _object(row, ('row_id', 'subject_relation_id', 'subject_scope', 'field_path', 'comparator_contract',
                'left_fact', 'right_fact', 'classification', 'reason_code', 'evidence_refs', 'interpretation'))
            if row['row_id'] in ids or row['subject_scope'] != scope_key or row['interpretation'] != 'current_analysis' or row['reason_code'] not in REASONS or row['classification'] not in {'known_same', 'known_changed', 'unknown'} or row['comparator_contract'] != 'rpnh/json_equal/v1':
                raise ComparisonContextError('invalid_response')
            ids.add(row['row_id']); _text(row['row_id']); _text(row['field_path'])
            if row['subject_relation_id'] is not None and row['subject_relation_id'] not in relation_ids: raise ComparisonContextError('invalid_response')
            _fact(row['left_fact']); _fact(row['right_fact'])
            for ref in _unique(row['evidence_refs']): reference(ref)
            recomputed = _row(scope_key, row['subject_relation_id'], row['field_path'], row['left_fact'], row['right_fact'],
                              applicable=row['classification'] != 'unknown', reason=row['reason_code'])
            if recomputed != row: raise ComparisonContextError('invalid_response')
        if result['counts'] != _axis(axis, scope_key, result['rows'], result['coverage'], result['unknown_reasons'])['counts']:
            raise ComparisonContextError('invalid_response')
    if 'definition' in request['axes'] and value['axes']['definition']['rows'] != _definition_rows({s: value[s] for s in ('left', 'right')}, mapping, scope_key):
        raise ComparisonContextError('invalid_response')
    return value
