"""HOST grouping is configuration only; original Registry authority remains."""
import json
from types import SimpleNamespace

import pytest

from cpn.rpnh.collaboration.read_host_hierarchy import expand_host_hierarchy
from cpn.rpnh.collaboration.read_host_config import load_read_host_config
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError, IndexQuery, TypedIndexClause
from test_reader_preflight import configured
from test_registry_read_session import counts


def selections(*sources):
    return tuple(SimpleNamespace(source_ref=SimpleNamespace(source_id=name)) for name in sources)


def hierarchy():
    return {'roots': ['root'], 'nodes': {
        'root': {'sources': ['b'], 'children': ['left', 'right']},
        'left': {'sources': ['a'], 'children': []},
        'right': {'sources': ['a', 'c'], 'children': ['left']}}}


def test_stable_sources_first_diamond_unique_exact_leaves():
    original = selections('a', 'b', 'c')
    assert expand_host_hierarchy(hierarchy(), original) == (original[1], original[0], original[2])


@pytest.mark.parametrize('mutation', (
    lambda h: h['roots'].append('root'),
    lambda h: h['roots'].append('unknown'),
    lambda h: h['nodes']['left']['children'].append('root'),
    lambda h: h['nodes']['left']['children'].append('unknown'),
    lambda h: h['nodes']['left']['sources'].append('unknown'),
    lambda h: h['nodes']['left']['sources'].append('a'),
    lambda h: h['nodes']['root']['children'].append('left'),
    lambda h: h['nodes'].update(unreachable={'sources': [], 'children': []}),
    lambda h: h['nodes']['left'].update(extra=True),
    lambda h: h.update(extra=True),
    lambda h: h.update(roots=[]),
    lambda h: h.update(roots=['']),
    lambda h: h.update(roots=('root',)),
    lambda h: h['nodes']['left'].update(sources=[1]),
    lambda h: h['nodes']['left'].update(sources='a'),
    lambda h: h['nodes']['left'].update(children=[{}]),
))
def test_invalid_hierarchy_rejected(mutation):
    value = hierarchy()
    mutation(value)
    with pytest.raises(RegistryReadSessionError) as error:
        expand_host_hierarchy(value, selections('a', 'b', 'c'))
    assert error.value.code == 'INVALID_HOST_CONFIGURATION'


def test_ambiguous_or_unreachable_concrete_source_rejected():
    for value in (selections('a','b','c','a'), selections('a','b','c','unused')):
        with pytest.raises(RegistryReadSessionError):
            expand_host_hierarchy(hierarchy(), value)


def test_depth_checks_long_path_even_when_dag_suffix_visited_first():
    nodes = {'tail': {'sources': ['a'], 'children': []}}
    for i in range(32):
        nodes[str(i)] = {'sources': [], 'children': [str(i+1) if i < 31 else 'tail']}
    value = {'roots': ['tail', '0'], 'nodes': nodes}
    with pytest.raises(RegistryReadSessionError):
        expand_host_hierarchy(value, selections('a'))
    del nodes['0']
    value['roots'] = ['tail', '1']
    assert expand_host_hierarchy(value, selections('a'))[0].source_ref.source_id == 'a'


def test_node_and_fanout_limits():
    for value in (
        {'roots': ['root'], 'nodes': {'root': {'sources': [], 'children': ['x'] * 1025}}},
        {'roots': ['0'], 'nodes': {str(i): {'sources': [], 'children': []} for i in range(1025)}},
    ):
        with pytest.raises(RegistryReadSessionError):
            expand_host_hierarchy(value, selections('a'))


def test_actual_v2_flat_equivalence_and_hierarchy_rebind_invalidates(tmp_path):
    world, _, path = configured(tmp_path)
    before = counts(world)
    flat = load_read_host_config(path).open_session()
    spec = IndexQuery((world[-1],), (TypedIndexClause('resource_version/v1'),), 100)
    baseline = flat.query_index(spec)
    flat.close()
    doc = json.loads(path.read_text())
    doc.update(schema_version='rpnh/registry_read_host_config/v2', hierarchy={
        'roots': ['root'], 'nodes': {'root': {'sources': [], 'children': ['leaf']},
        'leaf': {'sources': [world[-1]], 'children': []}}})
    path.write_text(json.dumps(doc))
    reader = load_read_host_config(path).open_session()
    result = reader.query_index(spec)
    # Opaque session/cut identifiers differ; actual typed payload and exact refs agree.
    assert [{k: v for k, v in x.items() if k != 'source_cut'} for x in result['entries']] == [{k: v for k, v in x.items() if k != 'source_cut'} for x in baseline['entries']]
    assert result['coverage'] == baseline['coverage']
    assert result['global_atomic_snapshot'] is False
    doc['hierarchy']['roots'] = ['leaf', 'root']
    path.write_text(json.dumps(doc))
    with pytest.raises(RegistryReadSessionError) as error:
        reader.final_recheck()
    assert error.value.code == 'ACCESS_CHANGED'
    assert not reader.query_index(spec)['entries']
    reader.close()
    assert counts(world) == before


def test_v1_rejects_hierarchy_and_v2_requires_it(tmp_path):
    _, _, path = configured(tmp_path)
    original = json.loads(path.read_text())
    for value in ({**original, 'hierarchy': hierarchy()},
                  {**original, 'schema_version': 'rpnh/registry_read_host_config/v2'}):
        path.write_text(json.dumps(value))
        with pytest.raises(RegistryReadSessionError):
            load_read_host_config(path)


def test_two_source_hierarchy_keeps_existing_independent_authority(tmp_path):
    from test_registry_read_session import owner, publish, issue
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    world, _, path = configured(tmp_path)
    other = owner(tmp_path / 'second', 'source-b')
    publish(other, 'second source', 'second')
    context = issue(other)
    doc = json.loads(path.read_text())
    doc['sources'].append({
        'source_ref': SourceQualifiedVersionRef(other[-1], other[2].task_ref).to_dict(),
        'access_path': 'local', 'registry_root': str(other[0].run_dir),
        'binding_generation': '1', 'observer_context': context.to_dict()})
    doc.update(schema_version='rpnh/registry_read_host_config/v2', hierarchy={
        'roots': ['root'], 'nodes': {
            'root': {'sources': ['source-b'], 'children': ['leaf']},
            'leaf': {'sources': [world[-1]], 'children': []}}})
    path.write_text(json.dumps(doc))
    loaded = load_read_host_config(path)
    assert tuple(s.source_ref.source_id for s in loaded.request.selection.sources) == ('source-b', world[-1])
    before = counts(world), counts(other)
    with loaded.open_session() as reader:
        result = reader.query_index(IndexQuery(('source-b', world[-1]), (TypedIndexClause('resource_version/v1'),), 100))
        assert {x['entry_ref']['source_id'] for x in result['entries']} == {'source-b', world[-1]}
        assert result['global_atomic_snapshot'] is False
    assert (counts(world), counts(other)) == before
