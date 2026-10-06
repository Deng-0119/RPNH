import { observationContext } from './dashboard-model.mjs';

// Keep native disclosure state local to each card, including unavailable frames.
const openReferences = new WeakMap();
const MODE_LABELS = Object.freeze({
    current: '当前观察（current）',
    'canonical-as-of': '所选历史观察（canonical-as-of）',
});
const COVERAGE_LABELS = Object.freeze({
    complete: '本帧披露范围完整（complete）',
    partial: '本帧披露范围部分可用（partial）',
    not_provided: '未提供披露（not_provided）',
    not_disclosed: '未披露（not_disclosed）',
    unsupported: '不支持披露（unsupported）',
    read_failed: '披露读取失败（read_failed）',
    stale_observation: '过期观察（stale_observation）',
});

/** Render only the committed, normalized frame. No reads, paging or runtime state. */
export function renderObservationPanel(container, frame, { mode, translate }) {
    const previous = container.querySelector('details');
    if (previous)
        openReferences.set(container, previous.open);
    container.hidden = !['flow', 'petri', 'list'].includes(mode);
    if (container.hidden)
        return;

    const make = (tag, text) => {
        const node = container.ownerDocument.createElement(tag);
        if (text != null)
            node.textContent = text;
        return node;
    };
    const title = make('h2', translate('本帧观察上下文'));
    title.id = 'observation-panel-title';
    let context;
    try {
        // This stricter helper can reject source hints accepted by the legacy
        // normalizer. Its failure must never reject the main rendered frame.
        context = observationContext(frame);
    }
    catch {
        // Clear prior context rather than leaving a stale cut beside a new frame.
        // This is a card error, not a fabricated coverage.read_failed declaration.
        container.replaceChildren(title, make('p', translate('上下文不可用')));
        container.dataset.state = 'unavailable';
        return;
    }
    const source = context.source_cuts[0], coverage = source.coverage, values = make('dl');
    const row = (key, label, value) => {
        const item = make('div');
        item.dataset.field = key;
        item.append(make('dt', translate(label)), make('dd', value ?? translate('未知')));
        values.append(item);
    };
    row('mode', '观察模式', MODE_LABELS[context.mode] ? translate(MODE_LABELS[context.mode]) : context.mode);
    row('head', '所选 cut 的 head', source.cut?.head_ordinal);
    row('selector', 'Checkpoint 选择提示', context.checkpoint_selector);
    row('latest', '该帧报告的最新 head', context.observed_capture.latest_head_ordinal);
    row('coverage', 'Firing 披露状态', COVERAGE_LABELS[coverage.state] ? translate(COVERAGE_LABELS[coverage.state]) : coverage.state);
    row('loaded', '已加载 firing 数', coverage.loaded_count);
    row('total', '披露范围 firing 总数', coverage.total_count);
    const selectorNote = make('p', translate('Checkpoint 选择提示不是分页 cursor，也不证明存在已保存的 checkpoint。'));
    const scopeNote = make('p', translate('计数与状态仅涵盖本帧明确列出的 transition 的 firing 披露。'));
    const completeNote = coverage.state === 'complete'
        ? make('p', translate('complete 仅表示本帧披露范围完整，不证明所有真实执行或 lifecycle 证据完整。')) : null;
    const references = make('details');
    references.open = openReferences.get(container) ?? false;
    references.append(make('summary', translate('精确引用（完整 JSON）')));
    for (const name of ['net_ref', 'checkpoint_ref']) {
        const reference = make('div');
        reference.dataset.reference = name;
        reference.append(make('h3', name), make('pre', source[name] == null
            ? translate('未知') : JSON.stringify(source[name], null, 2)));
        references.append(reference);
    }
    container.replaceChildren(title, values, selectorNote, scopeNote, ...(completeNote ? [completeNote] : []), references);
    container.dataset.state = 'available';
}
