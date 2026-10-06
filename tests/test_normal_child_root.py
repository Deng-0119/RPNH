"""One dormant, explicitly authorized normal-child owner lifecycle node.

No module-level product imports or fixture construction. This file is not an
authorization to collect or execute tests. The standalone runner selects only
the function below after the source/runner/node pins receive runtime approval.
"""


def test_one_normal_child_root_same_success(tmp_path):
    import hashlib
    import json

    from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.run import OwnerInput, start_run
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
    from cpn.rpnh.registry.execution_runtime import ExecutionRuntime
    from cpn.rpnh.registry.execution_net import (
        ExecutionNetDefinition, ExecutionTransition, ExecutionInputArc, ExecutionOutputArc,
    )
    from cpn.rpnh.file_execution_net import execution_parent
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.collaboration.schema_catalog import normal_child_root_schema_data
    from cpn.rpnh.collaboration.worksets import (
        WorksetOwner, WorksetExpectation, ExportResult, Contribute,
        bind_local_workset_source, prepare_local_delivery, finish_local_delivery,
        read_record, WORKSET,
    )
    from cpn.rpnh.collaboration.root_terminals import read_root_terminal
    from cpn.frontend.worksets import workset_view

    text_schema = "application/normal_child_root_text/v1"
    executor_key = "test/normal-child-root-never-dispatch/v1"
    terminal_key = "test/normal-child-root-never-terminal/v1"

    def never_dispatch(*args, **kwargs):
        raise AssertionError("this node must not dispatch executor or terminal tool callbacks")

    def owner(label, names):
        schemas, types, paths = normal_child_root_schema_data()
        catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
        registration = Registration()
        register_basic_components(registration)
        registration.register_schema(text_schema, {"$id": text_schema,
            "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
        registration.register_executor(executor_key, never_dispatch,
            identity={"implementation_id": "normal-child-root-static-products", "revision": "v1"},
            contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                "config_schema": CONFIG_SCHEMA_ID})
        registration.register_tool(terminal_key, never_dispatch,
            identity={"implementation_id": "normal-child-root-terminal-declaration", "revision": "v1"},
            contracts={"binding_protocol": "rpnh/module_terminal/v1"})
        bucket = {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None}
        components = [{"name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": text_schema},
                      {"name": "result", "direction": "output", "schema": text_schema}],
            "operations": [{"name": "run", "executor": executor_key, "inputs": ["request"], "outputs": ["result"],
                "request_port": None, "tools": [], "config": {}, "budget_binding": bucket,
                "outcomes": [{"name": "complete", "products": [{"port": "result"}], "effects": []}]}]}
            for name in names]
        module = ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1", "name": "NormalChildRoot",
            "components": components,
            "links": [{"source": {"component": left, "port": "result"}, "target": {"component": right, "port": "request"}}
                for left, right in zip(names, names[1:])],
            "entry": {"request": {"component": names[0], "port": "request"}},
            "exit": {"result": {"component": names[-1], "port": "result"}},
            "terminal": {"key": terminal_key, "source": {"component": names[-1], "port": "result"},
                "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
            "required_schemas": [CONFIG_SCHEMA_ID, text_schema], "budgets": {},
            "budget_buckets": [{**bucket, "max_attempts": 8}]})
        value = OwnerInput(text_schema, canonical_json("explicit normal child fixture input"), "Finite local input")
        result = start_run(module, registration, run_dir=tmp_path / label, task_input=value,
            entry_inputs={"request": value},
            budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
                ("rpnh/module_declaration/v1",), 8, 0, 8, 0),
            model_condition="offline-normal-child-no-model", owner_statement="Finite local Registry normal-child fixture",
            command_id="fixture:" + label, catalog=catalog, host_execution_bindings=None, configuration_sources=None)
        result.schema_gateway.bind_source_identity(source_id=label, command_id="source:" + label)
        return result

    def start(host, name, key):
        assert host.host_execution_bindings is None and host.control.edits.queue == []
        assert getattr(host, "revision_candidate_publisher", None) is None
        admitted = host.admit(name + ".run", logical_tau=0, command_id=key + ":admit", prepare_admission=None)
        assert admitted is not None
        assert host._core.get_version(admitted.admission.context.operation_binding_ref.version_id).metadata.get("workspace_binding_ref") is None
        execution = host.start(admitted, command_id=key + ":start")
        assert len(execution.operation.inputs) == 1
        return execution

    def products(host, execution, key, payload=None):
        assert host.control.edits.queue == [] and getattr(host, "revision_candidate_publisher", None) is None
        assert host._core.get_version(execution.operation.canonical.context.operation_binding_ref.version_id).metadata.get("workspace_binding_ref") is None
        _kernel, repository = host.operation_repository()
        _compiled, declared = repository.registered_compiled_operation(execution.operation)
        assert all(not outcome.effects for outcome in declared.declaration.outcomes)
        if payload is None:
            payload = canonical_json("derived:" + hashlib.sha256(execution.operation.inputs[0].artifact.payload).hexdigest())
        name = execution.operation.firing.transition_id.rsplit(".", 1)[0] + ".result"
        return host.products(execution, outcome_id="complete", products={name: (payload,)}, command_id=key + ":products")

    def qualified(host, document):
        return SourceQualifiedVersionRef.from_dict(document["record_ref"], catalog=host._core.catalog)

    def current_workset(host):
        rows = host._core.event_store.canonical_object_rows(object_type=WORKSET)
        docs = [read_record(host._core, json.loads(row["metadata_json"])["record_ref"]) for row in rows]
        return max(docs, key=lambda value: value["body"]["sequence"])

    source = owner("normal-child-source", ("produce", "send"))
    target = owner("normal-child-target", ("accept", "contribute", "root"))
    assert source.registration.declarations() == target.registration.declarations()
    source_worksets, target_worksets = WorksetOwner(source), WorksetOwner(target)
    request = source_worksets.request(
        requirements_ref=SourceQualifiedVersionRef("normal-child-source", source.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef("normal-child-source", source.original_input_ref.as_version_ref()),
        command_id="source:request")
    producing = start(source, "produce", "source:produce")
    produced = products(source, producing, "source:produce")
    source.succeed(produced, command_id="source:export", workset_action=ExportResult(qualified(source, request), produced.outputs[0].port_id))
    export_row, = source._core.event_store.canonical_object_rows(object_type="collaboration_result_export/v1")
    exported = read_record(source._core, json.loads(export_row["metadata_json"])["record_ref"])
    initial = target_worksets.create(
        requirements_ref=SourceQualifiedVersionRef("normal-child-target", target.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef("normal-child-target", target.original_input_ref.as_version_ref()),
        generation=0, expected_slots=("answer",), command_id="target:workset")
    sealed = target_worksets.change(WorksetExpectation.from_record(target._core, initial), action="seal", command_id="target:seal")
    delivery = source_worksets.logical_delivery(export_ref=qualified(source, exported), target_workset=sealed,
        slot="answer", command_id="source:delivery")
    sending = start(source, "send", "source:send")
    bind_local_workset_source(target, source, source_id="normal-child-source")
    attempt = prepare_local_delivery(source, sending, logical_delivery_ref=qualified(source, delivery), command_id="source:attempt")
    accepting = start(target, "accept", "target:accept")
    accepted_products = products(target, accepting, "target:accept", attempt.payload)
    acceptance = target_worksets.accept_delivery(attempt, expected=WorksetExpectation.from_record(target._core, sealed),
        slot="answer", decision="new", command_id="target:accepted", outputs=accepted_products,
        output_port=accepted_products.outputs[0].port_id)
    ack = finish_local_delivery(source, attempt, outcome="acknowledged", command_id="source:ack",
        target_owner=target, acceptance_ref=qualified(target, acceptance))
    assert ack.outcome == "acknowledged"
    source.succeed(products(source, sending, "source:send"), command_id="source:sent")
    contributing = start(target, "contribute", "target:contribute")
    contributed = products(target, contributing, "target:contribute")
    target.succeed(contributed, command_id="target:contributed", workset_action=Contribute(
        WorksetExpectation.from_record(target._core, current_workset(target)), qualified(target, acceptance),
        contributed.outputs[0].port_id, "answer"))
    root = start(target, "root", "target:root")
    parent = execution_parent(root.operation.canonical.context)
    runtime = ExecutionRuntime(target._core)
    definition = ExecutionNetDefinition("normal.root.child", ("done", "pending"),
        (ExecutionTransition("finish", "pure"),), (ExecutionInputArc("pending", "finish", 1),),
        (ExecutionOutputArc("finish", "done", 1),), "pending", 1, ("done",))
    child = runtime.instantiate(parent=parent, definition=definition, idempotency_key="root:child")
    child = runtime.start(instance_ref=child.instance_ref, parent=parent, checkpoint_ref=child.checkpoint.checkpoint_ref,
        transition_id="finish", idempotency_key="root:child:start", materialization_key=None)
    root_products = products(target, root, "target:root")
    child = runtime.settle(instance_ref=child.instance_ref, parent=parent, checkpoint_ref=child.checkpoint.checkpoint_ref,
        firing_ref=child.checkpoint.active_firing_refs[0], idempotency_key="root:child:settle",
        evidence_refs=(root_products.outputs[0].resource_ref.as_version_ref(),))
    assert child.checkpoint.map_ready and child.checkpoint.evidence_refs
    completed = target_worksets.complete_normal_children(
        expected=WorksetExpectation.from_record(target._core, current_workset(target)), outputs=root_products,
        output_port=root_products.outputs[0].port_id, command_id="target:normal-root")
    root_doc, workset, seal = completed["root"], completed["workset"], completed["seal"]
    assert workset["body"]["required_child_seal_ref"] is None
    assert workset["body"]["terminal_ref"] == root_doc["record_ref"]
    assert len(seal["children"]) == 1
    refs = [root_doc["record_ref"]["ref"], workset["record_ref"]["ref"], seal["execution_child_seal_ref"],
        seal["children"][0]["execution_terminal_mapping_ref"], root_doc["body"]["completion_ref"],
        root_doc["body"]["checkpoint_ref"], seal["operation_result_ref"]]
    transactions = {target._core.event_store.object_row(ref["version_id"])["transaction_id"] for ref in refs}
    assert transactions == {seal["success_transaction_id"]}
    assert len(target._core.event_store.object_rows_by_type("execution_instance/v1")) == 1
    assert len(target._core.event_store.object_rows_by_type("execution_checkpoint/v1")) == 3
    assert len(target._core.event_store.object_rows_by_type("execution_child_seal/v1")) == 1
    assert len(source._core.event_store.list_events_by_type(("operation_execution_started/v1",))) == 2
    assert len(target._core.event_store.list_events_by_type(("operation_execution_started/v1",))) == 3
    ordinal, epoch = target._core.event_store.max_ordinal(), target._core.event_store.writer_epoch
    object_bytes = {str(path.relative_to(target._core.object_store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target._core.object_store.root.rglob("*") if path.is_file()}
    readonly = _RegistryCore(target._core.run_dir, create=False, read_only=True, catalog=target._core.catalog)
    cold = read_root_terminal(readonly, root_doc["record_ref"])
    view = workset_view(readonly)
    assert cold["root"] == root_doc and cold["seal"] == seal
    assert view["schema_version"] == "rpnh/workset_view/v2"
    row, = view["current"]
    assert row["root_child_closure"]["seal_ref"] == root_doc["body"]["required_child_seal_ref"]
    assert row["required_child_seal_ref"] is None and row["root_terminal_evidence_ref"] is None
    assert row["physical_coverage"] == "source_not_observed"
    assert row["acceptance_count"] == row["contribution_count"] == 1
    assert readonly.event_store.max_ordinal() == ordinal and readonly.event_store.writer_epoch == epoch
    assert object_bytes == {str(path.relative_to(target._core.object_store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target._core.object_store.root.rglob("*") if path.is_file()}
    return {"node": "NCR_NODE01_SINGLE_CHILD_SINGLE_SLOT", "root": root_doc, "workset": workset, "seal": seal,
        "root_success_transaction_id": seal["success_transaction_id"], "read_cut": ordinal,
        "view_schema": view["schema_version"], "physical_coverage": row["physical_coverage"]}
