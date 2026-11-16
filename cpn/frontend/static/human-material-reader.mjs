/** Explicit human reads. Registry observations never invoke this delivery port. */
export const HUMAN_READ_CATALOG = 'rpnh/viewer_human_read/v1';
export const HUMAN_READ_PROGRESS = 'rpnh/human_viewer_progress/v1';
const MAX_BODY_BYTES = 2 * 1024 * 1024;
const AGENT_TEXT_TYPE = 'application/rpnh_agent_text/v1';
const CONTINUABLE = new Set(['accepted_waiting','admitted_waiting_delivery']);
const TERMINAL = new Set(['completed','rejected']);
const STATES = new Set(['not_found_at_read_cut',...CONTINUABLE,'release_authorized',
  'delivered_pending_settlement',...TERMINAL,'unknown']);
const text = value => typeof value === 'string' && value.length > 0;
const copy = value => structuredClone(value);
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const operationKey = (source,id) => JSON.stringify([source,id]);
const fail = code => { throw new Error(code); };
const code = error => text(error?.message) ? error.message : 'result_unknown';
function exactRef(value){
  if (!value || Object.keys(value).sort().join(',') !== 'entity_type,logical_id,version_id'
      || value.entity_type !== 'resource_version/v1' || !text(value.logical_id) || !text(value.version_id)) return null;
  return {entity_type:value.entity_type,logical_id:value.logical_id,version_id:value.version_id};
}
function selectedRef(entry,source){
  if (!entry || entry.source_id !== source) return null;
  if (entry.schema_version === 'rpnh/collaboration/source_version_ref/v1') return exactRef(entry.ref);
  if (entry.schema_version === 'rpnh/collaboration/source_resource_ref/v1'
      && entry.ref && Object.keys(entry.ref).sort().join(',') === 'resource_id,resource_version_id') {
    return exactRef({entity_type:'resource_version/v1',logical_id:entry.ref.resource_id,
      version_id:entry.ref.resource_version_id});
  }
  return null;
}
async function boundedBytes(response){
  const length = response.headers?.get('content-length');
  if (length !== null && length !== undefined && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) {
    await response.body?.cancel?.(); fail('scope_limit');
  }
  let bytes;
  if (typeof response.body?.getReader === 'function') {
    const reader = response.body.getReader(), chunks = []; let size = 0;
    try {
      for (;;) {
        const item = await reader.read(); if (item.done) break;
        size += item.value.byteLength;
        if (size > MAX_BODY_BYTES) { await reader.cancel(); fail('scope_limit'); }
        chunks.push(item.value);
      }
      bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.byteLength; }
    } finally { reader.releaseLock(); }
  } else {
    bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > MAX_BODY_BYTES) fail('scope_limit');
  }
  if (length !== null && length !== undefined && Number(length) !== bytes.byteLength) fail('incomplete_body');
  return bytes;
}
export function createHumanReadHttpApi(fetcher = globalThis.fetch){
  async function read(action,params,signal){
    const response = await fetcher(`/api/material-management?${new URLSearchParams({action,...params})}`,
      {cache:'no-store',credentials:'same-origin',redirect:'error',...(signal ? {signal} : {})});
    const result = await response.json();
    if (!response.ok) fail(result?.error ?? 'result_unknown');
    return result;
  }
  return {
    readCatalog:(source_id,signal) => read('read_catalog',{source_id},signal),
    readProgress:params => read('read_progress',params),
    async deliver(request){
      // No abort signal: leaving the view does not cancel an original command.
      const response = await fetcher('/api/material-delivery',{method:'POST',cache:'no-store',
        credentials:'same-origin',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
      if (response.status === 202) return {kind:'progress',progress:await response.json()};
      if (!response.ok) { const error = await response.json(); fail(error?.error ?? 'result_unknown'); }
      if (response.status !== 200) fail('unexpected_delivery_response');
      const bytes = await boundedBytes(response), media_type = response.headers?.get('content-type') ?? '';
      let value = null, display_error = null;
      if (!/^(?:text\/[^;]+|application\/(?:json|[^;]+\+json))(?:;|$)/i.test(media_type)) display_error = 'unsupported_media_type';
      else { try { value = new TextDecoder('utf-8',{fatal:true}).decode(bytes); } catch { display_error = 'invalid_utf8'; } }
      return {kind:'body',value,media_type,byte_count:bytes.byteLength,display_error};
    },
  };
}
function checkedCatalog(value,source){
  if (value?.schema_version !== HUMAN_READ_CATALOG || value.action !== 'read_catalog'
      || value.source_id !== source || typeof value.read_allowed !== 'boolean'
      || value.body_mode !== 'explicit_original_delivery' || !Array.isArray(value.targets)
      || value.targets.some(item => !text(item.id) || !text(item.label))
      || value.targets.some(item => Object.hasOwn(item,'transition_id') && !text(item.transition_id))
      || new Set(value.targets.map(item => item.id)).size !== value.targets.length
      || (!value.read_allowed && value.targets.length)) fail('invalid_read_catalog');
  return copy(value);
}
function checkedProgress(value,id,resource = null){
  if (value?.protocol !== HUMAN_READ_PROGRESS || value.command_id !== id || !STATES.has(value.state)
      || typeof value.can_issue_new_delivery !== 'boolean' || !Number.isSafeInteger(value.as_of) || value.as_of < 0)
    fail('invalid_read_progress');
  if (value.state !== 'not_found_at_read_cut'
      && (!exactRef(value.resource_ref) || (resource && !same(exactRef(value.resource_ref),resource)))) fail('invalid_read_progress');
  if (!CONTINUABLE.has(value.state) && value.can_issue_new_delivery) fail('invalid_read_progress');
  if (value.state === 'completed' && (!exactRef(value.result_ref) || value.result_kind !== 'read_completed' || value.error !== null))
    fail('invalid_read_progress');
  return copy(value);
}
function commandId(){
  if (typeof globalThis.crypto?.randomUUID !== 'function') fail('command_identity_unavailable');
  return `human-read:${globalThis.crypto.randomUUID()}`;
}

/** Disposable UI state; only original progress can authorize a continuation. */
export function createHumanMaterialReader({api,onChange = () => {},makeCommandId = commandId} = {}){
  if (!api || ['readCatalog','readProgress','deliver'].some(key => typeof api[key] !== 'function')) fail('human_read_api_required');
  let source = null, selected = null, catalog = null, targetId = null, body = null, error = null;
  let epoch = 0, catalogSerial = 0, catalogController = null, loading = false, lookup = null, lookupSerial = 0, lookupLoading = false;
  const cards = new Map(), usedIds = new Set();
  const eligible = () => selected?.availability === 'available' && selected.material_access === 'explicit_delivery_required';
  const targetAllowed = id => catalog?.read_allowed === true && catalog.targets.some(item => item.id === id);
  const sameResource = card => card.source_id === source && selected && same(card.resource_ref,selected.resource_ref);
  const matching = card => sameResource(card) && card.target_id === targetId;
  const latest = () => [...cards.values()].reverse().find(matching);
  const canContinue = card => matching(card) && eligible() && targetAllowed(card.target_id)
    && !loading && !lookupLoading && !card.sending && !card.querying && card.continueEpoch === epoch
    && CONTINUABLE.has(card.progress?.state) && card.progress.can_issue_new_delivery === true;
  function editableCard(){
    if (!body || loading || lookupLoading || !eligible() || selected.content_schema_ref !== AGENT_TEXT_TYPE) return null;
    const card = cards.get(operationKey(body.source_id,body.command_id));
    return card && matching(card) && !card.sending && !card.querying && !card.display_error
      && card.content_schema_ref === AGENT_TEXT_TYPE && card.body_received && typeof body.value === 'string'
      && card.progress?.state === 'completed' && card.progress.result_kind === 'read_completed'
      && card.progress.error === null ? card : null;
  }
  const view = card => ({command_id:card.command_id,source_id:card.source_id,target_id:card.target_id,
    target_label:card.target_label,target_transition:card.target_transition,recovered:card.recovered,
    resource_ref:copy(card.resource_ref),state:card.state,
    sending:card.sending,querying:card.querying,body_received:card.body_received,
    display_error:card.display_error,error:card.error,progress:copy(card.progress),can_continue:canContinue(card)});
  function getState(){
    const previous = latest(), unresolved = [...cards.values()].some(card => sameResource(card)
      && (card.sending || card.querying || !TERMINAL.has(card.progress?.state)));
    return {source,selected:copy(selected),catalog:copy(catalog),target_id:targetId,body:copy(body),error,loading,lookupLoading,
      can_read:!loading && !lookupLoading && eligible() && targetAllowed(targetId) && !unresolved,
      can_edit_received:Boolean(editableCard()),unresolved_read:unresolved,
      repeat:Boolean(previous),operations:[...cards.values()].map(view),lookup:copy(lookup)};
  }
  const emit = () => onChange(getState());
  function clearBody(){ body = null; }
  const ownsBody = card => body?.command_id === card.command_id && body.source_id === card.source_id;
  function resolveRecoveredTarget(card){
    if (!card.recovered) return;
    const matches = card.source_id === source && catalog?.read_allowed && text(card.target_transition)
      ? catalog.targets.filter(item => item.transition_id === card.target_transition) : [];
    const target = matches.length === 1 ? matches[0] : null;
    card.target_id = target?.id ?? null;
    card.target_label = target?.label ?? card.target_transition ?? card.command_id;
  }
  function applyProgress(card,result,observedEpoch){
    if (text(card.target_transition) && result.state !== 'not_found_at_read_cut'
        && result.target !== card.target_transition) fail('invalid_read_progress');
    if (!card.target_transition && text(result.target)) card.target_transition = result.target;
    card.progress = result; card.state = result.state; card.error = null;
    resolveRecoveredTarget(card);
    card.continueEpoch = observedEpoch === epoch ? epoch : -1;
  }
  function cancelCatalog(){ catalogSerial++; catalogController?.abort(); catalogController = null; loading = false; }
  function select(state){
    const nextSource = state.source ?? null, record = state.material ?? state.record;
    const resource = selectedRef(record?.entry_ref,nextSource);
    const next = resource ? {resource_ref:resource,availability:record.availability ?? 'unknown',
      availability_as_of:record.availability_as_of ?? null,material_access:record.material_access ?? null,
      content_schema_ref:record.fields?.content_schema_ref ?? null} : null;
    if (nextSource !== source || !same(next,selected) || state.error) {
      epoch++; clearBody(); lookupSerial++; lookup = null; lookupLoading = false;
      if (nextSource !== source || state.error) { cancelCatalog(); catalog = null; targetId = null; }
      source = nextSource; selected = next; error = state.error ?? null;
    }
    emit();
  }
  async function loadCatalog(){
    if (!source) return;
    cancelCatalog(); epoch++; clearBody(); catalog = null; targetId = null; error = null;
    const serial = catalogSerial, originalSource = source; catalogController = new AbortController(); loading = true; emit();
    try {
      const result = checkedCatalog(await api.readCatalog(originalSource,catalogController.signal),originalSource);
      if (serial !== catalogSerial || originalSource !== source) return;
      catalog = result; targetId = result.targets[0]?.id ?? null;
      for (const card of cards.values()) resolveRecoveredTarget(card);
    } catch (failure) { if (serial === catalogSerial) { error = code(failure); clearBody(); } }
    finally { if (serial === catalogSerial) { loading = false; catalogController = null; emit(); } }
  }
  function chooseTarget(id){
    if (!targetAllowed(id)) return;
    if (id !== targetId) { targetId = id; epoch++; clearBody(); }
    emit();
  }
  async function query(id,originalSource = source){
    const card = cards.get(operationKey(originalSource,id)); if (!card || card.sending || card.querying) return;
    const observedEpoch = epoch; card.querying = true; card.continueEpoch = -1; card.error = null; emit();
    try {
      const result = checkedProgress(await api.readProgress({source_id:card.source_id,material_command_id:id}),id,card.resource_ref);
      applyProgress(card,result,observedEpoch);
      if (result.state === 'unknown' || result.state === 'rejected' || result.state === 'not_found_at_read_cut') {
        if (ownsBody(card)) clearBody();
      }
    } catch (failure) { card.error = code(failure); card.state = 'unknown'; card.progress = null; if (ownsBody(card)) clearBody(); }
    finally { card.querying = false; emit(); }
  }
  async function deliver(card){
    const displayEpoch = epoch; card.sending = true; card.continueEpoch = -1; card.error = null;
    card.progress = null; card.state = 'sending'; clearBody(); emit();
    try {
      const result = await api.deliver({source_id:card.source_id,target_id:card.target_id,
        command_id:card.command_id,resource_ref:copy(card.resource_ref)});
      if (result?.kind === 'progress') {
        applyProgress(card,checkedProgress(result.progress,card.command_id,card.resource_ref),-1);
      } else if (result?.kind === 'body' && Number.isSafeInteger(result.byte_count) && result.byte_count >= 0
          && result.byte_count <= MAX_BODY_BYTES && (typeof result.value === 'string' || result.display_error)) {
        card.body_received = true; card.display_error = result.display_error; card.state = 'body_received';
        let value = result.value;
        if (!card.display_error && card.content_schema_ref === AGENT_TEXT_TYPE) {
          try { value = JSON.parse(value); if (typeof value !== 'string') throw new Error(); }
          catch { card.display_error = 'invalid_document_type'; value = null; }
        }
        if (displayEpoch === epoch && matching(card) && eligible() && !card.display_error) {
          body = {command_id:card.command_id,source_id:card.source_id,resource_ref:copy(card.resource_ref),value,
            media_type:result.media_type,byte_count:result.byte_count};
        }
      } else fail('invalid_delivery_response');
    } catch (failure) { card.state = 'unknown'; card.error = code(failure); card.progress = null; if (ownsBody(card)) clearBody(); }
    finally { card.sending = false; emit(); }
    // One metadata lookup after received bytes; this never issues another POST.
    if (card.state === 'body_received') await query(card.command_id,card.source_id);
    return view(card);
  }
  function read(){
    if (!getState().can_read) return;
    const id = makeCommandId(), key = operationKey(source,id);
    if (!text(id) || id.length > 512 || usedIds.has(key)) fail('command_identity_unavailable');
    usedIds.add(key);
    const card = {command_id:id,source_id:source,target_id:targetId,
      target_label:catalog.targets.find(item => item.id === targetId).label,
      target_transition:catalog.targets.find(item => item.id === targetId).transition_id ?? null,recovered:false,
      resource_ref:copy(selected.resource_ref),content_schema_ref:selected.content_schema_ref,
      state:'sending',sending:false,querying:false,progress:null,continueEpoch:-1,body_received:false,display_error:null,error:null};
    cards.set(key,card); return deliver(card);
  }
  function continueRead(id,originalSource = source){
    const card = cards.get(operationKey(originalSource,id)); if (!card || !canContinue(card)) return;
    if (card.recovered) card.content_schema_ref = selected.content_schema_ref;
    return deliver(card);
  }
  async function readEarlier(id){
    if (!source || lookupLoading || !text(id) || id.length > 512) return;
    const existing = cards.get(operationKey(source,id));
    if (existing && (existing.sending || existing.querying)) return;
    const serial = ++lookupSerial, originalSource = source, observedEpoch = epoch;
    lookup = null; lookupLoading = true; error = null; clearBody(); emit();
    try {
      const result = checkedProgress(await api.readProgress({source_id:originalSource,material_command_id:id}),id,existing?.resource_ref);
      let card = existing;
      if (!card && result.state !== 'not_found_at_read_cut' && !TERMINAL.has(result.state)) {
        card = {command_id:id,source_id:originalSource,target_id:null,target_label:id,target_transition:null,recovered:true,
          resource_ref:exactRef(result.resource_ref),content_schema_ref:null,state:result.state,sending:false,querying:false,
          progress:null,continueEpoch:-1,body_received:false,display_error:null,error:null};
        cards.set(operationKey(originalSource,id),card);
      }
      if (result.state !== 'not_found_at_read_cut') usedIds.add(operationKey(originalSource,id));
      if (card) applyProgress(card,result,observedEpoch);
      if (serial === lookupSerial && originalSource === source) lookup = result;
    } catch (failure) {
      if (existing) { existing.error = code(failure); existing.state = 'unknown'; existing.progress = null; existing.continueEpoch = -1; }
      if (serial === lookupSerial && originalSource === source) error = code(failure);
    }
    if (serial === lookupSerial) lookupLoading = false;
    emit();
  }
  function conceal(){ epoch++; clearBody(); cancelCatalog(); catalog = null; targetId = null; lookupSerial++; lookup = null; lookupLoading = false; emit(); }
  function captureEdit(){
    const card = editableCard(); if (!card) return null;
    const originalBody = body, originalEpoch = epoch;
    const isCurrent = () => body === originalBody && epoch === originalEpoch && editableCard() === card;
    return {content:copy({source_id:card.source_id,resource_ref:card.resource_ref,
      read_command_id:card.command_id,document_type:AGENT_TEXT_TYPE,complete:true,value:body.value}),isCurrent,
      invalidate(){ if (body === originalBody && epoch === originalEpoch) conceal(); }};
  }
  return {getState,select,loadCatalog,chooseTarget,read,query,continueRead,readEarlier,conceal,captureEdit};
}

const STRINGS = {
  en:{title:'Read this exact material',load:'Check body-reading capability',choose:'Select an exact resource record first.',
    closed:'This source has not enabled an explicit body reader.',allowed:'Read only the selected exact version through a disclosed target.',
    disabled:'This version is disabled. Its structural history remains visible.',unknown:'Current availability is unknown; body reading is closed.',
    legacy:'Use the record’s disclosed preview action for this source.',loading:'Checking permitted reading targets…',target:'Reading target',
    read:'Read this exact version',repeat:'Read again as a new request',context:'Exact version and current availability',
    hint:'Browsing and checking status do not deliver a body. A received body and an Owner-confirmed result are shown separately.',
    body:'Received text',bodyHint:'Displayed only in this view. Navigating away clears it; this does not erase copies already received elsewhere.',
    editReceived:'Edit this complete received text',
    editHint:'Available after the original read completes. Checks current save targets, then opens an independent editing copy.',
    operations:'Original reading requests',query:'Query original result',continue:'Continue this same request',
    continueHint:'Available only after the original Owner confirms no delivery has been released. Uses the same command identity.',
    unresolved:'This exact version has an unresolved original request. Query its original card before another read.',
    unmapped:'This original target cannot be matched to one disclosed reading target. Only status queries are available.',
    received:'Body bytes received; the original result is reported separately.',displayError:'Received bytes cannot be displayed as complete text:',
    identity:'Original identity and facts',lookup:'Find an earlier reading request',lookupId:'Original command identity',lookupRead:'Read original status',
    lookupHint:'Queries original facts and restores unresolved requests. It does not deliver a body or recreate a command.',error:'The request could not be confirmed:',
    state:{sending:'Request in progress',body_received:'Text received; checking original result',not_found_at_read_cut:'Not found at this observation',
      accepted_waiting:'Accepted; waiting for admission',admitted_waiting_delivery:'Admitted; waiting for explicit delivery',release_authorized:'Delivery released; result pending',
      delivered_pending_settlement:'Transport closed; Owner settlement pending',completed:'Original Owner confirmed completion',rejected:'Original request rejected',unknown:'Original result unknown'}},
  'zh-CN':{title:'读取此精确版本正文',load:'查看正文读取能力',choose:'请先选择一条精确资料记录。',
    closed:'此来源未开放显式正文读取。',allowed:'通过已披露目标读取当前选中的精确版本。',
    disabled:'此版本已停用，允许查看的结构历史仍保留。',unknown:'当前可用性未知，正文读取已关闭。',
    legacy:'此来源请使用记录中已披露的正文预览入口。',loading:'正在核查允许的读取目标…',target:'读取目标',
    read:'读取此精确版本',repeat:'发起一次新的正文读取',context:'精确版本与当前可用性',
    hint:'浏览和回查状态不会交付正文。正文收到与原 Owner 确认完成分别显示。',
    body:'本次收到的文本',bodyHint:'正文只在当前视图展示，离开后清空；这不代表删除其它地方已经收到的副本。',
    editReceived:'编辑本次完整文本',
    editHint:'原读取确认完成后，核查当前保存目标，再打开独立编辑副本。',
    operations:'原始读取请求',query:'回查原结果',continue:'继续本次读取',
    continueHint:'仅在原 Owner 刚确认尚未放行交付时可用，继续使用同一命令标识。',
    unresolved:'此精确版本仍有未终结的原请求，请从原命令卡回查后再读取。',
    unmapped:'原目标无法与当前披露的读取目标唯一对应，目前只允许回查状态。',
    received:'已收到正文；原始执行结果另行显示。',displayError:'已收到的字节无法作为完整文本展示：',
    identity:'原标识与事实',lookup:'查找此前的读取请求',lookupId:'原命令标识',lookupRead:'读取原状态',
    lookupHint:'回查原事实并恢复未终结请求，不交付正文，也不重建命令。',error:'本次请求未能确认：',
    state:{sending:'请求执行中',body_received:'已收到文本，正在核查原结果',not_found_at_read_cut:'截至本次观察未找到',
      accepted_waiting:'已接收，等待准入',admitted_waiting_delivery:'已准入，等待显式交付',release_authorized:'交付已获放行，结果待定',
      delivered_pending_settlement:'传输已关闭，等待原 Owner 结算',completed:'原 Owner 已确认完成',rejected:'原请求已拒绝',unknown:'原结果未知'}},
};

export function mountHumanMaterialReader(document,{root,fetcher = globalThis.fetch,makeCommandId,onEditReceived = null} = {}){
  if (!root) fail('human_read_panel_required');
  let language = 'en', editing = false; const nodes = {}, labels = [], cardNodes = new Map();
  const t = key => STRINGS[language][key] ?? key;
  const el = (tag,key = null) => { const node = document.createElement(tag); if (key) nodes[key] = node; return node; };
  const label = (node,key) => { labels.push([node,key]); node.textContent = t(key); return node; };
  const button = (key,action) => { const node = label(el('button',key),key); node.type = 'button';
    node.addEventListener('click',() => Promise.resolve().then(action).catch(error => {
      nodes.notice.textContent = `${t('error')} ${code(error)}`;
    })); return node; };
  root.className = 'material-management human-material-reader';
  root.append(label(el('h2'),'title'),label(el('p'),'hint'),button('load',() => controller.loadCatalog()),el('p','notice'));
  nodes.notice.setAttribute('role','status'); nodes.notice.setAttribute('aria-live','polite');
  const selected = el('details'); selected.append(label(el('summary'),'context'),el('pre','context')); root.append(selected);
  const targetLabel = el('label'), target = el('select','target'); targetLabel.append(label(el('span'),'target'),target);
  target.addEventListener('change',() => controller.chooseTarget(target.value)); root.append(targetLabel,button('read',() => controller.read()));
  const body = el('section','bodySection'); body.hidden = true;
  body.append(label(el('h3'),'body'),label(el('p'),'bodyHint'),el('pre','body'),button('editReceived',async () => {
    if (editing || typeof onEditReceived !== 'function') return;
    const selection = controller.captureEdit(); if (!selection) return;
    editing = true; render(controller.getState());
    try { await onEditReceived(selection); }
    finally { editing = false; render(controller.getState()); }
  }),label(el('p','editHint'),'editHint')); nodes.body.tabIndex = 0; root.append(body);
  root.append(label(el('h3'),'operations'),el('div','operations'));
  const lookup = el('details'), lookupLabel = el('label'); lookup.append(label(el('summary'),'lookup'));
  const identity = el('input','lookupId'); identity.type = 'text'; identity.maxLength = 512;
  lookupLabel.append(label(el('span'),'lookupId'),identity);
  lookup.append(lookupLabel,button('lookupRead',() => controller.readEarlier(identity.value)),label(el('p'),'lookupHint'),el('pre','lookupResult')); root.append(lookup);
  function render(state){
    for (const [node,key] of labels) node.textContent = t(key);
    nodes.load.disabled = !state.source || state.loading; nodes.lookupRead.disabled = !state.source || state.lookupLoading;
    const item = state.selected;
    const reason = !item ? 'choose' : item.availability === 'disabled' ? 'disabled' : item.availability !== 'available' ? 'unknown'
      : item.material_access !== 'explicit_delivery_required' ? 'legacy' : !state.catalog?.read_allowed ? 'closed'
      : state.unresolved_read ? 'unresolved' : 'allowed';
    nodes.notice.textContent = state.loading ? t('loading') : state.error ? `${t('error')} ${state.error}`
      : `${t(reason)}${state.catalog?.reason ? ' ' + state.catalog.reason : ''}`;
    nodes.context.textContent = item ? JSON.stringify({source_id:state.source,...item},null,2) : '';
    const targets = state.catalog?.targets ?? [];
    if (nodes.target.dataset.targets !== JSON.stringify(targets)) {
      nodes.target.dataset.targets = JSON.stringify(targets); nodes.target.replaceChildren();
      for (const item of targets) { const option = el('option'); option.value = item.id; option.textContent = item.label; nodes.target.append(option); }
    }
    nodes.target.value = state.target_id ?? ''; nodes.target.disabled = !targets.length || state.loading;
    nodes.read.textContent = t(state.repeat ? 'repeat' : 'read'); nodes.read.disabled = !state.can_read;
    nodes.bodySection.hidden = !state.body; nodes.body.textContent = state.body?.value ?? '';
    nodes.editReceived.hidden = nodes.editHint.hidden = typeof onEditReceived !== 'function';
    nodes.editReceived.disabled = editing || !state.can_edit_received;
    for (const card of state.operations) {
      const key = operationKey(card.source_id,card.command_id);
      let view = cardNodes.get(key);
      if (!view) {
        const article = el('article'); article.className = 'material-command';
        const status = el('h4'), context = el('p'), receipt = el('p'), error = el('p'), facts = el('pre'), actions = el('div'); actions.className = 'material-actions';
        const query = el('button'), proceed = el('button'); query.type = proceed.type = 'button';
        query.addEventListener('click',() => controller.query(card.command_id,card.source_id));
        proceed.addEventListener('click',() => controller.continueRead(card.command_id,card.source_id));
        const hint = el('p'), details = el('details'), summary = el('summary'); details.append(summary,facts);
        actions.append(query,proceed); article.append(status,context,receipt,error,actions,hint,details); nodes.operations.append(article);
        view = {status,context,receipt,error,facts,query,proceed,hint,summary}; cardNodes.set(key,view);
      }
      view.status.textContent = STRINGS[language].state[card.state] ?? card.state;
      view.context.textContent = `${card.source_id} · ${card.target_label} · ${card.resource_ref.version_id}`;
      view.receipt.textContent = card.display_error ? `${t('displayError')} ${card.display_error}` : card.body_received ? t('received') : '';
      view.error.textContent = card.error ? `${t('error')} ${card.error}` : '';
      view.query.textContent = t('query'); view.query.disabled = card.sending || card.querying;
      view.proceed.textContent = t('continue'); view.proceed.hidden = !card.can_continue; view.proceed.disabled = !card.can_continue;
      view.hint.textContent = card.can_continue ? t('continueHint') : card.recovered && !card.target_id ? t('unmapped') : '';
      view.summary.textContent = t('identity'); view.facts.textContent = JSON.stringify({command_id:card.command_id,
        source_id:card.source_id,target_id:card.target_id,target_transition:card.target_transition,
        resource_ref:card.resource_ref,progress:card.progress},null,2);
    }
    nodes.lookupResult.textContent = state.lookup ? JSON.stringify(state.lookup,null,2) : '';
  }
  const controller = createHumanMaterialReader({api:createHumanReadHttpApi(fetcher),makeCommandId,onChange:render});
  document.defaultView?.addEventListener?.('pagehide',() => controller.conceal()); render(controller.getState());
  return {controller,nodes,setBrowserState:state => controller.select(state),
    setLanguage(value){ language = value === 'zh-CN' ? 'zh-CN' : 'en'; render(controller.getState()); }};
}
