from __future__ import annotations

import json
from pathlib import Path

from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.components.request_protocol import validate_llm_request_message_history
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registry.schema_catalog import canonical_json

import pytest

from rpnh_rrsi.formal_role import (
    ACTION_EVIDENCE_PROFILE,
    CONFORMANCE_PROFILE,
    FormalRoleConfig,
    ROLE_LIMITS,
    _initial_state,
    _runtime_key,
    apply_response,
    run_role_session,
    tool_catalog,
)


class ScriptedPort:
    def __init__(self, selection: LLMExecutionSelection, responses: list[dict]):
        self.execution_policy = selection.as_registry_policy()
        self.responses = list(responses)
        self.requests: list[dict] = []

    def request_once(self, attempt):
        request = json.loads(attempt.canonical_request_bytes)
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected provider request")
        return LLMInputResponseBytes(
            canonical_json(self.responses.pop(0)), status_code=None,
            external_request_id=f"scripted-{len(self.requests)}")

    def close(self):
        pass


def _selection(tmp_path: Path) -> LLMExecutionSelection:
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "formal-role-test-model",
        "argv": ["formal-role-test-model"],
        "probe_argv": ["formal-role-test-model", "--probe"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    return LLMExecutionSelection(
        LLMInputTarget("formal-role-test-model", 4096, 65536),
        "local_process", adapter.resolve(), 30)


def _response(*calls: dict) -> dict:
    return {
        "protocol": "llm_response_envelope/v1",
        "text": "",
        "tool_calls": list(calls),
        "finish_reason": "tool_calls",
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "total_tokens": 15},
    }


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name,
            "arguments": json.dumps(arguments, separators=(",", ":"))}


def _request(role: str, occurrence: str, input_value: dict) -> dict:
    return {
        "schema_version": "rrsi_v06/formal_role_request/v1",
        "protocol_id": "formal-test",
        "round_id": "round-0",
        "occurrence_id": occurrence,
        "parent_action_ref": "parent-action-0",
        "role": role,
        "limits": dict(ROLE_LIMITS[role]),
        "input": input_value,
    }


def _digest_envelope(request: dict, digest: str = "bounded") -> dict:
    return {
        "schema_version": "rrsi_v06/formal_role_envelope/v1",
        "run": {
            "run_ref": {"logical_id": "run-" + request["occurrence_id"]},
            "task_ref": {"logical_id": "task-" + request["occurrence_id"]},
            "result_resource_ref": {"resource_id": "result-1"},
            "terminal_evidence_version_id": "terminal-1",
            "transition_trace": ["role.init", "role.model", "role.act"],
        },
        "result": {
            "schema_version": "rrsi_v06/formal_role_result/v1",
            "protocol_id": request["protocol_id"],
            "round_id": request["round_id"],
            "occurrence_id": request["occurrence_id"],
            "parent_action_ref": request["parent_action_ref"],
            "role": "digester",
            "termination": "submitted",
            "payload": {"digest": digest},
        },
    }


def test_proposer_is_one_business_occurrence_with_three_model_firings(tmp_path):
    selection = _selection(tmp_path)
    port = ScriptedPort(selection, [
        _response(_call("read", "rrsi_read_source", {"path": "policy.py"})),
        _response(_call("edit", "rrsi_edit_source", {
            "path": "policy.py", "old": "VALUE = 1", "new": "VALUE = 2"})),
        _response(_call("submit", "rrsi_submit_proposal", {
            "summary": "Use the corrected value.", "component": "policy"})),
    ])
    result = run_role_session(
        run_dir=tmp_path / "proposer-run",
        request=_request("proposer", "proposer-0", {
            "files": [{"path": "policy.py", "content": "VALUE = 1\n",
                       "mode": 0o755, "purpose": "policy_source"}],
            "traces": [],
            "editable_paths": ["policy.py"],
            "source_exts": [".py"],
            "create_mode": 0o644,
            "repair": False,
        }),
        selection=selection, llm_input_port=port)

    assert result["result"]["termination"] == "submitted"
    assert result["result"]["generation_count"] == 3
    assert result["result"]["successful_edits"] == 1
    member = result["result"]["payload"]["files"][0]
    assert member["content"] == "VALUE = 2\n"
    assert member["mode"] == 0o755
    assert result["run"]["transition_trace"] == [
        "role.init", "role.model", "role.act", "role.model", "role.act",
        "role.model", "role.act"]
    assert len(port.requests) == 3
    assert all(row["tools"] == tool_catalog("proposer") for row in port.requests)
    for row in port.requests:
        validate_llm_request_message_history(row["messages"])
    assert port.requests[1]["messages"][-1]["role"] == "tool"
    assert port.requests[1]["messages"][-2]["tool_calls"] == [{
        "id": "read", "type": "function",
        "function": {
            "name": "rrsi_read_source",
            "arguments": '{"path":"policy.py"}',
        },
    }]


def test_multiple_tool_calls_are_rejected_before_effect(tmp_path):
    selection = _selection(tmp_path)
    port = ScriptedPort(selection, [
        _response(
            _call("bad-edit", "rrsi_edit_source", {
                "path": "policy.py", "old": "VALUE = 1", "new": "VALUE = 9"}),
            _call("bad-submit", "rrsi_submit_proposal", {
                "summary": "bad batch", "component": "policy"})),
        _response(_call("edit", "rrsi_edit_source", {
            "path": "policy.py", "old": "VALUE = 1", "new": "VALUE = 2"})),
        _response(_call("submit", "rrsi_submit_proposal", {
            "summary": "single action path", "component": "policy"})),
    ])
    result = run_role_session(
        run_dir=tmp_path / "multi-reject-run",
        request=_request("proposer", "proposer-multi", {
            "files": [{"path": "policy.py", "content": "VALUE = 1\n",
                       "mode": 0o644, "purpose": "policy_source"}],
            "traces": [], "editable_paths": ["policy.py"],
            "source_exts": [".py"], "create_mode": 0o644,
            "repair": False,
        }),
        selection=selection, llm_input_port=port)

    state = result["result"]["final_state"]
    assert state["actions"][0]["status"] == "rejected_before_effect"
    assert state["actions"][0]["effect_applied"] is False
    assert result["result"]["successful_edits"] == 1
    assert result["result"]["payload"]["files"][0]["content"] == "VALUE = 2\n"
    rejected_history = port.requests[1]["messages"][-2]
    assert rejected_history["role"] == "assistant"
    assert "tool_calls" not in rejected_history
    for row in port.requests:
        validate_llm_request_message_history(row["messages"])


def test_valid_tool_call_does_not_require_provider_finish_literal() -> None:
    state = _initial_state(
        _request("critic", "critic-provider-neutral", {
            "files": [], "traces": [], "component": "policy",
        }),
        FormalRoleConfig("critic").to_dict(),
    )
    provider_response = _response(_call(
        "submit", "rrsi_submit_critique", {
            "decision": "accepted", "reason": "valid shape",
            "component": "policy",
        }))
    provider_response["finish_reason"] = "end_turn"

    outcome, output, result = apply_response({
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": provider_response,
    })

    assert (outcome, output) == ("complete", "result")
    assert result["termination"] == "submitted"


def test_role_rejects_explicit_length_truncation_before_effect() -> None:
    state = _initial_state(
        _request("critic", "critic-truncated", {
            "files": [], "traces": [], "component": "policy",
        }),
        FormalRoleConfig("critic").to_dict(),
    )
    provider_response = _response(_call(
        "submit", "rrsi_submit_critique", {
            "decision": "accepted", "reason": "must not apply",
            "component": "policy",
        }))
    provider_response["finish_reason"] = "length"

    outcome, output, next_state = apply_response({
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": provider_response,
    })

    assert (outcome, output) == ("continue", "state_out")
    assert next_state["actions"][0]["status"] == "rejected_before_effect"
    assert next_state["actions"][0]["result"] == {
        "error": "response_was_truncated"}


def test_analyst_digest_many_uses_independent_restricted_digester(tmp_path):
    selection = _selection(tmp_path)
    analyst_port = ScriptedPort(selection, [
        _response(_call("batch", "rrsi_digest_many", {"requests": [{
            "task_id": "task-a", "lens": "failure", "questions": ["Why?"]
        }, {
            "task_id": "unknown", "lens": "success", "questions": []
        }]})),
        _response(_call("analysis", "rrsi_submit_analysis", {
            "failure_modes": ["VALUE is stale"],
            "success_patterns": ["preserve mode"],
            "contrasts": ["task-a differs from unknown"],
        })),
    ])
    child_ports: list[ScriptedPort] = []

    def digest_runner(request, ordinal):
        child_port = ScriptedPort(selection, [
            _response(_call("read", "rrsi_read_trace", {
                "from_line": 1, "to_line": 2})),
            _response(_call("return", "rrsi_return_digest", {
                "digest": "The trace shows VALUE is stale."})),
        ])
        child_ports.append(child_port)
        return run_role_session(
            run_dir=tmp_path / "analyst-run" / "children" / f"digest-{ordinal}",
            request=request, selection=selection, llm_input_port=child_port)

    result = run_role_session(
        run_dir=tmp_path / "analyst-run",
        request=_request("analyst", "analyst-0", {
            "files": [],
            "traces": [{"task_id": "task-a", "trace_ref": "trace-a",
                        "text": "VALUE = 1\nExpected VALUE = 2"}],
            "task_table": [{"task_id": "task-a"}],
        }),
        selection=selection, llm_input_port=analyst_port,
        digest_runner=digest_runner)

    assert result["result"]["termination"] == "submitted"
    digests = result["result"]["payload"]["digests"]
    assert [row["status"] for row in digests] == ["submitted", "unknown_task"]
    assert digests[0]["digest"] == "The trace shows VALUE is stale."
    assert len(child_ports) == 1
    assert all(row["tools"] == tool_catalog("digester")
               for row in child_ports[0].requests)
    assert "rrsi_edit_source" not in {
        item["function"]["name"] for item in child_ports[0].requests[0]["tools"]}


def test_role_state_records_application_petri_adaptation() -> None:
    state = _initial_state(
        _request("critic", "critic-profile", {
            "files": [], "traces": [], "component": "policy",
        }),
        FormalRoleConfig("critic").to_dict(),
    )
    assert state["conformance_profile"] == CONFORMANCE_PROFILE
    assert state["action_evidence_profile"] == ACTION_EVIDENCE_PROFILE
    assert state["strict_agent_loop_conformance"] is False


def test_critic_cannot_expand_its_three_generation_cap() -> None:
    state = _initial_state(
        _request("critic", "critic-cap", {
            "files": [], "traces": [], "component": "policy",
        }),
        FormalRoleConfig("critic").to_dict(),
    )
    state["generation"] = 3
    response = {
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": _response(_call("submit", "rrsi_submit_critique", {
            "decision": "accepted", "reason": "too late",
            "component": "policy",
        })),
    }
    with pytest.raises(ValueError, match="fixed limit"):
        apply_response(response)


def test_digest_child_failure_after_dispatch_is_not_pre_effect_rejection() -> None:
    state = _initial_state(
        _request("analyst", "analyst-partial", {
            "files": [],
            "traces": [
                {"task_id": "task-a", "trace_ref": "trace-a", "text": "a"},
                {"task_id": "task-b", "trace_ref": "trace-b", "text": "b"},
            ],
        }),
        FormalRoleConfig("analyst").to_dict(),
    )
    response = {
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": _response(_call("batch", "rrsi_digest_many", {
            "requests": [
                {"task_id": "task-a", "lens": "failure", "questions": []},
                {"task_id": "task-b", "lens": "success", "questions": []},
            ],
        })),
    }
    calls = []

    def runner(request, ordinal):
        calls.append((request, ordinal))
        if ordinal == 1:
            raise ValueError("child failed after dispatch")
        return _digest_envelope(request, "a")

    with pytest.raises(RuntimeError, match="child dispatch"):
        apply_response(response, digest_runner=runner)
    assert len(calls) == 2


def test_runtime_identity_includes_protocol_and_round() -> None:
    assert _runtime_key({
        "protocol_id": "p-a", "round_id": "r-a", "occurrence_id": "same",
    }) != _runtime_key({
        "protocol_id": "p-b", "round_id": "r-b", "occurrence_id": "same",
    })


def test_malformed_tool_arguments_are_journaled_and_remain_correctable() -> None:
    state = _initial_state(
        _request("analyst", "analyst-malformed", {
            "files": [], "traces": [],
        }),
        FormalRoleConfig("analyst").to_dict(),
    )
    response = {
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": _response({
            "id": "bad", "name": "rrsi_submit_analysis",
            "arguments": '{"failure_modes":[]} trailing',
        }),
    }
    outcome, output, next_state = apply_response(response)
    assert (outcome, output) == ("continue", "state_out")
    action = next_state["actions"][0]
    assert action["status"] == "rejected_before_effect"
    assert action["effect_applied"] is False
    assert action["arguments"] == {
        "valid_json": False,
        "raw_text": '{"failure_modes":[]} trailing',
    }
    validate_llm_request_message_history(next_state["messages"])


def test_duplicate_tool_argument_keys_are_rejected_before_effect() -> None:
    state = _initial_state(
        _request("critic", "critic-duplicate", {
            "files": [], "traces": [], "component": "policy",
        }),
        FormalRoleConfig("critic").to_dict(),
    )
    response = {
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": _response({
            "id": "bad", "name": "rrsi_submit_critique",
            "arguments": (
                '{"decision":"accepted","decision":"rejected",'
                '"reason":"duplicate","component":"policy"}'),
        }),
    }

    outcome, output, next_state = apply_response(response)
    assert (outcome, output) == ("continue", "state_out")
    assert next_state["actions"][0]["status"] == "rejected_before_effect"
    assert next_state["actions"][0]["effect_applied"] is False
    assert next_state["actions"][0]["arguments"]["valid_json"] is False


def test_digester_search_is_literal_not_backtracking_regex() -> None:
    state = _initial_state(
        _request("digester", "digester-literal", {
            "selected_ref": "trace-a", "task_id": "task-a",
            "lens": "failure",
            "traces": [{"task_id": "task-a", "trace_ref": "trace-a",
                        "text": "a+b\nab"}],
        }),
        FormalRoleConfig("digester").to_dict(),
    )
    response = {
        "schema_version": "rrsi_v06/formal_role_response/v1",
        "state": state,
        "response": _response(_call(
            "search", "rrsi_search_trace", {"pattern": "a+b"})),
    }

    outcome, output, next_state = apply_response(response)
    assert (outcome, output) == ("continue", "state_out")
    assert next_state["actions"][0]["result"]["matches"] == [
        {"line": 1, "text": "a+b"}]


def test_invalid_role_request_is_rejected_before_registry_creation(tmp_path) -> None:
    selection = _selection(tmp_path)
    run_dir = tmp_path / "invalid-role"
    request = _request("analyst", "invalid-analyst", {
        "files": [], "traces": "not-an-array",
    })

    with pytest.raises(ValueError, match="pre-dispatch"):
        run_role_session(
            run_dir=run_dir, request=request, selection=selection,
            llm_input_port=ScriptedPort(selection, []))
    assert not run_dir.exists()
