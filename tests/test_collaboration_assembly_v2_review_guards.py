"""Regression probes for the two first-vertical static review conditions."""
import json
import pytest
from cpn.rpnh.collaboration.assembly_v2 import ASSEMBLY_V2_TYPE
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from test_collaboration_assembly_v2_publication import fixture, request, counts


def damage_host_metadata(core,reference,*,schema=None,authority=None):
    with core.event_store.connect() as db:
        row=db.execute('SELECT * FROM objects WHERE version_id=?',(str(reference.resource_version_id),)).fetchone()
        metadata=json.loads(row['metadata_json'])
        metadata['content_schema_ref']=schema
        metadata['content_schema_authority_ref']=authority
        publication=metadata['reference_provenance']['publication']
        publication['content_schema_ref']=schema
        publication['content_schema_authority_ref']=authority
        event=db.execute('SELECT payload_json FROM events WHERE event_id=?',(row['published_event_id'],)).fetchone()
        payload=json.loads(event[0]);payload['metadata']=metadata
        db.execute('UPDATE objects SET metadata_json=? WHERE version_id=?',(json.dumps(metadata),str(reference.resource_version_id)))
        db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(payload),row['published_event_id']))


def test_parent_assembly_source_closure_is_fresh_at_final_cut(fixture,monkeypatch):
    core,_,author,_,member=fixture
    parent=author.publish(**request(member))
    original=author._publish_document
    def publish(key,schema,document,**kwargs):
        result=original(key,schema,document,**kwargs)
        if key.endswith(':lowering'):
            path=core.object_store.path_for_version(parent.revision.plan_ref.ref.resource_version_id)
            value=path.read_bytes();changed=value.replace(b'Same',b'Evil',1)
            assert changed!=value and len(changed)==len(value)
            path.write_bytes(changed)
        return result
    monkeypatch.setattr(author,'_publish_document',publish)
    with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
        author.publish(**request(member,command_id='child',parent_ref=parent.revision.revision_ref))
    assert len(core.event_store.object_rows_by_type(ASSEMBLY_V2_TYPE))==1, 'child descriptor must not commit before detecting broken parent proof'


def test_host_schema_authority_missing_is_rejected_before_first_plan(fixture):
    core,gateway,author,_,member=fixture
    reference=gateway.declaration_refs['schema','application/rpnh_agent_text/v1']
    damage_host_metadata(core,reference,schema='registry_v1/registry_type_catalog/v1',authority=None)
    before=counts(core)
    with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
        author.publish(**request(member))
    assert counts(core)==before
    assert core.event_store.object_rows_by_type(ASSEMBLY_V2_TYPE)==()


@pytest.mark.parametrize('damage',('wrong_schema','wrong_authority','untyped_authority'))
def test_host_metadata_contract_is_locally_exact(fixture,damage):
    core,gateway,author,_,member=fixture
    if damage=='untyped_authority':
        reference=gateway.declaration_refs['executor','test/ordinary-agent/v1']
        schema=None
    else:
        reference=gateway.declaration_refs['schema','application/rpnh_agent_text/v1']
        schema='registry_v1/principal/v1' if damage=='wrong_schema' else 'registry_v1/registry_type_catalog/v1'
    damage_host_metadata(core,reference,schema=schema,authority=author.producer.to_dict()['ref'])
    before=counts(core)
    with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
        author.publish(**request(member))
    assert counts(core)==before


def test_host_frozen_catalog_entry_bytes_must_match_the_selected_contract(fixture):
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.schema_catalog import canonical_json
    core,_,author,_,member=fixture
    row=core.event_store.object_rows_by_type('registry_type_catalog/v1')[0]
    version=TypedId.parse(row['version_id']);path=core.object_store.path_for_version(version)
    body=json.loads(path.read_bytes());entry=body['schemas']['registry_v1/registry_type_catalog/v1']
    changed=entry['source'].replace('catalog_identity','catalog_identitx')
    assert changed!=entry['source'];entry['source']=changed
    payload=canonical_json(body);path.write_bytes(payload)
    with core.event_store.connect() as db:
        event=db.execute('SELECT payload_json FROM events WHERE event_id=?',(row['published_event_id'],)).fetchone()
        publication=json.loads(event[0]);publication['metadata']=body;publication['size']=len(payload)
        db.execute('UPDATE objects SET metadata_json=?,size=? WHERE version_id=?',(json.dumps(body),len(payload),row['version_id']))
        db.execute('UPDATE events SET payload_json=? WHERE event_id=?',(json.dumps(publication),row['published_event_id']))
    before=counts(core)
    with pytest.raises((ValueError,RegistryConflict,ObjectIntegrityError)):
        author.publish(**request(member))
    assert counts(core)==before


def test_builtin_schema_and_untyped_host_keep_their_actual_legal_authority(fixture):
    from cpn.rpnh.collaboration import GraphModuleAuthor,make_graph_source
    from test_collaboration_graph_source import graph_wire,recipe,source_ids
    core,gateway,author,selected,_=fixture
    source=make_graph_source(graph_wire())
    member=GraphModuleAuthor(gateway,selected,author.producer).publish(source=source,
        recipe=recipe(required_schemas=('application/graph_test_config/v1','rpnh/module_declaration/v1')),
        source_ids=source_ids(source),command_id='builtin-member')
    value=author.publish(**request(member))
    schemas=value.plan['host_requirements']['registrations']['schema']
    assert 'rpnh/module_declaration/v1' in schemas
    ref=gateway.declaration_refs['schema','rpnh/module_declaration/v1']
    metadata=json.loads(core.event_store.object_row(ref.resource_version_id)['metadata_json'])
    assert metadata['descriptors']['mechanical_schema'] is True
    assert metadata['content_schema_authority_ref']['entity_type']=='registry_type_catalog/v1'
    executor=gateway.declaration_refs['executor','test/ordinary-agent/v1']
    metadata=json.loads(core.event_store.object_row(executor.resource_version_id)['metadata_json'])
    assert metadata['content_schema_ref'] is metadata['content_schema_authority_ref'] is None
