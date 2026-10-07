// Independent controlled-DOM lifecycle regression. No real browser or socket.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {checkpointApp,response,deferred} from './checkpoint-test-harness.mjs';
import {ComparisonContextState} from '../../../cpn/frontend/static/comparison-context.mjs';
const fixture=JSON.parse(readFileSync(new URL('./comparison-context-fixture.json',import.meta.url),'utf8'));
const tick=()=>new Promise(r=>setTimeout(r,0));
const firstTarget=structuredClone(fixture.request.left);
const nextTarget=structuredClone(firstTarget);nextTarget.revision_ref.ref.version_id='resource_version:'+'e'.repeat(32);
function page(target,next_cursor){return {schema_version:'rpnh/comparison_selection/v1',session_id:fixture.request.session_id,targets:[{target,label:'Exact author',read_state:'not_read'}],source_cuts:fixture.context.request_echo.source_cuts,next_cursor,limits:fixture.request.limits};}

test('close clears paging capability and reopening cannot offer previous cursor',()=>{
 const state=new ComparisonContextState();state.open=true;state.nextCursor='old-selection-cursor';state.options=[firstTarget];state.close();
 assert.equal(state.nextCursor,null);
});

test('two overlapping More requests accept only the newest index result',async()=>{
 const older=deferred(),newer=deferred();let requests=0;
 const app=checkpointApp(async url=>{requests++;return requests===1?response(page(firstTarget,'next-page')):requests===2?older.promise:newer.promise;});
 app.api.crossComparison.open();await tick();
 const panel=app.document.getElementById('comparison-context-panel');
 const more=panel.querySelectorAll('button').find(b=>b.textContent==='More exact objects');assert.ok(more,'paging button exists');
 more.onclick();more.onclick();
 assert.ok(requests===2||requests===3, 'duplicate clicks may be deduplicated or superseded');
 const accepted=requests===2?older:newer, discarded=requests===2?newer:older;
 accepted.resolve(response(page(nextTarget,null)));await tick();
 assert.equal(app.api.crossComparison.state.options.length,2);
 discarded.resolve(response(page(nextTarget,null)));await tick();
 assert.equal(app.api.crossComparison.state.error,null);
 assert.equal(app.api.crossComparison.state.options.length,2);
 app.api.crossComparison.close();
});


test('evidence mode returns to verified recommendation after an explicit full-pair read',async()=>{
 const app=checkpointApp(async url=>{
  if(url.startsWith('/api/v2/comparison-selection'))return response(page(firstTarget,null));
  const req=JSON.parse(new URL(url,'http://local.invalid').searchParams.get('request'));
  const context=structuredClone(fixture.context);context.client_request_id=req.client_request_id;
  context.request_echo.view_preference=req.view_preference;context.presentation.display_mode='full_pair';
  return response(context);
 });
 app.api.crossComparison.open();await tick();
 const state=app.api.crossComparison.state;
 state.selection={left:structuredClone(fixture.request.left),right:structuredClone(fixture.request.right)};
 state.preference='full_pair';app.api.crossComparison.refreshLanguage();
 const panel=app.document.getElementById('comparison-context-panel');
 panel.querySelectorAll('button').find(b=>b.textContent==='Read comparison').onclick();
 for(let i=0;i<50&&!state.value;i++)await tick();
 assert.equal(state.error,null);assert.ok(state.value);assert.ok(panel.textContent.includes('Independent full pair'));
 const before=structuredClone(state.value);
 panel.querySelectorAll('button').find(b=>b.textContent==='Show evidence-based view').onclick();
 assert.ok(panel.textContent.includes('Verified correspondence in this scope'));
 assert.deepEqual(state.value,before,'display switch leaves accepted facts and cuts unchanged');
 app.api.crossComparison.close();
});

test('index authorization loss reports access_changed and clears old authorized options',async()=>{
 let calls=0;
 const app=checkpointApp(async()=>++calls===1?response(page(firstTarget,'more')):response({error:'access_changed'},403));
 app.api.crossComparison.open();await tick();
 const panel=app.document.getElementById('comparison-context-panel');
 panel.querySelectorAll('button').find(b=>b.textContent==='More exact objects').onclick();await tick();
 const state=app.api.crossComparison.state;
 assert.equal(state.error,'access_changed');assert.equal(state.value,null);
 assert.deepEqual(state.options,[]);assert.equal(state.nextCursor,null);assert.equal(state.cuts,null);
 app.api.crossComparison.close();
});


for(const pairStatus of [200,422])test(`starting comparison supersedes More without locking pagination (${pairStatus})`,async()=>{
 const pendingMore=deferred(),pendingPair=deferred();let indexCalls=0,pairRequest,moreSignal;
 const app=checkpointApp(async(url,options)=>{
  if(url.startsWith('/api/v2/comparison-selection')){
   indexCalls++;
   if(indexCalls===1)return response(page(firstTarget,'more'));
   if(indexCalls===2){moreSignal=options.signal;return pendingMore.promise;}
   return response(page(nextTarget,null));
  }
  pairRequest=JSON.parse(new URL(url,'http://local.invalid').searchParams.get('request'));
  return pendingPair.promise;
 });
 const panel=app.document.getElementById('comparison-context-panel');
 const button=name=>panel.querySelectorAll('button').find(b=>b.textContent===name);
 app.api.crossComparison.open();await tick();
 const state=app.api.crossComparison.state;
 state.selection={left:structuredClone(fixture.request.left),right:structuredClone(fixture.request.right)};
 app.api.crossComparison.refreshLanguage();
 button('More exact objects').onclick();button('Read comparison').onclick();
 pendingMore.resolve(response(page(nextTarget,null)));await tick();
 const value=structuredClone(fixture.context);value.client_request_id=pairRequest.client_request_id;
 pendingPair.resolve(response(pairStatus===200?value:{error:'projection_unavailable'},pairStatus));
 for(let i=0;i<50&&state.loading;i++)await tick();
 assert.equal(state.loading,false);assert.equal(state.error,pairStatus===200?null:'projection_unavailable');
 assert.equal(state.options.length,1,'superseded More must not append its targets');
 assert.equal(button('More exact objects').disabled,false,'superseded More must release loading state');
 assert.equal(moreSignal.aborted,true,'new pair generation aborts superseded index request');
 button('More exact objects').onclick();await tick();
 assert.equal(indexCalls,3);assert.equal(state.options.length,2);assert.equal(state.nextCursor,null);
 app.api.crossComparison.close();
});

test('late superseded More cannot release a newer index request, including comparison failure',async()=>{
 const older=deferred(),newer=deferred(),pair=deferred();let indexCalls=0;
 const app=checkpointApp(async url=>{
  if(!url.startsWith('/api/v2/comparison-selection'))return pair.promise;
  indexCalls++;return indexCalls===1?response(page(firstTarget,'more')):indexCalls===2?older.promise:indexCalls===3?newer.promise:response(page(nextTarget,null));
 });
 const panel=app.document.getElementById('comparison-context-panel');
 const button=name=>panel.querySelectorAll('button').find(b=>b.textContent===name);
 app.api.crossComparison.open();await tick();
 const state=app.api.crossComparison.state;
 state.selection={left:structuredClone(fixture.request.left),right:structuredClone(fixture.request.right)};
 app.api.crossComparison.refreshLanguage();
 button('More exact objects').onclick();button('Read comparison').onclick();
 button('More exact objects').onclick();assert.equal(indexCalls,3);
 older.resolve(response(page(nextTarget,null)));await tick();
 button('More exact objects').onclick();assert.equal(indexCalls,3,'old settlement cannot unlock newer in-flight cursor');
 pair.resolve(response({error:'projection_unavailable'},422));await tick();
 newer.resolve(response(page(nextTarget,null)));await tick();
 assert.equal(state.error,'projection_unavailable');assert.equal(state.options.length,1);
 assert.equal(button('More exact objects').disabled,false,'comparison failure supersedes newer index without permanent busy state');
 button('More exact objects').onclick();await tick();assert.equal(indexCalls,4);assert.equal(state.options.length,2);
 app.api.crossComparison.close();
});
