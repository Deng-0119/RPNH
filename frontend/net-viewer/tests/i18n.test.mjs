import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EN } from '../../../cpn/frontend/static/messages.mjs';
import { t, getLanguage, setLanguage, readLanguage, LANGUAGE_KEY, messageError } from '../../../cpn/frontend/static/i18n.mjs';
import { normalizeFrame, displayGraph, nodePresentation, cardState, TimelineState } from '../../../cpn/frontend/static/dashboard-model.mjs';
import { topologyKey, referenceText, wrapLabel } from '../../../cpn/frontend/static/model.mjs';
import { LayoutCache, geometry } from '../../../cpn/frontend/static/layout.mjs';
import ELK from 'elkjs/lib/elk.bundled.js';
const root=new URL('../../../cpn/frontend/static/',import.meta.url);
const sample=()=>normalizeFrame({schema_version:'rpnh/net_view/v1',source:{mode:'registry_current',verified_head_ordinal:5},summary:{},nodes:[
 {id:'in',label:'in',kind:'place',category:'place',hidden_by_default:false,active_token_count:1},
 {id:'worker.run',label:'worker.run',kind:'transition',category:'execution',hidden_by_default:false,inputs:['in'],outputs:[],runtime:{firings:[{status:'started',firing_ref:'exact-firing'}]}}
],edges:[{id:'exact-edge',source:'in',target:'worker.run',kind:'arc',mode:'consume',weight:1,outcome:null,hidden_by_default:false}]});

test('English is the default regardless of browser language or invalid saved values',()=>{
 assert.equal(getLanguage(),'en');assert.equal(readLanguage(null),'en');
 for(const value of [null,'zh','fr','<script>','__proto__'])assert.equal(readLanguage({getItem:()=>value}),'en');
 assert.equal(readLanguage({getItem:()=> 'zh-CN'}),'zh-CN');
 assert.equal(readLanguage({getItem:()=>{throw new Error('denied');}}),'en');
});
test('all UI translations are nonempty with identical interpolation slots',()=>{
 assert.ok(Object.keys(EN).length>240);
 for(const [key,value] of Object.entries(EN)){
  assert.ok(value.trim());assert.doesNotMatch(value,/[\u3400-\u9fff]/);
  assert.deepEqual([...(value.matchAll(/\{(\d+)\}/g))].map(m=>m[1]).sort(),[...(key.matchAll(/\{(\d+)\}/g))].map(m=>m[1]).sort(),key);
 }
});
test('static English shell and every marked text or accessible attribute have translations',()=>{
 const html=readFileSync(new URL('index.html',root),'utf8');assert.match(html,/<html lang="en">/);
 assert.match(html,/id="language"/);assert.match(html,/>English<\/option>/);assert.match(html,/>中文<\/option>/);
 for(const [,key] of html.matchAll(/data-i18n(?:-aria-label|-placeholder)?="([^"]+)"/g))assert.ok(Object.hasOwn(EN,key),key);
 assert.match(html,/>Run dashboard<\/small>/);assert.match(html,/>Run timeline<\/strong>/);
});
test('switching language is explicit, persists when possible and tolerates blocked storage',()=>{
 const saved=new Map(),storage={setItem:(k,v)=>saved.set(k,v),getItem:k=>saved.get(k)};
 setLanguage('zh-CN',storage);assert.equal(saved.get(LANGUAGE_KEY),'zh-CN');assert.equal(t('运行流程'),'运行流程');
 setLanguage('en',{setItem:()=>{throw new Error('quota');}});assert.equal(t('运行流程'),'Run workflow');
 assert.throws(()=>setLanguage('fr',storage),RangeError);assert.equal(getLanguage(),'en');
});
test('interpolated user text and exact references are never translated or executed',()=>{
 setLanguage('en',null);const raw='未提供 <img src=x onerror=alert(1)> {1}';
 assert.equal(t('第 {0} 次 · {1}',2,raw),`Attempt 2 · ${raw}`);
 assert.equal(referenceText({version_id:{value:'未提供'}}),'未提供');
 assert.throws(()=>t('not-a-catalog-key'));
});
test('built-in errors follow current language, without changing external errors',()=>{
 setLanguage('en',null);const error=messageError('无效的历史位置');assert.equal(error.message,'Invalid historical position');
 setLanguage('zh-CN',null);assert.equal(error.message,'无效的历史位置');
 setLanguage('en',null);assert.equal(new Error('外部原始错误').message,'外部原始错误');
});
test('locale changes cards, status and edges but not raw frames, scope or timeline',()=>{
 const f=sample();f.boundaries.entry=[{place:'in'}];const before=JSON.stringify(f),timeline=new TimelineState();timeline.select(5);
 setLanguage('en',null);const en=displayGraph(f);assert.equal(nodePresentation(f.net.nodes[0],f).name,'Receive task');assert.match(cardState(f.net.nodes[1]),/Unsettled/);
 setLanguage('zh-CN',null);const zh=displayGraph(f);assert.equal(nodePresentation(f.net.nodes[0],f).name,'接收任务');assert.match(cardState(f.net.nodes[1]),/未结算/);
 assert.equal(topologyKey(en),topologyKey(zh));assert.deepEqual(geometry(en).graph,geometry(zh).graph);
 assert.equal(JSON.stringify(f),before);assert.equal(timeline.cursor,5);assert.equal(timeline.mode,'history');
 setLanguage('en',null);
});
test('custom node names and descriptions remain verbatim in both languages',()=>{
 const f=sample();f.presentation.nodes={'worker.run':{name:'资料核查员',description:'Do not rewrite 真实业务文字',type:'Author type'}};
 for(const lang of ['en','zh-CN']){setLanguage(lang,null);const d=nodePresentation(f.net.nodes[1],f);assert.equal(d.name,'资料核查员');assert.equal(d.description,'Do not rewrite 真实业务文字');assert.equal(d.type,'Author type');}
 setLanguage('en',null);
});
test('real ELK cache reuses node positions and routes across both locales',async()=>{
 const f=sample(),cache=new LayoutCache(new ELK());f.boundaries.entry=[{place:'in'}];
 setLanguage('en',null);const en=await cache.get(displayGraph(f));
 setLanguage('zh-CN',null);assert.equal(await cache.get(displayGraph(f)),en);assert.equal(cache.runs,1);
 setLanguage('en',null);
});

test('bilingual captions keep English words intact, wrap CJK and bound long identifiers',()=>{
 assert.equal(wrapLabel('The workflow receives its input here.',27,2),'The workflow receives its\ninput here.');
 assert.equal(wrapLabel('任务输入',4,3),'任务\n输入');
 const lines=wrapLabel('very_long_identifier_without_spaces',10,2).split('\n');
 assert.equal(lines.length,2);assert.ok(lines[1].endsWith('…'));
 assert.equal(wrapLabel('Receive task',26,2),'Receive task');
});

test('caption localization is limited to text and leaves the existing renderer and graph intact', async () => {
 const { refreshCanvasText } = await import('../../../cpn/frontend/static/canvas-text.mjs');
 const { nodeKey, edgeKey } = await import('../../../cpn/frontend/static/model.mjs');
 const f=sample(); f.boundaries.entry=[{place:'in'}];
 const writes=[],cells=new Map();
 for(const node of f.net.nodes) cells.set(nodeKey(node.id),{attr:(key,value)=>writes.push([node.id,key,value])});
 for(const edge of f.net.edges) cells.set(edgeKey(edge.id),{attr:(key,value)=>writes.push([edge.id,key,value]),label:(index,value)=>writes.push([edge.id,'label',value])});
 const renderer={graph:{getCell:key=>cells.get(key)}},layout={nodes:new Map(f.net.nodes.map(n=>[n.id,{width:240}]))};
 const before=JSON.stringify(f);
 for(const language of ['en','zh-CN']) {
  setLanguage(language,null);refreshCanvasText(renderer,displayGraph(f,'petri'),layout);
  assert.ok(writes.some(([id,key,value])=>id==='in'&&key==='status/text'&&value===(language==='en'?'Entry place':'入口库所')));
 }
 assert.equal(JSON.stringify(f),before);
 assert.ok(writes.every(([,key])=>['tooltip/text','root/aria-label','eyebrow/text','title/text','subtitle/text','status/text','label'].includes(key)));
 setLanguage('en',null);
});
