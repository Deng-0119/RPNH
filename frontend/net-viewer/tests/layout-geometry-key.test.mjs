import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {LayoutCache, geometry} from '../../../cpn/frontend/static/layout.mjs';
import {topologyKey, renderKey} from '../../../cpn/frontend/static/model.mjs';
import {NetRenderer} from '../../../cpn/frontend/static/renderer.mjs';
const require=createRequire(import.meta.url);
const ELK=require('../../../cpn/frontend/static/assets/elk-api.js');
const {Worker}=require('../../../cpn/frontend/static/assets/elk-worker.js');
const elk=()=>new ELK({workerFactory:()=>new Worker()});
function sample(){return {source:{mode:'registry_current',run_dir:'source',net_ref:{version_id:'v1'}},view_mode:'overview',nodes:[{id:'p',label:'P',kind:'place',category:'place'},{id:'t',label:'T',kind:'transition',category:'execution'}],edges:[{id:'e',source:'p',target:'t',kind:'arc',mode:'consume',weight:1}]};}

test('semantic label changes reuse geometry while static render key updates',async()=>{
 const a=sample(), c=new LayoutCache(elk()), first=await c.get(a);
 const b=structuredClone(a);b.nodes[0].label='new semantic label';b.edges[0].weight=2;
 assert.notEqual(renderKey(a),renderKey(b));assert.equal(await c.get(b),first);assert.equal(c.runs,1);
 const d=structuredClone(b);d.nodes.reverse();d.edges.reverse();d.nodes[0].active_token_count=2;
 assert.equal(await c.get(d),first);assert.equal(renderKey(b),renderKey(d));
});
for(const [name,mutate] of [['display_label',x=>x.edges[0].display_label='reserved'],['hidden_places',x=>x.edges[0].hidden_places=['p']]]){
 test(`${name} label-box mutation invalidates old-equal topology`,async()=>{
  const a=sample(), b=structuredClone(a);mutate(b);assert.equal(topologyKey(a),topologyKey(b));
  assert.notDeepEqual(geometry(a).graph,geometry(b).graph);
  const c=new LayoutCache(elk()), one=await c.get(a),two=await c.get(b);
  assert.notEqual(one,two);assert.equal(c.runs,2);assert.equal(one.edges.get('e').labels.length,0);assert.equal(two.edges.get('e').labels.length,1);
 });
}
test('agent relation removes reserved box; same truthy text only changes renderer',async()=>{
 const a=sample();a.edges[0].display_label='A';const b=structuredClone(a);b.edges[0].display_label='B';
 const c=new LayoutCache(elk()), one=await c.get(a);assert.equal(await c.get(b),one);assert.notEqual(renderKey(a),renderKey(b));
 const d=structuredClone(b);d.edges[0].agent_relation=true;assert.equal(topologyKey(b),topologyKey(d));
 const two=await c.get(d);assert.notEqual(two,one);assert.equal(two.edges.get('e').labels.length,0);
});
test('real IDs and source prevent anonymous graph wrong bindings',async()=>{
 const a=sample(),b=sample();b.nodes[0].id='q';b.edges[0].source='q';
 assert.deepEqual(geometry(a).graph,geometry(b).graph);
 const c=new LayoutCache(elk());const one=await c.get(a),two=await c.get(b);
 assert.notEqual(one,two);assert.deepEqual([...two.nodes.keys()],['q','t']);assert.ok(!two.nodes.has('p'));
 const d=structuredClone(a);d.source.run_dir='other';assert.notEqual(await c.get(d),one);
});
test('pending identical geometry deduplicates and failure remains retryable',async()=>{
 const engine=elk();let release;const gate=new Promise(r=>release=r);let fail=true;
 const c=new LayoutCache({async layout(graph){await gate;if(fail)throw Error('failed');return engine.layout(graph);}});
 const a=sample(),b=sample();b.nodes[0].label='updated';const p=c.get(a),q=c.get(b);release();
 const result=await Promise.allSettled([p,q]);assert.ok(result.every(x=>x.status==='rejected'));assert.equal(c.runs,1);assert.equal(c.pending.size,0);assert.equal(c.cache.size,0);
 fail=false;await c.get(a);assert.equal(c.runs,2);
});
test('cache count limit unchanged',async()=>{
 const c=new LayoutCache(elk());for(let i=0;i<6;i++){const a=sample();a.source.run_dir=String(i);await c.get(a);}
 assert.equal(c.cache.size,4);assert.equal(c.cachedElements,12);
});
test('renderer applies new layout even when static key is equal',()=>{
 const a=sample();const calls=[];const cell={get(){return 'node';},attr(){},label(){}};
 const viewer={signature:null,layout:null,createNode(){return cell;},createEdge(){return cell;},graph:{resetCells(){calls.push(1);},getCell(){return cell;}},paper:{findViewByModel(){return null;}}};
 const layout={nodes:new Map([['p',{}],['t',{}]]),edges:new Map([['e',{}]])};
 assert.equal(NetRenderer.prototype.apply.call(viewer,a,layout),true);
 assert.equal(NetRenderer.prototype.apply.call(viewer,a,layout),false);
 assert.equal(NetRenderer.prototype.apply.call(viewer,a,{...layout}),true);assert.equal(calls.length,2);
});
test('all additional static display and style inputs invalidate render key only',()=>{
 const a=sample();for(const mutate of [x=>x.nodes[0].display={role:'data',name:'new',type:'T',description:'D'},x=>x.nodes[1].operation='new',x=>x.edges[0].resource=true,x=>x.edges[0].agent_relation=true,x=>x.edges[0].display_label='new',x=>x.edges[0].hidden_place=true]){const b=structuredClone(a);mutate(b);assert.notEqual(renderKey(a),renderKey(b));}
});
test('removing hidden_place rebuilds base label rather than retaining token count',()=>{
 const a=sample();a.edges[0].hidden_place=true;a.edges[0].display_label='relation';a.edges[0].active_token_count=3;
 let labelText;const cell={get(){return 'edge';},attr(){},label(i,value){labelText=value.attrs.text.text;}};
 const viewer={signature:null,layout:null,createNode(){return cell;},createEdge(e){labelText=e.display_label;return cell;},graph:{resetCells(){},getCell(){return cell;}},paper:{findViewByModel(){return null;}}};
 const layout={nodes:new Map([['p',{}],['t',{}]]),edges:new Map([['e',{}]])};
 NetRenderer.prototype.apply.call(viewer,a,layout);assert.equal(labelText,'relation · 3 个 token');
 const b=structuredClone(a);delete b.edges[0].hidden_place;
 assert.equal(NetRenderer.prototype.apply.call(viewer,b,layout),true);assert.equal(labelText,'relation');
});
