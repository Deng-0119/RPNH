import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {checkpointApp,response} from './checkpoint-test-harness.mjs';

const fixture=JSON.parse(readFileSync(new URL('./comparison-context-fixture.json',import.meta.url),'utf8'));
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
function source(id,projection=[]){return {
 source_id:id,source_ref:{source_id:id},cut:null,access_revision:'revision-1',
 access_state:'readable',access_path:'observer',
 coverage:{state:'not_queried',loaded_count:null,total_count:null},
 capabilities:{index:['resource_version/v1'],index_fields:{'resource_version/v1':['resource_ref']},
 record:['resource_version/v1'],record_fields:{'resource_version/v1':['resource_ref']},
 projection,material:false,export:false,execution:'not_checked'},
};}
function page(){return {schema_version:'rpnh/comparison_selection/v1',session_id:fixture.request.session_id,
 targets:[{target:fixture.request.left,label:'Exact target',read_state:'not_read'}],
 sources:[source(fixture.request.left.source_id,['net_instance/v1']),source('resource-only'),{
 source_id:'unavailable-source',source_ref:null,cut:null,access_revision:null,access_state:'NOT_DISCLOSED',
 coverage:{state:'unavailable',loaded_count:null,total_count:null}}],
 source_cuts:fixture.context.request_echo.source_cuts,next_cursor:'more',limits:fixture.request.limits};}

test('resource-only source remains selectable with unknown counts and separate capabilities',async()=>{
 let requests=0;const app=checkpointApp(async()=>{requests++;return response(page());});
 app.api.crossComparison.open();await tick();const panel=app.document.getElementById('comparison-context-panel');
 assert.equal(app.api.crossComparison.state.sources.length,3);
 const select=panel.querySelectorAll('select').find(el=>el.getAttribute('aria-label')==='Left source');
 assert.ok(select.children.some(el=>el.value==='resource-only'));
 select.value='resource-only';select.onchange();
 const disclosure=panel.querySelector('[data-source-description="left"]');assert.ok(disclosure);
 const value=JSON.parse(disclosure.querySelector('pre').textContent);
 assert.deepEqual(value.coverage,{state:'not_queried',loaded_count:null,total_count:null});
 assert.deepEqual(value.capabilities.projection,[]);assert.equal(value.capabilities.execution,'not_checked');
 assert.equal(requests,1,'source metadata interaction performs no object query');
 app.api.crossComparison.close();assert.deepEqual(app.api.crossComparison.state.sources,[]);
});

test('access change clears disclosed source authority and old selectors',async()=>{
 let requests=0;const app=checkpointApp(async()=>++requests===1?response(page()):response({error:'access_changed'},403));
 app.api.crossComparison.open();await tick();const panel=app.document.getElementById('comparison-context-panel');
 const sourceSelect=panel.querySelectorAll('select').find(el=>el.getAttribute('aria-label')==='Left source');
 sourceSelect.value=fixture.request.left.source_id;sourceSelect.onchange();
 panel.querySelectorAll('button').find(el=>el.textContent==='More exact objects').onclick();await tick();
 const state=app.api.crossComparison.state;assert.equal(state.error,'access_changed');
 assert.deepEqual(state.sources,[]);assert.deepEqual(state.options,[]);assert.equal(state.session,null);
 assert.deepEqual(state.selection,{left:null,right:null});
 assert.equal(state.source_left,null);assert.equal(state.source_right,null);
 assert.equal(panel.querySelector('[data-source-description="left"]'),null);
 app.api.crossComparison.close();
});
