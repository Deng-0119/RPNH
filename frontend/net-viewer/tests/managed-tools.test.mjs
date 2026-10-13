import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {managedRequest, managedPath, normalizeManaged, createManagedTools, renderManagedTools} from '../../../cpn/frontend/static/panels.mjs';
import {setLanguage} from '../../../cpn/frontend/static/i18n.mjs';
import {checkpointApp, response, deferred} from './checkpoint-test-harness.mjs';
import {fixtureFrame} from './firing-activity-fixture.mjs';

const frame = JSON.parse(readFileSync(new URL('./comparison-fixture.json', import.meta.url))).right.frame;
const ref = (kind, id) => ({entity_type:kind,logical_id:`resource:${id.repeat(32)}`,version_id:`resource_version:${id.repeat(32)}`});
const parent = ref('agent_tool_program_invocation/v1', '1'), first = ref('agent_tool_program_call/v1', '2'), second = ref('agent_tool_program_call/v1', '3');
const start = {resource_id:'resource:'+'4'.repeat(32),resource_version_id:'resource_version:'+'4'.repeat(32)};
const terminal = {...start,resource_version_id:'resource_version:'+'5'.repeat(32)};
const firing = {status:'settled',firing_ref:ref('transition_firing/v1', '6')};
const params = managedRequest(frame, firing);
const link = (label, ref) => ({label,ref});
function payload() {
    const {firing_ref,...selector} = params;
    return {schema_version:'rpnh/managed_tools/v1',read_only:true,coverage:'canonical_programs_at_checkpoint',
        task_id:frame.source.task_id, selector, firing_ref, capture:{head_ordinal:params.cut,writer_fencing_epoch:1}, program_refs:[parent],
        records:[{ref:parent,kind:parent.entity_type,value:{status:'returned'},links:[link('price-first',first),link('price-second',second)]},
            {ref:first,kind:first.entity_type,value:{logical_key:'price-first',status:'returned',output:{price:10}},links:[link('parent',parent),link('Start',start),link('terminal',terminal)]},
            {ref:second,kind:second.entity_type,value:{logical_key:'price-second',status:'returned',output:{price:10}},links:[link('parent',parent)]},
            {ref:start,kind:'managed_plugin_call',value:{state:'started'},links:[]},
            {ref:terminal,kind:'managed_plugin_call',value:{state:'returned',output:{price:10}},links:[link('Start',start)]}]};
}
class Node {
    constructor(tag) {this.tagName=tag;this.children=[];this.dataset={};this.text='';}
    set textContent(value) {this.text=String(value);this.children=[];}
    get textContent() {return this.text+this.children.map(n=>n.textContent).join('');}
    append(...nodes) {this.children.push(...nodes);}
}

for (const status of [200,503]) for (const autoRefresh of [true,false])
test(`managed navigation resets pending activity status=${status} polling=${autoRefresh}`, async()=>{
    const current=fixtureFrame();current.position.mode='live';current.coverage.firings='current_observations';
    current.net.nodes[0].runtime={firings:[{...firing,attempt_index:1}]};
    const request=managedRequest(current,firing),value=payload(),late=deferred();let activitySignal;
    const {firing_ref,...selector}=request;
    Object.assign(value,{selector,firing_ref,task_id:current.source.task_id});value.capture.head_ordinal=selector.cut;
    const h=checkpointApp(async(path,options)=>{
        if(path.startsWith('/api/v2/firing-activity')){activitySignal=options.signal;return late.promise;}
        assert.equal(path,managedPath(request));return response(value);
    });
    h.api.install(current);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
    const detail=()=>h.document.getElementById('detail'),find=(key,val)=>walk(detail()).find(n=>n.dataset[key]===val);
    const checkbox=h.document.getElementById('auto-refresh');checkbox.checked=autoRefresh;h.api.schedule();
    const pending=find('activityAction','load').onclick();
    assert.equal(h.api.activityState().loading,true);
    await find('managedOpen',firing.firing_ref.version_id).onclick();
    late.resolve(response({error:'late response intentionally cancelled'},status));await pending;
    assert.equal(activitySignal.aborted,true);
    assert.equal(h.api.activityState().loading,false);assert.equal(h.api.activityState().paused,false);
    walk(detail()).find(n=>n.tagName==='button'&&n.textContent==='Close managed program evidence').onclick();
    assert.equal(checkbox.disabled,false);assert.equal(h.timers.size,autoRefresh?1:0);
    assert.equal(h.api.state().selection.id,'step.run');assert.equal(h.api.state().frame,current);
});
const walk = n => [n,...n.children.flatMap(walk)];
const doc = globalThis.document;
afterEach(()=>{globalThis.document=doc;setLanguage('en');});

test('exact parent / child / Start / terminal links render in the original detail control', async()=>{
    globalThis.document={createElement:tag=>new Node(tag)};
    let box;
    const control=createManagedTools({request:async path=>{assert.equal(path,managedPath(params));return payload();},getFrame:()=>frame,
        onOpen(){},onClose(){},onChange(){box=new Node('section');renderManagedTools(box,frame,firing,control);}});
    await control.load(firing);
    const click=label=>{const b=walk(box).find(n=>n.dataset.managedLabel===label);assert.ok(b,label);b.onclick();};
    click('parent'); click('price-first'); click('Start');
    assert.ok(walk(box).some(n=>n.dataset.managedDetail==='managed_plugin_call'&&n.textContent.includes('started')));
    click('parent');click('price-first');click('terminal');
    assert.ok(walk(box).some(n=>n.dataset.managedOutput==='output'&&n.textContent.includes('10')));
    click('parent');click('price-second');
    assert.ok(walk(box).some(n=>n.dataset.managedExactRef&&n.textContent.includes(second.version_id)));
    assert.deepEqual(frame,JSON.parse(readFileSync(new URL('./comparison-fixture.json', import.meta.url))).right.frame);
});

test('wrong firing, missing target and duplicate exact records are rejected',()=>{
    let value=payload();value.firing_ref=first;
    assert.throws(()=>normalizeManaged(value,params,frame));
    value=payload();value.records.pop();assert.throws(()=>normalizeManaged(value,params,frame));
    value=payload();value.records.push(value.records[0]);assert.throws(()=>normalizeManaged(value,params,frame));
    assert.throws(()=>managedRequest(frame,{...firing,status:'started'}));
});

test('closing rejects late responses and a failed refresh clears prior output', async()=>{
    let resolve, calls=0;
    const control=createManagedTools({request:()=>++calls===1?new Promise(r=>resolve=r):Promise.reject({status:409}),getFrame:()=>frame,onOpen(){},onClose(){},onChange(){}});
    const pending=control.load(firing);control.close();resolve(payload());await pending;
    assert.deepEqual(control.state,{open:false});
    await control.load(firing);
    assert.equal(control.state.error,'stale_observation');assert.equal(control.state.value,null);
});

test('receipt output stays literal text and English/Chinese labels are available',()=>{
    globalThis.document={createElement:tag=>new Node(tag)};
    for (const language of ['en','zh-CN']) {
        setLanguage(language);
        const value=payload();value.records[1].value.output='<img src=x onerror=bad()>';
        const control={state:{open:true,params,value,selected:first},load(){},close(){},select(){}};
        const box=new Node('section');renderManagedTools(box,frame,firing,control);
        assert.ok(box.textContent.includes('<img src=x onerror=bad()>'));
        assert.ok(!walk(box).some(n=>n.tagName==='img'));
        assert.ok(box.textContent.includes(language==='en'?'View managed program evidence':'查看托管程序证据'));
    }
});

test('actual app retains the open firing while clicking details and clears them on member navigation', async()=>{
    const current=fixtureFrame();current.position.mode='live';current.coverage.firings='current_observations';
    current.net.nodes[0].runtime={firings:[{...firing,attempt_index:1}]};
    const request=managedRequest(current,firing), value=payload();
    const {firing_ref,...selector}=request;
    Object.assign(value,{selector,firing_ref,task_id:current.source.task_id});
    value.capture.head_ordinal=selector.cut;
    let late=null;
    const h=checkpointApp(async path=>{assert.equal(path,managedPath(request));return late?late.promise:response(value);});
    h.api.install(current);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
    const detail=()=>h.document.getElementById('detail');
    const find=(key,val)=>walk(detail()).find(n=>n.dataset[key]===val);
    h.document.getElementById('auto-refresh').checked=true;h.api.schedule();
    await find('managedOpen',firing.firing_ref.version_id).onclick();
    assert.equal(h.timers.size,0);
    assert.equal(walk(detail()).find(n=>n.dataset.firingId).open,true);
    find('managedLabel','parent').onclick();find('managedLabel','price-first').onclick();
    assert.ok(find('managedDetail',first.entity_type).textContent.includes('price-first'));
    late=deferred();const pending=find('managedOpen',firing.firing_ref.version_id).onclick();
    h.api.select('node','other');late.resolve(response(value));await pending;
    assert.ok(!walk(detail()).some(n=>n.dataset.managedValue));
    assert.ok(h.timers.size>0, 'member navigation restores the original polling choice');
    assert.equal(h.api.state().frame,current);
});


test('opening managed details invalidates an already pending dashboard response', async()=>{
    const current=fixtureFrame();current.position.mode='live';current.coverage.firings='current_observations';
    current.net.nodes[0].runtime={firings:[{...firing,attempt_index:1}]};
    const params=managedRequest(current,firing), value=payload();
    const {firing_ref,...selector}=params;
    Object.assign(value,{selector,firing_ref,task_id:current.source.task_id});value.capture.head_ordinal=selector.cut;
    const delayed=deferred();let dashboardSignal;
    const h=checkpointApp(async(path,options)=>{
        if(path==='/api/v1/dashboard'){dashboardSignal=options.signal;return delayed.promise;}
        assert.equal(path,managedPath(params));return response(value);
    });
    h.api.install(current);await h.api.mode('petri');h.api.select('node','step.run');h.api.tab('executions');
    const pending=h.api.loadFrame(null);
    const find=(key,val)=>walk(h.document.getElementById('detail')).find(n=>n.dataset[key]===val);
    await find('managedOpen',firing.firing_ref.version_id).onclick();
    assert.equal(dashboardSignal.aborted,true);
    find('managedLabel','parent').onclick();find('managedLabel','price-first').onclick();
    delayed.resolve(response(structuredClone(current)));await pending;
    assert.equal(h.api.state().frame,current);
    assert.ok(find('managedDetail',first.entity_type).textContent.includes('price-first'));
});
