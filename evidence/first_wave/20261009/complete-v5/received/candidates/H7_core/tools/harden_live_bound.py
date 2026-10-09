from pathlib import Path
root=Path('rpnh-parent-child-core-implementation/source/cpn/rpnh/registry')
p=root/'parent_bound.py';t=p.read_text();t=t.replace('require_origin=True,require_net=True,claim=None):','require_origin=True,require_net=True,claim=None,for_execution=False):')
needle="    if not require_origin:return marker\n";t=t.replace(needle,'''    if for_execution:
        authority_ref,authority=snap.canonical_latest('run_execution_authority/v1')
        if (authority['run_ref']!=marker['child_run_ref'] or authority['task_ref']!=marker['child_task_ref']
                or authority.get('execution_generation',0)!=0 or authority['status']!='running'
                or authority.get('terminal_evidence_ref') is not None):
            raise RegistryConflict('H7 bound execution authority is stopped, terminal, or foreign')
    if not require_origin:return marker
''')
t=t.replace('def assert_bound_integrity(core,*,claim=None,require_net=True):','def assert_bound_integrity(core,*,claim=None,require_net=True,for_execution=False):').replace('require_net=require_net,claim=claim)','require_net=require_net,claim=claim,for_execution=for_execution)')
t=t.replace("claim={**firing,'consumed_input_refs':delta['consumed_refs']})","claim={**firing,'consumed_input_refs':delta['consumed_refs']},for_execution=True)")
p.write_text(t)
for name in ['_invocation/execution.py','module_execution.py']:
 p=root/name;t=p.read_text().replace('assert_bound_integrity(lifecycle.service)','assert_bound_integrity(lifecycle.service, for_execution=True)').replace('assert_bound_integrity(core)','assert_bound_integrity(core, for_execution=True)');p.write_text(t)
p=root/'_invocation/admission.py';t=p.read_text().replace('assert_bound_integrity(lifecycle.service, claim={','assert_bound_integrity(lifecycle.service, for_execution=True, claim={');p.write_text(t)
