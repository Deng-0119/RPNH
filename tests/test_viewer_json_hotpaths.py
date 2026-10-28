"""Request-local JSON fast paths preserve exact bytes and all output gates."""
from collections import UserDict
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from cpn.frontend import server
from test_net_viewer import _projection


class Text(str):
    pass


class Record(dict):
    pass


@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('value', [
    [{'label': 'same', 'value': True, 'n': 1}] * 90,
    [{'中文': '🙂', 'control\n': '\x00\\"', 'value': None}] * 40,
    {True: (None, False, -12), None: 'value', 2.5: 'number'},
    {Text('key'): [Text('value'), Record(key='value')]},
    {'large': 'é' * 17000, 'tail': [{'key': 'value'}] * 4},
])
def test_exact_count_and_budget_after_repeated_keys(monkeypatch, canonical, value):
    opts = dict(sort_keys=True, separators=(',', ':')) if canonical else {}
    # Mixed key types cannot be sorted by Python's canonical encoder.
    if canonical and isinstance(value, dict) and any(not isinstance(k, str) for k in value):
        with pytest.raises(TypeError):
            server._json_body(value, canonical=True)
        return
    expected = json.dumps(value, ensure_ascii=False, allow_nan=False, **opts).encode('utf-8')
    assert server._preflight_json(value, compact=canonical) == len(expected)
    monkeypatch.setattr(server, '_MAX_RESPONSE_BYTES', len(expected))
    assert server._json_body(value, canonical=canonical) == expected
    monkeypatch.setattr(server, '_MAX_RESPONSE_BYTES', len(expected) - 1)
    with pytest.raises(server._ResponseLimitError):
        server._json_body(value, canonical=canonical)


def test_mapping_and_subclass_keep_complete_projection_validation():
    original = _projection()
    original['source'] = UserDict(original['source'])
    original['summary'] = UserDict(original['summary'])
    original['nodes'] = [UserDict(node) for node in original['nodes']]
    original['edges'] = [Record(edge) for edge in original['edges']]
    wrapped = UserDict(original)
    assert server._validate_projection(wrapped) is wrapped
    original['nodes'][0]['label'] = None
    with pytest.raises(ValueError):
        server._validate_projection(wrapped)
    plain = _projection()
    plain['edges'][0]['target'] = 'missing'
    assert server.handle_request(lambda: plain, 'GET', '/api/v1/net').status != 200


def test_projection_reads_summary_once():
    class Counting(Record):
        reads = 0
        def get(self, key, *default):
            if key == 'summary':
                self.reads += 1
            return super().get(key, *default)
    item = Counting(_projection())
    item['summary'] = UserDict(item['summary'])
    server._validate_projection(item)
    assert item.reads == 1


def test_no_request_cache_retains_changed_values_or_shares_threads():
    values = [[{'shared': f'value {i}', 'item': i}] * 10 for i in range(80)]
    expected = [json.dumps(v, ensure_ascii=False, allow_nan=False).encode() for v in values]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(server._json_body, values)) == expected
    value = [{'shared': 'before'}]
    assert b'before' in server._json_body(value)
    value[0]['shared'] = 'after'
    assert b'after' in server._json_body(value)


def test_shared_values_cycles_nonfinite_and_depth_still_rejected():
    shared = {'key': 'value'}
    assert server._json_body([shared, shared]) == json.dumps([shared, shared]).encode()
    shared['cycle'] = shared
    with pytest.raises(ValueError):
        server._json_body(shared)
    with pytest.raises(ValueError):
        server._json_body({'key': float('nan')})
    value = 'leaf'
    for _ in range(130):
        value = [value]
    with pytest.raises(server._ResponseLimitError):
        server._json_body(value)
