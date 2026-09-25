"""Actual native Registry history; UI reads never participate in execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest
from cpn.frontend.dashboard import RegistryDashboard, exact, presentation_for, public_net, topology_digest
from cpn.frontend.server import handle_request
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import SchemaCatalog

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('viewer_fixture', ROOT/'scripts/net_viewer_fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

@pytest.fixture(scope='module')
def run(tmp_path_factory):
    path = tmp_path_factory.mktemp('dashboard')/'run'
    _, _, _, catalog = fixture.make_run(path)
    return RegistryDashboard(path, catalog=catalog)

def test_persisted_history_exists_before_viewer_and_reopening(run):
    history = run.history()
    assert len(history['items']) == 4
    fresh = RegistryDashboard(run.run_dir, catalog=run.catalog)
    assert fresh.history() == history
    frames = [fresh.dashboard(cursor=i['cursor']) for i in history['items']]
    assert [len(next(n for n in f['net']['nodes'] if n['id']=='loop.step')['runtime']['firings']) for f in frames] == [0,1,2,3]
    assert [next(n for n in f['net']['nodes'] if n['id']=='loop.done')['active_token_count'] for f in frames] == [0,0,0,1]
    assert all(f['coverage']['firings']=='canonical_only' for f in frames)
    for before, after in zip(frames, frames[1:]):
        assert after['change']['previous_checkpoint_ref']==before['net']['marking']['checkpoint_ref']
        old={json.dumps(t['token_ref'],sort_keys=True) for n in before['net']['nodes'] for t in n.get('tokens',[]) if t['active_in_checkpoint']}
        new={json.dumps(t['token_ref'],sort_keys=True) for n in after['net']['nodes'] for t in n.get('tokens',[]) if t['active_in_checkpoint']}
        consumed={json.dumps(t['token_ref'],sort_keys=True) for t in after['change']['consumed']}
        produced={json.dumps(t['token_ref'],sort_keys=True) for t in after['change']['deposited']}
        assert new == old-consumed|produced
        assert after['change']['coverage']=='settlement_delta'

def test_historical_events_never_exceed_selected_complete_transaction(run):
    for i in run.history()['items']:
        frame=run.dashboard(cursor=i['cursor'])
        assert frame['source']['verified_head_ordinal']==i['cursor']
        for n in frame['net']['nodes']:
            for firing in n.get('runtime',{}).get('firings',[]):
                assert firing['publication_state']=='PUBLISHED'
                assert max(e['ordinal'] for e in firing['events'])<=i['cursor']
        events=run._open().event_store.canonical_events(after_ordinal=i['cursor']-1,through_ordinal=i['cursor'])
        assert len(events)==1 and events[0].event_type=='transaction_committed/v1'

def test_history_paging_and_invalid_cursors(run):
    latest=run.history(limit=2); earlier=run.history(before=latest['next_before'],limit=2)
    assert earlier['items']+latest['items']==run.history()['items']
    assert earlier['next_before'] is None
    for cursor in [True,-1,1,'1']:
        with pytest.raises(ValueError):run.dashboard(cursor=cursor)
    for limit in [0,101,True]:
        with pytest.raises(ValueError):run.history(limit=limit)

def test_observation_is_not_a_write_or_resume(run,monkeypatch):
    core=run._open();store=core.event_store
    # The established EventStore connection is sqlite; compare logical data,
    # not WAL metadata. Acquire-writer is forbidden even without a commit.
    before=(store.max_ordinal(),store.writer_epoch)
    objects={str(p.relative_to(run.run_dir)):p.read_bytes() for p in (run.run_dir/'.registry_v1/objects').rglob('*') if p.is_file()}
    monkeypatch.setattr(type(store),'acquire_writer',lambda *a,**k:pytest.fail('viewer acquired writer'))
    run.dashboard();run.history()
    for i in run.history()['items']:run.dashboard(cursor=i['cursor'])
    assert (store.max_ordinal(),store.writer_epoch)==before
    assert objects=={str(p.relative_to(run.run_dir)):p.read_bytes() for p in (run.run_dir/'.registry_v1/objects').rglob('*') if p.is_file()}

def test_current_counts_match_existing_projection(run):
    old=project_registry_net(run.run_dir,catalog=run.catalog);new=run.dashboard()
    assert new['net']['marking']['active_token_count']==old['marking']['active_token_count']
    assert len(new['net']['nodes'])==len(old['nodes'])
    assert len(new['net']['edges'])==len(old['edges'])
    assert new['boundaries']['entry'][0]['place']=='loop.input'
    assert new['boundaries']['terminal_evidence']=='not_provided'

def test_browser_disclosure_and_invalid_presentation_are_non_authoritative(run):
    raw=project_registry_net(run.run_dir,catalog=run.catalog)
    fake=deepcopy(raw);fake['nodes'][0]['config']={'secret':'DO_NOT_DISCLOSE'}
    assert 'DO_NOT_DISCLOSE' not in json.dumps(public_net(fake))
    assert public_net(fake)!=fake
    manifest={'schema_version':'rpnh/presentation/v1','topology_digest':topology_digest(raw),
              'title':'测试看板','nodes':{'loop.step':{'name':'计算步骤','description':'执行声明的计算'}}}
    assert presentation_for(manifest,raw)['status']=='matched'
    assert presentation_for({**manifest,'topology_digest':'other'},raw)['status']=='mismatch'
    assert presentation_for({**manifest,'nodes':{'missing':{}}},raw)['status']=='invalid'
    assert 'node_synopsis' not in json.dumps(run.dashboard())

def test_http_dashboard_queries_and_legacy_fallback(run):
    assert handle_request(run,'GET','/api/v1/dashboard').status==200
    assert handle_request(run,'HEAD','/api/v1/dashboard').body==b''
    assert handle_request(run,'GET','/api/v1/history?limit=2').status==200
    for query in ['?cursor=-1','?cursor=1&cursor=2','?bad=1','?cursor=nan']:
        assert handle_request(run,'GET','/api/v1/dashboard'+query).status==400
    assert handle_request(run,'POST','/api/v1/dashboard').status==405
    assert handle_request(lambda:run(),'GET','/api/v1/dashboard').status==501
    assert handle_request(lambda:run(),'GET','/api/v1/net').status==200

def test_missing_run_not_created(tmp_path):
    with pytest.raises(Exception): RegistryDashboard(tmp_path/'missing',catalog=SchemaCatalog())
    assert not (tmp_path/'missing').exists()

def test_presentation_defaults_do_not_pin_browser_language_or_rewrite_user_text(run):
    raw=project_registry_net(run.run_dir,catalog=run.catalog)
    default=presentation_for(None,raw)
    assert default['title'] is None and default['description'] is None
    manifest={'schema_version':'rpnh/presentation/v1','topology_digest':topology_digest(raw),
              'title':'用户名称', 'nodes':{'loop.step':{'description':'原始职责'}}}
    declared=presentation_for(manifest,raw)
    assert declared['title']=='用户名称'
    assert declared['description'] is None
    assert declared['nodes']['loop.step']['description']=='原始职责'


@pytest.fixture(scope='module')
def agent_run(tmp_path_factory):
    path = tmp_path_factory.mktemp('agent-overview')/'run'
    catalog = fixture.make_agent_run(path)
    return RegistryDashboard(path, catalog=catalog)


def test_overview_reads_real_agent_declarations_not_every_transition(agent_run):
    frame = agent_run.dashboard()
    assert [a['transition_id'] for a in frame['agent_nodes']] == [
        'planner.run', 'reviewer.run', 'writer.run']
    assert all(a['source'] == 'declared_llm_executor' for a in frame['agent_nodes'])
    assert len(frame['net']['nodes']) == 11 and len(frame['net']['edges']) == 15
    assert {n['id'] for n in frame['net']['nodes'] if n['kind']=='transition'} >= {'lookup.run','format.run'}
    assert all(not n['runtime']['firings'] for n in frame['net']['nodes'] if n['kind']=='transition')
    assert 'PRIVATE fixture instruction' not in json.dumps(frame)


def test_mechanical_fixture_is_not_an_agent_and_names_cannot_make_it_one(run):
    assert run.dashboard()['agent_nodes'] == []
    manifest = {'schema_version': 'rpnh/presentation/v1',
                'topology_digest': topology_digest(run.dashboard()['net']),
                'nodes': {'loop.step': {'name':'Agent', 'type':'智能体'}}}
    other = RegistryDashboard(run.run_dir, catalog=run.catalog, presentation=manifest)
    assert other.dashboard()['agent_nodes'] == []


def test_host_agent_mapping_is_explicit_validated_annotation_not_execution_authority(run):
    manifest = {'schema_version': 'rpnh/presentation/v1',
                'topology_digest': topology_digest(run.dashboard()['net']),
                'agent_nodes':['loop.step']}
    other = RegistryDashboard(run.run_dir, catalog=run.catalog, presentation=manifest)
    value = other.dashboard()
    assert value['agent_nodes'][0]['source'] == 'explicit_host_annotation'
    assert value['agent_nodes'][0]['agent_ref'] is None
    assert value['net'] == run.dashboard()['net']
    for invalid in [['missing'],['loop.input'],['loop.step','loop.step'],'loop.step']:
        manifest['agent_nodes'] = invalid
        frame = other.dashboard()
        assert frame['agent_nodes'] == [] and frame['presentation']['status'] == 'invalid'


def test_agent_inventory_is_same_at_persisted_initial_checkpoint_and_current(agent_run):
    current = agent_run.dashboard()
    initial = agent_run.dashboard(cursor=agent_run.history()['items'][0]['cursor'])
    assert initial['agent_nodes'] == current['agent_nodes']
    assert initial['coverage']['firings'] == 'canonical_only'


def test_agent_inspection_keeps_head_epoch_objects_and_core_authority_unchanged(agent_run,monkeypatch):
    core = agent_run._open()
    before = (core.event_store.max_ordinal(), core.event_store.writer_epoch)
    objects = {str(p): p.read_bytes()
               for p in (agent_run.run_dir/'.registry_v1/objects').rglob('*') if p.is_file()}
    monkeypatch.setattr(type(core.event_store),'acquire_writer',lambda *a,**k:pytest.fail('Agent view acquired writer'))
    for _ in range(2): agent_run.dashboard()
    assert before == (core.event_store.max_ordinal(),core.event_store.writer_epoch)
    assert objects == {str(p):p.read_bytes()
                       for p in (agent_run.run_dir/'.registry_v1/objects').rglob('*') if p.is_file()}


def test_registered_agent_binding_has_priority_over_transport(agent_run,monkeypatch):
    original = agent_run._load
    def load(core,ref,kind,upper):
        value = original(core,ref,kind,upper)
        if kind == 'executable_transition_binding/v1' and value['transition_id']=='planner.run':
            value = {**value,'agent_ref':{'test':'explicit-agent-binding'}}
        return value
    monkeypatch.setattr(agent_run,'_load',load)
    frame = agent_run.dashboard()
    assert frame['agent_nodes'][0]['source'] == 'registered_agent_binding'


def test_agent_classification_rejects_foreign_binding(agent_run,monkeypatch):
    original = agent_run._load
    def load(core,ref,kind,upper):
        value = original(core,ref,kind,upper)
        if kind == 'executable_transition_binding/v1': value = {**value,'transition_id':'foreign'}
        return value
    monkeypatch.setattr(agent_run,'_load',load)
    with pytest.raises(ValueError,match='Agent binding differs'): agent_run.dashboard()
