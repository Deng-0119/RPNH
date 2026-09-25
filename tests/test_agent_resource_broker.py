from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cpn.rpnh.firing_resource_access import RegisteredPetriFiringResourceAccess
from cpn.rpnh.harness import Harness, OperationDisposition
from cpn.rpnh.marking import PetriFiringResourceAccess
from cpn.rpnh.registry._registry import _RegistryCore
import cpn.rpnh.registry.agent_resource_broker as broker
from cpn.rpnh.registry.agent_resource_broker import (
    AgentResourceWaitCandidate,
    AgentLoopResourceRequest,
    _base_document,
    _grant_authority,
    _lifecycle_authority,
    _lifecycle_ref,
    _queue_entry,
    _request_identity,
    _subref,
    promote_one_waiting_agent_resource,
)
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operations import OperationExecutionAuthority
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.resources import (
    RegistryHead,
    ResourceVersionRef,
    TransitionFiringAuthority,
)


def _id(kind: str, value: int) -> TypedId:
    return TypedId(kind, f"{value:032x}")  # type: ignore[arg-type]


def _ref(entity_type: str, logical_kind: str, version_kind: str,
         value: int) -> VersionRef:
    return VersionRef(
        entity_type, _id(logical_kind, value), _id(version_kind, value + 100))


def _request() -> tuple[AgentLoopResourceRequest,
                        RegisteredPetriFiringResourceAccess]:
    checkpoint = _ref(
        "marking_checkpoint/v1", "marking_checkpoint",
        "marking_checkpoint_version", 1)
    net = _ref("net_instance/v1", "net_instance", "net_instance_version", 2)
    firing_ref = _ref(
        "transition_firing/v1", "transition_firing",
        "transition_firing_version", 3)
    operation_binding = _ref(
        "operation_binding/v1", "operation_binding",
        "operation_binding_version", 4)
    firing = TransitionFiringAuthority(
        transition_firing_ref=firing_ref,
        transition_id="worker.run",
        activation_ref=None,
        task_ref=_ref("task/v1", "task", "task_version", 5),
        task_branch_ref=_ref(
            "task_branch/v1", "task_branch", "task_branch_version", 6),
        task_round_ref=_ref(
            "task_round/v1", "task_round", "task_round_version", 7),
        net_ref=net,
        plan_ref=_ref("plan_version/v1", "plan", "plan_version", 8),
        node_ref=_ref(
            "node_declaration/v1", "node", "node_declaration_version", 9),
        operation_binding_ref=operation_binding,
        principal_ref=_ref(
            "principal/v1", "principal", "principal_version", 10),
        admission_marking_checkpoint_ref=checkpoint,
        budget_witness_ref=_ref(
            "budget_witness/v1", "snapshot", "snapshot", 11),
        firing_admission_ref=_ref(
            "firing_admission/v1", "firing_admission",
            "firing_admission_version", 12),
        claim_marking_delta_ref=_ref(
            "marking_delta/v1", "marking_delta", "marking_delta_version", 13),
        logical_tau=0,
        attempt_index=1,
        claimed_input_refs=(),
        verified_at_head=RegistryHead(0, 1, 0, {}),
    )
    resource = ResourceVersionRef(_id("resource", 14), _id("resource_version", 114))
    request = AgentLoopResourceRequest(
        firing=firing,
        invocation_ref=_ref(
            "invocation/v1", "invocation", "invocation_version", 15),
        operation_execution_lease_ref=_ref(
            "operation_execution_lease/v1", "operation_execution_lease",
            "operation_execution_lease_version", 16),
        operation_binding_ref=operation_binding,
        agent_loop_ref=_ref(
            "agent_loop/v1", "agent_loop", "agent_loop_version", 17),
        agent_turn_ref=_ref(
            "agent_turn/v1", "agent_turn", "agent_turn_version", 18),
        agent_action_ref=_ref(
            "agent_action/v2", "agent_action", "agent_action_version", 19),
        requester_agent_ref=_ref(
            "agent/v1", "agent", "agent_version", 20),
        resource_ref=resource,
        access_mode="read",
        llm_turns_used=1,
    )
    formal = RegisteredPetriFiringResourceAccess(
        checkpoint_ref=checkpoint,
        net_ref=net,
        access=PetriFiringResourceAccess(
            firing_ref=firing_ref,
            transition_id="worker.run",
            claim_epoch=0,
            lease_pool_place="worker.resources",
            resource_token_ref=_ref(
                "petri_token/v1", "petri_token", "petri_token_version", 21),
            lease_identity_ref=_ref(
                "resource_place/v1", "resource_place",
                "resource_place_version", 22),
            resource_ref=resource,
            access_mode="read",
            input_arc_mode="read",
            output_arc_mode=None,
        ),
    )
    return request, formal


def test_immediate_read_grant_has_exact_formal_evidence(tmp_path) -> None:
    core = _RegistryCore(tmp_path / "run", create=True)
    request, formal = _request()
    lifecycle_id = _request_identity(request)
    request_ref = _subref(lifecycle_id, "request")
    queue_ref = _subref(lifecycle_id, "queue_entry")
    heartbeat_ref = _subref(lifecycle_id, "heartbeat")
    document = _base_document(
        core, request, formal,
        lifecycle_ref=_lifecycle_ref(lifecycle_id, "granted"),
        request_ref=request_ref,
        queue_entry_ref=queue_ref,
        heartbeat_ref=heartbeat_ref,
        observed_at_utc="2026-09-19T00:00:00Z",
        stable_queue=[],
    )
    document.update({
        "state": "granted",
        "grant_ref": _ref_payload(_subref(lifecycle_id, "grant")),
        "lease_ref": _ref_payload(_subref(lifecycle_id, "lease")),
        "wake_reason": "lock_compatible",
        "resource_wait_started_at_utc": None,
        "blocked_started_at_utc": None,
    })

    core.catalog.validate_instance(
        "resource_access_lifecycle/v1", category="object", instance=document)
    lifecycle = _lifecycle_authority(document)
    grant = _grant_authority(lifecycle)

    assert grant.resource_ref == request.resource_ref
    assert grant.access_checkpoint_ref == formal.checkpoint_ref
    assert grant.resource_token_ref == formal.access.resource_token_ref
    assert grant.petri_input_arc_mode == "read"
    assert grant.petri_output_arc_mode is None
    assert grant.return_arc_required is False


def test_request_identity_is_idempotent_and_parent_action_scoped() -> None:
    request, _formal = _request()
    assert _request_identity(request) == _request_identity(request)
    changed = AgentLoopResourceRequest(
        firing=request.firing,
        invocation_ref=request.invocation_ref,
        operation_execution_lease_ref=request.operation_execution_lease_ref,
        operation_binding_ref=request.operation_binding_ref,
        agent_loop_ref=request.agent_loop_ref,
        agent_turn_ref=request.agent_turn_ref,
        agent_action_ref=_ref(
            "agent_action/v2", "agent_action", "agent_action_version", 23),
        requester_agent_ref=request.requester_agent_ref,
        resource_ref=request.resource_ref,
        access_mode=request.access_mode,
        llm_turns_used=request.llm_turns_used,
    )
    assert _request_identity(changed) != _request_identity(request)


def test_waiting_queue_entry_is_exact_and_stable() -> None:
    request, _formal = _request()
    lifecycle_id = _request_identity(request)
    request_ref = _subref(lifecycle_id, "request")
    queue_ref = _subref(lifecycle_id, "queue_entry")
    entry = _queue_entry(request, request_ref, queue_ref, 4)

    assert entry == _queue_entry(request, request_ref, queue_ref, 4)
    assert entry["transition_firing_ref"] == _ref_payload(
        request.firing.transition_firing_ref)
    assert entry["invocation_ref"] == _ref_payload(request.invocation_ref)
    assert entry["mode"] == "read"
    assert entry["ordinal"] == 4


def _waiting_document(core, request, formal, *, ordinal, prior=()):
    lifecycle_id = _request_identity(request)
    request_ref = _subref(lifecycle_id, "request")
    queue_ref = _subref(lifecycle_id, "queue_entry")
    own = _queue_entry(request, request_ref, queue_ref, ordinal)
    return _base_document(
        core, request, formal,
        lifecycle_ref=_lifecycle_ref(lifecycle_id, "waiting_resource"),
        request_ref=request_ref,
        queue_entry_ref=queue_ref,
        heartbeat_ref=_subref(lifecycle_id, "heartbeat"),
        observed_at_utc="2026-09-19T00:00:00Z",
        stable_queue=[*prior, own],
    ), own


def test_broker_promotes_exactly_one_retained_stable_queue_head(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    core = _RegistryCore(tmp_path / "run", create=True)
    first, formal = _request()
    second = replace(
        first,
        operation_execution_lease_ref=_ref(
            "operation_execution_lease/v1", "operation_execution_lease",
            "operation_execution_lease_version", 30),
        agent_action_ref=_ref(
            "agent_action/v2", "agent_action", "agent_action_version", 31),
    )
    first_document, first_entry = _waiting_document(
        core, first, formal, ordinal=0)
    second_document, _ = _waiting_document(
        core, second, formal, ordinal=1, prior=(first_entry,))
    documents = {
        str(_request_identity(first)): first_document,
        str(_request_identity(second)): second_document,
    }
    promoted = []
    sentinel = object()
    monkeypatch.setattr(broker, "_latest_lifecycle_documents", lambda _core: documents)
    monkeypatch.setattr(broker, "_request_is_active", lambda _core, _request: None)
    monkeypatch.setattr(
        broker, "derive_current_firing_resource_access",
        lambda _core, _firing, _resource, _mode: formal)
    monkeypatch.setattr(
        broker, "verify_registered_firing_resource_access_conflicts",
        lambda _core, _formal: None)

    def promote(_core, _firing, lifecycle, **kwargs):
        promoted.append((lifecycle, kwargs))
        return sentinel

    monkeypatch.setattr(broker, "promote_waiting_agent_resource", promote)

    result = promote_one_waiting_agent_resource(
        core,
        (AgentResourceWaitCandidate(
             second.firing, second.operation_execution_lease_ref),
         AgentResourceWaitCandidate(
             first.firing, first.operation_execution_lease_ref)),
        idempotency_key="test:promote-one",
    )

    assert result is sentinel
    assert len(promoted) == 1
    assert promoted[0][0].operation_execution_lease_ref == (
        first.operation_execution_lease_ref)
    assert promoted[0][1]["wake_reason"] == "firing_settled"


def test_broker_returns_no_progress_without_synthesizing_grant(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    core = _RegistryCore(tmp_path / "run", create=True)
    request, formal = _request()
    document, _entry = _waiting_document(
        core, request, formal, ordinal=0)
    monkeypatch.setattr(
        broker, "_latest_lifecycle_documents",
        lambda _core: {str(_request_identity(request)): document})
    monkeypatch.setattr(broker, "_request_is_active", lambda _core, _request: None)
    monkeypatch.setattr(
        broker, "derive_current_firing_resource_access",
        lambda _core, _firing, _resource, _mode: formal)
    monkeypatch.setattr(
        broker, "verify_registered_firing_resource_access_conflicts",
        lambda _core, _formal: (_ for _ in ()).throw(
            ValueError("still contended")))
    monkeypatch.setattr(
        broker, "promote_waiting_agent_resource",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("contended wait must not be promoted")))

    assert promote_one_waiting_agent_resource(
        core,
        (AgentResourceWaitCandidate(
            request.firing, request.operation_execution_lease_ref),),
        idempotency_key="test:no-progress",
    ) is None


def _typed_execution(request):
    execution = object.__new__(OperationExecutionAuthority)
    object.__setattr__(execution, "operation_execution_lease_ref",
                       request.operation_execution_lease_ref)
    object.__setattr__(execution, "operation", SimpleNamespace(
        firing=request.firing))
    return execution


def test_harness_resumes_promoted_wait_through_retained_callback(
        monkeypatch: pytest.MonkeyPatch) -> None:
    request, _formal = _request()
    execution = _typed_execution(request)
    resumed = []
    disposition = OperationDisposition(
        execution, "resource_wait", payload=object(),
        resume=lambda grant: resumed.append(grant))
    grant = SimpleNamespace(
        operation_execution_lease_ref=request.operation_execution_lease_ref,
        transition_firing_ref=request.firing.transition_firing_ref,
    )
    monkeypatch.setattr(
        broker, "promote_one_waiting_agent_resource",
        lambda _core, candidates, **_kwargs: (
            grant if candidates[0].operation_execution_lease_ref
            == request.operation_execution_lease_ref else None))
    submitted = []
    watched = []
    future = Future()
    harness = Harness.__new__(Harness)
    harness.owner = SimpleNamespace(_core=object())
    harness.submit_operation = lambda callback: (
        submitted.append(callback) or future)
    harness.event_loop = SimpleNamespace(
        watch_completion=lambda watched_future, callback: watched.append(
            (watched_future, callback)))
    harness._waiting = {request.operation_execution_lease_ref: disposition}
    harness._pending = {}
    harness._trace = []

    harness._owner_stop_requested = False
    assert harness._resume_resource_waits(
        idempotency_key="test:resume") == 1
    assert request.operation_execution_lease_ref in harness._pending
    assert not harness._waiting
    assert watched == [(future, watched[0][1])]
    assert submitted[0]() is None
    assert resumed == [grant]
