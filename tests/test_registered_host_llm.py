from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpn.components.agent_loop.optional_execution import (
    OPTIONAL_TOOL_BINDINGS,
    optional_agent_loop_schema_data,
)
from cpn.components.basic import register_basic_components
from cpn.components.execution_services import ExecutionServices
from cpn.components.registered_host_llm import (
    HOST_PROTOCOL,
    RegisteredHostLLM,
    RegisteredHostLLMCallBlocked,
    make_registered_llm_host_bindings,
    registered_host_execution_identity,
    registered_host_execution_route,
    registered_host_llm_schema_data,
)
from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.rpnh.agent_tasks import (
    EXECUTOR_KEY,
    TERMINAL_KEY,
    TEXT_SCHEMA,
    TEXT_SCHEMA_DOCUMENT,
    AgentStage,
    agent_task_catalog,
    build_agent_task_module,
)
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.operations import OperationAuthorityError
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.resource_access import ResourceReadContract
from cpn.rpnh.run import OwnerInput, start_run


def _unused_executor(**_kwargs):
    raise AssertionError("the focused Registry test does not dispatch the executor")


def _registration() -> Registration:
    registration = Registration()
    register_basic_components(registration)
    schemas, _types = optional_agent_loop_schema_data()
    for key, schema in schemas.items():
        registration.register_schema(key, schema)
    for key, (implementation, data) in OPTIONAL_TOOL_BINDINGS.items():
        registration.register_tool(key, implementation, **data)
    registration.register_schema(TEXT_SCHEMA, TEXT_SCHEMA_DOCUMENT)
    registration.register_executor(
        EXECUTOR_KEY,
        _unused_executor,
        identity={
            "implementation_id": "tests.registered_host_llm",
            "revision": "v1",
        },
        contracts={
            "transport": "llm",
            "input_ports": None,
            "output_ports": None,
            "host_protocols": [HOST_PROTOCOL],
            "resource_read_contracts": [
                ResourceReadContract(
                    metadata_only=False,
                    context_origins=("petri_operation",),
                    origin_kinds=("provider_request",),
                    require_producer_invocation=True,
                    require_provenance_binding=True,
                ).to_dict()
            ],
            "provider_request_schema": "runtime/llm_request_envelope/v1",
        },
    )
    registration.register_tool(
        TERMINAL_KEY,
        dict,
        identity={
            "implementation_id": "tests.registered_host_terminal",
            "revision": "v1",
        },
        contracts={"binding_protocol": "rpnh/module_terminal/v1"},
    )
    return registration


class _SuccessfulPort:
    def __init__(self, policy: dict[str, object]) -> None:
        self.execution_policy = policy

    def request_once(self, _attempt):
        return LLMInputResponseBytes(
            canonical_json({
                "protocol": "llm_response_envelope/v1",
                "text": "ready",
                "tool_calls": [],
                "finish_reason": "stop",
            }),
            status_code=200,
            external_request_id="offline-registered-host",
        )

    def close(self) -> None:
        pass


class _CountingPort(_SuccessfulPort):
    def __init__(self, policy: dict[str, object]) -> None:
        super().__init__(policy)
        self.calls = 0

    def request_once(self, attempt):
        self.calls += 1
        return super().request_once(attempt)


class _DirectGateway:
    def __init__(self, service) -> None:
        self._methods = service.gateway_methods()

    def __getattr__(self, name):
        return self._methods[name]


class _InjectedCrash(BaseException):
    pass


def _host_request(content: str = "ready") -> bytes:
    return canonical_json({
        "protocol": HOST_PROTOCOL,
        "messages": [{"role": "user", "content": content}],
        "tools": [],
        "tool_choice": "none",
        "placeholders": [],
    })


def _capability(execution, service, port) -> RegisteredHostLLM:
    return RegisteredHostLLM(
        execution=execution, gateway=_DirectGateway(service),
        input_port=port, interruption_requested=lambda: False)


def test_registered_host_route_and_catalog_are_host_neutral(
        tmp_path: Path,
) -> None:
    adapter = tmp_path / "local.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "local-model",
        "argv": ["local-model"],
        "probe_argv": ["local-model", "--probe"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    selection = LLMExecutionSelection(
        LLMInputTarget("local-model", 32, 65536),
        "local_process", adapter.resolve(), 30)

    route = registered_host_execution_route(selection)
    schemas, types = registered_host_llm_schema_data()

    assert route["model"] == "local-model"
    assert route["backend"] == "local_process"
    assert route["transport_kind"] == "subprocess"
    assert route["selection"] == registered_host_execution_identity(
        selection.as_registry_policy())
    assert set(schemas) == {"registry_v1/provider_attempt_completed/v1"}
    assert [item.name for item in types] == [
        "provider_attempt_completed/v1"]


def test_registered_host_identity_excludes_private_provider_material() -> None:
    policy = {
        "adapter_kind": "external_provider",
        "timeout_seconds": 30,
        "max_output_tokens": 64,
        "max_response_bytes": 65536,
        "route_provenance": [{
            "route_id": "primary",
            "provider": "provider",
            "backend": "responses",
            "protocol": "openai_chat_completions/v1",
            "endpoint": "https://private.example.invalid/v1/chat",
            "transport": "https",
            "outbound_model": "exact-model",
            "credential_binding": {
                "kind": "bearer_env", "env": "PRIVATE_API_KEY"},
            "headers": {"X-Private-Route": "private-header-value"},
        }],
        "adapter_profile": {
            "config_schema_version": "external_provider_adapter_config/v2",
            "config_revision": {"byte_count": 100, "modified_ns": 200},
            "route_count": 1,
            "recovery": {
                "strategy": "bounded_same_route_health_probe/v1",
                "max_probe_attempts": 3,
                "probe_timeout_budget_seconds": 300,
                "max_probe_success_formal_failure_cycles": 3,
            },
        },
    }

    identity = registered_host_execution_identity(policy)
    encoded = canonical_json(identity)

    assert identity["routes"] == [{
        "route_id": "primary",
        "provider": "provider",
        "backend": "responses",
        "transport": "https",
        "protocol": "openai_chat_completions/v1",
        "outbound_model": "exact-model",
    }]
    assert b"private.example.invalid" not in encoded
    assert b"PRIVATE_API_KEY" not in encoded
    assert b"private-header-value" not in encoded


def _started_execution(tmp_path: Path):
    module = build_agent_task_module(
        (AgentStage("worker", "Return one result."),),
        max_attempts_per_stage=1,
    )
    declaration = module.to_dict()
    target = LLMInputTarget("offline-registered-host", 32, 65536)
    policy = {
        "adapter_kind": "offline-test",
        "timeout_seconds": 30,
        "max_output_tokens": target.max_output_tokens,
        "max_response_bytes": target.max_response_bytes,
        "route_provenance": [{
            "route_id": "offline-test",
            "provider": "offline-test",
            "backend": "offline-test",
            "transport": "offline-test",
            "outbound_model": target.model_condition,
        }],
        "adapter_profile": {"config_schema_version": "offline-test/v1"},
    }
    route = {
        "schema_version": "optional_agent_execution_provenance/v1",
        "model": target.model_condition,
        "backend": "offline-test",
        "timeout_seconds": 30,
        "selection": registered_host_execution_identity(policy),
        "transport_kind": "offline-test",
        "response_protocol": "llm_response_envelope/v1",
    }
    host_bindings = make_registered_llm_host_bindings(
        target,
        provider_backend_config=route,
        provider_backend_schema_ref=(
            "component/optional_agent_execution_provenance/v1"),
        transport_contract={
            "interaction_protocol_ref": "llm_request_envelope/v1",
            "response_adapter_ref": "llm_response_envelope/v1",
        },
        prompt={"messages": [{"role": "user", "content": "ready"}]},
        tool_catalog={"tools": []},
    )
    request = OwnerInput(
        TEXT_SCHEMA, canonical_json("Return one result."),
        "Registered HOST test request",
    )
    owner = start_run(
        module,
        _registration(),
        run_dir=tmp_path,
        task_input=request,
        entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(
            tuple(declaration["budget_buckets"]),
            ("rpnh/module_declaration/v1",),
            1,
            0,
            1,
            0,
        ),
        model_condition=target.model_condition,
        owner_statement="Registered HOST focused test",
        command_id="offline:registered-host:start",
        catalog=agent_task_catalog(),
        host_execution_bindings=host_bindings,
    )
    event_loop = OwnerEventLoop(owner, tmp_path / "owner.sock")
    admitted = owner.admit(
        "worker.run", logical_tau=0,
        command_id="offline:registered-host:admit",
    )
    assert admitted is not None
    execution = owner.start(
        admitted, command_id="offline:registered-host:firing")
    return owner, event_loop, execution, target, policy


def test_registered_host_llm_uses_shared_provider_accounting_without_agent_loop(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, target, policy = _started_execution(tmp_path)
    try:
        services = ExecutionServices(
            owner=owner,
            event_loop=event_loop,
            llm_input_port=_SuccessfulPort(policy),
        )
        service = services._registered_host_llm_service
        assert service is not None
        _context, _target_ref, _target, roles = service._resources(execution)
        request = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "ready"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })

        attempt = service.prepare(execution, request, policy)
        response = _SuccessfulPort(policy).request_once(attempt)
        result = service.complete(
            execution,
            attempt,
            response,
            status_code=response.status_code,
            external_request_id=response.external_request_id,
        )

        assert json.loads(result)["text"] == "ready"
        assert attempt.invocation_ref.entity_type == "llm_call_spec/v3"
        assert tuple(owner._core.event_store.canonical_object_rows(
            object_type="agent_loop/v1")) == ()
        events = tuple(owner._core.event_store.list_events())
        assert len([event for event in events
                    if event.event_type == "provider_attempt_completed/v1"]) == 1
        assert len([event for event in events
                    if event.event_type == "llm_invocation_succeeded/v2"]) == 1
        assert owner._core.event_store.actual_model_call_counts() == (1, 0)
        response_event = next(
            event for event in events
            if event.event_type == "llm_response_registered/v2")
        response_ref = response_event.payload["response_resource_ref"]
        assert service.kernel._read_firing_registered(
            execution.operation.canonical.context,
            ResourceVersionRef(
                TypedId.parse(response_ref["resource_id"],
                              expected="resource"),
                TypedId.parse(response_ref["resource_version_id"],
                              expected="resource_version"),
            ),
        ) == result
        outputs = owner.products(
            execution,
            outcome_id="interrupted",
            products={},
            command_id="offline:registered-host:products",
        )
        owner.succeed(
            outputs, command_id="offline:registered-host:succeed")
    finally:
        event_loop.close()


def test_registered_host_llm_failure_closes_all_exact_identities(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        services = ExecutionServices(
            owner=owner,
            event_loop=event_loop,
            llm_input_port=_SuccessfulPort(policy),
        )
        service = services._registered_host_llm_service
        assert service is not None
        request = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "ready"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })
        attempt = service.prepare(execution, request, policy)
        service.close(
            execution, attempt,
            disposition="adapter_not_submitted",
            submission_state="not_submitted",
            failure_code="offline_test_failure",
        )

        events = tuple(owner._core.event_store.list_events())
        closure_events = tuple(
            event for event in events
            if event.payload.get("registered_host_llm_attempt_ref") == {
                "entity_type": attempt.attempt_ref.entity_type,
                "logical_id": str(attempt.attempt_ref.entity_id),
                "version_id": str(attempt.attempt_ref.version_id),
            }
        )
        closed = {event.event_type for event in closure_events}
        assert closed == {
            "registered_host_llm_attempt_reserved/v1",
            "provider_attempt_host_closed/v1",
            "llm_call_registered_host_closed/v1",
            "registered_host_llm_attempt_closed/v1",
        }
        for event in closure_events:
            if event.event_type.endswith("_closed/v1"):
                assert "next_attempt_allowed" not in event.payload
                historical_payload = dict(event.payload)
                historical_payload["next_attempt_allowed"] = False
                owner._core.catalog.validate_schema_ref(
                    event.payload_schema_ref, historical_payload)
        assert owner._core.event_store.actual_model_call_counts() == (0, 0)
        outputs = owner.products(
            execution,
            outcome_id="interrupted",
            products={},
            command_id="offline:registered-host:failed-products",
        )
        owner.succeed(
            outputs, command_id="offline:registered-host:failed-succeed")
    finally:
        event_loop.close()


def test_registered_host_llm_prepare_rejects_replay_or_changed_request(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        services = ExecutionServices(
            owner=owner,
            event_loop=event_loop,
            llm_input_port=_SuccessfulPort(policy),
        )
        service = services._registered_host_llm_service
        assert service is not None
        request = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "ready"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })
        service.prepare(execution, request, policy)
        with pytest.raises(
                OperationAuthorityError,
                match="must be reconciled, not replayed"):
            service.prepare(execution, request, policy)

        changed = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "changed"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })
        with pytest.raises(
                OperationAuthorityError,
                match="already owns a different canonical request"):
            service.prepare(execution, changed, policy)
    finally:
        event_loop.close()


def test_registered_host_llm_rejects_mismatched_physical_route(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        services = ExecutionServices(
            owner=owner,
            event_loop=event_loop,
            llm_input_port=_SuccessfulPort(policy),
        )
        service = services._registered_host_llm_service
        assert service is not None
        request = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "ready"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })
        wrong_policy = {**policy, "adapter_kind": "different-route"}
        with pytest.raises(
                OperationAuthorityError,
                match="differs from firing bindings"):
            service.prepare(execution, request, wrong_policy)
        assert tuple(owner._core.event_store.canonical_object_rows(
            object_type="llm_call_spec/v3")) == ()
    finally:
        event_loop.close()


def test_registered_host_llm_malformed_response_closes_terminal_bundle(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        services = ExecutionServices(
            owner=owner,
            event_loop=event_loop,
            llm_input_port=_SuccessfulPort(policy),
        )
        service = services._registered_host_llm_service
        assert service is not None
        request = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "ready"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })
        attempt = service.prepare(execution, request, policy)
        with pytest.raises(
                RegisteredHostLLMCallBlocked,
                match="framework_response_protocol_invalid"):
            service.complete(
                execution, attempt,
                LLMInputResponseBytes(
                    b"{}", status_code=200,
                    external_request_id="offline-malformed"),
                status_code=200,
                external_request_id="offline-malformed",
            )
        event_types = {
            event.event_type
            for event in owner._core.event_store.list_events()
            if event.payload.get("registered_host_llm_attempt_ref") == {
                "entity_type": attempt.attempt_ref.entity_type,
                "logical_id": str(attempt.attempt_ref.entity_id),
                "version_id": str(attempt.attempt_ref.version_id),
            }
        }
        assert {
            "provider_attempt_host_closed/v1",
            "llm_call_registered_host_closed/v1",
            "registered_host_llm_attempt_closed/v1",
        } <= event_types
        outputs = owner.products(
            execution, outcome_id="interrupted", products={},
            command_id="offline:registered-host:malformed-products")
        owner.succeed(
            outputs,
            command_id="offline:registered-host:malformed-succeed")
    finally:
        event_loop.close()


def test_registered_host_llm_prepare_failure_is_terminal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        services = ExecutionServices(
            owner=owner,
            event_loop=event_loop,
            llm_input_port=_SuccessfulPort(policy),
        )
        service = services._registered_host_llm_service
        assert service is not None

        def fail_materialization(**_kwargs):
            raise RuntimeError("injected materialization failure")

        monkeypatch.setattr(
            service.ledger, "record_materialization", fail_materialization)
        request = canonical_json({
            "protocol": HOST_PROTOCOL,
            "messages": [{"role": "user", "content": "ready"}],
            "tools": [],
            "tool_choice": "none",
            "placeholders": [],
        })
        with pytest.raises(RuntimeError, match="injected materialization"):
            service.prepare(execution, request, policy)

        assert len([
            event for event in owner._core.event_store.list_events()
            if event.event_type == "provider_attempt_host_closed/v1"
        ]) == 1
        outputs = owner.products(
            execution, outcome_id="interrupted", products={},
            command_id="offline:registered-host:prepare-failed-products")
        owner.succeed(
            outputs,
            command_id="offline:registered-host:prepare-failed-succeed")
    finally:
        event_loop.close()


def test_registered_host_llm_fresh_request_path_is_unchanged(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        port = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port)
        service = services._registered_host_llm_service

        result = _capability(execution, service, port).request(_host_request())

        assert json.loads(result)["text"] == "ready"
        assert port.calls == 1
        assert owner._core.event_store.actual_model_call_counts() == (1, 0)
    finally:
        event_loop.close()


def test_registered_host_llm_resumes_exact_pre_submission_attempt_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        port = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port)
        service = services._registered_host_llm_service
        original = service.ledger.record_materialization

        def crash_after_materialization(**kwargs):
            original(**kwargs)
            raise _InjectedCrash()

        monkeypatch.setattr(
            service.ledger, "record_materialization",
            crash_after_materialization)
        with pytest.raises(_InjectedCrash):
            service.prepare(execution, _host_request(), policy)
        monkeypatch.setattr(service.ledger, "record_materialization", original)
        before = service.classify_resume(
            execution, _host_request(), policy)
        assert before.classification == "pre_submission"
        assert before.preparation_step == "dispatch"

        result = _capability(execution, service, port).request(_host_request())

        after = service.classify_resume(execution, _host_request(), policy)
        assert json.loads(result)["text"] == "ready"
        assert after.classification == "semantic_success"
        assert after.attempt.attempt_ref == before.attempt.attempt_ref
        assert port.calls == 1
        assert len(owner._core.event_store.provider_attempt_rows_for_call(
            before.attempt.invocation_ref.entity_id,
            before.attempt.invocation_ref.version_id)) == 1
    finally:
        event_loop.close()


def test_registered_host_llm_permit_without_response_blocks_replay(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        port = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port)
        service = services._registered_host_llm_service
        attempt = service.prepare(execution, _host_request(), policy)

        with pytest.raises(
                RegisteredHostLLMCallBlocked, match="submission_unknown") as exc:
            _capability(execution, service, port).request(_host_request())

        assert exc.value.attempt.attempt_ref == attempt.attempt_ref
        assert exc.value.block_kind == "submission_reconciliation"
        assert port.calls == 0
        assert owner._core.event_store.actual_model_call_counts() == (0, 0)
    finally:
        event_loop.close()


def test_registered_host_llm_raw_response_finalizes_without_replay(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cpn.components.registered_host_llm as registered_host_llm

    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        physical = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=physical)
        service = services._registered_host_llm_service
        attempt = service.prepare(execution, _host_request(), policy)
        response = physical.request_once(attempt)
        original = registered_host_llm.canonicalize_llm_response_payload

        def crash_after_raw(_response):
            raise _InjectedCrash()

        monkeypatch.setattr(
            registered_host_llm, "canonicalize_llm_response_payload",
            crash_after_raw)
        with pytest.raises(_InjectedCrash):
            service.complete(
                execution, attempt, response,
                status_code=response.status_code,
                external_request_id=response.external_request_id)
        monkeypatch.setattr(
            registered_host_llm, "canonicalize_llm_response_payload", original)
        assert service.classify_resume(
            execution, _host_request(), policy).classification == "raw_response"
        resumed = _CountingPort(policy)

        result = _capability(execution, service, resumed).request(_host_request())

        assert result == bytes(response)
        assert physical.calls == 1
        assert resumed.calls == 0
        assert owner._core.event_store.actual_model_call_counts() == (1, 0)
    finally:
        event_loop.close()


def test_registered_host_llm_semantic_success_returns_registered_bytes(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        physical = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=physical)
        service = services._registered_host_llm_service
        attempt = service.prepare(execution, _host_request(), policy)
        response = physical.request_once(attempt)
        expected = service.complete(
            execution, attempt, response,
            status_code=response.status_code,
            external_request_id=response.external_request_id)
        before = owner._core.event_store.actual_model_call_counts()
        resumed = _CountingPort(policy)

        result = _capability(execution, service, resumed).request(_host_request())

        assert result == expected
        assert resumed.calls == 0
        assert owner._core.event_store.actual_model_call_counts() == before
    finally:
        event_loop.close()


def test_registered_host_llm_closed_duplicate_and_crossed_facts_reject(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed_root = tmp_path / "closed"
    owner, event_loop, execution, _target, policy = _started_execution(
        closed_root)
    try:
        port = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port)
        service = services._registered_host_llm_service
        attempt = service.prepare(execution, _host_request(), policy)
        service.close(
            execution, attempt, disposition="adapter_not_submitted",
            submission_state="not_submitted", failure_code="closed_test")
        with pytest.raises(RegisteredHostLLMCallBlocked, match="closed_test"):
            _capability(execution, service, port).request(_host_request())
        assert port.calls == 0
    finally:
        event_loop.close()

    duplicate_root = tmp_path / "duplicate"
    owner, event_loop, execution, _target, policy = _started_execution(
        duplicate_root)
    try:
        port = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=port)
        service = services._registered_host_llm_service
        attempt = service.prepare(execution, _host_request(), policy)
        store = owner._core.event_store
        original = store.list_events_by_aggregate

        def duplicated(aggregate_id, *, event_types=(), after_ordinal=0):
            events = original(
                aggregate_id, event_types=event_types,
                after_ordinal=after_ordinal)
            if (aggregate_id == str(attempt.provider_attempt_ref.entity_id)
                    and not event_types):
                reserved = next(
                    event for event in events
                    if event.event_type == "provider_attempt_reserved/v1")
                return (*events, reserved)
            return events

        monkeypatch.setattr(store, "list_events_by_aggregate", duplicated)
        with pytest.raises(
                RegisteredHostLLMCallBlocked,
                match="registered_host_resume_inconsistent"):
            _capability(execution, service, port).request(_host_request())
        assert port.calls == 0
        monkeypatch.setattr(store, "list_events_by_aggregate", original)
        with pytest.raises(
                RegisteredHostLLMCallBlocked,
                match="registered_host_resume_inconsistent"):
            _capability(execution, service, port).request(
                _host_request("crossed"))
        assert port.calls == 0
    finally:
        event_loop.close()


def test_registered_host_llm_repeated_reconciliation_is_idempotent(
        tmp_path: Path,
) -> None:
    owner, event_loop, execution, _target, policy = _started_execution(tmp_path)
    try:
        physical = _CountingPort(policy)
        services = ExecutionServices(
            owner=owner, event_loop=event_loop, llm_input_port=physical)
        service = services._registered_host_llm_service
        first = _capability(
            execution, service, physical).request(_host_request())
        event_count = len(owner._core.event_store.list_events())
        call_counts = owner._core.event_store.actual_model_call_counts()
        resumed = _CountingPort(policy)

        second = _capability(
            execution, service, resumed).request(_host_request())
        third = _capability(
            execution, service, resumed).request(_host_request())

        assert first == second == third
        assert resumed.calls == 0
        assert len(owner._core.event_store.list_events()) == event_count
        assert owner._core.event_store.actual_model_call_counts() == call_counts
    finally:
        event_loop.close()
