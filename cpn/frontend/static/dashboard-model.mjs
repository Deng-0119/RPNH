import { t as tr, messageError } from './i18n.mjs';
import { overviewGraph } from './overview.mjs';
import { agentMemberSummaries, agentCardState } from './agent-members.mjs';
/** Product read-model: abstraction is reversible; it never fires a transition. */
import { validateSnapshot, stable, firingSummary, referenceText, head } from './model.mjs';
export const DASHBOARD = 'rpnh/dashboard/v1';
export function normalizeFrame(value) {
    if (value?.schema_version === 'rpnh/net_view/v1') {
        validateSnapshot(value);
        return { schema_version: DASHBOARD, net: value, source: value.source,
            boundaries: { entry: [], exit: [], terminal_rules: [] }, transition_bindings: [],
            presentation: { title: null, description: null, nodes: {}, status: 'default' },
            position: { mode: 'live', cursor: head(value), latest_head: head(value) },
            coverage: { history: 'unsupported', firings: 'provider_defined' }, change: {} };
    }
    if (value?.schema_version !== DASHBOARD)
        throw messageError("不支持的看板数据版本");
    validateSnapshot(value.net);
    if (!['live', 'history'].includes(value.position?.mode))
        throw messageError("无效的观察模式");
    if (value.position.cursor != null && (!Number.isSafeInteger(value.position.cursor) || value.position.cursor < 0))
        throw messageError("无效的历史位置");
    if (head(value.net) !== value.source?.verified_head_ordinal)
        throw messageError("看板与网的观察位置不一致");
    if (stable(value.source.net_ref ?? null) !== stable(value.net.source.net_ref ?? null)
        || (value.source.run_dir ?? null) !== (value.net.source.run_dir ?? null))
        throw messageError("看板与网不属于同一运行");
    const cursor = value.position.cursor, latest = value.position.latest_head;
    if (latest != null && (!Number.isSafeInteger(latest) || latest < head(value.net)))
        throw messageError("最新观察上限无效");
    if (cursor != null && cursor > head(value.net))
        throw messageError("检查点晚于当前观察位置");
    if (value.position.mode === 'history' && cursor !== head(value.net))
        throw messageError("历史检查点与观察位置不一致");
    const ids = new Map(value.net.nodes.map(n => [n.id, n.kind]));
    for (const b of [...(value.boundaries?.entry ?? []), ...(value.boundaries?.exit ?? []), ...(value.boundaries?.terminal_rules ?? [])]) {
        if (ids.get(b.place) !== 'place')
            throw messageError("流程边界引用未知库所");
    }
    return value;
}
function observationFrame(frame) {
    if (frame?.schema_version !== DASHBOARD || !['live', 'history'].includes(frame.position?.mode))
        throw new TypeError('Expected normalizeFrame output');
    // The old normalizer checks net/run/head, but leaves these capture hints
    // optional. Missing hints are not conflicts; explicit disagreement is.
    for (const key of ['mode', 'writer_fencing_epoch', 'task_id']) {
        const source = frame.source?.[key], nested = frame.net.source?.[key];
        if (source != null && nested != null && stable(source) !== stable(nested))
            throw new TypeError(`Conflicting observation source ${key}`);
    }
    return frame;
}
/** Coverage of disclosed firing rows on this frame's transitions, not all activity.
 * Input is normalizeFrame output. Unknown/partial empty data is never true zero.
 */
export function observationCoverage(frame) {
    observationFrame(frame);
    const transitions = frame.net.nodes.filter(n => n.kind === 'transition');
    const declaration = typeof frame.coverage?.firings === 'string' ? frame.coverage.firings : null;
    const scope = { kind: 'frame_transition_firings', declaration,
        transition_ids: transitions.map(n => n.id) };
    const missing = [];
    const result = (state, loaded_count = null, total_count = null) =>
        ({ state, scope, loaded_count, total_count, missing });
    if (frame.source.mode !== 'registry_current') {
        missing.push('registry_observation');
        return result('unsupported');
    }
    if (['not_provided', 'not_disclosed', 'unsupported', 'read_failed', 'stale_observation'].includes(declaration)) {
        missing.push(`firings:${declaration}`);
        return result(declaration);
    }
    const expected = frame.position.mode === 'history' ? 'canonical_only' : 'current_observations';
    if (declaration !== expected) missing.push('firing_scope');
    if (frame.source.net_ref == null) missing.push('net_ref');
    if (head(frame.net) == null) missing.push('observation_cut');
    let loaded = 0, provided = 0;
    for (const node of transitions) {
        if (Array.isArray(node.runtime?.firings)) {
            loaded += node.runtime.firings.length;
            provided++;
        } else missing.push(`runtime.firings:${node.id}`);
    }
    if (!missing.length) return result('complete', loaded, loaded);
    return result(provided || declaration === 'partial' ? 'partial' : 'not_provided', loaded || null);
}
/** Detached, single-frame read context. Call normalizeFrame before this helper.
 * Compatibility hints are not cross-source authority. No history-page cursor,
 * SourceSet, access path or disclosure evidence is supplied by the old frame.
 */
export function observationContext(frame) {
    observationFrame(frame);
    const registry = frame.source.mode === 'registry_current';
    const historical = frame.position.mode === 'history';
    const ordinal = registry ? head(frame.net) : null;
    const writer = registry ? frame.source.writer_fencing_epoch ?? null : null;
    return structuredClone({
        mode: historical ? 'canonical-as-of' : 'current',
        source_set: null, manifest_version: null, query_scope: null,
        local_source: { mode: frame.source.mode, task_id: frame.source.task_id ?? null,
            run_dir: frame.source.run_dir ?? null },
        checkpoint_selector: registry ? frame.position.cursor ?? null : null,
        observed_capture: { latest_head_ordinal: registry ? frame.position.latest_head ?? null : null,
            writer_fencing_epoch: writer },
        source_cuts: [{
            source: { source_locator: null, record_ref: null, concept_id: null },
            net_ref: frame.source.net_ref ?? null,
            checkpoint_ref: frame.net.marking?.checkpoint_ref ?? null,
            cut: ordinal == null ? null : { head_ordinal: ordinal,
                // History carries today's capture epoch, not the historical writer's.
                writer_fencing_epoch: historical ? null : writer },
            cursor: null, path_ref: null, disclosure_ref: null, query_scope: null,
            coverage: observationCoverage(frame),
        }],
    });
}
export function scope(frame) { return stable([frame.source.task_id ?? null, frame.source.run_dir ?? null, frame.source.net_ref ?? null]); }
const namePart = id => String(id).replace(/\.run$/, '').split('.').join(' · ').replaceAll('_', ' ') || String(id);
export function nodePresentation(node, frame) {
    const metadata = frame.presentation?.nodes?.[node.id] ?? {};
    const entries = (frame.boundaries?.entry ?? []).filter(b => b.place === node.id);
    const exits = (frame.boundaries?.exit ?? []).filter(b => b.place === node.id);
    const terminals = (frame.boundaries?.terminal_rules ?? []).filter(b => b.place === node.id);
    const role = entries.length && (exits.length || terminals.length) ? 'boundary' : entries.length ? 'entry' : exits.length || terminals.length ? 'exit' : node.category === 'resource' ? 'resource' : node.kind === 'transition' ? 'step' : 'data';
    const type = metadata.type ?? ({ entry: tr("任务输入"), exit: tr("结果出口"), boundary: tr("输入 / 输出"), resource: tr("资源"), step: tr("执行步骤"), data: tr("数据 / 同步") }[role]);
    let name = metadata.name ?? (node.label !== node.id ? node.label : role === 'entry' ? tr("接收任务") : role === 'exit' ? tr("交付结果") : role === 'boundary' ? tr("任务接口") : role === 'resource' ? tr("资源 · ") + namePart(node.id) : role === 'step' ? namePart(node.id) : tr("数据交接"));
    // Distinguish multiple declared inputs/outputs without inventing business roles.
    const peers = role === 'entry' ? frame.boundaries?.entry : role === 'exit'
        ? [...(frame.boundaries?.exit ?? []), ...(frame.boundaries?.terminal_rules ?? [])] : [];
    if (!metadata.name && node.label === node.id && new Set((peers ?? []).map(b => b.place)).size > 1) {
        const labels = [...new Set([...entries, ...exits].map(b => b.name).filter(Boolean))];
        name += ` · ${labels.join(' / ') || namePart(node.id)}`;
    }
    const description = metadata.description ?? (role === 'entry' ? tr("流程从这里接收输入。") : role === 'exit' ? tr("声明的输出位置，不代表已完成。") : role === 'resource' ? tr("这一步依赖的资源或容量。") : role === 'step' ? tr("{0} 个输入 · {1} 个输出；选择查看详情。", node.inputs?.length ?? 0, node.outputs?.length ?? 0) : tr("保存数据或同步多个步骤。"));
    return { role, type, name, description, group: metadata.group ?? '', declared: Boolean(metadata.name), entries, exits, terminals };
}
export function cardState(node) {
    if (node.agent_summary) return agentCardState(node.agent_summary);
    if (node.kind === 'place')
        return Number.isInteger(node.active_token_count) ? tr("{0} 个有效 token", node.active_token_count) : tr("状态未提供");
    if (!node.runtime)
        return tr("执行状态未提供");
    const s = firingSummary(node);
    const other = s.other ? tr(" · {0} 次状态待识别", s.other) : '';
    if (s.pending)
        return tr("{0} 次未结算 · {1} 次已结算{2}", s.pending, s.settled, other);
    if (s.invalidated)
        return tr("{0} 次失效 · {1} 次已结算{2}", s.invalidated, s.settled, other);
    if (s.settled)
        return tr("{0} 次已结算{1}", s.settled, other);
    if (s.other)
        return tr("{0} 次已记录 · 状态待识别", s.other);
    return tr("该观察范围内无执行记录");
}
export function displayGraph(frame, mode = 'flow') {
    const raw = frame.net, nodes = raw.nodes.map(n => ({ ...n, display: nodePresentation(n, frame), source_ids: [n.id] }));
    if (mode === 'overview') {
        const view = overviewGraph({ ...raw, nodes }, frame.agent_nodes);
        const summaries = agentMemberSummaries(frame, view.nodes);
        view.nodes = view.nodes.map((n, index) => {
            const summary = summaries[index];
            return {...n, agent_summary: summary,
                runtime: {firings: summary.rows.filter(row => !row.conflict).map(row => row.firing)},
                display: {...n.display,
                    description: frame.presentation?.nodes?.[n.id]?.description ?? tr('执行这个流程中已声明的智能体任务。')}};
        });
        return view;
    }
    const incoming = new Map(), outgoing = new Map(), by = new Map(nodes.map(n => [n.id, n]));
    for (const e of raw.edges) {
        if (!incoming.has(e.target))
            incoming.set(e.target, []);
        incoming.get(e.target).push(e);
        if (!outgoing.has(e.source))
            outgoing.set(e.source, []);
        outgoing.get(e.source).push(e);
    }
    const folded = new Map();
    if (mode === 'flow')
        for (const n of nodes) {
            const ins = incoming.get(n.id) ?? [], outs = outgoing.get(n.id) ?? [];
            // Only an ordinary one-to-one produce/consume handoff. Read, reset,
            // fork/join, boundaries and resources always remain explicit.
            if (n.kind === 'place' && n.display.role === 'data' && ins.length === 1 && outs.length === 1
                && ins[0].kind === 'arc' && outs[0].kind === 'arc' && ins[0].mode === 'produce'
                && outs[0].mode === 'consume' && ins[0].weight === 1 && outs[0].weight === 1
                && ins[0].source !== outs[0].target && by.get(ins[0].source)?.kind === 'transition'
                && by.get(outs[0].target)?.kind === 'transition')
                folded.set(n.id, { node: n, input: ins[0], output: outs[0] });
        }
    const edges = raw.edges.filter(e => !folded.has(e.source) && !folded.has(e.target))
        .map(e => ({ ...e, source_ids: [e.id], display_label: mode === 'flow' ? friendlyArc(e) : null }));
    for (const [id, item] of folded)
        edges.push({ ...item.input, id: `flow:${id}`,
            target: item.output.target, source_ids: [item.input.id, item.output.id], hidden_place: id,
            display_label: friendlyArc(item.input) || tr("数据交接"), tokens: item.node.tokens,
            active_token_count: item.node.active_token_count });
    return { ...raw, view_mode: mode, nodes: nodes.filter(n => !folded.has(n.id)), edges,
        folded: [...folded.keys()], raw_node_count: raw.nodes.length, raw_edge_count: raw.edges.length };
}
export function friendlyArc(e) {
    if (e.resource)
        return e.mode === 'read' ? tr("读取资源") : tr("资源依赖");
    if (e.mode === 'read')
        return tr("读取 · 保留数据");
    if (e.mode === 'reset')
        return tr("重置");
    const words = { again: tr("继续迭代"), continue: tr("继续"), complete: tr("完成分支"), completed: tr("完成分支"), interrupted: tr("中断分支"), accepted: tr("接受"), rejected: tr("拒绝"), allowed: tr("允许"), denied: tr("拒绝"), tools: tr("调用工具") };
    return (words[e.outcome] ?? e.outcome) ?? (e.weight > 1 ? tr("{0} 份数据", e.weight) : '');
}
export function findRaw(frame, selection) {
    if (!selection)
        return null;
    return (selection.kind === 'node' ? frame.net.nodes : frame.net.edges).find(n => n.id === selection.id) ?? null;
}
export function visibleSelection(graph, selection) {
    if (!selection)
        return null;
    if (selection.kind === 'node') {
        const direct = graph.nodes.find(n => n.id === selection.id || n.source_ids?.includes(selection.id));
        if (direct) return {kind: 'node', id: direct.id};
        const edge = graph.edges.find(e => e.hidden_place === selection.id || e.intermediate_node_ids?.includes(selection.id) || e.hidden_places?.includes(selection.id));
        return edge ? { kind: 'edge', id: edge.id } : null;
    }
    const edge = graph.edges.find(e => e.id === selection.id || e.source_ids.includes(selection.id));
    return edge ? { kind: 'edge', id: edge.id } : null;
}
export function allFirings(frame) {
    return frame.net.nodes.flatMap(n => (n.runtime?.firings ?? []).map(f => ({ node: n, firing: f })))
        .sort((a, b) => (b.firing.admission_ordinal ?? 0) - (a.firing.admission_ordinal ?? 0));
}
export function isAdjacent(previous, current) {
    return previous && scope(previous) === scope(current) && current.change?.coverage === 'settlement_delta'
        && referenceText(previous.net.marking?.checkpoint_ref) === referenceText(current.change.previous_checkpoint_ref);
}
/** Separate request generations: polling cannot steal a history selection. */
export class TimelineState {
    constructor() { this.mode = 'live'; this.cursor = null; this.serial = 0; this.resetHistory(); }
    resetHistory() { this.items = []; this.nextBefore = undefined; this.historyHead = null; this.endReason = null; }
    select(cursor) { this.mode = 'history'; this.cursor = cursor; return ++this.serial; }
    live() { this.mode = 'live'; this.cursor = null; return ++this.serial; }
    begin() { return ++this.serial; }
    accepts(serial) { return serial === this.serial; }
    updatePage(page, requestedBefore = null) {
        if (page.schema_version !== 'rpnh/dashboard_history/v1' || !Array.isArray(page.items)
            || page.coverage !== 'current_net_canonical_checkpoints')
            throw messageError("历史索引格式无效");
        const upper = page.source?.verified_head_ordinal;
        if (!Number.isSafeInteger(upper) || upper < 0)
            throw messageError("历史索引上限无效");
        if (this.historyHead != null && upper < this.historyHead)
            return false;
        for (let i = 0; i < page.items.length; i++) {
            const item = page.items[i];
            if (!Number.isSafeInteger(item.cursor) || item.cursor < 0 || item.cursor > upper
                || (requestedBefore != null && item.cursor >= requestedBefore)
                || (i && item.cursor <= page.items[i - 1].cursor)
                || !item.checkpoint_ref)
                throw messageError("历史索引顺序或边界无效");
        }
        if (page.next_before != null && (!page.items.length || page.next_before !== page.items[0].cursor))
            throw messageError("历史分页位置无效");
        const oldest = this.items[0]?.cursor;
        this.update(page.items);
        // Refreshing the newest page must not forget already-loaded older pages.
        if (oldest == null || (page.items.length && page.items[0].cursor <= oldest))
            this.nextBefore = page.next_before;
        this.historyHead = upper;
        this.endReason = page.end_reason;
        return true;
    }
    update(items) {
        const merged = new Map(this.items.map(i => [i.cursor, i]));
        for (const i of items) {
            if (!Number.isSafeInteger(i.cursor) || i.cursor < 0)
                throw messageError("历史索引位置无效");
            if (merged.has(i.cursor) && stable(merged.get(i.cursor).checkpoint_ref) !== stable(i.checkpoint_ref))
                throw messageError("同一位置出现不同 checkpoint");
            merged.set(i.cursor, i);
        }
        this.items = [...merged.values()].sort((a, b) => a.cursor - b.cursor);
    }
}
