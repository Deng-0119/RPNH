import test from 'node:test';
import assert from 'node:assert/strict';
import ELK from 'elkjs/lib/elk.bundled.js';
import {displayGraph,normalizeFrame,visibleSelection} from '../../../cpn/frontend/static/dashboard-model.mjs';
import {topologyKey} from '../../../cpn/frontend/static/model.mjs';
import {geometry,decodeLayout,LayoutCache} from '../../../cpn/frontend/static/layout.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {refreshCanvasText} from '../../../cpn/frontend/static/canvas-text.mjs';
const node=(id,kind='place',extra={})=>({id,label:id,kind,category:kind==='place'?'place':'execution',hidden_by_default:false,...extra});
const edge=(id,source,target,mode,extra={})=>({id,source,target,kind:'arc',mode,weight:1,outcome:null,hidden_by_default:false,...extra});
function frame(){
 const f=normalizeFrame({schema_version:'rpnh/net_view/v1',source:{mode:'registry_current',verified_head_ordinal:3,run_dir:'test'},summary:{},
 nodes:[node('input'),node('a','transition'),node('p1'),node('tool','transition'),node('p2'),node('check','transition'),node('p3'),node('b','transition'),node('p4'),node('c','transition'),node('output'),node('r','place',{category:'resource'})],
 edges:[edge('i','input','a','consume'),edge('a1','a','p1','produce'),edge('t1','p1','tool','consume'),edge('t2','tool','p2','produce'),edge('g1','p2','check','consume'),edge('g2','check','p3','produce'),edge('b1','p3','b','consume'),edge('b2','b','p4','produce'),edge('c1','p4','c','consume'),edge('o','c','output','produce'),edge('ra','r','a','read'),edge('rc','r','c','read')]});
 f.agent_nodes=['a','b','c'].map(transition_id=>({transition_id,source:'fixture_explicit_agents'}));
 f.presentation.nodes={a:{name:'Planner',description:'Plan the task.'},b:{name:'Reviewer',description:'Review the draft.'},c:{name:'Writer',description:'Prepare the answer.'}};return f;
}
const pairs=v=>v.edges.map(e=>[e.source,e.target]);
test('only Agent nodes survive: no inputs, outputs, tools, guards, places or resources',()=>{
 const f=frame(),before=JSON.stringify(f),v=displayGraph(f,'overview');
 assert.deepEqual(v.nodes.map(n=>n.id),['a','b','c']);assert.deepEqual(pairs(v),[['a','b'],['b','c']]);
 assert.ok(v.nodes.every(n=>n.kind==='transition'&&n.display.role==='agent'));
 assert.ok(v.edges.every(e=>e.agent_relation&&e.display_label===''));assert.equal(JSON.stringify(f),before);
 assert.equal(displayGraph(f,'petri').nodes.length,f.net.nodes.length);assert.deepEqual(displayGraph(f,'petri').edges.map(e=>e.id),f.net.edges.map(e=>e.id));
});
test('cross tools and guards to NEXT Agent only, with exact original arc witness',()=>{
 const f=frame(),v=displayGraph(f,'overview'),e=v.edges[0];
 assert.deepEqual(e.witness_arc_ids,['a1','t1','t2','g1','g2','b1']);assert.deepEqual(e.source_ids,[...e.witness_arc_ids].sort());
 assert.ok(e.intermediate_node_ids.includes('tool'));assert.ok(!pairs(v).some(([a,b])=>a==='a'&&b==='c'));
 assert.equal(visibleSelection(v,{kind:'node',id:'tool'}).id,e.id);
});
test('neither an Agent-looking name nor a presentation type converts an ordinary transition',()=>{
 const f=frame();delete f.agent_nodes;f.net.nodes[3].executor='amazing-agent-tool';f.presentation.nodes.tool={name:'Agent',type:'智能体'};
 assert.equal(displayGraph(f,'overview').nodes.length,0);
 f.net.nodes[1].executor_declaration={contracts:{transport:'llm'}};
 assert.deepEqual(displayGraph(f,'overview').nodes.map(n=>n.id),['a']);
});
test('an explicit empty inventory stays empty rather than guessing from contract or names',()=>{
 const f=frame();f.agent_nodes=[];f.net.nodes[1].executor_declaration={contracts:{transport:'llm'}};
 const v=displayGraph(f,'overview');assert.equal(v.nodes.length,0);assert.equal(v.edges.length,0);assert.equal(v.agent_coverage,'provided');
});
test('invalid Agent references fail closed; places cannot be marked Agent',()=>{
 for(const id of ['missing','p1']){const f=frame();f.agent_nodes=[{transition_id:id}];assert.throws(()=>displayGraph(f,'overview'));}
});
test('resources and reset arcs cannot create invented Agent succession',()=>{
 const f=frame();f.net.edges=f.net.edges.filter(e=>!['b2','c1'].includes(e.id));
 f.net.edges.push(edge('pool','b','r','produce'),edge('r-c','r','c','consume'),edge('reset','p3','c','reset',{kind:'reset_arc'}));
 assert.deepEqual(pairs(displayGraph(f,'overview')),[['a','b']]);
});
test('fork/join remains an Agent relation graph without invented order among parallel Agents',()=>{
 const f=frame();f.net.edges.push(edge('parallel','p3','c','read'));f.net.edges=f.net.edges.filter(e=>!['b2','c1'].includes(e.id));
 assert.deepEqual(pairs(displayGraph(f,'overview')),[['a','b'],['a','c']]);
});
test('names, boundary flags, capacity, active tokens and resource toggle cannot reveal nonagents',()=>{
 const f=frame();f.net.nodes[2].label='Important';f.net.nodes[2].capacity=1;f.net.nodes[2].active_token_count=7;f.boundaries.entry=[{place:'p1'}];
 f.presentation.nodes.p1={name:'Important business place'};
 assert.deepEqual(displayGraph(f,'overview').nodes.map(n=>n.id),['a','b','c']);
});
test('intermediate loops terminate and genuine Agent feedback becomes one self relation',()=>{
 const f=frame();f.net.edges.push(edge('again','check','p1','produce'),edge('return','p3','a','consume'));
 const v=displayGraph(f,'overview');assert.deepEqual(pairs(v),[['a','a'],['a','b'],['b','c']]);
});
test('two structural alternatives between the same Agents are one unlabeled relation, with both sources retained',()=>{
 const f=frame();f.net.edges.push(edge('alternative','a','p3','produce',{outcome:'other'}));
 const v=displayGraph(f,'overview'),e=v.edges.find(e=>e.source==='a'&&e.target==='b');assert.equal(v.edges.length,2);assert.ok(e.source_ids.includes('alternative'));assert.ok(e.source_ids.includes('a1'));
});
test('explicit scoped Agent identity merges activation variants, never equal labels or global names',()=>{
 const f=frame();f.agent_nodes[0].semantic_group={component:'team',semantic_node_id:'agent'};f.agent_nodes[1].semantic_group={component:'team',semantic_node_id:'agent'};
 const v=displayGraph(f,'overview');assert.deepEqual(v.nodes.map(n=>n.id),['a','c']);assert.deepEqual(v.nodes[0].source_ids,['a','b']);assert.deepEqual(visibleSelection(v,{kind:'node',id:'b'}),{kind:'node',id:'a'});
 f.agent_nodes[1].semantic_group.component='other';assert.equal(displayGraph(f,'overview').nodes.length,3);
});
test('locale and checkpoint updates keep Agent identities and layout unchanged',()=>{
 const f=frame();setLanguage('en',null);const a=displayGraph(f,'overview');f.net.nodes[2].active_token_count=9;setLanguage('zh-CN',null);const b=displayGraph(f,'overview');
 assert.equal(topologyKey(a),topologyKey(b));assert.deepEqual(geometry(a).graph,geometry(b).graph);setLanguage('en',null);
});
test('real ELK lays out only Agent cards with no label budget or technical text',async()=>{
 const v=displayGraph(frame(),'overview'),g=geometry(v);assert.equal(g.graph.children.length,3);assert.equal(g.graph.edges.length,2);assert.ok(g.graph.edges.every(e=>e.labels.length===0));
 const l=decodeLayout(await new ELK().layout(g.graph),g);assert.ok(l.nodes.get('a').x<l.nodes.get('b').x);assert.ok(l.nodes.get('b').x<l.nodes.get('c').x);
});
test('empty Agent overview uses an honest empty canvas without requiring layout nodes',async()=>{
 const f=frame();f.agent_nodes=[];const cache=new LayoutCache({layout(){throw Error('must not layout fake Agent');}}),l=await cache.get(displayGraph(f,'overview'));
 assert.equal(l.nodes.size,0);assert.equal(l.edges.size,0);assert.equal(cache.runs,0);
});
test('Agent captions and links never include token counts, technical IDs or arc verbs',()=>{
 const f=frame(),v=displayGraph(f,'overview'),writes=[];
 const cells=new Map([...v.nodes.map(n=>['node:'+n.id,{attr:(k,x)=>writes.push([k,x])}]),...v.edges.map(e=>['edge:'+e.id,{attr:(k,x)=>writes.push([k,x]),label:(_i,x)=>writes.push(['label',x.attrs.text.text])}])]);
 refreshCanvasText({graph:{getCell:k=>cells.get(k)}},v,{nodes:new Map(v.nodes.map(n=>[n.id,{width:224}]))});
 assert.ok(writes.filter(([k])=>k==='label'||k==='status/text').every(([,x])=>x===''));
 assert.ok(writes.filter(([k])=>k==='subtitle/text').some(([,x])=>x.includes('Plan the task')));
});

test('explicit input rollback is not an Agent self-handoff',()=>{
  const f=frame();
  const input={id:'original-input',label:'original-input',kind:'place',category:'place'};
  f.net.nodes.push(input);
  f.net.edges.push(edge('take-original','original-input','a','consume'));
  f.net.edges.push({...edge('restore-original','a','original-input','produce'),emit:'forward',forward_source:'original-input',outcome:'interrupted'});
  const g=displayGraph(f,'overview');
  assert.ok(!g.edges.some(e=>e.source==='a'&&e.target==='a'));
});
