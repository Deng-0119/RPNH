/** Explicit finite-version disable intentions over the original Owner ports.
 * Plans and receipts are observed facts, never publication authority. A failed
 * response retains its original identity; querying does not repeat a write.
 */
export const DISABLE_SCHEMA='rpnh/viewer_material_disable/v1';
const PLAN='rpnh/material_disable_plan/v1',COMMAND='rpnh/material_command/v1';
const SUBMIT='rpnh/material_submit_receipt/v1',RECEIPT='rpnh/material_receipt_view/v1';
const ACTIVE=new Set(['REGISTERED','ADMITTED','PREPARED','RECOVERY_REQUIRED']);
const FINAL=new Set(['PUBLISHED','REJECTED']);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const keys=(value,names)=>object(value)&&Object.keys(value).length===names.length&&names.every(name=>Object.hasOwn(value,name));
const text=value=>typeof value==='string'&&value.length>0&&value.length<=4096&&!/[\u0000-\u001f\u007f]/.test(value);
const identity=value=>text(value)&&value.length<=512;
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const clone=value=>structuredClone(value);
const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const encode=value=>JSON.stringify(canonical(value));
const same=(a,b)=>encode(a)===encode(b);
const fail=()=>{throw new Error('invalid_disable_view');};
const errorCode=error=>/^[a-zA-Z0-9_:-]{1,100}$/.test(error?.message??'')?error.message:'result_unknown';
const accessError=error=>['access_changed','source_not_allowed','target_not_allowed','action_unavailable'].includes(errorCode(error));
const cardKey=(source,id)=>JSON.stringify([source,id]);
const unique=values=>new Set(values.map(encode)).size===values.length;
const sameSet=(left,right)=>same(left.map(encode).sort(),right.map(encode).sort());
function ref(value,type){
  if(!keys(value,['entity_type','logical_id','version_id'])||value.entity_type!==type||!text(value.logical_id)||!text(value.version_id))fail();
  return clone(value);
}
function refs(value,type,{finite=false}={}){
  if(!Array.isArray(value)||(finite&&(value.length<1||value.length>64)))fail();
  const result=value.map(item=>ref(item,type));if(!unique(result))fail();return result;
}
function execution(value){
  if(!keys(value,['task_ref','task_round_ref','net_instance_ref']))fail();
  return {task_ref:ref(value.task_ref,'task/v1'),task_round_ref:ref(value.task_round_ref,'task_round/v1'),
    net_instance_ref:ref(value.net_instance_ref,'net_instance/v1')};
}
function planBody(value){
  if(!keys(value,['protocol','source_task_ref','source_cut','target_resource_refs','address_bindings',
      'observed_active_firing_refs','latest_checkpoint_ref','carrier_refs','carrier_inventory_complete','purge_requested'])
      ||value.protocol!==PLAN||!integer(value.source_cut)||value.carrier_inventory_complete!==false||value.purge_requested!==false)fail();
  const task=ref(value.source_task_ref,'task/v1'),targets=refs(value.target_resource_refs,'resource_version/v1',{finite:true});
  const carriers=refs(value.carrier_refs,'resource_version/v1',{finite:true});
  if(!sameSet(carriers,targets))fail();
  refs(value.observed_active_firing_refs,'transition_firing/v1');ref(value.latest_checkpoint_ref,'marking_checkpoint/v1');
  if(!Array.isArray(value.address_bindings))fail();
  const addresses=value.address_bindings.map(item=>{
    if(!keys(item,['address','expected_binding_ref','target_resource_ref'])||!keys(item.address,['scope_ref','opaque_name'])
        ||!same(ref(item.address.scope_ref,'task/v1'),task)||!text(item.address.opaque_name)
        ||!targets.some(target=>same(target,ref(item.target_resource_ref,'resource_version/v1'))))fail();
    ref(item.expected_binding_ref,'resource_address_binding/v1');return item.address;
  });
  if(!unique(addresses))fail();return clone(value);
}
function commandParams(value,planRef,plan){
  if(!keys(value,['protocol','operation','target','execution','confirmed_plan_ref'])||value.protocol!==COMMAND
      ||value.operation!=='disable_versions'||!text(value.target)||!same(ref(value.confirmed_plan_ref,'resource_version/v1'),planRef)
      ||!same(execution(value.execution).task_ref,plan.source_task_ref))fail();
  return clone(value);
}
function catalogView(reply,source,selected){
  if(!object(reply)||reply.schema_version!==DISABLE_SCHEMA||reply.action!=='disable_catalog'||reply.source_id!==source
      ||typeof reply.supported!=='boolean'||typeof reply.prepare_allowed!=='boolean'||reply.body_mode!=='metadata_only'
      ||!(reply.reason===null||text(reply.reason))||!Array.isArray(reply.targets)
      ||!(reply.availability===null||['available','disabled','unknown'].includes(reply.availability)))fail();
  if(reply.as_of_ordinal===null){
    if(reply.supported||reply.prepare_allowed||reply.targets.length||reply.plan_params!==null||reply.resource_ref!==null)fail();
    return clone(reply);
  }
  if(!integer(reply.as_of_ordinal)||!same(reply.resource_ref===null?null:ref(reply.resource_ref,'resource_version/v1'),selected))fail();
  const targets=reply.targets.map(item=>{
    if(!keys(item,['id','label','target','execution'])||!text(item.id)||!text(item.label)||!text(item.target))fail();
    execution(item.execution);return clone(item);
  });
  if(!unique(targets.map(item=>item.id))||(!reply.supported&&targets.length))fail();
  if(reply.prepare_allowed){
    if(!reply.supported||!targets.length||selected===null||reply.availability!=='available'
        ||!keys(reply.plan_params,['protocol','target_resource_refs'])||reply.plan_params.protocol!==PLAN
        ||!same(refs(reply.plan_params.target_resource_refs,'resource_version/v1',{finite:true}),[selected]))fail();
  }else if(reply.plan_params!==null)fail();
  return clone(reply);
}
function planView(reply,source,id,previous=null,requested=null){
  if(!object(reply)||reply.schema_version!==DISABLE_SCHEMA||reply.action!=='read_disable_plan'||reply.source_id!==source
      ||reply.plan_command_id!==id||!integer(reply.as_of_ordinal)||!Array.isArray(reply.submit_targets)
      ||!(reply.reason===null||text(reply.reason))||!['current','changed','unknown'].includes(reply.current_scope))fail();
  if(reply.status==='NOT_FOUND_AT_READ_CUT'){
    if(reply.plan_ref!==null||reply.plan!==null||reply.submit_targets.length||reply.delivery_observation!==null||reply.current_scope!=='unknown')fail();
    return clone(reply);
  }
  if(reply.status!=='PLAN_PREPARED')fail();
  const plan=planBody(reply.plan),planRef=ref(reply.plan_ref,'resource_version/v1');
  if(reply.as_of_ordinal<plan.source_cut||(requested&&!sameSet(requested,plan.target_resource_refs)))fail();
  if(previous&&(!same(previous.plan_ref,planRef)||!same(previous.plan,plan)))fail();
  const targets=reply.submit_targets.map(item=>{
    if(!keys(item,['id','label','params'])||!text(item.id)||!text(item.label))fail();
    commandParams(item.params,planRef,plan);return clone(item);
  });
  if(!unique(targets.map(item=>item.id))||(reply.current_scope!=='current'&&targets.length))fail();
  const delivery=reply.delivery_observation;
  if(!keys(delivery,['status','proof_refs','reason','authorizes_publication'])
      ||!['drained','in_use','unknown'].includes(delivery.status)||delivery.authorizes_publication!==false
      ||!(delivery.reason===null||text(delivery.reason))||!Array.isArray(delivery.proof_refs))fail();
  for(const proof of delivery.proof_refs){if(!text(proof?.entity_type))fail();ref(proof,proof.entity_type);}
  return clone(reply);
}
function receiptView(reply,id,previous=null,submitted=false){
  if(!object(reply)||reply.protocol!==(submitted?SUBMIT:RECEIPT)||reply.command_id!==id)fail();
  if(!submitted&&reply.status==='NOT_FOUND_AT_READ_CUT')return clone(reply);
  const commandRef=ref(reply.command_ref,'resource_version/v1');
  if(previous?.command_ref&&!same(previous.command_ref,commandRef))fail();
  if(submitted){if(reply.status!=='REGISTERED')fail();return clone(reply);}
  if(!ACTIVE.has(reply.status)&&!FINAL.has(reply.status))fail();
  const expected={admission_ref:'firing_admission/v1',firing_ref:'transition_firing/v1',invocation_ref:'invocation/v1',
    result_ref:'resource_version/v1',completion_ref:'firing_completion/v2',operation_result_ref:'operation_result/v1',rejection_ref:'firing_rejection/v1'};
  for(const [name,type] of Object.entries(expected))if(reply[name]!=null)ref(reply[name],type);
  const executionRefs=['admission_ref','firing_ref','invocation_ref'];
  for(const name of executionRefs)if(previous?.[name]&&reply[name]&&!same(previous[name],reply[name]))fail();
  const hasExecution=executionRefs.every(name=>reply[name]!=null);
  if(['ADMITTED','PREPARED','RECOVERY_REQUIRED'].includes(reply.status)&&!hasExecution)fail();
  if(reply.status==='PUBLISHED'){
    if(!hasExecution||reply.result_kind!=='versions_disabled'||reply.error!==null||!reply.result_ref||!reply.completion_ref||!reply.operation_result_ref
        ||!Array.isArray(reply.output_refs)||reply.output_refs.length!==1||!Array.isArray(reply.binding_refs)||reply.binding_refs.length!==0
        ||reply.rejection_ref!=null)fail();
    ref(reply.output_refs[0],'resource_version/v1');
  }else if(reply.status==='REJECTED'){
    if(!reply.result_ref||!object(reply.error)||!text(reply.error.code)||reply.completion_ref!=null||reply.operation_result_ref!=null
        ||(reply.output_refs!=null&&(!Array.isArray(reply.output_refs)||reply.output_refs.length))
        ||(reply.binding_refs!=null&&(!Array.isArray(reply.binding_refs)||reply.binding_refs.length)))fail();
    if(reply.result_kind==='not_admitted'){
      if(executionRefs.some(name=>reply[name]!=null)||reply.rejection_ref!=null||!text(reply.rejection_transaction_id)||reply.registered_completion_event_id!=null)fail();
    }else if((reply.result_kind!=null&&reply.result_kind!=='publish_rejected')||!hasExecution||!reply.rejection_ref
        ||!text(reply.registered_completion_event_id)||reply.rejection_transaction_id!=null)fail();
  }
  return clone(reply);
}
function commandView(reply,source,id,card){
  if(!object(reply)||reply.schema_version!==DISABLE_SCHEMA||reply.action!=='read_disable_receipt'||reply.source_id!==source
      ||reply.command_id!==id||!integer(reply.as_of_ordinal)||reply.status!==reply.receipt?.status)fail();
  const receipt=receiptView(reply.receipt,id,card?.receipt);
  if(reply.status==='NOT_FOUND_AT_READ_CUT'){
    if(reply.params!==null||reply.plan_ref!==null||reply.plan_command_id!==null||reply.plan!==null)fail();
    return clone(reply);
  }
  const plan=planBody(reply.plan),planRef=ref(reply.plan_ref,'resource_version/v1');
  if(!identity(reply.plan_command_id)||reply.as_of_ordinal<plan.source_cut)fail();
  commandParams(reply.params,planRef,plan);
  if(card?.params&&(!same(card.params,reply.params)||!same(card.plan_ref,planRef)
      ||!same(card.plan,plan)||card.plan_command_id!==reply.plan_command_id))fail();
  return {...clone(reply),receipt};
}

export function createMaterialDisableApi(fetcher=globalThis.fetch){
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
  return {catalog:(source,resource,signal)=>read('disable_catalog',{source_id:source,...resource?{resource_ref:resource}:{}},signal),
    prepare:request=>write('disable_plan',request),submit:request=>write('disable_submit',request),
    plan:(source,id)=>read('read_disable_plan',{source_id:source,plan_command_id:id}),
    receipt:(source,id)=>read('read_disable_receipt',{source_id:source,material_command_id:id})};
}
function commandIdentity(kind){
  if(typeof globalThis.crypto?.randomUUID!=='function')throw new Error('command_identity_unavailable');
  return `material-disable-${kind}:`+globalThis.crypto.randomUUID();
}

export function createMaterialDisable({api,makeCommandId=commandIdentity,confirmDisable=()=>false,onChange=()=>{},onDisabled=()=>{}}={}){
  if(!api||['catalog','prepare','submit','plan','receipt'].some(name=>typeof api[name]!=='function'))throw new Error('DISABLE_API_REQUIRED');
  let source=null,selected=null,externalDenied=false,denied=false,catalog=null,loading=false,error=null,generation=0,catalogSerial=0;
  let clock=0,catalogObservation=0,catalogController=null,activePlan=null,lookup=null;
  const plans=new Map(),commands=new Map(),used=new Set(),notified=new Set(),lookups=new Map(),accessRevisions=new Map();
  const lookupPending=()=>[...lookups.values()].some(value=>value===source);
  const current=()=>[...commands.values()].filter(card=>card.source_id===source);
  const pendingPlan=()=>[...plans.values()].some(card=>card.source_id===source&&['PREPARING_PLAN','PLAN_UNKNOWN'].includes(card.status));
  const unresolved=()=>current().some(card=>!FINAL.has(card.status));
  const reviewAfter=()=>Math.max(0,...current().map(card=>card.review_after??0));
  const available=()=>Boolean(source&&!denied&&!externalDenied);
  const canPrepare=()=>Boolean(available()&&!loading&&!lookupPending()&&catalog?.prepare_allowed&&!pendingPlan()&&!unresolved()
    &&catalogObservation>reviewAfter()&&activePlan===null);
  function canConfirm(card){
    return Boolean(available()&&!loading&&!lookupPending()&&card.source_id===source&&activePlan===card.plan_command_id
      &&card.status==='PLAN_PREPARED'&&!card.busy&&!card.confirming&&!card.command_id&&card.view?.current_scope==='current'
      &&card.view.submit_targets.length&&card.observation>reviewAfter()&&!unresolved()&&!pendingPlan());
  }
  function getState(){
    const visible=available();
    return {source,resource_ref:clone(selected),catalog:clone(catalog),loading,error,can_load:visible&&!loading,
      can_prepare:canPrepare(),can_lookup:visible&&!lookupPending(),lookup_loading:lookupPending(),lookup:clone(lookup),active_plan_id:activePlan,
      blocked:visible&&(pendingPlan()||unresolved()||catalogObservation<=reviewAfter()),
      plans:visible?[...plans.values()].filter(card=>card.source_id===source).map(card=>({...clone(card),can_confirm:canConfirm(card),can_query:!card.busy})):[],
      commands:visible?current().map(card=>({...clone(card),can_query:!card.querying})):[]};
  }
  const emit=()=>onChange(getState());
  function scrub(original){
    accessRevisions.set(original,(accessRevisions.get(original)??0)+1);
    for(const card of plans.values())if(card.source_id===original){card.view=null;card.requested=null;card.status='PLAN_UNKNOWN';card.observation=0;}
    for(const card of commands.values())if(card.source_id===original){card.params=null;card.plan=null;card.plan_ref=null;card.receipt=null;card.status='RESULT_UNKNOWN';card.review_after=null;}
  }
  function invalidate(){++generation;++catalogSerial;catalogController?.abort();catalogController=null;loading=false;lookup=null;activePlan=null;catalog=null;catalogObservation=0;++clock;}
  function select(nextSource,resource=null,accessError=false){
    const next=typeof nextSource==='string'&&nextSource?nextSource:null;let exact=null,invalid=Boolean(accessError);
    try{if(resource!==null)exact=ref(resource,'resource_version/v1');}catch{invalid=true;}
    if(next===source&&same(exact,selected)&&invalid===externalDenied)return;
    if(invalid&&next!==null)scrub(next);
    invalidate();source=next;selected=exact;externalDenied=invalid;denied=invalid;error=invalid?'access_changed':null;emit();
  }
  function noteError(cause,card=null,plan=false,original=source){
    const code=errorCode(cause);if(original===source)error=code;
    if(card){card.error=code;if(plan)card.status='PLAN_UNKNOWN';else if(!FINAL.has(card.status))card.status='RESULT_UNKNOWN';}
    // A denied A request remains evidence about A after navigation to B.
    // Clear A's retained facts and invalidate A's other in-flight reads,
    // without clearing B's catalog, notice, or independent work.
    if(accessError(cause)){
      scrub(original);if(original===source){invalidate();denied=true;error=code;}
    }
  }
  async function loadCatalog(){
    if(!available()||loading)return;
    const original=source,selection=clone(selected),epoch=generation,serial=++catalogSerial,observation=++clock;
    catalogController=new AbortController();catalog=null;catalogObservation=0;activePlan=null;loading=true;error=null;emit();
    try{
      const value=await api.catalog(original,selection,catalogController.signal);
      if(epoch!==generation||serial!==catalogSerial)return;
      catalog=catalogView(value,original,selection);catalogObservation=observation;
    }catch(cause){if((epoch===generation&&serial===catalogSerial)||accessError(cause)){
      noteError(cause,null,false,original);
      // Revocation invalidates the request generation. Render its cleared
      // facts now; the superseded request's finally must not resurrect them.
      emit();
    }}
    finally{if(epoch===generation&&serial===catalogSerial){loading=false;catalogController=null;emit();}}
  }
  function newIdentity(kind){
    const id=makeCommandId(kind),key=cardKey(source,id);if(!identity(id)||used.has(key))throw new Error('command_identity_unavailable');
    used.add(key);return id;
  }
  function acceptPlan(card,raw,observation,activate=true){
    const value=planView(raw,card.source_id,card.plan_command_id,card.view,card.requested);
    if(value.status==='NOT_FOUND_AT_READ_CUT'){card.status='PLAN_UNKNOWN';card.error='NOT_FOUND_AT_READ_CUT';return value;}
    card.view=value;card.status='PLAN_PREPARED';card.error=null;card.observation=observation;
    if(activate&&card.source_id===source)activePlan=card.plan_command_id;return value;
  }
  async function prepare(){
    if(!canPrepare())return;
    const id=newIdentity('plan'),original=source,epoch=generation,params=clone(catalog.plan_params),observation=++clock;
    const card={source_id:original,plan_command_id:id,status:'PREPARING_PLAN',requested:clone(params.target_resource_refs),view:null,
      observation:0,command_id:null,busy:true,confirming:false,error:null,recovered:false};
    plans.set(cardKey(original,id),card);emit();
    try{const reply=await api.prepare({source_id:original,owner_command:'prepare_material_disable_plan',command_id:id,params});
      if(epoch===generation)acceptPlan(card,reply,observation);else card.status='PLAN_UNKNOWN';
    }catch(cause){if(epoch===generation||accessError(cause))noteError(cause,card,true,original);else card.status='PLAN_UNKNOWN';}
    finally{card.busy=false;emit();}
  }
  function applyReceipt(card,view){
    if(view.status==='NOT_FOUND_AT_READ_CUT'){
      if(!FINAL.has(card.status)){card.status='RESULT_UNKNOWN';card.error='NOT_FOUND_AT_READ_CUT';}return;
    }
    const receipt=view.receipt;
    if(FINAL.has(card.status)){
      if(!FINAL.has(receipt.status))return;
      if(!same(card.receipt,receipt))fail();
    }
    const previous=card.receipt;
    if(previous?.admission_ref&&receipt.status==='REGISTERED')return;
    if(previous?.admission_ref&&receipt.result_kind==='not_admitted')fail();
    const order=['REGISTERED','ADMITTED','PREPARED'];
    if(order.includes(previous?.status)&&order.includes(receipt.status)&&order.indexOf(receipt.status)<order.indexOf(previous.status))return;
    if(!FINAL.has(card.status)&&FINAL.has(receipt.status))card.review_after=++clock;
    Object.assign(card,{params:clone(view.params),plan:clone(view.plan),plan_ref:clone(view.plan_ref),plan_command_id:view.plan_command_id,
      receipt:clone(receipt),status:receipt.status,error:null});
    if(receipt.status==='PUBLISHED'&&!notified.has(cardKey(card.source_id,card.command_id))){
      notified.add(cardKey(card.source_id,card.command_id));
      onDisabled(clone({source_id:card.source_id,target_resource_refs:card.plan.target_resource_refs,command_id:card.command_id}));
    }
  }
  async function confirmPlan(id,targetId=null){
    const card=plans.get(cardKey(source,id));if(!card||!canConfirm(card))return;
    const target=card.view.submit_targets.find(item=>item.id===(targetId??card.view.submit_targets[0]?.id));if(!target)return;
    const original=source,epoch=generation,view=card.view,observation=card.observation;
    card.confirming=true;error=null;emit();let confirmed=false;
    try{confirmed=await confirmDisable(clone({source_id:source,plan_command_id:id,plan_ref:view.plan_ref,plan:view.plan,
      delivery_observation:view.delivery_observation,target}));}
    catch(cause){if(epoch===generation)error=errorCode(cause);}
    finally{card.confirming=false;emit();}
    if(confirmed!==true||epoch!==generation||card.view!==view||card.observation!==observation||!canConfirm(card))return;
    const commandId=newIdentity('command');card.command_id=commandId;
    const command={source_id:original,command_id:commandId,plan_command_id:id,params:clone(target.params),plan_ref:clone(view.plan_ref),
      plan:clone(view.plan),receipt:null,status:'SUBMITTING',sending:true,querying:false,error:null,review_after:null,recovered:false};
    commands.set(cardKey(original,commandId),command);emit();
    try{
      const raw=await api.submit({source_id:original,owner_command:'material_submit',command_id:commandId,params:clone(target.params)});
      if(epoch!==generation){if(!FINAL.has(command.status))command.status='RESULT_UNKNOWN';return;}
      const receipt=receiptView(raw,commandId,command.receipt,true);
      if(!command.receipt||command.receipt.protocol!==RECEIPT){command.receipt=receipt;command.status='REGISTERED';command.error=null;}
    }catch(cause){if(epoch===generation||accessError(cause))noteError(cause,command,false,original);else if(!FINAL.has(command.status))command.status='RESULT_UNKNOWN';}
    finally{command.sending=false;emit();}
  }
  async function queryPlan(id){
    if(!available()||lookupPending()||!identity(id))return;
    const original=source,key=cardKey(original,id),existing=plans.get(key);if(existing?.busy)return;
    const card=existing??{source_id:original,plan_command_id:id,status:'PLAN_UNKNOWN',requested:null,view:null,observation:0,
      command_id:null,busy:false,confirming:false,error:null,recovered:true};
    const epoch=generation,access=accessRevisions.get(original)??0,observation=++clock,requestKey='plan:'+key;
    card.busy=true;used.add(key);lookups.set(requestKey,original);lookup=null;error=null;emit();
    try{
      const raw=await api.plan(original,id);if(access!==(accessRevisions.get(original)??0))return;
      const value=acceptPlan(card,raw,observation,epoch===generation);
      if(value.status!=='NOT_FOUND_AT_READ_CUT')plans.set(key,card);
      if(epoch===generation)lookup=clone(value);
    }catch(cause){if(epoch===generation||accessError(cause))noteError(cause,existing??null,true,original);}
    finally{card.busy=false;lookups.delete(requestKey);emit();}
  }
  async function query(id){
    if(!available()||lookupPending()||!identity(id))return;
    const original=source,key=cardKey(original,id),existing=commands.get(key);if(existing?.querying)return;
    const card=existing??{source_id:original,command_id:id,plan_command_id:null,params:null,plan_ref:null,plan:null,receipt:null,
      status:'RESULT_UNKNOWN',sending:false,querying:false,error:null,review_after:null,recovered:true};
    const epoch=generation,access=accessRevisions.get(original)??0,requestKey='command:'+key;
    card.querying=true;used.add(key);lookups.set(requestKey,original);lookup=null;error=null;emit();
    try{
      const raw=await api.receipt(original,id);if(access!==(accessRevisions.get(original)??0))return;
      const value=commandView(raw,original,id,card);applyReceipt(card,value);
      if(value.status!=='NOT_FOUND_AT_READ_CUT')commands.set(key,card);
      if(epoch===generation)lookup=clone(value);
    }catch(cause){if(epoch===generation||accessError(cause))noteError(cause,existing??null,false,original);}
    finally{card.querying=false;lookups.delete(requestKey);emit();}
  }
  return {getState,select,loadCatalog,prepare,confirmPlan,queryPlan,query,
    readEarlier:(kind,id)=>kind==='plan'?queryPlan(id):kind==='command'?query(id):undefined,
    close(){select(null);}};
}

const STRINGS={
  en:{title:'Disable exact versions',hint:'Prepare an exact version plan, review its scope, then explicitly confirm the original command. Existing positions and history remain.',
    load:'Check disable actions',prepare:'Prepare disable plan',choose:'Choose a readable local source.',unchecked:'Check the current exact selection before preparing a plan.',
    loading:'Reading permitted disable targets…',closed:'Disable management is not enabled for this selection.',ready:'Prepare the selected exact version for review. No version is disabled by preparing a plan.',
    blocked:'An original outcome is unresolved or needs a fresh observation. Query its original identity, then check current actions after the result.',
    source:'Source',version:'Exact version',context:'Current selection',plans:'Original confirmation plans',commands:'Original disable commands',
    queryPlan:'Query original plan',confirm:'Confirm this disable plan',query:'Query original disable result',facts:'Exact original facts',
    lookup:'Find an earlier plan or disable command',kind:'Identity kind',planKind:'Plan preparation',commandKind:'Disable command',id:'Original stable identity',read:'Read original status',
    lookupHint:'Queries the original source and identity. It never prepares, resubmits, or retries a command.',
    planRetained:'This original plan is retained. It does not disable a version or authorize physical cleanup.',
    scopeChanged:'The original scope or current target changed. This plan cannot be submitted from this observation.',
    drain:{drained:'No pending delivery was proven at this read cut. The original final publication gate will check again.',in_use:'An original delivery is still in use. This is not a wait queue; the original command may be refused.',unknown:'Delivery state is unconfirmed. The original publication gate remains authoritative.'},
    confirmText:'Disable only the exact versions in this original plan? This blocks future body access under the original policy. Positions, history and retained carriers remain; this does not physically delete them.',
    targets:'Confirmed exact versions',addresses:'Observed positions',active:'Observed active firings',checkpoint:'Observed checkpoint',carriers:'Physical carrier inventory is incomplete; no purge is requested.',
    unknown:'Outcome is unconfirmed. Query this original identity; do not create a replacement request.',error:'Request could not be confirmed:',
    status:{PREPARING_PLAN:'Preparing original confirmation plan',PLAN_UNKNOWN:'Original plan outcome unknown',PLAN_PREPARED:'Plan prepared; disablement requires the original command result',
      SUBMITTING:'Submitting original disable command',RESULT_UNKNOWN:'Original disable result unknown',REGISTERED:'Registered; waiting for original PN admission',
      ADMITTED:'Admitted; disable effect is not published',PREPARED:'Effect prepared; disable is unconfirmed',RECOVERY_REQUIRED:'Original Owner recovery required',
      PUBLISHED:'Original Owner confirmed these exact versions disabled',REJECTED:'Not disabled by this command; original rejection recorded'}},
  'zh-CN':{title:'停用精确版本',hint:'先准备精确版本计划，复核范围后再明确确认原命令；已有位置和历史仍保留。',
    load:'查看停用操作',prepare:'准备停用计划',choose:'请选择可读的本地来源。',unchecked:'请先查看当前精确选择的停用操作，再准备计划。',
    loading:'正在读取允许的停用目标…',closed:'此选择未开放停用管理。',ready:'可为当前精确版本准备复核计划；准备计划本身不会停用版本。',
    blocked:'原结果尚未确认，或仍需新观察。请回查原标识，并在确认结果后重新查看当前操作。',
    source:'来源',version:'精确版本',context:'当前选择',plans:'原确认计划',commands:'原停用命令',
    queryPlan:'回查原计划',confirm:'确认此停用计划',query:'回查原停用结果',facts:'精确原始事实',
    lookup:'查找此前的计划或停用命令',kind:'标识类型',planKind:'计划准备',commandKind:'停用命令',id:'原稳定标识',read:'读取原状态',
    lookupHint:'仅查询原来源和标识，不准备计划、不重新提交、不重试命令。',
    planRetained:'原计划继续保留；它本身不会停用版本，也不授权物理清理。',
    scopeChanged:'原确认范围或当前目标已变化；此观察下不能提交该计划。',
    drain:{drained:'本次读取位置已证明没有待完成的交付；原最终发布门仍会重新检查。',in_use:'原交付仍在使用中。这不是等待队列；原命令仍可能被拒绝。',unknown:'交付状态尚未确认；是否允许发布仍由原门决定。'},
    confirmText:'只停用原计划中的这些精确版本？按原规则关闭后续正文访问；已有位置、历史和保留载体仍存在，本操作不做物理删除。',
    targets:'确认的精确版本',addresses:'观察到的位置',active:'观察到的活动 firing',checkpoint:'观察到的 checkpoint',carriers:'物理载体清单不完整；未请求物理清理。',
    unknown:'结果未确认。请回查同一原标识，不另建替代请求。',error:'本次请求未能确认：',
    status:{PREPARING_PLAN:'正在准备原确认计划',PLAN_UNKNOWN:'原计划准备结果未知',PLAN_PREPARED:'计划已准备；停用结果以原命令回执为准',
      SUBMITTING:'正在提交原停用命令',RESULT_UNKNOWN:'原停用结果未知',REGISTERED:'已登记，等待原 PN 准入',
      ADMITTED:'已准入，停用效果尚未发布',PREPARED:'效果已准备，尚未确认停用',RECOVERY_REQUIRED:'需要原 Owner 恢复',
      PUBLISHED:'原 Owner 已确认停用这些精确版本',REJECTED:'本命令未完成停用，原拒绝已记录'}},
};
function selectedResource(state){
  const entry=state.record?.entry_ref;if(!entry||entry.source_id!==state.source)return null;
  if(entry.schema_version==='rpnh/collaboration/source_resource_ref/v1'&&keys(entry.ref,['resource_id','resource_version_id']))
    return ref({entity_type:'resource_version/v1',logical_id:entry.ref.resource_id,version_id:entry.ref.resource_version_id},'resource_version/v1');
  if(entry.schema_version==='rpnh/collaboration/source_version_ref/v1'&&entry.ref?.entity_type==='resource_version/v1')return ref(entry.ref,'resource_version/v1');
  return null;
}
export function mountMaterialDisable(document,{root,fetcher=globalThis.fetch,makeCommandId,
    confirmDisable=message=>document.defaultView?.confirm?.(message)===true,onDisabled=()=>{}}={}){
  if(!root)throw new Error('DISABLE_PANEL_ROOT_REQUIRED');
  let language='en',renderedIdentity='';const nodes={},labels=[],views=new Map();
  const t=name=>STRINGS[language][name]??name;
  const el=(tag,name)=>{const node=document.createElement(tag);if(name)nodes[name]=node;return node;};
  const label=(node,name)=>{labels.push([node,name]);node.textContent=t(name);return node;};
  const invoke=action=>Promise.resolve().then(action).catch(cause=>{nodes.notice.textContent=`${t('error')} ${errorCode(cause)}`;});
  const button=(name,action)=>{const node=label(el('button',name),name);node.type='button';node.addEventListener('click',()=>invoke(action));return node;};
  root.className='material-management material-disable';
  root.append(label(el('h2'),'title'),label(el('p'),'hint'),button('load',()=>controller.loadCatalog()),
    button('prepare',()=>controller.prepare()),el('p','notice'));
  nodes.notice.setAttribute('role','status');nodes.notice.setAttribute('aria-live','polite');
  const context=el('details');context.append(label(el('summary'),'context'),el('pre','context'));root.append(context);
  root.append(label(el('h3'),'plans'),el('div','plans'),label(el('h3'),'commands'),el('div','commands'));
  const lookup=el('details'),kind=el('select','lookupKind'),id=el('input','lookupId');lookup.append(label(el('summary'),'lookup'));
  for(const [value,name] of [['plan','planKind'],['command','commandKind']]){const option=label(el('option'),name);option.value=value;kind.append(option);}
  kind.value='command';id.type='text';id.maxLength=512;
  const kindLabel=el('label'),idLabel=el('label');kindLabel.append(label(el('span'),'kind'),kind);idLabel.append(label(el('span'),'id'),id);
  lookup.append(kindLabel,idLabel,button('read',()=>controller.readEarlier(kind.value,id.value)),label(el('p'),'lookupHint'),el('pre','lookupResult'));root.append(lookup);
  function cardNodes(kind,card){
    const id=kind==='plan'?card.plan_command_id:card.command_id,key=JSON.stringify([kind,card.source_id,id]);
    let view=views.get(key);if(view)return view;
    const article=el('article'),title=el('h4'),status=el('p'),hint=el('p'),error=el('p'),summary=el('summary'),facts=el('pre'),details=el('details');
    article.className='material-command';details.append(summary,facts);
    const query=el('button');query.type='button';query.addEventListener('click',()=>invoke(()=>kind==='plan'?controller.queryPlan(id):controller.query(id)));
    const targets=el('div');targets.className='material-actions';article.append(title,status,hint,error,query,targets,details);
    nodes[kind==='plan'?'plans':'commands'].append(article);view={title,status,hint,error,summary,facts,query,targets,targetIds:'',buttons:new Map()};views.set(key,view);return view;
  }
  function render(state){
    for(const [node,name] of labels)node.textContent=t(name);
    nodes.load.disabled=!state.can_load;nodes.prepare.disabled=!state.can_prepare;nodes.read.disabled=!state.can_lookup;
    const status=!state.source?'choose':state.loading?'loading':state.blocked?'blocked':!state.catalog?'unchecked':state.catalog.prepare_allowed?'ready':'closed';
    nodes.notice.textContent=state.error?`${t('error')} ${state.error}`:t(status)+(state.catalog?.reason?' '+state.catalog.reason:'');
    nodes.context.textContent=JSON.stringify({source_id:state.source,resource_ref:state.resource_ref,as_of_ordinal:state.catalog?.as_of_ordinal??null},null,2);
    nodes.lookupResult.textContent=state.lookup?JSON.stringify(state.lookup,null,2):'';
    const identities=JSON.stringify([state.plans.map(card=>[card.source_id,card.plan_command_id]),state.commands.map(card=>[card.source_id,card.command_id])]);
    if(identities!==renderedIdentity){renderedIdentity=identities;views.clear();nodes.plans.replaceChildren();nodes.commands.replaceChildren();}
    for(const card of state.plans){
      const view=cardNodes('plan',card);view.title.textContent=`${card.source_id} · ${card.plan_command_id}`;
      view.status.textContent=STRINGS[language].status[card.status]??card.status;
      view.hint.textContent=card.view?(card.view.current_scope==='current'?t('planRetained'):t('scopeChanged'))+' '+t('carriers')+' '+STRINGS[language].drain[card.view.delivery_observation.status]:t('unknown');
      view.error.textContent=card.error?`${t('error')} ${card.error}`:'';view.summary.textContent=t('facts');
      view.facts.textContent=JSON.stringify({source_id:card.source_id,plan_command_id:card.plan_command_id,command_id:card.command_id,view:card.view},null,2);
      view.query.textContent=t('queryPlan');view.query.disabled=!card.can_query;
      const targets=card.view?.submit_targets??[],ids=JSON.stringify(targets.map(item=>item.id));
      if(ids!==view.targetIds){view.targetIds=ids;view.targets.replaceChildren();view.buttons.clear();
        for(const target of targets){const node=el('button');node.type='button';node.addEventListener('click',()=>invoke(()=>controller.confirmPlan(card.plan_command_id,target.id)));
          view.targets.append(node);view.buttons.set(target.id,node);}}
      for(const target of targets){const node=view.buttons.get(target.id);node.textContent=`${t('confirm')} · ${target.label}`;node.disabled=!card.can_confirm;}
    }
    for(const card of state.commands){
      const view=cardNodes('command',card);view.title.textContent=`${card.source_id} · ${card.command_id}`;
      view.status.textContent=STRINGS[language].status[card.status]??card.status;
      view.hint.textContent=card.status==='RESULT_UNKNOWN'?t('unknown'):'';view.error.textContent=card.error?`${t('error')} ${card.error}`:'';
      view.summary.textContent=t('facts');view.facts.textContent=JSON.stringify({source_id:card.source_id,command_id:card.command_id,
        plan_command_id:card.plan_command_id,plan_ref:card.plan_ref,params:card.params,plan:card.plan,receipt:card.receipt},null,2);
      view.query.textContent=t('query');view.query.disabled=!card.can_query;
    }
  }
  const controller=createMaterialDisable({api:createMaterialDisableApi(fetcher),makeCommandId,onChange:render,onDisabled,
    confirmDisable:selection=>confirmDisable(`${t('confirmText')}\n\n${t('source')}: ${selection.source_id}\n${t('targets')}:\n${selection.plan.target_resource_refs.map(item=>`${item.logical_id} · ${item.version_id}`).join('\n')}\n${t('addresses')}:\n${selection.plan.address_bindings.map(item=>`${item.address.opaque_name} · ${item.expected_binding_ref.version_id} → ${item.target_resource_ref.version_id}`).join('\n')}\n${t('active')}:\n${selection.plan.observed_active_firing_refs.map(item=>item.version_id).join('\n')}\n${t('checkpoint')}: ${selection.plan.latest_checkpoint_ref.version_id}\n${t('carriers')}\n${STRINGS[language].drain[selection.delivery_observation.status]}`)});
  document.defaultView?.addEventListener?.('pagehide',()=>controller.close());render(controller.getState());
  return {controller,nodes,setLanguage(value){language=value==='zh-CN'?'zh-CN':'en';render(controller.getState());},
    setBrowserState(state){let selected=null,invalid=Boolean(state.error);
      const source=state.sources?.find(item=>item.source_id===state.source);
      invalid ||= state.source!==null&&(!source||source.access_state!=='readable'||source.access_mode!=='local_owner');
      try{selected=selectedResource(state);}catch{invalid=true;}
      controller.select(state.source,selected,invalid);
    }};
}
