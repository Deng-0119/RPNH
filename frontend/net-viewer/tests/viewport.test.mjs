import test from 'node:test';
import assert from 'node:assert/strict';
import { NetRenderer } from '../../../cpn/frontend/static/renderer.mjs';
import { referenceText } from '../../../cpn/frontend/static/model.mjs';

test('viewport measures independent host, not the Paper element', () => {
  const calls = [];
  const viewer = { container: { clientWidth: 1000, clientHeight: 600 },
    element: { clientWidth: 1, clientHeight: 1 },
    paper: { options: { width: 1, height: 1 }, setDimensions(w, h) {
      calls.push([w, h]); this.options.width = w; this.options.height = h;
    } } };
  NetRenderer.prototype.syncSize.call(viewer);
  assert.deepEqual(calls, [[1000, 600]]);
  NetRenderer.prototype.syncSize.call(viewer);
  assert.equal(calls.length, 1, 'unchanged host must not feed a resize loop');
  viewer.container.clientHeight = 380;
  NetRenderer.prototype.syncSize.call(viewer);
  assert.deepEqual(calls.at(-1), [1000, 380]);
});

test('temporarily hidden host keeps the last nonzero viewport', () => {
  const viewer = { container: { clientWidth: 0, clientHeight: 0 },
    paper: { options: { width: 900, height: 500 }, setDimensions() { assert.fail('hidden host resize'); } } };
  NetRenderer.prototype.syncSize.call(viewer);
  assert.deepEqual(viewer.paper.options, { width: 900, height: 500 });
});

test('checkpoint identity renders exact wire or dataclass ID, not object coercion', () => {
  assert.equal(referenceText({ version_id: 'cp-wire' }), 'cp-wire');
  assert.equal(referenceText({ version_id: { value: 'cp-typed' } }), 'cp-typed');
  assert.equal(referenceText('cp-direct'), 'cp-direct');
  assert.equal(referenceText(null), 'Not provided');
  assert.equal(referenceText({ unexpected: true }), '{"unexpected":true}');
});


test('resource filtering and focus update both link roots and detached labels', () => {
  const cells = new Map();
  for (const id of ['node:r', 'node:t', 'edge:e']) cells.set(id, {
    attrs: {}, labels: {}, attr(key, value) { this.attrs[key] = value; },
    label(index, value) { this.labels[index] = value; },
  });
  const nodes = [{id: 'r', kind: 'place', category: 'resource'}, {id: 't', kind: 'transition', category: 'execution'}];
  const edge = {id: 'e', source: 'r', target: 't', mode: 'read', kind: 'arc', weight: 1};
  const viewer = {paper: {options: {labelsLayer: true}}, snapshot: {nodes, edges: [edge]}, byNode: new Map(nodes.map(n => [n.id, n])),
    graph: { getCell(id) { return cells.get(id); } } };
  const counts = NetRenderer.prototype.decorate.call(viewer, {showResources: false});
  assert.deepEqual(counts, {visibleNodes: 1, visibleEdges: 0});
  assert.equal(cells.get('edge:e').attrs['root/display'], 'none');
  assert.equal(cells.get('edge:e').labels[0].attrs.root.display, 'none');
  assert.equal(cells.get('edge:e').labels[0].attrs.root.tabindex, -1);
  NetRenderer.prototype.decorate.call(viewer, {showResources: true, search: 'no-match'});
  const label = cells.get('edge:e').labels[0].attrs.root;
  assert.equal(label.display, null); assert.equal(label.tabindex, 0);
  assert.equal(label.opacity, cells.get('edge:e').attrs['root/opacity']);
  assert.equal(label['data-edge-id'], 'e');
  viewer.paper.options.labelsLayer = false;
  NetRenderer.prototype.decorate.call(viewer, {showResources: true, search: 'no-match'});
  assert.equal(cells.get('edge:e').labels[0].attrs.root.opacity, 1, 'inline captions inherit link opacity exactly once');
});


test('fit includes very tall graphs below the normal zoom floor and remains reversible', () => {
  let scale = 1, tx = 0, ty = 0;
  const viewer = {layout: {width: 3872, height: 25274}, minScale: .03,
    syncSize() {}, element: {clientWidth: 1230, clientHeight: 757,
      getBoundingClientRect() {return {left: 0, top: 0, width: 1230, height: 757};}},
    paper: {scale(value) {if (value !== undefined) scale = value; return {sx: scale};},
      translate(x, y) {if (x !== undefined) {tx = x; ty = y;} return {tx, ty};}},
  };
  NetRenderer.prototype.fit.call(viewer);
  const fit = scale;
  assert.ok(fit < .03, 'fixed 3% floor would clip this verified ELK layout');
  assert.ok(tx >= 0 && ty >= 0);
  assert.ok(3872 * scale + tx <= 1230 && 25274 * scale + ty <= 757);
  NetRenderer.prototype.zoom.call(viewer, 1.25);
  assert.ok(scale > fit);
  NetRenderer.prototype.zoom.call(viewer, .8);
  assert.ok(Math.abs(scale - fit) < 1e-12);
});
