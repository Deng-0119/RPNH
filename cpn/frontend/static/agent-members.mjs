/** Frame-local display/navigation only. No lookup, execution or cross-source authority. */
import { stable, head } from './model.mjs';
import { t as tr } from './i18n.mjs';

const present = value => typeof value === 'string' && value.trim().length > 0;
const identifier = value => present(value) || (value && typeof value === 'object' && !Array.isArray(value) && present(value.value));
// Recognize the complete reference shapes already disclosed by the viewer.
// A version-only display string cannot establish the missing logical identity.
export function exactReference(ref) {
    return Boolean(ref && typeof ref === 'object' && !Array.isArray(ref)
        && identifier(ref.version_id)
        && ((present(ref.entity_type) && (identifier(ref.logical_id) || identifier(ref.entity_id)))
            || (present(ref.kind) && identifier(ref.object_id))));
}
export function firingScope(frame) {
    const source = frame?.source, nested = frame?.net?.source;
    if (!source || !nested || !present(source.mode) || !exactReference(source.net_ref)
        || !(present(source.task_id) || present(source.run_dir))) return null;
    for (const key of ['mode', 'task_id', 'run_dir', 'net_ref']) {
        if (source[key] != null && nested[key] != null && stable(source[key]) !== stable(nested[key])) return null;
    }
    return structuredClone({ mode: source.mode, task_id: source.task_id ?? null,
        run_dir: source.run_dir ?? null, net_ref: source.net_ref });
}
const pending = new Set(['admitted', 'started', 'dispatch_authorized', 'returned_unsettled', 'failed_unsettled']);
const unavailable = new Set(['not_provided', 'not_disclosed', 'unsupported', 'read_failed', 'stale_observation']);

/** Rebuilt from this frame only; every disclosed member association participates
 * in conflict detection even when it belongs to another Agent card.
 */
function frameMembers(frame) {
    const bound = firingScope(frame), disclosure = frame?.coverage?.firings;
    const withheld = unavailable.has(disclosure), refs = new Map(), rows = [];
    const members = (frame?.net?.nodes ?? []).filter(node => node.kind === 'transition').map(node => {
        const transition_id = node.id;
        const provided = !withheld && Array.isArray(node?.runtime?.firings);
        const member = { transition_id, node, provided, rows: [] };
        const seen = new Map();
        for (const firing of provided ? node.runtime.firings : []) {
            const refKey = exactReference(firing?.firing_ref) ? stable(firing.firing_ref) : null;
            const factKey = stable(firing);
            // Deduplicate only the same complete identity with identical disclosed facts.
            if (refKey && seen.get(refKey)?.has(factKey)) continue;
            if (refKey) {
                if (!seen.has(refKey)) seen.set(refKey, new Set());
                seen.get(refKey).add(factKey);
            }
            const row = { transition_id, firing, refKey,
                conflict: firing?.transition_id != null && firing.transition_id !== transition_id, target: null };
            member.rows.push(row); rows.push(row);
            if (refKey) {
                if (!refs.has(refKey)) refs.set(refKey, []);
                refs.get(refKey).push(row);
            }
        }
        return member;
    });
    for (const list of refs.values()) {
        // Multiple member associations or differing facts under one full ref are inconsistent.
        if (list.length > 1) for (const row of list) row.conflict = true;
    }
    for (const row of rows) {
        row.reason = row.conflict ? 'inconsistent' : !row.refKey || !bound ? 'identity_missing' : null;
        if (!row.reason) row.target = { scope: structuredClone(bound), transition_id: row.transition_id,
            firing_ref: structuredClone(row.firing.firing_ref), observed_head: head(frame.net) };
    }
    return new Map(members.map(member => [member.transition_id, member]));
}

/** Only aggregate.source_ids choose displayed members and counts. The index
 * supplies frame-wide conflicts, never other members' rows or counts.
 */
function summarizeMembers(frame, aggregate, index) {
    const members = [...new Set(aggregate?.source_ids ?? [])].map(transition_id => index.get(transition_id)
        ?? { transition_id, node: null, provided: false, rows: [] });
    const rows = members.flatMap(member => member.rows), disclosure = frame?.coverage?.firings;
    const counts = { settled: 0, pending: 0, invalidated: 0, outcome_unknown: 0, other: 0 };
    for (const row of rows) {
        const firing = row.firing;
        if (!row.conflict) {
            const status = firing?.status;
            if (status === 'settled' || status === 'invalidated' || status === 'outcome_unknown') counts[status]++;
            else if (pending.has(status)) counts.pending++;
            else counts.other++;
        }
    }
    const conflicts = rows.filter(row => row.conflict).length;
    const complete = frame?.source?.mode === 'registry_current'
        && disclosure === (frame.position?.mode === 'history' ? 'canonical_only' : 'current_observations')
        && frame.source.net_ref != null && head(frame.net) != null
        && members.length > 0 && members.every(member => member.provided) && !conflicts;
    return { members, rows, counts, conflicts, complete, disclosure: disclosure ?? null,
        missing: members.filter(member => !member.provided).length,
        loaded_count: rows.length || (complete ? 0 : null), total_count: complete ? rows.length : null };
}

export function agentMembers(frame, aggregate) {
    return summarizeMembers(frame, aggregate, frameMembers(frame));
}
// One pure, short-lived index per graph construction; no mutable frame cache.
export function agentMemberSummaries(frame, aggregates) {
    const index = frameMembers(frame);
    return aggregates.map(aggregate => summarizeMembers(frame, aggregate, index));
}

export function agentCardState(summary) {
    if (summary.conflicts) return tr('已披露记录不一致 · {0} 行冲突', summary.conflicts);
    if (summary.loaded_count == null) return tr('成员执行披露未知');
    const c = summary.counts;
    return tr('已披露：{0} 未结算 · {1} 已结算 · {2} 未知', c.pending, c.settled, c.outcome_unknown + c.other)
        + (c.invalidated ? tr(' · {0} 失效', c.invalidated) : '')
        + (!summary.complete ? tr(' · 范围不完整') : '');
}

export function allMemberFirings(frame) {
    return agentMembers(frame, { source_ids: frame.net.nodes.filter(node => node.kind === 'transition').map(node => node.id) });
}
export function sameFiringTarget(a, b) {
    return Boolean(a && b && a.transition_id === b.transition_id && stable(a.scope) === stable(b.scope)
        && stable(a.firing_ref) === stable(b.firing_ref));
}
/** Revalidate only in an accepted frame. Advancing head alone does not change scope. */
export function resolveFiringTarget(frame, target) {
    const bound = firingScope(frame);
    if (!bound || !target?.scope || stable(bound) !== stable(target.scope)) return { target: null, reason: 'scope_changed' };
    const rows = allMemberFirings(frame).rows.filter(row => row.transition_id === target.transition_id
        && row.refKey && row.refKey === stable(target.firing_ref));
    if (!rows.length) return { target: null, reason: 'missing' };
    if (rows.some(row => row.conflict)) return { target: null, reason: 'inconsistent' };
    return { target: rows[0].target, reason: rows[0].reason };
}
