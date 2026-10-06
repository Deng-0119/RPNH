import * as sourceObservationModule from '../../../cpn/frontend/static/source-observation.mjs';
import * as activityModule from '../../../cpn/frontend/static/firing-activity.mjs';
import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import * as model from '../../../cpn/frontend/static/model.mjs';
import * as dashboard from '../../../cpn/frontend/static/dashboard-model.mjs';
import * as locale from '../../../cpn/frontend/static/i18n.mjs';
import { renderInspector, renderExecutionTable, element } from '../../../cpn/frontend/static/panels.mjs';
import { renderObservationPanel } from '../../../cpn/frontend/static/observation-panel.mjs';
import { allMemberFirings, resolveFiringTarget, sameFiringTarget } from '../../../cpn/frontend/static/agent-members.mjs';
afterEach(() => locale.setLanguage('en'));

// A deliberately minimal DOM/renderer/layout surface. This executes the real app
// functions and panels, but does not claim browser, paint, accessibility or ELK QA.
class Node {
    constructor(tag, document) { this.tagName = tag; this.ownerDocument = document; this.children = []; this.dataset = {}; this.attributes = {}; this.text = ''; this.value = ''; this.checked = false; this.hidden = false; this.open = false; this.scrollTop = 0; }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map(n => n.textContent).join(''); }
    set innerHTML(_) { throw new Error('HTML assignment is forbidden'); }
    append(...nodes) { this.children.push(...nodes); for (const n of nodes) n.parentElement = this; }
    replaceChildren(...nodes) { this.text = ''; this.children = []; this.append(...nodes); }
    setAttribute(k, v) { this.attributes[k] = String(v); }
    getAttribute(k) { return this.attributes[k]; }
    querySelectorAll(selector) { return walk(this).filter(n => n !== this && (selector.startsWith('.') ? n.className?.split(' ').includes(selector.slice(1)) : n.tagName === selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(n => n !== this); }
}
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
function dom() {
    const ids = new Map(), selectors = new Map();
    const document = { createElement: tag => new Node(tag, document), createElementNS: (_, tag) => new Node(tag, document),
        getElementById: id => { if (!ids.has(id)) ids.set(id, new Node('section', document)); return ids.get(id); },
        querySelector: selector => { if (!selectors.has(selector)) selectors.set(selector, new Node('section', document)); return selectors.get(selector); },
        querySelectorAll: () => [] };
    document.documentElement = new Node('html', document); document.head = new Node('head', document);
    document.getElementById('bootstrap').textContent = '{"showResources":true}';
    document.getElementById('error-banner').hidden = true;
    const empty = document.getElementById('canvas-empty');
    const spinner = new Node('span', document); spinner.className = 'spinner';
    empty.append(new Node('h2', document), new Node('p', document), spinner);
    globalThis.document = document;
    return document;
}
const ref = id => ({ entity_type: 'transition_firing/v1', logical_id: id, version_id: 'same-display-version' });
function frame() {
    const source = { mode: 'registry_current', task_id: 'task-a', run_dir: '/display-only/run', net_ref: { entity_type: 'net_instance/v1', logical_id: 'net-a', version_id: 'v1' }, verified_head_ordinal: 10 };
    const nodes = ['a', 'b', 'c'].map((id, i) => ({ id, label: 'Same Agent name', kind: 'transition', category: 'execution', inputs: [], outputs: [], runtime: { firings: [{ firing_ref: ref('firing-' + id), status: i === 0 ? 'settled' : i === 1 ? 'started' : 'outcome_unknown', attempt_index: i + 1, admission_ordinal: i + 1 }] } }));
    nodes[1].runtime.firings.push({ firing_ref: ref('firing-b-rework'), status: 'failed_unsettled', attempt_index: 3, admission_ordinal: 4 });
    return dashboard.normalizeFrame({ schema_version: 'rpnh/dashboard/v1', source, net: { schema_version: 'rpnh/net_view/v1', source: structuredClone(source), nodes, edges: [] },
        agent_nodes: nodes.map(n => ({ transition_id: n.id, agent_ref: { logical_id: 'agent' } })),
        position: { mode: 'live', cursor: 10, latest_head: 10 }, coverage: { firings: 'current_observations' } });
}
const fakeLayout = graph => ({ width: 600, height: 400, nodes: new Map(graph.nodes.map((n, i) => [n.id, { x: i * 240, y: 30, width: 224, height: 126 }])), edges: new Map() });
function app() {
    const document = dom(), applied = [], renderer = {
        paper: { svg: document.createElement('svg') }, apply: graph => { applied.push(graph); },
        decorate: () => ({ visibleNodes: applied.at(-1)?.nodes.length ?? 0 }),
        viewport: () => ({ scale: 1, tx: 0, ty: 0 }), fit: () => {}, restore: () => {}, center: () => {}, illustrate: () => {} };
    let getLayout = async graph => fakeLayout(graph);
    const layouts = { runs: 0, get: graph => getLayout(graph) };
    const context = vm.createContext({...sourceObservationModule,...activityModule, ...model, ...dashboard, ...locale, tr: locale.t,
        renderInspector, renderExecutionTable, renderObservationPanel, element, resolveFiringTarget,
        document, window: {}, console, structuredClone, setTimeout, clearTimeout, AbortController,
        refreshCanvasText: () => {}, applyWireBridges: () => ({ crossings: 0 }), injectedRenderer: renderer, injectedLayouts: layouts });
    const source = readFileSync(new URL('../../../cpn/frontend/static/app.js', import.meta.url), 'utf8');
    const script = source.replace(/^import .*;\n/gm, '').replace(/\nstart\(\)\.catch[\s\S]*$/, '') + `
        renderer = injectedRenderer; layouts = injectedLayouts;
        globalThis.testApp = { render, select, openPetri, detail, refreshLanguage,
            install(value) { frame = value; },
            mode(value) { mode = value; return render(); },
            ticket() { return timeline.begin(); },
            state() { return { frame, graph, selection, firingTarget, mode, tab, renderSerial }; }
        };`;
    vm.runInContext(script, context, { filename: 'actual-app-with-injected-dom.js' });
    return { document, applied, api: context.testApp, setLayout: fn => { getLayout = fn; } };
}
const executionBoxes = document => walk(document.getElementById('detail')).filter(n => n.tagName === 'details' && n.dataset.memberId);
const firingButtons = document => walk(document.getElementById('detail')).filter(n => n.tagName === 'button' && n.dataset.firingId);

for (const language of ['en', 'zh-CN']) {
    test(`real Overview panel shows every mixed-state member and exact navigation in ${language}`, async () => {
        locale.setLanguage(language);
        const { api, document } = app(), value = frame(); api.install(value); await api.render();
        api.select('node', 'a');
        const buttons = firingButtons(document);
        assert.equal(buttons.length, 4); assert.equal(buttons.filter(b => b.dataset.memberId === 'b').length, 2);
        const target = allMemberFirings(value).rows.find(r => r.firing.firing_ref.logical_id === 'firing-b-rework').target;
        const button = buttons.find(b => b.dataset.firingId === model.stable(target.firing_ref));
        await button.onclick();
        assert.equal(api.state().mode, 'petri'); assert.equal(api.state().selection.id, 'b'); assert.equal(api.state().tab, 'executions');
        assert.ok(sameFiringTarget(api.state().firingTarget, target));
        assert.equal(executionBoxes(document).filter(box => box.open).length, 1);
        assert.equal(executionBoxes(document).find(box => box.open).dataset.firingId, model.stable(target.firing_ref));
        assert.ok(document.getElementById('detail').textContent.includes(language === 'en' ? 'Full firing reference' : '完整 firing 引用'));
        await api.mode('overview');
        assert.equal(api.state().selection.id, 'b');
        const open = walk(document.getElementById('detail')).find(n => n.id === 'expand-overview');
        await open.onclick();
        assert.equal(api.state().selection.id, 'b'); assert.ok(sameFiringTarget(api.state().firingTarget, target));
        assert.equal(executionBoxes(document).filter(box => box.open).length, 1);
        locale.setLanguage('en');
    });
}

test('each concurrent/rework row with the same display string reaches only its full ref and member', async () => {
    const { api, document } = app(), value = frame(); api.install(value); await api.render();
    for (const row of allMemberFirings(value).rows) {
        await api.mode('overview'); api.select('node', 'a');
        await firingButtons(document).find(button => button.dataset.firingId === row.refKey).onclick();
        assert.equal(api.state().selection.id, row.transition_id);
        assert.equal(executionBoxes(document).filter(box => box.open).length, 1);
        assert.equal(executionBoxes(document).find(box => box.open).dataset.firingId, row.refKey);
    }
});

test('execution table calls the same full-target path and opens Petri executions', async () => {
    const { api, document } = app(), value = frame(); api.install(value); await api.render(); await api.mode('list');
    const buttons = walk(document.getElementById('execution-table')).filter(n => n.tagName === 'button');
    assert.equal(buttons.length, 4);
    await buttons[0].onclick();
    assert.equal(api.state().mode, 'petri'); assert.equal(api.state().selection.id, 'b');
    assert.equal(api.state().firingTarget.firing_ref.logical_id, 'firing-b-rework');
    assert.equal(executionBoxes(document).filter(box => box.open).length, 1);
});

test('member entry remains reachable when runtime is missing; legacy rows are visible but disabled', async () => {
    const { api, document } = app(), value = frame();
    delete value.net.nodes[2].runtime; value.net.nodes[1].runtime.firings[0].firing_ref = 'legacy-short';
    api.install(value); await api.render(); api.select('node', 'a');
    const root = document.getElementById('detail');
    assert.match(root.textContent, /Member records are not provided/);
    assert.match(root.textContent, /Full reference or source identity is missing/);
    const legacy = walk(root).filter(n => n.tagName === 'button').find(n => n.dataset.memberId === 'b' && n.dataset.firingId === '');
    assert.equal(legacy.disabled, true); assert.equal(legacy.onclick(), undefined);
    const memberButton = walk(root).find(n => n.tagName === 'button' && n.textContent.endsWith(' · c'));
    await memberButton.onclick();
    assert.equal(api.state().selection.id, 'c'); assert.equal(api.state().mode, 'petri'); assert.equal(api.state().tab, 'executions');
    assert.equal(api.state().firingTarget, null);
    assert.match(document.getElementById('detail').textContent, /not provided/);
});

test('conflicting disclosure keeps all rows visible, disabled and explicitly marked', async () => {
    const { api, document } = app(), value = frame();
    value.net.nodes[1].runtime.firings = [structuredClone(value.net.nodes[0].runtime.firings[0])];
    api.install(value); await api.render(); api.select('node', 'a');
    assert.match(document.getElementById('detail').textContent, /Inconsistent disclosure.*conflicting rows/);
    const buttons = firingButtons(document).filter(b => b.dataset.memberId !== 'c');
    assert.equal(buttons.length, 2); assert.ok(buttons.every(b => b.disabled));
    await api.mode('list');
    assert.equal(walk(document.getElementById('execution-table')).filter(n => n.tagName === 'button' && n.disabled).length, 2);
});

test('same-net accepted refresh preserves exact selection; removal clears it with an explicit notice', async () => {
    const { api, document } = app(), value = frame(); api.install(value); await api.render();
    const target = allMemberFirings(value).rows[1].target; await api.openPetri(target);
    const next = structuredClone(value); next.source.verified_head_ordinal = next.net.source.verified_head_ordinal = 11;
    next.position.cursor = next.position.latest_head = 11;
    assert.equal(await api.render(value, api.ticket(), next), true);
    assert.ok(sameFiringTarget(api.state().firingTarget, target)); assert.equal(api.state().firingTarget.observed_head, 11);
    const removed = structuredClone(next); removed.net.nodes[1].runtime.firings.shift();
    assert.equal(await api.render(next, api.ticket(), removed), true);
    assert.equal(api.state().firingTarget, null); assert.equal(api.state().selection, null);
    assert.match(document.getElementById('error-banner').textContent, /no longer discloses.*cleared/);
});

test('changed net/task/run identity clears rather than selecting an identically named member', async () => {
    for (const key of ['net_ref', 'task_id', 'run_dir']) {
        const { api, document } = app(), value = frame(); api.install(value); await api.render();
        await api.openPetri(allMemberFirings(value).rows[1].target);
        const next = structuredClone(value);
        next.source[key] = next.net.source[key] = key === 'net_ref' ? { ...next.source.net_ref, version_id: 'v2' } : 'other';
        await api.render(value, api.ticket(), next);
        assert.equal(api.state().selection, null); assert.equal(api.state().firingTarget, null);
        assert.match(document.getElementById('error-banner').textContent, /Source scope changed.*cleared/);
    }
});

test('an accepted conflicting target refresh clears selection without choosing one fact', async () => {
    const { api, document } = app(), value = frame(); api.install(value); await api.render();
    const target = allMemberFirings(value).rows[1].target; await api.openPetri(target);
    const conflict = structuredClone(value); conflict.net.nodes[1].runtime.firings.push({ ...conflict.net.nodes[1].runtime.firings[0], status: 'settled' });
    await api.render(value, api.ticket(), conflict);
    assert.equal(api.state().firingTarget, null); assert.match(document.getElementById('error-banner').textContent, /inconsistent.*cleared/);
});

test('late layout for a removed target cannot clear a newer click or accepted frame', async () => {
    const { api, document, setLayout, applied } = app(), value = frame(); api.install(value); await api.render();
    const rows = allMemberFirings(value).rows; await api.openPetri(rows[1].target);
    const removed = structuredClone(value); removed.net.nodes[1].runtime.firings = [];
    let release;
    setLayout(graph => new Promise(resolve => { release = () => resolve(fakeLayout(graph)); }));
    const stale = api.render(value, api.ticket(), removed);
    setLayout(async graph => fakeLayout(graph));
    await api.openPetri(rows[3].target);
    const count = applied.length;
    release(); assert.equal(await stale, false);
    assert.equal(applied.length, count); assert.equal(api.state().frame, value);
    assert.ok(sameFiringTarget(api.state().firingTarget, rows[3].target));
    assert.equal(document.getElementById('error-banner').hidden, true);
});

test('timeline generation rejects an otherwise latest render before changing selection or graph', async () => {
    const { api, setLayout, applied } = app(), value = frame(); api.install(value); await api.render();
    const target = allMemberFirings(value).rows[1].target; await api.openPetri(target);
    const removed = structuredClone(value); removed.net.nodes[1].runtime.firings = [];
    let release; setLayout(graph => new Promise(resolve => { release = () => resolve(fakeLayout(graph)); }));
    const stale = api.render(value, api.ticket(), removed); const count = applied.length; api.ticket();
    release(); assert.equal(await stale, false); assert.equal(applied.length, count);
    assert.ok(sameFiringTarget(api.state().firingTarget, target));
});

test('language refresh keeps full selection and HTML-looking labels and refs remain plain text', async () => {
    const { api, document } = app(), value = frame();
    value.net.nodes[1].label = '<img src=x onerror=alert(1)>';
    value.net.nodes[1].runtime.firings[0].firing_ref.logical_id = '<script>alert(1)</script>';
    api.install(value); await api.render(); const target = allMemberFirings(value).rows[1].target;
    await api.openPetri(target); locale.setLanguage('zh-CN'); api.refreshLanguage();
    assert.ok(sameFiringTarget(api.state().firingTarget, target));
    assert.equal(executionBoxes(document).filter(box => box.open).length, 1);
    assert.match(document.getElementById('detail').textContent, /<script>alert\(1\)<\/script>/);
    assert.equal(document.getElementById('detail').querySelector('script'), null);
    await api.mode('overview'); assert.match(document.getElementById('detail').textContent, /<img src=x onerror=alert\(1\)>/);
    assert.equal(document.getElementById('detail').querySelector('img'), null);
    locale.setLanguage('en');
});

test('legacy rows without task/run scope remain visible and cannot be rebound across anonymous frames', async () => {
    const { api, document } = app(), value = frame();
    for (const source of [value.source, value.net.source]) { delete source.task_id; delete source.run_dir; }
    api.install(value); await api.render(); api.select('node', 'a');
    assert.equal(firingButtons(document).length, 4); assert.ok(firingButtons(document).every(b => b.disabled));
    assert.match(document.getElementById('detail').textContent, /source identity is missing/);
    const next = structuredClone(value); next.net.nodes[1].runtime.firings[0].firing_ref = ref('different-anonymous-instance');
    await api.render(value, api.ticket(), next);
    assert.equal(api.state().firingTarget, null); assert.ok(firingButtons(document).every(b => b.disabled));
});

test('a stale row callback after scope replacement cannot select the same name in the new net', async () => {
    const { api, document } = app(), value = frame(); api.install(value); await api.render(); api.select('node', 'a');
    const staleButton = firingButtons(document).find(button => button.dataset.memberId === 'b');
    const next = structuredClone(value); next.source.net_ref.version_id = next.net.source.net_ref.version_id = 'v2';
    await api.render(value, api.ticket(), next);
    assert.equal(await staleButton.onclick(), false);
    assert.equal(api.state().firingTarget, null); assert.equal(api.state().selection, null);
    assert.equal(api.state().mode, 'overview'); assert.match(document.getElementById('error-banner').textContent, /scope changed/);
});

test('repeated exact clicks and an interrupted mode switch never let a late Petri layout overwrite newer navigation', async () => {
    const { api, setLayout, applied } = app(), value = frame(); api.install(value); await api.render();
    const rows = allMemberFirings(value).rows;
    let release; setLayout(graph => new Promise(resolve => { release = () => resolve(fakeLayout(graph)); }));
    const firstClick = api.openPetri(rows[1].target);
    assert.equal(api.state().selection.id, 'b'); assert.equal(api.state().tab, 'executions');
    setLayout(async graph => fakeLayout(graph));
    await api.openPetri(rows[3].target); const count = applied.length;
    release(); assert.equal(await firstClick, false); assert.equal(applied.length, count);
    assert.ok(sameFiringTarget(api.state().firingTarget, rows[3].target));
    setLayout(graph => new Promise(resolve => { release = () => resolve(fakeLayout(graph)); }));
    const interrupted = api.openPetri(rows[2].target);
    setLayout(async graph => fakeLayout(graph)); await api.mode('overview');
    release(); assert.equal(await interrupted, false);
    assert.equal(api.state().graph.view_mode, 'overview'); assert.equal(api.state().selection.id, 'b');
    assert.ok(sameFiringTarget(api.state().firingTarget, rows[2].target));
});

test('withheld disclosure and complete-empty disclosure have distinct execution-table empty states', async () => {
    const { api, document } = app(), value = frame(); value.coverage.firings = 'not_disclosed';
    api.install(value); await api.render(); await api.mode('list');
    assert.match(document.getElementById('execution-table').textContent, /does not provide|not provide/);
    const empty = structuredClone(value); empty.coverage.firings = 'current_observations';
    for (const node of empty.net.nodes) node.runtime.firings = [];
    await api.render(value, api.ticket(), empty);
    assert.equal(document.getElementById('execution-table').textContent, locale.t('该观察范围内尚无执行记录。'));
});

test('a disclosed member entry opens its executions without silently choosing a firing', async () => {
    const { api, document } = app(), value = frame(); api.install(value); await api.render(); api.select('node', 'a');
    const memberButton = walk(document.getElementById('detail')).find(node => node.tagName === 'button' && node.textContent.endsWith(' · b'));
    await memberButton.onclick();
    assert.equal(api.state().selection.id, 'b'); assert.equal(api.state().mode, 'petri'); assert.equal(api.state().tab, 'executions');
    assert.equal(api.state().firingTarget, null); assert.equal(executionBoxes(document).length, 2);
    assert.equal(executionBoxes(document).filter(box => box.open).length, 0);
    assert.match(document.getElementById('detail').textContent, /2 record rows disclosed/);
});

test('separate Agent cards, Petri executions and list all flag cross-card conflicts before click, then refresh cleanly', async () => {
    const { api, document } = app(), value = frame();
    value.agent_nodes = value.net.nodes.map(node => ({ transition_id: node.id, semantic_group: 'group-' + node.id }));
    value.net.nodes[1].runtime.firings = [structuredClone(value.net.nodes[0].runtime.firings[0])];
    api.install(value); await api.render();
    assert.equal(api.state().graph.nodes.length, 3);
    for (const id of ['a', 'b']) {
        api.select('node', id);
        const summary = api.state().graph.nodes.find(node => node.id === id).agent_summary;
        assert.equal(summary.complete, false); assert.equal(summary.conflicts, 1); assert.equal(summary.counts.settled, 0);
        assert.match(document.getElementById('detail').textContent, /Inconsistent disclosure/);
        const buttons = firingButtons(document); assert.equal(buttons.length, 1); assert.equal(buttons[0].disabled, true);
        assert.equal(await buttons[0].onclick(), undefined); assert.equal(api.state().mode, 'overview');
    }
    api.select('node', 'c'); assert.equal(firingButtons(document)[0].disabled, false, 'unrelated same-short reference remains navigable');
    await api.openPetri({ transition_id: 'a' });
    assert.equal(api.state().tab, 'executions'); assert.match(document.getElementById('detail').textContent, /Inconsistent reference/);
    assert.equal(executionBoxes(document).filter(box => box.open).length, 0);
    await api.mode('list');
    assert.equal(walk(document.getElementById('execution-table')).filter(node => node.tagName === 'button' && node.disabled).length, 2);
    const next = structuredClone(value); next.net.nodes[1].runtime.firings[0].firing_ref.logical_id = 'repaired-b-reference';
    await api.render(value, api.ticket(), next); await api.mode('overview'); api.select('node', 'a');
    assert.equal(api.state().graph.nodes.find(node => node.id === 'a').agent_summary.complete, true);
    assert.equal(firingButtons(document)[0].disabled, false);
    assert.doesNotMatch(document.getElementById('detail').textContent, /Inconsistent/);
    await firingButtons(document)[0].onclick();
    assert.equal(api.state().selection.id, 'a'); assert.equal(api.state().firingTarget.firing_ref.logical_id, 'firing-a');
});
