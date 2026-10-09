"""Fixed product-origin wire and paired Python documentation contracts.

These synthetic documents check shape only. They do not execute Registry
histories, provider calls, a viewer, or an installed artifact.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import re
import tomllib

import pytest
from jsonschema import Draft7Validator

from cpn.rpnh import collaboration
from cpn.rpnh.collaboration.registry_read_contracts import (
    IndexQuery, PRODUCT_ORIGIN_CONTRACT_REVISION, PRODUCT_ORIGIN_PAGE_SCHEMA,
    PRODUCT_ORIGIN_PROFILE, ReadLimits, document_digest,
    product_origin_delivery_contract, registry_read_contract_schema_data,
)


ROOT = Path(__file__).resolve().parents[1]
UNSUPPORTED = (
    'direct_derivations', 'calls_in_execution', 'observed_reads', 'formal_access',
    'declarations', 'parent_child', 'recursive_ancestors', 'content_influence',
)


def _id(kind):
    return kind + ':' + 'a' * 32


def _version(kind, version=1):
    return {'schema_version': 'rpnh/collaboration/source_version_ref/v1',
        'source_id': 'source-a', 'ref': {'entity_type': kind + '/v' + str(version),
            'logical_id': _id(kind), 'version_id': _id(kind + '_version')}}


def _resource():
    return {'schema_version': 'rpnh/collaboration/source_resource_ref/v1',
        'source_id': 'source-a', 'ref': {'resource_id': _id('resource'),
            'resource_version_id': _id('resource_version')}}


def _page():
    return {'schema_version': PRODUCT_ORIGIN_PAGE_SCHEMA,
        'profile': PRODUCT_ORIGIN_PROFILE, 'contract_revision': PRODUCT_ORIGIN_CONTRACT_REVISION,
        'root': _resource(), 'source_cut': {'source_id': 'source-a', 'cut_id': 'opaque-cut',
            'head': {'ordinal': 17, 'writer_fencing_epoch': 1},
            'reader_contract_version': 'rpnh/registry_read/v1'},
        'access_revision': 'opaque-access-revision',
        'root_proof': {'producer_invocation_ref': _version('invocation'),
            'transition_firing_ref': _version('transition_firing'),
            'firing_completion_ref': _version('firing_completion', 2),
            'operation_result_ref': _version('operation_result'),
            'operation_binding_ref': _version('operation_binding'), 'root_role': 'registered_output'},
        'rows': [
            {'role': 'start_input', 'position': 0, 'input_binding_ref': _version('petri_token'),
                'resource_ref': _resource(), 'evidence': {'start_event_id': _id('event'),
                    'start_transaction_id': _id('transaction'), 'start_ordinal': 9},
                'verification': {'binding_identity': 'exact_at_cut', 'resource_identity': 'exact_at_cut',
                    'target_record': 'not_requested', 'material': 'not_read'}},
            {'role': 'claim', 'token_ref': _version('petri_token'), 'resource_ref': None,
                'classification': 'non_consuming_claim',
                'evidence': {'transition_firing_ref': _version('transition_firing'),
                    'claim_marking_delta_ref': _version('marking_delta')},
                'verification': {'token_record': 'verified_at_cut',
                    'resource_target': 'not_requested', 'material': 'not_read'}},
        ],
        'coverage': {'scope': 'authorized_root_at_cut', 'state': 'complete', 'relations': {
            **{name: {'state': 'complete', 'witness': 'verified_at_cut'}
                for name in ('producer_execution', 'start_inputs', 'claims')},
            **{name: {'state': 'not_in_profile'} for name in UNSUPPORTED},
        }}, 'continuation': None}


@pytest.fixture
def validator():
    schemas, objects, paths = registry_read_contract_schema_data()
    assert not objects
    assert paths[PRODUCT_ORIGIN_PAGE_SCHEMA] == ROOT / 'cpn/schemas/rpnh/product_origin_page.v1.schema.json'
    schema = schemas[PRODUCT_ORIGIN_PAGE_SCHEMA]
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def test_contract_constants_complete_content_and_fresh_containers():
    contract = product_origin_delivery_contract()
    assert contract['profile'] == PRODUCT_ORIGIN_PROFILE == 'product_origin_v1'
    assert contract['page_schema'] == PRODUCT_ORIGIN_PAGE_SCHEMA == 'rpnh/product_origin_page/v1'
    assert contract['contract_revision'] == PRODUCT_ORIGIN_CONTRACT_REVISION == 2
    assert contract['request']['include_default'] == ('producer_execution', 'start_inputs', 'claims')
    assert contract['coverage']['unsupported_relations'] == UNSUPPORTED
    assert set(contract['page']['exact_fields']) == set(_page())
    assert {name for name, _ in contract['page']['root_proof']} == set(_page()['root_proof'])
    assert contract['cursor']['kind'] == 'product_origin'
    original_digest = document_digest(contract)
    contract['request']['defaults']['include'] = 'changed'
    assert document_digest(product_origin_delivery_contract()) == original_digest
    assert document_digest(contract) != original_digest


def test_composed_schema_and_existing_defaults():
    schemas, objects, paths = registry_read_contract_schema_data()
    assert set(schemas) == {'rpnh/registry_source_cut/v1', 'rpnh/registry_read_session_request/v1',
        'rpnh/registry_index_query/v1', PRODUCT_ORIGIN_PAGE_SCHEMA}
    assert set(paths) == set(schemas)
    assert objects == ()
    assert IndexQuery.__dataclass_fields__['page_size'].default == 100
    assert ReadLimits().max_page_size == 1000
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert 'schemas/**/*.json' in metadata['tool']['setuptools']['package-data']['cpn']
    assert 'recursive-include docs *.json *.md *.txt' in (ROOT / 'MANIFEST.in').read_text()


def test_optional_public_exports_keep_existing_lazy_map():
    for name in ('PRODUCT_ORIGIN_PROFILE', 'PRODUCT_ORIGIN_PAGE_SCHEMA', 'PRODUCT_ORIGIN_CONTRACT_REVISION'):
        assert collaboration._OPTIONAL_PUBLIC_EXPORTS[name] == '.registry_read_contracts'
        assert name in collaboration.__all__
    assert collaboration._OPTIONAL_PUBLIC_EXPORTS['query_product_origin_v1'] == '.registry_read_session'
    assert 'query_product_origin_v1' in collaboration.__all__
    assert collaboration._OPTIONAL_PUBLIC_EXPORTS['open_read_host_session'] == '.read_host_config'


@pytest.mark.parametrize('role', ['registered_output', 'invocation_produced_resource', 'operation_result'])
def test_schema_accepts_fixed_root_roles_and_both_row_shapes(validator, role):
    page = _page()
    page['root_proof']['root_role'] = role
    if role == 'operation_result':
        page['root'] = _version('operation_result')
    validator.validate(page)


def test_schema_accepts_producer_only_and_cumulative_partial(validator):
    page = _page()
    page['rows'] = []
    for name in ('start_inputs', 'claims'):
        page['coverage']['relations'][name] = {'state': 'not_in_profile'}
    validator.validate(page)
    page = _page()
    page['rows'] = page['rows'][:1]
    page['coverage']['state'] = 'partial'
    page['coverage']['relations']['claims']['state'] = 'partial'
    page['continuation'] = 'opaque-origin-cursor'
    validator.validate(page)
    # Empty requested Claims may already be complete while Start still pages.
    page['coverage']['relations']['claims']['state'] = 'complete'
    page['coverage']['relations']['start_inputs']['state'] = 'partial'
    validator.validate(page)


@pytest.mark.parametrize(('path', 'value'), [
    (('extra',), True),
    (('root_proof', 'outputs'), []),
    (('root_proof', 'root_role'), 'formal_output_verified'),
    (('root_proof', 'root_role'), 'operation_result'),
    (('root_proof', 'firing_completion_ref', 'ref', 'entity_type'), 'firing_completion/v1'),
    (('root',), _version('resource')),
    (('root', 'ref', 'resource_id'), _id('operation_result')),
    (('root', 'ref', 'resource_id'), _id('resource') + '\n'),
    (('source_cut', 'head', 'ordinal'), True),
    (('source_cut', 'private_head'), {}),
    (('rows', 0, 'position'), True),
    (('rows', 0, 'resource_ref'), None),
    (('rows', 0, 'evidence', 'start_event_id'), _version('invocation')),
    (('rows', 0, 'evidence', 'start_transaction_id'), _id('event')),
    (('rows', 0, 'verification', 'target_record'), 'verified_at_cut'),
    (('rows', 0, 'input_binding_ref'), _version('invocation')),
    (('rows', 1, 'token_ref'), None),
    (('rows', 1, 'resource_ref'), _version('petri_token')),
    (('rows', 1, 'classification'), 'observed_read'),
    (('rows', 1, 'verification', 'material'), 'read'),
    (('coverage', 'total_count'), 2),
    (('coverage', 'relations', 'producer_execution', 'state'), 'partial'),
    (('coverage', 'relations', 'claims', 'state'), 'partial'),
    (('coverage', 'relations', 'start_inputs'), {'state': 'not_in_profile'}),
    (('coverage', 'relations', 'claims'), {'state': 'not_in_profile'}),
    (('coverage', 'relations', 'content_influence', 'witness'), 'verified_at_cut'),
    (('coverage', 'relations', 'observed_reads', 'state'), 'complete'),
    (('continuation',), 'opaque-origin-cursor'),
])
def test_schema_rejects_noncontract_shapes(validator, path, value):
    page = _page()
    destination = page
    for part in path[:-1]:
        destination = destination[part]
    destination[path[-1]] = deepcopy(value)
    assert not validator.is_valid(page), path


def test_schema_rejects_missing_proof_or_empty_partial_page(validator):
    page = _page()
    del page['root_proof']['operation_binding_ref']
    assert not validator.is_valid(page)
    page = _page()
    page['coverage']['state'] = 'partial'
    page['continuation'] = 'opaque-origin-cursor'
    page['rows'] = []
    assert not validator.is_valid(page)


@pytest.mark.parametrize('relative', ['reference/registry-read-sessions', 'guides/independent-registry-reader'])
def test_paired_docs_metadata_and_product_contract(relative):
    english = ROOT / 'docs' / (relative + '.md')
    chinese = ROOT / 'docs' / (relative + '_ZH.md')
    documents = [english.read_text(), chinese.read_text()]
    for text, counterpart in zip(documents, (chinese.name, english.name)):
        assert 'counterpart: ' + counterpart in text
        assert 'revision: "2026-10-09.1"' in text
        assert 'status: implemented' in text
        for term in ('query_product_origin_v1', 'producer_execution', 'start_inputs', 'claims',
                'petri_output', 'workspace_write', 'operation_result/v1',
                'UNSUPPORTED_SETTLEMENT_SHAPE', 'LIMIT_EXCEEDED', 'continuation'):
            assert term in text
        for code in re.findall(r'```python\n(.*?)\n```', text, flags=re.S):
            ast.parse(code)
    if relative.startswith('guides/'):
        code_pairs = [re.findall(r'```python\n(.*?)\n```', text, flags=re.S) for text in documents]
        assert code_pairs[0] == code_pairs[1]
        assert '"include": None' in code_pairs[0][0]
        assert '**request, cursor=cursor' in code_pairs[0][0]
        assert 'include=("producer_execution",)' in code_pairs[0][1]
    else:
        for text in documents:
            assert '../../cpn/schemas/rpnh/product_origin_page.v1.schema.json' in text
            assert PRODUCT_ORIGIN_PAGE_SCHEMA in text
