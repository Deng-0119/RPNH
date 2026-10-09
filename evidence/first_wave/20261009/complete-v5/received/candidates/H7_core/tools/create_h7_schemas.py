from pathlib import Path
import json
root=Path(__file__).resolve().parents[1]/'source/cpn/schemas'
s=lambda **kw: {'type':'string',**kw}
ref={'type':'object','additionalProperties':False,'properties':{'entity_type':s(minLength=1),'logical_id':s(pattern=r'^[a-z][a-z0-9_]*:[a-f0-9]{32}$'),'version_id':s(pattern=r'^[a-z][a-z0-9_]*:[a-f0-9]{32}$')},'required':['entity_type','logical_id','version_id']}
hash_=s(pattern='^[a-f0-9]{64}$'); num={'type':'integer','minimum':0}
def obj(p): return {'type':'object','additionalProperties':False,'properties':p,'required':list(p)}
def arr(i): return {'type':'array','items':i}
worker=obj({'uid':num,'pid':{'type':'integer','minimum':1},'start_ticks':{'type':'integer','minimum':1},'boot_id':s(minLength=1)})
source=obj({'source_id':s(minLength=1),'binding_ref':ref,'run_ref':ref,'task_ref':ref,'branch_ref':ref})
target=obj({'key_digest':hash_,'root_binding':s(minLength=1),'relative_path':s(pattern=r'^h7-runs/[a-f0-9]{64}/run$'),'transport_task_id':s(pattern=r'^task-h7-[a-f0-9]{64}$'),'document_root':s(minLength=1)})
execution=obj({'firing_ref':ref,'invocation_ref':ref,'lease_ref':ref,'start_event_id':s(pattern=r'^event:[a-f0-9]{32}$'),'net_ref':ref,'round_ref':ref,'run_authority_ref':ref,'execution_generation':num,'original_writer_epoch':num})
request=obj({'schema_version':{'const':'rpnh/parent_child_request/v1'},'slot_id':s(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$'),'child_kind':{'enum':['agent_task','module']},'definition':{'type':'object'},'public_configuration':{'type':'object'}})
materials=obj({'request_sha256':hash_,'normalized_request':{'type':'object'},'inventory':arr(obj({'name':s(minLength=1),'size':num,'sha256':hash_})),'public_material_digest':hash_,'initial_declaration_digest':hash_})
common={'record_ref':ref,'parent':source,'slot_id':s(minLength=1),'execution':execution}
records={
'parent_child_intent/v1':{**common,'request_ref':ref,'request':request,'materials':materials,'target':target,'envelope':{'type':'object'},'envelope_digest':hash_},
'parent_child_dispatch/v1':{**common,'intent_ref':ref,'bundle_digest':hash_},
'parent_child_worker/v1':{**common,'intent_ref':ref,'dispatch_ref':ref,'worker':worker},
'parent_child_acceptance/v1':{**common,'intent_ref':ref,'dispatch_ref':ref,'worker_ref':ref,'worker':worker,'request_digest':hash_,'target':target,'public_material_digest':hash_,'envelope_digest':hash_,'allowed_action':{'const':'fresh_bound_bootstrap_once'}},
'parent_bound_bootstrap/v1':{'record_ref':ref,'child_run_ref':ref,'child_task_ref':ref,'child_branch_ref':ref,'genesis_ref':ref,'bootstrap_ref':ref,'initial_writer_epoch':num,'acceptance':{'type':'object'},'initial_declaration_digest':hash_},
'parent_bound_origin/v1':{'record_ref':ref,'marker_ref':ref,'child_run_ref':ref,'child_task_ref':ref,'child_source_binding_ref':ref,'capability_ref':ref,'initial_writer_epoch':num,'initial_declaration_digest':hash_},
}
content={'rpnh/parent_child_request/v1':request,'rpnh/parent_origin_capability/v1':obj({'schema_version':{'const':'rpnh/parent_origin_capability/v1'},'origin_ref':ref,'marker_ref':ref,'child_run_ref':ref,'child_task_ref':ref,'parent_acceptance':{'type':'object'},'initial_writer_epoch':num,'initial_declaration_digest':hash_})}
for key,p in records.items():content['registry_v1/'+key]=obj(p)
content['registry_v1/parent_child_recorded/v1']=obj({'record_ref':ref,'predecessor_refs':arr(ref)})
for key,shape in content.items():
 doc={'$schema':'http://json-schema.org/draft-07/schema#','$id':key,**shape}
 space,rel=key.split('/',1); path=root/space/(rel.replace('/','.')+'.schema.json'); path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(doc,indent=2)+'\n')
idx=root/'INDEX.json'; doc=json.loads(idx.read_text()); by={v['schema_id']:v for v in doc['schemas']}
for key in content:
 space,rel=key.split('/',1);by[key]={'schema_id':key,'path':space+'/'+rel.replace('/','.')+'.schema.json'}
doc['schemas']=sorted(by.values(),key=lambda x:(x['path'],x['schema_id']));idx.write_text(json.dumps(doc,indent=2)+'\n')
cat=root.parent/'rpnh/registry/schema_catalog.py';text=cat.read_text();text=text.replace('CURRENT_OBJECT_TYPES = (','CURRENT_OBJECT_TYPES = (\n'+''.join('    "'+key+'",\n' for key in records));text=text.replace('CURRENT_EVENT_TYPES = (','CURRENT_EVENT_TYPES = (\n    "parent_child_recorded/v1",');text=text.replace('CURRENT_CONTENT_SCHEMA_REFS = (','CURRENT_CONTENT_SCHEMA_REFS = (\n    "rpnh/parent_child_request/v1",\n    "rpnh/parent_origin_capability/v1",');cat.write_text(text)
