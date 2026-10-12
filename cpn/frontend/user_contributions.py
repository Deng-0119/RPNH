"""Read-only recorded-proposer overlay for the original, exact adopted PN.

The trusted HOST must supply both its original collaboration session and an
already-authorized RegistryReadSession. Browser JSON never supplies credentials.
No author/identity facts, grants, projections, or Registry records are created.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs

from .checkpoint_view import selector
from cpn.rpnh.registry.schema_catalog import canonical_json, canonical_text
from cpn.rpnh.registry.user_collaboration import (SESSION, PROPOSAL, REVIEW, DECISION, RECEIPT,
    check_session, mint_action)
from cpn.rpnh.registry._event_store.user_collaboration import read_fence, exact
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
from cpn.rpnh.collaboration._plain_merge_model import normalize
from cpn.rpnh.module import ModuleDeclaration

SCHEMA = 'rpnh/user_contributions/v1'
MAX_FACTS = 2048
MAX_REVISIONS = 128
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PROJECTED_CHANGES = 20000
MAX_MODEL_BYTES = 32 * 1024 * 1024


class ContributionInvalid(ValueError):
    pass


class ContributionAccessChanged(PermissionError):
    pass


class ContributionStale(RuntimeError):
    pass


class ContributionLimit(RuntimeError):
    pass


def parse_query(query):
    try:
        if len(query.encode()) > 8192:
            raise ValueError('query too large')
        q = parse_qs(query, keep_blank_values=True, strict_parsing=True, errors='strict')
        if set(q) != {'net_ref', 'checkpoint_ref', 'cut'} or any(len(v) != 1 for v in q.values()):
            raise ValueError('invalid keys')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('duplicate JSON key')
                value[key] = item
            return value
        if re.fullmatch(r'[1-9][0-9]{0,15}', q['cut'][0]) is None or int(q['cut'][0]) > 2**53 - 1:
            raise ValueError('invalid cut')
        return selector(*(json.loads(q[k][0], object_pairs_hook=unique) for k in ('net_ref', 'checkpoint_ref')), int(q['cut'][0]))
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ContributionInvalid('invalid_query') from exc


def _key(ref):
    return canonical_text(ref)


def _state(atoms, key):
    return canonical_json({'present': key in atoms, 'value': atoms.get(key)})



def _changed_fields(before, after, prefix, depth=0):
    """Field paths only; never disclose config values, prompts or tool content."""
    if canonical_json(before) == canonical_json(after):
        return []
    if depth < 8 and type(before) is dict and type(after) is dict:
        paths = []
        for key in sorted(set(before) | set(after)):
            escaped = str(key).replace('~', '~0').replace('/', '~1')
            path = prefix + '/' + escaped
            if key not in before or key not in after:
                paths.append(path)
            else:
                paths.extend(_changed_fields(before[key], after[key], path, depth + 1))
            if len(paths) > 128:
                return [prefix]
        return paths
    return [prefix]

def project_contributions(selected, source, runtime, receipt, facts, load_revision):
    """Pure projection from verified canonical facts/materials, never authority.

    Retained values are followed only through exact parent edges, including both
    sides of a merge. Equal bytes in an unselected proposal confer no credit.
    Names, publication principal, model, provider and executor are not actors.
    """
    if len(runtime['nodes']) > 10000:
        raise ContributionLimit('node_limit')
    rows = {n['id']: {'node_id': n['id'], 'state': 'unknown', 'changes': []} for n in runtime['nodes']}
    result = {'schema_version': SCHEMA, 'selector': selected, 'source': source,
        'coverage': {'status': 'unknown', 'reason': 'no_exact_adoption_receipt'},
        'actors': [], 'nodes': list(rows.values()), 'actions': [],
        'selected_revision_ref': None, 'adoption_receipt_ref': None}
    if receipt is None:
        return result
    by_ref = {_key(v['self_ref']): v for values in facts.values() for v in values}
    decision = by_ref[_key(receipt['data']['decision_ref'])]
    revision_ref = receipt['data']['result_revision_ref']
    if (receipt['data']['operation'] != 'adopt' or receipt['data']['result_ref'] != selected['net_ref']
            or decision['data']['operation'] != 'adopt' or decision['data']['result_revision_ref'] != revision_ref
            or decision['project_ref'] != receipt['project_ref']):
        raise ValueError('adoption evidence mismatch')
    project = receipt['project_ref']
    proposals = [v for v in facts.get(PROPOSAL, ()) if v['project_ref'] == project]
    proposals_by_revision = defaultdict(list)
    for p in proposals:
        proposals_by_revision[_key(p['data']['candidate_revision_ref'])].append(p)
    models, lineage, active, retained = {}, set(), set(), {}
    model_bytes = trace_entries = projected_changes = 0

    def actor(fact):
        session = by_ref.get(_key(fact['session_ref']))
        if session is None or session['self_ref']['entity_type'] != SESSION:
            raise ValueError('missing exact actor session')
        return session['data']['actor'], session['self_ref']

    def model(ref):
        nonlocal model_bytes
        key = _key(ref)
        if key not in models:
            if len(models) >= MAX_REVISIONS:
                raise ContributionLimit('revision_limit')
            value = load_revision(ref)
            model_bytes += len(canonical_json(value))
            if model_bytes > MAX_MODEL_BYTES:
                raise ContributionLimit('material_limit')
            models[key] = value
        return models[key]

    def visit(ref):
        nonlocal trace_entries
        key = _key(ref)
        if key in active:
            raise ValueError('revision cycle')
        if key in retained:
            return retained[key]
        active.add(key); lineage.add(key)
        current = model(ref)
        atoms, parent_values = current['atoms'], []
        for parent in current['parents']:
            parent_values.append((model(parent)['atoms'], visit(parent)))
        values = {}
        for subject in atoms:
            evidence = {}
            for parent_atoms, parent_trace in parent_values:
                if _state(atoms, subject) == _state(parent_atoms, subject):
                    evidence.update(parent_trace.get(subject, {}))
            values[subject] = evidence
        for proposal in proposals_by_revision[key]:
            data = proposal['data']; base = model(data['base_revision_ref'])
            if data['base_revision_ref'] not in current['parents']:
                raise ValueError('proposal base is not an exact parent')
            # The original proposal validator binds the complete Module payload.
            if canonical_json(data['payload']) != canonical_json(current['module']):
                raise ValueError('proposal candidate payload mismatch')
            who, session = actor(proposal)
            for subject in atoms:
                if _state(atoms, subject) == _state(base['atoms'], subject):
                    continue
                element_id, field = subject.rsplit('/', 1)
                element = current['elements'].get(element_id)
                if element is None or element['kind'] not in {'operation', 'port', 'component'}:
                    continue
                change = {'actor': who, 'session_ref': session, 'proposal_ref': proposal['self_ref'],
                    'base_revision_ref': data['base_revision_ref'], 'candidate_revision_ref': ref,
                    'element_id': element_id, 'locator': element['locator'],
                    'change': 'added' if element_id not in base['elements'] else 'modified',
                    'status': 'adopted' if ref == revision_ref else 'inherited',
                    'fields': _changed_fields(base['atoms'].get(subject), atoms[subject], field)}
                values[subject][_key(proposal['self_ref']) + ':' + subject] = change
        trace_entries += sum(len(v) for v in values.values())
        if trace_entries > 100000:
            raise ContributionLimit('lineage_limit')
        active.remove(key); retained[key] = values
        return values

    trace = visit(revision_ref)
    target = model(revision_ref)
    # Source equality is checked by the caller against this exact adopted wire.
    for item in target['lowering']:
        if item['kind'] not in {'operation', 'port', 'component'}:
            continue
        changes = {}
        for subject, values in trace.items():
            if subject.rsplit('/', 1)[0] == item['element_id']:
                changes.update(values)
        for node_id in item['nodes']:
            if node_id not in rows:
                raise ValueError('lowering references absent runtime node')
            for value in changes.values():
                copy = deepcopy(value)
                # Display the selected revision's locator after a stable-ID rename.
                copy['locator'] = item['locator']
                existing = next((c for c in rows[node_id]['changes'] if c['proposal_ref'] == copy['proposal_ref']
                    and c['element_id'] == copy['element_id']), None)
                if existing is None:
                    projected_changes += 1
                    if projected_changes > MAX_PROJECTED_CHANGES:
                        raise ContributionLimit('change_limit')
                    rows[node_id]['changes'].append(copy)
                else:
                    existing['fields'] = sorted(set(existing['fields']) | set(copy['fields']))
    for row in rows.values():
        actors = {c['actor'] for c in row['changes']}
        if actors:
            row['state'] = 'overlap' if len(actors) > 1 else ('inherited' if all(c['status'] == 'inherited' for c in row['changes']) else 'changed')
    actions = []
    for proposal in proposals:
        who, session = actor(proposal)
        ids = [n['node_id'] for n in rows.values() if any(c['proposal_ref'] == proposal['self_ref'] for c in n['changes'])]
        actions.append({'role': 'proposed', 'actor': who, 'session_ref': session,
            'evidence_ref': proposal['self_ref'], 'node_ids': ids,
            'status': 'retained_delta' if ids else ('lineage_no_retained_node_delta' if _key(proposal['data']['candidate_revision_ref']) in lineage else 'unselected')})
    for reference in decision['data']['review_refs']:
        review = by_ref[_key(reference)]; who, session = actor(review)
        actions.append({'role': 'reviewed', 'actor': who, 'session_ref': session, 'evidence_ref': review['self_ref'],
            'node_ids': [], 'status': review['data']['disposition']})
    who, session = actor(decision)
    actions.append({'role': 'adopted', 'actor': who, 'session_ref': session, 'evidence_ref': receipt['self_ref'],
        'node_ids': [], 'status': 'exact_net_receipt'})
    # No node execution-to-human binding is inferred from session or proposal.
    actors = {_key(a['session_ref']): {'actor': a['actor'], 'session_ref': a['session_ref']} for a in actions}
    result.update(selected_revision_ref=revision_ref, adoption_receipt_ref=receipt['self_ref'],
        actors=list(actors.values()), actions=actions,
        coverage={'status': 'partial', 'reason': 'recorded_proposers_plain_parent_lineage; authorship_and_execution_unknown; no_removed_or_module_edge_only_overlay'})
    return result


class UserContributionView:
    """HOST opt-in; use existing sessions, never reconstruct them from JSON.

    The original read session must already allow the selected runtime, author
    descriptors, stored public projections and exact definition/map material.
    This class does not issue or widen that permission. Its lifetime is bounded
    by both original sessions. An unbound standalone Viewer returns 501.
    """
    def __init__(self, host, session, reader, *, source_id):
        from cpn.rpnh.collaboration.user_collaboration import UserCollaborationHost
        from cpn.rpnh.collaboration.registry_read_session import RegistryReadSession
        if type(host) is not UserCollaborationHost or type(reader) is not RegistryReadSession:
            raise TypeError('original HOST and read session required')
        check_session(host._core.event_store, session)
        if source_id != host._binding['source_id']:
            raise ValueError('source binding differs')
        self.host, self.session, self.reader, self.source_id = host, session, reader, source_id
        self.run_dir = host._core.run_dir

    def _fence(self, db):
        core = self.host._core
        action = mint_action(core.event_store, self.session, 'read', None, observer=self.session._observer)
        try:
            read_fence(core.event_store, db, core.task_id, action)
        except RegistryConflict as exc:
            raise ContributionAccessChanged('access_changed') from exc

    def __call__(self, *, net_ref, checkpoint_ref, cut):
        selected = selector(net_ref, checkpoint_ref, cut)
        core = self.host._core
        with core.event_store.connect() as db:
            db.execute('BEGIN'); self._fence(db)
            before = (db.execute('SELECT COALESCE(MAX(ordinal),0) FROM events').fetchone()[0],
                int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0]))
            # Bound before allocating or hydrating user facts. Read only complete
            # original canonical publications at the selected historical cut.
            kinds = (SESSION, PROPOSAL, REVIEW, DECISION, RECEIPT)
            totals = db.execute('SELECT COUNT(*),COALESCE(SUM(o.size),0) FROM objects o '
                'JOIN events e ON e.event_id=o.published_event_id WHERE o.object_type IN (?,?,?,?,?) '
                'AND e.task_id=? AND e.ordinal<=?', (*kinds, str(core.task_id), cut)).fetchone()
            if totals[0] > MAX_FACTS or totals[1] > 16 * 1024 * 1024:
                raise ContributionLimit('fact_limit')
            candidates = db.execute('SELECT o.object_type,o.logical_id,o.version_id,o.transaction_id,e.ordinal '
                'FROM objects o JOIN events e ON e.event_id=o.published_event_id '
                'WHERE o.object_type IN (?,?,?,?,?) AND e.task_id=? AND e.ordinal<=? ORDER BY e.ordinal LIMIT ?',
                (*kinds, str(core.task_id), cut, MAX_FACTS + 1)).fetchall()
            if len(candidates) > MAX_FACTS:
                raise ContributionLimit('fact_limit')
            facts = defaultdict(list)
            for row in candidates:
                terminal = db.execute("SELECT ordinal FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (row['transaction_id'],)).fetchall()
                if len(terminal) != 1 or terminal[0]['ordinal'] > cut:
                    continue
                ref = {k: row[v] for k, v in (('entity_type', 'object_type'), ('logical_id', 'logical_id'), ('version_id', 'version_id'))}
                facts[row['object_type']].append(exact(core.event_store, db, core.task_id, ref, row['object_type']))
        if self.reader.source_state(self.source_id)['source_ref']['ref'] != self.host._binding['task_ref']:
            raise ContributionAccessChanged('access_changed')
        source_cut = self.reader.capture_cut(self.source_id, ordinal=cut)
        def qualified(ref):
            return SourceQualifiedVersionRef.from_dict({'schema_version': 'rpnh/collaboration/source_version_ref/v1',
                'source_id': self.source_id, 'ref': ref}, catalog=core.catalog)
        def material(ref):
            reference = SourceQualifiedResourceRef.from_dict(ref, catalog=core.catalog)
            return json.loads(self.reader.read_material(resource_ref=reference, at_cut=source_cut)['body'])
        runtime = self.reader.read_public_projection(entry_ref=qualified(checkpoint_ref), at_cut=source_cut)['record']
        if runtime['net_ref']['ref'] != net_ref or runtime['checkpoint_commit_ordinal'] != cut:
            raise ContributionInvalid('checkpoint_selector_mismatch')
        receipts = [r for r in facts[RECEIPT] if r['data']['operation'] == 'adopt' and r['data']['result_ref'] == net_ref]
        if len(receipts) > 1:
            raise ValueError('ambiguous net adoption')
        receipt = receipts[0] if receipts else None
        cache = {}
        def load(ref):
            key = _key(ref)
            if key in cache:
                return cache[key]
            if ref['entity_type'] != 'collaboration_net_revision/v1':
                raise NotImplementedError('unsupported author revision')
            reference = qualified(ref)
            record = self.reader.read_exact(entry_ref=reference, at_cut=source_cut)['record']
            if record['selected_change_refs'] or record['definition_kind'] != 'closed_module':
                raise NotImplementedError('unsupported selected-change ancestry')
            if any(p['source_id'] != self.source_id for p in record['parent_revision_refs']):
                raise NotImplementedError('cross_source_ancestry_unsupported')
            if any(record[k]['source_id'] != self.source_id for k in ('definition_ref', 'element_mapping_ref')):
                raise NotImplementedError('cross_source_material_unsupported')
            projection = self.reader.read_public_projection(entry_ref=reference, at_cut=source_cut)['record']
            module = material(record['definition_ref']); elements = material(record['element_mapping_ref'])
            normalized = normalize(SimpleNamespace(module=ModuleDeclaration.from_dict(module), element_map=elements,
                compiled=SimpleNamespace(fragments={}), host_requirements={}))
            value = {'module': module, 'atoms': normalized['atoms'], 'parents': [r['ref'] for r in record['parent_revision_refs']],
                'elements': {r['element_id']: r for r in elements['elements']}, 'lowering': projection['lowering']}
            cache[key] = value
            return value
        if receipt is not None:
            target = load(receipt['data']['result_revision_ref'])
            wire = material(runtime['configuration']['declared_configuration_ref'])
            if canonical_json(wire['source']) != canonical_json(target['module']):
                raise ValueError('adopted compiled source differs from revision')
        result = project_contributions(selected, {'task_id': str(core.task_id), 'source_id': self.source_id, 'task_ref': self.host._binding['task_ref']}, runtime,
            receipt, facts, load)
        payload = canonical_json(result)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ContributionLimit('response_limit')
        self.reader.final_recheck((self.source_id,))
        with core.event_store.connect() as db:
            self._fence(db)
        if before != (core.event_store.max_ordinal(), core.event_store.writer_epoch):
            raise ContributionStale('stale_observation')
        return result
