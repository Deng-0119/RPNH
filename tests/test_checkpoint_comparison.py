"""Finite offline comparison contracts, including an existing deterministic run.

Synthetic mutations do not claim browser, malicious HOST, or OS isolation QA.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from cpn.frontend.comparison_view import (comparison_view, project_comparison, validate_comparison_response,
    parse_query, selectors, ComparisonInvalid, ComparisonStale, ComparisonAccessChanged, UNKNOWN_AXES)
from cpn.frontend.dashboard import RegistryDashboard, exact
from cpn.frontend.server import handle_request
from test_dashboard import fixture


def ref(kind, digit):
    stem = kind.split('/')[0]
    return {'entity_type': kind, 'logical_id': stem+':'+digit*32, 'version_id': stem+'_version:'+digit*32}

NET = ref('net_instance/v1', '1')
LEFT = {'net_ref': NET, 'checkpoint_ref': ref('marking_checkpoint/v1', '2'), 'cut': 20}
RIGHT = {'net_ref': NET, 'checkpoint_ref': ref('marking_checkpoint/v1', '3'), 'cut': 30}
CAPTURE = {'head_ordinal': 40, 'writer_fencing_epoch': 2}


def envelope(selected):
    source = {'mode': 'registry_current', 'task_id': 'task:'+'4'*32, 'run_dir': '/display-only/run',
              'net_ref': NET, 'verified_head_ordinal': selected['cut'], 'writer_fencing_epoch': 2}
    active = selected['cut'] == 20
    token = {'token_ref': ref('petri_token/v1', '5'), 'place': 'p', 'kind': None,
             'resource_ref': None, 'active_in_checkpoint': active}
    return deepcopy({'schema_version': 'rpnh/checkpoint_view/v1', 'selector': selected, 'capture': CAPTURE,
        'navigation': {'previous_net_segment': None, 'coverage': 'complete', 'end_reason': 'initial_checkpoint',
                       'loaded_checkpoints': 2, 'max_chain': 2048},
        'adoption_evidence': {'status': 'no_evidence_at_cut', 'coverage': 'complete', 'cut': selected['cut'],
                             'current_net_ref': None, 'records': []},
        'coverage': {'topology': 'selected_declaration', 'marking': 'selected_checkpoint',
                     'bindings': 'selected_net_exact_closure', 'firings': 'not_provided',
                     'activity': 'not_provided', 'cross_net_delta': 'not_provided'},
        'frame': {'schema_version': 'rpnh/dashboard/v1', 'source': source,
                  'net': {'schema_version': 'rpnh/net_view/v1', 'source': source, 'summary': {},
                          'nodes': [{'id': 'p', 'label': '<img src=x onerror=alert(1)>', 'kind': 'place',
                                     'category': 'place', 'hidden_by_default': False, 'capacity': None,
                                     'tokens': [token], 'active_token_count': int(active)}], 'edges': [],
                          'marking': {'checkpoint_ref': selected['checkpoint_ref'], 'epoch': 0,
                                      'token_count': 1, 'active_token_count': int(active)}},
                  'boundaries': {'entry': [], 'exit': [], 'terminal_rules': []}, 'transition_bindings': [],
                  'agent_nodes': [], 'presentation': {'nodes': {}}, 'change': {},
                  'position': {'mode': 'history', 'cursor': selected['cut'], 'latest_head': 40},
                  'coverage': {'history': 'selected_saved_checkpoint', 'firings': 'not_provided'}}})


def payload():
    return project_comparison(envelope(LEFT), envelope(RIGHT), LEFT, RIGHT)


def path(left=LEFT, right=RIGHT):
    return '/api/v2/comparison-view?'+urlencode({'left_selector': json.dumps(left), 'right_selector': json.dumps(right)})


class Provider:
    def __init__(self): self.calls = []
    def comparison_view(self, **kwargs):
        self.calls.append(kwargs)
        return payload()


def test_exact_selectors_get_head_and_readonly_static_route():
    provider = Provider()
    result = handle_request(provider, 'GET', path())
    assert result.status == 200
    assert result.headers['Cache-Control'] == 'no-store'
    assert provider.calls == [{'left_selector': LEFT, 'right_selector': RIGHT}]
    assert handle_request(provider, 'HEAD', path()).body == b''
    assert handle_request(provider, 'POST', path()).status == 405
    assert handle_request(object(), 'GET', '/comparison-view.mjs').status == 200
    assert handle_request(object(), 'GET', path()).status == 501


@pytest.mark.parametrize('target', [
    '/api/v2/comparison-view', path()+'&left_selector={}', path()+'&latest=1',
    '/api/v2/comparison-view?left_selector={}&right_selector={}',
    path(dict(LEFT, cut=True)), path(dict(LEFT, cut=0)), path(dict(LEFT, cut=2**53)),
    path(dict(LEFT, cut=20.0)), path(dict(LEFT, extra=1)),
    path(dict(LEFT, net_ref=ref('net_instance/v1', 'a'))),
])
def test_invalid_requests_never_call_provider(target):
    provider = Provider()
    assert handle_request(provider, 'GET', target).status == 400
    assert provider.calls == []


def test_nested_duplicate_keys_rejected():
    raw = json.dumps(LEFT).replace('"cut": 20', '"cut": 20, "cut": 20')
    with pytest.raises(ComparisonInvalid):
        parse_query(urlencode({'left_selector': raw, 'right_selector': json.dumps(RIGHT)}))


def test_public_facts_are_descriptive_and_unknown_never_zero():
    value = payload()
    assert value['global_atomic_snapshot'] is False
    assert value['comparison_mode'] == 'descriptive' and value['comparability'] == 'not_established'
    assert value['coverage']['status'] == 'partial'
    assert value['coverage']['unknown_axes'] == list(UNKNOWN_AXES)
    for axis in UNKNOWN_AXES:
        assert value['axes'][axis] == {'coverage': 'not_provided', 'rows': []}
    rows = value['axes']['tokens']['rows']
    assert [r['field'] for r in rows if r['classification'] == 'content_change'] == ['active_in_checkpoint']
    assert all(r['reason_source'] == 'current_analysis' for r in rows)
    validate_comparison_response(value, LEFT, RIGHT)


def test_missing_field_is_unknown_even_when_null_on_other_side():
    left, right = envelope(LEFT), envelope(RIGHT)
    right['frame']['net']['nodes'][0].pop('capacity')
    value = project_comparison(left, right, LEFT, RIGHT)
    row = next(r for r in value['axes']['definition']['rows'] if r['field'] == 'capacity')
    assert row['classification'] == 'unavailable'
    assert row['left_fact'] == {'status': 'provided', 'value': None}
    assert row['right_fact'] == {'status': 'not_provided', 'value': None}


def test_exact_occurrences_do_not_match_by_resource_or_label():
    left, right = envelope(LEFT), envelope(RIGHT)
    right['frame']['net']['nodes'][0]['tokens'][0]['token_ref'] = ref('petri_token/v1', '6')
    value = project_comparison(left, right, LEFT, RIGHT)
    assert {r['classification'] for r in value['axes']['tokens']['rows']} == {'present_left_only', 'present_right_only'}
    assert 'deleted' not in json.dumps(value['axes'])


@pytest.mark.parametrize('change', [
    lambda v: v['frame']['source'].update(task_id='another-task'),
    lambda v: v['frame']['source'].update(run_dir='/another-run'),
    lambda v: v['capture'].update(head_ordinal=41),
    lambda v: v['frame']['net']['marking'].update(active_token_count=99),
    lambda v: v['frame']['net']['nodes'][0].update(tokens=[]),
    lambda v: v['frame']['net']['nodes'][0].update(runtime={'firings': []}),
    lambda v: v['coverage'].update(marking='unavailable'),
    lambda v: v['coverage'].update(activity='complete'),
    lambda v: v['frame']['position'].update(cursor=31),
    lambda v: v['selector'].update(cut=31),
])
def test_partial_unavailable_or_substituted_side_never_creates_diff(change):
    right = envelope(RIGHT)
    change(right)
    with pytest.raises((ValueError, RuntimeError)):
        project_comparison(envelope(LEFT), right, LEFT, RIGHT)


@pytest.mark.parametrize('change', [
    lambda v: v.update(comparison_mode='ranking'), lambda v: v.update(global_atomic_snapshot=True),
    lambda v: v['axes']['tokens'].update(rows=[]), lambda v: v['coverage'].update(status='complete'),
    lambda v: v['axes']['native_scores'].update(coverage='complete', rows=[{'score': 1}]),
    lambda v: v.update(right_selector=LEFT),
])
def test_response_boundary_recomputes_rows_and_fixed_scope(change):
    value = payload(); change(value)
    class Wrong:
        def comparison_view(self, **kwargs): return value
    result = handle_request(Wrong(), 'GET', path())
    assert result.status == 503 and json.loads(result.body) == {'error': 'read_failed'}


@pytest.mark.parametrize('error,status,code', [
    (ComparisonAccessChanged('PRIVATE'), 403, 'access_changed'),
    (ComparisonStale('PRIVATE'), 409, 'stale_observation'),
    (OSError('PRIVATE'), 503, 'read_failed'), (ValueError('PRIVATE'), 503, 'read_failed')])
def test_failures_never_disclose_half_pair_or_private_error(error, status, code):
    class Broken:
        def comparison_view(self, **kwargs): raise error
    response = handle_request(Broken(), 'GET', path())
    assert response.status == status and json.loads(response.body) == {'error': code}


def test_pair_reads_independent_exact_selectors_and_final_current_binding(monkeypatch):
    import cpn.frontend.comparison_view as module
    calls = []
    pin = ('/display-only/run', 'db', 'task:'+'4'*32)
    monkeypatch.setattr(module, '_source_pin', lambda provider: (pin, CAPTURE))
    monkeypatch.setattr(module, 'checkpoint_view', lambda provider, **selected: calls.append(selected) or envelope(selected))
    value = comparison_view(object(), LEFT, RIGHT)
    assert calls == [LEFT, RIGHT]
    assert value['left_selector']['cut'] == 20 and value['right_selector']['cut'] == 30
    for changed_pin, changed_capture, expected in [
        (('different',)+pin[1:], CAPTURE, ComparisonAccessChanged),
        (pin, dict(CAPTURE, head_ordinal=41), ComparisonStale)]:
        captures = iter([(pin, CAPTURE), (changed_pin, changed_capture)])
        monkeypatch.setattr(module, '_source_pin', lambda provider: next(captures))
        with pytest.raises(expected): comparison_view(object(), LEFT, RIGHT)


def test_second_read_failure_and_revocation_discard_first_side(monkeypatch):
    import cpn.frontend.comparison_view as module
    pin = ('/display-only/run', 'db', 'task:'+'4'*32)
    monkeypatch.setattr(module, '_source_pin', lambda provider: (pin, CAPTURE))
    def read(provider, **selected):
        if selected == RIGHT: raise ValueError('unavailable second cut')
        return envelope(selected)
    monkeypatch.setattr(module, 'checkpoint_view', read)
    with pytest.raises(ValueError): comparison_view(object(), LEFT, RIGHT)
    monkeypatch.setattr(module, 'checkpoint_view', lambda provider, **selected: envelope(selected))
    checks = []
    def revoked(provider):
        if checks: raise ComparisonAccessChanged('revoked')
        checks.append(True); return pin, CAPTURE
    monkeypatch.setattr(module, '_source_pin', revoked)
    with pytest.raises(ComparisonAccessChanged): comparison_view(object(), LEFT, RIGHT)


def test_same_path_same_task_physical_replacement_is_rejected(tmp_path, monkeypatch):
    import cpn.frontend.comparison_view as module
    db = tmp_path/'registry.sqlite3';db.write_bytes(b'fixture')
    store = SimpleNamespace(path=db, max_ordinal=lambda:40, writer_epoch=2)
    core = SimpleNamespace(task_id='task:'+'4'*32, event_store=store)
    provider = SimpleNamespace(run_dir=tmp_path, task_id=core.task_id, binding=None, catalog=object(),
                               _open=lambda:core, _bound=lambda:None)
    def read(provider, **selected):
        value = envelope(selected)
        for source in (value['frame']['source'], value['frame']['net']['source']): source['run_dir'] = str(tmp_path)
        if selected == RIGHT:
            replacement = tmp_path/'replacement';replacement.write_bytes(b'fixture');replacement.replace(db)
        return value
    monkeypatch.setattr(module, 'checkpoint_view', read)
    with pytest.raises(ComparisonAccessChanged): comparison_view(provider, LEFT, RIGHT)


@pytest.fixture(scope='module')
def actual_run(tmp_path_factory):
    path = tmp_path_factory.mktemp('comparison')/'run'
    _, _, _, catalog = fixture.make_run(path)
    return RegistryDashboard(path, catalog=catalog)


def test_actual_registry_pair_exact_cuts_readonly_and_unknown_activity(actual_run, monkeypatch):
    history = actual_run.history(); items = history['items']; net = exact(history['source']['net_ref'])
    left = {'net_ref': net, 'checkpoint_ref': items[0]['checkpoint_ref'], 'cut': items[0]['cursor']}
    right = {'net_ref': net, 'checkpoint_ref': items[-1]['checkpoint_ref'], 'cut': items[-1]['cursor']}
    store = actual_run._open().event_store
    before = (store.max_ordinal(), store.writer_epoch)
    objects = {str(p):p.read_bytes() for p in (actual_run.run_dir/'.registry_v1/objects').rglob('*') if p.is_file()}
    monkeypatch.setattr(type(store), 'acquire_writer', lambda *a, **k: pytest.fail('comparison acquired writer'))
    result = actual_run.comparison_view(left_selector=left, right_selector=right)
    validate_comparison_response(result, left, right)
    assert result['left']['frame']['source']['verified_head_ordinal'] == left['cut']
    assert result['right']['frame']['source']['verified_head_ordinal'] == right['cut']
    assert result['axes']['activity']['coverage'] == 'not_provided'
    assert any(r['classification'] != 'unchanged' for r in result['axes']['tokens']['rows'])
    assert all(r['classification'] == 'unchanged' for r in result['axes']['bindings']['rows'])
    assert (store.max_ordinal(), store.writer_epoch) == before
    assert objects == {str(p):p.read_bytes() for p in (actual_run.run_dir/'.registry_v1/objects').rglob('*') if p.is_file()}


def test_actual_reader_limit_does_not_fall_back_to_latest_or_absence(actual_run):
    history = actual_run.history(); net = exact(history['source']['net_ref']); items = history['items']
    selected = lambda i: {'net_ref': net, 'checkpoint_ref': items[i]['checkpoint_ref'], 'cut': items[i]['cursor']}
    bounded = RegistryDashboard(actual_run.run_dir, catalog=actual_run.catalog, max_checkpoints=1)
    with pytest.raises(ValueError, match='reader_limit'):
        bounded.comparison_view(left_selector=selected(0), right_selector=selected(-1))


def test_socket_free_actual_adoption_same_cut_is_partial_readonly(tmp_path, monkeypatch):
    path = tmp_path/'adopted'
    catalog = fixture.make_agent_run(path)
    run = RegistryDashboard(path, catalog=catalog)
    history = run.history(); item = history['items'][0]
    selected = {'net_ref': exact(history['source']['net_ref']), 'checkpoint_ref': item['checkpoint_ref'], 'cut': item['cursor']}
    store = run._open().event_store; before = (store.max_ordinal(), store.writer_epoch)
    monkeypatch.setattr(type(store), 'acquire_writer', lambda *a, **k: pytest.fail('comparison acquired writer'))
    value = run.comparison_view(left_selector=selected, right_selector=selected)
    validate_comparison_response(value, selected, selected)
    assert value['coverage']['status'] == 'partial'
    for axis in ('definition', 'bindings', 'tokens', 'marking'):
        assert all(r['classification'] == 'unchanged' for r in value['axes'][axis]['rows'])
    assert value['axes']['terminal_evidence']['coverage'] == 'not_provided'
    assert (store.max_ordinal(), store.writer_epoch) == before
    assert 'PRIVATE fixture instruction' not in json.dumps(value)


@pytest.mark.parametrize('inject', [
    lambda v:v.update(private_extension={'resource_body':'DO-NOT-DISCLOSE'}),
    lambda v:v['frame']['net']['nodes'][0].update(config={'private_prompt':'DO-NOT-DISCLOSE'}),
    lambda v:v['frame']['net']['nodes'][0]['tokens'][0].update(resource_body='DO-NOT-DISCLOSE'),
    lambda v:v['frame']['net']['nodes'][0]['tokens'][0]['token_ref'].update(private='DO-NOT-DISCLOSE'),
    lambda v:v['frame']['boundaries'].update(private='DO-NOT-DISCLOSE'),
    lambda v:v['frame']['source'].update(credentials='DO-NOT-DISCLOSE'),
])
def test_custom_provider_private_extensions_fail_closed(inject):
    right=envelope(RIGHT);inject(right)
    with pytest.raises((ValueError, TypeError, KeyError)):
        project_comparison(envelope(LEFT),right,LEFT,RIGHT)
    value=payload();inject(value['right'])
    class Custom:
        def comparison_view(self,**kwargs):return value
    result=handle_request(Custom(),'GET',path())
    assert result.status==503 and b'DO-NOT-DISCLOSE' not in result.body


def test_unused_presentation_change_summary_are_not_forwarded():
    right=envelope(RIGHT)
    for key in ('presentation','agent_nodes','change'):
        right['frame'][key]={'private':'DO-NOT-DISCLOSE'}
    right['frame']['net']['summary']['private']='DO-NOT-DISCLOSE'
    value=project_comparison(envelope(LEFT),right,LEFT,RIGHT)
    assert 'DO-NOT-DISCLOSE' not in json.dumps(value)


def test_node_wire_fixture_matches_python_public_projection():
    from pathlib import Path
    fixture_path=Path(__file__).resolve().parents[1]/'frontend/net-viewer/tests/comparison-fixture.json'
    assert json.loads(fixture_path.read_text()) == payload()
