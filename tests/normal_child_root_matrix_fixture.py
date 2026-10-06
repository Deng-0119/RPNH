"""Dormant finite fixture helpers for separately authorized normal-child nodes.

All product imports and construction remain inside explicitly invoked helpers.
The prepared owner and products stay in RAM for exact public replay checks.
"""


def never_dispatch(*_args, **_kwargs):
    raise AssertionError("matrix fixtures cannot dispatch executor or terminal tools")


def new_declared_owner(directory, *, label, module_document, entry_texts, admit_cap):
    """Use an explicit pinned Module and a distinct resource for each entry."""
    from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
    from cpn.rpnh.collaboration.schema_catalog import normal_child_root_schema_data
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
    from cpn.rpnh.run import OwnerInput, start_run

    text_schema = "application/normal_child_root_text/v1"
    executor = "test/normal-child-matrix-never-dispatch/v1"
    terminal = "test/normal-child-matrix-never-terminal/v1"
    schemas, types, paths = normal_child_root_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(text_schema, {
        "$id": text_schema, "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"
    })
    registration.register_executor(executor, never_dispatch,
        identity={"implementation_id": "normal-child-matrix-static-products", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None,
                   "config_schema": CONFIG_SCHEMA_ID})
    registration.register_tool(terminal, never_dispatch,
        identity={"implementation_id": "normal-child-matrix-terminal-declaration", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    module = ModuleDeclaration.from_dict(module_document)
    assert set(entry_texts) == set(module.entry)
    assert type(admit_cap) is int and admit_cap > 0
    assert module.to_dict()["budget_buckets"] == [{"bucket_id": "work", "budget_scope": "module",
        "finalization_scope": None, "max_attempts": admit_cap}]
    task_input = OwnerInput(text_schema, canonical_json("finite normal child matrix task input"), "Finite matrix input")
    entry_inputs = {name: OwnerInput(text_schema, canonical_json(value), "Finite " + name)
                    for name, value in entry_texts.items()}
    owner = start_run(module, registration, run_dir=directory / label, task_input=task_input,
        entry_inputs=entry_inputs,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), admit_cap, 0, admit_cap, 0),
        model_condition="offline-normal-child-no-model", owner_statement="Finite local matrix fixture",
        command_id="fixture:" + label, catalog=catalog,
        host_execution_bindings=None, configuration_sources=None)
    owner.schema_gateway.bind_source_identity(source_id=label, command_id="source:" + label)
    return owner


def operation_component(name, *, outputs=("result",)):
    """Build only the ordinary no-effect component used by these fixed fixtures."""
    text_schema = "application/normal_child_root_text/v1"
    config_schema = "application/operation_component_config/v1"
    return {
        "name": name, "key": "operation", "config_schema": config_schema, "config": {},
        "ports": [{"name": "request", "direction": "input", "schema": text_schema},
                  *[{"name": port, "direction": "output", "schema": text_schema} for port in outputs]],
        "operations": [{"name": "run", "executor": "test/normal-child-matrix-never-dispatch/v1",
            "inputs": ["request"], "outputs": list(outputs), "request_port": None,
            "tools": [], "config": {},
            "budget_binding": {"bucket_id": "work", "budget_scope": "module", "finalization_scope": None},
            "outcomes": [{"name": "complete", "products": [{"port": port} for port in outputs], "effects": []}]}],
    }


def new_linear_owner(directory, *, label, names):
    module = {
        "schema_version": "rpnh/module_declaration/v1", "name": "NormalChildMatrix",
        "components": [operation_component(name) for name in names],
        "links": [{"source": {"component": left, "port": "result"},
                   "target": {"component": right, "port": "request"}}
                  for left, right in zip(names, names[1:])],
        "entry": {"request": {"component": names[0], "port": "request"}},
        "exit": {"result": {"component": names[-1], "port": "result"}},
        "terminal": {"key": "test/normal-child-matrix-never-terminal/v1",
            "source": {"component": names[-1], "port": "result"},
            "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": ["application/operation_component_config/v1", "application/normal_child_root_text/v1"],
        "budgets": {},
        "budget_buckets": [{"bucket_id": "work", "budget_scope": "module", "finalization_scope": None, "max_attempts": 8}],
    }
    return new_declared_owner(directory, label=label, module_document=module,
        entry_texts={"request": "finite normal child matrix input"}, admit_cap=8)


def start_parent(owner, component, key):
    assert owner.host_execution_bindings is None and owner.control.edits.queue == []
    assert getattr(owner, "revision_candidate_publisher", None) is None
    admitted = owner.admit(component + ".run", logical_tau=0,
                           command_id=key + ":admit", prepare_admission=None)
    assert admitted is not None
    assert owner._core.get_version(
        admitted.admission.context.operation_binding_ref.version_id
    ).metadata.get("workspace_binding_ref") is None
    execution = owner.start(admitted, command_id=key + ":start")
    assert len(execution.operation.inputs) == 1
    return execution


def register_products(owner, execution, key, payload=None):
    import hashlib
    from cpn.rpnh.registry.schema_catalog import canonical_json

    assert owner.control.edits.queue == []
    assert getattr(owner, "revision_candidate_publisher", None) is None
    assert owner._core.get_version(
        execution.operation.canonical.context.operation_binding_ref.version_id
    ).metadata.get("workspace_binding_ref") is None
    _kernel, repository = owner.operation_repository()
    _compiled, declared = repository.registered_compiled_operation(execution.operation)
    assert all(not outcome.effects for outcome in declared.declaration.outcomes)
    if payload is None:
        payload = canonical_json("derived:" + hashlib.sha256(
            execution.operation.inputs[0].artifact.payload).hexdigest())
    port = execution.operation.firing.transition_id.rsplit(".", 1)[0] + ".result"
    return owner.products(execution, outcome_id="complete", products={port: (payload,)},
                          command_id=key + ":products")


def qualified(owner, document):
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    return SourceQualifiedVersionRef.from_dict(document["record_ref"], catalog=owner._core.catalog)


def current_workset(owner, workset_reference):
    import json
    from cpn.rpnh.collaboration.worksets import WORKSET, read_record

    rows = owner._core.event_store.canonical_object_rows(object_type=WORKSET)
    documents = [read_record(owner._core, json.loads(row["metadata_json"])["record_ref"])
                 for row in rows if row["logical_id"] == workset_reference["ref"]["logical_id"]]
    assert documents
    return max(documents, key=lambda document: document["body"]["sequence"])


def prepare_two_owner_root(directory, *, prefix):
    """Construct one actual accepted slot and leave its root Start open.

    The caller must register root products and run its explicitly authorized
    children. No normal completion, replay, rejection or terminal is implicit.
    """
    import json
    from types import SimpleNamespace
    from cpn.rpnh.collaboration.references import SourceQualifiedVersionRef
    from cpn.rpnh.collaboration.worksets import (
        Contribute, ExportResult, WorksetExpectation, WorksetOwner,
        bind_local_workset_source, finish_local_delivery, prepare_local_delivery, read_record,
    )
    from cpn.rpnh.file_execution_net import execution_parent

    source_label, target_label = prefix + "-source", prefix + "-target"
    source = new_linear_owner(directory, label=source_label, names=("produce", "send"))
    target = new_linear_owner(directory, label=target_label, names=("accept", "contribute", "root"))
    assert source.registration.declarations() == target.registration.declarations()
    source_worksets, target_worksets = WorksetOwner(source), WorksetOwner(target)
    request = source_worksets.request(
        requirements_ref=SourceQualifiedVersionRef(source_label, source.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef(source_label, source.original_input_ref.as_version_ref()),
        command_id=prefix + ":source:request")
    producing = start_parent(source, "produce", prefix + ":source:produce")
    produced = register_products(source, producing, prefix + ":source:produce")
    source.succeed(produced, command_id=prefix + ":source:export",
        workset_action=ExportResult(qualified(source, request), produced.outputs[0].port_id))
    export_row, = source._core.event_store.canonical_object_rows(object_type="collaboration_result_export/v1")
    exported = read_record(source._core, json.loads(export_row["metadata_json"])["record_ref"])
    initial = target_worksets.create(
        requirements_ref=SourceQualifiedVersionRef(target_label, target.identity.task_ref),
        input_binding_ref=SourceQualifiedVersionRef(target_label, target.original_input_ref.as_version_ref()),
        generation=0, expected_slots=("answer",), command_id=prefix + ":target:workset")
    sealed = target_worksets.change(WorksetExpectation.from_record(target._core, initial),
        action="seal", command_id=prefix + ":target:seal")
    delivery = source_worksets.logical_delivery(export_ref=qualified(source, exported),
        target_workset=sealed, slot="answer", command_id=prefix + ":source:delivery")
    sending = start_parent(source, "send", prefix + ":source:send")
    bind_local_workset_source(target, source, source_id=source_label)
    attempt = prepare_local_delivery(source, sending, logical_delivery_ref=qualified(source, delivery),
                                     command_id=prefix + ":source:attempt")
    accepting = start_parent(target, "accept", prefix + ":target:accept")
    accepted_products = register_products(target, accepting, prefix + ":target:accept", attempt.payload)
    acceptance = target_worksets.accept_delivery(attempt,
        expected=WorksetExpectation.from_record(target._core, sealed), slot="answer", decision="new",
        command_id=prefix + ":target:accepted", outputs=accepted_products,
        output_port=accepted_products.outputs[0].port_id)
    ack = finish_local_delivery(source, attempt, outcome="acknowledged",
        command_id=prefix + ":source:ack", target_owner=target, acceptance_ref=qualified(target, acceptance))
    assert ack.outcome == "acknowledged"
    sent_products = register_products(source, sending, prefix + ":source:send")
    source.succeed(sent_products, command_id=prefix + ":source:sent")
    contributing = start_parent(target, "contribute", prefix + ":target:contribute")
    contributed = register_products(target, contributing, prefix + ":target:contribute")
    target.succeed(contributed, command_id=prefix + ":target:contributed", workset_action=Contribute(
        WorksetExpectation.from_record(target._core, current_workset(target, initial["record_ref"])),
        qualified(target, acceptance), contributed.outputs[0].port_id, "answer"))
    root = start_parent(target, "root", prefix + ":target:root")
    return SimpleNamespace(prefix=prefix, source=source, target=target,
        source_worksets=source_worksets, target_worksets=target_worksets,
        request=request, exported=exported, initial=initial, sealed=sealed,
        attempt=attempt, acceptance=acceptance, ack=ack,
        producing=producing, produced=produced, sending=sending, sent_products=sent_products,
        accepting=accepting, accepted_products=accepted_products,
        contributing=contributing, contributed=contributed, root=root,
        parent=execution_parent(root.operation.canonical.context),
        expected=WorksetExpectation.from_record(target._core, current_workset(target, initial["record_ref"])))
