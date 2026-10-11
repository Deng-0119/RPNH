"""Bounded read policy and operation-local accounting, without real workloads."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from cpn.rpnh.collaboration.registry_read_contracts import (
    ReadLimits, ReadLimitOverrides, ReadSessionRequest, ExplicitSources,
    SourceSelection, _effective_read_limits)
from cpn.rpnh.collaboration.registry_typed_readers import TypedReadError, payload_at
from cpn.rpnh.collaboration._projection_read_budget import (
    _ProjectionReadBudget, projection_read_scope)
from cpn.rpnh.collaboration import public_projections as public
from cpn.rpnh.collaboration import read_host_config
from test_registry_read_session import world, publish
from test_registry_typed_exchange import snapshot
from test_shared_public_entrypoints import document, write

MIB = 1024 * 1024


def test_material_policy_sparse_omission_explicit_defaults_and_legacy_replace():
    defaults = ReadLimits()
    host = ReadLimits(max_material_bytes=5 * MIB, max_material_closure_bytes=11 * MIB,
                      max_response_bytes=12 * MIB, max_scan_bytes=128 * MIB,
                      max_sources=200, session_seconds=1200)
    assert _effective_read_limits(defaults, ReadLimitOverrides()).to_dict() == defaults.to_dict()
    inherited = _effective_read_limits(host, ReadLimitOverrides())
    assert (inherited.max_material_bytes, inherited.max_material_closure_bytes) == (5 * MIB, 11 * MIB)
    assert inherited.to_dict() == host.to_dict()
    explicit = _effective_read_limits(host, ReadLimitOverrides(max_material_bytes=4 * MIB))
    assert (explicit.max_material_bytes, explicit.max_material_closure_bytes) == (4 * MIB, 11 * MIB)
    closure = _effective_read_limits(host, ReadLimitOverrides(max_material_closure_bytes=3))
    assert (closure.max_material_bytes, closure.max_material_closure_bytes) == (5 * MIB, 3)
    assert _effective_read_limits(host, ReadLimitOverrides(max_material_bytes=999 * MIB,
        max_material_closure_bytes=999 * MIB)).to_dict() == inherited.to_dict()
    assert _effective_read_limits(host, ReadLimits()).to_dict() == defaults.to_dict()
    assert _effective_read_limits(host, ReadLimitOverrides(max_page_size=2)).max_material_bytes == 5 * MIB
    assert replace(ReadLimits(), max_material_bytes=8).to_dict()['max_material_closure_bytes'] == 8
    assert replace(ReadLimits(max_material_closure_bytes=9), max_material_bytes=8).to_dict()['max_material_closure_bytes'] == 9
    assert ReadLimits(max_material_bytes=7).tightened(ReadLimits(max_material_bytes=6)).to_dict()['max_material_closure_bytes'] == 6


@pytest.mark.parametrize('value', [True, False, 0, -1, 2.5, '8'])
def test_invalid_policy_numbers(value):
    for cls in (ReadLimits, ReadLimitOverrides):
        for key in ('max_material_bytes', 'max_material_closure_bytes'):
            with pytest.raises(ValueError):
                cls(**{key: value})
    with pytest.raises(TypeError):
        ReadLimitOverrides(unlimited=True)


def test_request_wire_omission_and_json_null_are_distinct(tmp_path):
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.identities import TypedId
    ref = SourceQualifiedVersionRef('source', VersionRef('task/v1', TypedId.parse('task:' + 'a' * 32),
                                                        TypedId.parse('task_version:' + 'b' * 32)))
    selection = ExplicitSources((SourceSelection(ref, 'local'),))
    request = ReadSessionRequest(selection, 'inspect')
    assert request.to_dict()['limits'] == {}
    explicit = replace(request, limits=ReadLimitOverrides(max_material_bytes=4 * MIB))
    assert explicit.to_dict()['limits'] == {'max_material_bytes': 4 * MIB}
    schema_path = Path(__file__).parents[1] / 'cpn/schemas/rpnh/registry_read_session_request.v1.schema.json'
    schema = json.loads(schema_path.read_text())
    jsonschema.validate(request.to_dict(), schema)
    jsonschema.validate(explicit.to_dict(), schema)
    jsonschema.validate(replace(request, limits=ReadLimits()).to_dict(), schema)
    for bad in ({'max_material_bytes': None}, {'max_material_closure_bytes': None}, {'unlimited': True}):
        wire = request.to_dict(); wire['limits'] = bad
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(wire, schema)
        value = document(tmp_path); value['limits'] = bad
        with pytest.raises(Exception) as error:
            read_host_config.load_read_host_config(write(tmp_path, value))
        assert error.value.code == 'INVALID_HOST_CONFIGURATION'


def test_owned_config_is_host_policy_and_change_invalidates(tmp_path, monkeypatch):
    value = document(tmp_path)
    value['limits'] = {'max_material_bytes': 5 * MIB, 'max_material_closure_bytes': 11 * MIB}
    path = write(tmp_path, value)
    loaded = read_host_config.load_read_host_config(path)
    assert loaded.request.limits.to_dict() == {}
    assert _effective_read_limits(loaded.binding.limits, loaded.request.limits).max_material_bytes == 5 * MIB
    monkeypatch.setattr(read_host_config, 'open_readonly_source', lambda *a, **k: pytest.fail('opened changed config'))
    value['limits']['max_material_bytes'] += 1; path.write_text(json.dumps(value))
    with pytest.raises(Exception) as error:
        loaded.binding.source_resolver('isolated-source', 'local-observer')
    assert error.value.code == 'ACCESS_CHANGED'


def reference(source='source', version='b'):
    return {'schema_version': 'rpnh/collaboration/source_resource_ref/v1', 'source_id': source,
            'ref': {'resource_id': 'resource:' + 'a' * 32, 'resource_version_id': 'resource_version:' + version * 32}}


def test_unique_budget_exact_boundary_identity_and_cleanup():
    raw = SimpleNamespace(max_material_bytes=3, max_material_closure_bytes=6)
    with projection_read_scope(raw) as scoped:
        ledger = scoped._projection_material_budget
        ledger.admit(reference(), SimpleNamespace(size=3))
        ledger.admit(reference(), SimpleNamespace(size=3))
        assert ledger.total_bytes == 3
        with projection_read_scope(scoped) as nested:
            assert nested is scoped
            ledger.admit(reference(version='c'), SimpleNamespace(size=3))
        assert ledger.total_bytes == 6
        with pytest.raises(TypedReadError, match='LIMIT_EXCEEDED'):
            ledger.admit(reference(source='other'), SimpleNamespace(size=1))
        with pytest.raises(TypedReadError, match='MATERIAL_TOO_LARGE'):
            ledger.admit(reference(), SimpleNamespace(size=4))
        with pytest.raises(TypedReadError, match='INTEGRITY_FAILED'):
            ledger.admit(reference(), SimpleNamespace(size=2))
    assert ledger.closed and not ledger._sizes
    assert not hasattr(raw, '_projection_material_budget')
    with projection_read_scope(raw) as fresh:
        assert fresh._projection_material_budget.total_bytes == 0
    assert _ProjectionReadBudget(SimpleNamespace(max_material_bytes=9)).max_closure_bytes == 9


def test_actual_large_nested_body_uses_host_limit_only_in_public_scope(world, monkeypatch):
    # One >4 MiB body proves the old hidden default is gone within the scope.
    # This is body-reader wiring, not a representative PN capacity benchmark.
    body = b'x' * (4 * MIB + 1)
    ref = publish(world, body, 'larger-than-default')
    snap = snapshot(world[0], world[-1]); snap.max_material_bytes = len(body)
    snap.max_material_closure_bytes = len(body)
    with pytest.raises(TypedReadError, match='MATERIAL_TOO_LARGE'):
        payload_at(world[0], ref, snap, material=True)
    def nested(core, target, *, snapshot):
        return payload_at(core, target, snapshot, material=True)[0]
    monkeypatch.setattr(public, '_read_public_projection_complete', nested)
    assert public.read_public_projection(world[0], ref, snapshot=snap) == body
    from cpn.rpnh.collaboration import registry_typed_readers as readers
    monkeypatch.setattr(readers, '_payload_at', lambda *a, **k: pytest.fail('over-budget body loader ran'))
    snap.max_material_bytes -= 1
    with pytest.raises(TypedReadError, match='MATERIAL_TOO_LARGE'):
        public.read_public_projection(world[0], ref, snapshot=snap)
    snap.max_material_bytes += 1; snap.max_material_closure_bytes -= 1
    with pytest.raises(TypedReadError, match='LIMIT_EXCEEDED'):
        public.read_public_projection(world[0], ref, snapshot=snap)
    assert not hasattr(snap, '_projection_material_budget')


def test_mapping_sides_share_unique_budget_and_failed_call_does_not_leak(world, monkeypatch):
    refs = [publish(world, b'abc', 'mapped-' + str(i)) for i in range(2)]
    snap = snapshot(world[0], world[-1]); snap.max_material_bytes = 3
    snap.max_material_closure_bytes = 5
    def nested(core, target, *, snapshot):
        payload_at(core, target, snapshot, material=True)
        payload_at(core, target, snapshot, material=True)
        return {'target_ref': target.to_dict(), 'projection_ref': target.to_dict(), 'mapping_groups': []}
    monkeypatch.setattr(public, '_read_public_projection_complete', nested)
    with pytest.raises(TypedReadError, match='LIMIT_EXCEEDED'):
        public.read_public_mapping(world[0], *refs, snapshot=snap)
    snap.max_material_closure_bytes = 6
    assert public.read_public_mapping(world[0], *refs, snapshot=snap)['groups'] == []
    snap.max_material_closure_bytes = 3
    assert public.read_public_mapping(world[0], refs[0], refs[0], snapshot=snap)['groups'] == []


def test_cached_payload_still_checks_permission_and_new_budget_before_hit(world, monkeypatch):
    from cpn.rpnh.collaboration import registry_typed_readers as readers
    ref = publish(world, b'abcd', 'cached')
    snap = snapshot(world[0], world[-1]); snap.max_material_bytes = 4
    snap.max_material_closure_bytes = 4
    # Prime a successful payload through the original exact reader, then emulate
    # an existing context cache. This test does not introduce a production cache.
    cached = payload_at(world[0], ref, snap, material=True)
    class Cache:
        hits = 0
        def cached(self, namespace, key, contract, loader):
            if namespace == 'payload':
                self.hits += 1
                return cached
            return loader()
        def reserve(self, **kwargs): pass
        def bytes_bound(self, value): return 0
    cache = Cache(); snap._origin_context = cache
    monkeypatch.setattr(readers, '_payload_at', lambda *a, **k: pytest.fail('cache missed'))
    accesses = []
    snap.check_authorized = lambda *args: accesses.append(args)
    with projection_read_scope(snap) as scoped:
        assert payload_at(world[0], ref, scoped, material=True)[0] == b'abcd'
        assert payload_at(world[0], ref, scoped, material=True)[0] == b'abcd'
        assert scoped._projection_material_budget.total_bytes == 4 and len(accesses) == 2
    assert cache.hits == 2
    with projection_read_scope(snap) as scoped:
        with pytest.raises(TypedReadError, match='MATERIAL_TOO_LARGE'):
            payload_at(world[0], ref, scoped, material=True, max_bytes=3)
    assert cache.hits == 2
    snap.max_material_closure_bytes = 3
    with projection_read_scope(snap) as scoped:
        with pytest.raises(TypedReadError, match='LIMIT_EXCEEDED'):
            payload_at(world[0], ref, scoped, material=True)
    assert cache.hits == 2
    snap.max_material_closure_bytes = 4
    snap.check_authorized = lambda *a: (_ for _ in ()).throw(TypedReadError('ACCESS_CHANGED'))
    with projection_read_scope(snap) as scoped:
        with pytest.raises(TypedReadError, match='ACCESS_CHANGED'):
            payload_at(world[0], ref, scoped, material=True)
    assert cache.hits == 2


from test_collaboration_plain_merge_result import fixture


def test_actual_merge_whole_operation_bytes_exact_boundaries_and_zero_writes(fixture, monkeypatch, record_property):
    from test_collaboration_plain_merge_projection import merged, stored, snapshot as merge_snapshot
    import test_collaboration_plain_merge as support
    inputs, author, value, _, _, _ = merged(fixture)
    core = inputs[0]
    _, raw, document = stored(core, value, author.binding)
    observed = []
    close = _ProjectionReadBudget.close
    def capture(ledger):
        observed.append((ledger.total_bytes, max(ledger._sizes.values(), default=0)))
        close(ledger)
    monkeypatch.setattr(_ProjectionReadBudget, 'close', capture)
    before = support.counts(core)
    snap = merge_snapshot(core, 'plain-source')
    graph = public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    total, body = observed[-1]
    record_property('small_synthetic_M_unique_body_bytes', total)
    record_property('small_synthetic_M_largest_body_bytes', body)
    assert total > len(raw) + sum(pin['bytes'] for pin in document['dependencies'])
    snap.max_material_bytes = body; snap.max_material_closure_bytes = total
    assert public.read_public_projection(core, value.revision.revision_ref, snapshot=snap) == graph
    snap.max_material_closure_bytes -= 1
    with pytest.raises(TypedReadError, match='LIMIT_EXCEEDED'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    snap.max_material_closure_bytes += 1; snap.max_material_bytes -= 1
    with pytest.raises(TypedReadError, match='MATERIAL_TOO_LARGE'):
        public.read_public_projection(core, value.revision.revision_ref, snapshot=snap)
    assert support.counts(core) == before



def test_real_session_host_raise_omitted_request_and_explicit_default(world):
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.collaboration.registry_read_session import (
        RegistryReadHostBinding, ExistingReadAuthorityProvider, open_readonly_source, open_registry_session)
    from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
    from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
    from test_registry_read_session import issue, counts
    body = b'z' * (4 * MIB + 1)
    ref = publish(world, body, 'host-raised-material')
    context = issue(world, material=(ref,))
    source = world[-1]
    selection = ExplicitSources((SourceSelection(SourceQualifiedVersionRef(source, world[2].task_ref), 'local'),))
    host = RegistryReadHostBinding('verified:test',
        lambda source_id, access_path: open_readonly_source(world[0].run_dir, catalog=world[0].catalog),
        ExistingReadAuthorityProvider({('verified:test', source, 'local'): context}),
        TypedReaderCatalog(), ReadLimits(max_material_bytes=len(body), max_material_closure_bytes=2 * len(body)))
    before = counts(world)
    with open_registry_session(ReadSessionRequest(selection, 'inspect'), host=host) as reader:
        assert reader.limits.max_material_bytes == len(body)
        assert reader.limits.max_material_closure_bytes == 2 * len(body)
        cut = reader.capture_cut(source)
        assert reader.read_material(resource_ref=ref, at_cut=cut)['bytes'] == body
    with open_registry_session(ReadSessionRequest(selection, 'inspect',
            ReadLimitOverrides(max_material_bytes=4 * MIB)), host=host) as reader:
        assert reader.limits.max_material_closure_bytes == 2 * len(body)
        cut = reader.capture_cut(source)
        with pytest.raises(RegistryReadSessionError) as error:
            reader.read_material(resource_ref=ref, at_cut=cut)
        assert error.value.code == 'MATERIAL_TOO_LARGE'
    assert counts(world) == before


@pytest.mark.parametrize('host_bytes,request_bytes,expected', [
    (4*MIB,None,4*MIB),(8*MIB,None,8*MIB),(8*MIB,4*MIB,4*MIB),
    (4*MIB,16*MIB,4*MIB),(8*MIB,16*MIB,8*MIB),
])
def test_host_response_policy_inherits_or_tightens(host_bytes,request_bytes,expected):
    request=ReadLimitOverrides() if request_bytes is None else ReadLimitOverrides(max_response_bytes=request_bytes)
    actual=_effective_read_limits(ReadLimits(max_response_bytes=host_bytes),request)
    assert actual.max_response_bytes==expected


@pytest.mark.parametrize('maximum,accepted', [(64,False),(65,True)])
def test_owned_host_config_source_count_follows_policy(tmp_path,maximum,accepted):
    from copy import deepcopy
    from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
    value=document(tmp_path)
    template=value['sources'][0]
    value['sources']=[]
    for i in range(65):
        row=deepcopy(template)
        row['source_ref']['source_id']='configured-source-'+str(i)
        value['sources'].append(row)
    value['limits']={'max_sources':maximum}
    path=write(tmp_path,value)
    if accepted:
        loaded=read_host_config.load_read_host_config(path)
        assert loaded.binding.limits.max_sources==65
        assert len(loaded.request.selection.sources)==65
    else:
        with pytest.raises(RegistryReadSessionError,match='invalid host configuration'):
            read_host_config.load_read_host_config(path)
