"""Dormant P05/P04/R02 acceptance node for two normal children and exact replay.

Static preparation grants no permission to import, collect, or execute this node.
The original owner and registered outputs stay in RAM through public replay.
"""


def test_two_children_exact_replay_and_closed_attach(tmp_path):
    import json

    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.collaboration.worksets import (
        Contribute, ExportResult, WorksetExpectation, WorksetOwner,
        bind_local_workset_source, finish_local_delivery, prepare_local_delivery,
        read_record,
    )
    from cpn.rpnh.file_execution_net import execution_parent
    from cpn.rpnh.registry.execution_net import (
        ExecutionInputArc, ExecutionNetDefinition, ExecutionNetError,
        ExecutionOutputArc, ExecutionTransition,
    )
    from cpn.rpnh.registry.execution_runtime import ExecutionRuntime
    from cpn.rpnh.registry.operations import RegisteredOperationOutputsAuthority
    from normal_child_root_matrix_evidence import (
        collect_closed_normal_root_evidence, expect_rejected_without_commit,
        snapshot_authority,
    )
    from normal_child_root_matrix_fixture import (
        current_workset, new_declared_owner, operation_component, qualified,
        register_products, start_parent,
    )

    prefix = "two-child-replay"
    source_label, target_label = prefix + "-source", prefix + "-target"

    def make_owner(label, names, admit_cap):
        module = {
            "schema_version": "rpnh/module_declaration/v1",
            "name": "NormalChildTwoChildReplay",
            "components": [operation_component(name) for name in names],
            "links": [
                {"source": {"component": left, "port": "result"},
                 "target": {"component": right, "port": "request"}}
                for left, right in zip(names, names[1:])
            ],
            "entry": {"request": {"component": names[0], "port": "request"}},
            "exit": {"result": {"component": names[-1], "port": "result"}},
            "terminal": {
                "key": "test/normal-child-matrix-never-terminal/v1",
                "source": {"component": names[-1], "port": "result"},
                "operation": "run", "outcome": "complete",
                "config": {"run_outcome": "complete"},
            },
            "required_schemas": [
                "application/operation_component_config/v1",
                "application/normal_child_root_text/v1",
            ],
            "budgets": {},
            "budget_buckets": [{
                "bucket_id": "work", "budget_scope": "module",
                "finalization_scope": None, "max_attempts": admit_cap,
            }],
        }
        assert len(names) == admit_cap
        return new_declared_owner(
            tmp_path, label=label, module_document=module,
            entry_texts={"request": "finite two-child exact replay input"},
            admit_cap=admit_cap,
        )

    source = make_owner(source_label, ("produce", "send"), 2)
    target = make_owner(target_label, ("accept", "contribute", "root"), 3)
    assert source.registration.declarations() == target.registration.declarations()
    source_worksets, target_worksets = WorksetOwner(source), WorksetOwner(target)
    request = source_worksets.request(
        requirements_ref=SourceQualifiedVersionRef(source_label, source.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef(
            source_label, source.original_input_ref.as_version_ref()),
        command_id=prefix + ":source:request",
    )
    producing = start_parent(source, "produce", prefix + ":source:produce")
    produced = register_products(source, producing, prefix + ":source:produce")
    source.succeed(
        produced, command_id=prefix + ":source:export",
        workset_action=ExportResult(qualified(source, request), produced.outputs[0].port_id),
    )
    export_row, = source._core.event_store.canonical_object_rows(
        object_type="collaboration_result_export/v1")
    exported = read_record(source._core, json.loads(export_row["metadata_json"])["record_ref"])
    initial = target_worksets.create(
        requirements_ref=SourceQualifiedVersionRef(target_label, target.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef(
            target_label, target.original_input_ref.as_version_ref()),
        generation=0, expected_slots=("answer",),
        command_id=prefix + ":target:workset",
    )
    sealed = target_worksets.change(
        WorksetExpectation.from_record(target._core, initial),
        action="seal", command_id=prefix + ":target:seal",
    )
    delivery = source_worksets.logical_delivery(
        export_ref=qualified(source, exported), target_workset=sealed,
        slot="answer", command_id=prefix + ":source:delivery",
    )
    sending = start_parent(source, "send", prefix + ":source:send")
    bind_local_workset_source(target, source, source_id=source_label)
    attempt = prepare_local_delivery(
        source, sending, logical_delivery_ref=qualified(source, delivery),
        command_id=prefix + ":source:attempt",
    )
    accepting = start_parent(target, "accept", prefix + ":target:accept")
    accepted_products = register_products(
        target, accepting, prefix + ":target:accept", attempt.payload)
    acceptance = target_worksets.accept_delivery(
        attempt, expected=WorksetExpectation.from_record(target._core, sealed),
        slot="answer", decision="new", command_id=prefix + ":target:accepted",
        outputs=accepted_products, output_port=accepted_products.outputs[0].port_id,
    )
    ack = finish_local_delivery(
        source, attempt, outcome="acknowledged", command_id=prefix + ":source:ack",
        target_owner=target, acceptance_ref=qualified(target, acceptance),
    )
    assert ack.outcome == "acknowledged"
    sent_products = register_products(source, sending, prefix + ":source:send")
    source.succeed(sent_products, command_id=prefix + ":source:sent")
    contributing = start_parent(target, "contribute", prefix + ":target:contribute")
    contributed = register_products(target, contributing, prefix + ":target:contribute")
    target.succeed(
        contributed, command_id=prefix + ":target:contributed",
        workset_action=Contribute(
            WorksetExpectation.from_record(
                target._core, current_workset(target, initial["record_ref"])),
            qualified(target, acceptance), contributed.outputs[0].port_id, "answer",
        ),
    )
    root = start_parent(target, "root", prefix + ":target:root")
    root_products = register_products(target, root, prefix + ":target:root")
    assert isinstance(root_products, RegisteredOperationOutputsAuthority)
    assert all(len(outputs.outputs) == 1 for outputs in (
        produced, sent_products, accepted_products, contributed, root_products))
    parent = execution_parent(root.operation.canonical.context)
    runtime = ExecutionRuntime(target._core)
    definition = ExecutionNetDefinition(
        "normal.root.two-child", ("done", "pending"),
        (ExecutionTransition("finish", "pure"),),
        (ExecutionInputArc("pending", "finish", 1),),
        (ExecutionOutputArc("finish", "done", 1),), "pending", 1, ("done",),
    )
    root_evidence = root_products.outputs[0].resource_ref.as_version_ref()

    def settle_child(key):
        child = runtime.instantiate(parent=parent, definition=definition, idempotency_key=key)
        child = runtime.start(
            instance_ref=child.instance_ref, parent=parent,
            checkpoint_ref=child.checkpoint.checkpoint_ref, transition_id="finish",
            idempotency_key=key + ":start", materialization_key=None,
        )
        child = runtime.settle(
            instance_ref=child.instance_ref, parent=parent,
            checkpoint_ref=child.checkpoint.checkpoint_ref,
            firing_ref=child.checkpoint.active_firing_refs[0],
            idempotency_key=key + ":settle", evidence_refs=(root_evidence,),
        )
        assert child.parent == parent
        assert child.checkpoint.map_ready and child.checkpoint.sequence == 2
        assert child.checkpoint.marking == {"done": 1}
        assert child.checkpoint.active_firing_refs == ()
        assert child.checkpoint.evidence_refs == (root_evidence,)
        return child

    first_key, second_key = prefix + ":root:child:one", prefix + ":root:child:two"
    first, second = settle_child(first_key), settle_child(second_key)
    assert first.instance_ref != second.instance_ref
    expected = WorksetExpectation.from_record(
        target._core, current_workset(target, initial["record_ref"]))
    output_port = root_products.outputs[0].port_id
    completion_command = prefix + ":target:normal-root"
    assert target_worksets.owner is target
    completed = target_worksets.complete_normal_children(
        expected=expected, outputs=root_products, output_port=output_port,
        command_id=completion_command,
    )

    # Preserve the original RAM authorities and all four original arguments.
    replay_before = snapshot_authority(
        target, workset_reference=initial["record_ref"], parent_references=(parent,))
    replayed = target_worksets.complete_normal_children(
        expected=expected, outputs=root_products, output_port=output_port,
        command_id=completion_command,
    )
    replay_after = snapshot_authority(
        target, workset_reference=initial["record_ref"], parent_references=(parent,))
    assert replay_after == replay_before
    assert replayed == completed
    assert target_worksets.owner is target and target.control.edits.queue == []

    root_document, workset, seal = completed["root"], completed["workset"], completed["seal"]
    assert root_document["record_ref"]["ref"]["entity_type"] == "collaboration_root_terminal/v2"
    assert root_document["body"]["expected"] == expected.to_dict()
    assert root_document["command_id"] == seal["success_command_id"] == completion_command
    assert workset["body"]["expected_slots"] == ["answer"]
    assert set(workset["body"]["acceptances"]) == set(workset["body"]["contributions"]) == {"answer"}
    assert workset["body"]["required_child_seal_ref"] is None
    assert workset["body"]["terminal_ref"] == root_document["record_ref"]
    assert root_document["body"]["required_child_seal_ref"]["ref"] == seal["execution_child_seal_ref"]
    assert seal["pre_seal_stream_head"] == 2
    assert len(seal["children"]) == 2
    child_by_version = {child["execution_instance_ref"]["version_id"]: child
                        for child in seal["children"]}
    assert set(child_by_version) == {str(first.instance_ref.version_id), str(second.instance_ref.version_id)}
    for child in (first, second):
        entry = child_by_version[str(child.instance_ref.version_id)]
        for field, reference in (
            ("execution_instance_ref", child.instance_ref),
            ("execution_net_definition_ref", child.definition_ref),
            ("execution_checkpoint_ref", child.checkpoint.checkpoint_ref),
        ):
            assert entry[field] == {
                "entity_type": reference.entity_type,
                "logical_id": str(reference.entity_id), "version_id": str(reference.version_id),
            }
        assert entry["evidence_refs"] == [{
            "entity_type": root_evidence.entity_type,
            "logical_id": str(root_evidence.entity_id), "version_id": str(root_evidence.version_id),
        }]

    # These are rows already captured by the required replay snapshot.
    execution_objects = replay_before["execution_objects"]
    instance_rows = [row for row in execution_objects if row["object_type"] == "execution_instance/v1"]
    mapping_rows = [row for row in execution_objects if row["object_type"] == "execution_terminal_mapping/v1"]
    assert len(instance_rows) == len(mapping_rows) == 2
    assert {row["version_id"] for row in instance_rows} == set(child_by_version)
    assert len([row for row in execution_objects if row["object_type"] == "execution_checkpoint/v1"]) == 6
    assert len([row for row in execution_objects if row["object_type"] == "execution_child_seal/v1"]) == 1
    mapping_by_version = {row["version_id"]: row for row in mapping_rows}
    assert set(mapping_by_version) == {
        child["execution_terminal_mapping_ref"]["version_id"] for child in seal["children"]}
    for child in seal["children"]:
        row = mapping_by_version[child["execution_terminal_mapping_ref"]["version_id"]]
        mapping = json.loads(row["metadata_json"])
        assert row["transaction_id"] == seal["success_transaction_id"]
        for field in ("execution_terminal_mapping_ref", "execution_instance_ref",
                      "execution_checkpoint_ref", "evidence_refs"):
            assert mapping[field] == child[field]
        for field in ("parent_invocation_ref", "parent_business_firing_ref",
                      "operation_result_ref", "successor_business_checkpoint_ref"):
            assert mapping[field] == seal[field]
        assert mapping["business_outcome"] == "completed" and mapping["workspace_revision_ref"] is None

    closed_key = prefix + ":root:child:closed-new"
    assert closed_key not in {first_key, second_key}

    def closed_attach():
        return runtime.instantiate(parent=parent, definition=definition, idempotency_key=closed_key)

    rejected = expect_rejected_without_commit(
        target, case_id="R02_CLOSED_PARENT_NEW_CHILD", workset_reference=initial["record_ref"],
        parent_references=(parent,), action=closed_attach, exception_type=ExecutionNetError,
        message="execution parent business firing is not exact and open",
        guard_qualname="ExecutionRuntime._validate_parent",
    )
    assert rejected["before"] == rejected["after"] == replay_after
    original_publication, = replay_before["parent_publications"]
    for snapshot in (rejected["before"], rejected["after"]):
        publication, = snapshot["parent_publications"]
        assert publication == original_publication
        assert publication["state"] == "PUBLISHED"
        assert publication["firing_version_id"] == str(parent.business_firing_ref.version_id)
        assert publication["invocation_version_id"] == str(parent.invocation_ref.version_id)
        assert publication["net_version_id"] == str(parent.business_net_ref.version_id)
        assert publication["admission_checkpoint_version_id"] == str(parent.business_checkpoint_ref.version_id)
        assert publication["published_transaction_id"] == seal["success_transaction_id"]
        retained_instances = [row for row in snapshot["execution_objects"]
                              if row["object_type"] == "execution_instance/v1"]
        assert len(retained_instances) == 2 and retained_instances == instance_rows
        assert snapshot["child_stream_heads"] == replay_before["child_stream_heads"]
        child_head, = snapshot["child_stream_heads"]
        assert child_head["stream_id"] == seal["child_stream_id"]
        assert child_head["sequence"] == seal["pre_seal_stream_head"] + 1 == 3
        assert snapshot["workset_stream_head"] == replay_before["workset_stream_head"]
        workset_head, = snapshot["workset_stream_head"]
        assert workset_head["sequence"] == workset["body"]["sequence"]

    closed_evidence = collect_closed_normal_root_evidence(target, completed)
    membership = closed_evidence["same_success_object_membership"]
    assert len(membership) == 8
    assert sum(item["object_row"]["object_type"] == "execution_terminal_mapping/v1"
               for item in membership) == 2
    assert {item["object_row"]["transaction_id"] for item in membership} == {seal["success_transaction_id"]}
    assert closed_evidence["cold_result"] == completed
    assert closed_evidence["before_cold"] == closed_evidence["after_cold"]
    assert closed_evidence["physical_coverage"] == "source_not_observed"

    # Reuse the original eight captured rows; no additional Registry read.
    completion_member, = [item for item in membership
                          if item["requested_ref"] == root_document["body"]["completion_ref"]]
    checkpoint_member, = [item for item in membership
                          if item["requested_ref"] == root_document["body"]["checkpoint_ref"]]
    completion_metadata = json.loads(completion_member["object_row"]["metadata_json"])
    checkpoint_metadata = json.loads(checkpoint_member["object_row"]["metadata_json"])
    root_delta_ref = completion_metadata["marking_delta_ref"]
    assert root_delta_ref["entity_type"] == "marking_delta/v1"
    assert set(root_delta_ref) == {"entity_type", "logical_id", "version_id"}
    assert checkpoint_metadata["settlement_delta_ref"] == root_delta_ref
    assert completion_metadata["transition_firing_ref"] == seal["parent_business_firing_ref"]
    assert completion_metadata["invocation_ref"] == seal["parent_invocation_ref"]
    assert completion_metadata["operation_result_ref"] == seal["operation_result_ref"]
    assert completion_metadata["successor_checkpoint_ref"] == checkpoint_member["requested_ref"]
    assert checkpoint_metadata["net_instance_ref"] == seal["parent_business_net_ref"]
    assert checkpoint_metadata["transition_firing_refs"] == [seal["parent_business_firing_ref"]]
    assert root_document["body"]["occurrence_ref"] in checkpoint_metadata["token_refs"]

    # These persisted locators are available in the already captured snapshot.
    # They alone do not prove the referenced genesis/catalog bytes or capability.
    locator_keys = (
        "type_catalog_logical_id", "type_catalog_version_id", "native_genesis_ref",
        "native_run_ref", "bootstrap_command_ref",
    )
    locator_rows = [row for row in replay_before["registry_meta"] if row["key"] in locator_keys]
    assert len(locator_rows) == len(locator_keys)
    locators = {row["key"]: row["value"] for row in locator_rows}
    assert set(locators) == set(locator_keys)
    catalog_ref = {
        "entity_type": "registry_type_catalog/v1",
        "logical_id": locators["type_catalog_logical_id"],
        "version_id": locators["type_catalog_version_id"],
    }
    genesis_ref = json.loads(locators["native_genesis_ref"])
    run_ref = json.loads(locators["native_run_ref"])
    bootstrap_ref = json.loads(locators["bootstrap_command_ref"])
    assert genesis_ref["entity_type"] == "native_genesis_manifest/v1"
    assert bootstrap_ref["entity_type"] == "bootstrap_command/v1"
    assert run_ref == seal["run_ref"]
    allocation_rows = replay_before["ordinary_allocation_objects"]
    root_delta_row, = [row for row in allocation_rows if row["version_id"] == root_delta_ref["version_id"]]
    root_delta = json.loads(root_delta_row["metadata_json"])
    assert root_delta["marking_delta_ref"] == root_delta_ref
    assert root_delta_row["transaction_id"] == seal["success_transaction_id"]
    assert root_delta["ordinary_token_ref_scheme"] == "normal_root_firing_scoped/v1"
    legacy_firing_refs = [acceptance["body"]["firing_ref"], {
        "entity_type": contributing.operation.firing.transition_firing_ref.entity_type,
        "logical_id": str(contributing.operation.firing.transition_firing_ref.entity_id),
        "version_id": str(contributing.operation.firing.transition_firing_ref.version_id),
    }]
    legacy_delta_rows = []
    for firing_ref in legacy_firing_refs:
        row, = [row for row in allocation_rows if row["object_type"] == "marking_delta/v1"
            and json.loads(row["metadata_json"])["phase"] == "settlement"
            and json.loads(row["metadata_json"])["transition_firing_refs"] == [firing_ref]]
        assert "ordinary_token_ref_scheme" not in json.loads(row["metadata_json"])
        assert row["transaction_id"] != seal["success_transaction_id"]
        legacy_delta_rows.append(row)
    catalog_row, = replay_before["initial_catalog_objects"]
    assert catalog_row["logical_id"] == catalog_ref["logical_id"]
    assert catalog_row["version_id"] == catalog_ref["version_id"]
    catalog_object_path = "resource_version/" + catalog_ref["version_id"].split(":")[1]
    assert closed_evidence["before_cold"]["immutable_object_sha256"][catalog_object_path] == catalog_row["expected_catalog_payload_sha256"]
    stored_delta_schema = json.loads(catalog_row["marking_delta_schema_entry"]["source"])
    assert stored_delta_schema["properties"]["ordinary_token_ref_scheme"] == {
        "type": "string", "const": "normal_root_firing_scoped/v1"}
    genesis_row, = [row for row in allocation_rows if row["version_id"] == genesis_ref["version_id"]]
    genesis = json.loads(genesis_row["metadata_json"])
    assert genesis["type_catalog_ref"] == catalog_ref and genesis["run_identity_ref"] == run_ref
    token_reference_witness = {
        "root_completion_object": completion_member["object_row"],
        "root_checkpoint_object": checkpoint_member["object_row"],
        "root_delta_ref": root_delta_ref,
        "root_success_token_refs": checkpoint_metadata["token_refs"],
        "persisted_locator_rows": locator_rows,
        "catalog_ref": catalog_ref, "genesis_ref": genesis_ref,
        "run_ref": run_ref, "bootstrap_ref": bootstrap_ref,
        "root_delta_object": root_delta_row,
        "legacy_acceptance_contribution_delta_objects": legacy_delta_rows,
        "initial_catalog_object": catalog_row, "genesis_object": genesis_row,
        "catalog_immutable_sha256": closed_evidence["before_cold"]["immutable_object_sha256"][catalog_object_path],
        "uncaptured": [
            "retained_token_refs_and_bytes_across_root_success",
        ],
        "observation_contract": "existing_captured_rows_only_no_additional_registry_reads",
    }
    return {
        "node": "NCR_P05_P04_R02_TWO_CHILD_REPLAY_CLOSED_ATTACH",
        "scope": {
            "axes": ["P05", "P04", "R02"],
            "source": {"label": source_label, "components": ["produce", "send"], "admit_cap": 2},
            "target": {"label": target_label, "components": ["accept", "contribute", "root"], "admit_cap": 3},
            "slot": "answer", "output_ports": ["result"],
            "child_places": list(definition.places), "child_transition": "finish",
            "child_recovery_mode": "pure", "successful_child_keys": [first_key, second_key],
            "closed_parent_fresh_key": closed_key,
        },
        "setup": {
            "source_request": request, "source_export": exported,
            "logical_delivery": delivery, "target_acceptance": acceptance,
            "physical_delivery_ref": attempt.physical_delivery_ref.to_dict(),
            "ack": {
                "delivery_ref": {
                    "entity_type": ack.delivery_ref.entity_type,
                    "logical_id": str(ack.delivery_ref.entity_id),
                    "version_id": str(ack.delivery_ref.version_id),
                },
                "terminal_event_id": str(ack.terminal_event_id),
                "outcome": ack.outcome,
                "observed_read_event_id": (None if ack.observed_read_event_id is None
                                           else str(ack.observed_read_event_id)),
            },
        },
        "child_states": [{
            "execution_instance_ref": child_by_version[str(child.instance_ref.version_id)]["execution_instance_ref"],
            "execution_checkpoint_ref": child_by_version[str(child.instance_ref.version_id)]["execution_checkpoint_ref"],
            "sequence": child.checkpoint.sequence, "status": child.checkpoint.status,
            "marking": child.checkpoint.marking,
        } for child in (first, second)],
        "closed_root": closed_evidence,
        "token_reference_witness": token_reference_witness,
        "exact_replay": {"before": replay_before, "after": replay_after, "result": replayed},
        "closed_attach": rejected,
    }
