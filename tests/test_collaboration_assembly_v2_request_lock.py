"""Legal alternate requests: real success controls, then immutable-plan conflicts."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from cpn.rpnh.collaboration import (
    AssemblyAuthorV2, AssemblyCompletion, GraphModuleAuthor, SourceQualifiedVersionRef,
    validate_assembly_revision,
)
from cpn.rpnh.collaboration import assembly_v2 as assembly
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.transaction import RegistryTransaction
from test_collaboration_assembly_v2_publication import A, B, assert_no_run, fixture, request
from test_collaboration_assembly_v2_recovery import CUTS, DurableCut, _facts, _payload, _stages, _version
from test_collaboration_graph_materials import forbidden_execution, registration
from test_collaboration_graph_source import recipe


ALTERNATE_TERMINAL = 'test/request-lock-terminal/v1'
VARIANTS = (('parent', 'plan'), ('producer', 'plan'), ('exact_member', 'plan'),
            ('recipe_and_HOST', 'plan'), ('member_UUID', 'generated_revision'),
            ('connections', 'generated_revision'), ('completion', 'lowering_mapping'),
            ('name', 'lowering_mapping'))


def _with_alternate_terminal(selected):
    selected.register_tool(ALTERNATE_TERMINAL, forbidden_execution,
        identity={'implementation_id': 'test.request-lock-terminal', 'revision': 'v1'},
        contracts={'binding_protocol': 'rpnh/module_terminal/v1'})
    return selected


def _registration(variant):
    selected = registration()
    return _with_alternate_terminal(selected) if variant == 'recipe_and_HOST' else selected


def _success_control(fixture, variant):
    core, gateway, author, selected, member = fixture
    original = request(member, command_id='assembly:request-lock',
                       **({'connections': ()} if variant == 'completion' else {}))
    changed = dict(original)
    producer = author.producer
    description = {'variant': variant}
    if variant == 'parent':
        parent = author.publish(**request(member, command_id='assembly:fixture-parent'))
        changed['parent_ref'] = parent.revision.revision_ref
        description['parent_ref'] = parent.revision.revision_ref.to_dict()
    elif variant == 'producer':
        ref = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
        body = {'principal_id': str(ref.entity_id), 'principal_version_id': str(ref.version_id),
                'display_name': 'Second request-lock fixture producer'}
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(body), metadata=body, media_type='application/json',
            schema_ref='registry_v1/principal/v1', idempotency_key='fixture:second-producer')
        producer = SourceQualifiedVersionRef(author.binding['source_id'], ref)
        description['producer_ref'] = producer.to_dict()
    elif variant in ('exact_member', 'recipe_and_HOST'):
        source = deepcopy(member.source)
        if variant == 'exact_member':
            source['graph']['nodes'].reverse()
            options = recipe()
            assert canonical_json(source) != canonical_json(member.source)
        else:
            _with_alternate_terminal(selected)
            options = recipe(terminal_key=ALTERNATE_TERMINAL)
        updated = GraphModuleAuthor(gateway, selected, author.producer).publish(source=source, recipe=options,
            source_ids={row['locator']: row['element_id'] for row in member.source_map['elements']},
            parent_ref=member.revision.revision_ref, command_id='member:request-lock:' + variant)
        if variant == 'exact_member':
            assert canonical_json(updated.module.to_dict()) == canonical_json(member.module.to_dict())
            assert updated.source_map == member.source_map
        changed['members'] = (original['members'][0], replace(original['members'][1], revision_ref=updated.revision.revision_ref))
        description.update(old_member_ref=member.revision.revision_ref.to_dict(),
                           new_member_ref=updated.revision.revision_ref.to_dict())
    elif variant == 'member_UUID':
        replacement = 'member:' + 'c' * 32
        changed['members'] = tuple(replace(row, member_id=replacement) if row.member_id == A else row
                                   for row in original['members'])
        changed['connections'] = tuple(replace(row,
            source_member_id=replacement if row.source_member_id == A else row.source_member_id,
            target_member_id=replacement if row.target_member_id == A else row.target_member_id)
            for row in original['connections'])
        completion = original['completion']
        changed['completion'] = replace(completion, member_id=replacement) if completion.member_id == A else completion
        description['replacement_member_id'] = replacement
    elif variant == 'connections':
        changed['connections'] = ()
    elif variant == 'completion':
        ids = {row['locator']: row['element_id'] for row in member.element_map['elements']}
        changed['completion'] = AssemblyCompletion(A, ids['/terminal'])
        assert original['connections'] == changed['connections'] == ()
    elif variant == 'name':
        changed['name'] = 'ChangedGraphPair'
    else:
        raise AssertionError(variant)

    control_author = AssemblyAuthorV2(gateway, _registration(variant), producer)
    control = control_author.publish(**{**changed, 'command_id': 'assembly:legal-control:' + variant})
    assert control.revision.producer_principal_ref == producer
    before_read = _facts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    verified = validate_assembly_revision(reader, control.revision.revision_ref, _registration(variant))
    assert verified.revision == control.revision and verified.lowering_map == control.lowering_map
    assert canonical_json(verified.compiled.to_dict()) == canonical_json(control.compiled.to_dict())
    assert _facts(core) == before_read
    if variant == 'recipe_and_HOST':
        tools = control.generated.host_requirements['registrations']['tool']
        assert ALTERNATE_TERMINAL in tools and 'test/graph-terminal/v1' not in tools
        chosen = next(row for row in control.generated.host_requirements['declaration_refs']
                      if row['kind'] == 'tool' and row['key'] == ALTERNATE_TERMINAL)
        host_ref = gateway.declaration_refs['tool', ALTERNATE_TERMINAL]
        assert chosen['resource_ref']['ref'] == {'resource_id': str(host_ref.resource_id),
            'resource_version_id': str(host_ref.resource_version_id)}
        description['new_final_used_HOST_ref'] = chosen['resource_ref']
        description['HOST_change_is_coupled_to_recipe_and_member'] = True
    description['control_ref'] = control.revision.revision_ref.to_dict()
    description['control_fresh_full_consumer'] = True
    assert_no_run(core)
    return original, changed, producer, control, description


@pytest.mark.parametrize('variant,cut', VARIANTS, ids=[value[0] for value in VARIANTS])
def test_legal_changed_request_cannot_replace_complete_original_plan(fixture, monkeypatch, variant, cut):
    core, gateway, author, _, _ = fixture
    original_args, changed_args, variant_producer, control, description = _success_control(fixture, variant)
    stages = _stages(author, original_args['command_id'])
    by_key = {key: (name, reference) for name, key, reference in stages}
    before, observed, saved_bytes = _facts(core), [], {}
    original_commit = RegistryTransaction.commit
    def committed_then_cut(transaction):
        result = original_commit(transaction)
        if transaction.event_store is core.event_store and transaction.idempotency_key in by_key:
            name, reference = by_key[transaction.idempotency_key]
            assert transaction._closed and name == CUTS[len(observed)]
            exact = _version(reference)
            row = core.event_store.object_row(exact.version_id)
            assert row is not None and row['transaction_id'] == str(transaction.transaction_id)
            with core.event_store.connect() as db:
                assert db.execute('SELECT status FROM transactions WHERE transaction_id=?',
                                  (str(transaction.transaction_id),)).fetchone()[0] == 'committed'
            raw = _payload(core, reference)
            saved_bytes[name] = raw
            observed.append({'stage': name, 'reference': reference.to_dict(), 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest(), 'transaction_id': str(transaction.transaction_id)})
            if name == cut:
                raise DurableCut('request lock at ' + cut)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(RegistryTransaction, 'commit', committed_then_cut)
        with pytest.raises(DurableCut, match='request lock at ' + cut):
            author.publish(**original_args)
    assert [row['stage'] for row in observed] == list(CUTS[:CUTS.index(cut) + 1])
    after_cut = _facts(core)
    assert after_cut['objects'] - before['objects'] == len(observed)
    assert after_cut['committed_transactions'] - before['committed_transactions'] == len(observed)
    assert core.event_store.object_row(_version(stages[-1][2]).version_id) is None
    plan_ref = stages[0][2]
    locked_plan = json.loads(saved_bytes['plan'])
    metadata = json.loads(core.event_store.object_row(_version(plan_ref).version_id)['metadata_json'])
    fixed_envelope = metadata['descriptors']['assembly_author_command_v2']
    locked_documents = json.loads(fixed_envelope)['documents']
    assert json.loads(fixed_envelope)['plan'] == locked_plan
    if variant in ('parent', 'producer', 'exact_member'):
        assert canonical_json(control.generated.module.to_dict()) == canonical_json(locked_documents[0])
        description['control_generated_Module_equals_locked_original_Module'] = True
    if variant == 'recipe_and_HOST':
        assert ALTERNATE_TERMINAL not in locked_documents[3]['registrations']['tool']

    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    new_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    replay = AssemblyAuthorV2(new_gateway, _registration(variant), author.producer)
    changed_author = AssemblyAuthorV2(new_gateway, _registration(variant), variant_producer)
    assert reopened.writer_epoch > core.writer_epoch and replay is not author
    assert _facts(reopened) == after_cut
    plan_attempts = []
    original_publish = changed_author._publish_document
    def observe_actual_plan(key, schema, document, **kwargs):
        if key == stages[0][1]:
            assert schema == assembly.PLAN_V2_SCHEMA
            plan_attempts.append(deepcopy(document))
        return original_publish(key, schema, document, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(changed_author, '_publish_document', observe_actual_plan)
        with pytest.raises(RegistryConflict, match='immutable complete prepared material') as rejected:
            changed_author.publish(**changed_args)
    assert len(plan_attempts) == 1  # Real preflight succeeded and reached the first plan writer.
    assert plan_attempts[0]['command_id'] == original_args['command_id']
    assert plan_attempts[0]['producer_principal_ref'] == variant_producer.to_dict()
    assert canonical_json(plan_attempts[0]) != canonical_json(locked_plan)
    assert _facts(reopened) == after_cut
    for name, _, reference in stages[:len(observed)]:
        assert _payload(reopened, reference) == saved_bytes[name]
    assert json.loads(reopened.event_store.object_row(_version(plan_ref).version_id)['metadata_json'])['descriptors']['assembly_author_command_v2'] == fixed_envelope

    value = replay.publish(**original_args)
    final_refs = (value.revision.plan_ref, value.generated.revision.definition_ref,
        value.generated.revision.element_mapping_ref, value.generated.revision.boundary_mapping_ref,
        value.generated.revision.host_requirements_ref, value.revision.generated_revision_ref,
        value.revision.compiled_inventory_ref, value.revision.lowering_mapping_ref, value.revision.revision_ref)
    assert final_refs == tuple(reference for _, _, reference in stages)
    assert value.plan == locked_plan
    actual_documents = [value.generated.module.to_dict(), value.generated.element_map,
        value.generated.boundary_map, value.generated.host_requirements, value.compiled.to_dict(), value.lowering_map]
    assert canonical_json(actual_documents) == canonical_json(locked_documents)
    for reference, signature, document in zip((final_refs[1], final_refs[2], final_refs[3], final_refs[4], final_refs[6], final_refs[7]),
                                              locked_plan['prepared_materials'], locked_documents, strict=True):
        raw = _payload(reopened, reference)
        assert raw == canonical_json(document) and signature['resource_ref'] == reference.to_dict()
        assert len(raw) == signature['bytes'] and hashlib.sha256(raw).hexdigest() == signature['sha256']
    for name, _, reference in stages[:len(observed)]:
        assert _payload(reopened, reference) == saved_bytes[name]
    after_recovery = _facts(reopened)
    assert after_recovery == {'objects': before['objects'] + 9, 'events': before['events'] + 25,
                              'committed_transactions': before['committed_transactions'] + 9}
    reader = _RegistryCore(reopened.run_dir, create=False, read_only=True, catalog=reopened.catalog)
    verified = validate_assembly_revision(reader, value.revision.revision_ref, _registration(variant))
    assert verified.revision == value.revision and verified.plan == value.plan and verified.lowering_map == value.lowering_map
    assert canonical_json(verified.generated.module.to_dict()) == canonical_json(value.generated.module.to_dict())
    assert canonical_json(verified.compiled.to_dict()) == canonical_json(value.compiled.to_dict())
    assert _facts(reopened) == after_recovery
    assert_no_run(reader)
    print('C_DR03_TRACE=' + json.dumps({'variant': variant, 'cut': cut, 'legal_control': description,
        'original_prefix': observed, 'variant_reached_real_plan_writer': True, 'rejection': str(rejected.value),
        'before': before, 'after_cut': after_cut, 'after_original_recovery': after_recovery,
        'old_epoch': core.writer_epoch, 'new_epoch': reopened.writer_epoch,
        'original_exact_materials_preserved': True, 'fresh_readonly_full_consumer': True}, sort_keys=True))
