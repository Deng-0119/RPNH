/** Pure protocol dictionaries, not producer-generated execution evidence. */
import {ACTIVITY_TYPES} from '../../../cpn/frontend/static/firing-activity.mjs';
import {normalizeFrame} from '../../../cpn/frontend/static/dashboard-model.mjs';
const id=(stem,n)=>stem+':'+n.toString(16).padStart(32,'0');
const ref=(type,logical,version,n)=>({entity_type:type,logical_id:id(logical,n),version_id:id(version,n)});
export const selected={net_ref:ref('net_instance/v1','net_instance','net_instance_version',1),checkpoint_ref:ref('marking_checkpoint/v1','marking_checkpoint','marking_checkpoint_version',2),cut:71};
export function fixtureFrame() {
    const source={mode:'registry_current',task_id:id('task',3),run_dir:'/synthetic/readonly',net_ref:selected.net_ref,verified_head_ordinal:71,writer_fencing_epoch:2};
    return normalizeFrame({schema_version:'rpnh/dashboard/v1',source,net:{schema_version:'rpnh/net_view/v1',source:structuredClone(source),summary:{},
        marking:{checkpoint_ref:selected.checkpoint_ref,token_count:0,active_token_count:0},nodes:[{id:'step.run',label:'Step',kind:'transition',category:'execution',inputs:[],outputs:[]},{id:'other',label:'Other',kind:'transition',category:'execution',inputs:[],outputs:[]}],edges:[]},
        position:{mode:'history',cursor:71,latest_head:500},coverage:{firings:'not_provided'},agent_nodes:[],boundaries:{entry:[],exit:[]},presentation:{nodes:{}},
        transition_bindings:[{transition_id:'step.run',node_ref:ref('node_declaration/v1','node','node_declaration_version',4),input_ports:[],output_ports:[]},{transition_id:'other',node_ref:ref('node_declaration/v1','node','node_declaration_version',14),input_ports:[],output_ports:[]}]});
}
export function envelope({cursor=null,limit=50,count=3,start=0,more=false,head=500,epoch=2,next='YQ'}={}) {
    const frame=fixtureFrame(),source={mode:frame.source.mode,task_id:frame.source.task_id,run_dir:frame.source.run_dir};
    const task_ref=ref('task/v1','task','task_version',3),run_ref=ref('native_run_identity/v1','run','run_version',5);
    const capture={head_ordinal:head,writer_fencing_epoch:epoch},scope={task_ref,run_ref,...selected,transition_ids:['step.run'],supported_event_types:[...ACTIVITY_TYPES],source,disclosure_profile:'single_registry_standard_activity_metadata/v1',evidence_cut:capture};
    // query_scope uses cut/net/checkpoint individually, never a nested selector.
    const firing_ref=ref('transition_firing/v1','transition_firing','transition_firing_version',6);
    const firing={firing_ref,node_ref:frame.transition_bindings[0].node_ref,transition_id:'step.run',attempt_index:1,admission_checkpoint_ref:selected.checkpoint_ref,
        invocation_ref:ref('invocation/v1','invocation','invocation_version',7),publication_class_at_evidence:'PROVISIONAL',publication_visible_position:null,
        business_outcome:'not_provided',completion:'not_provided',result:'not_provided',successor_checkpoint:'not_provided',delta:'not_provided'};
    const records=Array.from({length:count},(_,i)=>{const ordinal=80+start+i,event_id=id('event',ordinal),event_type=ACTIVITY_TYPES[Math.min(start+i,2)];return {
        firing_ref,event_id,event_type,transaction_id:id('transaction',ordinal),recorded_ordinal:ordinal,recorded_at:'recorded',transaction_commit_ordinal:ordinal+1,
        evidence:{pointer:{source,firing_ref},recorded_position:{event_id,ordinal},visible_position:null,publication_class:'PROVISIONAL',observed_at:capture}};});
    return {schema_version:'rpnh/firing_activity/v1',selector:structuredClone(selected),view:{context:{mode:'retrospective-activity',source_set_ref:null,manifest_ref:null,path_ref:null,disclosure_ref:null,query_scope:scope,source_cuts:[{source,cut:capture}]},
        activity:{source_identity:{task_ref,run_ref,source},supported_event_types:[...ACTIVITY_TYPES],record_range:{after_ordinal:0,through_ordinal:head},firings:count?[firing]:[],records,
            coverage:{scope,status:'page',loaded_count:count,total_count:null,missing:['completion','result','settlement','successor_checkpoint','delta','other_activity']},lifecycle_coverage:'not_provided'}},
        page:{request_cursor:cursor,returned_count:count,limit,has_more:more,next_cursor:more?next:null,end_of_supported_scope:!more}};
}
