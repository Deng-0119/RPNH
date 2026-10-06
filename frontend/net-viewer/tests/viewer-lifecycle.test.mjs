import test,{afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {checkpointApp,response,deferred} from './checkpoint-test-harness.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {checkpointPath} from '../../../cpn/frontend/static/checkpoint-view.mjs';

const fixture=JSON.parse(readFileSync(new URL('./comparison-fixture.json',import.meta.url),'utf8'));
afterEach(()=>setLanguage('en'));
const walk=node=>[node,...node.children.flatMap(walk)];
const button=(h,text)=>walk(h.document.getElementById('comparison-panel')).find(n=>n.tagName==='button'&&n.textContent===text);
function lifecycle(h,type,persisted) {
    const event=new Event(type);Object.defineProperty(event,'persisted',{value:persisted});h.window.dispatchEvent(event);
}
function clearedComparison(h) {
    const state=h.api.comparisonPanel.state;
    assert.equal(state.open,false);assert.equal(state.value,null);assert.equal(state.loading,false);
    assert.equal(state.source,null);assert.equal(state.pair,null);assert.deepEqual(state.options,[]);
    assert.equal(h.document.getElementById('comparison-panel').textContent,'');
}
async function boot() {
    const requests=[], current=structuredClone(fixture.right.frame);
    current.position.mode='live';current.coverage.history='current_net_canonical_checkpoints';
    let compare=async()=>response(fixture), dashboard=async()=>response(current);
    const h=checkpointApp(async(path,options)=>{
        requests.push({path,...options});
        if(path.startsWith('/api/v2/comparison-view'))return compare();
        if(path==='/api/v1/dashboard')return dashboard();
        if(path==='/api/v1/history')return response({schema_version:'rpnh/dashboard_history/v1',
            source:current.source,coverage:'current_net_canonical_checkpoints',next_before:null,end_reason:'initial_checkpoint',
            items:[fixture.left_selector,fixture.right_selector].map(s=>({cursor:s.cut,checkpoint_ref:s.checkpoint_ref}))});
        throw Error('Unexpected request: '+path);
    },{startup:true});
    await h.ready;
    assert.ok(h.api.state().frame,'production startup must finish its first dashboard render');
    assert.deepEqual(h.document.head.children.map(node=>node.src),['/assets/joint.js','/assets/elk-api.js']);
    assert.equal(typeof h.document.getElementById('refresh').onclick,'function','start must bind production controls');
    await h.api.mode('petri');
    h.window.history={pushState(){assert.fail('Lifecycle must not write history');},replaceState(){assert.fail('Lifecycle must not replace history');}};
    return {...h,requests,current,setComparison:fn=>{compare=fn;},setDashboard:fn=>{dashboard=fn;}};
}
async function refresh(h,autoRefresh) {
    if(!autoRefresh)return h.document.getElementById('refresh').onclick();
    assert.equal(h.timers.size,1,'exactly one poll must be queued');
    const [id,timer]=[...h.timers][0];h.timers.delete(id);await timer.fn();
}
function attached(h,viewer) {
    assert.equal(h.api.viewer(),viewer,'restoration keeps the original renderer');
    assert.equal(viewer.paper.removed,false);
    assert.equal(viewer.element.parentElement,h.document.getElementById('canvas-wrap'));
    assert.equal(viewer.resize.connected,true);
}

for(const autoRefresh of [true,false])
for(const pending of [true,false])test(`full startup bfcache preserves renderer and refresh ${autoRefresh}, comparison pending ${pending}`,async()=>{
    const h=await boot(),viewer=h.api.viewer(),late=deferred();
    h.document.getElementById('auto-refresh').checked=autoRefresh;h.document.getElementById('auto-refresh').onchange();
    if(pending)h.setComparison(()=>late.promise);
    h.document.getElementById('compare-checkpoints').onclick();
    const read=button(h,'Read comparison').onclick();
    if(!pending){await read;assert.ok(h.api.comparisonPanel.state.value);}
    const comparisonRequest=h.requests.at(-1);
    assert.equal(comparisonRequest.cache,'no-store');assert.equal(h.timers.size,0);
    lifecycle(h,'pagehide',true);
    clearedComparison(h);assert.equal(comparisonRequest.signal.aborted,true);assert.equal(h.timers.size,0);
    attached(h,viewer);
    lifecycle(h,'pageshow',true);
    late.resolve(response(fixture));await read;
    clearedComparison(h);attached(h,viewer);
    assert.equal(h.document.getElementById('auto-refresh').checked,autoRefresh);
    assert.equal(h.timers.size,autoRefresh?1:0);
    h.current.net.nodes[0].label='Fresh after first restore';await refresh(h,autoRefresh);
    assert.equal(viewer.snapshot.nodes[0].label,'Fresh after first restore');
    // A later traversal has no open comparison to drive its onClose callback.
    for(let cycle=0;cycle<2;cycle++) {
        lifecycle(h,'pagehide',true);assert.equal(h.timers.size,0);attached(h,viewer);
        lifecycle(h,'pageshow',true);lifecycle(h,'popstate',true);lifecycle(h,'pageshow',true);
        clearedComparison(h);assert.equal(h.timers.size,autoRefresh?1:0);
        h.current.net.nodes[0].label=`Fresh repeat ${cycle}`;await refresh(h,autoRefresh);
        assert.equal(viewer.snapshot.nodes[0].label,`Fresh repeat ${cycle}`);attached(h,viewer);
    }
    viewer.container.clientWidth=720;viewer.resize.notify();assert.equal(viewer.paper.options.width,720);
    const selected=h.document.getElementById('auto-refresh');selected.checked=!autoRefresh;selected.onchange();
    lifecycle(h,'pagehide',true);lifecycle(h,'pageshow',true);
    assert.equal(selected.checked,!autoRefresh);assert.equal(h.timers.size,autoRefresh?0:1);
});

for(const persisted of [false,undefined])test(`full startup ordinary pagehide (${persisted}) disposes renderer and cancels reads`,async()=>{
    const h=await boot(),viewer=h.api.viewer(),late=deferred();
    h.document.getElementById('auto-refresh').checked=true;h.api.schedule();
    h.setComparison(()=>late.promise);h.api.openComparison();const pending=button(h,'Read comparison').onclick();
    const request=h.requests.at(-1);h.api.pending();
    lifecycle(h,'pagehide',persisted);
    assert.equal(viewer.paper.removed,true);assert.equal(viewer.element.parentElement,null);assert.equal(viewer.resize.connected,false);
    assert.equal(request.signal.aborted,true);assert.equal(h.api.state().playing,false);assert.equal(h.timers.size,0);
    late.resolve(response(fixture));await pending;clearedComparison(h);assert.equal(h.timers.size,0);
});

test('full startup pagehide aborts a pending dashboard; late response cannot render or restart polling',async()=>{
    const h=await boot(),viewer=h.api.viewer(),late=deferred(),original=h.api.state().frame;
    h.document.getElementById('auto-refresh').checked=true;
    h.setDashboard(()=>late.promise);const pending=h.document.getElementById('refresh').onclick();
    const request=h.requests.at(-1);h.api.pending();
    lifecycle(h,'pagehide',true);
    assert.equal(request.signal.aborted,true);assert.equal(h.api.state().playing,false);assert.equal(h.timers.size,0);
    const stale=structuredClone(h.current);stale.net.nodes[0].label='Must not render';late.resolve(response(stale));await pending;
    assert.equal(h.api.state().frame,original);assert.notEqual(viewer.snapshot.nodes[0].label,'Must not render');assert.equal(h.timers.size,0);
    lifecycle(h,'pageshow',true);h.setDashboard(()=>response(h.current));await refresh(h,true);attached(h,viewer);
});

for(const cancellation of ['persisted restore','comparison'])
test(`full startup interrupted previous-net navigation releases controls after ${cancellation}`,async()=>{
    const current=structuredClone(fixture.right.frame),probe=structuredClone(fixture.right);
    current.position.mode='live';current.coverage.history='current_net_canonical_checkpoints';
    const old=structuredClone(fixture.left);
    old.selector.net_ref={entity_type:'net_instance/v1',logical_id:'net_instance:'+ '7'.repeat(32),version_id:'net_instance_version:'+ '7'.repeat(32)};
    old.frame.source.net_ref=structuredClone(old.selector.net_ref);
    old.frame.net.source.net_ref=structuredClone(old.selector.net_ref);
    probe.navigation.previous_net_segment=old.selector;
    probe.navigation.end_reason='net_version_boundary';
    let resolveLate,rejectLate;
    const late={promise:new Promise((resolve,reject)=>{resolveLate=resolve;rejectLate=reject;})},requests=[];
    const h=checkpointApp(async(path,options)=>{
        requests.push({path,...options});
        if(path===checkpointPath(old.selector)) {
            if(cancellation==='comparison')options.signal.addEventListener('abort',()=>rejectLate(new DOMException('Aborted','AbortError')),{once:true});
            return late.promise;
        }
        if(path==='/api/v1/dashboard')return response(current);
        if(path==='/api/v1/history')return response({schema_version:'rpnh/dashboard_history/v1',
            source:current.source,coverage:'current_net_canonical_checkpoints',next_before:null,end_reason:'net_version_boundary',
            items:[{cursor:fixture.right_selector.cut,checkpoint_ref:fixture.right_selector.checkpoint_ref}]});
        return response(probe);
    },{startup:true});
    await h.ready;
    assert.ok(h.api.state().checkpointProbe?.navigation.previous_net_segment);
    const viewer=h.api.viewer(),original=h.api.state().frame;
    h.document.getElementById('auto-refresh').checked=true;
    const pending=h.document.getElementById('previous-net').onclick(),request=requests.at(-1);
    assert.equal(h.api.state().checkpointBusy,true);
    if(cancellation==='persisted restore') {
        lifecycle(h,'pagehide',true);lifecycle(h,'pageshow',true);resolveLate(response(old));
    } else {
        h.document.getElementById('compare-checkpoints').onclick();
        button(h,'Close comparison').onclick();
    }
    assert.equal(request.signal.aborted,true);await pending;
    assert.equal(h.api.state().frame,original);attached(h,viewer);
    assert.equal(h.api.state().checkpointBusy,false);
    for(const id of ['previous-net','refresh','auto-refresh'])assert.equal(h.document.getElementById(id).disabled,false,id);
    assert.equal(h.document.getElementById('auto-refresh').checked,false,'cross-net navigation already paused automatic refresh');
    assert.equal(h.timers.size,0);
    h.document.getElementById('auto-refresh').checked=true;
    h.document.getElementById('auto-refresh').onchange();
    assert.equal(h.timers.size,1);
    if(cancellation==='comparison') {
        const before=requests.length;await h.document.getElementById('refresh').onclick();
        assert(requests.length>before,'ordinary refresh works after cancelling navigation');
        return;
    }
    await h.document.getElementById('previous-net').onclick();
    assert.ok(h.api.state().checkpointView,'cancelled navigation can be retried');
    assert.equal(h.api.state().checkpointBusy,false);
    assert.equal(h.document.getElementById('auto-refresh').disabled,true);
    assert.equal(h.timers.size,0,'saved historical capture must not start live polling');
});
