"""Bounded cross-page logical-object history on original canonical Registry."""
import json
import pytest
from cpn.frontend.local_registry_browser import LocalRegistryBrowserProvider
from cpn.frontend.registry_browser import parse_query, RegistryBrowserError, RegistryBrowserProvider
from cpn.rpnh.registry.identities import new_id
from test_local_registry_browser import make_run, publish, listing, request
from test_registry_read_session import counts


def revision(world, original, number):
    # Real schema-valid index revisions; no inference that they are adopted.
    from cpn.rpnh.registry.schema_catalog import canonical_json
    core=world[0];row=core.event_store.canonical_object_row(original.version_id)
    metadata=json.loads(row['metadata_json']);version=new_id('terminal_evidence_version')
    metadata['final_result_index_ref']['version_id']=str(version)
    core.publish_bytes(object_type='final_result_index/v1',logical_id=original.entity_id,
        version_id=version,payload=canonical_json(metadata),metadata=metadata,media_type=row['media_type'],
        schema_ref=row['schema_ref'],idempotency_key='revision-'+str(number))
    return version


@pytest.mark.parametrize('bound',[False,True])
def test_versions_cross_pages_exact_scope_and_stable_cut(tmp_path,bound):
    world=make_run(tmp_path/'run',bound=bound)
    from test_registry_navigation import index
    resource=publish(world,b'body');first=index(world,[resource])
    versions=[str(first.version_id)]+[str(revision(world,first,i)) for i in range(29)]
    index(world,[resource])
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    assert request(provider,'catalog')[1]['sources'][0]['version_query'] is True
    _,page=request(provider,'list',source_id=provider.source_id,entry_type='final_result_index/v1');anchor=page['entries'][0]['handle'];cut=page['source_cut']
    # A later version is outside the anchored observation.
    revision(world,first,30);before=counts(world)
    status,v1=request(provider,'versions',handle=anchor)
    assert status==200 and len(v1['entries'])==25 and v1['source_cut']==cut
    assert v1['version_scope']['selects_current'] is False
    status,v2=request(provider,'versions',handle=anchor,cursor=v1['next_cursor'])
    assert status==200 and len(v2['entries'])==5 and v2['next_cursor'] is None
    refs=[e['entry_ref']['ref'] for e in v1['entries']+v2['entries']]
    assert [r.get('version_id',r.get('resource_version_id')) for r in refs]==versions
    assert all(r.get('logical_id',r.get('resource_id'))==str(first.entity_id) for r in refs)
    assert request(provider,'versions',handle=anchor,cursor=v1['page_cursor'])[1]==v1
    assert request(provider,'record',handle=v2['entries'][-1]['handle'])[1]['fields']['final_result_index_ref']['version_id']==versions[-1]
    assert counts(world)==before


@pytest.mark.parametrize('direction',['list_into_versions','versions_into_list','other_anchor'])
def test_cursor_scope_cannot_change(tmp_path,direction):
    world=make_run(tmp_path/'run');publish(world);publish(world,b'other','other')
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    _,page=listing(provider);a,b=[e['handle'] for e in page['entries']]
    vp=request(provider,'versions',handle=a)[1]
    if direction=='list_into_versions':
        result=request(provider,'versions',handle=a,cursor=page['page_cursor'])
    elif direction=='versions_into_list':
        result=request(provider,'list',source_id=provider.source_id,entry_type='resource_version/v1',cursor=vp['page_cursor'])
    else:result=request(provider,'versions',handle=b,cursor=vp['page_cursor'])
    assert result[0]==409 and not provider._items and not provider._pages


def test_versions_parser_has_no_arbitrary_identity_or_source():
    assert parse_query('action=versions&handle=opaque&cursor=page')=={'action':'versions','handle':'opaque','cursor':'page'}
    for extra in ('&logical_id=other','&source_id=other','&handle=duplicate'):
        with pytest.raises(RegistryBrowserError):parse_query('action=versions&handle=opaque'+extra)


def test_unknown_anchor_and_shared_provider_do_not_extend_scope(tmp_path):
    world=make_run(tmp_path/'run')
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    assert request(provider,'versions',handle='unknown')[0]==409
    class Session:
        def describe(self):return {}
    shared=RegistryBrowserProvider(Session())
    assert request(shared,'versions',handle='unknown')[0]==400

