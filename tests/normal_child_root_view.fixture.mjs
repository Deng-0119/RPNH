/** Focused consumer fixture. Invoke runNormalChildRootViewFixture only when authorized. */
import assert from 'node:assert/strict';
import {normalizeWorksets, renderWorksets} from '../cpn/frontend/static/worksets.mjs';

const source='normal-child-target';
const local=(type, value, version=value)=>({entity_type:type,
    logical_id:`resource:${value.toString(16).padStart(32,'0')}`,
    version_id:`resource_version:${version.toString(16).padStart(32,'0')}`});
const ref=(type,value,version=value,sourceId=source)=>({
    schema_version:'rpnh/collaboration/source_version_ref/v1',source_id:sourceId,ref:local(type,value,version)});

function completedV1(){
    const workset=version=>ref('collaboration_workset/v1',1,version);
    const input=ref('resource_version/v1',10), acceptance=ref('collaboration_acceptance/v1',11);
    const logical=ref('collaboration_logical_delivery/v1',12,12,'normal-child-source');
    const physical=ref('resource_delivery/v1',13,13,'normal-child-source');
    const row={workset_ref:workset(5),state:'completed',generation:1,collection_version:1,sequence:5,
        requirements_ref:ref('resource_version/v1',9),input_binding_ref:input,expected_slots:['answer'],
        workset_sealed:true,required_child_seal_ref:null,acceptance_count:1,contribution_count:1,
        acceptance_coverage:'complete',contribution_coverage:'complete',physical_attempt_count:null,
        physical_coverage:'source_not_observed',physical_source_cuts:{},physical_attempts:[],
        acceptances:[{acceptance_ref:acceptance,target_workset_id:workset(5).ref.logical_id,
            collection_version:1,input_binding_ref:input,logical_delivery_ref:logical,
            initial_physical_delivery_ref:physical,occurrence_ref:local('petri_token/v1',14),
            slot:'answer',availability:'absent_from_current_marking'}],
        contributions:[{contribution_ref:ref('collaboration_contribution/v1',15),acceptance_ref:acceptance,
            occurrence_ref:local('petri_token/v1',16),slot:'answer'}],
        occurrence_count:1,available_occurrence_count:0,availability_checkpoint_ref:local('marking_checkpoint/v1',17),
        recorded_physical_attempts:[],root_terminal_ref:ref('collaboration_root_terminal/v1',18),
        root_terminal_evidence_ref:null,cancel_status:'not_provided',stop_status:'not_provided'};
    const actions=['create','seal','accept','contribute','complete'];
    return {schema_version:'rpnh/workset_view/v1',source_id:source,capture_cut:50,
        coverage:'complete_local_worksets',current:[row],history:actions.map((action,i)=>({
            workset_ref:workset(i+1),sequence:i+1,collection_version:1,action,
            state_at_commit:i===0?'open':i===4?'completed':'sealed',recorded_ordinal:10+i,is_current:i===4}))};
}

function completedV2(){
    const raw=completedV1(),row=raw.current[0];
    raw.schema_version='rpnh/workset_view/v2';
    row.root_terminal_ref=ref('collaboration_root_terminal/v2',18);
    row.root_child_closure={profile:'execution-v1-normal-only',root_terminal_ref:structuredClone(row.root_terminal_ref),
        seal_ref:ref('execution_child_seal/v1',19),verified_at_cut:raw.capture_cut};
    return raw;
}

export function runNormalChildRootViewFixture(){
    const legacy=completedV1(),normal=completedV2();
    assert.deepEqual(normalizeWorksets(legacy),legacy);
    assert.equal(Object.hasOwn(normalizeWorksets(legacy).current[0],'root_child_closure'),false);
    assert.deepEqual(normalizeWorksets(normal),normal);
    const cases=[
        raw=>{raw.schema_version='rpnh/workset_view/v3';},
        raw=>{raw.schema_version='rpnh/workset_view/v1';delete raw.current[0].root_child_closure;},
        raw=>{delete raw.current[0].root_child_closure;},
        raw=>{raw.current[0].root_child_closure=null;},
        raw=>{raw.current[0].root_child_closure.seal_ref=null;},
        raw=>{raw.current[0].root_child_closure.seal_ref.ref.entity_type='execution_children_sealed/v1';},
        raw=>{raw.current[0].root_child_closure.seal_ref.source_id='other-source';},
        raw=>{raw.current[0].root_child_closure.root_terminal_ref.source_id='other-source';},
        raw=>{raw.current[0].root_child_closure.root_terminal_ref.ref.version_id=local('x/v1',99).version_id;},
        raw=>{raw.current[0].root_child_closure.profile='execution-v1-any';},
        raw=>{raw.current[0].root_child_closure.verified_at_cut--;},
        raw=>{raw.current[0].root_child_closure.verified_at_cut='50';},
        raw=>{raw.current[0].root_child_closure.extra=true;},
        raw=>{raw.current[0].required_child_seal_ref=structuredClone(raw.current[0].root_child_closure.seal_ref);},
        raw=>{raw.current[0].root_terminal_ref.ref.entity_type='collaboration_root_terminal/v3';},
        raw=>{raw.current[0].state='sealed';},
    ];
    for(const change of cases){const value=structuredClone(normal);change(value);assert.throws(()=>normalizeWorksets(value));}
    const legacyWithClosure=completedV1();legacyWithClosure.current[0].root_child_closure=null;
    assert.throws(()=>normalizeWorksets(legacyWithClosure));

    const mixed=completedV2(),old=structuredClone(legacy.current[0]);
    old.workset_ref=ref('collaboration_workset/v1',101,105);
    old.acceptances[0].target_workset_id=old.workset_ref.ref.logical_id;old.root_child_closure=null;
    old.acceptances[0].acceptance_ref=ref('collaboration_acceptance/v1',111);
    old.acceptances[0].logical_delivery_ref=ref('collaboration_logical_delivery/v1',112,112,'normal-child-source');
    old.acceptances[0].initial_physical_delivery_ref=ref('resource_delivery/v1',113,113,'normal-child-source');
    old.acceptances[0].occurrence_ref=local('petri_token/v1',114);
    old.contributions[0].acceptance_ref=structuredClone(old.acceptances[0].acceptance_ref);
    old.contributions[0].contribution_ref=ref('collaboration_contribution/v1',115);
    old.contributions[0].occurrence_ref=local('petri_token/v1',116);
    old.root_terminal_ref=ref('collaboration_root_terminal/v1',118);
    mixed.current.push(old);mixed.history.push(...legacy.history.map((entry,i)=>({...structuredClone(entry),
        workset_ref:ref('collaboration_workset/v1',101,101+i),recorded_ordinal:30+i})));
    assert.deepEqual(normalizeWorksets(mixed),mixed);
    const noRoot=structuredClone(mixed);noRoot.current[1].root_terminal_ref=null;
    noRoot.current[1].state='sealed';noRoot.current[1].sequence=4;
    noRoot.current[1].workset_ref=ref('collaboration_workset/v1',101,104);
    noRoot.history.pop();noRoot.history[noRoot.history.length-1].is_current=true;
    assert.deepEqual(normalizeWorksets(noRoot),noRoot);
    const unbound=structuredClone(mixed);unbound.current[1].root_child_closure=structuredClone(normal.current[0].root_child_closure);
    assert.throws(()=>normalizeWorksets(unbound));

    // No browser or listener: a minimal DOM records exactly the rendered text.
    const documentDescriptor=Object.getOwnPropertyDescriptor(globalThis,'document');
    const node=()=>({children:[],textContent:'',append(...children){this.children.push(...children);},
        replaceChildren(...children){this.children=children;}});
    Object.defineProperty(globalThis,'document',{configurable:true,value:{createElement:node}});
    try{
        const container=node();renderWorksets(container,normal,'en');
        const lines=[];const visit=value=>{lines.push(value.textContent);for(const child of value.children)visit(child);};visit(container);
        assert(lines.some(value=>value.includes(normal.current[0].root_child_closure.seal_ref.ref.version_id)
            &&value.includes('execution-v1-normal-only')&&value.includes('Verified at cut: 50')));
        assert.equal(normal.current[0].required_child_seal_ref,null);
    }finally{
        if(documentDescriptor)Object.defineProperty(globalThis,'document',documentDescriptor);
        else delete globalThis.document;
    }
    return {positive_views:4,rejected_views:cases.length+2,rendered_root_seal:true};
}
