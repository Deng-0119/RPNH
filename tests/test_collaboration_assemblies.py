"""Offline stable-member Assembly publication, actual lowering and recovery."""

from copy import deepcopy
from dataclasses import replace
import json
import socket
import subprocess
import uuid

import pytest

from cpn.rpnh.collaboration import (
    AssemblyAuthor, AssemblyMember, AssemblyConnection, AssemblyCompletion,
    SourceQualifiedVersionRef, assembly_schema_data, read_assembly_revision, validate_assembly_revision,
)
from cpn.rpnh.collaboration.assemblies import ASSEMBLY_TYPE, ASSEMBLY_SCHEMA, PLAN_SCHEMA, LOWERING_SCHEMA, COMPILED_SCHEMA
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.collaboration._assembly_lowering import prefix, resolve_pointer
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from test_native_net_operations import _registration, _simple_module, ALT_TEXT, TEXT


A, B, C = ("member:" + value * 32 for value in "abc")


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = assembly_schema_data()
    core = _RegistryCore(tmp_path / "assembly", create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("assembly-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-a", command_id="source:bind")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id), "display_name": "Assembly owner"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
        idempotency_key="fixture:principal")
    registration = _registration()
    author = AssemblyAuthor(gateway, registration, SourceQualifiedVersionRef("source-a", principal))
    return core, gateway, author, registration


def _member(author, module=None, *, command="member:first", parent=None, ids=None):
    module = module or _simple_module()
    ids = ids or {key: "element:" + uuid.uuid4().hex for key in _elements(module)}
    result = author.author.publish(module=module, element_ids=ids, command_id=command, parent_ref=parent)
    return result, ids


def _request(member, ids, **changes):
    return {"name": "Pair", "members": (AssemblyMember(A, "Same", member.revision.revision_ref),
            AssemblyMember(B, "Same", member.revision.revision_ref)),
        "connections": (), "completion": AssemblyCompletion(B, ids["/terminal"]),
        "budget_policy": "shared_exact", "deployment_intent": "same_run_candidate", "command_id": "assembly:first", **changes}


def _counts(core):
    return (len(core.event_store.object_rows_by_type(ASSEMBLY_TYPE)),
        len(core.event_store.object_rows_by_type("collaboration_branch/v1")),
        len(core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1"))))


def _serial(ids):
    return (AssemblyConnection(A, ids["/exit/result"], B, ids["/entry/request"]),)


def test_two_same_definition_and_label_members_have_independent_stable_identity(fixture, monkeypatch):
    core, _, author, registration = fixture
    def forbidden(*args, **kwargs):
        raise AssertionError("offline Assembly must not execute sockets or processes")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    member, ids = _member(author)
    result = author.publish(**_request(member, ids))
    assert result.generated.module.links == ()
    assert len(result.generated.module.entry) == len(result.generated.module.exit) == 2
    assert set(result.compiled.fragments) == {f"{prefix(A)}_step", f"{prefix(B)}_step"}
    origins = result.lowering_map["origins"]
    assert len(origins) == 2 * len(ids)
    assert len({(item["member_id"], item["element_id"]) for item in origins}) == len(origins)
    terminals = [item for item in origins if item["kind"] == "terminal"]
    assert {item["member_id"]: item["disposition"] for item in terminals} == {A: "not_root_completion", B: "root_completion"}
    assert result.compiled.symbolic.terminal.source.component == f"{prefix(B)}_step"
    assert read_assembly_revision(core, result.revision.revision_ref) == result.revision
    assert validate_assembly_revision(core, result.revision.revision_ref, registration).lowering_map == result.lowering_map
    assert _counts(core) == (1, 0, 0)
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()


def test_label_only_rename_and_input_order_keep_generated_topology_compile_and_map(fixture):
    _, _, author, _ = fixture
    member, ids = _member(author)
    first = author.publish(**_request(member, ids))
    renamed = (AssemblyMember(B, "Second label", member.revision.revision_ref), AssemblyMember(A, "First label", member.revision.revision_ref))
    second = author.publish(**_request(member, ids, members=renamed, command_id="assembly:rename", parent_ref=first.revision.revision_ref))
    assert canonical_json(first.generated.module.to_dict()) == canonical_json(second.generated.module.to_dict())
    assert canonical_json(first.compiled.to_dict()) == canonical_json(second.compiled.to_dict())
    assert canonical_json(first.lowering_map) == canonical_json(second.lowering_map)
    assert second.revision.revision_ref.ref.entity_id == first.revision.revision_ref.ref.entity_id
    assert first.revision.plan_ref != second.revision.plan_ref


def test_only_explicit_connection_fuses_ports_and_maps_consumed_boundaries(fixture):
    _, _, author, _ = fixture
    member, ids = _member(author)
    value = author.publish(**_request(member, ids, connections=_serial(ids)))
    assert len(value.generated.module.links) == 1
    aliases = value.compiled.place_aliases
    source = value.compiled.symbolic.port_places[f"{prefix(A)}_step.result"]
    target = value.compiled.symbolic.port_places[f"{prefix(B)}_step.request"]
    assert source == target
    assert len(aliases) > len(set(aliases.values()))
    consumed = [item for item in value.lowering_map["origins"] if item["disposition"] == "consumed"]
    assert {(item["member_id"], item["source_locator"]) for item in consumed} == {(A, "/exit/result"), (B, "/entry/request")}
    assert all(item["declaration_locators"] == ["/links/0"] for item in consumed)
    assert any(item["source_locator"] == "/connections/0" for item in value.lowering_map["introduced"])
    for declaration in value.lowering_map["declarations"]:
        assert declaration["compiled_targets"]
        for target in declaration["compiled_targets"]:
            resolve_pointer(value.compiled.to_dict(), target)


def test_member_upgrade_changes_only_selected_exact_member(fixture):
    _, _, author, _ = fixture
    first_member, ids = _member(author)
    first = author.publish(**_request(first_member, ids))
    document = first_member.module.to_dict()
    document["components"][0]["operations"][0]["outcomes"][0]["name"] = "updated"
    document["terminal"]["outcome"] = "updated"
    updated, _ = _member(author, ModuleDeclaration.from_dict(document), command="member:second", parent=first_member.revision.revision_ref, ids=ids)
    second = author.publish(**_request(first_member, ids, command_id="assembly:upgrade", parent_ref=first.revision.revision_ref,
        members=(AssemblyMember(A, "Same", first_member.revision.revision_ref), AssemblyMember(B, "Same", updated.revision.revision_ref))))
    assert second.generated.module.components[0].operations[0].outcomes[0].name == "complete"
    assert second.generated.module.components[1].operations[0].outcomes[0].name == "updated"
    refs = {item["member_id"]: item["revision_ref"] for item in second.lowering_map["origins"]}
    assert refs == {A: first_member.revision.revision_ref.to_dict(), B: updated.revision.revision_ref.to_dict()}


def test_actual_final_context_lowering_maps_derived_component_nodes(fixture):
    from cpn.rpnh.petri_contracts import PlaceDeclaration
    _, _, author, registration = fixture
    base = registration.resolve("component", "operation")
    def lower(config, context):
        fragment = base(config, context)
        transition = "derived_" + context.component
        return replace(fragment, places=(*fragment.places, PlaceDeclaration(transition, TEXT)),
            transitions=(*fragment.transitions, replace(fragment.transitions[0], name=transition)),
            arcs=(*fragment.arcs, *(replace(arc, transition=transition) for arc in fragment.arcs)))
    registration.register_component("context-derived", lower, identity={"implementation_id": "test.context-derived", "revision": "v1"},
        contracts=registration.declaration("component", "operation")["contracts"])
    document = _simple_module().to_dict()
    document["components"][0]["key"] = "context-derived"
    member, ids = _member(author, ModuleDeclaration.from_dict(document))
    result = author.publish(**_request(member, ids))
    for key, fragment in result.compiled.fragments.items():
        assert any(place.name == "derived_" + key for place in fragment.places)
        assert not any(place.name == "derived_step" for place in fragment.places)
        component = next(item for item in result.lowering_map["declarations"] if item["locator"] == f"/components/{key}")
        assert len(component["compiled_targets"]) > 8
        operation = next(item for item in result.lowering_map["declarations"] if item["locator"] == f"/components/{key}/operations/run")
        assert sum(target.startswith('/symbolic/transitions/') for target in operation['compiled_targets']) == 2
    assert len(result.lowering_map["place_aliases"]) == 6


def test_replay_reopen_and_strict_read_do_not_publish_or_adopt(fixture):
    core, gateway, author, registration = fixture
    member, ids = _member(author)
    args = _request(member, ids)
    result = author.publish(**args)
    before = (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    assert author.publish(**args).revision == result.revision
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert validate_assembly_revision(reader, result.revision.revision_ref, registration).revision == result.revision
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    replay = AssemblyAuthor(RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref), _registration(), author.producer)
    assert replay.publish(**args).revision == result.revision
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before
    assert _counts(core) == (1, 0, 0)


def _alternative_module():
    document = _simple_module().to_dict()
    component = deepcopy(document['components'][0])
    component['name'] = 'alternative'
    document['components'].append(component)
    document['entry']['alternative_request'] = {'component': 'alternative', 'port': 'request'}
    document['exit']['alternative_result'] = {'component': 'alternative', 'port': 'result'}
    alternative = deepcopy(document['terminal'])
    alternative['source']['component'] = 'alternative'
    document['terminal_alternatives'] = [alternative]
    return ModuleDeclaration.from_dict(document)


def test_root_completion_preserves_alternatives_without_unselected_terminal_join(fixture):
    _, _, author, _ = fixture
    member, ids = _member(author, _alternative_module())
    result = author.publish(**_request(member, ids))
    assert len(result.generated.module.terminal_alternatives) == 1
    assert result.generated.module.terminal_alternatives[0].source.component == prefix(B) + '_alternative'
    terminals = [item for item in result.lowering_map['origins'] if item['kind'] == 'terminal']
    assert len(terminals) == 4
    assert sum(item['disposition'] == 'not_root_completion' for item in terminals) == 2


@pytest.mark.parametrize('damage', ['duplicate_member', 'wrong_direction', 'unknown_member', 'unknown_element',
    'duplicate_connection', 'constraints', 'budget', 'bucket', 'numeric_budget', 'schema',
    'terminal_consumed', 'alternative_consumed', 'alternative_selected', 'source_alias', 'target_alias',
    'budget_policy', 'deployment_intent'])
def test_invalid_composition_cannot_publish_assembly_or_generated_candidate(fixture, damage):
    core, _, author, _ = fixture
    document = (_alternative_module() if damage in {'alternative_consumed', 'alternative_selected'} else _simple_module()).to_dict()
    if damage == 'constraints':
        document['designer_constraints'] = {'opaque': {'flag': True}}
    if damage in {'budget', 'numeric_budget'}:
        document['budgets'] = {'attempts': 1}
    if damage == 'source_alias':
        document['exit']['alias'] = deepcopy(document['exit']['result'])
    if damage == 'target_alias':
        document['entry']['alias'] = deepcopy(document['entry']['request'])
    member, ids = _member(author, ModuleDeclaration.from_dict(document))
    args = _request(member, ids, connections=_serial(ids))
    if damage == 'duplicate_member':
        args['members'] = (args['members'][0], args['members'][0])
    elif damage in {'wrong_direction', 'unknown_member', 'unknown_element'}:
        args['connections'] = (AssemblyConnection(C if damage == 'unknown_member' else A,
            ids['/entry/request'] if damage == 'wrong_direction' else 'element:' + 'f' * 32 if damage == 'unknown_element' else ids['/exit/result'],
            B, ids['/entry/request']),)
    elif damage == 'duplicate_connection':
        args['connections'] = (*args['connections'], *args['connections'])
    elif damage in {'budget', 'bucket', 'numeric_budget', 'schema'}:
        changed = deepcopy(document)
        if damage in {'budget', 'numeric_budget'}:
            changed['budgets']['attempts'] = 2 if damage == 'budget' else 1.0
        elif damage == 'bucket':
            changed['budget_buckets'][0]['max_attempts'] = 4
        else:
            changed = _simple_module(input_schema=ALT_TEXT).to_dict()
        other, other_ids = _member(author, ModuleDeclaration.from_dict(changed), command='member:other')
        args['members'] = (AssemblyMember(A, 'Same', member.revision.revision_ref), AssemblyMember(B, 'Same', other.revision.revision_ref))
        args['connections'] = (AssemblyConnection(A, ids['/exit/result'], B, other_ids['/entry/request']),)
        args['completion'] = AssemblyCompletion(B, other_ids['/terminal'])
    elif damage == 'terminal_consumed':
        args['completion'] = AssemblyCompletion(A, ids['/terminal'])
    elif damage == 'alternative_consumed':
        args['connections'] = (AssemblyConnection(B, ids['/exit/alternative_result'], A, ids['/entry/request']),)
    elif damage == 'alternative_selected':
        args['completion'] = AssemblyCompletion(B, ids['/terminal_alternatives/0'])
    elif damage == 'source_alias':
        args['members'] = (*args['members'], AssemblyMember(C, 'Third', member.revision.revision_ref))
        args['connections'] = (*args['connections'], AssemblyConnection(A, ids['/exit/alias'], C, ids['/entry/request']))
    elif damage == 'budget_policy':
        args['budget_policy'] = 'isolated'
    elif damage == 'deployment_intent':
        args['deployment_intent'] = 'adopt_now'
    before = (_counts(core), len(core.event_store.object_rows_by_type('collaboration_net_revision/v1')))
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        author.publish(**args)
    assert (_counts(core), len(core.event_store.object_rows_by_type('collaboration_net_revision/v1'))) == before


@pytest.mark.parametrize('version', [1, 2, 3, 4])
def test_known_graph_derived_members_require_future_single_source_rebuild(fixture, version):
    core, _, author, registration = fixture
    registration.register_component(f'rpnh/agent-workflow-graph/v{version}', registration.resolve('component', 'operation'),
        identity={'implementation_id': 'test.graph-looking', 'revision': 'v1'},
        contracts=registration.declaration('component', 'operation')['contracts'])
    document = _simple_module().to_dict()
    document['components'][0]['key'] = f'rpnh/agent-workflow-graph/v{version}'
    member, ids = _member(author, ModuleDeclaration.from_dict(document))
    with pytest.raises(ValueError, match='single-source rebuild'):
        author.publish(**_request(member, ids))
    assert _counts(core) == (0, 0, 0)


def _raw_record(core, record, **changes):
    reference = SourceQualifiedVersionRef('source-a', VersionRef(ASSEMBLY_TYPE, record.revision_ref.ref.entity_id, new_id('resource_version')))
    value = replace(record, revision_ref=reference, **changes)
    core.publish_bytes(object_type=ASSEMBLY_TYPE, logical_id=reference.ref.entity_id, version_id=reference.ref.version_id,
        payload=canonical_json(value.to_dict()), metadata=value.to_dict(), media_type='application/json',
        schema_ref=ASSEMBLY_SCHEMA, idempotency_key=f'raw:{reference.ref.version_id}')
    return value


def _resource(author, document, schema):
    return author._publish_document('mutation:' + uuid.uuid4().hex, schema, document)


@pytest.mark.parametrize('damage', ['missing_origin', 'duplicate_origin', 'wrong_origin', 'dangling_target',
    'missing_declaration', 'missing_alias', 'compiled', 'generated', 'plan_label', 'host_refs', 'wrong_schema'])
def test_strict_consumer_rejects_schema_valid_but_inconsistent_exact_materials(fixture, damage):
    core, _, author, registration = fixture
    member, ids = _member(author)
    valid = author.publish(**_request(member, ids, connections=_serial(ids)))
    field, schema, document = 'lowering_mapping_ref', LOWERING_SCHEMA, deepcopy(valid.lowering_map)
    if damage == 'missing_origin':
        document['origins'].pop()
    elif damage == 'duplicate_origin':
        document['origins'].append(deepcopy(document['origins'][0]))
    elif damage == 'wrong_origin':
        document['origins'][0]['element_id'] = 'element:' + 'f' * 32
    elif damage == 'dangling_target':
        document['declarations'][0]['compiled_targets'] = ['/symbolic/places/9999']
    elif damage == 'missing_declaration':
        document['declarations'].pop()
    elif damage == 'missing_alias':
        document['place_aliases'].pop()
    elif damage == 'compiled':
        field, schema, document = 'compiled_inventory_ref', COMPILED_SCHEMA, valid.compiled.to_dict()
        document['source']['name'] = 'FalseName'
    elif damage in {'plan_label', 'host_refs'}:
        field, schema, document = 'plan_ref', PLAN_SCHEMA, deepcopy(valid.plan)
        if damage == 'plan_label':
            document['members'][0]['display_name'] = 'Different label'
        else:
            document['host_requirements']['declaration_refs'].pop()
    elif damage == 'wrong_schema':
        field, schema, document = 'lowering_mapping_ref', PLAN_SCHEMA, deepcopy(valid.plan)
    if damage == 'generated':
        record = _raw_record(core, valid.revision, generated_revision_ref=member.revision.revision_ref)
    else:
        record = _raw_record(core, valid.revision, **{field: _resource(author, document, schema)})
    assert read_assembly_revision(core, record.revision_ref) == record
    before = (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        validate_assembly_revision(core, record.revision_ref, registration)
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before


@pytest.mark.parametrize('damage', ['bytes', 'commit', 'relation', 'plan_schema_commit', 'generated_bytes', 'assembly_metadata'])
def test_strict_consumer_rechecks_persisted_bytes_and_canonical_closure(fixture, damage):
    core, _, author, registration = fixture
    member, ids = _member(author)
    result = author.publish(**_request(member, ids))
    version = result.revision.lowering_mapping_ref.ref.resource_version_id
    if damage in {'bytes', 'generated_bytes'}:
        if damage == 'generated_bytes':
            version = result.generated.revision.definition_ref.ref.resource_version_id
        path = core.object_store.path_for_version(version)
        path.write_bytes(path.read_bytes().replace(b'm_', b'n_', 1))
    else:
        with core.event_store.connect() as db:
            if damage == 'plan_schema_commit':
                version = author.schemas[PLAN_SCHEMA].resource_version_id
            if damage == 'relation':
                db.execute("DELETE FROM events WHERE event_id IN (SELECT published_event_id FROM relations WHERE json_extract(source_json,'$.version_id')=?)", (str(version),))
            elif damage == 'assembly_metadata':
                body = result.revision.to_dict()
                body['command_id'] = 'forged'
                db.execute('UPDATE objects SET metadata_json=? WHERE version_id=?', (json.dumps(body), str(result.revision.revision_ref.ref.version_id)))
            else:
                db.execute("DELETE FROM events WHERE event_type='transaction_committed/v1' AND transaction_id=(SELECT transaction_id FROM objects WHERE version_id=?)", (str(version),))
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        validate_assembly_revision(core, result.revision.revision_ref, registration)


def _interrupt_preparation(author, monkeypatch, cut):
    from cpn.rpnh.collaboration import assemblies, materials
    assembly_original = assemblies._publish_private_system
    closed_original = materials._publish_private_system
    generated_original = author.author.publish
    calls = []
    def observe(original):
        def publish(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append(result)
            if len(calls) == cut:
                raise RuntimeError('interrupted after immutable preparation')
            return result
        return publish
    monkeypatch.setattr(assemblies, '_publish_private_system', observe(assembly_original))
    monkeypatch.setattr(materials, '_publish_private_system', observe(closed_original))
    if cut == 'generated':
        def generated(**kwargs):
            generated_original(**kwargs)
            raise RuntimeError('interrupted after generated candidate')
        monkeypatch.setattr(author.author, 'publish', generated)
    def restore():
        monkeypatch.setattr(assemblies, '_publish_private_system', assembly_original)
        monkeypatch.setattr(materials, '_publish_private_system', closed_original)
        monkeypatch.setattr(author.author, 'publish', generated_original)
    return calls, restore


@pytest.mark.parametrize('cut', [1, 2, 3, 4, 5, 'generated', 6, 7])
def test_every_preparation_cut_reopens_and_reuses_original_exact_refs(fixture, monkeypatch, cut):
    core, gateway, author, _ = fixture
    member, ids = _member(author)
    args = _request(member, ids)
    calls, restore = _interrupt_preparation(author, monkeypatch, cut)
    with pytest.raises(RuntimeError, match='interrupted'):
        author.publish(**args)
    assert _counts(core) == (0, 0, 0)
    restore()
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    replay = AssemblyAuthor(RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref), _registration(), author.producer)
    result = replay.publish(**args)
    prepared = [result.revision.plan_ref.ref, *[getattr(result.generated.revision, field).ref for field in
        ('definition_ref', 'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref')],
        result.revision.compiled_inventory_ref.ref, result.revision.lowering_mapping_ref.ref]
    assert prepared[:len(calls)] == calls
    assert _counts(reopened) == (1, 0, 0)


@pytest.mark.parametrize('cut', [1, 'generated', 7])
@pytest.mark.parametrize('change', ['label', 'parent', 'member_revision', 'connections', 'completion', 'name', 'producer'])
def test_first_plan_freezes_complete_request_even_when_generated_module_is_equal(fixture, monkeypatch, cut, change):
    core, _, author, _ = fixture
    member, ids = _member(author)
    parent = author.publish(**_request(member, ids, command_id='assembly:parent')) if change == 'parent' else None
    other = _member(author, command='member:other', parent=member.revision.revision_ref, ids=ids)[0] if change == 'member_revision' else None
    args = _request(member, ids)
    _, restore = _interrupt_preparation(author, monkeypatch, cut)
    with pytest.raises(RuntimeError, match='interrupted'):
        author.publish(**args)
    restore()
    changed = dict(args)
    original_producer = author.producer
    if change == 'label':
        changed['members'] = (replace(args['members'][0], display_name='Different'), args['members'][1])
    elif change == 'parent':
        changed['parent_ref'] = parent.revision.revision_ref
    elif change == 'member_revision':
        changed['members'] = (replace(args['members'][0], revision_ref=other.revision.revision_ref), args['members'][1])
    elif change == 'connections':
        changed['connections'] = _serial(ids)
    elif change == 'completion':
        changed['completion'] = AssemblyCompletion(A, ids['/terminal'])
    elif change == 'name':
        changed['name'] = 'Changed'
    else:
        # Configuration attribution is part of the immutable plan, even before
        # any generated author or HOST material has been prepared.
        principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
        body = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Other'}
        core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
            payload=canonical_json(body), metadata=body, media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='fixture:other-principal')
        author.producer = SourceQualifiedVersionRef('source-a', principal)
    before = (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    with pytest.raises(RegistryConflict, match='conflict'):
        author.publish(**changed)
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before
    author.producer = original_producer
    assert author.publish(**args).plan['members'][0]['display_name'] == 'Same'


def test_final_descriptor_failure_leaves_only_prepared_resources_and_generated_candidate(fixture, monkeypatch):
    core, _, author, _ = fixture
    member, ids = _member(author)
    args = _request(member, ids)
    original = core.event_store._insert_event
    def fail(db, event):
        original(db, event)
        if event.payload.get('object_type') == ASSEMBLY_TYPE:
            raise RuntimeError('injected Assembly commit failure')
    monkeypatch.setattr(core.event_store, '_insert_event', fail)
    with pytest.raises(RuntimeError, match='injected'):
        author.publish(**args)
    assert _counts(core) == (0, 0, 0)
    assert len(core.event_store.object_rows_by_type('collaboration_net_revision/v1')) == 2
    resources = len(core.event_store.object_rows_by_type('resource_version/v1'))
    monkeypatch.setattr(core.event_store, '_insert_event', original)
    result = author.publish(**args)
    assert result.plan['command_id'] == args['command_id']
    assert len(core.event_store.object_rows_by_type('resource_version/v1')) == resources
    assert _counts(core) == (1, 0, 0)


@pytest.mark.parametrize('different', [False, True])
def test_concurrent_same_command_has_one_immutable_request_and_success(fixture, different):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    core, _, author, _ = fixture
    member, ids = _member(author)
    barrier = Barrier(2)
    def publish(index):
        args = _request(member, ids)
        if different and index:
            args['members'] = (replace(args['members'][0], display_name='Different'), args['members'][1])
        barrier.wait()
        try:
            return author.publish(**args)
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, (0, 1)))
    success = [item for item in results if not isinstance(item, RegistryConflict)]
    assert len(success) == (1 if different else 2)
    assert len({item.revision.revision_ref for item in success}) == 1
    assert _counts(core) == (1, 0, 0)


@pytest.mark.parametrize('damage', ['foreign', 'missing', 'nested'])
def test_exact_member_authority_is_local_present_and_single_layer(fixture, damage):
    core, _, author, _ = fixture
    member, ids = _member(author)
    args = _request(member, ids)
    if damage == 'foreign':
        ref = SourceQualifiedVersionRef('other-source', member.revision.revision_ref.ref)
    elif damage == 'missing':
        ref = SourceQualifiedVersionRef('source-a', VersionRef('collaboration_net_revision/v1', new_id('resource'), new_id('resource_version')))
    else:
        assembly = author.publish(**_request(member, ids, command_id='assembly:inner'))
        ref = assembly.revision.generated_revision_ref
    args['members'] = (AssemblyMember(A, 'Different source', ref), args['members'][1])
    before = _counts(core)
    with pytest.raises((ValueError, RegistryConflict, SchemaGovernanceError)):
        author.publish(**args)
    assert _counts(core) == before


def test_assembly_type_and_schemas_remain_opt_in():
    catalog = SchemaCatalog()
    with pytest.raises(SchemaGovernanceError):
        catalog.require(ASSEMBLY_TYPE, category='object')


def test_strict_consumer_rejects_second_result_identity_for_same_plan_and_command(fixture):
    core, _, author, registration = fixture
    member, ids = _member(author)
    valid = author.publish(**_request(member, ids))
    clone = _raw_record(core, valid.revision)
    assert read_assembly_revision(core, clone.revision_ref) == clone
    with pytest.raises(RegistryConflict, match='one immutable command result'):
        validate_assembly_revision(core, clone.revision_ref, registration)
    assert validate_assembly_revision(core, valid.revision.revision_ref, registration).revision == valid.revision


def test_member_and_connection_order_are_normalized_for_exact_command_replay(fixture):
    _, _, author, _ = fixture
    member, ids = _member(author)
    args = _request(member, ids, members=tuple(AssemblyMember(key, 'Repeated', member.revision.revision_ref) for key in (C, B, A)),
        connections=(AssemblyConnection(B, ids['/exit/result'], C, ids['/entry/request']), *_serial(ids)),
        completion=AssemblyCompletion(C, ids['/terminal']))
    first = author.publish(**args)
    second = author.publish(**{**args, 'members': tuple(reversed(args['members'])), 'connections': tuple(reversed(args['connections']))})
    assert first.revision == second.revision
    assert len(first.generated.module.links) == 2
    assert len(first.generated.module.entry) == 1


def test_every_alias_of_a_consumed_source_boundary_has_explicit_provenance(fixture):
    _, _, author, _ = fixture
    document = _simple_module().to_dict()
    document['exit']['alias'] = deepcopy(document['exit']['result'])
    member, ids = _member(author, ModuleDeclaration.from_dict(document))
    result = author.publish(**_request(member, ids, connections=_serial(ids)))
    source_exits = [item for item in result.lowering_map['origins'] if item['member_id'] == A and item['kind'] == 'exit']
    assert len(source_exits) == 2
    assert all(item['disposition'] == 'consumed' and item['declaration_locators'] == ['/links/0'] for item in source_exits)


def _dual_alias_module(registration, side, *, final_context_only=False, alternative=False):
    key = 'actual-alias-' + side
    aliases = {'other_result': 'result'} if side == 'output' else {'other_request': 'request'}
    _register_alias_lower(registration, key, aliases, final_context_only=final_context_only)
    document = _simple_module().to_dict()
    component = document['components'][0]
    component['key'] = key
    other_ports = deepcopy(component['ports'])
    for port in other_ports:
        port['name'] = 'other_' + port['name']
    component['ports'].extend(other_ports)
    operation = deepcopy(component['operations'][0])
    operation.update(name='other_run', inputs=['other_request'], outputs=['other_result'])
    operation['outcomes'][0]['products'][0]['port'] = 'other_result'
    component['operations'].append(operation)
    document['entry']['other_request'] = {'component': 'step', 'port': 'other_request'}
    document['exit']['other_result'] = {'component': 'step', 'port': 'other_result'}
    if alternative:
        independent = deepcopy(_simple_module().to_dict()['components'][0])
        independent['name'] = 'independent'
        document['components'].append(independent)
        document['entry']['independent_request'] = {'component': 'independent', 'port': 'request'}
        document['exit']['independent_result'] = {'component': 'independent', 'port': 'result'}
        document['terminal_alternatives'] = [deepcopy(document['terminal'])]
        document['terminal']['source']['component'] = 'independent'
    return ModuleDeclaration.from_dict(document)


def _register_alias_lower(registration, key, aliases, *, final_context_only=False):
    from cpn.rpnh.petri_contracts import PNFragment, PlaceDeclaration, TransitionDeclaration, ArcDeclaration, PortBinding
    def lower(config, context):
        active = aliases if not final_context_only or context.component.startswith('m_') else {}
        physical = lambda name: active.get(name, name)
        names = sorted({physical(port.name) for port in context.ports})
        places = tuple(PlaceDeclaration(name, TEXT) for name in names)
        arcs = []
        for operation in context.operations:
            arcs.extend(ArcDeclaration(physical(port), operation.name, 'input') for port in operation.inputs)
            arcs.extend(ArcDeclaration(physical(product.port), operation.name, 'output', mode='produce', outcome=outcome.name)
                for outcome in operation.outcomes for product in outcome.products)
        return PNFragment(places, tuple(TransitionDeclaration(operation.name, operation.name) for operation in context.operations),
            tuple(arcs), tuple(PortBinding(port.name, physical(port.name)) for port in context.ports), context.operations)
    registration.register_component(key, lower, identity={'implementation_id': 'test.' + key, 'revision': 'v1'},
        contracts=registration.declaration('component', 'operation')['contracts'])


@pytest.mark.parametrize('final_context_only', [False, True])
@pytest.mark.parametrize('case', ['terminal', 'alternative_terminal', 'source', 'target'])
def test_actual_final_place_alias_rejects_hidden_consumers_producers_and_terminals(fixture, final_context_only, case):
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    dual, ids = _member(author, _dual_alias_module(registration, 'input' if case == 'target' else 'output',
        final_context_only=final_context_only, alternative=case == 'alternative_terminal'), command='member:aliased')
    if case in {'terminal', 'alternative_terminal'}:
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Aliased', dual.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref)),
            connections=(AssemblyConnection(A, ids['/exit/other_result'], B, sids['/entry/request']),),
            completion=AssemblyCompletion(A, ids['/terminal']))
        expected = 'selected terminal carrier'
    elif case == 'source':
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Aliased', dual.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref), AssemblyMember(C, 'Simple', simple.revision.revision_ref)),
            connections=(AssemblyConnection(A, ids['/exit/result'], B, sids['/entry/request']), AssemblyConnection(A, ids['/exit/other_result'], C, sids['/entry/request'])),
            completion=AssemblyCompletion(C, sids['/terminal']))
        expected = 'actual source carrier'
    else:
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Simple', simple.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref), AssemblyMember(C, 'Aliased', dual.revision.revision_ref)),
            connections=(AssemblyConnection(A, sids['/exit/result'], C, ids['/entry/request']), AssemblyConnection(B, sids['/exit/result'], C, ids['/entry/other_request'])),
            completion=AssemblyCompletion(C, ids['/terminal']))
        expected = 'actual target carrier'
    before = (len(core.event_store.object_rows()), _counts(core))
    with pytest.raises(ValueError, match=expected):
        author.publish(**args)
    assert (len(core.event_store.object_rows()), _counts(core)) == before


def test_one_source_connection_contracts_all_actual_exit_aliases_and_provenance(fixture):
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    dual, ids = _member(author, _dual_alias_module(registration, 'output', final_context_only=True), command='member:aliased')
    args = _request(simple, sids,
        members=(AssemblyMember(A, 'Aliased', dual.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref)),
        connections=(AssemblyConnection(A, ids['/exit/other_result'], B, sids['/entry/request']),))
    result = author.publish(**args)
    assert len(result.generated.module.links) == 1
    assert set(result.generated.module.exit) == {prefix(B) + '_result'}
    consumed = [item for item in result.lowering_map['origins'] if item['member_id'] == A and item['kind'] == 'exit']
    assert len(consumed) == 2
    assert all(item['disposition'] == 'consumed' and item['declaration_locators'] == ['/links/0'] for item in consumed)
    assert validate_assembly_revision(core, result.revision.revision_ref, registration).revision == result.revision


@pytest.mark.parametrize('final_context_only', [False, True])
def test_single_target_connection_still_rejects_actual_entry_aliases(fixture, final_context_only):
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    dual, ids = _member(author, _dual_alias_module(registration, 'input', final_context_only=final_context_only), command='member:aliased')
    args = _request(simple, sids,
        members=(AssemblyMember(A, 'Simple', simple.revision.revision_ref), AssemblyMember(B, 'Aliased', dual.revision.revision_ref)),
        connections=(AssemblyConnection(A, sids['/exit/result'], B, ids['/entry/other_request']),),
        completion=AssemblyCompletion(B, ids['/terminal']))
    before = (len(core.event_store.object_rows()), _counts(core))
    with pytest.raises(ValueError, match='actual target carrier aliases'):
        author.publish(**args)
    assert (len(core.event_store.object_rows()), _counts(core)) == before


def _internally_fused_boundary_module(registration, side, *, alternative=False):
    # Distinct fragments, each with different pre-fusion place identities;
    # an original member link, not an Assembly link, makes them one carrier.
    _register_alias_lower(registration, 'passthrough-alias', {'result': 'request'}, final_context_only=True)
    if side == 'output':
        document = _simple_module().to_dict()
        right = deepcopy(document['components'][0])
        right.update(name='right', key='passthrough-alias')
        document['components'].append(right)
        document['links'] = [{'source': {'component': 'step', 'port': 'result'}, 'target': {'component': 'right', 'port': 'request'}}]
        document['exit']['other_result'] = {'component': 'right', 'port': 'result'}
        document['terminal']['source']['component'] = 'right'
    else:
        document = _dual_alias_module(registration, 'input', final_context_only=True).to_dict()
        left = deepcopy(_simple_module().to_dict()['components'][0])
        left.update(name='left', key='passthrough-alias')
        document['components'].append(left)
        document['links'] = [{'source': {'component': 'left', 'port': 'result'}, 'target': {'component': 'step', 'port': 'other_request'}}]
        document['entry']['left_request'] = {'component': 'left', 'port': 'request'}
        del document['entry']['other_request']
    if alternative:
        independent = deepcopy(_simple_module().to_dict()['components'][0])
        independent['name'] = 'independent'
        document['components'].append(independent)
        document['entry']['independent_request'] = {'component': 'independent', 'port': 'request'}
        document['exit']['independent_result'] = {'component': 'independent', 'port': 'result'}
        document['terminal_alternatives'] = [deepcopy(document['terminal'])]
        document['terminal']['source']['component'] = 'independent'
    return ModuleDeclaration.from_dict(document)


@pytest.mark.parametrize('case', ['source', 'target', 'terminal', 'alternative_terminal'])
def test_actual_carrier_cut_preserves_member_internal_fusion(fixture, case):
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    fused, ids = _member(author, _internally_fused_boundary_module(registration, 'input' if case == 'target' else 'output',
        alternative=case == 'alternative_terminal'), command='member:fused')
    if case == 'target':
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Simple', simple.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref), AssemblyMember(C, 'Fused', fused.revision.revision_ref)),
            connections=(AssemblyConnection(A, sids['/exit/result'], C, ids['/entry/request']), AssemblyConnection(B, sids['/exit/result'], C, ids['/entry/left_request'])),
            completion=AssemblyCompletion(C, ids['/terminal']))
        expected = 'actual target carrier'
    else:
        connections = (AssemblyConnection(A, ids['/exit/result'], B, sids['/entry/request']),)
        if case == 'source':
            connections += (AssemblyConnection(A, ids['/exit/other_result'], C, sids['/entry/request']),)
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Fused', fused.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref), AssemblyMember(C, 'Simple', simple.revision.revision_ref)),
            connections=connections, completion=AssemblyCompletion(A if case in {'terminal', 'alternative_terminal'} else C,
                ids['/terminal'] if case in {'terminal', 'alternative_terminal'} else sids['/terminal']))
        expected = 'selected terminal carrier' if case in {'terminal', 'alternative_terminal'} else 'actual source carrier'
    before = (len(core.event_store.object_rows()), _counts(core))
    with pytest.raises(ValueError, match=expected):
        author.publish(**args)
    assert (len(core.event_store.object_rows()), _counts(core)) == before


def test_single_connection_contracts_source_exits_fused_by_member_internal_links(fixture):
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    fused, ids = _member(author, _internally_fused_boundary_module(registration, 'output'), command='member:fused')
    result = author.publish(**_request(simple, sids,
        members=(AssemblyMember(A, 'Fused', fused.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref)),
        connections=(AssemblyConnection(A, ids['/exit/result'], B, sids['/entry/request']),)))
    assert set(result.generated.module.exit) == {prefix(B) + '_result'}
    consumed = [item for item in result.lowering_map['origins'] if item['member_id'] == A and item['kind'] == 'exit']
    assert len(consumed) == 2
    assert all(item['disposition'] == 'consumed' and item['declaration_locators'] == ['/links/1'] for item in consumed)
    assert validate_assembly_revision(core, result.revision.revision_ref, registration).revision == result.revision


def test_strict_consumer_independently_rejects_legacy_symbol_only_alias_assembly(fixture, monkeypatch):
    from cpn.rpnh.collaboration import assemblies
    from cpn.rpnh.compiler import compile_module
    from cpn.rpnh.net_operations import ComposeConnection, ComposePlan, compose_modules
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    dual, ids = _member(author, _dual_alias_module(registration, 'output', final_context_only=True), command='member:aliased')
    args = _request(simple, sids,
        members=(AssemblyMember(A, 'Aliased', dual.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref)),
        connections=(AssemblyConnection(A, ids['/exit/other_result'], B, sids['/entry/request']),),
        completion=AssemblyCompletion(A, ids['/terminal']))
    # Fault injection deliberately recreates the old symbol-only producer to
    # persist a canonical, schema-valid specimen; no Registry closure is forged.
    def symbol_only(plan, members, registration):
        def alias(member, identity):
            return next(item['locator'].split('/', 2)[2] for item in members[member].element_map['elements'] if item['element_id'] == identity)
        connections = tuple(ComposeConnection(prefix(item['source_member_id']), alias(item['source_member_id'], item['source_exit_element_id']),
            prefix(item['target_member_id']), alias(item['target_member_id'], item['target_entry_element_id'])) for item in plan['connections'])
        module = compose_modules({prefix(item['member_id']): members[item['member_id']].module for item in plan['members']},
            ComposePlan(plan['name'], prefix(plan['completion']['member_id']), connections, mode='explicit'))
        return module, compile_module(module, registration)
    with monkeypatch.context() as patch:
        patch.setattr(assemblies, 'compose_plan', symbol_only)
        invalid = author.publish(**args)
    assert read_assembly_revision(core, invalid.revision.revision_ref) == invalid.revision
    before = (len(core.event_store.object_rows()), len(core.event_store.list_events()))
    with pytest.raises(ValueError, match='selected terminal carrier'):
        validate_assembly_revision(core, invalid.revision.revision_ref, registration)
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before


@pytest.mark.parametrize('side', ['source', 'target'])
def test_connected_cross_direction_public_aliases_are_explicitly_unsupported(fixture, side):
    core, _, author, registration = fixture
    simple, sids = _member(author, command='member:simple')
    _register_alias_lower(registration, 'cross-direction', {'result': 'request'}, final_context_only=True)
    document = _simple_module().to_dict()
    document['components'][0]['key'] = 'cross-direction'
    cross, ids = _member(author, ModuleDeclaration.from_dict(document), command='member:cross')
    if side == 'source':
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Cross', cross.revision.revision_ref), AssemblyMember(B, 'Simple', simple.revision.revision_ref)),
            connections=(AssemblyConnection(A, ids['/exit/result'], B, sids['/entry/request']),))
    else:
        args = _request(simple, sids,
            members=(AssemblyMember(A, 'Simple', simple.revision.revision_ref), AssemblyMember(B, 'Cross', cross.revision.revision_ref)),
            connections=(AssemblyConnection(A, sids['/exit/result'], B, ids['/entry/request']),),
            completion=AssemblyCompletion(B, ids['/terminal']))
    before = (len(core.event_store.object_rows()), _counts(core))
    with pytest.raises(ValueError, match='cross-direction public boundary alias'):
        author.publish(**args)
    assert (len(core.event_store.object_rows()), _counts(core)) == before
