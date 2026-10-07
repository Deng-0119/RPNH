"""Managed output reachability using synthetic values and a recording provider."""
import json
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.managed_output import (
    bounded_managed_output_projection, bound_managed_output_page,
    render_managed_output, serialized_managed_json, validate_managed_output_page,
)
from cpn.components.agent_loop.compact import (
    build_replacement_history, reduce_tool_messages, CONTEXT_CHECKPOINT_PROMPT,
)
from cpn.components.agent_loop.models import AgentLoopState
from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin


ACTION = {"entity_type": "agent_action/v3", "logical_id": "agent_action:" + "a" * 32,
          "version_id": "agent_action_version:" + "b" * 32}
RECEIPT = {"resource_id": "resource:" + "c" * 32,
           "resource_version_id": "resource_version:" + "d" * 32}


def _metadata(output):
    return {"kind": "managed_native_plugin_result/v1", "output": output,
            "terminal_receipt_ref": RECEIPT}


def _args(**extra):
    return {"agent_action_ref": ACTION, "terminal_receipt_ref": RECEIPT, **extra}


@pytest.mark.parametrize("output", [None, "", 123, False, "中😀\"\\\n" * 500,
                                   {"nested": [None, {"z": "\t\"\\", "a": "中文"}]},
                                   "left" * 20000 + "UNIQUE-MIDDLE-ID" + "right" * 20000])
def test_pages_are_lossless_and_whole_serialized_budgeted(output):
    offset, parts = 0, []
    while True:
        page = bounded_managed_output_projection(_metadata(output), _args(offset_chars=offset, max_bytes=701))
        validate_managed_output_page(page)
        assert len(serialized_managed_json(page).encode("utf-8")) <= 701
        assert page["offset_chars"] == offset
        assert page["agent_action_ref"] == ACTION
        parts.append(page["content"])
        if page["next_offset_chars"] is None:
            break
        assert page["next_offset_chars"] == offset + len(page["content"])
        offset = page["next_offset_chars"]
    assert "".join(parts) == serialized_managed_json(output)
    assert json.loads("".join(parts)) == output
    eof = bounded_managed_output_projection(_metadata(output), _args(offset_chars=page["total_chars"], max_bytes=701))
    assert eof["content"] == "" and eof["next_offset_chars"] is None


@pytest.mark.parametrize("change", [
    {"offset_chars": -1}, {"offset_chars": True}, {"offset_chars": 999},
    {"max_bytes": 0}, {"max_bytes": True}, {"max_bytes": 128},
    {"terminal_receipt_ref": dict(RECEIPT, resource_id="resource:" + "e" * 32)},
    {"agent_action_ref": dict(ACTION, entity_type="agent_action/v2")},
    {"agent_action_ref": dict(ACTION, version_id="agent_action_version:bad")},
    {"unknown": "field"},
])
def test_invalid_or_unbudgetable_pages_reject(change):
    with pytest.raises(ValueError):
        bounded_managed_output_projection(_metadata("value"), _args(**change))


def test_page_reduction_keeps_exact_locator_and_continuation():
    source = "中\\\n" * 10000
    original = bounded_managed_output_projection(_metadata(source), _args(max_bytes=8000))
    reduced, = reduce_tool_messages([{"role": "tool", "tool_call_id": "read", "content": serialized_managed_json(original)}], byte_limit=700)
    page = json.loads(reduced["content"])
    assert len(reduced["content"].encode("utf-8")) <= 700
    assert page == bound_managed_output_page(original, 700)
    following = bounded_managed_output_projection(_metadata(source), _args(offset_chars=page["next_offset_chars"], max_bytes=700))
    assert page["content"] + following["content"] == serialized_managed_json(source)[:following["next_offset_chars"]]
    with pytest.raises(ValueError, match="minimum locator"):
        reduce_tool_messages([reduced], byte_limit=128)


def test_reader_absent_keeps_small_body_and_rejects_unreachable_large_body():
    output = render_managed_output(_metadata("small-UNIQUE-ID"), ACTION, reader_available=False, max_bytes=1000)
    assert output["output"] == "small-UNIQUE-ID" and output["reader"] is None
    assert output["agent_action_ref"] == ACTION
    with pytest.raises(ValueError, match="not exposed"):
        render_managed_output(_metadata("large" * 1000), ACTION, reader_available=False, max_bytes=1000)


def _group(call_id, result):
    return [{"role": "assistant", "content": "", "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": "managed", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": call_id, "content": serialized_managed_json(result)}]


def test_replacement_preserves_multiple_managed_groups_beyond_recent_tail():
    first = render_managed_output(_metadata("new-small-id"), ACTION, reader_available=False, max_bytes=1000)
    second = bounded_managed_output_projection(_metadata("large" * 20000), _args(max_bytes=700))
    history = _group("small", first) + _group("large", second)
    capsule = {"kind": "agent_context_fact_capsule", "schema_version": "agent_context_fact_capsule/v1"}
    replacement = build_replacement_history(history, "summary omits both results", retained_history_token_limit=1, fact_capsule=capsule)
    assert [e["message"] for e in replacement[2:]] == history
    again = build_replacement_history([e["message"] for e in replacement[2:]], "second replacement", retained_history_token_limit=1, fact_capsule=capsule)
    assert [e["message"] for e in again[2:]] == history


def _reader(source):
    return SimpleNamespace(_execution=lambda *a: None,
                           mechanical_lifecycle=SimpleNamespace(hydrate_action=lambda ref: source))


@pytest.mark.parametrize("change", [
    {"loop_id": "other"}, {"turn_sequence": 2},
    {"state": AgentLoopState.ACTION_REJECTED},
    {"managed_action": {"outcome": "outcome_unknown", "terminal_receipt_ref": RECEIPT}},
    {"managed_action": {"outcome": "failed", "terminal_receipt_ref": RECEIPT}},
    {"managed_action": None}, {"tool_error_ref": "error"},
])
def test_reader_rejects_cross_loop_early_failed_and_unknown(change):
    source = SimpleNamespace(loop_id="loop", turn_sequence=1, state=AgentLoopState.ACTION_APPLIED,
                             managed_action={"outcome": "returned", "terminal_receipt_ref": RECEIPT},
                             tool_error_ref=None, result_metadata=_metadata("value"))
    source.__dict__.update(change)
    with pytest.raises(ValueError, match="earlier settled returned"):
        WorkspaceExecutionMixin._read_managed_output(_reader(source), None, SimpleNamespace(loop_id="loop"),
                                                     SimpleNamespace(sequence=2), _args(), "read")


def test_reader_uses_only_immutable_action_output_and_never_dispatches():
    source = SimpleNamespace(loop_id="loop", turn_sequence=1, state=AgentLoopState.ACTION_APPLIED,
                             managed_action={"outcome": "returned", "terminal_receipt_ref": RECEIPT},
                             tool_error_ref=None, result_metadata=_metadata(None))
    refs, page = WorkspaceExecutionMixin._read_managed_output(_reader(source), None, SimpleNamespace(loop_id="loop"),
                                                            SimpleNamespace(sequence=2), _args(), "read")
    assert page["content"] == "null" and len(refs) == 1
    assert set(source.result_metadata) == {"kind", "output", "terminal_receipt_ref"}


def test_legacy_v2_stdout_stderr_projection_is_unchanged():
    from cpn.components.agent_loop.tool_projection import bounded_agent_action_output_projection
    result = {"kind": "workspace_execution/v1", "status": "completed", "exit_code": 0,
              "stdout": "old-stdout", "stderr": "old-stderr", "output_truncated": False, "command_started": True}
    for stream in ("stdout", "stderr"):
        page = bounded_agent_action_output_projection(result, {
            "agent_action_ref": dict(ACTION, entity_type="agent_action/v2"), "stream": stream, "offset_chars": 4, "max_chars": 3})
        assert page["content"] == result[stream][4:7] and page["next_offset_chars"] == 7


@pytest.mark.parametrize("result_limit", [4 * 1024 * 1024, 16 * 1024 * 1024])
def test_pages_near_office_and_ab_result_limits(result_limit):
    # Native Office and AB output limits; only synthetic JSON is materialized.
    output = "A" * (result_limit // 2) + "BOUNDARY-MIDDLE-ID" + "Z" * (result_limit // 2 - 128)
    metadata = _metadata(output)
    assert len(serialized_managed_json(output).encode("utf-8")) < result_limit
    first = bounded_managed_output_projection(metadata, _args(max_bytes=10000))
    middle = bounded_managed_output_projection(metadata, _args(offset_chars=result_limit // 2, max_bytes=10000))
    assert first["truncated"] and "BOUNDARY-MIDDLE-ID" not in first["content"]
    assert "BOUNDARY-MIDDLE-ID" in middle["content"]
    assert len(serialized_managed_json(middle).encode("utf-8")) <= 10000


@pytest.mark.parametrize("tamper", [None, "offset", "receipt", "continuation", "budget"])
def test_closed_saved_reader_record_rejects_tampered_page(tamper):
    from cpn.components.agent_loop.models import AgentActionRecord, AgentLoopProtocolError, stable_action_id
    from cpn.rpnh.registry.publication import _version_from_payload
    loop_id, call_id = "agent_loop:" + "e" * 32, "read-exact"
    arguments = _args()
    page = bounded_managed_output_projection(_metadata("closed-page"), arguments)
    if tamper == "offset":
        page = bounded_managed_output_projection(_metadata("closed-page"), _args(offset_chars=1))
    elif tamper == "receipt":
        page["terminal_receipt_ref"] = dict(RECEIPT, resource_id="resource:" + "f" * 32)
    elif tamper == "continuation":
        page["next_offset_chars"] = 1
    elif tamper == "budget":
        arguments["max_bytes"] = len(serialized_managed_json(page).encode("utf-8")) - 1

    def construct():
        return AgentActionRecord(
            action_id=stable_action_id(loop_id, 1, call_id), loop_id=loop_id, turn_sequence=1,
            tool_call_ordinal=0, tool_call_id=call_id, action_identity_kind="tool_call_id",
            action_identity_key=call_id, tool_name="read_managed_output", raw_arguments=serialized_managed_json(arguments),
            arguments=arguments, expected_revision=1, state=AgentLoopState.ACTION_APPLIED,
            result_refs=(_version_from_payload(ACTION),), result_metadata=page)

    if tamper:
        with pytest.raises(AgentLoopProtocolError):
            construct()
    else:
        assert construct().result_metadata == page


def synthetic_output(context, arguments):
    return {"unique_id": "FIRST-PRE-REQUEST-UNIQUE-ID"}


def _explicit_graph(reader):
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowEndpoint as E, AgentWorkflowExecution,
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
    )
    tools = ("complete_interaction", "read_managed_output", "write_file") if reader else ("complete_interaction", "write_file")
    return AgentWorkflowGraph((N("main", "Get synthetic output then finish.",
        (P("request", "task"),), (P("result", "result"),), AgentWorkflowExecution(tools=tools)),),
        (), E("main", "request"), E("main", "result"))


def _finish():
    from test_optional_context_compaction import _response
    return _response(tool_calls=[{
        "id": "write-result", "name": "write_file", "arguments": json.dumps({
            "path": "outputs/result.txt", "description": "Synthetic result",
            "content": json.dumps("managed survival verified"),
            "output_port_id": "team.result", "outcome_id": "complete"})},
        {"id": "complete-result", "name": "complete_interaction", "arguments": "{}"}], finish_reason="tool_calls")


def _configure_pressure(tmp_path, monkeypatch, port):
    from dataclasses import replace
    from test_optional_context_compaction import _configure_offline_task
    from cpn.rpnh import agent_tasks
    config = _configure_offline_task(tmp_path, monkeypatch, port, context_window_tokens=1000)
    selection = agent_tasks.load_llm_execution_selection(config)
    selection = replace(selection, input_target=replace(selection.input_target, context_compaction_retained_tokens=1))
    monkeypatch.setattr(agent_tasks, "load_llm_execution_selection", lambda _path: selection)
    return config


class RecordingCompactionProvider:
    def __init__(self):
        self.requests = []

    def request_once(self, attempt):
        from test_optional_context_compaction import _response
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        if len(self.requests) == 1:
            return _response(tool_calls=[{"id": "synthetic-once", "name": "synthetic", "arguments": "{}"}], finish_reason="tool_calls")
        tools = [json.loads(m["content"]) for m in envelope["messages"] if m.get("role") == "tool"]
        assert any(t.get("output", {}).get("unique_id") == "FIRST-PRE-REQUEST-UNIQUE-ID" for t in tools)
        assert "read_managed_output" not in [t["function"]["name"] for t in envelope["tools"]]
        if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
            assert len(self.requests) == 2  # No ordinary post-tool request preceded compaction.
            return _response(text="Summary deliberately contains no unique ID.", finish_reason="stop")
        assert len(self.requests) == 3
        return _finish()

    def close(self):
        pass


def test_small_result_survives_first_pre_request_compaction_at_actual_provider(tmp_path, monkeypatch):
    from test_optional_context_compaction import _objects
    from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
    from cpn.rpnh.agent_tasks import AgentTaskSpec, agent_task_catalog, run_agent_task
    from cpn.rpnh.registry._registry import _RegistryCore
    calls = []
    selected = PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "1", (
        PluginOperation("output", "Synthetic unique output", {"type": "object", "properties": {}, "additionalProperties": False}, {}, synthetic_output),)), {}),))
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _document: selected)

    def fake_worker(handler, packet, **kwargs):
        calls.append(packet["arguments"])
        return handler(None, packet["arguments"])

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", fake_worker)
    port = RecordingCompactionProvider()
    config = _configure_pressure(tmp_path, monkeypatch, port)
    result = run_agent_task(AgentTaskSpec(
        tmp_path / "run", "Preserve new tool evidence.", (),
        config, workflow_graph=_explicit_graph(False), max_attempts_per_stage=4, plugin_configuration={}, plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {"synthetic": {"selector": "synthetic/output"}}}}))
    assert result["stop_reason"] == "terminal" and calls == [{}]
    assert len(port.requests) == 3
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    compaction, = _objects(core, "agent_context_compaction/v3")
    assert compaction["trigger_reason"] == "context_pressure"
    assert "FIRST-PRE-REQUEST-UNIQUE-ID" in json.dumps(compaction["replacement_history"])
    action, = _objects(core, "agent_action/v3")
    assert action["outcome"] == "returned"
    assert action["non_delivery_reason"] == "provider_delivery_not_recorded"
    (tmp_path / "actual_fake_provider_requests.json").write_text(json.dumps(port.requests, indent=2))


def synthetic_large_output(context, arguments):
    return {"payload": "A" * 13000 + "UNIQUE-LARGE-MIDDLE-ID" + "Z" * 13000}


class RecordingReadbackProvider:
    def __init__(self, interrupt_state=None):
        self.requests = []
        self.normal_count = 0
        self.locators = {}
        self.interrupt_state = interrupt_state
        self.interrupted_requests = []

    def request_once_interruptible(self, attempt, *, interruption_requested):
        envelope = json.loads(attempt.canonical_request_bytes)
        if (self.interrupt_state and not self.interrupted_requests
                and self.normal_count == 1
                and envelope["messages"][-1].get("content") != CONTEXT_CHECKPOINT_PROMPT):
            import os
            import signal
            import time
            from cpn.rpnh.llm_contracts import LLMInputPortInterrupted
            self.interrupted_requests.append({"submission_state": self.interrupt_state, "request": envelope})
            os.kill(os.getpid(), signal.SIGINT)
            deadline = time.monotonic() + 2
            while not interruption_requested():
                if time.monotonic() >= deadline:
                    raise AssertionError("synthetic owner stop did not reach provider")
                time.sleep(0.01)
            raise LLMInputPortInterrupted(submission_state=self.interrupt_state)
        return self.request_once(attempt)

    def request_once(self, attempt):
        from test_optional_context_compaction import _response
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        assert "read_managed_output" in [t["function"]["name"] for t in envelope["tools"]]
        if not self.normal_count:
            self.normal_count += 1
            return _response(tool_calls=[
                {"id": "small-once", "name": "small", "arguments": "{}"},
                {"id": "large-once", "name": "large", "arguments": "{}"}], finish_reason="tool_calls")
        pages = {}
        for message in envelope["messages"]:
            if message.get("role") == "tool":
                value = json.loads(message["content"])
                if value.get("kind") == "managed_output_page/v1":
                    assert len(message["content"].encode("utf-8")) <= 10000
                    pages[message["tool_call_id"]] = value
        assert {"small-once", "large-once"}.issubset(pages)
        assert "FIRST-PRE-REQUEST-UNIQUE-ID" in pages["small-once"]["content"]
        assert "UNIQUE-LARGE-MIDDLE-ID" not in pages["large-once"]["content"]
        for name in ("small-once", "large-once"):
            locator = (pages[name]["agent_action_ref"], pages[name]["terminal_receipt_ref"])
            assert name not in self.locators or self.locators[name] == locator
            self.locators[name] = locator
        if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
            return _response(text="Continue using exact registered output pages.", finish_reason="stop")
        if self.normal_count == 1:
            self.normal_count += 1
            page = pages["large-once"]
            return _response(tool_calls=[{"id": "read-middle", "name": "read_managed_output", "arguments": json.dumps({
                "agent_action_ref": page["agent_action_ref"], "terminal_receipt_ref": page["terminal_receipt_ref"],
                "offset_chars": page["next_offset_chars"], "max_bytes": 10000})}], finish_reason="tool_calls")
        self.normal_count += 1
        assert "UNIQUE-LARGE-MIDDLE-ID" in pages["read-middle"]["content"]
        assert pages["read-middle"]["agent_action_ref"] == pages["large-once"]["agent_action_ref"]
        return _finish()

    def close(self):
        pass


@pytest.mark.parametrize("interrupt_state", [None, "not_submitted", "submission_unknown"])
def test_multiple_locators_and_middle_readback_reach_actual_provider(tmp_path, monkeypatch, caplog, interrupt_state):
    from dataclasses import replace
    from test_optional_context_compaction import _objects
    from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
    from cpn.rpnh.agent_tasks import AgentTaskSpec, agent_task_catalog, run_agent_task
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.components.agent_loop.compaction import CompactionExecutionMixin
    from cpn.components.agent_loop.mechanical_lifecycle import AgentLoopMechanicalLifecycle
    calls = []
    reconstructed = []
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    selected = PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "1", (
        PluginOperation("small", "Synthetic small output", schema, {}, synthetic_output),
        PluginOperation("large", "Synthetic large output", schema, {}, synthetic_large_output, max_result_bytes=65536),)), {}),))
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _document: selected)

    def fake_worker(handler, packet, **kwargs):
        calls.append(packet["context"]["call_id"])
        return handler(None, packet["arguments"])

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", fake_worker)
    complete = CompactionExecutionMixin.complete_agent_context_compaction_v1

    def rebuild_after_compaction(self, *args, **kwargs):
        completed = complete(self, *args, **kwargs)
        # Reconstruct the same-loop lifecycle from immutable Registry objects
        # before its first ordinary request. No in-memory result cache survives.
        fresh = AgentLoopMechanicalLifecycle(self.core, self.kernel)
        restored = fresh.hydrate_loop(fresh.loop_ref(completed.waiting_loop))
        assert restored == completed.waiting_loop
        assert fresh.latest_context_overlay(restored) is not None
        self.mechanical_lifecycle = fresh
        reconstructed.append(restored.loop_id)
        return replace(completed, waiting_loop=restored)

    monkeypatch.setattr(CompactionExecutionMixin, "complete_agent_context_compaction_v1", rebuild_after_compaction)
    port = RecordingReadbackProvider(interrupt_state)
    config = _configure_pressure(tmp_path, monkeypatch, port)
    spec = AgentTaskSpec(
        tmp_path / "run", "Read the middle of the synthetic output.", (), config,
        workflow_graph=_explicit_graph(True), max_attempts_per_stage=8,
        plugin_configuration={}, plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {"small": {"selector": "synthetic/small"},
                                             "large": {"selector": "synthetic/large"}}}})
    result = run_agent_task(spec)
    (tmp_path / "actual_fake_provider_requests.json").write_text(json.dumps(port.requests, indent=2))
    (tmp_path / "interrupted_fake_adapter_requests.json").write_text(json.dumps(port.interrupted_requests, indent=2))
    assert reconstructed
    if interrupt_state:
        assert result["stop_reason"] == "stopped_by_owner"
        assert calls == ["small-once", "large-once"]
        assert len(port.requests) == 2  # Initial response and completed compaction only.
        interrupted_core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
        interruption, = interrupted_core.event_store.list_events_by_type(("provider_attempt_owner_interrupted/v1",))
        assert interruption.payload["submission_state"] == interrupt_state
        assert len(_objects(interrupted_core, "agent_action/v3")) == 2
        compaction, = _objects(interrupted_core, "agent_context_compaction/v3")
        persisted = [json.loads(e["message"]["content"]) for e in compaction["replacement_history"][2:]
                     if e["message"]["role"] == "tool"]
        assert len(persisted) == 2
        assert all(p["reader"] == "read_managed_output" for p in persisted)
        # A public task resume opens a different invocation/loop. Frozen P1
        # authority must not bridge that boundary; same-loop reconstruction
        # above is the recovery path exercised by this test.
        assert "workspace_interruption_checkpoint" not in caplog.text
        return
    assert result["stop_reason"] == "terminal"
    assert calls == ["small-once", "large-once"]
    assert port.normal_count == 3
    # Reopening a read-only store demonstrates that both source refs and the
    # replacement are durable, not an in-memory last-result cache.
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    originals = _objects(core, "agent_action/v3")
    assert len(originals) == 2 and all(a["outcome"] == "returned" for a in originals)
    reads = [a for a in _objects(core, "agent_action/v2") if a["tool_name"] == "read_managed_output"]
    assert len(reads) == 1 and reads[0]["state"] == AgentLoopState.ACTION_APPLIED.value
    assert reads[0]["result_metadata"]["agent_action_ref"] == port.locators["large-once"][0]
    assert len(_objects(core, "agent_context_compaction/v3")) >= 1
    (tmp_path / "actual_fake_provider_requests.json").write_text(json.dumps(port.requests, indent=2))
    (tmp_path / "interrupted_fake_adapter_requests.json").write_text(json.dumps(port.interrupted_requests, indent=2))
