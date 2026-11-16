/** Explicit Viewer actions over the original Owner material HTTP protocol. */
import {createMaterialEditor,AGENT_TEXT_TYPE} from './material-editor.mjs';

export const MANAGEMENT_SCHEMA = 'rpnh/viewer_material_management/v1';
const CLOSED = Object.freeze({create:false,revise:false,publish:false});
const STRINGS = {
  en: {title:'Create and edit material',load:'Check material actions',create:'Create text',edit:'Edit complete text',
    closed:'Material management is not enabled for this source.',choose:'Choose a source to check its material actions.',
    allowed:'Only targets disclosed by this HOST are available. Saved versions appear after the original Owner confirms publication.',
    loading:'Reading permitted targets or a complete version…',cancelRead:'Cancel this read',
    target:'Save target',text:'Editable text',source:'Source',type:'Document type',draft:'Editing copy',
    prepare:'Prepare D1 only',save:'Save this text',close:'Close editing copy',
    dirty:'This editing copy has unsubmitted changes.',clean:'No new local changes. Prepared inputs and commands remain queryable.',
    handoff:'Saving freezes D1. You can continue editing D2 while the original command runs.',
    discard:'Discard the unsubmitted changes in this local editing copy? This does not cancel a command or discard a registered input.',
    prepared:'Prepared input retained by the Owner, not submitted. Closing this panel does not abandon it; this panel has no abandonment action.',
    preparedAbandon:'Prepared input retained by the Owner, not submitted. Abandoning it closes future submission and retains its original body and history.',
    abandon:'Abandon this prepared input',
    confirmAbandon:'Abandon this exact prepared input? It can no longer be submitted. Its original body, history, and your independent editing copy remain.',
    abandonUnknown:'Abandonment is unconfirmed. Query this original input. Do not submit it or send another abandonment request.',
    abandoned:'Original input was explicitly abandoned. Its acceptance history is retained, but it can no longer be submitted.',
    unknownInput:'The preparation outcome is unknown. Query this same input identity before taking another action.',
    unknownCommand:'The command outcome is unknown. Query the original command; do not infer failure or completion.',
    submit:'Submit prepared D1',queryInput:'Query this preparation',queryResult:'Query this command',
    operations:'Original commands and prepared inputs',frozen:'Frozen target',identity:'Stable identities and exact references',
    lookup:'Find an earlier preparation or command',lookupKind:'Identity kind',inputId:'Input preparation',commandId:'Material command',
    lookupId:'Original stable identity',read:'Read original result',lookupHint:'This read does not recreate a draft, submit, retry a tool, or change the current editing baseline.',
    error:'This request could not be confirmed. Existing text and command identities were retained.',
    noTarget:'No permitted target is available for this complete version. The original remains unchanged.',
    status:{PREPARING_INPUT:'Preparing frozen input',INPUT_UNKNOWN:'Preparation unknown',INPUT_ACCEPTED:'Input prepared, not submitted',INPUT_ABANDONED:'Input abandoned; submission closed',
      INPUT_ABANDONING:'Abandoning original prepared input',INPUT_ABANDONMENT_UNKNOWN:'Original input abandonment unconfirmed',
      SUBMITTING:'Submitting original command',RESULT_UNKNOWN:'Result unknown',REGISTERED:'Registered; awaiting PN admission',
      ADMITTED:'Admitted; execution has not published',PREPARED:'Products prepared; publication not confirmed',
      RECOVERY_REQUIRED:'Original Owner recovery required',PUBLISHED:'Published by the original Owner',REJECTED:'Not published; original rejection recorded'}},
  'zh-CN': {title:'创建与编辑资料',load:'查看资料操作能力',create:'新建文本',edit:'编辑完整文本',
    closed:'此来源未开放资料管理。',choose:'请先选择来源，再查看允许的资料操作。',
    allowed:'仅可选择此 HOST 披露的合法目标。原 Owner 确认正式发布后才显示保存完成。',
    loading:'正在读取允许目标或完整版本…',cancelRead:'取消本次读取',
    target:'保存目标',text:'可编辑文本',source:'来源',type:'资料类型',draft:'编辑副本',
    prepare:'仅准备 D1',save:'保存当前文本',close:'关闭编辑副本',
    dirty:'当前编辑副本有尚未提交的修改。',clean:'没有新的本地修改；已准备输入和原命令仍可回查。',
    handoff:'保存会冻结 D1；原命令执行期间可以继续编辑独立的 D2。',
    discard:'放弃当前本地编辑副本中尚未提交的修改？这不会取消原命令，也不会丢弃已登记输入。',
    prepared:'已准备输入由原 Owner 保留，尚未提交。关闭本面板不会放弃输入；本面板未提供显式放弃操作。',
    preparedAbandon:'已准备输入由原 Owner 保留，尚未提交。显式放弃会关闭后续提交，原正文和历史仍保留。',
    abandon:'放弃这份已准备输入',
    confirmAbandon:'放弃这份精确的已准备输入？它将不能再提交；原正文、历史以及独立编辑副本仍保留。',
    abandonUnknown:'原输入是否已放弃尚未确认。请回查这份原输入，不提交它，也不重发弃用请求。',
    abandoned:'原输入已被显式放弃。历史接收事实仍保留，但不能再次提交。',
    unknownInput:'输入准备结果未知。请使用同一输入标识回查后再操作。',
    unknownCommand:'原命令结果未知。请回查同一命令，不能据此判定失败或保存完成。',
    submit:'提交已准备的 D1',queryInput:'回查这次输入准备',queryResult:'回查这条原命令',
    operations:'原命令与已准备输入',frozen:'冻结目标',identity:'稳定标识与精确引用',
    lookup:'查找此前的输入准备或命令',lookupKind:'标识类型',inputId:'输入准备',commandId:'资料命令',
    lookupId:'原稳定标识',read:'读取原结果',lookupHint:'回查不会重建草稿、提交命令、重跑工具或修改当前编辑基线。',
    error:'本次请求未能确认；已有文本与原命令标识仍保留。',
    noTarget:'此完整版本没有允许的保存目标，原资料保持不变。',
    status:{PREPARING_INPUT:'正在准备冻结输入',INPUT_UNKNOWN:'准备结果未知',INPUT_ACCEPTED:'输入已准备，尚未提交',INPUT_ABANDONED:'输入已放弃，禁止再次提交',
      INPUT_ABANDONING:'正在显式放弃原输入',INPUT_ABANDONMENT_UNKNOWN:'原输入弃用结果未确认',
      SUBMITTING:'正在提交原命令',RESULT_UNKNOWN:'结果未知',REGISTERED:'已登记，等待原 PN 准入',
      ADMITTED:'已准入，执行尚未正式发布',PREPARED:'产物已准备，尚未确认正式发布',
      RECOVERY_REQUIRED:'需要原 Owner 恢复',PUBLISHED:'原 Owner 已正式发布',REJECTED:'未发布，原拒绝结果已登记'}}
};

export function createMaterialHttpApi(fetcher=globalThis.fetch){
  async function read(action,params,signal){
    const encoded=Object.fromEntries(Object.entries(params).map(([key,value])=>
      [key,typeof value==='object'?JSON.stringify(value):value]));
    const response=await fetcher(`/api/material-management?${new URLSearchParams({action,...encoded})}`,
      {cache:'no-store',credentials:'same-origin',...(signal?{signal}:{})});
    const data=await response.json();
    if(!response.ok)throw new Error(data?.error??'result_unknown');
    if(['catalog','editor'].includes(action)&&(data?.schema_version!==MANAGEMENT_SCHEMA
        ||data.action!==action||data.source_id!==params.source_id))throw new Error('INVALID_MANAGEMENT_VIEW');
    return data;
  }
  async function write(action,request){
    // Deliberately no AbortController. Closing a panel cannot cancel an Owner command.
    const response=await fetcher(`/api/material-management?action=${action}`,{method:'POST',
      cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(request)});
    const data=await response.json();
    if(!response.ok)throw new Error(data?.error??'result_unknown');
    return data;
  }
  return {catalog:(source_id,signal,resource_ref=null)=>read('catalog',
      {source_id,...(resource_ref?{resource_ref}:{})},signal),
    editor:(params,signal)=>read('editor',params,signal),
    prepareInput:request=>write('prepare_input',request),submit:request=>write('submit',request),
    abandonInput:request=>write('abandon_input',request),
    readInput:params=>read('read_input',params),readReceipt:params=>read('read_receipt',params)};
}

function resourceReference(entry,source){
  if(!entry||!entry.ref)return null;
  const ref=entry.ref;
  if(entry.source_id!==source)return null;
  if(entry.schema_version==='rpnh/collaboration/source_resource_ref/v1'
      &&Object.keys(ref).sort().join(',')==='resource_id,resource_version_id'){
    return {entity_type:'resource_version/v1',logical_id:ref.resource_id,version_id:ref.resource_version_id};
  }
  if(entry.schema_version==='rpnh/collaboration/source_version_ref/v1'
      &&ref.entity_type==='resource_version/v1'
      &&Object.keys(ref).sort().join(',')==='entity_type,logical_id,version_id')return structuredClone(ref);
  return null;
}

/**
 * Keep the textarea and operation buttons mounted: a late A result only updates
 * its own text nodes. Focus and the independently editable D2 are never restored
 * from a receipt. setBrowserState observes navigation without closing a draft.
 */
export function mountMaterialManagement(document,{root,fetcher=globalThis.fetch,
    confirmDiscard=message=>document.defaultView?.confirm?.(message)===true,
    confirmAbandon=message=>document.defaultView?.confirm?.(message)===true,makeCommandId}={}){
  if(!root)throw new Error('MATERIAL_PANEL_ROOT_REQUIRED');
  const api=createMaterialHttpApi(fetcher);
  let language='en',source=null,selected=null,catalog=null,readSerial=0,readController=null;
  let opening=false,notice='',returnFocus=null,lastEditor=null,lookupSerial=0,materialContext=null;
  const nodes={},labels=[],cards=new Map();
  const t=key=>STRINGS[language][key]??key;
  const el=(tag,key=null)=>{const node=document.createElement(tag);if(key)nodes[key]=node;return node;};
  const label=(node,key)=>{labels.push([node,key]);node.textContent=t(key);return node;};
  const button=(key,action)=>{const node=label(el('button',key),key);node.type='button';
    node.addEventListener('click',()=>Promise.resolve().then(action).catch(error=>showError(error)));return node;};
  const line=(key,control)=>{const node=el('label');node.append(label(el('span'),key),control);return node;};
  const errorCode=error=>typeof error?.message==='string'?error.message:'result_unknown';
  const showError=error=>{notice=`${t('error')} ${errorCode(error)}`;render(controller.getState());};
  root.className='material-management';
  root.append(label(el('h2'),'title'));
  const launch=el('div','launch');launch.className='material-actions';
  launch.append(button('load',()=>loadCatalog()),button('create',()=>openEditor()),
    button('edit',()=>openEditor(selected)),button('cancelRead',()=>cancelRead()));
  root.append(launch,el('p','capability'),el('p','notice'));
  nodes.notice.setAttribute('role','status');nodes.notice.setAttribute('aria-live','polite');
  const editing=el('section','editing');editing.hidden=true;
  editing.append(label(el('h3'),'draft'),el('p','context'));
  const select=el('select','target');select.addEventListener('change',()=>controller.selectTarget(select.value));
  editing.append(line('target',select));
  const textarea=el('textarea','text');textarea.rows=16;textarea.spellcheck=false;
  textarea.addEventListener('input',()=>controller.edit(textarea.value));
  editing.append(line('text',textarea),el('p','dirty'),label(el('p'),'handoff'));
  const actions=el('div');actions.className='material-actions';
  actions.append(button('prepare',()=>controller.prepare()),button('save',()=>controller.save()),
    button('close',()=>closeEditor()));editing.append(actions);root.append(editing);
  root.append(label(el('h3'),'operations'),el('div','operations'));
  const lookup=el('details','lookup');lookup.append(label(el('summary'),'lookup'));
  const kind=el('select','lookupKind');
  for(const [value,key] of [['input','inputId'],['command','commandId']]){
    const option=label(el('option'),key);option.value=value;kind.append(option);
  }
  kind.value='command';const identity=el('input','lookupId');identity.type='text';identity.maxLength=512;
  lookup.append(line('lookupKind',kind),line('lookupId',identity),button('read',()=>readEarlier()),
    label(el('p'),'lookupHint'),el('pre','lookupResult'));root.append(lookup);

  const controller=createMaterialEditor({api,makeCommandId,onChange:render});
  function cardNodes(card){
    let current=cards.get(card.input_command_id);if(current)return current;
    const section=el('article');section.className='material-command';
    const status=el('h4'),context=el('p'),retention=el('p'),error=el('p'),facts=el('pre');
    const buttons=el('div');buttons.className='material-actions';
    const make=(key,action)=>{const node=el('button');node.type='button';node.addEventListener('click',()=>
      Promise.resolve().then(action).catch(showError));buttons.append(node);return node;};
    const submit=make('submit',()=>controller.submitPrepared(card.input_command_id));
    const input=make('queryInput',()=>controller.queryInput(card.input_command_id));
    const result=make('queryResult',()=>controller.queryReceipt(card.input_command_id));
    const abandon=make('abandon',()=>{if(confirmAbandon(t('confirmAbandon')))return controller.abandonInput(card.input_command_id);});
    const details=el('details'),summary=el('summary');details.append(summary,facts);
    section.append(status,context,retention,error,buttons,details);nodes.operations.append(section);
    current={status,context,retention,error,facts,submit,input,result,abandon,summary};cards.set(card.input_command_id,current);return current;
  }
  function render(state){
    for(const [node,key] of labels)node.textContent=t(key);
    nodes.load.disabled=!source||opening;nodes.cancelRead.hidden=!opening;
    nodes.create.disabled=opening||!catalog?.targets?.some(item=>item.operation==='create_text')
      ||catalog.capabilities?.create!==true;
    nodes.edit.disabled=opening||selected===null||!catalog
      ||!(catalog.capabilities?.revise===true||catalog.capabilities?.create===true);
    nodes.read.disabled=!source;
    nodes.capability.textContent=!source?t('choose'):catalog?.prepare_allowed===true?t('allowed'):t('closed');
    nodes.notice.textContent=opening?t('loading'):notice;
    const draft=state.draft;nodes.editing.hidden=draft===null;
    if(draft){
      nodes.context.textContent=`${t('source')}: ${draft.source_id} · ${t('type')}: ${draft.document_type}`;
      if(lastEditor!==draft.editor_id){
        nodes.target.replaceChildren();
        for(const item of draft.targets){const option=el('option');option.value=item.id;
          option.textContent=`${item.label} · ${item.operation==='create_text'?t('create'):t('edit')}`;nodes.target.append(option);}
        lastEditor=draft.editor_id;
      }
      nodes.target.value=draft.selected_target_id??'';
      if(nodes.text.value!==draft.value)nodes.text.value=draft.value;
      nodes.text.disabled=draft.blocked!==null;nodes.target.disabled=draft.blocked!==null;
      nodes.dirty.textContent=draft.blocked?t('noTarget'):t(draft.dirty?'dirty':'clean');
      nodes.prepare.disabled=!draft.can_prepare;nodes.save.disabled=!draft.can_save;
    }
    for(const card of state.operations){
      const view=cardNodes(card);
      view.status.textContent=STRINGS[language].status[card.status]??card.status;
      view.context.textContent=`${t('source')}: ${card.source_id} · ${t('frozen')}: ${card.target.label}`;
      view.retention.textContent=card.input_current_state==='ABANDONED'?t('abandoned'):
        card.status==='INPUT_ABANDONMENT_UNKNOWN'?t('abandonUnknown'):
        card.status==='INPUT_ACCEPTED'?t(card.can_abandon?'preparedAbandon':'prepared'):
        card.status==='INPUT_UNKNOWN'?t('unknownInput'):card.status==='RESULT_UNKNOWN'?t('unknownCommand'):'';
      view.error.textContent=card.error?`${t('error')} ${card.error}`:'';
      view.facts.textContent=JSON.stringify({input_command_id:card.input_command_id,command_id:card.command_id,
        input_ref:card.input_ref,input_current_state:card.input_current_state,
        submission_allowed:card.submission_allowed,abandonment_ref:card.abandonment_ref,
        abandonment_command_id:card.abandonment_command_id,
        command_ref:card.command_ref,target:card.target,result:card.result},null,2);
      view.summary.textContent=t('identity');view.submit.textContent=t('submit');view.submit.hidden=!card.can_submit;
      view.input.textContent=t('queryInput');view.input.disabled=!card.can_query_input;
      view.result.textContent=t('queryResult');view.result.disabled=!card.can_query_result;
      view.abandon.textContent=t('abandon');view.abandon.hidden=!card.can_abandon;view.abandon.disabled=!card.can_abandon;
    }
  }
  function cancelRead(){readSerial++;readController?.abort();readController=null;opening=false;render(controller.getState());}
  async function loading(read,apply,onFailure=null){
    cancelRead();const serial=readSerial;readController=new AbortController();opening=true;notice='';render(controller.getState());
    try{const reply=await read(readController.signal);if(serial!==readSerial)return;apply(reply);}
    catch(error){if(serial!==readSerial)return;onFailure?.(error);catalog=null;controller.updateCapabilities(source,CLOSED);showError(error);}
    finally{if(serial===readSerial){opening=false;readController=null;render(controller.getState());}}
  }
  function loadCatalog(){
    if(!source)return;const current=source;
    return loading(signal=>api.catalog(current,signal),reply=>{catalog=reply;controller.updateCapabilities(current,reply.capabilities,
      {inputAbandonmentAllowed:reply.input_abandonment_allowed===true});});
  }
  function openEditor(resource=null){
    if(!source)return;const current=source;const original=controller.getState().draft;
    if(original?.dirty&&!confirmDiscard(t('discard')))return;
    const focus=document.activeElement;
    return loading(signal=>api.editor({source_id:current,...(resource?{resource_ref:resource}:{})},signal),reply=>{
      // Confirmation is for the local draft captured before the read. If D2 was
      // edited while it was pending, ask about that new revision explicitly.
      const now=controller.getState().draft;
      if(now?.dirty&&(now.editor_id!==original?.editor_id||now.revision!==original?.revision)
          &&!confirmDiscard(t('discard')))return;
      controller.open(reply,{discardCurrent:true});returnFocus=focus;nodes.text.focus();
    });
  }
  function closeEditor(){
    const draft=controller.getState().draft;
    if(draft?.dirty&&!confirmDiscard(t('discard')))return;
    cancelRead();controller.close({discardDraft:true});returnFocus?.focus?.();returnFocus=null;
  }
  function openReceivedText(selection){
    // Transfer an already completed original read into a separate local copy.
    // This does not read a body endpoint or authorize the later save.
    const content=selection?.content;
    if(!content||typeof selection.isCurrent!=='function'||!selection.isCurrent()
        ||content.source_id!==source||content.document_type!==AGENT_TEXT_TYPE
        ||content.complete!==true||typeof content.value!=='string')return;
    const current=source,original=controller.getState().draft,focus=document.activeElement;
    if(original?.dirty&&!confirmDiscard(t('discard')))return;
    return loading(signal=>api.catalog(current,signal,content.resource_ref),reply=>{
      if(source!==current||!selection.isCurrent())throw new Error('READ_CONTEXT_CHANGED');
      if(!Array.isArray(reply.document_types)||!reply.document_types.includes(AGENT_TEXT_TYPE)
          ||!Array.isArray(reply.targets)||reply.document_type!==AGENT_TEXT_TYPE
          ||reply.availability!=='available'||!Number.isSafeInteger(reply.availability_as_of)||reply.availability_as_of<0
          ||JSON.stringify(reply.resource_ref)!==JSON.stringify(content.resource_ref))throw new Error('INVALID_MANAGEMENT_VIEW');
      const targets=[...reply.targets.filter(item=>item.operation==='replace_text'&&reply.capabilities?.revise===true
          &&JSON.stringify(item.base_resource_ref)===JSON.stringify(content.resource_ref)),
        ...reply.targets.filter(item=>item.operation==='create_text'&&reply.capabilities?.create===true)];
      if(reply.prepare_allowed!==true||targets.length===0)throw new Error('NO_ALLOWED_TARGET_FOR_RECEIVED_VERSION');
      const now=controller.getState().draft;
      if(now?.dirty&&(now.editor_id!==original?.editor_id||now.revision!==original?.revision)
          &&!confirmDiscard(t('discard')))return;
      // Only a replacement of the received version survived the filter.
      // Never substitute a later current version as its editing baseline.
      controller.open({source_id:current,document_type:AGENT_TEXT_TYPE,value:content.value,
        complete:true,prepare_allowed:true,capabilities:reply.capabilities,targets,
        input_abandonment_allowed:reply.input_abandonment_allowed===true},{discardCurrent:true});
      catalog=reply;returnFocus=focus;nodes.text.focus();render(controller.getState());
    },error=>{if(errorCode(error)!=='NO_ALLOWED_TARGET_FOR_RECEIVED_VERSION')selection.invalidate?.();});
  }
  async function readEarlier(){
    if(!source)return;const current=source,id=nodes.lookupId.value;
    if(!id)return;const serial=++lookupSerial;nodes.lookupResult.textContent='';
    try{const input=nodes.lookupKind.value==='input';
      const result=await (input?api.readInput({source_id:current,input_command_id:id}):
        api.readReceipt({source_id:current,material_command_id:id}));
      if(serial!==lookupSerial||current!==source)return;
      if(result?.protocol!==(input?'rpnh/material_input_receipt/v1':'rpnh/material_receipt_view/v1')
          ||result[input?'input_command_id':'command_id']!==id)throw new Error('INVALID_ORIGINAL_RECEIPT');
      nodes.lookupResult.textContent=JSON.stringify(result,null,2);
    }catch(error){if(serial===lookupSerial&&current===source)showError(error);}
  }
  function setBrowserState(state){
    const next=state.source??null;
    if(next!==source){cancelRead();lookupSerial++;nodes.lookupResult.textContent='';catalog=null;source=next;notice='';}
    const exact=state.material??state.record;
    selected=resourceReference(exact?.entry_ref,source);
    const context=JSON.stringify({source,ref:selected,availability:exact?.availability,
      as_of:exact?.availability_as_of,access:exact?.material_access,schema:exact?.fields?.content_schema_ref});
    const changed=context!==materialContext;materialContext=context;
    if(exact?.availability==='disabled'||exact?.availability==='unknown'){
      selected=null;catalog=null;if(source)controller.updateCapabilities(source,CLOSED);
    }else if(exact?.material_access==='explicit_delivery_required'){
      // The old GET editor remains closed. A verified received copy retains
      // freshly checked metadata capability across a plain rerender.
      selected=null;
      if(changed){catalog=null;if(source)controller.updateCapabilities(source,CLOSED);}
    }
    if(state.error){catalog=null;if(source)controller.updateCapabilities(source,CLOSED);}
    render(controller.getState());
  }
  document.defaultView?.addEventListener?.('beforeunload',event=>{
    if(controller.getState().draft?.dirty){event.preventDefault?.();event.returnValue='';}
  });
  document.defaultView?.addEventListener?.('pagehide',()=>{cancelRead();lookupSerial++;});
  render(controller.getState());
  return {controller,setBrowserState,loadCatalog,openEditor,openReceivedText,closeEditor,cancelRead,
    setLanguage(value){language=value==='zh-CN'?'zh-CN':'en';render(controller.getState());},nodes};
}
