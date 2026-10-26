"""Configured group budgets for escaped executed read previews; no Registry runs."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.compact import (
    TOOL_BATCH_BYTE_LIMIT, bound_tool_result_groups, build_replacement_history,
)
from cpn.components.default_agent_executor import _agent_context_policy
from cpn.rpnh.runtime_policy import RuntimePolicy, runtime_policy_from_document


def serialized(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def executed_read():
    # A legal32k character preview: nested JSON and escaping exceed40k wire bytes.
    preview = (json.dumps({"component": "batch_0", "text": 'quoted"\\path',
                           "links": []}) + "\n") * 1024
    preview = preview[:32768]
    arguments = json.dumps({"path": "registered_resources/request/content", "max_chars": 32768})
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "read-actual-style", "type": "function", "function": {
                "name": "read_file", "arguments": arguments}}]},
        {"role": "tool", "tool_call_id": "read-actual-style", "content": json.dumps({
            "kind": "registered_file_read/v1", "content": preview,
            "resource_ref": {"resource_id": "resource:" + "a" * 32,
                             "resource_version_id": "resource_version:" + "b" * 32}})},
    ]


def settings(limit):
    policy = RuntimePolicy(context_tool_output_byte_limit=limit)
    loaded = runtime_policy_from_document(policy.as_document())
    assert loaded == policy
    reduction, _ = _agent_context_policy(SimpleNamespace(execution_policy={"runtime": loaded.as_document()}))
    return reduction


@pytest.mark.parametrize("path", ["next_turn", "compaction_request"])
def test_configured_read_preview_survives_direct_context_paths(monkeypatch, path):
    from cpn.components.agent_loop import context, compaction
    messages = executed_read()
    original = deepcopy(messages)
    assert TOOL_BATCH_BYTE_LIMIT < len(serialized(messages)) < 262144
    captured = []
    class StopAfterHistory(Exception):
        pass
    def stop(_):
        raise StopAfterHistory
    catalog_ref = object()
    loop = SimpleNamespace(tool_catalog_ref=catalog_ref)
    host = SimpleNamespace(
        _execution=lambda execution, loop: execution,
        _context=lambda loop: None,
        _normal_history_reasoning_replay=lambda context, loop: False,
        _declared=lambda context: ({"llm_input_target_ref": {}}, None, None),
        _static=lambda context, name: (catalog_ref, None, b'{"messages":[]}'),
        _target=stop,
        prepare_agent_system_initialization_v1=lambda *args: SimpleNamespace(payload=b"system", located_inputs=()),
        mechanical_lifecycle=SimpleNamespace(history_messages=lambda *args, **kwargs: messages,
                                            turn_events=lambda loop: ()),
    )
    module = context if path == "next_turn" else compaction
    def bounded(*args, **kwargs):
        result = bound_tool_result_groups(*args, **kwargs)
        captured.append(result)
        return result
    monkeypatch.setattr(module, "bound_tool_result_groups", bounded)
    monkeypatch.setattr(context, "_resource_from_payload", lambda value: value)
    def prepare(reduction):
        if path == "next_turn":
            return context.ContextExecutionMixin.prepare_agent_turn_context_v1(
                host, object(), loop, object(), tool_output_byte_limit=reduction.tool_output_byte_limit)
        return compaction.CompactionExecutionMixin._compaction_envelope(
            host, object(), loop, object(), reduction)
    # The previously fixed40k group bound rejects the untruncated configured preview.
    with pytest.raises(ValueError, match="minimum call/result envelopes"):
        bound_tool_result_groups(messages)
    # Default result reduction still fits the unchanged40k group budget.
    with pytest.raises(StopAfterHistory):
        prepare(settings(10000))
    assert len(serialized(captured[-1])) <= TOOL_BATCH_BYTE_LIMIT
    assert captured[-1][0] == original[0]
    with pytest.raises(StopAfterHistory):
        prepare(settings(262144))
    assert list(captured[-1]) == original
    assert messages == original


@pytest.mark.parametrize("pending", [True, False])
def test_configured_replacement_preserves_read_arguments_result_and_archive(pending):
    messages = executed_read()
    original = deepcopy(messages)
    capsule = {"kind": "agent_context_fact_capsule", "schema_version": "agent_context_fact_capsule/v1",
               "result_archive": {"reader": "read_managed_output", "locator": "original-registry"}}
    kwargs = dict(retained_history_token_limit=1 if pending else 100000,
                  fact_capsule=capsule, pending_tool_call_ids={"read-actual-style"} if pending else frozenset())
    with pytest.raises(ValueError, match="minimum call/result envelopes"):
        build_replacement_history(messages, "Continue the original task.", **kwargs)
    replacement = build_replacement_history(messages, "Continue the original task.", **kwargs,
        tool_output_byte_limit=settings(262144).tool_output_byte_limit)
    retained = [item["message"] for item in replacement[2:]]
    assert retained == original
    assert replacement[0]["result_archive"] == capsule["result_archive"]
    assert messages == original
    # Raising the group budget does not relax the independent token-tail limit.
    retired = build_replacement_history(messages, "Result already consumed.",
        retained_history_token_limit=1, fact_capsule=capsule, tool_output_byte_limit=262144)
    assert len(retired) == 2


def test_configured_group_still_rejects_oversized_executed_arguments():
    messages = executed_read()
    messages[0]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "path": "plan.json", "content": "x" * 270000})
    messages[0]["tool_calls"][0]["function"]["name"] = "write_file"
    original = deepcopy(messages)
    with pytest.raises(ValueError, match="minimum call/result envelopes"):
        build_replacement_history(messages, "No partial executed call.",
            retained_history_token_limit=1,
            fact_capsule={"kind": "agent_context_fact_capsule", "schema_version": "agent_context_fact_capsule/v1"},
            pending_tool_call_ids={"read-actual-style"}, tool_output_byte_limit=262144)
    assert messages == original
