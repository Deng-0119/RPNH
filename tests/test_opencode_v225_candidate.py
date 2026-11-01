"""Fixed 2.2.5 schema subset and negative boundaries; no native client certification."""
from pathlib import Path
from unittest.mock import patch

import pytest

from cpn.frontend.opencode_protocol import DEFAULT_PROFILE, OpenCodeProtocol, get_opencode_profile
from cpn.frontend.opencode_v2_protocol import OpenCodeV2Protocol
from cpn.frontend.opencode_launcher import frontend_launch_spec, isolated_environment, check_version
from cpn.frontend.opencode_http import _Server
from cpn.rpnh.frontend_application import FrontendError
from test_opencode_frontend import ApplicationDouble, LocalGateway, SID


@pytest.fixture
def context(tmp_path):
    app = ApplicationDouble()
    profile = get_opencode_profile(certification_version='2.2.5')
    protocol = OpenCodeV2Protocol(LocalGateway(app), str(tmp_path), profile=profile)
    return app, protocol


def create(protocol):
    return protocol.route('POST', '/api/session', {}).body['data']


def test_explicit_candidate_cannot_silently_use_legacy_protocol(context):
    app, protocol = context
    assert DEFAULT_PROFILE.version == '1.19.4'
    assert protocol.profile.certification_only is True
    with pytest.raises(ValueError, match='separate v2'):
        OpenCodeProtocol(LocalGateway(app), protocol.directory, profile=protocol.profile)
    with pytest.raises(ValueError):
        OpenCodeV2Protocol(LocalGateway(app), protocol.directory, profile=DEFAULT_PROFILE)
    with pytest.raises(ValueError):
        get_opencode_profile(certification_version='2.2.6')


def test_fixed_candidate_boot_and_no_credentials_from_host(context, tmp_path):
    _, protocol = context
    args, auth = frontend_launch_spec('/owner/opencode', 'http://127.0.0.1:1234', 'secret', tmp_path,
                                      profile=protocol.profile, resume=True)
    assert args == ['/owner/opencode', str(tmp_path), '--server', 'http://127.0.0.1:1234', '--continue']
    assert auth == {'OPENCODE_PASSWORD': 'secret'}
    (tmp_path / 'isolation').mkdir()
    env = isolated_environment(tmp_path / 'isolation', {'PATH':'/bin', 'OPENCODE_PASSWORD':'host-secret',
                                                        'PROVIDER_API_KEY':'provider-secret'})
    assert 'OPENCODE_PASSWORD' not in env and 'PROVIDER_API_KEY' not in env
    info = protocol.route('GET', '/api/info').body
    assert info['version'] == '2.2.5' and info['capabilities'] == {'persistentPty': False}
    assert set(info) == {'version', 'pid', 'urls', 'paths', 'capabilities'}


def test_username_is_version_bound_without_opening_a_socket(context):
    _, protocol = context
    with patch('cpn.frontend.opencode_http.ThreadingHTTPServer.__init__', return_value=None):
        server = _Server(protocol, 'secret')
    assert server.authorization == 'Basic b3BlbmNvZGU6c2VjcmV0'


def test_create_prompt_repeat_history_and_interrupt_project_original_gateway(context):
    app, protocol = context
    session = create(protocol)
    assert session['id'] == SID and session['location'] == {'directory': protocol.directory}
    assert set(session['tokens']) == {'input', 'output', 'reasoning', 'cache'}
    prompt = {'id': 'msg_user-msg_input-1', 'text': 'hello'}
    path = f'/api/session/{SID}/prompt'
    first = protocol.route('POST', path, prompt).body['data']
    repeated = protocol.route('POST', path, prompt).body['data']
    assert first == repeated
    assert first == {'id':'msg_user-msg_input-1', 'sessionID':SID, 'type':'user',
                     'time':{'created':1001}, 'payload':{'text':'hello'}, 'delivery':'queue'}
    assert len(app.views[0]['turns']) == 1
    with pytest.raises(FrontendError, match='Conflict'):
        protocol.route('POST', path, {**prompt, 'text':'changed'})
    app.commit('safe registered answer')
    history = protocol.route('GET', f'/api/session/{SID}/message').body
    assert history['cursor'] == {'previous':None, 'next':None}
    assert [m['type'] for m in history['data']] == ['user', 'assistant']
    assert history['data'][0]['id'] == first['id']
    assert history['data'][1]['content'] == [{'type':'text', 'text':'safe registered answer'}]
    assert protocol.route('POST', f'/api/session/{SID}/interrupt', {}).body == {'interrupted':True}
    assert app.calls.count('abort') == 1


@pytest.mark.parametrize('override', [
    {'files':[{'uri':'file:///secret'}]}, {'agents':[{'name':'other'}]}, {'skills':[{'id':'s'}]},
    {'metadata':{'tool':'run'}}, {'delivery':'steer'}, {'delivery':'queue'}, {'resume':True},
    {'tools':{}}, {'permissions':[]}, {'model':{'id':'other'}}, {'text':'/shell cat secret'},
])
def test_unknown_effects_rejected_before_submission(context, override):
    app, protocol = context
    create(protocol)
    with pytest.raises(FrontendError):
        protocol.route('POST', f'/api/session/{SID}/prompt', {'id':'msg_i', 'text':'safe', **override})
    assert 'submit' not in app.calls


def test_busy_identity_header_location_and_cursor_boundaries(context):
    app, protocol = context
    create(protocol)
    path = f'/api/session/{SID}/prompt'
    protocol.route('POST', path, {'id':'msg_i', 'text':'safe'})
    with pytest.raises(FrontendError, match='Busy'):
        protocol.route('POST', path, {'id':'msg_j', 'text':'new'})
    with pytest.raises(FrontendError, match='agree'):
        protocol.route('POST', path, {'id':'msg_i', 'text':'safe'}, {'Idempotency-Key':'different'})
    for path in ['/api/session?cursor=unknown', '/api/session?limit=1&limit=2', '/api/session?directory=/foreign',
                 f'/api/session/{SID}/message?limit=0', '/session', '/api/pty', '/api/schedule']:
        with pytest.raises(FrontendError):
            protocol.route('GET', path)
    with pytest.raises(FrontendError):
        protocol.route('GET', '/api/info', headers={'x-opencode-directory':'/foreign'})


def test_event_reconnect_is_read_only_non_durable_projection(context):
    app, protocol = context
    create(protocol)
    before = len(app.calls)
    for _ in range(2):
        connected = protocol.connected_event()
        assert connected['type'] == 'server.connected' and connected['data'] == {}
        assert set(connected) == {'id','type','data','location'}
        events = protocol.events()
        assert events[0]['type'] == 'session.status'
        assert all('durable' not in event and 'properties' not in event for event in events)
        protocol.route('GET', '/api/session')
    assert set(app.calls[before:]) == {'snapshot'}
    assert protocol.route('GET', '/api/event').status == 200
    with pytest.raises(FrontendError):
        protocol.route('GET', '/global/event')


def test_source_extracted_official_shape_subset(context):
    import json
    import jsonschema
    app, protocol = context
    schema = json.loads((Path(__file__).parent / 'fixtures/opencode/2.2.5/subset.schema.json').read_text())
    def validate(kind, data):
        jsonschema.Draft202012Validator({'$ref':'#/$defs/' + kind, '$defs':schema['$defs']}).validate(data)
    validate('ServerInfo', protocol.route('GET', '/api/info').body)
    validate('SessionInfo', create(protocol))
    response = protocol.route('POST', f'/api/session/{SID}/prompt', {'id':'msg_input-1', 'text':'question'})
    validate('SessionInboxUser', response.body['data'])
    app.commit('answer')
    for message in protocol.route('GET', f'/api/session/{SID}/message').body['data']:
        validate('Message', message)
    validate('Connected', protocol.connected_event())
    for event in protocol.events():
        validate('Status', event)


def test_candidate_http_auth_does_not_accept_legacy_username(context):
    import base64
    from http.client import parse_headers
    from io import BytesIO
    from types import SimpleNamespace
    from cpn.frontend.opencode_http import _Handler
    _, protocol = context
    with patch('cpn.frontend.opencode_http.ThreadingHTTPServer.__init__', return_value=None):
        server = _Server(protocol, 'secret')
    server.server_port = 1234
    handler = object.__new__(_Handler)
    handler.server = server
    for user, accepted in [('opencode', True), ('rpnh', False)]:
        authorization = 'Basic ' + base64.b64encode(f'{user}:secret'.encode()).decode()
        handler.headers = parse_headers(BytesIO(f'Host: 127.0.0.1:1234\r\nAuthorization: {authorization}\r\n\r\n'.encode()))
        if accepted:
            handler._validate()
        else:
            with pytest.raises(FrontendError, match='authentication'):
                handler._validate()


def test_candidate_exact_version_probe_and_reconstructed_history(context, tmp_path):
    import subprocess
    app, protocol = context
    with patch('cpn.frontend.opencode_launcher.subprocess.run', return_value=subprocess.CompletedProcess([],0,'2.2.5\n','')):
        check_version('/owner/opencode', {}, tmp_path, profile=protocol.profile)
        with pytest.raises(RuntimeError, match='exactly'):
            check_version('/owner/opencode', {}, tmp_path)
    create(protocol)
    protocol.route('POST', f'/api/session/{SID}/prompt', {'id':'msg_input-1', 'text':'question'})
    app.commit('committed only')
    before = len(app.calls)
    recovered = OpenCodeV2Protocol(LocalGateway(app), protocol.directory, profile=protocol.profile)
    assert recovered.route('GET', f'/api/session/{SID}/message').body == protocol.route('GET', f'/api/session/{SID}/message').body
    assert set(app.calls[before:]) == {'configuration', 'snapshot'}


def test_candidate_real_loopback_http_and_sse_do_not_dispatch_on_reconnect(context):
    import base64
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from cpn.frontend.opencode_http import OpenCodeHTTPServer
    app, protocol = context
    server = OpenCodeHTTPServer(protocol)
    server.start()
    def request(path, body=None, authorization=None):
        headers = {'Authorization': authorization or server.authorization}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        return urlopen(Request(server.url + path, data=None if body is None else json.dumps(body).encode(),
                               headers=headers), timeout=3)
    try:
        bad = 'Basic ' + base64.b64encode(('rpnh:' + server.password).encode()).decode()
        with pytest.raises(HTTPError) as error:
            request('/api/info', authorization=bad)
        assert error.value.code == 401
        with request('/api/session', {}) as response:
            assert json.load(response)['data']['id'] == SID
        with request(f'/api/session/{SID}/prompt', {'id':'msg_wire-input', 'text':'wire question'}) as response:
            assert json.load(response)['data']['id'] == 'msg_wire-input'
        before = app.calls.count('submit')
        for _ in range(2):
            with request('/api/event') as response:
                assert response.headers['Content-Type'] == 'text/event-stream'
                assert response.readline().startswith(b'id: ')
                event = json.loads(response.readline().removeprefix(b'data: '))
                assert event['type'] == 'server.connected' and event['data'] == {}
        assert app.calls.count('submit') == before == 1
        with request(f'/api/session/{SID}/interrupt', {}) as response:
            assert json.load(response) == {'interrupted': True}
    finally:
        server.close()


@pytest.mark.parametrize('identities', [
    ['msg_input-1', 'msg_input-1.rpnh-answer', 'msg_input-1.rpnh-answer.rpnh-answer'],
    [None, 'msg_legacy', 'msg_legacy.rpnh-answer'],
    ['msg_legacy', None, 'msg_legacy.rpnh-answer'],
])
def test_history_ids_are_disjoint_and_cold_rebuild_stable(context, identities):
    app, protocol = context
    create(protocol)
    path = f'/api/session/{SID}/prompt'
    user_ids = []
    for n, identity in enumerate(identities):
        body = {'text': f'question {n}'}
        if identity is not None:
            body['id'] = identity
        response = protocol.route('POST', path, body).body['data']
        user_ids.append(response['id'])
        if identity is not None:
            assert response['id'] == identity
        app.commit(f'answer {n}')
        history = protocol.route('GET', f'/api/session/{SID}/message').body['data']
        ids = [m['id'] for m in history]
        assert len(ids) == len(set(ids))
    fresh = OpenCodeV2Protocol(LocalGateway(app), protocol.directory, profile=protocol.profile)
    fresh.connected_event()
    assert fresh.route('GET', f'/api/session/{SID}/message').body['data'] == history
    assert [m['id'] for m in history if m['type'] == 'user'] == user_ids
    before = app.calls.count('submit')
    for identity in [history[1]['id'], f'msg_rpnh/{SID}/999/user']:
        with pytest.raises(FrontendError, match='identity'):
            fresh.route('POST', path, {'id': identity, 'text': 'cannot occupy projection namespace'})
    assert app.calls.count('submit') == before


def test_history_preserves_model_evidence_and_effort(context):
    app, protocol = context
    create(protocol)
    protocol.route('POST', f'/api/session/{SID}/prompt', {'id': 'msg_model-evidence', 'text': 'hello'})
    app.commit('answer')
    for evidence, basis in [('frontend-request/v3', 'registered_frontend_request'),
                            ('unavailable', 'session_current_selection')]:
        app.views[0]['turns'][0]['model_evidence'] = evidence
        history = protocol.route('GET', f'/api/session/{SID}/message').body['data']
        for message in history:
            assert message['metadata']['rpnh_turn_model_evidence'] == evidence
            assert message['metadata']['rpnh_turn_model_display_basis'] == basis
        assert history[1]['model']['variant'] == 'medium'


def test_non_v2_identity_rejected_and_existing_history_not_rewritten(context):
    app, protocol = context
    create(protocol)
    path = f'/api/session/{SID}/prompt'
    with pytest.raises(FrontendError, match='msg_'):
        protocol.route('POST', path, {'id': 'old-explicit', 'text': 'hello'})
    assert 'submit' not in app.calls
    # Simulate an existing turn accepted by another frontend; keep its ID/key,
    # rather than silently replacing user references to satisfy the new schema.
    app.submit(SID, 'hello', 'explicit:old-explicit')
    app.commit('answer')
    fresh = OpenCodeV2Protocol(LocalGateway(app), protocol.directory, profile=protocol.profile)
    with pytest.raises(FrontendError, match='cannot be preserved') as error:
        fresh.route('GET', f'/api/session/{SID}/message')
    assert error.value.status == 409
    assert app.views[0]['turns'][0]['key'] == 'explicit:old-explicit'


@pytest.mark.parametrize('ids', [
    ('msg_collision', 'msg_collision.rpnh-answer'),
    ('msg_collision.rpnh-answer', 'msg_collision'),
])
def test_legacy_collision_rejected_before_submit_without_rewriting_ids(context, ids):
    import copy
    app, candidate = context
    protocol = OpenCodeProtocol(LocalGateway(app), candidate.directory)
    protocol.route('POST', '/session', {})
    path = f'/session/{SID}/message'
    first = {'messageID': ids[0], 'parts': [{'type': 'text', 'text': 'first'}]}
    protocol.route('POST', path, first)
    app.commit('answer')
    original = protocol.route('GET', f'/session/{SID}/message').body
    assert [m['info']['id'] for m in original] == [ids[0], ids[0] + '.rpnh-answer']
    # Same key is still routed to the original owner for dedup/conflict checks.
    protocol.route('POST', path, first)
    assert len(app.views[0]['turns']) == 1
    before, calls = copy.deepcopy(app.views), app.calls.count('submit')
    cold = OpenCodeProtocol(LocalGateway(app), candidate.directory)
    with pytest.raises(FrontendError, match='collision'):
        cold.route('POST', path, {'messageID': ids[1], 'parts': [{'type': 'text', 'text': 'second'}]})
    assert app.views == before and app.calls.count('submit') == calls
    assert cold.route('GET', f'/session/{SID}/message').body == original


def test_legacy_existing_collision_fails_history_and_events_without_mutation(context):
    import copy
    app, candidate = context
    create(candidate)
    for identity in ['msg_collision', 'msg_collision.rpnh-answer']:
        app.submit(SID, identity, 'explicit:' + identity)
        app.commit('answer')
    before, calls = copy.deepcopy(app.views), app.calls.count('submit')
    cold = OpenCodeProtocol(LocalGateway(app), candidate.directory)
    with pytest.raises(FrontendError, match='collision'):
        cold.route('GET', f'/session/{SID}/message')
    with pytest.raises(FrontendError, match='collision'):
        cold.events()
    assert app.views == before and app.calls.count('submit') == calls


def test_legacy_concurrent_colliding_admissions_share_existing_protocol_lock(context):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    app, candidate = context
    protocol = OpenCodeProtocol(LocalGateway(app), candidate.directory)
    protocol.route('POST', '/session', {})
    start = threading.Barrier(2)
    def submit(identity):
        start.wait()
        try:
            protocol.route('POST', f'/session/{SID}/message',
                           {'messageID': identity, 'parts': [{'type': 'text', 'text': identity}]})
            return 'accepted'
        except FrontendError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ['msg_race', 'msg_race.rpnh-answer']))
    assert outcomes.count('accepted') == 1
    assert sum('collision' in item for item in outcomes) == 1
    assert app.calls.count('submit') == 1
    assert len(app.views[0]['turns']) == 1


def test_legacy_real_gateway_can_notify_during_admission(context):
    from concurrent.futures import ThreadPoolExecutor
    from cpn.rpnh.frontend_application import FrontendGateway
    app, candidate = context
    attached = []
    gateway = FrontendGateway(lambda: app,
                              on_change=lambda: attached[0].notify() if attached else None)
    try:
        protocol = OpenCodeProtocol(gateway, candidate.directory)
        attached.append(protocol)
        protocol.route('POST', '/session', {})
        before = protocol.revision()
        # Real owner-thread callback runs before the submit Future resolves.
        # Holding its notification condition across call would deadlock until
        # gateway timeout; an independent local admission mutex avoids that.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(protocol.route, 'POST', f'/session/{SID}/message',
                                  {'messageID': 'msg_real_gateway',
                                   'parts': [{'type': 'text', 'text': 'hello'}]})
            assert pending.result(timeout=2).status == 200
        assert protocol.revision() > before
        assert len(app.views[0]['turns']) == 1
        assert app.calls.count('submit') == 1
    finally:
        gateway.close()
