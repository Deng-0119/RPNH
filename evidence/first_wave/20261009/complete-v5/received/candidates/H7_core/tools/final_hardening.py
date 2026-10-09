from pathlib import Path
root=Path('rpnh-parent-child-core-implementation/source/cpn/rpnh/registry')
p=root/'parent_child.py';t=p.read_text();needle="""    prior=db.execute('SELECT transaction_id FROM objects WHERE version_id=?',(str(item.version_id),)).fetchone()
""";insert="""    record=records[0]
    if (record.stream_id!='h7:'+str(item.logical_id) or record.aggregate_id!=str(item.logical_id)
            or record.aggregate_type!=kind or record.idempotency_key!=idempotency_key
            or record.command_id!=idempotency_key or record.criticality!='authoritative'
            or record.payload_schema_ref!='registry_v1/parent_child_recorded/v1'
            or record.task_control or record.producer_principal!='framework'
            or record.causation_event_id is not None or record.parent_event_ids or record.occurred_at is not None):
        raise RegistryConflict('H7 record event identity/producer contract differs')
    commits=[e for e in events if e.event_type=='transaction_committed/v1']
    publications=[e for e in events if e.event_type=='object_version_published/v1']
    if (len(commits)!=1 or len(publications)!=1 or commits[0].producer_invocation_id is not None
            or commits[0].payload!={'object_count':1,'relation_count':len(relations),'fact_count':len(events)-1}
            or publications[0].producer_invocation_id!=item.producer_invocation_id
            or publications[0].payload.get('metadata')!=body
            or publications[0].payload.get('version_id')!=str(item.version_id)):
        raise RegistryConflict('H7 publication/commit envelope differs from its exact producer')
"""
assert needle in t;t=t.replace(needle,insert+needle);p.write_text(t)
p=root/'parent_bound.py';t=p.read_text();needle="        db.row_factory=sqlite3.Row\n        if _bound_signal(db):";t=t.replace(needle,"        db.row_factory=sqlite3.Row\n        if db.execute(\"SELECT 1 FROM sqlite_master WHERE type='table' AND name='objects'\").fetchone() is None:\n            return  # Preserve ordinary fresh empty-store initialization.\n        if _bound_signal(db):");p.write_text(t)
p=Path('rpnh-parent-child-core-implementation/source/tests/test_parent_child_core.py');t=p.read_text();t=t.replace('from cpn.rpnh.registry.event_store import RegistryConflict','from cpn.rpnh.registry.event_store import RegistryConflict\nfrom cpn.rpnh.registry.errors import StaleInvocationContext')
t=t.replace("with pytest.raises(RegistryConflict,match='stopped'):","with pytest.raises(StaleInvocationContext,match='stopped'):")
p.write_text(t)
