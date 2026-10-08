// Real NetRenderer + ELK with controlled DOM/JointJS doubles; not browser paint QA.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ELK from 'elkjs/lib/elk.bundled.js';
import {checkpointApp,response} from './checkpoint-test-harness.mjs';
import {rendererRuntime} from './viewer-lifecycle-harness.mjs';
import {NetRenderer} from '../../../cpn/frontend/static/renderer.mjs';
import {nodeKey,edgeKey} from '../../../cpn/frontend/static/model.mjs';
const fixture=JSON.parse(readFileSync(new URL('./comparison-context-fixture.json',import.meta.url),'utf8'));
const tick=()=>new Promise(resolve=>setTimeout(resolve,5));
function recount(axis){
    axis.counts.loaded_count=axis.rows.length;axis.counts.total_count=axis.rows.length;
    for(const classification of ['known_changed','known_same','unknown'])axis.counts[classification]=axis.rows.filter(row=>row.classification===classification).length;
}
function synthetic(){
    const value=structuredClone(fixture.context),a=value.axes.configuration;
    a.coverage='complete_in_declared_scope';a.counts.coverage=a.coverage;
    a.rows=Array.from({length:105},(_,i)=>{
        const classification=['known_changed','known_same','unknown'][i%3];
        return {...structuredClone(value.axes.definition.rows[0]),row_id:'field-'+i,subject_relation_id:null,
            field_path:'configuration.step.request.copy.occurrence.'+i,classification,
            left_fact:{state:'provided',value:'left-'+i,evidence_refs:[]},
            right_fact:classification==='unknown'?{state:'unknown',reason_code:'mapping_missing'}:{state:'provided',value:(classification==='known_same'?'left-':'right-')+i,evidence_refs:[]},
            reason_code:classification==='unknown'?'mapping_missing':'exact_identity'};
    });
    recount(a);
    const nodes=value.mapping.relations.filter(r=>r.left[0].subject_kind==='node');
    for(const [kind,l,r] of [['split',[nodes[0].left[0]],nodes.slice(1).map(n=>n.right[0])],['fusion',nodes.slice(1).map(n=>n.left[0]),[nodes[0].right[0]]]]){
        const relation={...structuredClone(nodes[0]),relation_id:kind,relation_kind:kind,semantic_claim:'author_correspondence',reason_code:'verified_author_mapping',author_direction:'left_to_right',left:l,right:r};
        relation.evidence[0].selected_endpoints=structuredClone([...l,...r]);value.mapping.relations.push(relation);
        value.axes.definition.rows.push({...structuredClone(a.rows[2]),row_id:kind+'-field',subject_relation_id:kind,field_path:'definition.group',classification:'unknown'});
    }
    recount(value.axes.definition);
    a.rows[100].subject_relation_id='split';
    return value;
}
async function mount(t,{pendingLayout=false}={}){
    let deny=false;const requests=[],instances=[],value=synthetic(),lifecycle={apply:0,fit:0,dispose:0,layout:0};
    const app=checkpointApp(async url=>{
        requests.push(url);
        if(deny)return response({error:'access_changed'},403);
        if(url.startsWith('/api/v2/comparison-selection'))return response({schema_version:'rpnh/comparison_selection/v1',session_id:fixture.request.session_id,targets:[{target:fixture.request.left,label:'Exact author',read_state:'not_read'}],source_cuts:fixture.context.request_echo.source_cuts,next_cursor:'more',limits:fixture.request.limits});
        const req=JSON.parse(new URL(url,'http://local.invalid').searchParams.get('request'));
        const context=structuredClone(value);context.client_request_id=req.client_request_id;return response(context);
    });
    const runtime=rendererRuntime(app.document,app.window),oldObserver=globalThis.ResizeObserver;
    globalThis.ResizeObserver=runtime.ResizeObserver;
    for(const name of ['apply','fit','dispose']){
        const original=NetRenderer.prototype[name];
        t.mock.method(NetRenderer.prototype,name,function(...args){lifecycle[name]++;if(name==='apply')instances.push(this);return original.apply(this,args);});
    }
    const Shape=app.window.joint.dia.Element.define();
    app.window.joint.dia.Element.define=()=>class extends Shape{
        getBBox(){const p=this.get('position'),s=this.get('size');return {center:()=>({x:p.x+s.width/2,y:p.y+s.height/2})};}
    };
    let release;const gate=new Promise(resolve=>{release=resolve;});
    const layoutWork=[],elk=new ELK(),layout=elk.layout.bind(elk);elk.layout=graph=>{lifecycle.layout++;const work=(async()=>{if(pendingLayout)await gate;return layout(graph);})();layoutWork.push(work);return work;};
    const panel=app.document.getElementById('comparison-context-panel'),api=app.api.crossComparison;
    const button=label=>panel.querySelectorAll('button').find(b=>b.textContent===label);
    api.setRendering({joint:app.window.joint,elk});api.open();await tick();
    api.state.selection={left:structuredClone(fixture.request.left),right:structuredClone(fixture.request.right)};
    api.refreshLanguage();button('Read comparison').onclick();
    for(let i=0;i<200&&(pendingLayout?lifecycle.layout!==2:instances.length!==2)&&!api.state.error;i++)await tick();
    assert.equal(api.state.error,null);assert.equal(instances.length,pendingLayout?0:2);assert.equal(lifecycle.layout,2);
    t.after(()=>{api.close();globalThis.ResizeObserver=oldObserver;});
    return {...app,panel,api,button,value,requests,instances,lifecycle,deny:()=>{deny=true;},async releaseLayout(){release();await Promise.all(layoutWork);await tick();}};
}
function snapshots(app){return app.instances.map(r=>({viewport:r.viewport(),attrs:[...r.graph.cells].map(([id,cell])=>[id,structuredClone(cell.attributes.attrs)])}));}
function assertVisibleEndpoints(app,relation){
    for(const [index,side] of ['left','right'].entries()){
        const renderer=app.instances[index],view=renderer.viewport(),points=[];
        for(const ep of relation[side]){
            const node=ep.subject_kind==='node',cell=renderer.graph.getCell(node?nodeKey(ep.subject_id):edgeKey(ep.subject_id));
            assert.equal(cell.attributes.attrs[node?'body/strokeWidth':'line/strokeWidth'],3.5);
            const p=(node?renderer.layout.nodes:renderer.layout.edges).get(ep.subject_id);
            if(node)points.push({x:p.x,y:p.y},{x:p.x+p.width,y:p.y+p.height});
            else for(const section of p.sections)points.push(section.startPoint,...(section.bendPoints??[]),section.endPoint);
        }
        for(const p of points){assert.ok(p.x*view.scale+view.tx>=0&&p.x*view.scale+view.tx<=renderer.element.clientWidth);assert.ok(p.y*view.scale+view.ty>=0&&p.y*view.scale+view.ty<=renderer.element.clientHeight);}
        const details=JSON.parse(app.panel.querySelector(`[data-comparison-details="${side}"]`).textContent);
        assert.deepEqual(details.map(item=>item.endpoint),relation[side]);
    }
}
for(const kind of ['single','split','fusion','edge'])test('pending first layout replays '+kind+' Locate selection, details, ARIA, scope and framing',async t=>{
    const app=await mount(t,{pendingLayout:true}),{panel,lifecycle}=app;
    const relation=app.value.mapping.relations.find(r=>kind==='single'?r.relation_kind==='same_exact_subject'&&r.left[0].subject_kind==='node':kind==='edge'?r.left[0].subject_kind==='edge':r.relation_kind===kind);
    panel.querySelector(`[data-field-relation="${relation.relation_id}"]`).onclick();
    const details=['left','right'].map(side=>panel.querySelector(`[data-comparison-details="${side}"]`).textContent);
    const mapping=panel.querySelector('[data-mapping-details]').textContent,calls=app.requests.length;
    await app.releaseLayout();
    assert.equal(app.api.state.error,null);assert.equal(app.instances.length,2);assertVisibleEndpoints(app,relation);
    for(const [i,side] of ['left','right'].entries()){
        assert.equal(panel.querySelector(`[data-comparison-details="${side}"]`).textContent,details[i]);
        assert.equal(panel.querySelector(`[data-node-scope="${side}"]`).disabled,relation[side].length!==1||relation[side][0].subject_kind!=='node');
        for(const ep of relation[side])if(ep.subject_kind==='node')assert.equal(app.instances[i].graph.getCell(nodeKey(ep.subject_id)).attributes.attrs['root/aria-pressed'],'true');
    }
    assert.equal(panel.querySelector('[data-mapping-details]').textContent,mapping);
    const replayed=snapshots(app);
    panel.querySelector(`[data-field-relation="${relation.relation_id}"]`).onclick();
    assert.deepEqual(snapshots(app),replayed,'pending Locate must frame exactly like Locate after layout');
    assert.equal(app.requests.length,calls);assert.deepEqual(lifecycle,{apply:2,fit:2,dispose:0,layout:2});
});
test('pending layout replays latest per-side endpoint navigation rather than the earlier relation',async t=>{
    const app=await mount(t,{pendingLayout:true}),{panel}=app;
    panel.querySelector('[data-field-relation="split"]').onclick();
    panel.querySelector('[data-field-relation="fusion"]').onclick();
    const relation=app.value.mapping.relations.find(r=>r.relation_id==='fusion'),ep=relation.left.at(-1);
    panel.querySelectorAll('[data-relation-endpoint]').filter(b=>b.dataset.relationEndpoint==='left').at(-1).onclick();
    const detail=panel.querySelector('[data-comparison-details="left"]').textContent;
    await app.releaseLayout();
    const renderer=app.instances[0];
    for(const node of app.value.left.graph.nodes)assert.equal(renderer.graph.getCell(nodeKey(node.id)).attributes.attrs['root/aria-pressed'],node.id===ep.subject_id?'true':'false');
    assert.equal(panel.querySelector('[data-comparison-details="left"]').textContent,detail);
    assert.equal(panel.querySelector('[data-node-scope="left"]').disabled,false);
    const position=renderer.layout.nodes.get(ep.subject_id),view=renderer.viewport();
    assert.ok(Math.abs((position.x+position.width/2)*view.scale+view.tx-renderer.element.clientWidth/2)<1e-6);
    assert.ok(Math.abs((position.y+position.height/2)*view.scale+view.ty-renderer.element.clientHeight/2)<1e-6);
    const right=relation.right[0];assert.equal(app.instances[1].graph.getCell(nodeKey(right.subject_id)).attributes.attrs['root/aria-pressed'],'true');
});
for(const action of ['axes','close','replacement'])test(action+' invalidates pending Locate before layout can replay it',async t=>{
    const app=await mount(t,{pendingLayout:true}),{panel,api,lifecycle}=app;
    panel.querySelector('[data-field-relation="split"]').onclick();
    if(action==='axes'){const axis=panel.querySelector('input');axis.checked=false;axis.onchange();}
    else if(action==='close')api.close();
    else{
        app.button('Read comparison').onclick();
        for(let i=0;i<200&&lifecycle.layout!==4&&!api.state.error;i++)await tick();
        assert.equal(lifecycle.layout,4);
    }
    await app.releaseLayout();
    assert.equal(api.state.error,null);
    assert.equal(app.instances.length,action==='replacement'?2:0);
    assert.equal(panel.querySelectorAll('[data-relation-endpoint]').length,0);
    if(action==='replacement')for(const [index,side] of ['left','right'].entries()){
        assert.equal(panel.querySelector(`[data-comparison-details="${side}"]`).textContent,'');
        assert.equal(panel.querySelector(`[data-node-scope="${side}"]`).disabled,true);
        for(const node of app.value[side].graph.nodes)assert.equal(app.instances[index].graph.getCell(nodeKey(node.id)).attributes.attrs['root/aria-pressed'],'false');
    }
});
test('105 fields: row 101, expansion and all classification filters retain both views, selections, scope counts and renderer identity',async t=>{
    const app=await mount(t),{panel,instances,lifecycle,api}=app;
    instances[0].onSelect('node','step.request');instances[1].onSelect('node','step.result');
    instances.forEach((r,i)=>r.restore({scale:1.5+i,tx:71+i,ty:-23-i}));
    const before=snapshots(app),calls=app.requests.length,originalValue=structuredClone(api.state.value),counts=panel.querySelector('[data-scope-counts="configuration"]'),countText=counts.textContent;
    const papers=panel.querySelectorAll('[data-comparison-paper]');
    const section=panel.querySelector('[data-comparison-axis="configuration"]');section.open=true;
    const row=panel.querySelector('[data-field-row="field-0"]');row.open=true;
    const filter=panel.querySelector('[data-field-filter="configuration"]');
    const more=panel.querySelector('[data-more-fields="configuration"]');let focusCalls=0;filter.focus=()=>focusCalls++;
    assert.equal(panel.querySelectorAll('[data-field-rows="configuration"]')[0].children.length,100);
    more.onclick();assert.ok(panel.querySelector('[data-field-row="field-100"]'));assert.equal(focusCalls,1);
    panel.querySelector('[data-field-row="field-100"]').open=true;
    for(let repeat=0;repeat<3;repeat++)for(const classification of ['known_changed','known_same','unknown','all']){
        filter.value=classification;filter.onchange();
        const rows=panel.querySelector('[data-field-rows="configuration"]').children;
        assert.equal(rows.length,classification==='all'?100:35);
        if(classification!=='all')for(const item of rows)assert.ok(item.textContent.startsWith(classification));
        assert.deepEqual(snapshots(app),before);assert.deepEqual(panel.querySelectorAll('[data-comparison-paper]'),papers);
        assert.equal(section.open,true);assert.equal(counts.textContent,countText);assert.equal(panel.querySelector('[data-scope-counts="configuration"]'),counts);
    }
    assert.equal(panel.querySelector('[data-field-row="field-0"]'),row);assert.equal(row.open,true);
    assert.deepEqual(api.state.value,originalValue);assert.equal(app.requests.length,calls);
    assert.deepEqual(lifecycle,{apply:2,fit:2,dispose:0,layout:2});
    console.log('P3_FIELD_LIFECYCLE',JSON.stringify({fields:105,states:before.map(s=>s.viewport),lifecycle,http_delta:app.requests.length-calls}));
});
for(const kind of ['split','fusion'])test(kind+' field navigation keeps all explicit endpoints and supports repeated group and individual navigation',async t=>{
    const app=await mount(t),{panel,lifecycle}=app,calls=app.requests.length;
    const nav=panel.querySelector(`[data-field-relation="${kind}"]`),relation=app.value.mapping.relations.find(r=>r.relation_id===kind);
    for(let i=0;i<3;i++){nav.onclick();assertVisibleEndpoints(app,relation);}
    const buttons=panel.querySelectorAll('[data-relation-endpoint]');
    assert.equal(buttons.length,relation.left.length+relation.right.length);
    assert.equal(panel.querySelector('[data-visual-pair]').disabled,true,'group cannot be silently converted to a first-endpoint pair');
    assert.equal(panel.querySelector(`[data-node-scope="${kind==='split'?'right':'left'}"]`).disabled,true);
    for(const b of buttons)b.onclick();
    nav.onclick();assertVisibleEndpoints(app,relation);
    const before=snapshots(app);const filter=panel.querySelector('[data-field-filter="definition"]');filter.value='unknown';filter.onchange();
    assert.deepEqual(snapshots(app),before);assert.deepEqual(lifecycle,{apply:2,fit:2,dispose:0,layout:2});assert.equal(app.requests.length,calls);
    console.log('P3_RELATION',JSON.stringify({kind,endpoints:buttons.length,http_delta:0,lifecycle}));
});
test('explicit edge relation frames its declared geometry without rebuilding either renderer',async t=>{
    const app=await mount(t),relation=app.value.mapping.relations.find(r=>r.left[0].subject_kind==='edge');
    app.panel.querySelector(`[data-field-relation="${relation.relation_id}"]`).onclick();
    assertVisibleEndpoints(app,relation);assert.deepEqual(app.lifecycle,{apply:2,fit:2,dispose:0,layout:2});
});
test('no relation: path/name/copy/occurrence hints never manufacture navigation; reasons and exact evidence remain text',async t=>{
    const app=await mount(t),before=snapshots(app),calls=app.requests.length;
    const row=app.panel.querySelector('[data-field-row="field-2"]');row.open=true;
    assert.equal(row.querySelectorAll('button').length,0);assert.match(row.textContent,/mapping_missing/);
    const facts=JSON.parse(row.querySelector('pre').textContent);
    assert.deepEqual(facts.evidence_refs,app.value.axes.configuration.rows[2].evidence_refs);
    assert.deepEqual(snapshots(app),before);assert.equal(app.requests.length,calls);
});
test('controlled key events select exact renderer nodes; native field controls retain their keyboard semantics',async t=>{
    const app=await mount(t),{panel,instances}=app;
    for(const [i,key] of ['Enter',' '].entries()){
        const paper=panel.querySelectorAll('[data-comparison-paper]')[i];
        const endpoint={dataset:{kind:'node',id:i?'step.result':'step.request'}};
        // The shared DOM double does not implement closest or browser activation.
        paper.closest=()=>endpoint;
        const event=new Event('keydown',{cancelable:true});Object.defineProperty(event,'key',{value:key});paper.dispatchEvent(event);
        assert.equal(event.defaultPrevented,true);
        assert.equal(instances[i].graph.getCell(nodeKey(endpoint.dataset.id)).attributes.attrs['root/aria-pressed'],'true');
    }
    const filter=panel.querySelector('[data-field-filter="configuration"]'),row=panel.querySelector('[data-field-row="field-0"]');
    assert.equal(filter.tagName,'select');assert.ok(filter.getAttribute('aria-label'));
    assert.equal(row.children[0].tagName,'summary');assert.equal(panel.querySelector('[data-field-relation="split"]').tagName,'button');
    const before=snapshots(app);filter.value='unknown';filter.onchange();assert.deepEqual(snapshots(app),before);
});
for(const action of ['target','axes','scope','access','context','popstate','pagehide','pageshow'])test(action+' invalidation clears old fields, graphs, details and selection',async t=>{
    const app=await mount(t),{api,panel,instances}=app;
    panel.querySelector('[data-field-relation="split"]').onclick();
    panel.querySelector('[data-more-fields="configuration"]').onclick();
    const filter=panel.querySelector('[data-field-filter="configuration"]');filter.value='unknown';filter.onchange();
    if(action==='target'){const source=panel.querySelectorAll('select')[0];source.value='';source.onchange();}
    else if(action==='axes'){const axis=panel.querySelector('input');axis.checked=false;axis.onchange();}
    else if(action==='scope'){instances[0].onSelect('node','step.request');app.deny();panel.querySelector('[data-node-scope="left"]').onclick();}
    else if(action==='access'){app.deny();app.button('More exact objects').onclick();}
    else if(action==='context'){app.deny();app.button('New observation').onclick();}
    else {const event=new Event(action);if(action==='pageshow')Object.defineProperty(event,'persisted',{value:true});app.window.dispatchEvent(event);}
    for(let i=0;i<100&&api.state.value;i++)await tick();
    assert.equal(api.state.value,null);assert.equal(panel.querySelectorAll('[data-comparison-paper]').length,0);
    assert.equal(panel.querySelectorAll('[data-field-row]').length,0);assert.equal(panel.querySelectorAll('[data-mapping-details]').length,0);
    assert.equal(app.lifecycle.dispose,2);assert.ok(instances.every(r=>r.paper.removed));
    assert.ok(instances.every(r=>!r.resize.connected));
    if(['access','context','scope'].includes(action)){
        for(let i=0;i<100&&!api.state.error;i++)await tick();assert.equal(api.state.error,'access_changed');
    }
});

test('a newly accepted context starts with default local filter/page state and no old selection',async t=>{
    const app=await mount(t),{api,panel}=app;
    panel.querySelector('[data-more-fields="configuration"]').onclick();
    const filter=panel.querySelector('[data-field-filter="configuration"]');filter.value='unknown';filter.onchange();
    panel.querySelector('[data-field-relation="split"]').onclick();
    app.button('Read comparison').onclick();
    assert.equal(api.state.value,null);assert.equal(panel.querySelectorAll('[data-field-row]').length,0);
    for(let i=0;i<200&&app.instances.length!==4&&!api.state.error;i++)await tick();
    assert.equal(api.state.error,null);assert.equal(app.instances.length,4);
    assert.equal(panel.querySelector('[data-field-filter="configuration"]').value,'all');
    assert.equal(panel.querySelector('[data-field-rows="configuration"]').children.length,100);
    assert.equal(panel.querySelector('[data-field-row="field-100"]'),null);
    assert.equal(panel.querySelector('[data-mapping-details]').textContent,'');
    assert.equal(panel.querySelector('[data-visual-pair]').disabled,true);
    assert.equal(panel.querySelector('[data-node-scope="left"]').disabled,true);
    assert.equal(panel.querySelector('[data-node-scope="right"]').disabled,true);
    assert.deepEqual(app.lifecycle,{apply:4,fit:4,dispose:2,layout:4});
});
