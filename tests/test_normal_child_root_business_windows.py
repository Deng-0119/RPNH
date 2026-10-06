"""Dormant independent legacy/business windows; each needs its own grant.

Only the selected fixed window creates two fresh Registries. No old fixture is
reopened or resumed, no unrelated target is initialized, and source sends are
ordinary Successes after one real export/delivery. Imports stay inside driver.
"""


def test_legacy_completed_state_window(tmp_path):
    return _run_business_window(tmp_path, window="b03-legacy")


def test_empty_evidence_window(tmp_path):
    return _run_business_window(tmp_path, window="b03-empty")


def test_active_firing_window(tmp_path):
    return _run_business_window(tmp_path, window="b03-active")


def test_nonterminal_window(tmp_path):
    return _run_business_window(tmp_path, window="b03-nonterminal")


def test_missing_consumption_window(tmp_path):
    return _run_business_window(tmp_path, window="b03-slots")


def _run_business_window(tmp_path, *, window):
    case_label = window
    assert case_label in ("b03-legacy", "b03-empty", "b03-active", "b03-nonterminal", "b03-slots")
    import hashlib
    import json

    from normal_child_root_matrix_fixture import (
        current_workset, new_declared_owner, operation_component, qualified,
        register_products, start_parent,
    )
    from normal_child_root_matrix_evidence import (
        expect_rejected_without_commit, snapshot_authority,
    )
    from cpn.frontend.worksets import workset_view
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.collaboration.worksets import (
        CompleteWorkset, Contribute, ExportResult, WorksetExpectation,
        WorksetOwner, bind_local_workset_source, finish_local_delivery,
        prepare_local_delivery, read_record,
    )
    from cpn.rpnh.file_execution_net import execution_parent
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.event_store import RegistryConflict
    from cpn.rpnh.registry.execution_net import (
        ExecutionInputArc, ExecutionNetDefinition, ExecutionOutputArc,
        ExecutionTransition,
    )
    from cpn.rpnh.registry.execution_runtime import ExecutionRuntime

    window_contracts = {
        "b03-legacy": {
            "node": "NCR_NODE03_LEGACY_COMPLETED_STATE_WINDOW",
            "matrix_ids": ("P03", "N13"),
            "negative_ids": ("b03:legacy:n13-closed",),
            "basic_lower": 6, "parent_start": 6, "products": 6,
            "owner_succeed": 6, "success_committed": 5, "expected_reject": 1,
            "authority_snapshots": 3, "legacy_empty_runtime_constructors": 5,
        },
        "b03-empty": {
            "node": "NCR_NODE03_EMPTY_EVIDENCE_WINDOW",
            "matrix_ids": ("N02",), "negative_ids": ("b03:empty:n02",),
            "basic_lower": 5, "parent_start": 5, "products": 5,
            "owner_succeed": 5, "success_committed": 4, "expected_reject": 1,
            "authority_snapshots": 2, "legacy_empty_runtime_constructors": 4,
        },
        "b03-active": {
            "node": "NCR_NODE03_ACTIVE_FIRING_WINDOW",
            "matrix_ids": ("N10",), "negative_ids": ("b03:active:n10",),
            "basic_lower": 6, "parent_start": 6, "products": 5,
            "owner_succeed": 5, "success_committed": 4, "expected_reject": 1,
            "authority_snapshots": 2, "legacy_empty_runtime_constructors": 4,
        },
        "b03-nonterminal": {
            "node": "NCR_NODE03_NONTERMINAL_WINDOW",
            "matrix_ids": ("N11",), "negative_ids": ("b03:nonterminal:n11",),
            "basic_lower": 6, "parent_start": 5, "products": 5,
            "owner_succeed": 5, "success_committed": 4, "expected_reject": 1,
            "authority_snapshots": 2, "legacy_empty_runtime_constructors": 4,
        },
        "b03-slots": {
            "node": "NCR_NODE03_MISSING_CONSUMPTION_WINDOW",
            "matrix_ids": ("N12",),
            "negative_ids": ("b03:slots:n12-no-acceptance", "b03:slots:n12-no-contribution",
                             "b03:slots:n12-unconsumed"),
            "basic_lower": 5, "parent_start": 5, "products": 5,
            "owner_succeed": 7, "success_committed": 4, "expected_reject": 3,
            "authority_snapshots": 6, "legacy_empty_runtime_constructors": 4,
        },
    }
    contract = window_contracts[case_label]
    send_component = "send_" + case_label.removeprefix("b03-")
    starts, product_submissions, children, deliveries = [], [], [], []
    rejected = {}

    def ref_payload(reference):
        return {
            "entity_type": reference.entity_type,
            "logical_id": str(reference.entity_id),
            "version_id": str(reference.version_id),
        }

    def endpoint(value):
        component, port = value.split(".")
        return {"component": component, "port": port}

    def terminal(component):
        return {
            "key": "test/normal-child-matrix-never-terminal/v1",
            "source": {"component": component, "port": "result"},
            "operation": "run", "outcome": "complete",
            "config": {"run_outcome": "complete"},
        }

    def module_document(label, names, links, entries, exits, primary, alternatives, cap):
        return {
            "schema_version": "rpnh/module_declaration/v1",
            "name": "NormalChildBusiness" + label.removeprefix("b03-").title(),
            "components": [operation_component(name) for name in names],
            "links": [{"source": endpoint(left), "target": endpoint(right)}
                      for left, right in links],
            "entry": {key: endpoint(value) for key, value in entries.items()},
            "exit": {key: endpoint(value) for key, value in exits.items()},
            "terminal": terminal(primary),
            "terminal_alternatives": [terminal(name) for name in alternatives],
            "required_schemas": ["application/operation_component_config/v1",
                                 "application/normal_child_root_text/v1"],
            "budgets": {},
            "budget_buckets": [{"bucket_id": "work", "budget_scope": "module",
                                "finalization_scope": None, "max_attempts": cap}],
        }

    specifications = (
        ("b03-source", ("produce", send_component),
         (("produce.result", send_component + ".request"),),
         {"request": "produce.request"}, {"result": send_component + ".result"},
         send_component, (), 2),
        ("b03-legacy", ("accept", "contribute", "root", "late"),
         (("accept.result", "contribute.request"), ("contribute.result", "root.request"),
          ("root.result", "late.request")),
         {"request": "accept.request"}, {"legacy": "root.result", "late": "late.result"},
         "root", ("late",), 4),
        ("b03-empty", ("accept", "contribute", "root"),
         (("accept.result", "contribute.request"), ("contribute.result", "root.request")),
         {"request": "accept.request"}, {"result": "root.result"}, "root", (), 3),
        ("b03-active", ("accept", "contribute", "root", "sibling"),
         (("accept.result", "contribute.request"), ("contribute.result", "root.request")),
         {"work": "accept.request", "sibling": "sibling.request"},
         {"result": "root.result", "sibling": "sibling.result"}, "root", (), 4),
        ("b03-nonterminal", ("accept", "contribute", "complete", "finish"),
         (("accept.result", "contribute.request"), ("contribute.result", "complete.request"),
          ("complete.result", "finish.request")),
         {"request": "accept.request"}, {"result": "finish.result"}, "finish", (), 3),
        ("b03-slots", ("accept", "contribute", "root"),
         (("accept.result", "contribute.request"),),
         {"accept": "accept.request", "probe": "root.request"},
         {"result": "root.result", "unconsumed": "contribute.result"}, "root", (), 3),
    )
    owners, modules = {}, {}
    for label, names, links, entries, exits, primary, alternatives, cap in specifications:
        if label not in ("b03-source", case_label):
            continue
        document = module_document(label, names, links, entries, exits, primary, alternatives, cap)
        modules[label] = document
        owners[label] = new_declared_owner(
            tmp_path, label=label, module_document=document,
            entry_texts={key: label + ": distinct entry " + key for key in entries},
            admit_cap=cap,
        )
    source = owners["b03-source"]
    assert all(owner.registration.declarations() == source.registration.declarations()
               for owner in owners.values())
    worksets = {label: WorksetOwner(owner) for label, owner in owners.items()}
    initial, sealed, acceptances, contributions = {}, {}, {}, {}
    for label in (case_label,):
        owner = owners[label]
        initial[label] = worksets[label].create(
            requirements_ref=SourceQualifiedVersionRef(label, owner.identity.task_ref),
            input_binding_ref=SourceQualifiedVersionRef(label, owner.original_input_ref.as_version_ref()),
            generation=0, expected_slots=("answer",), command_id=label + ":workset",
        )
        sealed[label] = worksets[label].change(
            WorksetExpectation.from_record(owner._core, initial[label]),
            action="seal", command_id=label + ":seal",
        )

    def start(label, component):
        execution = start_parent(owners[label], component, label + ":" + component)
        context = execution.operation.canonical.context
        starts.append({
            "owner": label, "component": component,
            "start_event_id": str(execution.start_event_id),
            "invocation_ref": ref_payload(context.invocation_ref),
            "firing_ref": ref_payload(context.own_transition_firing_ref),
            "claimed_input_refs": [ref_payload(ref) for ref in execution.operation.firing.claimed_input_refs],
        })
        return execution

    def products(label, execution, component, payload=None):
        registered = register_products(owners[label], execution, label + ":" + component, payload)
        assert len(registered.outputs) == 1
        product_submissions.append({
            "owner": label, "component": component,
            "firing_ref": ref_payload(execution.operation.firing.transition_firing_ref),
            "output_refs": [ref_payload(item.resource_ref.as_version_ref()) for item in registered.outputs],
        })
        return registered

    request = worksets["b03-source"].request(
        requirements_ref=SourceQualifiedVersionRef("b03-source", source.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef("b03-source", source.original_input_ref.as_version_ref()),
        command_id="b03-source:request",
    )
    producing = start("b03-source", "produce")
    produced = products("b03-source", producing, "produce")
    source.succeed(produced, command_id="b03-source:export",
                   workset_action=ExportResult(qualified(source, request), produced.outputs[0].port_id))
    export_row, = source._core.event_store.canonical_object_rows(object_type="collaboration_result_export/v1")
    exported = read_record(source._core, json.loads(export_row["metadata_json"])["record_ref"])
    assert exported["body"]["output_resource_ref"] == ref_payload(produced.outputs[0].resource_ref.as_version_ref())
    assert exported["body"]["request_ref"] == request["record_ref"]
    source_exports = [exported]
    export_publications = [{"export": exported, "canonical_object_row": dict(export_row)}]

    def accept_slot(label, send_component):
        owner = owners[label]
        selected_export = source_exports[-1]
        delivery = worksets["b03-source"].logical_delivery(
            export_ref=qualified(source, selected_export), target_workset=sealed[label],
            slot="answer", command_id=label + ":logical-delivery",
        )
        sending = start("b03-source", send_component)
        source_input_ref = ref_payload(sending.operation.inputs[0].resource_ref.as_version_ref())
        assert selected_export["body"]["output_resource_ref"] == source_input_ref
        assert delivery["body"]["export_ref"] == selected_export["record_ref"]
        bind_local_workset_source(owner, source, source_id="b03-source")
        attempt = prepare_local_delivery(
            source, sending, logical_delivery_ref=qualified(source, delivery),
            command_id=label + ":delivery-attempt",
        )
        accepting = start(label, "accept")
        accepted_products = products(label, accepting, "accept", attempt.payload)
        acceptance = worksets[label].accept_delivery(
            attempt, expected=WorksetExpectation.from_record(owner._core, sealed[label]),
            slot="answer", decision="new", command_id=label + ":accepted",
            outputs=accepted_products, output_port=accepted_products.outputs[0].port_id,
        )
        ack = finish_local_delivery(
            source, attempt, outcome="acknowledged", command_id=label + ":ack",
            target_owner=owner, acceptance_ref=qualified(owner, acceptance),
        )
        assert ack.outcome == "acknowledged"
        sent_products = products("b03-source", sending, send_component)
        sent_command = "b03-source:" + send_component + ":sent"
        assert send_component == "send_" + case_label.removeprefix("b03-")
        source.succeed(sent_products, command_id=sent_command)
        acceptances[label] = acceptance
        deliveries.append({
            "target": label, "source_send_component": send_component,
            "selected_source_export": selected_export,
            "source_send_actual_input_resource_ref": source_input_ref,
            "logical_delivery": delivery, "physical_delivery_ref": attempt.physical_delivery_ref.to_dict(),
            "acceptance": acceptance, "acknowledged_outcome": ack.outcome,
        })

    def contribute_slot(label):
        owner = owners[label]
        execution = start(label, "contribute")
        assert acceptances[label]["body"]["occurrence_ref"] in [
            ref_payload(ref) for ref in execution.operation.firing.claimed_input_refs
        ]
        registered = products(label, execution, "contribute")
        before = current_workset(owner, initial[label]["record_ref"])
        owner.succeed(
            registered, command_id=label + ":contributed",
            workset_action=Contribute(
                WorksetExpectation.from_record(owner._core, before),
                qualified(owner, acceptances[label]), registered.outputs[0].port_id, "answer",
            ),
        )
        after = current_workset(owner, initial[label]["record_ref"])
        assert after["body"]["state"] == "sealed"
        assert set(after["body"]["acceptances"]) == set(after["body"]["contributions"]) == {"answer"}
        contribution = read_record(owner._core, after["body"]["contributions"]["answer"])
        assert contribution["body"]["acceptance_ref"] == acceptances[label]["record_ref"]
        contributions[label] = contribution

    def settle_child(label, execution, registered, *, empty_evidence=False):
        parent = execution_parent(execution.operation.canonical.context)
        runtime = ExecutionRuntime(owners[label]._core)
        definition = ExecutionNetDefinition(
            "normal.matrix.business.child", ("done", "pending"),
            (ExecutionTransition("finish", "pure"),),
            (ExecutionInputArc("pending", "finish", 1),),
            (ExecutionOutputArc("finish", "done", 1),), "pending", 1, ("done",),
        )
        key = label + ":child"
        child = runtime.instantiate(parent=parent, definition=definition, idempotency_key=key)
        checkpoint_refs = [ref_payload(child.checkpoint.checkpoint_ref)]
        child = runtime.start(
            instance_ref=child.instance_ref, parent=parent, checkpoint_ref=child.checkpoint.checkpoint_ref,
            transition_id="finish", idempotency_key=key + ":start", materialization_key=None,
        )
        checkpoint_refs.append(ref_payload(child.checkpoint.checkpoint_ref))
        evidence = () if empty_evidence else (registered.outputs[0].resource_ref.as_version_ref(),)
        child = runtime.settle(
            instance_ref=child.instance_ref, parent=parent, checkpoint_ref=child.checkpoint.checkpoint_ref,
            firing_ref=child.checkpoint.active_firing_refs[0], idempotency_key=key + ":settle",
            evidence_refs=evidence,
        )
        checkpoint_refs.append(ref_payload(child.checkpoint.checkpoint_ref))
        assert child.checkpoint.map_ready and child.checkpoint.evidence_refs == evidence
        assert len({ref["version_id"] for ref in checkpoint_refs}) == 3
        children.append({
            "owner": label, "parent_firing_ref": ref_payload(parent.business_firing_ref),
            "instance_ref": ref_payload(child.instance_ref), "checkpoint_refs": checkpoint_refs,
            "map_ready": child.checkpoint.map_ready,
            "evidence_refs": [ref_payload(ref) for ref in evidence],
        })
        return parent

    def reject_completion(label, case_id, execution, registered, message, guard, *, other_parents=()):
        owner = owners[label]
        current = current_workset(owner, initial[label]["record_ref"])
        expected = WorksetExpectation.from_record(owner._core, current)
        parent = execution_parent(execution.operation.canonical.context)
        record = expect_rejected_without_commit(
            owner, case_id=case_id, workset_reference=initial[label]["record_ref"],
            parent_references=(parent, *other_parents),
            action=lambda: worksets[label].complete_normal_children(
                expected=expected, outputs=registered, output_port=registered.outputs[0].port_id,
                command_id=case_id,
            ),
            exception_type=RegistryConflict, message=message, guard_qualname=guard,
        )
        assert len(record["after"]["parent_publications"]) == 1 + len(other_parents)
        assert all(row["state"] == "PROVISIONAL" for row in record["after"]["parent_publications"])
        record["expected"] = expected.to_dict()
        record["claimed_input_refs"] = [ref_payload(ref) for ref in execution.operation.firing.claimed_input_refs]
        record["registered_output_refs"] = [ref_payload(item.resource_ref.as_version_ref())
                                           for item in registered.outputs]
        rejected[case_id] = record
        return record

    if case_label == "b03-legacy":
        accept_slot("b03-legacy", "send_legacy")
        contribute_slot("b03-legacy")
        legacy = owners["b03-legacy"]
        legacy_execution = start("b03-legacy", "root")
        legacy_products = products("b03-legacy", legacy_execution, "root")
        legacy_parent = execution_parent(legacy_execution.operation.canonical.context)
        legacy_before = current_workset(legacy, initial["b03-legacy"]["record_ref"])
        legacy.succeed(
            legacy_products, command_id="b03-legacy:p03-root",
            workset_action=CompleteWorkset(
                WorksetExpectation.from_record(legacy._core, legacy_before), legacy_products.outputs[0].port_id,
            ),
        )
        legacy_completed = current_workset(legacy, initial["b03-legacy"]["record_ref"])
        legacy_root = read_record(legacy._core, legacy_completed["body"]["terminal_ref"])
        assert legacy_root["schema_version"] == "registry_v1/collaboration_root_terminal/v1"
        assert legacy_completed["body"]["state"] == "completed"
        assert legacy_completed["body"]["required_child_seal_ref"] is None
        assert legacy_root["body"]["required_child_seal_ref"] is None
        legacy_snapshot = snapshot_authority(
            legacy, workset_reference=initial["b03-legacy"]["record_ref"], parent_references=(legacy_parent,),
        )
        assert legacy_snapshot["execution_objects"] == []
        legacy_publication, = legacy_snapshot["parent_publications"]
        assert legacy_publication["state"] == "PUBLISHED"
        legacy_membership = []
        for reference in (
            legacy_root["record_ref"]["ref"], legacy_completed["record_ref"]["ref"],
            legacy_root["body"]["completion_ref"], legacy_root["body"]["checkpoint_ref"],
        ):
            row = legacy._core.event_store.object_row(reference["version_id"])
            assert row is not None
            captured = dict(row)
            assert captured["object_type"] == reference["entity_type"]
            assert captured["logical_id"] == reference["logical_id"]
            legacy_membership.append({"requested_ref": reference, "object_row": captured})
        assert {item["object_row"]["transaction_id"] for item in legacy_membership} == {
            legacy_publication["published_transaction_id"]
        }

        late_execution = start("b03-legacy", "late")
        late_products = products("b03-legacy", late_execution, "late")
        settle_child("b03-legacy", late_execution, late_products)
        closed = reject_completion(
            "b03-legacy", "b03:legacy:n13-closed", late_execution, late_products,
            "completed Workset cannot be reopened by a late result", "validate_workset_publication",
        )
        assert closed["expected"] == WorksetExpectation.from_record(legacy._core, legacy_completed).to_dict()

    elif case_label == "b03-empty":
        accept_slot("b03-empty", "send_empty")
        contribute_slot("b03-empty")
        empty_execution = start("b03-empty", "root")
        empty_products = products("b03-empty", empty_execution, "root")
        settle_child("b03-empty", empty_execution, empty_products, empty_evidence=True)
        reject_completion(
            "b03-empty", "b03:empty:n02", empty_execution, empty_products,
            "normal child closure: every child requires map_ready and nonempty evidence", "child_snapshot",
        )

    elif case_label == "b03-active":
        accept_slot("b03-active", "send_active")
        contribute_slot("b03-active")
        active_execution = start("b03-active", "root")
        active_products = products("b03-active", active_execution, "root")
        settle_child("b03-active", active_execution, active_products)
        sibling = start("b03-active", "sibling")
        assert contributions["b03-active"]["body"]["occurrence_ref"] in [
            ref_payload(ref) for ref in active_execution.operation.firing.claimed_input_refs
        ]
        assert not set(active_execution.operation.firing.claimed_input_refs).intersection(
            sibling.operation.firing.claimed_input_refs)
        reject_completion(
            "b03-active", "b03:active:n10", active_execution, active_products,
            "Workset root completion cannot bypass another active ordinary firing", "_validate_business",
            other_parents=(execution_parent(sibling.operation.canonical.context),),
        )

    elif case_label == "b03-nonterminal":
        accept_slot("b03-nonterminal", "send_nonterminal")
        contribute_slot("b03-nonterminal")
        nonterminal_execution = start("b03-nonterminal", "complete")
        nonterminal_products = products("b03-nonterminal", nonterminal_execution, "complete")
        settle_child("b03-nonterminal", nonterminal_execution, nonterminal_products)
        assert contributions["b03-nonterminal"]["body"]["occurrence_ref"] in [
            ref_payload(ref) for ref in nonterminal_execution.operation.firing.claimed_input_refs
        ]
        reject_completion(
            "b03-nonterminal", "b03:nonterminal:n11", nonterminal_execution, nonterminal_products,
            "Workset root Success is not the actual registered Module terminal", "_validate_terminal_binding",
        )

    elif case_label == "b03-slots":
        slots_execution = start("b03-slots", "root")
        slots_products = products("b03-slots", slots_execution, "root")
        settle_child("b03-slots", slots_execution, slots_products)
        incomplete_message = "root completion requires a sealed complete Workset and independent child closure"
        no_acceptance = reject_completion(
            "b03-slots", "b03:slots:n12-no-acceptance", slots_execution, slots_products,
            incomplete_message, "_validate_business",
        )
        no_acceptance_body = json.loads(no_acceptance["before"]["workset_latest_object"]["metadata_json"])["body"]
        assert no_acceptance_body["state"] == "sealed"
        assert no_acceptance_body["acceptances"] == no_acceptance_body["contributions"] == {}
        # Observe the actual immutable orphan from the refused root proposal.
        # It must survive the later legacy acceptance without occupying its ID.
        object_root = owners[case_label]._core.object_store.root
        orphan_tokens = []
        for path in sorted((object_root / "petri_token_version").iterdir()):
            raw = path.read_bytes()
            document = json.loads(raw)
            if document["place"] == "root.result" and document["producer"] == "root.run":
                orphan_tokens.append((path, raw, document))
        orphan_path, orphan_raw, orphan_token = orphan_tokens[0]
        assert len(orphan_tokens) == 1
        root_firing_ref = ref_payload(slots_execution.operation.firing.transition_firing_ref)
        initial_root_deltas = []
        for path in sorted((object_root / "marking_delta_version").iterdir()):
            raw = path.read_bytes()
            document = json.loads(raw)
            if document["phase"] == "settlement" and document["transition_firing_refs"] == [root_firing_ref]:
                initial_root_deltas.append({"relative_path": str(path.relative_to(object_root)),
                    "sha256": hashlib.sha256(raw).hexdigest(), "document": document})
        initial_root_delta, = initial_root_deltas
        assert initial_root_delta["document"]["ordinary_token_ref_scheme"] == "normal_root_firing_scoped/v1"
        assert orphan_token["petri_token_ref"] in initial_root_delta["document"]["deposited_refs"]
        accept_slot("b03-slots", "send_slots")
        acceptance_token_ref = acceptances[case_label]["body"]["occurrence_ref"]
        acceptance_token_path = object_root / "petri_token_version" / acceptance_token_ref["version_id"].split(":")[1]
        acceptance_token_raw = acceptance_token_path.read_bytes()
        acceptance_token = json.loads(acceptance_token_raw)
        assert acceptance_token["petri_token_ref"] == acceptance_token_ref
        assert acceptance_token["token_id"] == orphan_token["token_id"]
        assert acceptance_token_ref != orphan_token["petri_token_ref"]
        assert orphan_path.read_bytes() == orphan_raw
        acceptance_firing_ref = acceptances[case_label]["body"]["firing_ref"]
        acceptance_deltas = []
        for path in sorted((object_root / "marking_delta_version").iterdir()):
            raw = path.read_bytes()
            document = json.loads(raw)
            if document["phase"] == "settlement" and document["transition_firing_refs"] == [acceptance_firing_ref]:
                acceptance_deltas.append({"relative_path": str(path.relative_to(object_root)),
                    "sha256": hashlib.sha256(raw).hexdigest(), "document": document})
        acceptance_delta, = acceptance_deltas
        assert "ordinary_token_ref_scheme" not in acceptance_delta["document"]
        no_contribution = reject_completion(
            "b03-slots", "b03:slots:n12-no-contribution", slots_execution, slots_products,
            incomplete_message, "_validate_business",
        )
        no_contribution_body = json.loads(no_contribution["before"]["workset_latest_object"]["metadata_json"])["body"]
        assert set(no_contribution_body["acceptances"]) == {"answer"}
        assert no_contribution_body["contributions"] == {}
        contribute_slot("b03-slots")
        assert contributions["b03-slots"]["body"]["occurrence_ref"] not in [
            ref_payload(ref) for ref in slots_execution.operation.firing.claimed_input_refs
        ]
        unconsumed = reject_completion(
            "b03-slots", "b03:slots:n12-unconsumed", slots_execution, slots_products,
            "root terminal must actually consume every expected contribution", "_validate_business",
        )
        unconsumed_body = json.loads(unconsumed["before"]["workset_latest_object"]["metadata_json"])["body"]
        assert set(unconsumed_body["acceptances"]) == set(unconsumed_body["contributions"]) == {"answer"}
        assert orphan_path.read_bytes() == orphan_raw
        token_allocation_observation = {
            "orphan_root_token": orphan_token,
            "orphan_relative_path": str(orphan_path.relative_to(object_root)),
            "orphan_sha256_before_acceptance": hashlib.sha256(orphan_raw).hexdigest(),
            "orphan_sha256_after_all_rejections": hashlib.sha256(orphan_path.read_bytes()).hexdigest(),
            "root_delta": initial_root_delta, "legacy_acceptance_delta": acceptance_delta,
            "legacy_acceptance_token": acceptance_token,
            "legacy_acceptance_token_sha256": hashlib.sha256(acceptance_token_raw).hexdigest(),
            "same_ordinal_distinct_exact_refs": True,
            "immutable_orphan_preserved": True,
        }

    assert set(owners) == {"b03-source", case_label}
    assert sum(len(module["components"]) for module in modules.values()) == contract["basic_lower"]
    assert len(starts) == contract["parent_start"]
    assert len(product_submissions) == contract["products"]
    assert len(children) == 1 and len(children[0]["checkpoint_refs"]) == 3
    assert bool(children[0]["evidence_refs"]) == (case_label != "b03-empty")
    assert len(deliveries) == len(source_exports) == len(export_publications) == 1
    assert len(rejected) == contract["expected_reject"]
    assert tuple(rejected) == contract["negative_ids"]
    assert deliveries[0]["target"] == case_label
    assert deliveries[0]["selected_source_export"]["record_ref"] == exported["record_ref"]
    scope = {
        "window_id": contract["node"], "case_label": case_label,
        "window_output_directory": str(tmp_path),
        "source_task_ref": ref_payload(source.identity.task_ref),
        "target_task_ref": ref_payload(owners[case_label].identity.task_ref),
        "matrix_ids_exercised": list(contract["matrix_ids"]),
        "negative_case_ids_exercised": list(contract["negative_ids"]),
        "source_topology": "one fresh source: produce ExportResult then one ordinary send Success",
        "chained_source_reexports_exercised": False,
        "unselected_windows_executed": False,
        "cold_scope": "legacy window final observation" if case_label == "b03-legacy" else "not invoked",
    }
    expected_counts = {
        "new_registries": 2, "source_identity_bind": 2,
        "basic_lower": contract["basic_lower"],
        "parent_admit": contract["parent_start"], "parent_start": contract["parent_start"],
        "products": contract["products"], "owner_succeed": contract["owner_succeed"],
        "success_committed": contract["success_committed"], "expected_reject": contract["expected_reject"],
        "normal_completion_facade": contract["expected_reject"],
        "child_instantiate": 1, "child_start": 1, "child_settle": 1,
        "child_lifecycle_return_hydrates": 3,
        "legacy_empty_runtime_constructors": contract["legacy_empty_runtime_constructors"],
        "authority_snapshots": contract["authority_snapshots"],
        "parent_receipt_ack": 1, "petri_input_ack": contract["parent_start"],
        "source_request": 1, "source_exports": 1, "source_export_canonical_queries": 1,
        "source_export_record_reads": 1, "source_send_export_success": 0,
        "source_send_plain_success": 1, "logical_delivery": 1,
        "accept_delivery": 1, "contribute_success": 1, "access_resource": 0,
        "cold_readonly_core": int(case_label == "b03-legacy"),
        "cold_record_reads": 2 if case_label == "b03-legacy" else 0,
        "cold_workset_view_v1": int(case_label == "b03-legacy"),
    }

    if case_label == "b03-legacy":
        # This selected legacy window's mutations and negative case have ended.
        # Cold v1 reads are its final phase; other windows do not invoke them.
        object_root = legacy._core.object_store.root
        before_cold = {
            "committed_ordinal": legacy._core.event_store.max_ordinal(),
            "writer_epoch": legacy._core.event_store.writer_epoch,
            "immutable_object_sha256": {
                str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in object_root.rglob("*") if path.is_file()
            },
        }
        readonly = _RegistryCore(legacy._core.run_dir, create=False, read_only=True, catalog=legacy._core.catalog)
        cold_root = read_record(readonly, legacy_root["record_ref"])
        cold_workset = read_record(readonly, legacy_completed["record_ref"])
        cold_view = workset_view(readonly)
        assert cold_root == legacy_root and cold_workset == legacy_completed
        assert cold_view["schema_version"] == "rpnh/workset_view/v1"
        cold_row, = [row for row in cold_view["current"] if row["workset_ref"] == legacy_completed["record_ref"]]
        assert cold_row["state"] == "completed"
        assert cold_row["root_terminal_ref"] == legacy_root["record_ref"]
        assert cold_row["required_child_seal_ref"] is None and cold_row["root_terminal_evidence_ref"] is None
        assert "root_child_closure" not in cold_row
        assert cold_row["physical_coverage"] == "source_not_observed"
        assert cold_row["acceptance_count"] == cold_row["contribution_count"] == 1
        after_cold = {
            "committed_ordinal": readonly.event_store.max_ordinal(),
            "writer_epoch": readonly.event_store.writer_epoch,
            "immutable_object_sha256": {
                str(path.relative_to(object_root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in object_root.rglob("*") if path.is_file()
            },
        }
        assert after_cold == before_cold
    result = {
        "node": contract["node"],
        "schema_version": "normal-child-root-business-window-evidence/v1",
        "scope": scope, "modules": modules, "source_request": request,
        "source_export": exported, "source_exports": source_exports,
        "source_export_publications": export_publications,
        "workset_initial": initial, "workset_sealed": sealed,
        "starts": starts, "product_submissions": product_submissions,
        "children": children, "deliveries": deliveries, "contributions": contributions,
        "negative_cases": rejected, "expected_entrypoint_counts": expected_counts,
        "observation_contract": ("required_authority_reads_plus_bounded_target_immutable_token_delta_reads"
            if case_label == "b03-slots" else "captured_from_required_assertion_reads_without_extra_reads"),
        "prewritten_file_rollback_claimed": False,
        "run_terminal_evidence_claimed": False,
    }
    if case_label == "b03-legacy":
        result["P03"] = {
            "root": legacy_root, "workset": legacy_completed,
            "zero_child_success_snapshot": legacy_snapshot,
            "same_success_object_membership": legacy_membership,
            "before_cold": before_cold, "cold_root": cold_root,
            "cold_workset": cold_workset, "python_view": cold_view, "after_cold": after_cold,
        }
    if case_label == "b03-slots":
        result["normal_root_token_allocation"] = token_allocation_observation
    return result
