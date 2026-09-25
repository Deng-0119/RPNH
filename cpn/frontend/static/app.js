import { t as tr, messageError, getLanguage, setLanguage, applyLanguage } from './i18n.mjs';
import { head, referenceText, searchText, firingSummary, stable } from './model.mjs';
import { normalizeFrame, scope, nodePresentation, displayGraph, visibleSelection, TimelineState, isAdjacent } from './dashboard-model.mjs';
import { LayoutCache } from './layout.mjs';
import { NetRenderer } from './renderer.mjs';
import { refreshCanvasText } from './canvas-text.mjs';
import { applyWireBridges } from './wire-geometry.mjs';
import { renderInspector, renderExecutionTable, element } from './panels.mjs';
const $ = id => document.getElementById(id), timeline = new TimelineState();
let emptyFailure=null;
let noticeMessage = null, healthMessage = () => tr("加载本地展示组件…");
function showMessage(value) { return typeof value === 'function' ? value() : value; }
function setHealth(message, state) { healthMessage = message; $('health').textContent = showMessage(message); if (state)
    $('health').dataset.state = state; }
applyLanguage(document);
$('language').value = getLanguage();
let renderer, layouts, frame, graph, layout, selection = null, firingId = null, mode = 'overview', tab = 'about', controller, timer, playTimer, playing = false, findIndex = -1, renderSerial = 0, dragTimer, playGeneration = 0, historySerial = 0;
let showResources = JSON.parse($('bootstrap').textContent).showResources, hasDashboard = true, historySupported = false, nextBefore = null, latestHead = null;
const viewports = new Map();
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
    renderInspector($('detail'), frame, chosen, tab, firingId, select, graph, openPetri);
}
function select(kind, id, firing = null) { selection = { kind, id }; firingId = firing; if (firing)
    tab = 'executions'; decorate(); detail(); }
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
    $('source-badge').dataset.mode = frame.position.mode;
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
    if (scopeChanged) {
        timeline.resetHistory();
        historySupported = false;
        nextBefore = null;
        selection = null;
        firingId = null;
        viewports.clear();
        ++historySerial;
    }
    if (mode !== 'overview' && graph?.view_mode === 'overview' && selection?.kind === 'edge') {
        const relation = graph.edges.find(e => e.id === selection.id);
        if (relation) selection = {kind:'edge', id:relation.witness_arc_ids?.[0] ?? relation.source_ids[0]};
    }
    frame = candidate;
    if (graph && oldView !== nextGraph.view_mode)
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
}
async function loadHistory(before = null) {
    if (!hasDashboard || !frame)
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
function schedule() { clearTimeout(timer); if ($('auto-refresh').checked)
    timer = setTimeout(async () => { if (timeline.mode === 'live')
        await loadFrame(null);
    else {
        await loadHistory();
        schedule();
    } }, 2500); }
async function loadFrame(cursor) {
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
async function jump(index) { const item = timeline.items[index]; if (!item)
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
function presentationWarning(value){
  if(value.presentation?.status==='mismatch')return tr('展示说明与这张网不匹配，已使用默认名称。');
  if(value.presentation?.status==='invalid')return tr('展示说明无效，已保留原流程并使用默认名称。');
  return value.presentation?.warning;
}
function openPetri() { mode = 'petri'; render(); }
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
    layouts = new LayoutCache(new window.ELK({ workerUrl: '/assets/elk-worker.js' }));
    if (!$('paper')?.parentElement) throw messageError('PetriNet 画布缺少宿主容器');
    renderer = new NetRenderer(window.joint, $('paper'), selectRendered);
    for (const m of ['overview', 'flow', 'petri', 'list'])
        $(`mode-${m}`).onclick = () => { mode = m; render(); };
    for (const t of ['about', 'executions', 'evidence'])
        $(`tab-${t}`).onclick = () => { tab = t; detail(); };
    $('line-bridges').onchange = () => { clearMotion(); decorate(); };
    $('open-petri-empty').onclick = openPetri;
    $('resources').onclick = () => { showResources = !showResources; decorate(); };
    $('fit').onclick = () => renderer.fit();
    $('zoom-in').onclick = () => renderer.zoom(1.25);
    $('zoom-out').onclick = () => renderer.zoom(.8);
    $('reset').onclick = () => { selection = null; firingId = null; $('search').value = ''; decorate(); detail(); renderer.fit(); };
    $('clear-selection').onclick = () => { selection = null; firingId = null; decorate(); detail(); };
    $('refresh').onclick = () => timeline.mode === 'live' ? loadFrame(null) : loadFrame(timeline.cursor);
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
    $('back-live').onclick = () => { stop(); timeline.live(); loadFrame(null); };
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
    $('time-slider').oninput = () => { const i = Number($('time-slider').value); stop(); const item = timeline.items[i]; if (!item)
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
    window.addEventListener('pagehide', () => { clearTimeout(timer); stop(); ++historySerial; controller?.abort(); renderer.dispose(); });
    await loadFrame(null);
}
start().catch(error => { notice(() => error.message); setHealth(() => tr("看板启动失败"), 'stale'); });
