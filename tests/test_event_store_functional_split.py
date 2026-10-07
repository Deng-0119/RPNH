from __future__ import annotations

import inspect
import sqlite3
from types import SimpleNamespace

import pytest

from cpn.rpnh.registry import event_store
from cpn.rpnh.registry._event_store import (
    accounting,
    backend,
    commit,
    net_lineage,
    proposal,
    provenance,
    queries,
    views,
)
from cpn.rpnh.registry._event_store.validation import (
    agent_loop,
    firing,
    historical,
    native_resume,
    operation,
    provider,
    resources,
    structural,
)
from cpn.rpnh.registry.event_store import (
    CanonicalView,
    EventStore,
    FiringView,
    ProvisionalObservationView,
    RegistryConflict,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import PendingEvent
from cpn.rpnh.registry.schema_catalog import SchemaCatalog


def test_view_types_keep_one_identity_across_compatibility_paths() -> None:
    assert views.CanonicalView is CanonicalView
    assert views.FiringView is FiringView
    assert views.ProvisionalObservationView is ProvisionalObservationView


def test_event_store_facade_delegates_with_the_same_store(monkeypatch) -> None:
    store = object.__new__(EventStore)
    marker = object()
    calls: list[tuple[str, object, tuple[object, ...]]] = []

    def record(name, result):
        def implementation(received_store, *args, **kwargs):
            assert received_store is store
            calls.append((name, received_store, (*args, kwargs)))
            return result

        return implementation

    monkeypatch.setattr(backend, "get_meta", record("backend", marker))
    monkeypatch.setattr(queries, "list_events", record("queries", (marker,)))
    monkeypatch.setattr(views, "canonical_view", record("views", marker))
    monkeypatch.setattr(
        provenance,
        "require_canonical_producer",
        record("provenance", marker),
    )
    monkeypatch.setattr(
        accounting,
        "actual_model_call_counts",
        record("accounting", (3, 1)),
    )

    assert store.get_meta("key") is marker
    assert store.list_events(after_ordinal=7) == (marker,)
    assert store.canonical_view(through_ordinal=9) is marker
    assert store.require_canonical_producer("resource", "producer") is marker
    assert store.actual_model_call_counts() == (3, 1)
    assert [name for name, _store, _arguments in calls] == [
        "backend",
        "queries",
        "views",
        "provenance",
        "accounting",
    ]


def test_net_lineage_facade_preserves_explicit_sqlite_snapshot(
        monkeypatch, tmp_path) -> None:
    store = object.__new__(EventStore)
    catalog = object()
    net_ref = object()
    database = sqlite3.connect(tmp_path / "snapshot.sqlite")
    marker = object()

    def validate(received_store, received_catalog, received_ref, *, _db=None):
        assert received_store is store
        assert received_catalog is catalog
        assert received_ref is net_ref
        assert _db is database
        return marker

    monkeypatch.setattr(net_lineage, "validate_registered_net_closure", validate)
    try:
        assert event_store.validate_registered_net_closure(
            store, catalog, net_ref, _db=database,
        ) is marker
    finally:
        database.close()


def test_extracted_backend_and_empty_views_keep_existing_behavior(tmp_path) -> None:
    store = EventStore(tmp_path / "registry.sqlite", SchemaCatalog())

    assert store.writer_epoch == 1
    assert store.get_or_create_meta("split-test", "value") == "value"
    assert store.get_meta("split-test") == "value"
    assert store.list_events() == ()
    assert store.object_rows() == ()
    assert store.relation_rows() == ()
    assert store.outbox_rows() == ()
    assert store.canonical_view() == CanonicalView(through_ordinal=0)
    assert store.first_and_last_events() == (None, None)
    assert not store.has_event_idempotency_prefix("missing:")


def _publish_empty_transaction(store: EventStore, *, key: str):
    task_id = new_id("task")
    transaction_id = new_id("transaction")
    stream_id = f"transaction:{transaction_id}"
    event = PendingEvent(
        event_type="transaction_committed/v1",
        criticality="authoritative",
        stream_id=stream_id,
        aggregate_id=str(transaction_id),
        aggregate_type="transaction",
        idempotency_key=key,
        command_id=key,
        payload={
            "object_count": 0,
            "relation_count": 0,
            "fact_count": 0,
        },
        payload_schema_ref="registry_v1/transaction_committed/v1",
    )
    result = store.publish_batch(
        task_id=task_id,
        branch_id="main",
        task_round_id=None,
        net_instance_id=None,
        transaction_id=transaction_id,
        idempotency_key=key,
        writer_epoch=1,
        objects=(),
        events=(event,),
        relations=(),
        expected_heads={stream_id: 0},
    )
    return transaction_id, result


def test_indexed_event_bounds_and_idempotency_prefix_preserve_facts(
        tmp_path) -> None:
    store = EventStore(tmp_path / "registry.sqlite", SchemaCatalog())
    _first_transaction, first = _publish_empty_transaction(
        store, key="indexed-prefix:first")
    _second_transaction, second = _publish_empty_transaction(
        store, key="other-key")

    first_event, last_event = store.first_and_last_events()
    assert first_event is not None
    assert last_event is not None
    assert first_event.event_id == first[0].event_id
    assert last_event.event_id == second[-1].event_id
    assert store.has_event_idempotency_prefix("indexed-prefix:")
    assert store.has_event_idempotency_prefix("other-key")
    assert not store.has_event_idempotency_prefix("indexed-prefix:missing")
    with pytest.raises(ValueError, match="prefix must be nonempty"):
        store.has_event_idempotency_prefix("")


def test_publish_batch_facade_delegates_complete_command(monkeypatch) -> None:
    store = object.__new__(EventStore)
    marker = (object(),)
    received = {}

    def publish(received_store, **kwargs):
        assert received_store is store
        received.update(kwargs)
        return marker

    monkeypatch.setattr(commit, "publish_batch", publish)
    task_id = new_id("task")
    transaction_id = new_id("transaction")
    assert store.publish_batch(
        task_id=task_id,
        branch_id="main",
        task_round_id=None,
        net_instance_id=None,
        transaction_id=transaction_id,
        idempotency_key="delegate",
        writer_epoch=3,
        objects=(),
        events=(object(),),
        relations=(),
        expected_heads={"stream": 2},
    ) is marker
    assert received["task_id"] == task_id
    assert received["transaction_id"] == transaction_id
    assert received["expected_heads"] == {"stream": 2}
    assert received["firing_publications"] == ()


def test_domain_validator_facade_hooks_delegate_without_new_snapshot(
        monkeypatch) -> None:
    store = object.__new__(EventStore)
    database = object()
    calls = []

    def resource(received_store, received_db, events, **kwargs):
        calls.append(("resource", received_store, received_db, events, kwargs))

    def settlement(received_db, objects, events):
        calls.append(("firing", received_db, objects, events))

    def materialization(objects, events):
        calls.append(("provider", objects, events))

    def authoritative(received_store, received_db, **kwargs):
        calls.append(("authoritative", received_store, received_db, kwargs))

    def budget(received_db, objects, events):
        calls.append(("budget", received_db, objects, events))

    def lifecycle(received_db, events):
        calls.append(("lifecycle", received_db, events))

    monkeypatch.setattr(
        resources, "validate_petri_firing_resource_access", resource)
    monkeypatch.setattr(
        firing, "validate_firing_resource_settlement_atomicity", settlement)
    monkeypatch.setattr(
        provider, "validate_provider_materialization_atomicity",
        materialization,
    )
    monkeypatch.setattr(
        firing, "validate_authoritative_references", authoritative)
    monkeypatch.setattr(provider, "validate_llm_model_call_budget", budget)
    monkeypatch.setattr(firing, "validate_lifecycle", lifecycle)

    store._validate_petri_firing_resource_access(
        database, (), task_id=new_id("task"), net_instance_id=None,
        writer_epoch=4,
    )
    store._validate_firing_resource_settlement_atomicity(database, (), ())
    store._validate_provider_materialization_atomicity((), ())
    store._validate_authoritative_references(
        database,
        task_id=new_id("task"),
        branch_id="main",
        task_round_id=None,
        net_instance_id=None,
        transaction_id=new_id("transaction"),
        idempotency_key="validation-delegate",
        transaction_writer_epoch=4,
        objects=(),
        events=(),
        relations=(),
    )
    store._validate_llm_model_call_budget(database, (), ())
    store._validate_lifecycle(database, ())

    assert [call[0] for call in calls] == [
        "resource", "firing", "provider", "authoritative", "budget",
        "lifecycle",
    ]
    assert calls[0][1:3] == (store, database)
    assert calls[1][1] is database
    assert calls[3][1:3] == (store, database)
    assert calls[4][1] is database
    assert calls[5][1] is database


def test_agent_loop_delegation_preserves_existing_callbacks(monkeypatch) -> None:
    store = object.__new__(EventStore)
    database = object()
    received = {}

    def validate(received_db, objects, events, **kwargs):
        received.update(kwargs)
        assert received_db is database
        assert objects == ()
        assert events == ()

    monkeypatch.setattr(agent_loop, "validate_agent_loop_atomicity", validate)
    store._validate_agent_loop_atomicity(
        database, (), (), writer_epoch=7)

    assert received["writer_epoch"] == 7
    assert received["validate_waiting_resource_grant_atomicity"] is (
        EventStore._validate_waiting_resource_grant_atomicity)
    assert received["is_parent_internal_leaf_kb_transaction"] is (
        EventStore._is_parent_internal_leaf_kb_transaction)
    assert received["registered_turn_budget_extension"].__self__ is store
    assert received["registered_operation_config"].__self__ is store


def test_commit_uses_one_connection_and_preserves_validator_order(
        monkeypatch, tmp_path) -> None:
    class RecordingStore(EventStore):
        def __init__(self, *args, **kwargs):
            self.record_connections = False
            self.connection_count = 0
            self.statements = []
            super().__init__(*args, **kwargs)

        def connect(self):
            database = super().connect()
            if self.record_connections:
                self.connection_count += 1
                database.set_trace_callback(self.statements.append)
            return database

    store = RecordingStore(tmp_path / "registry.sqlite", SchemaCatalog())
    store.record_connections = True
    order = []
    monkeypatch.setattr(
        store, "_validate_petri_firing_resource_access",
        lambda *args, **kwargs: order.append("resource"),
    )
    monkeypatch.setattr(
        store, "_validate_agent_loop_atomicity",
        lambda *args, **kwargs: order.append("agent_loop"),
    )
    monkeypatch.setattr(
        store, "_validate_firing_resource_settlement_atomicity",
        lambda *args, **kwargs: order.append("firing"),
    )
    monkeypatch.setattr(
        store, "_validate_provider_materialization_atomicity",
        lambda *args, **kwargs: order.append("provider"),
    )
    monkeypatch.setattr(
        store, "_validate_authoritative_references",
        lambda *args, **kwargs: order.append("authoritative"),
    )
    monkeypatch.setattr(
        store, "_validate_llm_model_call_budget",
        lambda *args, **kwargs: order.append("budget"),
    )
    monkeypatch.setattr(
        store, "_validate_lifecycle",
        lambda *args, **kwargs: order.append("lifecycle"),
    )

    _transaction_id, result = _publish_empty_transaction(
        store, key="ordered-commit")

    assert len(result) == 1
    assert store.connection_count == 1
    assert [statement for statement in store.statements
            if statement == "BEGIN IMMEDIATE"] == ["BEGIN IMMEDIATE"]
    assert order == [
        "resource", "agent_loop", "firing", "provider",
        "authoritative", "budget", "lifecycle",
    ]
    event_insert = next(
        index for index, statement in enumerate(store.statements)
        if statement.lstrip().startswith("INSERT INTO events"))
    terminal_update = next(
        index for index, statement in enumerate(store.statements)
        if statement.startswith("UPDATE transactions SET status="))
    outbox_insert = next(
        index for index, statement in enumerate(store.statements)
        if statement.startswith("INSERT INTO outbox"))
    sqlite_commit = max(
        index for index, statement in enumerate(store.statements)
        if statement == "COMMIT")
    assert event_insert < terminal_update < outbox_insert < sqlite_commit
    assert len(store.outbox_rows()) == 1


def test_validator_failure_rolls_back_transaction_and_outbox(
        monkeypatch, tmp_path) -> None:
    store = EventStore(tmp_path / "registry.sqlite", SchemaCatalog())
    reached = []
    monkeypatch.setattr(
        store, "_validate_petri_firing_resource_access",
        lambda *args, **kwargs: reached.append("resource"),
    )
    monkeypatch.setattr(
        store, "_validate_agent_loop_atomicity",
        lambda *args, **kwargs: reached.append("agent_loop"),
    )
    monkeypatch.setattr(
        store, "_validate_firing_resource_settlement_atomicity",
        lambda *args, **kwargs: reached.append("firing"),
    )
    monkeypatch.setattr(
        store, "_validate_provider_materialization_atomicity",
        lambda *args, **kwargs: reached.append("provider"),
    )

    def reject(*args, **kwargs):
        reached.append("authoritative")
        raise RegistryConflict("focused rollback")

    monkeypatch.setattr(store, "_validate_authoritative_references", reject)
    with pytest.raises(RegistryConflict, match="focused rollback"):
        _publish_empty_transaction(store, key="rollback")

    assert reached == [
        "resource", "agent_loop", "firing", "provider", "authoritative",
    ]
    assert store.list_events() == ()
    assert store.outbox_rows() == ()
    with store.connect() as database:
        assert database.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0] == 0


def test_proposal_context_reuses_one_snapshot_local_visibility_cache(
        tmp_path) -> None:
    store = EventStore(tmp_path / "registry.sqlite", SchemaCatalog())
    with store.connect() as database:
        statements = []
        database.set_trace_callback(statements.append)
        context = proposal.ProposalContext(database, (), (), ())
        assert context.db is database
        assert context.persisted_member_visible("object", "missing") is False
        assert context._member_visibility == {("object", "missing"): False}
        assert context.persisted_member_visible("object", "missing") is False
        assert sum(
            "WHERE value.version_id='missing'" in statement
            for statement in statements
        ) == 1


def test_authoritative_domain_functions_are_real_owners() -> None:
    owners = (
        (provider.require_canonical_provider_lineage,
         "canonical_provider_lineage_record"),
        (resources.validate_resource_objects, "resource_version/v1"),
        (operation.validate_operation_contract_objects,
         "operation_binding/v1"),
        (agent_loop.validate_current_invocation_authority,
         "agent_action/v2"),
        (native_resume.validate_native_resume_closure,
         "transition_firing_superseded_by_native_resume/v1"),
        (historical.historical_validate_fault_mechanical_event_closure,
         "fault_mechanical_firing_admitted/v1"),
        (structural.validate_structural_growth_pair_cas,
         "structural growth"),
    )
    for function, contract_marker in owners:
        source = inspect.getsource(function)
        assert function.__module__ != firing.__name__
        assert len(source.splitlines()) > 40
        assert contract_marker in source

    coordinator = inspect.getsource(firing.validate_authoritative_references)
    assert len(coordinator.splitlines()) < 60
    assert "validate_provider_provenance(context)" in coordinator
    assert "validate_resource_objects(context)" in coordinator
    assert "validate_operation_contract_objects(context)" in coordinator
    assert "validate_current_invocation_authority" not in coordinator
    assert "historical_validate_fault_mechanical_event_closure" not in coordinator
    assert max(
        len(inspect.getsource(module).splitlines())
        for module in (
            firing, provider, resources, agent_loop, operation,
            native_resume, historical, structural,
        )
    ) < 3_000


def test_transaction_validation_context_has_snapshot_state_only() -> None:
    source = inspect.getsource(proposal.ProposalContext)
    assert proposal.TransactionValidationContext is proposal.ProposalContext
    assert "sqlite3.connect" not in source
    assert ".commit(" not in source
    assert "_member_visibility" in source
    assert "_version_metadata" in source
    assert "pending_firing_claims" in source


def test_authoritative_coordinator_preserves_domain_and_event_order(
        monkeypatch) -> None:
    order: list[str] = []
    regular = SimpleNamespace(event_type="ordinary/v1", payload={})
    terminal = SimpleNamespace(
        event_type="operation_terminal_ready/v1", payload={})

    class Context:
        def __init__(self, db, objects, events, relations, **kwargs):
            self.events = events
            self.native_resume_superseded_firing_ids = set()

        def validate_reference_visibility(self):
            order.append("visibility")

    monkeypatch.setattr(
        proposal, "TransactionValidationContext", Context)
    monkeypatch.setattr(
        provider, "validate_provider_provenance",
        lambda context: order.append("provider-provenance"))
    monkeypatch.setattr(
        provider, "validate_attempt_response_boundary",
        lambda context: order.append("attempt-response"))
    monkeypatch.setattr(
        operation, "validate_operation_contract_objects",
        lambda context: order.append("operation-contracts"))
    monkeypatch.setattr(
        native_resume, "validate_native_resume_closure",
        lambda context: order.append("native-resume"))
    monkeypatch.setattr(
        resources, "validate_resource_objects",
        lambda context: order.append("resource-objects"))
    monkeypatch.setattr(
        structural, "validate_structural_growth_pair_cas",
        lambda context: order.append("structural"))
    monkeypatch.setattr(
        firing, "validate_firing_event",
        lambda context, pending: order.append(
            f"firing:{pending.event_type}"))
    monkeypatch.setattr(
        provider, "validate_provider_pre_resource_event",
        lambda context, pending: order.append("provider-pre-resource"))
    monkeypatch.setattr(
        resources, "validate_resource_event",
        lambda context, pending: order.append("resource-event"))
    monkeypatch.setattr(
        provider, "validate_provider_attempt_event",
        lambda context, pending: order.append("provider-attempt"))
    monkeypatch.setattr(
        operation, "validate_operation_event",
        lambda context, pending: order.append("operation-event"))
    monkeypatch.setattr(
        provider, "validate_provider_outcome_event",
        lambda context, pending: order.append("provider-outcome"))

    # This is a coordinator ordering fixture, not a fabricated full validation
    # context. Mock all cross-domain callbacks and assert the retained hooks.
    from cpn.rpnh.registry import execution_child_closure, observer_access
    monkeypatch.setattr(execution_child_closure, "validate_child_seal_publication",
        lambda context: order.append("child-seal"))
    monkeypatch.setattr(observer_access, "validate_observer_publications",
        lambda context: order.append("observer-access"))
    monkeypatch.setattr(provider, "validate_registered_host_llm_event",
        lambda context, pending: order.append("registered-host-llm"))

    firing.validate_authoritative_references(
        object(), object(), task_id=new_id("task"), branch_id="main",
        task_round_id=None, net_instance_id=None,
        transaction_id=new_id("transaction"), idempotency_key="ordered",
        transaction_writer_epoch=1, objects=(),
        events=(regular, terminal), relations=())

    assert order == [
        "visibility", "child-seal", "observer-access", "provider-provenance", "attempt-response",
        "operation-contracts", "native-resume", "resource-objects",
        "structural", "firing:ordinary/v1",
        "provider-pre-resource", "resource-event", "registered-host-llm", "provider-attempt",
        "operation-event", "provider-outcome",
        "firing:operation_terminal_ready/v1",
    ]


def test_extracted_domain_failure_rolls_back_commit(monkeypatch, tmp_path) -> None:
    store = EventStore(tmp_path / "registry.sqlite", SchemaCatalog())

    def reject(context):
        assert context.db.in_transaction
        raise RegistryConflict("provider domain rollback")

    monkeypatch.setattr(provider, "validate_provider_provenance", reject)
    with pytest.raises(RegistryConflict, match="provider domain rollback"):
        _publish_empty_transaction(store, key="domain-rollback")

    assert store.list_events() == ()
    assert store.outbox_rows() == ()
    with store.connect() as database:
        assert database.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0] == 0
