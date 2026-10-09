"""Explicit D0-only native observation injection; never an OS/transport proof.

The product has no evidence issuer. This test module deliberately allocates its
private class via object.__new__, after real temporary Registry/PN setup. It
never starts a worker or reserves a production target. Native D1 remains NOT_RUN.
"""
from dataclasses import replace
import json
from pathlib import Path
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry import parent_bound as bound
from cpn.rpnh.registry.schema_catalog import canonical_json,SchemaCatalog
from cpn.rpnh.registry.publication import _ref_payload,_version_from_payload
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.petri_contracts import ArcDeclaration,PlaceDeclaration,InitialTokenDeclaration,LeaseIdentityDeclaration,ResourceLeasePoolBinding
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.run import OwnerInput,start_run
from cpn.rpnh.collaboration import source_identity_schema_data
from test_static_lease_reads import _registration,_simple_module,TEXT,EXECUTOR,CONFIG_SCHEMA_ID,lower_operation


def evidence(store,action,body):
    value=object.__new__(h7._NativeBoundaryEvidence)
    value.store=store;value.action=action;value.body=h7._json(body);value.consumed=False
    return value


def catalog():
    schemas,types,paths=source_identity_schema_data()
    return SchemaCatalog(schemas=schemas,types=types,schema_paths=paths)


def child_definition(*,occurrences=2,legacy_origin=False):
    reg=_registration()
    def lower(config,context):
        fragment=lower_operation(config,context)
        if occurrences>1:
            fragment=replace(fragment,places=tuple(replace(p,initial_tokens=(InitialTokenDeclaration(count=occurrences-1,value='work',schema=TEXT),))
                if p.name=='request' else p for p in fragment.places))
        if not legacy_origin:
            return fragment
        # A genuine pre-mechanical business-origin declaration, retained only
        # for the pure compiler namespace-collision negative test.
        return replace(fragment,
            places=(*fragment.places,PlaceDeclaration('h7origin',h7.CAPABILITY_SCHEMA,token_kind='resource_lease',capacity=1,reusable=True)),
            arcs=(*fragment.arcs,ArcDeclaration('h7origin','run','input',mode='read')),
            lease_identities=(*fragment.lease_identities,LeaseIdentityDeclaration('h7origin')),
            lease_pools=(*fragment.lease_pools,ResourceLeasePoolBinding('h7origin','h7origin',('h7origin',))))
    reg.register_component('test_h7_child',lower,identity={'implementation_id':'test.h7.child','revision':'v1'},contracts={'config_schema':CONFIG_SCHEMA_ID})
    doc=_simple_module('H7Child').to_dict();doc['components'][0]['key']='test_h7_child'
    if legacy_origin:doc['required_schemas'].append(h7.CAPABILITY_SCHEMA)
    module=ModuleDeclaration.from_dict(doc)
    return reg,module,compile_module(module,reg)


def _parent_owner(path,*,request_document,outcomes=('complete','failed'),capacity=1,with_launch=True,executor=h7.NATIVE_LAUNCH_EXECUTOR):
    reg=_registration()
    reg.register_executor(h7.NATIVE_LAUNCH_EXECUTOR,lambda **kwargs: None,
        identity={'implementation_id':'test.h7.native_observer','revision':'v1'},
        contracts={'transport':'deterministic','input_ports':None,'output_ports':None,'config_schema':CONFIG_SCHEMA_ID})
    def lower(config,context):
        f=lower_operation(config,context)
        extra=[PlaceDeclaration('h7capacity',TEXT,channel='control',token_kind='agent_resource',capacity=capacity,reusable=True,
            initial_tokens=(InitialTokenDeclaration(count=capacity),))]
        arcs=[ArcDeclaration('h7capacity','run','input',mode='borrow')]
        arcs += [ArcDeclaration('h7capacity','run','output',mode='return',outcome=outcome,emit='content_less') for outcome in outcomes]
        leases=();pools=()
        if with_launch:
            extra.append(PlaceDeclaration('h7launch',TEXT,token_kind='resource_lease',capacity=1,reusable=True))
            arcs.append(ArcDeclaration('h7launch','run','input',mode='read'))
            leases=(LeaseIdentityDeclaration('h7launch'),);pools=(ResourceLeasePoolBinding('h7launch','h7launch',('h7launch',)),)
        return replace(f,places=(*f.places,*extra),arcs=(*f.arcs,*arcs),lease_identities=leases,lease_pools=pools)
    reg.register_component('test_h7_parent',lower,identity={'implementation_id':'test.h7.parent','revision':'v1'},contracts={'config_schema':CONFIG_SCHEMA_ID})
    doc=_simple_module('H7Parent',input_schema=h7.REQUEST_SCHEMA).to_dict();doc['components'][0]['key']='test_h7_parent'
    operation=doc['components'][0]['operations'][0];operation['executor']=executor
    operation['outcomes']=[{'name':outcome,'products':[{'port':'result'}]} for outcome in outcomes]
    doc['required_schemas']=sorted(set(doc['required_schemas'])|{TEXT})
    module=ModuleDeclaration.from_dict(doc)
    request=request_document
    task=OwnerInput(h7.REQUEST_SCHEMA,canonical_json(request),'Typed child request')
    owner=start_run(module,reg,run_dir=path,task_input=task,entry_inputs={'request':task},
        resource_inputs={'step.h7launch':OwnerInput(TEXT,canonical_json('launch'),'Launch lease')} if with_launch else {},
        budgets=ModuleBudgetDeclaration(tuple(doc['budget_buckets']),(TEXT,),3,0,3,0),
        model_condition='offline-h7',owner_statement='Offline Registry H7 test',command_id='parent:fresh',catalog=catalog())
    owner.schema_gateway.bind_source_identity(source_id='parent-source',command_id='parent:source')
    admitted=owner.admit('step.run',logical_tau=0,command_id='parent:admit')
    execution=owner.start(admitted,command_id='parent:start')
    request_ref=execution.operation.inputs[0].resource_ref.as_version_ref()
    return owner,admitted,execution,request_ref


def parent_owner(path,*,child_registration=None,child_module=None,child_task=None,
                 child_entries=None,child_resources=None,request_document=None,
                 request_transform=None,**kwargs):
    if request_document is not None:
        # Explicit unregistered diagnostic input for the registered-producer
        # tests. This draft is never used by the current intent/history path.
        owner,admitted,execution,request_ref=_parent_owner(path,request_document=request_document,**kwargs)
        draft=h7.prepare_child_materials(parent=h7.parent_identity(owner._core),request_ref=request_ref,
            request_bytes=canonical_json(request_document),root_binding='test-root-binding',control_root=str(path.parent/'control'),
            public_payloads={name:b'{}' for name in h7.MATERIAL_SECTIONS-{'normalized_request'}})
        return owner,admitted,execution,draft
    from registered_history_fixtures import installed_history_layout,prepare_registered_history
    if child_registration is None:
        child_registration=_registration()
    if child_module is None:
        child_module=_simple_module('HistoryChild')
    task=child_task or OwnerInput(TEXT,canonical_json('task'),'Task')
    entries={'request':task} if child_entries is None else child_entries
    resources={} if child_resources is None else child_resources
    with installed_history_layout(path.parent/(path.name+'-materials'),child_registration,child_module,
            task=task,entries=entries,resources=resources,request_transform=request_transform) as setup:
        owner,admitted,execution,request_ref=_parent_owner(path,request_document=setup.request,**kwargs)
        prepared=prepare_registered_history(owner,request_ref,canonical_json(setup.request),path.parent/'control')
    return owner,admitted,execution,prepared


def phase(owner,kind,prior_ref,details):
    core=owner._core;prior=core.get_version(prior_ref.version_id).metadata
    base={k:prior[k] for k in ('parent','slot_id','execution')}
    ref=h7._ref(kind,canonical_json(base['parent']['run_ref']).decode(),base['slot_id'])
    body={'record_ref':_ref_payload(ref),**base,**details}
    if kind==h7.PARENT_KINDS[1]:body['intent_ref']=_ref_payload(prior_ref)
    elif kind==h7.PARENT_KINDS[2]:body.update(intent_ref=prior['intent_ref'],dispatch_ref=_ref_payload(prior_ref))
    else:
        intent=core.get_version(_version_from_payload(prior['intent_ref']).version_id).metadata
        body.update(intent_ref=prior['intent_ref'],dispatch_ref=prior['dispatch_ref'],worker_ref=_ref_payload(prior_ref),
            worker=prior['worker'],target=intent['target'],public_material_digest=intent['materials']['public_material_digest'],
            envelope_digest=intent['envelope_digest'],allowed_action='fresh_bound_bootstrap_once')
    return h7._advance_record(core,kind,prior_ref,details=details,evidence=evidence(core.event_store,kind,body))


def accepted_parent(path,**kwargs):
    owner,admitted,execution,prepared=parent_owner(path,**kwargs)
    intent=h7.register_child_intent(owner._core,execution,prepared)
    dispatch=phase(owner,h7.PARENT_KINDS[1],intent,{'bundle_digest':'a'*64})
    worker=phase(owner,h7.PARENT_KINDS[2],dispatch,{'worker':{'uid':1000,'pid':12345,'start_ticks':100,'boot_id':'offline-only'}})
    intent_body=owner._core.get_version(intent.version_id).metadata
    acceptance=phase(owner,h7.PARENT_KINDS[3],worker,{'request_digest':h7.digest(h7._json(h7.bootstrap_request_material(intent_body,_ref_payload(dispatch))))})
    return owner,admitted,execution,prepared,intent,dispatch,worker,acceptance


def bound_owner(path,monkeypatch,*,occurrences=2,missing_arc=False):
    from bound_lowering_fixtures import compiled_bound_owner
    reg,module,_=child_definition(occurrences=occurrences)
    return compiled_bound_owner(path,monkeypatch,reg,module,missing_arc=missing_arc)
