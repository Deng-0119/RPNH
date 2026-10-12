"""Exact registered JSON edits through the original semantic write boundary."""
from copy import deepcopy
import json
from pathlib import Path
import re

import pytest
from jsonschema import Draft7Validator

from cpn.components.agent_loop.action_execution import OPTIONAL_TOOL_BINDINGS
from cpn.components.agent_loop.tool_catalog import TOOL_ARGUMENT_SCHEMAS
from cpn.components.agent_loop.workspace import _replace_registered_json
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_optional_context_compaction import _configure_offline_task, _response
from test_workspace_write_atomicity import _WriteProductHarness


def test_schema_source_only_and_existing_copy_variant():
    schema = TOOL_ARGUMENT_SCHEMAS['write_file']
    assert OPTIONAL_TOOL_BINDINGS['write_file'][1]['contracts']['arguments'] is schema
    assert 'json_replacements' in OPTIONAL_TOOL_BINDINGS['write_file'][1]['contracts']['description']
    validator = Draft7Validator(schema)
    base = {'path': 'outputs/result.json', 'description': 'Edited source.'}
    source = {'resource_id': 'resource:' + '1' * 32,
              'resource_version_id': 'resource_version:' + '2' * 32}
    edits = [{'pointer': '/value', 'value': None}]
    assert validator.is_valid({**base, 'source_resource_ref': source})
    assert validator.is_valid({**base, 'source_resource_ref': source, 'json_replacements': edits})
    assert not validator.is_valid({**base, 'content': '{}', 'json_replacements': edits})
    assert not validator.is_valid({**base, 'source_resource_ref': source,
                                   'json_replacements': [{'pointer': '/value'}]})


def test_pointer_escaping_list_and_argument_values_are_lossless():
    original = {'a/b': {'~key': [0, {'n': 1}]}, '~1': True, '': 'empty', 'untouched': '汉字'}
    payload = canonical_json(original)
    edits = [{'pointer': '/a~1b/~0key/1', 'value': {'n': 2}},
             {'pointer': '/a~1b/~0key/1/n', 'value': 3},
             {'pointer': '/~01', 'value': False}, {'pointer': '/', 'value': None}]
    before = deepcopy(edits)
    expected = {'a/b': {'~key': [0, {'n': 3}]}, '~1': False, '': None, 'untouched': '汉字'}
    assert json.loads(_replace_registered_json(payload, edits)) == expected
    assert edits == before
    assert json.loads(payload) == original
    assert json.loads(_replace_registered_json(payload, [{'pointer': '', 'value': [1, 2]}])) == [1, 2]


def test_invalid_targets_never_reach_semantic_publication(tmp_path):
    source = b'{"items":[{"value":1}],"scalar":false}'
    service = _WriteProductHarness(
        tmp_path, lambda *_: pytest.fail('must not publish'),
        preflight=lambda *_: pytest.fail('must not reach semantic preflight'))
    service._deliver = lambda *_: (None, None, source)
    args = {'path': 'outputs/result.json', 'description': 'Invalid replacement.',
            'source_resource_ref': {'resource_id': 'resource:' + '1' * 32,
                                    'resource_version_id': 'resource_version:' + '2' * 32},
            'output_port_id': 'worker.result', 'outcome_id': 'complete'}
    for pointer in ('/missing', '/items/1', '/items/-', '/items/01', '/items/~2', '/scalar/x'):
        with pytest.raises(ValueError):
            service._write_product(service.execution, service.loop,
                {**args, 'json_replacements': [{'pointer': pointer, 'value': 2}]}, 'bad-edit')
    assert list(tmp_path.iterdir()) == []


class _NativeEditingPort:
    """Synthetic model responses; all delivery/write/publication gates are native."""
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.calls = 0
        self.source_ref = None
        self.missing_target_no_product = False

    def request_once(self, attempt):
        self.calls += 1
        envelope = json.loads(attempt.canonical_request_bytes)
        if self.calls == 1:
            path = re.search(r'registered_resources/[a-f0-9]{32}/[a-f0-9]{32}/content',
                             json.dumps(envelope['messages'])).group()
            calls = [{'id': 'read', 'name': 'read_file',
                      'arguments': json.dumps({'path': path, 'max_chars': 64})}]
        elif self.calls in (2, 3):
            read = next(json.loads(m['content']) for m in envelope['messages']
                        if m.get('role') == 'tool' and m.get('tool_call_id') == 'read')
            self.source_ref = read['resource_ref']
            arguments = {'path': 'outputs/result.json', 'description': 'Model-selected exact JSON replacement.',
                         'source_resource_ref': self.source_ref, 'output_port_id': 'main.result',
                         'outcome_id': 'complete'}
            if self.calls == 2:
                arguments['json_replacements'] = [{'pointer': '/components/74/config/value', 'value': 8}]
                calls = [{'id': 'missing-target', 'name': 'write_file', 'arguments': json.dumps(arguments)}]
            else:
                core = _RegistryCore(self.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
                products = [row for row in core.event_store.object_rows_by_type('resource_version/v1')
                            if json.loads(row['metadata_json']).get('descriptors', {}).get('output_port_id')]
                assert products == []
                self.missing_target_no_product = True
                arguments['json_replacements'] = [
                    {'pointer': '/components/3/config/value', 'value': 8},
                    {'pointer': '/a~1b/~0key/1', 'value': '模型选择'}]
                calls = [{'id': 'edited-product', 'name': 'write_file', 'arguments': json.dumps(arguments)},
                         {'id': 'complete', 'name': 'complete_interaction', 'arguments': '{}'}]
        else:
            raise AssertionError('unexpected extra synthetic request')
        return _response(tool_calls=calls, finish_reason='tool_calls')

    def close(self):
        pass


def test_native_registered_text_edit_after_missing_target(tmp_path, monkeypatch):
    original = {'components': [{'name': f'n{i}', 'config': {'value': i, 'untouched': 'x' * 1350}}
                               for i in range(74)], 'a/b': {'~key': ['keep', 'before']}}
    source_text = canonical_json(original).decode()
    assert len(source_text) > 100000
    run_dir = tmp_path / 'run'
    port = _NativeEditingPort(run_dir)
    selection_path = _configure_offline_task(tmp_path, monkeypatch, port)
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir, prompt=source_text,
        stages=(AgentStage('main', 'Apply the exact supplied JSON replacements.'),),
        execution_config_path=selection_path, max_attempts_per_stage=3))
    assert result['stop_reason'] == 'terminal'
    assert port.calls == 3 and port.missing_target_no_product
    expected = deepcopy(original)
    expected['components'][3]['config']['value'] = 8
    expected['a/b']['~key'][1] = '模型选择'
    assert json.loads(result['output']) == expected
    core = _RegistryCore(run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    source_version = TypedId.parse(port.source_ref['resource_version_id'], expected='resource_version')
    source = core.object_store.read_registered(core.get_version(source_version))
    assert json.loads(source) == source_text
    products = [json.loads(row['metadata_json']) for row in core.event_store.object_rows_by_type('resource_version/v1')
                if json.loads(row['metadata_json']).get('descriptors', {}).get('output_port_id')]
    product, = products
    assert product['reference_provenance']['derived_from_refs'] == [{
        'entity_type': 'resource_version/v1',
        'logical_id': port.source_ref['resource_id'],
        'version_id': port.source_ref['resource_version_id'],
    }]
