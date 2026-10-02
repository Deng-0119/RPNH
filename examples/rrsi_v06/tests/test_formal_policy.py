from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registry.schema_catalog import canonical_json

from rpnh_rrsi.formal_policy import (
    _formal_request,
    _restricted_build_messages,
    _run_policy_trial_unchecked,
    run_policy_trial,
)
from rpnh_rrsi.formal_protocol import (
    formal_protocol_mapping,
    load_formal_protocol,
)


class ScriptedLLMInputPort:
    def __init__(self, selection: LLMExecutionSelection, responses: list[dict]):
        self.execution_policy = selection.as_registry_policy()
        self.responses = list(responses)
        self.requests: list[dict] = []

    def request_once(self, attempt):
        self.requests.append(json.loads(attempt.canonical_request_bytes))
        return LLMInputResponseBytes(canonical_json(self.responses.pop(0)), status_code=None,
                                     external_request_id="formal-policy-scripted")

    def close(self):
        pass


def _selection(tmp_path: Path) -> LLMExecutionSelection:
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({"schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process", "model_condition": "formal-policy-test-model",
        "argv": ["formal-policy-test-model"], "probe_argv": ["formal-policy-test-model", "--probe"],
        "env": {}, "inherit_env": []}), encoding="utf-8")
    return LLMExecutionSelection(LLMInputTarget("formal-policy-test-model", 4096, 65536),
                                 "local_process", adapter.resolve(), 30)


def _request() -> dict:
    return {"protocol_id": "formal-policy-test", "split": "heldout",
            "evaluation_id": "heldout-candidate-0", "candidate_id": "candidate-0",
            "task_id": "task-0", "repetition": 0, "attempt": 0,
            "occurrence_id": "formal-policy-test:heldout-candidate-0:task-0:0:0",
            "source_files": [{"path": "policy.py", "mode": 0o751,
                "content": "def build_messages(raw):\n    return [{'role': 'user', 'content': str(raw)}]\n"},
                {"path": "agent/__init__.py", "mode": 0o644, "content": ""}],
            "raw_task_input": {"prompt": "Return the timeout object."}, "expected": 17, "weight": 2.5}


def _response(text: str) -> dict:
    return {"protocol": "llm_response_envelope/v1", "text": text, "tool_calls": [],
            "finish_reason": "stop", "usage": {"input_tokens": 3, "output_tokens": 2}}


def test_policy_trial_correct_response_materializes_and_imports_policy(tmp_path: Path):
    selection = _selection(tmp_path)
    port = ScriptedLLMInputPort(selection, [_response('{"timeout":17}')])
    root = tmp_path / "correct"
    result = _run_policy_trial_unchecked(
        run_dir=root, request=_request(), selection=selection,
        llm_input_port=port)

    assert result["transition_trace"] == ["policy.prepare", "policy.model", "policy.grade"]
    assert result["result"]["reward"] == 1
    assert result["result"]["output"] == 17
    assert result["result"]["usage"] == {"input_tokens": 3, "output_tokens": 2}
    assert Path(result["result"]["actual_module_path"]) == root / "policy.py"
    assert (root / "policy.py").stat().st_mode & 0o777 == 0o751
    assert (root / "agent" / "__init__.py").read_bytes() == b""
    assert port.requests[0]["tools"] == []
    assert port.requests[0]["messages"] == [{
        "role": "user", "content": "{'prompt': 'Return the timeout object.'}"}]


def test_policy_trial_invalid_or_wrong_response_is_complete_zero_reward(tmp_path: Path):
    selection = _selection(tmp_path)
    port = ScriptedLLMInputPort(selection, [_response('{"timeout":"wrong"}')])
    result = _run_policy_trial_unchecked(
        run_dir=tmp_path / "invalid", request=_request(), selection=selection,
        llm_input_port=port)

    assert result["result"]["reward"] == 0
    assert result["result"]["output"] is None
    assert result["result"]["execution_trace"].startswith("response_text_is_not_exact_timeout_object")
    assert result["terminal_evidence_ref"]["entity_type"] == "run_terminal_evidence/v1"


def test_policy_trial_rejects_tool_call_even_with_correct_text(tmp_path: Path):
    selection = _selection(tmp_path)
    response = _response('{"timeout":17}')
    response["tool_calls"] = [{
        "id": "not-allowed", "name": "anything", "arguments": "{}"}]
    response["finish_reason"] = "tool_calls"
    port = ScriptedLLMInputPort(selection, [response])
    result = _run_policy_trial_unchecked(
        run_dir=tmp_path / "tool-call", request=_request(),
        selection=selection, llm_input_port=port)
    assert result["result"]["reward"] == 0
    assert result["result"]["execution_trace"].startswith(
        "response_contains_tool_calls")


def test_policy_scores_valid_text_without_provider_specific_finish_reason(
        tmp_path: Path) -> None:
    selection = _selection(tmp_path)
    response = _response('{"timeout":17}')
    response["finish_reason"] = "end_turn"
    port = ScriptedLLMInputPort(selection, [response])

    result = _run_policy_trial_unchecked(
        run_dir=tmp_path / "provider-neutral-finish", request=_request(),
        selection=selection, llm_input_port=port)

    assert result["result"]["reward"] == 1
    assert result["result"]["execution_trace"].startswith("parsed")


def test_policy_scores_valid_text_when_finish_reason_is_absent(
        tmp_path: Path) -> None:
    selection = _selection(tmp_path)
    response = _response('{"timeout":17}')
    response.pop("finish_reason")
    port = ScriptedLLMInputPort(selection, [response])

    result = _run_policy_trial_unchecked(
        run_dir=tmp_path / "provider-neutral-no-finish", request=_request(),
        selection=selection, llm_input_port=port)

    assert result["result"]["reward"] == 1
    assert result["result"]["execution_trace"].startswith("parsed")


def test_policy_rejects_explicit_length_truncation(tmp_path: Path) -> None:
    selection = _selection(tmp_path)
    response = _response('{"timeout":17}')
    response["finish_reason"] = "length"
    port = ScriptedLLMInputPort(selection, [response])

    result = _run_policy_trial_unchecked(
        run_dir=tmp_path / "truncated", request=_request(),
        selection=selection, llm_input_port=port)

    assert result["result"]["reward"] == 0
    assert result["result"]["execution_trace"].startswith(
        "response_was_truncated")


def test_policy_source_cannot_import_or_execute_top_level_effects(
        tmp_path: Path) -> None:
    path = tmp_path / "policy.py"
    path.write_text(
        "import rpnh_rrsi.formal_policy\n"
        "rpnh_rrsi.formal_policy.BUCKET_ID = 'changed'\n"
        "def build_messages(raw):\n    return []\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="only build_messages"):
        _restricted_build_messages(path)


def _formal_request_fixture(tmp_path: Path) -> tuple[object, object, dict]:
    protocol = load_formal_protocol(
        Path(__file__).parents[1] / "protocol.example.json")
    selection = _selection(tmp_path)
    mapping = formal_protocol_mapping(protocol)
    task = mapping["task_manifests"]["calibration"]["tasks"][0]
    request = {
        "protocol_id": protocol.protocol_id,
        "split": "calibration",
        "evaluation_id": "calibration-h0",
        "candidate_id": "H0",
        "task_id": task["task_id"],
        "repetition": 0,
        "attempt": 0,
        "occurrence_id": (
            f"{protocol.protocol_id}:calibration-h0:{task['task_id']}:0:0"),
        "source_files": [{
            "path": item["path"], "content": item["content"],
            "mode": item["mode"],
        } for item in mapping["source_fixture"]["files"]],
        "raw_task_input": task["raw_input"],
        "expected": task["expected"],
        "weight": task["weight"],
    }
    return protocol, selection, request


def test_formal_boundary_rejects_task_tampering(tmp_path: Path) -> None:
    protocol, selection, request = _formal_request_fixture(tmp_path)
    assert _formal_request(protocol, request, selection)["expected"] == 15
    request["expected"] = 16
    with pytest.raises(ValueError, match="grading"):
        _formal_request(protocol, request, selection)


def test_public_formal_entrypoint_accepts_caller_run_root(tmp_path: Path) -> None:
    protocol, selection, request = _formal_request_fixture(tmp_path)
    port = ScriptedLLMInputPort(selection, [_response('{"timeout":15}')])
    result = run_policy_trial(
        run_dir=tmp_path / "public-entrypoint", protocol=protocol,
        request=request, selection=selection, llm_input_port=port)
    assert result["result"]["reward"] == 1
