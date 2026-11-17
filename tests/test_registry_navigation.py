"""Exact same-cut links from declared metadata; no workflows or model calls."""
import json
import pytest
from cpn.frontend.registry_navigation import declared_links
from cpn.frontend.local_registry_browser import LocalRegistryBrowserProvider
from cpn.frontend.registry_browser import RegistryBrowserError
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_local_registry_browser import make_run, publish, listing, request
from test_registry_read_session import counts


def resource_wire(ref):
    return {'resource_id':str(ref.resource_id),'resource_version_id':str(ref.resource_version_id)}


def index(world, refs):
    # Synthetic pointer descriptor, not a certification of a real terminal run.
    core=world[0]
    ref=VersionRef('final_result_index/v1',new_id('terminal_evidence'),new_id('terminal_evidence_version'))
    result=VersionRef('resource_version/v1',refs[0].resource_id,refs[0].resource_version_id)
    body={'final_result_index_ref':_ref_payload(ref),'authority_kind':'test', 'terminal_outcome':'complete',
          'entries':[resource_wire(r) for r in refs], 'upstream_outcome':None,'upstream_disposition_refs':[],
          'terminal_result_ref':_ref_payload(result),
          'terminal_occurrence_ref':_ref_payload(VersionRef('transition_firing/v1',new_id('transition_firing'),new_id('transition_firing_version')))}
    core.publish_bytes(object_type=ref.entity_type,logical_id=ref.entity_id,version_id=ref.version_id,
        payload=canonical_json(body),metadata=body,media_type='application/json',schema_ref='registry_v1/final_result_index/v1',idempotency_key=str(ref.entity_id))
    return ref


def pointer_page(provider):
    return request(provider,'list',source_id=provider.source_id,entry_type='final_result_index/v1')[1]


@pytest.mark.parametrize('bound',[False,True])
def test_exact_target_crosses_pages_without_changing_cut_or_facts(tmp_path,bound):
    world=make_run(tmp_path/'run',bound=bound)
    refs=[publish(world,f'body-{i}'.encode(),f'item-{i}') for i in range(30)]
    index(world,[refs[-1]])
    before=counts(world)
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    _,resources=listing(provider)
    assert len(resources['entries'])==25
    page=pointer_page(provider)
    status,record=request(provider,'record',handle=page['entries'][0]['handle'])
    assert status==200
    link=record['linked_entries'][0]
    assert link['label']=='entries[0]' and link['status']=='readable'
    status,target=request(provider,'material',handle=link['handle'])
    assert status==200 and target['body']=='body-29'
    assert target['source_cut']==record['source_cut']==page['source_cut']
    assert counts(world)==before
    again=request(provider,'record',handle=page['entries'][0]['handle'])[1]
    assert again['linked_entries']==record['linked_entries']


def test_missing_reference_is_not_invented_and_links_are_bounded(tmp_path,monkeypatch):
    import cpn.frontend.local_registry_browser as module
    world=make_run(tmp_path/'run');refs=[publish(world,b'body',str(i)) for i in range(3)];index(world,refs)
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    page=pointer_page(provider)
    monkeypatch.setattr(module,'PAGE_SIZE',2)
    record=request(provider,'record',handle=page['entries'][0]['handle'])[1]
    assert len(record['linked_entries'])==2 and record['link_coverage']=={
        'scope':'declared_refs_of_selected_record','total':5,'shown':2,'has_more':True}
    # A separate reader sees the declared missing firing but supplies no capability.
    monkeypatch.setattr(module,'PAGE_SIZE',25)
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    page=pointer_page(provider);record=request(provider,'record',handle=page['entries'][0]['handle'])[1]
    missing=record['linked_entries'][-1]
    assert missing=={'label':'terminal_occurrence_ref','status':'unavailable'}


def test_resource_task_link_and_target_revalidation(tmp_path):
    world=make_run(tmp_path/'run');publish(world)
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    _,page=listing(provider)
    record=request(provider,'record',handle=page['entries'][0]['handle'])[1]
    task=next(link for link in record['linked_entries'] if link['label']=='task_ref')
    status,result=request(provider,'record',handle=task['handle'])
    assert status==200 and result['entry_ref']==task['entry_ref'] and result['linked_entries']==[]
    provider._items[task['handle']]['ref']['ref']['version_id']='changed'
    assert request(provider,'record',handle=task['handle'])[0]==409


def test_arbitrary_metadata_is_not_crawled():
    value={'entity_type':'task/v1','logical_id':str(new_id('task')),'version_id':str(new_id('task_version'))}
    assert declared_links('custom/v1',{'task_ref':value})==[]
    assert declared_links('resource_version/v1',{'extensions':{'task_ref':value},'summary':json.dumps(value)})==[]
    with pytest.raises(ValueError):declared_links('resource_version/v1',{'task_ref':{**value,'source_id':'elsewhere'}})


def test_provisional_target_is_not_followable(tmp_path):
    from test_canonical_head_projection import mark_firing
    world=make_run(tmp_path/'run');target=publish(world,b'hidden');index(world,[target])
    store=world[0].event_store;row=store.canonical_object_row(target.resource_version_id)
    firing=mark_firing(store,row['published_event_id'],row['transaction_id'])
    with store.connect() as db:
        db.execute('INSERT INTO firing_temporary_members VALUES (?,?,?,?)',
                   (firing,'object',str(target.resource_version_id),row['transaction_id']))
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    page=pointer_page(provider);status,record=request(provider,'record',handle=page['entries'][0]['handle'])
    assert status==200 and record['linked_entries'][0]=={'label':'entries[0]','status':'unavailable'}


def test_link_capacity_error_clears_all_handles(tmp_path,monkeypatch):
    import cpn.frontend.local_registry_browser as module
    world=make_run(tmp_path/'run');ref=publish(world);index(world,[ref])
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    page=pointer_page(provider);monkeypatch.setattr(module,'MAX_ITEMS',len(provider._items))
    assert request(provider,'record',handle=page['entries'][0]['handle'])==(413,{'error':'scope_limit'})
    assert not provider._items and not provider._pages


def test_missing_target_carrier_keeps_parent_record_readable(tmp_path):
    world=make_run(tmp_path/'run');target=publish(world,b'body');index(world,[target])
    world[0].object_store.path_for_version(target.resource_version_id).unlink()
    provider=LocalRegistryBrowserProvider(world[0].run_dir,catalog=world[0].catalog)
    page=pointer_page(provider);status,record=request(provider,'record',handle=page['entries'][0]['handle'])
    assert status==200 and record['fields']['entries']==[resource_wire(target)]
    for link, label in zip(record['linked_entries'][:2], ('entries[0]', 'terminal_result_ref')):
        # The canonical identity survives a missing carrier, not its body access.
        assert link['label'] == label and link['status'] == 'readable'
        assert link['material_may_be_available'] is False
        status, metadata = request(provider, 'record', handle=link['handle'])
        assert status == 200 and metadata['material_may_be_available'] is False
    # A denied material request invalidates all retained handles, intentionally.
    assert request(provider, 'material', handle=record['linked_entries'][0]['handle']) == (403, {'error': 'access_changed'})
    assert request(provider, 'record', handle=record['linked_entries'][1]['handle']) == (409, {'error': 'stale_observation'})
    _, resources = listing(provider)
    assert resources['entries'][0]['material_may_be_available'] is False

