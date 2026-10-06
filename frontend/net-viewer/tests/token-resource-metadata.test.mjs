import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {normalizeFrame} from '../../../cpn/frontend/static/dashboard-model.mjs';
import {stable} from '../../../cpn/frontend/static/model.mjs';
import {checkpointReference,tokenResourceRequest,tokenResourcePath,tokenResourceTarget,normalizeTokenResourceMetadata} from '../../../cpn/frontend/static/checkpoint-view.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {checkpointApp,response,deferred} from './checkpoint-test-harness.mjs';
afterEach(()=>setLanguage('en'));
const ref=(kind,n,stem=kind.split('/')[0])=>({entity_type:kind,logical_id:`${stem}:${n.repeat(32)}`,version_id:`${stem}_version:${n.repeat(32)}`});
const pair={resource_id:'resource:'+'2'.repeat(32),resource_version_id:'resource_version:'+'3'.repeat(32)};
function envelope(cut=75) {
    const selected={net_ref:ref('net_instance/v1',cut===75?'4':'9'),checkpoint_ref:ref('marking_checkpoint/v1',cut===75?'5':'a'),cut};
    const capture={head_ordinal:130,writer_fencing_epoch:2};
    const source={mode:'registry_current',task_id:'task:'+'6'.repeat(32),run_dir:'/display-only/run',net_ref:selected.net_ref,verified_head_ordinal:cut,writer_fencing_epoch:2};
    const tokens=['1','b'].map(n=>({token_ref:ref('petri_token/v1',n),resource_ref:structuredClone(pair),place:'p',active_in_checkpoint:true}));
    return {schema_version:'rpnh/checkpoint_view/v1',selector:selected,capture,
        navigation:{previous_net_segment:null,coverage:'complete',end_reason:'initial_checkpoint',loaded_checkpoints:2,max_chain:2048},
        adoption_evidence:{status:'no_evidence_at_cut',coverage:'complete',cut,current_net_ref:null,records:[]},coverage:{firings:'not_provided'},
        frame:{schema_version:'rpnh/dashboard/v1',source,net:{schema_version:'rpnh/net_view/v1',source:structuredClone(source),summary:{},
            marking:{checkpoint_ref:selected.checkpoint_ref,epoch:0,token_count:2,active_token_count:2},nodes:[
                {id:'p',label:'p',kind:'place',category:'place',tokens,active_token_count:2},
                {id:'q',label:'q',kind:'place',category:'place',tokens:[],active_token_count:0}],edges:[]},
            boundaries:{entry:[],exit:[],terminal_rules:[]},transition_bindings:[],agent_nodes:[],presentation:{nodes:{}},
            position:{mode:'history',cursor:cut,latest_head:130},coverage:{history:'selected_saved_checkpoint',firings:'not_provided'},change:{}}};
}
function withMetadata(value,token=value.frame.net.nodes[0].tokens[0]) {
    value=structuredClone(value);
    value.token_resource_metadata={schema_version:'rpnh/token_resource_metadata/v1',
        scope:{task_ref:ref('task/v1','6'),run_ref:ref('native_run_identity/v1','7','run'),...value.selector,capture:value.capture,token_ref:token.token_ref,resource_ref:token.resource_ref},
        occurrence:Object.fromEntries(['token_ref','resource_ref','place','active_in_checkpoint'].map(k=>[k,token[k]])),
        registered_metadata:{byte_size:6,media_type:'application/json',content_schema_ref:'<script>ordinary schema label</script>'},
        registration:{published_event_id:'event:'+'8'.repeat(32),publication_recorded_ordinal:40,publication_transaction_commit_ordinal:42},
        coverage:{status:'complete',scope:'one_checkpoint_token_resource'},verification:{metadata_scope:'canonical_at_selected_checkpoint',
            body_read_by_metadata_projection:false,content_validation:'not_performed',actual_verified_byte_size:null}};
    return value;
}
const buttons=h=>h.document.getElementById('detail').querySelectorAll('button').filter(b=>b.dataset.tokenResource);
const close=h=>h.document.getElementById('detail').querySelectorAll('button').find(b=>b.dataset.closeTokenResource).onclick();
async function setup(fetcher,value=envelope()) {
    const h=checkpointApp(fetcher);h.api.install(normalizeFrame(value.frame));await h.api.mode('petri');h.api.select('node','p');h.api.tab('executions');return h;
}

test('target and DTO reject partial refs, unsafe capture, extra fields, scope substitutions and false verification',()=>{
    const value=withMetadata(envelope()),frame=normalizeFrame(value.frame),token=frame.net.nodes[0].tokens[0],request=tokenResourceRequest(frame,token);
    assert.equal(normalizeTokenResourceMetadata(value,request,frame).registered_metadata.byte_size,6);
    for(const change of [v=>v.expected_capture.head_ordinal=2**53,v=>v.token_ref.extra=1,v=>delete v.resource_ref.resource_id,v=>v.expected_capture.extra=1,v=>v.expected_task_id='task:bad']) {
        const v=structuredClone(request.target);change(v);assert.throws(()=>tokenResourceTarget(v));
    }
    for(const change of [v=>v.token_resource_metadata.locator='PRIVATE',v=>v.token_resource_metadata.registered_metadata.summary='PRIVATE',
        v=>v.token_resource_metadata.scope.cut=130,v=>v.token_resource_metadata.scope.capture.writer_fencing_epoch=3,
        v=>v.token_resource_metadata.scope.run_ref.logical_id='task:'+'7'.repeat(32),v=>v.token_resource_metadata.occurrence.place='q',
        v=>v.token_resource_metadata.verification.actual_verified_byte_size=6,v=>v.token_resource_metadata.registration.publication_transaction_commit_ordinal=76,
        v=>v.token_resource_metadata.registered_metadata.byte_size=true,v=>v.token_resource_metadata.occurrence.token_ref=ref('petri_token/v1','f')]) {
        const v=structuredClone(value);change(v);assert.throws(()=>normalizeTokenResourceMetadata(v,request,frame));
    }
});
for(const language of ['en','zh-CN'])test(`actual app button displays one occurrence with text-only registered metadata in ${language}`,async()=>{
    setLanguage(language);const value=envelope(),calls=[];
    const h=await setup(async path=>{calls.push(path);return response(withMetadata(value));},value),initial=h.api.state().frame;
    assert.equal(buttons(h).length,2);await buttons(h)[0].onclick();
    assert.deepEqual(calls,[tokenResourcePath(tokenResourceRequest(initial,initial.net.nodes[0].tokens[0]))]);
    const text=h.document.getElementById('detail').textContent;
    assert.match(text,/C75.*H130\/E2/);assert.match(text,/40/);assert.match(text,/42/);assert.match(text,/<script>ordinary schema label<\/script>/);
    assert.equal(h.document.getElementById('detail').querySelectorAll('script').length,0);
    assert.equal(h.api.state().frame,initial);assert.equal(h.motions.length,0);assert.equal(h.api.activityState().observation,null);
    assert.equal(h.api.resourceState().metadata.occurrence.token_ref.logical_id,initial.net.nodes[0].tokens[0].token_ref.logical_id);
    close(h);assert.equal(h.api.resourceState().metadata,null);
});
test('missing, null, malformed pairs and missing capture do not create clickable fallback',async()=>{
    for(const change of [t=>delete t.resource_ref,t=>t.resource_ref=null,t=>t.resource_ref={resource_id:pair.resource_id}]) {
        const value=envelope();value.frame.net.nodes[0].tokens=value.frame.net.nodes[0].tokens.slice(0,1);change(value.frame.net.nodes[0].tokens[0]);
        const h=await setup(async()=>{throw Error('must not fetch');},value);assert.equal(buttons(h).length,0);
    }
    const value=envelope();delete value.frame.position.latest_head;const h=await setup(async()=>{throw Error('must not fetch');},value);assert.equal(buttons(h).length,0);
});
test('duplicate click sends once; close rejects delayed results',async()=>{
    const delayed=deferred();let calls=0;const value=envelope(),h=await setup(async()=>{calls++;return delayed.promise;},value);
    const click=buttons(h)[0].onclick,pending=click();await click();assert.equal(calls,1);close(h);
    delayed.resolve(response(withMetadata(value)));await pending;assert.equal(h.api.resourceState().metadata,null);
});
test('same-pair other token beats old response; place navigation and new checkpoint invalidate selection',async()=>{
    const late=deferred(),value=envelope();let calls=0;
    const h=await setup(async()=>++calls===1?late.promise:response(withMetadata(value,value.frame.net.nodes[0].tokens[1])),value);
    const pending=buttons(h)[0].onclick();await buttons(h)[1].onclick();late.resolve(response(withMetadata(value)));await pending;
    assert.equal(stable(h.api.resourceState().metadata.occurrence.token_ref),stable(value.frame.net.nodes[0].tokens[1].token_ref));
    h.api.select('node','q');assert.equal(h.api.resourceState().metadata,null);
    h.api.select('node','p');await buttons(h)[1].onclick();
    const old=h.api.state().frame;await h.api.render(old,undefined,normalizeFrame(envelope(130).frame));assert.equal(h.api.resourceState().metadata,null);
});
test('late metadata after returning live does not attach to the new frame',async()=>{
    const late=deferred(),value=envelope();const live=structuredClone(value.frame);live.position.mode='live';
    const h=await setup(async path=>path.includes('token_resource=')?late.promise:response(path.startsWith('/api/v1/history')?{
        schema_version:'rpnh/dashboard_history/v1',source:live.source,coverage:'current_net_canonical_checkpoints',items:[],next_before:null,end_reason:'initial_checkpoint'}:live),value);
    const pending=buttons(h)[0].onclick();await h.api.returnToLive();late.resolve(response(withMetadata(value)));await pending;
    assert.equal(h.api.resourceState().metadata,null);assert.equal(h.api.state().frame.position.mode,'live');
});
test('language change preserves exact pending selection and renders in the new language',async()=>{
    const delayed=deferred(),value=envelope(),h=await setup(async()=>delayed.promise,value);
    const pending=buttons(h)[0].onclick();setLanguage('zh-CN');h.api.refreshLanguage();delayed.resolve(response(withMetadata(value)));await pending;
    assert.match(h.document.getElementById('detail').textContent,/登记大小/);assert.equal(h.api.resourceState().metadata.scope.cut,75);
});
test('403 clears old details, 409 retains only the same scope; unsupported never falls back',async()=>{
    for(const status of [403,409,400,404,501]) {
        const value=envelope();let calls=0;const h=await setup(async()=>response(++calls===1?withMetadata(value):{error:'private'},calls===1?200:status),value);
        await buttons(h)[0].onclick();await buttons(h)[0].onclick();assert.equal(calls,2);
        assert.equal(h.api.resourceState().metadata===null,status===403);
        assert.equal(h.api.resourceState().error,status===403?'access_changed':status===409?'stale_observation':'unsupported');
    }
});
test('foreign source response clears old details without changing the canonical frame',async()=>{
    const value=envelope();let calls=0;const h=await setup(async()=>{const result=withMetadata(value);if(++calls===2)result.frame.source.run_dir='/changed';return response(result);},value);
    await buttons(h)[0].onclick();const initial=h.api.state().frame;await buttons(h)[0].onclick();
    assert.equal(h.api.resourceState().metadata,null);assert.equal(h.api.resourceState().error,'access_changed');assert.equal(h.api.state().frame,initial);
});
test('nullable schema is explicitly unavailable, never an absent resource',async()=>{
    const value=withMetadata(envelope());value.token_resource_metadata.registered_metadata.content_schema_ref=null;
    const h=await setup(async()=>response(value),value);await buttons(h)[0].onclick();
    assert.match(h.document.getElementById('detail').textContent,/Registered schema identifier not provided/);
    assert.equal(h.api.resourceState().metadata.registered_metadata.byte_size,6);assert.equal(buttons(h).length,2);
});

test('every exact reference scalar rejects coercible JSON values, including legacy typed identities',()=>{
    const kinds=['net_instance/v1','marking_checkpoint/v1','petri_token/v1','task/v1','native_run_identity/v1'];
    for(const kind of kinds)for(const key of ['logical_id','version_id']) {
        const good=ref(kind,'a',kind==='native_run_identity/v1'?'run':undefined);
        for(const bad of [[good[key]],null,1,true,{kind:'irrelevant',value:good[key]},new String(good[key])]) {
            const value={...good,[key]:bad};assert.throws(()=>checkpointReference(value,kind));
        }
    }
    const legacy={entity_type:'net_instance/v1',entity_id:{kind:'net_instance',value:'4'.repeat(32)},version_id:{kind:'net_instance_version',value:'4'.repeat(32)}};
    assert.deepEqual(checkpointReference(legacy,'net_instance/v1',true),ref('net_instance/v1','4'));
    for(const side of ['entity_id','version_id'])for(const key of ['kind','value']) {
        const value=structuredClone(legacy);value[side][key]=[value[side][key]];
        assert.throws(()=>checkpointReference(value,'net_instance/v1',true));
    }
});
test('resource pair, expected task and publication event IDs are strings, never regex coercions',()=>{
    const value=withMetadata(envelope()),frame=normalizeFrame(value.frame),original=tokenResourceRequest(frame,frame.net.nodes[0].tokens[0]);
    for(const key of ['resource_id','resource_version_id'])for(const wrap of [v=>[v],v=>new String(v),v=>({toString:()=>v})]) {
        const target=structuredClone(original.target);target.resource_ref[key]=wrap(target.resource_ref[key]);
        assert.throws(()=>tokenResourceTarget(target));
    }
    for(const wrap of [v=>[v],v=>new String(v),v=>({toString:()=>v})]) {
        const target=structuredClone(original.target);target.expected_task_id=wrap(target.expected_task_id);
        assert.throws(()=>tokenResourceTarget(target));
        const result=structuredClone(value);result.token_resource_metadata.registration.published_event_id=wrap(result.token_resource_metadata.registration.published_event_id);
        assert.throws(()=>normalizeTokenResourceMetadata(result,original,frame));
    }
});
test('array-valued pair, token and task identifiers never expose an actionable card',async()=>{
    for(const change of [v=>v.frame.net.nodes[0].tokens[0].resource_ref.resource_id=[pair.resource_id],
        v=>v.frame.net.nodes[0].tokens[0].resource_ref.resource_version_id=[pair.resource_version_id],
        v=>v.frame.net.nodes[0].tokens[0].token_ref.logical_id=[v.frame.net.nodes[0].tokens[0].token_ref.logical_id],
        v=>v.frame.net.nodes[0].tokens[0].token_ref.version_id=[v.frame.net.nodes[0].tokens[0].token_ref.version_id],
        v=>{v.frame.source.task_id=[v.frame.source.task_id];v.frame.net.source.task_id=v.frame.source.task_id;}]) {
        const value=envelope();value.frame.net.nodes[0].tokens=value.frame.net.nodes[0].tokens.slice(0,1);change(value);
        const h=await setup(async()=>{throw Error('Malformed target must not fetch');},value);
        assert.equal(buttons(h).length,0);
    }
});
test('same request anchors exact task/run across refresh and repeated access failures',async()=>{
    for(const change of [m=>m.scope.run_ref=ref('native_run_identity/v1','c','run'),
        m=>m.scope.run_ref.version_id='run_version:'+'c'.repeat(32),m=>m.scope.task_ref.version_id='task_version:'+'c'.repeat(32)]) {
        const value=envelope();let calls=0;
        const h=await setup(async()=>{const result=withMetadata(value);if(++calls===2 || calls===3)change(result.token_resource_metadata);return response(result);},value);
        await buttons(h)[0].onclick();const known=structuredClone(h.api.resourceState().identity),initial=h.api.state().frame;
        for(let i=0;i<2;i++) {
            await buttons(h)[0].onclick();assert.equal(h.api.resourceState().metadata,null);
            assert.equal(h.api.resourceState().error,'access_changed');assert.equal(stable(h.api.resourceState().identity),stable(known));
            assert.equal(h.api.state().frame,initial);
        }
        await buttons(h)[0].onclick();assert.ok(h.api.resourceState().metadata);
        assert.equal(stable(h.api.resourceState().identity),stable(known));
    }
});
test('stale retained card keeps its identity anchor; closing and changing frame clears it',async()=>{
    const value=envelope();let calls=0;
    const h=await setup(async()=>++calls===2?response({error:'stale_observation'},409):response(withMetadata(value)),value);
    await buttons(h)[0].onclick();const known=structuredClone(h.api.resourceState().identity);
    await buttons(h)[0].onclick();assert.equal(h.api.resourceState().error,'stale_observation');assert.equal(stable(h.api.resourceState().identity),stable(known));
    close(h);assert.equal(h.api.resourceState().identity,null);
    await buttons(h)[0].onclick();assert.equal(stable(h.api.resourceState().identity),stable(known));
    const old=h.api.state().frame;await h.api.render(old,undefined,normalizeFrame(envelope(130).frame));
    assert.equal(h.api.resourceState().identity,null);assert.equal(h.api.resourceState().metadata,null);
});
test('explicitly changing occurrence permits a fresh scope anchor without carrying an old run',async()=>{
    const value=envelope();let calls=0;
    const h=await setup(async()=>{const result=withMetadata(value,value.frame.net.nodes[0].tokens[calls?1:0]);
        if(++calls>1)result.token_resource_metadata.scope.run_ref=ref('native_run_identity/v1','c','run');return response(result);},value);
    await buttons(h)[0].onclick();const old=h.api.resourceState().identity.run_ref;
    await buttons(h)[1].onclick();assert.equal(h.api.resourceState().error,null);
    assert.notDeepEqual(h.api.resourceState().identity.run_ref,old);
});
