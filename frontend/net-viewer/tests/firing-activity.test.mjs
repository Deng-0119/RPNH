import test from 'node:test';
import assert from 'node:assert/strict';
import {activityRequest,activityPath,normalizeActivityPage,mergeActivity,activityCards,resolveActivityTarget} from '../../../cpn/frontend/static/firing-activity.mjs';
import {fixtureFrame,envelope} from './firing-activity-fixture.mjs';
const normalized=(value,request=activityRequest(fixtureFrame(),['step.run'],value.page.request_cursor,value.page.limit))=>normalizeActivityPage(value,request,fixtureFrame());
test('separate observation never promotes provisional or derives unobserved start',()=>{
    const frame=fixtureFrame(),before=JSON.stringify(frame),a=envelope({limit:2,count:2,more:true});
    const one=mergeActivity(null,normalized(a));assert.equal(activityCards(one)[0].observed_stage,'unknown');assert.equal(one.records.length,2);assert.equal(one.firings.length,1);assert.equal(one.complete,false);
    const two=mergeActivity(one,normalized(envelope({cursor:'YQ',limit:2,count:1,start:2})));
    assert.equal(activityCards(two)[0].observed_stage,'started');assert.equal(two.complete,true);assert.equal(two.records.length,3);assert.equal(JSON.stringify(frame),before);
    assert.equal(two.firings[0].result,'not_provided');assert.equal(two.firings[0].publication_visible_position,null);
    const target=activityCards(two)[0].target;assert.deepEqual(resolveActivityTarget(two,target),target);assert.deepEqual(resolveActivityTarget(one,target),target.context_key===one.context_key?target:null);
    assert.equal(resolveActivityTarget(two,{...target,context_key:'foreign'}),null);
});
test('query has exact selector, bounded explicit members and no arbitrary evidence cut',()=>{
    const f=fixtureFrame();assert.match(activityPath(activityRequest(f,['step.run'])),/^\/api\/v2\/firing-activity\?/);
    for(const members of [[],['step.run','step.run'],['foreign']])assert.throws(()=>activityRequest(f,members));
    assert.throws(()=>activityRequest(f,['step.run'],'',50));assert.throws(()=>activityRequest(f,['step.run'],null,101));
});
test('strict decoder rejects source/task/run scope, typed ref, cuts, coverage and outcome lies',()=>{
    const mutations=[v=>v.view.context.query_scope.task_ref.logical_id='task:'+'b'.repeat(32),v=>v.view.activity.source_identity.run_ref={...v.view.activity.source_identity.run_ref,version_id:'run_version:'+'b'.repeat(32)},
        v=>v.selector.cut++,v=>v.view.context.query_scope.transition_ids=['other'],v=>v.view.activity.supported_event_types.reverse(),
        v=>v.view.activity.records[0].firing_ref={...v.view.activity.records[0].firing_ref,logical_id:'transition_firing:'+'b'.repeat(32)},v=>v.view.activity.records[0].transaction_commit_ordinal=1,
        v=>v.view.activity.records[0].evidence.visible_position=100,v=>v.view.activity.firings[0].result='success',v=>v.view.activity.lifecycle_coverage='complete',
        v=>v.view.activity.coverage.total_count=3,v=>v.page.next_cursor='YQ',v=>v.view.activity.firings[0].node_ref.logical_id='node:'+'b'.repeat(32)];
    for(const [i,change] of mutations.entries()){const v=structuredClone(envelope());change(v);assert.throws(()=>normalized(v),`mutation ${i}`);}
});
test('only continuous first-to-last pages establish the supported scope',()=>{
    const first=mergeActivity(null,normalized(envelope({limit:2,count:2,more:true})));
    const suffix=normalized(envelope({cursor:'YQ',limit:2,count:1,start:2}));
    assert.throws(()=>mergeActivity(null,suffix));const done=mergeActivity(first,suffix);assert.throws(()=>mergeActivity(done,suffix));
    assert.throws(()=>mergeActivity(first,normalized(envelope({cursor:'YQ',limit:2,count:1,start:2,head:501}))));
    assert.throws(()=>mergeActivity(first,normalized(envelope({cursor:'YQ',limit:2,count:1,start:0}))));
    const foreign=normalized(envelope({cursor:'YQ',limit:2,count:1,start:2}));foreign.view.activity.firings[0].attempt_index=2;assert.throws(()=>mergeActivity(first,foreign));
});
test('refresh atomically replaces rather than mixing heads; same epoch cannot excuse a new head',()=>{
    const old=mergeActivity(null,normalized(envelope()));const fresh=mergeActivity(old,normalized(envelope({head:501,count:1})));
    assert.equal(fresh.records.length,1);assert.equal(fresh.context.source_cuts[0].cut.head_ordinal,501);assert.equal(old.records.length,3);
});
test('a published-class synthetic page still supplies no settlement or result',()=>{
    const v=envelope(),f=v.view.activity.firings[0];f.publication_class_at_evidence='PUBLISHED';f.publication_visible_position=90;
    for(const r of v.view.activity.records){r.evidence.publication_class='PUBLISHED';r.evidence.visible_position=Math.max(90,r.transaction_commit_ordinal);}
    const state=mergeActivity(null,normalized(v));assert.equal(activityCards(state)[0].observed_stage,'started');assert.equal(state.firings[0].completion,'not_provided');assert.equal(state.firings[0].business_outcome,'not_provided');
});
test('cursor cycles fail even when they skip across multiple valid page boundaries',()=>{
    const a=mergeActivity(null,normalized(envelope({limit:2,count:2,more:true,next:'YQ'})));
    const b=mergeActivity(a,normalized(envelope({cursor:'YQ',limit:2,count:2,start:2,more:true,next:'Yg'})));
    assert.throws(()=>mergeActivity(b,normalized(envelope({cursor:'Yg',limit:2,count:2,start:4,more:true,next:'YQ'}))));
});
test('empty first page completes only the three-type supported scope',()=>{
    const page=normalized(envelope({count:0}));const state=mergeActivity(null,page);assert.equal(state.complete,true);assert.equal(state.records.length,0);assert.equal(page.view.activity.lifecycle_coverage,'not_provided');assert.equal(page.view.activity.coverage.total_count,null);
});
