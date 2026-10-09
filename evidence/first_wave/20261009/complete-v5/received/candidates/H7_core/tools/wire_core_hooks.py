from pathlib import Path
root=Path(__file__).resolve().parents[1]/'source/cpn/rpnh'
def edit(p,old,new):
 p=root/p;txt=p.read_text();assert old in txt,(p,old);p.write_text(txt.replace(old,new,1))
edit(Path('registry/parent_child.py'),'SchemaCatalog().validate(REQUEST_SCHEMA, request)','SchemaCatalog().validate_schema_ref(REQUEST_SCHEMA, request)')
p=root/'registry/parent_child.py';t=p.read_text().replace('source_version_id=? AND target_version_id=?',"json_extract(source_json,'$.version_id')=? AND json_extract(target_json,'$.version_id')=?").replace('SELECT r.target_version_id FROM relations r JOIN objects o ON o.version_id=r.target_version_id',"SELECT json_extract(r.target_json,'$.version_id') FROM relations r JOIN objects o ON o.version_id=json_extract(r.target_json,'$.version_id')").replace('r.source_version_id=?',"json_extract(r.source_json,'$.version_id')=?")
t=t.replace("or any(e.event_type=='parent_child_recorded/v1'", "or any(o.object_type=='native_run_identity/v1' and BOUND_PROTOCOL in o.metadata.get('protocol_versions',()) for o in objects)\n        or any(e.event_type=='parent_child_recorded/v1'")
p.write_text(t)
edit(Path('registry/_event_store/commit.py'),'        if existing is not None:\n            if existing["status"]', '''        from ..parent_child import validate_parent_child_commit
        validate_parent_child_commit(self_store := event_store, db,
            task_id=task_id, branch_id=branch_id, task_round_id=task_round_id,
            net_instance_id=net_instance_id, transaction_id=transaction_id,
            idempotency_key=idempotency_key, writer_epoch=writer_epoch,
            objects=objects, events=events, relations=relations, existing=existing,
            extra_commands=bool(snapshot_predecessors or dependency_root_predecessor
                or publication_commands or workspace_head_commands))
        if existing is not None:
            if existing["status"]''')
edit(Path('registry/_registry.py'),'        if create:\n            if database_path.exists():','''        if not self.read_only:
            from .parent_bound import preflight_bound_writer
            preflight_bound_writer(database_path)
        if create:
            if database_path.exists():''')
edit(Path('registry/event_store.py'),'        self.read_only = bool(read_only)\n        if self.read_only:', '''        self.read_only = bool(read_only)
        if not self.read_only:
            from .parent_bound import preflight_bound_writer
            preflight_bound_writer(self.path)
        if self.read_only:''')
p=root/'registry/_event_store/backend.py';t=p.read_text()
for name in ('rotate_writer','acquire_writer'):
 start=t.index('def '+name+'(');end=t.find('\ndef ',start+4)
 chunk=t[start:end];needle='        db.execute("BEGIN IMMEDIATE")';assert needle in chunk
 chunk=chunk.replace(needle,needle+'\n        from ..parent_bound import reject_bound_writer_at\n        reject_bound_writer_at(db)',1);t=t[:start]+chunk+t[end:]
p.write_text(t)
edit(Path('registry/_invocation/admission.py'),'    caller_idempotency_key = idempotency_key','''    from ..parent_bound import assert_bound_integrity
    assert_bound_integrity(lifecycle.service, claim={
        'net_instance_ref': _ref_payload(claim.net_instance_ref),
        'claimed_input_refs': [_ref_payload(ref) for ref in claim.claimed_input_refs],
        'consumed_input_refs': [_ref_payload(ref) for ref in (
            claim.claimed_input_refs if claim.consumed_input_refs is None else claim.consumed_input_refs)]})
    caller_idempotency_key = idempotency_key''')
edit(Path('registry/_invocation/execution.py'),'    if not isinstance(context, InvocationContext):','''    from ..parent_bound import assert_bound_integrity
    assert_bound_integrity(lifecycle.service)
    if not isinstance(context, InvocationContext):''')
edit(Path('registry/module_execution.py'),'    executable, structure, marking = hydrate_module_runtime(core)', '''    from .parent_bound import assert_bound_integrity
    assert_bound_integrity(core)
    executable, structure, marking = hydrate_module_runtime(core)''')
edit(Path('registry/bootstrap.py'),'    project_identity_objects: tuple[object, ...] = (),','''    project_identity_objects: tuple[object, ...] = (),
    _parent_bound_acceptance: dict | None = None,''')
edit(Path('registry/bootstrap.py'),'    tx.commit()\n    core.event_store.get_or_create_meta', '''    if _parent_bound_acceptance is not None:
        from .parent_bound import stage_bound_bootstrap
        stage_bound_bootstrap(core, tx, run_ref=run_ref, task_ref=task_ref,
            branch_ref=branch_ref, genesis_ref=genesis_ref, bootstrap_ref=bootstrap_ref,
            acceptance=_parent_bound_acceptance)
    tx.commit()
    core.event_store.get_or_create_meta''')
# All explicit reentry helpers reject at entry before their own replay paths.
for file,name in [('run_authority.py','resume_owner_stopped_run'),('run_authority.py','record_recovered_run_entry'),('checkpoint_reentry.py','stage_checkpoint_reentry')]:
 p=root/'registry'/file;t=p.read_text();start=t.index('def '+name+'(');body=t.index('    """',start);end=t.index('"""',body+7)+3
 t=t[:end]+"\n    from .parent_bound import reject_bound_reentry\n    reject_bound_reentry(core)"+t[end:];p.write_text(t)
edit(Path('registry/task_ledger.py'),'        existing = self.service.event_store.list_events_by_idempotency_key(\n            idempotency_key)','''        from .parent_bound import assert_bound_adoption
        assert_bound_adoption(self.service, net_instance_ref, idempotency_key)
        existing = self.service.event_store.list_events_by_idempotency_key(
            idempotency_key)''')
