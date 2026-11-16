/** Explicit two-stage intentions over the original single-carrier purge ports.
 * Only original published receipts establish authorization or a final result.
 * An unknown reply retains its ID; closing a pane never cancels a command.
 */
export const PURGE_SCHEMA='rpnh/viewer_material_purge/v1';
const COMMAND='rpnh/material_command/v1',PURGE='rpnh/material_purge/v1',SCOPE='one_exact_immutable_blob';
const SUBMIT='rpnh/material_submit_receipt/v1',RECEIPT='rpnh/material_receipt_view/v1';
const ACTIVE=new Set(['REGISTERED','ADMITTED','PREPARED','RECOVERY_REQUIRED']),FINAL=new Set(['PUBLISHED','REJECTED']);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const shape=(value,required,optional=[])=>object(value)&&required.every(name=>Object.hasOwn(value,name))
  &&Object.keys(value).every(name=>required.includes(name)||optional.includes(name));
const text=value=>typeof value==='string'&&value.length>0&&value.length<=4096&&!/[\u0000-\u001f\u007f]/.test(value);
const identity=value=>text(value)&&value.length<=512;
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const clone=value=>structuredClone(value);
const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const encode=value=>JSON.stringify(canonical(value)),same=(left,right)=>encode(left)===encode(right);
const unique=values=>new Set(values.map(encode)).size===values.length;
const fail=()=>{throw new Error('invalid_purge_view');};
const errorCode=error=>/^[a-zA-Z0-9_:-]{1,100}$/.test(error?.message??'')?error.message:'result_unknown';
const accessError=error=>['access_changed','source_not_allowed'].includes(errorCode(error));
const phaseError=error=>['target_not_allowed','action_unavailable','purge_unauthorized'].includes(errorCode(error));
const authorityError=error=>accessError(error)||phaseError(error);
const cardKey=(source,id)=>JSON.stringify([source,id]);
function ref(value,type){
  if(!shape(value,['entity_type','logical_id','version_id'])||value.entity_type!==type||!text(value.logical_id)||!text(value.version_id))fail();return value;
}
function refs(value,type){if(!Array.isArray(value))fail();for(const item of value)ref(item,type);return value;}
function execution(value){
  if(!shape(value,['task_ref','task_round_ref','net_instance_ref']))fail();
  ref(value.task_ref,'task/v1');ref(value.task_round_ref,'task_round/v1');ref(value.net_instance_ref,'net_instance/v1');return value;
}
function selection(value){
  if(!shape(value,['material_command_ref','target_resource_ref','carrier_ref','carrier_role'])
      ||!['semantic_output','workspace_file'].includes(value.carrier_role))fail();
  for(const name of ['material_command_ref','target_resource_ref','carrier_ref'])ref(value[name],'resource_version/v1');
  if((value.carrier_role==='semantic_output')!==same(value.carrier_ref,value.target_resource_ref))fail();return value;
}
function params(value,operation){
  if(!shape(value,['protocol','operation','target','execution',...(operation==='authorize_purge'?['material_command_ref','carrier_ref']:['authorization_ref'])])
      ||value.protocol!==COMMAND||value.operation!==operation||!text(value.target))fail();execution(value.execution);
  for(const name of operation==='authorize_purge'?['material_command_ref','carrier_ref']:['authorization_ref'])ref(value[name],'resource_version/v1');return value;
}
function authorization(value,chosen,task,commandRef=null){
  if(!shape(value,['protocol','source_task_ref','carrier_ref','scope','all_carriers_cleared','authorization_command_ref',
      'material_command_ref','target_resource_ref','carrier_role','disable_effect_refs'])||value.protocol!==PURGE||value.scope!==SCOPE
      ||value.all_carriers_cleared!==false||!same(ref(value.source_task_ref,'task/v1'),task)
      ||!same(selection({material_command_ref:value.material_command_ref,target_resource_ref:value.target_resource_ref,
        carrier_ref:value.carrier_ref,carrier_role:value.carrier_role}),chosen))fail();
  ref(value.authorization_command_ref,'resource_version/v1');
  if(commandRef!==null&&!same(value.authorization_command_ref,commandRef))fail();
  refs(value.disable_effect_refs,'resource_version/v1');if(!value.disable_effect_refs.length||!unique(value.disable_effect_refs))fail();return value;
}
function resultBody(value,chosen,task,authRef){
  if(!shape(value,['protocol','source_task_ref','carrier_ref','scope','all_carriers_cleared','authorization_ref','state'])
      ||value.protocol!==PURGE||value.scope!==SCOPE||value.all_carriers_cleared!==false||!['removed','already_absent'].includes(value.state)
      ||!same(ref(value.source_task_ref,'task/v1'),task)||!same(ref(value.carrier_ref,'resource_version/v1'),chosen.carrier_ref)
      ||!same(ref(value.authorization_ref,'resource_version/v1'),authRef))fail();return value;
}
function logicalAvailability(value,cut){
  if(!shape(value,['availability','availability_as_of','effect_refs','proof_refs'],['public_reason'])
      ||!['available','disabled','unknown'].includes(value.availability)||value.availability_as_of!==cut
      ||(Object.hasOwn(value,'public_reason')&&!text(value.public_reason)))fail();
  refs(value.effect_refs,'resource_version/v1');if(!Array.isArray(value.proof_refs))fail();
  for(const item of value.proof_refs){if(!['resource_version/v1','operation_result/v1','firing_completion/v2'].includes(item?.entity_type))fail();ref(item,item.entity_type);}
  if(value.availability==='disabled'){if(!value.effect_refs.length||!value.proof_refs.length)fail();}
  else if(value.effect_refs.length||value.proof_refs.length)fail();
}
function inspectedScope(value,selected,cut,taskId){
  if(!shape(value,['protocol','task_ref','command_ref','output_ref','firing_ref','observation_ordinal','carriers','current_path_mapping',
      'coverage','all_registry_carriers_proved','purge_authorized','availability_changed'])||value.protocol!=='rpnh/material_carrier_inspection/v1'
      ||value.coverage!=='literal_producer_and_its_workspace_lineage'||value.all_registry_carriers_proved!==false
      ||value.purge_authorized!==false||value.availability_changed!==false||value.observation_ordinal!==cut
      ||!same(ref(value.output_ref,'resource_version/v1'),selected)||!Array.isArray(value.carriers)||value.carriers.length<5)fail();
  ref(value.task_ref,'task/v1');if(taskId!==null&&value.task_ref.logical_id!==taskId)fail();
  ref(value.command_ref,'resource_version/v1');ref(value.firing_ref,'transition_firing/v1');
  const [input,semantic,file,candidate,...archives]=value.carriers;
  if(input?.role!=='dedicated_input'||semantic?.role!=='semantic_output'||file?.role!=='workspace_file'
      ||candidate?.role!=='settlement_candidate_archive'||archives.some(item=>item?.role!=='workspace_archive'))fail();
  for(const item of value.carriers){
    const extra=item.role==='workspace_file'?['path']:item.role==='settlement_candidate_archive'?['path','retain_for_settlement_proof']
      :item.role==='workspace_archive'?['path','contains_other_files','exact_path_resource_ref']:[];
    if(!shape(item,['carrier_ref','role','carrier_state','observation_reason','purge_authorized','proof_refs',...extra])
        ||item.carrier_state!=='unknown'||item.observation_reason!=='BODY_NOT_OBSERVED'||item.purge_authorized!==false||!Array.isArray(item.proof_refs))fail();
    ref(item.carrier_ref,item.role==='settlement_candidate_archive'?'workspace_revision_candidate/v1'
      :item.role==='workspace_archive'?'workspace_revision/v1':'resource_version/v1');
  }
  if(!unique(value.carriers.map(item=>item.carrier_ref))||!same(semantic.carrier_ref,selected)
      ||input.proof_refs.length!==2||!same(ref(input.proof_refs[0],'resource_version/v1'),value.command_ref)
      ||semantic.proof_refs.length!==1||!same(ref(input.proof_refs[1],'resource_version/v1'),ref(semantic.proof_refs[0],'resource_version/v1'))
      ||file.proof_refs.length!==2||!same(ref(file.proof_refs[1],'workspace_revision_candidate/v1'),candidate.carrier_ref))fail();
  ref(file.proof_refs[0],'execution_terminal_mapping/v1');
  if(candidate.proof_refs.length!==2||candidate.retain_for_settlement_proof!==true
      ||!same(ref(candidate.proof_refs[1],'resource_version/v1'),semantic.proof_refs[0])
      ||same(ref(candidate.proof_refs[0],'execution_terminal_mapping/v1'),file.proof_refs[0]))fail();
  const mapping=value.current_path_mapping;
  if(!text(file.path)||candidate.path!==file.path||!shape(mapping,['workspace_revision_ref','path','resource_ref','still_maps_to_inspected_file'])
      ||mapping.path!==file.path||typeof mapping.still_maps_to_inspected_file!=='boolean')fail();
  ref(mapping.workspace_revision_ref,'workspace_revision/v1');if(mapping.resource_ref!==null)ref(mapping.resource_ref,'resource_version/v1');
  if(mapping.still_maps_to_inspected_file!==same(mapping.resource_ref,file.carrier_ref))fail();
  for(const archive of archives){
    if(archive.path!==file.path||typeof archive.contains_other_files!=='boolean'||archive.proof_refs.length!==1
        ||!same(ref(archive.proof_refs[0],'execution_terminal_mapping/v1'),file.proof_refs[0])
        ||!same(ref(archive.exact_path_resource_ref,'resource_version/v1'),file.carrier_ref)
        ||archive.carrier_ref.logical_id!==mapping.workspace_revision_ref.logical_id)fail();
  }
}
function targets(value,operation,chosen,task,authRef=null){
  if(!Array.isArray(value))fail();
  for(const item of value){
    if(!shape(item,['id','label','params'])||!text(item.id)||!text(item.label))fail();params(item.params,operation);
    if(!same(item.params.execution.task_ref,task))fail();
    if(operation==='authorize_purge'){
      if(!same(item.params.material_command_ref,chosen.material_command_ref)||!same(item.params.carrier_ref,chosen.carrier_ref))fail();
    }else if(!same(item.params.authorization_ref,authRef))fail();
  }
  if(!unique(value.map(item=>item.id)))fail();return value;
}
function catalogView(value,source,selected,taskId){
  if(!shape(value,['schema_version','action','source_id','supported','capabilities','reason','resource_ref','as_of_ordinal',
      'logical_availability','inspection','choices','scope','all_carriers_cleared','response_content'])||value.schema_version!==PURGE_SCHEMA
      ||value.action!=='purge_catalog'||value.source_id!==source||typeof value.supported!=='boolean'
      ||!shape(value.capabilities,['authorize_purge','purge_carrier'])||Object.values(value.capabilities).some(item=>typeof item!=='boolean')
      ||!(value.reason===null||text(value.reason))||!Array.isArray(value.choices)||value.scope!==SCOPE||value.all_carriers_cleared!==false
      ||value.response_content!=='metadata_and_proof_refs_only')fail();
  if(!value.supported){
    if(Object.values(value.capabilities).some(Boolean)||value.resource_ref!==null||value.as_of_ordinal!==null
        ||value.logical_availability!==null||value.inspection!==null||value.choices.length
        ||!['management_not_configured','action_unavailable'].includes(value.reason))fail();return clone(value);
  }
  if(!Object.values(value.capabilities).some(Boolean)||!integer(value.as_of_ordinal))fail();
  if(value.inspection===null){
    if(value.logical_availability!==null||value.resource_ref!==null||value.choices.length
        ||!['selection_required','authorization_unavailable','carrier_inspection_unavailable'].includes(value.reason)
        ||(value.reason==='selection_required'&&selected!==null)
        ||(value.reason==='authorization_unavailable'&&value.capabilities.authorize_purge))fail();return clone(value);
  }
  if(!value.capabilities.authorize_purge||selected===null||!same(ref(value.resource_ref,'resource_version/v1'),selected))fail();
  inspectedScope(value.inspection,selected,value.as_of_ordinal,taskId);logicalAvailability(value.logical_availability,value.as_of_ordinal);
  for(const choice of value.choices){
    if(!shape(choice,['carrier_ref','carrier_role','authorization_targets']))fail();
    const chosen=selection({material_command_ref:value.inspection.command_ref,target_resource_ref:selected,
      carrier_ref:choice.carrier_ref,carrier_role:choice.carrier_role});
    const row=value.inspection.carriers.find(item=>same(item.carrier_ref,choice.carrier_ref));
    if(!row||row.role!==choice.carrier_role||!targets(choice.authorization_targets,'authorize_purge',chosen,value.inspection.task_ref).length)fail();
  }
  if(!unique(value.choices.map(item=>item.carrier_ref)))fail();
  if(value.choices.length){if(value.logical_availability.availability!=='disabled'||value.reason!==null)fail();}
  else if(value.logical_availability.availability==='available'){if(value.reason!=='purge_not_disabled')fail();}
  else if(value.logical_availability.availability==='unknown'){if(value.reason!=='material_availability_unknown')fail();}
  else if(!['execution_changed','target_unavailable'].includes(value.reason))fail();return clone(value);
}
function receipt(value,id,operation,previous=null,submitted=false){
  if(!object(value)||value.protocol!==(submitted?SUBMIT:RECEIPT)||value.command_id!==id)fail();
  if(!submitted&&value.status==='NOT_FOUND_AT_READ_CUT')return value;
  ref(value.command_ref,'resource_version/v1');if(previous?.command_ref&&!same(value.command_ref,previous.command_ref))fail();
  if(submitted){if(value.status!=='REGISTERED')fail();return value;}
  if(!ACTIVE.has(value.status)&&!FINAL.has(value.status))fail();
  const types={admission_ref:'firing_admission/v1',firing_ref:'transition_firing/v1',invocation_ref:'invocation/v1',result_ref:'resource_version/v1',
    completion_ref:'firing_completion/v2',operation_result_ref:'operation_result/v1',rejection_ref:'firing_rejection/v1'};
  for(const [name,type] of Object.entries(types))if(value[name]!=null)ref(value[name],type);
  const executionRefs=['admission_ref','firing_ref','invocation_ref'];
  for(const name of executionRefs)if(previous?.[name]&&value[name]&&!same(previous[name],value[name]))fail();
  const hasExecution=executionRefs.every(name=>value[name]!=null);
  if(['ADMITTED','PREPARED','RECOVERY_REQUIRED'].includes(value.status)&&!hasExecution)fail();
  if(value.status==='PUBLISHED'){
    if(!hasExecution||value.result_kind!==(operation==='authorize_purge'?'purge_authorized':'carrier_purged')||value.error!==null
        ||!value.result_ref||!value.completion_ref||!value.operation_result_ref||!Array.isArray(value.output_refs)||value.output_refs.length!==1
        ||!Array.isArray(value.binding_refs)||value.binding_refs.length||value.rejection_ref!=null)fail();ref(value.output_refs[0],'resource_version/v1');
  }else if(value.status==='REJECTED'){
    if(!value.result_ref||!object(value.error)||!text(value.error.code)||value.completion_ref!=null||value.operation_result_ref!=null
        ||(value.output_refs!=null&&(!Array.isArray(value.output_refs)||value.output_refs.length))
        ||(value.binding_refs!=null&&(!Array.isArray(value.binding_refs)||value.binding_refs.length)))fail();
    if(value.result_kind==='not_admitted'){
      if(executionRefs.some(name=>value[name]!=null)||value.rejection_ref!=null||!text(value.rejection_transaction_id)||value.registered_completion_event_id!=null)fail();
    }else if((value.result_kind!=null&&value.result_kind!=='publish_rejected')||!hasExecution||!value.rejection_ref
        ||!text(value.registered_completion_event_id)||value.rejection_transaction_id!=null)fail();
  }
  return value;
}
function commandView(value,source,id,card,taskId){
  if(!shape(value,['schema_version','action','source_id','command_id','status','operation','params','selection','authorization_ref','authorization',
      'purge_result_ref','purge_result','execute_targets','execute_reason','receipt','as_of_ordinal','scope','all_carriers_cleared'])
      ||value.schema_version!==PURGE_SCHEMA||value.action!=='read_purge_receipt'||value.source_id!==source||value.command_id!==id
      ||value.status!==value.receipt?.status||!integer(value.as_of_ordinal)||value.scope!==SCOPE||value.all_carriers_cleared!==false
      ||!Array.isArray(value.execute_targets)||!(value.execute_reason===null||text(value.execute_reason)))fail();
  if(value.status==='NOT_FOUND_AT_READ_CUT'){
    receipt(value.receipt,id,null,card?.receipt);
    if(['operation','params','selection','authorization_ref','authorization','purge_result_ref','purge_result'].some(name=>value[name]!==null)
        ||value.execute_targets.length||value.execute_reason!=='not_found_at_read_cut')fail();return clone(value);
  }
  if(!['authorize_purge','purge_carrier'].includes(value.operation))fail();
  params(value.params,value.operation);selection(value.selection);const task=value.params.execution.task_ref;
  if(taskId!==null&&task.logical_id!==taskId)fail();receipt(value.receipt,id,value.operation,card?.receipt);
  if(card?.operation&&card.operation!==value.operation)fail();
  if(card?.params&&!same(card.params,value.params))fail();if(card?.selection&&!same(card.selection,value.selection))fail();
  if(value.operation==='authorize_purge'){
    if(!same(value.params.material_command_ref,value.selection.material_command_ref)||!same(value.params.carrier_ref,value.selection.carrier_ref)
        ||value.purge_result_ref!==null||value.purge_result!==null)fail();
    if(value.status==='PUBLISHED'){
      ref(value.authorization_ref,'resource_version/v1');
      if(!same(value.authorization_ref,value.receipt.output_refs[0]))fail();authorization(value.authorization,value.selection,task,value.receipt.command_ref);
      targets(value.execute_targets,'purge_carrier',value.selection,task,value.authorization_ref);
      if(value.execute_targets.length?value.execute_reason!==null:!['action_unavailable','execution_changed','target_unavailable'].includes(value.execute_reason))fail();
    }else if(value.authorization_ref!==null||value.authorization!==null||value.execute_targets.length||value.execute_reason!=='authorization_not_published')fail();
  }else{
    if(!same(ref(value.authorization_ref,'resource_version/v1'),value.params.authorization_ref)||value.execute_targets.length||value.execute_reason!=='phase_not_authorization')fail();
    authorization(value.authorization,value.selection,task);
    if(same(value.authorization.authorization_command_ref,value.receipt.command_ref))fail();
    if(value.status==='PUBLISHED'){
      if(!same(ref(value.purge_result_ref,'resource_version/v1'),value.receipt.output_refs[0])||same(value.purge_result_ref,value.authorization_ref))fail();
      resultBody(value.purge_result,value.selection,task,value.authorization_ref);
    }else if(value.purge_result_ref!==null||value.purge_result!==null)fail();
  }
  if(card?.view?.authorization&&(!same(card.view.authorization_ref,value.authorization_ref)||!same(card.view.authorization,value.authorization)))fail();
  if(card?.authorization&&(!same(card.authorization_ref,value.authorization_ref)||!same(card.authorization,value.authorization)))fail();
  return clone(value);
}
export function createMaterialPurgeApi(fetcher=globalThis.fetch){
  async function read(action,params,signal){
    const query=Object.fromEntries(Object.entries(params).map(([name,value])=>[name,object(value)?JSON.stringify(value):value]));
    const response=await fetcher(`/api/material-management?${new URLSearchParams({action,...query})}`,
      {method:'GET',cache:'no-store',credentials:'same-origin',...(signal?{signal}:{})});
    const value=await response.json();if(!response.ok)throw new Error(value?.error??'result_unknown');return value;
  }
  async function write(action,body){
    const response=await fetcher(`/api/material-management?action=${action}`,{method:'POST',cache:'no-store',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();if(!response.ok)throw new Error(value?.error??'result_unknown');return value;
  }
  return {catalog:(source,resource,signal)=>read('purge_catalog',{source_id:source,...resource?{resource_ref:resource}:{}},signal),
    authorize:body=>write('purge_authorize',body),execute:body=>write('purge_execute',body),
    receipt:(source,id)=>read('read_purge_receipt',{source_id:source,material_command_id:id})};
}
function commandIdentity(phase){
  if(typeof globalThis.crypto?.randomUUID!=='function')throw new Error('command_identity_unavailable');
  return `material-purge-${phase}:`+globalThis.crypto.randomUUID();
}

export function createMaterialPurge({api,makeCommandId=commandIdentity,confirmPurge=()=>false,onChange=()=>{}}={}){
  if(!api||['catalog','authorize','execute','receipt'].some(name=>typeof api[name]!=='function'))throw new Error('PURGE_API_REQUIRED');
  let source=null,selected=null,taskId=null,externalDenied=false,denied=false,catalog=null,loading=false,error=null;
  let generation=0,catalogSerial=0,catalogController=null,clock=0,catalogObservation=0,activeAuthorization=null,confirmation=null,lookup=null;
  const commands=new Map(),used=new Set(),lookups=new Map(),accessRevisions=new Map(),capabilityRevisions=new Map();
  const current=()=>[...commands.values()].filter(card=>card.source_id===source);
  const available=()=>Boolean(source&&!externalDenied&&!denied);
  const lookupPending=()=>[...lookups.values()].some(value=>value===source);
  const unresolved=()=>current().some(card=>!FINAL.has(card.status));
  const reviewAfter=(except=null)=>Math.max(0,...current().filter(card=>card!==except).map(card=>card.review_after??0));
  const canAuthorize=()=>Boolean(available()&&!loading&&!confirmation&&!lookupPending()&&!unresolved()
    &&catalog?.choices.length&&catalogObservation>reviewAfter());
  function canExecute(card){
    if(!available()||loading||confirmation||lookupPending()||unresolved()||card.source_id!==source||card.operation!=='authorize_purge'
        ||card.status!=='PUBLISHED'||activeAuthorization!==card.command_id||!card.view?.execute_targets.length
        ||card.observation<=reviewAfter(card))return false;
    return !current().some(other=>other.operation==='purge_carrier'&&same(other.params?.authorization_ref,card.authorization_ref)
      &&other.status==='PUBLISHED');
  }
  function getState(){
    const visible=available();return {source,resource_ref:clone(selected),catalog:clone(catalog),loading,error,
      can_load:visible&&!loading&&!confirmation,can_authorize:canAuthorize(),can_lookup:visible&&!lookupPending(),
      lookup_loading:lookupPending(),lookup:clone(lookup),active_authorization_id:activeAuthorization,
      blocked:visible&&(unresolved()||lookupPending()||(catalog!==null&&catalogObservation<=reviewAfter())),
      commands:visible?current().map(card=>({...clone(card),can_query:!card.querying&&!lookupPending(),can_execute:canExecute(card)})):[]};
  }
  const emit=()=>onChange(getState());
  function scrubCard(card){
    card.revision=(card.revision??0)+1;card.params=null;card.selection=null;card.authorization_ref=null;card.authorization=null;
    card.view=null;card.receipt=null;card.status='RESULT_UNKNOWN';card.observation=0;card.review_after=null;
  }
  function scrub(original){
    accessRevisions.set(original,(accessRevisions.get(original)??0)+1);
    for(const card of commands.values())if(card.source_id===original)scrubCard(card);
  }
  function observeCapabilities(value,original){
    if(value.capabilities.authorize_purge&&value.capabilities.purge_carrier)return false;
    capabilityRevisions.set(original,(capabilityRevisions.get(original)??0)+1);
    for(const card of commands.values())if(card.source_id===original&&!value.capabilities[card.operation])scrubCard(card);
    if(original===source)activeAuthorization=null;return true;
  }
  function invalidate(){
    ++generation;++catalogSerial;catalogController?.abort();catalogController=null;loading=false;catalog=null;
    catalogObservation=0;activeAuthorization=null;confirmation=null;lookup=null;++clock;
  }
  function select(nextSource,resource=null,accessDenied=false,expectedTaskId=null){
    const next=text(nextSource)?nextSource:null,nextTask=text(expectedTaskId)?expectedTaskId:null;
    let exact=null,invalid=Boolean(accessDenied);try{if(resource!==null)exact=clone(ref(resource,'resource_version/v1'));}catch{invalid=true;}
    if(next===source&&same(exact,selected)&&invalid===externalDenied&&nextTask===taskId)return;
    if(next!==null&&(invalid||(next===source&&nextTask!==taskId)))scrub(next);
    invalidate();source=next;selected=exact;taskId=nextTask;externalDenied=invalid;denied=invalid;error=invalid?'access_changed':null;emit();
  }
  function noteError(cause,card=null,original=source){
    const code=errorCode(cause);if(original===source)error=code;
    if(card){card.view=null;if(!FINAL.has(card.status)){card.status='RESULT_UNKNOWN';card.error=code;}}
    if(accessError(cause)){
      scrub(original);if(original===source){invalidate();denied=true;error=code;}
    }else if(phaseError(cause)){
      // Closing A cannot close the independently permitted B lookup. Clear
      // this rejected phase's facts, but retain explicit read actions.
      if(card)scrubCard(card);if(original===source){invalidate();error=code;}
    }
  }
  async function loadCatalog(){
    if(!available()||loading||confirmation)return;
    const original=source,selection=clone(selected),expectedTask=taskId,epoch=generation,serial=++catalogSerial,observation=++clock;
    catalogController=new AbortController();catalog=null;catalogObservation=0;activeAuthorization=null;loading=true;error=null;emit();
    try{
      const raw=await api.catalog(original,selection,catalogController.signal),value=catalogView(raw,original,selection,expectedTask);
      if(observeCapabilities(value,original))emit();
      if(epoch!==generation||serial!==catalogSerial)return;
      catalog=value;catalogObservation=observation;
    }catch(cause){if((epoch===generation&&serial===catalogSerial)||authorityError(cause)){noteError(cause,null,original);emit();}}
    finally{if(epoch===generation&&serial===catalogSerial){loading=false;catalogController=null;emit();}}
  }
  function newIdentity(phase,original){
    const id=makeCommandId(phase),key=cardKey(original,id);if(!identity(id)||used.has(key))throw new Error('command_identity_unavailable');
    used.add(key);return id;
  }
  async function ask(value){
    const marker={source_id:source,operation:value.operation};confirmation=marker;error=null;emit();
    try{return await confirmPurge(clone(value))===true;}
    catch(cause){if(confirmation===marker)error=errorCode(cause);return false;}
    finally{if(confirmation===marker)confirmation=null;emit();}
  }
  async function submit(operation,target,chosen,auth=null,authRef=null){
    const original=source,epoch=generation,access=accessRevisions.get(original)??0,capability=capabilityRevisions.get(original)??0,
      id=newIdentity(operation==='authorize_purge'?'authorize':'execute',original);
    const card={source_id:original,command_id:id,operation,params:clone(target.params),selection:clone(chosen),
      authorization_ref:clone(authRef),authorization:clone(auth),view:null,receipt:null,status:'SUBMITTING',sending:true,querying:false,
      error:null,review_after:null,observation:0,revision:0,recovered:false};
    commands.set(cardKey(original,id),card);activeAuthorization=null;emit();
    try{
      const raw=await api[operation==='authorize_purge'?'authorize':'execute']({source_id:original,owner_command:'material_submit',command_id:id,params:clone(target.params)});
      if(access!==(accessRevisions.get(original)??0)||capability!==(capabilityRevisions.get(original)??0)||card.revision!==0){
        if(!FINAL.has(card.status))card.status='RESULT_UNKNOWN';return;
      }
      if(epoch!==generation){if(!FINAL.has(card.status))card.status='RESULT_UNKNOWN';return;}
      const accepted=receipt(raw,id,operation,card.receipt,true);
      // A GET may already have observed a later original result while this
      // POST response was delayed. Registration must not replace that result.
      if(card.receipt?.protocol!==RECEIPT){card.receipt=clone(accepted);card.status='REGISTERED';card.error=null;}
    }catch(cause){if(epoch===generation||authorityError(cause))noteError(cause,card,original);else if(!FINAL.has(card.status))card.status='RESULT_UNKNOWN';}
    finally{card.sending=false;emit();}
  }
  async function authorize(carrierRef,targetId=null){
    if(!canAuthorize())return;
    const original=source,epoch=generation,view=catalog,observation=catalogObservation;
    const choice=view.choices.find(item=>same(item.carrier_ref,carrierRef));if(!choice)return;
    const target=choice.authorization_targets.find(item=>item.id===(targetId??choice.authorization_targets[0]?.id));if(!target)return;
    const chosen=selection({material_command_ref:view.inspection.command_ref,target_resource_ref:view.resource_ref,
      carrier_ref:choice.carrier_ref,carrier_role:choice.carrier_role});
    const accepted=await ask({source_id:original,operation:'authorize_purge',selection:chosen,
      disable_effect_refs:view.logical_availability.effect_refs,target});
    if(!accepted||epoch!==generation||catalog!==view||catalogObservation!==observation||!canAuthorize())return;
    return submit('authorize_purge',target,chosen);
  }
  async function execute(id,targetId=null){
    const card=commands.get(cardKey(source,id));if(!card||!canExecute(card))return;
    const original=source,epoch=generation,view=card.view,observation=card.observation;
    const target=view.execute_targets.find(item=>item.id===(targetId??view.execute_targets[0]?.id));if(!target)return;
    const accepted=await ask({source_id:original,operation:'purge_carrier',authorization_command_id:id,selection:card.selection,
      authorization_ref:view.authorization_ref,authorization:view.authorization,target});
    if(!accepted||epoch!==generation||card.view!==view||card.observation!==observation||!canExecute(card))return;
    return submit('purge_carrier',target,card.selection,view.authorization,view.authorization_ref);
  }
  function applyReceipt(card,value,observation){
    if(value.status==='NOT_FOUND_AT_READ_CUT'){
      if(!FINAL.has(card.status)){card.status='RESULT_UNKNOWN';card.error='NOT_FOUND_AT_READ_CUT';}return false;
    }
    if(FINAL.has(card.status)){
      if(!FINAL.has(value.status))return false;
      if(!same(card.receipt,value.receipt))fail();
    }
    const previous=card.receipt,next=value.receipt,order=['REGISTERED','ADMITTED','PREPARED'];
    if(previous?.admission_ref&&next.status==='REGISTERED')return false;
    if(previous?.admission_ref&&next.result_kind==='not_admitted')fail();
    if(order.includes(previous?.status)&&order.includes(next.status)&&order.indexOf(next.status)<order.indexOf(previous.status))return false;
    if(!FINAL.has(card.status)&&FINAL.has(next.status))card.review_after=++clock;
    Object.assign(card,{operation:value.operation,params:clone(value.params),selection:clone(value.selection),authorization_ref:clone(value.authorization_ref),
      authorization:clone(value.authorization),view:clone(value),receipt:clone(next),status:next.status,error:null,observation});return true;
  }
  async function query(id){
    if(!available()||lookupPending()||!identity(id))return;
    const original=source,key=cardKey(original,id),existing=commands.get(key);if(existing?.querying)return;
    const card=existing??{source_id:original,command_id:id,operation:null,params:null,selection:null,authorization_ref:null,authorization:null,
      view:null,receipt:null,status:'RESULT_UNKNOWN',sending:false,querying:false,error:null,review_after:null,observation:0,revision:0,recovered:true};
    const epoch=generation,access=accessRevisions.get(original)??0,capability=capabilityRevisions.get(original)??0,
      revision=card.revision,expectedTask=taskId,observation=++clock;
    card.querying=true;used.add(key);lookups.set(key,original);activeAuthorization=null;lookup=null;error=null;emit();
    try{
      const raw=await api.receipt(original,id);
      if(access!==(accessRevisions.get(original)??0)||capability!==(capabilityRevisions.get(original)??0)||revision!==card.revision)return;
      const value=commandView(raw,original,id,card,expectedTask),applied=applyReceipt(card,value,observation);
      if(value.status!=='NOT_FOUND_AT_READ_CUT')commands.set(key,card);
      if(epoch===generation){
        lookup=clone(value);
        if(applied&&value.operation==='authorize_purge'&&value.status==='PUBLISHED')activeAuthorization=id;
      }
    }catch(cause){if(epoch===generation||authorityError(cause))noteError(cause,existing??null,original);}
    finally{card.querying=false;lookups.delete(key);emit();}
  }
  return {getState,select,loadCatalog,authorize,execute,query,readEarlier:query,close(){select(null);}};
}

const STRINGS={
  en:{title:'Clean up one exact carrier',hint:'Review an original finite scope, confirm its authorization, then separately confirm execution after the original authorization is published.',
    load:'Check cleanup actions',authorize:'Authorize this carrier',execute:'Execute this authorized cleanup',query:'Query original cleanup result',read:'Read original cleanup status',
    choose:'Choose a readable local source. An earlier original command can also be looked up by its ID.',
    unchecked:'No fresh cleanup scope is loaded. Review current actions, or query an earlier original command ID.',loading:'Reading original finite carrier scope…',
    closed:'No new authorization is available from this observation.',ready:'Choose exactly one original carrier for Phase A confirmation.',
    blocked:'An original command is unresolved or a fresh observation is required. Query its original ID before starting another intention.',
    error:'Original cleanup request could not be confirmed:',context:'Current exact selection',scope:'Scope is one exact immutable semantic or FILE blob. Input, candidate/workspace archives, working paths, positions, other versions and other carriers remain outside this scope. All carriers cleared: false.',
    io:'Reviewing original scope may read archives to validate provenance. It does not deliver body bytes or observe every carrier’s physical state.',
    choices:'Fresh single-carrier choices',commands:'Original authorization and execution commands',original:'Original identity, scope and receipt',
    lookup:'Find an earlier original command',id:'Original stable command ID',lookupHint:'Reads the original phase and immutable scope. This does not recreate authorization, resubmit execution or cancel a command.',
    selection:'Exact single-carrier scope',facts:'Original scope and proof references',authorization:'Original published authorization',
    phaseA:'Phase A · authorization',phaseB:'Phase B · execution',authorizationHint:'A published authorization is permission for its exact scope. Physical completion requires the original execution result.',
    pendingHint:'Physical state is not established by this command status. Physical work can precede final publication; query the same original ID.',
    noExecute:'No current execution target is available from this original authorization observation.',
    confirmA:'Request authorization for only this exact immutable carrier? Physical execution requires a separate confirmation after the original authorization is published.',
    confirmB:'Request cleanup of only this exact immutable carrier under this original authorization? The original tool may remove its bytes or confirm they were already absent. An unconfirmed reply must be queried by its original ID.',
    source:'Source',effects:'Original disable effects',authRef:'Original authorization reference',
    status:{SUBMITTING:'Submitting original command; result unconfirmed',RESULT_UNKNOWN:'Original result unknown; query this ID',REGISTERED:'Registered; awaiting original admission',
      ADMITTED:'Admitted; original final result unconfirmed',PREPARED:'Prepared; original final result unconfirmed',RECOVERY_REQUIRED:'Original Owner recovery required; final result unconfirmed',
      REJECTED:'Original command publication refused; this is not a physical-state observation'},
    authorized:'Original finite authorization published',published:'Original cleanup result published',
    removed:'removed · Original tool confirmed removal of this exact carrier.',already_absent:'already_absent · Original tool confirmed this exact carrier was already absent.'},
  'zh-CN':{title:'清理一个精确载体',hint:'先复核原有限范围并确认授权；原授权正式发布后，再单独明确确认执行。',
    load:'查看清理操作',authorize:'授权此载体',execute:'执行此原授权清理',query:'回查原清理结果',read:'读取原清理状态',
    choose:'请选择可读的本地来源；也可输入此前的原命令 ID 回查。',unchecked:'尚未加载新的清理范围；请查看当前操作，或回查此前的原命令 ID。',
    loading:'正在读取原有限载体范围…',closed:'此观察未开放新的授权。',ready:'请选择一个原载体，明确确认 Phase A 授权。',
    blocked:'原命令尚未确认，或需要新观察；开始另一意图前请回查原 ID。',error:'本次原清理请求未能确认：',context:'当前精确选择',
    scope:'范围仅为一个精确 immutable semantic 或 FILE blob。输入、candidate/workspace 归档、工作路径、位置、其它版本与其它载体都在范围之外；all_carriers_cleared=false。',
    io:'复核原范围可能读取归档以验证历史来源；不交付正文，也不观察每个载体的物理状态。',choices:'新观察中的单载体选择',commands:'原授权与执行命令',
    original:'原标识、范围与回执',lookup:'查找此前的原命令',id:'原稳定命令 ID',lookupHint:'读取原阶段及不可变范围；不重建授权、不重新提交执行，也不取消原命令。',
    selection:'精确单载体范围',facts:'原范围与证明引用',authorization:'原已发布授权',phaseA:'Phase A · 授权',phaseB:'Phase B · 执行',
    authorizationHint:'已发布授权仅表示其精确范围的许可；物理完成仍以原执行结果为准。',pendingHint:'本命令状态不能确定物理状态；物理操作可能早于最终发布，请回查同一原 ID。',
    noExecute:'此原授权观察没有可用的当前执行目标。',
    confirmA:'只为这个精确 immutable 载体申请原清理授权？原授权正式发布后，物理执行还需要再次单独确认。',
    confirmB:'按这个原授权，仅请求清理其中这个精确 immutable 载体？原工具可能删除其字节，或确认它此前已不存在。回包未确认时必须查询原 ID。',
    source:'来源',effects:'原停用效果',authRef:'原授权引用',
    status:{SUBMITTING:'正在提交原命令；结果尚未确认',RESULT_UNKNOWN:'原结果未知，请回查此 ID',REGISTERED:'已登记，等待原准入',ADMITTED:'已准入；原最终结果尚未确认',
      PREPARED:'已准备；原最终结果尚未确认',RECOVERY_REQUIRED:'需要原 Owner 恢复；最终结果尚未确认',REJECTED:'原命令发布被拒绝；这不表示已观察物理状态'},
    authorized:'原有限授权已正式发布',published:'原清理结果已正式发布',removed:'removed · 原工具已确认移除这个精确载体。',already_absent:'already_absent · 原工具已确认这个精确载体此前已不存在。'},
};
function selectedResource(state){
  const entry=state.record?.entry_ref;if(!entry||entry.source_id!==state.source)return null;
  if(entry.schema_version==='rpnh/collaboration/source_resource_ref/v1'&&shape(entry.ref,['resource_id','resource_version_id']))
    return ref({entity_type:'resource_version/v1',logical_id:entry.ref.resource_id,version_id:entry.ref.resource_version_id},'resource_version/v1');
  if(entry.schema_version==='rpnh/collaboration/source_version_ref/v1'&&entry.ref?.entity_type==='resource_version/v1')return ref(entry.ref,'resource_version/v1');
  return null;
}
export function mountMaterialPurge(document,{root,fetcher=globalThis.fetch,makeCommandId,
    confirmPurge=message=>document.defaultView?.confirm?.(message)===true}={}){
  if(!root)throw new Error('PURGE_PANEL_ROOT_REQUIRED');
  let language='en',renderedCommands='',renderedChoices='';const nodes={},labels=[],views=new Map(),choices=new Map();
  const t=name=>STRINGS[language][name]??name;
  const el=(tag,name)=>{const node=document.createElement(tag);if(name)nodes[name]=node;return node;};
  const label=(node,name)=>{labels.push([node,name]);node.textContent=t(name);return node;};
  const invoke=action=>Promise.resolve().then(action).catch(cause=>{nodes.notice.textContent=`${t('error')} ${errorCode(cause)}`;});
  const button=(name,action,dataset)=>{const node=label(el('button',name),name);node.type='button';node.dataset.action=dataset;
    node.addEventListener('click',()=>invoke(action));return node;};
  root.className='material-management material-purge';
  root.append(label(el('h2'),'title'),label(el('p'),'hint'),button('load',()=>controller.loadCatalog(),'purge-catalog'),el('p','notice'),label(el('p'),'scope'),label(el('p'),'io'));
  nodes.notice.setAttribute('role','status');nodes.notice.setAttribute('aria-live','polite');
  const context=el('details');context.append(label(el('summary'),'context'),el('pre','context'));root.append(context);
  root.append(label(el('h3'),'choices'),el('div','choices'),label(el('h3'),'commands'),el('div','commands'));
  const lookup=el('details'),id=el('input','lookupId'),idLabel=el('label');id.type='text';id.maxLength=512;
  id.dataset.purpose='purge-command-id';id.setAttribute('data-purpose','purge-command-id');
  idLabel.append(label(el('span'),'id'),id);lookup.append(label(el('summary'),'lookup'),idLabel,
    button('read',()=>controller.query(id.value),'purge-lookup'),label(el('p'),'lookupHint'),el('pre','lookupResult'));root.append(lookup);
  function commandNodes(card){
    const key=cardKey(card.source_id,card.command_id);let view=views.get(key);if(view)return view;
    const article=el('article'),title=el('h4'),status=el('p'),hint=el('p'),error=el('p'),summary=el('summary'),facts=el('pre'),details=el('details');
    article.className='material-command';details.append(summary,facts);
    const query=el('button');query.type='button';query.dataset.action='purge-query';query.dataset.commandId=card.command_id;
    query.addEventListener('click',()=>invoke(()=>controller.query(card.command_id)));
    const targets=el('div');targets.className='material-actions';article.append(title,status,hint,error,query,targets,details);
    nodes.commands.append(article);view={title,status,hint,error,summary,facts,query,targets,targetKey:'',buttons:new Map()};views.set(key,view);return view;
  }
  function render(state){
    for(const [node,name] of labels)node.textContent=t(name);
    nodes.load.disabled=!state.can_load;nodes.read.disabled=!state.can_lookup;
    const status=!state.source?'choose':state.loading?'loading':state.blocked?'blocked':!state.catalog?'unchecked':state.catalog.choices.length?'ready':'closed';
    nodes.notice.textContent=state.error?`${t('error')} ${state.error}`:t(status)+(state.catalog?.reason?' '+state.catalog.reason:'');
    nodes.context.textContent=JSON.stringify({source_id:state.source,resource_ref:state.resource_ref,as_of_ordinal:state.catalog?.as_of_ordinal??null},null,2);
    nodes.lookupResult.textContent=state.lookup?JSON.stringify(state.lookup,null,2):'';
    const choiceKey=encode(state.catalog);
    if(choiceKey!==renderedChoices){
      renderedChoices=choiceKey;nodes.choices.replaceChildren();choices.clear();
      for(const choice of state.catalog?.choices??[]){
        const article=el('article'),title=el('h4'),summary=el('summary'),facts=el('pre'),details=el('details');
        article.className='material-command';title.textContent=choice.carrier_role;details.append(summary,facts);article.append(title);
        const buttons=[];
        for(const target of choice.authorization_targets){
          const row=el('div'),name=el('span'),node=el('button');row.className='material-actions';name.textContent=target.label;
          node.type='button';node.dataset.action='purge-authorize';node.dataset.carrierRole=choice.carrier_role;node.dataset.targetId=target.id;
          node.addEventListener('click',()=>invoke(()=>controller.authorize(choice.carrier_ref,target.id)));row.append(name,node);article.append(row);buttons.push(node);
        }
        facts.textContent=JSON.stringify({carrier_ref:choice.carrier_ref,carrier_role:choice.carrier_role,resource_ref:state.catalog.resource_ref,
          inspection:state.catalog.inspection,logical_availability:state.catalog.logical_availability},null,2);
        article.append(details);nodes.choices.append(article);choices.set(encode(choice.carrier_ref),{summary,buttons});
      }
    }
    for(const view of choices.values()){view.summary.textContent=t('facts');for(const node of view.buttons){node.textContent=t('authorize');node.disabled=!state.can_authorize;}}
    const commandKeys=encode(state.commands.map(card=>[card.source_id,card.command_id]));
    if(commandKeys!==renderedCommands){renderedCommands=commandKeys;views.clear();nodes.commands.replaceChildren();}
    for(const card of state.commands){
      const view=commandNodes(card);view.title.textContent=`${t(card.operation==='purge_carrier'?'phaseB':'phaseA')} · ${card.command_id}`;
      view.status.textContent=card.status==='PUBLISHED'?(card.operation==='authorize_purge'?t('authorized'):
        card.view?.purge_result?t(card.view.purge_result.state):t('published')):STRINGS[language].status[card.status]??card.status;
      view.hint.textContent=card.operation==='authorize_purge'?t('authorizationHint'):
        card.status==='PUBLISHED'&&card.view?.purge_result?'':t('pendingHint');
      if(card.operation==='authorize_purge'&&card.status==='PUBLISHED'&&!card.view?.execute_targets.length)view.hint.textContent+=' '+t('noExecute')+(card.view?.execute_reason?' '+card.view.execute_reason:'');
      view.error.textContent=card.error?`${t('error')} ${card.error}`:'';view.query.textContent=t('query');view.query.disabled=!card.can_query;
      view.summary.textContent=t('original');view.facts.textContent=JSON.stringify({source_id:card.source_id,command_id:card.command_id,operation:card.operation,
        params:card.params,selection:card.selection,authorization_ref:card.authorization_ref,authorization:card.authorization,view:card.view,receipt:card.receipt},null,2);
      const targets=card.view?.execute_targets??[],targetKey=encode(targets);
      if(targetKey!==view.targetKey){view.targetKey=targetKey;view.targets.replaceChildren();view.buttons.clear();
        for(const target of targets){const name=el('span'),node=el('button');name.textContent=target.label;node.type='button';
          node.dataset.action='purge-execute';node.dataset.commandId=card.command_id;node.dataset.targetId=target.id;
          node.addEventListener('click',()=>invoke(()=>controller.execute(card.command_id,target.id)));view.targets.append(name,node);view.buttons.set(target.id,node);}}
      for(const target of targets){const node=view.buttons.get(target.id);node.textContent=t('execute');node.disabled=!card.can_execute;}
    }
  }
  const controller=createMaterialPurge({api:createMaterialPurgeApi(fetcher),makeCommandId,onChange:render,
    confirmPurge:value=>confirmPurge(`${t(value.operation==='authorize_purge'?'confirmA':'confirmB')}\n\n${t('source')}: ${value.source_id}\n${t('selection')}:\n${JSON.stringify(value.selection,null,2)}\n${value.operation==='authorize_purge'?t('effects')+':\n'+JSON.stringify(value.disable_effect_refs,null,2):t('authRef')+':\n'+JSON.stringify(value.authorization_ref,null,2)+'\n'+t('authorization')+':\n'+JSON.stringify(value.authorization,null,2)}\n\n${t('scope')}`)});
  document.defaultView?.addEventListener?.('pagehide',()=>controller.close());render(controller.getState());
  return {controller,nodes,setLanguage(value){language=value==='zh-CN'?'zh-CN':'en';render(controller.getState());},
    setBrowserState(state){let selected=null,invalid=Boolean(state.error);const source=state.sources?.find(item=>item.source_id===state.source);
      invalid ||= state.source!=null&&(!source||source.access_state!=='readable'||source.access_mode!=='local_owner');
      try{selected=selectedResource(state);}catch{invalid=true;}controller.select(state.source,selected,invalid,source?.task_id??null);
    }};
}
