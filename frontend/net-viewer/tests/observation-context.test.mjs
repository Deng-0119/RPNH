import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFrame, observationContext, observationCoverage } from '../../../cpn/frontend/static/dashboard-model.mjs';

// JSON shapes follow RegistryDashboard.dashboard and project_registry_net.
// These are synthetic read DTOs, not generated Registry execution evidence.
const ref = (kind, id, version) => ({ kind, object_id: { value: id }, version_id: { value: version } });
function snapshot() {
    return {
        schema_version: 'rpnh/net_view/v1',
        source: { mode: 'registry_current', task_id: 'task-a', run_dir: '/display-only/run',
            net_ref: ref('net_instance', 'net-a', 'net-a-v2'), verified_head_ordinal: 18,
            writer_fencing_epoch: 4 },
        marking: { checkpoint_ref: ref('marking_checkpoint', 'checkpoint-a', 'checkpoint-a-v3'), epoch: 2 },
        nodes: [
            { id: 'input', label: 'Input', kind: 'place', category: 'place' },
            { id: 'step', label: 'Step', kind: 'transition', category: 'execution',
                runtime: { firings: [{ firing_ref: ref('transition_firing', 'f-a', 'f-a-v1'), status: 'settled' }] } },
        ],
        edges: [{ id: 'consume', source: 'input', target: 'step', kind: 'arc', mode: 'consume', weight: 1 }],
    };
}
function frame() {
    const net = snapshot();
    return normalizeFrame({ schema_version: 'rpnh/dashboard/v1', source: structuredClone(net.source), net,
        boundaries: { entry: [], exit: [], terminal_rules: [] },
        position: { mode: 'live', cursor: 13, latest_head: 18 },
        coverage: { firings: 'current_observations', history: 'current_net_canonical_checkpoints' } });
}
function freeze(value) {
    if (value && typeof value === 'object') {
        for (const child of Object.values(value)) freeze(child);
        Object.freeze(value);
    }
    return value;
}

test('current context separates selected head, checkpoint selector and paging cursor', () => {
    const f = frame(), context = observationContext(f), cut = context.source_cuts[0];
    assert.equal(context.mode, 'current');
    assert.deepEqual(cut.cut, { head_ordinal: 18, writer_fencing_epoch: 4 });
    assert.equal(context.checkpoint_selector, 13);
    assert.deepEqual(context.observed_capture, { latest_head_ordinal: 18, writer_fencing_epoch: 4 });
    assert.equal(cut.cursor, null);
    assert.deepEqual(cut.net_ref, f.source.net_ref);
    assert.deepEqual(cut.checkpoint_ref, f.net.marking.checkpoint_ref);
    assert.equal(cut.coverage.state, 'complete');
    assert.equal(cut.coverage.total_count, 1);
});

test('history never promotes the current capture writer epoch into its selected cut', () => {
    const f = frame();
    f.position = { mode: 'history', cursor: 13, latest_head: 40 };
    f.source.verified_head_ordinal = f.net.source.verified_head_ordinal = 13;
    f.source.writer_fencing_epoch = f.net.source.writer_fencing_epoch = 9;
    f.coverage.firings = 'canonical_only';
    const context = observationContext(normalizeFrame(f));
    assert.equal(context.mode, 'canonical-as-of');
    assert.deepEqual(context.source_cuts[0].cut, { head_ordinal: 13, writer_fencing_epoch: null });
    assert.equal(context.checkpoint_selector, 13);
    assert.deepEqual(context.observed_capture, { latest_head_ordinal: 40, writer_fencing_epoch: 9 });
    assert.equal(context.source_cuts[0].cursor, null);
    assert.equal(context.source_cuts[0].coverage.state, 'complete');
    assert.equal(context.source_cuts[0].coverage.scope.declaration, 'canonical_only');
});

test('legacy net_view fallback preserves references without claiming firing completeness', () => {
    const net = snapshot();
    net.execution = { head_ordinal: 21, firing_count: 999 };
    const f = normalizeFrame(net), context = observationContext(f);
    assert.equal(context.source_cuts[0].cut.head_ordinal, 21);
    assert.equal(context.checkpoint_selector, 21); // Existing fallback compatibility hint only.
    assert.equal(context.observed_capture.latest_head_ordinal, 21);
    assert.equal(context.source_cuts[0].cursor, null);
    assert.deepEqual(context.source_cuts[0].net_ref, net.source.net_ref);
    assert.deepEqual(context.source_cuts[0].checkpoint_ref, net.marking.checkpoint_ref);
    assert.deepEqual(observationCoverage(f), {
        state: 'partial', scope: { kind: 'frame_transition_firings', declaration: 'provider_defined', transition_ids: ['step'] },
        loaded_count: 1, total_count: null, missing: ['firing_scope'],
    });
});

test('initial_configured fallback cannot manufacture a Registry observation', () => {
    const net = snapshot();
    net.source = { mode: 'initial_configured' };
    delete net.marking;
    delete net.nodes[1].runtime;
    const context = observationContext(normalizeFrame(net));
    assert.equal(context.mode, 'current');
    assert.equal(context.source_cuts[0].cut, null);
    assert.equal(context.source_cuts[0].net_ref, null);
    assert.equal(context.source_cuts[0].checkpoint_ref, null);
    assert.equal(context.checkpoint_selector, null);
    assert.deepEqual(context.observed_capture, { latest_head_ordinal: null, writer_fencing_epoch: null });
    assert.equal(context.source_cuts[0].coverage.state, 'unsupported');
    assert.equal(context.source_cuts[0].coverage.loaded_count, null);
    assert.equal(context.source_cuts[0].coverage.total_count, null);
});

test('missing and unsupported firing evidence never become zero', () => {
    for (const declaration of [undefined, 'not_provided', 'not_disclosed', 'unsupported', 'read_failed', 'stale_observation']) {
        const f = frame();
        f.coverage.firings = declaration;
        delete f.net.nodes[1].runtime;
        const coverage = observationCoverage(f);
        assert.equal(coverage.state, declaration ?? 'not_provided');
        assert.equal(coverage.loaded_count, null);
        assert.equal(coverage.total_count, null);
        assert.ok(coverage.missing.length);
    }
});

test('partial records count only loaded positive rows and never claim a total', () => {
    const f = frame();
    f.net.nodes.push({ id: 'undisclosed', label: 'Undisclosed', kind: 'transition', category: 'execution' });
    let coverage = observationCoverage(f);
    assert.equal(coverage.state, 'partial');
    assert.equal(coverage.loaded_count, 1);
    assert.equal(coverage.total_count, null);
    assert.deepEqual(coverage.scope.transition_ids, ['step', 'undisclosed']);
    assert.deepEqual(coverage.missing, ['runtime.firings:undisclosed']);
    f.net.nodes[1].runtime.firings = [];
    coverage = observationCoverage(f);
    assert.equal(coverage.state, 'partial');
    assert.equal(coverage.loaded_count, null);
    assert.equal(coverage.total_count, null);
});

test('true zero needs complete known disclosure, net identity and selected cut', () => {
    const f = frame();
    f.net.nodes[1].runtime.firings = [];
    assert.deepEqual(observationCoverage(f), {
        state: 'complete', scope: { kind: 'frame_transition_firings', declaration: 'current_observations', transition_ids: ['step'] },
        loaded_count: 0, total_count: 0, missing: [],
    });
    for (const change of [
        x => { delete x.coverage.firings; },
        x => { x.coverage.firings = 'provider_defined'; },
        x => { x.coverage.firings = 'canonical_only'; },
        x => { delete x.source.net_ref; delete x.net.source.net_ref; },
        x => { x.source.verified_head_ordinal = x.net.source.verified_head_ordinal = null; x.position.cursor = null; },
    ]) {
        const unknown = structuredClone(f);
        change(unknown);
        const coverage = observationCoverage(normalizeFrame(unknown));
        assert.notEqual(coverage.state, 'complete');
        assert.equal(coverage.loaded_count, null);
        assert.equal(coverage.total_count, null);
    }
});

test('zero-valued positions and exact wire-string references are preserved', () => {
    const f = frame();
    f.source.verified_head_ordinal = f.net.source.verified_head_ordinal = 0;
    f.source.writer_fencing_epoch = f.net.source.writer_fencing_epoch = 0;
    f.source.net_ref = f.net.source.net_ref = 'net-v0';
    f.net.marking.checkpoint_ref = 'checkpoint-v0';
    f.position = { mode: 'history', cursor: 0, latest_head: 0 };
    f.coverage.firings = 'canonical_only';
    f.net.nodes[1].runtime.firings = [];
    const context = observationContext(normalizeFrame(f));
    assert.equal(context.source_cuts[0].cut.head_ordinal, 0);
    assert.equal(context.source_cuts[0].net_ref, 'net-v0');
    assert.equal(context.source_cuts[0].checkpoint_ref, 'checkpoint-v0');
    assert.equal(context.checkpoint_selector, 0);
    assert.deepEqual(context.observed_capture, { latest_head_ordinal: 0, writer_fencing_epoch: 0 });
    assert.equal(context.source_cuts[0].coverage.total_count, 0);
});

test('missing Registry cut remains null and explicit partial disclosure cannot be complete', () => {
    const f = frame();
    f.source.verified_head_ordinal = f.net.source.verified_head_ordinal = null;
    f.position = { mode: 'live', cursor: null, latest_head: null };
    const context = observationContext(normalizeFrame(f));
    assert.equal(context.source_cuts[0].cut, null);
    assert.deepEqual(context.source_cuts[0].net_ref, f.source.net_ref);
    assert.deepEqual(context.source_cuts[0].checkpoint_ref, f.net.marking.checkpoint_ref);
    assert.equal(context.source_cuts[0].coverage.state, 'partial');
    assert.equal(context.source_cuts[0].coverage.total_count, null);
    f.coverage.firings = 'partial';
    f.net.nodes[1].runtime.firings = [];
    const coverage = observationCoverage(f);
    assert.equal(coverage.state, 'partial');
    assert.equal(coverage.loaded_count, null);
    assert.equal(coverage.total_count, null);
});

test('a known empty frame scope is zero, without inferring any hidden transitions', () => {
    const f = frame();
    f.net.nodes = [];
    f.net.edges = [];
    assert.deepEqual(observationCoverage(f).scope.transition_ids, []);
    assert.equal(observationCoverage(f).loaded_count, 0);
    assert.equal(observationCoverage(f).total_count, 0);
    f.coverage.firings = 'provider_defined';
    assert.equal(observationCoverage(f).loaded_count, null);
    assert.equal(observationCoverage(f).total_count, null);
});

test('local source hints neither identify a SourceSet nor absorb observation cuts', () => {
    const a = frame(), b = frame();
    b.source.verified_head_ordinal = b.net.source.verified_head_ordinal = 22;
    b.position.latest_head = 22;
    const first = observationContext(a), second = observationContext(normalizeFrame(b));
    assert.deepEqual(first.local_source, second.local_source);
    assert.notDeepEqual(first.source_cuts[0].cut, second.source_cuts[0].cut);
    b.source.run_dir = b.net.source.run_dir = '/another/local/path';
    b.source.task_id = b.net.source.task_id = 'task-b';
    const other = observationContext(normalizeFrame(b));
    assert.notDeepEqual(first.local_source, other.local_source);
    for (const context of [first, second, other]) {
        assert.equal(context.source_set, null);
        assert.equal(context.manifest_version, null);
        assert.equal(context.query_scope, null);
        assert.deepEqual(context.source_cuts[0].source, { source_locator: null, record_ref: null, concept_id: null });
        for (const key of ['cursor', 'path_ref', 'disclosure_ref', 'query_scope'])
            assert.equal(context.source_cuts[0][key], null);
    }
});

test('helpers require normalized input and do not mutate even deeply frozen frames', () => {
    assert.throws(() => observationContext(snapshot()), /normalizeFrame/);
    assert.throws(() => observationCoverage(snapshot()), /normalizeFrame/);
    const f = freeze(frame()), before = structuredClone(f);
    const context = observationContext(f), coverage = observationCoverage(f);
    context.source_cuts[0].net_ref.version_id.value = 'changed-output';
    context.source_cuts[0].checkpoint_ref.object_id.value = 'changed-output';
    context.source_cuts[0].coverage.scope.transition_ids.push('output-only');
    coverage.scope.transition_ids.push('another-output-only');
    assert.deepEqual(f, before);
    assert.deepEqual(observationContext(f).source_cuts[0].net_ref, before.source.net_ref);
    assert.deepEqual(f.net.nodes, before.net.nodes);
    assert.deepEqual(f.net.edges, before.net.edges);
});

test('explicit unavailable disclosure takes precedence over stray rows and counts', () => {
    for (const declaration of ['not_provided', 'not_disclosed', 'unsupported', 'read_failed', 'stale_observation']) {
        const f = frame();
        f.coverage.firings = declaration;
        f.net.execution = { firing_count: 1000 };
        const coverage = observationCoverage(f);
        assert.equal(coverage.state, declaration);
        assert.equal(coverage.loaded_count, null);
        assert.equal(coverage.total_count, null);
    }
});

test('both helpers reject explicit mode, capture-epoch and task conflicts without changing normalization', () => {
    for (const historical of [false, true]) {
        for (const [key, conflicting] of [
            ['mode', 'initial_configured'], ['writer_fencing_epoch', 9], ['task_id', 'task-b'],
        ]) {
            const f = frame();
            f.net.nodes[1].runtime.firings = [];
            if (historical) {
                f.position = { mode: 'history', cursor: 18, latest_head: 40 };
                f.coverage.firings = 'canonical_only';
            }
            f.net.source[key] = conflicting;
            const before = structuredClone(f);
            assert.equal(normalizeFrame(f), f, 'the existing UI normalization behavior is unchanged');
            for (const helper of [observationContext, observationCoverage])
                assert.throws(() => helper(f), new RegExp(`Conflicting observation source ${key}`));
            assert.deepEqual(f, before);
        }
    }
});

test('optional missing capture epoch or task hints stay compatible and are never backfilled', () => {
    for (const key of ['writer_fencing_epoch', 'task_id']) {
        for (const side of ['source', 'net']) {
            for (const value of [null, undefined]) {
                const f = frame(), target = side === 'source' ? f.source : f.net.source;
                if (value === undefined) delete target[key];
                else target[key] = value;
                f.net.nodes[1].runtime.firings = [];
                const normalized = normalizeFrame(f), context = observationContext(normalized);
                assert.equal(observationCoverage(normalized).state, 'complete');
                assert.equal(observationCoverage(normalized).total_count, 0);
                if (side === 'source' && key === 'writer_fencing_epoch') {
                    assert.equal(context.observed_capture.writer_fencing_epoch, null);
                    assert.equal(context.source_cuts[0].cut.writer_fencing_epoch, null);
                }
                if (side === 'source' && key === 'task_id')
                    assert.equal(context.local_source.task_id, null);
            }
        }
    }
    const absentMode = frame();
    delete absentMode.source.mode;
    const normalized = normalizeFrame(absentMode);
    assert.equal(observationCoverage(normalized).state, 'unsupported');
    assert.equal(observationContext(normalized).source_cuts[0].cut, null);
});

test('actual dataclass and wire reference JSON shapes remain exact with backend-optional nested task ID', () => {
    const dataclass = kind => ({
        entity_type: `${kind}/v1`,
        entity_id: { kind, value: 'a'.repeat(32) },
        version_id: { kind: `${kind}_version`, value: 'b'.repeat(32) },
    });
    const wire = kind => ({
        entity_type: `${kind}/v1`, logical_id: `${kind}:${'a'.repeat(32)}`,
        version_id: `${kind}_version:${'b'.repeat(32)}`,
    });
    for (const [netReference, checkpointReference] of [
        [dataclass('net_instance'), wire('marking_checkpoint')],
        [wire('net_instance'), dataclass('marking_checkpoint')],
    ]) {
        const f = frame();
        f.source.net_ref = structuredClone(netReference);
        f.net.source.net_ref = structuredClone(netReference);
        f.net.marking.checkpoint_ref = structuredClone(checkpointReference);
        delete f.net.source.task_id;
        const before = structuredClone(f), context = observationContext(normalizeFrame(f));
        assert.deepEqual(context.source_cuts[0].net_ref, netReference);
        assert.deepEqual(context.source_cuts[0].checkpoint_ref, checkpointReference);
        assert.equal(context.local_source.task_id, f.source.task_id);
        assert.equal(context.source_cuts[0].coverage.state, 'complete');
        context.source_cuts[0].net_ref.entity_type = 'output-only';
        context.source_cuts[0].checkpoint_ref.entity_type = 'output-only';
        assert.deepEqual(f, before);
    }
});
