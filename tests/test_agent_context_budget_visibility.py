"""Offline rendering of authoritative per-request allowances; no Registry writes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.context import (
    ContextExecutionMixin, build_agent_system_initialization,
)
from cpn.components.agent_loop.tool_catalog import build_agent_tool_catalog
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef


def invocation_ref():
    return VersionRef("invocation/v1", TypedId.parse("invocation:" + "a" * 32),
                      TypedId.parse("invocation_version:" + "b" * 32))


def render(*, role="actor", output_kind="ordinary_document", **budgets):
    return build_agent_system_initialization(
        invocation_ref=invocation_ref(), sponsoring_transition_firing_ref=None,
        role=role, catalog=build_agent_tool_catalog(tool_names=("read_file",)),
        located_inputs=(), output_kind=output_kind,
        output_contract="Report the assigned facts.", **budgets).payload.decode()


@pytest.mark.parametrize("local_cap,task_cap", [(12, 96), (None, 96), (12, None), (None, None)])
def test_current_usage_remaining_and_unbounded_are_rendered(local_cap, task_cap):
    budgets = dict(llm_turn_cap=local_cap, llm_turns_used=3,
                   task_call_cap=task_cap, task_calls_used=21)
    original = deepcopy(budgets)
    first = render(**budgets)
    second = render(**dict(budgets, llm_turns_used=4, task_calls_used=22))
    assert first != second
    assert f"llm_turn_cap={local_cap if local_cap is not None else 'unbounded'}" in first
    assert "llm_turns_used=3" in first
    assert f"llm_turns_remaining={local_cap - 3 if local_cap is not None else 'unbounded'}" in first
    assert f"task_call_cap={task_cap if task_cap is not None else 'unbounded'}" in first
    assert "task_calls_used=21" in first
    assert f"task_calls_remaining={task_cap - 21 if task_cap is not None else 'unbounded'}" in first
    assert "llm_turns_used=4" in second and "task_calls_used=22" in second
    assert "native task accounting" in first
    assert "snapshot at request preparation" in first
    assert "not a count of all registered requests or billing" in first
    assert "last local ordinary turn" not in first
    assert budgets == original


def test_normal_request_initialization_uses_live_authority_without_mutation():
    catalog = build_agent_tool_catalog(tool_names=("read_file",))
    loop = SimpleNamespace(invocation_ref=invocation_ref(), llm_turn_budget=12,
                           llm_turns_used=10)
    calls = [20]
    operation = SimpleNamespace(declaration=SimpleNamespace(
        outcomes=(), config={"agent_loop_role": "actor"}))
    execution = SimpleNamespace(operation=SimpleNamespace(
        firing=SimpleNamespace(transition_firing_ref=None)))
    host = SimpleNamespace(
        _execution=lambda execution, loop: execution,
        _catalog=lambda *args: None, _context=lambda loop: None,
        _declared=lambda context: (None, None, operation),
        _project_workspace=lambda execution, loop: (),
        core=SimpleNamespace(event_store=SimpleNamespace(
            actual_model_call_counts=lambda: (calls[0], 0),
            ordinary_model_call_limit=lambda: 96)))
    prepare = ContextExecutionMixin.prepare_agent_system_initialization_v1
    earlier = prepare(host, execution, loop, catalog).payload.decode()
    assert "llm_turns_remaining=2" in earlier
    assert "last local ordinary turn" not in earlier
    loop.llm_turns_used = 11
    calls[0] = 21
    before = vars(loop).copy()
    final = prepare(host, execution, loop, catalog).payload.decode()
    assert "llm_turns_used=11" in final and "llm_turn_cap=12" in final
    assert "llm_turns_remaining=1" in final
    assert "last local ordinary turn" in final
    assert "task_calls_used=21" in final and "task_calls_remaining=75" in final
    assert vars(loop) == before and calls == [21]


@pytest.mark.parametrize("role,output_kind", [
    ("actor", "ordinary_document"),
    ("critic", "a2c_critic_document"),
    ("reviewer", "finalization_review"),
])
def test_last_turn_notice_is_limited_to_current_role_segment(role, output_kind):
    text = render(role=role, output_kind=output_kind, llm_turn_cap=12,
                  llm_turns_used=11, task_call_cap=96, task_calls_used=21)
    assert "Current role/segment ordinary-turn allowance" in text
    assert ("last local ordinary turn in the current role/segment "
            "under its current configured cap") in text
    assert "last response" not in text


@pytest.mark.parametrize("field,value", [
    ("llm_turn_cap", True), ("llm_turn_cap", 0),
    ("llm_turns_used", -1), ("llm_turns_used", 13),
    ("task_call_cap", False), ("task_call_cap", 0),
    ("task_calls_used", -1), ("task_calls_used", 97),
])
def test_existing_static_budget_validation_is_preserved(field, value):
    budgets = dict(llm_turn_cap=12, llm_turns_used=11,
                   task_call_cap=96, task_calls_used=21)
    budgets[field] = value
    with pytest.raises(ValueError, match="agent initialization"):
        render(**budgets)
