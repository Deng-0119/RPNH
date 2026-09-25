"""Only successful schema syntax is memoized; authorities stay uncached."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from jsonschema import Draft7Validator, SchemaError, ValidationError
from cpn.rpnh._schema_validation import (
    _check_canonical_schema, _MAX_SCHEMA_CHARS, check_draft7_schema,
)
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError


@pytest.fixture(autouse=True)
def clear_cache():
    _check_canonical_schema.cache_clear()
    yield
    _check_canonical_schema.cache_clear()


def schema():
    return {'$schema':'http://json-schema.org/draft-07/schema#',
            '$id':'cache_test/value/v1','type':'object',
            'properties':{'x':{'type':'integer'}}, 'required':['x'],
            'additionalProperties':False}


def test_exact_copies_share_syntax_check_not_instance_validation():
    source = schema()
    check_draft7_schema(source)
    check_draft7_schema(copy.deepcopy(source))
    assert _check_canonical_schema.cache_info().hits == 1
    with pytest.raises(ValidationError):
        Draft7Validator(source).validate({'x':'wrong'})
    assert _check_canonical_schema.cache_info().currsize == 1


def test_mutating_same_id_cannot_reuse_prior_success():
    source = schema()
    check_draft7_schema(source)
    source['properties']['x']['type'] = 'not-a-json-type'
    for _ in range(2):
        with pytest.raises(SchemaError):
            check_draft7_schema(source)
    assert _check_canonical_schema.cache_info().currsize == 1
    assert _check_canonical_schema.cache_info().hits == 0


def test_equal_schemas_do_not_grant_registration_or_cross_catalog_authority():
    a, b = SchemaCatalog(), SchemaCatalog()
    a.register_schema(schema()['$id'], schema())
    a.validate_schema_ref(schema()['$id'], {'x':1})
    with pytest.raises(SchemaGovernanceError, match='not in'):
        b.validate_schema_ref(schema()['$id'], {'x':1})
    with pytest.raises(SchemaGovernanceError, match='violates'):
        a.validate_schema_ref(schema()['$id'], {'x':'bad'})
    changed = schema(); changed['properties']['x']['type']='string'
    with pytest.raises(SchemaGovernanceError, match='immutable'):
        a.register_schema(schema()['$id'], changed)


def test_new_catalog_rechecks_mechanical_file_bytes(tmp_path, monkeypatch):
    source = SchemaCatalog._schema_root()
    import shutil
    target=tmp_path/'schemas';shutil.copytree(source,target)
    monkeypatch.setattr(SchemaCatalog, '_schema_root', staticmethod(lambda:target))
    SchemaCatalog()
    document=next(target.glob('registry_v1/task.v1.schema.json'))
    value=json.loads(document.read_text());value['type']='not-a-json-type'
    document.write_text(json.dumps(value))
    with pytest.raises(SchemaGovernanceError, match='invalid'):
        SchemaCatalog()


def test_bounded_cache_and_large_schema_bypass():
    for index in range(300):
        check_draft7_schema({'title':str(index),'type':'integer'})
    assert _check_canonical_schema.cache_info().currsize == 256
    before=_check_canonical_schema.cache_info()
    check_draft7_schema({'description':'x'*(_MAX_SCHEMA_CHARS+1)})
    assert _check_canonical_schema.cache_info() == before


def test_parallel_checks_do_not_share_mutable_results():
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(check_draft7_schema, [schema() for _ in range(16)])) == [None]*16
    assert _check_canonical_schema.cache_info().currsize == 1


@pytest.mark.parametrize('source', [True, False, {}, {'type':['integer','string']},
    {'type':'invalid'}, {'required':'x'}, {'properties':{'x':{'minimum':'bad'}}},
    {'type':('integer','string')}, {'properties':{1:{}}}])
def test_reference_meta_validator_agrees(source):
    def verdict(checker):
        try: checker(source)
        except Exception as exc: return type(exc)
        return None
    assert verdict(check_draft7_schema) == verdict(Draft7Validator.check_schema)


def test_warm_syntax_cache_does_not_skip_compiled_net_integrity():
    from cpn.rpnh.agent_tasks import AgentStage, build_agent_task_module, agent_task_registration
    from cpn.rpnh.compiler import compile_module
    from cpn.rpnh.executable_net import load_compiled_net
    from cpn.rpnh.petri_contracts import DeclarationError
    compiled = compile_module(build_agent_task_module((AgentStage('main','Answer.'),)),
                              agent_task_registration())
    document = compiled.to_dict()
    load_compiled_net(document)
    before = _check_canonical_schema.cache_info().hits
    key = next(iter(document['operation_handles']))
    document['operation_handles'][key] = 'op:99999'
    with pytest.raises(DeclarationError):
        load_compiled_net(document)
    assert _check_canonical_schema.cache_info().hits >= before
