from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import tarfile

from cpn.components.agent_loop.models import AgentLoopState
from cpn.components.agent_loop.optional_host_bindings import (
    make_optional_agent_host_bindings,
)
from cpn.components.execution_services import ExecutionServices
from cpn.rpnh.agent_tasks import (
    AgentStage,
    TEXT_SCHEMA,
    agent_task_catalog,
    agent_task_registration,
    build_agent_task_module,
)
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.firing_recovery import (
    record_registered_operation_completion,
)
from cpn.rpnh.registry.operations import register_operation_outputs
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.run import OwnerInput, resume_run, start_run


def _multi_output_module() -> ModuleDeclaration:
    document = build_agent_task_module((
        AgentStage("worker", "Write both declared outputs."),
    ), max_attempts_per_stage=2).to_dict()
    component = document["components"][0]
    result_port = next(
        item for item in component["ports"] if item["name"] == "result")
    component["ports"].insert(-1, {
        **result_port,
        "name": "memo",
    })
    operation = component["operations"][0]
    operation["outputs"].insert(1, "memo")
    operation["outcomes"][0] = {
        "name": "accept",
        "products": [
            {"port": "result", "minimum": 1, "maximum": 1},
            {"port": "memo", "minimum": 1, "maximum": 1},
        ],
        "effects": [],
    }
    document["terminal"]["outcome"] = "accept"
    document["terminal"]["config"]["run_outcome"] = "accept"
    return ModuleDeclaration.from_dict(document)


def _response(*, duplicate_second_port: bool, complete: bool) -> bytes:
    calls = [{
        "id": "write-result",
        "name": "write_file",
        "arguments": json.dumps({
            "path": "outputs/result.txt",
            "description": "Primary accepted output.",
            "content": json.dumps("result"),
            "output_port_id": "worker.result",
            "outcome_id": "accept",
        }),
    }, {
        "id": "write-memo",
        "name": "write_file",
        "arguments": json.dumps({
            "path": "outputs/memo.txt",
            "description": "Secondary accepted output.",
            "content": json.dumps("memo"),
            "output_port_id": (
                "worker.result" if duplicate_second_port
                else "worker.memo"),
            "outcome_id": "accept",
        }),
    }]
    if complete:
        calls.append({
            "id": "complete",
            "name": "complete_interaction",
            "arguments": "{}",
        })
    return canonical_json({
        "protocol": "llm_response_envelope/v1",
        "tool_calls": calls,
        "finish_reason": "tool_calls",
    })


def _stored_turn(tmp_path: Path, response: bytes):
    module = _multi_output_module()
    declaration = module.to_dict()
    target = LLMInputTarget("offline-multi-output", 256, 65536)
    backend = {
        "schema_version": "optional_agent_execution_provenance/v1",
        "model": target.model_condition,
        "backend": "offline-test",
        "timeout_seconds": 30,
        "selection": {},
        "transport_kind": "offline-test",
        "response_protocol": "llm_response_envelope/v1",
    }
    host_bindings = make_optional_agent_host_bindings(
        target,
        provider_backend_config=backend,
        transport_contract={
            "interaction_protocol_ref": "llm_request_envelope/v1",
            "response_adapter_ref": "llm_response_envelope/v1",
        },
    )
    request = OwnerInput(
        TEXT_SCHEMA, canonical_json("Produce both outputs."),
        "Offline multi-output request",
    )
    owner = start_run(
        module,
        agent_task_registration(),
        run_dir=tmp_path,
        task_input=request,
        entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(
            tuple(declaration["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 2, 0, 2, 0,
        ),
        model_condition=target.model_condition,
        owner_statement="Offline same-turn authority test",
        command_id="offline:multi-output:start",
        catalog=agent_task_catalog(),
        host_execution_bindings=host_bindings,
    )
    event_loop = OwnerEventLoop(owner, tmp_path / "owner.sock")
    admitted = owner.admit(
        "worker.run", logical_tau=0,
        command_id="offline:multi-output:admit")
    assert admitted is not None
    execution = owner.start(
        admitted, command_id="offline:multi-output:firing")
    services = ExecutionServices(
        owner=owner, event_loop=event_loop, llm_input_port=object())
    service = services._optional_agent_service
    assert service is not None
    catalog = service.current_agent_tool_catalog_v1(execution)
    command = service.prepare_agent_loop_start_v1(
        execution, catalog,
        idempotency_key="offline:multi-output:loop:start")
    loop = service.start_agent_loop_v1(command)
    waiting = service.mark_agent_loop_waiting_v1(
        loop, expected_revision=loop.revision,
        idempotency_key="offline:multi-output:loop:waiting")
    initialization = service.prepare_agent_system_initialization_v1(
        execution, waiting, catalog)
    prepared = service.prepare_agent_turn_context_v1(
        execution, waiting, catalog)
    attempt = service.prepare_agent_llm_turn_v1(
        execution, waiting, catalog, initialization,
        idempotency_key="offline:multi-output:turn:prepare",
        prepared_context=prepared,
    ).attempt
    services._prepare_optional_input_submission(execution, attempt)
    services._register_optional_input_return(
        execution, attempt, response,
        status_code=200, external_request_id="offline-response")
    stored, turn = service.record_agent_llm_turn_v1(
        waiting, attempt, response,
        idempotency_key="offline:multi-output:turn:record")
    actions = service.prepare_agent_turn_actions_v1(stored, turn)
    return owner, event_loop, service, execution, catalog, stored, turn, actions


def test_same_turn_multi_output_uses_exact_firing_authority(
        tmp_path: Path,
) -> None:
    positive = _stored_turn(
        tmp_path / "positive",
        _response(duplicate_second_port=False, complete=True),
    )
    (owner, event_loop, service, execution, catalog,
     stored, turn, actions) = positive
    try:
        settled, records = service.settle_agent_turn_actions_v1(
            stored, turn, actions,
            permitted_tool_names=catalog.tool_names,
            idempotency_key="offline:multi-output:actions",
            execution=execution,
        )

        assert settled.state == AgentLoopState.COMPLETED
        assert [record.state for record in records] == [
            AgentLoopState.ACTION_APPLIED,
            AgentLoopState.ACTION_APPLIED,
            AgentLoopState.COMPLETED,
        ]
        assert len(settled.written_resource_refs) == 2
        assert len(set(settled.written_resource_refs)) == 2
        headers = tuple(
            service.kernel._firing_header(
                execution.operation.canonical.context, ref)
            for ref in settled.written_resource_refs)
        _binding, compiled, _operation = service._declared(
            execution.operation.canonical.context)
        expected_port_ids = {
            port.port_id for port in compiled.ports
            if port.name in {"worker.result", "worker.memo"}
        }
        assert {
            value
            for header in headers
            for descriptor in header.descriptor_labels
            if descriptor.name == "output_port_id"
            for value in descriptor.values
        } == expected_port_ids
        assert {
            value
            for header in headers
            for descriptor in header.descriptor_labels
            if descriptor.name == "output_outcome_id"
            for value in descriptor.values
        } == {"accept"}

        service.finalize_agent_workspace_v1(
            execution, settled,
            idempotency_key="offline:multi-output:workspace")
        artifacts = tuple(
            verify_resource(
                owner._core, service.kernel,
                execution.operation.canonical, ref)
            for ref in settled.written_resource_refs)
        outputs = register_operation_outputs(
            service.repository, execution, artifacts,
            selected_outcome_id="accept",
            idempotency_key="offline:multi-output:outputs")
        owner.succeed(outputs, command_id="offline:multi-output:success")
        canonical_headers = tuple(
            service.kernel._header(
                ref, through_head=service.kernel._head())
            for ref in settled.written_resource_refs)
        assert tuple(header.ref for header in canonical_headers) == (
            settled.written_resource_refs)
    finally:
        event_loop.close()

    negative = _stored_turn(
        tmp_path / "negative",
        _response(duplicate_second_port=True, complete=False),
    )
    (negative_owner, negative_event_loop, negative_service,
     negative_execution, negative_catalog, negative_stored,
     negative_turn, negative_actions) = negative
    try:
        rejected, records = negative_service.settle_agent_turn_actions_v1(
            negative_stored, negative_turn, negative_actions,
            permitted_tool_names=negative_catalog.tool_names,
            idempotency_key="offline:duplicate-output:actions",
            execution=negative_execution,
        )

        assert rejected.state == AgentLoopState.WAITING_FOR_LLM
        assert [record.state for record in records] == [
            AgentLoopState.ACTION_APPLIED,
            AgentLoopState.ACTION_REJECTED,
        ]
        assert len(rejected.written_resource_refs) == 1
        assert records[1].tool_error_ref is not None
        assert not negative_owner._core.event_store.list_events_by_type((
            "operation_terminal_ready/v1",
            "transition_firing_settled/v1",
        ))
    finally:
        negative_event_loop.close()


def test_workspace_completion_recovers_from_frozen_candidate_without_replay(
        tmp_path: Path) -> None:
    (owner, event_loop, service, execution, catalog,
     stored, turn, actions) = _stored_turn(
        tmp_path, _response(duplicate_second_port=False, complete=True))
    try:
        settled, _records = service.settle_agent_turn_actions_v1(
            stored, turn, actions,
            permitted_tool_names=catalog.tool_names,
            idempotency_key="offline:recovery:actions",
            execution=execution,
        )
        service.finalize_agent_workspace_v1(
            execution, settled,
            idempotency_key="offline:recovery:workspace")
        artifacts = tuple(
            verify_resource(
                owner._core, service.kernel,
                execution.operation.canonical, ref)
            for ref in settled.written_resource_refs)
        outputs = register_operation_outputs(
            service.repository, execution, artifacts,
            selected_outcome_id="accept",
            idempotency_key="offline:recovery:outputs")
        completion = record_registered_operation_completion(
            owner._core, service.kernel, service.repository, outputs,
            idempotency_key="offline:recovery:completion")
        candidate_ref = _version_from_payload(
            completion.payload["workspace_revision_candidate_ref"])
        candidate = owner._core.get_version(candidate_ref.version_id)
        candidate_payload = owner._core.object_store.read_registered(candidate)

        # This mutation occurs after the durable completion authority.  A new
        # writer must settle from the immutable candidate, not rescan live
        # files or rerun the completed AgentLoop/workspace action.
        workspace_root = service._workspace_root(settled)
        workspace_root.joinpath("outputs/result.txt").write_text(
            "post-completion mutation", encoding="utf-8")
    finally:
        event_loop.close()

    resumed = resume_run(
        agent_task_registration(), run_dir=tmp_path,
        model_condition="offline-multi-output",
        catalog=agent_task_catalog(),
        host_execution_bindings=owner.host_execution_bindings,
    )
    assert resumed.snapshot()["active_firings"] == []
    assert sum(
        event.event_type == "registered_operation_completion_recorded/v1"
        for event in resumed._core.event_store.list_events()) == 1
    assert sum(
        event.event_type == "transition_firing_settled/v1"
        for event in resumed._core.event_store.list_events()) == 1

    revisions = resumed._core.event_store.canonical_object_rows(
        object_type="workspace_revision/v1")
    assert len(revisions) == 2
    final = resumed._core.get_version(
        _version_from_payload(json.loads(
            revisions[-1]["metadata_json"])["workspace_revision_ref"]
        ).version_id)
    final_payload = resumed._core.object_store.read_registered(final)
    assert final_payload == candidate_payload
    with tarfile.open(fileobj=io.BytesIO(final_payload), mode="r:") as archive:
        assert archive.extractfile("outputs/result.txt").read() == b"result"
        assert archive.extractfile("outputs/memo.txt").read() == b"memo"

    mappings = resumed._core.event_store.canonical_object_rows(
        object_type="execution_terminal_mapping/v1")
    assert len(mappings) == 3
    assert all(
        json.loads(row["metadata_json"])["workspace_revision_ref"]
        == final.metadata["workspace_revision_ref"]
        for row in mappings)
    firing_version_id = str(
        execution.operation.firing.transition_firing_ref.version_id)
    with sqlite3.connect(
            tmp_path / ".registry_v1" / "registry.sqlite3") as database:
        publication_transaction, = database.execute(
            "SELECT published_transaction_id FROM firing_publications "
            "WHERE firing_version_id=?", (firing_version_id,)).fetchone()
        mapping_transactions = {
            row[0] for row in database.execute(
                "SELECT transaction_id FROM objects "
                "WHERE object_type='execution_terminal_mapping/v1'")}
        revision_transaction, = database.execute(
            "SELECT transaction_id FROM objects "
            "WHERE version_id=?",
            (str(final.version_id),)).fetchone()
    assert mapping_transactions == {publication_transaction}
    assert revision_transaction == publication_transaction
