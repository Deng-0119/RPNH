/** Three-event retrospective evidence. Never merges into frame.net.runtime. */
import {stable} from './model.mjs';
import {checkpointSelector, selectorForFrame} from './checkpoint-view.mjs';
import {messageError} from './i18n.mjs';
export const ACTIVITY_TYPES = Object.freeze(['firing_admitted/v1','transition_firing_started/v1','operation_execution_started/v1']);
const fail = () => {throw messageError('活动响应的身份、范围或分页无效');};
const same = (a,b) => stable(a) === stable(b);
const keys = (v,n) => v && typeof v==='object' && !Array.isArray(v) && same(Object.keys(v).sort(),[...n].sort());
const positive = n => Number.isSafeInteger(n) && n>0;
const token = s => typeof s==='string' && s.length>0 && s.length<=8192 && /^[A-Za-z0-9_-]+$/.test(s);
const kinds = {'task/v1':['task','task_version'],'native_run_identity/v1':['run','run_version'],
    'transition_firing/v1':['transition_firing','transition_firing_version'],
    'node_declaration/v1':['node','node_declaration_version'],'invocation/v1':['invocation','invocation_version']};
function ref(v,type) {
    if (!keys(v,['entity_type','logical_id','version_id']) || v.entity_type!==type || !kinds[type]?.every((kind,i)=>new RegExp(`^${kind}:[a-f0-9]{32}$`).test(v[i?'version_id':'logical_id']))) fail();
    return v;
}
function source(v) {
    if (!keys(v,['mode','run_dir','task_id']) || v.mode!=='registry_current' || typeof v.run_dir!=='string' || !v.run_dir || !/^task:[a-f0-9]{32}$/.test(v.task_id)) fail();
}
export function activityRequest(frame, transition_ids, cursor=null, limit=50) {
    const selector=selectorForFrame(frame), ids=[...transition_ids].sort();
    if (!ids.length || ids.length>64 || new Set(ids).size!==ids.length || ids.some(x=>typeof x!=='string' || !x || x.length>256
        || !frame.net.nodes.some(n=>n.kind==='transition' && n.id===x) || !frame.transition_bindings?.some(b=>b.transition_id===x))
        || !positive(limit) || limit>100 || (cursor!==null && !token(cursor))) fail();
    const origin={mode:frame.source.mode,run_dir:frame.source.run_dir,task_id:frame.source.task_id};source(origin);
    return {...selector,transition_ids:ids,cursor,limit};
}
export function activityPath(request) {
    const entries=Object.entries(request).filter(([,v])=>v!==null);
    const query=entries.map(([k,v])=>`${k}=${encodeURIComponent(typeof v==='object'?JSON.stringify(v):String(v))}`).join('&');
    if (query.length>16384) fail();
    return '/api/v2/firing-activity?'+query;
}
export function normalizeActivityPage(value, request, frame) {
    if (!keys(value,['schema_version','selector','view','page']) || value.schema_version!=='rpnh/firing_activity/v1'
        || !same(checkpointSelector(value.selector),checkpointSelector({net_ref:request.net_ref,checkpoint_ref:request.checkpoint_ref,cut:request.cut}))
        || !same(value.selector,selectorForFrame(frame)) || !keys(value.view,['context','activity'])) fail();
    const {context,activity:a}=value.view, page=value.page, q=context?.query_scope;
    if (!keys(context,['mode','source_set_ref','manifest_ref','path_ref','disclosure_ref','query_scope','source_cuts'])
        || context.mode!=='retrospective-activity' || ['source_set_ref','manifest_ref','path_ref','disclosure_ref'].some(k=>context[k]!==null)
        || !Array.isArray(context.source_cuts) || context.source_cuts.length!==1
        || !keys(context.source_cuts[0],['source','cut'])) fail();
    const capture=context.source_cuts[0].cut;
    if (!keys(capture,['head_ordinal','writer_fencing_epoch']) || !positive(capture.head_ordinal)
        || !Number.isSafeInteger(capture.writer_fencing_epoch) || capture.writer_fencing_epoch<0 || capture.head_ordinal<request.cut) fail();
    if (!keys(q,['task_ref','run_ref','net_ref','checkpoint_ref','cut','transition_ids','supported_event_types','source','disclosure_profile','evidence_cut'])) fail();
    ref(q.task_ref,'task/v1');ref(q.run_ref,'native_run_identity/v1');source(q.source);
    if (q.source.task_id!==q.task_ref.logical_id || q.source.task_id!==frame.source.task_id || q.source.run_dir!==frame.source.run_dir
        || !same(q.source,context.source_cuts[0].source) || !same(q.net_ref,request.net_ref)
        || !same(q.checkpoint_ref,request.checkpoint_ref) || q.cut!==request.cut
        || !same(q.transition_ids,request.transition_ids) || !same(q.supported_event_types,ACTIVITY_TYPES)
        || q.disclosure_profile!=='single_registry_standard_activity_metadata/v1' || !same(q.evidence_cut,capture)) fail();
    if (!keys(a,['source_identity','supported_event_types','record_range','firings','records','coverage','lifecycle_coverage'])
        || !keys(a.source_identity,['task_ref','run_ref','source'])
        || !same(a.source_identity,{task_ref:q.task_ref,run_ref:q.run_ref,source:q.source})
        || !same(a.supported_event_types,ACTIVITY_TYPES) || !same(a.record_range,{after_ordinal:0,through_ordinal:capture.head_ordinal})
        || a.lifecycle_coverage!=='not_provided' || !Array.isArray(a.firings) || !Array.isArray(a.records)
        || !keys(a.coverage,['scope','status','loaded_count','total_count','missing'])
        || !same(a.coverage.scope,q) || a.coverage.status!=='page' || a.coverage.loaded_count!==a.records.length
        || a.coverage.total_count!==null || !same(a.coverage.missing,['completion','result','settlement','successor_checkpoint','delta','other_activity'])) fail();
    if (!keys(page,['request_cursor','returned_count','limit','has_more','end_of_supported_scope','next_cursor'])
        || page.request_cursor!==request.cursor || page.returned_count!==a.records.length || page.limit!==request.limit
        || a.records.length>page.limit || typeof page.has_more!=='boolean' || page.end_of_supported_scope!==!page.has_more
        || (page.has_more ? (!token(page.next_cursor) || a.records.length!==page.limit || page.next_cursor===page.request_cursor) : page.next_cursor!==null)) fail();
    const firings=new Map();
    for (const f of a.firings) {
        if (!keys(f,['firing_ref','node_ref','transition_id','attempt_index','admission_checkpoint_ref','invocation_ref',
            'publication_class_at_evidence','publication_visible_position','business_outcome','completion','result','successor_checkpoint','delta'])) fail();
        ref(f.firing_ref,'transition_firing/v1');ref(f.node_ref,'node_declaration/v1');ref(f.invocation_ref,'invocation/v1');
        checkpointSelector({net_ref:request.net_ref,checkpoint_ref:f.admission_checkpoint_ref,cut:request.cut});
        const binding=frame.transition_bindings.find(b=>b.transition_id===f.transition_id), k=stable(f.firing_ref);
        if (!q.transition_ids.includes(f.transition_id) || !binding || !same(binding.node_ref,f.node_ref)
            || !positive(f.attempt_index) || firings.has(k)
            || !['PROVISIONAL','PUBLISHED'].includes(f.publication_class_at_evidence)
            || (f.publication_class_at_evidence==='PROVISIONAL' ? f.publication_visible_position!==null : !positive(f.publication_visible_position) || f.publication_visible_position>capture.head_ordinal)
            || ['business_outcome','completion','result','successor_checkpoint','delta'].some(k=>f[k]!=='not_provided')) fail();
        firings.set(k,f);
    }
    const seen=new Set(), used=new Set();let previous=null;
    for (const r of a.records) {
        if (!keys(r,['firing_ref','event_id','event_type','transaction_id','recorded_ordinal','recorded_at','transaction_commit_ordinal','evidence'])) fail();
        ref(r.firing_ref,'transition_firing/v1');const f=firings.get(stable(r.firing_ref)), e=r.evidence;
        if (!f || !/^event:[a-f0-9]{32}$/.test(r.event_id) || !/^transaction:[a-f0-9]{32}$/.test(r.transaction_id)
            || !ACTIVITY_TYPES.includes(r.event_type) || !positive(r.recorded_ordinal) || !positive(r.transaction_commit_ordinal)
            || r.recorded_ordinal>r.transaction_commit_ordinal || r.transaction_commit_ordinal>capture.head_ordinal
            || typeof r.recorded_at!=='string' || seen.has(r.event_id)
            || (previous && (r.recorded_ordinal<previous.recorded_ordinal || (r.recorded_ordinal===previous.recorded_ordinal && r.event_id<=previous.event_id)))
            || !keys(e,['pointer','recorded_position','visible_position','publication_class','observed_at'])
            || !same(e.pointer,{source:q.source,firing_ref:r.firing_ref}) || !same(e.recorded_position,{event_id:r.event_id,ordinal:r.recorded_ordinal})
            || !same(e.observed_at,capture) || e.publication_class!==f.publication_class_at_evidence
            || (e.publication_class==='PROVISIONAL' ? e.visible_position!==null : e.visible_position!==Math.max(f.publication_visible_position,r.transaction_commit_ordinal))) fail();
        seen.add(r.event_id);used.add(stable(r.firing_ref));previous=r;
    }
    if (used.size!==firings.size) fail();
    return structuredClone(value);
}
/** Only continuous, validated pages from the first page establish this scope. */
export function mergeActivity(previous, page) {
    const a=page.view.activity, key=stable(page.view.context), first=page.page.request_cursor===null;
    if (!first && (!previous || key!==previous.context_key || previous.next_cursor!==page.page.request_cursor
        || previous.cursors.includes(page.page.request_cursor))) fail();
    const result=first?{context_key:key,context:page.view.context,selector:page.selector,firings:[],records:[],cursors:[]}:structuredClone(previous);
    if (page.page.next_cursor!==null && result.cursors.includes(page.page.next_cursor)) fail();
    const events=new Map(result.records.map(r=>[r.event_id,r])), firings=new Map(result.firings.map(f=>[stable(f.firing_ref),f]));
    let last=result.records.at(-1);
    for (const r of a.records) {
        if (events.has(r.event_id) || (last && (r.recorded_ordinal<last.recorded_ordinal || (r.recorded_ordinal===last.recorded_ordinal && r.event_id<=last.event_id)))) fail();
        events.set(r.event_id,r);last=r;
    }
    for (const f of a.firings) {
        const k=stable(f.firing_ref);
        if (firings.has(k) && !same(firings.get(k),f)) fail();
        firings.set(k,f);
    }
    result.records=[...events.values()];result.firings=[...firings.values()];result.cursors.push(page.page.request_cursor);
    result.next_cursor=page.page.next_cursor;result.complete=page.page.end_of_supported_scope;
    return result;
}
export function activityCards(observation) {
    if (!observation) return [];
    return observation.firings.map(f=>{
        const records=observation.records.filter(r=>same(r.firing_ref,f.firing_ref));
        return {...f,records,observed_stage:records.some(r=>r.event_type==='operation_execution_started/v1')?'started':'unknown',
            target:{kind:'firing_activity',context_key:observation.context_key,transition_id:f.transition_id,firing_ref:f.firing_ref}};
    });
}
export function resolveActivityTarget(observation,target) {
    if (!keys(target,['kind','context_key','transition_id','firing_ref']) || target.kind!=='firing_activity'
        || observation?.context_key!==target.context_key) return null;
    return activityCards(observation).find(f=>same(f.target,target))?.target ?? null;
}
