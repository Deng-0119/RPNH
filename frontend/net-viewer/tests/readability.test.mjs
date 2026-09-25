import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const jointGeometry=createRequire(import.meta.url)('@joint/core').g;
import ELK from 'elkjs/lib/elk.bundled.js';
import {displayGraph,normalizeFrame,visibleSelection} from '../../../cpn/frontend/static/dashboard-model.mjs';
import {topologyKey,arcLabel} from '../../../cpn/frontend/static/model.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {geometry,decodeLayout} from '../../../cpn/frontend/static/layout.mjs';
import {planWirePaths} from '../../../cpn/frontend/static/wire-geometry.mjs';
import {refreshCanvasText} from '../../../cpn/frontend/static/canvas-text.mjs';
const node=(id,kind)=>({id,label:id,kind,category:kind==='place'?'place':'execution',hidden_by_default:false});
const arc=(id,source,target,mode='consume',outcome=null)=>({id,source,target,kind:'arc',mode,weight:1,outcome,hidden_by_default:false});
function sample(){
 const net={schema_version:'rpnh/net_view/v1',source:{mode:'registry_current',run_dir:'test',net_ref:'v1',verified_head_ordinal:9},summary:{},
  nodes:[node('in','place'),node('a','transition'),node('handoff1','place'),node('handoff2','place'),node('b','transition'),node('out','place')],
  edges:[arc('i','in','a'),arc('a1','a','handoff1','produce'),arc('a2','a','handoff2','produce'),arc('b1','handoff1','b'),arc('b2','handoff2','b'),arc('o','b','out','produce')]};
 const f=normalizeFrame(net);f.boundaries={entry:[{place:'in'}],exit:[{place:'out'}],terminal_rules:[]};return f;
}
// Compact PN Overview assertions are superseded by agent-overview.test.mjs.
// The user requires only Agent cards; full PN and crossing tests remain below.
function crossed(options={}) {
 const snapshot={nodes:[node('a','place'),node('b','transition'),node('c','place'),node('d','transition')],edges:[arc('h','a','b'),arc('v','c','d')]};
 const nodes=new Map([['a',{x:-60,y:30,width:20,height:20}],['b',{x:230,y:30,width:20,height:20}],['c',{x:90,y:-60,width:20,height:20}],['d',{x:90,y:170,width:20,height:20}]]);
 const edges=new Map([['h',{sections:[{startPoint:{x:0,y:50},endPoint:{x:200,y:50}}]}],['v',{sections:[{startPoint:{x:100,y:-20},endPoint:{x:100,y:140}}]}]]);
 return {snapshot,layout:{nodes,edges,width:260,height:260}};
}
test('independent crossing has exactly one U bridge and one underpass gap with unchanged topology',()=>{
 const {snapshot,layout}=crossed(),before=JSON.stringify(snapshot),g=planWirePaths(snapshot,layout);
 assert.equal(g.crossings.length,1);assert.deepEqual(g.crossings[0],{over:'h',under:'v',x:100,y:50});
 assert.equal(g.paths.get('h').bridges,1);assert.match(g.paths.get('h').path,/C /);
 assert.equal((g.paths.get('v').path.match(/M /g)||[]).length,2);
 assert.equal(JSON.stringify(snapshot),before);
 const off=planWirePaths(snapshot,layout,{enabled:false});assert.equal(off.crossings.length,0);assert.doesNotMatch(off.paths.get('h').path,/C /);
});
test('hidden resources leave no phantom bridges and toggling visibility restores the exact crossings',()=>{
 const {snapshot,layout}=crossed();snapshot.nodes[2].category='resource';
 assert.equal(planWirePaths(snapshot,layout).crossings.length,0);
 assert.equal(planWirePaths(snapshot,layout,{showResources:true}).crossings.length,1);
 assert.equal(planWirePaths(snapshot,layout).paths.get('h').bridges,0);
});
test('shared endpoints, tangencies, bend intersections, collinear and near-node crossings never get U markers',()=>{
 for(const mutate of [
  (s,l)=>s.edges[1].source='a',
  (s,l)=>l.edges.get('v').sections[0].startPoint={x:100,y:50},
  (s,l)=>l.edges.get('v').sections[0].bendPoints=[{x:100,y:50},{x:180,y:50}],
  (s,l)=>l.edges.set('v',{sections:[{startPoint:{x:20,y:50},endPoint:{x:180,y:50}}]}),
  (s,l)=>l.nodes.set('extra',{x:95,y:45,width:20,height:20})]){
  const {snapshot,layout}=crossed();mutate(snapshot,layout);assert.equal(planWirePaths(snapshot,layout).crossings.length,0);
 }
});
test('reverse wires preserve endpoints and stable bridge ownership across input permutations',()=>{
 const {snapshot,layout}=crossed();for(const value of layout.edges.values()){const s=value.sections[0];[s.startPoint,s.endPoint]=[s.endPoint,s.startPoint];}
 const a=planWirePaths(snapshot,layout);snapshot.edges.reverse();const b=planWirePaths(snapshot,layout);
 assert.deepEqual([...a.paths],[...b.paths]);assert.match(a.paths.get('h').path,/^M 200 50/);assert.match(a.paths.get('h').path,/L 0 50$/);
});
test('rounded bends preserve the exact ELK endpoints without adding graph nodes',()=>{
 const {snapshot,layout}=crossed();layout.edges.get('h').sections[0].bendPoints=[{x:60,y:50},{x:60,y:90},{x:150,y:90},{x:150,y:50}];
 const a=planWirePaths(snapshot,layout,{enabled:false});assert.match(a.paths.get('h').path,/C /);assert.match(a.paths.get('h').path,/^M 0 50/);assert.match(a.paths.get('h').path,/L 200 50$/);
});
test('actual ELK non-planar graph produces drawable independent crossing markers',async()=>{
 const nodes=[...Array.from({length:4},(_,i)=>node('p'+i,'place')),...Array.from({length:4},(_,i)=>node('t'+i,'transition'))];
 const edges=nodes.slice(0,4).flatMap(p=>nodes.slice(4).map(t=>arc(p.id+'-'+t.id,p.id,t.id)));
 const f=normalizeFrame({schema_version:'rpnh/net_view/v1',source:{mode:'initial_configured'},summary:{},nodes,edges});
 const snap=displayGraph(f,'petri'),g=geometry(snap),l=decodeLayout(await new ELK().layout(g.graph),g),plan=planWirePaths(snap,l);
 assert.ok(plan.crossings.length>0,'real ELK paths must exercise bridges, not merely a synthetic d string');
});
test('PetriNet labels localize modes/type/human names while retaining exact IDs and raw outcomes',()=>{
 const f=sample(),before=JSON.stringify(f),writes=[];
 const cells=new Map([...f.net.nodes.map(n=>['node:'+n.id,{attr:(k,v)=>writes.push([n.id,k,v])}]),...f.net.edges.map(e=>['edge:'+e.id,{attr:(k,v)=>writes.push([e.id,k,v]),label:(_i,v)=>writes.push([e.id,'label',v])}])]);
 const renderer={graph:{getCell:k=>cells.get(k)}},layout={nodes:new Map(f.net.nodes.map(n=>[n.id,{width:270}]))};
 for(const lang of ['en','zh-CN']){setLanguage(lang,null);refreshCanvasText(renderer,displayGraph(f,'petri'),layout);}
 assert.ok(writes.some(([id,key,value])=>id==='in'&&key==='title/text'&&value.includes('接收任务')));
 assert.ok(writes.some(([id,key,value])=>id==='a'&&key==='eyebrow/text'&&value==='转换'));
 assert.ok(writes.some(([id,key,value])=>id==='a'&&key==='subtitle/text'&&value.includes('a')));
 assert.match(arcLabel({...f.net.edges[0],outcome:'accept-code'}),/^消费 ×1 · 分支 accept-code$/);
 assert.equal(JSON.stringify(f),before);setLanguage('en',null);assert.match(arcLabel(f.net.edges[0]),/^Consume/);
});
test('default shell exposes overview, localized PetriNet and a keyboard accessible wire control',()=>{
 const html=readFileSync(new URL('../../../cpn/frontend/static/index.html',import.meta.url),'utf8');
 assert.match(html,/<button aria-pressed="true" data-i18n="简化概览" id="mode-overview">Overview/);
 assert.match(html,/data-i18n="Petri 网" id="mode-petri"/);assert.match(html,/id="line-bridges" type="checkbox" checked/);
});

test('every bridge and rounded corner parses with the pinned JointJS path parser',async()=>{
 const {snapshot,layout}=crossed();layout.edges.get('h').sections[0].bendPoints=[{x:60,y:50},{x:60,y:90},{x:150,y:90},{x:150,y:50}];
 for(const enabled of [false,true])for(const route of planWirePaths(snapshot,layout,{enabled}).paths.values()){
  const parsed=jointGeometry.Path.parse(route.path);assert.ok(parsed.length()>0);assert.ok(parsed.serialize().length>0);
 }
});
