"""One actual adapted episode, or a separate no-model native preflight."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import threading
import time

from cpn.rpnh.agent_tasks import AgentTaskSpec, AgentStage
from cpn.plugins.catalog import load_catalog
from cpn.plugins.managed_tools import ManagedPluginToolCatalog
from . import plugin
from .backend import ReleaseBackend, ReleaseServer
from .design import actor_contracts, designer_prompt, parse_design, native_stage_graph, ROLES
from .user_runtime import create_user, run_user

FACTORIES={'release_handoff':plugin.factory}


def dump(path,value):
    path=Path(path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink!=1):
        raise ValueError('output must be an ordinary independent file')
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str)+'\n')


def user_specs(root,execution,plan,contracts,endpoint,*,cap=64):
    config=plugin.configuration(endpoint)
    catalog=load_catalog(config,factories=FACTORIES)
    result={}
    for role in ROLES:
        stages=tuple(AgentStage(s['stage_id'],s['instruction']+'\nUse your declared stage result port, then complete_interaction. '
            'Release any read claim with finish_read before completing the stage.') for s in plan['roles'][role])
        tools={name:{'selector':'release_handoff/'+name} for name in plugin.ROLE_TOOLS[role]}
        bindings={stage.stage_id:{'tools':tools,'admitted_effects':['external_read','external_write']} for stage in stages}
        managed=ManagedPluginToolCatalog(catalog,tools,admitted_effects=['external_read','external_write'])
        policy={'policy_id':'managed_conflict_domains/v1','max_in_flight':1,
            'conflict_domains':[{'registration_key':t.registration_key,'effect':t.effect,
                'reads':['release-world'] if t.effect=='external_read' else [],
                'writes':['release-world'] if t.effect=='external_write' else [],'unknown':False} for t in managed.tools]}
        prompt=json.dumps({'role':role,'business_contract':contracts[role],
            'scenario':'Local controlled-user adaptation; no external Drive/Gitea/ITSM accounts.',
            'published_project_entry':'project/index; call discover starting at project, then follow relative links. '
                'This link is the shared source Registry entry; resource bodies require actual read_note.',
            'resource_rules':'read_note returns a canonical version string and retained read_id. '
                'Use finish_read before final completion. update_note uses canonical expected_version, not Drive integer. '
                'Discover/read actual Git commits; confirm_release needs finished read_id, current note_version, '
                'actual sql_commit, ISO UTC final_verification_time, findings; support adds incident_id. '
                'status returns current UTC time. Keep original incident fields unchanged.',
            'scope':'Your private workspace has only your request and stage outputs; all shared world reads use declared tools. '
                'Do not claim actions that tools did not perform. A busy response explicitly has no business write effect; '
                'unknown effect requires stop/reconciliation.'},ensure_ascii=False)
        result[role]=AgentTaskSpec(run_dir=root/'users'/role,prompt=prompt,
            stages=stages if len(stages)==1 else (),
            workflow_graph=native_stage_graph(stages) if len(stages)>1 else None,
            execution_config_path=execution,max_attempts_per_stage=cap,aggregate_call_cap=cap*len(stages),
            plugin_configuration=config,plugin_catalog_digest=catalog.digest,managed_bindings=bindings,
            managed_tool_policy=policy,owner_statement='Authorized controlled '+role+' user in one local release world')
    return result


def prepared_users(root,specs):
    return {role:create_user(spec,workspace_root=root,source_id='mup-'+root.name+'-'+role,
        owner_name={'operations':'Olivia','developer':'Ethan','qa':'Mia','support':'Ivan'}[role],factories=FACTORIES)
        for role,spec in specs.items()}


def preflight(root,execution,task):
    from cpn.plugins.managed_tools import ManagedPluginInvocationService, execute_prepared_invocation
    from cpn.rpnh.registry.schema_catalog import canonical_json
    contracts=actor_contracts(task)
    plan={'roles':{r:[{'stage_id':r,'instruction':'Offline exact native tool boundary fixture.'}] for r in ROLES}}
    endpoint=root/'bridge.sock'
    specs=user_specs(root,execution,plan,contracts,endpoint,cap=8)
    users=prepared_users(root,specs)
    stop=threading.Event()
    backend=ReleaseBackend(root,users,stop=stop)
    services,executions={},{}
    for role,user in users.items():
        admission=user.owner.admit(role+'.run',logical_tau=0,command_id='preflight:admit')
        assert admission is not None
        executions[role]=user.owner.start(admission,command_id='preflight:start')
        kernel,repository=user.owner.operation_repository()
        catalog=load_catalog(specs[role].plugin_configuration,factories=FACTORIES)
        managed=ManagedPluginToolCatalog(catalog,specs[role].managed_bindings[role]['tools'],
            admitted_effects=['external_read','external_write'])
        services[role]=ManagedPluginInvocationService(user.owner,kernel,repository,managed)
    calls=[]
    def call(role,name,args):
        service=services[role]; call_id='preflight-'+str(len(calls))
        prepared=service.prepare(name,execution=executions[role],call_id=call_id,arguments=args)
        backend.prepared(users[role],executions[role],call_id,name,args,prepared)
        completed=execute_prepared_invocation(prepared,cancelled=lambda:False)
        result=service.finish(prepared,completed)
        calls.append({'role':role,'name':name,'result':result})
        dump(root/'native-tool-calls.json',calls)
        assert result.get('outcome')=='returned',result
        return result['output']
    checks={}
    with ReleaseServer(endpoint,backend):
        created=call('operations','create_file',{**contracts['operations']['required_file_fields'],'body':'Offline fixture release note; not a formal agent answer.'})
        assert created['status']=='created',created
        checks['ops_actual_write']=True
        discovered=call('qa','discover',{'path':'project'})
        assert discovered['status']=='found'
        checks['relative_index_actual_native_tool']=True
        qa=call('qa','read_note',{'path':'project/release-note'})
        support=call('support','read_note',{'path':'project/release-note'})
        assert qa['token_ref']==support['token_ref'] and qa['version']==support['version']
        checks['same_token_actual_readread_overlap']=True
        try:
            services['qa'].prepare('update_note',execution=executions['qa'],call_id='wrong-role',arguments={})
        except ValueError as exc:
            checks['wrong_role_native_tool_denied']=type(exc).__name__
        else: raise AssertionError('QA acquired undeclared editing tool')
        editargs={'expected_version':qa['version'],'body':'Offline fixture revised note.'}
        busy=call('operations','update_note',editargs)
        assert busy['status']=='busy' and busy['effect']=='not_executed',busy
        checks['actual_native_readwrite_denied']=True
        call('qa','finish_read',{'read_id':qa['read_id'],'findings':'offline read complete'})
        call('support','finish_read',{'read_id':support['read_id'],'findings':'offline read complete'})
        revised=call('operations','update_note',editargs)
        assert revised['status']=='updated',revised
        assert revised['canonical']['slot_ref']==qa['slot_ref'] and revised['canonical']['version']!=qa['version']
        checks['same_mutable_slot_new_version']=True
        stale=call('qa','confirm_release',{'read_id':qa['read_id'],'note_version':revised['canonical']['version']})
        assert stale['status']=='stale',stale
        checks['old_read_not_new_confirmation']=True
        again=call('qa','read_note',{'path':'project/release-note'})
        assert again['body']=='Offline fixture revised note.'
        call('qa','finish_read',{'read_id':again['read_id'],'findings':'actual new version consumed'})
        message=call('operations','handoff',{'recipient':'developer','topic':'fixture','text':'Native source-bearing message.'})
        received=call('developer','await_handoff',{'topic':'fixture'})
        assert received['status']=='delivered' and received['resource_ref']==message['resource_ref'],received
        checks['native_cross_private_handoff_body']=True
    for role,user in users.items():
        # These fixtures exercise native managed operations directly, not a
        # completed AgentLoop. Preserve their active facts and stop honestly.
        user.owner.record_owner_stop(idempotency_key='preflight:owner-stop')
    backend.events.close()
    backend.source.owner.record_owner_stop(idempotency_key='preflight:source-stop')
    checks['fixture_lifecycle']='owner-stopped, no fabricated AgentLoop completion'
    from .viewer_export import capture
    capture(backend.source.owner,root/'viewer-source')
    canonical_source=backend.source.current()
    reader_preflight(root,execution)
    checks['actual_native_registered_file_allow_deny_and_workspace_completion']=True
    checks['official_viewer_projection_export']=True
    dump(root/'PREFLIGHT.json',{'status':'PASS','checks':checks,'model_provider_calls':0,
        'users':{r:{'task_ref':str(u.owner.identity.task_ref),'principal_ref':str(u.owner.principal_ref)} for r,u in users.items()},
        'source':canonical_source,'actual_tool_calls':len(calls)})
    print('PREFLIGHT PASS',json.dumps(checks),flush=True)


def reader_preflight(root,execution):
    base=json.loads(Path(execution).read_text())
    adapter={'schema_version':'local_process_adapter_config/v1','adapter_kind':'local_process',
        'model_condition':'mup-native-reader-fixture-v1',
        'argv':['{python}',str(Path(__file__).parent/'tests/reader_fixture.py')],
        'probe_argv':['{python}','--version'],
        'env':{'MUP_FORBIDDEN_READ_PATH':str(root/'hidden-evaluator-fixture.txt')},
        'inherit_env':['PATH','HOME','TMPDIR','PYTHONDONTWRITEBYTECODE']}
    (root/'hidden-evaluator-fixture.txt').write_text('Synthetic evaluation-side marker; not an admitted agent input.\n')
    dump(root/'reader-adapter.json',adapter)
    base.update(model_condition=adapter['model_condition'],adapter_config_path=str(root/'reader-adapter.json'))
    dump(root/'reader-selection.json',base)
    reader_spec=AgentTaskSpec(run_dir=root/'reader-fixture',prompt='NATIVE_READER_FIXTURE_INPUT',
        stages=(AgentStage('reader','Read and validate the exact registered request; synthetic reader fixture only.'),),
        execution_config_path=root/'reader-selection.json',max_attempts_per_stage=8,aggregate_call_cap=8)
    reader=create_user(reader_spec,workspace_root=root,source_id='mup-native-reader-fixture',owner_name='Synthetic reader preflight')
    reader_result=run_user(reader,reader_spec)
    dump(root/'reader-result.json',reader_result)
    assert reader_result['stop_reason']=='terminal' and json.loads(reader_result['output'])['fixture_only'],reader_result
    from .viewer_export import capture
    capture(reader.owner,root/'viewer-reader')
    dump(root/'READER_PREFLIGHT.json',{'status':'PASS','native_registered_input_read':True,'forbidden_grader_path_denied':True,'native_workspace_completion':True,'official_viewer_export':True,'paid_model_calls':0})
    return reader_result

def resume_designer(spec, root):
    """Use the native committed-checkpoint reentry for this same failed design Task.

    This does not assert that the prior provider submission was never submitted.
    The native classifier retains that unknown attempt and creates a new generation.
    """
    from cpn.llm_adapters import load_llm_execution_selection
    from cpn.rpnh.agent_tasks import agent_task_catalog, agent_task_registration, _execution_route
    from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
    from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
    from cpn.rpnh.run import resume_run
    from cpn.frontend.dashboard import RegistryDashboard, exact
    from cpn.rpnh.collaboration.sources import get_local_source_identity
    from .user_runtime import IndependentUser
    selection=load_llm_execution_selection(spec.execution_config_path)
    catalog=agent_task_catalog()
    schemas,types,paths=user_collaboration_schema_data()
    for key,schema in schemas.items(): catalog.register_schema(key,schema,source_path=paths.get(key))
    for definition in types: catalog.register_type(definition)
    registration=agent_task_registration()
    frame=RegistryDashboard(spec.run_dir,catalog=catalog).dashboard()
    checkpoint=exact(frame['net']['marking']['checkpoint_ref'])['version_id']
    bindings=make_optional_agent_host_bindings(selection.input_target,
        workspace_policy=selection.runtime_policy.workspace,
        provider_backend_config=_execution_route(selection),
        transport_contract={'interaction_protocol_ref':'llm_request_envelope/v1',
                            'response_adapter_ref':'llm_response_envelope/v1'},
        managed_catalogs={},managed_tool_policy=None,tool_program_policy=None)
    owner=resume_run(registration,run_dir=spec.run_dir,
        model_condition=selection.input_target.model_condition,catalog=catalog,
        host_execution_bindings=bindings,checkpoint_version_id=checkpoint,
        reopen_command_id='mup-design-provider-recovery',
        reopen_reason='Continue same formal design Task after authorized local provider-state configuration repair; retain original submission unknown')
    identity=get_local_source_identity(owner._core)
    host=UserCollaborationHost(owner._core,owner.schema_gateway)
    session=host.create_session(origin_core=owner._core,principal_ref=owner.principal_ref,
        command_id='mup-design-recovered-owner-session')
    dump(root/'design-reentry.json',{'task_ref':str(owner.identity.task_ref),
        'principal_ref':str(owner.principal_ref),'checkpoint_version_id':checkpoint,
        'old_provider_attempt':'submission_unknown retained','new_generation':True})
    return IndependentUser(owner,selection,identity.source_id,'design.run',False,host,session)


def formal(root,execution,task,*,resume_design=False,continue_business=False):
    contracts=actor_contracts(task)
    started=datetime.now(timezone.utc).isoformat()
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    prompt=designer_prompt(contracts,plugin.DESCRIPTIONS)
    if resume_design or continue_business:
        if (root/'users').exists() or (root/'world').exists():
            raise ValueError('Design-only continuation requires no initialized business users/world')
        original=json.loads((root/'design-input.json').read_text())
        if original['prompt']!=prompt:
            raise ValueError('Same-Task design recovery must preserve original task instructions')
        started=original['at']
    else:
        dump(root/'design-input.json',{'prompt':prompt,'at':started})
    spec=AgentTaskSpec(run_dir=root/'designer',prompt=prompt,
        stages=(AgentStage('design','Generate the actual role stages using the supplied JSON contract. '
            'Write only JSON to the declared result output and complete_interaction.'),),
        execution_config_path=execution,max_attempts_per_stage=48,aggregate_call_cap=48,
        owner_statement='Authorized first design call of the single formal release episode')
    if continue_business:
        design_result=json.loads((root/'design-result-resumed.json').read_text())
        if design_result['stop_reason']!='terminal' or not design_result['terminal_result_ref']:
            raise ValueError('Business continuation requires actual terminal design evidence')
    else:
        designer=(resume_designer(spec,root) if resume_design else create_user(spec,workspace_root=root,source_id='mup-'+root.name+'-designer',owner_name='Release design agent'))
        design_result=run_user(designer,spec,interruption_requested=stop.is_set)
        dump(root/('design-result-resumed.json' if resume_design else 'design-result.json'),design_result)
    if design_result['stop_reason']!='terminal' or not design_result['output']:
        dump(root/'RESULT.json',{'status':'partial','phase':'actual_design','design_result':design_result,'started':started})
        return 2
    plan=parse_design(design_result['output'])
    dump(root/'adopted-design.json',plan)
    endpoint=root/'bridge.sock'
    specs=user_specs(root,execution,plan,contracts,endpoint)
    users=prepared_users(root,specs)
    backend=ReleaseBackend(root,users,stop=stop)
    dump(root/'design-adoption.json',{'design_resource':design_result['terminal_result_ref'],
        'roles':{r:{'stages':[s['stage_id'] for s in plan['roles'][r]],
            'compiled_private_graph':None if specs[r].workflow_graph is None else specs[r].workflow_graph.to_dict(),
            'task_ref':str(u.owner.identity.task_ref),'principal_ref':str(u.owner.principal_ref)} for r,u in users.items()},
        'source':backend.source.current(),'source_net':'trusted generic resource access machinery, not designer output'})
    from .viewer_export import capture
    for role,user in users.items():
        capture(user.owner,root/'viewer'/role/'initial')
    capture(backend.source.owner,root/'viewer'/'source'/'initial')
    results={}
    with ReleaseServer(endpoint,backend), ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(run_user,users[r],specs[r],factories=FACTORIES,
            on_prepare=backend.prepared,interruption_requested=stop.is_set):r for r in ROLES}
        for job in as_completed(jobs):
            role=jobs[job]
            try: result=job.result()
            except Exception as exc:
                stop.set();result={'error_type':type(exc).__name__,'error':str(exc)}
            if result.get('stop_reason')!='terminal':
                stop.set()
            results[role]=result
            dump(root/(role+'-result.json'),result)
            print('ROLE_RESULT',role,result.get('stop_reason',result.get('error_type')),flush=True)
    backend.events.close()
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.registry.module_execution import active_module_firings
    core=backend.source.owner._core
    executable,_,marking=hydrate_module_runtime(core)
    live=[t for t in marking.tokens if t.state.epoch==marking.epoch and t.state.consumed_by is None]
    source_quiescence={
        'active_firings':len(active_module_firings(core,executable.net_ref)),
        'pending_requests':sum(t.state.place.startswith('source.request') or t.state.place=='source.write_request' for t in live),
        'lease_claims':sum(len(t.state.lease_claims) for t in live)}
    source_quiescence['quiescent']=not any(source_quiescence.values())
    current=backend.source.current()
    complete=all(r.get('stop_reason')=='terminal' for r in results.values()) and len(results)==4 and not stop.is_set()
    consistent=all(backend.confirmations.get(r,{}).get('note_version')==current['version'] for r in ('qa','support'))
    complete=complete and consistent and not backend.reads and backend.pending_edit is None and source_quiescence['quiescent']
    if complete:
        backend.source.owner.record_owner_stop(idempotency_key='formal:source-stop')
    for role,user in users.items():
        capture(user.owner,root/'viewer'/role/'final')
    capture(backend.source.owner,root/'viewer'/'source'/'final')
    dump(root/'world-final.json',backend.world.snapshot())
    dump(root/'RESULT.json',{'status':'complete' if complete else 'partial',
        'started':started,'finished':datetime.now(timezone.utc).isoformat(),'roles':results,
        'current_note':current,'confirmations':backend.confirmations,
        'same_world_consistent':consistent,'active_reads':list(backend.reads),
        'source_pending_edit':backend.pending_edit is not None,'manual_hold':stop.is_set(),
        'source_quiescence':source_quiescence,'source_owner_stopped':complete,
        'actual_original_business_evaluation':'NOT_RUN','official_judge':'NOT_RUN',
        'official_score':'NOT_COMPARABLE_ADAPTED'})
    return 0 if complete else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execution',type=Path,required=True)
    parser.add_argument('--task',type=Path,required=True)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--reader-preflight',action='store_true')
    parser.add_argument('--continue-business',action='store_true',help='Adopt the existing terminal design after local startup repair; no model redesign')
    parser.add_argument('--resume-design',action='store_true',help='Native same-Task checkpoint reentry; requires separate provider repair authority')
    parser.add_argument('--workspace-root',type=Path,default=Path.cwd())
    args=parser.parse_args()
    root=args.output.absolute()
    boundary=args.workspace_root.resolve()
    if root.resolve()!=root or not root.is_relative_to(boundary) or root==boundary:
        raise ValueError('ordinary task-owned output directory required')
    if args.resume_design or args.continue_business:
        if (args.resume_design and args.continue_business) or args.preflight or args.reader_preflight or not (root/'designer').is_dir():
            raise ValueError('resume-design requires the existing design Task only')
    else:
        if root.exists(): raise ValueError('fresh output directory required')
        root.mkdir(parents=True)
    task=json.loads(args.task.read_text())
    if args.reader_preflight:
        reader_preflight(root,args.execution)
        return 0
    if args.preflight:
        preflight(root,args.execution,task)
        return 0
    return formal(root,args.execution,task,resume_design=args.resume_design,continue_business=args.continue_business)


if __name__=='__main__':
    raise SystemExit(main())
