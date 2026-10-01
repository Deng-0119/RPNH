from __future__ import annotations

import json
import os
from pathlib import Path
import signal

import pytest

from cpn.components.agent_loop.tools import (
    AgentToolSyntaxError,
    build_agent_tool_catalog,
    derive_atomic_subtask_tools,
    validate_agent_tool_call,
)
from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.rpnh.agent_tasks import (
    AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task,
)
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registry._registry import _RegistryCore


def _response(**values) -> LLMInputResponseBytes:
    payload = {"protocol": "llm_response_envelope/v1", "tool_calls": []}
    payload.update(values)
    return LLMInputResponseBytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        status_code=None, external_request_id=None)


def _tool_names(envelope: dict) -> tuple[str, ...]:
    return tuple(
        item["function"]["name"] for item in envelope.get("tools", []))


class _DelegatedLeafPort:
    def __init__(self) -> None:
        self.requests: list[tuple[object, dict]] = []

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append((attempt, envelope))
        names = _tool_names(envelope)
        messages = envelope["messages"]
        if len(self.requests) == 1:
            assert "delegate_leaf" in names
            return _response(tool_calls=[{
                "id": "delegate-one", "name": "delegate_leaf",
                "arguments": json.dumps({
                    "instruction": (
                        "Inspect Registry metadata, then return the exact phrase "
                        "DELEGATED-FINAL."),
                    "resource_refs": [],
                }),
            }], finish_reason="tool_calls")
        if "delegate_leaf" not in names:
            assert "request_resource" not in names
            if not any(message.get("role") == "tool" for message in messages):
                return _response(tool_calls=[{
                    "id": "child-query", "name": "query_registry_resources",
                    "arguments": "{}",
                }], finish_reason="tool_calls")
            assert any(
                "delegated_tool_result/v1" in message.get("content", "")
                for message in messages if message.get("role") == "tool")
            return _response(text="DELEGATED-FINAL", finish_reason="stop")

        delegated_results = [
            message for message in messages
            if message.get("role") == "tool"
            and message.get("tool_call_id") == "delegate-one"]
        assert len(delegated_results) == 1
        assert "DELEGATED-FINAL" in delegated_results[0]["content"]
        return _response(tool_calls=[{
            "id": "write-result", "name": "write_file",
            "arguments": json.dumps({
                "path": "outputs/result.txt",
                "description": "Result produced after delegated child closure.",
                "content": json.dumps("delegate lifecycle complete"),
                "output_port_id": "main.result",
                "outcome_id": "complete",
            }),
        }, {
            "id": "complete-result", "name": "complete_interaction",
            "arguments": "{}",
        }], finish_reason="tool_calls")

    def close(self):
        pass


class _MixedValidityDelegatedLeafPort:
    """Return one valid delegate beside one malformed delegate action."""

    def __init__(self) -> None:
        self.requests: list[tuple[object, dict]] = []

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append((attempt, envelope))
        if "delegate_leaf" in _tool_names(envelope):
            assert len(self.requests) == 1
            return _response(tool_calls=[{
                "id": "valid-delegate", "name": "delegate_leaf",
                "arguments": json.dumps({
                    "instruction": "Return VALID-DELEGATE.",
                    "resource_refs": [],
                }),
            }, {
                "id": "malformed-delegate", "name": "delegate_leaf",
                "arguments": (
                    '{"instruction":"Return MALFORMED",'
                    '"resource_refs":[]}]'),
            }, {
                "id": "write-result", "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": (
                        "Result survives one malformed delegated sibling."),
                    "content": json.dumps("mixed delegate batch complete"),
                    "output_port_id": "main.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "complete-result", "name": "complete_interaction",
                "arguments": "{}",
            }], finish_reason="tool_calls")
        assert "delegate_leaf" not in _tool_names(envelope)
        return _response(text="VALID-DELEGATE", finish_reason="stop")

    def close(self):
        pass


class _OwnerStoppedDelegatedLeafPort:
    def __init__(self) -> None:
        self.requests = 0

    def request_once(self, attempt):
        self.requests += 1
        envelope = json.loads(attempt.canonical_request_bytes)
        if "delegate_leaf" in _tool_names(envelope):
            return _response(tool_calls=[{
                "id": "delegate-until-stop", "name": "delegate_leaf",
                "arguments": json.dumps({
                    "instruction": "Continue local inspection until stopped.",
                    "resource_refs": [],
                }),
            }], finish_reason="tool_calls")
        os.kill(os.getpid(), signal.SIGINT)
        return _response(tool_calls=[{
            "id": "child-progress", "name": "query_registry_resources",
            "arguments": "{}",
        }], finish_reason="tool_calls")

    def close(self):
        pass


def test_delegated_leaf_catalog_omits_parent_only_resource_request() -> None:
    child_tools = derive_atomic_subtask_tools(build_agent_tool_catalog())
    child_schemas = {
        item["name"]: item["arguments"] for item in child_tools}

    assert "delegate_leaf" not in child_schemas
    assert "request_resource" not in child_schemas
    selection = validate_agent_tool_call(
        loop_id="agent_loop:" + "0" * 32,
        turn_sequence=0, expected_revision=0,
        tool_call_id="child-request", tool_name="request_resource",
        raw_arguments=json.dumps({
            "resource_id": "resource:" + "1" * 32,
            "resource_version_id": "resource_version:" + "2" * 32,
            "access_mode": "read",
        }),
        tool_argument_schemas=child_schemas,
    )
    assert isinstance(selection, AgentToolSyntaxError)
    assert selection.code == "tool_not_permitted"


def _configure_offline_task(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        port: object,
) -> Path:
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-delegate-leaf",
        "argv": ["python", "-c", "pass"],
        "probe_argv": ["python", "-c", "pass"],
        "env": {}, "inherit_env": [],
    }), encoding="utf-8")
    selection = LLMExecutionSelection(
        LLMInputTarget("offline-delegate-leaf", 1024, 65536),
        "local_process", adapter_path, 30)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.load_llm_execution_selection",
        lambda _path: selection)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)
    execution_path = tmp_path / "selection.json"
    execution_path.write_text("{}", encoding="utf-8")
    return execution_path


def _objects(core: _RegistryCore, object_type: str) -> list[dict]:
    return [
        json.loads(row["metadata_json"])
        for row in core.event_store.object_rows_by_type(object_type)
    ]


@pytest.mark.parametrize(
    "max_attempts_per_stage", (6, None), ids=("bounded", "unmetered"))
def test_delegate_leaf_multi_turn_provider_and_registry_closure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        max_attempts_per_stage: int | None,
) -> None:
    port = _DelegatedLeafPort()
    execution_path = _configure_offline_task(tmp_path, monkeypatch, port)
    run_dir = tmp_path / "run"

    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Exercise one parent-owned delegated child session.",
        stages=(AgentStage("main", "Use delegate_leaf, then finish."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=max_attempts_per_stage,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "delegate lifecycle complete"
    assert len(port.requests) == 4
    child_envelopes = [
        envelope for _attempt, envelope in port.requests
        if "delegate_leaf" not in _tool_names(envelope)]
    assert len(child_envelopes) == 2

    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    invocations = [
        item for item in _objects(core, "llm_invocation_spec/v1")
        if item["invocation_kind"] == "delegated_subtask"]
    invocations.sort(key=lambda item: item["local_sequence"])
    assert [item["local_sequence"] for item in invocations] == [0, 1]
    assert len({item["child_session_id"] for item in invocations}) == 1
    assert invocations[0]["prior_turn_refs"] == []
    assert invocations[1]["prior_turn_refs"] == [
        invocations[0]["llm_invocation_ref"]]

    successes = core.event_store.list_events_by_type(
        ("llm_invocation_succeeded/v1",))
    child_invocation_refs = [
        item["llm_invocation_ref"] for item in invocations]
    child_successes = [
        event for event in successes
        if event.payload["llm_invocation_ref"] in child_invocation_refs]
    assert len(child_successes) == 2

    actions = [
        item for item in _objects(core, "agent_action/v2")
        if item["tool_name"] == "delegate_leaf"]
    assert len(actions) == 1
    action = actions[0]
    assert action["state"] == "ACTION_APPLIED"
    result_ref = action["result_metadata"]["result_resource_ref"]
    result_resource, = [
        item for item in _objects(core, "resource_version/v1")
        if item["resource_id"] == result_ref["resource_id"]
        and item["resource_version_id"] == result_ref["resource_version_id"]]
    assert result_resource["origin_kind"] == (
        "parent_owned_delegated_subtask_result")
    extension = result_resource["extensions"][
        "registry.parent_owned_delegated_subtask/v1"]
    assert extension["parent_action_ref"] == action["agent_action_ref"]
    assert extension["child_session_id"] == invocations[0]["child_session_id"]


def test_malformed_delegate_sibling_is_rejected_without_blocking_firing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _MixedValidityDelegatedLeafPort()
    execution_path = _configure_offline_task(tmp_path, monkeypatch, port)
    run_dir = tmp_path / "run"

    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Settle a mixed-validity delegated action batch.",
        stages=(AgentStage("main", "Complete the requested batch."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=4,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "mixed delegate batch complete"
    assert len(port.requests) == 2

    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    delegate_actions = [
        item for item in _objects(core, "agent_action/v2")
        if item["tool_name"] == "delegate_leaf"]
    assert [(item["tool_call_id"], item["state"])
            for item in delegate_actions] == [
        ("valid-delegate", "ACTION_APPLIED"),
        ("malformed-delegate", "ACTION_REJECTED"),
    ]


def test_owner_stop_interrupts_an_unmetered_delegated_session(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _OwnerStoppedDelegatedLeafPort()
    execution_path = _configure_offline_task(
        tmp_path, monkeypatch, port)

    result = run_agent_task(AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="Exercise owner stop during delegated execution.",
        stages=(AgentStage("main", "Delegate until the owner stops."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=None,
    ))

    assert result["stop_reason"] == "stopped_by_owner"
    assert port.requests == 2
