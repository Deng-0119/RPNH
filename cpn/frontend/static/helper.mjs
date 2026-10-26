/** Independent help entry point: graph/app import failures do not disable guidance. */
import {t as tr, getLanguage, setLanguage, applyLanguage} from './i18n.mjs';

export function helpShortcut(event) {
  if (event.key !== '?' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || event.defaultPrevented) return false;
  const target = event.target;
  return !target?.isContentEditable && !target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"])');
}
export function cleanCatalog(raw) {
  if (raw?.schema !== 'rpnh/help/v1' || !Array.isArray(raw.topics) || raw.topics.length > 64) throw Error('Invalid help catalog');
  const ids = new Set();
  for (const topic of raw.topics) {
    if (!/^[a-z][a-z0-9_-]{0,39}$/.test(topic.id) || ids.has(topic.id)) throw Error('Invalid help topic');
    ids.add(topic.id);
    for (const language of ['en','zh-CN']) {
      if (typeof topic.title?.[language] !== 'string' || typeof topic.summary?.[language] !== 'string'
          || !Array.isArray(topic.steps?.[language]) || !topic.steps[language].every(x=>typeof x==='string')) throw Error('Invalid help text');
    }
  }
  return raw;
}
export function installHelper(document, window, fetcher = fetch) {
  const $ = id => document.getElementById(id), dialog = $('guide');
  if (!dialog || !$('help')) return null;
  let catalog = null, selected = 'viewer', opener = null;
  const text = (tag, value, className) => {const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;};
  const localized = value => value?.[getLanguage()] ?? value?.en ?? '';
  function contextText() {
    const context = window.rpnhViewerContext;
    const diagnostic = window.rpnhViewerDiagnostic;
    if (diagnostic) return tr('最近读取问题：{0}；症状代码：{1}。此分类不是根因结论。', diagnostic.component, diagnostic.code);
    if (!context) return tr('帮助独立于图形组件；图形启动失败时仍可阅读。');
    const view = {overview:tr('智能体概览'),flow:tr('详细流程'),petri:tr('Petri 网'),list:tr('执行列表')}[context.mode] ?? '';
    return tr('当前视图：{0}。{1}', view, context.selection ? tr('所选对象：{0}', context.selection) : tr('尚未选择对象'));
  }
  function drawAnswer() {
    $('helper-context').textContent = contextText();
    const container=$('help-answer');container.replaceChildren();
    if (!catalog) {container.append(text('p',tr('帮助主题暂不可读。下方基本指南与页面底部离线帮助仍可使用。')));return;}
    const topic=catalog.topics.find(t=>t.id===selected) ?? catalog.topics[0];if(!topic)return;
    container.append(text('h3',localized(topic.title)),text('p',localized(topic.summary)));
    const steps=text('ol','');for(const step of topic.steps[getLanguage()])steps.append(text('li',step));container.append(steps);
    const location=text('details','');location.append(text('summary',tr('内部位置与完整文档')));
    location.append(text('p',String(topic.owner ?? ''),'mono'));
    // Curated repository locations are text, never arbitrary links or command execution.
    for(const path of [...(topic.source_paths??[]),...(topic.docs??[])]) if(typeof path==='string')location.append(text('p',path,'mono'));
    container.append(location,text('p',`rpnh help ${topic.id} --language ${getLanguage()}`,'mono'));
  }
  function drawTopics() {
    const container=$('help-topics');container.replaceChildren();if(!catalog)return;
    const term=$('help-search').value.trim().toLocaleLowerCase();
    for(const topic of catalog.topics) {
      if(term && ![topic.id,localized(topic.title),localized(topic.summary)].join(' ').toLocaleLowerCase().includes(term))continue;
      const button=text('button',localized(topic.title));button.type='button';button.setAttribute('aria-pressed',String(topic.id===selected));
      button.onclick=()=>{selected=topic.id;drawTopics();drawAnswer();};container.append(button);
    }
    if(!container.children.length)container.append(text('p',tr('未找到主题；请尝试界面、连接或恢复。')));
  }
  function open(topic='viewer', trigger=null) {
    selected=topic;opener=trigger??document.activeElement;drawTopics();drawAnswer();
    if(!dialog.open)dialog.showModal();$('help-search').focus();
  }
  function update() {
    if(dialog.open){drawTopics();drawAnswer();}
    const diagnostic=window.rpnhViewerDiagnostic;
    $('error-help').hidden=!diagnostic;
    $('error-help').dataset.helpTopic=diagnostic?.topic??'connection';
  }
  $('help').onclick=event=>open(window.rpnhViewerDiagnostic?.topic??'viewer',event.currentTarget);
  $('close-help').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{if(opener?.isConnected!==false)opener?.focus?.();});
  for(const id of ['view-help','selection-help','error-help']) $(id)?.addEventListener('click',event=>open(event.currentTarget.dataset.helpTopic??'legend',event.currentTarget));
  $('help-search').addEventListener('input',drawTopics);
  document.addEventListener('keydown',event=>{
    if (!helpShortcut(event) || document.querySelector('dialog[open]'))return;
    event.preventDefault();open(window.rpnhViewerDiagnostic?.topic??'viewer');
  });
  $('language')?.addEventListener('change',()=>{setLanguage($('language').value);applyLanguage(document);drawTopics();drawAnswer();});
  const api={open,update};window.rpnhViewerHelp=api;
  const controller=new AbortController(), deadline=setTimeout(()=>controller.abort(),8000);
  Promise.resolve().then(()=>fetcher('/help-catalog.json',{cache:'no-store',signal:controller.signal}))
    .then(response=>{if(!response.ok)throw Error('Help unavailable');return response.json();})
    .then(raw=>{catalog=cleanCatalog(raw);drawTopics();drawAnswer();})
    .catch(()=>{drawAnswer();}).finally(()=>clearTimeout(deadline));
  window.addEventListener('pagehide',()=>controller.abort(),{once:true});
  update();return api;
}
if(typeof document!=='undefined' && typeof window!=='undefined')installHelper(document,window);
