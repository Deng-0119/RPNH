"""Actual numbers/files Registry material and D0-only bound owner setup."""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import json
from cpn.rpnh.registry import parent_child as h7,parent_bound as bound
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.public_module_materials import prepare_module_material_draft
from cpn.rpnh.bound_child_lowering import with_bound_origin,ORIGIN_SYMBOL
from cpn.rpnh.run import OwnerInput,start_run
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.components.execution_services import ExecutionServices
from cpn.components.registered_material_checks import RegisteredMaterialContext
from registered_material_fixtures import make_layout,parent_with_request
from registered_agent_owner_fixtures import install_owner_test_boundaries,RegisteredAgentOwnerFixture
from parent_child_fixtures import evidence,phase,catalog


def module_material(path,monkeypatch,story):
    setup=make_layout(path/'layout',monkeypatch,story)
    parent,admitted,execution,old=parent_with_request(path/'parent',setup.request)
    draft=prepare_module_material_draft(profile_id=setup.entrypoint.name,parent=h7.parent_identity(parent._core),request_bytes=old.request_bytes,
        root_binding='test-root-binding',control_root=str(path/'control'))
    inventory=parent.schema_gateway.publish_public_material_inventory(draft,command_id='module-gate-material')
    prepared=h7.prepare_registered_child_materials(parent._core,request_ref=old.request_ref,request_bytes=old.request_bytes,
        registered_inventory_ref=inventory.resource_ref,root_binding='test-root-binding',control_root=str(path/'control'))
    intent=h7.register_child_intent(parent._core,execution,prepared)
    return SimpleNamespace(setup=setup,owner=parent,execution=execution,draft=draft,inventory=inventory,prepared=prepared,intent=intent,path=path)


def start_module_owner(material,monkeypatch,*,bound_mode=True,with_context=True,entry_override=None,task_override=None,global_cap=1):
    import cpn.rpnh.run as run
    boundary=install_owner_test_boundaries(monkeypatch)
    parent=material.owner
    target=material.prepared.materials['normalized_request']['target']
    destination=Path(target['document_root']).parent.parent/target['relative_path']
    if not bound_mode: destination=material.path/'ordinary-legacy-owner'
    module=with_bound_origin(material.setup.module) if bound_mode else material.setup.module
    registration=material.draft.registration
    request_schema=next(p['schema'] for p in material.setup.module.to_dict()['components'][0]['ports'] if p['name']=='request')
    data=material.setup.request['public_configuration']['inputs']
    task=OwnerInput(request_schema,canonical_json(data if task_override is None else task_override),'Actual Module task input')
    entry=OwnerInput(request_schema,canonical_json(data if entry_override is None else entry_override),'Actual Module entry input')
    resources={}
    original_boot=run._bootstrap_identity;original_publish=run._publish_private_system
    if bound_mode:
        if not hasattr(material,'d0_acceptance'):
            dispatch=phase(parent,h7.PARENT_KINDS[1],material.intent,{'bundle_digest':'a'*64})
            worker=phase(parent,h7.PARENT_KINDS[2],dispatch,{'worker':{'uid':1000,'pid':12345,'start_ticks':100,'boot_id':'offline-only'}})
            intent_body=parent._core.get_version(material.intent.version_id).metadata
            material.d0_acceptance=phase(parent,h7.PARENT_KINDS[3],worker,{'request_digest':h7.digest(h7._json(h7.bootstrap_request_material(intent_body,_ref_payload(dispatch))))})
        accepted=material.d0_acceptance;body=parent._core.get_version(accepted.version_id).metadata
        with parent._core.event_store.connect() as db:
            row=db.execute('SELECT transaction_id FROM objects WHERE version_id=?',(str(accepted.version_id),)).fetchone()
            fact=db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='parent_child_recorded/v1'",(row[0],)).fetchone()
            committed=db.execute("SELECT ordinal FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",(row[0],)).fetchone()
        external={'parent_source_id':body['parent']['source_id'],'acceptance_json':h7._json(body).decode(),
            'intent_json':h7._json(parent._core.get_version(material.intent.version_id).metadata).decode(),
            'acceptance_transaction_id':row[0],'acceptance_event_id':fact[0],'acceptance_commit_ordinal':committed[0],
            'initial_declaration_digest':material.prepared.materials['initial_declaration_digest']}
        def bootstrap(core,manifest,**kwargs):
            boundary.counts['d0_native_bootstrap_evidence']+=1
            with h7._native_boundary(evidence(core.event_store,'bound_bootstrap',external),core.event_store,'bound_bootstrap',external):
                return original_boot(core,replace(manifest,protocol_versions=(*manifest.protocol_versions,h7.BOUND_PROTOCOL)),_parent_bound_acceptance=external,**kwargs)
        def publish(core,task_ref,command,**kwargs):
            if command.content_schema_ref!=h7.CAPABILITY_SCHEMA: return original_publish(core,task_ref,command,**kwargs)
            boundary.counts['d0_native_origin_evidence']+=1
            from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
            RegistryRegistrationGateway(core,task_ref,command.origin.bootstrap_command_ref).bind_source_identity(source_id='child-source',command_id='child:source')
            marker=json.loads(core.event_store.object_rows_by_type(h7.BOUND_KINDS[0])[0]['metadata_json'])
            return bound.close_bound_origin(core,evidence=evidence(core.event_store,'bound_origin',marker))
        placeholder={'schema_version':h7.CAPABILITY_SCHEMA,'origin_ref':_ref_payload(accepted),'marker_ref':_ref_payload(accepted),
            'child_run_ref':_ref_payload(parent.identity.run_ref),'child_task_ref':_ref_payload(parent.identity.task_ref),'parent_acceptance':external,
            'initial_writer_epoch':1,'initial_declaration_digest':external['initial_declaration_digest']}
        resources={ORIGIN_SYMBOL:OwnerInput(h7.CAPABILITY_SCHEMA,canonical_json(placeholder),'Explicit D0 protected origin observation')}
    with monkeypatch.context() as patch:
        if bound_mode:
            patch.setattr(run,'_bootstrap_identity',bootstrap);patch.setattr(run,'_publish_private_system',publish)
        owner=start_run(module,registration,run_dir=destination,task_input=task,entry_inputs={'request':entry},resource_inputs=resources,
            budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']),('rpnh/module_declaration/v1',),global_cap,0,global_cap,0),
            model_condition='native-plugin-no-model',owner_statement='Offline Module consistency only',command_id='module-gate:fresh',catalog=catalog())
    loop=OwnerEventLoop(owner,destination/'owner.sock')
    try:
        admitted=owner.admit('plugin.run',logical_tau=0,command_id='module-gate:admit')
        execution=owner.start(admitted,command_id='module-gate:start')
        context=RegisteredMaterialContext(parent._core,material.inventory.resource_ref,material.inventory.root['public_material_digest'],owner._core) if with_context else None
        services=ExecutionServices(owner=owner,event_loop=loop,registered_material_context=context)
        return RegisteredAgentOwnerFixture(owner,loop,admitted,execution,services,None,{},boundary)
    except BaseException:loop.close();raise
