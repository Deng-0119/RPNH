"""Owner delivery is once; the delivered instruction remains turn history."""
import json

import pytest

from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.llm_contracts import LLMInputResponseBytes
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.run import RunOwner
from test_optional_context_compaction import (
    _completion_response, _configure_offline_task, _response,
)


@pytest.mark.parametrize("context_window", [None, 1000, 10000],
                         ids=["ordinary", "compaction-trimmed", "compaction-retained"])
def test_native_feedback_survives_tool_turn_without_redelivery(
        tmp_path, monkeypatch, context_window):
    compact = context_window is not None
    # Equal text in distinct commands must keep its original multiplicity.
    feedback = ["Retain owner constraint A.", "Retain owner constraint B.",
                "Retain owner constraint A."]
    late = "Apply this only to the next newly prepared request."
    original_boundary = RunOwner.request_boundary
    selections = []

    def boundary(owner, context, *, request_id):
        if not selections:
            for index, text in enumerate(feedback):
                assert owner.control.message(
                    {"target": "main.run", "body": text},
                    f"feedback-{index}")["status"] == "QUEUED"
        selected = original_boundary(owner, context, request_id=request_id)
        if not selections:
            owner.control.message({"target": "main.run", "body": late}, "late")
            # An already selected request must never acquire later input.
            assert original_boundary(owner, context, request_id=request_id) == selected
        selections.append(selected)
        return selected

    monkeypatch.setattr(RunOwner, "request_boundary", boundary)

    class Port:
        def __init__(self):
            self.requests = []
            self.checkpoints = []
            self.normal = []

        def request_once(self, attempt):
            envelope = json.loads(attempt.canonical_request_bytes)
            self.requests.append(envelope)
            if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
                self.checkpoints.append(attempt)
                if len(self.checkpoints) == 1:
                    # Exercise immutable compaction retry after protocol rejection.
                    return LLMInputResponseBytes(b'{"invalid":true}',
                        status_code=None, external_request_id=None)
                return _response(text="Keep the owner constraints and continue.",
                                 finish_reason="stop")
            self.normal.append(envelope)
            if len(self.normal) == 1:
                located = [json.loads(line[2:])["sandbox_path"]
                           for line in envelope["messages"][0]["content"].splitlines()
                           if line.startswith("- {")
                           and "sandbox_path" in json.loads(line[2:])]
                assert located
                return _response(tool_calls=[{
                    "id": "read-input", "name": "read_file",
                    "arguments": json.dumps({"path": located[0]}),
                }], finish_reason="tool_calls",
                    **({"usage": {"input_tokens": context_window * 95 // 100}}
                       if compact else {}))
            return _completion_response("feedback retained")

        def close(self):
            pass

    port = Port()
    selection = _configure_offline_task(
        tmp_path, monkeypatch, port,
        context_window_tokens=context_window)
    result = run_agent_task(AgentTaskSpec(
        run_dir=tmp_path / "run", prompt="Read the supplied input and finish.",
        stages=(AgentStage("main", "Apply owner feedback."),),
        execution_config_path=selection, max_attempts_per_stage=4))
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "feedback retained"
    assert len(port.normal) == 2
    first, second = [request["messages"] for request in port.normal]
    assert first[-3:] == [{"role": "user", "content": text} for text in feedback]
    expected = [{"role": "user", "content": text} for text in feedback]
    positions = [index for index, message in enumerate(second)
                 if message in expected]
    # A 1000-token model grants a 250-token retained tail. The pending tool
    # group survives, while feedback is summarized under the existing policy.
    # Covered turns must not reintroduce feedback discarded by that overlay.
    assert [second[index] for index in positions] == (
        [] if context_window == 1000 else expected)
    assistant_index = next(index for index, message in enumerate(second)
                           if message.get("tool_calls", [{}])[0].get("id") == "read-input")
    if positions:
        assert positions[-1] < assistant_index
    assert second[assistant_index + 1]["role"] == "tool"
    read_result = json.loads(second[assistant_index + 1]["content"])
    assert read_result["result_kind" if compact else "kind"] == "registered_file_read/v1"
    assert second[-1] == {"role": "user", "content": late}
    assert [item["messages"] for item in selections] == [feedback, [late]]
    if compact:
        assert len(port.checkpoints) == 2
        assert port.checkpoints[0].canonical_request_bytes == port.checkpoints[1].canonical_request_bytes
        for attempt in port.checkpoints:
            messages = json.loads(attempt.canonical_request_bytes)["messages"]
            assert [message for message in messages if message in expected] == expected
            assert not any(message.get("content") == late for message in messages)

    core = _RegistryCore(tmp_path / "run", create=False, read_only=True,
                         catalog=agent_task_catalog())
    invocations = [json.loads(row["metadata_json"]) for row in
                   core.event_store.object_rows_by_type("llm_invocation_spec/v1")]
    ordinary_refs = {item["llm_invocation_ref"]["version_id"] for item in invocations
                     if item["invocation_kind"] == "normal_turn"}
    turn_events = core.event_store.list_events_by_type(("agent_turn_recorded/v2",))
    assert len(turn_events) == 2
    assert {event.payload["llm_invocation_ref"]["version_id"] for event in turn_events} == ordinary_refs
    # Checkpoint requests have a terminal system instruction but never become
    # ordinary turn history; their separate completion produces the overlay.
    assert all(str(attempt.invocation_ref.version_id) not in ordinary_refs
               for attempt in port.checkpoints)
    receipts = []
    for row in core.event_store.canonical_object_rows(object_type="resource_version/v1"):
        if json.loads(row["metadata_json"]).get("content_schema_ref") != "rpnh/owner_control/v1":
            continue
        resource = core.get_version(TypedId.parse(row["version_id"], expected="resource_version"))
        body = json.loads(core.object_store.read_registered(resource))
        if body["kind"] == "receipt":
            receipts.append(body["data"])
    delivered = [item for item in receipts if item["status"] == "DELIVERED"]
    assert len(delivered) == 4
    assert len({item["message_ref"]["version_id"] for item in delivered}) == 4
    assert [item["messages"] for item in receipts if item["status"] == "REQUEST_INPUTS"] == [feedback, [late]]


def test_length_interrupted_feedback_reaches_checkpoint_retry_and_replay(
        tmp_path, monkeypatch):
    from cpn.components.agent_loop.optional_execution import OptionalAgentLoopRegistryService

    feedback = ["Preserve the owner-approved input.", "Do not repeat settled work."]
    reentry = "OWNER_REENTRY: retain the accepted checkpoint."
    monkeypatch.setattr(OptionalAgentLoopRegistryService, "_owner_reentry_prompt",
                        lambda _self: reentry)
    selections = []
    original_boundary = RunOwner.request_boundary

    def boundary(owner, context, *, request_id):
        if not selections:
            for index, text in enumerate(feedback):
                assert owner.control.message(
                    {"target": "main.run", "body": text},
                    f"length-feedback-{index}")["status"] == "QUEUED"
        selected = original_boundary(owner, context, request_id=request_id)
        selections.append(selected)
        return selected

    monkeypatch.setattr(RunOwner, "request_boundary", boundary)

    class Port:
        def __init__(self):
            self.normal = []
            self.checkpoints = []

        def request_once(self, attempt):
            messages = json.loads(attempt.canonical_request_bytes)["messages"]
            if messages[-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
                self.checkpoints.append(attempt)
                if len(self.checkpoints) == 1:
                    return LLMInputResponseBytes(b'{"invalid":true}',
                        status_code=None, external_request_id=None)
                return _response(text="Continue using the owner-approved input.",
                                 finish_reason="stop")
            self.normal.append(attempt)
            if len(self.normal) == 1:
                return _response(text="TRUNCATED_ASSISTANT_NOT_HISTORY",
                                 finish_reason="length")
            return _completion_response("length feedback retained")

        def close(self):
            pass

    port = Port()
    selection = _configure_offline_task(tmp_path, monkeypatch, port)
    result = run_agent_task(AgentTaskSpec(
        run_dir=tmp_path / "run", prompt="Finish using the accepted input.",
        stages=(AgentStage("main", "Apply owner feedback."),),
        execution_config_path=selection, max_attempts_per_stage=4))
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "length feedback retained"
    assert len(port.normal) == len(port.checkpoints) == 2
    expected = [{"role": "user", "content": text} for text in feedback]
    for attempt in [port.normal[0], *port.checkpoints, port.normal[1]]:
        messages = json.loads(attempt.canonical_request_bytes)["messages"]
        assert [message for message in messages if message in expected] == expected
        assert all(message.get("content") != "TRUNCATED_ASSISTANT_NOT_HISTORY"
                   for message in messages)
        assert not any(message.get("tool_calls") for message in messages)
    for attempt in port.checkpoints:
        messages = json.loads(attempt.canonical_request_bytes)["messages"]
        assert messages[-2:] == [{"role": "system", "content": reentry},
                                {"role": "system", "content": CONTEXT_CHECKPOINT_PROMPT}]
    assert port.checkpoints[0].invocation_ref == port.checkpoints[1].invocation_ref
    assert port.checkpoints[0].canonical_request_bytes == port.checkpoints[1].canonical_request_bytes
    assert [item["messages"] for item in selections] == [feedback, []]
    assert selections[0]["request_id"] != selections[1]["request_id"]
    assert ":after-compaction:" in selections[1]["request_id"]

    core = _RegistryCore(tmp_path / "run", create=False, read_only=True,
                         catalog=agent_task_catalog())
    turn_events = core.event_store.list_events_by_type(("agent_turn_recorded/v2",))
    assert len(turn_events) == 1
    assert turn_events[0].payload["sequence"] == 0
    compaction, = [json.loads(row["metadata_json"]) for row in
                   core.event_store.object_rows_by_type("agent_context_compaction/v3")]
    assert compaction["covered_turn_refs"] == []
    retained = [entry["message"] for entry in compaction["replacement_history"]
                if entry["kind"] == "retained_model_visible_message"]
    assert retained == expected
    delivered = []
    for row in core.event_store.canonical_object_rows(object_type="resource_version/v1"):
        if json.loads(row["metadata_json"]).get("content_schema_ref") != "rpnh/owner_control/v1":
            continue
        resource = core.get_version(TypedId.parse(row["version_id"], expected="resource_version"))
        body = json.loads(core.object_store.read_registered(resource))
        if body["kind"] == "receipt" and body["data"]["status"] == "DELIVERED":
            delivered.append(body["data"]["message_ref"]["version_id"])
    assert len(delivered) == len(set(delivered)) == 2
