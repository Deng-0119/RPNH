"""Catalog syntax memoization never replaces instance or authority checks."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from jsonschema import Draft7Validator

from cpn.components.agent_loop import tool_catalog, tool_validation
from cpn.rpnh._schema_validation import _check_canonical_schema


@pytest.fixture(autouse=True)
def clear_syntax_cache():
    _check_canonical_schema.cache_clear()
    yield
    _check_canonical_schema.cache_clear()


def payload(schema=None):
    return tool_catalog._canonical_catalog_document({
        "schema_version": "agent_tool_catalog/v1",
        "surface_kind": "parent_current",
        "workspace_scope": "firing_private_projection",
        "tools": [{"name": "example", "description": "Example tool.",
                   "arguments": schema or {
                       "$id": "example/v1", "type": "object",
                       "properties": {"value": {"type": "integer"}},
                       "required": ["value"], "additionalProperties": False}}],
    })


def validate(raw, schemas):
    return tool_validation.validate_agent_tool_call(
        loop_id="agent_loop:" + "0" * 32, turn_sequence=1, expected_revision=0,
        tool_call_id="call", tool_name="example", raw_arguments=raw,
        tool_argument_schemas=schemas)


def test_repeated_catalog_reads_and_calls_share_only_successful_syntax(monkeypatch):
    checks = []
    original = Draft7Validator.check_schema

    def counted(schema):
        checks.append(copy.deepcopy(schema))
        return original(schema)

    monkeypatch.setattr(Draft7Validator, "check_schema", counted)
    for _ in range(20):
        catalog = tool_catalog.parse_agent_tool_catalog(payload())
        assert catalog.tool_descriptors[0]["name"] == "example"
        schemas = catalog.argument_schemas
        assert isinstance(validate('{"value":1}', schemas),
                          tool_validation.ValidatedAgentToolAction)
        assert validate('{"value":"wrong"}', schemas).code == "arguments_invalid"
    assert len(checks) == 1
    assert _check_canonical_schema.cache_info().currsize == 1


def test_returned_descriptors_and_schemas_are_not_shared_mutable_cache():
    catalog = tool_catalog.parse_agent_tool_catalog(payload())
    descriptor = catalog.tool_descriptors[0]
    descriptor["arguments"]["properties"]["value"]["type"] = "string"
    assert catalog.argument_schemas["example"]["properties"]["value"]["type"] == "integer"
    schemas = catalog.argument_schemas
    schemas["example"]["required"].append("new")
    assert catalog.argument_schemas["example"]["required"] == ["value"]


def test_same_id_changed_content_and_unpermitted_tool_still_rejected():
    schema = json.loads(payload())["tools"][0]["arguments"]
    old = tool_catalog.parse_agent_tool_catalog(payload(schema))
    changed = copy.deepcopy(schema)
    changed["properties"]["value"]["type"] = "string"
    new = tool_catalog.parse_agent_tool_catalog(payload(changed))
    assert validate('{"value":1}', new.argument_schemas).code == "arguments_invalid"
    assert isinstance(validate('{"value":1}', old.argument_schemas),
                      tool_validation.ValidatedAgentToolAction)
    assert validate('{"value":1}', {}).code == "tool_not_permitted"
    changed["properties"]["value"]["type"] = "invalid"
    for _ in range(2):
        with pytest.raises(ValueError, match="argument schema is invalid"):
            tool_catalog.parse_agent_tool_catalog(payload(changed))


@pytest.mark.parametrize("raw", [
    '{"value":1}', '{"value":"bad"}', '{}', '{"value":1,"extra":2}',
    '{"value":1,"value":2}', '[]', 'invalid json',
])
def test_cached_and_original_checker_have_identical_call_verdicts(raw, monkeypatch):
    schemas = tool_catalog.parse_agent_tool_catalog(payload()).argument_schemas
    candidate = validate(raw, schemas)
    monkeypatch.setattr(tool_validation, "check_draft7_schema", Draft7Validator.check_schema)
    assert validate(raw, schemas) == candidate


def test_catalog_duplicate_fields_still_rejected_when_schema_is_warm():
    tool_catalog.parse_agent_tool_catalog(payload())
    duplicate = payload().replace(b'"value":{"type":"integer"}',
                                  b'"value":{"type":"integer","type":"string"}')
    with pytest.raises(ValueError, match="duplicate field"):
        tool_catalog.parse_agent_tool_catalog(duplicate)


def test_parallel_catalogs_keep_fresh_descriptors():
    def read(_):
        catalog = tool_catalog.parse_agent_tool_catalog(payload())
        descriptors = catalog.tool_descriptors
        assert isinstance(validate('{"value":1}', catalog.argument_schemas),
                          tool_validation.ValidatedAgentToolAction)
        return descriptors

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, range(16)))
    assert all(result == results[0] for result in results)
    assert len({id(result[0]) for result in results}) == 16
    assert _check_canonical_schema.cache_info().currsize == 1
