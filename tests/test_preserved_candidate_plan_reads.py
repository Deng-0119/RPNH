"""Exact historical v2 reads and explicitly injected malformed specimens."""
from copy import deepcopy
import hashlib
import json
import socket
import subprocess
import urllib.request
import uuid

import pytest

from cpn.rpnh.collaboration import ClosedModuleAuthor, SourceQualifiedVersionRef, candidate_v2_schema_data
from cpn.rpnh.collaboration.candidate_plans import _command_key, _empty_hosts, _operation_refs, _record_ref
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.collaboration.preserved_candidate_plans import (
    _collect_preserved_plan_dependencies, _read_preserved_candidate_plan_at, read_preserved_candidate_plan,
)
from cpn.rpnh.net_operations import apply_replacement, prepare_replacement
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._candidate_read_context import _CandidateReadContext
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE, PLAN_V2_SCHEMA, MANIFEST_V2_TYPE
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.runtime_binding_contracts import freeze_candidate_document
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run
from test_native_net_operations import TEXT
from test_preserved_basis_reads import basis_for
from test_preserved_slot_projection import _slot_module, _slot_registration


@pytest.fixture(params=[False, True], ids=['slot', 'slot_and_resource'])
def preserved_plan_fixture(tmp_path, monkeypatch, request):
    def forbidden(*args, **kwargs):
        raise AssertionError('historical candidate reader cannot use sockets/processes/network')
    for target, field in ((socket, 'socket'), (subprocess, 'Popen'), (urllib.request, 'Request'), (urllib.request, 'urlopen')):
        monkeypatch.setattr(target, field, forbidden)
    schemas, types, paths = candidate_v2_schema_data()
    with_resource = request.param
    module, task = _slot_module('Creation', with_resource), OwnerInput(TEXT, canonical_json('task'), 'Task')
    owner = start_run(module, _slot_registration(with_resource), run_dir=tmp_path / 'preserved-plan', task_input=task,
        entry_inputs={'request': task}, resource_inputs={'step.asset': task} if with_resource else None,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']), (TEXT,), 3, 0, 3, 0),
        model_condition='offline-slot-plan', owner_statement='Offline preserved plan fixture', command_id='fixture:slot-plan',
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner.schema_gateway.bind_source_identity(source_id='slot-source', command_id='fixture:source')
    outcome = apply_replacement(owner, prepare_replacement(owner, _slot_module('Replacement', with_resource)),
        command_id='fixture:replacement')
    assert outcome['status'] == 'ADOPTED'
    basis = basis_for(owner._core.event_store.list_events_by_type(('net_adopted/v1',))[-1])
    author = ClosedModuleAuthor(owner.schema_gateway, owner.registration,
        SourceQualifiedVersionRef('slot-source', owner.principal_ref))
    candidate = _slot_module('ClosedCandidate', with_resource)
    material = author.publish(module=candidate, element_ids={locator: 'element:' + uuid.uuid4().hex
        for locator in _elements(candidate)}, command_id='fixture:closed-candidate')
    core = owner._core
    current = core.get_version(basis.net_ref.version_id).metadata
    operation = core.get_version(_version_from_payload(current['operation_binding_refs'][0]).version_id).metadata
    key = _command_key(core.task_id, 'slot-source', 'fixture:history-plan')
    native = {name: ref_payload(getattr(owner.identity, name)) for name in (
        'task_ref', 'run_ref', 'task_branch_ref', 'genesis_manifest_ref')}
    native.update(branch_id=owner.identity.branch_id, protocol_versions=list(owner.identity.protocol_versions))
    compiled = material.compiled
    plan = {'schema_version': PLAN_V2_SCHEMA,
        'plan_ref': ref_payload(_record_ref(PLAN_V2_TYPE, key + ':plan')),
        'manifest_ref': ref_payload(_record_ref(MANIFEST_V2_TYPE, key + ':manifest')),
        'source_id': 'slot-source', 'command_id': 'fixture:history-plan',
        'owner_task_ref': ref_payload(owner.identity.task_ref), 'producer_principal_ref': author.producer.to_dict(),
        'author_ref': material.revision.revision_ref.to_dict(), 'principal_ref': ref_payload(owner.principal_ref),
        'bootstrap_ref': ref_payload(owner.bootstrap_ref), 'task_round_ref': ref_payload(owner.task_round_ref),
        'authority_decision_ref': operation['authority_decision_ref'], 'run_identity': native,
        'graph_command_key': key + ':graph', 'operation_command_key': key + ':operations',
        'schema_refs': {item['key']: item['resource_ref']['ref'] for item in material.host_requirements['declaration_refs']
            if item['kind'] == 'schema' and item['key'] in compiled.source.required_schemas},
        'operation_refs': _operation_refs(compiled, key + ':operations'),
        'compiled': compiled.to_dict(), 'host_requirements': material.host_requirements,
        'entry_inputs': {}, 'owner_resource_inputs': current['module_resource_bindings']['owner_resource_inputs'],
        'owner_input_resources': [], 'preserved_slot_refs': current['module_resource_bindings']['slot_refs'],
        'preserved_basis': basis.to_dict(), 'host_bindings': _empty_hosts(compiled),
        'runtime_dependencies': {item.name: {} for item in compiled.symbolic.transitions},
        'host_inventory': {'resource_refs': [], 'artifact_refs': []}, 'command_context': {'extra': True},
        'dependency_evidence': []}
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        plan['dependency_evidence'] = _collect_preserved_plan_dependencies(_CandidateReadContext.from_core(core, db), plan)
    return owner, material, plan, basis


def publish_fixture_plan(core, document):
    """Real raw Core publication through the fixed v2 first/replay gate."""
    ref = _version_from_payload(document['plan_ref'])
    key = _command_key(core.task_id, document['source_id'], document['command_id'])
    core.publish_bytes(object_type=PLAN_V2_TYPE, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=freeze_candidate_document(document).encode(), metadata=document, media_type='application/json',
        schema_ref=PLAN_V2_SCHEMA, idempotency_key=key + ':plan')
    return ref


def inject_historical_plan_specimen(core, document, *, carrier_key=None):
    """Inject a canonical historical malformed record after normal rejection.

    Publish a v1 carrier through the unchanged generic contract, then rewrite
    its explicit object/publication/command fields and exact file as a database
    specimen. This helper never disables the gate and is used only by negative
    historical-reader tests. It does not claim valid v2 admission.
    """
    from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE, PLAN_SCHEMA, MANIFEST_TYPE
    ref = _version_from_payload(document['plan_ref'])
    carrier = deepcopy(document)
    carrier.pop('preserved_basis')
    carrier['schema_version'] = PLAN_SCHEMA
    carrier['plan_ref']['entity_type'] = PLAN_TYPE
    carrier['manifest_ref']['entity_type'] = MANIFEST_TYPE
    path = core.object_store.path_for_version(ref.version_id)
    # Normal rejection may leave an immutable prewrite. Fixture injection is
    # explicitly allowed to corrupt this test file; production never does.
    path.write_bytes(freeze_candidate_document(carrier).encode())
    key = carrier_key or _command_key(core.task_id, document['source_id'], document['command_id']) + ':plan'
    core.publish_bytes(object_type=PLAN_TYPE, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=freeze_candidate_document(carrier).encode(), metadata=carrier, media_type='application/json',
        schema_ref=PLAN_SCHEMA, idempotency_key=key)
    data = freeze_candidate_document(document).encode()
    path.write_bytes(data)
    with core.event_store.connect() as db:
        row = db.execute('SELECT published_event_id,transaction_id FROM objects WHERE version_id=?', (str(ref.version_id),)).fetchone()
        event = db.execute('SELECT payload_json FROM events WHERE event_id=?', (row['published_event_id'],)).fetchone()
        publication = json.loads(event[0])
        publication.update(object_type=PLAN_V2_TYPE, schema_ref=PLAN_V2_SCHEMA, metadata=document, size=len(data))
        db.execute('UPDATE objects SET object_type=?,schema_ref=?,metadata_json=?,size=? WHERE version_id=?',
            (PLAN_V2_TYPE, PLAN_V2_SCHEMA, json.dumps(document), len(data), str(ref.version_id)))
        db.execute('UPDATE events SET aggregate_type=?,payload_json=? WHERE event_id=?',
            (PLAN_V2_TYPE, json.dumps(publication), row['published_event_id']))
        command = json.loads(db.execute('SELECT command_json FROM transactions WHERE transaction_id=?',
            (row['transaction_id'],)).fetchone()[0])
        command['objects'][0].update(object_type=PLAN_V2_TYPE, metadata=document)
        command['events'][0].update(aggregate_type=PLAN_V2_TYPE, payload=publication)
        db.execute('UPDATE transactions SET command_json=? WHERE transaction_id=?',
            (json.dumps(command), row['transaction_id']))
    return ref


def test_historical_reader_has_one_cut_recorder_real_author_and_no_host_or_writes(preserved_plan_fixture, monkeypatch):
    owner, material, plan, basis = preserved_plan_fixture
    core = owner._core
    ref = publish_fixture_plan(core, plan)
    before = len(core.event_store.object_rows()), len(core.event_store.list_events())
    actual, original = set(), ObjectStore.read_registered
    def record(store, prepared):
        payload = original(store, prepared)
        actual.add((prepared.object_type, str(prepared.logical_id), str(prepared.version_id),
            hashlib.sha256(payload).hexdigest(), len(payload), prepared.media_type))
        return payload
    def forbidden(*args, **kwargs):
        raise AssertionError('historical read cannot resolve HOST, write or open another cut')
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        context = _CandidateReadContext.from_core(core, db)
        with monkeypatch.context() as patch:
            patch.setattr(ObjectStore, 'read_registered', record)
            patch.setattr(Registration, 'resolve', forbidden)
            patch.setattr(core.event_store, 'connect', forbidden)
            patch.setattr(core, 'get_version', forbidden)
            patch.setattr(core, 'begin', forbidden)
            result = _read_preserved_candidate_plan_at(context, ref)
    assert result.plan == plan and result.preserved_basis == basis
    observed_dependencies = {row for row in actual if row[:3] != (ref.entity_type, str(ref.entity_id), str(ref.version_id))}
    expected = {(r['ref']['entity_type'], r['ref']['logical_id'], r['ref']['version_id'], r['sha256'], r['size'], r['media_type'])
        for r in result.plan['dependency_evidence']}
    assert observed_dependencies == expected
    assert ref_payload(material.revision.revision_ref.ref) in [r['ref'] for r in result.plan['dependency_evidence']]
    assert (len(core.event_store.object_rows()), len(core.event_store.list_events())) == before
    for kind in ('runtime_binding_manifest/v2', 'binding_readiness/v2', 'transition_firing/v1',
                 'llm_invocation_attempt/v1', 'operation_execution_lease/v1'):
        assert core.event_store.object_rows_by_type(kind) == ()


def recollect(core, plan):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        plan['dependency_evidence'] = _collect_preserved_plan_dependencies(_CandidateReadContext.from_core(core, db), plan)


def test_saved_history_remains_exact_after_real_later_owner_adoption(preserved_plan_fixture):
    owner, _, plan, basis = preserved_plan_fixture
    ref = publish_fixture_plan(owner._core, plan)
    before = read_preserved_candidate_plan(owner._core, ref)
    with_resource = bool(plan['owner_resource_inputs'])
    outcome = apply_replacement(owner, prepare_replacement(owner, _slot_module('LaterReplacement', with_resource)),
        command_id='fixture:later-owner-adoption')
    assert outcome['status'] == 'ADOPTED'
    latest = basis_for(owner._core.event_store.list_events_by_type(('net_adopted/v1',))[-1])
    assert latest != basis and latest.net_ref != basis.net_ref
    assert read_preserved_candidate_plan(owner._core, ref) == before


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_empty_v2_selection_has_null_basis_and_only_static_evidence(preserved_plan_fixture):
    owner, _, plan, _ = preserved_plan_fixture
    plan['preserved_slot_refs'] = {}
    plan['preserved_basis'] = None
    recollect(owner._core, plan)
    ref = publish_fixture_plan(owner._core, plan)
    result = read_preserved_candidate_plan(owner._core, ref)
    assert result.preserved_basis is None
    assert not any(item['ref']['entity_type'] == 'net_instance/v1' for item in result.plan['dependency_evidence'])


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
@pytest.mark.parametrize('axis', [
    'source', 'owner', 'run', 'round', 'authority', 'author', 'compiled_source', 'compiled_schema',
    'host_material', 'schema_ref', 'plan_id', 'manifest_id', 'operation_refs', 'graph_key', 'operation_key',
    'basis_digest', 'slot_ref', 'entry', 'owner_resources', 'host_binding', 'evidence_missing', 'evidence_sha',
])
def test_canonical_static_bad_plan_is_rejected_by_independent_reader(preserved_plan_fixture, axis):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.identities import new_id
    owner, _, plan, _ = preserved_plan_fixture
    if axis == 'source':
        plan['source_id'] = 'other-source'
    elif axis == 'owner':
        plan['owner_task_ref']['logical_id'] = str(new_id('task'))
    elif axis == 'run':
        plan['run_identity']['run_ref']['version_id'] = str(new_id('run_version'))
    elif axis == 'round':
        plan['task_round_ref']['logical_id'] = str(new_id('task_round'))
    elif axis == 'authority':
        plan['authority_decision_ref']['version_id'] = str(new_id('user_authority_decision_version'))
    elif axis == 'author':
        plan['author_ref']['ref']['version_id'] = str(new_id('resource_version'))
    elif axis == 'compiled_source':
        plan['compiled']['source']['name'] = 'DifferentSource'
    elif axis == 'compiled_schema':
        plan['compiled']['registrations']['schema'][TEXT]['schema']['type'] = 'number'
    elif axis == 'host_material':
        plan['host_requirements']['declaration_refs'][0]['resource_ref']['ref']['resource_version_id'] = str(new_id('resource_version'))
    elif axis == 'schema_ref':
        plan['schema_refs'][TEXT]['resource_version_id'] = str(new_id('resource_version'))
    elif axis == 'plan_id':
        plan['plan_ref']['logical_id'] = str(new_id('resource'))
    elif axis == 'manifest_id':
        plan['manifest_ref']['version_id'] = str(new_id('resource_version'))
    elif axis == 'operation_refs':
        plan['operation_refs'] = {}
    elif axis == 'graph_key':
        plan['graph_command_key'] += ':other'
    elif axis == 'operation_key':
        plan['operation_command_key'] += ':other'
    elif axis == 'basis_digest':
        plan['preserved_basis']['adoption_event_sha256'] = '0' * 64
    elif axis == 'slot_ref':
        next(iter(plan['preserved_slot_refs'].values()))['version_id'] = str(new_id('logical_slot_version'))
    elif axis == 'entry':
        plan['entry_inputs'] = {'not_an_entry': []}
    elif axis == 'owner_resources':
        plan['owner_resource_inputs'] = {'not_a_lease': dict(plan['schema_refs'][TEXT])}
    elif axis == 'host_binding':
        next(iter(plan['host_bindings'].values()))['activation_ref'] = dict(plan['principal_ref'])
    elif axis == 'evidence_missing':
        plan['dependency_evidence'] = []
    else:
        plan['dependency_evidence'][0]['sha256'] = '0' * 64
    from cpn.rpnh.collaboration.authoring import AuthorRevisionReadError
    expected = AuthorRevisionReadError if axis == 'author' else RegistryConflict
    with pytest.raises(expected):
        publish_fixture_plan(owner._core, plan)
    assert owner._core.event_store.object_rows_by_type(PLAN_V2_TYPE) == ()
    ref = inject_historical_plan_specimen(owner._core, plan)
    with pytest.raises(expected) as failure:
        read_preserved_candidate_plan(owner._core, ref)
    if axis == 'author':
        assert type(failure.value.__cause__) is RegistryConflict
        assert 'canonical publication/commit closure' in str(failure.value)


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_schema_integer_float_requires_typed_basis_validation(preserved_plan_fixture):
    owner, _, plan, _ = preserved_plan_fixture
    event = plan['preserved_basis']['adoption_event']
    event['task_control_sequence'] = float(event['task_control_sequence'])
    with pytest.raises(TypeError, match='positive builtin integer'):
        publish_fixture_plan(owner._core, plan)  # Standard Draft7 permits 4.0.
    ref = inject_historical_plan_specimen(owner._core, plan)
    with pytest.raises(TypeError, match='positive builtin integer'):
        read_preserved_candidate_plan(owner._core, ref)


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_request_context_boolean_number_metadata_difference_rejects(preserved_plan_fixture):
    from cpn.rpnh.registry.event_store import RegistryConflict
    owner, _, plan, _ = preserved_plan_fixture
    core = owner._core
    ref = publish_fixture_plan(core, plan)
    # Keep the payload bytes frozen, but make row/publication metadata agree on
    # 1 where the complete request really contained true. Python == is unsafe.
    plan['command_context']['extra'] = 1
    with core.event_store.connect() as db:
        event = db.execute('SELECT published_event_id FROM objects WHERE version_id=?', (str(ref.version_id),)).fetchone()[0]
        db.execute('UPDATE objects SET metadata_json=? WHERE version_id=?', (json.dumps(plan), str(ref.version_id)))
        row = db.execute('SELECT payload_json FROM events WHERE event_id=?', (event,)).fetchone()
        payload = json.loads(row[0]); payload['metadata'] = plan
        db.execute('UPDATE events SET payload_json=? WHERE event_id=?', (json.dumps(payload), event))
    with pytest.raises(RegistryConflict, match='bytes differ from registered metadata'):
        read_preserved_candidate_plan(core, ref)


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_shared_author_basis_reference_cannot_change_bytes_between_roles(preserved_plan_fixture, monkeypatch):
    import cpn.rpnh.collaboration.preserved_candidate_plans as reader
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.identities import TypedId
    owner, _, plan, _ = preserved_plan_fixture
    core = owner._core
    ref = publish_fixture_plan(core, plan)
    schema = plan['schema_refs'][TEXT]
    path = core.object_store.path_for_version(TypedId.parse(schema['resource_version_id']))
    original_bytes = path.read_bytes()
    replacement = original_bytes.replace(b'"string"', b'"number"')
    assert replacement != original_bytes and len(replacement) == len(original_bytes)
    original = reader._pure_author_preflight
    def change_after_author(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_bytes(replacement)
        return result
    monkeypatch.setattr(reader, '_pure_author_preflight', change_after_author)
    try:
        with pytest.raises(RegistryConflict, match='candidate dependency changed within one read cut'):
            read_preserved_candidate_plan(core, ref)
    finally:
        path.write_bytes(original_bytes)


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_equal_schema_bytes_under_alternate_exact_ref_are_not_author_projection(preserved_plan_fixture):
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    from cpn.rpnh.registry.strict_contracts import content_schema_ref_payload
    owner, _, plan, _ = preserved_plan_fixture
    ref = owner.schema_gateway.schema_refs[TEXT]
    prepared = owner._core.get_version(ref.resource_version_id)
    alternate = _publish_private_system(owner._core, owner.identity.task_ref, PublishResource(
        origin=PrivateSystemOrigin(owner.bootstrap_ref), payload=owner._core.object_store.read_registered(prepared),
        media_type='application/schema+json', content_schema_ref='registry_v1/registry_type_catalog/v1',
        summary='Explicit alternate schema', lifetime_ref=owner.bootstrap_ref, idempotency_key='fixture:alternate-schema'))
    assert alternate != ref
    plan['schema_refs'][TEXT] = content_schema_ref_payload(alternate)
    with pytest.raises(RegistryConflict, match='exact author declarations'):
        publish_fixture_plan(owner._core, plan)
    plan_ref = inject_historical_plan_specimen(owner._core, plan)
    with pytest.raises(RegistryConflict, match='exact author declarations'):
        read_preserved_candidate_plan(owner._core, plan_ref)


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
def test_mechanical_reader_does_not_attest_that_host_produced_the_fragments(preserved_plan_fixture):
    from cpn.rpnh.executable_net import _load_compiled_net_offline
    owner, material, plan, _ = preserved_plan_fixture
    wire = plan['compiled']
    wire['fragments']['step']['logical_slots'][0]['artifact_synopsis'] = 'Static alternate synopsis'
    wire['symbolic']['logical_slots'][0]['artifact_synopsis'] = 'Static alternate synopsis'
    altered = _load_compiled_net_offline(wire)
    assert altered != material.compiled
    assert altered.source == material.compiled.source
    recollect(owner._core, plan)
    ref = publish_fixture_plan(owner._core, plan)
    assert read_preserved_candidate_plan(owner._core, ref).plan['compiled'] == wire


@pytest.mark.parametrize('preserved_plan_fixture', [False], indirect=True)
@pytest.mark.parametrize('state', ['PUBLISHED', 'PROVISIONAL'])
def test_historical_plan_keeps_business_promotion_semantics(preserved_plan_fixture, state):
    from cpn.rpnh.registry.event_store import RegistryConflict, RegistryCorruptError
    from test_candidate_plan_resources import mark_member
    owner, _, plan, _ = preserved_plan_fixture
    resource = owner.original_input_ref
    plan['entry_inputs'] = {'request': [{'resource_id': str(resource.resource_id),
        'resource_version_id': str(resource.resource_version_id)}]}
    recollect(owner._core, plan)
    ref = publish_fixture_plan(owner._core, plan)
    # Read-side membership specimen and a real Core promotion transaction;
    # this does not claim a full actual firing/publication workflow.
    mark_member(owner._core, resource.resource_version_id, state)
    if state == 'PUBLISHED':
        assert read_preserved_candidate_plan(owner._core, ref).plan == plan
    else:
        with pytest.raises((RegistryConflict, RegistryCorruptError)):
            read_preserved_candidate_plan(owner._core, ref)
