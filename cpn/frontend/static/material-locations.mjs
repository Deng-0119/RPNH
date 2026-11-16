/** Explicit metadata-only location inspection and original bind/unbind commands.
 *
 * Cards retain the original source, command identity and frozen HOST target.
 * They neither authorize an action nor infer completion from a local change.
 * Unknown outcomes are queried, never resubmitted under another identity.
 */
export const LOCATION_SCHEMA = 'rpnh/viewer_material_locations/v1';
const COMMAND = 'rpnh/material_command/v1';
const SUBMIT = 'rpnh/material_submit_receipt/v1';
const RECEIPT = 'rpnh/material_receipt_view/v1';
const ACTIVE = new Set(['REGISTERED', 'ADMITTED', 'PREPARED', 'RECOVERY_REQUIRED']);
const FINAL = new Set(['PUBLISHED', 'REJECTED']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 4096 && !value.includes('\0');
const copy = value => structuredClone(value);
const same = (left,right) => JSON.stringify(left) === JSON.stringify(right);
const fail = code => { throw new Error(code); };
const key = (source,id) => JSON.stringify([source,id]);
const code = error => /^[a-zA-Z0-9_:-]{1,100}$/.test(error?.message ?? '') ? error.message : 'result_unknown';

function exactKeys(value, keys) {
  return object(value) && Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}
function ref(value,type) {
  if (!exactKeys(value,['entity_type','logical_id','version_id']) || value.entity_type !== type
      || !text(value.logical_id) || !text(value.version_id)) fail('INVALID_LOCATION_REFERENCE');
  return {entity_type:type,logical_id:value.logical_id,version_id:value.version_id};
}
function address(value) {
  if (!exactKeys(value,['scope_ref','opaque_name']) || !text(value.opaque_name)) fail('INVALID_LOCATION_ADDRESS');
  return {scope_ref:ref(value.scope_ref,'task/v1'),opaque_name:value.opaque_name};
}
function unbindTarget(value) {
  if (!object(value) || !text(value.id) || !text(value.label)
      || !exactKeys(value.params,['protocol','operation','target','execution','address','expected_binding_ref'])) {
    fail('INVALID_LOCATION_TARGET');
  }
  const params = value.params;
  if (params.protocol !== COMMAND || params.operation !== 'unbind' || !text(params.target)
      || !exactKeys(params.execution,['task_ref','task_round_ref','net_instance_ref'])) fail('INVALID_LOCATION_TARGET');
  const execution = {task_ref:ref(params.execution.task_ref,'task/v1'),
    task_round_ref:ref(params.execution.task_round_ref,'task_round/v1'),
    net_instance_ref:ref(params.execution.net_instance_ref,'net_instance/v1')};
  const location = address(params.address);
  if (!same(location.scope_ref,execution.task_ref)) fail('INVALID_LOCATION_SCOPE');
  return {id:value.id,label:value.label,resource_ref:ref(value.resource_ref,'resource_version/v1'),
    params:{protocol:COMMAND,operation:'unbind',target:params.target,execution,address:location,
      expected_binding_ref:ref(params.expected_binding_ref,'resource_address_binding/v1')}};
}

function bindTarget(value,selected) {
  if (!exactKeys(value,['id','label','address','binding_ref','tombstoned','resource_ref','params'])
      || !text(value.id) || !text(value.label) || typeof value.tombstoned!=='boolean'
      || selected===null || !exactKeys(value.params,
        ['protocol','operation','target','execution','address','expected_binding_ref','target_resource_ref'])) {
    fail('INVALID_LOCATION_BIND_TARGET');
  }
  const params=value.params;
  if (params.protocol!==COMMAND || params.operation!=='bind' || !text(params.target)
      || !exactKeys(params.execution,['task_ref','task_round_ref','net_instance_ref'])) fail('INVALID_LOCATION_BIND_TARGET');
  const execution={task_ref:ref(params.execution.task_ref,'task/v1'),
    task_round_ref:ref(params.execution.task_round_ref,'task_round/v1'),
    net_instance_ref:ref(params.execution.net_instance_ref,'net_instance/v1')};
  const position=address(value.address),targetAddress=address(params.address);
  const resource=ref(value.resource_ref,'resource_version/v1');
  const requested=ref(params.target_resource_ref,'resource_version/v1');
  const binding=value.binding_ref===null?null:ref(value.binding_ref,'resource_address_binding/v1');
  const expected=params.expected_binding_ref===null?null:ref(params.expected_binding_ref,'resource_address_binding/v1');
  if (!same(position,targetAddress) || !same(position.scope_ref,execution.task_ref)
      || !same(resource,selected) || !same(requested,selected) || !same(binding,expected)
      || value.tombstoned!==(binding!==null)) fail('INVALID_LOCATION_BIND_TARGET');
  return {id:value.id,label:value.label,address:position,binding_ref:binding,
    tombstoned:value.tombstoned,resource_ref:resource,
    params:{protocol:COMMAND,operation:'bind',target:params.target,execution,address:targetAddress,
      expected_binding_ref:expected,target_resource_ref:requested}};
}

function catalogView(reply,source,selected) {
  if (!object(reply) || reply.schema_version !== LOCATION_SCHEMA || reply.action !== 'location_catalog'
      || reply.source_id !== source || typeof reply.supported !== 'boolean'
      || !(reply.reason === null || text(reply.reason)) || reply.body_mode !== 'metadata_only'
      || !Array.isArray(reply.positions) || !Array.isArray(reply.targets)
      || (Object.hasOwn(reply,'bind_targets')&&!Array.isArray(reply.bind_targets))
      || !Object.hasOwn(reply,'resource_ref')) fail('INVALID_LOCATION_CATALOG');
  const disclosedBindTargets=reply.bind_targets??[];
  // The existing unconfigured provider returns no read cut or selection. It
  // must stay a closed catalog, including when an exact resource is selected.
  if (!reply.supported && reply.as_of_ordinal===null) {
    if (reply.reason!=='management_not_configured' || reply.resource_ref!==null
        || reply.positions.length || reply.targets.length || disclosedBindTargets.length || !exactKeys(reply,
          ['schema_version','action','source_id','supported','reason','resource_ref',
            'as_of_ordinal','positions','targets','body_mode',...(Object.hasOwn(reply,'bind_targets')?['bind_targets']:[])])) fail('INVALID_LOCATION_CATALOG');
    return {...copy(reply),bind_targets:[]};
  }
  if (!Number.isSafeInteger(reply.as_of_ordinal) || reply.as_of_ordinal<0) fail('INVALID_LOCATION_CATALOG');
  const selectedRef = reply.resource_ref === null ? null : ref(reply.resource_ref,'resource_version/v1');
  if (!same(selectedRef,selected)) fail('LOCATION_SELECTION_CHANGED');
  const targets = reply.targets.map(unbindTarget);
  if (new Set(targets.map(item=>item.id)).size !== targets.length || (!reply.supported && targets.length)) {
    fail('INVALID_LOCATION_CATALOG');
  }
  const positions = reply.positions.map(item=>{
    if (!object(item) || !text(item.id) || !text(item.label) || typeof item.tombstoned !== 'boolean'
        || !Object.hasOwn(item,'resource_ref') || !Object.hasOwn(item,'binding_ref')
        || !(item.unbind_target_id === null || text(item.unbind_target_id))) fail('INVALID_LOCATION_POSITION');
    const position = {id:item.id,label:item.label,address:address(item.address),tombstoned:item.tombstoned,
      resource_ref:item.resource_ref === null ? null : ref(item.resource_ref,'resource_version/v1'),
      binding_ref:item.binding_ref === null ? null : ref(item.binding_ref,'resource_address_binding/v1'),
      unbind_target_id:item.unbind_target_id};
    if ((position.tombstoned && (position.resource_ref !== null || position.binding_ref === null))
        || (!position.tombstoned && position.resource_ref === null && position.binding_ref !== null)
        || (position.resource_ref !== null && position.binding_ref === null)
        || (selected !== null && !same(position.resource_ref,selected))) fail('INVALID_LOCATION_POSITION');
    if (position.unbind_target_id !== null) {
      const target = targets.find(target=>target.id === position.unbind_target_id);
      if (!reply.supported || !target || position.tombstoned || position.resource_ref === null
          || !same(target.params.address,position.address)
          || !same(target.params.expected_binding_ref,position.binding_ref)
          || !same(target.resource_ref,position.resource_ref)) fail('INVALID_LOCATION_TARGET');
    }
    return position;
  });
  if (new Set(positions.map(item=>item.id)).size !== positions.length
      || new Set(positions.map(item=>JSON.stringify(item.address))).size !== positions.length
      || targets.some(target=>positions.filter(item=>item.unbind_target_id===target.id).length !== 1)) {
    fail('INVALID_LOCATION_CATALOG');
  }
  const bindTargets=disclosedBindTargets.map(item=>bindTarget(item,selectedRef));
  if ((!reply.supported&&bindTargets.length)
      || new Set(bindTargets.map(item=>item.id)).size!==bindTargets.length
      || new Set(bindTargets.map(item=>JSON.stringify(item.address))).size!==bindTargets.length
      || bindTargets.some(target=>positions.some(position=>position.resource_ref!==null&&same(position.address,target.address)))) {
    fail('INVALID_LOCATION_BIND_TARGET');
  }
  return {schema_version:LOCATION_SCHEMA,action:'location_catalog',source_id:source,
    supported:reply.supported,reason:reply.reason,resource_ref:selectedRef,
    as_of_ordinal:reply.as_of_ordinal,positions,targets,bind_targets:bindTargets,body_mode:'metadata_only'};
}

export function createMaterialLocationsApi(fetcher=globalThis.fetch) {
  async function read(action,params,signal) {
    const query = Object.fromEntries(Object.entries(params).map(([name,value])=>
      [name,object(value)?JSON.stringify(value):value]));
    const response = await fetcher(`/api/material-management?${new URLSearchParams({action,...query})}`,
      {cache:'no-store',credentials:'same-origin',...(signal?{signal}:{})});
    const result = await response.json();
    if (!response.ok) throw new Error(result?.error ?? 'result_unknown');
    return result;
  }
  return {
    catalog:(source,resource,signal)=>read('location_catalog',
      {source_id:source,...(resource?{resource_ref:resource}:{})},signal),
    async submit(request) {
      // No abort signal: leaving a page never cancels the original Owner task.
      const response = await fetcher('/api/material-management?action=location_submit',
        {method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(request)});
      const result = await response.json();
      if (!response.ok) throw new Error(result?.error ?? 'result_unknown');
      return result;
    },
    receipt:(source,id)=>read('read_receipt',{source_id:source,material_command_id:id}),
  };
}

function commandIdentity(operation='unbind') {
  if (typeof globalThis.crypto?.randomUUID !== 'function') fail('command_identity_unavailable');
  return `material-location-${operation}:` + globalThis.crypto.randomUUID();
}

export function createMaterialLocations({api,makeCommandId=commandIdentity,confirmUnbind=()=>false,
    confirmBind=()=>false,onChange=()=>{}}={}) {
  if (!api || ['catalog','submit','receipt'].some(name=>typeof api[name]!=='function')) fail('LOCATION_API_REQUIRED');
  let source=null, selected=null, catalog=null, loading=false, error=null, epoch=0, revision=0, controller=null, denied=false;
  let catalogObservation=0, lookup=null, lookupSerial=0;
  const cards=new Map(), used=new Set(), confirming=new Set(), lookups=new Map();
  const emit=()=>onChange(getState());
  const contextKey=()=>JSON.stringify([source,selected]);
  const lookupPending=originalSource=>[...lookups.values()].includes(originalSource);
  const requiresReview=card=>!FINAL.has(card.status)||catalogObservation<=card.review_after;
  function targetFor(id,operation='unbind') {
    let position,target;
    if (operation==='bind') {
      target=catalog?.bind_targets.find(item=>item.id===id);
      if (target) position={id:target.id,label:target.label,address:target.address,
        binding_ref:target.binding_ref,tombstoned:target.tombstoned,resource_ref:null,unbind_target_id:null};
    } else {
      position=catalog?.positions.find(item=>item.id===id);
      target=catalog?.targets.find(item=>item.id===position?.unbind_target_id);
    }
    if (!catalog?.supported || !position || !target || loading || denied || lookupPending(source)) return null;
    for (const card of cards.values()) {
      if (card.source_id!==source) continue;
      // A recovered receipt does not disclose its operation or address. Until
      // its original outcome is final and then freshly observed, guard the
      // source instead of guessing which visible position belongs to it.
      if (card.target===null) { if (requiresReview(card)) return null; continue; }
      if (!same(card.target.params.address,position.address)) continue;
      if (requiresReview(card)
          || (card.status==='PUBLISHED' && same(card.target.params.expected_binding_ref,position.binding_ref))) return null;
    }
    if (confirming.has(key(source,JSON.stringify(position.address)))) return null;
    return {position,target};
  }
  function getState() {
    return {source,resource_ref:copy(selected),catalog:copy(catalog),catalog_revision:revision,loading,error,
      can_load:Boolean(source&&!loading&&!denied),can_lookup:Boolean(source&&!denied&&!lookupPending(source)),
      lookup:copy(lookup),lookup_loading:lookupPending(source),
      source_blocked:[...cards.values()].some(card=>card.source_id===source&&card.target===null&&requiresReview(card)),
      positions:(catalog?.positions??[]).map(item=>({...copy(item),can_unbind:Boolean(targetFor(item.id))})),
      bind_targets:(catalog?.bind_targets??[]).map(item=>({...copy(item),can_bind:Boolean(targetFor(item.id,'bind'))})),
      cards:[...cards.values()].map(card=>({source_id:card.source_id,command_id:card.command_id,
        target:copy(card.target),position:copy(card.position),recovered:card.recovered,status:card.status,
        command_ref:copy(card.command_ref),result:copy(card.result),error:card.error,
        sending:card.sending,querying:card.querying}))};
  }
  function select(nextSource,resource=null,accessError=false) {
    const next=typeof nextSource==='string'&&nextSource?nextSource:null;
    let exact=null;
    try { if (resource!==null) exact=ref(resource,'resource_version/v1'); }
    catch { accessError=true; }
    if (next===source&&same(exact,selected)&&denied===Boolean(accessError)) return;
    ++epoch; controller?.abort();controller=null;loading=false;
    if (next!==source || denied!==Boolean(accessError)) { ++lookupSerial;lookup=null; }
    source=next;selected=exact;denied=Boolean(accessError);catalog=null;catalogObservation=0;++revision;
    error=accessError?'access_changed':null;emit();
  }
  async function loadCatalog() {
    if (!source||denied||loading) return;
    const originalSource=source, originalRef=copy(selected), serial=++epoch;
    // Record request-start order: an older in-flight observation that returns
    // after REJECTED is still older than that confirmed result.
    const observation=++revision;
    controller=new AbortController();loading=true;catalog=null;catalogObservation=0;error=null;emit();
    try {
      const result=await api.catalog(originalSource,originalRef,controller.signal);
      if (serial!==epoch) return;
      catalog=catalogView(result,originalSource,originalRef);catalogObservation=observation;++revision;
    } catch (cause) {
      if (serial===epoch) { catalog=null;catalogObservation=0;error=code(cause);++revision; }
    } finally {
      if (serial===epoch) { loading=false;controller=null;emit(); }
    }
  }
  function accept(card,reply,protocol=RECEIPT) {
    if (!object(reply)||reply.command_id!==card.command_id||reply.protocol!==protocol) fail('INVALID_LOCATION_RECEIPT');
    if (reply.protocol===RECEIPT && reply.status==='NOT_FOUND_AT_READ_CUT') {
      if (!FINAL.has(card.status)) {card.status='RESULT_UNKNOWN';card.error='NOT_FOUND_AT_READ_CUT';}
      return;
    }
    const commandRef=ref(reply.command_ref,'resource_version/v1');
    if (card.command_ref && !same(card.command_ref,commandRef)) fail('COMMAND_REFERENCE_CHANGED');
    if (reply.protocol===SUBMIT && reply.status!=='REGISTERED') fail('INVALID_LOCATION_RECEIPT');
    if (!ACTIVE.has(reply.status)&&!FINAL.has(reply.status)) fail('INVALID_LOCATION_RECEIPT');
    // A late initial acceptance cannot replace facts already obtained by GET.
    if (reply.protocol===SUBMIT && card.result?.protocol===RECEIPT) return;
    const result={protocol:reply.protocol,command_id:reply.command_id,command_ref:commandRef,status:reply.status};
    for (const [name,type] of Object.entries({admission_ref:'firing_admission/v1',firing_ref:'transition_firing/v1',
      invocation_ref:'invocation/v1',result_ref:'resource_version/v1',completion_ref:'firing_completion/v2',
      operation_result_ref:'operation_result/v1',rejection_ref:'firing_rejection/v1'})) {
      if (reply[name]!=null) result[name]=ref(reply[name],type);
    }
    const executionRefs=['admission_ref','firing_ref','invocation_ref'];
    const executionKnown=executionRefs.some(name=>card.result?.[name]);
    for (const name of executionRefs) {
      if (card.result?.[name]&&result[name]&&!same(card.result[name],result[name])) fail('LOCATION_EXECUTION_REFERENCE_CHANGED');
    }
    if (executionKnown&&reply.result_kind==='not_admitted') fail('LOCATION_ADMISSION_CHANGED');
    // Keep the last verified original execution identities even after a query
    // error. A stale REGISTERED cut cannot erase them and admit another firing.
    if (executionKnown&&result.status==='REGISTERED') return;
    if (['ADMITTED','PREPARED','RECOVERY_REQUIRED'].includes(reply.status)
        && (!result.admission_ref||!result.firing_ref||!result.invocation_ref)) fail('INCOMPLETE_LOCATION_PROGRESS');
    if (reply.status==='PUBLISHED') {
      if (reply.protocol!==RECEIPT || reply.result_kind!=='location_changed' || reply.error!==null
          || !result.admission_ref || !result.firing_ref || !result.invocation_ref
          || !result.result_ref || !result.completion_ref || !result.operation_result_ref
          || !Array.isArray(reply.output_refs)||reply.output_refs.length!==0
          || !Array.isArray(reply.binding_refs)||reply.binding_refs.length!==1) fail('INCOMPLETE_LOCATION_RESULT');
      const binding=ref(reply.binding_refs[0],'resource_address_binding/v1');
      if (card.target && same(binding,card.target.params.expected_binding_ref)) fail('UNCHANGED_LOCATION_RESULT');
      result.result_kind='location_changed';result.output_refs=[];result.binding_refs=[binding];result.error=null;
    } else if (reply.status==='REJECTED') {
      if (reply.protocol!==RECEIPT || !result.result_ref || !object(reply.error)||!text(reply.error.code)
          || reply.completion_ref!=null || reply.operation_result_ref!=null
          || (reply.output_refs!=null && (!Array.isArray(reply.output_refs)||reply.output_refs.length))
          || (reply.binding_refs!=null && (!Array.isArray(reply.binding_refs)||reply.binding_refs.length))) fail('INCOMPLETE_LOCATION_REJECTION');
      // These are the two existing read_receipt shapes. A post-admission
      // rejection intentionally has no result_kind in the original reader.
      if (reply.result_kind==='not_admitted') {
        if (!text(reply.rejection_transaction_id) || result.admission_ref || result.firing_ref
            || result.invocation_ref || result.rejection_ref || reply.registered_completion_event_id!=null) fail('INCOMPLETE_LOCATION_REJECTION');
        result.result_kind='not_admitted';result.rejection_transaction_id=reply.rejection_transaction_id;
      } else {
        if ((reply.result_kind!=null&&reply.result_kind!=='publish_rejected')
            || !result.admission_ref || !result.firing_ref || !result.invocation_ref || !result.rejection_ref
            || !text(reply.registered_completion_event_id) || reply.rejection_transaction_id!=null) fail('INCOMPLETE_LOCATION_REJECTION');
        if (reply.result_kind!=null) result.result_kind=reply.result_kind;
        result.registered_completion_event_id=reply.registered_completion_event_id;
      }
      result.error={code:reply.error.code};
    }
    if (FINAL.has(card.status)) {
      if (!FINAL.has(result.status)) return;
      if (!same(card.result,result)) fail('TERMINAL_LOCATION_RESULT_CHANGED');
    } else if (card.result && ['REGISTERED','ADMITTED','PREPARED'].includes(card.result.status)
        && ['REGISTERED','ADMITTED','PREPARED'].includes(result.status)
        && ['REGISTERED','ADMITTED','PREPARED'].indexOf(result.status)<['REGISTERED','ADMITTED','PREPARED'].indexOf(card.result.status)) return;
    if (!FINAL.has(card.status)&&FINAL.has(result.status)) card.review_after=revision;
    card.command_ref=commandRef;card.result=result;card.status=result.status;card.error=null;
  }
  function unconfirmed(card,cause,fromSubmit=false) {
    card.error=code(cause);
    if (!FINAL.has(card.status) && !(fromSubmit&&card.result?.protocol===RECEIPT)) card.status='RESULT_UNKNOWN';
    if (card.source_id===source && ['access_changed','source_not_allowed','target_not_allowed'].includes(card.error)) {
      catalog=null;catalogObservation=0;++revision;error=card.error;
    }
  }
  async function submitSelection(positionId,operation) {
    const selection=targetFor(positionId,operation);if (!selection) return;
    const originalSource=source, originalCatalog=catalog, originalContext=contextKey();
    const confirmationKey=key(source,JSON.stringify(selection.position.address));
    confirming.add(confirmationKey);emit();
    let confirmed=false;
    try {confirmed=await (operation==='bind'?confirmBind:confirmUnbind)(copy({source_id:source,...selection}));}
    catch (cause) {if (source===originalSource) error=code(cause);}
    finally {confirming.delete(confirmationKey);emit();}
    if (confirmed!==true || contextKey()!==originalContext || catalog!==originalCatalog || !targetFor(positionId,operation)) return;
    const id=makeCommandId(operation), identity=key(originalSource,id);
    if (!text(id)||id.length>512||used.has(identity)) fail('command_identity_unavailable');
    used.add(identity);
    const card={source_id:originalSource,command_id:id,target:copy(selection.target),position:copy(selection.position),recovered:false,
      catalog_revision:revision,review_after:null,command_ref:null,result:null,status:'SUBMITTING',error:null,sending:true,querying:false};
    cards.set(identity,card);emit();
    try {accept(card,await api.submit({source_id:originalSource,owner_command:'material_submit',command_id:id,params:copy(card.target.params)}),SUBMIT);}
    catch (cause) {unconfirmed(card,cause,true);}
    finally {card.sending=false;emit();}
  }
  const unbind=positionId=>submitSelection(positionId,'unbind');
  const bind=targetId=>submitSelection(targetId,'bind');
  async function query(id,originalSource=source) {
    const card=cards.get(key(originalSource,id));if (!card||card.querying) return;
    card.querying=true;emit();
    try {accept(card,await api.receipt(card.source_id,card.command_id));}
    catch (cause) {unconfirmed(card,cause);}
    finally {card.querying=false;emit();}
  }
  async function readEarlier(id) {
    if (!source||denied||lookupPending(source)||!text(id)||id.length>512) return;
    const originalSource=source, identity=key(originalSource,id), existing=cards.get(identity);
    if (existing?.querying) return;
    const serial=++lookupSerial;
    const card=existing??{source_id:originalSource,command_id:id,target:null,position:null,recovered:true,
      catalog_revision:null,review_after:null,command_ref:null,result:null,status:'RESULT_UNKNOWN',error:null,sending:false,querying:false};
    used.add(identity);lookups.set(identity,originalSource);card.querying=true;lookup=null;error=null;emit();
    try {
      const reply=await api.receipt(originalSource,id);accept(card,reply);
      // A not-found cut is not proof of a registered command. Keep a known
      // card unresolved, but do not invent a new operation from a typed ID.
      if (reply.status!=='NOT_FOUND_AT_READ_CUT') cards.set(identity,card);
      if (serial===lookupSerial && originalSource===source) lookup=reply.status==='NOT_FOUND_AT_READ_CUT'
        ?{protocol:RECEIPT,command_id:id,status:'NOT_FOUND_AT_READ_CUT'}:copy(card.result);
    } catch (cause) {
      if (existing) unconfirmed(existing,cause);
      if (serial===lookupSerial && originalSource===source) error=code(cause);
    } finally {
      card.querying=false;lookups.delete(identity);emit();
    }
  }
  return {getState,select,loadCatalog,unbind,bind,query,readEarlier};
}

const STRINGS={
  en:{title:'Material locations',hint:'Inspect exact current positions. Adding a position keeps the same version; removing one keeps its body and other positions.',
    load:'View or refresh positions',choose:'Choose a source first.',unchecked:'Use View or refresh positions to inspect current metadata.',loading:'Reading position metadata…',closed:'Location actions are not enabled for this source.',
    empty:'No positions match this exact selection.',allowed:'Choose a disclosed position action. The original Owner decides the result.',
    context:'Source, exact selection, and observation',position:'Position',bound:'Currently bound',vacant:'Empty position',tombstone:'Position was removed',
    remove:'Remove this position',readOnly:'No removal target is disclosed for this position.',blocked:'An original request or result already uses this position. Query it, then refresh metadata after its outcome is confirmed.',
    confirm:'Remove only this exact position? The material body and other positions remain. This does not disable or clean up the material.',
    bindTitle:'Add a position for this version',bindHint:'Choose an empty destination disclosed by the HOST. This adds a reference to the same exact version; it does not copy the body or remove existing positions.',
    add:'Add to this position',noBinding:'No previous binding',
    confirmBind:'Add this exact version to this position? It remains the same version; no body is copied and existing positions remain.',
    boundPublished:'The original Owner added this exact version to the position',
    source:'Source',baseline:'Original binding',version:'Exact resource version',metadata:'Exact position metadata',
    operations:'Original position requests',query:'Query original result',facts:'Frozen target and original facts',
    lookup:'Find an earlier position request',lookupId:'Original command identity',lookupRead:'Read original status',
    lookupHint:'Queries the original receipt in the selected source. It does not recreate a command or infer its operation or position.',
    recovered:'Recovered original request',unmapped:'The original receipt does not identify an operation or position. Query the same original command; a pending outcome pauses new location actions in this source.',
    sourceBlocked:'An earlier request has no confirmed outcome or needs a new observation. Query its original result, then refresh positions after confirmation.',
    recoveredPublished:'The original Owner confirmed a location change; its exact operation and position were not restored.',
    recoveredResult:'Original location change published. Refresh positions before starting a new intention.',
    result:'The original Owner confirmed location_changed. Refresh positions for a new observation.',
    unknown:'Outcome unconfirmed. Query this original command; do not submit a replacement request.',error:'Request could not be confirmed:',
    states:{SUBMITTING:'Submitting original position request',RESULT_UNKNOWN:'Original result unknown',REGISTERED:'Registered; waiting for original PN admission',
      ADMITTED:'Admitted; not published',PREPARED:'Prepared; publication unconfirmed',RECOVERY_REQUIRED:'Original Owner recovery required',
      PUBLISHED:'This position was removed by the original Owner',REJECTED:'Not published; original rejection recorded'}},
  'zh-CN':{title:'资料位置',hint:'查看精确的当前位置。添加位置仍引用同一版本；移除一个位置会保留正文及其它位置。',
    load:'查看或刷新位置',choose:'请先选择来源。',unchecked:'请点击“查看或刷新位置”读取当前元数据。',loading:'正在读取位置元数据…',closed:'此来源未开放位置操作。',
    empty:'没有匹配此精确选择的当前位置。',allowed:'请选择已披露的位置操作；实际结果由原 Owner 决定。',
    context:'来源、精确选择与观察位置',position:'位置',bound:'当前已绑定',vacant:'空位置',tombstone:'此位置已解除',
    remove:'移除此位置',readOnly:'此位置未披露移除目标。',blocked:'原请求或结果已关联此位置；请回查，并在确认结果后刷新元数据。',
    confirm:'只移除这个精确位置？资料正文和其它位置仍保留；这不会停用或物理清理资料。',
    bindTitle:'为此版本添加位置',bindHint:'请选择 HOST 披露的空位置。这里只添加同一精确版本的引用，不复制正文，也不移除已有位置。',
    add:'添加到此位置',noBinding:'无原位置绑定',
    confirmBind:'将此精确版本添加到这个位置？仍是同一版本，不复制正文，已有位置保留。',
    boundPublished:'原 Owner 已将此精确版本添加到该位置',
    source:'来源',baseline:'原位置绑定',version:'精确资料版本',metadata:'精确位置元数据',
    operations:'原位置请求',query:'回查原结果',facts:'冻结目标与原始事实',
    lookup:'查找此前的位置请求',lookupId:'原命令标识',lookupRead:'读取原状态',
    lookupHint:'读取当前来源下的原回执，不重建命令，也不推断其操作或位置。',
    recovered:'已恢复的原请求',unmapped:'原回执未披露具体操作和位置。请回查同一原命令；未确认的结果会暂停此来源的新位置操作。',
    sourceBlocked:'此前请求尚未确认结果，或仍需新观察。请回查原结果，并在确认后重新刷新位置。',
    recoveredPublished:'原 Owner 已确认位置变更；具体操作和位置未在此恢复。',
    recoveredResult:'原位置变更已发布。开始新意图前请刷新位置。',
    result:'原 Owner 已确认 location_changed。请刷新位置开始新的观察。',
    unknown:'结果未确认。请回查同一原命令，不另建替代请求。',error:'本次请求未能确认：',
    states:{SUBMITTING:'正在提交原位置请求',RESULT_UNKNOWN:'原结果未知',REGISTERED:'已登记，等待原 PN 准入',
      ADMITTED:'已准入，尚未发布',PREPARED:'已准备，发布尚未确认',RECOVERY_REQUIRED:'需要原 Owner 恢复',
      PUBLISHED:'原 Owner 已移除此位置',REJECTED:'未发布，原拒绝已记录'}},
};

function selectedResource(state) {
  const entry=state.record?.entry_ref;
  if (!entry||entry.source_id!==state.source) return null;
  if (entry.schema_version==='rpnh/collaboration/source_resource_ref/v1'
      && exactKeys(entry.ref,['resource_id','resource_version_id'])) {
    return ref({entity_type:'resource_version/v1',logical_id:entry.ref.resource_id,version_id:entry.ref.resource_version_id},'resource_version/v1');
  }
  if (entry.schema_version==='rpnh/collaboration/source_version_ref/v1'&&entry.ref?.entity_type==='resource_version/v1') return ref(entry.ref,'resource_version/v1');
  return null;
}

export function mountMaterialLocations(document,{root,fetcher=globalThis.fetch,makeCommandId,
    confirmUnbind=message=>document.defaultView?.confirm?.(message)===true,
    confirmBind=message=>document.defaultView?.confirm?.(message)===true}={}) {
  if (!root) fail('LOCATION_PANEL_ROOT_REQUIRED');
  let language='en', lastRevision=-1;
  const nodes={},labels=[],positionViews=new Map(),bindViews=new Map(),cardViews=new Map();
  const t=name=>STRINGS[language][name]??name;
  const el=(tag,name)=>{const node=document.createElement(tag);if(name)nodes[name]=node;return node;};
  const label=(node,name)=>{labels.push([node,name]);node.textContent=t(name);return node;};
  const button=(name,action)=>{const node=label(el('button',name),name);node.type='button';
    node.addEventListener('click',()=>Promise.resolve().then(action).catch(cause=>{nodes.notice.textContent=`${t('error')} ${code(cause)}`;}));return node;};
  root.className='material-management material-locations';
  root.append(label(el('h2'),'title'),label(el('p'),'hint'),button('load',()=>controller.loadCatalog()),el('p','notice'));
  nodes.notice.setAttribute('role','status');nodes.notice.setAttribute('aria-live','polite');
  const context=el('details');context.append(label(el('summary'),'context'),el('pre','context'));root.append(context);
  const lookup=el('details'),lookupLabel=el('label');lookup.append(label(el('summary'),'lookup'));
  const identity=el('input','lookupId');identity.type='text';identity.maxLength=512;
  lookupLabel.append(label(el('span'),'lookupId'),identity);
  lookup.append(lookupLabel,button('lookupRead',()=>controller.readEarlier(identity.value)),
    label(el('p'),'lookupHint'),el('pre','lookupResult'));root.append(lookup);
  root.append(el('div','positions'));
  const bindSection=el('section','bindSection');
  bindSection.append(label(el('h3'),'bindTitle'),label(el('p'),'bindHint'),el('div','bindTargets'));
  root.append(bindSection,label(el('h3'),'operations'),el('div','operations'));
  function render(state) {
    for (const [node,name] of labels) node.textContent=t(name);
    nodes.load.disabled=!state.can_load;
    nodes.lookupRead.disabled=!state.can_lookup;
    nodes.lookupResult.textContent=state.lookup?JSON.stringify(state.lookup,null,2):'';
    const status=!state.source?'choose':state.loading?'loading':!state.catalog?'unchecked':!state.catalog.supported?'closed':state.source_blocked?'sourceBlocked':state.positions.length||state.bind_targets.length?'allowed':'empty';
    nodes.notice.textContent=state.error?`${t('error')} ${state.error}`:t(status)+(state.catalog?.reason?' '+state.catalog.reason:'');
    nodes.context.textContent=JSON.stringify({source_id:state.source,resource_ref:state.resource_ref,
      as_of_ordinal:state.catalog?.as_of_ordinal??null,body_mode:'metadata_only'},null,2);
    if (lastRevision!==state.catalog_revision) {
      lastRevision=state.catalog_revision;positionViews.clear();bindViews.clear();
      nodes.positions.replaceChildren();nodes.bindTargets.replaceChildren();
      for (const position of state.positions) {
        const article=el('article');article.className='material-command';
        const title=el('h4'),status=el('p'),facts=el('pre'),summary=el('summary'),details=el('details');details.append(summary,facts);
        const remove=el('button');remove.type='button';remove.addEventListener('click',()=>controller.unbind(position.id).catch(cause=>{nodes.notice.textContent=`${t('error')} ${code(cause)}`;}));
        const hint=el('p');article.append(title,status,remove,hint,details);nodes.positions.append(article);
        positionViews.set(position.id,{title,status,facts,summary,remove,hint});
      }
      for (const target of state.bind_targets) {
        const article=el('article');article.className='material-command';
        const title=el('h4'),status=el('p'),facts=el('pre'),summary=el('summary'),details=el('details');details.append(summary,facts);
        const add=el('button');add.type='button';add.addEventListener('click',()=>controller.bind(target.id).catch(cause=>{nodes.notice.textContent=`${t('error')} ${code(cause)}`;}));
        const hint=el('p');article.append(title,status,add,hint,details);nodes.bindTargets.append(article);
        bindViews.set(target.id,{title,status,facts,summary,add,hint});
      }
    }
    for (const position of state.positions) {
      const view=positionViews.get(position.id);
      view.title.textContent=`${position.label} · ${position.address.opaque_name}`;
      view.status.textContent=t(position.tombstoned?'tombstone':position.resource_ref?'bound':'vacant');
      view.facts.textContent=JSON.stringify(position,null,2);view.summary.textContent=t('metadata');
      view.remove.textContent=t('remove');view.remove.hidden=position.unbind_target_id===null;view.remove.disabled=!position.can_unbind;
      view.hint.textContent=position.unbind_target_id===null?t('readOnly'):!position.can_unbind?t('blocked'):'';
    }
    nodes.bindSection.hidden=state.bind_targets.length===0;
    for (const target of state.bind_targets) {
      const view=bindViews.get(target.id);
      view.title.textContent=`${target.label} · ${target.address.opaque_name}`;
      view.status.textContent=t(target.tombstoned?'tombstone':'vacant');
      view.facts.textContent=JSON.stringify(target,null,2);view.summary.textContent=t('metadata');
      view.add.textContent=t('add');view.add.disabled=!target.can_bind;
      view.hint.textContent=target.can_bind?'':t('blocked');
    }
    for (const card of state.cards) {
      const identity=key(card.source_id,card.command_id);let view=cardViews.get(identity);
      if (!view) {
        const article=el('article');article.className='material-command';
        const title=el('h4'),status=el('p'),hint=el('p'),error=el('p'),query=el('button'),details=el('details'),summary=el('summary'),facts=el('pre');
        query.type='button';query.addEventListener('click',()=>controller.query(card.command_id,card.source_id));
        details.append(summary,facts);article.append(title,status,hint,error,query,details);nodes.operations.append(article);
        view={title,status,hint,error,query,summary,facts};cardViews.set(identity,view);
      }
      view.title.textContent=card.recovered?`${card.source_id} · ${t('recovered')} · ${card.command_id}`
        :`${card.source_id} · ${card.position.label} · ${card.target.params.address.opaque_name}`;
      view.status.textContent=card.status==='PUBLISHED'
        ?card.recovered?t('recoveredPublished'):card.target.params.operation==='bind'?t('boundPublished'):STRINGS[language].states.PUBLISHED
        :STRINGS[language].states[card.status]??card.status;
      view.hint.textContent=card.status==='PUBLISHED'?t(card.recovered?'recoveredResult':'result')
        :card.recovered?t('unmapped'):card.status==='RESULT_UNKNOWN'?t('unknown'):'';
      view.error.textContent=card.error?`${t('error')} ${card.error}`:'';
      view.query.textContent=t('query');view.query.disabled=card.querying;
      view.summary.textContent=t('facts');view.facts.textContent=JSON.stringify({source_id:card.source_id,
        command_id:card.command_id,recovered:card.recovered,target:card.target,command_ref:card.command_ref,result:card.result},null,2);
    }
  }
  const controller=createMaterialLocations({api:createMaterialLocationsApi(fetcher),makeCommandId,onChange:render,
    confirmUnbind:selection=>confirmUnbind(`${t('confirm')}\n\n${t('source')}: ${selection.source_id}\n${t('position')}: ${selection.position.label} · ${selection.position.address.opaque_name}\n${t('baseline')}: ${selection.position.binding_ref.version_id}\n${t('version')}: ${selection.position.resource_ref.version_id}`),
    confirmBind:selection=>confirmBind(`${t('confirmBind')}\n\n${t('source')}: ${selection.source_id}\n${t('position')}: ${selection.position.label} · ${selection.position.address.opaque_name}\n${t('baseline')}: ${selection.position.binding_ref?.version_id??t('noBinding')}\n${t('version')}: ${selection.target.resource_ref.version_id}`)});
  document.defaultView?.addEventListener?.('pagehide',()=>controller.select(null));
  render(controller.getState());
  return {controller,nodes,setBrowserState(state){let resource=null,invalid=false;
    try {resource=selectedResource(state);}catch {invalid=true;}
    controller.select(state.source,resource,Boolean(state.error)||invalid);},
    setLanguage(value){language=value==='zh-CN'?'zh-CN':'en';render(controller.getState());}};
}
