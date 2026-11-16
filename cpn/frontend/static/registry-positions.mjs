/** Read-only, task-scoped position observations. Handles never authorize writes
 * or main-reader navigation. This module owns no material command state. */
export const POSITION_SCHEMA='rpnh/registry_browser/v1';
const CUT_SCHEMA='rpnh/registry_browser/local_cut/v1';
const VERSION_SCHEMA='rpnh/collaboration/source_version_ref/v1';
const RESOURCE_SCHEMA='rpnh/collaboration/source_resource_ref/v1';
const LOCAL_SCHEMA='rpnh/registry_browser/local_task_ref/v1';
const MAX_PAGES=128,MAX_ROWS=3200;
const clone=value=>structuredClone(value);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const keys=(value,names)=>object(value)&&Object.keys(value).length===names.length&&names.every(name=>Object.hasOwn(value,name));
const text=value=>typeof value==='string'&&value.length>0;
const token=value=>text(value)&&value.length<=256&&!/[\u0000-\u001f\u007f]/.test(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
const invalid=()=>{throw new Error('read_failed');};
const ref=(value,kind)=>keys(value,['entity_type','logical_id','version_id'])&&value.entity_type===kind&&text(value.logical_id)&&text(value.version_id);
const nativeResource=value=>keys(value,['resource_id','resource_version_id'])&&text(value.resource_id)&&text(value.resource_version_id);

function capability(source){
  const value=source?.position_query;
  if(!object(source)||!text(source.source_id)||!text(source.task_id)||typeof source.source_bound!=='boolean'
      ||source.access_mode!=='local_owner'||source.access_state!=='readable'
      ||!keys(value,['supported','scope','history','body_mode'])||value.supported!==true
      ||value.scope!=='task_scope_only'||value.history!==true||value.body_mode!=='metadata_only'
      ||(!source.source_bound&&source.source_id!=='local-task:'+source.task_id))return null;
  return clone({source_id:source.source_id,task_id:source.task_id,source_bound:source.source_bound,position_query:value});
}
function qualified(value,source,expected,resource=false){
  if(source.source_bound){
    return keys(value,['schema_version','source_id','ref'])&&value.source_id===source.source_id
      &&value.schema_version===(resource?RESOURCE_SCHEMA:VERSION_SCHEMA)&&same(value.ref,expected);
  }
  const local=resource?{entity_type:'resource_version/v1',logical_id:expected.resource_id,version_id:expected.resource_version_id}:expected;
  return keys(value,['schema_version','task_id','ref'])&&value.schema_version===LOCAL_SCHEMA
    &&value.task_id===source.task_id&&same(value.ref,local);
}
function address(value,source){
  return keys(value,['scope_ref','opaque_name'])&&ref(value.scope_ref,'task/v1')
    &&value.scope_ref.logical_id===source.task_id&&text(value.opaque_name);
}
function cut(value,source){
  return keys(value,['schema_version','source_id','source_bound','task_id','through_ordinal','head_event_id','cut_id'])
    &&value.schema_version===CUT_SCHEMA&&value.source_id===source.source_id&&value.source_bound===source.source_bound
    &&value.task_id===source.task_id&&integer(value.through_ordinal)&&token(value.cut_id)
    &&(value.head_event_id===null?value.through_ordinal===0:text(value.head_event_id));
}
const ROW_KEYS=['binding_ref','previous_binding_ref','operation','tombstoned','resource_ref','entry_ref',
  'resource_entry_ref','commit_ordinal','producer_invocation_ref','firing_ref','availability'];
const PAGE_KEYS=['schema_version','action','source_id','source_cut','page_cursor','next_cursor',
  'availability_as_of','body_mode','coverage','entries'];
function auditRow(row,source,upper,position){
  if(!keys(row,[...ROW_KEYS,...position?['handle','address']:[]])||!ref(row.binding_ref,'resource_address_binding/v1')
      ||(row.previous_binding_ref!==null&&(!ref(row.previous_binding_ref,'resource_address_binding/v1')
        ||row.previous_binding_ref.logical_id!==row.binding_ref.logical_id))
      ||same(row.binding_ref,row.previous_binding_ref)||!qualified(row.entry_ref,source,row.binding_ref)
      ||!integer(row.commit_ordinal)||row.commit_ordinal>upper
      ||(row.producer_invocation_ref!==null&&!ref(row.producer_invocation_ref,'invocation/v1'))
      ||(row.firing_ref!==null&&!ref(row.firing_ref,'transition_firing/v1'))
      ||(position&&(!token(row.handle)||!address(row.address,source))))invalid();
  if(row.operation==='unbind'){
    if(row.tombstoned!==true||row.resource_ref!==null||row.resource_entry_ref!==null||row.availability!==null)invalid();
  }else if(row.operation==='bind'){
    if(row.tombstoned!==false||!nativeResource(row.resource_ref)||!qualified(row.resource_entry_ref,source,row.resource_ref,true)
        ||!['available','disabled','unknown'].includes(row.availability))invalid();
  }else invalid();
}
function pageView(reply,source,action,expected){
  const history=action==='position_history';
  if(!keys(reply,[...PAGE_KEYS,...history?['handle','address','head_binding_ref']:[]])
      ||reply.schema_version!==POSITION_SCHEMA||reply.action!==action||reply.source_id!==source.source_id
      ||!cut(reply.source_cut,source)||reply.body_mode!=='metadata_only'||!integer(reply.availability_as_of)
      ||reply.availability_as_of<reply.source_cut.through_ordinal||!token(reply.page_cursor)
      ||(reply.next_cursor!==null&&!token(reply.next_cursor))||reply.next_cursor===reply.page_cursor
      ||!Array.isArray(reply.entries)||reply.entries.length>MAX_ROWS)invalid();
  if(expected.cut!==null&&!same(reply.source_cut,expected.cut))throw new Error('stale_observation');
  if(expected.cursor!==null&&reply.page_cursor!==expected.cursor)throw new Error('stale_observation');
  const coverage=reply.coverage;
  if(!keys(coverage,['canonical_only','scope','through_ordinal','has_more','order'])||coverage.canonical_only!==true
      ||coverage.scope!=='task_scope_only'||coverage.through_ordinal!==reply.source_cut.through_ordinal
      ||coverage.has_more!==(reply.next_cursor!==null)
      ||coverage.order!==(history?'binding_chain_newest_first':'address_identity_ascending'))invalid();
  for(const row of reply.entries)auditRow(row,source,reply.source_cut.through_ordinal,!history);
  if(history){
    if(reply.handle!==expected.anchor.handle||!same(reply.address,expected.anchor.address)
        ||!same(reply.head_binding_ref,expected.anchor.binding_ref)||!reply.entries.length)invalid();
    let previous=expected.first,ordinal=expected.maxOrdinal??reply.source_cut.through_ordinal;
    const seen=new Set(expected.prior??[]);
    for(const row of reply.entries){
      const identity=JSON.stringify(canonical(row.binding_ref));
      if(!same(row.binding_ref,previous)||seen.has(identity)||row.commit_ordinal>ordinal)invalid();
      seen.add(identity);previous=row.previous_binding_ref;ordinal=row.commit_ordinal;
    }
    if((previous!==null)!==coverage.has_more||seen.has(JSON.stringify(canonical(previous))))invalid();
  }else{
    if(new Set(reply.entries.map(row=>row.handle)).size!==reply.entries.length
        ||new Set(reply.entries.map(row=>JSON.stringify(canonical(row.address)))).size!==reply.entries.length)invalid();
    if(reply.entries.some(row=>!same(row.address.scope_ref,reply.entries[0].address.scope_ref)))invalid();
    if(coverage.has_more&&!reply.entries.length)invalid();
  }
  return clone(reply);
}

const fresh=source=>({source:source?.source_id??null,view:'closed',busy:false,error:null,entries:[],
  source_cut:null,availability_as_of:null,coverage:null,page:0,page_cursor:null,next_cursor:null,anchor:null});
const ERRORS=new Set(['access_changed','not_disclosed','stale_observation','scope_limit','unsupported','read_failed']);
export function createRegistryPositions({fetcher=globalThis.fetch,onChange=()=>{}}={}){
  let source=null,state=fresh(null),generation=0,pending=null,denied=false,positionPage=null,historyPage=null;
  const invalidate=()=>{++generation;pending?.abort();pending=null;};
  const getState=()=>clone({...state,visible:source!==null,can_open:Boolean(source&&!state.busy&&!denied),
    can_return:state.view==='history'&&positionPage!==null&&!denied,
    can_previous:!state.busy&&state.page>0,can_next:!state.busy&&state.next_cursor!==null});
  const emit=()=>onChange(getState());
  const clear=()=>{positionPage=null;historyPage=null;state=fresh(source);};
  function select(next){
    const context=capability(next);if(same(context,source))return;
    invalidate();source=context;denied=false;clear();emit();
  }
  async function request(action,params,expected,apply){
    if(!source||denied)return;
    invalidate();const serial=generation,original=clone(source);pending=new AbortController();
    state={...fresh(source),view:action==='positions'?'positions':'history',busy:true,page:expected.page,
      anchor:expected.anchor?clone(expected.anchor):null};emit();
    try{
      const query=new URLSearchParams({action,...params});
      const response=await fetcher(`/api/registry-browser?${query}`,{method:'GET',signal:pending.signal,cache:'no-store',credentials:'same-origin'});
      if(serial!==generation)return;
      const raw=await response.json();if(serial!==generation)return;
      if(!response.ok)throw new Error(ERRORS.has(raw?.error)?raw.error:'read_failed');
      const data=pageView(raw,original,action,expected);if(serial!==generation)return;
      apply(data);
      state={...state,busy:false,error:null,entries:data.entries,source_cut:data.source_cut,
        availability_as_of:data.availability_as_of,coverage:data.coverage,page_cursor:data.page_cursor,next_cursor:data.next_cursor};
    }catch(error){
      if(serial!==generation)return;
      const code=ERRORS.has(error?.message)?error.message:'read_failed';
      denied=code==='access_changed'||code==='not_disclosed';clear();state.error=code;
    }finally{if(serial===generation){pending=null;state.busy=false;emit();}}
  }
  function positions(page=0,cursor=null,fixedCut=null,cursors=[]){
    if(!source||denied||page>=MAX_PAGES)return;
    return request('positions',{source_id:source.source_id,...cursor?{cursor}:{}},
      {page,cursor,cut:fixedCut},data=>{
        const retained=cursors.slice();retained[page]=data.page_cursor;
        positionPage={page,cursor:data.page_cursor,cut:clone(data.source_cut),cursors:retained};historyPage=null;
      });
  }
  function history(page,cursor,route){
    const prior=route.pages.slice(0,page).flatMap(item=>item.identities);
    const previous=page>0?route.pages[page-1]:null;
    const expected={page,cursor,cut:route.cut,anchor:route.anchor,
      first:page===0?route.anchor.binding_ref:previous.previous,
      maxOrdinal:previous?.lastOrdinal,prior};
    return request('position_history',{handle:route.anchor.handle,...cursor?{cursor}:{}},expected,data=>{
      const pages=route.pages.slice();pages[page]={cursor:data.page_cursor,
        identities:data.entries.map(row=>JSON.stringify(canonical(row.binding_ref))),
        previous:clone(data.entries.at(-1).previous_binding_ref),lastOrdinal:data.entries.at(-1).commit_ordinal};
      historyPage={...route,page,pages};
    });
  }
  const api={getState,select,
    open(){if(!state.busy&&source&&!denied){clear();return positions();}},
    refresh(){return api.open();},
    history(handle){
      if(state.busy||state.view!=='positions'||!positionPage)return;
      const row=state.entries.find(item=>item.handle===handle);if(!row)return;
      const anchor={handle:row.handle,address:clone(row.address),binding_ref:clone(row.binding_ref)};
      return history(0,null,{anchor,cut:clone(positionPage.cut),page:0,pages:[]});
    },
    next(){
      if(state.busy||state.next_cursor===null||state.page+1>=MAX_PAGES)return;
      return state.view==='positions'?positions(state.page+1,state.next_cursor,positionPage.cut,positionPage.cursors)
        :history(state.page+1,state.next_cursor,historyPage);
    },
    previous(){
      if(state.busy||state.page===0)return;
      const page=state.page-1;
      return state.view==='positions'?positions(page,positionPage.cursors[page],positionPage.cut,positionPage.cursors)
        :history(page,historyPage.pages[page].cursor,historyPage);
    },
    returnPositions(){
      if(!positionPage||state.view!=='history'||denied)return;
      return positions(positionPage.page,positionPage.cursor,positionPage.cut,positionPage.cursors);
    },
    close(){invalidate();clear();emit();},
    dispose(){invalidate();source=null;denied=false;clear();emit();},
  };
  return api;
}

const STRINGS={
  en:{title:'Task positions',historyTitle:'History of this exact position',
    scope:'Read-only · task-scope addresses only. Other scopes and never-bound HOST destinations are outside this view.',
    distinction:'Position history follows one exact address across resources and producers. Object versions remain a separate query.',
    open:'View task positions',refresh:'Refresh positions (new observation)',history:'View position history',
    previous:'Previous page',next:'Next page',back:'Back to observed positions',close:'Close position view',
    unchecked:'Open the position view to make an explicit metadata observation.',loading:'Reading position metadata…',
    empty:'No canonical task-scope positions exist at this observation.',
    observed:'Structure remains at the displayed observation. Availability is rechecked for each response.',
    order:'Newest to oldest, following exact binding predecessors. This is not the object-version list.',
    bound:'Bound to an exact version',unbound:'Unbound (tombstone)',rebound:'Bound after an observed unbind',
    available:'available',disabled:'disabled',unknown:'unknown',availability:'Logical availability',
    page:'Page',source:'Source',address:'Exact address',audit:'Exact audit references',observation:'Observation and coverage',
    provenance:'Execution references describe the binding change. They do not assign ownership of the material. Logical availability does not confirm a physical carrier or deliver a body.',
    error:'Position query failed: ',access_changed:'Access changed. Refresh the source before reading again.',
    not_disclosed:'This observation is no longer disclosed. Refresh the source before reading again.',
    stale_observation:'The retained observation expired or changed. Open a new observation.',
    scope_limit:'The bounded read limit was reached. Open a new observation.',unsupported:'Position queries are not supported for this source.',
    read_failed:'The response could not be verified. No previous position data is retained.'},
  'zh-CN':{title:'任务位置',historyTitle:'此精确位置的历史',
    scope:'只读 · 仅本任务作用域的地址。其他作用域和从未绑定的 HOST 候选目标不在本视图内。',
    distinction:'位置历史沿同一精确地址跨资料与生产者追溯；对象版本仍是独立查询。',
    open:'查看任务位置',refresh:'刷新位置（新观察）',history:'查看位置历史',previous:'上一页',next:'下一页',
    back:'返回原位置观察',close:'关闭位置视图',unchecked:'请打开位置视图，明确查询一次元数据观察。',loading:'正在读取位置元数据…',
    empty:'本次观察下没有正式任务位置。',observed:'结构保持本次观察；每次响应重新检查当前可用性。',
    order:'沿精确绑定前驱从新到旧展示；这不是对象版本列表。',bound:'绑定到精确版本',unbound:'已解除绑定（墓碑）',
    rebound:'在已观察的解绑后重新绑定',available:'可用',disabled:'已停用',unknown:'未知',availability:'逻辑可用性',
    page:'第几页',source:'来源',address:'精确地址',audit:'精确审计引用',observation:'观察与覆盖范围',
    provenance:'执行引用描述本次位置变更，不据此推断资料归属。逻辑可用不证明物理载体存在，也不交付正文。',error:'位置查询未完成：',
    access_changed:'访问条件已变化，请刷新来源后重查。',not_disclosed:'此观察已不可披露，请刷新来源后重查。',
    stale_observation:'原观察已失效或变化，请建立新观察。',scope_limit:'已达有界读取限制，请建立新观察。',
    unsupported:'此来源不支持位置查询。',read_failed:'无法核实响应，原位置数据已清除。'},
};
export function mountRegistryPositions(document,{root,fetcher=globalThis.fetch}={}){
  if(!root)throw new Error('POSITION_PANEL_ROOT_REQUIRED');
  let language='en';const nodes={};const t=key=>STRINGS[language][key]??key;
  const make=(tag,name)=>{const node=document.createElement(tag);if(name)nodes[name]=node;return node;};
  const label=(node,key)=>{node.dataset.positionLabel=key;node.textContent=t(key);return node;};
  const button=(name,key,action)=>{const node=label(make('button',name),key);node.type='button';node.dataset.positionAction=name;
    node.addEventListener('click',action);return node;};
  const focus=node=>node?.focus?.({preventScroll:true});
  let controller;
  async function navigate(action,after=null){
    focus(nodes.heading);await action();
    if(document.activeElement===nodes.heading&&!root.hidden&&after)focus(after()??nodes.heading);
  }
  root.className='registry-positions';root.hidden=true;
  const heading=make('h3','heading');heading.tabIndex=-1;root.append(heading,label(make('p'),'scope'),label(make('p'),'distinction'));
  const actions=make('div','actions');actions.className='material-actions';root.append(actions);
  actions.append(button('open','open',()=>navigate(()=>controller.open())),button('refresh','refresh',()=>navigate(()=>controller.refresh())),
    button('back','back',()=>{const handle=controller.getState().anchor?.handle;
      return navigate(()=>controller.returnPositions(),()=>{
        const row=Array.from(nodes.rows.childNodes).find(node=>node.dataset.positionHandle===handle);
        return Array.from(row?.childNodes??[]).find(node=>node.dataset.positionAction==='history');
      });}),
    button('close','close',()=>{controller.close();focus(nodes.open);}));
  const status=make('p','status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');root.append(status);
  root.append(make('p','address'),make('p','page'),make('div','rows'));
  const pagination=make('div','pagination');pagination.className='pagination';root.append(pagination);
  pagination.append(button('previous','previous',()=>navigate(()=>controller.previous())),button('next','next',()=>navigate(()=>controller.next())));
  const details=make('details','details');details.append(label(make('summary'),'observation'),make('pre','observation'));root.append(details);
  root.append(label(make('p'),'provenance'));
  function render(state){
    root.hidden=!state.visible;nodes.heading.textContent=t(state.view==='history'?'historyTitle':'title');
    const walk=node=>{if(node.dataset?.positionLabel)node.textContent=t(node.dataset.positionLabel);for(const child of node.childNodes??[])walk(child);};
    for(const child of root.childNodes)walk(child);
    nodes.open.hidden=state.view!=='closed';nodes.open.disabled=!state.can_open;
    nodes.refresh.hidden=state.view==='closed';nodes.refresh.disabled=!state.can_open;
    nodes.back.hidden=state.view!=='history';nodes.back.disabled=!state.can_return;
    nodes.close.hidden=state.view==='closed';
    nodes.status.textContent=state.error?t('error')+t(state.error):state.busy?t('loading'):state.view==='closed'?t('unchecked')
      :state.entries.length?t(state.view==='history'?'order':'observed'):t('empty');
    nodes.address.textContent=state.anchor?`${t('address')}: ${state.anchor.address.opaque_name}\n${JSON.stringify(state.anchor.address.scope_ref)}`:'';
    nodes.page.textContent=state.page_cursor?`${t('source')}: ${state.source} · ${t('page')} ${state.page+1}`:'';
    nodes.rows.replaceChildren();
    for(let index=0;index<state.entries.length;index++){
      const row=state.entries[index],article=make('article');article.className='position-audit';
      if(state.view==='positions')article.dataset.positionHandle=row.handle;
      const title=make('h4');title.textContent=state.view==='positions'?row.address.opaque_name:row.binding_ref.version_id;article.append(title);
      const operation=make('p'),previous=state.entries[index+1];
      const rebound=state.view==='history'&&row.operation==='bind'&&previous?.operation==='unbind'&&same(row.previous_binding_ref,previous.binding_ref);
      operation.textContent=t(row.operation==='unbind'?'unbound':rebound?'rebound':'bound');article.append(operation);
      if(row.resource_ref){const resource=make('p');resource.textContent=row.resource_ref.resource_version_id;article.append(resource);
        const availability=make('p');availability.textContent=`${t('availability')}: ${t(row.availability)}`;article.append(availability);}
      if(state.view==='positions'){
        const open=label(make('button'),'history');open.type='button';open.dataset.positionAction='history';open.disabled=state.busy;
        open.addEventListener('click',()=>navigate(()=>controller.history(row.handle)));article.append(open);
      }
      const audit=make('details');audit.append(label(make('summary'),'audit'));const body=make('pre');body.textContent=JSON.stringify(row,null,2);audit.append(body);article.append(audit);nodes.rows.append(article);
    }
    nodes.pagination.hidden=!state.page_cursor;nodes.previous.disabled=!state.can_previous;nodes.next.disabled=!state.can_next;
    nodes.details.hidden=!state.source_cut;nodes.observation.textContent=state.source_cut?JSON.stringify({source_cut:state.source_cut,
      availability_as_of:state.availability_as_of,coverage:state.coverage},null,2):'';
  }
  controller=createRegistryPositions({fetcher,onChange:render});render(controller.getState());
  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape'||!root.contains?.(document.activeElement))return;
    event.preventDefault?.();
    if(controller.getState().can_return)navigate(()=>controller.returnPositions());else{controller.close();focus(nodes.open);}
  });
  document.defaultView?.addEventListener?.('pagehide',()=>controller.dispose());
  return {controller,nodes,setBrowserState(state){
    const selected=state.type==='resource_version/v1'&&!state.error?state.sources.find(item=>item.source_id===state.source):null;
    controller.select(selected);
  },setLanguage(value){language=value==='zh-CN'?'zh-CN':'en';render(controller.getState());},dispose:()=>controller.dispose()};
}
