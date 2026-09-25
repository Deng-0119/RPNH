import { t as tr, messageError } from './i18n.mjs';
/** Detached read-model helpers. Never infer execution or permission from a graph. */
export const SCHEMA = 'rpnh/net_view/v1';
const inFlight = new Set(['admitted', 'started', 'dispatch_authorized', 'returned_unsettled', 'failed_unsettled', 'outcome_unknown']);
export function validateSnapshot(data) {
    if (!data || data.schema_version !== SCHEMA || !['initial_configured', 'registry_current'].includes(data.source?.mode))
        throw messageError("不支持的网快照来源或版本");
    if (!Array.isArray(data.nodes) || !Array.isArray(data.edges))
        throw messageError("快照缺少节点或弧");
    const nodes = new Map(), edges = new Set();
    for (const n of data.nodes) {
        if (!n || typeof n.id !== 'string' || !n.id || nodes.has(n.id) || typeof n.label !== 'string' || !['place', 'transition'].includes(n.kind) || !['place', 'execution', 'resource'].includes(n.category))
            throw messageError("无效或重复的 Petri 节点");
        if (n.runtime && !Array.isArray(n.runtime.firings))
            throw messageError("执行记录缺少 firing 数组");
        nodes.set(n.id, n);
    }
    for (const e of data.edges) {
        if (!e || typeof e.id !== 'string' || !e.id || edges.has(e.id) || !nodes.has(e.source) || !nodes.has(e.target) || nodes.get(e.source).kind === nodes.get(e.target).kind || !Number.isInteger(e.weight) || e.weight < 1 || typeof e.kind !== 'string' || (e.outcome != null && typeof e.outcome !== 'string'))
            throw messageError("无效、重复或非二部 Petri 弧");
        edges.add(e.id);
    }
    const ordinal = head(data);
    if (ordinal != null && (!Number.isSafeInteger(ordinal) || ordinal < 0))
        throw messageError("无效的 Registry head");
    return data;
}
export function stable(value) {
    if (Array.isArray(value))
        return `[${value.map(stable).join(',')}]`;
    if (value && typeof value === 'object')
        return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stable(value[k])}`).join(',')}}`;
    return JSON.stringify(value) ?? 'null';
}
export const head = data => data.execution?.head_ordinal ?? data.source.verified_head_ordinal ?? null;
export const identity = data => stable([data.source.mode, data.source.run_dir ?? null, data.source.net_ref ?? null]);
export const nodeKey = id => `node:${id}`;
export const edgeKey = id => `edge:${id}`;
export function topologyKey(data) {
    return stable([identity(data), data.view_mode ?? 'petri', [...data.nodes].sort(byId).map(n => [n.id, n.label, n.kind, n.category, n.display?.role ?? null]),
        [...data.edges].sort(byId).map(e => [e.id, e.source, e.target, e.kind, e.mode, e.weight, e.outcome])]);
}
export const byId = (a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
export function wrapLabel(value, columns = 26, maxLines = 3) {
    // Wrap Latin words as units; CJK characters and overlong IDs can still break.
    // This only formats labels: original text remains in the raw model/details.
    const tokens = String(value).match(/\r\n|\r|\n|[ \t]+|[A-Za-z0-9_]+(?:[-'][A-Za-z0-9_]+)*|[^\s]/gu) ?? [];
    const units = text => [...text].reduce((sum, char) => sum + (char.codePointAt(0) > 255 ? 2 : 1), 0);
    const lines = [''];
    let width = 0, truncated = false;
    function nextLine() {
        lines[lines.length - 1] = lines[lines.length - 1].trimEnd();
        if (lines.length >= maxLines) { truncated = true; return false; }
        lines.push(''); width = 0; return true;
    }
    outer: for (const token of tokens) {
        if (/^[\r\n]+$/.test(token)) { if (!nextLine()) break; continue; }
        if (/^[ \t]+$/.test(token)) {
            if (width && width < columns) { lines[lines.length - 1] += ' '; width++; }
            continue;
        }
        const size = units(token);
        if (size <= columns) {
            if (width + size > columns && !nextLine()) break;
            lines[lines.length - 1] += token; width += size;
        } else {
            for (const char of token) {
                const step = units(char);
                if (width + step > columns && !nextLine()) break outer;
                lines[lines.length - 1] += char; width += step;
            }
        }
    }
    if (truncated) {
        let last = lines[lines.length - 1].trimEnd();
        while (last && units(last) + 2 > columns) last = [...last].slice(0, -1).join('');
        lines[lines.length - 1] = last + '…';
    }
    return lines.map(line => line.trimEnd()).join('\n');
}
export function firingSummary(node) {
    const firings = node.runtime?.firings ?? [];
    const counts = { settled: 0, pending: 0, invalidated: 0, other: 0, total: firings.length };
    for (const f of firings) {
        if (f.status === 'settled')
            counts.settled++;
        else if (f.status === 'invalidated')
            counts.invalidated++;
        else if (f.publication_state === 'PROVISIONAL' || inFlight.has(f.status))
            counts.pending++;
        else
            counts.other++;
    }
    return counts;
}
export function statusLabel(node) {
    if (!node.runtime)
        return tr("执行记录：未提供");
    const s = firingSummary(node);
    if (!s.total)
        return tr("尚无准入记录");
    return tr("已结算 {0} · 未结算 {1}{2}{3}", s.settled, s.pending, s.invalidated ? tr(" · 失效 {0}", s.invalidated) : '', s.other ? tr(" · 其他 {0}", s.other) : '');
}
export function tokensLabel(node, sourceMode) {
    if (Number.isInteger(node.active_token_count))
        return String(node.active_token_count);
    if (sourceMode === 'initial_configured' && Array.isArray(node.initial_tokens))
        return String(node.initial_tokens.reduce((sum, token) => sum + (token.count ?? 1), 0));
    return '?';
}
export function arcLabel(edge) {
    if (edge.agent_relation) return tr('智能体后继关系');
    const modes = {consume: tr('消费'), produce: tr('产出'), read: tr('读取'), reset: tr('重置')};
    return `${modes[edge.mode] ?? edge.mode ?? edge.kind} ×${edge.weight}${edge.outcome != null ? ` · ${tr('分支')} ${edge.outcome}` : ''}`;
}
export function arcStyle(edge) {
    if (edge.kind === 'reset_arc' || edge.mode === 'reset')
        return { dash: '3 3', color: '#a33c52', marker: 'double' };
    if (edge.mode === 'read')
        return { dash: '6 4', color: '#7160a5', marker: 'open' };
    if (edge.resource || edge.kind === 'variable_resource_arc')
        return { dash: '9 3', color: '#a56114', marker: 'arrow' };
    return { dash: '', color: '#64748b', marker: 'arrow' };
}
export function resourceEdge(edge, byNode) {
    return Boolean(edge.hidden_by_default || edge.resource || byNode.get(edge.source)?.category === 'resource' || byNode.get(edge.target)?.category === 'resource');
}
export function searchText(item) {
    return [item.id, item.label, item.display?.name, item.display?.description, item.display?.group, item.kind, item.mode, item.outcome, item.operation, item.capability?.selector, item.runtime?.status].filter(v => v != null).join(' ').toLocaleLowerCase();
}
/** Only local observation ordering. This is not a Registry lease or checkpoint. */
export class SnapshotOrder {
    constructor() { this.serial = 0; this.last = null; }
    begin() { return ++this.serial; }
    current(serial) { return serial === this.serial; }
    check(data, serial) {
        if (!this.current(serial))
            return false;
        if (this.last && identity(this.last) === identity(data) && head(this.last) != null && head(data) != null && head(data) < head(this.last))
            throw messageError("收到较旧的 Registry head，保留最后一个完整快照");
        return true;
    }
    commit(data, serial) { if (!this.check(data, serial))
        return false; this.last = data; return true; }
}
/** Registry JSON can contain either wire strings or dataclass {value} IDs. */
export function referenceText(reference) {
    const value = reference?.version_id ?? reference;
    if (value == null)
        return tr("未提供");
    if (typeof value === 'string')
        return value;
    if (typeof value.value === 'string')
        return value.value;
    return stable(value);
}
