/** UI protocol tests; the Python tests use original registered runtime objects. */
import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {executionNetRequest, executionNetPath, normalizeExecutionNets, createExecutionNets, renderExecutionNets} from '../../cpn/frontend/static/panels.mjs';
import {setLanguage} from '../../cpn/frontend/static/i18n.mjs';
import {checkpointApp, response, deferred} from '../../frontend/net-viewer/tests/checkpoint-test-harness.mjs';
import {fixtureFrame, envelope} from '../../frontend/net-viewer/tests/firing-activity-fixture.mjs';
const ref=(type,n)=>({entity_type:type,logical_id:`${type.split('/')[0]}:${n.repeat(32)}`,version_id:`${type.split('/')[0]}_version:${n.repeat(32)}`});
const firing={status:'started',attempt_index:1,firing_ref:ref('transition_firing/v1','a')};
function frame(){const f=fixtureFrame();f.position.mode='live';f.coverage.firings='current_observations';f.net.nodes[0].runtime={firings:[firing]};return f;}
function payload(f){
    const p=executionNetRequest(f,firing),parent={parent_business_firing_ref:p.firing_ref,parent_business_net_ref:p.net_ref,
        parent_invocation_ref:ref('invocation/v1','b'),parent_business_checkpoint_ref:p.checkpoint_ref};
    const instance=ref('execution_instance/v1','c'),definition=ref('execution_net_definition/v1','d'),checkpoint=ref('execution_checkpoint/v1','e');
    return {schema_version:'rpnh/execution_nets/v1',read_only:true,coverage:'current_attached_execution_nets',
        selector:{net_ref:p.net_ref,checkpoint_ref:p.checkpoint_ref,cut:p.cut},source:p.expected_source,capture:p.expected_capture,
        firing_ref:p.firing_ref,parent,instance_ref:null,publication_state:'PROVISIONAL',children:[{...parent,
            execution_instance_ref:instance,execution_net_definition_ref:definition,checkpoint_ref:checkpoint,
            status:'running',map_ready:false,map_ready_checkpoint_ref:null,sealed:false,evidence_refs:[],
            tokens:[{token_ref:ref('execution_token/v1','f'),place:'pending',ordinal:0,produced_by_firing_ref:null}],active_firings:[],
            definition:{execution_net_definition_ref:definition,definition_key:'original.fixture',places:['pending','done'],
                transitions:[{transition_id:'finish',recovery_mode:'pure'}],input_arcs:[{place:'pending',transition_id:'finish',weight:2}],
                output_arcs:[{place:'done',transition_id:'finish',weight:1}],initial_place:'pending',initial_tokens:2,terminal_places:['done']}}]};
}
const walk=n=>[n,...n.children.flatMap(walk)];
afterEach(()=>setLanguage('en'));

test('scope checks reject history, another parent/source/capture, and malformed topology',()=>{
    const f=frame(),p=executionNetRequest(f,firing),v=payload(f);
    assert.equal(normalizeExecutionNets(v,p),v);
    for(const mutate of [x=>x.capture.head_ordinal++,x=>x.source.task_id='other',x=>x.parent.parent_business_firing_ref=ref('transition_firing/v1','1'),
        x=>x.children[0].parent_invocation_ref=ref('invocation/v1','1'),x=>x.children[0].definition.input_arcs[0].place='invented',x=>x.children.push(x.children[0])]){
        const bad=structuredClone(v);mutate(bad);assert.throws(()=>normalizeExecutionNets(bad,p));
    }
    f.position.mode='history';assert.throws(()=>executionNetRequest(f,firing));
});

test('actual app opens attached instance, draws exact PN, retains business graph and returns',async()=>{
    const f=frame(),original=structuredClone(f),v=payload(f),p=executionNetRequest(f,firing);
    const h=checkpointApp(async path=>{assert.equal(path,executionNetPath(p));return response(v);});
    h.api.install(f);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
    const detail=()=>h.document.getElementById('detail'),find=(key,val)=>walk(detail()).find(n=>n.dataset[key]===val);
    h.document.getElementById('auto-refresh').checked=true;h.api.schedule();
    await find('executionOpen',firing.firing_ref.version_id).onclick();assert.equal(h.timers.size,0);
    find('executionInstance',v.children[0].execution_instance_ref.version_id).onclick();
    assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-place']).length,2);
    assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-transition']).length,1);
    assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-arc']).length,2);
    assert.ok(detail().textContent.includes(v.children[0].checkpoint_ref.version_id));
    assert.equal(walk(detail()).find(n=>n.dataset.firingId).open,true);
    assert.deepEqual(f,original);assert.equal(h.api.state().frame,f);
    walk(detail()).find(n=>n.tagName==='button'&&n.textContent==='Return to business firing').onclick();
    assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-place']).length,0);
    assert.ok(h.timers.size>0);
});

test('navigation discards pending response and access/stale failures clear old detail',async()=>{
    const f=frame(),v=payload(f),late=deferred();
    const h=checkpointApp(async()=>late.promise);h.api.install(f);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
    const detail=()=>h.document.getElementById('detail');
    const pending=walk(detail()).find(n=>n.dataset.executionOpen).onclick();
    h.api.select('node','other');late.resolve(response(v));await pending;
    assert.ok(!walk(detail()).some(n=>n.dataset.executionInstance));
    for(const status of [403,409]){
        let reject=false;
        const control=createExecutionNets({getFrame:()=>f,request:async()=>{if(reject)throw {status};return v;},onOpen(){},onClose(){},onChange(){}});
        await control.load(firing);control.select(v.children[0].execution_instance_ref);
        control.select(ref('execution_instance/v1','1'));assert.deepEqual(control.state.selected,v.children[0].execution_instance_ref);
        reject=true;await control.load(firing);assert.equal(control.state.value,null);assert.equal(control.state.selected,null);
        assert.equal(control.state.error,status===403?'access_changed':'stale_observation');
    }
});

test('execution labels are bilingual and original labels render literally',()=>{
    const h=checkpointApp(async()=>response({})),f=frame(),v=payload(f),p=executionNetRequest(f,firing);
    v.children[0].definition.definition_key='<img src=x onerror=bad()>';
    for(const lang of ['en','zh-CN']){
        setLanguage(lang);const target=h.document.createElement('section');
        renderExecutionNets(target,f,firing,{state:{open:true,params:p,value:v,selected:v.children[0].execution_instance_ref},load(){},close(){},select(){}});
        assert.ok(target.textContent.includes(v.children[0].definition.definition_key));
        assert.ok(!walk(target).some(n=>n.tagName==='img'));
        assert.ok(target.textContent.includes(lang==='en'?'View execution child nets':'查看执行子网'));
    }
});


for(const status of [200,503]) for(const autoRefresh of [true,false])
    test(`execution navigation cancels pending activity status=${status} and restores polling=${autoRefresh}`,async()=>{
        const f=frame(),original=structuredClone(f),v=payload(f),late=deferred();let activitySignal;
        const h=checkpointApp(async(path,options)=>{
            if(path.startsWith('/api/v2/firing-activity')){activitySignal=options.signal;return late.promise;}
            assert.equal(path,executionNetPath(executionNetRequest(f,firing)));return response(v);
        });
        h.api.install(f);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
        const detail=()=>h.document.getElementById('detail'),find=(key,val)=>walk(detail()).find(n=>n.dataset[key]===val);
        const checkbox=h.document.getElementById('auto-refresh');checkbox.checked=autoRefresh;h.api.schedule();
        const pending=find('activityAction','load').onclick();
        assert.equal(h.api.activityState().loading,true);assert.equal(h.api.activityState().paused,true);
        await find('executionOpen',firing.firing_ref.version_id).onclick();
        late.resolve(response(status===200?envelope():{error:'late read failure'},status));await pending;
        assert.equal(activitySignal.aborted,true);
        assert.equal(h.api.activityState().loading,false);assert.equal(h.api.activityState().paused,false);
        assert.equal(h.api.activityState().observation,null);assert.equal(h.api.activityState().error,null);
        find('executionInstance',v.children[0].execution_instance_ref.version_id).onclick();
        assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-place']).length,2);
        assert.equal(walk(detail()).find(n=>n.dataset.firingId?.includes(firing.firing_ref.version_id)).open,true);
        assert.equal(h.timers.size,0);assert.equal(checkbox.disabled,true);
        walk(detail()).find(n=>n.tagName==='button'&&n.textContent==='Return to business firing').onclick();
        assert.equal(checkbox.checked,autoRefresh);assert.equal(checkbox.disabled,false);
        assert.equal(h.timers.size,autoRefresh?1:0);
        assert.equal(h.api.state().selection.id,'step.run');assert.equal(h.api.state().firingTarget,null);
        assert.equal(h.api.state().frame,f);assert.deepEqual(f,original);
    });

for(const status of [409,503])
    test(`global refresh immediately clears execution detail even when dashboard fails with ${status}`,async()=>{
        const f=frame(),original=structuredClone(f),v=payload(f),dashboard=deferred();
        const h=checkpointApp(async path=>{
            if(path==='/api/v1/dashboard')return dashboard.promise;
            assert.equal(path,executionNetPath(executionNetRequest(f,firing)));return response(v);
        });
        h.api.install(f);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
        const detail=()=>h.document.getElementById('detail'),find=(key,val)=>walk(detail()).find(n=>n.dataset[key]===val);
        h.document.getElementById('auto-refresh').checked=true;
        await find('executionOpen',firing.firing_ref.version_id).onclick();
        find('executionInstance',v.children[0].execution_instance_ref.version_id).onclick();
        assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-place']).length,2);
        const pending=h.api.loadFrame(null);
        const cleared=()=>{
            assert.ok(!walk(detail()).some(n=>n.dataset.executionInstance));
            assert.ok(!walk(detail()).some(n=>n.attributes['data-execution-place']));
            assert.ok(!walk(detail()).some(n=>n.tagName==='button'&&n.textContent==='Return to business firing'));
            assert.ok(find('executionOpen',firing.firing_ref.version_id));
        };
        // Inspect while dashboard is still pending, before either success or failure can repaint.
        let immediateError;try{cleared();}catch(error){immediateError=error;}
        dashboard.resolve(response({error:'dashboard unavailable'},status));await pending;
        if(immediateError)throw immediateError;
        cleared();assert.equal(h.document.getElementById('health').dataset.state,'stale');
        assert.equal(h.document.getElementById('auto-refresh').disabled,false);assert.equal(h.timers.size,1);
        assert.equal(h.api.state().selection.id,'step.run');assert.equal(h.api.state().frame,f);assert.deepEqual(f,original);
        // The still-valid business firing can explicitly reopen its attached child after failure.
        await find('executionOpen',firing.firing_ref.version_id).onclick();
        find('executionInstance',v.children[0].execution_instance_ref.version_id).onclick();
        assert.equal(walk(detail()).filter(n=>n.attributes['data-execution-place']).length,2);
    });
