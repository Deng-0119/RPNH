/** Explicit exact-pair navigation. No graph data is an execution capability. */
import { stable, nodeKey, edgeKey } from './model.mjs';
import { NetRenderer } from './renderer.mjs';
import { LayoutCache } from './layout.mjs';

export const CONTEXT_SCHEMA='rpnh/comparison_context/v1';
export const AXES=Object.freeze(['definition','configuration','materials','runtime']);
const same=(a,b)=>stable(a)===stable(b);
const errorCode=error=>error?.code??({403:'access_changed',409:'stale_observation',413:'scope_limit',422:'projection_unavailable',501:'unsupported'}[error?.status]??'read_failed');
const invalid=()=>{const error=new Error('invalid_response');error.code='invalid_response';throw error;};
const object=(v,keys,optional=[])=>{if(!v||Array.isArray(v)||typeof v!=='object'||keys.some(k=>!Object.hasOwn(v,k))||Object.keys(v).some(k=>!keys.includes(k)&&!optional.includes(k)))invalid();return v;};
const text=v=>{if(typeof v!=='string'||!v.length||v.length>4096||/[\x00-\x1f]/.test(v))invalid();return v;};
const integer=(v,min=0)=>{if(!Number.isSafeInteger(v)||v<min)invalid();};
const array=v=>{if(!Array.isArray(v)||v.length>200000)invalid();return v;};
const unique=v=>{array(v);if(new Set(v.map(stable)).size!==v.length)invalid();return v;};
const reasons=new Set(['exact_identity','verified_author_mapping','explicit_presence','not_requested','not_provided','not_disclosed','not_captured','incomplete_scope','mapping_missing','mapping_unsupported','mapping_conflicting','comparator_unsupported','projection_unavailable','scope_limit']);
const nodeFields=['id','label','kind','category','hidden_by_default','operation','operation_id','executor','inputs','outputs','token_kind','capacity','schema'];
const edgeFields=['id','source','target','kind','mode','weight','outcome','hidden_by_default','resource','direction','emit','forward_source'];
const coverage=['complete_in_declared_scope','partial','not_provided','not_requested'];
export async function contextKey(value){const bytes=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(value)));return [...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');}
function ref(value,source=null,type=null){object(value,['schema_version','source_id','ref']);text(value.source_id);if(source!==null&&source!==value.source_id)invalid();
    if(value.schema_version==='rpnh/collaboration/source_resource_ref/v1'){object(value.ref,['resource_id','resource_version_id']);if(type)invalid();for(const k of ['resource_id','resource_version_id'])if(!/^[a-z][a-z0-9_]*:[a-f0-9]{32}$/.test(value.ref[k]))invalid();}
    else{if(value.schema_version!=='rpnh/collaboration/source_version_ref/v1')invalid();object(value.ref,['entity_type','logical_id','version_id']);if(type!==null&&type!==value.ref.entity_type)invalid();if(!/^[a-z][a-z0-9_]*(?:\/[a-z][a-z0-9_]*)*\/v[1-9][0-9]*$/.test(value.ref.entity_type))invalid();for(const k of ['logical_id','version_id'])if(!/^[a-z][a-z0-9_]*:[a-f0-9]{32}$/.test(value.ref[k]))invalid();}return value;}
export function comparisonTarget(v){const fields={net_instance:['net_ref'],checkpoint:['net_ref','checkpoint_ref','checkpoint_commit_ordinal'],author_revision:['revision_ref']}[v?.kind];if(!fields)invalid();object(v,['kind','source_id',...fields]);text(v.source_id);for(const field of fields){if(field.endsWith('_ref')){ref(v[field],v.source_id,{net_ref:'net_instance/v1',checkpoint_ref:'marking_checkpoint/v1',revision_ref:null}[field]);if(field==='revision_ref'&&!['collaboration_net_revision/v1','collaboration_assembly_revision/v9'].includes(v[field].ref.entity_type))invalid();}else integer(v[field],1);}return v;}
function occurrence(v){for(const step of array(v)){object(step,['declaration_ref','member_id']);ref(step.declaration_ref);text(step.member_id);}}
function scope(v){if(v?.kind==='full_net')object(v,['kind']);else if(v?.kind==='nodes'){object(v,['kind','node_ids']);if(!unique(v.node_ids).length)invalid();v.node_ids.forEach(text);}else if(['module','subnet'].includes(v?.kind)){object(v,['kind','selector']);const field=v.kind==='module'?'element_id':'subnet_id';object(v.selector,['declaration_ref','occurrence_path',field]);ref(v.selector.declaration_ref);occurrence(v.selector.occurrence_path);text(v.selector[field]);}else invalid();}
function cut(v){object(v,['source_id','cut_id','head','reader_contract_version']);text(v.source_id);text(v.cut_id);text(v.reader_contract_version);object(v.head,['ordinal','writer_fencing_epoch']);integer(v.head.ordinal);integer(v.head.writer_fencing_epoch);}
function endpoint(v){object(v,['side','target_key','subject_kind','subject_id','occurrence_path']);if(!['left','right'].includes(v.side)||!['node','edge','boundary','author_element'].includes(v.subject_kind))invalid();text(v.target_key);text(v.subject_id);occurrence(v.occurrence_path);}
function fact(v){if(v?.state==='provided'){object(v,['state','value','evidence_refs']);for(const r of unique(v.evidence_refs))ref(r);}else if(v?.state==='unknown'){object(v,['state','reason_code']);if(!reasons.has(v.reason_code))invalid();}else if(v?.state==='absent_in_complete_scope'){object(v,['state','absence_evidence_refs']);if(!unique(v.absence_evidence_refs).length)invalid();v.absence_evidence_refs.forEach(r=>ref(r));}else invalid();}
export function comparisonContextPath(request){return '/api/v2/comparison-context?'+new URLSearchParams({request:JSON.stringify(request)});}

/** Validate closed shapes, exact cut/target/scope and recomputable field facts. */
export async function normalizeComparisonContext(value,request){
    object(value,['schema_version','client_request_id','context_id','purpose','request_echo','global_atomic_snapshot','publication','sources','left','right','mapping','presentation','axes','comparability','authority']);
    if(value.schema_version!==CONTEXT_SCHEMA||value.client_request_id!==request.client_request_id||value.purpose!=='descriptive_read_only'||value.global_atomic_snapshot!==false||value.publication!=='complete_pair'||value.comparability!=='not_established'||value.authority!=='display_only')invalid();text(value.context_id);
    const echo=object(value.request_echo,['left','right','source_cuts','scope','axes','view_preference','visual_pairs']);
    for(const k of ['left','right','scope','axes','view_preference','visual_pairs'])if(!same(echo[k],request[k]))invalid();
    if(request.source_cuts!==null&&!same(echo.source_cuts,request.source_cuts))invalid();
    const sources=new Map();
    for(const observed of array(value.sources)){object(observed,['observation_key','source_state','final_recheck']);const state=object(observed.source_state,['source_id','source_ref','cut','access_revision','access_state','coverage']);
        if(observed.final_recheck!=='passed'||sources.has(state.source_id)||state.source_id!==state.cut?.source_id||!same(state.cut,echo.source_cuts[state.source_id]))invalid();ref(state.source_ref,state.source_id,'task/v1');cut(state.cut);text(state.access_revision);text(state.access_state);object(state.coverage,['state','loaded_count','total_count']);text(state.coverage.state);for(const k of ['loaded_count','total_count'])if(state.coverage[k]!==null)integer(state.coverage[k]);if(observed.observation_key!==await contextKey(state))invalid();sources.set(state.source_id,observed);}
    if(!same([...sources.keys()].sort(),Object.keys(echo.source_cuts).sort()))invalid();
    const subjects={}, bySubject={};
    for(const side of ['left','right']){const s=object(value[side],['selected_target','source_observation_key','scope_resolution','graph','public_definition_provenance','axis_input_coverage','hierarchy_navigation']);comparisonTarget(s.selected_target);
        if(!same(s.selected_target,request[side])||s.source_observation_key!==sources.get(request[side].source_id)?.observation_key)invalid();
        const g=object(s.graph,['schema_version','target_key','scope_key','nodes','edges','boundary_edges','topology_coverage']);
        if(g.schema_version!=='rpnh/comparison_pn/v1'||g.target_key!==await contextKey(request[side])||g.scope_key!==await contextKey({target:request[side],scope:request.scope[side]})||g.topology_coverage!=='complete_in_declared_scope')invalid();
        const nodeIds=new Set(),edgeIds=new Set();bySubject[side]=new Map();subjects[side]=new Set();
        for(const n of array(g.nodes)){object(n,nodeFields.slice(0,5),nodeFields.slice(5));text(n.id);text(n.label);if(nodeIds.has(n.id)||!['transition','place'].includes(n.kind)||!['execution','place','resource'].includes(n.category)||typeof n.hidden_by_default!=='boolean')invalid();nodeIds.add(n.id);for(const k of nodeFields.slice(5))if(Object.hasOwn(n,k)){if(['inputs','outputs'].includes(k))unique(n[k]).forEach(text);else if(k==='capacity'){if(n[k]!==null)integer(n[k]);}else if(n[k]!==null&&typeof n[k]!=='string')invalid();}subjects[side].add('node:'+n.id);bySubject[side].set('node:'+n.id,n);}
        for(const e of array(g.edges)){object(e,edgeFields.slice(0,8),edgeFields.slice(8));text(e.id);text(e.kind);text(e.mode);integer(e.weight,1);if(edgeIds.has(e.id)||!nodeIds.has(e.source)||!nodeIds.has(e.target)||typeof e.hidden_by_default!=='boolean'||(e.outcome!==null&&typeof e.outcome!=='string')||(Object.hasOwn(e,'resource')&&typeof e.resource!=='boolean'))invalid();edgeIds.add(e.id);for(const k of ['direction','emit','forward_source'])if(Object.hasOwn(e,k)&&e[k]!==null)text(e[k]);subjects[side].add('edge:'+e.id);bySubject[side].set('edge:'+e.id,e);}
        const r=object(s.scope_resolution,['scope_key','requested_scope','resolved_scope','member_node_ids','member_edge_ids','boundary_edges','membership_evidence_refs','coverage']);
        if(r.scope_key!==g.scope_key||!same(r.requested_scope,request.scope[side])||!same(r.resolved_scope,request.scope[side])||!same(r.member_node_ids,[...nodeIds].sort())||!same(r.member_edge_ids,[...edgeIds].sort())||!same(r.boundary_edges,g.boundary_edges)||r.coverage!=='complete_in_declared_scope')invalid();r.membership_evidence_refs.forEach(v=>ref(v));
        if(request.scope[side].kind==='nodes'&&!same([...request.scope[side].node_ids].sort(),r.member_node_ids))invalid();
        const boundary=new Set();for(const b of array(g.boundary_edges)){object(b,['edge_id','inside_node_id','outside_endpoint','direction']);if(boundary.has(b.edge_id)||edgeIds.has(b.edge_id)||!nodeIds.has(b.inside_node_id)||!['incoming','outgoing'].includes(b.direction))invalid();text(b.edge_id);boundary.add(b.edge_id);const out=b.outside_endpoint;if(out.state==='provided'){object(out,['state','node_id']);text(out.node_id);if(nodeIds.has(out.node_id))invalid();}else{object(out,['state','reason_code']);if(out.state!=='unknown'||!reasons.has(out.reason_code))invalid();}}
        if(request.scope[side].kind==='full_net'&&boundary.size)invalid();
        const nav=object(s.hierarchy_navigation,['coverage','scopes']);if(!['provided','not_provided'].includes(nav.coverage)||nav.coverage==='not_provided'&&nav.scopes.length)invalid();const scopeKeys=new Set();for(const h of array(nav.scopes)){object(h,['scope','parent_scope','label','node_ids','edge_ids','evidence_refs']);scope(h.scope);if(h.parent_scope!==null)scope(h.parent_scope);text(h.label);unique(h.node_ids).forEach(text);unique(h.edge_ids).forEach(text);h.evidence_refs.forEach(v=>ref(v));if(scopeKeys.has(stable(h.scope)))invalid();scopeKeys.add(stable(h.scope));}
        const p=object(s.public_definition_provenance,['descriptor_refs','mapping_refs','status']);if(!['verified','partial','not_provided'].includes(p.status))invalid();for(const k of ['descriptor_refs','mapping_refs'])unique(p[k]).forEach(v=>ref(v));object(s.axis_input_coverage,AXES);for(const c of Object.values(s.axis_input_coverage)){object(c,['coverage','reason_codes']);if(!coverage.includes(c.coverage)||unique(c.reason_codes).some(r=>!reasons.has(r)))invalid();}
    }
    const mapping=object(value.mapping,['contract_version','coverage','relations','explicit_presence_changes','unmapped','reason_codes']);if(mapping.contract_version!=='rpnh/comparison_mapping/v1'||!['complete_in_declared_scope','partial','none'].includes(mapping.coverage)||unique(mapping.reason_codes).some(r=>!reasons.has(r)))invalid();
    const relations=new Map(),covered={left:new Set(),right:new Set()};
    const sameDefinition=same(request.left.kind==='author_revision'?request.left.revision_ref:request.left.net_ref,request.right.kind==='author_revision'?request.right.revision_ref:request.right.net_ref);
    for(const r of array(mapping.relations)){object(r,['relation_id','relation_kind','left','right','validation','semantic_claim','evidence','coverage','reason_code','author_direction']);text(r.relation_id);if(relations.has(r.relation_id)||!['same_exact_subject','retained_author_element','copied_from','split','fusion','many_to_many'].includes(r.relation_kind)||!['verified','unverified','unsupported','conflicting'].includes(r.validation)||!['identity','author_correspondence'].includes(r.semantic_claim)||!['complete_relation','incomplete_relation'].includes(r.coverage)||!reasons.has(r.reason_code)||![null,'left_to_right','right_to_left'].includes(r.author_direction))invalid();if(r.relation_kind==='many_to_many'&&r.validation==='verified'||r.semantic_claim==='identity'&&(!sameDefinition||r.relation_kind!=='same_exact_subject'||r.left.length!==1||r.right.length!==1||['subject_kind','subject_id','occurrence_path'].some(k=>!same(r.left[0][k],r.right[0][k]))))invalid();relations.set(r.relation_id,r);
        for(const side of ['left','right']){if(!unique(r[side]).length)invalid();for(const ep of r[side]){endpoint(ep);if(ep.side!==side||ep.target_key!==value[side].graph.target_key||!subjects[side].has(ep.subject_kind+':'+ep.subject_id))invalid();if(ep.occurrence_path.length&&!value[side].hierarchy_navigation.scopes.some(h=>same(h.scope.selector?.occurrence_path,ep.occurrence_path)&&h[ep.subject_kind==='node'?'node_ids':'edge_ids'].includes(ep.subject_id)))invalid();if(r.validation==='verified')covered[side].add(ep.subject_kind+':'+ep.subject_id);}}
        if(!r.evidence.length)invalid();for(const e of array(r.evidence)){object(e,['source_id','evidence_ref','evidence_kind','selected_endpoints','source_cut','access_revision','verification_contract','validation']);ref(e.evidence_ref,e.source_id);const native=e.verification_contract==='rpnh/native_capability_derivation/v1';if(native&&(r.semantic_claim!=='author_correspondence'||!['copied_from','retained_author_element'].includes(r.relation_kind)||r.left.length!==1||r.right.length!==1||!['node','edge'].includes(r.left[0].subject_kind)||r.left[0].subject_kind!==r.right[0].subject_kind))invalid();const s=sources.get(e.source_id)?.source_state;if(!s||!same(e.source_cut,s.cut)||e.access_revision!==s.access_revision||e.evidence_kind!=='exact_public_projection'||!['rpnh/public_pn_projection/v1','rpnh/native_capability_derivation/v1'].includes(e.verification_contract)||e.validation!=='verified'||!same(e.selected_endpoints,[...r.left,...r.right]))invalid();}}
    const presenceIds=new Set();for(const change of array(mapping.explicit_presence_changes)){object(change,['change_id','subject','state','reason_code','absence_evidence_refs','validation']);const ep=change.subject;endpoint(ep);const side=ep.side,k=ep.subject_kind+':'+ep.subject_id;if(presenceIds.has(change.change_id)||ep.target_key!==value[side].graph.target_key||!subjects[side].has(k)||covered[side].has(k)||change.state!==(side==='left'?'present_left_only':'present_right_only')||change.reason_code!=='explicit_presence'||change.validation!=='verified'||!unique(change.absence_evidence_refs).length)invalid();text(change.change_id);for(const r of change.absence_evidence_refs){ref(r);if(!sources.has(r.source_id))invalid();}presenceIds.add(change.change_id);covered[side].add(k);}
    object(mapping.unmapped,['left','right']);for(const side of ['left','right']){const u=object(mapping.unmapped[side],['scope_key','subjects','coverage']);if(u.scope_key!==value[side].graph.scope_key||u.coverage!=='complete_in_declared_scope')invalid();for(const ep of unique(u.subjects)){endpoint(ep);if(ep.side!==side||ep.target_key!==value[side].graph.target_key)invalid();}if(!same(u.subjects.map(ep=>ep.subject_kind+':'+ep.subject_id).sort(),[...subjects[side]].filter(k=>!covered[side].has(k)).sort()))invalid();}
    const any=[...relations.values()].some(r=>r.validation==='verified'), complete=['left','right'].every(s=>covered[s].size===subjects[s].size)&&(any||presenceIds.size>0||sameDefinition)&&![...relations.values()].some(r=>r.validation==='conflicting');
    const expectedCoverage=complete?'complete_in_declared_scope':any||presenceIds.size?'partial':'none', recommended=complete?'reliable_diff':any||presenceIds.size?'partial_mapping':'full_pair';if(mapping.coverage!==expectedCoverage)invalid();
    const presentation=object(value.presentation,['recommended_mode','display_mode','full_pair_navigation']);if(presentation.recommended_mode!==recommended||presentation.display_mode!==(request.view_preference==='full_pair'?'full_pair':recommended))invalid();const nav=object(presentation.full_pair_navigation,['availability','reason_code','targets','source_cuts','scope','view_preference']);if(!['available','requires_read','unavailable'].includes(nav.availability)||!reasons.has(nav.reason_code)||!same(nav.targets,{left:request.left,right:request.right})||!same(nav.source_cuts,echo.source_cuts)||!same(nav.scope,{kind:'full_pair',left:{kind:'full_net'},right:{kind:'full_net'}})||nav.view_preference!=='full_pair')invalid();
    object(value.axes,AXES);const pairScope=await contextKey({left:value.left.graph.scope_key,right:value.right.graph.scope_key});
    for(const axis of AXES){const a=object(value.axes[axis],['axis','coverage','scope_key','rows','counts','unknown_reasons']);if(a.axis!==axis||a.scope_key!==pairScope||!coverage.includes(a.coverage)||unique(a.unknown_reasons).some(r=>!reasons.has(r)))invalid();if(!request.axes.includes(axis)&&(a.coverage!=='not_requested'||a.rows.length))invalid();const ids=new Set(),counts={known_changed:0,known_same:0,unknown:0};
        for(const row of array(a.rows)){object(row,['row_id','subject_relation_id','subject_scope','field_path','comparator_contract','left_fact','right_fact','classification','reason_code','evidence_refs','interpretation']);text(row.row_id);text(row.field_path);if(ids.has(row.row_id)||row.subject_scope!==pairScope||row.interpretation!=='current_analysis'||row.comparator_contract!=='rpnh/json_equal/v1'||!Object.hasOwn(counts,row.classification)||!reasons.has(row.reason_code)||row.subject_relation_id!==null&&!relations.has(row.subject_relation_id))invalid();ids.add(row.row_id);fact(row.left_fact);fact(row.right_fact);unique(row.evidence_refs).forEach(r=>ref(r));
            const l=row.left_fact,r=row.right_fact;if(row.classification!=='unknown'){if(l.state==='provided'&&r.state==='provided'){if(row.classification!==(same(l.value,r.value)?'known_same':'known_changed'))invalid();}else if(new Set([l.state,r.state]).size!==2||![l.state,r.state].includes('provided')||![l.state,r.state].includes('absent_in_complete_scope')||row.classification!=='known_changed')invalid();}
            // Public single-subject definition values must come from this exact
            // graph. Endpoint correspondence is verified separately below.
            if(axis==='definition'&&row.subject_relation_id!==null&&row.field_path!=='definition.group'){const rel=relations.get(row.subject_relation_id),field=row.field_path.slice('definition.'.length);if(rel.left.length!==1||rel.right.length!==1)invalid();for(const side of ['left','right']){const ep=rel[side][0],source=bySubject[side].get(ep.subject_kind+':'+ep.subject_id),f=row[side+'_fact'];if(['source','target'].includes(field)&&rel.semantic_claim!=='identity'){if(f.state==='provided'&&f.value!=='verified_corresponding_endpoint')invalid();}else if(Object.hasOwn(source,field)){if(f.state!=='provided'||!same(f.value,source[field]))invalid();}else if(f.state!=='unknown')invalid();}}
            counts[row.classification]++;}
        const visible=!['not_provided','not_requested'].includes(a.coverage),expected={scope_key:pairScope,unit:'field_rows',loaded_count:visible?a.rows.length:null,total_count:a.coverage==='complete_in_declared_scope'?a.rows.length:null,known_changed:visible?counts.known_changed:null,known_same:visible?counts.known_same:null,unknown:visible?counts.unknown:null,coverage:a.coverage==='not_requested'?'not_provided':a.coverage};if(!same(a.counts,expected))invalid();}
    if(request.axes.includes('definition')){
        const expected=new Map(),key=(relation,path)=>stable([relation,path]);
        for(const relation of relations.values()){
            const applicable=relation.validation==='verified'&&relation.left.length===1&&relation.right.length===1;
            if(!applicable){expected.set(key(relation.relation_id,'definition.group'),null);continue;}
            const l=bySubject.left.get(relation.left[0].subject_kind+':'+relation.left[0].subject_id),r=bySubject.right.get(relation.right[0].subject_kind+':'+relation.right[0].subject_id);
            for(const field of new Set([...Object.keys(l),...Object.keys(r)])){if(field==='id')continue;
                const facts={left:Object.hasOwn(l,field)?{state:'provided',value:l[field]}:{state:'unknown'},right:Object.hasOwn(r,field)?{state:'provided',value:r[field]}:{state:'unknown'}};
                if(['source','target'].includes(field)&&relation.semantic_claim!=='identity'){
                    const linked=[...relations.values()].some(x=>x.validation==='verified'&&x.left.length===1&&x.right.length===1&&x.left[0].subject_kind==='node'&&x.right[0].subject_kind==='node'&&x.left[0].subject_id===l[field]&&x.right[0].subject_id===r[field]);
                    for(const side of ['left','right'])facts[side]=linked?{state:'provided',value:'verified_corresponding_endpoint'}:{state:'unknown'};
                }
                expected.set(key(relation.relation_id,'definition.'+field),facts);
            }
        }
        for(const change of mapping.explicit_presence_changes){const ep=change.subject,side=ep.side,item=bySubject[side].get(ep.subject_kind+':'+ep.subject_id);for(const [field,v] of Object.entries(item)){if(field==='id')continue;const facts={left:{state:'absent_in_complete_scope'},right:{state:'absent_in_complete_scope'}};facts[side]={state:'provided',value:v};expected.set(key(null,'definition.presence.'+change.change_id+'.'+field),facts);}}
        for(const side of ['left','right'])for(const ep of mapping.unmapped[side].subjects){const item=bySubject[side].get(ep.subject_kind+':'+ep.subject_id);for(const [field,v] of Object.entries(item)){if(field==='id')continue;const facts={left:{state:'unknown'},right:{state:'unknown'}};facts[side]={state:'provided',value:v};expected.set(key(null,'definition.unmapped.'+side+'.'+ep.subject_kind+'.'+ep.subject_id+'.'+field),facts);}}
        if(expected.size!==value.axes.definition.rows.length)invalid();
        for(const row of value.axes.definition.rows){const k=key(row.subject_relation_id,row.field_path);if(!expected.has(k))invalid();const facts=expected.get(k);if(facts){for(const side of ['left','right']){const actual=row[side+'_fact'];if(facts[side].state!==actual.state||actual.state==='provided'&&!same(facts[side].value,actual.value))invalid();}}else if(row.classification!=='unknown')invalid();expected.delete(k);}
        if(expected.size)invalid();
    }
    return structuredClone(value);
}

export class ComparisonContextState {
    constructor(){this.generation=0;this.open=false;this.loading=false;this.value=null;this.error=null;this.request=null;this.ticket=null;this.selection={left:null,right:null};this.scope={kind:'full_pair',left:{kind:'full_net'},right:{kind:'full_net'}};this.axes=[...AXES];this.visualPairs=[];this.cuts=null;this.session=null;this.options=[];this.sources=[];this.nextCursor=null;this.limits=null;this.indexLoaded=false;}
    clear(){++this.generation;this.value=null;this.error=null;this.loading=false;this.ticket=null;}
    close(){this.clear();this.open=false;this.request=null;this.options=[];this.sources=[];this.cuts=null;this.session=null;this.selection={left:null,right:null};this.visualPairs=[];this.indexLoaded=false;this.nextCursor=null;this.limits=null;this.source_left=null;this.source_right=null;}
    change(patch){this.clear();Object.assign(this,patch);}
    begin(){if(!this.open||this.loading||!this.selection.left||!this.selection.right||!this.session)return null;this.clear();this.loading=true;const request={schema_version:'rpnh/comparison_request/v1',session_id:this.session,client_request_id:'comparison-'+this.generation,left:structuredClone(this.selection.left),right:structuredClone(this.selection.right),source_cuts:structuredClone(this.cuts),scope:structuredClone(this.scope),axes:[...this.axes],view_preference:this.preference??'auto',visual_pairs:structuredClone(this.visualPairs),limits:structuredClone(this.limits)};this.request=request;this.ticket={generation:this.generation,request:structuredClone(request)};return this.ticket;}
    accepts(ticket){return this.open&&ticket?.generation===this.generation&&same(ticket.request,this.request);}
    async accept(ticket,raw){if(!this.accepts(ticket))return false;const value=await normalizeComparisonContext(raw,ticket.request);if(!this.accepts(ticket))return false;this.value=value;this.cuts=structuredClone(value.request_echo.source_cuts);this.loading=false;this.error=null;return true;}
    fail(ticket,error){if(!this.accepts(ticket))return false;this.clear();this.error=errorCode(error);return true;}
}

/** Independent selectors plus two real NetRenderer/ELK panels. */
export function installComparisonContextPanel(container,{request,translate=t=>t,events=globalThis.window,onClose=()=>{},onOpen=()=>{}}={}){
    const state=new ComparisonContextState();let controller=null,indexController=null,joint=null,elk=null,renderers=[],renderEpoch=0,indexGeneration=0,indexLoading=false,activeIndexCursor=null,selected={left:null,right:null};
    const make=(tag,text)=>{const el=container.ownerDocument.createElement(tag);if(text!==undefined)el.textContent=String(text);return el;};
    const button=(text,fn)=>{const b=make('button',translate(text));b.onclick=fn;return b;};
    function clearRenderers(){++renderEpoch;for(const renderer of renderers)renderer.dispose();renderers=[];selected={left:null,right:null};}
    function invalidate(patch){controller?.abort();controller=null;indexController?.abort();++indexGeneration;indexLoading=false;clearRenderers();state.change(patch);draw();}
    function close(resume=false){const wasOpen=state.open;controller?.abort();indexController?.abort();controller=null;indexController=null;++indexGeneration;indexLoading=false;activeIndexCursor=null;clearRenderers();state.close();draw();if(resume&&wasOpen)onClose();}
    function choose(side,target){const selection={...state.selection,[side]:target};invalidate({selection,scope:{kind:'full_pair',left:{kind:'full_net'},right:{kind:'full_net'}},visualPairs:[],preference:'auto'});}
    async function readIndex(more=false){const cursor=more?state.nextCursor:null;if(indexLoading&&activeIndexCursor===cursor)return;indexController?.abort();indexController=new AbortController();const generation=state.generation,indexTicket=++indexGeneration;indexLoading=true;activeIndexCursor=cursor;
        try{const raw=await request('/api/v2/comparison-selection'+(cursor?'?'+new URLSearchParams({cursor}):''),indexController.signal);if(!state.open||generation!==state.generation||indexTicket!==indexGeneration)return;
            object(raw,['schema_version','session_id','targets','source_cuts','next_cursor','limits'],['sources']);const sources=raw.sources??[];const sourceIds=new Set();for(const source of array(sources)){text(source.source_id);if(sourceIds.has(source.source_id))invalid();sourceIds.add(source.source_id);text(source.access_state);object(source.coverage,['state','loaded_count','total_count']);if(source.coverage.loaded_count!==null||source.coverage.total_count!==null)invalid();if(source.access_state==='readable'){object(source,['source_id','source_ref','cut','access_revision','access_state','coverage','capabilities','access_path']);if(source.coverage.state!=='not_queried')invalid();object(source.capabilities,['index','index_fields','record','record_fields','projection','material','export','execution']);if(source.capabilities.execution!=='not_checked')invalid();}else{object(source,['source_id','source_ref','cut','access_revision','access_state','coverage']);if(source.coverage.state!=='unavailable')invalid();}}if(raw.schema_version!=='rpnh/comparison_selection/v1')invalid();text(raw.session_id);for(const item of array(raw.targets)){object(item,['target','label','read_state']);comparisonTarget(item.target);text(item.label);if(item.read_state!=='not_read')invalid();}for(const [sid,c] of Object.entries(raw.source_cuts)){cut(c);if(c.source_id!==sid)invalid();}
            if(more&&(state.session!==raw.session_id||!same(state.cuts,raw.source_cuts)||!same(state.sources,sources)))invalid();
            const options=more?[...state.options,...raw.targets]:raw.targets;if(new Set(options.map(v=>stable(v.target))).size!==options.length)invalid();state.options=options;state.sources=structuredClone(sources);state.session=raw.session_id;state.cuts=raw.source_cuts;state.limits=raw.limits;state.nextCursor=raw.next_cursor;state.indexLoaded=true;state.error=null;indexLoading=false;draw();
        }catch(error){if(error.name!=='AbortError'&&state.open&&generation===state.generation&&indexTicket===indexGeneration){indexLoading=false;clearRenderers();state.clear();state.error=errorCode(error);if(state.error==='access_changed'){state.options=[];state.sources=[];state.nextCursor=null;state.cuts=null;state.session=null;state.limits=null;state.indexLoaded=false;state.selection={left:null,right:null};state.source_left=null;state.source_right=null;}draw();}}
        finally{if(indexTicket===indexGeneration&&indexLoading){indexLoading=false;activeIndexCursor=null;if(state.open)draw();}}
    }
    async function load(){const ticket=state.begin();if(!ticket)return;indexController?.abort();indexController=null;++indexGeneration;indexLoading=false;activeIndexCursor=null;controller?.abort();controller=new AbortController();clearRenderers();draw();
        try{const raw=await request(comparisonContextPath(ticket.request),controller.signal);if(await state.accept(ticket,raw))draw();}
        catch(error){if(state.accepts(ticket)){state.fail(ticket,error);clearRenderers();if(state.error==='access_changed'){state.sources=[];state.options=[];state.cuts=null;state.session=null;state.limits=null;state.nextCursor=null;state.indexLoaded=false;state.selection={left:null,right:null};state.source_left=null;state.source_right=null;}draw();}}
    }
    function changeScope(side,next){const scopeValue={kind:'selected_pair',left:structuredClone(state.scope.left),right:structuredClone(state.scope.right),[side]:structuredClone(next)};
        if(scopeValue.left.kind==='full_net'&&scopeValue.right.kind==='full_net')scopeValue.kind='full_pair';invalidate({scope:scopeValue,visualPairs:[]});load();}
    function fullPair(){if(!state.value)return;const nav=state.value.presentation.full_pair_navigation;if(nav.availability==='unavailable')return;
        if(nav.availability==='available'){state.preference='full_pair';draw();return;}
        invalidate({scope:structuredClone(nav.scope),cuts:structuredClone(nav.source_cuts),preference:'full_pair',visualPairs:[]});load();}
    function select(side,kind,id){if(!state.value)return;selected[side]={kind,id};const index=side==='left'?0:1;renderers[index]?.decorate({showResources:true,selection:selected[side]});const details=container.querySelector(`[data-comparison-details="${side}"]`);if(details){const graph=state.value[side].graph;const item=graph[kind==='node'?'nodes':'edges'].find(n=>n.id===id);details.textContent=item?JSON.stringify(item,null,2):'';}updateSelectionButtons();}
    function updateSelectionButtons(){const pair=container.querySelector('[data-visual-pair]');if(pair)pair.disabled=!selected.left||!selected.right||selected.left.kind==='group'||selected.right.kind==='group';for(const side of ['left','right']){const b=container.querySelector(`[data-node-scope="${side}"]`);if(b)b.disabled=selected[side]?.kind!=='node';}}
    function addVisualPair(){if(!state.value||!selected.left||!selected.right||selected.left.kind==='group'||selected.right.kind==='group')return;const pair={pair_id:'visual-'+(state.visualPairs.length+1)};for(const side of ['left','right'])pair[side]=[{side,target_key:state.value[side].graph.target_key,subject_kind:selected[side].kind,subject_id:selected[side].id,occurrence_path:[]}];invalidate({visualPairs:[...state.visualPairs,pair]});load();}
    // A relation is a group of explicit DTO endpoints, never a guessed pair.
    // Reuse the existing renderer's graph/layout and viewport restore surface;
    // neither navigation nor field-panel updates recreate a renderer.
    function showRelation(relation){
        if(!state.value)return;
        for(const side of ['left','right']){
            const endpoints=relation[side],renderer=renderers[side==='left'?0:1];
            selected[side]=endpoints.length===1?{kind:endpoints[0].subject_kind,id:endpoints[0].subject_id}:{kind:'group',endpoints};
            if(renderer){
                renderer.decorate({showResources:true,selection:endpoints.length===1?selected[side]:null});
                const points=[];
                for(const ep of endpoints){
                    const isNode=ep.subject_kind==='node';
                    const cell=renderer.graph.getCell(isNode?nodeKey(ep.subject_id):edgeKey(ep.subject_id));
                    cell?.attr(isNode?'body/strokeWidth':'line/strokeWidth',3.5);
                    if(isNode)cell?.attr('root/aria-pressed','true');
                    const position=(isNode?renderer.layout.nodes:renderer.layout.edges).get(ep.subject_id);
                    if(isNode&&position)points.push({x:position.x,y:position.y},{x:position.x+position.width,y:position.y+position.height});
                    else for(const section of position?.sections??[])points.push(section.startPoint,...(section.bendPoints??[]),section.endPoint);
                }
                if(points.length){
                    const bounds=points.reduce((b,p)=>({left:Math.min(b.left,p.x),top:Math.min(b.top,p.y),right:Math.max(b.right,p.x),bottom:Math.max(b.bottom,p.y)}),{left:Infinity,top:Infinity,right:-Infinity,bottom:-Infinity});
                    const x=bounds.left,y=bounds.top,width=bounds.right-x,height=bounds.bottom-y;
                    const w=renderer.element.clientWidth,h=renderer.element.clientHeight;
                    if(w>0&&h>0){const scale=Math.min(1.3,.92*Math.min(w/Math.max(1,width),h/Math.max(1,height)));renderer.restore({scale,tx:w/2-(x+width/2)*scale,ty:h/2-(y+height/2)*scale});}
                }
            }
            const details=container.querySelector(`[data-comparison-details="${side}"]`);
            if(details)details.textContent=JSON.stringify(endpoints.map(ep=>({endpoint:ep,subject:state.value[side].graph[ep.subject_kind==='node'?'nodes':'edges'].find(item=>item.id===ep.subject_id)})),null,2);
        }
        updateSelectionButtons();
        const details=container.querySelector('[data-mapping-details]');
        if(details){
            details.replaceChildren(make('pre',JSON.stringify(relation,null,2)));
            for(const side of ['left','right'])for(const ep of relation[side]){
                const b=make('button',`${side} · ${ep.subject_kind} · ${ep.subject_id}`);
                b.dataset.relationEndpoint=side;
                b.onclick=()=>{select(side,ep.subject_kind,ep.subject_id);renderers[side==='left'?0:1]?.center(ep.subject_kind,ep.subject_id);};
                details.append(b);
            }
            details.parentElement.open=true;
        }
    }
    function fieldSection(axis,value){
        const a=value.axes[axis],section=make('details');section.className='comparison-axis';section.dataset.comparisonAxis=axis;
        section.append(make('summary',translate({definition:'定义',configuration:'配置',materials:'材料',runtime:'运行事实'}[axis])+' · '+a.coverage));
        const counts=make('p',a.counts.loaded_count===null?translate('未提供，保持未知'):translate('已计算字段 {0} 项：不同 {1}，相同 {2}，未知 {3}',a.counts.loaded_count,a.counts.known_changed,a.counts.known_same,a.counts.unknown));
        counts.dataset.scopeCounts=axis;section.append(counts);
        if(axis==='runtime')section.append(make('p',translate('运行覆盖仅含所选检查点 marking 与 token 引用；firing、活动和完成证据未知。')));
        const filter=make('select');filter.dataset.fieldFilter=axis;filter.setAttribute('aria-label',['字段不同','已提供字段相同','未知'].map(label=>translate(label)).join(' / '));
        for(const classification of ['all','known_changed','known_same','unknown']){const option=make('option',classification);option.value=classification;filter.append(option);}filter.value='all';
        const rows=make('div');rows.dataset.fieldRows=axis;
        const shown=make('p');shown.setAttribute('aria-live','polite');shown.dataset.fieldCount=axis;
        let limit=100;
        const rowElements=new Map(),relations=new Map(value.mapping.relations.map(r=>[r.relation_id,r]));
        function rowElement(row){
            if(rowElements.has(row.row_id))return rowElements.get(row.row_id);
            const r=make('details');r.dataset.fieldRow=row.row_id;
            r.append(make('summary',row.classification+' · '+row.field_path),make('pre',JSON.stringify({left:row.left_fact,right:row.right_fact,reason_code:row.reason_code,evidence_refs:row.evidence_refs},null,2)));
            const relation=relations.get(row.subject_relation_id);
            if(relation){const b=button('定位',()=>showRelation(relation));b.append(make('span',` · ${relation.relation_kind} · ${relation.left.length} ↔ ${relation.right.length}`));b.dataset.fieldRelation=relation.relation_id;r.append(b);}
            rowElements.set(row.row_id,r);return r;
        }
        const more=button('显示更多字段',()=>{limit+=100;update();if(more.hidden)filter.focus?.();});
        more.dataset.moreFields=axis;
        function update(){
            const matching=a.rows.filter(row=>filter.value==='all'||row.classification===filter.value),visible=matching.slice(0,limit);
            rows.replaceChildren(...visible.map(rowElement));
            shown.textContent=`${visible.length} / ${matching.length}`;
            more.hidden=visible.length===matching.length;
        }
        filter.onchange=()=>{limit=100;update();};
        section.append(filter,shown,rows,more);update();return section;
    }
    function draw(){clearRenderers();container.hidden=!state.open;if(!state.open){container.replaceChildren();return;}
        const title=make('h2',translate('比较独立网'));title.id='comparison-context-title';const heading=make('div');heading.className='comparison-heading';heading.append(title,button('关闭比较',()=>close(true)));const children=[heading,make('p',translate('选择两侧 exact 对象；同名不建立身份。四轴分别保留未知，跨来源没有全局原子快照。'))];
        const controls=make('div');controls.className='comparison-controls';
        for(const side of ['left','right']){const wrapper=make('div');wrapper.className='comparison-selector';const label=make('label',translate(side==='left'?'左侧对象':'右侧对象'));const sourceSelect=make('select');sourceSelect.setAttribute('aria-label',translate(side==='left'?'左侧来源':'右侧来源'));const allSources=[...new Set([...state.sources.map(x=>x.source_id),...state.options.map(x=>x.target.source_id)])];const placeholder=make('option',translate('选择来源'));placeholder.value='';sourceSelect.append(placeholder);for(const source of allSources){const description=state.sources.find(x=>x.source_id===source);const option=make('option',source+(description?' · '+description.access_state:''));option.value=source;sourceSelect.append(option);}sourceSelect.value=state.selection[side]?.source_id??state['source_'+side]??'';
            const targetSelect=make('select');targetSelect.setAttribute('aria-label',translate(side==='left'?'左侧对象':'右侧对象'));const empty=make('option',translate('选择精确对象'));empty.value='';targetSelect.append(empty);state.options.forEach((option,index)=>{if(option.target.source_id!==sourceSelect.value)return;const el=make('option',option.label);el.value=String(index);targetSelect.append(el);});targetSelect.value=state.selection[side]?String(state.options.findIndex(x=>same(x.target,state.selection[side]))):'';
            sourceSelect.onchange=()=>{state['source_'+side]=sourceSelect.value;choose(side,null);};targetSelect.onchange=()=>choose(side,targetSelect.value===''?null:structuredClone(state.options[Number(targetSelect.value)].target));label.append(sourceSelect,targetSelect);wrapper.append(label);const source=state.sources.find(x=>x.source_id===sourceSelect.value);if(source){const disclosure=make('details');disclosure.dataset.sourceDescription=side;disclosure.append(make('summary',translate('来源访问与读取范围')),make('pre',JSON.stringify({source_id:source.source_id,access_state:source.access_state,coverage:source.coverage,...(source.capabilities?{capabilities:source.capabilities}:{})},null,2)));wrapper.append(disclosure);}controls.append(wrapper);}
        const read=button('读取比较',load);read.disabled=state.loading||!state.session||!state.selection.left||!state.selection.right;controls.append(read,button('交换两侧',()=>{const selection={left:state.selection.right,right:state.selection.left},scopes={kind:state.scope.kind,left:state.scope.right,right:state.scope.left};invalidate({selection,scope:scopes,visualPairs:[]});}));
        controls.append(button('新观察',()=>{invalidate({selection:{left:null,right:null},options:[],sources:[],cuts:null,nextCursor:null,limits:null,indexLoaded:false,scope:{kind:'full_pair',left:{kind:'full_net'},right:{kind:'full_net'}},visualPairs:[]});readIndex();}));
        if(state.nextCursor){const more=button('更多精确对象',()=>readIndex(true));more.disabled=indexLoading;controls.append(more);}children.push(controls);
        const axes=make('div');axes.className='comparison-axes-select';for(const axis of AXES){const label=make('label',translate({definition:'定义',configuration:'配置',materials:'材料',runtime:'运行事实'}[axis])),input=make('input');input.type='checkbox';input.checked=state.axes.includes(axis);input.onchange=()=>invalidate({axes:AXES.filter(a=>a===axis?input.checked:state.axes.includes(a))});label.prepend(input);axes.append(label);}children.push(axes);
        if(state.loading)children.push(make('p',translate('正在读取完整比较…')));
        if(state.error)children.push(make('p',translate('比较不可用；旧结果已清除。')+' '+state.error));
        if(!state.options.length&&!state.error)children.push(make('p',translate(state.indexLoaded?'没有获授权的可比较对象':'正在读取获授权的对象索引…')));
        if(state.value){const value=state.value,mode=state.preference==='full_pair'?'full_pair':value.presentation.recommended_mode;
            children.push(make('p',translate({reliable_diff:'当前范围已核验对应',partial_mapping:'部分范围已核验对应',full_pair:'独立完整双网'}[mode])));
            const actions=make('div');actions.className='comparison-controls';actions.append(button('完整双网',fullPair));if(state.scope.kind==='full_pair')actions.append(button('按证据显示',()=>{state.preference='auto';draw();}));const visual=button('视觉配对所选对象',addVisualPair);visual.dataset.visualPair='';visual.disabled=true;actions.append(visual,make('span',translate('视觉配对不增加可靠性，也不写入 Registry。')));children.push(actions);
            const panels=make('div');panels.className='comparison-net-pair';
            for(const side of ['left','right']){const s=value[side],panel=make('section');panel.className='comparison-net-side';panel.append(make('h3',translate(side==='left'?'左侧对象':'右侧对象')),make('p',state.options.find(x=>same(x.target,s.selected_target))?.label??s.selected_target.source_id));const buttons=make('div');buttons.className='comparison-controls';
                const scopeSelect=make('select');scopeSelect.setAttribute('aria-label',translate('声明范围'));const options=[{scope:{kind:'full_net'},label:translate('完整网')},...s.hierarchy_navigation.scopes];for(let i=0;i<options.length;i++){const op=make('option',options[i].label);op.value=String(i);scopeSelect.append(op);}scopeSelect.value=String(options.findIndex(x=>same(x.scope,state.scope[side])));scopeSelect.onchange=()=>changeScope(side,options[Number(scopeSelect.value)].scope);buttons.append(scopeSelect);
                const current=s.hierarchy_navigation.scopes.find(x=>same(x.scope,state.scope[side]));const parent=button('父级范围',()=>changeScope(side,current?.parent_scope??{kind:'full_net'}));parent.disabled=state.scope[side].kind==='full_net';buttons.append(parent);const selectedScope=button('范围缩到所选节点',()=>{if(selected[side]?.kind==='node')changeScope(side,{kind:'nodes',node_ids:[selected[side].id]});});selectedScope.dataset.nodeScope=side;selectedScope.disabled=true;buttons.append(selectedScope);const index=side==='left'?0:1;buttons.append(button('完整视图',()=>renderers[index]?.fit()),button('放大',()=>renderers[index]?.zoom(1.25)),button('缩小',()=>renderers[index]?.zoom(.8)));panel.append(buttons);
                if(s.hierarchy_navigation.coverage==='not_provided')panel.append(make('p',translate('未提供模块层级')));
                panel.append(make('p',translate('当前范围：{0} 个节点，{1} 条内部弧，{2} 条边界连接',s.graph.nodes.length,s.graph.edges.length,s.graph.boundary_edges.length)));
                const wrap=make('div');wrap.className='comparison-canvas-wrap';const canvas=make('div');canvas.className='comparison-paper';canvas.dataset.comparisonPaper=side;canvas.tabIndex=0;canvas.setAttribute('aria-label',translate(side==='left'?'左侧 Petri 网':'右侧 Petri 网'));wrap.append(canvas);panel.append(wrap);
                const boundaries=make('details');boundaries.append(make('summary',translate('边界连接')),make('pre',JSON.stringify(s.graph.boundary_edges,null,2)));panel.append(boundaries);const details=make('pre');details.dataset.comparisonDetails=side;panel.append(details);panels.append(panel);}
            children.push(panels);const mapping=make('details');mapping.append(make('summary',translate('对应证据与视觉配对')));for(const relation of value.mapping.relations){const b=make('button',`${relation.relation_kind} · ${relation.left.length} ↔ ${relation.right.length} · ${relation.validation}${relation.author_direction?' · '+relation.author_direction:''}`);b.onclick=()=>showRelation(relation);mapping.append(b);}for(const pair of state.visualPairs){const b=make('button',translate('仅视觉')+' · '+pair.pair_id);b.onclick=()=>showRelation(pair);mapping.append(b);}const detail=make('div');detail.dataset.mappingDetails='';mapping.append(detail);children.push(mapping);
            for(const axis of AXES)children.push(fieldSection(axis,value));
            children.push(make('p',translate('不同不表示优劣、业务等价或可安全替换；未提供不等于零。')));
        }
        container.replaceChildren(...children);if(state.value)renderPair(state.value);
    }
    async function renderPair(value){if(!joint||!elk)return;const generation=state.generation,epoch=++renderEpoch,panels=['left','right'].map(side=>container.querySelector(`[data-comparison-paper="${side}"]`));if(panels.some(x=>!x))return;for(const p of panels)p.parentElement.style.visibility='hidden';
        try{const graphs=['left','right'].map(side=>({...value[side].graph,view_mode:'petri',source:{mode:'initial_configured'}}));const caches=[new LayoutCache(elk),new LayoutCache(elk)];const positions=await Promise.all(graphs.map((graph,i)=>caches[i].get(graph)));
            if(!state.open||state.generation!==generation||state.value!==value||epoch!==renderEpoch)return;
            const next=panels.map((panel,index)=>new NetRenderer(joint,panel,(kind,id)=>select(index===0?'left':'right',kind,id)));renderers=next;
            next.forEach((renderer,index)=>{renderer.apply(graphs[index],positions[index]);renderer.decorate({showResources:true});});
            for(const p of panels)p.parentElement.style.visibility='visible';next.forEach(renderer=>renderer.fit());
        }catch(error){if(state.open&&state.generation===generation&&epoch===renderEpoch){clearRenderers();state.clear();state.error='read_failed';draw();}}
    }
    events?.addEventListener?.('popstate',()=>close(true));events?.addEventListener?.('pagehide',()=>close());events?.addEventListener?.('pageshow',event=>{if(event.persisted||state.value||state.loading)close(true);});
    return {state,open(){close();state.open=true;state.scope={kind:'full_pair',left:{kind:'full_net'},right:{kind:'full_net'}};state.preference='auto';onOpen();draw();readIndex();},close,refreshLanguage:draw,setRendering(deps){joint=deps.joint;elk=deps.elk;if(state.value)draw();}};
}
