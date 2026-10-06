import test from 'node:test';
import assert from 'node:assert/strict';
import { agentMembers, agentCardState, allMemberFirings, exactReference, firingScope, sameFiringTarget, resolveFiringTarget } from '../../../cpn/frontend/static/agent-members.mjs';
import { normalizeFrame, displayGraph, nodePresentation, cardState } from '../../../cpn/frontend/static/dashboard-model.mjs';
import { overviewGraph } from '../../../cpn/frontend/static/overview.mjs';
import { topologyKey, referenceText, firingSummary } from '../../../cpn/frontend/static/model.mjs';
import { geometry } from '../../../cpn/frontend/static/layout.mjs';
import { setLanguage } from '../../../cpn/frontend/static/i18n.mjs';

const ref = (id, version = 'same-short-version') => ({ entity_type: 'transition_firing/v1', logical_id: id, version_id: version });
const firing = (id, status = 'settled') => ({ firing_ref: ref(id), status, attempt_index: 1 });
const member = (id, firings = []) => ({ id, label: 'Same name', kind: 'transition', category: 'execution', inputs: [], outputs: [], runtime: { firings } });
function frame(nodes = [member('a', [firing('fa')]), member('b', [firing('fb', 'started')])]) {
    const source = { mode: 'registry_current', task_id: 'task', run_dir: '/display-only/run',
        net_ref: { entity_type: 'net_instance/v1', logical_id: 'net', version_id: 'net-v1' }, verified_head_ordinal: 18 };
    const net = { schema_version: 'rpnh/net_view/v1', source: structuredClone(source), nodes, edges: [] };
    return normalizeFrame({ schema_version: 'rpnh/dashboard/v1', source, net,
        position: { mode: 'live', cursor: 18, latest_head: 18 }, coverage: { firings: 'current_observations' },
        agent_nodes: nodes.filter(n => n.kind === 'transition').map(n => ({ transition_id: n.id, semantic_group: 'agent-one' })) });
}
function freeze(value) {
    if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
    return value;
}
const aggregate = (...source_ids) => ({ source_ids });

test('all explicit members contribute mixed settled, pending, outcome unknown and unrecognized states', () => {
    const value = frame([member('a', [firing('fa')]), member('b', [firing('fb', 'started'), firing('fb2', 'outcome_unknown')]),
        member('c', [firing('fc', 'future_state')]), member('d')]);
    delete value.net.nodes[3].runtime;
    const summary = agentMembers(value, aggregate('a', 'b', 'c', 'd'));
    assert.deepEqual(summary.counts, { settled: 1, pending: 1, invalidated: 0, outcome_unknown: 1, other: 1 });
    assert.equal(summary.missing, 1); assert.equal(summary.loaded_count, 4); assert.equal(summary.total_count, null);
    assert.match(agentCardState(summary), /1 unsettled.*1 settled.*2 unknown.*Partial scope/);
    const card = displayGraph(value, 'overview').nodes[0];
    assert.equal(firingSummary(card).pending, 2, 'renderer/focus see other members, not just settled representative');
    assert.equal(cardState(card), agentCardState(summary));
});

test('only source_ids choose members and equal names alone never merge', () => {
    const value = frame();
    value.net.nodes.push(member('outsider', [firing('hidden-from-group', 'started')]));
    value.agent_nodes.push({ transition_id: 'outsider' });
    const view = displayGraph(value, 'overview');
    assert.equal(view.nodes.length, 2);
    assert.deepEqual(view.nodes[0].source_ids, ['a', 'b']);
    assert.equal(view.nodes[0].agent_summary.rows.length, 2);
    assert.equal(agentMembers(value, aggregate('b')).rows[0].transition_id, 'b');
});

test('aggregate runtime updates preserve graph topology, raw membership and anonymous geometry', () => {
    const value = frame();
    const raw = { ...value.net, nodes: value.net.nodes.map(n => ({ ...n, display: nodePresentation(n, value), source_ids: [n.id] })) };
    const original = overviewGraph(raw, value.agent_nodes), view = displayGraph(value, 'overview');
    assert.equal(topologyKey(view), topologyKey(original));
    assert.deepEqual(geometry(view).graph, geometry(original).graph);
    const next = structuredClone(value);
    next.net.nodes[1].runtime.firings.push(firing('concurrent-b', 'admitted'));
    next.net.nodes[0].runtime.firings[0].status = 'invalidated';
    const refreshed = displayGraph(next, 'overview');
    assert.equal(topologyKey(refreshed), topologyKey(view));
    assert.deepEqual(geometry(refreshed).graph, geometry(view).graph);
    assert.equal(refreshed.nodes[0].agent_summary.counts.pending, 2);
});

test('duplicate source IDs and identical full-ref facts deduplicate without mutating input', () => {
    const value = frame(), row = value.net.nodes[0].runtime.firings[0];
    value.net.nodes[0].runtime.firings.push({ attempt_index: 1, status: 'settled', firing_ref: { version_id: row.firing_ref.version_id, logical_id: 'fa', entity_type: 'transition_firing/v1' } });
    const before = structuredClone(value), selection = aggregate('a', 'a', 'b');
    const summary = agentMembers(freeze(value), freeze(selection));
    assert.equal(summary.members.length, 2); assert.equal(summary.rows.length, 2); assert.equal(summary.conflicts, 0);
    assert.deepEqual(value, before);
    summary.rows[0].target.firing_ref.logical_id = 'modified detached target';
    summary.rows[0].target.scope.net_ref.logical_id = 'modified detached scope';
    assert.deepEqual(value, before);
});

test('same short version in different full refs and different members remains independently addressable', () => {
    const value = frame(); value.net.nodes[0].runtime.firings.push(firing('rework-a', 'returned_unsettled'));
    const rows = allMemberFirings(value).rows;
    assert.equal(new Set(rows.map(row => referenceText(row.firing.firing_ref))).size, 1);
    assert.equal(new Set(rows.map(row => row.refKey)).size, 3);
    for (const row of rows) assert.ok(sameFiringTarget(resolveFiringTarget(value, row.target).target, row.target));
    assert.equal(sameFiringTarget(rows[0].target, rows[1].target), false);
    assert.equal(sameFiringTarget(rows[0].target, { ...rows[0].target, transition_id: 'b' }), false);
});

test('conflicting facts under one full ref are visible, flagged and never arbitrarily selected', () => {
    const value = frame([member('a', [firing('same'), firing('same', 'started')])]);
    const summary = agentMembers(value, aggregate('a'));
    assert.equal(summary.rows.length, 2); assert.equal(summary.conflicts, 2);
    assert.equal(summary.total_count, null); assert.equal(summary.counts.settled, 0); assert.equal(summary.counts.pending, 0);
    assert.ok(summary.rows.every(row => row.target === null && row.reason === 'inconsistent'));
    assert.match(agentCardState(summary), /Inconsistent disclosure/);
});

test('same full ref on different members or an explicit mismatching member association is inconsistent', () => {
    const value = frame([member('a', [firing('same')]), member('b', [firing('same')])]);
    assert.equal(allMemberFirings(value).conflicts, 2);
    const localTarget = agentMembers(frame([member('a', [firing('same')])]), aggregate('a')).rows[0].target;
    assert.equal(agentMembers(value, aggregate('a')).rows[0].target, null, 'single-card disclosure already flags the frame-wide conflict');
    assert.equal(resolveFiringTarget(value, localTarget).reason, 'inconsistent', 'navigation checks all disclosed member associations in the frame');
    const explicit = frame([member('a', [{ ...firing('only'), transition_id: 'b' }])]);
    assert.equal(allMemberFirings(explicit).rows[0].reason, 'inconsistent');
});

test('true empty disclosure is zero; missing runtime, unknown coverage and missing members remain unknown', () => {
    const value = frame([member('a')]);
    const empty = agentMembers(value, aggregate('a'));
    assert.equal(empty.loaded_count, 0); assert.equal(empty.total_count, 0); assert.equal(empty.complete, true);
    delete value.net.nodes[0].runtime;
    let summary = agentMembers(value, aggregate('a'));
    assert.equal(summary.loaded_count, null); assert.equal(summary.total_count, null); assert.match(agentCardState(summary), /unknown/);
    value.net.nodes[0].runtime = { firings: [] }; value.coverage.firings = 'partial';
    assert.equal(agentMembers(value, aggregate('a')).loaded_count, null);
    value.coverage.firings = 'current_observations';
    summary = agentMembers(value, aggregate('a', 'not-a-member'));
    assert.equal(summary.members.length, 2); assert.equal(summary.members[1].node, null); assert.equal(summary.total_count, null);
});

for (const state of ['not_provided', 'not_disclosed', 'unsupported', 'read_failed', 'stale_observation']) {
    test(`${state} does not turn stray runtime rows into disclosed counts or navigation`, () => {
        const value = frame(); value.coverage.firings = state;
        const summary = allMemberFirings(value);
        assert.equal(summary.rows.length, 0); assert.equal(summary.loaded_count, null); assert.equal(summary.total_count, null);
    });
}

test('wire, dataclass and object references retain every field; version-only legacy identities cannot navigate', () => {
    const refs = [ref('wire'),
        { entity_type: 'transition_firing/v1', entity_id: { kind: 'transition_firing', value: 'id' }, version_id: { kind: 'transition_firing_version', value: 'version' } },
        { kind: 'transition_firing', object_id: { value: 'object' }, version_id: { value: 'version' }, extra: { disclosed: true } }];
    for (const full of refs) {
        const value = frame([member('a', [{ firing_ref: full, status: 'settled' }])]);
        assert.equal(exactReference(full), true);
        assert.deepEqual(allMemberFirings(value).rows[0].target.firing_ref, full);
    }
    for (const incomplete of ['version-only', { version_id: 'version-only' }, null, {}]) {
        const value = frame([member('a', [{ firing_ref: incomplete, status: 'settled' }])]);
        const summary = allMemberFirings(value);
        assert.equal(summary.rows.length, 1); assert.equal(summary.rows[0].target, null);
        assert.equal(summary.rows[0].reason, 'identity_missing');
    }
});

test('missing comparable task/run/net scope never creates a reusable target', () => {
    for (const change of [v => { delete v.source.task_id; delete v.source.run_dir; },
        v => { v.source.net_ref = null; }, v => { v.source.net_ref = 'short-net-version'; },
        v => { v.net.source.task_id = 'conflict'; }]) {
        const value = frame(); change(value);
        assert.equal(firingScope(value), null); assert.equal(allMemberFirings(value).rows[0].target, null);
    }
    const value = frame(); delete value.source.task_id; delete value.net.source.task_id;
    assert.ok(allMemberFirings(value).rows[0].target, 'exact net plus run identity can support optional task hint');
});

test('same-net head advance and history preserve full target; missing, moved or different scopes do not', () => {
    const value = frame(), target = allMemberFirings(value).rows[1].target;
    const next = structuredClone(value);
    next.source.verified_head_ordinal = next.net.source.verified_head_ordinal = 22;
    next.position = { mode: 'history', cursor: 22, latest_head: 25 }; next.coverage.firings = 'canonical_only';
    assert.ok(sameFiringTarget(resolveFiringTarget(next, target).target, target));
    next.net.nodes[1].runtime.firings = [];
    assert.equal(resolveFiringTarget(next, target).reason, 'missing');
    next.net.nodes[0].runtime.firings.push(value.net.nodes[1].runtime.firings[0]);
    assert.equal(resolveFiringTarget(next, target).reason, 'missing', 'never rebind to another member');
    for (const key of ['task_id', 'run_dir', 'net_ref', 'mode']) {
        const other = structuredClone(value);
        other.source[key] = other.net.source[key] = key === 'net_ref' ? { ...other.source.net_ref, version_id: 'v2' } : 'other';
        assert.equal(resolveFiringTarget(other, target).reason, 'scope_changed', key);
    }
});

test('English and Chinese labels translate while disclosed status and identity values stay unchanged', () => {
    const value = frame(); value.net.nodes[1].runtime.firings[0].status = '未知状态<plain-text>';
    const summary = allMemberFirings(value);
    try {
        setLanguage('en'); assert.match(agentCardState(summary), /Disclosed: 0 unsettled.*1 unknown/);
        setLanguage('zh-CN'); assert.match(agentCardState(summary), /已披露：0 未结算.*1 未知/);
        assert.equal(summary.rows[1].firing.status, '未知状态<plain-text>');
    } finally { setLanguage('en'); }
});

test('cross-card conflicts are indexed frame-wide but each summary exposes only its source_ids', () => {
    const value = frame([member('a', [firing('shared')]), member('b', [firing('shared')]), member('c', [firing('unique', 'started')])]);
    value.agent_nodes = value.net.nodes.map(node => ({ transition_id: node.id, semantic_group: 'group-' + node.id }));
    const before = structuredClone(value), view = displayGraph(freeze(value), 'overview');
    assert.equal(view.nodes.length, 3);
    for (const id of ['a', 'b']) {
        const node = view.nodes.find(node => node.id === id), summary = node.agent_summary;
        assert.equal(summary.conflicts, 1); assert.equal(summary.rows.length, 1);
        assert.equal(summary.rows[0].transition_id, id); assert.equal(summary.rows[0].target, null);
        assert.equal(summary.rows[0].reason, 'inconsistent'); assert.equal(summary.complete, false);
        assert.equal(summary.total_count, null); assert.equal(summary.counts.settled, 0);
        assert.match(cardState(node), /Inconsistent disclosure/);
        assert.equal(node.runtime.firings.length, 0, 'renderer never treats the conflicting record as settled');
        assert.equal(agentMembers(value, aggregate(id)).rows[0].reason, 'inconsistent');
    }
    const clean = view.nodes.find(node => node.id === 'c').agent_summary;
    assert.equal(clean.conflicts, 0); assert.equal(clean.complete, true); assert.equal(clean.rows.length, 1);
    assert.equal(clean.counts.pending, 1); assert.ok(clean.rows[0].target);
    assert.deepEqual(value, before);
});

test('conflict indexing is rebuilt for each frame and never retains old conflicts or leaks unrelated rows', () => {
    const value = frame([member('a', [firing('shared')]), member('b', [firing('shared')])]);
    assert.equal(agentMembers(value, aggregate('a')).rows[0].target, null);
    const repaired = structuredClone(value);
    repaired.net.nodes[1].runtime.firings[0].firing_ref.logical_id = 'separate-full-reference';
    const next = agentMembers(repaired, aggregate('a'));
    assert.equal(next.conflicts, 0); assert.equal(next.complete, true); assert.equal(next.rows.length, 1); assert.ok(next.rows[0].target);
    assert.equal(next.rows[0].transition_id, 'a');
    assert.equal(agentMembers(value, aggregate('a')).rows[0].target, null, 'another observation cannot overwrite the original frame result');
    repaired.net.nodes[1].runtime.firings.push(structuredClone(repaired.net.nodes[1].runtime.firings[0]));
    assert.equal(agentMembers(repaired, aggregate('b', 'b')).rows.length, 1, 'identical same-member rows still deduplicate');
});
