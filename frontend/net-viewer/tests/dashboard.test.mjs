import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFrame, displayGraph, TimelineState, visibleSelection, nodePresentation, isAdjacent } from '../../../cpn/frontend/static/dashboard-model.mjs';
import { topologyKey } from '../../../cpn/frontend/static/model.mjs';
import { geometry, decodeLayout } from '../../../cpn/frontend/static/layout.mjs';
import ELK from 'elkjs/lib/elk.bundled.js';
function frame(){
 const node=(id,kind)=>({id,label:id,kind,category:kind==='place'?'place':'execution',hidden_by_default:false});
 const edge=(id,source,target,mode)=>({id,source,target,mode,kind:'arc',weight:1,outcome:null,hidden_by_default:false});
 return normalizeFrame({schema_version:'rpnh/net_view/v1',source:{mode:'registry_current',verified_head_ordinal:7},summary:{},nodes:[node('in','place'),node('a.run','transition'),node('middle','place'),node('b.run','transition'),node('out','place')],edges:[edge('i','in','a.run','consume'),edge('p','a.run','middle','produce'),edge('c','middle','b.run','consume'),edge('o','b.run','out','produce')]});
}
test('simple handoff abstraction is reversible; exact PN is unchanged',()=>{const f=frame(),g=displayGraph(f);assert.deepEqual(g.folded,['middle']);assert.equal(g.nodes.length,4);assert.equal(g.edges.length,3);assert.deepEqual(g.edges.find(e=>e.hidden_place).source_ids,['p','c']);assert.equal(displayGraph(f,'petri').nodes.length,5);assert.deepEqual(visibleSelection(g,{kind:'node',id:'middle'}),{kind:'edge',id:'flow:middle'});assert.equal(f.net.nodes.length,5);});
test('read/reset/resource/branch/boundary do not get folded',()=>{for(const mode of ['read','reset']){const f=frame();f.net.edges[2].mode=mode;assert.deepEqual(displayGraph(f).folded,[]);}const resource=frame();resource.net.nodes[2].category='resource';assert.deepEqual(displayGraph(resource).folded,[]);const boundary=frame();boundary.boundaries.entry=[{place:'middle'}];assert.deepEqual(displayGraph(boundary).folded,[]);const branch=frame();branch.net.edges.push({...branch.net.edges[2],id:'second'});assert.deepEqual(displayGraph(branch).folded,[]);});
test('initial token or topology alone never invents a start boundary',()=>{const f=frame();f.net.nodes[0].active_token_count=1;assert.equal(nodePresentation(f.net.nodes[0],f).role,'data');f.boundaries.entry=[{place:'in'}];assert.equal(nodePresentation(f.net.nodes[0],f).role,'entry');});
test('metadata is display only; prompts and configs are not descriptions',()=>{const f=frame();f.net.nodes[1].config={node_synopsis:'SECRET PROMPT'};assert.ok(!nodePresentation(f.net.nodes[1],f).description.includes('SECRET'));f.presentation.nodes={'a.run':{name:'核查员',description:'核对输入'}};assert.equal(nodePresentation(f.net.nodes[1],f).name,'核查员');assert.equal(f.net.nodes[1].id,'a.run');});
test('state and folded-token updates do not relayout unchanged topology',()=>{const f=frame(),key=topologyKey(displayGraph(f));f.net.nodes[2].active_token_count=4;f.net.nodes[1].runtime={firings:[{status:'started'}]};assert.equal(topologyKey(displayGraph(f)),key);});
test('timeline keeps history mode and rejects stale requests',()=>{const t=new TimelineState();t.update([{cursor:1,checkpoint_ref:'a'},{cursor:3,checkpoint_ref:'c'}]);const old=t.begin(),selected=t.select(1);assert.equal(t.accepts(old),false);assert.equal(t.accepts(selected),true);t.update([{cursor:5,checkpoint_ref:'e'}]);assert.equal(t.mode,'history');assert.equal(t.cursor,1);assert.throws(()=>t.update([{cursor:1,checkpoint_ref:'bad'}]));});
test('only adjacent exact same-run checkpoints qualify for animation',()=>{const a=frame(),b=frame();a.net.marking={checkpoint_ref:'a'};b.change={coverage:'settlement_delta',previous_checkpoint_ref:'a'};assert.ok(isAdjacent(a,b));b.change.previous_checkpoint_ref='other';assert.ok(!isAdjacent(a,b));});
test('real ELK lays out flow and exact PN without losing identities or sending secrets',async()=>{const f=frame();f.boundaries.entry=[{place:'in'}];f.boundaries.exit=[{place:'out'}];f.presentation.nodes={'a.run':{name:'PRIVATE DISPLAY'}};for(const mode of ['flow','petri']){const g=displayGraph(f,mode),source=geometry(g);assert.ok(!JSON.stringify(source.graph).includes('PRIVATE DISPLAY'));const out=decodeLayout(await new ELK().layout(source.graph),source);assert.equal(out.nodes.size,g.nodes.length);assert.equal(out.edges.size,g.edges.length);assert.ok(out.nodes.get('in').x<out.nodes.get('out').x);}});

test('fallback names do not collapse distinct operations into one component name',()=>{const f=frame();const a={...f.net.nodes[1],id:'worker.inspect_request',label:'worker.inspect_request'},b={...a,id:'worker.finalize',label:'worker.finalize'};assert.notEqual(nodePresentation(a,f).name,nodePresentation(b,f).name);assert.ok(nodePresentation(a,f).name.includes('inspect request'));});

test('history paging retains the older-page boundary when live indexing refreshes',()=>{
  const t=new TimelineState(),page=(cursors,next_before,upper=8)=>({schema_version:'rpnh/dashboard_history/v1',source:{verified_head_ordinal:upper},coverage:'current_net_canonical_checkpoints',end_reason:'initial_checkpoint',items:cursors.map(cursor=>({cursor,checkpoint_ref:`c${cursor}`})),next_before});
  assert.ok(t.updatePage(page([5,6],5)));assert.equal(t.nextBefore,5);
  assert.ok(t.updatePage(page([3,4],3),5));assert.equal(t.nextBefore,3);
  t.select(3);assert.ok(t.updatePage(page([7,8],7)));assert.equal(t.nextBefore,3);assert.equal(t.cursor,3);assert.equal(t.mode,'history');
  t.updatePage(page([1,2],null),3);assert.equal(t.nextBefore,null);
  t.updatePage(page([7,8],7));assert.equal(t.nextBefore,null);assert.equal(t.items.length,8);
  assert.equal(t.updatePage(page([1],null,4)),false);
});
test('history validation is atomic and reset separates net versions',()=>{
  const t=new TimelineState(),page={schema_version:'rpnh/dashboard_history/v1',source:{verified_head_ordinal:9},coverage:'current_net_canonical_checkpoints',items:[{cursor:2,checkpoint_ref:'c2'}],next_before:null};
  t.updatePage(page);const before=structuredClone(t.items);
  for(const invalid of [{...page,items:[{cursor:10,checkpoint_ref:'x'}]},{...page,next_before:1},{...page,items:[{cursor:3,checkpoint_ref:'x'},{cursor:2,checkpoint_ref:'c2'}]},{...page,items:[{cursor:2,checkpoint_ref:'another'}]}])assert.throws(()=>t.updatePage(invalid));
  assert.deepEqual(t.items,before);t.resetHistory();assert.deepEqual(t.items,[]);assert.equal(t.historyHead,null);assert.equal(t.nextBefore,undefined);
});
test('later live or step navigation invalidates a pending slider callback',()=>{
  const t=new TimelineState(),drag=t.select(1);t.live();assert.ok(!t.accepts(drag));
  const secondDrag=t.select(1);t.select(3);assert.ok(!t.accepts(secondDrag));
});
test('dashboard validates exact source, place boundary and historical time together',()=>{
  const good=frame();good.position={mode:'history',cursor:7,latest_head:9};normalizeFrame(good);
  const wrongRun=structuredClone(good);wrongRun.source={...wrongRun.source,net_ref:'other'};assert.throws(()=>normalizeFrame(wrongRun));
  const wrongTime=structuredClone(good);wrongTime.position.cursor=6;assert.throws(()=>normalizeFrame(wrongTime));
  const future=structuredClone(good);future.position.latest_head=5;assert.throws(()=>normalizeFrame(future));
  const notAPlace=structuredClone(good);notAPlace.boundaries.entry=[{place:'a.run'}];assert.throws(()=>normalizeFrame(notAPlace));
});
test('multiple declared boundaries get distinguishable neutral fallback names',()=>{
  const f=frame();f.boundaries.entry=[{name:'document',place:'in'},{name:'criteria',place:'middle'}];
  assert.notEqual(nodePresentation(f.net.nodes[0],f).name,nodePresentation(f.net.nodes[2],f).name);
  assert.ok(nodePresentation(f.net.nodes[0],f).name.includes('document'));
});
test('unrecognized recorded execution is never displayed as no execution',async()=>{
  const {cardState}=await import('../../../cpn/frontend/static/dashboard-model.mjs');
  assert.match(cardState({kind:'transition',runtime:{firings:[{status:'future-state'}]}}),/Recorded.*unknown/);
  assert.match(cardState({kind:'transition',runtime:{firings:[{status:'settled'},{status:'future-state'}]}}),/Settled.*Unknown/);
});
