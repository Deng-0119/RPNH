import test,{afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {checkpointApp,response,deferred} from './checkpoint-test-harness.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {fixtureFrame,envelope} from './firing-activity-fixture.mjs';
import {stable} from '../../../cpn/frontend/static/model.mjs';
afterEach(()=>setLanguage('en'));
async function app(fetcher){const h=checkpointApp(fetcher);h.api.install(fixtureFrame());await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');return h;}
for(const lang of ['en','zh-CN']) test(`actual app/panel renders independent evidence and preserves canonical frame in ${lang}`,async()=>{
    setLanguage(lang);const requests=[];const h=await app(async path=>{requests.push(path);return response(envelope());});const before=JSON.stringify(h.api.state().frame);
    assert.equal(requests.length,0);assert.match(h.document.getElementById('detail').textContent,lang==='en'?/View recorded activity/:/查看已记录活动/);
    const load=h.document.getElementById('detail').querySelectorAll('button').find(b=>b.dataset.activityAction==='load');
    await load.onclick();const text=h.document.getElementById('detail').textContent;
    assert.match(text,/H500\/E2/);assert.match(text,/#71/);assert.match(text,/operation_execution_started\/v1/);assert.match(text,/transition_firing_version:/);assert.match(text,lang==='en'?/Operation start recorded; outcome unknown/:/已记录 operation start；结果未知/);
    assert.equal(JSON.stringify(h.api.state().frame),before);assert.equal(h.api.state().firingTarget,null);assert.equal(requests.length,1);
    const card=h.document.getElementById('detail').querySelectorAll('details').find(n=>n.dataset.activityFiring);card.open=true;card.ontoggle();
    setLanguage(lang==='en'?'zh-CN':'en');h.api.refreshLanguage();const next=h.document.getElementById('detail').querySelectorAll('details').find(n=>n.dataset.activityFiring===card.dataset.activityFiring);assert.equal(next.open,true);assert.equal(requests.length,1);
});
test('actual app serializes repeated load-more, preserves old evidence on stale, then refresh replaces',async()=>{
    const pending=deferred();let n=0;const h=await app(async()=>{n++;return n===1?response(envelope({count:50,more:true})):n===2?pending.promise:response(envelope({head:501}));});
    await h.api.loadActivity();const next=h.api.loadActivity('more');await h.api.loadActivity('more');assert.equal(n,2);
    pending.resolve(response({error:'stale_observation'},409));await next;assert.equal(h.api.activityState().observation.records.length,50);assert.equal(h.api.activityState().stale,true);await h.api.loadActivity('more');assert.equal(n,2);
    await h.api.loadActivity('refresh');assert.equal(n,3);assert.equal(h.api.activityState().observation.records.length,3);assert.equal(h.api.activityState().observation.context.source_cuts[0].cut.head_ordinal,501);
});
test('refresh failure retains old H/E; a source eligibility change clears it',async()=>{
    let n=0;const h=await app(async()=>++n===1?response(envelope()):response({error:'failure'},n===2?503:403));
    await h.api.loadActivity();const old=h.api.activityState().observation;await h.api.loadActivity('refresh');assert.equal(h.api.activityState().observation,old);assert.equal(h.api.activityState().error,'read_failed');
    await h.api.loadActivity('refresh');assert.equal(h.api.activityState().observation,null);assert.equal(h.api.activityState().error,'access_changed');
});
for(const action of ['close','member','clear','live','frame']) test(`late activity success/error/finally cannot revive ${action}`,async()=>{
    for(const status of [200,503]){
        const delayed=deferred();const h=await app(async path=>path.startsWith('/api/v2/firing-activity')?delayed.promise:response(fixtureFrame()));
        const pending=h.api.loadActivity();
        if(action==='close'||action==='clear')h.api.closeActivity();else if(action==='member')h.api.select('node','other');else if(action==='live')await h.api.returnToLive();else await h.api.render(null,undefined,fixtureFrame());
        delayed.resolve(response(status===200?envelope():{error:'late'},status));await pending;
        assert.equal(h.api.activityState().observation,null);assert.equal(h.api.activityState().loading,false);assert.equal(h.api.activityState().error,null);
    }
});
test('slow earlier refresh cannot replace a faster newer explicit refresh',async()=>{
    const a=deferred(),b=deferred();let n=0;const h=await app(async()=>++n===1?a.promise:b.promise);const old=h.api.loadActivity();const latest=h.api.loadActivity('refresh');
    b.resolve(response(envelope({head:501})));await latest;a.resolve(response(envelope()));await old;
    assert.equal(h.api.activityState().observation.context.source_cuts[0].cut.head_ordinal,501);assert.equal(h.api.activityState().loading,false);
});
test('activity target resolves separately and never enters retained checkpoint firingTarget',async()=>{
    const h=await app(async()=>response(envelope()));await h.api.loadActivity();const o=h.api.activityState().observation,f=o.firings[0];
    const target={kind:'firing_activity',context_key:o.context_key,transition_id:f.transition_id,firing_ref:f.firing_ref};
    assert.equal(h.api.selectActivity(target),true);assert.equal(h.api.state().firingTarget,null);assert.ok(h.api.activityState().expanded.has(stable(f.firing_ref)));
    assert.equal(h.api.selectActivity({...target,context_key:'wrong'}),false);
});
test('explicit load pauses existing autoplay and polling without fetching a canonical frame',async()=>{
    let calls=0;const h=await app(async()=>{calls++;return response(envelope());});h.document.getElementById('auto-refresh').checked=true;h.api.schedule();assert.ok(h.timers.size>0);h.api.pending();
    await h.api.loadActivity();assert.equal(h.api.state().playing,false);assert.equal(h.document.getElementById('auto-refresh').checked,true);assert.equal(h.document.getElementById('auto-refresh').disabled,true);assert.match(h.document.getElementById('source-badge').textContent,/retained/i);assert.equal(h.timers.size,0);assert.equal(calls,1);
    h.api.closeActivity();assert.equal(h.document.getElementById('auto-refresh').checked,true);assert.equal(h.document.getElementById('auto-refresh').disabled,false);assert.ok(h.timers.size>0);
});
test('checkpoint navigation cancels a pending activity even when checkpoint read then fails',async()=>{
    const delayed=deferred();const h=await app(async path=>path.startsWith('/api/v2/firing-activity')?delayed.promise:response({error:'unavailable'},503));
    h.api.install(h.api.state().frame,{navigation:{previous_net_segment:{...envelope().selector,cut:70}}});
    const pending=h.api.loadActivity();await h.api.openPreviousNet();delayed.resolve(response(envelope()));await pending;
    assert.equal(h.api.activityState().observation,null);assert.equal(h.api.activityState().loading,false);assert.equal(h.api.state().frame.position.cursor,71);
});
test('internally consistent replacement of an established run identity clears old evidence',async()=>{
    let calls=0;const h=await app(async()=>{const v=envelope();if(++calls>1)v.view.context.query_scope.run_ref.version_id='run_version:'+'a'.repeat(32);return response(v);});
    await h.api.loadActivity();await h.api.loadActivity('refresh');assert.equal(h.api.activityState().observation,null);assert.equal(h.api.activityState().error,'access_changed');
});

for (const action of ['openPetri','overview']) for (const autoRefresh of [true,false]) for (const loaded of [true,false])
    test(`actual ${action} restores preference=${autoRefresh} after activity=${loaded} without duplicate timers`,async()=>{
        const requests=[];const h=await app(async path=>{requests.push(path);return response(envelope());});
        const checkbox=h.document.getElementById('auto-refresh');checkbox.checked=autoRefresh;h.api.schedule();
        const beforeFrame=JSON.stringify(h.api.state().frame),beforeTimers=[...h.timers.keys()];
        assert.equal(h.timers.size,autoRefresh?1:0);
        if(loaded){await h.api.loadActivity();assert.equal(h.api.activityState().paused,true);assert.equal(h.timers.size,0);}
        const navigate=()=>action==='openPetri'?h.api.openPetri({transition_id:'step.run'}):h.document.getElementById('mode-overview').onclick();
        await navigate();
        assert.equal(h.api.activityState().observation,null);assert.equal(h.api.activityState().paused,false);
        assert.equal(checkbox.checked,autoRefresh);assert.equal(checkbox.disabled,false);
        assert.doesNotMatch(h.document.getElementById('source-badge').textContent,/retained/i);
        assert.equal(h.timers.size,autoRefresh?1:0);for(const timer of h.timers.values())assert.equal(timer.ms,2500);
        const resumedTimers=[...h.timers.keys()];if(!loaded)assert.deepEqual(resumedTimers,beforeTimers);
        await navigate();await navigate();assert.deepEqual([...h.timers.keys()],resumedTimers);
        assert.equal(JSON.stringify(h.api.state().frame),beforeFrame);assert.equal(requests.length,loaded?1:0);
        for(const path of requests)assert.ok(path.startsWith('/api/v2/firing-activity'));
    });
