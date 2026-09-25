from __future__ import annotations

import inspect
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cpn.rpnh import budgets
from cpn.rpnh.registry import (
    provider_calls,
    provider_execution,
    resource_service,
)
from cpn.rpnh.registry._provider_calls import attempts, calls, legacy, recovery
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.provider_execution import ProviderExecution
from cpn.rpnh.registry.resources import RegistryHead, ResourceVersionRef


def test_provider_ledger_facade_preserves_identity_and_signatures() -> None:
    ledger = provider_calls.ProviderAttemptLedger(object())
    assert provider_calls.ProviderAttemptLedger.__module__ == (
        "cpn.rpnh.registry.provider_calls")
    assert provider_calls.LLMCallV2.__module__ == (
        "cpn.rpnh.registry.provider_calls")
    assert provider_calls.ProviderAttemptV2.__module__ == (
        "cpn.rpnh.registry.provider_calls")
    assert provider_calls.materialize_call_v2_publication is (
        calls.materialize_call_v2_publication)
    assert provider_execution.materialize_attempt_v2_reservation is (
        attempts.materialize_attempt_v2_reservation)
    assert vars(ledger) == {"service": ledger.service}

    owners = {
        calls: ("create_call_v2",),
        attempts: (
            "reserve_v2", "complete_v2", "record_materialization",
            "reconcile_v2", "_revalidate_call_v2_context",
            "_provider_request_schema", "_canonical_budget_scope", "_append",
            "dispatch_started", "submission_permitted",
            "submission_not_permitted", "submission_unknown",
            "close_before_dispatch",
        ),
        recovery: (
            "reconcile_interrupted_attempts_on_resume",
            "_hydrate_recovery_attempt",
        ),
        legacy: (
            "_historical_create_call", "_historical_revalidate_call_context",
            "_historical_reserve", "_historical_cancelled_before_submission",
            "_historical_failed", "_historical_outcome_unknown",
            "_historical_prove_not_submitted", "_historical_prove_submitted",
            "_historical_cancelled_after_submission",
            "_historical_reconcile_failed",
            "_historical_reconcile_cancelled_after_submission",
            "_require_proof", "_historical_adoption_event",
            "_historical_append_adoption", "_historical_adopt",
            "_historical_candidate_not_adopted_event",
            "_historical_append_candidate_not_adopted",
            "_historical_candidate_not_adopted", "_historical_fail_call",
        ),
    }
    for owner, names in owners.items():
        for name in names:
            assert inspect.signature(
                getattr(provider_calls.ProviderAttemptLedger, name)
            ) == inspect.signature(getattr(owner, name))
    for name in (
            "reserve_provider_attempt_v2",
            "commit_provider_raw_response_v2"):
        assert inspect.signature(getattr(ProviderExecution, name)) == (
            inspect.signature(getattr(attempts, name)))


def test_materializer_facade_reexports_are_cycle_safe() -> None:
    code = "\n".join((
        "from cpn.rpnh.registry._provider_calls.calls import "
        "materialize_call_v2_publication as call_internal",
        "from cpn.rpnh.registry.provider_calls import "
        "materialize_call_v2_publication as call_facade",
        "assert call_facade is call_internal",
        "from cpn.rpnh.registry.provider_execution import "
        "materialize_attempt_v2_reservation as attempt_facade",
        "from cpn.rpnh.registry._provider_calls.attempts import "
        "materialize_attempt_v2_reservation as attempt_internal",
        "assert attempt_facade is attempt_internal",
    ))
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=".", check=False,
        capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_current_v2_facades_delegate_to_the_same_bound_owner(
        monkeypatch: pytest.MonkeyPatch) -> None:
    core = object()
    ledger = provider_calls.ProviderAttemptLedger(core)
    execution = ProviderExecution(core)
    call_marker = object()
    attempt_marker = object()
    observed = []

    def create(actual_ledger, **kwargs):
        observed.append(("call", actual_ledger, kwargs))
        return call_marker

    def reserve(actual_execution, **kwargs):
        observed.append(("attempt", actual_execution, kwargs))
        return attempt_marker

    monkeypatch.setattr(calls, "create_call_v2", create)
    monkeypatch.setattr(attempts, "reserve_provider_attempt_v2", reserve)

    values = {name: object() for name in (
        "context", "loop_ref", "request_resource_ref",
        "terminal_delivery_ref", "semantic_prompt_resource_ref",
        "llm_execution_target_ref", "transport_contract_ref",
        "tool_catalog_ref", "prior_turn_refs",
    )}
    assert ledger.create_call_v2(
        context=values["context"], loop_ref=values["loop_ref"],
        turn_sequence=3,
        request_resource_ref=values["request_resource_ref"],
        terminal_delivery_ref=values["terminal_delivery_ref"],
        semantic_prompt_resource_ref=values["semantic_prompt_resource_ref"],
        llm_execution_target_ref=values["llm_execution_target_ref"],
        backend="backend", model="model",
        transport_contract_ref=values["transport_contract_ref"],
        interaction_protocol_ref="interaction/v1",
        response_adapter_ref="adapter/v1",
        tool_catalog_ref=values["tool_catalog_ref"],
        prior_turn_refs=values["prior_turn_refs"],
        timeout_seconds=7, max_response_bytes=11,
        call_id=None, idempotency_key="call-key",
    ) is call_marker
    assert execution.reserve_provider_attempt_v2(
        context=values["context"], call=call_marker,
        prior_attempt=None, idempotency_key="attempt-key",
    ) is attempt_marker

    assert observed[0][0:2] == ("call", ledger)
    assert observed[0][2]["idempotency_key"] == "call-key"
    assert observed[1][0:2] == ("attempt", execution)
    assert observed[1][2] == {
        "context": values["context"],
        "call": call_marker,
        "prior_attempt": None,
        "idempotency_key": "attempt-key",
    }
    assert execution._ProviderExecution__core is core
    assert vars(execution) == {"_ProviderExecution__core": core}


def test_substantive_owners_keep_transactions_out_of_facades() -> None:
    call_source = inspect.getsource(calls.create_call_v2)
    assert call_source.count("self.service.begin(") == 1
    assert call_source.count("tx.commit()") == 1
    assert ".begin(" not in inspect.getsource(
        provider_calls.ProviderAttemptLedger.create_call_v2)

    reservation_source = inspect.getsource(
        attempts._ProviderExecutionCommands.reserve_provider_attempt_v2)
    assert reservation_source.count("self.__core.begin(") == 1
    assert reservation_source.count("tx.commit()") == 1
    assert ".begin(" not in inspect.getsource(
        ProviderExecution.reserve_provider_attempt_v2)

    assert legacy._historical_create_call.__module__.endswith(
        "._provider_calls.legacy")
    assert recovery.reconcile_interrupted_attempts_on_resume.__module__.endswith(
        "._provider_calls.recovery")


def test_interrupted_v2_recovery_closes_exact_attempts_without_retransmission(
        monkeypatch: pytest.MonkeyPatch) -> None:
    reserved_id = new_id("provider_attempt")
    reserved_version = new_id("provider_attempt_version")
    dispatch_id = new_id("provider_attempt")
    dispatch_version = new_id("provider_attempt_version")
    reserved_event = SimpleNamespace(
        aggregate_id=str(reserved_id),
        event_type="provider_attempt_reserved/v1",
        event_id=new_id("event"), payload={})
    dispatch_event = SimpleNamespace(
        aggregate_id=str(dispatch_id),
        event_type="provider_attempt_dispatch_started/v2",
        event_id=new_id("event"), payload={})

    class EventStore:
        def list_events(self):
            return (reserved_event, dispatch_event)

        def object_rows(self):
            return (
                {"logical_id": str(reserved_id),
                 "object_type": "provider_attempt_spec/v1",
                 "version_id": str(reserved_version)},
                {"logical_id": str(dispatch_id),
                 "object_type": "provider_attempt_spec/v1",
                 "version_id": str(dispatch_version)},
            )

    versions = {
        reserved_version: SimpleNamespace(metadata={
            "provider_attempt_version_id": str(reserved_version)}),
        dispatch_version: SimpleNamespace(metadata={
            "provider_attempt_version_id": str(dispatch_version)}),
    }
    service = SimpleNamespace(
        event_store=EventStore(),
        get_version=lambda version_id: versions[version_id],
    )
    ledger = provider_calls.ProviderAttemptLedger(service)
    closed = []

    def hydrate(self, attempt_ref, metadata):
        assert metadata["provider_attempt_version_id"] == str(
            attempt_ref.version_id)
        return SimpleNamespace(ref=attempt_ref)

    def close(self, attempt, **kwargs):
        closed.append(("reserved", attempt.ref, kwargs))

    def not_permitted(self, attempt, **kwargs):
        closed.append(("dispatch", attempt.ref, kwargs))

    monkeypatch.setattr(
        provider_calls.ProviderAttemptLedger,
        "_hydrate_recovery_attempt", hydrate)
    monkeypatch.setattr(
        provider_calls.ProviderAttemptLedger, "close_before_dispatch", close)
    monkeypatch.setattr(
        provider_calls.ProviderAttemptLedger,
        "submission_not_permitted", not_permitted)

    def no_retransmission(*args, **kwargs):
        raise AssertionError("reserved/dispatch recovery must not publish or resend")

    reconciled = ledger.reconcile_interrupted_attempts_on_resume(
        publish_permitted_attempt_diagnostic=no_retransmission)

    assert reconciled == tuple(sorted((str(reserved_id), str(dispatch_id))))
    assert {item[0] for item in closed} == {"reserved", "dispatch"}
    reserved = next(item for item in closed if item[0] == "reserved")
    dispatched = next(item for item in closed if item[0] == "dispatch")
    assert reserved[2]["closed_reason"] == "pre_dispatch_runtime_unavailable"
    assert reserved[2]["require_current_writer"] is False
    assert dispatched[2]["dispatch_event_id"] == dispatch_event.event_id
    assert dispatched[2]["closed_reason"] == "submission_permit_commit_failed"


def test_actual_v2_call_and_request_materialization_resolve_registry_siblings(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def version(entity_type, logical_kind, version_kind):
        return VersionRef(
            entity_type, new_id(logical_kind), new_id(version_kind))

    def resource():
        return ResourceVersionRef(
            new_id("resource"), new_id("resource_version"))

    invocation_ref = version(
        "invocation/v1", "invocation", "invocation_version")
    binding_ref = version(
        "operation_binding/v1", "operation_binding",
        "operation_binding_version")
    operation_spec_ref = version(
        "operation_spec/v1", "operation_spec", "operation_spec_version")
    firing_ref = version(
        "transition_firing/v1", "transition_firing",
        "transition_firing_version")
    loop_ref = version(
        "agent_loop/v1", "agent_loop", "agent_loop_version")
    delivery_ref = version(
        "resource_delivery/v1", "resource_delivery",
        "resource_delivery_version")
    manifest_ref = version(
        "task_recovery_manifest/v1", "task", "resource_version")
    request_ref = resource()
    prompt_ref = resource()
    target_ref = resource()
    transport_ref = resource()
    catalog_ref = resource()
    schema_source_ref = resource()
    request_schema = "registry_v1/test_provider_request/v1"

    context = SimpleNamespace(
        invocation_ref=invocation_ref,
        operation_binding_ref=binding_ref,
        own_transition_firing_ref=firing_ref,
        budget_scope="model_call",
        finalization_scope=None,
        origin="petri_operation",
        parent_invocation_ref=None,
        accounting_parent_invocation_ref=invocation_ref,
    )

    class EventStore:
        def __init__(self):
            self.rows = {}
            self.events = []

        def firing_view(self, **kwargs):
            return ("view", kwargs)

        def object_row(self, version_id):
            return self.rows.get(version_id)

        def list_events_by_aggregate(self, aggregate_id, *, event_types=()):
            return tuple(
                event for event in self.events
                if event.aggregate_id == aggregate_id
                and (not event_types or event.event_type in event_types))

    class Transaction:
        def __init__(self, service):
            self.service = service
            self.prewrites = []
            self.relations = []
            self.events = []
            self.committed = False

        def prewrite(self, **kwargs):
            self.prewrites.append(kwargs)
            self.service.objects[kwargs["version_id"]] = SimpleNamespace(
                metadata=dict(kwargs["metadata"]))
            self.service.event_store.rows[kwargs["version_id"]] = {
                "object_type": kwargs["object_type"],
                "logical_id": str(kwargs["logical_id"]),
                "metadata_json": json.dumps(kwargs["metadata"]),
            }

        def relate(self, relation, **kwargs):
            self.relations.append((relation, kwargs))

        def append(self, event):
            self.events.append(event)

        def commit(self):
            self.committed = True
            self.service.event_store.events.extend(self.events)
            return tuple(self.events)

    event_store = EventStore()
    rows = {
        request_ref.resource_version_id: {
            "object_type": "resource_version/v1",
            "logical_id": str(request_ref.resource_id),
            "metadata_json": json.dumps({
                "content_schema_ref": request_schema}),
        },
        delivery_ref.version_id: {
            "object_type": "resource_delivery/v1",
            "logical_id": str(delivery_ref.entity_id),
            "metadata_json": json.dumps({
                "state": "acknowledged",
                "boundary": "llm_prompt",
                "resource_ref": provider_calls._resource_payload(request_ref),
                "context_ref": provider_calls._ref_payload(invocation_ref),
                "authorization_ref": provider_calls._ref_payload(binding_ref),
            }),
        },
    }
    for exact_ref in (prompt_ref, target_ref, transport_ref, catalog_ref):
        rows[exact_ref.resource_version_id] = {
            "object_type": "resource_version/v1",
            "logical_id": str(exact_ref.resource_id),
            "metadata_json": "{}",
        }
    event_store.rows.update(rows)

    versions = {
        binding_ref.version_id: SimpleNamespace(
            object_type="operation_binding/v1",
            logical_id=binding_ref.entity_id,
            metadata={
                "operation_spec_ref": provider_calls._ref_payload(
                    operation_spec_ref)}),
        operation_spec_ref.version_id: SimpleNamespace(
            object_type="operation_spec/v1",
            logical_id=operation_spec_ref.entity_id,
            metadata={"implementation_contracts": {
                "provider_request_schema": request_schema}}),
        manifest_ref.version_id: SimpleNamespace(
            object_type=manifest_ref.entity_type,
            logical_id=manifest_ref.entity_id,
            metadata={}),
    }

    class Service:
        def __init__(self):
            self.event_store = event_store
            self.objects = {}
            self.transactions = []
            self.object_store = SimpleNamespace(
                read_registered=lambda prepared: b"{}")
            self.catalog = SimpleNamespace(
                validate_schema_ref=lambda schema, value: None)

        def get_version(self, version_id):
            return versions[version_id]

        def verify_registered_content_schema_ref(self, *args, **kwargs):
            return SimpleNamespace(schema_id=request_schema)

        def recovery_manifest_ref(self):
            return manifest_ref

        def begin(self, **kwargs):
            transaction = Transaction(self)
            self.transactions.append(transaction)
            return transaction

    service = Service()

    class Lifecycle:
        def __init__(self, actual_service):
            assert actual_service is service

        def revalidate_io(self, actual_context, *, boundary):
            assert actual_context is context
            assert boundary in {
                "agent-llm-call-creation", "provider-attempt-reservation"}

    prepared_request = SimpleNamespace(
        metadata={
            "content_schema_authority_ref": (
                provider_calls._resource_payload(schema_source_ref))},
        media_type="application/json",
    )

    class Kernel:
        def __init__(self, actual_service):
            assert actual_service is service

        def _exact_object_for_view(self, view, ref, expected_type=None):
            assert ref == loop_ref
            return SimpleNamespace(metadata={
                "invocation_ref": provider_calls._ref_payload(invocation_ref),
                "operation_binding_ref": provider_calls._ref_payload(
                    binding_ref),
            })

        def _firing_prepared(self, actual_context, ref, *, view=None):
            assert actual_context is context
            assert ref == request_ref
            return prepared_request

        def _binding_allows(self, *args, **kwargs):
            return True

        def _exact_object(self, ref, *, expected_type=None):
            return service.objects[ref.version_id]

        def _head(self):
            return RegistryHead(1, 1, 1, {})

    monkeypatch.setattr(calls, "InvocationLifecycle", Lifecycle)
    monkeypatch.setattr(attempts, "InvocationLifecycle", Lifecycle)
    monkeypatch.setattr(resource_service, "_ResourceServiceKernel", Kernel)
    monkeypatch.setattr(
        budgets, "validate_budget_binding",
        lambda manifest, binding: {
            "budget_scope": "model_call", "finalization_scope": None})

    ledger = provider_calls.ProviderAttemptLedger(service)
    call = ledger.create_call_v2(
        context=context,
        loop_ref=provider_calls._ref_payload(loop_ref),
        turn_sequence=0,
        request_resource_ref=request_ref,
        terminal_delivery_ref=delivery_ref,
        semantic_prompt_resource_ref=prompt_ref,
        llm_execution_target_ref=target_ref,
        backend="backend", model="model",
        transport_contract_ref=transport_ref,
        interaction_protocol_ref="interaction/v1",
        response_adapter_ref="adapter/v1",
        tool_catalog_ref=catalog_ref,
        prior_turn_refs=(), timeout_seconds=7, max_response_bytes=4096,
        idempotency_key="actual-call",
    )
    assert isinstance(call, provider_calls.LLMCallV2)
    assert service.transactions[0].committed is True
    assert len(service.transactions[0].relations) == 5

    attempt = provider_calls.ProviderAttemptV2(
        new_id("provider_attempt"), new_id("provider_attempt_version"),
        call, "model_call", None)
    service.objects[attempt.version_id] = SimpleNamespace(metadata={
        "llm_call_ref": provider_calls._ref_payload(call.ref),
        "invocation_ref": provider_calls._ref_payload(invocation_ref),
    })
    monkeypatch.setattr(
        provider_calls.ProviderAttemptLedger,
        "_revalidate_call_v2_context",
        lambda self, actual_call, **kwargs: context)

    materialized = ledger.record_materialization(
        context=context, attempt=attempt, request_payload=b"wire-bytes",
        idempotency_key="actual-materialization")
    assert materialized.request_payload == b"wire-bytes"
    assert materialized.receipt.provider_attempt_ref == attempt.ref
    assert service.transactions[1].committed is True
    assert ledger._canonical_budget_scope(context) == ("model_call", None)


def test_v2_reconciliation_missing_core_command_raises_unknown() -> None:
    def version(entity_type, logical_kind, version_kind):
        return VersionRef(
            entity_type, new_id(logical_kind), new_id(version_kind))

    def resource():
        return ResourceVersionRef(
            new_id("resource"), new_id("resource_version"))

    call = provider_calls.LLMCallV2(
        new_id("llm_call"), new_id("llm_call_version"),
        version("invocation/v1", "invocation", "invocation_version"),
        version(
            "operation_binding/v1", "operation_binding",
            "operation_binding_version"),
        provider_calls._ref_payload(version(
            "agent_loop/v1", "agent_loop", "agent_loop_version")),
        0, resource(),
        version(
            "resource_delivery/v1", "resource_delivery",
            "resource_delivery_version"),
        resource(), resource(), "backend", "model", resource(),
        "interaction/v1", "adapter/v1", resource(), (), 7, 4096)
    attempt = provider_calls.ProviderAttemptV2(
        new_id("provider_attempt"), new_id("provider_attempt_version"),
        call, "model_call", None)
    ledger = provider_calls.ProviderAttemptLedger(SimpleNamespace())

    with pytest.raises(
            provider_calls.ProviderAttemptUnknown,
            match="v2 reconciliation is not integrated"):
        ledger.reconcile_v2(
            attempt,
            proof_ref=version(
                "resource_version/v1", "resource", "resource_version"),
            idempotency_key="missing-reconciliation-command")


def test_legacy_reservation_hydrates_valid_node_limit(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def version(entity_type, logical_kind, version_kind):
        return VersionRef(
            entity_type, new_id(logical_kind), new_id(version_kind))

    invocation_ref = version(
        "invocation/v1", "invocation", "invocation_version")
    binding_ref = version(
        "operation_binding/v1", "operation_binding",
        "operation_binding_version")
    delivery_ref = version(
        "resource_delivery/v1", "resource_delivery",
        "resource_delivery_version")
    node_ref = version(
        "node_declaration/v1", "node",
        "node_declaration_version")
    budget_ref = version(
        "task_recovery_manifest/v1", "task", "resource_version")
    request_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    backend_ref = version(
        "resource_version/v1", "resource", "resource_version")
    digest = "a" * 64
    context = SimpleNamespace(
        invocation_ref=invocation_ref,
        operation_binding_ref=binding_ref,
        context_digest="legacy-context",
        origin="petri_operation",
        accounting_parent_invocation_ref=invocation_ref,
    )
    call = provider_calls.LLMCall(
        new_id("llm_call"), new_id("llm_call_version"), invocation_ref,
        None, context.context_digest, binding_ref, request_ref, delivery_ref)
    authority = provider_calls.ExternallyValidatedProviderRequestAuthority(
        request_ref, invocation_ref, binding_ref)
    binding = {
        "llm_execution_target_ref": provider_calls._resource_payload(
            ResourceVersionRef(backend_ref.entity_id, backend_ref.version_id)),
        "input_binding_refs": [provider_calls._ref_payload(backend_ref)],
        "readable_resource_refs": [provider_calls._ref_payload(backend_ref)],
        "node_ref": provider_calls._ref_payload(node_ref),
    }

    versions = {
        request_ref.resource_version_id: SimpleNamespace(
            object_type="resource_version/v1",
            logical_id=request_ref.resource_id,
            payload_digest=digest,
            metadata={"payload_digest": digest}),
        backend_ref.version_id: SimpleNamespace(
            object_type="resource_version/v1",
            logical_id=backend_ref.entity_id,
            payload_digest=digest,
            metadata={
                "content_schema_ref": "registry_v1/provider_backend_config/v1",
                "payload_digest": digest}),
        delivery_ref.version_id: SimpleNamespace(
            object_type="resource_delivery/v1",
            logical_id=delivery_ref.entity_id,
            metadata={
                "state": "acknowledged", "boundary": "llm_prompt",
                "resource_ref": provider_calls._resource_payload(request_ref),
                "resource_digest": digest}),
        call.version_id: SimpleNamespace(metadata={
            "invocation_version_id": str(invocation_ref.version_id),
            "context_digest": context.context_digest}),
        node_ref.version_id: SimpleNamespace(
            object_type="node_declaration/v1",
            logical_id=node_ref.entity_id,
            metadata={"resource_bounds": {"max_llm_attempts": 1}}),
    }

    class EventStore:
        def object_row(self, version_id):
            if version_id == call.version_id:
                return {
                    "object_type": "llm_call_spec/v1",
                    "logical_id": str(call.call_id),
                }
            return None

        def provider_attempt_rows_for_call(self, call_id, version_id):
            return ()

    class Transaction:
        def __init__(self):
            self.prewrites = []
            self.relations = []
            self.events = []
            self.committed = False

        def prewrite(self, **kwargs):
            self.prewrites.append(kwargs)

        def relate(self, relation, **kwargs):
            self.relations.append((relation, kwargs))

        def append(self, event):
            self.events.append(event)

        def commit(self):
            self.committed = True
            return tuple(self.events)

    transaction = Transaction()
    requested_versions = []

    def get_version(version_id):
        requested_versions.append(version_id)
        return versions[version_id]

    service = SimpleNamespace(
        event_store=EventStore(), get_version=get_version,
        recovery_manifest_ref=lambda: budget_ref,
        writer_epoch=17,
        begin=lambda **kwargs: transaction,
    )
    ledger = provider_calls.ProviderAttemptLedger(service)
    monkeypatch.setattr(
        provider_calls.ProviderAttemptLedger, "_canonical_budget_scope",
        lambda self, actual_context: ("model_call", None))

    attempt = ledger._historical_reserve(
        context=context, call=call, request_authority=authority,
        operation_binding_metadata=binding,
        llm_execution_target_ref=backend_ref,
        request_resource_ref=request_ref,
        terminal_delivery_ref=delivery_ref,
        backend="backend", model="model", transport_kind="subprocess",
        timeout_seconds=7, response_protocol="response/v1",
        request_payload_digest=digest,
        idempotency_key="valid-legacy-node-limit")

    assert isinstance(attempt, provider_calls.ProviderAttempt)
    assert node_ref.version_id in requested_versions
    assert transaction.committed is True
    assert len(transaction.prewrites) == 1
    assert len(transaction.relations) == 5
    assert len(transaction.events) == 1
