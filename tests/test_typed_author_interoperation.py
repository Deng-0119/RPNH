"""Finite ControlIR -> existing author materials and exact Branch; no runtime."""
from copy import deepcopy
from dataclasses import replace
import json
import uuid

import pytest

from cpn.rpnh.collaboration import (
    AssemblyAuthor, AssemblyAuthorV2, AssemblyMember, AssemblyMemberV2,
    AssemblyConnection, AssemblyCompletion, ClosedModuleAuthor,
    SourceQualifiedVersionRef, graph_assembly_schema_data,
    current_branch, read_branch_version, validate_closed_revision,
)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.control_ir import ControlIR, ControlIRError, PROOF_KEY
from cpn.rpnh.executable_net import load_compiled_net, load_compiled_control_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from test_control_ir_compiler import author_document, registration

A, B = ('member:' + c * 32 for c in 'ab')


@pytest.fixture
def world(tmp_path):
    schemas, types, paths = graph_assembly_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(tmp_path / 'typed-author', create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(('typed-author-interoperation/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id='source-typed', command_id='source:typed')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    body = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id),
            'display_name': 'Offline typed author'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id,
        version_id=principal.version_id, payload=canonical_json(body), metadata=body,
        media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='fixture:principal')
    reg = registration()
    producer = SourceQualifiedVersionRef('source-typed', principal)
    author = ClosedModuleAuthor(gateway, reg, producer)
    return core, gateway, reg, producer, author


def prepared(reg, capacity=10, version=1):
    document = author_document()
    document['bindings']['capacity']['value'] = capacity
    document['bindings']['capacity']['origin']['ref']['version'] = version
    compiled = compile_module(ControlIR.from_dict(document), reg)
    ids = {path: 'element:' + uuid.uuid5(uuid.NAMESPACE_URL, 'typed-author:' + path).hex
           for path in _elements(compiled.source)}
    return document, compiled, ids


def counts(core):
    with core.event_store.connect() as db:
        return {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in ('objects', 'events', 'relations', 'transactions')}


def prove_typed(validated, original, compiled):
    assert canonical_json(validated.module.to_dict()) == canonical_json(compiled.source.to_dict())
    assert canonical_json(validated.compiled.to_dict()) == canonical_json(compiled.to_dict())
    proof = validated.module.designer_constraints[PROOF_KEY]
    assert proof['author'] == original
    assert proof['evaluations'] == compiled.source.designer_constraints[PROOF_KEY]['evaluations']
    assert proof['evaluations'][0]['read_set']['heads'] == []
    assert len(proof['evaluations'][0]['read_set']['immutable_refs']) == 1
    assert all(row['status'] == 'NATIVE' for row in proof['capabilities'])
    assert load_compiled_control_net(validated.compiled.to_json()).to_dict() == compiled.to_dict()
    assert validated.host_requirements['registrations'] == compiled.to_dict()['registrations']


def no_runtime(core):
    assert core.event_store.list_events_by_type(('net_adopted/v1', 'marking_checkpoint_committed/v1',
        'firing_started/v1', 'firing_succeeded/v1')) == ()
    assert core.event_store.object_rows_by_type('net_instance/v1') == ()
    assert core.branch_id == 'main'


def test_prepared_typed_author_publishes_reopens_and_tracks_exact_branch_head(world):
    core, gateway, reg, _, author = world
    original, compiled, ids = prepared(reg)
    first = author.publish(module=compiled.source, element_ids=ids, command_id='typed:first')
    prove_typed(first, original, compiled)
    before_retry = counts(core)
    replay = author.publish(module=compiled.source, element_ids=ids, command_id='typed:first')
    assert replay.revision == first.revision and counts(core) == before_retry
    branch = gateway.create_author_branch(head_revision_ref=first.revision.revision_ref, command_id='typed:branch')
    changed, changed_compiled, retained_ids = prepared(reg, capacity=20, version=2)
    second = author.publish(module=changed_compiled.source, element_ids=retained_ids,
        command_id='typed:second', parent_ref=first.revision.revision_ref)
    assert retained_ids == ids
    assert first.compiled.symbolic.operations[0].config == {'threshold': 7}
    assert second.compiled.symbolic.operations[0].config == {'threshold': 13}
    advanced = gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=first.revision.revision_ref, expected_stream_head=1,
        next_revision_ref=second.revision.revision_ref, command_id='typed:advance')
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    exact = current_branch(reader, branch.branch_ref.ref.entity_id)
    assert exact == advanced and exact.head_revision_ref == second.revision.revision_ref
    assert read_branch_version(reader, branch.branch_ref).head_revision_ref == first.revision.revision_ref
    prove_typed(validate_closed_revision(reader, exact.head_revision_ref, reg), changed, changed_compiled)
    prove_typed(validate_closed_revision(reader, first.revision.revision_ref, reg), original, compiled)
    no_runtime(core)
    print('TYPED_BRANCH_RESULT', json.dumps({'first_ref': first.revision.revision_ref.to_dict(),
        'head_ref': exact.head_revision_ref.to_dict(), 'branch_ref': exact.branch_ref.to_dict(),
        'first_threshold': 7, 'head_threshold': 13, 'proof_preserved': True}, sort_keys=True))


def test_invalid_prepared_proof_fails_before_revision_publication(world):
    core, _, reg, _, author = world
    _, compiled, ids = prepared(reg)
    module = compiled.source.to_dict()
    module['components'][0]['operations'][0]['config']['threshold'] = 8
    before = counts(core)
    with pytest.raises(ControlIRError, match='control_proof_mismatch'):
        author.publish(module=ModuleDeclaration.from_dict(module), element_ids=ids, command_id='typed:invalid')
    assert counts(core) == before
    no_runtime(core)


def test_required_typed_boundary_rejects_stripped_and_unsupported_proofs(world):
    _, _, reg, _, author = world
    _, compiled, ids = prepared(reg)
    real = author.publish(module=compiled.source, element_ids=ids, command_id='typed:required')
    document = real.compiled.to_dict()
    assert load_compiled_control_net(document).to_dict() == document
    stripped = deepcopy(document)
    for surface in (stripped['source'], stripped['symbolic']):
        del surface['designer_constraints'][PROOF_KEY]
    assert load_compiled_net(stripped).symbolic.operations[0].config == {'threshold': 7}
    with pytest.raises(ControlIRError, match='control_proof_required'):
        load_compiled_control_net(stripped)
    for mode in ('unknown', 'null'):
        damaged = deepcopy(document)
        for surface in (damaged['source'], damaged['symbolic']):
            if mode == 'unknown':
                surface['designer_constraints']['rpnh_control_ir_v2'] = surface['designer_constraints'].pop(PROOF_KEY)
            else:
                surface['designer_constraints'][PROOF_KEY] = None
        for loader in (load_compiled_net, load_compiled_control_net):
            with pytest.raises(ControlIRError):
                loader(damaged)


def test_real_assembly_publish_rejects_typed_member_without_erasing_proof(world):
    core, gateway, reg, producer, author = world
    original, compiled, ids = prepared(reg)
    member = author.publish(module=compiled.source, element_ids=ids, command_id='typed:member')
    branch = gateway.create_author_branch(head_revision_ref=member.revision.revision_ref, command_id='typed:member-branch')
    publishers = ((AssemblyAuthor(gateway, reg, producer), AssemblyMember,
                   'Assembly does not support nonempty member designer_constraints'),
                  (AssemblyAuthorV2(gateway, reg, producer), AssemblyMemberV2,
                   'plain v1 member cannot supply graph or opaque constraints proof'))
    for index, (publisher, member_type, message) in enumerate(publishers, 1):
        before = counts(core)
        with pytest.raises(ValueError, match=message) as caught:
            publisher.publish(name='Typed pair', members=(member_type(A, 'Same', member.revision.revision_ref),
                member_type(B, 'Same', member.revision.revision_ref)),
                connections=(AssemblyConnection(A, ids['/exit/result'], B, ids['/entry/request']),),
                completion=AssemblyCompletion(B, ids['/terminal']), budget_policy='shared_exact',
                deployment_intent='same_run_candidate', command_id='typed:assembly-v' + str(index))
        assert counts(core) == before
        assert current_branch(core, branch.branch_ref.ref.entity_id) == branch
        prove_typed(validate_closed_revision(core, member.revision.revision_ref, reg), original, compiled)
        print('ASSEMBLY_TYPED_SEAM', json.dumps({'version': index, 'error': str(caught.value),
            'registry_counts_unchanged': True, 'member_proof_preserved': True}, sort_keys=True))
    no_runtime(core)


def test_prepared_typed_module_recompile_matches_direct_author():
    reg = registration()
    original, compiled, _ = prepared(reg)
    reopened_module = ModuleDeclaration.from_json(compiled.source.to_json())
    recompiled = compile_module(reopened_module, reg)
    assert canonical_json(recompiled.to_dict()) == canonical_json(compiled.to_dict())
    assert recompiled.source.designer_constraints[PROOF_KEY]['author'] == original
    assert recompiled.source.designer_constraints[PROOF_KEY] == compiled.source.designer_constraints[PROOF_KEY]
    assert recompiled.fragments == compiled.fragments
    assert recompiled.symbolic == compiled.symbolic
    assert load_compiled_control_net(recompiled.to_json()).to_dict() == compiled.to_dict()


def test_prepared_typed_remote_schema_rejects_without_retrieval():
    _, compiled, _ = prepared(registration())
    unreachable = registration(config_schema={'$ref': 'https://example.invalid/never-retrieve-prepared-schema.json'})
    with pytest.raises(ControlIRError, match='schema_reference_unresolvable'):
        compile_module(compiled.source, unreachable)


def test_legacy_direct_python_numeric_constraint_keys_still_normalize():
    base = ModuleDeclaration.from_dict(author_document()['module'])
    direct = replace(base, designer_constraints={1: {'legacy_values': [1, 1.0]}})
    normalized = ModuleDeclaration.from_dict(direct.to_dict())
    assert normalized.designer_constraints == {'1': {'legacy_values': [1, 1.0]}}
    compiled_direct = compile_module(direct, registration())
    compiled_normalized = compile_module(normalized, registration())
    assert canonical_json(compiled_direct.to_dict()) == canonical_json(compiled_normalized.to_dict())
    assert compiled_direct.source.designer_constraints == normalized.designer_constraints
    assert PROOF_KEY not in compiled_direct.source.designer_constraints
