from __future__ import annotations

import json

import pytest

from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.rpnh.registry._registry import _RegistryCore


INPUT = {
    "type": "object", "additionalProperties": False,
    "properties": {"value": {"type": "integer"}}, "required": ["value"],
}
ANY_OUTPUT = {}


def first_value(context, arguments):
    return {"node": "first", "nested": [arguments["value"], {"ok": True}]}


def second_value(context, arguments):
    return {"node": "second", "nested": [arguments["value"], None]}


def read_failure(context, arguments):
    raise RuntimeError("external read failed")


def write_uncertain(context, arguments):
    raise RuntimeError("external write outcome is intentionally unknown")


def null_value(context, arguments):
    return None


def _catalog(*, max_result_bytes=65536) -> PluginCatalog:
    definition = PluginDefinition("managed", "1", (
        PluginOperation(
            "first", "Plugin generic first", INPUT, ANY_OUTPUT,
            first_value, max_result_bytes=max_result_bytes),
        PluginOperation(
            "second", "Plugin generic second", INPUT, ANY_OUTPUT,
            second_value, effect="external_read",
            max_result_bytes=max_result_bytes),
        PluginOperation(
            "read_failure", "Fail one external read", INPUT, ANY_OUTPUT,
            read_failure, effect="external_read",
            max_result_bytes=max_result_bytes),
        PluginOperation(
            "write_uncertain", "Submit one external write", INPUT,
            ANY_OUTPUT, write_uncertain, effect="external_write",
            max_result_bytes=max_result_bytes),
        PluginOperation(
            "null_value", "Return JSON null", INPUT, ANY_OUTPUT,
            null_value, max_result_bytes=max_result_bytes),
    ))
    return PluginCatalog((BoundPlugin(definition, {}),))


def _graph():
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowArc as A, AgentWorkflowEndpoint as E,
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
    )
    return AgentWorkflowGraph((
        N("first", "First managed node.", (P("request", "task"),),
          (P("handoff", "handoff"),)),
        N("second", "Second managed node.", (P("handoff", "handoff"),),
          (P("result", "result"),)),
    ), (A("handoff", E("first", "handoff"), E("second", "handoff")),),
        E("first", "request"), E("second", "result"))


class _TwoNodePort:
    def request_once(self, attempt):
        from test_main_session_registry import _response
        request = json.loads(attempt.canonical_request_bytes)
        text = "\n".join(
            message.get("content", "")
            for message in request["messages"]
            if isinstance(message.get("content"), str))
        first = "First managed node." in text
        name = "shared"
        if not any(message.get("role") == "tool"
                   for message in request["messages"]):
            return _response([{
                "id": "managed-first" if first else "managed-second",
                "name": name, "arguments": json.dumps({"value": 7}),
            }])
        output_port = "team.output__first__handoff" if first else "team.result"
        return _response([
            {"id": "write-first" if first else "write-second",
             "name": "write_file", "arguments": json.dumps({
                 "path": "out/first.txt" if first else "out/second.txt",
                 "description": "Managed node output",
                 "content": json.dumps("first" if first else "second"),
                 "output_port_id": output_port,
                 "outcome_id": "complete",
             })},
            {"id": "complete-first" if first else "complete-second",
             "name": "complete_interaction", "arguments": "{}"},
        ])

    def close(self):
        pass


class _FailurePort:
    def request_once(self, attempt):
        from test_main_session_registry import _response
        return _response([
            {"id": "rejected", "name": "bad_args",
             "arguments": json.dumps({"value": "not-an-integer"})},
            {"id": "read-failed", "name": "read_fail",
             "arguments": json.dumps({"value": 1})},
            {"id": "write", "name": "write_file", "arguments": json.dumps({
                "path": "out/result.txt", "description": "Failure evidence",
                "content": json.dumps("handled"),
                "output_port_id": "main.result", "outcome_id": "complete",
            })},
            {"id": "complete", "name": "complete_interaction",
             "arguments": "{}"},
        ])

    def close(self):
        pass


class _UncertainPort:
    def request_once(self, attempt):
        from test_main_session_registry import _response
        return _response([{
            "id": "stable-external-submission", "name": "submit",
            "arguments": json.dumps({"value": 1}),
        }])

    def close(self):
        pass


class _BuiltinOnlyPort:
    def request_once(self, attempt):
        from test_main_session_registry import _response
        return _response([
            {"id": "write", "name": "write_file", "arguments": json.dumps({
                "path": "out/result.txt", "description": "Ordinary output",
                "content": json.dumps("ordinary"),
                "output_port_id": "main.result", "outcome_id": "complete",
            })},
            {"id": "complete", "name": "complete_interaction",
             "arguments": "{}"},
        ])

    def close(self):
        pass


class _RecoveryPort:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    def request_once(self, attempt):
        from test_main_session_registry import _response
        self.calls += 1
        return _response([{
            "id": "restart-managed-call", "name": self.name,
            "arguments": json.dumps({"value": 3}),
        }])

    def close(self):
        pass


class _NullPort:
    def request_once(self, attempt):
        from test_main_session_registry import _response
        request = json.loads(attempt.canonical_request_bytes)
        if not any(message.get("role") == "tool"
                   for message in request["messages"]):
            return _response([{
                "id": "null-managed-call", "name": "return_null",
                "arguments": json.dumps({"value": 1}),
            }])
        return _response([
            {"id": "write", "name": "write_file", "arguments": json.dumps({
                "path": "out/result.txt", "description": "Null result",
                "content": json.dumps("done"),
                "output_port_id": "main.result", "outcome_id": "complete",
            })},
            {"id": "complete", "name": "complete_interaction",
             "arguments": "{}"},
        ])

    def close(self):
        pass


class _ManagedCompactionPort:
    def __init__(self):
        self.requests = []
        self.projection = None

    @staticmethod
    def _text_response(**values):
        from cpn.rpnh.llm_contracts import LLMInputResponseBytes
        payload = {"protocol": "llm_response_envelope/v1", "tool_calls": []}
        payload.update(values)
        return LLMInputResponseBytes(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            status_code=None, external_request_id=None)

    def request_once(self, attempt):
        from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
        from test_main_session_registry import _response
        envelope = json.loads(attempt.canonical_request_bytes)
        self.requests.append(envelope)
        checkpoint = (
            envelope["messages"][-1].get("content")
            == CONTEXT_CHECKPOINT_PROMPT)
        if len(self.requests) == 1:
            return _response([{
                "id": "managed-before-compaction", "name": "managed_value",
                "arguments": json.dumps({"value": 5}),
            }])
        if checkpoint:
            projections = []
            for message in envelope["messages"]:
                if message.get("role") != "tool":
                    continue
                try:
                    value = json.loads(message["content"])
                except (TypeError, ValueError):
                    continue
                if value.get("kind") == (
                        "managed_native_plugin_result/v1"):
                    projections.append(value)
            assert len(projections) == 1
            self.projection = projections[0]
            assert self.projection["output"] == {
                "node": "first", "nested": [5, {"ok": True}]}
            assert self.projection["reader"] is None
            return self._text_response(
                text="Managed evidence body and exact references retained.",
                finish_reason="stop")
        if len(self.requests) == 2:
            return self._text_response(
                text="Force a checkpoint after the managed result.",
                finish_reason="length")
        return _response([
            {"id": "write-after-compaction", "name": "write_file",
             "arguments": json.dumps({
                 "path": "out/result.txt",
                 "description": "Compaction result",
                 "content": json.dumps("compacted"),
                 "output_port_id": "main.result",
                 "outcome_id": "complete",
             })},
            {"id": "complete-after-compaction",
             "name": "complete_interaction", "arguments": "{}"},
        ])

    def close(self):
        pass


def _action_documents(run_dir):
    core = _RegistryCore(run_dir, create=False, read_only=True)
    return [
        (row["object_type"], json.loads(row["metadata_json"]))
        for row in core.event_store.object_rows()
        if row["object_type"] in {"agent_action/v2", "agent_action/v3"}
    ]


def test_node_scoped_managed_calls_persist_v3_alongside_builtin_v2(
        tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    monkeypatch.setattr(
        "cpn.plugins.catalog.load_catalog", lambda _document: selected)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: _TwoNodePort())
    override = {
        "type": "object", "additionalProperties": False,
        "properties": {"value": {"type": "integer", "minimum": 1}},
        "required": ["value"],
    }
    spec = AgentTaskSpec(
        tmp_path / "run", "Use node-scoped managed tools.", (),
        _write_execution_profile(tmp_path), workflow_graph=_graph(),
        max_parallel_nodes=1, plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={
            "first": {"tools": {"shared": {
                "selector": "managed/first",
                "description": "Exact first-node descriptor",
                "input_schema": override,
            }}},
            "second": {
                "tools": {"shared": {
                    "selector": "managed/second",
                    "description": "Exact second-node descriptor",
                    "input_schema": override,
                }},
                "admitted_effects": ["pure", "external_read"],
            },
        })

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    assert result["actual_model_call_counts"][0] == 4
    actions = _action_documents(tmp_path / "run")
    assert sum(kind == "agent_action/v3" for kind, _ in actions) == 2
    assert sum(kind == "agent_action/v2" for kind, _ in actions) == 4
    managed = [document for kind, document in actions
               if kind == "agent_action/v3"]
    assert {item["selector"] for item in managed} == {
        "managed/first", "managed/second"}
    assert {item["effect"] for item in managed} == {
        "pure", "external_read"}
    assert {item["output"]["node"] for item in managed} == {
        "first", "second"}
    assert all(item["request_admission_receipt_ref"]
               == item["started_receipt_ref"] for item in managed)
    assert all(item["model_visible_result_ref"] is None for item in managed)
    assert all(item["non_delivery_reason"]
               == "provider_delivery_not_recorded" for item in managed)
    assert all(item["admitted_at_utc"].endswith("Z") for item in managed)


def test_managed_rejection_and_external_read_failure_are_closed(
        tmp_path, monkeypatch):
    from cpn.plugins.worker import WorkerFailure
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    selected = _catalog(max_result_bytes=1)
    monkeypatch.setattr(
        "cpn.plugins.catalog.load_catalog", lambda _document: selected)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: _FailurePort())
    calls = []

    def fail_read(*_args, **_kwargs):
        calls.append(True)
        raise WorkerFailure("handler_failed")

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", fail_read)
    spec = AgentTaskSpec(
        tmp_path / "run", "Close managed failures.",
        (AgentStage("main", "Handle managed failures."),),
        _write_execution_profile(tmp_path), plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {
            "tools": {
                "bad_args": {
                    "selector": "managed/first",
                    "input_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
                "read_fail": {"selector": "managed/read_failure"},
            },
            "admitted_effects": ["pure", "external_read"],
        }})

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    assert result["actual_model_call_counts"][0] == 1
    assert calls == [True]
    managed = [document for kind, document in _action_documents(
        tmp_path / "run") if kind == "agent_action/v3"]
    assert [item["outcome"] for item in managed] == ["rejected", "failed"]
    rejected, failed = managed
    assert rejected["request_admission_receipt_ref"] is None
    assert rejected["terminal_receipt_ref"] is None
    assert rejected["non_delivery_reason"] == "rejected_before_dispatch"
    assert rejected["error"]["code"] == "arguments_invalid"
    assert "declared plugin schema" in rejected["error"]["detail"]
    assert failed["request_admission_receipt_ref"] is not None
    assert failed["terminal_receipt_ref"] is not None
    assert failed["non_delivery_reason"] == "managed_call_failed"

    from cpn.rpnh.registry.identities import TypedId
    from cpn.rpnh.registry.models import VersionRef
    turn = rejected["agent_turn_ref"]
    turn_ref = VersionRef(
        turn["entity_type"], TypedId.parse(turn["logical_id"]),
        TypedId.parse(turn["version_id"]))
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True)
    all_rows = core.event_store.agent_action_rows_for_turn(turn_ref)
    settled_rows = core.event_store.settled_agent_action_rows_for_turn(
        turn_ref)
    assert [row["object_type"] for row in all_rows] == [
        "agent_action/v3", "agent_action/v3",
        "agent_action/v2", "agent_action/v2",
    ]
    assert [row["version_id"] for row in settled_rows] == [
        row["version_id"] for row in all_rows]


def test_external_write_unknown_commits_v3_head_and_never_redispatches(
        tmp_path, monkeypatch):
    from cpn.plugins.worker import WorkerFailure
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    monkeypatch.setattr(
        "cpn.plugins.catalog.load_catalog", lambda _document: selected)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: _UncertainPort())
    calls = []

    def uncertain(*_args, **_kwargs):
        calls.append(True)
        raise WorkerFailure("worker_protocol_failed")

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", uncertain)
    spec = AgentTaskSpec(
        tmp_path / "run", "Submit exactly once.",
        (AgentStage("main", "Submit the external write."),),
        _write_execution_profile(tmp_path), plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {
            "tools": {"submit": {
                "selector": "managed/write_uncertain"}},
            "admitted_effects": ["pure", "external_write"],
        }})

    first = run_agent_task(spec)
    assert first["stop_reason"] == "blocked_or_waiting"
    assert first["actual_model_call_counts"][0] == 1
    assert calls == [True]
    first_actions = [document for kind, document in _action_documents(
        tmp_path / "run") if kind == "agent_action/v3"]
    assert len(first_actions) == 1
    assert first_actions[0]["outcome"] == "outcome_unknown"
    assert first_actions[0]["terminal_receipt_ref"] is not None

    # The run returns the reconciliation block immediately after committing
    # the v3 action and successor Loop; no automatic path calls the worker a
    # second time.
    assert calls == [True]


def test_no_binding_run_remains_v2_and_model_accounting_is_unchanged(
        tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: _BuiltinOnlyPort())
    spec = AgentTaskSpec(
        tmp_path / "run", "Use ordinary tools.",
        (AgentStage("main", "Complete ordinarily."),),
        _write_execution_profile(tmp_path))

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    assert result["actual_model_call_counts"][0] == 1
    actions = _action_documents(tmp_path / "run")
    assert len(actions) == 2
    assert {kind for kind, _document in actions} == {"agent_action/v2"}


@pytest.mark.parametrize(("effect", "selector"), (
    ("pure", "managed/first"),
    ("external_read", "managed/second"),
))
def test_started_only_managed_call_recovers_to_v3_unknown_without_redispatch(
        tmp_path, monkeypatch, effect, selector):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    port = _RecoveryPort("recover_call")
    monkeypatch.setattr(
        "cpn.plugins.catalog.load_catalog", lambda _document: selected)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: port)
    worker_calls = []

    monkeypatch.setattr(
        "cpn.plugins.worker.execute_worker",
        lambda *_args, **_kwargs: worker_calls.append(True))
    from cpn.plugins.managed_tools import ManagedPluginInvocationService
    original_invoke = ManagedPluginInvocationService.invoke

    def recover_with_reconstructed_service(
            self, name, *, execution, call_id, arguments,
            interruption_requested=None, registration_key=None):
        declaration = self.catalog.declaration(name)
        execution = self._authorize(execution, declaration)
        key, refs = self._refs(execution, call_id)
        assert self._claim(
            execution, declaration, call_id, arguments, key, refs)
        rebuilt = ManagedPluginInvocationService(
            self.owner, self.kernel, self.repository, self.catalog)
        return original_invoke(
            rebuilt, name, execution=execution, call_id=call_id,
            arguments=arguments,
            interruption_requested=interruption_requested,
            registration_key=registration_key)

    monkeypatch.setattr(
        ManagedPluginInvocationService, "invoke",
        recover_with_reconstructed_service)
    spec = AgentTaskSpec(
        tmp_path / "run", "Recover one admitted managed call.",
        (AgentStage("main", "Recover without redispatch."),),
        _write_execution_profile(tmp_path), plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {
            "tools": {"recover_call": {"selector": selector}},
            "admitted_effects": [effect],
        }})

    resumed = run_agent_task(spec)

    assert resumed["stop_reason"] == "blocked_or_waiting"
    assert resumed["actual_model_call_counts"][0] == 1
    assert port.calls == 1
    assert worker_calls == []
    managed = [document for kind, document in _action_documents(
        tmp_path / "run") if kind == "agent_action/v3"]
    assert len(managed) == 1
    assert managed[0]["effect"] == effect
    assert managed[0]["outcome"] == "outcome_unknown"
    assert managed[0]["error"] == {
        "code": "managed_terminal_observation_missing"}
    assert managed[0]["terminal_receipt_ref"] is not None


def test_json_null_output_has_canonical_size_four(tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    monkeypatch.setattr(
        "cpn.plugins.catalog.load_catalog", lambda _document: selected)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: _NullPort())
    spec = AgentTaskSpec(
        tmp_path / "run", "Preserve JSON null.",
        (AgentStage("main", "Return and preserve null."),),
        _write_execution_profile(tmp_path), plugin_configuration={},
        plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {"return_null": {
            "selector": "managed/null_value"}}}})

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    managed = [document for kind, document in _action_documents(
        tmp_path / "run") if kind == "agent_action/v3"]
    assert len(managed) == 1
    assert managed[0]["output"] is None
    assert managed[0]["output_size_bytes"] == 4


def test_managed_result_compaction_preserves_body_without_callable_reader(
        tmp_path, monkeypatch):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    port = _ManagedCompactionPort()
    monkeypatch.setattr(
        "cpn.plugins.catalog.load_catalog", lambda _document: selected)
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda *_args, **_kwargs: port)
    spec = AgentTaskSpec(
        tmp_path / "run", "Compact managed history.",
        (AgentStage("main", "Use managed evidence, then compact."),),
        _write_execution_profile(tmp_path), max_attempts_per_stage=4,
        plugin_configuration={}, plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {"managed_value": {
            "selector": "managed/first"}}}})

    result = run_agent_task(spec)

    assert result["stop_reason"] == "terminal"
    assert port.projection is not None
    assert port.projection["agent_action_ref"]["entity_type"] == (
        "agent_action/v3")
    assert port.projection["terminal_receipt_ref"][
        "resource_version_id"].startswith("resource_version:")
    assert port.projection["output"]["nested"][0] == 5
    assert port.projection["reader"] is None
    assert "provider_content_delivery" not in port.projection
    assert "model_consumption_confirmed" not in port.projection


def test_managed_visible_name_cannot_shadow_write_file(tmp_path):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()

    with pytest.raises(ValueError, match="collide"):
        AgentTaskSpec(
            tmp_path / "run", "Reject ambiguous tools.",
            (AgentStage("main", "Do not shadow built-ins."),),
            _write_execution_profile(tmp_path), plugin_configuration={},
            plugin_catalog_digest=selected.digest,
            managed_bindings={"main": {"tools": {"write_file": {
                "selector": "managed/first"}}}})


@pytest.mark.parametrize("reserved_name", (
    "complete_interaction",
    "delegate_leaf",
    "query_environment_resources",
    "query_registry_resources",
    "read_action_output",
    "read_file",
    "request_resource",
    "search_text",
    "workspace",
    "write_file",
))
def test_managed_visible_name_cannot_shadow_any_fixed_builtin(
        tmp_path, reserved_name):
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowEndpoint as E, AgentWorkflowExecution,
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
    )
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    graph = AgentWorkflowGraph((N(
        "main", "Run without the read surface.",
        (P("request", "task"),), (P("result", "result"),),
        AgentWorkflowExecution(tools=(
            "complete_interaction", "write_file"))),), (),
        E("main", "request"), E("main", "result"))

    with pytest.raises(ValueError, match="reserved built-in"):
        AgentTaskSpec(
            tmp_path / "run", "Reject a disabled reserved name.", (),
            _write_execution_profile(tmp_path), workflow_graph=graph,
            plugin_configuration={}, plugin_catalog_digest=selected.digest,
            managed_bindings={"main": {"tools": {reserved_name: {
                "selector": "managed/first"}}}})


def test_default_managed_bindings_are_empty_and_immutable(tmp_path):
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
    from test_main_session_registry import _write_execution_profile
    spec = AgentTaskSpec(
        tmp_path / "run", "Keep the legacy empty default.",
        (AgentStage("main", "Complete ordinarily."),),
        _write_execution_profile(tmp_path))

    assert dict(spec.managed_bindings) == {}
    with pytest.raises(TypeError):
        spec.managed_bindings["main"] = {}


def test_plugin_executed_workflow_node_rejects_managed_bindings(tmp_path):
    from cpn.rpnh.agent_tasks import AgentTaskSpec
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowEndpoint as E, AgentWorkflowExecution,
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
    )
    from test_main_session_registry import _write_execution_profile
    selected = _catalog()
    graph = AgentWorkflowGraph((N(
        "native", "Run the native operation.",
        (P("request", "task"),), (P("result", "result"),),
        AgentWorkflowExecution(plugin="managed/first")),), (),
        E("native", "request"), E("native", "result"))

    with pytest.raises(ValueError, match="plugin-executed"):
        AgentTaskSpec(
            tmp_path / "run", "Reject mixed execution ownership.", (),
            _write_execution_profile(tmp_path), workflow_graph=graph,
            plugin_configuration={}, plugin_catalog_digest=selected.digest,
            managed_bindings={"native": {"tools": {"managed_value": {
                "selector": "managed/first"}}}})


@pytest.mark.parametrize(("output", "limit", "delivery"), (
    ("small", 512, "full"),
    ("x" * 1024, 256, "bounded"),
))
def test_prior_managed_result_reference_distinguishes_delivery_bounds(
        output, limit, delivery):
    from cpn.components.agent_loop.compact import prior_tool_result_reference
    action_ref = {
        "entity_type": "agent_action/v3",
        "logical_id": f"agent_action:{'a' * 32}",
        "version_id": f"agent_action_version:{'b' * 32}",
    }

    terminal_ref = {
        "resource_id": f"resource:{'c' * 32}",
        "resource_version_id": f"resource_version:{'d' * 32}",
    }
    metadata = {
        "kind": "managed_native_plugin_result/v1",
        "output": output,
        "terminal_receipt_ref": terminal_ref,
    }
    projected = prior_tool_result_reference(
        metadata, action_ref, model_visible_byte_limit=limit)

    assert projected["agent_action_ref"] == action_ref
    assert projected["terminal_receipt_ref"] == terminal_ref
    assert projected["provider_content_delivery"] == delivery
    assert projected["output_replayed"] is False
    assert "output" not in projected
    if delivery == "bounded":
        assert "not delivered in full" in projected["history_notice"]

    unrecorded = prior_tool_result_reference(metadata, action_ref)
    assert unrecorded["provider_content_delivery"] == "not_recorded"
    assert "No provider-delivery claim" in unrecorded["history_notice"]


def test_component_schema_inventory_includes_managed_action_v3():
    from cpn.components.schema_catalog import component_schema_data

    documents, definitions, paths = component_schema_data()

    assert "registry_v1/agent_action/v3" in documents
    assert paths["registry_v1/agent_action/v3"].name == (
        "agent_action.v3.schema.json")
    assert any(
        definition.name == "agent_action/v3"
        and definition.category == "object"
        for definition in definitions)
