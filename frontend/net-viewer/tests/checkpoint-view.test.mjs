import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeFrame} from '../../../cpn/frontend/static/dashboard-model.mjs';
import {checkpointSelector, checkpointPath, selectorForFrame, normalizeCheckpointView} from '../../../cpn/frontend/static/checkpoint-view.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {checkpointApp, response, deferred} from './checkpoint-test-harness.mjs';
afterEach(()=>setLanguage('en'));
const ref=(type,kind,version,n)=>({entity_type:type,logical_id:kind+':'+String(n).repeat(32),version_id:version+':'+String(n).repeat(32)});
const selected=(n,cut)=>({net_ref:ref('net_instance/v1','net_instance','net_instance_version',n),checkpoint_ref:ref('marking_checkpoint/v1','marking_checkpoint','marking_checkpoint_version',n),cut});
const old=selected(1,75), current=selected(2,130);
function envelope(selector=current) {
    const source={mode:'registry_current',task_id:'task:'+ 'a'.repeat(32),run_dir:'/display-only/run',net_ref:selector.net_ref,verified_head_ordinal:selector.cut,writer_fencing_epoch:2};
    return {schema_version:'rpnh/checkpoint_view/v1',selector:structuredClone(selector),capture:{head_ordinal:150,writer_fencing_epoch:2},
        navigation:{previous_net_segment:selector.cut===130?structuredClone(old):null,coverage:'complete',end_reason:selector.cut===130?'net_version_boundary':'initial_checkpoint',loaded_checkpoints:2,max_chain:2048},
        adoption_evidence:{status:'no_evidence_at_cut',coverage:'complete',cut:selector.cut,current_net_ref:null,records:[]},coverage:{firings:'not_provided'},
        frame:{schema_version:'rpnh/dashboard/v1',source,net:{schema_version:'rpnh/net_view/v1',source:structuredClone(source),summary:{},
            marking:{checkpoint_ref:selector.checkpoint_ref,epoch:0,token_count:1,active_token_count:1},nodes:[
                {id:'p',label:'p',kind:'place',category:'place',tokens:[{token_ref:{logical_id:'token-'+selector.cut},place:'p',resource_ref:null,active_in_checkpoint:true}],active_token_count:1},
                {id:'t',label:'t',kind:'transition',category:'execution',inputs:['in'],outputs:[]}],edges:[]},
            boundaries:{entry:[{name:'in',port:'in',place:'p'}],exit:[],terminal_rules:[]},
            transition_bindings:[{transition_id:'t',input_ports:[{name:'in',place:'p'}],output_ports:[]}],agent_nodes:[],
            presentation:{nodes:{}},position:{mode:'history',cursor:selector.cut,latest_head:150},coverage:{history:'selected_saved_checkpoint',firings:'not_provided'},change:{}}};
}
function live() {const value=structuredClone(envelope().frame);value.position.mode='live';value.source.verified_head_ordinal=value.net.source.verified_head_ordinal=150;value.coverage.history='current_net_canonical_checkpoints';value.coverage.firings='current_observations';value.net.nodes[1].runtime={firings:[]};return normalizeFrame(value);}
const page=()=>({schema_version:'rpnh/dashboard_history/v1',source:live().source,coverage:'current_net_canonical_checkpoints',items:[{cursor:130,checkpoint_ref:current.checkpoint_ref}],next_before:null,end_reason:'net_version_boundary'});

test('new envelope preserves exact checkpoint cut, separate capture, and missing firing evidence',()=>{
    const value=normalizeCheckpointView(envelope(old),old,live().source);
    assert.equal(value.frame.position.cursor,75);assert.equal(value.capture.head_ordinal,150);assert.equal(value.frame.coverage.firings,'not_provided');
    assert.match(checkpointPath(old),/^\/api\/v2\/checkpoint-view\?net_ref=/);assert.deepEqual(selectorForFrame(live()),current);
});
test('exact selector rejects missing, additional and wrongly typed fields',()=>{
    for(const value of [{...current,cut:true},{...current,cut:0},{...current,extra:1},{...current,net_ref:{...current.net_ref,entity_type:'task/v1'}},{...current,checkpoint_ref:null}])assert.throws(()=>checkpointSelector(value));
});
test('envelope rejects frame substitution, mixed cuts, source change and malformed navigation',()=>{
    for(const change of [v=>v.selector.cut++,v=>v.frame.source.net_ref=old.net_ref,v=>v.frame.position.cursor=75,v=>v.frame.net.marking.checkpoint_ref=old.checkpoint_ref,
        v=>v.frame.source.run_dir='/another-run',v=>v.frame.net.source.task_id='another-task',v=>v.capture.writer_fencing_epoch++,v=>v.navigation.previous_net_segment=current,v=>v.navigation.coverage='unknown',
        v=>v.frame.transition_bindings=[],v=>v.frame.net.nodes[1].runtime={firings:[]},v=>v.frame.coverage.firings='canonical_only']){
        const value=envelope();change(value);assert.throws(()=>normalizeCheckpointView(value,current,live().source));
    }
});
test('no-evidence requires complete query; partial evidence stays unknown',()=>{
    const value=envelope(old);value.adoption_evidence.coverage='partial';assert.throws(()=>normalizeCheckpointView(value,old));
    value.adoption_evidence.status='unknown';assert.equal(normalizeCheckpointView(value,old).adoption_evidence.status,'unknown');
    value.adoption_evidence.status='current_at_cut';value.adoption_evidence.coverage='complete';assert.throws(()=>normalizeCheckpointView(value,old));
});
for(const language of ['en','zh-CN']) test(`actual app enters exact old net and returns retained live H150, never fresh latest in ${language}`,async()=>{
    setLanguage(language);const paths=[];
    const h=checkpointApp(async path=>{paths.push(path);if(path===checkpointPath(old))return response(envelope(old));throw Error('unexpected request '+path);});
    const initial=live();h.api.install(initial,envelope());h.api.history(page());await h.api.render();h.api.select('node','p');h.api.viewport();h.api.pending();
    h.document.getElementById('auto-refresh').checked=true;
    await h.api.openPreviousNet();let state=h.api.state();
    assert.equal(state.frame.source.verified_head_ordinal,75);assert.equal(state.selection,null);assert.equal(state.playing,false);assert.equal(state.viewports.length,0);assert.equal(h.motions.length,0);
    assert.equal(state.timeline.items.length,0);assert.equal(h.timers.size,0);assert.equal(h.document.getElementById('time-slider').disabled,true);
    assert.equal(h.document.getElementById('auto-refresh').checked,false);assert.match(h.document.getElementById('checkpoint-status').textContent,/75/);
    await h.api.returnCheckpointCapture();state=h.api.state();assert.equal(state.frame,initial);assert.equal(state.frame.position.cursor,130);assert.equal(state.frame.source.verified_head_ordinal,150);
    assert.equal(state.retainedCapture,true);assert.equal(state.timeline.items.length,1);assert.equal(state.selection,null);assert.equal(h.document.getElementById('refresh').disabled,true);
    assert.equal(h.document.getElementById('auto-refresh').disabled,true);assert.equal(h.document.getElementById('back-live').getAttribute('aria-pressed'),'false');
    assert.match(h.document.getElementById('source-badge').textContent,language==='en'?/Retained/:/保留/);
    h.api.schedule();await h.api.loadFrame(null);assert.equal(paths.length,1);assert.equal(h.timers.size,0);
});
test('actual v1 history boundary probes exact current checkpoint without replacing live frame',async()=>{
    const paths=[];const h=checkpointApp(async path=>{paths.push(path);return response(path==='/api/v1/history'?page():envelope());});
    const initial=live();h.api.install(initial);await h.api.loadHistory();assert.equal(h.api.state().frame,initial);assert.equal(h.api.state().checkpointProbe.selector.cut,130);
    assert.deepEqual(paths,['/api/v1/history',checkpointPath(current)]);
});
for(const status of [404,501]) test(`v2 ${status} keeps complete frame and never falls back to v1 net`,async()=>{
    const paths=[];const h=checkpointApp(async path=>{paths.push(path);return response({error:'unavailable'},status);});const initial=live();h.api.install(initial,envelope());
    await h.api.openPreviousNet();assert.equal(h.api.state().frame,initial);assert.equal(h.api.state().returnCount,0);assert.deepEqual(paths,[checkpointPath(old)]);
});
test('newer live request defeats an old delayed checkpoint response',async()=>{
    const delayed=deferred(),paths=[];const h=checkpointApp(async path=>{paths.push(path);if(path===checkpointPath(old))return delayed.promise;if(path==='/api/v1/dashboard')return response(live());if(path==='/api/v1/history')return response(page());return response(envelope());});
    h.api.install(live(),envelope());const pending=h.api.openPreviousNet();await h.api.returnToLive();delayed.resolve(response(envelope(old)));await pending;
    assert.equal(h.api.state().frame.source.verified_head_ordinal,150);assert.equal(h.api.state().checkpointView,null);assert.equal(h.api.state().returnCount,0);
});
test('duplicate click makes one request; mismatched source retains frame',async()=>{
    const delayed=deferred();let calls=0;const h=checkpointApp(async()=>{calls++;return delayed.promise;});const initial=live();h.api.install(initial,envelope());const pending=h.api.openPreviousNet();await h.api.openPreviousNet();
    const value=envelope(old);value.frame.source.task_id='foreign';delayed.resolve(response(value));await pending;
    assert.equal(calls,1);assert.equal(h.api.state().frame,initial);assert.equal(h.api.state().returnCount,0);
});
test('layout failure preserves complete old view and its return stack',async()=>{
    const h=checkpointApp(async()=>response(envelope(old)));const initial=live();h.api.install(initial,envelope());h.setLayout(async()=>{throw Error('layout failed');});
    await h.api.openPreviousNet();assert.equal(h.api.state().frame,initial);assert.equal(h.api.state().returnCount,0);
});

test('ordinary v1 return-to-live preserves the existing polling choice',async()=>{
    const h=checkpointApp(async path=>response(path==='/api/v1/dashboard'?live():path==='/api/v1/history'?page():envelope()));
    h.api.install(live());h.document.getElementById('auto-refresh').checked=true;
    await h.api.returnToLive();assert.equal(h.document.getElementById('auto-refresh').checked,true);assert.ok(h.timers.size>0);
});
