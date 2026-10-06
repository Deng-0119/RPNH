import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { normalizeSourceObservation, SourceObservationState, sourceObservationPath, renderSourceObservation } from '../../../cpn/frontend/static/source-observation.mjs';
import { EN } from '../../../cpn/frontend/static/messages.mjs';

const ref=(kind,digit)=>({schema_version:'rpnh/collaboration/source_version_ref/v1',source_id:'R1',ref:{entity_type:kind,logical_id:'resource:'+digit.repeat(32),version_id:'resource_version:'+digit.repeat(32)}});
function fixture(){
    const observation=ref('collaboration_source_observation/v1','1');
    return {schema_version:'rpnh/source_observation/v1',observation_ref:observation,source_set_ref:ref('collaboration_source_set/v1','2'),manifest_version:1,
        available_manifest:ref('collaboration_source_set/v1','3'),available_observations:[observation,ref('collaboration_source_observation/v1','4')],kind:'query',previous_observation_ref:null,supplement_of:null,dependencies:[],
        query_scope:{limit:1,media_types:[]},publication:{state:'PUBLISHED',visible_ordinal:30},sources:[{source_id:'R1',access_path:'main',cut:{ordinal:10},headers:[{ref:{schema_version:'rpnh/collaboration/source_resource_ref/v1',source_id:'R1',ref:{resource_id:'resource:'+'5'.repeat(32),resource_version_id:'resource_version:'+'5'.repeat(32)}},display_summary:'<img src=x onerror=boom()>',media_type:'text/plain',byte_size:12}],coverage:{state:'partial',loaded_count:1,total_count:null},capture_coverage:{state:'partial',loaded_count:1,total_count:null},current_access:'readable'},
            {source_id:'R3',access_path:'main',cut:null,headers:[],coverage:{state:'unavailable',loaded_count:null,total_count:null},capture_coverage:{state:'unavailable',loaded_count:null,total_count:null},current_access:'unavailable'}],
        coverage:{state:'partial',loaded_count:1,total_count:null,expected_source_count:2,missing_sources:['R1','R3']},global_atomic_snapshot:false,causal_completeness:'not_established'};
}
class Node{
    constructor(tag,document){this.tagName=tag;this.ownerDocument=document;this.children=[];this.dataset={};this.hidden=false;this.text='';}
    set textContent(value){this.text=String(value);this.children=[];}
    get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
    set innerHTML(_){throw new Error('unsafe HTML');}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;this.text='';}
}
function host(){const document={createElement:tag=>new Node(tag,document)};return document.createElement('section');}
const walk=n=>[n,...n.children.flatMap(walk)];
const translate=key=>{assert.ok(Object.hasOwn(EN,key),'missing translation: '+key);return EN[key];};

test('SourceSet panel consumes the saved source scope, unknown totals and independent cuts safely',()=>{
    const state=new SourceObservationState(),request=state.begin();state.accept(request,fixture());
    const root=host(),selected=[];
    renderSourceObservation(root,state,{translate,onSelect:r=>selected.push(r)});
    assert.match(root.textContent,/S@1/);assert.match(root.textContent,/Total: Unknown/);assert.match(root.textContent,/R3.*unavailable/);
    assert.match(root.textContent,/newer source manifest/);assert.match(root.textContent,/neither a global atomic snapshot nor causal completeness/);
    assert.match(root.textContent,/<img src=x onerror=boom\(\)>/);
    const next=walk(root).find(n=>n.tagName==='button'&&n.textContent==='Observation 2');next.onclick();assert.deepEqual(selected,[fixture().available_observations[1]]);
    assert.match(sourceObservationPath(selected[0]),/^\/api\/v2\/source-observation\?observation_ref=/);
});

test('SourceSet responses cannot mix observations, duplicate sources or report missing sources as zero',()=>{
    const raw=fixture();assert.deepEqual(normalizeSourceObservation(raw),raw);
    for(const mutate of [r=>r.global_atomic_snapshot=true,r=>r.sources.push(structuredClone(r.sources[0])),r=>r.sources[1].coverage.total_count=0,r=>r.coverage.total_count=1,r=>r.sources[0].authority='secret',r=>{r.sources[1].coverage.state='complete';r.coverage.state='complete';r.coverage.total_count=0},r=>r.query_scope=null]){
        const value=fixture();mutate(value);assert.throws(()=>normalizeSourceObservation(value));
    }
    assert.throws(()=>normalizeSourceObservation(raw,ref('collaboration_source_observation/v1','7')));
});

test('new selection, repeated requests and Close reject delayed responses and clear revoked content',()=>{
    const state=new SourceObservationState();const first=state.begin(),second=state.begin();
    assert.equal(state.accept(first,fixture()),false);assert.equal(state.accept(second,fixture()),true);
    const third=state.begin();assert.equal(state.value,null);state.fail(third,403);assert.equal(state.value,null);assert.equal(state.error,'access_changed');
    const fourth=state.begin();state.close();assert.equal(state.accept(fourth,fixture()),false);
    const root=host();renderSourceObservation(root,state,{translate});assert.equal(root.hidden,true);assert.equal(root.textContent,'');
});

test('supplement is displayed independently and the application uses the real GET/panel wiring',()=>{
    const raw=fixture();raw.kind='supplement';raw.query_scope=null;raw.supplement_of=raw.available_observations[1];
    const state=new SourceObservationState();state.accept(state.begin(),raw);const root=host();renderSourceObservation(root,state,{translate});
    assert.match(root.textContent,/Separate supplemental read/);
    const app=readFileSync(new URL('../../../cpn/frontend/static/app.js',import.meta.url),'utf8');
    assert.match(app,/request\(sourceObservationPath\(reference\)/);assert.match(app,/sourceObservation\.accept\(selected,raw\)/);assert.match(app,/renderSourceObservation\(\$\('source-observation-panel'\)/);
    const html=readFileSync(new URL('../../../cpn/frontend/static/index.html',import.meta.url),'utf8');assert.match(html,/id="source-observations"/);assert.match(html,/id="source-observation-panel"/);
});

test('actual Python N01 GET response reaches the existing Viewer model and panel', {skip:!process.env.RPNH_SOURCE_QUERY_FIXTURE},()=>{
    const raw=JSON.parse(readFileSync(process.env.RPNH_SOURCE_QUERY_FIXTURE,'utf8'));
    const state=new SourceObservationState();state.accept(state.begin(raw.observation_ref),raw);
    const root=host();renderSourceObservation(root,state,{translate});
    assert.deepEqual(state.value.sources.map(r=>r.source_id),['R1','R2','R3']);
    assert.equal(state.value.sources[2].current_access,'not_checked');assert.equal(state.value.sources[2].coverage.total_count,null);
    assert.match(root.textContent,/R1/);assert.match(root.textContent,/R3/);assert.match(root.textContent,/S@1/);
    assert.ok(state.value.sources.slice(0,2).every(r=>r.current_access==='readable'&&r.headers.length===1));
    assert.equal(state.value.coverage.loaded_count,2);
    assert.deepEqual(state.value.sources.slice(0,2).map(r=>r.headers),raw.sources.slice(0,2).map(r=>r.headers));
    assert.ok(state.value.available_manifest);assert.equal(state.value.global_atomic_snapshot,false);
});
