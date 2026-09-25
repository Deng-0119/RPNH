import test from 'node:test';
import assert from 'node:assert/strict';
import ELK from '../node_modules/elkjs/lib/elk.bundled.js';
import { validateSnapshot, topologyKey, firingSummary, SnapshotOrder, nodeKey, edgeKey, arcStyle } from '../../../cpn/frontend/static/model.mjs';
import { geometry, LayoutCache, decodeLayout } from '../../../cpn/frontend/static/layout.mjs';
function sample() {
  return { schema_version: 'rpnh/net_view/v1', source: { mode: 'registry_current', run_dir: 'same', net_ref: { version_id: 'v1' } }, execution: { head_ordinal: 10 },
    nodes: [ { id: 'p', label: 'Request', kind: 'place', category: 'place', active_token_count: 1 },
      { id: 't', label: '执行测试 长名称 <script> never HTML', kind: 'transition', category: 'execution', runtime: { firings: [] } },
      { id: 'r', label: 'Resource', kind: 'place', category: 'resource' } ],
    edges: [ { id: 'p', source: 'p', target: 't', kind: 'arc', mode: 'consume', weight: 1, outcome: null },
      { id: 'back', source: 't', target: 'p', kind: 'arc', mode: 'produce', weight: 1, outcome: 'again' },
      { id: 'parallel', source: 't', target: 'p', kind: 'arc', mode: 'produce', weight: 1, outcome: 'interrupted' },
      { id: 'resource', source: 'r', target: 't', kind: 'arc', mode: 'read', weight: 1, outcome: null, resource: true } ] };
}
test('valid bipartite cyclic net and separate visual namespaces', () => {
  const s = sample(); assert.equal(validateSnapshot(s), s); assert.notEqual(nodeKey('p'), edgeKey('p'));
});
test('invalid duplicate or non-bipartite structure is rejected', () => {
  for (const change of [s => s.nodes.push(s.nodes[0]), s => s.edges.push(s.edges[0]), s => s.edges[0].target = 'r', s => s.edges[0].target = 'missing', s => s.edges[0].weight = 0]) {
    const s = sample(); change(s); assert.throws(() => validateSnapshot(s));
  }
});
test('runtime and response order do not change topology signature', () => {
  const a = sample(), b = structuredClone(a); b.nodes[0].active_token_count = 0; b.execution.head_ordinal++; b.nodes.reverse(); b.edges.reverse();
  assert.equal(topologyKey(a), topologyKey(b));
  b.edges[0].weight = 2; assert.notEqual(topologyKey(a), topologyKey(b));
});
test('multiple firings retain older provisional execution, not only newest status', () => {
  assert.deepEqual(firingSummary({ runtime: { status: 'settled', firings: [
    { status: 'outcome_unknown', publication_state: 'PROVISIONAL' },
    { status: 'settled', business_outcome: 'deny' }, { status: 'invalidated' }] } }),
    { settled: 1, pending: 1, invalidated: 1, other: 0, total: 3 });
});
test('stale requests and backwards Registry heads cannot replace a view', () => {
  const order = new SnapshotOrder(), s = sample(), first = order.begin(), second = order.begin();
  assert.equal(order.commit(s, first), false); assert.equal(order.commit(s, second), true);
  const old = structuredClone(s); old.execution.head_ordinal = 9;
  assert.throws(() => order.check(old, order.begin()));
  old.source.net_ref.version_id = 'new-net'; assert.equal(order.commit(old, order.serial), true);
});
test('worker receives anonymous geometry, never actual labels or Registry payload', () => {
  const s = sample(); s.nodes[1].config = { SECRET: 'DO_NOT_SEND' }; s.nodes[0].tokens = [{ resource_ref: 'secret-ref' }];
  const serialized = JSON.stringify(geometry(s).graph);
  for (const privateText of ['DO_NOT_SEND', 'secret-ref', '执行测试', 'Request', 'interrupted']) assert.ok(!serialized.includes(privateText));
});
test('read, reset, variable-resource and ordinary arcs remain distinguishable', () => {
  const styles = [ { mode: 'consume' }, { mode: 'read' }, { kind: 'reset_arc' }, { kind: 'variable_resource_arc' } ].map(arcStyle);
  assert.equal(new Set(styles.map(JSON.stringify)).size, 4);
});
test('real ELK retains parallel arcs and cycle routes; state updates reuse layout', async () => {
  const s = sample(), cache = new LayoutCache(new ELK());
  const result = await cache.get(s);
  assert.equal(result.nodes.size, 3); assert.equal(result.edges.size, 4);
  const a = result.edges.get('back').sections, b = result.edges.get('parallel').sections;
  assert.notDeepEqual(a, b);
  const changed = structuredClone(s); changed.nodes[0].active_token_count = 0; changed.execution.head_ordinal++;
  assert.equal(await cache.get(changed), result); assert.equal(cache.runs, 1);
  changed.nodes.push({ id: 'extra', label: 'extra', kind: 'place', category: 'place' });
  assert.equal((await cache.get(changed)).nodes.size, 4); assert.equal(cache.runs, 2);
});
test('failed or multi-section layouts never silently omit routes', () => {
  const source = geometry(sample()); assert.throws(() => decodeLayout({ children: [], edges: [] }, source));
});
test('empty graphs can be laid out without invented nodes', async () => {
  const s = sample(); s.nodes = []; s.edges = [];
  const result = await new LayoutCache(new ELK()).get(s); assert.equal(result.nodes.size, 0); assert.equal(result.edges.size, 0);
});


test('real ELK allocates anonymous label boxes rather than piling them at the origin', async () => {
  const data = sample();
  const input = geometry(data);
  for (const label of [...input.graph.children.flatMap(n => n.labels), ...input.graph.edges.flatMap(e => e.labels)]) {
    assert.equal(label.text, 'label');
  }
  const result = await new LayoutCache(new ELK()).get(data);
  for (const node of data.nodes.filter(n => n.kind === 'place')) {
    const shape = result.nodes.get(node.id), label = shape.labels[0];
    assert.ok(label.y >= shape.height, 'place label must be below its circle');
    assert.ok(Math.abs(label.x + label.width / 2 - shape.width / 2) < 1e-6, 'place label must be centered');
  }
  const boxes = [...result.edges.values()].map(edge => edge.labels[0]);
  assert.equal(new Set(boxes.map(b => `${b.x},${b.y}`)).size, boxes.length, 'edge labels cannot all use (0, 0)');
  for (const label of boxes) {
    assert.ok([label.x, label.y, label.width, label.height].every(Number.isFinite));
    assert.ok(label.x > 0 && label.y > 0, 'labels stay inside the padded graph');
    for (const node of result.nodes.values()) {
      const overlap = label.x < node.x + node.width && label.x + label.width > node.x &&
        label.y < node.y + node.height && label.y + label.height > node.y;
      assert.equal(overlap, false, 'edge label must not cover a Petri node body');
    }
  }
});
