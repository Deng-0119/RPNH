"""Synthetic installed layout + real original AgentTask/Registry material flow.

Not a production installation or callable safety proof. No model or native
transport runs. The parent request is finalized after real bootstrap identity,
but strictly before its first resource publication, claim, and Start.
"""
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import json
import os
import pytest
from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.public_module_materials import read_installed_contract
from cpn.rpnh.public_agent_materials import prepare_agent_material_draft, TARGET, BACKEND
from cpn.rpnh.agent_tasks import AgentTaskSpec, AgentStage, agent_task_registration, TEXT_SCHEMA
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from registered_material_fixtures import make_layout,LayoutDistribution


def make_agent_layout(path,monkeypatch):
    setup=make_layout(path,monkeypatch,'files')
    contract=setup.contract
    contract['contract_id']='agent-public-fixture';contract['revision']='r1'
    contract['profile_id']='rpnh-agent-offline-fixture/v1'
    setup.entrypoint.name=contract['profile_id']
    contract['supported_payloads']=['agent_task']
    contract['supported_shape']['module_operations']=[]
    contract['supported_shape']['agent_task_mode']='single_stage_plugin_free'
    adapter=next(u['unit_id'] for u in contract['implementation_units'] if u['distribution']['name']=='rpnh-candidate-layout')
    # The actual callback methods below live in this public asset, not in
    # the product adapter unit. The secret is an execution-time argument.
    import sys
    credential_path=Path(__file__).with_name('registered_agent_credentials.py')
    credential_distribution=LayoutDistribution('rpnh-public-credential-fixture','1.0',{'registered_agent_credentials.py':credential_path})
    setup.distributions[credential_distribution.metadata['Name']]=credential_distribution
    credential_unit='u.credentials'
    contract['implementation_units'].append({'unit_id':credential_unit,'distribution':{'name':'rpnh-public-credential-fixture','version':'1.0'},
        'runtime_abi':sys.implementation.cache_tag,'mode':'installed_digest_identity','asset_ids':['credential-code'],
        'dependency_unit_ids':[adapter]})
    contract['implementation_units'].sort(key=lambda unit:unit['unit_id'])
    raw=credential_path.read_bytes()
    setup.document['profiles'][0]['assets'].append({'asset_id':'credential-code','member':'registered_agent_credentials.py','size':len(raw),'sha256':w.sha(raw)})
    setup.document['profiles'][0]['assets'].sort(key=lambda asset:asset['asset_id'])
    prep=contract['preparation_roots']['compiler']
    contract['registrations']=[{'kind':d['kind'],'key':d['key'],'implementation_unit_ids':[] if d['kind']=='schema' else list(prep),'public_slot_ids':[]} for d in sorted(agent_task_registration().declarations(),key=lambda d:(d['kind'],d['key']))]
    def slot(identity,role,schema):
        return {'slot_id':identity,'role':role,'body_schema_id':schema,'cardinality':'one',
            'selection_rule':{'kind':'payload','payload_kind':'agent_task','registration_key':None}}
    contract['public_slots']=[slot('backend','public_host',BACKEND),slot('budget','budget','rpnh/module_declaration/v1'),
        slot('input','input',TEXT_SCHEMA),slot('policy','execution_policy',w.POLICY),slot('target','public_host',TARGET),
        slot('transport','public_host','llm_request_envelope/v1')]
    contract['execution_roots']['adapter']=[adapter]
    contract['binding_slots']=[{'slot_id':'credential-slot','service_ids':['service-a'],'account_binding_ids':['account-a','account-b'],
        'resolver':{'id':'resolver','revision':'r1','implementation_unit_id':credential_unit,'abi':'rpnh/opaque-secret-resolver/v1'},
        'renderer':{'id':'renderer','revision':'r1','implementation_unit_id':credential_unit,'abi':'rpnh/credential-renderer/v1'}}]
    setup.manifest.write_bytes(w.canonical(setup.document))
    observed=read_installed_contract(setup.entrypoint,'agent_task',set())
    observations={uid:w.decode(raw,canonical_required=True) for uid,raw in observed.observations}
    digest=w.sha(dict(observed.observations)[adapter])
    credential_digest=w.sha(dict(observed.observations)[credential_unit])
    from cpn.rpnh.runtime_policy import RuntimePolicy
    runtime=RuntimePolicy().as_document();runtime['context_pressure_trigger_ratio']=0.73
    policy={'schema_version':w.POLICY,'selection_id':'default-selection','adapter_kind':'external_provider',
        'implementation_unit_id':adapter,'implementation_digest':digest,'model_condition':'offline-public-model',
        'timeout_seconds':30,'max_output_tokens':256,'max_response_bytes':65536,'context_window_tokens':32768,'context_compaction_retained_tokens':4096,
        'reasoning_effort':'high','default_reasoning_effort':'high','supported_reasoning_efforts':['high'],
        'physical_profile_id':'synthetic-public-profile','runtime':runtime,
        'route_provenance':[{'route_id':'primary','provider':'offline-provider','backend':'chat-completions','protocol':'openai_chat_completions/v1',
            'endpoint':'https://provider.example.invalid/v1/chat/completions','transport':'https','outbound_model':'offline-public-model',
            'service_id':'service-a','account_binding_id':'account-a','secret_binding_id':'binding-a','credential_renderer_id':'renderer'}],
        'adapter_profile':{'config_schema_version':'rpnh/public_external_provider_config/v1','route_count':1,
            'recovery':{'strategy':'bounded_same_route_health_probe/v1','max_probe_attempts':1,'max_probe_success_formal_failure_cycles':1,'probe_timeout_budget_seconds':1}}}
    binding={'binding_id':'binding-a','slot_id':'credential-slot','service_id':'service-a','account_binding_id':'account-a','binding_revision':'r1',
        'resolver_id':'resolver','resolver_revision':'r1','resolver_unit_id':credential_unit,'resolver_implementation_sha256':credential_digest,
        'renderer_id':'renderer','renderer_revision':'r1','renderer_unit_id':credential_unit,'renderer_implementation_sha256':credential_digest}
    setup.policy=policy;setup.bindings=[binding];setup.observations=observations;setup.adapter_unit=adapter
    setup.private_config=path/'private-synthetic-config.json'
    setup.private_config.write_text(json.dumps({'secret':'SYNTHETIC-PRIVATE-DO-NOT-READ','endpoint':'https://different.example.invalid','account':'private-account'}))
    return setup


def agent_request(setup,parent,control_root):
    target=h7._target(parent,'child','test-root-binding',str(control_root))
    run=Path(target['document_root']).parent.parent/target['relative_path']
    spec=AgentTaskSpec(run_dir=run,prompt='Return the registered offline answer.',stages=(AgentStage('worker','Return one result.'),),
        execution_config_path=None,max_attempts_per_stage=2,max_parallel_nodes=1,owner_socket_path=run/'owner.sock',registered_execution_sources=(('default','policy-default'),))
    return {'schema_version':h7.REQUEST_SCHEMA,'slot_id':'child','child_kind':'agent_task',
        'definition':spec.as_worker_document(document_root=Path(target['document_root'])),
        'public_configuration':{'policy':setup.policy,'opaque_bindings':setup.bindings}}


def parent_agent_request(path,setup,monkeypatch):
    import cpn.rpnh.run as run
    import parent_child_fixtures as f
    actual_start=f.start_run;actual_boot=run._bootstrap_identity
    final=[]
    def start(module,registration,**kwargs):
        task=kwargs['task_input']
        def bootstrap(core,manifest):
            identity=actual_boot(core,manifest)
            parent={'source_id':'parent-source','run_ref':_ref_payload(identity.run_ref),'task_ref':_ref_payload(identity.task_ref)}
            request=agent_request(setup,parent,path.parent/'control')
            # Test-only deferred producer input, before any request publication.
            # No registered bytes, claim, request ref or Start exists yet.
            assert not core.event_store.object_rows_by_type('operation_binding/v1')
            object.__setattr__(task,'payload',canonical_json(request))
            final.append(request)
            return identity
        with monkeypatch.context() as patch:
            patch.setattr(run,'_bootstrap_identity',bootstrap)
            return actual_start(module,registration,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(f,'start_run',start)
        owner,admitted,execution,request_ref=f._parent_owner(path,request_document=setup.request)
    assert len(final)==1
    actual=owner._core.object_store.read_registered(owner._core.get_version(request_ref.version_id))
    assert actual==canonical_json(final[0])
    return owner,admitted,execution,request_ref,final[0]


def registered_agent_case(path,monkeypatch):
    setup=make_agent_layout(path/'layout',monkeypatch)
    owner,admitted,execution,request_ref,request=parent_agent_request(path/'parent',setup,monkeypatch)
    raw=canonical_json(request)
    draft=prepare_agent_material_draft(profile_id=setup.entrypoint.name,parent=h7.parent_identity(owner._core),request_bytes=raw,
        root_binding='test-root-binding',control_root=str(path/'control'))
    inventory=owner.schema_gateway.publish_public_material_inventory(draft,command_id='agent-public-materials')
    prepared=h7.prepare_registered_child_materials(owner._core,request_ref=request_ref,request_bytes=raw,
        registered_inventory_ref=inventory.resource_ref,root_binding='test-root-binding',control_root=str(path/'control'))
    intent=h7.register_child_intent(owner._core,execution,prepared)
    return SimpleNamespace(setup=setup,owner=owner,admitted=admitted,execution=execution,request=request,request_ref=request_ref,
        draft=draft,inventory=inventory,prepared=prepared,intent=intent,path=path)


def public_selection(case,*,policy=None,bindings=None):
    from cpn.llm_adapters.config import RegisteredLLMExecutionSelection
    return RegisteredLLMExecutionSelection(contract=case.setup.contract,policy=case.setup.policy if policy is None else policy,
        bindings=case.setup.bindings if bindings is None else bindings,observations=case.setup.observations)


def public_port(case,path,*,policy=None,bindings=None,secret='SYNTHETIC-ROTATABLE-SECRET',counts=None):
    from cpn.llm_adapters.public_credentials import PublicCredentialCapability
    from registered_agent_credentials import SyntheticCredentialStore
    from cpn.llm_adapters.factory import build_llm_input_port
    counts={} if counts is None else counts
    selection=public_selection(case,policy=policy,bindings=bindings)
    identity=selection.public_identity
    store=SyntheticCredentialStore(binding=identity['binding'],secret=secret,counts=counts)
    def capability(role,callback):
        value=identity[role]
        return None if value is None else PublicCredentialCapability(w.canonical(value['identity']),w.canonical(value['observation']),callback)
    port=build_llm_input_port(selection,destination_run_root=path,resolver=capability('resolver',store.resolve),renderer=capability('renderer',store.render))
    return port,counts
