import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { normalizeFrame } from '../../../cpn/frontend/static/dashboard-model.mjs';
import { renderObservationPanel } from '../../../cpn/frontend/static/observation-panel.mjs';
import { EN } from '../../../cpn/frontend/static/messages.mjs';

// Minimal DOM surface: no browser, layout engine, dependencies or HTML parser.
class Node {
    constructor(tag, document) {
        this.tagName = tag;
        this.ownerDocument = document;
        this.children = [];
        this.dataset = {};
        this.hidden = false;
        this.open = false;
        this.text = '';
    }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
    set innerHTML(_) { throw new Error('HTML assignment is forbidden'); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.text = ''; this.children = children; }
    querySelector(tag) { return walk(this).find(node => node !== this && node.tagName === tag) ?? null; }
}
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
function host() {
    const document = { createElement: tag => new Node(tag, document) };
    return document.createElement('section');
}
const en = key => { assert.ok(Object.hasOwn(EN, key), `missing built-in translation: ${key}`); return EN[key]; };
const zh = key => key;
const stateText = {
    complete: ["This frame's disclosed scope is complete (complete)", '本帧披露范围完整（complete）'],
    partial: ["This frame's disclosed scope is partly available (partial)", '本帧披露范围部分可用（partial）'],
    not_provided: ['Disclosure not provided (not_provided)', '未提供披露（not_provided）'],
    not_disclosed: ['Not disclosed (not_disclosed)', '未披露（not_disclosed）'],
    unsupported: ['Disclosure unsupported (unsupported)', '不支持披露（unsupported）'],
    read_failed: ['Disclosure read failed (read_failed)', '披露读取失败（read_failed）'],
    stale_observation: ['Stale observation (stale_observation)', '过期观察（stale_observation）'],
};
const render = (root, value, mode = 'flow', translate = en) => renderObservationPanel(root, value, { mode, translate });
const field = (root, key) => walk(root).find(node => node.dataset.field === key)?.children[1].textContent;
const reference = (root, key) => walk(root).find(node => node.dataset.reference === key)?.querySelector('pre').textContent;
const ref = (kind, id, version) => ({ kind, object_id: { value: id }, version_id: { value: version } });
function snapshot() {
    return {
        schema_version: 'rpnh/net_view/v1',
        source: { mode: 'registry_current', task_id: 'task-a', run_dir: '/display-only/run',
            net_ref: ref('net_instance', 'net-a', 'net-a-v2'), verified_head_ordinal: 18, writer_fencing_epoch: 4 },
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
        position: { mode: 'live', cursor: 13, latest_head: 40 },
        coverage: { firings: 'current_observations', history: 'current_net_canonical_checkpoints' } });
}
function freeze(value) {
    if (value && typeof value === 'object') {
        Object.values(value).forEach(freeze);
        Object.freeze(value);
    }
    return value;
}

test('current cut 18, selector 13 and frame-reported latest 40 remain separate', () => {
    const root = host();
    render(root, frame());
    assert.equal(root.hidden, false);
    assert.equal(root.dataset.state, 'available');
    assert.equal(field(root, 'mode'), 'Current observation (current)');
    assert.equal(field(root, 'head'), '18');
    assert.equal(field(root, 'selector'), '13');
    assert.equal(field(root, 'latest'), '40');
    assert.equal(field(root, 'coverage'), stateText.complete[0]);
    assert.equal(field(root, 'loaded'), '1');
    assert.equal(field(root, 'total'), '1');
    assert.match(root.textContent, /not a pagination cursor.*does not prove a saved checkpoint/);
    assert.match(root.textContent, /transitions explicitly listed in this frame/);
    assert.match(root.textContent, /only this frame's disclosed scope is complete/);
    assert.doesNotMatch(root.textContent, /writer_fencing_epoch|marking.epoch/);
    const next = frame(); next.position.latest_head = 22;
    render(root, next);
    assert.equal(field(root, 'latest'), '22', 'must not accumulate latest head across frames');
});

test('history shows canonical-as-of with its selected cut and frame capture separately', () => {
    const value = frame(), root = host();
    value.position = { mode: 'history', cursor: 13, latest_head: 40 };
    value.source.verified_head_ordinal = value.net.source.verified_head_ordinal = 13;
    value.coverage.firings = 'canonical_only';
    render(root, normalizeFrame(value), 'petri');
    assert.equal(field(root, 'mode'), 'Selected historical observation (canonical-as-of)');
    assert.equal(field(root, 'head'), '13');
    assert.equal(field(root, 'latest'), '40');
    assert.equal(field(root, 'coverage'), stateText.complete[0]);
});

test('complete disclosed scope preserves genuine zero counts and position zero', () => {
    const value = frame(), root = host();
    value.source.verified_head_ordinal = value.net.source.verified_head_ordinal = 0;
    value.position = { mode: 'history', cursor: 0, latest_head: 0 };
    value.coverage.firings = 'canonical_only';
    value.net.nodes[1].runtime.firings = [];
    render(root, normalizeFrame(value), 'list');
    for (const key of ['head', 'selector', 'latest', 'loaded', 'total']) assert.equal(field(root, key), '0', key);
    assert.equal(field(root, 'coverage'), stateText.complete[0]);
});

test('partial positive rows have unknown total; unproven empty and missing positions stay unknown', () => {
    const value = frame(), root = host();
    value.net.nodes.push({ id: 'other', kind: 'transition', category: 'execution', label: 'Other' });
    render(root, normalizeFrame(value));
    assert.equal(field(root, 'coverage'), stateText.partial[0]);
    assert.equal(field(root, 'loaded'), '1');
    assert.equal(field(root, 'total'), 'Unknown');
    assert.doesNotMatch(root.textContent, /only this frame's disclosed scope is complete/);
    value.net.nodes[1].runtime.firings = [];
    value.source.verified_head_ordinal = value.net.source.verified_head_ordinal = null;
    value.position = { mode: 'live', cursor: null, latest_head: null };
    render(root, normalizeFrame(value));
    for (const key of ['head', 'selector', 'latest', 'loaded', 'total']) assert.equal(field(root, key), 'Unknown', key);
});

for (const state of ['not_provided', 'not_disclosed', 'unsupported', 'read_failed', 'stale_observation']) {
    test(`explicit ${state} disclosure retains unknown counts despite stray firing rows`, () => {
        const value = frame(), root = host();
        value.coverage.firings = state;
        value.net.execution = { firing_count: 1000 };
        render(root, normalizeFrame(value));
        assert.equal(field(root, 'coverage'), stateText[state][0]);
        assert.equal(field(root, 'loaded'), 'Unknown');
        assert.equal(field(root, 'total'), 'Unknown');
        assert.equal(root.dataset.state, 'available', 'unavailable disclosure is a valid context');
    });
}

test('initial and legacy frames retain their supported, limited meanings', () => {
    const initial = snapshot(), root = host();
    initial.source = { mode: 'initial_configured' };
    delete initial.marking;
    delete initial.nodes[1].runtime;
    render(root, normalizeFrame(initial));
    assert.equal(field(root, 'coverage'), stateText.unsupported[0]);
    for (const key of ['head', 'selector', 'latest', 'loaded', 'total']) assert.equal(field(root, key), 'Unknown', key);
    assert.equal(reference(root, 'net_ref'), 'Unknown');
    assert.equal(reference(root, 'checkpoint_ref'), 'Unknown');
    const legacy = snapshot(); legacy.execution = { head_ordinal: 21, firing_count: 999 };
    render(root, normalizeFrame(legacy));
    for (const key of ['head', 'selector', 'latest']) assert.equal(field(root, key), '21', key);
    assert.equal(field(root, 'coverage'), stateText.partial[0]);
    assert.equal(field(root, 'loaded'), '1');
    assert.equal(field(root, 'total'), 'Unknown');
});

test('optional missing source hints remain compatible; absent disclosure never proves completeness', () => {
    const root = host();
    for (const key of ['task_id', 'writer_fencing_epoch']) {
        for (const side of ['source', 'net']) {
            for (const empty of [null, undefined]) {
                const value = frame(), target = side === 'source' ? value.source : value.net.source;
                if (empty === undefined) delete target[key]; else target[key] = empty;
                render(root, normalizeFrame(value));
                assert.equal(root.dataset.state, 'available');
                assert.equal(field(root, 'coverage'), stateText.complete[0]);
            }
        }
    }
    const value = frame(); delete value.coverage.firings;
    render(root, normalizeFrame(value));
    assert.equal(field(root, 'coverage'), stateText.partial[0], 'already disclosed rows retain their positive loaded count');
    assert.equal(field(root, 'loaded'), '1');
    assert.equal(field(root, 'total'), 'Unknown');
    delete value.net.nodes[1].runtime;
    render(root, normalizeFrame(value));
    assert.equal(field(root, 'coverage'), stateText.not_provided[0]);
    assert.equal(field(root, 'loaded'), 'Unknown');
    assert.equal(field(root, 'total'), 'Unknown');
    const absentMode = frame(); delete absentMode.source.mode;
    render(root, normalizeFrame(absentMode));
    assert.equal(root.dataset.state, 'available');
    assert.equal(field(root, 'coverage'), stateText.unsupported[0]);
    assert.equal(field(root, 'head'), 'Unknown');
});

test('string, wire and dataclass net/checkpoint references are complete exact JSON', () => {
    const dataclass = kind => ({ entity_type: `${kind}/v1`, entity_id: { kind, value: 'a'.repeat(32) },
        version_id: { kind: `${kind}_version`, value: 'b'.repeat(32) } });
    const wire = kind => ({ entity_type: `${kind}/v1`, logical_id: `${kind}:${'a'.repeat(32)}`,
        version_id: `${kind}_version:${'b'.repeat(32)}` });
    for (const make of [kind => `${kind}:exact-long-reference`, wire, dataclass]) {
        const value = frame(), root = host(), net = make('net_instance'), checkpoint = make('marking_checkpoint');
        value.source.net_ref = value.net.source.net_ref = net;
        value.net.marking.checkpoint_ref = checkpoint;
        delete value.net.source.task_id;
        render(root, normalizeFrame(value));
        assert.equal(reference(root, 'net_ref'), JSON.stringify(net, null, 2));
        assert.equal(reference(root, 'checkpoint_ref'), JSON.stringify(checkpoint, null, 2));
        assert.deepEqual(JSON.parse(reference(root, 'net_ref')), net);
        assert.deepEqual(JSON.parse(reference(root, 'checkpoint_ref')), checkpoint);
        assert.equal(root.querySelector('details').open, false);
        assert.equal(root.querySelector('a'), null);
    }
});

test('HTML-looking IDs stay plain text and the deeply frozen input is unchanged', () => {
    const value = frame(), root = host();
    value.source.net_ref = value.net.source.net_ref = '<img src=x onerror=alert(1)>未结算';
    value.net.marking.checkpoint_ref = '<script>alert(1)</script>';
    const before = structuredClone(value);
    render(root, freeze(value));
    assert.equal(reference(root, 'net_ref'), JSON.stringify(before.source.net_ref));
    assert.equal(reference(root, 'checkpoint_ref'), JSON.stringify(before.net.marking.checkpoint_ref));
    assert.equal(root.querySelector('img'), null);
    assert.equal(root.querySelector('script'), null);
    assert.deepEqual(value, before);
});

test('normal → source conflict → normal clears stale contents without escaping or inventing coverage', () => {
    for (const history of [false, true]) {
        for (const [key, conflict] of [['mode', 'initial_configured'], ['task_id', 'task-b'], ['writer_fencing_epoch', 9]]) {
            const value = frame(), root = host();
            if (history) { value.position = { mode: 'history', cursor: 18, latest_head: 40 }; value.coverage.firings = 'canonical_only'; }
            render(root, value); root.querySelector('details').open = true;
            const invalid = structuredClone(value); invalid.net.source[key] = conflict;
            assert.equal(normalizeFrame(invalid), invalid, 'legacy normalization still accepts these conflicts');
            assert.doesNotThrow(() => render(root, invalid));
            assert.equal(root.dataset.state, 'unavailable');
            assert.equal(root.textContent, "This frame's observation contextContext unavailable");
            assert.equal(root.querySelector('details'), null);
            assert.equal(field(root, 'coverage'), undefined);
            assert.doesNotMatch(root.textContent, /read_failed|net-a|18|40/);
            render(root, value);
            assert.equal(root.dataset.state, 'available');
            assert.equal(field(root, 'head'), '18');
            assert.equal(root.querySelector('details').open, true);
            assert.doesNotMatch(root.textContent, /Context unavailable/);
        }
    }
});

test('language updates only built-in copy; details preserve both open and closed state', () => {
    const value = frame(), root = host();
    value.source.net_ref = value.net.source.net_ref = '未结算';
    render(root, value);
    const fields = ['head', 'selector', 'latest', 'loaded', 'total'];
    const prior = fields.map(key => field(root, key)), exact = reference(root, 'net_ref');
    root.querySelector('details').open = true;
    render(root, value, 'flow', zh);
    assert.match(root.textContent, /本帧观察上下文/);
    assert.doesNotMatch(root.textContent, /This frame's observation context/);
    assert.deepEqual(fields.map(key => field(root, key)), prior);
    assert.equal(reference(root, 'net_ref'), exact);
    assert.equal(field(root, 'mode'), '当前观察（current）');
    assert.equal(field(root, 'coverage'), stateText.complete[1]);
    assert.equal(root.querySelector('details').open, true);
    render(root, value, 'petri', en);
    assert.equal(root.querySelector('details').open, true);
    root.querySelector('details').open = false;
    render(root, value, 'list', zh);
    assert.equal(root.querySelector('details').open, false);
    const other = host(); render(other, value);
    assert.equal(other.querySelector('details').open, false, 'state is local to each card');
});

test('Overview and unrecognized modes hide the whole technical card without generating context', () => {
    const root = host();
    render(root, frame()); root.querySelector('details').open = true;
    for (const mode of ['overview', 'other']) {
        render(root, null, mode);
        assert.equal(root.hidden, true);
        assert.equal(root.dataset.state, 'available');
    }
    for (const mode of ['flow', 'petri', 'list']) {
        render(root, frame(), mode);
        assert.equal(root.hidden, false);
        assert.equal(root.querySelector('details').open, true);
    }
});

test('summary-only wiring follows frame commit, preserves request-error behavior and refreshes language', () => {
    const read = name => readFileSync(new URL('../../../cpn/frontend/static/' + name, import.meta.url), 'utf8');
    const app = read('app.js'), panel = read('observation-panel.mjs'), html = read('index.html'), css = read('style.css');
    assert.equal((app.match(/renderObservationPanel\(/g) ?? []).length, 1);
    const summary = app.slice(app.indexOf('function summary()'), app.indexOf('async function render('));
    assert.match(summary, /renderObservationPanel\(\$\('observation-panel'\), frame, \{ mode, translate: tr \}\)/);
    const rendering = app.slice(app.indexOf('async function render('), app.indexOf('function timeControls()'));
    assert.ok(rendering.indexOf('!timeline.accepts(ticket)') < rendering.indexOf('frame = candidate;'));
    assert.ok(rendering.indexOf('frame = candidate;') < rendering.indexOf('summary();'));
    const loading = app.slice(app.indexOf('async function loadFrame('), app.indexOf('function stop()'));
    const failure = loading.slice(loading.lastIndexOf('catch (error)'));
    assert.doesNotMatch(failure, /renderObservationPanel|summary\(|frame\s*=/);
    const language = app.slice(app.indexOf('function refreshLanguage()'), app.indexOf("$('language').addEventListener"));
    assert.match(language, /summary\(\)/);
    assert.match(html, /<section aria-labelledby="observation-panel-title" hidden id="observation-panel"><\/section>/);
    assert.match(panel, /container\.hidden = !\['flow', 'petri', 'list'\]\.includes\(mode\)/);
    assert.match(panel, /observationContext\(frame\)/);
    assert.doesNotMatch(panel, /\b(?:fetch|latestHead|localStorage|sessionStorage|innerHTML|insertAdjacentHTML)\b/);
    assert.doesNotMatch(panel, /error-banner|timeline|writer_fencing_epoch|referenceText/);
    assert.match(css, /#observation-panel pre\{[^}]*white-space:pre-wrap;[^}]*overflow-wrap:anywhere/);
});


test('both modes and all seven disclosure states have explicit English and Chinese descriptions', () => {
    const root = host();
    for (const [state, [english, chinese]] of Object.entries(stateText)) {
        const value = frame(); value.coverage.firings = state === 'complete' ? 'current_observations' : state;
        render(root, normalizeFrame(value), 'flow', en);
        assert.equal(field(root, 'coverage'), english);
        assert.equal(field(root, 'mode'), 'Current observation (current)');
        render(root, value, 'flow', zh);
        assert.equal(field(root, 'coverage'), chinese);
        assert.equal(field(root, 'mode'), '当前观察（current）');
    }
    const historical = frame(); historical.position = { mode: 'history', cursor: 18, latest_head: 40 };
    historical.coverage.firings = 'canonical_only';
    render(root, normalizeFrame(historical), 'flow', en);
    assert.equal(field(root, 'mode'), 'Selected historical observation (canonical-as-of)');
    render(root, historical, 'flow', zh);
    assert.equal(field(root, 'mode'), '所选历史观察（canonical-as-of）');
    historical.net.source.task_id = 'conflicting-task';
    render(root, historical, 'flow', zh);
    assert.equal(root.textContent, '本帧观察上下文上下文不可用');
});

test('local catch covers context generation and does not misclassify unrelated DOM failures', () => {
    const root = host(), create = root.ownerDocument.createElement;
    root.ownerDocument.createElement = tag => {
        if (tag === 'dl') throw new Error('Synthetic unrelated DOM error');
        return create(tag);
    };
    assert.throws(() => render(root, frame()), /Synthetic unrelated DOM error/);
    assert.notEqual(root.dataset.state, 'unavailable');
    const invalid = frame(); invalid.net.source.task_id = 'conflicting-task';
    assert.doesNotThrow(() => render(root, invalid));
    assert.equal(root.dataset.state, 'unavailable');
});
