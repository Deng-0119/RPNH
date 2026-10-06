import {stable} from './model.mjs';
import {checkpointSelector, checkpointReference, normalizeCheckpointView, selectorForFrame} from './checkpoint-view.mjs';
import {messageError} from './i18n.mjs';

const UNKNOWN = ['definition_details','effective_execution','activity','resource_contents','native_scores','terminal_evidence'];
const AXES = ['definition','bindings','tokens','marking',...UNKNOWN];
const classifications = ['unchanged','content_change','present_left_only','present_right_only','unavailable'];
const same = (a,b) => stable(a) === stable(b);
const invalid = () => {throw messageError('检查点比较响应的身份或范围无效');};
const keys = (value, fields) => value && typeof value === 'object' && !Array.isArray(value)
    && same(Object.keys(value).sort(), [...fields].sort());
export function comparisonPair(left, right) {
    left=checkpointSelector(left);right=checkpointSelector(right);
    if(!same(left.net_ref,right.net_ref)) invalid();
    return {left_selector:left,right_selector:right};
}
export function comparisonPath(pair) {
    return '/api/v2/comparison-view?'+Object.entries(comparisonPair(pair.left_selector,pair.right_selector))
        .map(([k,v])=>`${k}=${encodeURIComponent(JSON.stringify(v))}`).join('&');
}
export function comparisonContext(frame) {
    const selected=selectorForFrame(frame);
    if (typeof frame.source.run_dir!=='string' || typeof frame.source.task_id!=='string') invalid();
    return {run_dir:frame.source.run_dir,task_id:frame.source.task_id,net_ref:selected.net_ref};
}
export function comparisonChoices(frame, items=[]) {
    const current=selectorForFrame(frame), result=new Map([[stable(current),current]]);
    for(const item of items) {
        const value=checkpointSelector({net_ref:current.net_ref,
            checkpoint_ref:checkpointReference(item.checkpoint_ref,'marking_checkpoint/v1',true),cut:item.cursor});
        result.set(stable(value),value);
    }
    return [...result.values()].sort((a,b)=>a.cut-b.cut);
}

const nodeFields=['label','kind','category','hidden_by_default','operation','operation_id','executor','inputs','outputs','token_kind','capacity','schema'];
const edgeFields=['source','target','kind','mode','weight','outcome','hidden_by_default','resource','direction','emit','forward_source'];
const bindingFields=['node_ref','operation_binding_ref','executable_binding_ref','operation','operation_id','input_ports','output_ports'];
const tokenFields=['place','kind','resource_ref','active_in_checkpoint'];
const has=(value,key)=>Object.hasOwn(value,key);
function records(items,idKey,fields) {
    if(!Array.isArray(items))invalid();const result=new Map();
    for(const item of items) {
        if(!item || !has(item,idKey))invalid();const key=stable(item[idKey]);if(result.has(key))invalid();
        result.set(key,[item[idKey],Object.fromEntries(fields.filter(k=>has(item,k)).map(k=>[k,item[k]]))]);
    }
    return result;
}
function facts(frame) {
    const net=frame.net,definition=new Map();
    for(const [group,fields] of [['nodes',nodeFields],['edges',edgeFields]])
        for(const [key,[id,value]] of records(net[group],'id',fields))definition.set(`${group}:${key}`,[{kind:group,id},value]);
    definition.set('boundaries',[{kind:'boundaries'},Object.fromEntries(['entry','exit','terminal_rules'].filter(k=>has(frame.boundaries,k)).map(k=>[k,frame.boundaries[k]]))]);
    const bindings=records(frame.transition_bindings,'transition_id',bindingFields),tokens=[];
    for(const node of net.nodes) {
        if(node.kind!=='place')continue;
        if(!Array.isArray(node.tokens))invalid();
        for(const token of node.tokens) {
            checkpointReference(token.token_ref,'petri_token/v1');
            if(token.place!==node.id || typeof token.active_in_checkpoint!=='boolean')invalid();tokens.push(token);
        }
        if(node.active_token_count!==node.tokens.filter(t=>t.active_in_checkpoint).length)invalid();
    }
    const marking=net.marking;
    if(!Number.isSafeInteger(marking.epoch) || marking.epoch<0 || marking.token_count!==tokens.length
        || marking.active_token_count!==tokens.filter(t=>t.active_in_checkpoint).length)invalid();
    return {definition,bindings,tokens:records(tokens,'token_ref',tokenFields),
        marking:new Map([['marking',[{kind:'marking'},Object.fromEntries(['epoch','token_count','active_token_count'].map(k=>[k,marking[k]]))]]])};
}
function rows(left,right) {
    const result=[];
    for(const key of [...new Set([...left.keys(),...right.keys()])].sort()) {
        const l=left.get(key),r=right.get(key),subject=(l??r)[0];
        for(const field of [...new Set([...Object.keys(l?.[1]??{}),...Object.keys(r?.[1]??{})])].sort()) {
            const fact=record=>!record?{status:'absent_at_selected_cut',value:null}:
                has(record[1],field)?{status:'provided',value:record[1][field]}:{status:'not_provided',value:null};
            const a=fact(l),b=fact(r),classification=[a.status,b.status].includes('not_provided')?'unavailable':
                !l?'present_right_only':!r?'present_left_only':same(a.value,b.value)?'unchanged':'content_change';
            result.push({subject,field,classification,left_fact:a,right_fact:b,reason_source:'current_analysis'});
        }
    }
    return result;
}
export function comparisonAxes(leftFrame,rightFrame) {
    const left=facts(leftFrame),right=facts(rightFrame),axes={};
    for(const axis of Object.keys(left))axes[axis]={coverage:'complete_in_declared_scope',rows:rows(left[axis],right[axis])};
    for(const axis of UNKNOWN)axes[axis]={coverage:'not_provided',rows:[]};
    return axes;
}

function publicSide(value) {
    const allowed=(obj,fields)=>{if(!obj || typeof obj!=='object' || Array.isArray(obj) || Object.keys(obj).some(k=>!fields.includes(k)))invalid();};
    const scalar=obj=>{if(Object.values(obj).some(v=>v!==null && !['string','number','boolean'].includes(typeof v)))invalid();};
    const ref=(obj,type)=>{if(!keys(obj,['entity_type','logical_id','version_id']) || obj.entity_type!==type
        || typeof obj.logical_id!=='string' || typeof obj.version_id!=='string')invalid();};
    allowed(value,['schema_version','selector','frame','navigation','capture','adoption_evidence','coverage']);
    if(!keys(value.capture,['head_ordinal','writer_fencing_epoch']))invalid();
    const frame=value.frame,net=frame.net;
    allowed(frame,['schema_version','source','net','boundaries','transition_bindings','position','coverage']);
    for(const source of [frame.source,net.source]) {
        allowed(source,['mode','run_dir','task_id','net_ref','verified_head_ordinal','writer_fencing_epoch']);
        ref(source.net_ref,'net_instance/v1');scalar(Object.fromEntries(Object.entries(source).filter(([k])=>k!=='net_ref')));
    }
    allowed(net,['schema_version','source','summary','nodes','edges','marking']);if(!keys(net.summary,[]))invalid();
    allowed(net.marking,['checkpoint_ref','epoch','token_count','active_token_count']);
    ref(net.marking.checkpoint_ref,'marking_checkpoint/v1');scalar(Object.fromEntries(Object.entries(net.marking).filter(([k])=>k!=='checkpoint_ref')));
    for(const node of net.nodes) {
        allowed(node,['id',...nodeFields,'active_token_count','tokens']);
        scalar(Object.fromEntries(Object.entries(node).filter(([k])=>!['inputs','outputs','tokens'].includes(k))));
        for(const field of ['inputs','outputs'])if(has(node,field) && (!Array.isArray(node[field]) || node[field].some(v=>typeof v!=='string')))invalid();
        for(const token of node.tokens??[]) {
            allowed(token,['token_ref',...tokenFields]);ref(token.token_ref,'petri_token/v1');
            scalar(Object.fromEntries(Object.entries(token).filter(([k])=>!['token_ref','resource_ref'].includes(k))));
            if(token.resource_ref!==null) {if(!keys(token.resource_ref,['resource_id','resource_version_id']))invalid();scalar(token.resource_ref);}
        }
    }
    for(const edge of net.edges){allowed(edge,['id',...edgeFields]);scalar(edge);}
    const boundaries=frame.boundaries;allowed(boundaries,['entry','exit','terminal_rules','terminal_evidence']);
    if(has(boundaries,'terminal_evidence') && boundaries.terminal_evidence!=='not_provided')invalid();
    for(const field of ['entry','exit'])for(const port of boundaries[field]??[]){allowed(port,['name','port','place']);scalar(port);}
    for(const rule of boundaries.terminal_rules??[]) {
        allowed(rule,['key','operation','transition_ids','outcome','port','place']);
        scalar(Object.fromEntries(Object.entries(rule).filter(([k])=>k!=='transition_ids')));
        if(!Array.isArray(rule.transition_ids) || rule.transition_ids.some(v=>typeof v!=='string'))invalid();
    }
    for(const binding of frame.transition_bindings) {
        allowed(binding,['transition_id',...bindingFields]);
        for(const [key,kind] of [['node_ref','node_declaration/v1'],['operation_binding_ref','operation_binding/v1'],['executable_binding_ref','executable_transition_binding/v1']])ref(binding[key],kind);
        scalar(Object.fromEntries(Object.entries(binding).filter(([k])=>['transition_id','operation','operation_id'].includes(k))));
        for(const key of ['input_ports','output_ports'])for(const port of binding[key]){allowed(port,['name','port_id','place']);scalar(port);}
    }
    allowed(frame.position,['mode','cursor','at','latest_head']);scalar(frame.position);
    allowed(frame.coverage,['history','firings','agent_nodes','provisional_history','terminal_evidence','end_reason']);scalar(frame.coverage);
    allowed(value.coverage,['topology','marking','bindings','firings','activity','cross_net_delta']);scalar(value.coverage);
    const nav=value.navigation;allowed(nav,['previous_net_segment','coverage','end_reason','max_chain','loaded_checkpoints']);
    if(nav.previous_net_segment!==null)checkpointSelector(nav.previous_net_segment);
    scalar(Object.fromEntries(Object.entries(nav).filter(([k])=>k!=='previous_net_segment')));
    const adoption=value.adoption_evidence;allowed(adoption,['status','coverage','cut','scope','current_net_ref','records']);
    if(adoption.current_net_ref!==null)ref(adoption.current_net_ref,'net_instance/v1');
    scalar(Object.fromEntries(Object.entries(adoption).filter(([k])=>!['current_net_ref','records'].includes(k))));
    for(const record of adoption.records) {
        allowed(record,['event_id','recorded_ordinal','visible_at_commit','net_ref']);ref(record.net_ref,'net_instance/v1');
        scalar(Object.fromEntries(Object.entries(record).filter(([k])=>k!=='net_ref')));
    }
}

/** Strict descriptive-only boundary. Missing or unavailable is never equality. */
export function normalizeComparison(value, expected, currentSource) {
    const pair=comparisonPair(expected.left_selector,expected.right_selector);
    if (!keys(value,['schema_version','comparison_mode','comparability','global_atomic_snapshot','source',
        'left_selector','right_selector','capture','left','right','coverage','axes'])
        || value.schema_version!=='rpnh/checkpoint_comparison/v1' || value.comparison_mode!=='descriptive'
        || value.comparability!=='not_established' || value.global_atomic_snapshot!==false
        || !same(value.left_selector,pair.left_selector) || !same(value.right_selector,pair.right_selector)
        || !keys(value.capture,['head_ordinal','writer_fencing_epoch'])
        || !same(value.source,currentSource)
        || !same(value.coverage,{status:'partial',scope:'public_checkpoint_projection',unknown_axes:UNKNOWN})
        || !keys(value.axes,AXES)) invalid();
    publicSide(value.left);publicSide(value.right);
    const left=normalizeCheckpointView(value.left,pair.left_selector,currentSource);
    const right=normalizeCheckpointView(value.right,pair.right_selector,currentSource);
    if (!same(left.capture,right.capture) || !same(left.capture,value.capture)) invalid();
    for (const side of [left,right]) {
        if (!same(comparisonContext(side.frame),currentSource)
            || side.coverage.topology!=='selected_declaration' || side.coverage.bindings!=='selected_net_exact_closure'
            || side.coverage.marking!=='selected_checkpoint'
            || ['activity','cross_net_delta'].some(k=>side.coverage[k]!=='not_provided')) invalid();
    }
    for(const axis of AXES) {
        const data=value.axes[axis];
        if (!keys(data,['coverage','rows']) || !Array.isArray(data.rows)) invalid();
        if (UNKNOWN.includes(axis)) {
            if(data.coverage!=='not_provided' || data.rows.length) invalid();
            continue;
        }
        if (data.coverage!=='complete_in_declared_scope') invalid();
        const seen=new Set();
        for(const row of data.rows) {
            if(!keys(row,['subject','field','classification','left_fact','right_fact','reason_source'])
                || typeof row.field!=='string' || !row.field || row.reason_source!=='current_analysis'
                || !classifications.includes(row.classification)) invalid();
            const key=stable([row.subject,row.field]);if(seen.has(key))invalid();seen.add(key);
            for(const fact of [row.left_fact,row.right_fact]) {
                if(!keys(fact,['status','value']) || !['provided','not_provided','absent_at_selected_cut'].includes(fact.status)
                    || (fact.status!=='provided' && fact.value!==null)) invalid();
            }
            const a=row.left_fact,b=row.right_fact;
            const classification=[a.status,b.status].includes('not_provided')?'unavailable':
                a.status==='absent_at_selected_cut'?'present_right_only':b.status==='absent_at_selected_cut'?'present_left_only':
                same(a.value,b.value)?'unchanged':'content_change';
            if (a.status==='absent_at_selected_cut' && b.status==='absent_at_selected_cut'
                || row.classification!==classification) invalid();
        }
    }
    const ordered=axes=>Object.fromEntries(Object.entries(axes).map(([axis,data])=>[axis,{...data,
        rows:[...data.rows].sort((a,b)=>stable([a.subject,a.field])<stable([b.subject,b.field])?-1:1)}]));
    if(!same(ordered(value.axes),ordered(comparisonAxes(left.frame,right.frame))))invalid();
    return structuredClone(value);
}

export class ComparisonState {
    constructor(){this.generation=0;this.open=false;this.loading=false;this.value=null;this.error=null;this.options=[];this.source=null;this.pair=null;}
    show(frame,items=[]) {
        this.close();this.options=comparisonChoices(frame,items);this.source=comparisonContext(frame);this.open=true;
        this.pair=comparisonPair(this.options[0],selectorForFrame(frame));
    }
    choose(side,index) {
        if(!this.open || !['left_selector','right_selector'].includes(side) || !Number.isInteger(index) || !this.options[index]) return false;
        ++this.generation;this.loading=false;this.value=null;this.error=null;
        this.pair=comparisonPair(side==='left_selector'?this.options[index]:this.pair.left_selector,
            side==='right_selector'?this.options[index]:this.pair.right_selector);return true;
    }
    begin(){if(!this.open || this.loading)return null;this.loading=true;this.value=null;this.error=null;
        return {generation:++this.generation,pair:structuredClone(this.pair),source:structuredClone(this.source)};}
    accepts(ticket){return this.open && ticket?.generation===this.generation && same(ticket.pair,this.pair) && same(ticket.source,this.source);}
    accept(ticket,value){if(!this.accepts(ticket))return false;const next=normalizeComparison(value,ticket.pair,ticket.source);
        this.value=next;this.loading=false;return true;}
    fail(ticket,status){if(!this.accepts(ticket))return false;this.value=null;this.loading=false;
        this.error=status===403?'access_changed':status===409?'stale_observation':[400,404,501].includes(status)?'not_provided':'read_failed';return true;}
    close(){++this.generation;this.open=false;this.loading=false;this.value=null;this.error=null;this.options=[];this.source=null;this.pair=null;}
}

const axisLabels={definition:'公开定义',bindings:'精确转换绑定',tokens:'token 实例',marking:'marking 摘要',
    definition_details:'未披露的定义详情',effective_execution:'实际模型、工具与 workspace',activity:'执行活动',resource_contents:'资源正文',native_scores:'原生评分',terminal_evidence:'终态证据'};
const classLabels={unchanged:'已提供字段相同',content_change:'字段不同',present_left_only:'仅左侧检查点包含',present_right_only:'仅右侧检查点包含',unavailable:'未知，不能判等'};
export function renderComparison(container,state,{translate=t=>t,onChoose=()=>{},onCompare=()=>{},onClose=()=>{}}={}) {
    const make=(tag,text)=>{const el=container.ownerDocument.createElement(tag);if(text!=null)el.textContent=String(text);return el;};
    container.hidden=!state.open;if(!state.open){container.replaceChildren();return;}
    const title=make('h2',translate('比较检查点'));title.id='comparison-title';
    const close=make('button',translate('关闭比较'));close.onclick=onClose;
    const content=[title,close,make('p',translate('同一 Registry、同一 exact 网；仅描述已提供的事实。未提供不等于相同，两个 cut 不构成全局原子快照。'))];
    const controls=make('div');controls.className='comparison-controls';
    for(const [side,label] of [['left_selector','左侧检查点'],['right_selector','右侧检查点']]) {
        const wrapper=make('label',translate(label)),select=make('select');select.setAttribute('aria-label',translate(label));
        state.options.forEach((choice,index)=>{const option=make('option',`C${choice.cut} · ${choice.checkpoint_ref.version_id}`);
            option.value=String(index);option.selected=same(choice,state.pair[side]);select.append(option);});
        select.value=String(state.options.findIndex(x=>same(x,state.pair[side])));
        select.onchange=()=>onChoose(side,Number(select.value));wrapper.append(select);controls.append(wrapper);
    }
    const compare=make('button',translate('读取比较'));compare.disabled=state.loading;compare.onclick=onCompare;controls.append(compare);content.push(controls);
    if(state.loading)content.push(make('p',translate('正在读取两个精确检查点…')));
    else if(state.error)content.push(make('p',translate('比较不可用；旧结果已清除。')+' '+state.error));
    else if(state.value) {
        const value=state.value;
        content.push(make('p',`C${value.left_selector.cut} ↔ C${value.right_selector.cut} · H${value.capture.head_ordinal}/E${value.capture.writer_fencing_epoch}`),
            make('p',translate('描述性比较；未建立评测可比性，不判断优胜或因果。')));
        for(const axis of AXES) {
            const data=value.axes[axis],section=make('section');section.append(make('h3',translate(axisLabels[axis])));
            if(data.coverage==='not_provided')section.append(make('p',translate('未提供，保持未知')));
            else {
                const changed=data.rows.filter(r=>r.classification!=='unchanged'),unchanged=data.rows.length-changed.length;
                section.append(make('p',translate('仅当前公开字段：不同或未知 {0} 项；相同 {1} 项',changed.length,unchanged)));
                if(!changed.length)section.append(make('p',translate('此公开范围内未发现差异；其他证据仍未知。')));
                for(const row of changed) {
                    const detail=make('details');detail.append(make('summary',`${translate(classLabels[row.classification])} · ${typeof row.subject==='string'?row.subject:JSON.stringify(row.subject)} · ${row.field}`),
                        make('pre',JSON.stringify({left:row.left_fact,right:row.right_fact},null,2)));section.append(detail);
                }
            }
            content.push(section);
        }
    }
    container.replaceChildren(...content);
}

/** Own one complete result, with no retained half-pair or result cache. */
export function installComparisonPanel(container,{request,translate=t=>t,onClose=()=>{},events=null}={}) {
    const state=new ComparisonState();let controller=null,resumeOnShow=false;
    const draw=()=>renderComparison(container,state,{translate,
        onChoose:(side,index)=>{controller?.abort();state.choose(side,index);draw();},
        onCompare:load,onClose:()=>close(true)});
    function close(resume=false){const wasOpen=state.open;controller?.abort();controller=null;state.close();draw();if(wasOpen && resume)onClose();}
    async function load(){const ticket=state.begin();if(!ticket)return;controller?.abort();controller=new AbortController();draw();
        try {const value=await request(comparisonPath(ticket.pair),controller.signal);state.accept(ticket,value);}
        catch(error){if(state.accepts(ticket))state.fail(ticket,error.status);}
        finally {if(state.accepts(ticket))draw();}}
    // Neither Back/Forward nor a restored bfcache page may redisclose cached pairs.
    events?.addEventListener?.('popstate',()=>close(true));
    events?.addEventListener?.('pagehide',()=>{resumeOnShow=resumeOnShow || state.open;close();});
    events?.addEventListener?.('pageshow',()=>{
        const resume=resumeOnShow || state.open;resumeOnShow=false;close();if(resume)onClose();
    });
    return {state,open(frame,items=[]){controller?.abort();state.show(frame,items);draw();},close,refreshLanguage:draw};
}
