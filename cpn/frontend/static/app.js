import { installComparisonContextPanel } from './comparison-context.mjs';
import { installComparisonPanel } from './comparison-view.mjs';
import { SourceObservationState, sourceObservationPath, renderSourceObservation } from './source-observation.mjs';
import { activityRequest, activityPath, normalizeActivityPage, mergeActivity, resolveActivityTarget } from './firing-activity.mjs';
import { installWorksetPanel } from './worksets.mjs';
import { selectorForFrame, checkpointPath, normalizeCheckpointView, tokenResourceRequest, tokenResourcePath, normalizeTokenResourceMetadata } from './checkpoint-view.mjs';
import { t as tr, messageError, getLanguage, setLanguage, applyLanguage } from './i18n.mjs';
import { head, referenceText, searchText, firingSummary, stable } from './model.mjs';
import { normalizeFrame, scope, nodePresentation, displayGraph, visibleSelection, TimelineState, isAdjacent } from './dashboard-model.mjs';
import { LayoutCache } from './layout.mjs';
import { NetRenderer } from './renderer.mjs';
import { refreshCanvasText } from './canvas-text.mjs';
import { applyWireBridges } from './wire-geometry.mjs';
import { renderInspector, renderExecutionTable, element } from './panels.mjs';
import { renderObservationPanel } from './observation-panel.mjs';
import { resolveFiringTarget } from './agent-members.mjs';
const $ = id => document.getElementById(id), timeline = new TimelineState();
const sourceObservation = new SourceObservationState();
let sourceObservationController=null;
function drawSourceObservation(){renderSourceObservation($('source-observation-panel'),sourceObservation,{translate:tr,onSelect:loadSourceObservation,onClose:()=>{sourceObservationController?.abort();sourceObservation.close();drawSourceObservation();}});}
async function loadSourceObservation(reference=null){
    crossComparison.close();comparisonPanel.close();
    sourceObservationController?.abort();sourceObservationController=new AbortController();
    const selected=sourceObservation.begin(reference);drawSourceObservation();
    try{const raw=await request(sourceObservationPath(reference),sourceObservationController.signal);sourceObservation.accept(selected,raw);}
    catch(error){if(error.name!=='AbortError')sourceObservation.fail(selected,error.status);}
    drawSourceObservation();
}
$('source-observations').onclick=()=>loadSourceObservation();
let emptyFailure=null;
let noticeMessage = null, healthMessage = () => tr("加载本地展示组件…");
function showMessage(value) { return typeof value === 'function' ? value() : value; }
function setHealth(message, state) { healthMessage = message; $('health').textContent = showMessage(message); if (state)
    $('health').dataset.state = state; }
applyLanguage(document);
$('language').value = getLanguage();
let renderer, layouts, frame, graph, layout, selection = null, firingTarget = null, mode = 'overview', tab = 'about', controller, timer, playTimer, playing = false, findIndex = -1, renderSerial = 0, dragTimer, playGeneration = 0, historySerial = 0;
const comparisonOnly=Boolean(JSON.parse($('bootstrap').textContent).comparisonOnly);
let showResources = JSON.parse($('bootstrap').textContent).showResources, hasDashboard = true, historySupported = false, nextBefore = null, latestHead = null;
const viewports = new Map();
let checkpointView = null, checkpointProbe = null, probeSerial = 0, retainedCapture = false, checkpointBusy = false;
const checkpointReturns = [];
const crossComparison = installComparisonContextPanel($('comparison-context-panel'), {request, translate:tr, events:window,
    onOpen:()=>{comparisonPanel.close();stop();clearTimeout(timer);controller?.abort();timeline.begin();++historySerial;++probeSerial;checkpointBusy=false;cancelActivity();cancelTokenResource();},onClose:()=>schedule()});
$('compare-nets').onclick=()=>crossComparison.open();
const comparisonPanel = installComparisonPanel($('comparison-panel'), {request, translate:tr, events:window, onClose:()=>schedule()});
function openComparison() {
    if (!frame) return;
    crossComparison.close();
    // Validate exact choices before interrupting the existing observation flow.
    try {comparisonPanel.open(frame, checkpointView ? [] : timeline.items);}
    catch {comparisonPanel.close();notice(()=>tr('当前视图没有可比较的精确检查点。'));return;}
    stop();clearTimeout(timer);controller?.abort();timeline.begin();++historySerial;++probeSerial;
    checkpointBusy=false;timeControls();
    cancelActivity();cancelTokenResource();
}
$('compare-checkpoints').onclick=openComparison;

let activity = {observation:null,loading:false,error:null,stale:false,paused:false,expanded:new Set()}, activityGeneration=0, activityController=null;
function cancelActivity(resume=false) {
    const wasPaused=activity.paused;
    ++activityGeneration; activityController?.abort(); activityController=null;
    activity={observation:null,loading:false,error:null,stale:false,paused:false,expanded:new Set()};
    if(wasPaused && frame && graph) {summary();timeControls();if(resume) schedule();}
}
function activityScope() {
    try {return stable({request:activityRequest(frame,[selection.id]),source:{task_id:frame.source.task_id,run_dir:frame.source.run_dir}});}
    catch {return null;}
}
async function loadActivity(action='load') {
    if (!frame || selection?.kind!=='node' || (action==='more' && (activity.loading || activity.stale || !activity.observation?.next_cursor))) return;
    if (activity.loading && action!=='refresh') return;
    const currentScope=activityScope(); if (!currentScope) return;
    stop();clearTimeout(timer);
    const old=activity.observation, cursor=action==='more'?old.next_cursor:null;
    const params=activityRequest(frame,[selection.id],cursor), navigation=timeline.serial;
    activityController?.abort(); const generation=++activityGeneration;
    activityController=new AbortController(); activity={...activity,loading:true,error:null,paused:true};detail();summary();timeControls();
    const accepted=()=>generation===activityGeneration && timeline.accepts(navigation) && currentScope===activityScope();
    try {
        const raw=await request(activityPath(params),activityController.signal);
        if (!accepted()) return;
        const origin=raw?.view?.activity?.source_identity;
        if (origin?.source && (origin.source.task_id!==frame.source.task_id || origin.source.run_dir!==frame.source.run_dir
            || (old && stable(origin)!==stable({task_ref:old.context.query_scope.task_ref,run_ref:old.context.query_scope.run_ref,source:old.context.query_scope.source})))) {const e=new Error('access changed');e.status=403;throw e;}
        const page=normalizeActivityPage(raw,params,frame);
        const next=mergeActivity(action==='more'?old:null,page);
        activity={observation:next,loading:false,error:null,stale:false,paused:true,expanded:action==='more'?activity.expanded:new Set()};
    } catch(error) {
        if (!accepted() || error.name==='AbortError') return;
        if (error.status===403) {cancelActivity();activity.error='access_changed';detail();return;}
        activity={...activity,loading:false,error:error.status===501?'unsupported':error.status===409?'stale_observation':'read_failed',
            stale:activity.stale || error.status===409};
    } finally {
        if (accepted()) {activity.loading=false;detail();summary();timeControls();}
    }
}
function selectActivity(target) {
    const resolved=resolveActivityTarget(activity.observation,target);
    if (!resolved || selection?.id!==resolved.transition_id) return false;
    selection={kind:'node',id:resolved.transition_id};firingTarget=null;
    activity.expanded.add(stable(resolved.firing_ref));return true;
}

let tokenResource={request:null,metadata:null,identity:null,loading:false,error:null}, resourceGeneration=0, resourceController=null;
function cancelTokenResource() {
    ++resourceGeneration;resourceController?.abort();resourceController=null;
    tokenResource={request:null,metadata:null,identity:null,loading:false,error:null};
}
function resourceRequest(token) {
    return tokenResourceRequest(frame,token,checkpointView?.capture??null);
}
function resourceScope(token) {
    try { return stable({request:resourceRequest(token),source:{task_id:frame.source.task_id,run_dir:frame.source.run_dir},selection}); }
    catch { return null; }
}
async function loadTokenResource(token) {
    if (!frame || selection?.kind!=='node' || selection.id!==token.place) return;
    const bound=resourceScope(token);if(!bound)return;
    const params=resourceRequest(token);
    if(tokenResource.loading && stable(tokenResource.request)===stable(params))return;
    const sameRequest=stable(tokenResource.request)===stable(params);
    const old=sameRequest?tokenResource.metadata:null, identity=sameRequest?tokenResource.identity:null;
    resourceController?.abort();const generation=++resourceGeneration,navigation=timeline.serial;
    resourceController=new AbortController();tokenResource={request:params,metadata:old,identity,loading:true,error:null};detail();
    const accepted=()=>generation===resourceGeneration && timeline.accepts(navigation) && bound===resourceScope(token);
    try {
        const value=await request(tokenResourcePath(params),resourceController.signal);
        if(!accepted())return;
        if(value?.frame?.source?.task_id!==frame.source.task_id || value?.frame?.source?.run_dir!==frame.source.run_dir) {
            const error=new Error('access changed');error.status=403;throw error;
        }
        const metadata=normalizeTokenResourceMetadata(value,params,frame);
        // This frame's first successful response teaches exact task/run identity.
        // Retain that anchor for this request scope even when a later 403 clears details.
        if(identity && ['task_ref','run_ref'].some(k=>stable(identity[k])!==stable(metadata.scope[k]))) {
            const error=new Error('access changed');error.status=403;throw error;
        }
        tokenResource={request:params,metadata,identity:identity??{
            task_ref:structuredClone(metadata.scope.task_ref),run_ref:structuredClone(metadata.scope.run_ref)},loading:false,error:null};
    } catch(error) {
        if(!accepted() || error.name==='AbortError')return;
        const status=error.status;
        tokenResource={request:params,metadata:status===403?null:old,identity,loading:false,
            error:status===403?'access_changed':status===409?'stale_observation':[400,404,501].includes(status)?'unsupported':'read_failed'};
    } finally {if(accepted()){tokenResource.loading=false;detail();}}
}

function loadScript(src) { return new Promise((resolve, reject) => { const s = document.createElement('script'); s.src = src; s.onload = resolve; s.onerror = () => reject(messageError("本地展示资产未构建，请按分支手册构建或安装完整 wheel。")); document.head.append(s); }); }
async function request(path, signal) { const r = await fetch(path, { cache: 'no-store', signal }); const value = await r.json(); if (!r.ok) {
    const e = new Error(value.error ?? `HTTP ${r.status}`);
    e.status = r.status;
    throw e;
} return value; }
function clearMotion() { renderer?.paper.svg.querySelectorAll('.token-illustration').forEach(node => node.remove()); }
function notice(message) { noticeMessage = message; const text = showMessage(message); $('error-banner').hidden = !text; $('error-banner').textContent = text ?? ''; }
function detail() {
    const chosen = mode === 'overview' ? visibleSelection(graph, selection) : selection;
    $('inspector').hidden = !chosen;
    document.querySelector('.detail-tabs').hidden = mode === 'overview';
    if (!frame || !chosen)
        return;
    const item = (mode === 'overview' ? (chosen.kind === 'node' ? graph.nodes : graph.edges) : (chosen.kind === 'node' ? frame.net.nodes : frame.net.edges)).find(n => n.id === chosen.id);
    $('detail-title').textContent = item ? (chosen.kind === 'node' ? nodePresentation(item, frame).name : tr("连接关系")) : tr("步骤详情");
    for (const t of ['about', 'executions', 'evidence'])
        $(`tab-${t}`).setAttribute('aria-pressed', String(tab === t));
    renderInspector($('detail'), frame, chosen, tab, firingTarget, select, graph, openPetri, {state:activity,load:loadActivity,close:()=>{cancelActivity(true);detail();},select:selectActivity}, {state:tokenResource,request:resourceRequest,load:loadTokenResource,close:()=>{cancelTokenResource();detail();}});
}
function select(kind, id, firing = null) {
    if (firing) return openPetri(firing);
    if (selection?.kind!==kind || selection?.id!==id) {cancelActivity(true);cancelTokenResource();}
    selection = { kind, id }; firingTarget = null; decorate(); detail();
}
function selectRendered(kind, id) {
    if (kind === 'edge') {
        const e = graph.edges.find(e => e.id === id);
        if (e?.agent_relation) { select('edge', e.id); return; }
        if (e?.hidden_place && !e.overview) {
            select('node', e.hidden_place);
            return;
        }
        if (e?.source_ids)
            id = e.source_ids[0];
    }
    select(kind, id);
}
function drawMap() {
    if (!layout || !graph)
        return;
    const svg = $('minimap');
    svg.replaceChildren();
    svg.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);
    const ns = 'http://www.w3.org/2000/svg';
    for (const e of graph.edges) {
        if (!showResources && e.resource)
            continue;
        const x = layout.edges.get(e.id)?.sections[0];
        if (!x)
            continue;
        const p = document.createElementNS(ns, 'path');
        p.setAttribute('d', [x.startPoint, ...(x.bendPoints ?? []), x.endPoint].map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' '));
        p.setAttribute('stroke', '#b8c5df');
        p.setAttribute('stroke-width', '4');
        p.setAttribute('fill', 'none');
        svg.append(p);
    }
    for (const n of graph.nodes) {
        if (!showResources && n.category === 'resource')
            continue;
        const p = layout.nodes.get(n.id), r = document.createElementNS(ns, 'rect');
        for (const [k, v] of Object.entries({ x: p.x, y: p.y, width: p.width, height: p.height, rx: 8, fill: n.display?.role === 'entry' ? '#359f85' : n.display?.role === 'exit' ? '#9184ce' : '#8b9fcd' }))
            r.setAttribute(k, v);
        svg.append(r);
    }
}
function decorate() {
    if (!graph)
        return;
    const counts = renderer.decorate({ showResources, search: $('search').value, selection: visibleSelection(graph, selection) });
    $('counts').textContent = mode === 'overview' ? tr('{0} 个智能体 · {1} 条后继关系', graph.nodes.length, graph.edges.length) : tr("显示 {0} 个节点 · 原始 {1} 节点 / {2} 弧", counts.visibleNodes, frame.net.nodes.length, frame.net.edges.length);
    $('resources').hidden = mode === 'overview';
    const resources = frame.net.nodes.filter(n => n.category === 'resource').length;
    $('resources').textContent = tr("{0}资源 ({1})", showResources ? tr("隐藏") : tr("显示"), resources);
    $('resources').setAttribute('aria-pressed', String(showResources));
    const term = $('search').value.trim().toLocaleLowerCase();
    const hits = (mode === 'overview' ? graph.nodes : frame.net.nodes).filter(n => term && searchText({ ...n, display: nodePresentation(n, frame) }).includes(term));
    $('search-status').textContent = term ? tr("{0} 个匹配", hits.length) : '';
    const wire = applyWireBridges(renderer, graph, layout, { showResources, enabled: $('line-bridges').checked });
    $('bridge-status').textContent = $('line-bridges').checked ? tr('独立交叉 {0} 处', wire.crossings) : tr('跳线已关闭');
    drawMap();
}
function summary() {
    renderObservationPanel($('observation-panel'), frame, { mode, translate: tr });
    document.querySelector('.run-metrics').hidden = mode === 'overview';
    $('checkpoint').hidden = mode === 'overview';
    const steps = frame.net.nodes.filter(n => n.kind === 'transition'), known = steps.every(n => n.runtime), counts = steps.map(firingSummary);
    $('run-title').textContent = frame.presentation?.title ?? tr("运行流程");
    $('run-description').textContent = frame.presentation?.description ?? tr('从任务输入到结果输出，查看每一步的真实记录。');
    $('metric-steps').textContent = steps.length;
    $('metric-active').textContent = known && frame.coverage?.firings !== 'canonical_only' ? counts.reduce((a, n) => a + n.pending, 0) : '—';
    $('metric-settled').textContent = known ? counts.reduce((a, n) => a + n.settled, 0) : '—';
    $('metric-tokens').textContent = frame.net.marking?.active_token_count ?? '—';
    $('source-badge').textContent = frame.net.source.mode === 'initial_configured' ? tr("初始声明") : frame.position.mode === 'history' ? tr("历史检查点") : tr("当前状态");
    if (retainedCapture) $('source-badge').textContent = tr('暂停 · 保留捕获');
    if(activity.paused) $('source-badge').textContent=tr('暂停 · 保留当前观察');
    $('source-badge').dataset.mode = activity.paused ? 'activity_retained' : retainedCapture ? 'retained' : frame.position.mode;
    $('checkpoint').textContent = `#${frame.source.verified_head_ordinal ?? '?'} · ${referenceText(frame.net.marking?.checkpoint_ref)}`;
    $('mode-caption').textContent = mode === 'overview' ? tr('智能体概览 · 仅显示 Agent → Agent 后继关系') : mode === 'petri' ? tr("PetriNet · 完整库所、转换与弧") : mode === 'list' ? tr("执行列表 · 一行对应一次真实执行") : tr("流程视图 · 左 → 右{0}", graph.folded.length ? tr(" · {0} 个单一交接可在详情展开", graph.folded.length) : '');
    for (const m of ['overview', 'flow', 'petri', 'list'])
        $(`mode-${m}`).setAttribute('aria-pressed', String(mode === m));
    if (frame.presentation?.warning)
        notice(() => presentationWarning(frame));
}
async function render(previous = null, ticket = timeline.serial, candidate = frame) {
    if (!candidate)
        return false;
    const serial = ++renderSerial, selectedMode = mode;
    let nextGraph = displayGraph(candidate, mode === 'list' ? 'flow' : mode);
    const nextLayout = await layouts.get(nextGraph);
    // A newer navigation may arrive while ELK is still computing. Commit only
    // a complete, still-requested frame, never a stale graph with a new header.
    if (serial !== renderSerial || mode !== selectedMode || !timeline.accepts(ticket))
        return false;
    // Locale may have changed during the layout request; geometry is locale independent.
    nextGraph = displayGraph(candidate, mode === 'list' ? 'flow' : mode);
    const first = !graph, scopeChanged = previous && scope(previous) !== scope(candidate), oldView = graph?.view_mode;
    const resolved = firingTarget ? resolveFiringTarget(candidate, firingTarget) : null;
    if (scopeChanged) {
        timeline.resetHistory();
        historySupported = false;
        nextBefore = null;
        selection = null;
        firingTarget = null;
        viewports.clear();
        ++historySerial;
    }
    if (mode !== 'overview' && graph?.view_mode === 'overview' && selection?.kind === 'edge') {
        const relation = graph.edges.find(e => e.id === selection.id);
        if (relation) selection = {kind:'edge', id:relation.witness_arc_ids?.[0] ?? relation.source_ids[0]};
    }
    if (frame && frame!==candidate) {comparisonPanel.close();crossComparison.close();cancelActivity();cancelTokenResource();}
    frame = candidate;
    if (resolved) {
        firingTarget = resolved.target;
        selection = firingTarget ? { kind: 'node', id: firingTarget.transition_id } : null;
    }
    if (graph && !scopeChanged && oldView !== nextGraph.view_mode)
        viewports.set(oldView, renderer.viewport());
    clearMotion();
    graph = nextGraph;
    layout = nextLayout;
    renderer.apply(graph, layout);
    refreshCanvasText(renderer, graph, layout);
    $('canvas-wrap').hidden = mode === 'list';
    $('execution-table').hidden = mode !== 'list';
    if (mode === 'list')
        renderExecutionTable($('execution-table'), frame, select);
    summary();
    if (resolved?.reason) navigationNotice(resolved.reason);
    decorate();
    detail();
    overviewEmpty();
    $('paper').dataset.ready = 'true';
    $('paper').dataset.head = String(head(frame.net));
    $('paper').dataset.layoutRuns = String(layouts.runs);
    $('paper').dataset.viewMode = mode;
    if (first || scopeChanged) {
        renderer.fit();
        if (renderer.viewport().scale < .45 && graph.nodes.length > 8) {
            const start = graph.nodes.find(n => n.display?.role === 'entry') ?? graph.nodes[0];
            if (start)
                renderer.center('node', start.id);
        }
    }
    else if (oldView !== graph.view_mode) {
        const remembered = viewports.get(graph.view_mode);
        if (remembered)
            renderer.restore(remembered);
        else
            renderer.fit();
    }
    if (mode !== 'overview' && $('motion').checked && isAdjacent(previous, frame))
        renderer.illustrate(frame);
    timeControls();
    return true;
}
function timeControls() {
    checkpointControls();
    $('motion').disabled = mode === 'overview';
    $('motion').title = mode === 'overview' ? tr('概览仅显示检查点状态；变化动画请使用详细流程或 Petri 网。') : ''; 
    const index = timeline.mode === 'live' ? timeline.items.length - 1 : timeline.items.findIndex(i => i.cursor === timeline.cursor), size = timeline.items.length;
    $('time-slider').disabled = !historySupported || size < 2;
    $('time-slider').max = String(Math.max(0, size - 1));
    $('time-slider').value = String(Math.max(0, index));
    $('previous').disabled = !historySupported || index <= 0;
    $('next').disabled = !historySupported || index < 0 || index >= size - 1;
    $('play').disabled = !historySupported || size < 2;
    $('play').textContent = playing ? tr("暂停") : tr("播放");
    $('back-live').setAttribute('aria-pressed', String(timeline.mode === 'live'));
    $('back-live').textContent = timeline.mode === 'live' ? tr("● 实时位置") : latestHead > frame?.source.verified_head_ordinal ? tr("返回实时 · 有新进展") : tr("返回实时");
    $('time-position').textContent = historySupported && index >= 0 ? `${index + 1} / ${size} · #${timeline.items[index].cursor}` : tr("当前态");
    const boundary = timeline.endReason === 'reader_limit' ? tr(" · 已到读取上限") : timeline.endReason === 'net_version_boundary' ? tr(" · 止于网版本边界") : nextBefore != null ? tr(" · 可加载更早记录") : '';
    $('timeline-scope').textContent = historySupported ? tr("当前网版本的正式检查点 · 未结算中间态不回放") + boundary : tr("此入口未提供持久历史；不会用浏览器缓存冒充回放");
    const at = frame?.position.at, date = at ? new Date(at) : null;
    const readable = date && !Number.isNaN(date.getTime()) ? date.toLocaleString(getLanguage()) : null;
    $('observation-time').textContent = readable ? tr("检查点记录时间：{0}", readable) : '';
    $('observation-time').dateTime = readable ? date.toISOString() : '';
    $('time-slider').setAttribute('aria-valuetext', historySupported && index >= 0 ? tr("已加载第 {0} 个，共 {1} 个检查点；记录位置 {2}", index + 1, size, timeline.items[index].cursor) : tr("此入口未提供历史"));
    $('play').setAttribute('aria-label', playing ? tr("暂停历史回放，不暂停任务") : tr("播放历史，不执行任务"));
    $('load-earlier').hidden = !nextBefore;
    const change = frame?.change;
    $('change-summary').textContent = mode !== 'overview' && frame?.position.mode === 'history' && change?.coverage === 'settlement_delta' ? tr("这一检查点：消费 {0} 个 token，产出 {1} 个 token。动画仅为变化示意。", change.consumed.length, change.deposited.length) : tr("拖动只改变观看位置，不暂停、不重跑真实任务。");
    if (checkpointView || retainedCapture || checkpointBusy) {
        for (const id of ['time-slider', 'previous', 'next', 'play', 'speed', 'load-earlier', 'refresh', 'auto-refresh']) $(id).disabled = true;
        $('load-earlier').hidden = true;
        $('back-live').setAttribute('aria-pressed', 'false');
        $('back-live').textContent = tr('返回实时');
        $('timeline-scope').textContent = checkpointView ? tr('所选保存检查点 · 无跨网播放 · 执行活动未提供') : tr('暂停 · 保留原完整捕获；返回实时才读取新状态');
        $('time-position').textContent = `#${frame?.source.verified_head_ordinal ?? '?'}`;
        $('change-summary').textContent = tr('网版本、检查点 cut 与捕获 H/E 分开；不会重建采用时点或跨网变化动画。');
        $('refresh').title = tr('请显式返回实时以读取新状态');
    } else {
        for (const id of ['speed', 'refresh', 'auto-refresh']) $(id).disabled = false;
        $('refresh').title = '';
    }
    if(activity.paused) {
        $('auto-refresh').disabled=true; $('play').disabled=true;
        $('timeline-scope').textContent=tr('保留当前主图观察；活动单独固定 H/E');
        $('back-live').setAttribute('aria-pressed','false');
    }
}
async function loadHistory(before = null) {
    if (!hasDashboard || !frame || checkpointView || retainedCapture || checkpointBusy)
        return;
    const serial = ++historySerial, bound = scope(frame);
    try {
        const data = await request('/api/v1/history' + (before != null ? `?before=${before}` : ''));
        if (serial !== historySerial || scope(frame) !== bound)
            return;
        if (scope({ source: data.source }) !== bound) {
            notice(() => tr("运行或网结构已更新。返回实时后重新读取该版本的历史。"));
            return;
        }
        if (!timeline.updatePage(data, before))
            return;
        historySupported = true;
        nextBefore = timeline.nextBefore;
        latestHead = Math.max(latestHead ?? 0, data.source.verified_head_ordinal);
        timeControls();
        if (timeline.endReason === 'net_version_boundary') await probeCheckpoint();
    }
    catch (error) {
        if (serial !== historySerial || scope(frame) !== bound)
            return;
        if (error.status === 501 || error.status === 404) {
            historySupported = false;
            timeControls();
        }
        else {
            setHealth(() => tr("历史索引暂不可读：") + error.message);
        }
    }
}
function schedule() { clearTimeout(timer); if (!comparisonOnly && !crossComparison.state.open && !comparisonPanel.state.open && !activity.paused && !checkpointView && !retainedCapture && !checkpointBusy && $('auto-refresh').checked)
    timer = setTimeout(async () => { if (timeline.mode === 'live')
        await loadFrame(null);
    else {
        await loadHistory();
        schedule();
    } }, 2500); }
async function loadFrame(cursor) {
    if (checkpointView || retainedCapture || checkpointBusy) return;
    comparisonPanel.close();crossComparison.close();
    ++probeSerial; checkpointProbe = null;
    cancelActivity();cancelTokenResource();
    const ticket = timeline.begin();
    controller?.abort();
    controller = new AbortController();
    setHealth(() => cursor == null ? tr("读取当前状态…") : tr("读取历史检查点…"));
    try {
        let value;
        if (hasDashboard) {
            try {
                value = await request('/api/v1/dashboard' + (cursor != null ? `?cursor=${cursor}` : ''), controller.signal);
            }
            catch (error) {
                if (cursor == null && (error.status === 404 || error.status === 501))
                    hasDashboard = false;
                else
                    throw error;
            }
        }
        if (!hasDashboard) {
            if (cursor != null)
                throw messageError("旧 provider 不提供历史");
            value = await request('/api/v1/net', controller.signal);
        }
        const data = normalizeFrame(value);
        if (!timeline.accepts(ticket))
            return;
        if (cursor != null && (data.position.cursor !== cursor || data.position.mode !== 'history'))
            throw messageError("返回的历史位置不匹配");
        if (frame && scope(data) === scope(frame) && cursor == null && timeline.mode === 'live' && head(data.net) < head(frame.net))
            throw messageError("收到较旧的状态，保留最后完整画面");
        const previous = frame;
        if (previous && previous.source.run_dir !== data.source.run_dir)
            throw messageError("运行绑定发生变化，未切换到其他任务");
        if (previous && previous.source.task_id != null && data.source.task_id !== previous.source.task_id)
            throw messageError("任务身份发生变化，未替换当前画面");
        notice(null);
        if (!await render(previous, ticket, data))
            return;
        latestHead = Math.max(latestHead ?? 0, data.position.latest_head ?? 0);
        timeControls();
        if (!timeline.accepts(ticket))
            return;
        const observedAt = new Date();
        setHealth(() => tr("{0} · {1} · 布局 {2} 次", cursor == null ? tr("实时观察") : tr("历史查看"), observedAt.toLocaleTimeString(getLanguage()), layouts.runs), 'ok');
        if (cursor == null)
            await loadHistory();
        else if (timeline.endReason === 'net_version_boundary')
            await probeCheckpoint();
    }
    catch (error) {
        if (!timeline.accepts(ticket) || error.name === 'AbortError')
            return;
        notice(() => `${frame ? tr("保留最后完整画面") : tr("尚无可用画面")}：${error.message}`);
        setHealth(() => tr("读取失败 · 状态可能已过期"), 'stale');
        if (!frame) {
            emptyFailure=error;
            $('canvas-empty').querySelector('h2').textContent = tr("无法读取这次运行");
            $('canvas-empty').querySelector('p').textContent = error.message;
        }
    }
    finally {
        if (timeline.accepts(ticket))
            schedule();
    }
}
function stop() { playing = false; ++playGeneration; clearTimeout(playTimer); clearTimeout(dragTimer); timeControls(); }
async function jump(index) { if (checkpointView || retainedCapture || checkpointBusy) return; const item = timeline.items[index]; if (!item)
    return; timeline.select(item.cursor); timeControls(); await loadFrame(item.cursor); }
async function playStep(generation) { if (!playing || generation !== playGeneration)
    return; let i = timeline.items.findIndex(x => x.cursor === timeline.cursor); if (timeline.mode === 'live' || i === timeline.items.length - 1) {
    await jump(0);
    i = 0;
}
else {
    await jump(i + 1);
    i++;
} if (!playing || generation !== playGeneration)
    return; if (i >= timeline.items.length - 1) {
    stop();
    return;
} playTimer = setTimeout(() => playStep(generation), Number($('speed').value)); }

function checkpointControls() {
    const active = checkpointView ?? checkpointProbe;
    $('previous-net').hidden = !active;
    $('previous-net').disabled = checkpointBusy || !active?.navigation.previous_net_segment;
    $('return-capture').hidden = checkpointReturns.length === 0;
    $('return-capture').disabled = checkpointBusy;
    $('return-capture').textContent = checkpointReturns.length ? tr('返回保留视图 #{0}', checkpointReturns.at(-1).frame.source.verified_head_ordinal) : tr('返回保留视图');
    const evidence = checkpointView?.adoption_evidence;
    const labels = {current_at_cut: tr('此 cut 当前采用'), previously_adopted: tr('此 cut 之前曾采用'),
        no_evidence_at_cut: tr('此 cut 无采用证据（查询完整）'), unknown: tr('采用证据未提供或不完整')};
    $('checkpoint-status').textContent = checkpointView
        ? tr('已保存 exact 网与 marking · cut #{0} · 捕获 H{1}/E{2} · {3}', checkpointView.selector.cut,
            checkpointView.capture.head_ordinal, checkpointView.capture.writer_fencing_epoch, labels[evidence?.status] ?? labels.unknown)
        : retainedCapture ? tr('暂停 · 保留捕获 H{0}/E{1}', frame?.source.verified_head_ordinal, frame?.source.writer_fencing_epoch ?? '?')
        : active?.navigation.end_reason === 'reader_limit' ? tr('前一网段范围不完整：已到读取上限') : '';
}
async function probeCheckpoint() {
    if (!frame || checkpointView || retainedCapture || checkpointBusy) return;
    const serial = ++probeSerial, bound = scope(frame), ticket = timeline.serial;
    try {
        const selected = selectorForFrame(frame);
        const value = normalizeCheckpointView(await request(checkpointPath(selected)), selected, frame.source);
        if (serial !== probeSerial || !timeline.accepts(ticket) || scope(frame) !== bound) return;
        checkpointProbe = value; timeControls();
    } catch (error) {
        if (serial !== probeSerial || !timeline.accepts(ticket)) return;
        checkpointProbe = null;
        // No fallback to the v1 net endpoint: capability absence is local.
        $('checkpoint-status').textContent = error.status === 404 || error.status === 501
            ? tr('此入口不支持保存检查点跨网导航') : tr('保存检查点导航暂不可读：{0}', error.message);
    }
}
function retainedState() {
    return {frame, checkpointView, checkpointProbe, historySupported, nextBefore, latestHead,
        timeline: {mode: timeline.mode, cursor: timeline.cursor, items: structuredClone(timeline.items),
            nextBefore: timeline.nextBefore, historyHead: timeline.historyHead, endReason: timeline.endReason}};
}
function pauseCheckpointNavigation() {
    comparisonPanel.close();crossComparison.close();
    cancelActivity();cancelTokenResource();
    stop(); clearTimeout(timer); controller?.abort(); ++historySerial; ++probeSerial;
    $('auto-refresh').checked = false;
    return timeline.begin();
}
async function openPreviousNet() {
    if (checkpointBusy) return;
    const selected = (checkpointView ?? checkpointProbe)?.navigation.previous_net_segment;
    if (!selected) return;
    const saved = retainedState(), ticket = pauseCheckpointNavigation();
    checkpointBusy = true; timeControls();
    controller = new AbortController();
    try {
        const value = normalizeCheckpointView(await request(checkpointPath(selected), controller.signal), selected, saved.frame.source);
        if (!timeline.accepts(ticket)) return;
        if (!await render(saved.frame, ticket, value.frame)) return;
        checkpointReturns.push(saved);
        checkpointView = value; checkpointProbe = null; retainedCapture = false;
        timeline.resetHistory(); timeline.mode = 'history'; timeline.cursor = value.selector.cut;
        historySupported = false; nextBefore = null;
        selection = null; firingTarget = null; viewports.clear(); clearMotion();
        notice(null); summary(); detail();
        setHealth(() => tr('保存检查点 · 暂停观察'), 'ok');
    } catch (error) {
        if (!timeline.accepts(ticket) || error.name === 'AbortError') return;
        notice(() => tr('保留最后完整画面') + '：' + error.message);
        setHealth(() => tr('读取失败 · 状态可能已过期'), 'stale');
    } finally {
        if (timeline.accepts(ticket)) { checkpointBusy = false; timeControls(); }
    }
}
async function returnCheckpointCapture() {
    if (checkpointBusy || !checkpointReturns.length) return;
    const saved = checkpointReturns.at(-1), ticket = pauseCheckpointNavigation();
    checkpointBusy = true;
    try {
        if (!await render(frame, ticket, saved.frame)) return;
        checkpointReturns.pop(); checkpointView = saved.checkpointView; checkpointProbe = saved.checkpointProbe;
        retainedCapture = !checkpointView;
        Object.assign(timeline, saved.timeline); // Never restore a request serial.
        historySupported = saved.historySupported; nextBefore = saved.nextBefore; latestHead = saved.latestHead;
        selection = null; firingTarget = null; viewports.clear(); clearMotion();
        summary(); detail(); notice(null);
        setHealth(() => tr('暂停 · 保留捕获'), 'ok');
    } catch (error) {
        if (timeline.accepts(ticket)) notice(() => tr('保留最后完整画面') + '：' + error.message);
    } finally {
        if (timeline.accepts(ticket)) { checkpointBusy = false; timeControls(); }
    }
}
function returnToLive() {
    if (!checkpointView && !retainedCapture && !checkpointBusy && checkpointReturns.length === 0) {
        stop(); timeline.live(); return loadFrame(null);
    }
    pauseCheckpointNavigation(); checkpointBusy = false; checkpointView = null; checkpointProbe = null;
    retainedCapture = false; checkpointReturns.length = 0; timeline.resetHistory();
    historySupported = false; nextBefore = null; timeline.live(); timeControls(); return loadFrame(null);
}

function presentationWarning(value){
  if(value.presentation?.status==='mismatch')return tr('展示说明与这张网不匹配，已使用默认名称。');
  if(value.presentation?.status==='invalid')return tr('展示说明无效，已保留原流程并使用默认名称。');
  return value.presentation?.warning;
}
function navigationNotice(reason) {
    notice(() => reason === 'scope_changed' ? tr('来源范围已改变或身份不足，已清除执行选择。')
        : reason === 'inconsistent' ? tr('执行引用或成员关联不一致，已清除执行选择。')
        : tr('当前帧不再披露所选成员的完整执行引用，已清除执行选择。'));
}
function openPetri(target = null) {
    cancelActivity(true);cancelTokenResource();
    if (target?.firing_ref != null) {
        const resolved = resolveFiringTarget(frame, target);
        if (!resolved.target) {
            selection = null; firingTarget = null;
            navigationNotice(resolved.reason); decorate(); detail();
            return false;
        }
        // Set all selection fields together, before the async layout/render starts.
        selection = { kind: 'node', id: resolved.target.transition_id };
        firingTarget = resolved.target;
        tab = 'executions';
    } else if (target?.transition_id != null) {
        if (!frame.net.nodes.some(node => node.kind === 'transition' && node.id === target.transition_id)) return false;
        selection = { kind: 'node', id: target.transition_id };
        firingTarget = null; tab = 'executions';
    }
    mode = 'petri';
    return render();
}
function overviewEmpty() {
    const empty = mode === 'overview' && graph && graph.nodes.length === 0;
    $('canvas-empty').hidden = !empty;
    $('open-petri-empty').hidden = !empty;
    $('canvas-empty').querySelector('.spinner').hidden = empty;
    if (empty) {
        $('canvas-empty').querySelector('h2').textContent = graph.agent_coverage === 'provided' ? tr('此流程没有声明的智能体') : tr('尚未提供智能体信息');
        $('canvas-empty').querySelector('p').textContent = tr('概览只显示智能体；工具、判断和数据节点请在 Petri 网中查看。');
    }
}
function refreshLanguage() {
    drawSourceObservation();comparisonPanel.refreshLanguage();crossComparison.refreshLanguage();
    const savedNotice=noticeMessage;
    const openDetails = [...$('detail').querySelectorAll('details')].map((d, i) => d.open ? i : -1), scroll = $('detail').scrollTop;
    applyLanguage(document);
    $('language').value = getLanguage();
    if (frame && graph && layout) {
        graph = displayGraph(frame, mode === 'list' ? 'flow' : mode);
        renderer.apply(graph, layout);
    refreshCanvasText(renderer, graph, layout);
        overviewEmpty();
        if (mode === 'list')
            renderExecutionTable($('execution-table'), frame, select);
        summary();
        decorate();
        detail();
        timeControls();
        for (const i of openDetails)
            if (i >= 0 && $('detail').querySelectorAll('details')[i])
                $('detail').querySelectorAll('details')[i].open = true;
        $('detail').scrollTop = scroll;
    }
    notice(savedNotice);
    setHealth(healthMessage);
    if(!frame&&emptyFailure){$('canvas-empty').querySelector('h2').textContent=tr('无法读取这次运行');$('canvas-empty').querySelector('p').textContent=emptyFailure.message;}
}
$('language').onchange = () => { setLanguage($('language').value); refreshLanguage(); };
async function start() {
    await Promise.all([loadScript('/assets/joint.js'), loadScript('/assets/elk-api.js')]);
    const elk = new window.ELK({ workerUrl: '/assets/elk-worker.js' });
    layouts = new LayoutCache(elk);crossComparison.setRendering({joint:window.joint,elk});
    if (!$('paper')?.parentElement) throw messageError('PetriNet 画布缺少宿主容器');
    renderer = new NetRenderer(window.joint, $('paper'), selectRendered);
    for (const m of ['overview', 'flow', 'petri', 'list'])
        $(`mode-${m}`).onclick = () => { if(m==='overview') {cancelActivity(true);cancelTokenResource();} mode = m; render(); };
    for (const t of ['about', 'executions', 'evidence'])
        $(`tab-${t}`).onclick = () => { tab = t; detail(); };
    $('line-bridges').onchange = () => { clearMotion(); decorate(); };
    $('open-petri-empty').onclick = openPetri;
    $('resources').onclick = () => { showResources = !showResources; decorate(); };
    $('fit').onclick = () => renderer.fit();
    $('zoom-in').onclick = () => renderer.zoom(1.25);
    $('zoom-out').onclick = () => renderer.zoom(.8);
    $('reset').onclick = () => { cancelActivity(true);cancelTokenResource(); selection = null; firingTarget = null; $('search').value = ''; decorate(); detail(); renderer.fit(); };
    $('clear-selection').onclick = () => { cancelActivity(true);cancelTokenResource(); selection = null; firingTarget = null; decorate(); detail(); };
    $('refresh').onclick = () => checkpointView || retainedCapture || checkpointBusy ? undefined : timeline.mode === 'live' ? loadFrame(null) : loadFrame(timeline.cursor);
    $('auto-refresh').onchange = schedule;
    $('search').oninput = () => { findIndex = -1; decorate(); };
    const find = () => { if (!frame)
        return; const term = $('search').value.trim().toLocaleLowerCase(), matches = (mode === 'overview' ? graph.nodes : frame.net.nodes).filter(n => term && searchText({ ...n, display: nodePresentation(n, frame) }).includes(term)); if (!matches.length)
        return; const n = matches[++findIndex % matches.length]; if (n.category === 'resource')
        showResources = true; select('node', n.id); const visible = visibleSelection(graph, selection); if (visible)
        renderer.center(visible.kind, visible.id); };
    $('find-next').onclick = find;
    $('search').onkeydown = e => { if (e.key === 'Enter')
        find(); };
    $('focus-current').onclick = () => { if (!frame)
        return; const pool = mode === 'overview' ? graph.nodes : frame.net.nodes; const n = pool.find(n => firingSummary(n).pending > 0) ?? (mode === 'overview' ? pool[0] : pool.find(n => n.active_token_count > 0)); if (n) {
        select('node', n.id);
        const v = visibleSelection(graph, selection);
        if (v)
            renderer.center(v.kind, v.id);
    } };
    $('back-live').onclick = returnToLive;
    $('previous-net').onclick = openPreviousNet;
    $('return-capture').onclick = returnCheckpointCapture;
    $('previous').onclick = () => { stop(); const i = timeline.mode === 'live' ? timeline.items.length - 1 : timeline.items.findIndex(x => x.cursor === timeline.cursor); jump(i - 1); };
    $('next').onclick = () => { stop(); jump(timeline.items.findIndex(x => x.cursor === timeline.cursor) + 1); };
    $('play').onclick = () => { if (playing)
        stop();
    else {
        stop();
        playing = true;
        const generation = ++playGeneration;
        timeControls();
        playStep(generation);
    } };
    $('time-slider').oninput = () => { if (checkpointView || retainedCapture || checkpointBusy) return; const i = Number($('time-slider').value); stop(); const item = timeline.items[i]; if (!item)
        return; const ticket = timeline.select(item.cursor); controller?.abort(); $('time-position').textContent = `${i + 1} / ${timeline.items.length} · #${item.cursor}`; dragTimer = setTimeout(() => { if (timeline.accepts(ticket))
        loadFrame(item.cursor); }, 100); };
    $('load-earlier').onclick = () => loadHistory(nextBefore);
    $('help').onclick = () => $('guide').showModal();
    $('close-help').onclick = () => $('guide').close();
    $('fullscreen').onclick = async () => { try {
        if (document.fullscreenElement)
            await document.exitFullscreen();
        else
            await document.documentElement.requestFullscreen();
    }
    catch {
        notice(() => tr("此浏览器不允许全屏，仍可使用缩放和完整视图。"));
    } };
    $('minimap-toggle').onclick = () => { $('minimap').hidden = !$('minimap').hidden; $('minimap-toggle').setAttribute('aria-expanded', String(!$('minimap').hidden)); };
    $('minimap').onclick = e => { if (!layout)
        return; const point = $('minimap').createSVGPoint(); point.x = e.clientX; point.y = e.clientY; const p = point.matrixTransform($('minimap').getScreenCTM().inverse()), v = renderer.viewport(); renderer.paper.translate(renderer.paper.options.width / 2 - p.x * v.scale, renderer.paper.options.height / 2 - p.y * v.scale); };
    $('motion').onchange = () => { if (!$('motion').checked)
        clearMotion(); };
    $('motion').checked = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.addEventListener('pagehide', event => {
        cancelActivity();cancelTokenResource(); clearTimeout(timer); stop(); ++historySerial; ++probeSerial; timeline.begin(); controller?.abort();
        checkpointBusy=false;
        // A bfcache entry keeps this app instance: its paper and resize observer
        // must survive, while pending reads and comparison state are discarded.
        if (!event.persisted) renderer.dispose();
    });
    window.addEventListener('pageshow', event => { if (event.persisted) { timeControls();schedule(); } });
    if(comparisonOnly){$('workspace').hidden=true;document.querySelector('.timeline').hidden=true;document.querySelector('.run-header').hidden=true;crossComparison.open();setHealth(()=>tr('独立只读比较已就绪'),'ok');}
    else await loadFrame(null);
}
start().catch(error => { notice(() => error.message); setHealth(() => tr("看板启动失败"), 'stale'); });

installWorksetPanel(document.getElementById("worksets"));
