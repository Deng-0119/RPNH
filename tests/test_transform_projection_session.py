"""Public transform content contract through temporary real read sessions."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid
import pytest
import test_collaboration_plain_merge as support
import test_collaboration_plain_transform as transform_support
from cpn.rpnh.collaboration import SourceQualifiedResourceRef, SourceQualifiedVersionRef
from cpn.rpnh.collaboration import public_projections as public
from cpn.rpnh.collaboration.host_readiness import HostDeclarationSnapshot, diagnose_host_requirements
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.registry_read_contracts import ReadLimits, ReadSessionRequest, ExplicitSources, SourceSelection, RegistryReadSessionError
from cpn.rpnh.collaboration.registry_read_session import RegistryReadHostBinding, ExistingReadAuthorityProvider, open_registry_session, open_readonly_source
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access, revoke_observer_access, observer_access_schema_data
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.schema_catalog import canonical_json

def raw_path(core, ref):
    return core.object_store.path_for_version(ref.ref.resource_version_id)


@contextmanager
def rewrite_fixture(core, reference, raw, *, metadata_changes=None, coherent_descriptor=True):
    """Reversible mutation of this test's own temporary Registry, never user history."""
    path = raw_path(core, reference)
    original = path.read_bytes()
    version = str(reference.ref.resource_version_id)
    with core.event_store.connect() as db:
        row = dict(db.execute('SELECT * FROM objects WHERE version_id=?', (version,)).fetchone())
        old_event = db.execute('SELECT payload_json FROM events WHERE event_id=?',
                               (row['published_event_id'],)).fetchone()[0]
    metadata = json.loads(row['metadata_json'])
    metadata['size'] = len(raw)
    publication = metadata['reference_provenance']['publication']
    publication['size'] = len(raw)
    for key, value in (metadata_changes or {}).items():
        metadata[key] = deepcopy(value)
        if key in publication:
            publication[key] = deepcopy(value)
    if coherent_descriptor and 'content_sha256' in metadata.get('descriptors', {}):
        metadata['descriptors']['content_sha256'] = hashlib.sha256(raw).hexdigest()
        if 'descriptors' in publication:
            publication['descriptors'] = deepcopy(metadata['descriptors'])
    event = json.loads(old_event)
    event['metadata'], event['size'] = metadata, len(raw)
    try:
        path.write_bytes(raw)
        with core.event_store.connect() as db:
            db.execute('UPDATE objects SET metadata_json=?,size=? WHERE version_id=?',
                       (json.dumps(metadata), len(raw), version))
            db.execute('UPDATE events SET payload_json=? WHERE event_id=?',
                       (json.dumps(event), row['published_event_id']))
        yield metadata
    finally:
        path.write_bytes(original)
        with core.event_store.connect() as db:
            db.execute('UPDATE objects SET metadata_json=?,size=? WHERE version_id=?',
                       (row['metadata_json'], row['size'], version))
            db.execute('UPDATE events SET payload_json=? WHERE event_id=?',
                       (old_event, row['published_event_id']))


def fixture_world(tmp_path, monkeypatch):
    original = transform_support.catalog_data
    def observer_catalog():
        schemas, types, paths = original()
        extra, extra_types, extra_paths = observer_access_schema_data()
        for key, value in extra.items():
            assert key not in schemas or canonical_json(schemas[key]) == canonical_json(value)
        return ({**schemas, **extra}, tuple({t.name: t for t in (*types, *extra_types)}.values()),
                {**paths, **extra_paths})
    monkeypatch.setattr(transform_support, 'catalog_data', observer_catalog)
    inputs, author, _ = transform_support.fixture.__wrapped__(tmp_path, monkeypatch)
    core = inputs[0]
    args = transform_support.split_request(inputs[5])
    value = author.publish(**args)
    return core, author, value, inputs


@pytest.fixture
def world(tmp_path, monkeypatch):
    return fixture_world(tmp_path, monkeypatch)


def counts(core):
    with core.event_store.connect() as db:
        return tuple(db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                     for table in ('objects', 'events', 'transactions', 'relations')) + (core.writer_epoch,)


def issue(world, *, denied=()):
    core, author, _, _ = world
    catalog = TypedReaderCatalog()
    fields = {kind: tuple(catalog.fields(kind)) for kind in catalog.entry_types}
    refs = tuple(SourceQualifiedResourceRef(author.binding['source_id'], ResourceVersionRef(
        TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))
        for row in core.event_store.object_rows_by_type('resource_version/v1'))
    material = tuple(ref for ref in refs if canonical_json(ref.to_dict()) not in denied)
    grant = issue_observer_access(author.gateway, principal_ref=author.producer.ref,
        scope=ObserverReadScope(fields, fields, material), purpose='transform-projection-contract',
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        command_id='pp01-grant:' + uuid.uuid4().hex)
    return grant


def session(world, grant, *, limits=ReadLimits(), catalog=None):
    core, author, _, _ = world
    source = author.binding['source_id']
    selected = SourceSelection(SourceQualifiedVersionRef(source, author.gateway._task_ref), 'local')
    def resolver(source_id, access_path):
        assert (source_id, access_path) == (source, 'local')
        result = open_readonly_source(core.run_dir, catalog=core.catalog)
        assert result.core.read_only
        return result
    host = RegistryReadHostBinding('pp01:verified', resolver,
        ExistingReadAuthorityProvider({('pp01:verified', source, 'local'): grant}),
        catalog or TypedReaderCatalog(), limits=limits)
    return open_registry_session(ReadSessionRequest(ExplicitSources((selected,)), 'transform-projection-contract',
                                                   limits=limits), host=host)


def read_outcome(reader, value, cut, monkeypatch):
    before = counts(reader._sources[cut.source_id].resolved.core)
    readonly = reader._sources[cut.source_id].resolved.core
    assert readonly.read_only
    forbidden_calls = []
    def forbidden(*args, **kwargs):
        forbidden_calls.append(True)
        raise AssertionError('session reader attempted compilation, HOST resolution or publication')
    with monkeypatch.context() as patch:
        for path in ('cpn.rpnh.compiler.compile_module', 'cpn.rpnh.collaboration.materials.compile_module',
                     'cpn.rpnh.collaboration.plain_transform.compile_module',
                     'cpn.rpnh.collaboration.plain_transform._validate_transform_at',
                     'cpn.rpnh.registration.Registration.resolve'):
            patch.setattr(path, forbidden)
        patch.setattr(readonly, 'begin', forbidden)
        patch.setattr(readonly, 'publish_bytes', forbidden)
        try:
            envelope = reader.read_public_projection(entry_ref=value.revision.revision_ref, at_cut=cut)
            outcome = {'status': 'ok', 'coverage': envelope['record']['mapping_coverage']}
        except RegistryReadSessionError as exc:
            envelope = None
            outcome = {'status': 'error', 'code': exc.code}
    assert forbidden_calls == []
    return outcome, envelope, before


def record(label, outcome):
    print('PP01_SESSION_CASE=' + json.dumps({'case': label, 'outcome': outcome}, sort_keys=True))


def original_documents(world):
    core, author, value, _ = world
    command = json.loads(raw_path(core, value.command_ref).read_bytes())
    projection_ref = public.projection_resource_ref(core, value.revision.revision_ref, binding=author.binding)
    stored = json.loads(raw_path(core, projection_ref).read_bytes())
    return command, projection_ref, stored


@contextmanager
def history(world, command, projection_ref, stored, *, material=None):
    core, author, value, _ = world
    projection = deepcopy(stored)
    with ExitStack() as stack:
        updates = {}
        if material is not None:
            reference, raw = material
            metadata = stack.enter_context(rewrite_fixture(core, reference, raw))
            key = canonical_json(reference.to_dict())
            updates[key] = raw
            spec = next(spec for spec in command['prepared_materials']
                        if canonical_json(spec['resource_ref']) == key)
            spec['metadata'] = deepcopy(metadata)
            spec['sha256'] = hashlib.sha256(raw).hexdigest()
        command_raw = canonical_json(command)
        stack.enter_context(rewrite_fixture(core, value.command_ref, command_raw))
        updates[canonical_json(value.command_ref.to_dict())] = command_raw
        for dependency in projection['dependencies']:
            raw = updates.get(canonical_json(dependency['ref']))
            if raw is not None:
                dependency.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        stack.enter_context(rewrite_fixture(core, projection_ref, canonical_json(projection)))
        yield


def diagnose(value):
    requirements = value.host_requirements['registrations']
    snapshot = HostDeclarationSnapshot(requirements)
    matched = diagnose_host_requirements(requirements, snapshot).to_dict()
    assert matched['declarations_status'] == 'matched' and matched['execution_ready'] is False
    changed = deepcopy(requirements)
    key = next(iter(changed['executor']))
    changed['executor'][key]['identity']['revision'] += ':changed'
    mismatch = diagnose_host_requirements(changed, snapshot).to_dict()
    assert mismatch['declarations_status'] == 'mismatch' and mismatch['execution_ready'] is False
    missing = diagnose_host_requirements(requirements, HostDeclarationSnapshot({})).to_dict()
    unchecked = diagnose_host_requirements(requirements).to_dict()
    assert missing['declarations_status'] == 'missing' and unchecked['declarations_status'] == 'not_checked'
    assert missing['execution_ready'] is unchecked['execution_ready'] is False
    return matched


def test_canonical_session_history_cut_reopen_and_host_interleave(world, monkeypatch):
    core, author, split, inputs = world
    fused = author.publish(**transform_support.fusion_request(split))
    grant = issue(world)
    before = counts(core)
    original = {}
    with session(world, grant) as reader:
        cut = reader.capture_cut(author.binding['source_id'])
        for value in (split, fused):
            outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
            assert outcome == {'status': 'ok', 'coverage': 'verified'}
            original[value.revision.command_id] = envelope['record']
            report = diagnose(value)
            again, repeated, _ = read_outcome(reader, value, cut, monkeypatch)
            assert again == outcome and canonical_json(repeated) == canonical_json(envelope)
            assert diagnose(value) == report
        assert counts(core) == before
        later = author.publish(**transform_support.split_request(inputs[5], command_id='later-split'))
        after_append = counts(core)
        outcome, old, _ = read_outcome(reader, split, cut, monkeypatch)
        assert outcome == {'status': 'ok', 'coverage': 'verified'}
        assert old['source_cut'] == cut.to_dict() and old['record'] == original['split']
        outcome, missing, _ = read_outcome(reader, later, cut, monkeypatch)
        assert outcome == {'status': 'error', 'code': 'NOT_PRESENT_AT_CUT'} and missing is None
        assert counts(core) == after_append
    with session(world, grant) as reopened:
        cut = reopened.capture_cut(author.binding['source_id'])
        for value in (split, fused):
            outcome, envelope, _ = read_outcome(reopened, value, cut, monkeypatch)
            assert outcome == {'status': 'ok', 'coverage': 'verified'}
            assert envelope['record'] == original[value.revision.command_id]
        assert counts(core) == after_append
    record('normal-session-cut-reopen-host', {'status': 'ok', 'histories': 2,
           'same_cut_repeat': True, 'future_record_rejected': True, 'host_execution_ready': False})


def test_session_noncanonical_history_representations(world, monkeypatch):
    core, author, value, _ = world
    original, projection_ref, stored = original_documents(world)
    grant = issue(world)
    ref = value.revision.definition_ref
    raw = raw_path(core, ref).read_bytes()
    assert b'"name":"Plain"' in raw
    variants = {
        'reorder': json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True,
                              separators=(',', ':')).encode(),
        'whitespace': b' ' + raw,
        'unicode_escape': raw.replace(b'"name":"Plain"', b'"name":"\\u0050lain"', 1),
        'duplicate_same': raw[:-1] + b',"name":"Plain"}',
        'duplicate_shadowed': raw.replace(b'"name":"Plain"', b'"name":"Other"', 1)[:-1] + b',"name":"Plain"}',
    }
    for label, changed in variants.items():
        assert changed != raw and canonical_json(json.loads(changed)) == canonical_json(json.loads(raw))
        with history(world, deepcopy(original), projection_ref, stored, material=(ref, changed)):
            before = counts(core)
            with session(world, grant) as reader:
                cut = reader.capture_cut(author.binding['source_id'])
                outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
                expected = {'status': 'ok', 'coverage': 'verified'}
                assert outcome == expected
                assert (envelope is None) == (expected['status'] == 'error')
                record('history-' + label, outcome)
            assert counts(core) == before
    assert raw_path(core, ref).read_bytes() == raw


@pytest.mark.parametrize('damage', ('wrong', 'missing', 'integer'))
def test_session_ignores_retired_prepared_material_sha(world, monkeypatch, damage):
    core, author, value, _ = world
    command, projection_ref, stored = original_documents(world)
    grant = issue(world)
    spec = command['prepared_materials'][0]
    if damage == 'wrong': spec['sha256'] = ('0' if spec['sha256'][0] != '0' else '1') + spec['sha256'][1:]
    elif damage == 'missing': del spec['sha256']
    else: spec['sha256'] = 7
    with history(world, command, projection_ref, stored):
        before = counts(core)
        with session(world, grant) as reader:
            cut = reader.capture_cut(author.binding['source_id'])
            outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
            expected = {'status': 'ok', 'coverage': 'verified'}
            assert outcome == expected
            assert (envelope is None) == (expected['status'] == 'error')
            record('sha-' + damage, outcome)
        assert counts(core) == before


def test_session_denials_and_limits(world, monkeypatch):
    core, author, value, _ = world
    cases = [
        ('denied_command', (value.command_ref,), ReadLimits(), {'status': 'error', 'code': 'INTEGRITY_FAILED'}),
        ('denied_definition', (value.revision.definition_ref,), ReadLimits(), {'status': 'error', 'code': 'INTEGRITY_FAILED'}),
        ('material_limit', (), ReadLimits(max_material_bytes=1), {'status': 'error', 'code': 'MATERIAL_TOO_LARGE'}),
        ('closure_limit', (), ReadLimits(max_material_closure_bytes=1), {'status': 'error', 'code': 'LIMIT_EXCEEDED'}),
        ('response_limit', (), ReadLimits(max_response_bytes=1), {'status': 'error', 'code': 'LIMIT_EXCEEDED'}),
    ]
    for label, denied, limits, expected in cases:
        grant = issue(world, denied={canonical_json(ref.to_dict()) for ref in denied})
        before = counts(core)
        with session(world, grant, limits=limits) as reader:
            cut = reader.capture_cut(author.binding['source_id'])
            outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
            assert outcome == expected
            assert envelope is None
            record(label, outcome)
        assert counts(core) == before


@pytest.mark.parametrize('damage', ('metadata', 'reference'))
def test_session_preserves_prepared_metadata_and_reference_binding(world, monkeypatch, damage):
    core, author, value, _ = world
    command, projection_ref, stored = original_documents(world)
    grant = issue(world)
    first, other = command['prepared_materials'][:2]
    assert first['resource_ref'] != other['resource_ref']
    if damage == 'metadata':
        first['metadata']['summary'] = 'different from the registered material metadata'
    else:
        # Both materials are authorized. Changing the command's exact reference
        # must not substitute another document for this prepared material.
        first['resource_ref'] = deepcopy(other['resource_ref'])
    with history(world, command, projection_ref, stored):
        before = counts(core)
        with session(world, grant) as reader:
            cut = reader.capture_cut(author.binding['source_id'])
            outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
            assert outcome == {'status': 'error', 'code': 'INTEGRITY_FAILED'}
            assert envelope is None
            record('prepared-' + damage + '-rejected', outcome)
        assert counts(core) == before


@pytest.mark.parametrize('change', ('revoke', 'expiry'))
def test_session_final_delivery_recheck(world, monkeypatch, change):
    core, author, value, _ = world
    grant = issue(world)
    cat = TypedReaderCatalog()
    with session(world, grant, catalog=cat) as reader:
        cut = reader.capture_cut(author.binding['source_id'])
        original = cat.read_public_projection
        observed = []
        def after_valid_projection(*args, **kwargs):
            result = original(*args, **kwargs)
            assert result['mapping_coverage'] == 'verified'
            observed.append('projection-completed')
            if change == 'revoke':
                revoke_observer_access(author.gateway, grant_ref=grant.grant_ref, command_id='revoke-at-delivery')
            else:
                reader.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            return result
        monkeypatch.setattr(cat, 'read_public_projection', after_valid_projection)
        outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
        assert observed == ['projection-completed'] and envelope is None
        assert outcome == {'status': 'error', 'code': 'ACCESS_CHANGED' if change == 'revoke' else 'SESSION_EXPIRED'}
        assert not reader._cuts and not reader._cursors
        record('final-' + change, outcome)


@pytest.mark.parametrize('mutation', ('representation', 'content'))
def test_session_between_dependency_and_material_reads(world, monkeypatch, mutation):
    core, author, value, _ = world
    grant = issue(world)
    path = raw_path(core, value.transform_map_ref)
    raw = path.read_bytes()
    if mutation == 'representation':
        changed = json.dumps(dict(reversed(list(json.loads(raw).items()))), ensure_ascii=True,
                             separators=(',', ':')).encode()
        assert canonical_json(json.loads(changed)) == canonical_json(json.loads(raw))
    else:
        changed = raw.replace(b'"kind":"split"', b'"kind":"spLit"', 1)
        assert canonical_json(json.loads(changed)) != canonical_json(json.loads(raw))
    assert len(raw) == len(changed) and raw != changed
    target = canonical_json(value.transform_map_ref.to_dict())
    real_payload = public.payload_at
    calls = []
    def mutate_after_real_dependency_read(*args, **kwargs):
        result = real_payload(*args, **kwargs)
        ref = args[1].to_dict() if hasattr(args[1], 'to_dict') else args[1]
        if canonical_json(ref) == target:
            calls.append(result[0])
            if len(calls) == 1:
                assert result[0] == raw
                path.write_bytes(changed)
        return result
    before = counts(core)
    try:
        with session(world, grant) as reader:
            cut = reader.capture_cut(author.binding['source_id'])
            with monkeypatch.context() as patch:
                patch.setattr(public, 'payload_at', mutate_after_real_dependency_read)
                outcome, envelope, _ = read_outcome(reader, value, cut, monkeypatch)
            assert len(calls) >= 3 and calls[0] == raw and all(x == changed for x in calls[1:])
            accepted = mutation == 'representation'
            expected = {'status': 'ok', 'coverage': 'verified'} if accepted else {'status': 'error', 'code': 'INTEGRITY_FAILED'}
            assert outcome == expected and (envelope is not None) == accepted
            assert path.read_bytes() == changed
            record('between-read-' + mutation, outcome)
    finally:
        path.write_bytes(raw)
    assert counts(core) == before and path.read_bytes() == raw
