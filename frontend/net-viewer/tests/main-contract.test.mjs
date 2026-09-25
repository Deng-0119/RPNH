import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateSnapshot, statusLabel, tokensLabel, SnapshotOrder, head } from '../../../cpn/frontend/static/model.mjs';

const minimal = () => ({schema_version: 'rpnh/net_view/v1', source: {mode: 'registry_current', verified_head_ordinal: 12}, summary: {},
  nodes: [{id: 'p', label: 'input', kind: 'place', category: 'place', hidden_by_default: false, active_token_count: 2},
          {id: 't', label: 'step', kind: 'transition', category: 'execution', hidden_by_default: false}],
  edges: [{id: 'e', source: 'p', target: 't', kind: 'arc', mode: 'consume', weight: 1, outcome: null, hidden_by_default: false}]});

test('main-only projection needs neither firing records nor a backend discriminator', () => {
  const snapshot = minimal(); assert.equal(validateSnapshot(snapshot), snapshot);
  assert.equal(statusLabel(snapshot.nodes[1]), 'Execution records: not provided');
  assert.equal(tokensLabel(snapshot.nodes[0], snapshot.source.mode), '2');
  assert.equal(head(snapshot), 12);
});
test('main source verified_head_ordinal rejects older observations', () => {
  const order = new SnapshotOrder(); order.commit(minimal(), order.begin());
  const older = minimal(); older.source.verified_head_ordinal = 11;
  assert.throws(() => order.check(older, order.begin()), /older/);
});
test('optional execution details enrich the same projection without a provider-specific path', () => {
  const snapshot = minimal(); snapshot.nodes[1].runtime = {firings: [{status: 'settled'}]};
  validateSnapshot(snapshot); assert.match(statusLabel(snapshot.nodes[1]), /Settled: 1/);
});
test('initial marking declarations count multiplicities rather than array length', () => {
  assert.equal(tokensLabel({initial_tokens: [{count: 3}, {count: 2}]}, 'initial_configured'), '5');
  assert.equal(tokensLabel({}, 'registry_current'), '?');
});
test('shared display sources do not select implementations by backend name', () => {
  for (const name of ['app.js', 'model.mjs', 'layout.mjs', 'renderer.mjs', 'panels.mjs']) {
    const text = readFileSync(new URL('../../../cpn/frontend/static/' + name, import.meta.url), 'utf8');
    assert.doesNotMatch(text, /\b(?:dsh|codex|claude)\b/i, name);
  }
});
