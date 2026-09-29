from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
from cpn.components.agent_loop.models import AgentContextOverlay
from cpn.rpnh.agent_tasks import (
    AgentStage, AgentTaskSpec, agent_task_catalog, reopen_agent_task,
    run_agent_task,
)
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.runtime_policy import RuntimePolicy, WorkspacePolicy
from cpn.llm_adapters.config import LLMExecutionSelection


def _response(**values) -> LLMInputResponseBytes:
    payload = {"protocol": "llm_response_envelope/v1", "tool_calls": []}
    payload.update(values)
    return LLMInputResponseBytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        status_code=None, external_request_id=None)


def _completion_response(value: str) -> LLMInputResponseBytes:
    return _response(tool_calls=[{
        "id": "write-result", "name": "write_file",
        "arguments": json.dumps({
            "path": "outputs/result.txt",
            "description": "Offline context-compaction result.",
            "content": json.dumps(value),
            "output_port_id": "main.result", "outcome_id": "complete",
        }),
    }, {
        "id": "complete-result", "name": "complete_interaction",
        "arguments": "{}",
    }], finish_reason="tool_calls")


def _configure_offline_task(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port,
        *, context_window_tokens: int | None = None,
        runtime_policy: RuntimePolicy | None = None,
) -> Path:
    effective_runtime = runtime_policy or RuntimePolicy()
    port.execution_policy = {"runtime": effective_runtime.as_document()}
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-context-compaction",
        "argv": ["python", "-c", "pass"],
        "probe_argv": ["python", "-c", "pass"],
        "env": {}, "inherit_env": [],
    }), encoding="utf-8")
    selection = LLMExecutionSelection(
        LLMInputTarget(
            "offline-context-compaction", 128, 65536,
            context_window_tokens),
        "local_process", adapter_path, 30,
        effective_runtime)
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


class _LengthThenReplayPort:
    def __init__(self, *, owner_reentry_prompt: str | None = None) -> None:
        self.requests: list[dict] = []
        self.owner_reentry_prompt = owner_reentry_prompt

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        if len(self.requests) == 1:
            return _response(finish_reason="length")
        if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
            assert envelope["tools"]
            assert envelope["tool_choice"] == "auto"
            if self.owner_reentry_prompt is not None:
                assert envelope["messages"][-2] == {
                    "role": "system",
                    "content": self.owner_reentry_prompt,
                }
            return _response(
                text="Resume the interrupted semantic slot and finish it.",
                finish_reason="stop")
        assert any(
            "Resume the interrupted semantic slot" in message.get("content", "")
            for message in envelope["messages"])
        assert any(
            "agent_context_fact_capsule" in message.get("content", "")
            for message in envelope["messages"])
        if self.owner_reentry_prompt is not None:
            assert sum(
                message.get("content") == self.owner_reentry_prompt
                for message in envelope["messages"]) == 1
        return _completion_response("length replay complete")

    def close(self):
        pass


def test_length_interruption_compacts_before_same_slot_replay(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_reentry_prompt = (
        "OWNER_CHECKPOINT_REENTRY_INSTRUCTION: preserve accepted evidence.")
    from cpn.components.agent_loop.optional_execution import (
        OptionalAgentLoopRegistryService,
    )
    monkeypatch.setattr(
        OptionalAgentLoopRegistryService, "_owner_reentry_prompt",
        lambda _self: owner_reentry_prompt)
    port = _LengthThenReplayPort(
        owner_reentry_prompt=owner_reentry_prompt)
    execution_path = _configure_offline_task(
        tmp_path, monkeypatch, port,
        runtime_policy=RuntimePolicy(workspace=WorkspacePolicy(
            timeout_seconds=17,
            memory_bytes=268435456,
            process_limit=7,
            source_size_bytes=1048576,
            input_size_bytes=2097152,
        )))
    run_dir = tmp_path / "run"

    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir, prompt="Complete one offline task.",
        stages=(AgentStage("main", "Produce the requested result."),),
        execution_config_path=execution_path, max_attempts_per_stage=3,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "length replay complete"
    assert len(port.requests) == 3
    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    workspace_profiles = _objects(core, "numerical_tool_profile/v1")
    assert len(workspace_profiles) == 1
    assert {
        key: workspace_profiles[0][key]
        for key in (
            "timeout_seconds", "memory_bytes", "process_limit",
            "source_size_bytes", "input_size_bytes")
    } == {
        "timeout_seconds": 17,
        "memory_bytes": 268435456,
        "process_limit": 7,
        "source_size_bytes": 1048576,
        "input_size_bytes": 2097152,
    }
    interruptions = core.event_store.list_events_by_type(
        ("llm_invocation_interrupted/v1",))
    assert len(interruptions) == 1
    interruption = interruptions[0].payload
    assert interruption["finish_reason"] == "length"
    assert interruption["turn_sequence"] == 0

    compaction, = _objects(core, "agent_context_compaction/v3")
    core.catalog.validate_instance(
        "agent_context_compaction/v3", category="object",
        instance=compaction)
    assert compaction["trigger_reason"] == "response_length"
    assert compaction["covered_turn_refs"] == []
    assert [entry["kind"] for entry in compaction["replacement_history"]] == [
        "agent_context_fact_capsule", "compaction_summary"]
    assert compaction["interrupted_llm_invocation_attempt_ref"] == {
        key: interruption["llm_invocation_attempt_ref"][key]
        for key in ("entity_type", "logical_id", "version_id")}

    normal_invocations = [
        item for item in _objects(core, "llm_invocation_spec/v1")
        if item["invocation_kind"] == "normal_turn"]
    replay, = [
        item for item in normal_invocations
        if "replay_after_compaction_ref" in item]
    assert replay["turn_sequence"] == interruption["turn_sequence"]
    assert replay["replay_after_compaction_ref"] == (
        compaction["agent_context_compaction_ref"])
    assert replay["replay_interrupted_response_ref"] == (
        interruption["response_resource_ref"])


class _PressureRetryPort:
    def __init__(self) -> None:
        self.requests: list[tuple[object, dict]] = []

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append((attempt, envelope))
        checkpoint = (
            envelope["messages"][-1].get("content")
            == CONTEXT_CHECKPOINT_PROMPT)
        if len(self.requests) == 1:
            return _response(
                text="OLD-COVERED-BODY", finish_reason="stop",
                usage={"input_tokens": 950})
        if checkpoint and len([
                value for _attempt, value in self.requests
                if value["messages"][-1].get("content")
                == CONTEXT_CHECKPOINT_PROMPT]) == 1:
            return LLMInputResponseBytes(
                b'{"not":"a canonical response"}',
                status_code=None, external_request_id=None)
        if checkpoint:
            return _response(
                text="MODEL-SUMMARY-FOR-FORWARD-CONTINUATION",
                finish_reason="stop")
        combined = "\n".join(
            message.get("content", "") for message in envelope["messages"]
            if isinstance(message.get("content"), str))
        assert "OLD-COVERED-BODY" in combined
        assert "MODEL-SUMMARY-FOR-FORWARD-CONTINUATION" in combined
        assert "agent_context_fact_capsule" in combined
        return _completion_response("pressure compaction complete")

    def close(self):
        pass


class _WorkspaceThenCompactionOwnerLossPort:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.compaction_interrupted = False

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        checkpoint = (
            envelope["messages"][-1].get("content")
            == CONTEXT_CHECKPOINT_PROMPT)
        if checkpoint and not self.compaction_interrupted:
            self.compaction_interrupted = True
            raise KeyboardInterrupt("simulated owner loss during compaction")
        if len(self.requests) == 1:
            return _response(tool_calls=[{
                "id": "settled-workspace-write",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": (
                        "printf 'settled-before-owner-loss\\n' "
                        "> settled.txt"),
                    "timeout_seconds": 10,
                }),
            }], finish_reason="tool_calls", usage={"input_tokens": 950})
        if checkpoint:
            raise AssertionError("abandoned compaction was replayed")
        return _completion_response("checkpoint reentry complete")

    def close(self):
        pass


def test_checkpoint_reentry_finalizes_settled_workspace_before_interruption(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _WorkspaceThenCompactionOwnerLossPort()
    execution_path = _configure_offline_task(
        tmp_path, monkeypatch, port, context_window_tokens=1000)
    run_dir = tmp_path / "run"
    spec = AgentTaskSpec(
        run_dir=run_dir,
        prompt="Preserve a settled workspace action across owner loss.",
        stages=(AgentStage("main", "Produce the requested result."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=4,
    )

    with pytest.raises(
            KeyboardInterrupt, match="simulated owner loss during compaction"):
        run_agent_task(spec)

    interrupted = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    from cpn.rpnh.registry.checkpoint_reentry import committed_checkpoint_refs
    selected, = committed_checkpoint_refs(interrupted)

    result = reopen_agent_task(
        spec, checkpoint_version_id=str(selected.version_id),
        command_id="test-workspace-compaction-owner-loss-reentry",
        reason="Return to the selected cut after a settled workspace action.")

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "checkpoint reentry complete"
    assert len(port.requests) == 3
    final = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    revisions = _objects(final, "workspace_revision/v1")
    assert any(
        "settled.txt" in revision["inventory_paths"]
        and "settled.txt" in revision["changed_paths"]
        for revision in revisions)


class _RollingCompactionPort:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.normal_count = 0
        self.compaction_count = 0

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        checkpoint = (
            envelope["messages"][-1].get("content")
            == CONTEXT_CHECKPOINT_PROMPT)
        combined = "\n".join(
            message.get("content", "") for message in envelope["messages"]
            if isinstance(message.get("content"), str))
        if checkpoint:
            self.compaction_count += 1
            if self.compaction_count == 1:
                assert "SESSION-ZERO-TURN" in combined
                return _response(text="SUMMARY-ZERO", finish_reason="stop")
            assert self.compaction_count == 2
            assert "SUMMARY-ZERO" in combined
            assert "SESSION-ZERO-TURN" in combined
            assert "SESSION-ONE-TURN" in combined
            return _response(text="SUMMARY-ALL", finish_reason="stop")
        self.normal_count += 1
        if self.normal_count == 1:
            return _response(
                text="SESSION-ZERO-TURN", finish_reason="stop",
                usage={"input_tokens": 950})
        if self.normal_count == 2:
            assert "SUMMARY-ZERO" in combined
            assert "SESSION-ZERO-TURN" in combined
            return _response(
                text="SESSION-ONE-TURN", finish_reason="stop",
                usage={"input_tokens": 950})
        assert self.normal_count == 3
        assert "SUMMARY-ALL" in combined
        assert "SESSION-ZERO-TURN" in combined
        assert "SESSION-ONE-TURN" in combined
        return _completion_response("rolling compaction complete")

    def close(self):
        pass


class _LargeImmediateWorkspaceOutputPort:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        if len(self.requests) == 1:
            return _response(tool_calls=[{
                "id": "large-workspace-output",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": (
                        "python -c \"print('LIVE-FULL-' + "
                        "'X' * 20000 + '-FULL-END')\""),
                    "timeout_seconds": 10,
                }),
            }], finish_reason="tool_calls")
        tool_messages = [
            message for message in envelope["messages"]
            if message.get("role") == "tool"]
        assert len(tool_messages) == 1
        visible = tool_messages[0]["content"]
        assert len(visible.encode("utf-8")) <= 512
        assert "bytes omitted from model-visible tool output" in visible
        assert "agent_action_ref" in visible
        assert "-FULL-END" in visible
        return _completion_response("bounded immediate output complete")

    def close(self):
        pass


def test_immediate_workspace_output_uses_configured_model_visible_limit(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _LargeImmediateWorkspaceOutputPort()
    execution_path = _configure_offline_task(
        tmp_path, monkeypatch, port,
        runtime_policy=RuntimePolicy(
            context_tool_output_byte_limit=512))
    run_dir = tmp_path / "run"

    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir, prompt="Exercise bounded workspace output.",
        stages=(AgentStage("main", "Produce the requested result."),),
        execution_config_path=execution_path, max_attempts_per_stage=3,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "bounded immediate output complete"
    assert len(port.requests) == 2
    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    workspace_actions = [
        item for item in _objects(core, "agent_action/v2")
        if item["tool_name"] == "workspace"]
    assert len(workspace_actions) == 1
    metadata = workspace_actions[0]["result_metadata"]
    assert metadata["output_truncated"] is False
    assert metadata["stdout"].startswith("LIVE-FULL-")
    assert metadata["stdout"].rstrip().endswith("-FULL-END")
    assert len(metadata["stdout"]) > 20_000


def test_pressure_projection_retry_identity_and_effective_history(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _PressureRetryPort()
    execution_path = _configure_offline_task(
        tmp_path, monkeypatch, port, context_window_tokens=1000)
    run_dir = tmp_path / "run"

    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir, prompt="Exercise projected context pressure.",
        stages=(AgentStage("main", "Produce the requested result."),),
        # Initial turn + rejected compaction + retried compaction + final turn.
        execution_config_path=execution_path, max_attempts_per_stage=4,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "pressure compaction complete"
    checkpoint_attempts = [
        attempt for attempt, envelope in port.requests
        if envelope["messages"][-1].get("content")
        == CONTEXT_CHECKPOINT_PROMPT]
    assert [attempt.attempt_ordinal for attempt in checkpoint_attempts] == [0, 1]
    assert (checkpoint_attempts[0].invocation_ref
            == checkpoint_attempts[1].invocation_ref)
    assert (checkpoint_attempts[0].attempt_ref
            != checkpoint_attempts[1].attempt_ref)

    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    compaction, = _objects(core, "agent_context_compaction/v3")
    core.catalog.validate_instance(
        "agent_context_compaction/v3", category="object",
        instance=compaction)
    assert compaction["trigger_reason"] == "context_pressure"
    assert compaction["source_context_session_ordinal"] == 0
    assert compaction["context_session_ordinal"] == 1
    assert compaction["first_turn_sequence"] == 0
    assert compaction["last_turn_sequence"] == 0
    assert len(compaction["covered_turn_refs"]) == 1
    capsule, summary, retained = compaction["replacement_history"]
    assert capsule["kind"] == "agent_context_fact_capsule"
    assert capsule["covered_turn_refs"] == compaction["covered_turn_refs"]
    assert capsule["source_context_session_ordinal"] == 0
    assert capsule["context_session_ordinal"] == 1
    assert summary == {
        "kind": "compaction_summary",
        "content": "MODEL-SUMMARY-FOR-FORWARD-CONTINUATION",
    }
    assert retained == {
        "kind": "retained_model_visible_message",
        "message": {"role": "assistant", "content": "OLD-COVERED-BODY"},
    }

    # A replacement overlay may cover an arbitrarily long immutable turn
    # prefix.  The Registry retains every original turn while the model sees
    # one summary prefix plus a bounded recent-history tail.
    forty_turn_refs = [{
        "entity_type": "agent_turn/v1",
        "logical_id": f"agent_turn:{index + 1:032x}",
        "version_id": f"agent_turn_version:{index + 101:032x}",
    } for index in range(40)]
    expanded = json.loads(json.dumps(compaction))
    expanded.update({
        "first_turn_sequence": 0,
        "last_turn_sequence": 39,
        "covered_turn_refs": forty_turn_refs,
    })
    expanded_capsule = expanded["replacement_history"][0]
    expanded_capsule.update({
        "first_turn_sequence": 0,
        "last_turn_sequence": 39,
        "covered_turn_count": 40,
        "covered_turn_refs": forty_turn_refs,
    })
    core.catalog.validate_instance(
        "agent_context_compaction/v3", category="object",
        instance=expanded)
    overlay = AgentContextOverlay(
        compaction_ref=_version_from_payload(
            expanded["agent_context_compaction_ref"]),
        loop_id=expanded["agent_loop_ref"]["logical_id"],
        source_context_session_ordinal=0,
        context_session_ordinal=1,
        trigger_reason=expanded["trigger_reason"],
        first_turn_sequence=0,
        last_turn_sequence=39,
        covered_turn_refs=tuple(
            _version_from_payload(ref) for ref in forty_turn_refs),
        replacement_history=tuple(expanded["replacement_history"]),
        llm_invocation_ref=_version_from_payload(
            expanded["llm_invocation_ref"]),
        llm_invocation_attempt_ref=_version_from_payload(
            expanded["llm_invocation_attempt_ref"]),
    )
    assert len(overlay.covered_turn_refs) == 40
    assert len(overlay.model_visible_messages) == 3

    failed = core.event_store.list_events_by_type(
        ("llm_invocation_failed/v1",))
    assert len(failed) == 1
    assert failed[0].payload["disposition"] == "protocol_rejected"
    assert failed[0].payload["next_attempt_allowed"] is True


def test_repeated_compaction_opens_new_context_sessions_and_keeps_recent_turns(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _RollingCompactionPort()
    execution_path = _configure_offline_task(
        tmp_path, monkeypatch, port, context_window_tokens=1000)
    run_dir = tmp_path / "run"

    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir, prompt="Exercise rolling context sessions.",
        stages=(AgentStage("main", "Produce the requested result."),),
        execution_config_path=execution_path, max_attempts_per_stage=6,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "rolling compaction complete"
    assert port.compaction_count == 2
    core = _RegistryCore(
        run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    compactions = _objects(core, "agent_context_compaction/v3")
    compactions.sort(key=lambda item: item["context_session_ordinal"])
    assert [
        (item["source_context_session_ordinal"],
         item["context_session_ordinal"])
        for item in compactions
    ] == [(0, 1), (1, 2)]
    assert [len(item["covered_turn_refs"]) for item in compactions] == [1, 2]
    assert len({
        item["agent_loop_ref"]["logical_id"] for item in compactions
    }) == 1
    assert len({
        item["replacement_history"][0]["transition_firing_ref"]["logical_id"]
        for item in compactions
    }) == 1
    assert [
        entry["message"]["content"]
        for entry in compactions[-1]["replacement_history"][2:]
    ] == ["SESSION-ZERO-TURN", "SESSION-ONE-TURN"]
