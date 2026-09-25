from __future__ import annotations

import random
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import get_type_hints

import pytest

from cpn.components.agent_loop import (
    mechanical_contracts,
    mechanical_lifecycle,
    models,
    service,
    timing,
    tools,
)
from cpn.components.agent_loop.action_execution import OPTIONAL_TOOL_BINDINGS
from cpn.components.agent_loop.mechanical_lifecycle import (
    AgentActionSettlementPlan,
    AgentLoopMechanicalLifecycle,
    AgentLoopMechanicalLifecycleError,
)
from cpn.components.agent_loop.action_records import (
    AgentActionSettlementPlan as FunctionalAgentActionSettlementPlan,
)
from cpn.components.agent_loop.context import UserAttachmentNavigation
from cpn.components.agent_loop.models import AgentLoopSnapshot, AgentLoopState
from cpn.components.agent_loop.optional_execution import (
    OptionalAgentCapabilityUnavailable,
    OptionalAgentLoopRegistryService,
)
from cpn.components.agent_loop.ports import (
    AgentLoopLLMPort,
    AgentLoopRegistryPort,
)
from cpn.components.agent_loop.tool_catalog import (
    build_agent_tool_catalog,
    derive_atomic_subtask_tools,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef


_AGENT_LOOP_IMPORT_MODULES = tuple(
    f"cpn.components.agent_loop.{module_name}"
    for module_name in (
        "ports",
        "loop_state",
        "turn_execution",
        "turn_records",
        "action_execution",
        "action_records",
        "context",
        "compaction",
        "delegation",
        "resource_wait",
        "workspace",
        "timing",
        "mechanical_contracts",
        "mechanical_lifecycle",
    )
)


def _assert_fresh_import_order(module_names: tuple[str, ...]) -> None:
    script = "\n".join(
        ("import importlib", *(f"importlib.import_module({name!r})"
                               for name in module_names)))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _ref(entity_type: str, logical_kind: str, version_kind: str) -> VersionRef:
    return VersionRef(
        entity_type, new_id(logical_kind), new_id(version_kind))


def _snapshot() -> AgentLoopSnapshot:
    return AgentLoopSnapshot(
        loop_id=str(new_id("agent_loop")),
        loop_version_id=str(new_id("agent_loop_version")),
        invocation_ref=_ref(
            "invocation/v1", "invocation", "invocation_version"),
        operation_binding_ref=_ref(
            "operation_binding/v1", "operation_binding",
            "operation_binding_version"),
        state=AgentLoopState.NEW,
        revision=0,
        next_turn_sequence=0,
        llm_turn_budget=1,
        llm_turns_used=0,
        model_condition="offline-model",
        tool_catalog_ref=ResourceVersionRef(
            new_id("resource"), new_id("resource_version")),
        current_candidate_ref=None,
        adopted_candidate_ref=None,
        disposition_ref=None,
        owner_ref=_ref("agent/v1", "agent", "agent_version"),
    )


def test_facades_preserve_flat_protocols_exports_and_registered_bindings() -> None:
    assert service.AgentLoopLLMPort is AgentLoopLLMPort
    assert service.AgentLoopRegistryPort is AgentLoopRegistryPort
    assert AgentActionSettlementPlan is FunctionalAgentActionSettlementPlan
    assert models.AgentActionTimingEvidence is timing.AgentActionTimingEvidence
    assert models.AgentTurnTimingEvidence is timing.AgentTurnTimingEvidence
    assert models.AgentOperationTimingEvidence is timing.AgentOperationTimingEvidence
    assert tools.UserAttachmentNavigation is UserAttachmentNavigation
    assert service.__all__ == [
        "AgentLoopLLMPort",
        "AgentLoopRegistryPort",
        "AgentLoopService",
        "CompletedAgentContextCompaction",
        "CompletedAgentLLMInvocation",
        "PreparedAgentContextCompaction",
        "PreparedAgentLLMTurn",
        "RegistryAgentLoopLLMPort",
        "StartAgentLoopCommand",
    ]
    protocol_methods = {
        name for name, value in vars(AgentLoopRegistryPort).items()
        if not name.startswith("_") and callable(value)
    }
    assert {
        "prepare_agent_llm_turn_v1",
        "settle_agent_turn_actions_v1",
        "consume_agent_loop_resource_grant_v1",
    }.issubset(protocol_methods)
    for protocol in (AgentLoopLLMPort, AgentLoopRegistryPort):
        for name, value in vars(protocol).items():
            if not name.startswith("_") and callable(value):
                assert get_type_hints(value)
    assert "prepare_agent_llm_turn_v1" not in vars(
        OptionalAgentLoopRegistryService)
    assert OptionalAgentLoopRegistryService.prepare_agent_llm_turn_v1.__module__ \
        == "cpn.components.agent_loop.turn_execution"
    assert OptionalAgentLoopRegistryService._envelope.__module__ \
        == "cpn.components.agent_loop.context"
    assert OptionalAgentLoopRegistryService._run_workspace.__module__ \
        == "cpn.components.agent_loop.workspace"
    assert AgentLoopMechanicalLifecycle.commit_loop_successor.__module__ \
        == "cpn.components.agent_loop.loop_state"
    assert AgentLoopMechanicalLifecycle.settle_action_batch.__module__ \
        == "cpn.components.agent_loop.action_records"
    assert AgentLoopMechanicalLifecycle.record_provider_turn.__module__ \
        == "cpn.components.agent_loop.turn_records"
    assert all(
        data["identity"]["implementation_id"]
        == f"components.agent_loop.optional_execution.{name}"
        for name, (_implementation, data) in OPTIONAL_TOOL_BINDINGS.items()
    )


def test_mechanical_lifecycle_reexports_leaf_contract_objects_exactly() -> None:
    contract_names = (
        "AgentLoopMechanicalLifecycleError",
        "_RETRYABLE_LLM_FAILURE_DISPOSITIONS",
        "_LLM_FAILURE_DISPOSITIONS",
        "_LLM_FAILURES_REQUIRING_TRANSPORT_DETAIL",
        "_LLM_SUBMISSION_STATES",
        "_OWNER_INTERRUPTION_SUBMISSION_STATES",
    )
    assert AgentLoopMechanicalLifecycleError is \
        mechanical_contracts.AgentLoopMechanicalLifecycleError
    for name in contract_names:
        assert getattr(mechanical_lifecycle, name) is getattr(
            mechanical_contracts, name)


@pytest.mark.parametrize(
    "module_name",
    _AGENT_LOOP_IMPORT_MODULES,
    ids=lambda module_name: module_name.rsplit(".", 1)[-1],
)
def test_each_agent_loop_module_imports_in_a_fresh_process(
        module_name: str) -> None:
    _assert_fresh_import_order((module_name,))


@pytest.mark.parametrize(
    ("order_name", "module_names"),
    (
        ("forward", _AGENT_LOOP_IMPORT_MODULES),
        ("reverse", tuple(reversed(_AGENT_LOOP_IMPORT_MODULES))),
        ("random", tuple(random.Random(1).sample(
            _AGENT_LOOP_IMPORT_MODULES,
            k=len(_AGENT_LOOP_IMPORT_MODULES)))),
    ),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_agent_loop_modules_import_in_any_order(
        order_name: str, module_names: tuple[str, ...]) -> None:
    del order_name
    _assert_fresh_import_order(module_names)


def test_delegation_is_parent_owned_and_child_catalog_is_nonrecursive() -> None:
    assert (
        OptionalAgentLoopRegistryService
        .prepare_parent_owned_delegated_subtask_call_v1.__module__
        == "cpn.components.agent_loop.delegation"
    )
    child_tools = derive_atomic_subtask_tools(build_agent_tool_catalog())
    child_names = {item["name"] for item in child_tools}
    assert "delegate_leaf" not in child_names
    assert "request_resource" not in child_names
    assert "read_file" in child_names


def test_unsupported_monitored_workspace_behavior_is_unchanged() -> None:
    gateway = object.__new__(OptionalAgentLoopRegistryService)
    with pytest.raises(
            OptionalAgentCapabilityUnavailable,
            match="monitored_workspace"):
        gateway.begin_monitored_workspace_action_v1(
            None, None, None,
            permitted_tool_names=(), idempotency_key="unsupported",
            execution=None)


def test_supplied_successor_transaction_still_commits_exactly_once() -> None:
    before = _snapshot()
    after = replace(
        before,
        loop_version_id=str(new_id("agent_loop_version")),
        revision=1,
    )

    class _Transaction:
        def __init__(self) -> None:
            self.commit_count = 0
            self.validator = None

        def validate_before_commit(self, validator) -> None:
            self.validator = validator

        def commit(self) -> None:
            self.commit_count += 1

    mechanics = object.__new__(AgentLoopMechanicalLifecycle)
    staged: list[object] = []
    mechanics._validate_current_loop = lambda _loop: None
    mechanics.prewrite_loop = lambda tx, loop: staged.append((tx, loop))
    mechanics.hydrate_loop = lambda _ref: after
    tx = _Transaction()

    committed = mechanics.commit_loop_successor(
        before,
        after,
        idempotency_key="same-owner-transaction",
        transaction=tx,
        allow_same_state=True,
    )

    assert committed is after
    assert tx.commit_count == 1
    assert tx.validator is not None
    assert staged == [(tx, after)]
