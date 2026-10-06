import { activityRequest, activityCards } from './firing-activity.mjs';
import { stable } from './model.mjs';
import { t as tr, messageError } from './i18n.mjs';
import { referenceText, firingSummary, arcLabel } from './model.mjs';
import { nodePresentation, cardState } from './dashboard-model.mjs';
import { agentMembers, agentCardState, allMemberFirings, sameFiringTarget } from './agent-members.mjs';
export function element(tag, text, className) { const el = document.createElement(tag); if (text != null)
    el.textContent = String(text); if (className)
    el.className = className; return el; }
const title = (target, text) => target.append(element('h3', text, 'section-title'));
function pairs(target, values) { const dl = element('dl', null, 'kv'); for (const [k, v] of values) {
    dl.append(element('dt', k), element('dd', v));
} target.append(dl); }
function json(target, label, value, opened = false) { const d = element('details'), summary = element('summary', label); d.append(summary, element('pre', JSON.stringify(value, null, 2))); d.open = opened; target.append(d); }
export function renderInspector(target, frame, selection, tab = 'about', firingTarget = null, select = () => { }, graph = null, openFlow = () => {}, activity = null, resource = null) {
    target.replaceChildren();
    if (graph?.view_mode === 'overview') {
        const selected = selection && (selection.kind === 'node' ? graph.nodes : graph.edges)
            .find(n => n.id === selection.id || (selection.kind === 'edge' && n.source_ids?.includes(selection.id)));
        if (!selected) return;
        const open = element('button', tr('打开完整 Petri 网'), 'primary');
        open.id = 'expand-overview'; open.onclick = () => openFlow();
        if (selection.kind === 'node') {
            target.append(element('span', tr('智能体'), 'status-pill'),
                element('p', selected.display.description, 'detail-description'));
            renderAgentMembers(target, frame, selected, openFlow);
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
    const d = nodePresentation(item, frame);
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
            for (const t of item.tokens ?? []) {
                const box = element('div', null, 'info-card');
                box.append(element('p', `${t.active_in_checkpoint ? tr("有效") : tr("非当前")} · ${referenceText(t.token_ref)}`, 'mono'));
                if (!Object.hasOwn(t, 'resource_ref'))
                    box.append(element('p', tr('此响应未提供此 token 的资源引用字段。'), 'muted'));
                else if (t.resource_ref === null)
                    box.append(element('p', tr('此 token 记录没有资源引用（resource_ref: null）。'), 'muted'));
                else
                    json(box, tr('token 资源引用（完整 JSON）'), t.resource_ref, true);
                if (resource && t.resource_ref != null) renderTokenResource(box, t, resource);
                target.append(box);
            }
            if (!item.tokens)
                target.append(element('p', tr("此投影未提供 token 引用。"), 'muted'));
            else if (!item.tokens.length)
                target.append(element('p', tr("当前检查点中此库所没有 token。"), 'muted'));
        }
        else {
            const disclosed = allMemberFirings(frame), member = disclosed.members.find(m => m.transition_id === item.id);
            const rows = member?.rows ?? [];
            target.append(element('p', member?.provided ? tr('本帧已披露 {0} 行记录', rows.length) : tr("逐次执行记录未提供；不表示未执行。"), 'muted'));
            for (const row of [...rows].reverse()) {
                const f = row.firing ?? {}, box = element('details', null, 'info-card');
                box.dataset.firingId = row.refKey ?? '';
                box.dataset.memberId = item.id;
                box.open = sameFiringTarget(row.target, firingTarget);
                box.append(element('summary', tr("第 {0} 次 · {1}", f.attempt_index ?? '?', executionStatus(f.status))));
                pairs(box, [[tr("业务结果"), f.business_outcome ?? tr("未提供")], [tr("准入位置"), f.admission_ordinal ?? tr("未提供")]]);
                box.append(element('p', referenceText(f.firing_ref), 'mono'));
                json(box, tr('完整 firing 引用'), f.firing_ref ?? null);
                if (row.reason) box.append(element('p', navigationReason(row.reason), 'muted'));
                for (const event of f.events ?? [])
                    box.append(element('p', `#${event.ordinal} · ${event.type}`, 'muted'));
                target.append(box);
            }
            if (activity) renderActivity(target, frame, item, activity);
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
function renderTokenResource(box, token, control) {
    let request;try {request=control.request(token);} catch {return;}
    const button=element('button',tr('查看资源登记'),'firing-row');
    button.dataset.tokenResource=stable(token.token_ref);button.onclick=()=>control.load(token);box.append(button);
    const state=control.state;
    if(stable(state.request)!==stable(request))return;
    button.disabled=state.loading;
    const close=element('button',tr('关闭资源登记'));close.dataset.closeTokenResource='true';close.onclick=control.close;box.append(close);
    if(state.loading)box.append(element('p',tr('正在读取资源登记…'),'muted'));
    if(state.error)box.append(element('p',tr({access_changed:'来源披露资格已改变，旧资源登记已清除。',
        stale_observation:'捕获已过期；仅保留同范围旧登记，请重新选择观察。',unsupported:'资源登记详情未提供；保留原 token 引用。',
        read_failed:'资源登记读取失败；未更新已有详情。'}[state.error]),'muted'));
    const value=state.metadata;if(!value)return;
    const scope=value.scope,metadata=value.registered_metadata,record=value.registration;
    title(box,tr('资源登记详情'));
    box.append(element('p',tr('所选检查点 C{0} · 捕获 H{1}/E{2}',scope.cut,scope.capture.head_ordinal,scope.capture.writer_fencing_epoch),'mono'));
    pairs(box,[[tr('登记大小（字节）'),metadata.byte_size],[tr('登记媒体类型'),metadata.media_type],
        [tr('登记 schema 标识'),metadata.content_schema_ref??tr('登记 schema 标识未提供')],
        [tr('登记 publication 记录位置'),record.publication_recorded_ordinal],
        [tr('所属完整事务提交位置'),record.publication_transaction_commit_ordinal]]);
    box.append(element('p',record.published_event_id,'mono'),
        element('p',tr('登记位置不代表首次正式可见、读取、消费或交付。'),'muted'),
        element('p',tr('本 metadata 投影零额外正文读取；未做内容完整性验证。登记值不是实测值。'),'muted'));
}

export function renderExecutionTable(target, frame, select) {
    target.replaceChildren();
    const disclosed = allMemberFirings(frame);
    const rows = [...disclosed.rows].sort((a, b) => (b.firing?.admission_ordinal ?? 0) - (a.firing?.admission_ordinal ?? 0));
    if (!rows.length) {
        target.append(element('p', disclosed.loaded_count == null ? tr("当前数据没有提供逐次执行记录。") : tr("该观察范围内尚无执行记录。"), 'info-card'));
        return;
    }
    const table = element('table'), thead = element('thead'), headerRow = element('tr');
    for (const text of [tr("步骤"), tr("第几次"), tr("记录状态"), tr("业务结果"), tr("准入位置")])
        headerRow.append(element('th', text));
    thead.append(headerRow);
    table.append(thead);
    const tbody = element('tbody');
    for (const entry of rows) {
        const node = disclosed.members.find(m => m.transition_id === entry.transition_id).node, f = entry.firing ?? {};
        const row = element('tr'), cell = element('td'), b = element('button', nodePresentation(node, frame).name);
        b.disabled = !entry.target;
        b.onclick = () => { if (entry.target) return select('node', node.id, entry.target); };
        if (entry.reason) cell.append(element('p', navigationReason(entry.reason), 'muted'));
        cell.append(b);
        row.append(cell, element('td', f.attempt_index ?? '—'), element('td', executionStatus(f.status)), element('td', f.business_outcome ?? tr("未提供")), element('td', '#' + (f.admission_ordinal ?? '?')));
        tbody.append(row);
    }
    table.append(tbody);
    target.append(table);
}
function navigationReason(reason) {
    return reason === 'inconsistent' ? tr('引用或成员关联不一致，不能精确导航。') : tr('缺少完整引用或来源身份，不能精确导航。');
}
function renderAgentMembers(target, frame, aggregate, openFlow) {
    const summary = agentMembers(frame, aggregate);
    title(target, tr('本帧全部成员与执行'));
    target.append(element('p', agentCardState(summary), 'info-card'));
    pairs(target, [[tr('真实成员'), summary.members.length],
        [tr('已加载记录行'), summary.loaded_count ?? tr('未知')],
        [tr('本帧范围总数'), summary.total_count ?? tr('未知')],
        [tr('观察位置'), frame.source.verified_head_ordinal ?? tr('未提供')]]);
    target.append(element('p', summary.complete ? tr('仅本帧这些成员的披露范围完整，不代表全部执行历史。') : tr('披露范围不完整或未知；缺失记录不能按零次执行解释。'), 'muted'));
    for (const member of summary.members) {
        const label = member.node ? nodePresentation(member.node, frame).name : member.transition_id;
        const button = element('button', label + ' · ' + member.transition_id, 'firing-row');
        button.disabled = !member.node;
        button.onclick = () => { if (member.node) return openFlow({ transition_id: member.transition_id }); };
        target.append(button);
        if (!member.provided) target.append(element('p', tr('成员记录未提供，不表示未执行。'), 'muted'));
        else if (!member.rows.length) target.append(element('p', tr('本帧此成员披露列表为空。'), 'muted'));
        for (const row of member.rows) {
            const f = row.firing ?? {}, box = element('div', null, 'info-card');
            const choice = element('button', tr('第 {0} 次 · {1}', f.attempt_index ?? '?', executionStatus(f.status)), 'firing-row');
            choice.dataset.memberId = member.transition_id;
            choice.dataset.firingId = row.refKey ?? '';
            choice.disabled = !row.target;
            choice.onclick = () => { if (row.target) return openFlow(row.target); };
            box.append(choice, element('p', referenceText(f.firing_ref), 'mono'));
            json(box, tr('完整 firing 引用'), f.firing_ref ?? null);
            if (row.reason) box.append(element('p', navigationReason(row.reason), 'muted'));
            target.append(box);
        }
    }
    target.append(element('p', tr('选择实例只定位当前帧的真实成员和完整引用，不授予执行权限。'), 'muted'));
}
function executionStatus(status) { const known = { settled: tr("已结算"), started: tr("已记录开始"), invalidated: tr("已失效"), admitted: tr("已准入"), dispatch_authorized: tr("调度已授权"), returned_unsettled: tr("已返回，尚未结算"), failed_unsettled: tr("已失败，尚未结算"), outcome_unknown: tr("结果未知") }; return known[status] ?? tr("状态待识别 · {0}", status ?? tr("未提供")); }

function renderActivity(target, frame, item, controls) {
    const state=controls.state, observed=state.observation, box=element('section',null,'activity-section');
    box.dataset.activity='true';title(box,tr('已记录活动（独立观察）'));
    if(state.paused) box.append(element('p',tr('保留当前主图观察；关闭活动后按原设置恢复自动刷新。'),'muted'));
    let available=true;try {activityRequest(frame,[item.id]);} catch {available=false;}
    const button=(label,action,disabled=false)=>{const b=element('button',tr(label));b.dataset.activityAction=action;b.disabled=disabled;b.onclick=()=>controls.load(action);box.append(b);};
    if (!observed) button('查看已记录活动','load',!available || state.loading);
    else {button('显式刷新活动','refresh',false);button('加载更多活动','more',state.loading || state.stale || !observed.next_cursor);}
    if(observed || state.loading || state.error) {const close=element('button',tr('关闭活动观察'));close.dataset.activityAction='close';close.onclick=controls.close;box.append(close);}
    if (!available) box.append(element('p',tr('缺少合格检查点或来源，活动未提供。'),'muted'));
    if (state.loading) box.append(element('p',tr('正在读取独立活动观察…'),'muted'));
    const errors={unsupported:'此来源不支持活动读取。',stale_observation:'活动观察已过期；保留旧 H/E，请显式刷新。',read_failed:'活动未更新；读取失败，保留已有观察。',access_changed:'来源披露资格已改变，旧活动已清除。'};
    if(state.error) box.append(element('p',tr(errors[state.error] ?? errors.read_failed),'info-card'));
    if(observed) {
        const cut=observed.context.source_cuts[0].cut;
        box.append(element('p',tr('活动依据 H{0}/E{1}；主图仍为检查点 #{2}',cut.head_ordinal,cut.writer_fencing_epoch,observed.selector.cut),'info-card'));
        box.append(element('p',tr('仅列准入与开始记录；完成、结果和结算详情未提供。'),'muted'));
        pairs(box,[[tr('已加载活动事件'),observed.records.length],[tr('已加载独立 firing'),observed.firings.length],
            [tr('这三类范围总数'),observed.complete?observed.records.length:tr('未知')],[tr('全部活动与 lifecycle 总数'),tr('未知')]]);
        box.append(element('p',tr(observed.complete?'仅这些成员在此 H/E 的三类事件已完整加载。':'这三类事件尚未完整加载；缺席不能解释为零。'),'muted'));
        for(const f of activityCards(observed)) {
            const card=element('details',null,'info-card'),key=stable(f.firing_ref);card.dataset.activityFiring=key;card.dataset.memberId=f.transition_id;card.open=state.expanded.has(key);
            card.ontoggle=()=>{if(card.open){state.expanded.add(key);controls.select(f.target);}else state.expanded.delete(key);};
            card.append(element('summary',tr('第 {0} 次 · {1}',f.attempt_index,tr(f.observed_stage==='started'?'已记录 operation start；结果未知':'当前已加载证据的开始阶段未知'))));
            pairs(card,[[tr('证据位置所见 publication class'),f.publication_class_at_evidence],
                [tr('正式可见位置'),f.publication_visible_position===null?tr('未提供'):String(f.publication_visible_position)],
                [tr('完成 / 结果 / successor checkpoint / delta'),tr('未提供')]]);
            json(card,tr('完整 firing 引用'),f.firing_ref,true);
            for(const r of f.records) card.append(element('p',tr('记录 #{0} · {1} · 完整事务提交 #{2}',r.recorded_ordinal,r.event_type,r.transaction_commit_ordinal),'mono'));
            box.append(card);
        }
    }
    target.append(box);
}
