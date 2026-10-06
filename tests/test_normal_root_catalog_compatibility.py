"""Dormant genuine-history reader fixture for a separately pinned artifact.

The caller must supply an authorized isolated copy and its actual old result.
This helper creates no Registry and never copies, repairs or opens the original.
It is not collected as an automatic synthetic-history regression.
"""


def verify_genuine_legacy_root_copy(copied_run_dir, expected):
    import hashlib
    import json

    from cpn.frontend.worksets import workset_view
    from cpn.rpnh.collaboration.root_terminals import read_root_terminal
    from cpn.rpnh.collaboration.schema_catalog import normal_child_root_schema_data
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json

    schemas, types, paths = normal_child_root_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(copied_run_dir, create=False, read_only=True, catalog=catalog)
    object_root = core.object_store.root
    before = {
        "committed_ordinal": core.event_store.max_ordinal(),
        "writer_epoch": core.event_store.writer_epoch,
        "immutable_object_sha256": {
            str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in object_root.rglob("*") if path.is_file()
        },
    }
    verified = read_root_terminal(core, expected["root"]["record_ref"])
    assert verified["root"] == expected["root"]
    assert verified["workset"] == expected["workset"]
    assert verified["seal"] == expected["seal"]
    assert verified["verified_at_cut"] == expected["read_cut"]
    view = workset_view(core)
    assert view["schema_version"] == "rpnh/workset_view/v2"
    row, = [item for item in view["current"]
            if item["workset_ref"] == verified["workset"]["record_ref"]]
    assert row["physical_coverage"] == "source_not_observed"
    assert row["root_terminal_ref"] == verified["root"]["record_ref"]
    assert row["root_child_closure"]["seal_ref"] == verified["root"]["body"]["required_child_seal_ref"]
    assert row["required_child_seal_ref"] is None

    completion_ref = verified["root"]["body"]["completion_ref"]
    completion = core.get_version(TypedId.parse(completion_ref["version_id"], expected="firing_completion_version"))
    completion_bytes = core.object_store.read_registered(completion)
    assert completion_bytes == canonical_json(completion.metadata)
    delta_ref = completion.metadata["marking_delta_ref"]
    delta = core.get_version(TypedId.parse(delta_ref["version_id"], expected="marking_delta_version"))
    delta_bytes = core.object_store.read_registered(delta)
    assert delta_bytes == canonical_json(delta.metadata)
    assert delta.metadata["marking_delta_ref"] == delta_ref
    assert "ordinary_token_ref_scheme" not in delta.metadata

    genesis_ref = json.loads(core.event_store.get_meta("native_genesis_ref"))
    genesis = core.get_version(TypedId.parse(genesis_ref["version_id"], expected="native_genesis_version"))
    genesis_bytes = core.object_store.read_registered(genesis)
    assert genesis_bytes == canonical_json(genesis.metadata)
    catalog_ref = genesis.metadata["type_catalog_ref"]
    original_catalog = core.get_version(TypedId.parse(catalog_ref["version_id"], expected="resource_version"))
    catalog_bytes = core.object_store.read_registered(original_catalog)
    assert catalog_bytes == canonical_json(original_catalog.metadata)
    old_schema_entry = original_catalog.metadata["schemas"]["registry_v1/marking_delta/v1"]
    old_schema = json.loads(old_schema_entry["source"])
    assert "ordinary_token_ref_scheme" not in old_schema["properties"]
    assert original_catalog.logical_id == TypedId.parse(catalog_ref["logical_id"], expected="schema")
    assert original_catalog.object_type == "registry_type_catalog/v1"

    after = {
        "committed_ordinal": core.event_store.max_ordinal(),
        "writer_epoch": core.event_store.writer_epoch,
        "immutable_object_sha256": {
            str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in object_root.rglob("*") if path.is_file()
        },
    }
    assert after == before
    return {
        "node": "NORMAL_ROOT_GENUINE_PRECHANGE_COLD_READER",
        "before": before, "after": after, "verified": verified, "view": view,
        "legacy_delta_ref": delta_ref, "legacy_delta": dict(delta.metadata),
        "completion_sha256": hashlib.sha256(completion_bytes).hexdigest(),
        "legacy_delta_sha256": hashlib.sha256(delta_bytes).hexdigest(),
        "genesis_ref": genesis_ref, "genesis": dict(genesis.metadata),
        "genesis_sha256": hashlib.sha256(genesis_bytes).hexdigest(),
        "catalog_ref": catalog_ref, "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "legacy_marking_delta_schema_entry": old_schema_entry,
        "physical_coverage": "source_not_observed",
        "authoritative_writes": False,
        "original_artifact_opened": False,
        "filesystem_sidecar_immutability_claimed": False,
    }


def verify_old_initial_catalog_blocks_new_normal_write(directory, old_delta_source):
    """Candidate fixture: publish old schema bytes initially, with current HOST.

    The fixture-local bundle override is explicit input authority construction,
    never a mutation of an already published catalog or a validator replacement.
    This candidate requires its own static scope/call review before execution.
    """
    import hashlib
    import json

    from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.collaboration.schema_catalog import normal_child_root_schema_data
    from cpn.rpnh.collaboration.worksets import WorksetExpectation, WorksetOwner
    from cpn.rpnh.file_execution_net import execution_parent
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
    from cpn.rpnh.run import OwnerInput, start_run
    from normal_child_root_matrix_evidence import expect_rejected_without_commit
    from normal_child_root_matrix_fixture import never_dispatch, operation_component, register_products, start_parent

    schema_id = "registry_v1/marking_delta/v1"
    old_schema = json.loads(old_delta_source)
    assert old_schema["$id"] == schema_id
    assert "ordinary_token_ref_scheme" not in old_schema["properties"]

    class OldInitialCatalog(SchemaCatalog):
        def bundle(self):
            data = super().bundle()
            data["schemas"][schema_id]["source"] = old_delta_source
            return data

    schemas, types, paths = normal_child_root_schema_data()
    catalog = OldInitialCatalog(schemas=schemas, types=types, schema_paths=paths)
    host_delta_source = catalog.schema_path(schema_id).read_text(encoding="utf-8")
    assert json.loads(host_delta_source)["properties"]["ordinary_token_ref_scheme"] == {
        "type": "string", "const": "normal_root_firing_scoped/v1"}
    assert host_delta_source != old_delta_source
    registration = Registration()
    register_basic_components(registration)
    text_schema = "application/normal_child_root_text/v1"
    registration.register_schema(text_schema, {
        "$id": text_schema, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    registration.register_executor("test/normal-child-matrix-never-dispatch/v1", never_dispatch,
        identity={"implementation_id": "normal-child-matrix-static-products", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                   "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool("test/normal-child-matrix-never-terminal/v1", never_dispatch,
        identity={"implementation_id": "normal-child-matrix-terminal-declaration", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    module_document = {
        "schema_version": "rpnh/module_declaration/v1", "name": "OldCatalogNormalWriteRefusal",
        "components": [operation_component("root")], "links": [],
        "entry": {"request": {"component": "root", "port": "request"}},
        "exit": {"result": {"component": "root", "port": "result"}},
        "terminal": {"key": "test/normal-child-matrix-never-terminal/v1",
            "source": {"component": "root", "port": "result"}, "operation": "run",
            "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, text_schema], "budgets": {},
        "budget_buckets": [{"bucket_id": "work", "budget_scope": "module",
            "finalization_scope": None, "max_attempts": 1}],
    }
    module = ModuleDeclaration.from_dict(module_document)
    owner = start_run(module, registration, run_dir=directory / "old-catalog-target",
        task_input=OwnerInput(text_schema, canonical_json("old catalog task"), "Old catalog task"),
        entry_inputs={"request": OwnerInput(text_schema, canonical_json("old catalog input"), "Old catalog input")},
        budgets=ModuleBudgetDeclaration(tuple(module_document["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="offline-normal-child-no-model", owner_statement="Bounded initial catalog refusal",
        command_id="fixture:old-catalog-target", catalog=catalog,
        host_execution_bindings=None, configuration_sources=None)
    owner.schema_gateway.bind_source_identity(source_id="old-catalog-target", command_id="source:old-catalog-target")
    worksets = WorksetOwner(owner)
    initial = worksets.create(
        requirements_ref=SourceQualifiedVersionRef("old-catalog-target", owner.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef("old-catalog-target", owner.original_input_ref.as_version_ref()),
        generation=0, expected_slots=("answer",), command_id="old-catalog-target:create")
    sealed = worksets.change(WorksetExpectation.from_record(owner._core, initial),
        action="seal", command_id="old-catalog-target:seal")
    execution = start_parent(owner, "root", "old-catalog-target:root")
    outputs = register_products(owner, execution, "old-catalog-target:root")
    parent = execution_parent(execution.operation.canonical.context)
    expected = WorksetExpectation.from_record(owner._core, sealed)
    genesis = owner._core.get_version(owner.identity.genesis_manifest_ref.version_id)
    catalog_ref = genesis.metadata["type_catalog_ref"]
    persisted_catalog = owner._core.get_version(TypedId.parse(catalog_ref["version_id"], expected="resource_version"))
    persisted_bytes = owner._core.object_store.read_registered(persisted_catalog)
    assert persisted_bytes == canonical_json(persisted_catalog.metadata)
    assert persisted_catalog.metadata["schemas"][schema_id]["source"] == old_delta_source
    object_root = owner._core.object_store.root
    files_before = {str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in object_root.rglob("*") if path.is_file()}
    rejected = expect_rejected_without_commit(owner,
        case_id="OLD_CATALOG_NEW_NORMAL_WRITE", workset_reference=initial["record_ref"],
        parent_references=(parent,),
        action=lambda: worksets.complete_normal_children(expected=expected, outputs=outputs,
            output_port=outputs.outputs[0].port_id, command_id="old-catalog-target:normal-root"),
        exception_type=RegistryConflict,
        message="normal root token allocation: original persisted catalog lacks the normal-root allocation capability",
        guard_qualname="_require_normal_root_schema_rule")
    files_after = {str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in object_root.rglob("*") if path.is_file()}
    assert files_after == files_before
    return {"node": "OLD_INITIAL_CATALOG_REJECTS_NEW_NORMAL_WRITE",
        "module": module_document, "initial": initial, "sealed": sealed,
        "catalog_ref": catalog_ref, "catalog_sha256": hashlib.sha256(persisted_bytes).hexdigest(),
        "old_delta_source_sha256": hashlib.sha256(old_delta_source.encode()).hexdigest(),
        "host_delta_source_sha256": hashlib.sha256(host_delta_source.encode()).hexdigest(),
        "rejection": rejected, "immutable_files_before": files_before,
        "immutable_files_after": files_after, "initial_catalog_fixture_override": True,
        "new_child_lifecycle_calls": 0, "normal_projection_or_material_prewrite_expected": 0}


def verify_plain_empty_success_and_retained_refs(directory):
    """One ordinary empty Success; then a pure retained-ref snapshot check.

    The final pure snapshot is not a normal-root Success or an empty normal
    completion. It only exercises the retained-reference branch of Core.
    """
    import hashlib
    import json

    from cpn.rpnh.marking import TeamNetMarking
    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    from cpn.rpnh.registry.normal_root_token_allocation import NORMAL_ROOT_TOKEN_SCHEME
    from normal_child_root_matrix_fixture import new_declared_owner, operation_component, start_parent

    module = {
        "schema_version": "rpnh/module_declaration/v1", "name": "PlainEmptySuccessRetention",
        "components": [operation_component("empty", outputs=()), operation_component("held")],
        "links": [],
        "entry": {"request": {"component": "empty", "port": "request"},
                  "held": {"component": "held", "port": "request"}},
        "exit": {"result": {"component": "held", "port": "result"}},
        "terminal": {"key": "test/normal-child-matrix-never-terminal/v1",
            "source": {"component": "held", "port": "result"}, "operation": "run",
            "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": ["application/operation_component_config/v1",
                             "application/normal_child_root_text/v1"],
        "budgets": {}, "budget_buckets": [{"bucket_id": "work", "budget_scope": "module",
            "finalization_scope": None, "max_attempts": 1}],
    }
    owner = new_declared_owner(directory, label="plain-empty-retained", module_document=module,
        entry_texts={"request": "consume empty input", "held": "preserve original input"}, admit_cap=1)
    execution = start_parent(owner, "empty", "plain-empty-retained:empty")
    claimed = execution.operation.firing.claimed_input_refs
    prior = owner._core.get_version(execution.operation.firing.admission_marking_checkpoint_ref.version_id)
    claimed_versions = {str(ref.version_id) for ref in claimed}
    retained_ref, = [ref for ref in prior.metadata["token_refs"] if ref["version_id"] not in claimed_versions]
    retained = owner._core.get_version(TypedId.parse(retained_ref["version_id"], expected="petri_token_version"))
    retained_before = owner._core.object_store.read_registered(retained)
    outputs = owner.products(execution, outcome_id="complete", products={},
        command_id="plain-empty-retained:products")
    assert outputs.outputs == ()
    successor = owner.succeed(outputs, command_id="plain-empty-retained:success")
    assert len(successor.token_refs) == 1
    assert str(successor.token_refs[0].version_id) == retained_ref["version_id"]
    checkpoint = owner._core.get_version(successor.checkpoint_ref.version_id)
    delta_ref = checkpoint.metadata["settlement_delta_ref"]
    delta = owner._core.get_version(TypedId.parse(delta_ref["version_id"], expected="marking_delta_version"))
    assert "ordinary_token_ref_scheme" not in delta.metadata
    assert delta.metadata["deposited_refs"] == []
    assert {ref["version_id"] for ref in delta.metadata["consumed_refs"]} == claimed_versions
    assert checkpoint.metadata["token_refs"] == [retained_ref]
    retained_after = owner._core.object_store.read_registered(retained)
    assert retained_after == retained_before

    executable, structure, authority = hydrate_module_runtime(owner._core)
    assert authority.checkpoint_ref == successor.checkpoint_ref
    local = TeamNetMarking.from_authority(structure, authority)
    snapshot = local.pure_typed_snapshot(executable,
        ordinary_token_ref_scheme=NORMAL_ROOT_TOKEN_SCHEME,
        allocation_firing_ref=execution.operation.firing.transition_firing_ref)
    assert tuple(state.token_ref for state in snapshot.tokens) == successor.token_refs
    assert snapshot.tokens[0] == successor.tokens[0].state
    assert owner.control.edits.queue == []
    return {"node": "PLAIN_EMPTY_SUCCESS_AND_PURE_RETAINED_REFERENCE",
        "module": module, "legacy_delta": dict(delta.metadata),
        "checkpoint": dict(checkpoint.metadata), "retained_ref": retained_ref,
        "retained_token": json.loads(retained_before),
        "retained_sha256_before": hashlib.sha256(retained_before).hexdigest(),
        "retained_sha256_after": hashlib.sha256(retained_after).hexdigest(),
        "pure_normal_selector_retained_refs": [retained_ref],
        "pure_snapshot_published": False, "normal_root_completed": False,
        "ordinary_success_count": 1, "empty_output_bundle": True}


def verify_old_catalog_and_plain_empty_window(directory, old_delta_source):
    """Exactly two fresh local Registries, one refusal and one plain Success."""
    old_catalog = verify_old_initial_catalog_blocks_new_normal_write(directory, old_delta_source)
    plain_empty = verify_plain_empty_success_and_retained_refs(directory)
    return {
        "node": "NORMAL_TOKEN_OLD_CATALOG_AND_PLAIN_EMPTY",
        "old_catalog": old_catalog,
        "plain_empty": plain_empty,
        "new_registry_count": 2,
        "basic_callback_count": 3,
        "parent_start_count": 2,
        "products_count": 2,
        "succeed_count": 2,
        "success_count": 1,
        "rejection_count": 1,
        "child_lifecycle_count": 0,
        "delivery_count": 0,
        "revision_count": 0,
    }
