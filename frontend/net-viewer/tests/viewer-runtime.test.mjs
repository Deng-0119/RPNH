import test from 'node:test';
import assert from 'node:assert/strict';
import {createReadRequest,BoundedLayoutEngine,diagnosticTopic} from '../../../cpn/frontend/static/viewer-runtime.mjs';
const pending=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const response=(value,status=200)=>({ok:status===200,status,json:async()=>value});
const graph=n=>({children:Array.from({length:n},(_,i)=>({id:String(i)})),edges:[]});

test('HTTP deadline aborts a nonresponsive fetch, frees slot and reports a bounded diagnostic',async()=>{
  const signals=[],errors=[];const read=createReadRequest((_path,options)=>{signals.push(options.signal);return new Promise(()=>{});},{timeoutMs:10,maxConcurrent:1,onError:e=>errors.push(e.code)});
  const first=read('/api/v1/net');await assert.rejects(read('/api/v1/net'),{code:'viewer_read_busy'});
  await assert.rejects(first,{code:'viewer_read_timeout'});assert.equal(signals[0].aborted,true);assert.equal(read.activeCount(),0);
  assert.deepEqual(errors,['viewer_read_busy','viewer_read_timeout']);
});
test('external cancellation is not a user error and a late response cannot win',async()=>{
  const later=pending(),controller=new AbortController(),errors=[];
  const read=createReadRequest(()=>later.promise,{onError:e=>errors.push(e)});
  const request=read('/api/v1/net',controller.signal);controller.abort();
  await assert.rejects(request,{name:'AbortError'});later.resolve(response({late:true}));
  assert.equal(read.activeCount(),0);assert.deepEqual(errors,[]);
});
test('completed requests release listeners while the composed fetch signal still reflects owner cancellation',async()=>{
  const controller=new AbortController();let signal;
  const read=createReadRequest((_p,options)=>{signal=options.signal;return response({ok:true});});
  assert.deepEqual(await read('/api/v1/net',controller.signal),{ok:true});controller.abort();assert.equal(signal.aborted,true);
});
test('untrusted backend error strings do not become diagnoses or HTML',async()=>{
  const read=createReadRequest(()=>response({error:'<img src=x onerror=alert(1)> /private/secret'},403));
  await assert.rejects(read('/api/v1/net'),e=>e.code==='viewer_read_failed' && !e.message.includes('private') && !e.message.includes('<img') && e.helpTopic==='access');
  assert.equal(diagnosticTopic({status:409}),'history');assert.equal(diagnosticTopic({status:413}),'layout');
});
test('malformed JSON and shutdown cancel are finite and distinguishable',async()=>{
  const read=createReadRequest(()=>({ok:true,status:200,json:async()=>{throw Error('private text');}}));
  await assert.rejects(read('/api/v1/net'),{code:'viewer_invalid_response'});
  const blocked=createReadRequest(()=>new Promise(()=>{}));const r=blocked('/api/v1/net');blocked.abortAll();
  await assert.rejects(r,{name:'AbortError'});assert.equal(blocked.activeCount(),0);
});
test('worker serializes layouts, deduplicated cache is separate, and queue limits reject explicit work',async()=>{
  const first=pending();let calls=0;
  const engine=new BoundedLayoutEngine(()=>({layout:async g=>++calls===1?first.promise:g,terminateWorker(){}}),{maxJobs:2,maxElements:10,maxQueuedElements:10});
  const a=engine.layout(graph(5)),b=engine.layout(graph(5));await Promise.resolve();assert.equal(calls,1);
  await assert.rejects(engine.layout(graph(1)),{code:'viewer_layout_busy'});
  await assert.rejects(engine.layout(graph(11)),{code:'viewer_layout_size_limit'});
  first.resolve({first:true});assert.deepEqual(await a,{first:true});assert.equal((await b).children.length,5);
  assert.equal(calls,2);assert.equal(engine.weight,0);engine.terminateWorker();
});
test('worker timeout terminates generation, rejects queued jobs, ignores late results and recreates lazily',async()=>{
  const late=pending();let workers=0,terminated=0;
  const engine=new BoundedLayoutEngine(()=>{const index=++workers;return {layout:()=>index===1?late.promise:Promise.resolve({fresh:true}),terminateWorker(){terminated++;}};},{timeoutMs:10});
  const a=engine.layout(graph(2)),b=engine.layout(graph(2));
  await Promise.all([assert.rejects(a,{code:'viewer_layout_timeout'}),assert.rejects(b,{code:'viewer_layout_timeout'})]);
  assert.equal(terminated,1);assert.equal(engine.weight,0);assert.equal(engine.active,null);assert.equal(engine.queue.length,0);
  late.resolve({stale:true});assert.deepEqual(await engine.layout(graph(1)),{fresh:true});assert.equal(workers,2);
  engine.terminateWorker();assert.equal(terminated,2);await assert.rejects(engine.layout(graph(1)),{code:'viewer_layout_closed'});
});
test('dispose cancels active and queued layout without waiting for their completion',async()=>{
  let terminated=0;const engine=new BoundedLayoutEngine(()=>({layout:()=>new Promise(()=>{}),terminateWorker(){terminated++;}}));
  const a=engine.layout(graph(1)),b=engine.layout(graph(1));await Promise.resolve();engine.terminateWorker();
  await Promise.all([assert.rejects(a,{code:'viewer_layout_closed'}),assert.rejects(b,{code:'viewer_layout_closed'})]);assert.equal(terminated,1);
});

test('bounded read preserves comparison POST body and headers without overriding controlled cache or cancellation',async()=>{
  let received;const owner=new AbortController(),body=JSON.stringify({visual_pairs:[{pair_id:'exact'}]});
  const read=createReadRequest((_p,options)=>{received=options;return response({ok:true});});
  assert.deepEqual(await read('/api/v2/comparison-context',owner.signal,{method:'POST',headers:{'Content-Type':'application/json'},body,cache:'force-cache'}),{ok:true});
  assert.equal(received.method,'POST');assert.equal(received.body,body);assert.equal(received.headers['Content-Type'],'application/json');
  assert.equal(received.cache,'no-store');owner.abort();assert.equal(received.signal.aborted,true);
});
