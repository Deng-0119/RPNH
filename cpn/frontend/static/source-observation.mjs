import { stable } from './model.mjs';

const states = new Set(['complete','partial','not_loaded','not_established','unavailable','denied','access_changed','read_failed','missing_dependency']);
function exact(value, kind) {
    if (!value || value.schema_version !== 'rpnh/collaboration/source_version_ref/v1'
        || typeof value.source_id !== 'string' || !value.source_id || value.ref?.entity_type !== kind
        || !/^resource:[a-f0-9]{32}$/.test(value.ref.logical_id)
        || !/^resource_version:[a-f0-9]{32}$/.test(value.ref.version_id)) throw new Error('invalid source reference');
    return value;
}
export function sourceObservationPath(reference=null) {
    return '/api/v2/source-observation' + (reference ? '?observation_ref='+encodeURIComponent(JSON.stringify(reference)) : '');
}
export function normalizeSourceObservation(raw, requested=null) {
    if (raw?.schema_version !== 'rpnh/source_observation/v1' || raw.global_atomic_snapshot !== false
        || raw.causal_completeness !== 'not_established' || !['query','supplement'].includes(raw.kind)
        || !Number.isInteger(raw.manifest_version) || raw.manifest_version < 1) throw new Error('invalid source observation');
    exact(raw.observation_ref,'collaboration_source_observation/v1');
    exact(raw.source_set_ref,'collaboration_source_set/v1');
    if (requested && stable(requested)!==stable(raw.observation_ref)) throw new Error('observation selection changed');
    if (raw.kind==='query' && (!raw.query_scope || !Number.isInteger(raw.query_scope.limit) || raw.query_scope.limit<1 || raw.query_scope.limit>1000 || !Array.isArray(raw.query_scope.media_types) || raw.query_scope.media_types.some(x=>typeof x!=='string'))) throw new Error('missing exact query scope');
    if (raw.kind==='supplement' && raw.query_scope!==null) throw new Error('supplement invents a query scope');
    if (raw.publication?.state!=='PUBLISHED' || !Number.isInteger(raw.publication.visible_ordinal) || raw.publication.visible_ordinal<1) throw new Error('capture is not canonically published');
    if (!Array.isArray(raw.sources) || !Array.isArray(raw.available_observations)) throw new Error('missing source scope');
    raw.available_observations.forEach(r=>exact(r,'collaboration_source_observation/v1'));
    if (!raw.available_observations.some(r=>stable(r)===stable(raw.observation_ref))) throw new Error('unselected observation');
    const ids=new Set();let count=0,complete=true;
    for(const row of raw.sources) {
        if (!row || typeof row.source_id!=='string' || !row.source_id || ids.has(row.source_id)
            || typeof row.access_path!=='string' || !row.access_path || !Array.isArray(row.headers)
            || !states.has(row.coverage?.state) || !states.has(row.capture_coverage?.state)
            || !['readable','denied','not_established','unavailable','access_changed','read_failed','not_checked'].includes(row.current_access)
            || 'authority' in row || 'cursor' in row) throw new Error('invalid source row');
        ids.add(row.source_id);count+=row.headers.length;complete &&=row.coverage.state==='complete';
        if (row.current_access==='readable') {
            if (!row.cut || !Number.isInteger(row.cut.ordinal) || row.coverage.loaded_count!==row.headers.length
                || row.coverage.total_count!==(row.coverage.state==='complete'?row.headers.length:null)) throw new Error('invalid source page coverage');
        } else if (['complete','partial'].includes(row.coverage.state) || row.headers.length || row.coverage.loaded_count!==null || row.coverage.total_count!==null
                   || row.capture_coverage.loaded_count!==null || row.capture_coverage.total_count!==null) throw new Error('inaccessible source retained content');
    }
    if (raw.coverage?.state!==(complete?'complete':'partial') || raw.coverage.loaded_count!==count
        || raw.coverage.total_count!==(complete?count:null) || raw.coverage.expected_source_count!==ids.size) throw new Error('invalid aggregate coverage');
    return structuredClone(raw);
}

export class SourceObservationState {
    constructor(){this.generation=0;this.value=null;this.error=null;this.loading=false;this.open=false;}
    begin(reference=null){this.open=true;this.loading=true;this.error=null;this.value=null;return {generation:++this.generation,reference};}
    accept(request,raw){if(request.generation!==this.generation||!this.open)return false;this.value=normalizeSourceObservation(raw,request.reference);this.loading=false;return true;}
    fail(request,status){if(request.generation!==this.generation||!this.open)return false;this.value=null;this.loading=false;this.error=status===403?'access_changed':status===501?'not_provided':'read_failed';return true;}
    close(){++this.generation;this.open=false;this.loading=false;this.value=null;this.error=null;}
}

export function renderSourceObservation(container,state,{translate=t=>t,onSelect=()=>{},onClose=()=>{}}={}) {
    const make=(tag,text)=>{const el=container.ownerDocument.createElement(tag);if(text!=null)el.textContent=String(text);return el;};
    container.hidden=!state.open;
    if(!state.open){container.replaceChildren();return;}
    const title=make('h2',translate('已记录来源查询'));title.id='source-observation-title';
    const close=make('button',translate('关闭'));close.onclick=onClose;
    const children=[title,close,make('p',translate('每个来源有独立 cut；此观察不证明全局原子或因果完整。'))];
    if(state.loading)children.push(make('p',translate('正在读取已记录观察…')));
    else if(state.error)children.push(make('p',translate('来源观察不可用')+': '+state.error));
    else if(state.value){
        const data=state.value;
        children.push(make('p',`S@${data.manifest_version} · ${data.kind} · ${data.coverage.state} · ${translate('已加载')}: ${data.coverage.loaded_count} · ${translate('总数')}: ${data.coverage.total_count??translate('未知')}`));
        children.push(make('p',`${translate('查询范围')}: ${data.query_scope?JSON.stringify(data.query_scope):translate('精确依赖补读')} · ${translate('正式可见位置')}: ${data.publication.visible_ordinal}`));
        if(data.available_manifest)children.push(make('p',translate('有新来源清单；此观察仍属于原清单。')));
        if(data.supplement_of)children.push(make('p',translate('独立补读；原观察保持不变。')));
        const nav=make('div');
        data.available_observations.forEach((ref,index)=>{const button=make('button',`${translate('观察')} ${index+1}`);button.disabled=stable(ref)===stable(data.observation_ref);button.onclick=()=>onSelect(ref);nav.append(button);});children.push(nav);
        for(const row of data.sources){
            const section=make('section');section.dataset.sourceId=row.source_id;
            section.append(make('p',`${translate('捕获披露标识')}: ${row.capture_disclosure??translate('未知')} · ${translate('当前披露标识')}: ${row.current_disclosure??translate('未知')}`));
            section.append(make('h3',`${row.source_id} · ${row.access_path}`),make('p',`${translate('捕获时')}: ${row.capture_coverage.state} · ${translate('当前访问')}: ${row.current_access} · cut: ${row.cut?.ordinal??translate('未知')}`),make('p',`${translate('已加载')}: ${row.coverage.loaded_count??translate('未知')} · ${translate('总数')}: ${row.coverage.total_count??translate('未知')}`));
            const list=make('ul');for(const header of row.headers)list.append(make('li',`${header.display_summary??header.ref.ref.resource_version_id} · ${header.media_type} · ${header.byte_size}`));section.append(list);children.push(section);
        }
        const refs=make('details');refs.append(make('summary',translate('精确引用（完整 JSON）')),make('pre',JSON.stringify({observation_ref:data.observation_ref,source_set_ref:data.source_set_ref,dependencies:data.dependencies},null,2)));children.push(refs);
    }
    container.replaceChildren(...children);
}
