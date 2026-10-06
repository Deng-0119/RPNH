/** Read-only Workset facts. Counts never infer missing physical source coverage. */
const integer = value => Number.isSafeInteger(value) && value >= 0;
const text=value=>typeof value==='string'&&value.length>0&&value.trim()===value;
const id=value=>text(value)&&/^[a-z][a-z0-9_]*:[a-f0-9]{32}$/.test(value);
const types={W:'collaboration_workset/v1',A:'collaboration_acceptance/v1',C:'collaboration_contribution/v1',
    L:'collaboration_logical_delivery/v1',O:'petri_token/v1',P:'resource_delivery/v1',T:'collaboration_root_terminal/v1',
    T2:'collaboration_root_terminal/v2',S:'execution_child_seal/v1'};
const fail=()=>{throw new Error('Invalid or inconsistent Workset observation');};
const own=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===names.length&&names.every(k=>Object.hasOwn(value,k));
function local(value,type){
    if(!own(value,['entity_type','logical_id','version_id'])||!text(value.entity_type)||! /^[a-z][a-z0-9_]*(?:\/[a-z][a-z0-9_]*)*\/v[1-9][0-9]*$/.test(value.entity_type)||!id(value.logical_id)||!id(value.version_id)
       ||(type&&value.entity_type!==type))fail();
    return [value.entity_type,value.logical_id,value.version_id].join('|');
}
function ref(value,type){
    if(!own(value,['schema_version','source_id','ref'])||value.schema_version!=='rpnh/collaboration/source_version_ref/v1'||!text(value.source_id))fail();
    return value.source_id+'|'+local(value.ref,type);
}
function unique(values){if(new Set(values).size!==values.length)fail();}
export function normalizeWorksets(raw) {
    if(!own(raw,['schema_version','source_id','capture_cut','coverage','current','history'])
       ||!['rpnh/workset_view/v1','rpnh/workset_view/v2'].includes(raw.schema_version)||!integer(raw.capture_cut)
       ||!['complete_local_worksets','not_registered'].includes(raw.coverage)
       ||!Array.isArray(raw.current)||!Array.isArray(raw.history)
       ||(raw.source_id!==null&&!text(raw.source_id)))fail();
    if(raw.coverage==='not_registered'&&(raw.source_id!==null||raw.current.length||raw.history.length))fail();
    const normalChildren=raw.schema_version==='rpnh/workset_view/v2';
    if(normalChildren&&raw.coverage==='not_registered')fail();
    const ids=[],versions=new Set();
    for(const row of raw.current){
        if(!own(row,['workset_ref','state','generation','collection_version','sequence','requirements_ref','input_binding_ref','expected_slots',
            'workset_sealed','required_child_seal_ref','acceptance_count','contribution_count','acceptance_coverage','contribution_coverage',
            'physical_attempt_count','physical_coverage','physical_source_cuts','physical_attempts','acceptances','contributions',
            'occurrence_count','available_occurrence_count','availability_checkpoint_ref',
            'recorded_physical_attempts','root_terminal_ref','root_terminal_evidence_ref','cancel_status','stop_status',
            ...(normalChildren?['root_child_closure']:[])]))fail();
        const identity=ref(row.workset_ref,types.W);ids.push(row.workset_ref.ref.logical_id);
        if(row.workset_ref.source_id!==raw.source_id||!['open','sealed','completed'].includes(row.state)
           ||!integer(row.sequence)||row.sequence<1||!integer(row.collection_version)||row.collection_version<1||!integer(row.generation)
           ||!Array.isArray(row.expected_slots)||!row.expected_slots.length||!row.expected_slots.every(text))fail();
        unique(row.expected_slots);ref(row.requirements_ref);ref(row.input_binding_ref);
        if(row.workset_sealed!==(row.state==='sealed'||row.state==='completed'))fail();
        if(row.required_child_seal_ref!==null)ref(row.required_child_seal_ref);
        if(row.availability_checkpoint_ref!==null)local(row.availability_checkpoint_ref,'marking_checkpoint/v1');
        if(row.root_terminal_ref!==null){
            const type=row.root_terminal_ref.ref?.entity_type;
            if(type!==types.T&&!(normalChildren&&type===types.T2))fail();
            ref(row.root_terminal_ref,type);
        }
        if(normalChildren){
            if(row.required_child_seal_ref!==null)fail();
            if(row.root_terminal_ref?.ref.entity_type===types.T2){
                const closure=row.root_child_closure;
                if(!own(closure,['profile','root_terminal_ref','seal_ref','verified_at_cut'])
                   ||closure.profile!=='execution-v1-normal-only'||!integer(closure.verified_at_cut)
                   ||closure.verified_at_cut!==raw.capture_cut||row.state!=='completed'
                   ||ref(closure.root_terminal_ref,types.T2)!==ref(row.root_terminal_ref,types.T2)
                   ||closure.root_terminal_ref.source_id!==raw.source_id||closure.seal_ref?.source_id!==raw.source_id)fail();
                ref(closure.seal_ref,types.S);
            }else if(row.root_child_closure!==null)fail();
        }
        if(row.root_terminal_evidence_ref!==null)local(row.root_terminal_evidence_ref,'run_terminal_evidence/v1');
        if(row.cancel_status!=='not_provided'||row.stop_status!=='not_provided')fail();
        const accepts=new Map(),slots=[];
        for(const a of row.acceptances||[]){
            if(!own(a,['acceptance_ref','target_workset_id','collection_version','input_binding_ref','logical_delivery_ref','initial_physical_delivery_ref','occurrence_ref','slot','availability'])
               ||!['available','absent_from_current_marking','not_provided'].includes(a.availability))fail();
            const aKey=ref(a.acceptance_ref,types.A);ref(a.logical_delivery_ref,types.L);ref(a.initial_physical_delivery_ref,types.P);local(a.occurrence_ref,types.O);
            if(a.acceptance_ref.source_id!==raw.source_id||!row.expected_slots.includes(a.slot)||accepts.has(aKey)
               ||a.target_workset_id!==row.workset_ref.ref.logical_id||a.collection_version!==row.collection_version
               ||ref(a.input_binding_ref)!==ref(row.input_binding_ref)
               ||a.logical_delivery_ref.source_id!==a.initial_physical_delivery_ref.source_id)fail();
            accepts.set(aKey,a);slots.push(a.slot);
        }
        unique(slots);const contributions=[],contributionSlots=[];
        unique([...accepts.values()].map(a=>local(a.occurrence_ref,types.O)));
        if(row.acceptance_coverage==='complete' ? row.occurrence_count!==accepts.size : row.occurrence_count!==null)fail();
        if(row.available_occurrence_count!==null&&(!integer(row.available_occurrence_count)
           ||row.acceptance_coverage!=='complete'||[...accepts.values()].some(a=>a.availability==='not_provided')
           ||row.available_occurrence_count!==[...accepts.values()].filter(a=>a.availability==='available').length
           ||row.availability_checkpoint_ref===null))fail();
        if(row.available_occurrence_count===null&&[...accepts.values()].some(a=>a.availability!=='not_provided'))fail();
        for(const c of row.contributions||[]){
            if(!own(c,['contribution_ref','acceptance_ref','occurrence_ref','slot']))fail();
            const key=ref(c.contribution_ref,types.C),a=accepts.get(ref(c.acceptance_ref,types.A));local(c.occurrence_ref,types.O);
            if(!a||a.slot!==c.slot||c.contribution_ref.source_id!==raw.source_id)fail();
            contributions.push(key);contributionSlots.push(c.slot);
        }
        unique(contributions);unique(contributionSlots);
        for(const [coverage,count,items]of [[row.acceptance_coverage,row.acceptance_count,row.acceptances],
            [row.contribution_coverage,row.contribution_count,row.contributions]]){
            if(!Array.isArray(items)||!['complete','not_provided'].includes(coverage)
               ||(coverage==='complete'?(!integer(count)||count!==items.length):count!==null)||items.length>row.expected_slots.length)fail();
        }
        if(!row.contributions.length&&(row.contribution_count!==null||row.contribution_coverage!=='not_provided'))fail();
        if(!Array.isArray(row.physical_attempts)||!Array.isArray(row.recorded_physical_attempts)
           ||!['complete_named_deliveries','source_not_observed'].includes(row.physical_coverage)
           ||(row.physical_coverage==='complete_named_deliveries'?(!integer(row.physical_attempt_count)||row.physical_attempt_count!==row.physical_attempts.length):row.physical_attempt_count!==null))fail();
        const attempts=[];
        if(!row.physical_source_cuts||typeof row.physical_source_cuts!=='object'||Array.isArray(row.physical_source_cuts)
           ||Object.entries(row.physical_source_cuts).some(([source,cut])=>!text(source)||!integer(cut)))fail();
        for(const p of row.physical_attempts){
            if(!own(p,['physical_delivery_ref','logical_delivery_ref','outcome']))fail();
            ref(p.physical_delivery_ref,types.P);const logical=ref(p.logical_delivery_ref,types.L);
            if(p.physical_delivery_ref.source_id!==p.logical_delivery_ref.source_id||!['unknown','acknowledged','failed'].includes(p.outcome)
               ||![...accepts.values()].some(a=>ref(a.logical_delivery_ref,types.L)===logical))fail();
            attempts.push(p.physical_delivery_ref.source_id+'|'+p.physical_delivery_ref.ref.entity_type+'|'+p.physical_delivery_ref.ref.logical_id);
        }
        unique(attempts);const proofs=[];
        for(const p of row.recorded_physical_attempts){
            if(!own(p,['physical_delivery_ref','logical_delivery_ref','acceptance_ref','original_outcome','reconciliation_refs'])
               ||!Array.isArray(p.reconciliation_refs)||!p.reconciliation_refs.length)fail();
            unique(p.reconciliation_refs.map(r=>ref(r,'collaboration_delivery_reconciliation/v1')));
            const physical=ref(p.physical_delivery_ref,types.P),logical=ref(p.logical_delivery_ref,types.L),a=accepts.get(ref(p.acceptance_ref,types.A));
            if(!a||ref(a.logical_delivery_ref,types.L)!==logical||p.physical_delivery_ref.source_id!==p.logical_delivery_ref.source_id
               ||!['unknown','acknowledged'].includes(p.original_outcome))fail();
            proofs.push(physical);
        }
        unique(proofs);
        const history=raw.history.filter(h=>h.workset_ref?.ref?.logical_id===row.workset_ref.ref.logical_id);
        if(history.length!==row.sequence||history.filter(h=>h.is_current).length!==1
           ||!history.some(h=>h.is_current&&ref(h.workset_ref,types.W)===identity))fail();
    }
    unique(ids);
    for(const h of raw.history){
        if(!own(h,['workset_ref','sequence','collection_version','action','state_at_commit','recorded_ordinal','is_current']))fail();
        const key=ref(h.workset_ref,types.W);
        if(h.workset_ref.source_id!==raw.source_id||!ids.includes(h.workset_ref.ref.logical_id)||versions.has(key)
           ||!integer(h.sequence)||h.sequence<1||!integer(h.collection_version)||h.collection_version<1
           ||!integer(h.recorded_ordinal)||h.recorded_ordinal>raw.capture_cut
           ||!['create','grow','seal','accept','contribute','complete'].includes(h.action)
           ||!['open','sealed','completed'].includes(h.state_at_commit)||typeof h.is_current!=='boolean')fail();
        versions.add(key);
    }
    return structuredClone(raw);
}

const labels = {
    en: {title:'Worksets', current:'Current business collection', history:'Immutable history', refresh:'Refresh', close:'Close',
        empty:'No Workset records', missing:'Not observed', failed:'Workset facts are unavailable',
        acceptance:'Accepted', contribution:'Contributed', physical:'Physical attempts', source:'Source coverage',
        root:'Root terminal evidence', child:'Required-child seal', rootChild:'Root child closure', cut:'Verified at cut', cancel:'Cancellation', stop:'Stop',
        records:'Recorded physical evidence', occurrences:'Accepted occurrences', available:'Currently available', links:'Exact acceptance / occurrence / contribution links',
        note:'This view captures current Worksets independently of the selected PN checkpoint'},
    zh: {title:'Workset', current:'当前业务集合', history:'不可变历史', refresh:'刷新', close:'关闭',
        empty:'尚无 Workset 记录', missing:'未观察', failed:'Workset 事实暂不可用',
        acceptance:'已接纳', contribution:'已贡献', physical:'物理交付尝试', source:'来源覆盖',
        root:'根 terminal 证据', child:'required-child seal', rootChild:'根 child 闭合', cut:'验证 cut', cancel:'取消', stop:'停止',
        records:'已登记物理证据', occurrences:'接纳 occurrence', available:'当前可用', links:'精确接纳 / occurrence / 贡献关联',
        note:'本视图独立捕获当前 Workset，不替代所选 PN 检查点'}
};
function el(tag, text) {const node=document.createElement(tag); if(text!==undefined)node.textContent=text; return node;}
export function renderWorksets(container, value, language='en') {
    const data=normalizeWorksets(value), t=labels[language]||labels.en;
    container.replaceChildren(el('p',t.note),el('p',`${data.source_id||t.missing} · cut ${data.capture_cut}`));
    if (!data.current.length) container.append(el('p',t.empty));
    for (const row of data.current) {
        const section=el('section'); section.className='info-card';
        section.append(el('h3',`${t.current}: ${row.state} · g${row.generation} / c${row.collection_version}`));
        section.append(el('p',`${t.acceptance}: ${row.acceptance_count??t.missing} / ${row.expected_slots.length} · ${t.contribution}: ${row.contribution_count??t.missing} / ${row.expected_slots.length}`));
        section.append(el('p',`${t.occurrences}: ${row.occurrence_count??t.missing} · ${t.available}: ${row.available_occurrence_count??t.missing}`));
        const links=el('details');links.append(el('summary',t.links));const related=el('ul');
        for(const a of row.acceptances)related.append(el('li',`A ${a.acceptance_ref.ref.version_id} → O ${a.occurrence_ref.version_id} · ${a.slot} · ${a.availability}`));
        for(const c of row.contributions)related.append(el('li',`C ${c.contribution_ref.ref.version_id} uses A ${c.acceptance_ref.ref.version_id} → ${c.occurrence_ref.version_id} · ${c.slot}`));
        links.append(related);section.append(links);
        section.append(el('p',`${t.physical}: ${row.physical_attempt_count??t.missing} · ${t.source}: ${row.physical_coverage}`));
        section.append(el('p',`${t.root}: ${row.root_terminal_evidence_ref?.version_id||t.missing}`));
        section.append(el('p',`${t.child}: ${row.required_child_seal_ref?.ref?.version_id||t.missing} · ${t.cancel}: ${t.missing} · ${t.stop}: ${t.missing}`));
        if(row.root_child_closure){
            const closure=row.root_child_closure;
            section.append(el('p',`${t.rootChild}: ${closure.seal_ref.ref.version_id} · ${closure.profile} · ${t.cut}: ${closure.verified_at_cut}`));
        }
        section.append(el('h4',t.records));
        const facts=el('ul');
        for (const item of row.recorded_physical_attempts) facts.append(el('li',`${item.original_outcome} · ${item.physical_delivery_ref.ref.logical_id} → ${item.acceptance_ref.ref.logical_id}`));
        section.append(facts); container.append(section);
    }
    container.append(el('h3',t.history)); const history=el('ol');
    for (const row of data.history) history.append(el('li',`v${row.sequence} · c${row.collection_version} · ${row.action} · ${row.state_at_commit}${row.is_current?' · current':''}`));
    container.append(history);
}

export function installWorksetPanel(button) {
    if (!button) return;
    const dialog=el('dialog'), heading=el('div'), content=el('div'), refresh=el('button'), close=el('button');
    heading.className='guide-heading';content.className='guide-body';
    heading.append(el('h2','Worksets'),refresh,close);dialog.append(heading,content);document.body.append(dialog);
    let serial=0, controller=null;
    const language=()=>document.getElementById('language')?.value==='zh-CN'?'zh':'en';
    const cancel=()=>{serial++;controller?.abort();controller=null;};
    async function load(){
        cancel(); const request=serial;controller=new AbortController();const t=labels[language()];
        refresh.textContent=t.refresh;close.textContent=t.close;refresh.disabled=true;content.replaceChildren(el('p','…'));
        try {
            const response=await fetch('/api/v2/worksets',{signal:controller.signal,cache:'no-store'});
            if (!response.ok) throw new Error('Unavailable'); const raw=await response.json();
            if(request===serial&&dialog.open)renderWorksets(content,raw,language());
        } catch(error){if(request===serial&&dialog.open&&error.name!=='AbortError')content.replaceChildren(el('p',t.failed));}
        finally {if(request===serial)refresh.disabled=false;}
    }
    button.addEventListener('click',()=>{if(!dialog.open)dialog.showModal();load();});
    refresh.addEventListener('click',load);close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',cancel);dialog.addEventListener('cancel',cancel);
}
