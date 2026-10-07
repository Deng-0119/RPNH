import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ComparisonContextState,normalizeComparisonContext,comparisonContextPath} from '../../../cpn/frontend/static/comparison-context.mjs';
import {checkpointApp,response,deferred} from './checkpoint-test-harness.mjs';
const fixture=JSON.parse(readFileSync(new URL('./comparison-context-fixture.json',import.meta.url),'utf8'));
const fresh=()=>structuredClone(fixture);
function selecting(){const s=new ComparisonContextState();s.open=true;s.selection={left:fixture.request.left,right:fixture.request.right};s.session=fixture.request.session_id;s.limits=fixture.request.limits;s.cuts=null;return s;}
function matching(ticket){const v=structuredClone(fixture.context);v.client_request_id=ticket.request.client_request_id;return v;}

test('actual public-session fixture validates all sources, scopes, fields and counts',async()=>{
 const {request,context}=fresh();assert.equal((await normalizeComparisonContext(context,request)).presentation.display_mode,'reliable_diff');
 assert.match(comparisonContextPath(request),/^\/api\/v2\/comparison-context\?request=/);
});
for(const [name,mutate] of [
 ['extra nested field',v=>v.left.graph.nodes[0].secret='never'],
 ['target substitution',v=>v.request_echo.left.source_id='other'],
 ['cut substitution',v=>v.sources[0].source_state.cut.head.ordinal++],
 ['counts substitution',v=>v.axes.definition.counts.known_same++],
 ['field substitution',v=>v.axes.definition.rows[0].left_fact.value='wrong'],
 ['unknown becomes null',v=>v.axes.runtime.rows.push({left_fact:{state:'provided',value:null}})],
 ['scope substitution',v=>v.left.scope_resolution.member_node_ids.pop()],
 ['evidence source substitution',v=>v.mapping.relations[0].evidence[0].source_id='other'],
 ['mapping endpoint substitution',v=>v.mapping.relations[0].left[0].subject_id='other'],
 ['native derivation cannot establish identity',v=>v.mapping.relations[0].evidence[0].verification_contract='rpnh/native_capability_derivation/v1'],
 ['unknown projection contract',v=>v.mapping.relations[0].evidence[0].verification_contract='rpnh/native_capability_derivation/v2'],
 ['false runtime authority',v=>v.authority='execute'],
])test('reject whole context: '+name,async()=>{const {request,context}=fresh();mutate(context);await assert.rejects(normalizeComparisonContext(context,request));});

test('generation accepts only newest complete pair and clears counts on selection/cancel',async()=>{
 const state=selecting(),old=state.begin();assert.equal(state.begin(),null);
 state.change({axes:['definition']});assert.equal(state.value,null);const newest=state.begin();
 assert.equal(await state.accept(old,matching(old)),false);
 state.close();assert.equal(await state.accept(newest,matching(newest)),false);assert.equal(state.value,null);assert.equal(state.open,false);
});
test('accepted pair pins returned cuts for larger-scope requests and failures clear both',async()=>{
 const state=selecting(),ticket=state.begin();assert.equal(await state.accept(ticket,matching(ticket)),true);
 assert.deepEqual(state.cuts,fixture.context.request_echo.source_cuts);const next=state.begin();assert.deepEqual(next.request.source_cuts,state.cuts);
 assert.equal(state.value,null);state.fail(next,{status:403});assert.equal(state.error,'access_changed');assert.equal(state.value,null);assert.equal(state.loading,false);
});
test('closing during asynchronous validation never reinstates an old context',async()=>{
 const state=selecting(),ticket=state.begin(),pending=state.accept(ticket,matching(ticket));state.close();assert.equal(await pending,false);assert.equal(state.value,null);
});
test('independent comparison opens before any current dashboard or current checkpoint',async()=>{
 const page={schema_version:'rpnh/comparison_selection/v1',session_id:fixture.request.session_id,targets:[{target:fixture.request.left,label:'Exact author <img src=x>',read_state:'not_read'}],source_cuts:fixture.context.request_echo.source_cuts,next_cursor:null,limits:fixture.request.limits};
 const calls=[],app=checkpointApp(async url=>{calls.push(url);return response(page);});app.api.crossComparison.open();await new Promise(r=>setTimeout(r,0));
 assert.equal(app.api.state().frame,undefined);assert.equal(app.api.crossComparison.state.options.length,1);assert.deepEqual(calls,['/api/v2/comparison-selection']);
 assert.equal(app.api.crossComparison.state.selection.left,null);assert.equal(app.api.crossComparison.state.selection.right,null);
 const source=app.document.getElementById('comparison-context-panel').querySelectorAll('select')[0];source.value='source-a';source.onchange();
 assert.match(app.document.getElementById('comparison-context-panel').textContent,/Exact author/);
});
for(const event of ['popstate','pagehide'])test(event+' clears entire independent pair and aborts late index data',async()=>{
 const wait=deferred(),app=checkpointApp(()=>wait.promise);app.api.crossComparison.open();app.window.dispatchEvent(new Event(event));
 wait.resolve(response({}));await new Promise(r=>setTimeout(r,0));assert.equal(app.api.crossComparison.state.open,false);assert.equal(app.api.crossComparison.state.value,null);assert.equal(app.document.getElementById('comparison-context-panel').textContent,'');
});

test('cannot omit every definition row and launder counts into an empty complete scope',async()=>{
 const {request,context}=fresh();const a=context.axes.definition;a.rows=[];Object.assign(a.counts,{loaded_count:0,total_count:0,known_changed:0,known_same:0,unknown:0});
 await assert.rejects(normalizeComparisonContext(context,request));
});

test('actual dual NetRenderer lifecycle uses real ELK geometry; controlled DOM is not browser paint',async()=>{
 const {default:ELK}=await import('elkjs/lib/elk.bundled.js');const {rendererRuntime}=await import('./viewer-lifecycle-harness.mjs');
 const page={schema_version:'rpnh/comparison_selection/v1',session_id:fixture.request.session_id,targets:[{target:fixture.request.left,label:'Exact author',read_state:'not_read'}],source_cuts:fixture.context.request_echo.source_cuts,next_cursor:null,limits:fixture.request.limits};
 const app=checkpointApp(async url=>{if(url.startsWith('/api/v2/comparison-selection'))return response(page);const request=JSON.parse(new URL(url,'http://local.invalid').searchParams.get('request'));const context=structuredClone(fixture.context);context.client_request_id=request.client_request_id;return response(context);});
 const runtime=rendererRuntime(app.document,app.window);globalThis.ResizeObserver=runtime.ResizeObserver;
 const elk=new ELK(),calls=[];const actual=elk.layout.bind(elk);elk.layout=graph=>{calls.push(graph);return actual(graph);};
 const panel=app.document.getElementById('comparison-context-panel');app.api.crossComparison.setRendering({joint:app.window.joint,elk});app.api.crossComparison.open();await new Promise(r=>setTimeout(r,0));
 for(const side of [0,1]){let selects=panel.querySelectorAll('select');const source=selects[side*2];source.value='source-a';source.onchange();selects=panel.querySelectorAll('select');const target=selects[side*2+1];target.value='0';target.onchange();}
 panel.querySelectorAll('button').find(b=>b.textContent==='Read comparison').onclick();
 for(let i=0;i<100&&!(panel.querySelectorAll('[data-comparison-paper]').length===2&&panel.querySelectorAll('[data-comparison-paper]').every(n=>n.children.length&&n.parentElement.style.visibility==='visible'));i++)await new Promise(r=>setTimeout(r,5));
 assert.equal(app.api.crossComparison.state.error,null);assert.equal(calls.length,2);assert.equal(panel.querySelectorAll('[data-comparison-paper]').length,2);
 assert.equal(panel.querySelectorAll('[data-comparison-paper]').every(n=>n.children.some(c=>c.tagName==='svg')),true);
 assert.equal(calls.every(g=>g.children.length===fixture.context.left.graph.nodes.length&&g.edges.length===fixture.context.left.graph.edges.length),true);
 assert.equal(JSON.stringify(calls).includes('Exact author'),false);
 app.window.dispatchEvent(new Event('pagehide'));assert.equal(panel.textContent,'');assert.equal(app.api.crossComparison.state.value,null);
 delete globalThis.ResizeObserver;
});

test('index-visible target with denied PN payload clears both graphs and counts without empty-graph fallback',async()=>{
 const state=selecting(),first=state.begin();assert.equal(await state.accept(first,matching(first)),true);
 assert.ok(state.value.axes.definition.counts.loaded_count>0);
 const denied=state.begin();assert.equal(state.value,null);
 assert.equal(state.fail(denied,{status:422}),true);
 assert.equal(state.error,'projection_unavailable');assert.equal(state.value,null);assert.equal(state.loading,false);
 assert.deepEqual(state.selection,{left:fixture.request.left,right:fixture.request.right});
});
