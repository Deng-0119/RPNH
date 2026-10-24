"""Unknown submission is diagnostic-only, never proof that replay is safe.

This is a separately scoped correctness repair to the existing recovery/v1
behavior, not a concurrency benchmark. All transports are in-process fakes.
"""
import json
import socket
import signal
import threading

import pytest

from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputPortInterrupted
from test_external_provider_concurrency import (
    _Factory, _Response, _finished, _install, _reopen,
)
from test_external_provider_health_recovery import (
    MODEL, _audit, _port_and_attempt, _provider_response,
)


@pytest.mark.parametrize("phase", ["partial_write", "headers"])
def test_successful_health_probe_never_replays_unknown_formal_call(tmp_path, monkeypatch, phase):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    formal = 0
    def request(connection):
        nonlocal formal
        if connection.prompt == "original prompt":
            formal += 1
            if phase == "partial_write" and formal == 1:
                raise ConnectionResetError("synthetic partial request write")
    def response(connection):
        if phase == "headers" and connection.prompt == "original prompt" and formal == 1:
            raise socket.timeout("synthetic headers timeout")
        return _Response(connection)
    factory = _Factory(request_hook=request, response_hook=response)
    _install(monkeypatch, factory)

    with pytest.raises(LLMInputPortFailure) as failed:
        port.request_once(attempt)

    failure = failed.value
    assert failure.disposition == failure.submission_state == "submission_unknown"
    assert failure.failure_code == "external_submission_unknown_requires_reconciliation"
    assert formal == 1
    assert [connection.prompt for connection in factory.requests] == [
        "original prompt", "Reply with READY."]
    assert {connection.document["model"] for connection in factory.requests} == {MODEL}
    final, = _finished(tmp_path, attempt)
    assert final["outcome"] == "no_result"
    assert final["detail"]["formal_call_count"] == 1
    assert final["detail"]["probe_call_count"] == 1
    assert final["detail"]["physical_call_count"] == 2
    assert final["detail"]["recovery_reason"] == "submission_unknown_requires_reconciliation"
    assert final["detail"]["last_formal_provider_attempt"]["outcome"] == "submission_unknown"
    assert final["detail"]["last_provider_attempt"]["outcome"] == "response_returned"
    original_finished, = [item for item in _audit(tmp_path)
                         if item["lifecycle"] == "provider_attempt_finished"
                         and item["call_kind"] == "real_model_call"]
    assert original_finished["detail"]["submission_state"] == (
        "request_write_started_completion_unknown" if phase == "partial_write"
        else "request_write_completed_no_response_headers")
    port.close()
    reopened = _reopen(tmp_path)
    with pytest.raises(LLMInputPortFailure, match="external_duplicate_submission_prevented"):
        reopened.request_once(attempt)
    assert len(factory.requests) == 2
    reopened.close()


@pytest.mark.parametrize("body_failure", ["disconnect", "size_limit"])
def test_incomplete_response_is_reconciliation_not_repair(tmp_path, monkeypatch, body_failure):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    def read(_connection):
        if body_failure == "size_limit":
            raise OverflowError("synthetic response bound")
        raise ConnectionResetError("synthetic incomplete body")
    factory = _Factory(response_hook=lambda c: _Response(c, read_hook=read))
    _install(monkeypatch, factory)
    with pytest.raises(LLMInputPortFailure) as failed:
        port.request_once(attempt)
    assert failed.value.submission_state == "submission_unknown"
    assert failed.value.disposition == "submission_unknown"
    assert len(factory.requests) == 1
    finished, = [item for item in _audit(tmp_path)
                 if item["lifecycle"] == "provider_attempt_finished"]
    assert finished["detail"]["submission_state"] == "response_headers_received_body_incomplete"
    assert finished["outcome"] == ("response_too_large" if body_failure == "size_limit" else "transport_failure")
    assert not finished["detail"]["retry_eligible"]
    final, = _finished(tmp_path, attempt)
    assert final["detail"]["physical_call_count"] == 1
    port.close()


@pytest.mark.parametrize("status", [400, 503])
def test_complete_http_response_preserves_original_failure_classification(tmp_path, monkeypatch, status):
    port, attempt, factory = _port_and_attempt(tmp_path, monkeypatch, [
        (status, b'{"error":"synthetic"}', "formal-1"),
        *([(503, b'{"error":"down"}', f"probe-{n}") for n in range(3)] if status == 503 else []),
    ])
    with pytest.raises(LLMInputPortFailure) as failed:
        port.request_once(attempt)
    assert failed.value.submission_state == "response_observed"
    assert failed.value.disposition == ("provider_retryable" if status == 503 else "provider_failure")
    assert len(factory.requests) == (4 if status == 503 else 1)
    port.close()


def test_complete_malformed_response_is_protocol_rejected_not_unknown(tmp_path, monkeypatch):
    port, attempt, factory = _port_and_attempt(tmp_path, monkeypatch, [(200, b"not-json", "formal-1")])
    with pytest.raises(LLMInputPortFailure) as failed:
        port.request_once(attempt)
    assert failed.value.disposition == "protocol_rejected"
    assert failed.value.submission_state == "response_observed"
    assert len(factory.requests) == 1
    port.close()


def _fault_transport(monkeypatch, *, body_incomplete):
    def read(_connection):
        raise ConnectionResetError("synthetic incomplete formal body")
    def response(connection):
        if connection.prompt == "Reply with READY.":
            return _Response(connection)
        if body_incomplete:
            return _Response(connection, read_hook=read)
        raise socket.timeout("synthetic formal headers timeout")
    factory = _Factory(response_hook=response)
    _install(monkeypatch, factory)
    return factory


def _stop_after_unknown_formal(monkeypatch, stop):
    from cpn.llm_adapters.external_provider import ExternalProviderInputPort
    original = ExternalProviderInputPort._perform_request
    attempts = []
    def perform(self, attempt, **kwargs):
        result = original(self, attempt, **kwargs)
        if kwargs["call_kind"] == "real_model_call" and result.outcome == "submission_unknown":
            attempts.append(attempt)
            stop()
        return result
    monkeypatch.setattr(ExternalProviderInputPort, "_perform_request", perform)
    return attempts


@pytest.mark.parametrize("body_incomplete", [False, True, "probe_cancel"], ids=["headers", "body", "probe_cancel"])
def test_real_agent_task_records_unknown_and_exact_reconciliation_block(
        tmp_path, monkeypatch, body_incomplete):
    """Real Registry/PN/AgentLoop owner; requires its normal AF_UNIX environment."""
    from cpn.components.agent_loop.optional_execution import OptionalAgentLoopRegistryService
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task
    from cpn.rpnh.registry._registry import _RegistryCore

    unused, _attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    unused.close()
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1", "adapter_kind": "external_provider",
        "model_condition": MODEL, "adapter_config_path": str(tmp_path / "adapter.json"),
        "timeout_seconds": 10, "max_output_tokens": 16, "max_response_bytes": 1024 * 1024,
    }))
    blocks, recorded = [], []
    # AgentLoop installs its own gateway method over ExecutionServices.
    # Observe that real authority without replacing the registration path.
    real_block = OptionalAgentLoopRegistryService.register_operation_execution_block
    real_failure = OptionalAgentLoopRegistryService.record_llm_invocation_failure_v1
    def observe_block(self, *args, **kwargs):
        block = real_block(self, *args, **kwargs)
        blocks.append(block)
        return block
    def observe_failure(self, *args, **kwargs):
        retry = real_failure(self, *args, **kwargs)
        recorded.append((args[1], retry, dict(kwargs)))
        return retry
    monkeypatch.setattr(OptionalAgentLoopRegistryService, "register_operation_execution_block", observe_block)
    monkeypatch.setattr(OptionalAgentLoopRegistryService, "record_llm_invocation_failure_v1", observe_failure)
    factory = _fault_transport(monkeypatch, body_incomplete=body_incomplete is True)
    cancelled_attempts = []
    if body_incomplete == "probe_cancel":
        # Invoke the actual installed task stop handler, without sending a
        # process-wide signal or replacing the Registry/owner authority.
        def stop_task():
            handler = signal.getsignal(signal.SIGINT)
            assert callable(handler)
            handler(signal.SIGINT, None)
        cancelled_attempts = _stop_after_unknown_formal(monkeypatch, stop_task)
    spec = AgentTaskSpec(tmp_path / "run", "Synthetic unknown transport boundary.",
                         (AgentStage("main", "Return one result."),), execution,
                         max_attempts_per_stage=4)
    result = run_agent_task(spec)
    core = _RegistryCore(spec.run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    if body_incomplete == "probe_cancel":
        assert result["stop_reason"] == "stopped_by_owner"
        interruption, = core.event_store.list_events_by_type(("llm_invocation_owner_interrupted/v1",))
        assert interruption.payload["submission_state"] == "submission_unknown"
        attempt, = cancelled_attempts
        assert interruption.payload["llm_invocation_attempt_ref"]["version_id"] == str(attempt.attempt_ref.version_id)
        assert len(factory.requests) == 1
        assert not recorded
        assert not core.event_store.list_events_by_type(("llm_invocation_succeeded/v1",))
        return
    assert result["stop_reason"] == "blocked_or_waiting"
    failure, = core.event_store.list_events_by_type(("llm_invocation_failed/v1",))
    assert failure.payload["disposition"] == "submission_unknown"
    assert failure.payload["submission_state"] == "submission_unknown"
    registered_attempt, retry_allowed, detail = recorded[0]
    assert len(recorded) == 1 and retry_allowed is False
    assert detail["submission_state"] == "submission_unknown"
    assert len(blocks) == 1
    assert blocks[0].block_kind == "submission_reconciliation"
    assert blocks[0].exact_error_ref == registered_attempt.attempt_ref
    assert failure.payload["llm_invocation_attempt_ref"]["version_id"] == str(registered_attempt.attempt_ref.version_id)
    assert len(factory.requests) == (1 if body_incomplete else 2)
    final, = _finished(tmp_path, registered_attempt)
    assert final["detail"]["formal_call_count"] == 1
    assert final["detail"]["probe_call_count"] == (0 if body_incomplete else 1)
    assert not core.event_store.list_events_by_type(("llm_invocation_succeeded/v1",))


@pytest.mark.parametrize("body_incomplete", [False, True, "probe_cancel"], ids=["headers", "body", "probe_cancel"])
def test_real_registered_host_records_exact_unknown_without_replay(
        tmp_path, monkeypatch, body_incomplete):
    """Actual registered HOST ledger and exact selected external adapter."""
    from cpn.components.execution_services import ExecutionServices
    from cpn.components.registered_host_llm import (
        EXECUTION_PROVENANCE_DOCUMENT, EXECUTION_PROVENANCE_SCHEMA,
        RegisteredHostLLM, RegisteredHostLLMCallBlocked, make_registered_llm_host_bindings,
        registered_host_execution_route,
    )
    from cpn.llm_adapters.config import LLMExecutionSelection
    from cpn.llm_adapters.factory import build_llm_input_port
    from cpn.rpnh.agent_tasks import AgentStage, TEXT_SCHEMA, agent_task_catalog, build_agent_task_module
    from cpn.rpnh.control_server import OwnerEventLoop
    from cpn.rpnh.llm_contracts import LLMInputTarget
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from cpn.rpnh.run import OwnerInput, start_run
    from test_registered_host_llm import _DirectGateway, _capability, _host_request, _registration

    unused, _attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    unused.close()
    target = LLMInputTarget(MODEL, 16, 1024 * 1024)
    selection = LLMExecutionSelection(target, "external_provider", tmp_path / "adapter.json", 10)
    module = build_agent_task_module((AgentStage("worker", "Return one result."),), max_attempts_per_stage=1)
    host_bindings = make_registered_llm_host_bindings(
        target, provider_backend_config=registered_host_execution_route(selection),
        provider_backend_schema_ref=EXECUTION_PROVENANCE_SCHEMA,
        transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                            "response_adapter_ref": "llm_response_envelope/v1"},
        prompt={"messages": [{"role": "user", "content": "ready"}]}, tool_catalog={"tools": []})
    owner_input = OwnerInput(TEXT_SCHEMA, canonical_json("Return one result."), "Synthetic unknown boundary")
    registration = _registration()
    registration.register_schema(EXECUTION_PROVENANCE_SCHEMA, EXECUTION_PROVENANCE_DOCUMENT)
    owner = start_run(
        module, registration, run_dir=tmp_path / "run", task_input=owner_input,
        entry_inputs={"request": owner_input},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
                                       ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition=MODEL, owner_statement="Synthetic unknown transport boundary",
        command_id="unknown:start", catalog=agent_task_catalog(), host_execution_bindings=host_bindings)
    event_loop = OwnerEventLoop(owner, tmp_path / "run" / "owner.sock")
    port = build_llm_input_port(selection, destination_run_root=tmp_path / "run")
    factory = _fault_transport(monkeypatch, body_incomplete=body_incomplete is True)
    cancel = threading.Event()
    if body_incomplete == "probe_cancel":
        _stop_after_unknown_formal(monkeypatch, cancel.set)
    try:
        admitted = owner.admit("worker.run", logical_tau=0, command_id="unknown:admit")
        assert admitted is not None
        execution = owner.start(admitted, command_id="unknown:firing")
        services = ExecutionServices(owner=owner, event_loop=event_loop, llm_input_port=port)
        service = services._registered_host_llm_service
        capability = RegisteredHostLLM(execution=execution,
            gateway=_DirectGateway(service), input_port=port,
            interruption_requested=cancel.is_set)
        with pytest.raises(RegisteredHostLLMCallBlocked) as blocked:
            capability.request(_host_request())
        assert blocked.value.block_kind == (
            "owner_interrupted" if body_incomplete == "probe_cancel" else "submission_reconciliation")
        attempt = blocked.value.attempt
        assert blocked.value.__cause__.submission_state == "submission_unknown"
        failure, = owner._core.event_store.list_events_by_type(("registered_host_llm_attempt_closed/v1",))
        assert failure.payload["submission_state"] == "submission_unknown"
        assert failure.payload["disposition"] == (
            "owner_interrupted" if body_incomplete == "probe_cancel" else "submission_unknown")
        assert failure.payload["registered_host_llm_attempt_ref"]["version_id"] == str(attempt.attempt_ref.version_id)
        before = len(factory.requests)
        with pytest.raises(RegisteredHostLLMCallBlocked):
            _capability(execution, service, port).request(_host_request())
        assert len(factory.requests) == before == (1 if body_incomplete else 2)
        assert len(_finished(tmp_path, attempt)) == 1
        assert not owner._core.event_store.list_events_by_type(("llm_invocation_succeeded/v2",))
    finally:
        port.close()
        event_loop.close()


@pytest.mark.parametrize("phase", ["before_write", "partial_write", "headers", "body", "complete_probe"])
def test_cancelled_health_probe_cannot_downgrade_original_formal_unknown(
        tmp_path, monkeypatch, phase):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    cancel = threading.Event()
    original_perform = port._perform_request
    def perform(*args, **kwargs):
        result = original_perform(*args, **kwargs)
        if kwargs["call_kind"] == "real_model_call":
            assert result.outcome == "submission_unknown"
            if phase == "before_write":
                cancel.set()
        return result
    monkeypatch.setattr(port, "_perform_request", perform)
    def cancelled_io(connection):
        cancel.set()
        assert connection.transport_socket.shutdown_called.wait(5)
        raise ConnectionAbortedError("synthetic diagnostic cancellation")
    def request(connection):
        if connection.prompt == "Reply with READY." and phase == "partial_write":
            cancelled_io(connection)
    def completed_probe(_connection):
        cancel.set()
    def response(connection):
        if connection.prompt == "original prompt":
            raise socket.timeout("synthetic original unknown request")
        assert connection.prompt == "Reply with READY."
        if phase == "headers":
            cancelled_io(connection)
        if phase == "body":
            return _Response(connection, read_hook=cancelled_io)
        if phase == "complete_probe":
            return _Response(connection, read_hook=completed_probe)
        return _Response(connection)
    factory = _Factory(request_hook=request, response_hook=response)
    _install(monkeypatch, factory)
    with pytest.raises(LLMInputPortInterrupted) as stopped:
        port.request_once_interruptible(attempt, interruption_requested=cancel.is_set)
    assert stopped.value.submission_state == "submission_unknown"
    assert len(factory.requests) == (1 if phase == "before_write" else 2)
    final, = _finished(tmp_path, attempt)
    assert final["outcome"] == "owner_interrupted"
    assert final["detail"]["submission_state"] == "submission_unknown"
    assert final["detail"]["last_formal_provider_attempt"]["outcome"] == "submission_unknown"
    assert final["detail"]["formal_call_count"] == 1
    assert final["detail"]["probe_call_count"] == 1
    physical = [item for item in _audit(tmp_path)
                if item["lifecycle"] == "provider_attempt_finished"]
    assert [item["call_kind"] for item in physical] == ["real_model_call", "health_probe"]
    assert physical[0]["outcome"] == "submission_unknown"
    assert physical[1]["outcome"] == "owner_interrupted"
    assert port._inflight == set()
    port.close()


def test_cancelled_probe_after_complete_503_does_not_invent_formal_unknown(tmp_path, monkeypatch):
    port, attempt, _ = _port_and_attempt(tmp_path, monkeypatch, [])
    cancel = threading.Event()
    original = port._perform_request
    def perform(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["call_kind"] == "real_model_call":
            assert result.detail["submission_state"] == "response_complete"
            cancel.set()
        return result
    monkeypatch.setattr(port, "_perform_request", perform)
    factory = _Factory(response_hook=lambda c: _Response(c, status=503))
    _install(monkeypatch, factory)
    with pytest.raises(LLMInputPortInterrupted) as stopped:
        port.request_once_interruptible(attempt, interruption_requested=cancel.is_set)
    assert stopped.value.submission_state == "not_submitted"
    final, = _finished(tmp_path, attempt)
    assert final["detail"]["last_formal_provider_attempt"]["outcome"] == "http_failure"
    assert final["detail"]["last_formal_provider_attempt"]["diagnostic"]["submission_state"] == "response_complete"
    assert len(factory.requests) == 1
    port.close()
