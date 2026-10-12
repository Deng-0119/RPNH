"""Recorded actor projection tests, using original offline Registry fixtures."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlencode
import pytest

from cpn.frontend.user_contributions import (project_contributions, parse_query, ContributionInvalid,
    UserContributionView, ContributionAccessChanged)
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import handle_request
from cpn.rpnh.registry.user_collaboration import SESSION, PROPOSAL, REVIEW, DECISION, RECEIPT


def ref(kind, number):
    stem = {'net_instance/v1': 'net_instance', 'marking_checkpoint/v1': 'marking_checkpoint'}.get(kind, 'resource')
    return {'entity_type': kind, 'logical_id': f'{stem}:{number:032x}', 'version_id': f'{stem}_version:{number:032x}'}


def pure_world():
    selected = dict(net_ref=ref('net_instance/v1', 1), checkpoint_ref=ref('marking_checkpoint/v1', 2), cut=100)
    revisions = [ref('collaboration_net_revision/v1', i) for i in range(10, 14)]
    sessions = [dict(self_ref=ref(SESSION, i), data={'actor': who}) for i, who in ((20, 'owner'), (21, 'alice'), (22, 'bob'))]
    project = ref('user_collaboration_project/v1', 25)
    proposals = [dict(self_ref=ref(PROPOSAL, i), session_ref=sessions[s]['self_ref'], project_ref=project,
        data={'candidate_revision_ref': revisions[r], 'base_revision_ref': revisions[0], 'payload': {'value': value}})
        for i, s, r, value in ((30, 1, 1, 'changed'), (31, 2, 2, 'changed'))]
    review = dict(self_ref=ref(REVIEW, 40), session_ref=sessions[0]['self_ref'], data={'disposition': 'approve'})
    decision = dict(self_ref=ref(DECISION, 41), session_ref=sessions[0]['self_ref'], project_ref=project,
        data={'operation': 'adopt', 'result_revision_ref': revisions[3], 'review_refs': [review['self_ref']]})
    receipt = dict(self_ref=ref(RECEIPT, 42), project_ref=project, data={'operation': 'adopt',
        'decision_ref': decision['self_ref'], 'result_revision_ref': revisions[3], 'result_ref': selected['net_ref']})
    element = {'element_id': 'element:' + 'a' * 32, 'kind': 'operation', 'locator': '/components/step/operations/run'}
    models = {}
    for i, value, parents in ((0, 'base', []), (1, 'changed', [revisions[0]]), (2, 'changed', [revisions[0]]), (3, 'changed', [revisions[1]])):
        models[json.dumps(revisions[i], sort_keys=True)] = {'module': {'value': value},
            'atoms': {element['element_id'] + '/value': value}, 'parents': parents,
            'elements': {element['element_id']: element}, 'lowering': [{**element, 'nodes': ['step.run']}]}
    facts = {SESSION: sessions, PROPOSAL: proposals, REVIEW: [review], DECISION: [decision], RECEIPT: [receipt]}
    def run():
        return project_contributions(selected, {'task_id': 'task', 'run_dir': '/fixture'},
            {'nodes': [{'id': 'step.run'}, {'id': 'unknown.place'}]}, receipt, facts,
            lambda r: models[json.dumps(r, sort_keys=True)])
    return run, models, revisions, selected, facts


def test_equal_unselected_proposal_never_gets_retained_credit():
    run, *_ = pure_world(); result = run()
    row = result['nodes'][0]
    assert row['state'] == 'inherited'
    assert {x['actor'] for x in row['changes']} == {'alice'}
    assert next(x for x in result['actions'] if x['actor'] == 'bob')['status'] == 'unselected'
    assert result['nodes'][1]['state'] == 'unknown'
    assert not any(a['role'] == 'executed' for a in result['actions'])
    assert result['coverage']['status'] == 'partial'


def test_shared_exact_parent_lineage_retains_overlap_without_unique_owner():
    run, models, revisions, *_ = pure_world()
    models[json.dumps(revisions[3], sort_keys=True)]['parents'].append(revisions[2])
    row = run()['nodes'][0]
    assert row['state'] == 'overlap'
    assert {x['actor'] for x in row['changes']} == {'alice', 'bob'}


def test_unattributed_later_change_removes_old_credit():
    run, models, revisions, *_ = pure_world()
    last = models[json.dumps(revisions[3], sort_keys=True)]
    last['atoms'] = {next(iter(last['atoms'])): 'new unknown edit'}
    assert run()['nodes'][0]['state'] == 'unknown'



def test_json_boolean_and_integer_changes_are_not_conflated():
    from cpn.frontend.user_contributions import _changed_fields, _state
    assert _state({'k': True}, 'k') != _state({'k': 1}, 'k')
    assert _changed_fields({'config': {'flag': True}}, {'config': {'flag': 1}}, 'value') == ['value/config/flag']
    assert _state({}, 'k') != _state({'k': None}, 'k')


def test_restoring_old_bytes_does_not_resurrect_broken_lineage_credit():
    run, models, revisions, *_ = pure_world()
    intermediate_ref = ref('collaboration_net_revision/v1', 14)
    target = models[json.dumps(revisions[3], sort_keys=True)]
    intermediate = deepcopy(target)
    intermediate['atoms'] = {next(iter(target['atoms'])): 'unattributed replacement'}
    intermediate['parents'] = [revisions[1]]
    models[json.dumps(intermediate_ref, sort_keys=True)] = intermediate
    target['parents'] = [intermediate_ref]
    assert run()['nodes'][0]['state'] == 'unknown'


def test_missing_session_never_infers_actor_from_publication_or_provider():
    run, _, _, _, facts = pure_world()
    facts[SESSION] = [s for s in facts[SESSION] if s['data']['actor'] != 'alice']
    with pytest.raises(ValueError, match='actor session'):
        run()


def test_projection_limit_fails_before_returning_partial_colors(monkeypatch):
    from cpn.frontend.user_contributions import ContributionLimit
    monkeypatch.setattr('cpn.frontend.user_contributions.MAX_PROJECTED_CHANGES', 0)
    run, *_ = pure_world()
    with pytest.raises(ContributionLimit):
        run()

def test_no_receipt_is_unknown_even_with_runtime_node_names():
    _, _, _, selected, facts = pure_world()
    result = project_contributions(selected, {}, {'nodes': [{'id': 'alice.model'}]}, None, facts,
        lambda r: pytest.fail('no unrelated material reads'))
    assert result['actors'] == [] and result['nodes'][0]['state'] == 'unknown'


def test_query_rejects_actor_credentials_and_duplicate_or_noncanonical_cuts():
    *_, selected, facts = pure_world()
    query = urlencode({k: json.dumps(v) if isinstance(v, dict) else v for k, v in selected.items()})
    assert parse_query(query) == selected
    for suffix in ('&actor=alice', '&cut=100', '&session_ref={}'): 
        with pytest.raises(ContributionInvalid): parse_query(query + suffix)
    for bad in ('01', '0', '-1', '1e2', '9007199254740992'):
        with pytest.raises(ContributionInvalid): parse_query(query.replace('cut=100', 'cut=' + bad))
    assert handle_request(lambda: {}, 'GET', '/api/v2/user-contributions?' + query).status == 501
    assert handle_request(lambda: {}, 'POST', '/api/v2/user-contributions?' + query).status == 405


def public_reader(world):
    """Explicit test-owner grant through original public issuer, never product code."""
    from datetime import datetime, timedelta, timezone
    from cpn.rpnh.collaboration import SourceQualifiedVersionRef, SourceQualifiedResourceRef
    from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
    from cpn.rpnh.collaboration.registry_read_contracts import ReadSessionRequest, ExplicitSources, SourceSelection
    from cpn.rpnh.collaboration.registry_read_session import RegistryReadHostBinding, ExistingReadAuthorityProvider, open_registry_session, open_readonly_source
    from cpn.rpnh.registry.observer_access import ObserverReadScope
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.identities import TypedId
    cat = TypedReaderCatalog()
    fields = {kind: tuple(cat.fields(kind)) for kind in cat.entry_types}
    materials = tuple(SourceQualifiedResourceRef(world['source'], ResourceVersionRef(
        TypedId.parse(r['logical_id']), TypedId.parse(r['version_id'])))
        for r in world['core'].event_store.object_rows_by_type('resource_version/v1'))
    grant = world['host'].issue_read_access(world['session'], principal_ref=world['principal'],
        scope=ObserverReadScope(fields, fields, materials), purpose='contribution-test',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='view-grant')
    def resolver(source_id, access_path):
        assert source_id == world['source'] and access_path == 'local'
        return open_readonly_source(world['core'].run_dir, catalog=world['core'].catalog)
    return open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(
        SourceQualifiedVersionRef(world['source'], world['owner'].task_ref), 'local'),)), 'contribution-test'),
        host=RegistryReadHostBinding('test-owner', resolver,
            ExistingReadAuthorityProvider({('test-owner', world['source'], 'local'): grant}), cat))


def test_original_owner_adoption_projects_real_nodes(runtime_world, monkeypatch):
    """Synthetic owner setup; projection uses real read sessions and lowering."""
    import test_terminal_owner_adoption as native
    from test_user_collaboration_authority import join, proposal, decision, advance
    from cpn.rpnh.net_operations import prepare_replacement
    from cpn.rpnh.registry._event_store.branch_publication import branch_command_key
    owner, w = runtime_world
    document = w['base'].module.to_dict()
    document['components'][0]['operations'][0]['config']['capacities'] = {'result': 2}
    join(w); p, candidate, _ = proposal(w, document=document)
    d, review = decision(w, p, candidate)
    accepted = advance(w, candidate, w['host'].action(w['a'], d))
    receipt = w['host'].receipt_ref(branch_command_key('advance'))
    plan = prepare_replacement(owner, candidate.module)
    adopt = w['host'].decide(w['a'], w['project'], operation='adopt', proposal_refs=(p,), review_refs=(review,),
        result_revision_ref=candidate.revision.revision_ref, expected_branch_ref=accepted.branch_ref,
        expected_head_ref=accepted.head_revision_ref, expected_runtime_ref=plan.base_net_ref,
        accepted_receipt_ref=receipt, command_id='adopt-view')
    assert owner.control.edits.submit(plan.owner_command(), 'owner-adopt-view', collaboration_action=w['host'].action(w['a'], adopt))['status'] == 'ADOPTED'
    from cpn.rpnh.registry.observer_access import observer_access_schema_data
    # The fixture explicitly selects the original owner read grant. Product code
    # never calls this issuer or auto-generates a read scope.
    world = dict(core=w['core'], gateway=w['gateway'], principal=owner.principal_ref,
        source='plain-source', owner=SimpleNamespace(task_ref=w['gateway']._task_ref), host=w['host'], session=w['a'])
    with public_reader(world) as reader:
        view = UserContributionView(w['host'], w['a'], reader, source_id='plain-source')
        dashboard = RegistryDashboard(w['core'].run_dir, catalog=w['core'].catalog, user_contribution_view=view)
        frame = dashboard.dashboard()
        selected = dict(net_ref=frame['source']['net_ref'], checkpoint_ref=frame['net']['marking']['checkpoint_ref'], cut=frame['position']['cursor'])
        from cpn.frontend.dashboard import exact
        selected = {k: exact(v) if isinstance(v, dict) else v for k, v in selected.items()}
        before = w['core'].event_store.max_ordinal()
        def forbidden(*args, **kwargs):
            raise AssertionError('Viewer must not compile, lower or execute')
        monkeypatch.setattr('cpn.rpnh.compiler.compile_module', forbidden)
        result = dashboard.user_contributions(**selected)
        assert w['core'].event_store.max_ordinal() == before
        changed = [n for n in result['nodes'] if n['changes']]
        assert changed and {c['actor'] for n in changed for c in n['changes']} == {w['b'].actor}
        assert set(n['node_id'] for n in result['nodes']) == set(n['id'] for n in frame['net']['nodes'])
        assert next(a for a in result['actions'] if a['role'] == 'adopted')['actor'] == w['a'].actor
        query = urlencode({k: json.dumps(v) if isinstance(v, dict) else v for k, v in selected.items()})
        assert handle_request(dashboard, 'GET', '/api/v2/user-contributions?' + query).status == 200
        assert handle_request(dashboard, 'HEAD', '/api/v2/user-contributions?' + query).body == b''
        assert 'run_dir' not in result['source'] and result['source']['task_ref']['logical_id'] == str(w['core'].task_id)
        original_read = reader.read_exact
        def foreign_parent(**kwargs):
            value = deepcopy(original_read(**kwargs))
            if value['record'].get('parent_revision_refs'):
                value['record']['parent_revision_refs'][0]['source_id'] = 'foreign-source'
            return value
        monkeypatch.setattr(reader, 'read_exact', foreign_parent)
        assert handle_request(dashboard, 'GET', '/api/v2/user-contributions?' + query).status == 501
        monkeypatch.setattr(reader, 'read_exact', original_read)
        reader.close()
        assert handle_request(dashboard, 'GET', '/api/v2/user-contributions?' + query).status == 403


from test_user_collaboration_authority_native import runtime_world
