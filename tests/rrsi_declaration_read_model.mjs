// Actual Viewer read-model, no DOM, browser, HTTP or socket.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {normalizeFrame, displayGraph, observationCoverage} from '../cpn/frontend/static/dashboard-model.mjs';

for (const role of ['analyst', 'proposer', 'critic', 'digester']) {
  const input = JSON.parse(fs.readFileSync(new URL(`../examples/rrsi_v06/figures/${role}/declaration-frame.json`, import.meta.url)));
  const before = JSON.stringify(input), frame = normalizeFrame(input);
  const petri = displayGraph(frame, 'petri'), flow = displayGraph(frame, 'flow');
  const overview = displayGraph(frame, 'overview');
  assert.equal(petri.nodes.length, 7);
  assert.equal(petri.edges.length, 7);
  assert.equal(flow.nodes.length, 6);
  assert.equal(flow.edges.length, 6);
  assert.deepEqual(flow.folded, ['role.response']);
  assert.ok(flow.nodes.some(n => n.id === 'role.state'));
  assert.deepEqual(overview.nodes.map(n => n.id), ['role.model']);
  assert.equal(overview.edges.length, 1);
  const loop = overview.edges[0];
  assert.equal(loop.source, 'role.model');
  assert.equal(loop.target, 'role.model');
  assert.equal(overview.relation_semantics, 'next_agent_structural_path_not_execution_order');
  assert.equal(loop.witness_paths.length, 1);
  const edges = new Map(input.net.edges.map(e => [e.id, e]));
  const witness = loop.witness_paths[0].map(id => {
    assert.ok(edges.has(id), `Unknown witness edge: ${id}`);
    return edges.get(id);
  });
  assert.deepEqual(witness.map(e => e.source), ['role.model', 'role.response', 'role.act', 'role.state']);
  assert.deepEqual(witness.map(e => e.target), ['role.response', 'role.act', 'role.state', 'role.model']);
  assert.equal(witness[2].outcome, 'continue');
  const coverage = observationCoverage(frame);
  assert.equal(coverage.state, 'unsupported');
  assert.equal(coverage.loaded_count, null);
  assert.equal(coverage.total_count, null);
  assert.deepEqual(coverage.missing, ['registry_observation']);
  // Empty display aggregation is not evidence of zero observed firings.
  assert.deepEqual(overview.nodes[0].runtime.firings, []);
  assert.equal(JSON.stringify(input), before);
  console.log(`${role}: Petri 7/7, Flow 6/6, Overview 1/1; structural witness and unknown coverage verified`);
}
