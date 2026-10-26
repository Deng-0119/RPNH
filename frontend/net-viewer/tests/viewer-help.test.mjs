import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {helpShortcut,cleanCatalog,installHelper} from '../../../cpn/frontend/static/helper.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
const catalog=JSON.parse(readFileSync(new URL('../../../cpn/frontend/static/help-catalog.json',import.meta.url),'utf8'));
const index=readFileSync(new URL('../../../cpn/frontend/static/index.html',import.meta.url),'utf8');
const walk=n=>[n,...n.children.flatMap(walk)];
class Element extends EventTarget {
  constructor(tag,document){super();this.tagName=tag;this.document=document;this.children=[];this.attributes={};this.dataset={};this.value='';this.open=false;this.hidden=false;this.isConnected=true;this.text='';}
  set innerHTML(_){throw Error('Untrusted HTML assignment');}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(n=>n.textContent).join('');}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.text='';this.children=children;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  getAttribute(k){return this.attributes[k];}
  focus(){this.document.activeElement=this;}
  showModal(){this.open=true;}
  close(){this.open=false;this.dispatchEvent(new Event('close'));}
}
function dom(){const ids=new Map();const document=Object.assign(new EventTarget(),{createElement:tag=>new Element(tag,document),getElementById:id=>{if(!ids.has(id))ids.set(id,new Element(id==='guide'?'dialog':'button',document));return ids.get(id);},querySelector:selector=>selector==='dialog[open]'&&document.getElementById('guide').open?document.getElementById('guide'):null,querySelectorAll:()=>[]});document.documentElement=new Element('html',document);return {document,window:new EventTarget()};}
const settle=()=>new Promise(r=>setImmediate(r));

test('helper is a separate module entry and native offline fallback is outside the dialog',()=>{
  assert.match(index,/<script src="\/helper.mjs" type="module"><\/script><script src="\/app.js"/);
  assert.match(index,/<\/dialog>[\s\S]*<details class="offline-help">/);
  assert.match(index,/aria-labelledby="guide-title"/);assert.doesNotMatch(index,/Three levels of detail/);
});
test('? shortcut respects editable controls, browser modifiers and composition',()=>{
  const base={key:'?',target:{closest:()=>null}};assert.equal(helpShortcut(base),true);
  for(const patch of [{isComposing:true},{ctrlKey:true},{metaKey:true},{altKey:true},{defaultPrevented:true},{key:'h'},{target:{isContentEditable:true}},{target:{closest:()=>({})}}])assert.equal(helpShortcut({...base,...patch}),false);
});
test('shared catalog has exact bilingual text and rejects duplicated or malformed topics',()=>{
  assert.equal(cleanCatalog(catalog),catalog);assert.ok(catalog.topics.some(t=>t.id==='provider'));
  for(const mutate of [x=>x.schema='other',x=>x.topics.push(x.topics[0]),x=>delete x.topics[0].steps.en]){const bad=structuredClone(catalog);mutate(bad);assert.throws(()=>cleanCatalog(bad));}
});
test('helper operates without app/renderer, restores focus, localizes and never interprets HTML',async()=>{
  setLanguage('en');const {document,window}=dom();const hostile=structuredClone(catalog);hostile.topics[0].title.en='<img src=x onerror=alert(1)>';
  hostile.topics[0].docs=['javascript:alert(2)'];const api=installHelper(document,window,async()=>({ok:true,json:async()=>hostile}));await settle();
  const opener=document.getElementById('help');opener.focus();api.open('basic',opener);
  assert.equal(document.getElementById('guide').open,true);assert.equal(document.activeElement,document.getElementById('help-search'));
  assert.ok(document.getElementById('help-answer').textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(walk(document.getElementById('help-answer')).some(n=>['img','script','a'].includes(n.tagName)),false);
  document.getElementById('guide').close();assert.equal(document.activeElement,opener);
  document.getElementById('language').value='zh-CN';document.getElementById('language').dispatchEvent(new Event('change'));api.open('viewer',opener);
  assert.ok(document.getElementById('help-answer').textContent.includes('看懂当前 Viewer'));
  document.getElementById('help-search').value='provider';document.getElementById('help-search').dispatchEvent(new Event('input'));
  assert.equal(document.getElementById('help-topics').children.length,1);
  setLanguage('en');
});
test('catalog failure keeps functioning modal and basic offline guidance instead of disabling help',async()=>{
  const {document,window}=dom();const api=installHelper(document,window,async()=>{throw Error('offline');});await settle();api.open();
  assert.equal(document.getElementById('guide').open,true);
  assert.match(document.getElementById('help-answer').textContent,/Help topics are unavailable/);
  window.rpnhViewerDiagnostic={topic:'assets',code:'viewer_assets_unavailable',component:'viewer / startup'};api.update();
  assert.equal(document.getElementById('error-help').hidden,false);
  assert.match(document.getElementById('helper-context').textContent,/Last read problem/);
});
