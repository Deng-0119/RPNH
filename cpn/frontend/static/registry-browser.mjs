/** Read-only Registry projection. Explicit material actions use a separate HOST panel. */
import {mountMaterialManagement} from './material-management.mjs';
import {mountHumanMaterialReader} from './human-material-reader.mjs';
import {mountMaterialLocations} from './material-locations.mjs';
import {mountMaterialDisable} from './material-disable.mjs';
import {mountMaterialCarriers} from './material-carriers.mjs';
import {mountMaterialPurge} from './material-purge.mjs';
import {mountRegistryPositions} from './registry-positions.mjs';
export const SCHEMA = 'rpnh/registry_browser/v1';
export const STRINGS = {
  en: {unsupported_type:'Type unavailable in this reader catalog',local_owner:'Local owner · read-only · no grant deadline',local_task:'Local task · no published source binding',published_source:'Published source binding',title:'Registry browser',back:'Back to run viewer',readonly:'Read-only · Registry indexes and materials',scope:'Sources and types are virtual groups, not filesystem folders. Only disclosed fields are shown. Index access does not grant material access.',sources:'Sources / entry types',refresh:'Refresh (new observation)',search:'Filter loaded page only',reference:'Exact entry reference',fields:'Disclosed index fields',entry:'Entry',overview:'Details',actions:'View',index:'Index fields',open:'Inspect record',previous:'Previous page',next:'Next page',close:'Close details',preview:'Preview text',detail:'Exact record',material:'Plain-text material',loading:'Loading…',empty:'No entries on this loaded page.',choose:'Choose a source and entry type.',noSources:'No disclosed sources or supported entry types.',unavailable:'Text-preview capability has not been disclosed.',language:'Language',observation:'Observation / coverage',error:'Read failed; previous data cleared.',invalid_query:'Invalid query.',access_changed:'Access changed or the session expired. Refresh the catalog; if this persists, close and reopen an authorized read session. This page cannot grant or renew access.',not_disclosed:'This information is not disclosed.',stale_observation:'Source changed. Refresh to start a new observation.',scope_limit:'A read, session or observation limit was reached. Refresh may not clear it; close and reopen an authorized read session if needed.',invalid_utf8:'Material is not valid UTF-8 text.',unsupported:'This read is unsupported. Open your local run with --run, or use an authorized shared read session through --read-host-config.',read_failed:'The source could not be read.'},
  'zh-CN': {unsupported_type:'当前读取目录不支持此类型',local_owner:'本地所有者 · 只读 · 无授权到期时间',local_task:'本地任务 · 未发布来源绑定',published_source:'已发布来源绑定',title:'Registry 浏览器',back:'返回运行视图',readonly:'只读 · Registry 索引与资料',scope:'来源与类型是虚拟分组，不是文件系统目录。只展示获准披露的字段。索引可见不代表正文可读。',sources:'来源 / 条目类型',refresh:'刷新（新观察）',search:'仅筛选当前已加载页',reference:'精确条目引用',fields:'已披露索引字段',entry:'条目',overview:'概要',actions:'查看',index:'索引字段',open:'查看记录',previous:'上一页',next:'下一页',close:'关闭详情',preview:'查看正文',detail:'精确记录',material:'纯文本资料',loading:'正在读取…',empty:'当前已加载页没有匹配条目。',choose:'请选择来源与条目类型。',noSources:'没有可披露的来源或支持的条目类型。',unavailable:'未披露正文预览能力。',language:'语言',observation:'观察位置 / 覆盖范围',error:'读取失败，旧数据已清空。',invalid_query:'查询无效。',access_changed:'访问权限已变化或会话已过期。请刷新来源目录；如仍失败，需关闭并重新打开已授权的读取会话。本页面不会授予或续订权限。',not_disclosed:'该信息未获准披露。',stale_observation:'来源已变化，请刷新开始新观察。',scope_limit:'已达到读取、会话或观察容量限制。刷新不一定能解除限制；必要时需关闭并重新打开已授权的读取会话。',invalid_utf8:'资料不是有效的 UTF-8 文本。',unsupported:'不支持此读取。用 --run 打开自己的本地运行，或用 --read-host-config 打开已授权的共享读取会话。',read_failed:'无法读取来源。'}
};
Object.assign(STRINGS.en,{navigation:'Change source / type',versionsDetails:'Versions and technical details',resources:'Resources',results:'Final-result pointers',workspace:'Workspace revisions',advanced:'Other types',sourceDetails:'Source identity and access',loadedScope:'Only this loaded page is shown. Grouped versions are not complete history; no version is automatically selected as current.',versions:'Versions on this page',technical:'Technical details',version:'Exact version',pageLabel:'Page',noResources:'No resource type is disclosed. Open other types below.',resourcesHint:'Read original resources without mixing them with execution records. Result pointers and workspace revisions remain separate facts.'});
Object.assign(STRINGS['zh-CN'],{navigation:'切换来源 / 类型',versionsDetails:'版本与技术详情',resources:'资料资源',results:'最终成果指针',workspace:'工作区修订',advanced:'其它类型',sourceDetails:'来源身份与访问',loadedScope:'仅展示当前已加载页。合并显示的版本不代表完整历史，也不自动选定“当前版本”。',versions:'本页中的版本',technical:'技术详情',version:'精确版本',pageLabel:'页',noResources:'未披露资料资源类型，可展开下方其它类型。',resourcesHint:'先浏览原始资料，不与执行内部记录混排。成果指针与工作区修订仍作为独立事实展示。'});
Object.assign(STRINGS.en,{links:'Declared related records',linkScope:'Exact references of this selected record only; not a complete relationship graph.',linkMore:'Some declared references are not shown. Inspect the original record for the full list.',task_ref:'Owning task',net_ref:'Producing net',producer_ref:'Producer',round_ref:'Task round',terminal_result_ref:'Terminal result',terminal_occurrence_ref:'Terminal firing',run_ref:'Run identity',latest_checkpoint_ref:'Referenced checkpoint',terminal_evidence_ref:'Terminal evidence',deliverable:'Deliverable',linkUnavailable:'Not available at this observation'});
Object.assign(STRINGS['zh-CN'],{links:'已声明的相关记录',linkScope:'仅为所选记录中的精确引用，不是完整关系图。',linkMore:'部分已声明引用未在此展示，可展开原记录查看完整列表。',task_ref:'所属任务',net_ref:'所属网',producer_ref:'生产者',round_ref:'任务轮次',terminal_result_ref:'终态结果',terminal_occurrence_ref:'终态 firing',run_ref:'运行身份',latest_checkpoint_ref:'所引用的 checkpoint',terminal_evidence_ref:'终态证据',deliverable:'成果',linkUnavailable:'当前观察位置不可用'});
const PRIMARY_TYPES = new Map([['resource_version/v1','resources'],['final_result_index/v1','results'],['workspace_revision/v1','workspace']]);
export function navigationGroups(types=[]){
  return {primary:types.filter(type=>PRIMARY_TYPES.has(type)).map(type=>({type,label:PRIMARY_TYPES.get(type)})),advanced:types.filter(type=>!PRIMARY_TYPES.has(type))};
}
// Group only disclosed exact identities on this page; do not infer newest/adopted versions.
export function groupPageEntries(entries,entryType=''){
  const groups=[], byIdentity=new Map();
  for(const entry of entries){
    const value=entry.entry_ref, ref=value?.ref;
    const identity=ref?.logical_id??ref?.resource_id;
    const type=ref?.entity_type??entryType;
    const owner=value?.source_id??value?.task_id;
    const key=typeof identity==='string'&&typeof owner==='string'?JSON.stringify([value.schema_version,owner,type,identity]):null;
    let group=key===null?null:byIdentity.get(key);
    if(!group){group={identity:identity??null,entries:[]};groups.push(group);if(key!==null)byIdentity.set(key,group);}
    group.entries.push(entry);
  }
  return groups;
}
Object.assign(STRINGS.en,{allVersions:'Browse object versions',versionScope:'Versions of one exact logical object at the original observation; not a current-version selection.',allEntries:'Back to type (new observation)'});
Object.assign(STRINGS['zh-CN'],{allVersions:'查看该对象的跨页版本',versionScope:'原观察位置下同一逻辑对象的版本，不判定当前版本。',allEntries:'返回类型列表（新观察）'});
export const RETURN_LIMITS = Object.freeze({frames:32,bytes:256*1024});
// Navigation is presentation state only. Never retain previous record/body payloads.
const emptyView = () => ({filter:'',expanded:[],focus:null,scrollX:0,scrollY:0});
function navigationView(value={}) {
  const text=value=>typeof value==='string'?value:'';
  const coordinate=value=>Number.isFinite(value)?Math.max(0,value):0;
  return {filter:text(value.filter).slice(0,4096),
    expanded:Array.isArray(value.expanded)?value.expanded.filter(x=>typeof x==='string'&&x.length<=2048).slice(0,256):[],
    focus:typeof value.focus==='string'&&value.focus.length<=2048?value.focus:null,
    scrollX:coordinate(value.scrollX),scrollY:coordinate(value.scrollY)};
}
Object.assign(STRINGS.en,{returnPrevious:'Back to previous view',returnLimited:'Earlier return positions were dropped to keep navigation bounded.',filteredEmpty:'No matches on this loaded page.'});
Object.assign(STRINGS['zh-CN'],{returnPrevious:'返回上一层',returnLimited:'为限制导航占用，较早的返回位置已移除。',filteredEmpty:'当前已加载页没有匹配项。'});
Object.assign(STRINGS.en,{governed_delivery_required:'This source requires an explicit managed body delivery. This read-only route cannot deliver the material.',material_disabled:'This exact version is disabled. Its permitted structural history remains visible.',material_availability_unknown:'Current material availability could not be proved. Body access is closed.',availability:'Current availability',available:'Available',disabled:'Disabled',unknown:'Unknown',availabilityAsOf:'Availability checked at'});
Object.assign(STRINGS['zh-CN'],{governed_delivery_required:'此来源要求显式的受治理正文交付，旧只读入口不能交付正文。',material_disabled:'此精确版本已停用，允许查看的结构历史仍保留。',material_availability_unknown:'无法证明资料当前可用，正文入口已关闭。',availability:'当前可用性',available:'可用',disabled:'已停用',unknown:'未知',availabilityAsOf:'可用性核查位置'});
const STRUCTURAL_RESOURCE_FIELDS=new Set(['resource_id','resource_version_id','media_type','content_schema_ref','byte_count','size','commit_ordinal','task_ref','producer_ref','round_ref','net_ref','origin_kind','content_schema_authority_ref','provenance_producer_invocation_ref','provenance_operation_binding_ref']);
// Defensive rendering follows the server's current policy projection.  It does
// not derive availability, grant a read, or retain a returned hidden body.
function applyCurrentMaterialPolicy(value){
  if(value.availability!==undefined){
    if(!['available','disabled','unknown'].includes(value.availability))throw new Error('material_availability_unknown');
    if(value.availability!=='available'||value.material_access==='explicit_delivery_required'){
      if(value.action==='material')throw new Error(value.availability==='disabled'?'material_disabled':value.availability==='unknown'?'material_availability_unknown':'governed_delivery_required');
      if(value.fields)value.fields=Object.fromEntries(Object.entries(value.fields).filter(([key])=>STRUCTURAL_RESOURCE_FIELDS.has(key)));
      delete value.body;value.material_may_be_available=false;
    }
  }
  for(const entry of value.entries??[])applyCurrentMaterialPolicy(entry);
  for(const entry of value.linked_entries??[])applyCurrentMaterialPolicy(entry);
  return value;
}
const blank = () => ({sources:[],source:null,type:null,entries:[],record:null,material:null,handle:null,cursors:[],page:0,next:null,sourceCut:null,coverage:null,versionAnchor:null,versionRef:null,busy:false,error:null,canReturn:false,historyLimited:false,restoredView:null});
export function createRegistryBrowser({fetcher=globalThis.fetch,onChange=()=>{},captureView=emptyView}={}) {
  let state=blank(), generation=0, controller=null, current=null, listLocation=null;
  let frames=[], retainedBytes=0;
  const emit=()=>{state.canReturn=frames.length>0;onChange(state);};
  const invalidate=()=>{generation++;controller?.abort();controller=null;};
  const clearHistory=()=>{frames=[];retainedBytes=0;current=null;listLocation=null;state.historyLimited=false;state.restoredView=null;};
  const reset=()=>{clearHistory();state=blank();};
  const rejectRead=()=>{invalidate();reset();state.error='read_failed';emit();};
  const expectedReference=handle=>{
    const candidates=[...state.entries,...(state.record?.linked_entries??[])];
    if(state.handle===handle&&state.record)candidates.push({handle,entry_ref:state.record.entry_ref});
    const matches=candidates.filter(entry=>entry.handle===handle);
    return matches.length&&validReference(matches[0].entry_ref)&&matches.every(entry=>sameReference(entry.entry_ref,matches[0].entry_ref))?structuredClone(matches[0].entry_ref):null;
  };
  const clearDetail=()=>{state.record=null;state.material=null;state.handle=null;};
  const origin=()=>{if(current&&!state.busy)current.view=navigationView(captureView());return current?structuredClone(current):null;};
  function remember(frame){
    if(!frame?.list?.cursor||frame.list.cut==null)return;
    if(frame.detail&&(frame.detail.cut==null||!sameReference(frame.detail.cut,frame.list.cut)))return;
    const bytes=new TextEncoder().encode(JSON.stringify(frame)).length;
    if(bytes>RETURN_LIMITS.bytes){frames=[];retainedBytes=0;state.historyLimited=true;return;}
    while(frames.length&&(frames.length>=RETURN_LIMITS.frames||retainedBytes+bytes>RETURN_LIMITS.bytes)){
      retainedBytes-=frames.shift().bytes;state.historyLimited=true;
    }
    frames.push({frame:structuredClone(frame),bytes});retainedBytes+=bytes;
  }
  async function request(action,params,prepare,apply) {
    invalidate();const serial=generation;controller=new AbortController();
    const signal=controller.signal;
    prepare();state.busy=true;state.error=null;state.restoredView=null;emit();
    // One abort/generation boundary also covers multi-read return navigation.
    const read=async(action,params)=>{
      const query=new URLSearchParams({action,...params});
      const response=await fetcher(`/api/registry-browser?${query}`,{signal,cache:'no-store',credentials:'same-origin'});
      if(serial!==generation)return null;
      const data=await response.json();
      if(serial!==generation)return null;
      if(!response.ok)throw new Error(Object.hasOwn(STRINGS.en,data?.error)?data.error:'read_failed');
      if(!data||data.schema_version!==SCHEMA||data.action!==action)throw new Error('read_failed');
      return applyCurrentMaterialPolicy(data);
    };
    try {
      const data=await read(action,params);if(serial!==generation)return;
      const update=await apply(data,read);if(serial!==generation)return;
      if(typeof update==='function')update();
      state.busy=false;emit();
    } catch(error) {
      if(serial!==generation)return;
      reset();state.error=Object.hasOwn(STRINGS.en,error?.message)?error.message:'read_failed';emit();
    }
  }
  const listParams=location=>({...location.anchor?{handle:location.anchor}:{source_id:location.source,entry_type:location.type},...location.cursor?{cursor:location.cursor}:{}});
  function checkPage(data,location,exact=false){
    if(data.source_id!==location.source||data.entry_type!==location.type||!Array.isArray(data.entries))throw new Error('read_failed');
    if(location.anchor&&(!sameReference(data.version_scope?.anchor_ref,location.versionRef)||data.version_scope?.scope!=='same_logical_object_at_observed_cut'||data.version_scope?.selects_current!==false||!data.entries.every(entry=>sameObject(entry.entry_ref,location.versionRef))))throw new Error('read_failed');
    if(exact&&(location.cut==null||!sameReference(data.source_cut,location.cut)||data.page_cursor!==location.cursor))throw new Error('stale_observation');
  }
  function applyPage(data,location){
    state.source=location.source;state.type=location.type;state.page=location.page;
    state.versionAnchor=location.anchor;state.versionRef=location.versionRef;
    state.entries=data.entries;state.next=data.next_cursor??null;state.sourceCut=data.source_cut;state.coverage=data.coverage;
    state.cursors=[...location.cursors];state.cursors[location.page]=data.page_cursor??location.cursor;
    listLocation={...location,cursor:state.cursors[location.page],cut:data.source_cut,cursors:[...state.cursors]};
  }
  function catalog(){return request('catalog',{},reset,data=>{if(!Array.isArray(data.sources))throw new Error('read_failed');state.sources=data.sources;});}
  function page(index,cursor=null,{from=null}={}){
    if(!from&&!state.versionAnchor)clearHistory();
    const location={source:state.source,type:state.type,page:index,cursor,anchor:state.versionAnchor,versionRef:state.versionRef,cursors:[...state.cursors]};
    if(location.source===null||location.type===null)return;
    return request(location.anchor?'versions':'list',listParams(location),()=>{state.entries=[];clearDetail();state.sourceCut=null;state.coverage=null;state.next=null;state.page=index;},data=>{
      checkPage(data,location);applyPage(data,location);
      if(from)remember(from);
      current={list:structuredClone(listLocation),detail:null,view:emptyView()};
    });
  }
  function inspect(action,handle,{direct=true}={}){
    const expected=expectedReference(handle);if(!expected)return rejectRead();
    const from=origin();
    const same=current?.detail?.action===action&&current.detail.handle===handle;
    return request(action,{handle},()=>{if(direct)clearDetail();else state.material=null;},data=>{
      if(!sameReference(expected,data.entry_ref))throw new Error('read_failed');
      if(action==='record'&&!validLinks(data.linked_entries,expected))throw new Error('read_failed');
      if(action==='material'&&typeof data.body!=='string')throw new Error('read_failed');
      // Old projections without cuts remain readable, but cannot create a return frame.
      if(listLocation?.cut!=null&&data.source_cut!=null&&!sameReference(data.source_cut,listLocation.cut))throw new Error('stale_observation');
      if(direct&&!same)remember(from);
      if(action==='record'){state.record=data;state.handle=handle;}
      else state.material=data;
      // Inline preview keeps the record locator. Returning never revives its old body.
      if(direct)current={list:listLocation?structuredClone(listLocation):null,detail:{action,handle,ref:expected,cut:data.source_cut},view:emptyView()};
    });
  }
  function returnPrevious(){
    if(state.busy||!frames.length)return;
    const target=frames.at(-1).frame,location=target.list;
    return request(location.anchor?'versions':'list',listParams(location),()=>{state.entries=[];clearDetail();state.coverage=null;state.sourceCut=null;state.next=null;},async(data,read)=>{
      checkPage(data,location,true);
      let detail=null;
      if(target.detail){
        const query=target.detail;
        if(query.cut==null||!sameReference(query.cut,location.cut))throw new Error('stale_observation');
        detail=await read(query.action,{handle:query.handle});if(detail===null)return;
        if(!sameReference(detail.entry_ref,query.ref)||!sameReference(detail.source_cut,query.cut))throw new Error('stale_observation');
        if(query.action==='record'&&!validLinks(detail.linked_entries,query.ref))throw new Error('read_failed');
        if(query.action==='material'&&typeof detail.body!=='string')throw new Error('read_failed');
      }
      // Apply only after every read succeeds and request() confirms this generation.
      return ()=>{
        applyPage(data,location);
        if(target.detail?.action==='record'){state.record=detail;state.handle=target.detail.handle;}
        if(target.detail?.action==='material')state.material=detail;
        retainedBytes-=frames.pop().bytes;
        current=structuredClone(target);state.restoredView=structuredClone(target.view);
      };
    });
  }
  const api={
    get state(){return state;},
    get navigationStats(){return {frames:frames.length,retainedBytes};},catalog,
    select(source,type){clearHistory();state.source=source;state.type=type;state.versionAnchor=null;state.versionRef=null;state.cursors=[];return page(0);},
    versions(handle){
      const expected=expectedReference(handle),source=state.sources.find(s=>s.source_id===state.source);
      if(!expected||source?.version_query!==true||state.busy)return;
      const from=origin();state.versionAnchor=handle;state.versionRef=expected;state.cursors=[];return page(0,null,{from});
    },
    refresh(){return catalog();},
    next(){if(!state.busy&&state.next!==null)return page(state.page+1,state.next);},
    previous(){if(!state.busy&&state.page>0&&state.cursors[state.page-1]!=null)return page(state.page-1,state.cursors[state.page-1]);},
    record(handle){return inspect('record',handle);},
    preview(entryHandle=null){const direct=entryHandle!==null;const handle=direct?entryHandle:state.handle;if(!handle||(!direct&&!state.record?.material_may_be_available))return;return inspect('material',handle,{direct});},
    returnPrevious,
    close(){
      const reload=state.busy&&!state.entries.length&&listLocation?.cursor;
      invalidate();const view=frames.find(item=>!item.frame.detail&&sameReference(item.frame.list,listLocation))?.frame.view;
      clearDetail();frames=[];retainedBytes=0;state.busy=false;state.historyLimited=false;
      current=listLocation?{list:structuredClone(listLocation),detail:null,view:view??emptyView()}:null;
      if(reload){
        const location=structuredClone(listLocation);
        return request(location.anchor?'versions':'list',listParams(location),()=>{},data=>{
          checkPage(data,location,true);applyPage(data,location);
          state.restoredView=view?structuredClone(view):emptyView();
        });
      }
      state.restoredView=view?structuredClone(view):null;emit();
    },
    dispose(){invalidate();reset();emit();}
  };return api;
}
// Structural comparison preserves all source/version identity fields, independent of key order.
export function sameReference(left,right){
  if(left===right)return true;
  if(!left||!right||typeof left!=='object'||typeof right!=='object'||Array.isArray(left)!==Array.isArray(right))return false;
  const keys=Object.keys(left);return keys.length===Object.keys(right).length&&keys.every(key=>Object.hasOwn(right,key)&&sameReference(left[key],right[key]));
}
function validReference(value){
  if(!value||!value.ref)return false;
  const ref=value.ref;const text=key=>typeof ref[key]==='string'&&ref[key].length>0;
  if(value.schema_version==='rpnh/registry_browser/local_task_ref/v1')return Object.keys(value).length===3&&typeof value.task_id==='string'&&value.task_id.startsWith('task:')&&Object.keys(ref).length===3&&text('entity_type')&&text('logical_id')&&text('version_id');
  if(typeof value.source_id!=='string'||!value.source_id)return false;
  if(value.schema_version==='rpnh/collaboration/source_resource_ref/v1')return text('resource_id')&&text('resource_version_id');
  return value.schema_version==='rpnh/collaboration/source_version_ref/v1'&&text('entity_type')&&text('logical_id')&&text('version_id');
}
function sameObject(a,b){
  if(!validReference(a)||!validReference(b)||a.schema_version!==b.schema_version||a.source_id!==b.source_id||a.task_id!==b.task_id)return false;
  const left=a.ref,right=b.ref;
  return (left.logical_id??left.resource_id)===(right.logical_id??right.resource_id)&&left.entity_type===right.entity_type;
}
function validLinks(links,parent){
  if(links===undefined)return true;
  return Array.isArray(links)&&links.every(link=>link&&typeof link.label==='string'&&
    (link.status==='readable'?typeof link.handle==='string'&&validReference(link.entry_ref)&&
      (parent.source_id?link.entry_ref.source_id===parent.source_id:link.entry_ref.task_id===parent.task_id&&!link.entry_ref.source_id):
      ['unavailable','unsupported'].includes(link.status)&&!link.handle));
}
export function entryPresentation(entry,entryType=''){
  const fields=entry.fields??{},ref=entry.entry_ref?.ref??{};
  const named=[fields.title,fields.name,fields.summary].find(value=>typeof value==='string'&&value.trim());
  const id=ref.logical_id??ref.resource_id;
  const kind=ref.entity_type??entryType;
  const label=named??[kind,id].filter(value=>typeof value==='string'&&value).join(' · ');
  const facts=['media_type','byte_count','commit_ordinal'].filter(key=>Object.hasOwn(fields,key)).map(key=>({key,value:fields[key]}));
  const summary=typeof fields.summary==='string'&&fields.summary!==label?fields.summary:'';
  return {label,kind,facts,summary};
}
export const exactText=value=>typeof value==='string'?value:JSON.stringify(value,null,2)??'';
export function filteredEntries(entries,query){const needle=query.toLocaleLowerCase();return entries.filter(row=>exactText({entry_ref:row.entry_ref,fields:row.fields}).toLocaleLowerCase().includes(needle));}
/** All Registry-controlled content uses textContent, including text preview. */
export function textElement(document,tag,value){const node=document.createElement(tag);node.textContent=exactText(value);return node;}
export function mountRegistryBrowser(document,fetcher=globalThis.fetch){
  const $=id=>document.getElementById(id);let language='en';let lastEntries=null;let navigation=0;let focusHint=null;let lastRestored=null;
  const management=$('material-management')?mountMaterialManagement(document,{root:$('material-management'),fetcher}):null;
  const humanReader=$('human-material-reader')?mountHumanMaterialReader(document,{root:$('human-material-reader'),fetcher,
    onEditReceived:management?selection=>management.openReceivedText(selection):null}):null;
  const locations=$('material-locations')?mountMaterialLocations(document,{root:$('material-locations'),fetcher}):null;
  const positions=$('registry-positions')?mountRegistryPositions(document,{root:$('registry-positions'),fetcher}):null;
  const carriers=$('material-carriers')?mountMaterialCarriers(document,{root:$('material-carriers'),fetcher}):null;
  const purgePanel=$('material-purge')?mountMaterialPurge(document,{root:$('material-purge'),fetcher}):null;
  const disablePanel=$('material-disable')?mountMaterialDisable(document,{root:$('material-disable'),fetcher,
    onDisabled:result=>{
      // A verified original result invalidates only its currently displayed
      // exact body. Closing details cancels late reads and clears the reader's
      // disposable copy; the independent editor draft is left intact.
      const entry=(api.state.material??api.state.record)?.entry_ref;
      if(api.state.source!==result.source_id||entry?.source_id!==result.source_id)return;
      const ref=entry.ref;
      if((result.target_resource_refs??[]).some(target=>
        target.logical_id===(ref?.resource_id??ref?.logical_id)
        &&target.version_id===(ref?.resource_version_id??ref?.version_id))){
        const editor=$('material-management'),focus=document.activeElement;
        const preserveEditorFocus=Boolean(editor?.contains?.(focus));
        const scroll={x:document.defaultView?.scrollX??0,y:document.defaultView?.scrollY??0};
        api.close();
        if(preserveEditorFocus&&editor?.contains?.(focus)){
          focus.focus({preventScroll:true});document.defaultView?.scrollTo?.(scroll.x,scroll.y);
        }
      }
    }}):null;
  const narrow=document.defaultView?.matchMedia?.('(max-width:750px)');let navigationExpanded=!narrow?.matches;
  const t=key=>STRINGS[language][key]??key;
  const keyedNodes=()=>Array.from(document.querySelectorAll('[data-return-key]'));
  const captureView=()=>({filter:$('filter').value,
    expanded:keyedNodes().filter(node=>node.open).map(node=>node.dataset.returnKey),
    focus:focusHint??document.activeElement?.dataset?.returnKey??null,
    scrollX:document.defaultView?.scrollX??0,scrollY:document.defaultView?.scrollY??0});
  const button=(label,action,key=null)=>{
    const node=textElement(document,'button',label);node.type='button';
    if(key!==null)node.dataset.returnKey=key;
    node.addEventListener('click',()=>{focusHint=key;try{return action();}finally{focusHint=null;}});return node;
  };
  function renderNavigation(state){
    const compact=Boolean(narrow?.matches);
    $('navigation-toggle').hidden=!compact;
    $('navigation-toggle').setAttribute('aria-expanded',String(!compact||navigationExpanded));
    const selected=state.source===null?t('choose'):`${state.source} · ${t(PRIMARY_TYPES.get(state.type)??state.type)}`;
    $('navigation-toggle').textContent=`${t('navigation')}: ${selected}`;
    $('navigation-content').hidden=compact&&!navigationExpanded;
  }
  function render(state){
    const restoring=state.restoredView&&state.restoredView!==lastRestored;
    const view=restoring?state.restoredView:captureView();
    renderNavigation(state);
    management?.setBrowserState(state);
    humanReader?.setBrowserState(state);
    locations?.setBrowserState(state);
    positions?.setBrowserState(state);
    disablePanel?.setBrowserState(state);
    carriers?.setBrowserState(state);
    purgePanel?.setBrowserState(state);
    document.documentElement.lang=language;
    document.querySelectorAll('[data-label]').forEach(node=>{node.textContent=t(node.dataset.label);});
    $('refresh').disabled=state.busy;
    $('return-previous').hidden=!state.canReturn;$('return-previous').disabled=state.busy;
    $('return-limit').hidden=!state.historyLimited;
    $('status').textContent=state.error?`${t('error')} ${t(state.error)}`:state.busy?t('loading'):state.source===null?t(state.sources.length?'choose':'noSources'):'';
    $('sources').replaceChildren();
    for(const source of state.sources){
      const section=document.createElement('section');section.className='source-group';section.append(textElement(document,'h3',source.source_id));
      const identity=document.createElement('details');identity.append(textElement(document,'summary',t('sourceDetails')),textElement(document,'p',source.source_id),textElement(document,'p',source.access_state));
      if(source.access_mode==='local_owner')identity.append(textElement(document,'p',t('local_owner')),textElement(document,'p',t(source.source_bound?'published_source':'local_task')));
      if(source.body_read_mode==='explicit_delivery_required')identity.append(textElement(document,'p',t('governed_delivery_required')));
      section.append(identity);
      const groups=navigationGroups(source.entry_types);
      const typeButton=(type,label)=>{const item=button(label,()=>{navigation++;if(narrow?.matches){navigationExpanded=false;$('navigation-toggle').focus();}api.select(source.source_id,type);});item.setAttribute('aria-pressed',String(state.source===source.source_id&&state.type===type));return item;};
      for(const {type,label} of groups.primary)section.append(typeButton(type,t(label)));
      if(!groups.primary.length)section.append(textElement(document,'p',t('noResources')));
      const advanced=document.createElement('details');advanced.className='advanced-types';advanced.open=state.source===source.source_id&&!PRIMARY_TYPES.has(state.type);
      advanced.append(textElement(document,'summary',t('advanced')));
      for(const type of groups.advanced)advanced.append(typeButton(type,type));
      for(const type of source.unsupported_entry_types??[])advanced.append(textElement(document,'p',`${type} · ${t('unsupported_type')}`));
      if(groups.advanced.length||(source.unsupported_entry_types??[]).length)section.append(advanced);$('sources').append(section);
    }
    if(lastEntries!==state.entries){$('filter').value='';lastEntries=state.entries;}
    if(restoring)$('filter').value=view.filter;
    $('location').textContent=state.source===null?t('resources'):t(PRIMARY_TYPES.get(state.type)??state.type);
    $('page-status').textContent=state.source===null?'':`${t('pageLabel')} ${state.page+1} · ${state.entries.length}`;
    $('version-scope').hidden=!state.versionAnchor;$('version-scope').textContent=state.versionAnchor?t('versionScope'):'';
    $('all-entries').hidden=!state.versionAnchor;
    $('rows').replaceChildren();
    const appendEntry=(entry,parent)=>{
      const display=entryPresentation(entry,state.type);
      const block=document.createElement('div');block.className='version-item';
      const label=textElement(document,'strong',display.label);label.className='entry-label';block.append(label);
      if(display.summary){const summary=textElement(document,'p',display.summary);summary.className='entry-summary';block.append(summary);}
      const ref=entry.entry_ref?.ref??{};const version=ref.version_id??ref.resource_version_id;
      const identity=version?textElement(document,'p',`${t('version')}: ${version}`):null;
      const technical=document.createElement('details');technical.className='entry-more';technical.dataset.returnKey='entry:'+entry.handle+':technical';technical.append(textElement(document,'summary',t('versionsDetails')));if(identity)technical.append(identity);
      const meta=textElement(document,'p',display.facts.filter(item=>item.key!=='commit_ordinal').map(item=>`${item.key}: ${item.value}`).join(' · '));meta.className='muted';block.append(meta);
      if(entry.availability)block.append(textElement(document,'p',`${t('availability')}: ${t(entry.availability)}`));
      const actions=document.createElement('div');actions.className='entry-actions';actions.append(button(t('open'),()=>api.record(entry.handle),'entry:'+entry.handle+':record'));if(entry.material_may_be_available)actions.append(button(t('preview'),()=>api.preview(entry.handle),'entry:'+entry.handle+':material'));
      if(!state.versionAnchor&&state.sources.find(s=>s.source_id===state.source)?.version_query===true)technical.append(button(t('allVersions'),()=>api.versions(entry.handle),'entry:'+entry.handle+':versions'));
      technical.append(textElement(document,'pre',{entry_ref:entry.entry_ref,fields:entry.fields,
        ...(entry.availability?{availability:entry.availability,availability_as_of:entry.availability_as_of}: {})}));
      if(sameReference(entry.entry_ref,(state.record??state.material)?.entry_ref))block.setAttribute('aria-current','true');
      block.append(actions,technical);parent.append(block);
    };
    for(const group of groupPageEntries(filteredEntries(state.entries,$('filter').value),state.type)){
      const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=3;
      if(group.entries.length===1)appendEntry(group.entries[0],cell);
      else{
        const versions=document.createElement('details');versions.className='version-group';versions.dataset.returnKey='group:'+JSON.stringify([state.source,state.type,group.identity]);
        versions.append(textElement(document,'summary',`${t('versions')} (${group.entries.length}) · ${entryPresentation(group.entries[0],state.type).label}`));
        for(const entry of group.entries)appendEntry(entry,versions);cell.append(versions);
      }
      row.append(cell);$('rows').append(row);
    }
    $('empty').textContent=t(state.entries.length?'filteredEmpty':'empty');
    $('empty').hidden=state.busy||state.source===null||$('rows').childNodes.length>0;
    $('previous').disabled=state.busy||state.page===0||state.cursors[state.page-1]==null;
    $('next').disabled=state.busy||state.next===null;
    $('observation').textContent=state.sourceCut==null?'':exactText({source_cut:state.sourceCut,coverage:state.coverage});
    $('details').hidden=!state.record&&!state.material;
    $('detail-title').textContent=t(state.record?'detail':'material');
    $('record-section').hidden=!state.record;
    $('record').textContent=state.record?exactText({entry_ref:state.record.entry_ref,fields:state.record.fields,
      ...(state.record.availability?{availability:state.record.availability,availability_as_of:state.record.availability_as_of}: {})}):'';
    $('links').replaceChildren();$('links-section').hidden=!state.record?.linked_entries?.length;
    for(const link of state.record?.linked_entries??[]){
      const label=link.label.startsWith('entries[')?`${t('deliverable')} ${Number(link.label.slice(8,-1))+1}`:t(link.label);
      const line=document.createElement('div');line.className='related-link';
      line.append(textElement(document,'span',label));
      if(link.status==='readable'){
        line.append(button(t('open'),()=>api.record(link.handle),'link:'+link.handle+':record'));
        if(link.material_may_be_available)line.append(button(t('preview'),()=>api.preview(link.handle),'link:'+link.handle+':material'));
      }else line.append(textElement(document,'span',t(link.status==='unsupported'?'unsupported_type':'linkUnavailable')));
      $('links').append(line);
    }
    $('links-more').hidden=!state.record?.link_coverage?.has_more;

    $('preview').hidden=!state.record?.material_may_be_available;$('preview').disabled=state.busy;
    $('unavailable').hidden=!state.record||state.record.material_may_be_available;
    $('material-section').hidden=!state.material;
    $('material-meta').textContent=state.material?exactText({entry_ref:state.material.entry_ref,media_type:state.material.media_type,byte_count:state.material.byte_count,source_cut:state.material.source_cut}):'';
    $('material').textContent=state.material?.body??'';
    for(const node of keyedNodes())if(node.tagName?.toLowerCase()==='details')node.open=view.expanded.includes(node.dataset.returnKey);
    if(restoring){
      lastRestored=state.restoredView;
      const target=keyedNodes().find(node=>node.dataset.returnKey===view.focus);
      (target??$('location')).focus({preventScroll:true});
      document.defaultView?.scrollTo?.(view.scrollX,view.scrollY);
    }
    if(state.error){lastRestored=null;$('filter').value='';}

  }
  const api=createRegistryBrowser({fetcher,onChange:render,captureView});
  $('return-previous').addEventListener('click',()=>api.returnPrevious());
  $('navigation-toggle').addEventListener('click',()=>{navigationExpanded=!navigationExpanded;renderNavigation(api.state);});
  narrow?.addEventListener?.('change',()=>{
    navigationExpanded=!narrow.matches;
    const restoreFocus=narrow.matches&&$('navigation-content').contains?.(document.activeElement);
    const leaveToggle=!narrow.matches&&document.activeElement===$('navigation-toggle');
    renderNavigation(api.state);
    if(restoreFocus)$('navigation-toggle').focus();
    if(leaveToggle)$('filter').focus();
  });
  $('language').addEventListener('change',event=>{language=event.target.value;management?.setLanguage(language);humanReader?.setLanguage(language);locations?.setLanguage(language);positions?.setLanguage(language);disablePanel?.setLanguage(language);carriers?.setLanguage(language);purgePanel?.setLanguage(language);render(api.state);});
  $('filter').addEventListener('input',()=>render(api.state));
  const openResources=async(preserve=false)=>{const previous=preserve?{source:api.state.source,type:api.state.type}:null;const serial=++navigation;await api.catalog();if(serial!==navigation)return;if(previous&&api.state.sources.some(s=>s.source_id===previous.source&&(s.entry_types??[]).includes(previous.type))){await api.select(previous.source,previous.type);return;}const source=api.state.sources.find(item=>(item.entry_types??[]).includes('resource_version/v1'));if(source&&!api.state.error&&api.state.source===null)await api.select(source.source_id,'resource_version/v1');};
  $('refresh').addEventListener('click',()=>{if(!api.state.busy)openResources(true);});$('previous').addEventListener('click',()=>api.previous());$('next').addEventListener('click',()=>api.next());
  $('all-entries').addEventListener('click',()=>api.select(api.state.source,api.state.type));
  const closeDetails=()=>{api.close();if(!api.state.restoredView)$('location').focus();};
  $('close').addEventListener('click',closeDetails);$('preview').addEventListener('click',()=>api.preview());
  $('back').addEventListener('click',()=>{navigation++;api.dispose();});
  document.defaultView?.addEventListener('pagehide',()=>{navigation++;api.dispose();});
  document.defaultView?.addEventListener('pageshow',event=>{if(event.persisted)openResources();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('material-management')?.contains?.(document.activeElement)&&!$('human-material-reader')?.contains?.(document.activeElement)&&!$('material-locations')?.contains?.(document.activeElement)&&!$('registry-positions')?.contains?.(document.activeElement)&&!$('material-disable')?.contains?.(document.activeElement)&&!$('material-carriers')?.contains?.(document.activeElement)&&!$('material-purge')?.contains?.(document.activeElement))closeDetails();});
  openResources();return api;
}
if(typeof document!=='undefined')mountRegistryBrowser(document);

