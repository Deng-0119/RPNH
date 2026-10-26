"""Real Registry checks for normal-turn response source binding.

Synthetic canonical input-port returns enter the original provider ledger.
No socket owner, provider transport, model, or tool executor is used. The
normal host startup retains its local environment-inventory subprocess.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.optional_execution import (
    OptionalAgentLoopRegistryService,
)
from cpn.components.agent_loop.optional_host_bindings import (
    make_optional_agent_host_bindings,
)
from cpn.rpnh.agent_tasks import (
    AgentStage, TEXT_SCHEMA, agent_task_catalog, agent_task_registration,
    build_agent_task_module,
)
from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.registry.errors import ResourceIntegrityFault, StaleInvocationContext
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.response_protocol import canonicalize_llm_response_payload
from cpn.rpnh.run import OwnerInput, start_run


RESPONSE = canonicalize_llm_response_payload({
    "protocol": "llm_response_envelope/v1", "text": "alpha",
    "reasoning_content": "known", "tool_calls": [],
})
DIFFERENT_RESPONSE = canonicalize_llm_response_payload({
    "protocol": "llm_response_envelope/v1", "text": "omega",
    "reasoning_content": "other", "tool_calls": [],
})


def _forbid_tools(*args, **kwargs):
    raise AssertionError("No tool execution is permitted in this fixture")


@pytest.fixture
def case(tmp_path):
    module = build_agent_task_module((
        AgentStage("worker", "Store a synthetic response."),
    ), max_attempts_per_stage=1)
    target = LLMInputTarget("offline-v1-binding", 256, 65536)
    backend = {
        "schema_version": "optional_agent_execution_provenance/v1",
        "model": target.model_condition, "backend": "offline-test",
        "timeout_seconds": 30, "selection": {},
        "transport_kind": "offline-test",
        "response_protocol": "llm_response_envelope/v1",
    }
    request = OwnerInput(
        TEXT_SCHEMA, canonical_json("Offline storage boundary"),
        "Synthetic storage boundary")
    owner = start_run(
        module, agent_task_registration(), run_dir=tmp_path / "owner",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(
            tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition=target.model_condition,
        owner_statement="Offline v1 response source-binding test",
        command_id="binding:start", catalog=agent_task_catalog(),
        host_execution_bindings=make_optional_agent_host_bindings(
            target, provider_backend_config=backend,
            transport_contract={
                "interaction_protocol_ref": "llm_request_envelope/v1",
                "response_adapter_ref": "llm_response_envelope/v1",
            }),
    )
    admitted = owner.admit(
        "worker.run", logical_tau=0, command_id="binding:admit")
    execution = owner.start(admitted, command_id="binding:firing")
    kernel, repository = owner.operation_repository()
    service = OptionalAgentLoopRegistryService(
        owner=owner, kernel=kernel, repository=repository,
        provider_attempts=owner._core.provider_attempts,
        invoke_tool=_forbid_tools)
    catalog = service.current_agent_tool_catalog_v1(execution)
    command = service.prepare_agent_loop_start_v1(
        execution, catalog, idempotency_key="binding:loop")
    loop = service.start_agent_loop_v1(command)
    waiting = service.mark_agent_loop_waiting_v1(
        loop, expected_revision=loop.revision,
        idempotency_key="binding:waiting")
    initialization = service.prepare_agent_system_initialization_v1(
        execution, waiting, catalog)
    prepared = service.prepare_agent_turn_context_v1(
        execution, waiting, catalog)
    attempt = service.prepare_agent_llm_turn_v1(
        execution, waiting, catalog, initialization,
        idempotency_key="binding:prepare",
        prepared_context=prepared).attempt
    return SimpleNamespace(
        owner=owner, service=service, execution=execution, catalog=catalog,
        waiting=waiting, prepared=prepared, attempt=attempt)


def _raw(case, payload=RESPONSE):
    provider = case.service.provider_attempt_for_agent_llm_v1(case.attempt)
    ledger = case.owner._core.provider_attempts
    materialized = ledger.record_materialization(
        context=case.execution.operation.canonical.context,
        attempt=provider, request_payload=case.attempt.canonical_request_bytes,
        idempotency_key="binding:materialization")
    ledger.dispatch_started(
        provider,
        materialization_receipt_ref=(
            materialized.receipt.provider_payload_materialization_receipt_ref),
        request_byte_count=len(case.attempt.canonical_request_bytes),
        idempotency_key="binding:dispatch")
    dispatch = case.owner._core.event_store.list_events_by_aggregate(
        str(provider.attempt_id),
        event_types=("provider_attempt_dispatch_started/v2",))[0]
    ledger.submission_permitted(
        provider, dispatch_event_id=dispatch.event_id,
        operation_execution_lease_ref=case.execution.operation_execution_lease_ref,
        operation_start_event_id=case.execution.start_event_id,
        lifecycle_observation_event_id=dispatch.event_id,
        evidence_receipt_id=None, idempotency_key="binding:permit")
    return ledger.complete_v2(
        provider, response=payload, status_code=200,
        external_request_id="offline-binding", idempotency_key="binding:raw")


def _record(case, payload=RESPONSE, attempt=None):
    return case.service.record_agent_llm_turn_v1(
        case.waiting, attempt or case.attempt, payload,
        idempotency_key="binding:record")


def test_same_registered_bytes_round_trip(case):
    authority = _raw(case)
    stored, turn = _record(case)
    context = case.service._context(stored)
    for ref in (authority.response_resource_ref, turn.response_resource_ref):
        assert case.service.kernel._read_firing_registered(context, ref) == RESPONSE
    assert case.service.hydrate_current_agent_turn_v1(stored) == turn


def test_different_same_length_bytes_rejected_without_mutation(case):
    assert RESPONSE != DIFFERENT_RESPONSE
    assert len(RESPONSE) == len(DIFFERENT_RESPONSE)
    authority = _raw(case)
    core = case.owner._core
    with core.event_store.connect() as database:
        before_database = tuple(database.iterdump())
    before_payloads = {
        path.relative_to(core.object_store.root): path.read_bytes()
        for path in core.object_store.root.rglob("*") if path.is_file()
    }
    with pytest.raises(ResourceIntegrityFault) as caught:
        _record(case, DIFFERENT_RESPONSE)
    assert str(caught.value) == (
        "optional response differs from its exact Core raw return")
    with core.event_store.connect() as database:
        assert tuple(database.iterdump()) == before_database
    assert {
        path.relative_to(core.object_store.root): path.read_bytes()
        for path in core.object_store.root.rglob("*") if path.is_file()
    } == before_payloads
    assert case.service.mechanical_lifecycle.current_loop(case.waiting) == case.waiting
    assert not case.service.mechanical_lifecycle.turn_events(case.waiting)

    # A rejected mismatch does not consume this turn's idempotency key.
    stored, turn = _record(case)
    context = case.service._context(stored)
    for ref in (authority.response_resource_ref, turn.response_resource_ref):
        assert case.service.kernel._read_firing_registered(context, ref) == RESPONSE
    assert case.service.hydrate_current_agent_turn_v1(stored) == turn


def test_crossed_registered_neutral_invocation_rejected(case):
    second = case.service.prepare_agent_llm_turn_v1(
        case.execution, case.waiting, case.catalog, case.prepared.initialization,
        idempotency_key="binding:second", prepared_context=case.prepared).attempt
    assert second.invocation_ref != case.attempt.invocation_ref
    _raw(case)
    crossed = replace(case.attempt, invocation_ref=second.invocation_ref)
    with pytest.raises(ResourceIntegrityFault, match="crossed its neutral invocation"):
        _record(case, attempt=crossed)
    assert not case.service.mechanical_lifecycle.turn_events(case.waiting)


def test_stale_writer_cannot_record(case):
    _raw(case)
    case.owner._core.event_store.acquire_writer()
    with pytest.raises(StaleInvocationContext):
        _record(case)


def test_stale_writer_cannot_hydrate_current_turn(case):
    _raw(case)
    stored, _ = _record(case)
    case.owner._core.event_store.acquire_writer()
    with pytest.raises(StaleInvocationContext):
        case.service.hydrate_current_agent_turn_v1(stored)


def test_immutable_publication_rejects_same_length_replacement(case):
    authority = _raw(case)
    store = case.owner._core.object_store
    with pytest.raises(ObjectIntegrityError, match="immutable version collision"):
        store._publish_payload(
            DIFFERENT_RESPONSE, authority.response_resource_ref.resource_version_id)
    assert store.path_for_version(
        authority.response_resource_ref.resource_version_id).read_bytes() == RESPONSE
