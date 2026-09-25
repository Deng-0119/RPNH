import { cardState } from './dashboard-model.mjs';
import { nodeKey, edgeKey, topologyKey, statusLabel, firingSummary, tokensLabel, arcLabel, arcStyle, wrapLabel, resourceEdge, searchText } from './model.mjs';
/** JointJS is a renderer only. All semantic relationships remain in the snapshot. */
export class NetRenderer {
  constructor(joint, element, onSelect) {
    this.joint = joint; this.element = element; this.onSelect = onSelect; this.signature = null; this.selected = null;
    this.minScale = .03;
    this.container = element.parentElement;
    if (!this.container) throw new Error('PetriNet 画布缺少宿主容器');
    const { dia } = joint;
    this.graph = new dia.Graph({}, { cellNamespace: joint.shapes });
    this.paper = new dia.Paper({ el: element, model: this.graph, cellViewNamespace: joint.shapes,
      width: Math.max(1, this.container.clientWidth), height: Math.max(1, this.container.clientHeight), gridSize: 1, async: false, interactive: false, clickThreshold: 4,
      defaultConnectionPoint: { name: 'anchor' }, background: { color: 'transparent' } });
    this.Shape = dia.Element.define('rpnh.DisplayNode', {}, { markup: [] });
    this.paper.on('cell:pointerclick', view => this.onSelect(view.model.get('semanticKind'), view.model.get('semanticId')));
    element.addEventListener('keydown', event => {
      if (!['Enter', ' '].includes(event.key)) return;
      const cell = event.target.closest('[data-id]');
      if (cell) { event.preventDefault(); this.onSelect(cell.dataset.kind, cell.dataset.id); }
    });
    // Capture blank-canvas drags only. Capturing node clicks at the SVG root
    // caused the former viewer's click/detail regression.
    let drag = null;
    element.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('.joint-cell')) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, origin: this.paper.translate() };
      element.setPointerCapture(event.pointerId);
    });
    element.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      this.paper.translate(drag.origin.tx + event.clientX - drag.x, drag.origin.ty + event.clientY - drag.y);
    });
    const stop = event => { if (drag?.id === event.pointerId) { drag = null; if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId); } };
    element.addEventListener('pointerup', stop); element.addEventListener('pointercancel', stop);
    element.addEventListener('lostpointercapture', () => { drag = null; });
    element.addEventListener('wheel', event => { event.preventDefault(); this.zoom(event.deltaY < 0 ? 1.12 : 1 / 1.12, event); }, { passive: false });
    // Paper writes dimensions to its own element. Measure the independent
    // host, never that element, or the initial size becomes a feedback loop.
    this.resize = new ResizeObserver(() => this.syncSize());
    this.resize.observe(this.container);
    this.syncSize();
  }
  syncSize() {
    const width = this.container.clientWidth, height = this.container.clientHeight;
    // A temporarily hidden host must not destroy the last usable viewport.
    if (width <= 0 || height <= 0) return;
    if (this.paper.options.width !== width || this.paper.options.height !== height) {
      this.paper.setDimensions(width, height);
    }
  }
  createNode(node, position) {
    const compact = this.viewMode === 'overview';
    const flow = (this.viewMode === 'flow' || compact), card = flow || node.kind === 'transition';
    const d = node.display ?? { role: node.kind === 'transition' ? 'step' : 'data', name: node.label, type: node.kind, description: '' };
    const colors = { entry: '#16836b', exit: '#6554bd', boundary: '#16836b', resource: '#a56818', step: '#4865c8', data: '#6b7b91' };
    const accent = colors[d.role] ?? colors.step;
    const label = position.labels?.[0] ?? { x: -69, y: 68, width: 194 };
    const common = { fontFamily: 'system-ui, sans-serif', fill: '#202c43', pointerEvents: 'none' };
    const markup = [{ tagName: card ? 'rect' : 'circle', selector: 'body' },
      { tagName: 'text', selector: 'eyebrow' }, { tagName: 'text', selector: 'title' },
      { tagName: 'text', selector: 'subtitle' }, { tagName: 'text', selector: 'status' },
      { tagName: 'path', selector: 'divider' }, { tagName: 'title', selector: 'tooltip' }];
    const attrs = card ? {
      body: { width: position.width, height: position.height, rx: 12, fill: '#fff', stroke: accent, strokeWidth: 1.4 },
      eyebrow: { ...common, x: 16, y: 23, fontSize: 10, fontWeight: 700, fill: accent, text: flow ? d.type : 'TRANSITION' },
      title: { ...common, x: 16, y: 36, fontSize: compact ? 14 : 15, fontWeight: 650, textVerticalAnchor: 'top', text: wrapLabel(flow ? d.name : node.id, Math.floor((position.width - 32) / 8), 2) },
      subtitle: { ...common, x: 16, y: 73, fontSize: 11, fill: '#67748a', textVerticalAnchor: 'top', text: compact ? '' : wrapLabel(flow ? d.description : node.operation ?? '', Math.floor((position.width - 32) / 6.5), 2) },
      status: { ...common, x: 16, y: position.height - 13, fontSize: 10, fill: '#627187' },
      divider: { d: `M 14 ${position.height - 32} H ${position.width - 14}`, stroke: '#edf0f5', strokeWidth: 1, pointerEvents: 'none' },
    } : {
      body: { cx: 28, cy: 28, r: 28, fill: '#fff', stroke: accent, strokeWidth: 2, strokeDasharray: node.category === 'resource' ? '5 3' : '' },
      title: { ...common, x: label.x + label.width / 2, y: label.y, textAnchor: 'middle', fontSize: 11, textVerticalAnchor: 'top', text: wrapLabel(node.id, 26, 2) },
      subtitle: { ...common, x: 28, y: 29, textAnchor: 'middle', fontSize: 18, fontWeight: 600, textVerticalAnchor: 'middle' },
      status: { ...common, x: label.x + label.width / 2, y: label.y + 37, textAnchor: 'middle', fontSize: 10, fill: accent },
      eyebrow: { text: '' }, divider: { d: '' },
    };
    return new this.Shape({ id: nodeKey(node.id), semanticId: node.id, semanticKind: 'node',
      position: { x: position.x, y: position.y }, size: { width: position.width, height: position.height }, markup,
      ports: { groups: { layout: { position: { name: 'absolute' }, markup: [{ tagName: 'circle', selector: 'portBody' }],
        attrs: { portBody: { r: 0, magnet: false, pointerEvents: 'none' } } } },
        items: position.ports.map(p => ({ id: p.id, group: 'layout', args: { x: p.x, y: p.y } })) },
      attrs: { ...attrs, tooltip: { text: `${d.name} — ${node.id}` }, root: { 'data-id': node.id, 'data-kind': 'node', tabindex: 0, role: 'button', 'aria-label': `${d.type}：${d.name}` } }, z: 2 });
  }
  createEdge(edge, position) {
    const style = arcStyle(edge), section = position.sections[0], label = position.labels?.[0];
    const marker = style.marker === 'open' ? { type: 'path', d: 'M 9 -4 L 0 0 L 9 4', fill: 'none', stroke: style.color } :
      style.marker === 'double' ? { type: 'path', d: 'M 12 -5 L 7 0 L 12 5 M 6 -5 L 1 0 L 6 5', fill: 'none', stroke: style.color } :
      { type: 'path', d: 'M 8 -4 L 0 0 L 8 4 Z', fill: style.color, stroke: style.color };
    const cell = new this.joint.shapes.standard.Link({ id: edgeKey(edge.id), semanticId: edge.id, semanticKind: 'edge',
      source: { id: nodeKey(edge.source), port: position.sources[0] }, target: { id: nodeKey(edge.target), port: position.targets[0] },
      vertices: section.bendPoints ?? [], connector: { name: 'normal' },
      attrs: { line: { stroke: style.color, strokeWidth: 1.7, strokeDasharray: style.dash, targetMarker: marker },
        wrapper: { strokeWidth: 16 }, root: { 'data-id': edge.id, 'data-kind': 'edge', tabindex: 0, role: 'button', 'aria-label': arcLabel(edge) } }, z: 1 });
    cell.appendLabel({ position: label ? { distance: 0, offset: { x: label.x + label.width / 2 - section.startPoint.x,
      y: label.y + label.height / 2 - section.startPoint.y }, args: { absoluteOffset: true } } : { distance: .5 },
      attrs: { text: { text: wrapLabel(edge.display_label ?? arcLabel(edge), 34, 1), fontSize: 10, fill: style.color, fontFamily: 'system-ui, sans-serif' },
        rect: { fill: '#fcfdff', stroke: 'none' } } });
    return cell;
  }
  apply(snapshot, layout) {
    this.viewMode = snapshot.view_mode ?? 'petri';
    const signature = topologyKey(snapshot), changed = signature !== this.signature;
    if (changed) {
      const cells = [...snapshot.nodes.map(n => this.createNode(n, layout.nodes.get(n.id))), ...snapshot.edges.map(e => this.createEdge(e, layout.edges.get(e.id)))];
      this.graph.resetCells(cells); this.signature = signature; this.layout = layout;
      for (const cell of cells) this.paper.findViewByModel(cell)?.el.classList.add(cell.get('semanticKind') === 'node' ? 'node' : 'edge-group');
    }
    this.snapshot = snapshot; this.byNode = new Map(snapshot.nodes.map(n => [n.id, n]));
    for (const n of snapshot.nodes) {
      const cell = this.graph.getCell(nodeKey(n.id));
      const card = (this.viewMode === 'flow' || this.viewMode === 'overview') || n.kind === 'transition';
      if (card) {
        cell.attr('status/text', cardState(n));
        cell.attr('body/fill', n.kind === 'transition' && firingSummary(n).pending ? '#fff9e9' : n.kind === 'place' && n.active_token_count > 0 ? '#eef9f5' : '#fff');
      } else {
        cell.attr('subtitle/text', tokensLabel(n, snapshot.source.mode));
        cell.attr('status/text', n.display?.role === 'entry' ? '入口库所' : n.display?.role === 'exit' ? '输出库所' : n.category === 'resource' ? '资源库所' : 'Place');
        cell.attr('body/fill', n.active_token_count > 0 ? '#e4f6ee' : '#fff');
      }
    }
    for (const e of snapshot.edges) if (e.hidden_place) {
      const count = Number.isInteger(e.active_token_count) ? `${e.active_token_count} 个 token` : 'token 未提供';
      this.graph.getCell(edgeKey(e.id)).label(0, { attrs: { text: { text: `${e.display_label} · ${count}` } } });
    }
    return changed;
  }
  decorate({ showResources, search = '', selection = null }) {
    if (!this.snapshot) return;
    const term = search.trim().toLocaleLowerCase(), focus = new Set();
    if (selection?.kind === 'node') {
      focus.add(selection.id);
      this.snapshot.edges.forEach(e => { if (e.source === selection.id || e.target === selection.id) { focus.add(e.source); focus.add(e.target); } });
    } else if (selection?.kind === 'edge') {
      const edge = this.snapshot.edges.find(e => e.id === selection.id);
      if (edge) { focus.add(edge.source); focus.add(edge.target); }
    }
    let visibleNodes = 0, visibleEdges = 0;
    for (const n of this.snapshot.nodes) {
      const cell = this.graph.getCell(nodeKey(n.id)), visible = showResources || n.category !== 'resource';
      const match = !term || searchText(n).includes(term), selected = selection?.kind === 'node' && selection.id === n.id;
      cell.attr('root/display', visible ? null : 'none');
      cell.attr('root/opacity', match && (!focus.size || focus.has(n.id)) ? 1 : .2);
      cell.attr('body/strokeWidth', selected ? 3.5 : n.kind === 'place' ? 2 : 1.6);
      cell.attr('root/aria-pressed', String(selected));
      if (visible) visibleNodes++;
    }
    for (const e of this.snapshot.edges) {
      const cell = this.graph.getCell(edgeKey(e.id)), visible = showResources || !resourceEdge(e, this.byNode);
      cell.attr('root/display', visible ? null : 'none');
      const opacity = (!term || searchText(e).includes(term)) && (!focus.size || focus.has(e.source) && focus.has(e.target)) ? 1 : .16;
      cell.attr('root/opacity', opacity);
      // Labels can be inline (the pinned default) or in an optional SVG layer.
      // Keep caption display/focus state explicit in either configuration.
      cell.label(0, { attrs: { root: { display: visible ? null : 'none',
        opacity: this.paper.options.labelsLayer ? opacity : 1,
        'data-edge-id': e.id, 'data-id': e.id, 'data-kind': 'edge', tabindex: visible ? 0 : -1,
        role: 'button', 'aria-label': arcLabel(e) } } });
      cell.attr('line/strokeWidth', selection?.kind === 'edge' && selection.id === e.id ? 3.5 : 1.7);
      if (visible) visibleEdges++;
    }
    return { visibleNodes, visibleEdges };
  }
  fit() {
    if (!this.layout) return;
    this.syncSize();
    const w = this.element.clientWidth, h = this.element.clientHeight;
    const scale = Math.min(1.3, .92 * Math.min(w / Math.max(1, this.layout.width), h / Math.max(1, this.layout.height)));
    if (!Number.isFinite(scale) || scale <= 0) return;
    this.minScale = Math.min(.03, scale);
    this.paper.scale(scale); this.paper.translate((w - this.layout.width * scale) / 2, (h - this.layout.height * scale) / 2);
  }
  zoom(factor, event) {
    const box = this.element.getBoundingClientRect(), x = event ? event.clientX - box.left : box.width / 2, y = event ? event.clientY - box.top : box.height / 2;
    const old = this.paper.scale().sx, next = Math.max(this.minScale, Math.min(4, old * factor)), { tx, ty } = this.paper.translate();
    this.paper.scale(next); this.paper.translate(x - (x - tx) * next / old, y - (y - ty) * next / old);
  }
  center(kind, id) {
    const cell = this.graph.getCell(kind === 'edge' ? edgeKey(id) : nodeKey(id));
    if (!cell) return;
    const p = cell.getBBox().center(), scale = Math.max(.55, this.paper.scale().sx);
    this.paper.scale(scale); this.paper.translate(this.element.clientWidth / 2 - p.x * scale, this.element.clientHeight / 2 - p.y * scale);
  }
  dispose() { this.resize.disconnect(); this.paper.remove(); }  viewport() { return { scale: this.paper.scale().sx, ...this.paper.translate() }; }
  restore(view) { this.paper.scale(view.scale, view.scale); this.paper.translate(view.tx, view.ty); }
  illustrate(frame) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !this.layout) return;
    const refs = new Set((frame.change?.firing_refs ?? []).map(r => JSON.stringify(r)));
    const transitions = new Set(frame.net.nodes.filter(n => n.runtime?.firings.some(f => refs.has(JSON.stringify(f.firing_ref)))).map(n => n.id));
    const inputs = new Set((frame.change?.consumed ?? []).map(t => t.place));
    const outputs = new Set((frame.change?.deposited ?? []).map(t => t.place));
    const edges = this.snapshot.edges.filter(e => !e.resource && e.mode !== 'read' && e.mode !== 'reset' &&
      (inputs.has(e.source) && transitions.has(e.target) || transitions.has(e.source) && outputs.has(e.target) ||
       e.hidden_place && (inputs.has(e.hidden_place) || outputs.has(e.hidden_place))));
    const ns = 'http://www.w3.org/2000/svg', group = document.createElementNS(ns, 'g');
    const v = this.viewport(); group.setAttribute('transform', `translate(${v.tx} ${v.ty}) scale(${v.scale})`);
    group.setAttribute('pointer-events', 'none'); group.setAttribute('aria-hidden', 'true'); group.setAttribute('class', 'token-illustration');
    this.paper.svg.append(group);
    const paths = [];
    for (const edge of edges) {
      const section = this.layout.edges.get(edge.id)?.sections[0]; if (!section) continue;
      const path = document.createElementNS(ns, 'path'), circle = document.createElementNS(ns, 'circle');
      const points = [section.startPoint, ...(section.bendPoints ?? []), section.endPoint];
      const rendered = this.paper.findViewByModel(this.graph.getCell(edgeKey(edge.id)))?.getSerializedConnection();
      path.setAttribute('d', rendered || points.map((p,i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' ')); path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'none');
      circle.setAttribute('r', '5'); circle.setAttribute('fill', '#258b72'); circle.setAttribute('stroke', 'white'); circle.setAttribute('stroke-width', '2');
      group.append(path, circle); paths.push({ path, circle, length: path.getTotalLength() });
    }
    const start = performance.now();
    const tick = now => { const t = Math.min(1,(now-start)/750); for (const p of paths) { const point = p.path.getPointAtLength(t*p.length); p.circle.setAttribute('cx',point.x); p.circle.setAttribute('cy',point.y); } if(t<1 && group.isConnected) requestAnimationFrame(tick); else group.remove(); };
    requestAnimationFrame(tick);
  }

}
