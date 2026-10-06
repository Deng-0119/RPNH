"""Finite 14C Core publication checks. No start_run/adoption/worker execution."""
from copy import deepcopy
from dataclasses import replace
import json
import uuid
import pytest
from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.collaboration import ClosedModuleAuthor, SourceQualifiedVersionRef, candidate_v2_schema_data
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.collaboration.candidate_plans import CandidatePlanPublisher, _command_key
from cpn.rpnh.collaboration.preserved_candidate_publisher import PreservedCandidatePlanPublisher
from cpn.rpnh.collaboration.preserved_candidate_plans import read_preserved_candidate_plan
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef, PendingEvent
from cpn.rpnh.registry.operations import bind_operation_registration
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import publish_user_authority_decision, ref_payload
from cpn.rpnh.registry.runtime_binding_contracts import PLAN_TYPE, freeze_candidate_document
from cpn.rpnh.registry.preserved_binding_contracts import PLAN_V2_TYPE
TEXT = "application/net_operation_test_text/v1"
ALT_TEXT = "application/net_operation_test_alt_text/v1"
EXECUTOR = "test/net-operation-business/v1"
TERMINAL = "test/net-operation-terminal/v1"

def _schema(schema_id):
    return {"$id": schema_id, "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "string"}

def _registration():
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(TEXT, _schema(TEXT))
    registration.register_schema(ALT_TEXT, _schema(ALT_TEXT))
    registration.register_executor(EXECUTOR, lambda **_kwargs: None,
        identity={"implementation_id": "test.net_operation_business", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None,
                   "output_ports": None, "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "test.net_operation_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return registration

def _simple_module(name="Simple", *, input_schema=TEXT, output_schema=TEXT):
    binding = {"bucket_id": "work", "budget_scope": "module",
               "finalization_scope": None}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": name,
        "components": [{
            "name": "step", "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": input_schema},
                {"name": "result", "direction": "output", "schema": output_schema},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"],
                "request_port": None, "tools": [], "config": {},
                "budget_binding": binding,
                "outcomes": [{"name": "complete",
                              "products": [{"port": "result"}]}],
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": TERMINAL,
                     "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete",
                     "config": {"run_outcome": "complete"}},
        "required_schemas": sorted({CONFIG_SCHEMA_ID, input_schema, output_schema}),
        "budgets": {},
        "budget_buckets": [{**binding, "max_attempts": 3}],
    })

@pytest.fixture
def plan_fixture(tmp_path):
    from cpn.rpnh.collaboration.candidate_plans import CandidatePlanPublisher
    schemas, types, paths = candidate_v2_schema_data()
    core = _RegistryCore(tmp_path / "candidate", create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    identity = _bootstrap_identity(core, NativeBootstrapManifest(("candidate-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-a", command_id="source:bind")

    def publish(kind, logical, version, body, key):
        ref = VersionRef(kind, new_id(logical), new_id(version))
        document = body(ref)
        core.publish_bytes(object_type=kind, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(document), metadata=document, media_type="application/json",
            schema_ref="registry_v1/" + kind, idempotency_key=key)
        return ref

    principal = publish("principal/v1", "principal", "principal_version", lambda ref: {
        "principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id),
        "display_name": "Offline candidate owner"}, "fixture:principal")
    round_ref = publish("task_round/v1", "task_round", "task_round_version", lambda ref: {
        "task_round_id": str(ref.entity_id), "task_id": str(core.task_id), "round_number": 1,
        "predecessor_task_round_id": None, "task_branch_ref": ref_payload(identity.task_branch_ref)}, "fixture:round")
    decision = publish_user_authority_decision(core, authority_kind="scope",
        canonical_statement="Prepare local static candidates", user_principal_ref=principal,
        governed_artifact_refs=(identity.task_ref,), selected_choices={"candidate": "offline"},
        effective_sequence=1, supersedes_ref=None, idempotency_key="fixture:authority")
    registration = _registration()
    author = ClosedModuleAuthor(gateway, registration, SourceQualifiedVersionRef("source-a", principal))
    bind_operation_registration(registration)
    publisher = CandidatePlanPublisher(gateway, registration, author.producer)
    return core, identity, gateway, principal, round_ref, decision, registration, author, publisher

def authored(fixture, module=None, command="author:first"):
    module = _simple_module() if module is None else module
    return fixture[-2].publish(module=module,
        element_ids={key: "element:" + uuid.uuid4().hex for key in _elements(module)}, command_id=command)

def candidate_request(fixture, revision, **changes):
    request = dict(author_ref=revision.revision.revision_ref, identity=fixture[1],
        task_round_ref=fixture[4], authority_decision_ref=fixture[5], command_id="candidate:first")
    return dict(request, **changes)


class StringKind(str):
    pass


class ReservedKey(str):
    def startswith(self, *args, **kwargs):
        return False

    def endswith(self, *args, **kwargs):
        return False


def counts(core):
    return len(core.event_store.object_rows()), len(core.event_store.list_events())


def stage(core, document, kind, key):
    ref = _version_from_payload(document['plan_ref'])
    tx = core.begin(idempotency_key=key)
    prepared = tx.prewrite(object_type=kind, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=freeze_candidate_document(document).encode(), metadata=document,
        media_type='application/json', schema_ref='registry_v1/' + kind)
    return tx, prepared


def fresh(document):
    value = deepcopy(document)
    value['plan_ref'].update(logical_id=str(new_id('resource')), version_id=str(new_id('resource_version')))
    return value


def mixed_version_commit(core, document, key):
    tx, prepared = stage(core, document, PLAN_TYPE, key)
    # Supported direct EventStore batch boundary: genuine v1 prewrite paired with
    # a v2 publication kind. No validator patch or stored-DB corruption.
    body = {'logical_id': str(prepared.logical_id), 'version_id': str(prepared.version_id),
        'object_type': PLAN_V2_TYPE, 'size': prepared.size, 'media_type': prepared.media_type,
        'schema_ref': prepared.schema_ref, 'storage_locator': prepared.storage_locator,
        'metadata': dict(prepared.metadata)}
    events = (PendingEvent(event_type='object_version_published/v1', criticality='authoritative',
        stream_id=f'object:{prepared.logical_id}', aggregate_id=str(prepared.logical_id),
        aggregate_type=PLAN_V2_TYPE, idempotency_key=key, command_id=key, payload=body,
        payload_schema_ref='registry_v1/object_version_published/v1'),
        PendingEvent(event_type='transaction_committed/v1', criticality='authoritative',
        stream_id=f'transaction:{tx.transaction_id}', aggregate_id=str(tx.transaction_id),
        aggregate_type='transaction', idempotency_key=key, command_id=key,
        payload={'object_count': 1, 'relation_count': 0, 'fact_count': 1},
        payload_schema_ref='registry_v1/transaction_committed/v1'))
    return core.event_store.publish_batch(task_id=core.task_id, branch_id=core.branch_id,
        task_round_id=None, net_instance_id=None, transaction_id=tx.transaction_id,
        idempotency_key=key, writer_epoch=core.writer_epoch, objects=(prepared,),
        events=events, relations=(), expected_heads={e.stream_id: 0 for e in events})


def test_four_pending_axes_through_real_core(plan_fixture):
    f = plan_fixture
    core = f[0]
    revision = authored(f)
    request = candidate_request(f, revision)
    v1 = f[-1].publish(**request).plan
    publisher = PreservedCandidatePlanPublisher(f[2], f[6], f[7].producer)
    v2 = publisher.publish(**dict(request, command_id='candidate:v2')).plan
    unknown = 'collaboration_candidate_plan/v99'
    schema = 'registry_v1/' + unknown
    core.catalog.register_schema(schema, {'$id': schema, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
    core.catalog.register_type(replace(core.catalog.require(PLAN_V2_TYPE, category='object'), name=unknown, schema_ref=schema))
    actions = [
        ('ordinary-v2-string-subclass', lambda: stage(core, fresh(v2), StringKind(PLAN_V2_TYPE), 'fixture:ordinary-v2')[0].commit()),
        ('ordinary-unknown-string-subclass', lambda: core.publish_bytes(object_type=StringKind(unknown),
            logical_id=new_id('resource'), version_id=new_id('resource_version'), payload=b'{}', metadata={},
            media_type='application/json', schema_ref=schema, idempotency_key='fixture:unknown')),
        ('reserved-key-subclass', lambda: core.begin(idempotency_key=ReservedKey('collaboration-candidate:finite:plan')).commit()),
        ('v1-prepared-v2-publication', lambda: mixed_version_commit(core, fresh(v1), 'fixture:mixed-version')),
    ]
    outcomes = []
    for axis, action in actions:
        before = counts(core)
        try:
            action()
        except RegistryConflict as error:
            outcomes.append({'axis': axis, 'outcome': 'REJECTED', 'error': str(error), 'unchanged': counts(core) == before})
        else:
            outcomes.append({'axis': axis, 'outcome': 'ACCEPTED', 'unchanged': counts(core) == before})
    print('FOUR_AXIS_OUTCOMES ' + json.dumps(outcomes, sort_keys=True), flush=True)
    assert all(row['outcome'] == 'REJECTED' and row['unchanged'] for row in outcomes), outcomes


@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('kind_type', [str, StringKind], ids=['builtin', 'subclass'])
def test_legacy_v1_ordinary_publication_and_replay(plan_fixture, mixed, kind_type):
    f = plan_fixture
    doc = fresh(f[-1].publish(**candidate_request(f, authored(f))).plan)
    tx, _ = stage(f[0], doc, kind_type(PLAN_TYPE), 'fixture:ordinary-v1')
    if mixed:
        logical, version = new_id('principal'), new_id('principal_version')
        body = {'principal_id': str(logical), 'principal_version_id': str(version), 'display_name': 'Extra'}
        tx.prewrite(object_type='principal/v1', logical_id=logical, version_id=version,
            payload=canonical_json(body), metadata=body, media_type='application/json', schema_ref='registry_v1/principal/v1')
    first = tx.commit()
    before = counts(f[0])
    replay, _ = stage(f[0], doc, kind_type(PLAN_TYPE), 'fixture:ordinary-v1')
    if mixed:
        replay.prewrite(object_type='principal/v1', logical_id=logical, version_id=version,
            payload=canonical_json(body), metadata=body, media_type='application/json', schema_ref='registry_v1/principal/v1')
    recovered = replay.commit()
    assert all(type(event.ordinal) is int for event in recovered)
    assert tuple(replace(event, ordinal=None) for event in recovered) == first
    assert counts(f[0]) == before


def test_v2_producer_reader_reopen_and_replay_remain_inert(plan_fixture):
    f = plan_fixture
    publisher = PreservedCandidatePlanPublisher(f[2], f[6], f[7].producer)
    request = candidate_request(f, authored(f), command_context={'input': True})
    first = publisher.publish(**request)
    before = counts(f[0])
    assert publisher.publish(**request) == first
    reader = _RegistryCore(f[0].run_dir, create=False, read_only=True, catalog=f[0].catalog)
    assert read_preserved_candidate_plan(reader, first.plan_ref) == first
    assert counts(f[0]) == before
    with pytest.raises(RegistryConflict, match='complete frozen request'):
        publisher.publish(**dict(request, command_context={'input': 1}))
    for kind in ('runtime_binding_manifest/v2', 'binding_readiness/v2', 'operation_execution_lease/v1', 'llm_invocation_attempt/v1', 'net_instance/v1'):
        assert f[0].event_store.object_rows_by_type(kind) == ()
    assert f[0].event_store.list_events_by_type(('net_adopted/v1', 'marking_checkpoint_committed/v1')) == ()


def test_v2_entry_preserves_existing_v1(plan_fixture):
    f = plan_fixture
    request = candidate_request(f, authored(f))
    first = f[-1].publish(**request)
    before = counts(f[0])
    assert PreservedCandidatePlanPublisher(f[2], f[6], f[7].producer).publish(**request) == first
    assert first.plan_ref.entity_type == PLAN_TYPE and counts(f[0]) == before


@pytest.mark.parametrize('axis', ['ordinary', 'reserved-empty', 'unknown'])
def test_builtin_string_neighbor_refusals(plan_fixture, axis):
    f = plan_fixture
    core = f[0]
    publisher = PreservedCandidatePlanPublisher(f[2], f[6], f[7].producer)
    doc = publisher.publish(**candidate_request(f, authored(f))).plan
    unknown = 'collaboration_candidate_plan/v99'
    schema = 'registry_v1/' + unknown
    if axis == 'unknown':
        core.catalog.register_schema(schema, {'$id': schema, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
        core.catalog.register_type(replace(core.catalog.require(PLAN_V2_TYPE, category='object'), name=unknown, schema_ref=schema))
    before = counts(core)
    with pytest.raises(RegistryConflict):
        if axis == 'ordinary':
            stage(core, fresh(doc), PLAN_V2_TYPE, 'fixture:ordinary-v2')[0].commit()
        elif axis == 'reserved-empty':
            core.begin(idempotency_key='collaboration-candidate:finite:plan').commit()
        else:
            core.publish_bytes(object_type=unknown, logical_id=new_id('resource'), version_id=new_id('resource_version'),
                payload=b'{}', metadata={}, media_type='application/json', schema_ref=schema, idempotency_key='fixture:unknown')
    assert counts(core) == before
