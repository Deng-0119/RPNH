/** Explicit read observations of the original literal producer's carriers.
 * A catalog candidate is not a proof or grant. Neither inspection mode writes
 * Registry facts, returns a body, changes availability or authorizes cleanup.
 */
export const CARRIERS_SCHEMA='rpnh/viewer_material_carriers/v1';
const INSPECTION='rpnh/material_carrier_inspection/v1',PURGE='rpnh/material_purge/v1';
const CONTENT='metadata_and_proof_refs_only';
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const shape=(value,required,optional=[])=>object(value)&&required.every(name=>Object.hasOwn(value,name))
  &&Object.keys(value).every(name=>required.includes(name)||optional.includes(name));
const text=value=>typeof value==='string'&&value.length>0&&value.length<=4096&&!/[\u0000-\u001f\u007f]/.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const clone=value=>structuredClone(value);
const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(
  Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const encode=value=>JSON.stringify(canonical(value));
const same=(left,right)=>encode(left)===encode(right);
const fail=()=>{throw new Error('invalid_carriers_view');};
const errorCode=error=>/^[a-zA-Z0-9_:-]{1,100}$/.test(error?.message??'')?error.message:'result_unknown';
const accessError=error=>['access_changed','source_not_allowed','action_unavailable','management_not_configured'].includes(errorCode(error));
function ref(value,type){
  if(!shape(value,['entity_type','logical_id','version_id'])||value.entity_type!==type||!text(value.logical_id)||!text(value.version_id))fail();
  return value;
}
function refs(value,type){if(!Array.isArray(value))fail();for(const item of value)ref(item,type);return value;}
function path(value){
  if(!text(value)||value.startsWith('/')||value.split('/').some(part=>!part||part==='.'||part==='..')
      ||value.split('/')[0]==='registered_resources')fail();return value;
}
function catalogView(value,source,selected){
  if(!shape(value,['schema_version','action','source_id','supported','inspection_allowed','reason','resource_ref',
      'selection_status','as_of_ordinal','logical_availability','response_content'])||value.schema_version!==CARRIERS_SCHEMA
      ||value.action!=='carrier_catalog'||value.source_id!==source||typeof value.supported!=='boolean'
      ||typeof value.inspection_allowed!=='boolean'||value.logical_availability!==null||value.response_content!==CONTENT)fail();
  if(!value.supported){
    if(value.inspection_allowed||!['management_not_configured','action_unavailable'].includes(value.reason)
        ||value.resource_ref!==null||value.selection_status!=='not_selected'||value.as_of_ordinal!==null)fail();
    return clone(value);
  }
  if(!integer(value.as_of_ordinal)||!same(value.resource_ref===null?null:ref(value.resource_ref,'resource_version/v1'),selected))fail();
  if(value.selection_status==='not_selected'){
    if(selected!==null||value.inspection_allowed||value.reason!=='selection_required')fail();
  }else if(value.selection_status==='unsupported'){
    if(selected===null||value.inspection_allowed||value.reason!=='selection_not_supported')fail();
  }else if(value.selection_status==='candidate'){
    if(selected===null||!value.inspection_allowed||value.reason!==null)fail();
  }else fail();return clone(value);
}
function logicalAvailability(value,cut){
  if(!shape(value,['availability','availability_as_of','effect_refs','proof_refs'],['public_reason'])
      ||!['available','disabled','unknown'].includes(value.availability)||value.availability_as_of!==cut
      ||(Object.hasOwn(value,'public_reason')&&!text(value.public_reason)))fail();
  refs(value.effect_refs,'resource_version/v1');
  if(!Array.isArray(value.proof_refs))fail();
  for(const item of value.proof_refs){
    if(!['resource_version/v1','operation_result/v1','firing_completion/v2'].includes(item?.entity_type))fail();ref(item,item.entity_type);
  }
  if(value.availability==='disabled'){
    if(!value.effect_refs.length||!value.proof_refs.length)fail();
  }else if(value.effect_refs.length||value.proof_refs.length)fail();
}
function purgeEvidence(value,carrier){
  if(!shape(value,['protocol','carrier_ref','authorization_refs','results','pending','all_carriers_cleared'])
      ||value.protocol!==PURGE||value.all_carriers_cleared!==false||!same(ref(value.carrier_ref,'resource_version/v1'),carrier)
      ||!Array.isArray(value.results)||!Array.isArray(value.pending))fail();
  refs(value.authorization_refs,'resource_version/v1');
  for(const result of value.results){
    if(!shape(result,['command_ref','result_ref','receipt_ref','state'])||!['removed','already_absent'].includes(result.state))fail();
    for(const name of ['command_ref','result_ref','receipt_ref'])ref(result[name],'resource_version/v1');
  }
  for(const pending of value.pending){
    if(pending.status==='not_admitted'){
      if(!shape(pending,['command_ref','status']))fail();
    }else if(pending.status==='recovery_required'){
      if(!shape(pending,['command_ref','firing_ref','status']))fail();ref(pending.firing_ref,'transition_firing/v1');
    }else fail();ref(pending.command_ref,'resource_version/v1');
  }
  if((value.results.length||value.pending.length)&&!value.authorization_refs.length)fail();
}
function carrierState(item,observe){
  if(item.purge_authorized!==false)fail();
  const hasEvidence=Object.hasOwn(item,'purge_evidence'),cleanupRole=['semantic_output','workspace_file'].includes(item.role);
  if(!observe){if(item.carrier_state!=='unknown'||item.observation_reason!=='BODY_NOT_OBSERVED'||hasEvidence)fail();return;}
  if(hasEvidence){if(!cleanupRole)fail();purgeEvidence(item.purge_evidence,item.carrier_ref);}
  const results=item.purge_evidence?.results??[],pending=item.purge_evidence?.pending??[];
  const removed=results.some(value=>value.state==='removed'),recovering=pending.some(value=>value.status==='recovery_required');
  if(item.carrier_state==='present'){
    if(item.observation_reason!==null||hasEvidence)fail();
  }else if(item.carrier_state==='purged'){
    if(item.observation_reason!=='CANONICAL_PURGE_RESULT'||!hasEvidence||!removed)fail();
  }else if(item.carrier_state==='already_absent'){
    if(item.observation_reason!=='CANONICAL_ABSENCE_OBSERVATION'||!hasEvidence||!results.length||removed)fail();
  }else if(item.carrier_state==='unexpected_missing'){
    if(item.observation_reason!=='NO_CANONICAL_PURGE_RESULT'||hasEvidence!==cleanupRole||results.length||recovering)fail();
  }else if(item.carrier_state==='unknown'){
    if(item.observation_reason==='BODY_OBSERVATION_FAILED'){if(hasEvidence)fail();}
    else if(item.observation_reason==='PURGE_PROOF_UNAVAILABLE'){if(!cleanupRole||hasEvidence)fail();}
    else if(item.observation_reason==='PURGE_RECOVERY_REQUIRED'){if(!hasEvidence||results.length||!recovering)fail();}
    else fail();
  }else fail();
}
function inspectionView(value,source,selected,observe,taskId,minimumCut){
  if(!shape(value,['schema_version','action','source_id','resource_ref','observe_payloads','as_of_ordinal',
      'logical_availability','inspection','response_content'])||value.schema_version!==CARRIERS_SCHEMA
      ||value.action!=='read_material_carriers'||value.source_id!==source||!same(ref(value.resource_ref,'resource_version/v1'),selected)
      ||value.observe_payloads!==observe||!integer(value.as_of_ordinal)||value.as_of_ordinal<minimumCut||value.response_content!==CONTENT)fail();
  const inspection=value.inspection;
  if(!shape(inspection,['protocol','task_ref','command_ref','output_ref','firing_ref','observation_ordinal','carriers',
      'current_path_mapping','coverage','all_registry_carriers_proved','purge_authorized','availability_changed'])
      ||inspection.protocol!==INSPECTION||inspection.observation_ordinal!==value.as_of_ordinal
      ||inspection.coverage!=='literal_producer_and_its_workspace_lineage'||inspection.all_registry_carriers_proved!==false
      ||inspection.purge_authorized!==false||inspection.availability_changed!==false||!Array.isArray(inspection.carriers)
      ||inspection.carriers.length<5||!same(ref(inspection.output_ref,'resource_version/v1'),selected))fail();
  ref(inspection.task_ref,'task/v1');if(taskId!==null&&inspection.task_ref.logical_id!==taskId)fail();
  ref(inspection.command_ref,'resource_version/v1');ref(inspection.firing_ref,'transition_firing/v1');
  logicalAvailability(value.logical_availability,value.as_of_ordinal);
  const rows=inspection.carriers,[input,semantic,file,candidate,...archives]=rows;
  if(input.role!=='dedicated_input'||semantic.role!=='semantic_output'||file.role!=='workspace_file'
      ||candidate.role!=='settlement_candidate_archive'||archives.some(item=>item.role!=='workspace_archive'))fail();
  const base=['carrier_ref','role','carrier_state','observation_reason','purge_authorized','proof_refs'];
  for(const item of rows){
    const extra=item.role==='workspace_file'?['path']:item.role==='settlement_candidate_archive'?['path','retain_for_settlement_proof']
      :item.role==='workspace_archive'?['path','contains_other_files','exact_path_resource_ref']:[];
    if(!shape(item,[...base,...extra],['purge_evidence']))fail();
    ref(item.carrier_ref,item.role==='settlement_candidate_archive'?'workspace_revision_candidate/v1'
      :item.role==='workspace_archive'?'workspace_revision/v1':'resource_version/v1');
    if(!Array.isArray(item.proof_refs))fail();carrierState(item,observe);
  }
  if(new Set(rows.map(item=>encode(item.carrier_ref))).size!==rows.length||!same(semantic.carrier_ref,selected))fail();
  if(input.proof_refs.length!==2||!same(ref(input.proof_refs[0],'resource_version/v1'),inspection.command_ref)
      ||semantic.proof_refs.length!==1||!same(ref(semantic.proof_refs[0],'resource_version/v1'),ref(input.proof_refs[1],'resource_version/v1')))fail();
  if(file.proof_refs.length!==2||!same(ref(file.proof_refs[1],'workspace_revision_candidate/v1'),candidate.carrier_ref))fail();
  ref(file.proof_refs[0],'execution_terminal_mapping/v1');
  if(candidate.proof_refs.length!==2||candidate.retain_for_settlement_proof!==true
      ||!same(ref(candidate.proof_refs[1],'resource_version/v1'),semantic.proof_refs[0])
      ||same(ref(candidate.proof_refs[0],'execution_terminal_mapping/v1'),file.proof_refs[0]))fail();
  const filePath=path(file.path),mapping=inspection.current_path_mapping;
  if(path(candidate.path)!==filePath||!shape(mapping,['workspace_revision_ref','path','resource_ref','still_maps_to_inspected_file'])
      ||path(mapping.path)!==filePath||typeof mapping.still_maps_to_inspected_file!=='boolean')fail();
  ref(mapping.workspace_revision_ref,'workspace_revision/v1');
  if(mapping.resource_ref!==null)ref(mapping.resource_ref,'resource_version/v1');
  if(mapping.still_maps_to_inspected_file!==same(mapping.resource_ref,file.carrier_ref))fail();
  for(const archive of archives){
    if(path(archive.path)!==filePath||typeof archive.contains_other_files!=='boolean'||archive.proof_refs.length!==1
        ||!same(ref(archive.proof_refs[0],'execution_terminal_mapping/v1'),file.proof_refs[0])
        ||!same(ref(archive.exact_path_resource_ref,'resource_version/v1'),file.carrier_ref)
        ||archive.carrier_ref.logical_id!==mapping.workspace_revision_ref.logical_id)fail();
  }
  return clone(value);
}

export function createMaterialCarriersApi(fetcher=globalThis.fetch){
  async function read(action,source,resource,observe,signal){
    const params={action,source_id:source,...resource?{resource_ref:JSON.stringify(resource)}:{},
      ...(observe===undefined?{}:{observe_payloads:String(observe)})};
    const response=await fetcher(`/api/material-management?${new URLSearchParams(params)}`,
      {method:'GET',cache:'no-store',credentials:'same-origin',...(signal?{signal}:{})});
    const value=await response.json();if(!response.ok)throw new Error(value?.error??'result_unknown');return value;
  }
  return {catalog:(source,resource,signal)=>read('carrier_catalog',source,resource,undefined,signal),
    inspect:(source,resource,observe,signal)=>{
      if(typeof observe!=='boolean')throw new Error('invalid_query');
      return read('read_material_carriers',source,resource,observe,signal);
    }};
}
export function createMaterialCarriers({api,onChange=()=>{}}={}){
  if(!api||typeof api.catalog!=='function'||typeof api.inspect!=='function')throw new Error('CARRIERS_API_REQUIRED');
  let source=null,selected=null,taskId=null,externalDenied=false,denied=false,catalog=null,view=null;
  let generation=0,pending=null,loading=false,phase=null,error=null;
  const canInspect=()=>Boolean(source&&selected&&!externalDenied&&!denied&&!loading);
  const getState=()=>({source,resource_ref:clone(selected),catalog:clone(catalog),view:clone(view),loading,phase,error,can_inspect:canInspect()});
  const emit=()=>onChange(getState());
  function invalidate(){++generation;pending?.abort();pending=null;loading=false;phase=null;catalog=null;view=null;}
  function select(nextSource,resource=null,accessDenied=false,expectedTaskId=null){
    const next=text(nextSource)?nextSource:null;let exact=null,invalid=Boolean(accessDenied);
    try{if(resource!==null)exact=clone(ref(resource,'resource_version/v1'));}catch{invalid=true;}
    const nextTask=text(expectedTaskId)?expectedTaskId:null;
    if(next===source&&same(exact,selected)&&invalid===externalDenied&&nextTask===taskId)return;
    invalidate();source=next;selected=exact;taskId=nextTask;externalDenied=invalid;denied=invalid;error=invalid?'access_changed':null;emit();
  }
  async function inspect(observe){
    if(typeof observe!=='boolean')throw new Error('invalid_query');if(!canInspect())return;
    const original=source,selection=clone(selected),expectedTask=taskId,epoch=++generation;
    const request=new AbortController();pending=request;catalog=null;view=null;loading=true;phase='catalog';error=null;emit();
    try{
      const rawCatalog=await api.catalog(original,selection,request.signal);if(epoch!==generation)return;
      catalog=catalogView(rawCatalog,original,selection);if(!catalog.inspection_allowed)return;
      phase='inspection';emit();
      const raw=await api.inspect(original,selection,observe,request.signal);if(epoch!==generation)return;
      view=inspectionView(raw,original,selection,observe,expectedTask,catalog.as_of_ordinal);
    }catch(cause){
      // An observed source-wide denial still applies after a same-source
      // selection change. No facts from another source are retained here.
      if(accessError(cause)&&original===source){invalidate();denied=true;error=errorCode(cause);emit();}
      else if(epoch===generation){catalog=null;view=null;error=errorCode(cause);}
    }finally{if(epoch===generation){pending=null;loading=false;phase=null;emit();}}
  }
  return {getState,select,inspect,close(){select(null);}};
}

const STRINGS={
  en:{title:'Material carriers',hint:'Inspect the original carrier relationships of the selected exact semantic output. Both actions validate historical proof and may read candidate and workspace archives; no body is delivered.',
    relationships:'View carrier relationships',observe:'Observe carrier states',choose:'Select an exact semantic output in a readable local source.',
    unchecked:'No carrier observation requested. Each action checks current capability and then requests its own new observation.',
    loading:'Reading original carrier proof…',checking:'Checking current carrier inspection capability…',closed:'Carrier inspection is unavailable for this selection.',
    ready:'Original observation returned. These facts describe this read cut; they do not grant body access or cleanup.',error:'Carrier observation could not be confirmed:',
    context:'Exact selection and observation',logical:'Logical body availability',physical:'Physical carrier state',facts:'Original carrier references and proof',
    resultFacts:'Complete original inspection and availability',mapping:'Current path mapping',scope:'Coverage: literal producer and its workspace lineage only. The full Registry carrier inventory is not proved. Purge is not authorized; availability is unchanged.',
    relationMode:'Relationship mode omits additional per-carrier byte/size observations. Unknown does not mean present, absent or purged.',
    physicalMode:'Physical mode adds sequential per-carrier byte/size observations. These are not an atomic filesystem snapshot; missing bytes alone do not prove cleanup.',
    roles:{dedicated_input:'Dedicated input',semantic_output:'Semantic output',workspace_file:'Workspace FILE',settlement_candidate_archive:'Settlement candidate archive',workspace_archive:'Workspace archive'}},
  'zh-CN':{title:'资料载体',hint:'查看当前精确语义输出的原载体关系。两个动作都会验证历史证明，并可能读取 candidate 与 workspace 归档；不交付正文。',
    relationships:'查看载体关系',observe:'观察载体状态',choose:'请在可读的本地来源中选择精确语义输出。',
    unchecked:'尚未请求载体观察；每次明确操作都会核对当前能力，再请求一次新观察。',loading:'正在读取原载体证明…',checking:'正在核对当前载体检查能力…',
    closed:'此选择未开放载体检查。',ready:'已返回原观察；这些事实仅描述本次读取位置，不授予正文访问或清理权限。',error:'本次载体观察未能确认：',
    context:'精确选择与观察位置',logical:'逻辑正文可用性',physical:'物理载体状态',facts:'原载体引用与证明',resultFacts:'完整原检查与可用性',mapping:'当前路径映射',
    scope:'覆盖范围仅为 literal producer 及其 workspace lineage，未证明全 Registry 载体清单；不授权物理清理，不改变可用性。',
    relationMode:'关系模式不追加逐载体字节与大小观察；unknown 不表示存在、缺失或已清理。',
    physicalMode:'物理模式追加逐载体字节与大小观察；这些先后发生的观察不是原子文件系统快照，字节缺失本身不证明已清理。',
    roles:{dedicated_input:'专用输入',semantic_output:'语义输出',workspace_file:'Workspace FILE',settlement_candidate_archive:'结算 candidate 归档',workspace_archive:'Workspace 归档'}},
};
function selectedResource(state){
  const entry=state.record?.entry_ref;if(!entry||entry.source_id!==state.source)return null;
  if(entry.schema_version==='rpnh/collaboration/source_resource_ref/v1'&&shape(entry.ref,['resource_id','resource_version_id']))
    return ref({entity_type:'resource_version/v1',logical_id:entry.ref.resource_id,version_id:entry.ref.resource_version_id},'resource_version/v1');
  if(entry.schema_version==='rpnh/collaboration/source_version_ref/v1'&&entry.ref?.entity_type==='resource_version/v1')return ref(entry.ref,'resource_version/v1');
  return null;
}
export function mountMaterialCarriers(document,{root,fetcher=globalThis.fetch}={}){
  if(!root)throw new Error('CARRIERS_PANEL_ROOT_REQUIRED');
  let language='en';const nodes={},labels=[];
  const t=name=>STRINGS[language][name]??name;
  const el=(tag,name)=>{const node=document.createElement(tag);if(name)nodes[name]=node;return node;};
  const label=(node,name)=>{labels.push([node,name]);node.textContent=t(name);return node;};
  const button=(name,observe)=>{const node=label(el('button',name),name);node.type='button';
    node.dataset.action=observe?'carrier-observe':'carrier-relationships';
    node.addEventListener('click',()=>controller.inspect(observe));return node;};
  root.className='material-management material-carriers';
  const actions=el('div');actions.className='material-actions';actions.append(button('relationships',false),button('observe',true));
  root.append(label(el('h2'),'title'),label(el('p'),'hint'),actions,el('p','notice'),label(el('p'),'scope'));
  nodes.notice.setAttribute('role','status');nodes.notice.setAttribute('aria-live','polite');
  const context=el('details');context.append(label(el('summary'),'context'),el('pre','context'));root.append(context);
  const result=el('div','result');result.append(label(el('h3'),'logical'),el('p','logical'),el('p','mode'),label(el('h3'),'physical'),el('div','carriers'));
  const mapping=el('details');mapping.append(label(el('summary'),'mapping'),el('pre','mapping'));result.append(mapping);
  const facts=el('details');facts.append(label(el('summary'),'resultFacts'),el('pre','facts'));result.append(facts);root.append(result);
  function render(state){
    for(const [node,name] of labels)node.textContent=t(name);
    nodes.relationships.disabled=!state.can_inspect;nodes.observe.disabled=!state.can_inspect;
    const status=!state.source||!state.resource_ref?'choose':state.loading?(state.phase==='catalog'?'checking':'loading')
      :state.view?'ready':state.catalog&&!state.catalog.inspection_allowed?'closed':'unchecked';
    nodes.notice.textContent=state.error?`${t('error')} ${state.error}`:t(status)+(state.catalog?.reason?' '+state.catalog.reason:'');
    nodes.context.textContent=JSON.stringify({source_id:state.source,resource_ref:state.resource_ref,
      as_of_ordinal:state.view?.as_of_ordinal??state.catalog?.as_of_ordinal??null,observe_payloads:state.view?.observe_payloads??null},null,2);
    nodes.result.hidden=!state.view;nodes.carriers.replaceChildren();
    nodes.logical.textContent='';nodes.mode.textContent='';nodes.mapping.textContent='';nodes.facts.textContent='';
    if(!state.view)return;
    const value=state.view;nodes.logical.textContent=value.logical_availability.availability
      +(value.logical_availability.public_reason?' · '+value.logical_availability.public_reason:'');
    nodes.mode.textContent=t(value.observe_payloads?'physicalMode':'relationMode');
    nodes.mapping.textContent=JSON.stringify(value.inspection.current_path_mapping,null,2);nodes.facts.textContent=JSON.stringify(value,null,2);
    for(const carrier of value.inspection.carriers){
      const article=el('article'),title=el('h4'),status=el('p'),details=el('details'),summary=el('summary'),original=el('pre');
      article.className='material-command';title.textContent=`${STRINGS[language].roles[carrier.role]} · ${carrier.role}`;
      status.textContent=`${t('physical')}: ${carrier.carrier_state}`+(carrier.observation_reason?' · '+carrier.observation_reason:'');
      summary.textContent=t('facts');original.textContent=JSON.stringify(carrier,null,2);details.append(summary,original);
      article.append(title,status,details);nodes.carriers.append(article);
    }
  }
  const controller=createMaterialCarriers({api:createMaterialCarriersApi(fetcher),onChange:render});
  document.defaultView?.addEventListener?.('pagehide',()=>controller.close());render(controller.getState());
  return {controller,nodes,setLanguage(value){language=value==='zh-CN'?'zh-CN':'en';render(controller.getState());},
    setBrowserState(state){let selected=null,invalid=Boolean(state.error);
      const source=state.sources?.find(item=>item.source_id===state.source);
      invalid ||= state.source!=null&&(!source||source.access_state!=='readable'||source.access_mode!=='local_owner');
      try{selected=selectedResource(state);}catch{invalid=true;}
      controller.select(state.source,selected,invalid,source?.task_id??null);
    }};
}
