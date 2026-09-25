from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

import cpn.components.agent_loop.optional_execution as optional
from cpn.components.agent_loop.models import (
    AgentLoopState,
    stable_action_id,
)
from cpn.components.agent_loop.tools import ValidatedAgentToolAction
from cpn.components.tool_executors import PreparedRegisteredAgentAction
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.resources import (
    AgentLoopResourceGrantAuthority,
    AgentLoopResourceLifecycleAuthority,
    RegistryHead,
    ResourceVersionRef,
    TransitionFiringAuthority,
)
from cpn.rpnh.response_protocol import LLMToolCallObservation


def _id(kind: str, value: int) -> TypedId:
    return TypedId(kind, f"{value:032x}")  # type: ignore[arg-type]


def _ref(
        entity_type: str, logical_kind: str, version_kind: str, value: int,
) -> VersionRef:
    return VersionRef(
        entity_type, _id(logical_kind, value),
        _id(version_kind, value + 1000))


def _authority(*, waiting: bool):
    resource_ref = ResourceVersionRef(
        _id("resource", 20), _id("resource_version", 1020))
    values = dict(
        lifecycle_ref=_ref(
            "resource_access_lifecycle/v1", "firing_resource_use",
            "firing_resource_use_version", 21),
        request_ref=_ref(
            "resource_access_request/v1", "firing_resource_use",
            "firing_resource_use_version", 22),
        transition_firing_ref=_ref(
            "transition_firing/v1", "transition_firing",
            "transition_firing_version", 1),
        invocation_ref=_ref(
            "invocation/v1", "invocation", "invocation_version", 2),
        operation_execution_lease_ref=_ref(
            "operation_execution_lease/v1", "operation_execution_lease",
            "operation_execution_lease_version", 3),
        operation_binding_ref=_ref(
            "operation_binding/v1", "operation_binding",
            "operation_binding_version", 4),
        agent_loop_ref=_ref(
            "agent_loop/v1", "agent_loop", "agent_loop_version", 5),
        agent_turn_ref=_ref(
            "agent_turn/v1", "agent_turn", "agent_turn_version", 6),
        agent_action_ref=_ref(
            "agent_action/v2", "agent_action", "agent_action_version", 7),
        resource_ref=resource_ref,
        access_mode="read",
        access_checkpoint_ref=_ref(
            "marking_checkpoint/v1", "marking_checkpoint",
            "marking_checkpoint_version", 8),
        access_net_ref=_ref(
            "net_instance/v1", "net_instance", "net_instance_version", 9),
        access_claim_epoch=0,
        resource_token_ref=_ref(
            "petri_token/v1", "petri_token", "petri_token_version", 10),
        lease_pool_place="team.resource_leases",
        lease_identity_ref=_ref(
            "resource_place/v1", "resource_place",
            "resource_place_version", 11),
        petri_input_arc_mode="read",
        petri_output_arc_mode=None,
        petri_arc_kind="read",
        return_arc_required=False,
        writer_fencing_epoch=1,
        heartbeat_ref=_ref(
            "resource_access_heartbeat/v1", "firing_resource_use",
            "firing_resource_use_version", 12),
        llm_turns_used=1,
    )
    if waiting:
        return AgentLoopResourceLifecycleAuthority(
            queue_entry_ref=_ref(
                "resource_access_queue_entry/v1", "firing_resource_use",
                "firing_resource_use_version", 13),
            agent_action_ref=values["agent_action_ref"],
            requester_agent_ref=_ref(
                "agent/v1", "agent", "agent_version", 14),
            logical_resource_id=resource_ref.resource_id,
            lock_resource_ref=resource_ref,
            resource_ref=resource_ref,
            state="waiting_resource",
            heartbeat_at_utc="2026-09-19T00:00:00Z",
            **{key: value for key, value in values.items()
               if key not in {"agent_action_ref", "resource_ref"}},
        )
    return AgentLoopResourceGrantAuthority(
        queue_entry_id="resource-wait:one",
        grant_ref=_ref(
            "resource_access_grant/v1", "firing_resource_use",
            "firing_resource_use_version", 15),
        lease_ref=_ref(
            "resource_access_lease/v1", "firing_resource_use",
            "firing_resource_use_version", 16),
        **values,
    )


def _firing(authority) -> TransitionFiringAuthority:
    return TransitionFiringAuthority(
        transition_firing_ref=authority.transition_firing_ref,
        transition_id="team.worker",
        activation_ref=None,
        task_ref=_ref("task/v1", "task", "task_version", 30),
        task_branch_ref=_ref(
            "task_branch/v1", "task_branch", "task_branch_version", 31),
        task_round_ref=_ref(
            "task_round/v1", "task_round", "task_round_version", 32),
        net_ref=authority.access_net_ref,
        plan_ref=_ref("plan_version/v1", "plan", "plan_version", 33),
        node_ref=_ref(
            "node_declaration/v1", "node", "node_declaration_version", 34),
        operation_binding_ref=authority.operation_binding_ref,
        principal_ref=_ref(
            "principal/v1", "principal", "principal_version", 35),
        admission_marking_checkpoint_ref=authority.access_checkpoint_ref,
        budget_witness_ref=_ref(
            "budget_witness/v1", "snapshot", "snapshot", 36),
        firing_admission_ref=_ref(
            "firing_admission/v1", "firing_admission",
            "firing_admission_version", 37),
        claim_marking_delta_ref=_ref(
            "marking_delta/v1", "marking_delta",
            "marking_delta_version", 38),
        logical_tau=0,
        attempt_index=1,
        claimed_input_refs=(),
        verified_at_head=RegistryHead(0, 1, 0, {}),
    )


class _Mechanical:
    def __init__(self, authority) -> None:
        self.authority = authority
        self.plan = None

    def action_ref(self, _action_id, _key):
        return self.authority.agent_action_ref

    def successor_loop_ref(self, _loop, _key):
        return self.authority.agent_loop_ref

    def begin_action_policy_transaction(
            self, loop, *, idempotency_key, transaction_scope):
        tx = SimpleNamespace(
            idempotency_key=idempotency_key,
            event_store=object(),
            net_instance_id=transaction_scope["net_instance_id"])
        return tx, loop

    def finish_action_policy_transaction(self, **kwargs):
        self.plan = kwargs["plan"]
        return "committed"


@pytest.mark.parametrize("waiting", [False, True])
def test_request_resource_settlement_uses_exact_typed_lifecycle(
    monkeypatch: pytest.MonkeyPatch, waiting: bool,
) -> None:
    authority = _authority(waiting=waiting)
    loop = SimpleNamespace(
        loop_id=str(authority.agent_loop_ref.entity_id),
        revision=3,
        llm_turns_used=1,
        written_resource_refs=(),
    )
    turn = SimpleNamespace(sequence=0, turn_id=str(
        authority.agent_turn_ref.entity_id))
    tool_call_id = "request-one"
    action_id = stable_action_id(loop.loop_id, 0, tool_call_id)
    authority = replace(
        authority,
        agent_action_ref=VersionRef(
            "agent_action/v2",
            TypedId.parse(action_id, expected="agent_action"),
            authority.agent_action_ref.version_id))
    mechanical = _Mechanical(authority)
    service = object.__new__(optional.OptionalAgentLoopRegistryService)
    service.core = object()
    service.mechanical_lifecycle = mechanical
    context = SimpleNamespace(
        agent_ref=_ref("agent/v1", "agent", "agent_version", 14),
        invocation_ref=authority.invocation_ref,
        operation_binding_ref=authority.operation_binding_ref,
        task_round_ref=_ref(
            "task_round/v1", "task_round", "task_round_version", 17),
        net_instance_ref=authority.access_net_ref,
    )
    service._context = lambda _loop: context
    execution = SimpleNamespace(
        operation=SimpleNamespace(firing=_firing(authority)),
        operation_execution_lease_ref=(
            authority.operation_execution_lease_ref),
    )
    raw_arguments = (
        '{"access_mode":"read","resource_id":"'
        + str(authority.resource_ref.resource_id)
        + '","resource_version_id":"'
        + str(authority.resource_ref.resource_version_id) + '"}')
    validation = ValidatedAgentToolAction(
        action_id,
        loop.loop_id, 0, tool_call_id, "request_resource",
        raw_arguments, {
            "resource_id": str(authority.resource_ref.resource_id),
            "resource_version_id": str(
                authority.resource_ref.resource_version_id),
            "access_mode": "read",
        }, loop.revision)
    call = LLMToolCallObservation(
        0, tool_call_id, "request_resource", raw_arguments, (),
        "tool_call_id", tool_call_id)
    prepared = PreparedRegisteredAgentAction(
        call, validation, raw_arguments)

    def prepare(_core, tx, request, *, idempotency_key):
        assert tx.idempotency_key == idempotency_key
        assert request.agent_loop_ref == authority.agent_loop_ref
        assert request.agent_turn_ref == authority.agent_turn_ref
        assert request.agent_action_ref == authority.agent_action_ref
        assert request.resource_ref == authority.resource_ref
        return authority

    monkeypatch.setattr(optional, "prepare_agent_resource_request", prepare)
    result = service._settle_agent_resource_request(
        execution, loop, turn, prepared,
        turn_ref=authority.agent_turn_ref,
        idempotency_key="request-resource:test")

    assert result == "committed"
    plan = mechanical.plan
    assert plan.records[0].state == (
        AgentLoopState.WAITING_RESOURCE
        if waiting else AgentLoopState.ACTION_APPLIED)
    assert plan.final_state == (
        AgentLoopState.WAITING_RESOURCE
        if waiting else AgentLoopState.WAITING_FOR_LLM)
    metadata = plan.records[0].result_metadata
    assert metadata["agent_loop_ref"] == _ref_payload(
        authority.agent_loop_ref)
    assert metadata["agent_turn_ref"] == _ref_payload(
        authority.agent_turn_ref)
    assert metadata["agent_action_ref"] == _ref_payload(
        authority.agent_action_ref)
    assert metadata["resource_ref"] == {
        "resource_id": str(authority.resource_ref.resource_id),
        "resource_version_id": str(
            authority.resource_ref.resource_version_id),
    }
    assert metadata["available_on_next_turn"].endswith("/content")
    assert (metadata["resource_access_grant_ref"] is None) is waiting
    assert (metadata["resource_access_lease_ref"] is None) is waiting
