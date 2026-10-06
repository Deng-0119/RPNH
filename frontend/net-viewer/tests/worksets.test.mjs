import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {normalizeWorksets,renderWorksets,installWorksetPanel} from '../../../cpn/frontend/static/worksets.mjs';

const reference=n=>({schema_version:'rpnh/collaboration/source_version_ref/v1',source_id:'fixture',ref:{entity_type:'collaboration_workset/v1',logical_id:'resource:'+n.repeat(32),version_id:'resource_version:'+n.repeat(32)}});
function value(){
    const ref=reference('a');
    return {schema_version:'rpnh/workset_view/v1',source_id:'fixture',capture_cut:20,coverage:'complete_local_worksets',
        current:[{workset_ref:ref,state:'open',generation:0,collection_version:1,sequence:1,expected_slots:['answer'],
            requirements_ref:reference('b'),input_binding_ref:reference('c'),workset_sealed:false,root_terminal_ref:null,
            occurrence_count:0,available_occurrence_count:null,availability_checkpoint_ref:null,
            acceptance_count:0,contribution_count:null,acceptance_coverage:'complete',contribution_coverage:'not_provided',
            acceptances:[],contributions:[],physical_attempt_count:null,physical_coverage:'source_not_observed',
            physical_source_cuts:{},physical_attempts:[],recorded_physical_attempts:[],root_terminal_evidence_ref:null,required_child_seal_ref:null,
            cancel_status:'not_provided',stop_status:'not_provided'}],
        history:[{workset_ref:ref,sequence:1,collection_version:1,action:'create',state_at_commit:'open',recorded_ordinal:20,is_current:true}]};
}
class Element {
    constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.open=false;this.text='';}
    set textContent(v){this.text=String(v);this.children=[];}
    get textContent(){return this.text+this.children.map(x=>x.textContent).join(' ');}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.text='';this.children=children;}
    addEventListener(name,fn){this.listeners[name]=fn;}
    showModal(){this.open=true;}
    close(){this.open=false;this.listeners.close?.();}
}
function dom(){
    const body=new Element('body'), language={value:'en'};
    globalThis.document={body,createElement:tag=>new Element(tag),getElementById:id=>id==='language'?language:null};
    return body;
}
test('unknown physical coverage and historical/current business fields remain distinct',()=>{
    dom();const parent=new Element('div');renderWorksets(parent,value());
    assert.match(parent.textContent,/Physical attempts: Not observed/);
    assert.match(parent.textContent,/Immutable history/);assert.match(parent.textContent,/Current business collection/);
    const wrong=value();wrong.current[0].physical_attempt_count=0;assert.throws(()=>normalizeWorksets(wrong));
    const missing=value();missing.current[0].contribution_coverage='not_provided';missing.current[0].contribution_count=null;
    renderWorksets(parent,missing);assert.match(parent.textContent,/Contributed: Not observed/);
    const repeated=value();repeated.history.push(repeated.history[0]);assert.throws(()=>normalizeWorksets(repeated));
    const invalidType=value();invalidType.current[0].workset_ref.ref.entity_type='collaboration_acceptance/v1';assert.throws(()=>normalizeWorksets(invalidType));
    const invalidCoverage=value();invalidCoverage.current[0].contribution_coverage='invented';assert.throws(()=>normalizeWorksets(invalidCoverage));
    const falseZero=value();falseZero.current[0].contribution_count=0;falseZero.current[0].contribution_coverage='complete';assert.throws(()=>normalizeWorksets(falseZero));
});
test('close cancels an in-flight response and reopening accepts only its newest request',async()=>{
    const body=dom(),button=new Element('button'),requests=[];
    globalThis.fetch=(_url,options)=>new Promise(resolve=>requests.push({resolve,options}));
    installWorksetPanel(button);button.listeners.click();const dialog=body.children[0];
    assert.equal(requests.length,1);dialog.close();assert.equal(requests[0].options.signal.aborted,true);
    button.listeners.click();requests[0].resolve({ok:true,json:async()=>value()});
    await new Promise(resolve=>setImmediate(resolve));assert.equal(dialog.children[1].textContent,'…');
    requests[1].resolve({ok:true,json:async()=>value()});await new Promise(resolve=>setImmediate(resolve));
    assert.match(dialog.children[1].textContent,/Current business collection/);
});
if(process.env.RPNH_WORKSET_DTO){
    test('actual Registry N02 DTO feeds the production model and panel',()=>{
        const data=JSON.parse(readFileSync(process.env.RPNH_WORKSET_DTO,'utf8'));dom();const parent=new Element('div');
        const normalized=normalizeWorksets(data);assert.equal(normalized.current[0].physical_attempt_count,2);
        assert.equal(normalized.current[0].acceptance_count,1);assert.equal(normalized.current[0].contribution_count,1);
        assert.equal(normalized.current[0].occurrence_count,1);assert.equal(normalized.current[0].available_occurrence_count,0);
        assert.ok(normalized.current[0].root_terminal_evidence_ref);
        const unknown=structuredClone(data);unknown.current[0].acceptances[0].availability='not_provided';
        assert.throws(()=>normalizeWorksets(unknown));
        const missingTotal=structuredClone(data);missingTotal.current[0].available_occurrence_count=null;
        assert.throws(()=>normalizeWorksets(missingTotal));
        renderWorksets(parent,data);assert.match(parent.textContent,/unknown/);assert.match(parent.textContent,/acknowledged/);
        assert.match(parent.textContent,/Physical attempts: 2/);
        assert.match(parent.textContent,/Accepted occurrences: 1/);assert.match(parent.textContent,/Currently available: 0/);
    });
}
