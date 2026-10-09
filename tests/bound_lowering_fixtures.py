"""D0-only bootstrap injection. Origin PN comes from the product compiler."""
from dataclasses import replace
import json
from cpn.rpnh.bound_child_lowering import with_bound_origin, ORIGIN_SYMBOL
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.registry import parent_child as h7, parent_bound as bound
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from parent_child_fixtures import evidence, accepted_parent, catalog
from test_static_lease_reads import TEXT


def compiled_bound_owner(path, monkeypatch, registration, module, *, entries=None, resources=None, task=None, missing_arc=False):
    import cpn.rpnh.run as run
    task = task or OwnerInput(TEXT, canonical_json('task'), 'Task')
    entries = {'request': task} if entries is None else entries
    resources = {} if resources is None else resources
    values = accepted_parent(path.parent / 'parent', child_registration=registration,
        child_module=module, child_task=task, child_entries=entries, child_resources=resources)
    module = with_bound_origin(module)
    compiled = compile_module(module, registration)
    parent = values[0]; accepted = values[-1]
    body = parent._core.get_version(accepted.version_id).metadata
    with parent._core.event_store.connect() as db:
        row = db.execute('SELECT transaction_id FROM objects WHERE version_id=?', (str(accepted.version_id),)).fetchone()
        fact = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='parent_child_recorded/v1'", (row[0],)).fetchone()
        committed = db.execute("SELECT ordinal FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (row[0],)).fetchone()
    external = {'parent_source_id': body['parent']['source_id'], 'acceptance_json': h7._json(body).decode(),
        'intent_json': h7._json(parent._core.get_version(values[4].version_id).metadata).decode(),
        'acceptance_transaction_id': row[0], 'acceptance_event_id': fact[0], 'acceptance_commit_ordinal': committed[0],
        'initial_declaration_digest': values[3].materials['initial_declaration_digest']}
    original_bootstrap = run._bootstrap_identity; original_publish = run._publish_private_system
    def bootstrap(core, manifest, **kwargs):
        marker_manifest = replace(manifest, protocol_versions=(*manifest.protocol_versions, h7.BOUND_PROTOCOL))
        with h7._native_boundary(evidence(core.event_store, 'bound_bootstrap', external), core.event_store, 'bound_bootstrap', external):
            return original_bootstrap(core, marker_manifest, _parent_bound_acceptance=external, **kwargs)
    def publish(core, task_ref, command, **kwargs):
        if command.content_schema_ref != h7.CAPABILITY_SCHEMA:
            return original_publish(core, task_ref, command, **kwargs)
        from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
        RegistryRegistrationGateway(core, task_ref, command.origin.bootstrap_command_ref).bind_source_identity(
            source_id='child-source', command_id='child:source')
        marker = json.loads(core.event_store.object_rows_by_type(h7.BOUND_KINDS[0])[0]['metadata_json'])
        return bound.close_bound_origin(core, evidence=evidence(core.event_store, 'bound_origin', marker))
    placeholder = {'schema_version': h7.CAPABILITY_SCHEMA, 'origin_ref': _ref_payload(accepted),
        'marker_ref': _ref_payload(accepted), 'child_run_ref': _ref_payload(parent.identity.run_ref),
        'child_task_ref': _ref_payload(parent.identity.task_ref), 'parent_acceptance': external,
        'initial_writer_epoch': 1, 'initial_declaration_digest': external['initial_declaration_digest']}
    resource_inputs = {**resources, ORIGIN_SYMBOL: OwnerInput(h7.CAPABILITY_SCHEMA, canonical_json(placeholder), 'D0 protected origin injection')}
    # Only transport/native issuance is injected. No component supplies an origin arc.
    with monkeypatch.context() as patch:
        patch.setattr(run, '_bootstrap_identity', bootstrap)
        patch.setattr(run, '_publish_private_system', publish)
        if missing_arc:
            # Corrupt only this negative's already compiled child publication.
            # Registered parent materials remain genuine and mechanically bound.
            damaged = replace(compiled, symbolic=replace(compiled.symbolic,
                arcs=tuple(a for a in compiled.symbolic.arcs if a.place != ORIGIN_SYMBOL)))
            patch.setattr(run, 'compile_module', lambda *_args, **_kwargs: damaged)
        child = start_run(module, registration, run_dir=path, task_input=task,
            entry_inputs=entries, resource_inputs=resource_inputs,
            budgets=ModuleBudgetDeclaration(tuple(module.to_dict()['budget_buckets']), (TEXT,), 20, 0, 20, 0),
            model_condition='offline-h7', owner_statement='Offline mechanical lowering',
            command_id='child:fresh', catalog=catalog())
    return child, values
