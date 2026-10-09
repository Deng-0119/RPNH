from pathlib import Path
p=Path('rpnh-parent-child-core-implementation/source/cpn/rpnh/registry/parent_bound.py');t=p.read_text()
needle="    snap=_Snapshot(store,db,task_id,proposal=objects)\n    markers="
insert="""    snap=_Snapshot(store,db,task_id,proposal=objects)
    if (any(o.producer_invocation_id is not None for o in objects)
            or any(e.producer_invocation_id is not None or e.producer_principal!='framework' or e.task_control
                   or e.event_type not in ('object_version_published/v1','relation_published/v1','transaction_committed/v1')
                   for e in events)
            or len(events)!=len(objects)+len(relations)+1
            or any(r.strength!='strong' or not r.system_owned or r.producer_invocation_id is not None or r.metadata
                   for r in relations)):
        raise RegistryConflict('H7 protected bootstrap/origin requires exact strong system-owned transaction closure')
    markers="""
assert needle in t;t=t.replace(needle,insert)
needle="        _require_evidence(store,'bound_bootstrap',body['acceptance'])\n"
t=t.replace(needle,"""        expected_marker=_ref(BOUND_KINDS[0],body['child_run_ref']['version_id'])
        if body['record_ref']!=_ref_payload(expected_marker):
            raise RegistryConflict('H7 marker self identity is not its unique native bootstrap identity')
"""+needle)
needle="    body=dict(origins[0].metadata);cap=capabilities[0]\n";t=t.replace(needle,needle+"""    actual_origin=_ref_payload(VersionRef(origins[0].object_type,origins[0].logical_id,origins[0].version_id))
    if body['record_ref']!=actual_origin or actual_origin!=_ref_payload(_ref(BOUND_KINDS[1],marker['record_ref']['version_id'])):
        raise RegistryConflict('H7 origin self identity differs from its unique marker')
""")
needle="    place=places[0]\n";t=t.replace(needle,needle+"""    if any(p.place==place.name for p in compiled.ports):
        raise RegistryConflict('H7 origin is an internal structural place, not a public operation port')
""")
needle="        for item in objects:\n            if item.object_type=='petri_token/v1' and item.metadata.get('lease_identity_ref')==cap and protected:\n"
insert="""        origin_resource={'resource_id':cap['logical_id'],'resource_version_id':cap['version_id']}
        proposed_origin=[]
        for item in objects:
            if item.object_type=='petri_token/v1':
                token=item.metadata
                # Check the adopted/frozen native origin place as well as refs;
                # a caller cannot evade protection by changing lease_identity.
                place=_check_bound_net(snap,token['net_instance_ref'],marker,cap)
                if (token['place']==place or token.get('lease_identity_ref')==cap
                        or token.get('resource_ref')==origin_resource):
                    proposed_origin.append(token)
                    if (token['place']!=place or token['resource_ref']!=origin_resource
                            or token['lease_identity_ref']!=cap or token['epoch']!=0
                            or token['consumed_by'] is not None or token['producer'] is not None):
                        raise RegistryConflict('H7 prospective origin token lacks exact native provenance')
            if item.object_type=='petri_token/v1' and item.metadata.get('lease_identity_ref')==cap and protected:
"""
assert needle in t;t=t.replace(needle,insert)
needle="                raise RegistryConflict('H7 checkpoint cannot drop protected origin reference')\n"
t=t.replace(needle,needle+"""        if len(proposed_origin)>1:
            raise RegistryConflict('H7 M0 cannot duplicate the protected origin token')
""")
p.write_text(t)
p=Path('rpnh-parent-child-core-implementation/source/tests/test_parent_child_core.py');t=p.read_text().replace('def test_bound_completion_registered_then_original_success_recovery','def test_bound_registered_completion_then_one_success_without_remint')
t=t.replace("    second=child.succeed(outputs,command_id='success')\n    assert first==second", "    with pytest.raises(StaleInvocationContext):\n        child.succeed(outputs,command_id='success')")
p.write_text(t)
