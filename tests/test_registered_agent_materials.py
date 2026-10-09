from dataclasses import replace
import pytest
from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry.public_materials import read_inventory,validate_draft,read_verified_materials
from registered_agent_material_fixtures import registered_agent_case,public_port

@pytest.fixture(scope='module')
def agent_case(tmp_path_factory):
    path=tmp_path_factory.mktemp('registered-agent-materials')
    patch=pytest.MonkeyPatch()
    case=registered_agent_case(path,patch)
    yield case
    patch.undo()


def test_real_agent_registered_inventory_intent(agent_case):
    c=agent_case
    assert c.inventory.root['child_kind']=='agent_task'
    assert c.inventory.root['opaque_bindings']
    assert c.intent.entity_type==h7.INTENT_V2
    assert read_inventory(c.owner._core,c.inventory.resource_ref)==c.inventory
    assert h7.register_child_intent(c.owner._core,c.execution,c.prepared)==c.intent
    assert not c.setup.loads
    with pytest.raises(h7.ParentChildUnsupported):
        h7._advance_record(c.owner._core,h7.PARENT_KINDS[1],c.intent,details={'bundle_digest':'a'*64},evidence=None)


def test_actual_agent_full_registration_and_policy(agent_case):
    checked=read_verified_materials(agent_case.owner._core,agent_case.inventory.resource_ref)
    docs=checked.documents;root=checked.inventory.root
    full=docs[root['roots']['registration']]['declarations']
    selected=docs[root['roots']['selection']]['selected_registrations']
    assert len(full)>len(selected)
    assert len(root['roots']['execution_policy'])==1
    port,_=public_port(agent_case,agent_case.path/'port')
    assert port.public_identity['policy']['runtime']['context_pressure_trigger_ratio']==0.73
    port.close()


@pytest.mark.parametrize('damage',['binding_revision','account','policy_missing','wrong_default','context','runtime','transport','schema','extra_binding'])
def test_agent_material_semantic_damage(agent_case,damage):
    draft=agent_case.draft;docs={name:w.decode(raw) for name,raw in draft.payloads if next(n for n in draft.inventory if n['node_id']==name)['encoding']!=w.OPAQUE_ENCODING}
    bindings=draft.bindings;roots=draft.roots
    if damage=='binding_revision': bindings[0]['binding_revision']='r2'
    elif damage=='account': bindings[0]['account_binding_id']='account-b'
    elif damage=='policy_missing': docs['policy-default'].pop('runtime')
    elif damage=='wrong_default': docs['payload']['registered_execution_sources']['default']='another-policy'
    elif damage=='context': docs['slot.target']['context_window_tokens']=16384
    elif damage=='runtime': docs['policy-default']['runtime']['context_pressure_trigger_ratio']=0.5
    elif damage=='transport': docs['slot.transport']['extra']=True
    elif damage=='schema': docs['payload']['schema_version']='rpnh/agent_task_spec/v12'
    else: bindings.append({**bindings[0],'binding_id':'binding-extra'})
    payloads=dict(draft.payloads);inventory=draft.inventory
    for node in inventory:
        if node['node_id'] in docs:
            raw=w.canonical(docs[node['node_id']]) if node['encoding']==w.C_ENCODING else __import__('cpn.rpnh.registry.schema_catalog',fromlist=['canonical_json']).canonical_json(docs[node['node_id']])
            payloads[node['node_id']]=raw;node['sha256']=w.sha(raw);node['size']=len(raw)
    changed=replace(draft,payloads=tuple(sorted(payloads.items())),inventory_bytes=w.canonical(inventory),bindings_bytes=w.canonical(bindings))
    with pytest.raises(Exception): validate_draft(changed)


def test_prepare_never_reads_nonempty_private_config(agent_case,monkeypatch):
    from pathlib import Path
    from cpn.rpnh.public_agent_materials import prepare_agent_material_draft
    from cpn.rpnh.registry.schema_catalog import canonical_json
    c=agent_case;calls=[];original=Path.read_bytes
    def guarded(path):
        if path==c.setup.private_config:
            calls.append(path);raise AssertionError('private configuration must remain unread')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',guarded)
    draft=prepare_agent_material_draft(profile_id=c.setup.entrypoint.name,parent=h7.parent_identity(c.owner._core),request_bytes=canonical_json(c.request),
        root_binding='test-root-binding',control_root=str(c.path/'control'))
    assert draft.public_material_digest==c.draft.public_material_digest
    assert calls==[]
    assert b'SYNTHETIC-PRIVATE' not in b''.join(raw for _,raw in draft.payloads)
    assert str(c.setup.private_config).encode() not in b''.join(raw for _,raw in draft.payloads)



def test_actual_credential_callback_source_is_in_registered_observation(agent_case):
    import inspect
    from pathlib import Path
    from registered_agent_credentials import SyntheticCredentialStore
    c=agent_case
    port,_=public_port(c,c.path/'credential-unit-proof')
    try:
        identity=port.public_identity
        observed_path=Path(inspect.getsourcefile(SyntheticCredentialStore)).resolve()
        for role in ('resolver','renderer'):
            capability=getattr(port._port,'_public_'+role)
            assert capability.callback.__self__.__class__ is SyntheticCredentialStore
            assert Path(inspect.getsourcefile(capability.callback.__func__)).resolve()==observed_path
            observation=identity[role]['observation']
            assert observation['unit_id']=='u.credentials'
            assert observation['assets']==[{'asset_id':'credential-code','size':observed_path.stat().st_size,
                'sha256':w.sha(observed_path.read_bytes()),'inline_node_id':None}]
            root_nodes={n['node_id']:n for n in c.inventory.root['content_inventory']}
            assert root_nodes['observation.u.credentials']['sha256']==w.sha(w.canonical(observation))
        root_before=read_inventory(c.owner._core,c.inventory.resource_ref)
        store=port._port._public_resolver.callback.__self__
        store.secret='ROTATED-RUNTIME-SYNTHETIC'
        assert read_inventory(c.owner._core,c.inventory.resource_ref)==root_before
        assert port.public_identity==identity
        assert store.counts=={}
        assert 'ROTATED-RUNTIME-SYNTHETIC' not in observed_path.read_text()
    finally: port.close()
