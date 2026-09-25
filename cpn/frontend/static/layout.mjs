import { t as tr, messageError } from './i18n.mjs';
import { topologyKey, byId, arcLabel } from './model.mjs';
/** Only anonymous geometry enters ELK. No config, token, resource or firing data. */
export function geometry(data) {
    // ELK 0.12 needs nonempty label text to lay out the reserved label box.
    // Use a constant placeholder: the worker must never receive semantic text.
    const compact = data.view_mode === 'overview';
    const nodes = [...data.nodes].sort(byId), edges = [...data.edges].sort(byId);
    const ids = new Map(nodes.map((n, i) => [n.id, `n${i}`]));
    const children = nodes.map(n => {
        const transition = n.kind === 'transition' || (data.view_mode === 'flow' || compact);
        const width = transition ? (compact ? 224 : n.kind === 'transition' ? 270 : 212) : 56, height = transition ? (compact ? 126 : 142) : 56;
        return { id: ids.get(n.id), width, height, ports: [],
            layoutOptions: { 'elk.portConstraints': 'FIXED_POS', ...(n.display?.role === 'entry' ? { 'elk.layered.layering.layerConstraint': 'FIRST' } : n.display?.role === 'exit' ? { 'elk.layered.layering.layerConstraint': 'LAST' } : {}) },
            labels: transition ? [] : [{ id: `${ids.get(n.id)}:label`, text: 'label', width: 194, height: 62,
                    layoutOptions: { 'elk.nodeLabels.placement': 'OUTSIDE V_BOTTOM H_CENTER' } }] };
    });
    const by = new Map(children.map(n => [n.id, n]));
    const layoutEdges = edges.map((e, i) => {
        const s = by.get(ids.get(e.source)), t = by.get(ids.get(e.target));
        const source = `${s.id}:out:${i}`, target = `${t.id}:in:${i}`;
        s.ports.push({ id: source, x: s.width, y: s.height / 2, width: 0, height: 0, layoutOptions: { 'elk.port.side': 'EAST' } });
        t.ports.push({ id: target, x: 0, y: t.height / 2, width: 0, height: 0, layoutOptions: { 'elk.port.side': 'WEST' } });
        return { id: `e${i}`, sources: [source], targets: [target], labels: e.agent_relation || (compact && !e.display_label && !e.hidden_places?.length) ? [] : [{ id: `e${i}:label`, text: 'label', width: compact ? 152 : 260, height: 20 }] };
    });
    for (const child of children) for (const side of ['WEST', 'EAST']) {
        const ports = child.ports.filter(p => p.layoutOptions['elk.port.side'] === side);
        ports.forEach((p, i) => { p.y = child.height * (i + 1) / (ports.length + 1); });
    }
    return { graph: { id: 'net', layoutOptions: { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.edgeRouting': 'ORTHOGONAL',
                'elk.spacing.nodeNode': '48', 'elk.spacing.edgeNode': '24', 'elk.layered.spacing.nodeNodeBetweenLayers': '70',
                'elk.layered.mergeEdges': 'false', 'elk.randomSeed': '1', 'elk.padding': '[top=36,left=36,bottom=36,right=36]' },
            children, edges: layoutEdges }, nodes, edges };
}
export function decodeLayout(result, source) {
    if (result.children?.length !== source.nodes.length || result.edges?.length !== source.edges.length)
        throw messageError("布局丢失节点或弧");
    const nodes = new Map(), edges = new Map();
    for (const n of result.children) {
        const i = Number(n.id.slice(1));
        if (!source.nodes[i] || ![n.x, n.y, n.width, n.height].every(Number.isFinite))
            throw messageError("布局节点坐标无效");
        nodes.set(source.nodes[i].id, n);
    }
    for (const e of result.edges) {
        const i = Number(e.id.slice(1));
        if (!source.edges[i] || !e.sections?.length)
            throw messageError("布局弧缺少路由");
        // Binary PN arcs normally have one section. Never silently drop extra sections.
        if (e.sections.length !== 1)
            throw messageError("暂不支持多段分叉路由；未替换当前快照");
        const section = e.sections[0];
        if (![section.startPoint, ...(section.bendPoints ?? []), section.endPoint].every(p => Number.isFinite(p?.x) && Number.isFinite(p?.y)))
            throw messageError("布局弧坐标无效");
        edges.set(source.edges[i].id, e);
    }
    return { nodes, edges, width: result.width, height: result.height };
}
export class LayoutCache {
    constructor(elk) { this.elk = elk; this.cache = new Map(); this.pending = new Map(); this.runs = 0; }
    async get(data) {
        if (!data.nodes.length) return {nodes: new Map(), edges: new Map(), width: 200, height: 120};
        const key = topologyKey(data);
        if (this.cache.has(key))
            return this.cache.get(key);
        if (this.pending.has(key))
            return this.pending.get(key);
        const work = (async () => {
            this.runs++;
            const source = geometry(data), start = performance.now();
            const result = decodeLayout(await this.elk.layout(source.graph), source);
            result.elapsedMs = performance.now() - start;
            this.cache.set(key, result);
            if (this.cache.size > 4)
                this.cache.delete(this.cache.keys().next().value);
            return result;
        })();
        this.pending.set(key, work);
        try {
            return await work;
        }
        finally {
            this.pending.delete(key);
        }
    }
}
