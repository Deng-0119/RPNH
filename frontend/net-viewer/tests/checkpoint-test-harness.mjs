import * as comparisonContextModule from '../../../cpn/frontend/static/comparison-context.mjs';
import * as comparisonModule from '../../../cpn/frontend/static/comparison-view.mjs';
import * as sourceObservationModule from '../../../cpn/frontend/static/source-observation.mjs';
import {installWorksetPanel} from '../../../cpn/frontend/static/worksets.mjs';
import {rendererRuntime} from './viewer-lifecycle-harness.mjs';
import * as activityModule from '../../../cpn/frontend/static/firing-activity.mjs';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import * as model from '../../../cpn/frontend/static/model.mjs';
import * as dashboard from '../../../cpn/frontend/static/dashboard-model.mjs';
import * as checkpoint from '../../../cpn/frontend/static/checkpoint-view.mjs';
import * as locale from '../../../cpn/frontend/static/i18n.mjs';
import {renderInspector, renderExecutionTable, element} from '../../../cpn/frontend/static/panels.mjs';
import {renderObservationPanel} from '../../../cpn/frontend/static/observation-panel.mjs';
import {resolveFiringTarget} from '../../../cpn/frontend/static/agent-members.mjs';
// A deliberately minimal DOM/renderer/layout surface. This executes the real app
// functions and panels, but does not claim browser, paint, accessibility or ELK QA.
class Node extends EventTarget {
    constructor(tag, document) { super(); this.tagName = tag; this.ownerDocument = document; this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.text = ''; this.value = ''; this.checked = false; this.hidden = false; this.open = false; this.scrollTop = 0; this.clientWidth = 900; this.clientHeight = 600; this.classList = {add: value => {this.className = [this.className, value].filter(Boolean).join(' ');}}; }
    set textContent(value) { this.text = String(value); this.children = []; }
    get textContent() { return this.text + this.children.map(n => n.textContent).join(''); }
    set innerHTML(_) { throw new Error('HTML assignment is forbidden'); }
    append(...nodes) { this.children.push(...nodes); for (const n of nodes) n.parentElement = this; }
    prepend(...nodes) { this.children.unshift(...nodes); for (const n of nodes) n.parentElement = this; }
    replaceChildren(...nodes) { this.text = ''; this.children = []; this.append(...nodes); }
    setAttribute(k, v) { this.attributes[k] = String(v); }
    getAttribute(k) { return this.attributes[k]; }
    querySelectorAll(selector) { const attr=selector.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/); return walk(this).filter(n => n !== this && (attr ? Object.hasOwn(n.dataset,attr[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())) && (attr[2]===undefined || n.dataset[attr[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]===attr[2]) : selector.startsWith('.') ? n.className?.split(' ').includes(selector.slice(1)) : n.tagName === selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(n => n !== this); this.parentElement = null; }
}
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
function dom(startup = false) {
    const ids = new Map(), selectors = new Map();
    const document = { createElement: tag => new Node(tag, document), createElementNS: (_, tag) => new Node(tag, document),
        getElementById: id => { if (!ids.has(id)) ids.set(id, new Node('section', document)); return ids.get(id); },
        querySelector: selector => { if (!selectors.has(selector)) selectors.set(selector, new Node('section', document)); return selectors.get(selector); },
        querySelectorAll: () => [] };
    document.documentElement = new Node('html', document); document.head = new Node('head', document);
    document.body = new Node('body', document); document.documentElement.append(document.head, document.body);
    document.body.append(document.getElementById('canvas-wrap'));
    document.getElementById('canvas-wrap').append(document.getElementById('paper'));
    if (startup) {
        const append = document.head.append.bind(document.head);
        document.head.append = (...nodes) => { append(...nodes); for (const node of nodes) if (node.tagName === 'script') queueMicrotask(() => node.onload()); };
    }
    document.getElementById('bootstrap').textContent = '{"showResources":true}';
    document.getElementById('error-banner').hidden = true;
    const empty = document.getElementById('canvas-empty');
    const spinner = new Node('span', document); spinner.className = 'spinner';
    empty.append(new Node('h2', document), new Node('p', document), spinner);
    globalThis.document = document;
    return document;
}

export function checkpointApp(fetcher, {startup = false} = {}) {
    const document = dom(startup), window = new EventTarget(), applied = [], motions = [], timers = new Map();
    let timerId = 0;
    const renderer = {paper: {svg: document.createElement('svg')},
        apply: graph => applied.push(graph), decorate: () => ({visibleNodes: applied.at(-1)?.nodes.length ?? 0}),
        viewport: () => ({scale: 1, tx: 9, ty: 8}), fit() {}, restore() {}, center() {},
        illustrate: value => motions.push(value)};
    let getLayout = async graph => ({width: 600, height: 400,
        nodes: new Map(graph.nodes.map((n,i) => [n.id,{x:i*240,y:30,width:224,height:126,ports:[]}])), edges:new Map()});
    const layouts = {runs: 0, get: graph => getLayout(graph)};
    const runtime = startup ? rendererRuntime(document, window) : {};
    const context = vm.createContext({...comparisonContextModule,...comparisonModule,...sourceObservationModule,...activityModule,...model, ...dashboard, ...checkpoint, ...locale, tr: locale.t,
        renderInspector, renderExecutionTable, renderObservationPanel, element, resolveFiringTarget, installWorksetPanel,
        document, window, console, structuredClone, AbortController, ...runtime,
        LayoutCache: class {constructor() {return layouts;}},
        setTimeout: (fn, ms) => { const id=++timerId;timers.set(id,{fn,ms});return id; },
        clearTimeout: id => timers.delete(id), fetch: fetcher,
        refreshCanvasText() {}, applyWireBridges: () => ({crossings:0}), injectedRenderer: renderer, injectedLayouts: layouts});
    const source = readFileSync(new URL('../../../cpn/frontend/static/app.js', import.meta.url),'utf8');
    // Bind the exact production mode-button loop without booting assets or making requests.
    const modeBindings = source.match(/    for \(const m of \['overview', 'flow', 'petri', 'list'\]\)\n        \$\(`mode-\$\{m\}`\)\.onclick = [^\n]+;/)?.[0];
    if (!modeBindings) throw new Error('Actual app mode bindings not found');
    const rendererSource = startup ? readFileSync(new URL('../../../cpn/frontend/static/renderer.mjs', import.meta.url),'utf8')
        .replace(/^import .*;\n/gm,'').replace('export class NetRenderer', 'class NetRenderer') : '';
    const appSource = source.replace(/^import .*;\n/gm,'');
    const script = rendererSource + (startup ? appSource.replace('start().catch', 'globalThis.ready=start().catch')
        : appSource.replace(/\nstart\(\)\.catch[\s\S]*$/,'')) + `
        ${startup ? '' : 'renderer=injectedRenderer; layouts=injectedLayouts;' + modeBindings}
        globalThis.api={loadFrame,loadHistory,probeCheckpoint,openPreviousNet,returnCheckpointCapture,returnToLive,
            render,select,schedule,timeControls,refreshLanguage,openComparison,comparisonPanel,crossComparison,loadActivity,cancelActivity,selectActivity,openPetri,
            viewer() {return renderer;},
            activityState() {return activity;},
            resourceState() {return tokenResource;},
            closeActivity() {cancelActivity(true);detail();},
            tab(value) {tab=value;detail();},
            install(value, probe=null) {frame=value;checkpointProbe=probe;},
            pending() {playing=true;playTimer=setTimeout(()=>loadFrame(null),1400);dragTimer=setTimeout(()=>loadFrame(130),100);},
            mode(value) {mode=value;return render();},
            history(page) {timeline.updatePage(page);historySupported=true;nextBefore=timeline.nextBefore;},
            viewport() {viewports.set('petri',{scale:2});},
            state() {return {frame,checkpointView,checkpointProbe,retainedCapture,checkpointBusy,selection,firingTarget,
                returnCount:checkpointReturns.length,viewports:[...viewports],playing,timeline,historySupported,nextBefore};}
        };`;
    vm.runInContext(script,context,{filename:'actual-checkpoint-app-with-dom.js'});
    return {api:context.api,ready:context.ready,document,window,applied,motions,timers,setLayout:fn=>{getLayout=fn;}};
}
export const response = (payload,status=200) => ({ok:status===200,status,json:async()=>structuredClone(payload)});
export const deferred = () => {let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
