"""Offline B5 consumers. Browser execution requires an explicit existing binary.

No executors, providers, terminal tools or external workers are dispatched.
The browser test never changes Chromium sandbox settings.
"""
from contextlib import contextmanager
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from unittest.mock import patch

import pytest

from normal_child_root_matrix_fixture import prepare_two_owner_root, register_products
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import _ProjectionHTTPServer
from cpn.rpnh.collaboration.worksets import CompleteWorkset
from cpn.rpnh.registry.execution_runtime import ExecutionRuntime
from cpn.rpnh.registry.execution_net import (
    ExecutionNetDefinition, ExecutionTransition, ExecutionInputArc, ExecutionOutputArc,
)


def save(directory, name, value):
    (directory / name).write_text(json.dumps(value, indent=2, default=str) + '\n')


def authority(core):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return {table: [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('registry_meta', 'transactions', 'objects', 'events', 'relations',
                              'firing_publications', 'firing_temporary_members', 'stream_heads', 'snapshots')}


def immutable_bytes(core):
    root = core.object_store.root
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


@pytest.fixture(scope='module')
def consumers(tmp_path_factory):
    directory = tmp_path_factory.mktemp('b5-consumers')
    evidence = Path(os.environ.get('B5_EVIDENCE', directory))
    evidence.mkdir(parents=True, exist_ok=True)
    fixtures, views, providers = {}, {}, {}
    for kind in ('v1', 'v2'):
        fixture = prepare_two_owner_root(directory, prefix='b5-' + kind)
        products = register_products(fixture.target, fixture.root, 'b5:' + kind + ':products')
        if kind == 'v1':
            fixture.target.succeed(products, command_id='b5:v1:complete',
                workset_action=CompleteWorkset(fixture.expected, products.outputs[0].port_id))
        else:
            runtime = ExecutionRuntime(fixture.target._core)
            definition = ExecutionNetDefinition('b5.consumer.child', ('done', 'pending'),
                (ExecutionTransition('finish', 'pure'),), (ExecutionInputArc('pending', 'finish', 1),),
                (ExecutionOutputArc('finish', 'done', 1),), 'pending', 1, ('done',))
            child = runtime.instantiate(parent=fixture.parent, definition=definition, idempotency_key='b5:child')
            child = runtime.start(instance_ref=child.instance_ref, parent=fixture.parent,
                checkpoint_ref=child.checkpoint.checkpoint_ref, transition_id='finish',
                idempotency_key='b5:child:start', materialization_key=None)
            child = runtime.settle(instance_ref=child.instance_ref, parent=fixture.parent,
                checkpoint_ref=child.checkpoint.checkpoint_ref, firing_ref=child.checkpoint.active_firing_refs[0],
                idempotency_key='b5:child:settle', evidence_refs=(products.outputs[0].resource_ref.as_version_ref(),))
            assert child.checkpoint.map_ready
            completed = fixture.target_worksets.complete_normal_children(expected=fixture.expected,
                outputs=products, output_port=products.outputs[0].port_id, command_id='b5:v2:complete')
            save(evidence, 'actual-normal-completion.json', completed)
        fixtures[kind] = fixture
        providers[kind] = RegistryDashboard(fixture.target._core.run_dir, catalog=fixture.target._core.catalog)
    cores = {f'{kind}-{name}': getattr(fixture, name)._core
             for kind, fixture in fixtures.items() for name in ('source', 'target')}
    before = {name: authority(core) for name, core in cores.items()}
    objects = {name: immutable_bytes(core) for name, core in cores.items()}
    save(evidence, 'authority-before.json', before)
    for kind, provider in providers.items():
        assert provider._open().read_only
        views[kind] = provider.worksets()
        assert views[kind]['schema_version'] == 'rpnh/workset_view/' + kind
        assert views[kind]['current'][0]['state'] == 'completed'
        assert views[kind]['current'][0]['root_terminal_ref']['ref']['entity_type'] == 'collaboration_root_terminal/' + kind
        assert views[kind]['current'][0]['root_terminal_evidence_ref'] is None
    save(evidence, 'genuine-views.json', views)
    try:
        yield providers, views, evidence
    finally:
        after = {name: authority(core) for name, core in cores.items()}
        save(evidence, 'authority-after.json', after)
        assert after == before
        assert {name: immutable_bytes(core) for name, core in cores.items()} == objects
        save(evidence, 'read-only-result.json', {'all_four_registry_authorities_equal': True,
            'all_immutable_object_bytes_equal': True, 'object_files': {k: len(v) for k, v in objects.items()}})


@contextmanager
def native_server(provider):
    server = _ProjectionHTTPServer(provider, '127.0.0.1', 0, False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def request(server, method='GET', path='/api/v2/worksets'):
    connection = http.client.HTTPConnection('127.0.0.1', server.bound_port, timeout=10)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return {'status': response.status, 'headers': dict(response.getheaders()), 'body': response.read().decode()}
    finally:
        connection.close()


def test_genuine_registry_native_http(consumers):
    providers, views, evidence = consumers
    receipts = {}
    for kind, provider in providers.items():
        with native_server(provider) as server:
            rows = receipts[kind] = {'origin': server.origin}
            rows['get'] = request(server)
            assert rows['get']['status'] == 200 and json.loads(rows['get']['body']) == views[kind]
            rows['head'] = request(server, 'HEAD')
            assert rows['head']['status'] == 200 and rows['head']['body'] == ''
            rows['post'] = request(server, 'POST')
            assert rows['post']['status'] == 405
            rows['query'] = request(server, path='/api/v2/worksets?cut=0')
            assert rows['query']['status'] == 400
            rows['module'] = request(server, path='/worksets.mjs')
            assert rows['module']['status'] == 200
            assert rows['module']['body'] == (Path(__file__).parents[1] / 'cpn/frontend/static/worksets.mjs').read_text()
    # Finite seam faults in the *read result*, not fabricated stored corruption.
    # Actual RegistryDashboard -> workset_view -> HTTP 409 is exercised each time.
    import cpn.rpnh.collaboration.root_terminals as root_terminals
    original = root_terminals.read_root_terminal_snapshot
    injections = {}
    for case in ('root', 'seal', 'cut'):
        calls = []
        def inconsistent(*args, **kwargs):
            result = deepcopy(original(*args, **kwargs))
            if case == 'root':
                result['root']['record_ref']['source_id'] = views['v1']['source_id']
            elif case == 'seal':
                result['seal']['execution_child_seal_ref']['entity_type'] = 'execution_children_sealed/v1'
            else:
                result['verified_at_cut'] -= 1
            calls.append(case)
            return result
        with patch.object(root_terminals, 'read_root_terminal_snapshot', inconsistent):
            with native_server(providers['v2']) as server:
                response = request(server)
                assert response['status'] == 409
                assert json.loads(response['body']) == {'error': 'workset_unavailable'}
        assert calls == [case]
        injections[case] = {'read_result_injection': True, 'calls': calls, 'response': response}
    with native_server(providers['v2']) as server:
        recovered = request(server)
        assert recovered['status'] == 200 and json.loads(recovered['body']) == views['v2']
    save(evidence, 'native-http.json', {'positive': receipts, 'read_seam_refusals': injections, 'recovered': recovered})


def test_genuine_registry_js_consumer(consumers):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node consumer NOT RUN: Node.js is not available')
    providers, views, evidence = consumers
    command = [node, str(Path(__file__).with_name('normal_child_consumers.node.mjs')), str(evidence / 'genuine-views.json')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    (evidence / 'node.stdout').write_text(result.stdout)
    (evidence / 'node.stderr').write_text(result.stderr)
    save(evidence, 'node-command.json', {'argv': command, 'cwd': str(Path.cwd()), 'exit': result.returncode})
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['genuine_registry_js']['positive_views'] == 2
    assert len(report['genuine_registry_js']['rejected']) == 6
    assert report['separately_mocked_dom'] == {'positive_views': 4, 'rejected_views': 18, 'rendered_root_seal': True}


def test_actual_browser_normal_root(consumers):
    binary = os.environ.get('B5_BROWSER_BINARY')
    if not binary:
        pytest.skip('Actual browser NOT RUN: B5_BROWSER_BINARY is not set; native launch refusal retained separately')
    node = shutil.which('node')
    if node is None:
        pytest.fail('B5_BROWSER_BINARY requires Node.js for the browser helper')
    providers, views, evidence = consumers
    with native_server(providers['v1']) as legacy, native_server(providers['v2']) as normal:
        command = [node, str(Path(__file__).with_name('normal_child_consumers.browser.mjs')),
                   binary, legacy.origin, normal.origin, str(evidence)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (evidence / 'browser.stdout').write_text(result.stdout)
    (evidence / 'browser.stderr').write_text(result.stderr)
    save(evidence, 'browser-command.json', {'argv': command, 'exit': result.returncode})
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['native_dom'] and report['positive_views'] == 2 and len(report['rejected']) == 6
