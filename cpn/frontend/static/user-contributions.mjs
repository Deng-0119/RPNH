import { stable } from './model.mjs';
import { checkpointReference, checkpointSelector, selectorForFrame } from './checkpoint-view.mjs';
import { t as tr, messageError } from './i18n.mjs';

const invalid = () => { const error=messageError('贡献记录的身份或范围无效'); error.status=400; throw error; };
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 16384;
const same = (a, b) => stable(a) === stable(b);
const record = value => value && typeof value === 'object' && !Array.isArray(value);
function exactReference(value, type = null) {
    if (!record(value) || !same(Object.keys(value).sort(), ['entity_type', 'logical_id', 'version_id'])
        || !text(value.entity_type) || (type && value.entity_type !== type)
        || !text(value.logical_id) || !text(value.version_id)
        || !/^[a-z][a-z0-9_]*:[a-f0-9]{32}$/.test(value.logical_id)
        || !/^[a-z][a-z0-9_]*_version:[a-f0-9]{32}$/.test(value.version_id)) invalid();
    if (type && (!/^resource:[a-f0-9]{32}$/.test(value.logical_id)
        || !/^resource_version:[a-f0-9]{32}$/.test(value.version_id))) invalid();
    return {...value};
}
const sessionRef = value => exactReference(value, 'user_collaboration_session/v1');
const revisionRef = value => exactReference(value, 'collaboration_net_revision/v1');
export function contributionPath(value) {
    const selector = checkpointSelector(value);
    return '/api/v2/user-contributions?' + Object.entries(selector).map(([key, value]) =>
        `${key}=${encodeURIComponent(typeof value === 'object' ? JSON.stringify(value) : String(value))}`).join('&');
}
export function contributionScope(frame) {
    try { return stable({selector: selectorForFrame(frame), source: {task_id: frame.source.task_id, run_dir: frame.source.run_dir}}); }
    catch { return null; }
}
/** Only exact, source-bound recorded facts can color the actual net. */
export function normalizeContributions(value, expected, frame) {
    const selector = checkpointSelector(value?.selector);
    if (value?.schema_version !== 'rpnh/user_contributions/v1' || !same(selector, checkpointSelector(expected))
        || !same(selector, selectorForFrame(frame)) || !record(value.source)
        || !same(Object.keys(value.source).sort(),['source_id','task_id','task_ref'])
        || value.source.task_id !== frame.source.task_id || !text(value.source.task_id) || !text(value.source.source_id)
        || checkpointReference(value.source.task_ref,'task/v1').logical_id !== value.source.task_id
        || !['complete', 'partial', 'unknown'].includes(value.coverage?.status)
        || typeof value.coverage.reason !== 'string' || value.coverage.reason.length > 16384
        || !Array.isArray(value.actors) || value.actors.length > 10000
        || !Array.isArray(value.nodes) || value.nodes.length > 50000
        || !Array.isArray(value.actions) || value.actions.length > 50000) invalid();
    const pairs = new Set(), actorSessions = value.actors.map(actor => {
        if (!text(actor?.actor)) invalid();
        const session_ref = sessionRef(actor.session_ref), key = stable([actor.actor, session_ref]);
        if (pairs.has(key)) invalid();
        pairs.add(key); return {actor: actor.actor, session_ref};
    });
    const checkActor = (actor, session, nullable = false) => {
        if (nullable && actor === null && session === null) return;
        if (!text(actor) || !pairs.has(stable([actor, sessionRef(session)]))) invalid();
    };
    const frameIds = new Set(frame.net.nodes.map(node => node.id)), seen = new Set();
    let changesCount = 0;
    const nodes = value.nodes.map(node => {
        if (!frameIds.has(node?.node_id) || seen.has(node.node_id)
            || !['changed', 'inherited', 'overlap', 'unknown'].includes(node.state)
            || !Array.isArray(node.changes) || (changesCount += node.changes.length) > 100000) invalid();
        seen.add(node.node_id);
        return {node_id: node.node_id, state: node.state, changes: node.changes.map(change => {
            checkActor(change?.actor, change?.session_ref);
            if (!text(change.element_id) || !text(change.locator) || !['added', 'modified', 'removed'].includes(change.change)
                || !['adopted', 'proposed', 'inherited', 'superseded'].includes(change.status)
                || !Array.isArray(change.fields) || change.fields.length > 1000 || change.fields.some(field => !text(field))) invalid();
            return {actor: change.actor, session_ref: sessionRef(change.session_ref),
                proposal_ref: exactReference(change.proposal_ref, 'user_collaboration_proposal/v1'),
                base_revision_ref: revisionRef(change.base_revision_ref), candidate_revision_ref: revisionRef(change.candidate_revision_ref),
                element_id: change.element_id, locator: change.locator, change: change.change, status: change.status, fields: [...change.fields]};
        })};
    });
    const actions = value.actions.map(action => {
        if (!['proposed', 'reviewed', 'adopted', 'executed'].includes(action?.role) || !text(action.status)
            || !Array.isArray(action.node_ids) || action.node_ids.length > 50000
            || new Set(action.node_ids).size !== action.node_ids.length || action.node_ids.some(id => !frameIds.has(id))) invalid();
        checkActor(action.actor, action.session_ref, true);
        return {role: action.role, actor: action.actor, session_ref: action.session_ref === null ? null : sessionRef(action.session_ref),
            evidence_ref: exactReference(action.evidence_ref), node_ids: [...action.node_ids], status: action.status};
    });
    return {schema_version: value.schema_version, selector, source: {task_id:value.source.task_id,source_id:value.source.source_id,task_ref:checkpointReference(value.source.task_ref,'task/v1')}, coverage: {...value.coverage},
        actors: actorSessions, nodes, actions,
        selected_revision_ref: value.selected_revision_ref === null ? null : revisionRef(value.selected_revision_ref),
        adoption_receipt_ref: value.adoption_receipt_ref === null ? null : exactReference(value.adoption_receipt_ref, 'user_collaboration_receipt/v1')};
}

export const CONTRIBUTION_UNKNOWN = '#78828f', CONTRIBUTION_OVERLAP = '#9254b7';
export function contributionColor(actor) {
    // The whole canonical label is hashed. No name, model or execution inference.
    let hash = 2166136261;
    for (const character of actor) hash = Math.imul(hash ^ character.codePointAt(0), 16777619) >>> 0;
    return `hsl(${hash % 360} 62% 38%)`;
}
export function contributionOverlay(value, actor = null) {
    if (!value) return null;
    const nodes = new Map(value.nodes.map(node => {
        const actors = [...new Set(node.changes.map(change => change.actor))];
        const color = node.state === 'overlap' ? CONTRIBUTION_OVERLAP
            : ['changed','inherited'].includes(node.state) && actors.length === 1 ? contributionColor(actors[0]) : CONTRIBUTION_UNKNOWN;
        return [node.node_id, {state: node.state, color, match: actor === null || actors.includes(actor),
            dash: node.state === 'overlap' ? '3 2' : node.state === 'inherited' ? '7 3' : node.state === 'unknown' ? '2 4' : ''}];
    }));
    return {nodes, actor};
}
export function contributionNodeStyle(overlay, id) {
    if (!overlay) return null;
    return overlay.nodes.get(id) ?? {state: 'unknown', color: CONTRIBUTION_UNKNOWN, match: overlay.actor === null, dash: '2 4'};
}
const stateLabels = {changed: '有变更记录', inherited: '继承记录', overlap: '重叠变更', unknown: '来源未知'};
const changeLabels = {added: '新增', modified: '修改', removed: '移除'};
const statusLabels = {adopted: '已采用', proposed: '仅提议', inherited: '继承', superseded: '已被替代'};
const roleLabels = {proposed: '提议记录', reviewed: '审阅记录', adopted: '采用记录', executed: '执行记录'};
const errorLabels = {unsupported: '此入口未配置受信任的 HOST 贡献读取；来源仍未知。', access_changed: '贡献访问已改变；已清除记录和配色。',
    stale: '所选观察已过期；已清除贡献记录，请刷新主图后重试。', invalid: '贡献记录的身份或范围无效', limit: '贡献记录超过读取上限；未显示不完整的归属配色。',
    failed: '贡献记录暂不可读；来源仍未知。'};

export function installContributionsPanel(target, {request, getFrame, onOpen = () => {}, onClose = () => {},
    onChange = () => {}, onFocus = () => {}, translate = tr, events = globalThis.window} = {}) {
    const state = {open: false, value: null, loading: false, error: null, actor: null, selected: null, limit: 80};
    let generation = 0, controller = null, bound = null, overlay = null, sourceAnchor = null;
    const el = (tag, text, className) => {const node = target.ownerDocument.createElement(tag);
        if (text != null) node.textContent = String(text); if (className) node.className = className; return node;};
    const button = (text, callback, name) => {const node = el('button', text); node.type = 'button'; node.onclick = callback;
        if (name) node.dataset.contributionAction = name; return node;};
    const evidence = (parent, label, value) => {const details = el('details'); details.dataset.contributionEvidence=stable(value); details.append(el('summary', label), el('pre', JSON.stringify(value, null, 2))); parent.append(details);};
    const changed = () => {overlay = state.open ? contributionOverlay(state.value, state.actor) ?? {nodes:new Map(),actor:null} : null; draw(); onChange();};
    function nodeDetails(parent, node) {
        const section = el('section', null, 'contribution-detail');
        section.append(el('h3', translate('节点贡献：{0}', node.node_id)), el('p', translate(stateLabels[node.state])),
            el('p', translate('作者身份未知；以下仅为记录中的提议者。'), 'muted'));
        for (const change of node.changes) {
            const card = el('article', null, 'contribution-change');
            card.append(el('strong', translate('记录中的提议者：{0}', change.actor)),
                el('p', `${translate(changeLabels[change.change])} · ${translate(statusLabels[change.status])}`),
                el('p', translate('元素 {0} · {1}', change.element_id, change.locator)),
                el('p', translate('变更字段：{0}', change.fields.join(', ') || '—')));
            evidence(card, translate('精确提议与修订引用'), change); section.append(card);
        }
        const actions = state.value.actions.filter(action => action.node_ids.includes(node.node_id));
        for (const action of actions) {
            const card = el('article', null, 'contribution-change');
            card.append(el('strong', translate(roleLabels[action.role])), el('p', action.actor ?? translate('行为者未记录')),
                el('p', translate('记录状态：{0}', action.status)));
            evidence(card, translate('精确行为证据'), action); section.append(card);
        }
        if (!node.changes.length && !actions.length) section.append(el('p', translate('此节点没有可用的贡献证据；不推断作者。')));
        parent.append(section);
    }
    function draw() {
        const scroll=target.scrollTop, opened=new Set([...target.querySelectorAll('details')].filter(node=>node.open).map(node=>node.dataset.contributionEvidence)),
            active=target.ownerDocument.activeElement, focus=active?.dataset?.contributionAction,
            focusNode=active?.dataset?.nodeId;
        drawContent();
        for(const node of target.querySelectorAll('details'))if(opened.has(node.dataset.contributionEvidence))node.open=true;
        if(focus)for(const node of target.querySelectorAll('[data-contribution-action]')) {
            if(node.dataset.contributionAction===focus&&node.dataset.nodeId===focusNode){node.focus?.();break;}
        }
        target.scrollTop=scroll;
    }
    function drawContent() {
        target.hidden = !state.open; target.replaceChildren();
        if (!state.open) return;
        const heading = el('div', null, 'contribution-heading'), title = el('h2', translate('用户贡献')); title.id = 'user-contributions-title';
        heading.append(title, button(translate('关闭贡献'), close, 'close')); target.append(heading);
        target.append(el('p', translate('本地 HOST 会话标签，不代表已验证的远端身份或模型。作者身份始终未知。'), 'contribution-disclosure'),
            el('p', translate('配色表示提议记录；采用、审阅和执行分别列示。同一元素可影响多个节点，不算多份独立贡献。'), 'muted'));
        const refresh = button(translate('重新读取贡献'), load, 'refresh'); refresh.disabled = state.loading;
        target.append(refresh);
        if (state.loading) {const status = el('p', translate('正在读取精确贡献记录…')); status.setAttribute('role', 'status'); target.append(status);}
        if (state.error) {const status = el('p', translate(errorLabels[state.error])); status.setAttribute('role', 'status'); target.append(status);}
        const value = state.value; if (!value) return;
        const coverage = {complete: '贡献读取完整', partial: '贡献读取不完整', unknown: '贡献读取范围未知'};
        target.append(el('p', `${translate(coverage[value.coverage.status])} · ${value.coverage.reason}`));
        const select = el('select'), label = el('label', translate('按记录中的行为者聚焦'));
        select.setAttribute('aria-label', translate('按记录中的行为者聚焦')); select.dataset.contributionAction = 'actor';
        const all = el('option', translate('所有记录中的行为者')); all.value = ''; select.append(all);
        for (const actor of [...new Set(value.actors.map(item => item.actor))]) {const option = el('option', actor); option.value = actor; select.append(option);}
        select.value = state.actor ?? '';
        select.onchange = () => {if (select.value && !value.actors.some(item => item.actor === select.value)) return;
            state.actor = select.value || null; state.limit = 80; changed();};
        label.append(select); target.append(label);
        const legend = el('div', null, 'contribution-legend');
        for (const kind of ['changed', 'inherited', 'overlap', 'unknown']) {
            const item = el('span', translate(stateLabels[kind])); item.dataset.contributionState = kind;
            item.style.borderColor = kind === 'overlap' ? CONTRIBUTION_OVERLAP : ['changed','inherited'].includes(kind) ? contributionColor(state.actor ?? value.actors[0]?.actor ?? '') : CONTRIBUTION_UNKNOWN;
            legend.append(item);
        }
        if (state.actor) {const item = el('span', state.actor); item.style.borderColor = contributionColor(state.actor); legend.append(item);}
        else {
            const actors = [...new Set(value.actors.map(item => item.actor))], palette = el('details'), colors = el('div', null, 'contribution-legend');
            palette.dataset.contributionEvidence='actor-palette';palette.append(el('summary',translate('记录中的行为者配色')));
            for(const actor of actors.slice(0,20)) {const item=el('span',actor);item.style.borderColor=contributionColor(actor);colors.append(item);}
            if(actors.length>20)colors.append(el('p',translate('另有 {0} 个行为者可在筛选中选择',actors.length-20)));
            palette.append(colors);target.append(palette);
        }
        target.append(legend, el('p', translate('未匹配节点仅变淡；完整 Petri 网与资源开关保持可用。'), 'muted'));
        evidence(target, translate('所选观察与采用证据'), {selector: value.selector, source: value.source, coverage: value.coverage,
            selected_revision_ref: value.selected_revision_ref, adoption_receipt_ref: value.adoption_receipt_ref, actors: value.actors});
        const allNodes = new Map(value.nodes.map(node => [node.node_id, node]));
        const nodes = getFrame().net.nodes.map(node => allNodes.get(node.id) ?? {node_id: node.id, state: 'unknown', changes: []})
            .filter(node => !state.actor || node.changes.some(change => change.actor === state.actor));
        const unique = new Set(nodes.flatMap(node => node.changes.filter(change => !state.actor || change.actor === state.actor)
            .map(change => stable([change.proposal_ref, change.element_id, change.locator, change.change]))));
        target.append(el('p', translate('{0} 个关联节点 · {1} 条去重元素变更记录', nodes.length, unique.size)));
        if (state.selected) nodeDetails(target, allNodes.get(state.selected) ?? {node_id: state.selected, state: 'unknown', changes: []});
        const list = el('div', null, 'contribution-nodes');
        for (const node of nodes.slice(0, state.limit)) {
            const row = button(`${node.node_id} · ${translate(stateLabels[node.state])}`, () => onFocus(node.node_id), 'focus');
            row.dataset.nodeId = node.node_id; row.setAttribute('aria-pressed', String(node.node_id === state.selected)); list.append(row);
        }
        target.append(list);
        if (nodes.length > state.limit) target.append(button(translate('显示更多节点（剩余 {0}）', nodes.length - state.limit),
            () => {state.limit += 80; draw();}, 'more'));
        const unscoped = value.actions.filter(action => action.node_ids.length === 0);
        if (unscoped.length) evidence(target, translate('未映射至节点的独立行为证据'), unscoped);
    }
    function close(resume = true) {
        const wasOpen = state.open; ++generation; controller?.abort(); controller = null; bound = null; overlay = null; sourceAnchor = null;
        Object.assign(state, {open: false, value: null, loading: false, error: null, actor: null, selected: null, limit: 80});
        draw(); if (wasOpen) {onChange(); onClose(resume);}
    }
    async function load() {
        if (!state.open) return;
        const frame = getFrame(), scope = contributionScope(frame);
        ++generation; controller?.abort(); const ticket = generation;
        state.value = null; state.error = null; state.actor = null; state.loading = true;
        if(bound!==scope)sourceAnchor=null;
        bound = scope; controller = new AbortController(); changed();
        const accepted = () => state.open && ticket === generation && bound === contributionScope(getFrame());
        try {
            if (!scope) {state.error = 'unsupported'; return;}
            const selector = selectorForFrame(frame), raw = await request(contributionPath(selector), controller.signal);
            if (!accepted()) return;
            if (raw?.source?.task_id !== frame.source.task_id || (sourceAnchor && !same(raw?.source,sourceAnchor))) {
                const error = new Error('access changed'); error.status = 403; throw error;
            }
            state.value = normalizeContributions(raw, selector, frame);
            sourceAnchor = structuredClone(state.value.source);
        } catch (error) {
            if (!accepted() || error.name === 'AbortError') return;
            state.value = null;
            state.error = error.status === 403 ? 'access_changed' : error.status === 409 ? 'stale'
                : [404, 501].includes(error.status) ? 'unsupported' : error.status === 413 ? 'limit' : error.status === 400 ? 'invalid' : 'failed';
        } finally {if (accepted()) {state.loading = false; changed();}}
    }
    function open() {
        if (state.open) return; state.open = true; onOpen(); return load();
    }
    const pagehide = () => close(false); events?.addEventListener('pagehide', pagehide); events?.addEventListener('popstate', () => close());
    draw();
    return {state, open, close, load, refreshLanguage: draw, overlay: () => overlay,
        selected(id) {if (id !== state.selected) {state.selected = id; if (state.open) draw();}},
        checkFrame() {if (state.open && bound !== contributionScope(getFrame())) close();}};
}
