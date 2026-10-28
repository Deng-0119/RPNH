from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import patch

import pytest

from cpn.components.agent_loop import tool_catalog as catalog
from cpn.components.agent_loop._parsed_catalog_cache import (
    PARSED_CATALOG_CACHE, ParsedCatalogCache, _estimated_size,
)


@pytest.fixture(autouse=True)
def empty_cache():
    PARSED_CATALOG_CACHE.clear()
    yield
    PARSED_CATALOG_CACHE.clear()


def payload():
    return catalog.build_agent_tool_catalog().payload


def test_public_paths_share_successful_parse_and_keep_fresh_descriptors():
    data = payload()
    PARSED_CATALOG_CACHE.clear()
    original = catalog._parse_agent_tool_catalog_document_uncached
    with patch.object(catalog, '_parse_agent_tool_catalog_document_uncached', wraps=original) as parse:
        first = catalog.parse_agent_tool_catalog(data)
        second = catalog.AgentToolCatalog(first.catalog_ref, data, first.tool_names)
        expected = first.tool_descriptors
        changed = first.tool_descriptors
        changed[0]['arguments']['poison'] = {'nested': True}
        assert second.tool_descriptors == expected
        assert 'poison' not in first.argument_schemas[first.tool_names[0]]
        assert parse.call_count == 1


def test_same_id_changed_payload_not_reused():
    data = payload()
    doc = json.loads(data)
    doc['tools'][0]['description'] += ' Changed.'
    changed = catalog._canonical_catalog_document(doc)
    assert catalog.parse_agent_tool_catalog(data).tool_descriptors != catalog.parse_agent_tool_catalog(changed).tool_descriptors


def test_public_names_and_canonical_equality_not_skipped_on_hit():
    data = payload()
    parsed = catalog.parse_agent_tool_catalog(data)
    with pytest.raises(ValueError, match='names differ'):
        catalog.AgentToolCatalog(parsed.catalog_ref, data, ())
    noncanonical = json.dumps(json.loads(data), indent=2).encode()
    for _ in range(2):
        with pytest.raises(ValueError, match='canonical'):
            catalog.parse_agent_tool_catalog(noncanonical)


@pytest.mark.parametrize('data', [b'', b'{', b'{"tools":[],"tools":[]}', b'[]', b'\xff', 'not bytes'])
def test_invalid_input_failures_not_cached(data):
    original = catalog._parse_agent_tool_catalog_document_uncached
    with patch.object(catalog, '_parse_agent_tool_catalog_document_uncached', wraps=original) as parse:
        for _ in range(2):
            with pytest.raises(ValueError):
                catalog.parse_agent_tool_catalog(data)
        assert parse.call_count == 2


def test_bytes_subclass_preserves_decode_behavior_without_cache():
    class Payload(bytes):
        calls = 0
        def decode(self, *args, **kwargs):
            type(self).calls += 1
            return super().decode(*args, **kwargs)
    data = Payload(payload())
    for _ in range(2):
        catalog._parse_agent_tool_catalog_document(data)
    assert Payload.calls == 2


def test_entry_and_byte_eviction_and_oversize_bypass():
    calls = []
    def parse(data):
        calls.append(data)
        return {'nested': [data.decode()]}
    one_size = _estimated_size((b'a', parse(b'a'))) + 256
    calls.clear()
    cache = ParsedCatalogCache(max_entries=1, max_estimated_bytes=one_size * 3)
    for key in (b'a', b'b', b'a'):
        cache.parse(key, parse)
    assert calls == [b'a', b'b', b'a']
    assert len(cache._entries) == 1
    cache = ParsedCatalogCache(max_entries=50, max_estimated_bytes=one_size)
    cache.parse(b'a', parse)
    cache.parse(b'b', parse)
    assert cache._estimated_bytes <= one_size
    assert len(cache._entries) == 1
    calls.clear()
    for _ in range(2):
        cache.parse(b'x' * (one_size + 1), parse)
    assert len(calls) == 2
    assert cache._estimated_bytes <= one_size


def test_parallel_descriptor_mutation_isolated():
    value = catalog.build_agent_tool_catalog(tool_names=tuple(sorted(catalog.AGENT_TOOL_NAMES)))
    expected = value.tool_descriptors
    def read(i):
        got = value.tool_descriptors
        got[0]['description'] = str(i)
        return got[0]['description']
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(read, range(32))) == [str(i) for i in range(32)]
    assert value.tool_descriptors == expected
    assert len(expected) == 15
