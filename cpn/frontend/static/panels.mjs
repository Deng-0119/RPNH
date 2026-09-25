import { t as tr, messageError } from './i18n.mjs';
import { referenceText, firingSummary, arcLabel } from './model.mjs';
import { nodePresentation, cardState, allFirings } from './dashboard-model.mjs';
export function element(tag, text, className) { const el = document.createElement(tag); if (text != null)
    el.textContent = String(text); if (className)
    el.className = className; return el; }
const title = (target, text) => target.append(element('h3', text, 'section-title'));
function pairs(target, values) { const dl = element('dl', null, 'kv'); for (const [k, v] of values) {
    dl.append(element('dt', k), element('dd', v));
} target.append(dl); }
function json(target, label, value, opened = false) { const d = element('details'), summary = element('summary', label); d.append(summary, element('pre', JSON.stringify(value, null, 2))); d.open = opened; target.append(d); }
export function renderInspector(target, frame, selection, tab = 'about', firingId = null, select = () => { }, graph = null, openFlow = () => {}) {
    target.replaceChildren();
    if (graph?.view_mode === 'overview') {
        const selected = selection && (selection.kind === 'node' ? graph.nodes : graph.edges)
            .find(n => n.id === selection.id || (selection.kind === 'edge' && n.source_ids?.includes(selection.id)));
        if (!selected) return;
        const open = element('button', tr('打开完整 Petri 网'), 'primary');
        open.id = 'expand-overview'; open.onclick = openFlow;
        if (selection.kind === 'node') {
            target.append(element('span', tr('智能体'), 'status-pill'),
                element('p', selected.display.description, 'detail-description'));
            for (const [label, inbound] of [[tr('前一个智能体'), true], [tr('后一个智能体'), false]]) {
                title(target, label);
                const ids = new Set(graph.edges.filter(e => (inbound ? e.target : e.source) === selected.id)
                    .map(e => inbound ? e.source : e.target));
                if (!ids.size) target.append(element('p', tr('此流程中没有直接相邻的智能体'), 'muted'));
                for (const id of ids) {
                    const node = graph.nodes.find(n => n.id === id);
                    const b = element('button', node.display.name, 'firing-row');
                    b.onclick = () => select('node', id); target.append(b);
                }
            }
        } else {
            const a = graph.nodes.find(n => n.id === selected.source), b = graph.nodes.find(n => n.id === selected.target);
            target.append(element('p', `${a.display.name} → ${b.display.name}`, 'detail-description'));
        }
        target.append(element('p', tr('箭头表示流程中的下一位智能体。条件、工具和数据细节请查看 Petri 网；不表示该路径已经执行。'), 'muted'), open);
        return;
    }
    const item = selection && (selection.kind === 'node' ? frame.net.nodes : frame.net.edges).find(n => n.id === selection.id);
    if (!item) {
        target.append(element('p', tr("选择一个步骤、库所或连线查看详情。"), 'muted'));
        return;
    }
    if (selection.kind === 'edge') {
        target.append(element('p', `${item.source} → ${item.target}`, 'detail-description'), element('span', arcLabel(item), 'status-pill'));
        target.append(element('p', tr("这是声明中的真实连线。读取、消费、产出和重置的含义不同。"), 'muted'));
        if (tab === 'evidence')
            json(target, tr("精确弧声明"), item, true);
        return;
    }
    const d = nodePresentation(item, frame), firings = item.runtime?.firings ?? [];
    if (tab === 'about') {
        target.append(element('span', d.type, 'status-pill'), element('p', d.description, 'detail-description'));
        title(target, tr("当前观察"));
        target.append(element('p', cardState(item)));
        if (item.kind === 'transition') {
            pairs(target, [[tr("输入"), tr("{0} 个端口", item.inputs?.length ?? 0)], [tr("输出"), tr("{0} 个端口", item.outputs?.length ?? 0)], [tr("说明来源"), d.declared ? tr("显式展示说明") : tr("默认类型说明")]]);
            title(target, tr("上下游"));
            const neighbors = new Set(frame.net.edges.filter(e => e.source === item.id || e.target === item.id).map(e => e.source === item.id ? e.target : e.source));
            for (const id of neighbors) {
                const n = frame.net.nodes.find(n => n.id === id);
                const button = element('button', nodePresentation(n, frame).name, 'firing-row');
                button.onclick = () => select('node', id);
                target.append(button);
            }
        }
        else {
            title(target, tr("数据位置"));
            pairs(target, [[tr("有效 token"), item.active_token_count ?? tr("未提供")], [tr("声明容量"), item.capacity ?? tr("未设上限")], [tr("资源角色"), item.category === 'resource' ? tr("资源库所") : tr("普通库所")]]);
            if (d.entries.length)
                target.append(element('p', tr("入口：") + d.entries.map(x => x.name).join('、'), 'info-card'));
            if (d.exits.length || d.terminals.length)
                target.append(element('p', tr("这是声明的输出位置。需要正式终态证据才能确认整个任务结束。"), 'info-card'));
        }
        title(target, tr("阅读提示"));
        target.append(element('p', frame.position.mode === 'history' ? tr("此处是选定检查点的正式状态；不重建当时尚未结算的中间观察。") : tr("未结算与已结算是记录状态，不是进程存活或业务成功的证明。"), 'muted'));
        if (!d.declared)
            target.append(element('p', tr("宿主可通过独立展示说明补充这个节点的名称和职责，不修改执行定义。"), 'muted'));
    }
    else if (tab === 'executions') {
        if (item.kind === 'place') {
            title(target, tr("精确 token 引用"));
            for (const t of item.tokens ?? [])
                target.append(element('p', `${t.active_in_checkpoint ? tr("有效") : tr("非当前")} · ${referenceText(t.token_ref)}`, 'mono info-card'));
            if (!item.tokens)
                target.append(element('p', tr("此投影未提供 token 引用。"), 'muted'));
            else if (!item.tokens.length)
                target.append(element('p', tr("当前检查点中此库所没有 token。"), 'muted'));
        }
        else {
            target.append(element('p', item.runtime ? tr("此观察范围内 {0} 次记录", firings.length) : tr("逐次执行记录未提供；不表示未执行。"), 'muted'));
            for (const f of [...firings].reverse()) {
                const box = element('details', null, 'info-card');
                box.dataset.firingId = referenceText(f.firing_ref);
                box.open = referenceText(f.firing_ref) === firingId;
                box.append(element('summary', tr("第 {0} 次 · {1}", f.attempt_index ?? '?', executionStatus(f.status))));
                pairs(box, [[tr("业务结果"), f.business_outcome ?? tr("未提供")], [tr("准入位置"), f.admission_ordinal ?? tr("未提供")]]);
                box.append(element('p', referenceText(f.firing_ref), 'mono'));
                for (const event of f.events ?? [])
                    box.append(element('p', `#${event.ordinal} · ${event.type}`, 'muted'));
                target.append(box);
            }
        }
    }
    else {
        title(target, tr("身份与来源"));
        pairs(target, [[tr("节点 ID"), item.id], [tr("网版本"), referenceText(frame.source.net_ref)], [tr("观察位置"), frame.source.verified_head_ordinal ?? tr("未提供")]]);
        json(target, tr("节点声明与已披露记录"), item, true);
        const binding = frame.transition_bindings?.find(b => b.transition_id === item.id);
        if (binding)
            json(target, tr("精确执行绑定"), binding);
        json(target, tr("检查点"), frame.net.marking ?? {}, false);
        json(target, tr("能力与历史范围"), frame.coverage ?? {}, false);
        target.append(element('p', tr("提示词、配置正文与资源正文不由标准看板读取接口公开。"), 'muted'));
    }
}
export function renderExecutionTable(target, frame, select) {
    target.replaceChildren();
    const rows = allFirings(frame);
    if (!rows.length) {
        target.append(element('p', frame.net.nodes.some(n => n.kind === 'transition' && !n.runtime) ? tr("当前数据没有提供逐次执行记录。") : tr("该观察范围内尚无执行记录。"), 'info-card'));
        return;
    }
    const table = element('table'), thead = element('thead'), headerRow = element('tr');
    for (const text of [tr("步骤"), tr("第几次"), tr("记录状态"), tr("业务结果"), tr("准入位置")])
        headerRow.append(element('th', text));
    thead.append(headerRow);
    table.append(thead);
    const tbody = element('tbody');
    for (const { node, firing: f } of rows) {
        const row = element('tr'), cell = element('td'), b = element('button', nodePresentation(node, frame).name);
        b.onclick = () => select('node', node.id, referenceText(f.firing_ref));
        cell.append(b);
        row.append(cell, element('td', f.attempt_index ?? '—'), element('td', executionStatus(f.status)), element('td', f.business_outcome ?? tr("未提供")), element('td', '#' + (f.admission_ordinal ?? '?')));
        tbody.append(row);
    }
    table.append(tbody);
    target.append(table);
}
function executionStatus(status) { const known = { settled: tr("已结算"), started: tr("已记录开始"), invalidated: tr("已失效"), admitted: tr("已准入"), dispatch_authorized: tr("调度已授权"), returned_unsettled: tr("已返回，尚未结算"), failed_unsettled: tr("已失败，尚未结算"), outcome_unknown: tr("结果未知") }; return known[status] ?? tr("状态待识别 · {0}", status ?? tr("未提供")); }
