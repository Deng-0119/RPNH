from pathlib import Path
root=Path('rpnh-parent-child-core-implementation/source/cpn/rpnh/registry')
p=root/'parent_child.py';t=p.read_text();needle="    if not intercepted:return\n";assert needle in t;t=t.replace(needle,'''    # Native observation/completion is outside this slice; do not release a
    # parent slot via an unrelated generic Success before that adapter exists.
    for event in events:
        if event.event_type == 'transition_firing_settled/v1':
            firing_ref=event.payload['transition_firing_ref']
            check=_Snapshot(store,db,task_id,firing=firing_ref['version_id'],proposal=objects)
            firing=check.obj(firing_ref)
            binding=check.obj(firing['operation_binding_ref'])
            spec=check.obj(binding['operation_spec_ref'])
            if spec.get('executor_key')==NATIVE_LAUNCH_EXECUTOR:
                raise ParentChildUnsupported('H7 native child observation/completion adapter is not wired')
    if not intercepted:return
''')
needle="    item=selected[0];kind=item.object_type\n";t=t.replace(needle,needle+"    from .parent_bound import _bound_signal\n    if _bound_signal(db):raise ParentChildUnsupported('H7a bound child cannot recursively launch another child')\n")
t=t.replace("                    or body['public_material_digest']!=intent['materials']['public_material_digest']):", "                    or body['public_material_digest']!=intent['materials']['public_material_digest']\n                    or body['request_digest']!=digest(_json(bootstrap_request_material(intent,body['dispatch_ref'])))):")
t+='''
def bootstrap_request_material(intent,dispatch_ref):
    """Pure exact request material, with no peer identity or permission."""
    return {'protocol':'rpnh/parent-child-bootstrap/v1','parent':intent['parent'],
        'slot_id':intent['slot_id'],'intent_ref':intent['record_ref'],'dispatch_ref':dispatch_ref,
        'execution':intent['execution'],'target':intent['target'],
        'envelope_digest':intent['envelope_digest'],'public_material_digest':intent['materials']['public_material_digest']}
''';p.write_text(t)
p=root/'parent_bound.py';t=p.read_text();needle="    _,marker=_marker(snap)\n    if marker['initial_writer_epoch']!=writer_epoch:\n";assert needle in t;t=t.replace(needle,"    _,marker=_marker(snap)\n    if any(o.object_type in ('native_run_identity/v1','native_genesis_manifest/v1','bootstrap_command/v1') for o in objects):\n        raise RegistryConflict('H7 bound native genesis cannot be relabelled or replayed')\n    if marker['initial_writer_epoch']!=writer_epoch:\n",1)
needle="    compiled=load_compiled_net(json.loads(raw));pn=compiled.symbolic\n";t=t.replace(needle,needle+"    from .parent_child import NATIVE_LAUNCH_EXECUTOR\n    if any(op.executor==NATIVE_LAUNCH_EXECUTOR for op in pn.operations):\n        raise ParentChildUnsupported('H7a child does not support recursive native launch')\n")
# prospective origin validates the exact initial catalog source too.
needle="    body=dict(origins[0].metadata);cap=capabilities[0]\n"
t=t.replace(needle,needle+"""    genesis=snap.obj(marker['genesis_ref'])
    if cap.metadata.get('content_schema_authority_ref')!=genesis['type_catalog_ref']:
        raise RegistryConflict('H7 prospective capability lacks original native catalog authority')
    catalog=snap.obj(genesis['type_catalog_ref'])
    from .schema_catalog import SchemaCatalog
    if (catalog.get('catalog_identity')!='rpnh-v1' or
            json.loads(catalog['schemas'][CAPABILITY_SCHEMA]['source']) !=
                json.loads(SchemaCatalog._mechanical_schema_path(CAPABILITY_SCHEMA).read_bytes())):
        raise RegistryConflict('H7 prospective capability catalog schema differs')
""")
p.write_text(t)
p=Path('rpnh-parent-child-core-implementation/source/tests/parent_child_fixtures.py');t=p.read_text().replace("    acceptance=phase(owner,h7.PARENT_KINDS[3],worker,{'request_digest':'b'*64})", "    intent_body=owner._core.get_version(intent.version_id).metadata\n    acceptance=phase(owner,h7.PARENT_KINDS[3],worker,{'request_digest':h7.digest(h7._json(h7.bootstrap_request_material(intent_body,_ref_payload(dispatch))))})");p.write_text(t)
