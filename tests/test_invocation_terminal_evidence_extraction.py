from types import SimpleNamespace

import cpn.rpnh.registry.invocations as invocations
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef


def _invocation_ref() -> VersionRef:
    return VersionRef(
        "invocation/v1",
        TypedId("invocation", "1" * 32),
        TypedId("invocation_version", "2" * 32),
    )


class _RecordingEmptyEventStore:
    def __init__(self) -> None:
        self.calls = []

    def list_events_by_producer(self, producer_invocation_id):
        self.calls.append(("list_events_by_producer", producer_invocation_id))
        return ()

    def object_rows_by_producer(self, producer_invocation_id, *, object_types):
        self.calls.append((
            "object_rows_by_producer",
            producer_invocation_id,
            object_types,
        ))
        return ()


def test_terminal_evidence_preserves_empty_inventory_query_order() -> None:
    event_store = _RecordingEmptyEventStore()
    lifecycle = invocations.InvocationLifecycle(
        SimpleNamespace(event_store=event_store))
    context = SimpleNamespace(invocation_ref=_invocation_ref())

    assert lifecycle._terminal_descendant_event_ids(context) == ()
    invocation_id = context.invocation_ref.entity_id
    assert event_store.calls == [
        ("list_events_by_producer", invocation_id),
        (
                "object_rows_by_producer",
                invocation_id,
                ("llm_call_spec/v1", "llm_call_spec/v2", "llm_call_spec/v3"),
            ),
        (
            "object_rows_by_producer",
            invocation_id,
                ("provider_attempt_spec/v1",),
            ),
            (
                "object_rows_by_producer",
                invocation_id,
                ("registered_host_llm_attempt/v1",),
            ),
        (
            "object_rows_by_producer",
            invocation_id,
            ("agent_context_compaction/v2",),
        ),
        (
            "object_rows_by_producer",
            invocation_id,
            ("agent_context_compaction/v3",),
        ),
    ]


def test_terminal_evidence_facade_passes_exact_lifecycle_dependencies(
        monkeypatch) -> None:
    event_store = object()
    lifecycle = invocations.InvocationLifecycle(
        SimpleNamespace(event_store=event_store))
    context = SimpleNamespace(invocation_ref=_invocation_ref())
    unknown_ref = VersionRef(
        "provider_submission_unknown/v1",
        TypedId("provider_submission_unknown", "3" * 32),
        TypedId("provider_submission_unknown_version", "4" * 32),
    )
    captured = {}

    def fake_terminal_evidence(actual_context, actual_unknown_ref, **dependencies):
        captured.update(dependencies)
        captured["context"] = actual_context
        captured["unknown_ref"] = actual_unknown_ref
        return ("event-b", "event-a")

    monkeypatch.setattr(
        invocations, "terminal_descendant_event_ids", fake_terminal_evidence)

    assert lifecycle._terminal_descendant_event_ids(
        context, unknown_ref) == ("event-b", "event-a")
    assert captured["context"] is context
    assert captured["unknown_ref"] is unknown_ref
    assert captured["event_store"] is event_store
    assert captured["admission_error"] is invocations.InvocationAdmissionError
    assert captured["require_ref"].__self__ is lifecycle
    assert captured["relation_endpoint"] is lifecycle._relation_endpoint
    assert captured["resolve_agent_turn_acceptance"].__self__ is lifecycle
    assert captured["terminal_call_events"] is lifecycle._TERMINAL_CALL_EVENTS
    assert captured["terminal_attempt_events"] is lifecycle._TERMINAL_ATTEMPT_EVENTS
    assert captured["ref_payload"] is invocations._ref_payload
    assert captured["ref_from_payload"] is invocations._ref_from_payload
