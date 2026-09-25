/** Agent-only relation projection. Never an executable or reduced Petri net.
 * An arrow means a structural data/control path to the NEXT Agent, not an
 * observed firing order or proof that guards permit a particular execution.
 */
import { t } from './i18n.mjs';
import { byId, stable } from './model.mjs';

export function overviewGraph(raw, agentNodes = undefined) {
  const by = new Map(raw.nodes.map(n => [n.id, n]));
  // Only explicit read-side Agent declarations or the legacy executor contract.
  // A friendly name, a presentation type, or an executor key substring is NOT identity.
  const declared = agentNodes === undefined ? raw.nodes.filter(n => n.kind === 'transition'
    && (n.executor_declaration?.contracts?.transport === 'llm'
      || n.execution_kind === 'agent')).map(n => ({transition_id: n.id})) : agentNodes;
  if (!Array.isArray(declared)) throw new Error('Agent declarations must be an array');
  const agentIds = new Set();
  for (const item of declared) {
    if (!item || by.get(item.transition_id)?.kind !== 'transition')
      throw new Error('Agent declaration must reference an existing transition');
    agentIds.add(item.transition_id);
  }
  let nodes = raw.nodes.filter(n => agentIds.has(n.id)).map(n => ({...n,
    // The only glyph is an Agent card. No entry/exit/resource pseudo-nodes.
    display: {...n.display, role: 'agent', type: t('智能体')}, source_ids: [n.id]}));
  const resource = n => n?.category === 'resource'
    || ['agent_resource', 'resource_lease'].includes(n?.token_kind);
  const paths = raw.edges.filter(e => {
    const s = by.get(e.source), d = by.get(e.target);
    if (!s || !d || e.resource || resource(s) || resource(d) || e.kind !== 'arc') return false;
    // Explicit token rollback is housekeeping, not a new Agent handoff.
    // Do not guess this from an outcome name such as 'interrupted'.
    if (e.mode === 'produce' && e.emit === 'forward' && e.forward_source === e.target) return false;
    return (s.kind === 'transition' && d.kind === 'place' && e.mode === 'produce')
      || (s.kind === 'place' && d.kind === 'transition' && ['consume', 'read'].includes(e.mode));
  }).sort(byId);
  const outgoing = new Map();
  for (const e of paths) {
    if (!outgoing.has(e.source)) outgoing.set(e.source, []);
    outgoing.get(e.source).push(e);
  }
  const edges = [];
  for (const source of [...agentIds].sort()) {
    const queue = [source], seen = new Set(queue), reached = new Set(), walked = [];
    const parent = new Map(), last = new Map();
    // Iterative traversal terminates through tool/condition loops. Stop at the
    // first Agent: A -> B -> C must never acquire an artificial A -> C shortcut.
    for (let i = 0; i < queue.length; i++) for (const e of outgoing.get(queue[i]) ?? []) {
      walked.push(e);
      if (agentIds.has(e.target)) { reached.add(e.target); if (!last.has(e.target)) last.set(e.target, e); }
      else if (!seen.has(e.target)) { seen.add(e.target); parent.set(e.target, e); queue.push(e.target); }
    }
    const reverse = new Map();
    for (const e of walked) {
      if (!reverse.has(e.target)) reverse.set(e.target, []);
      reverse.get(e.target).push(e);
    }
    for (const target of [...reached].sort()) {
      // Keep all supporting original arcs, plus a deterministic simple witness.
      // These are provenance, not extra glyphs or labels in the Overview.
      const back = [target], visited = new Set(back), support = new Map();
      for (let i = 0; i < back.length; i++) for (const e of reverse.get(back[i]) ?? []) {
        support.set(e.id, e);
        if (e.source !== source && !agentIds.has(e.source) && !visited.has(e.source)) {
          visited.add(e.source); back.push(e.source);
        }
      }
      const witness = [last.get(target)];
      while (witness[0].source !== source) witness.unshift(parent.get(witness[0].source));
      const intermediate = [...new Set([...support.values()].flatMap(e => [e.source,e.target]))]
        .filter(id => !agentIds.has(id)).sort();
      edges.push({id: `agent-relation:${JSON.stringify([source,target])}`, source, target,
        kind: 'agent_relation', mode: 'agent_successor', weight: 1, outcome: null,
        resource: false, hidden_by_default: false, overview: true, agent_relation: true,
        display_label: '', source_ids: [...support.keys()].sort(),
        witness_arc_ids: witness.map(e => e.id), intermediate_node_ids: intermediate,
        hidden_places: intermediate.filter(id => by.get(id).kind === 'place')});
    }
  }
  // Existing, explicitly scoped identity may have several activation/rework
  // transitions. One Agent card retains all of them; equal names alone never merge.
  const groups = new Map(), representative = new Map();
  for (const item of [...declared].sort((a,b) => a.transition_id.localeCompare(b.transition_id, 'en'))) {
    const key = item.semantic_group ? 'semantic:' + stable(item.semantic_group)
      : item.agent_ref ? 'agent:' + stable(item.agent_ref) : 'transition:' + item.transition_id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item.transition_id);
  }
  for (const ids of groups.values()) for (const id of ids) representative.set(id, ids[0]);
  nodes = [...groups.values()].map(ids => ({...nodes.find(n => n.id === ids[0]), source_ids: [...new Set(ids)]}));
  const relations = new Map();
  for (const e of edges) {
    const source = representative.get(e.source), target = representative.get(e.target);
    const id = `agent-relation:${JSON.stringify([source,target])}`;
    if (!relations.has(id)) relations.set(id, {...e, id, source, target, source_ids: [], intermediate_node_ids: [], hidden_places: [], witness_paths: []});
    const r = relations.get(id);
    for (const field of ['source_ids','intermediate_node_ids','hidden_places']) r[field] = [...new Set([...r[field], ...e[field]])].sort();
    r.witness_paths.push(e.witness_arc_ids);
  }
  return {...raw, view_mode: 'overview', nodes: nodes.sort(byId), edges: [...relations.values()].sort(byId),
    folded: raw.nodes.filter(n => !agentIds.has(n.id)).map(n => n.id).sort(),
    agent_coverage: agentNodes !== undefined || declared.length ? 'provided' : 'not_provided',
    relation_semantics: 'next_agent_structural_path_not_execution_order',
    raw_node_count: raw.nodes.length, raw_edge_count: raw.edges.length};
}
