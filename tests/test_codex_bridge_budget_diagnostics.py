from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.compact import ContextPressurePolicy, should_compact
from cpn.llm_adapters import codex_subscription_bridge as bridge
from cpn.llm_adapters.local_process import _bridge_failure_code, _stderr_excerpt


def _request():
    return {"protocol": "llm_request_envelope/v1", "model_condition": "fixture-model",
            "max_output_tokens": 128, "messages": [{"role": "user", "content": "keep me"}],
            "tools": [], "tool_choice": "auto"}


def _events(*, item_type="agent_message", event_type="item.completed"):
    answer = {"protocol": "llm_response_envelope/v1", "text": "fixture-ok",
              "tool_calls": [], "finish_reason": "stop", "usage": None}
    return (json.dumps({"type": event_type, "item": {
        "type": item_type, "text": json.dumps(answer), "payload": "PRIVATE_SENTINEL"}})
        + '\n' + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 42}})).encode()


def _main_fixture(tmp_path, monkeypatch, request, *, context_window=10000, schema=b'{}'):
    monkeypatch.chdir(tmp_path)
    executable = tmp_path / "unused-endpoint"
    executable.write_text("fixture, never executed")
    schema_file = tmp_path / "schema.json"
    schema_file.write_bytes(schema)
    payload = json.dumps(request, ensure_ascii=True, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode()
    monkeypatch.setattr(bridge.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=_events(), stderr=b'')

    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge, "_codex_process_environment", lambda **_: {})
    args = ["--codex", str(executable), "--model", "fixture-model",
            "--model-context-window", str(context_window), "--model-max-output-tokens", "128",
            "--output-token-policy", "cli_default_auto", "--reasoning-effort", "medium",
            "--verbosity", "low", "--response-schema", str(schema_file)]
    return args, calls


def test_rendered_budget_includes_instructions_history_tools_and_output_schema():
    request = _request()
    request["messages"] += [{"role": "assistant", "content": "prior turn " * 100}]
    request["tools"] = [{"name": "fixture", "parameters": {"description": "tool schema " * 100}}]
    prompt = bridge._build_endpoint_prompt(request)
    schema = json.dumps({"description": "output schema " * 100}).encode()
    budget = bridge._request_budget(prompt=prompt, response_schema=schema,
                                   reserved_output_tokens=128, context_window_tokens=10000)
    assert budget.prompt_bytes == len(prompt.encode())
    assert budget.instructions_bytes == len(bridge.RPNH_ENDPOINT_INSTRUCTIONS.encode())
    assert budget.output_schema_bytes == len(schema)
    # A canonical-input-only budget would fit; full rendered endpoint input must fail.
    canonical_only_window = (len(json.dumps(request).encode()) + 3) // 4 + 128
    with pytest.raises(bridge.CodexSubscriptionBridgeError) as failure:
        bridge._validate_request_budget(replace(budget, context_window_tokens=canonical_only_window))
    assert failure.value.failure_code == "context_budget_exceeded"
    assert budget.as_document()["exact_token_count"] is False
    assert budget.as_document()["cli_output_cap_enforced"] is False


@pytest.mark.parametrize("delta,allowed", [(-1, False), (0, True), (1, True)])
def test_main_estimated_budget_boundary_blocks_before_endpoint(tmp_path, monkeypatch, capsys, delta, allowed):
    request = _request()
    budget = bridge._request_budget(prompt=bridge._build_endpoint_prompt(request),
                                   response_schema=b'{}', reserved_output_tokens=128,
                                   context_window_tokens=10000)
    args, calls = _main_fixture(tmp_path, monkeypatch, request,
                               context_window=budget.estimated_total_tokens + delta)
    assert bridge.main(args) == (0 if allowed else 1)
    captured = capsys.readouterr()
    assert len(calls) == int(allowed)
    if allowed:
        assert json.loads(captured.out)["text"] == "fixture-ok"
        assert json.loads(captured.err.split("request_budget: ", 1)[1])["estimated_total_tokens"] == budget.estimated_total_tokens
        argv, kwargs = calls[0]
        assert kwargs["input"].decode() == bridge._build_endpoint_prompt(request)
        assert argv[argv.index("--model") + 1] == "fixture-model"
        assert "model_max_output_tokens=128" not in argv
    else:
        assert captured.out == ""
        assert _bridge_failure_code(_stderr_excerpt(captured.err.encode())) == "context_budget_exceeded"
    assert not list(tmp_path.glob("codex-subscription-endpoint-*"))


@pytest.mark.parametrize("growth", ["history", "tools", "output_schema", "instructions"])
def test_each_rendered_component_can_prevent_dispatch(tmp_path, monkeypatch, capsys, growth):
    request = _request()
    baseline = bridge._request_budget(prompt=bridge._build_endpoint_prompt(request),
                                     response_schema=b'{}', reserved_output_tokens=128,
                                     context_window_tokens=10000)
    schema = b'{}'
    if growth == "history":
        request["messages"].append({"role": "assistant", "content": "history " * 500})
    elif growth == "tools":
        request["tools"] = [{"name": "fixture", "parameters": {"description": "tool " * 500}}]
    elif growth == "output_schema":
        schema = json.dumps({"description": "schema " * 500}).encode()
    else:
        monkeypatch.setattr(bridge, "RPNH_ENDPOINT_INSTRUCTIONS", bridge.RPNH_ENDPOINT_INSTRUCTIONS + "instruction " * 500)
    args, calls = _main_fixture(tmp_path, monkeypatch, request, schema=schema,
                               context_window=baseline.estimated_total_tokens)
    assert bridge.main(args) == 1
    assert not calls
    assert "context_budget_exceeded" in capsys.readouterr().err


def test_existing_pressure_uses_reserve_and_prior_observed_usage():
    policy = ContextPressurePolicy(1000, 400, 0.9)
    assert not should_compact(policy, b'x' * 2396)
    assert should_compact(policy, b'x' * 2397)
    # Actual prior usage can trigger pressure even where byte fallback does not.
    assert not should_compact(policy, b'x' * 400)
    assert should_compact(policy, b'x' * 400, previous_request_bytes=400,
                          previous_input_tokens=600)


@pytest.mark.parametrize("event_type", ["item.started", "item.updated", "item.completed"])
@pytest.mark.parametrize("item_type", ["command_execution", "mcp_tool_call", "future_metadata"])
def test_rejected_items_retain_only_bounded_type_diagnostics(event_type, item_type):
    stream = _events(event_type=event_type, item_type=item_type)
    with pytest.raises(bridge.CodexSubscriptionBridgeError) as failure:
        bridge._canonical_response_from_codex_events(stream)
    error = failure.value
    assert error.failure_code == "codex_unsupported_item"
    assert error.diagnostic == bridge.CodexItemDiagnostic(event_type, item_type)
    assert "PRIVATE_SENTINEL" not in str(error)
    nonzero_error = bridge._codex_failure_from_events(stream)
    assert nonzero_error.diagnostic == error.diagnostic


@pytest.mark.parametrize("value", [None, {}, [], "token=PRIVATE_SENTINEL\n", "x" * 1000])
def test_malformed_item_types_do_not_leak_or_escape_typed_failure(value):
    with pytest.raises(bridge.CodexSubscriptionBridgeError) as failure:
        bridge._canonical_response_from_codex_events(_events(item_type=value))
    assert failure.value.diagnostic.item_type == (None if value is None else "<invalid-type>")
    assert "PRIVATE_SENTINEL" not in str(failure.value)


def test_cli_failure_diagnostic_survives_local_process_excerpt(tmp_path, monkeypatch, capsys):
    args, calls = _main_fixture(tmp_path, monkeypatch, _request())
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stdout=_events(item_type="future_metadata"), stderr=b'PRIVATE_SENTINEL'))
    assert bridge.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    excerpt = _stderr_excerpt(captured.err.encode())
    assert _bridge_failure_code(excerpt) == "codex_unsupported_item"
    assert json.loads(excerpt.split("diagnostic=", 1)[1]) == {
        "event_type": "item.completed", "item_type": "future_metadata"}
    assert "PRIVATE_SENTINEL" not in excerpt


@pytest.mark.parametrize("event_type", ["future.lifecycle", "secret=value\n", {}])
def test_unknown_events_are_diagnostic_only(event_type):
    with pytest.raises(bridge.CodexSubscriptionBridgeError) as failure:
        bridge._canonical_response_from_codex_events(_events(event_type=event_type))
    assert failure.value.failure_code == "codex_unsupported_event"
    assert failure.value.diagnostic.event_type == (
        "future.lifecycle" if event_type == "future.lifecycle" else "<invalid-type>")


def test_known_reasoning_and_final_message_still_accepted():
    reasoning = json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "private reasoning"}}).encode()
    answer = json.loads(bridge._canonical_response_from_codex_events(reasoning + b'\n' + _events()))
    assert answer["text"] == "fixture-ok"
    assert answer["usage"]["input_tokens"] == 42


def test_provider_capacity_failure_remains_distinct():
    stream = json.dumps({"type": "turn.failed", "error": {"message": "Selected model is at capacity."}}).encode()
    with pytest.raises(bridge.CodexSubscriptionBridgeError) as failure:
        bridge._canonical_response_from_codex_events(stream)
    assert failure.value.failure_code == "provider_model_capacity"


def test_registered_profile_context_enables_pressure_before_next_turn(tmp_path, monkeypatch):
    from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
    from cpn.llm_adapters.config import load_llm_execution_selection
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from test_optional_context_compaction import _response, _completion_response

    class Port:
        execution_policy = {}

        def __init__(self):
            self.requests = []

        def request_once(self, attempt):
            request = json.loads(attempt.canonical_request_bytes)
            self.requests.append(request)
            if len(self.requests) == 1:
                return _response(text="first turn", finish_reason="stop",
                                 usage={"input_tokens": 150000})
            if len(self.requests) == 2:
                # Configured reserve is 128000: pressure boundary is 144000,
                # well below the 0.9 * 272000 ratio-only threshold.
                assert request["messages"][-1]["content"] == CONTEXT_CHECKPOINT_PROMPT
                return _response(text="continue from first turn", finish_reason="stop")
            assert any("continue from first turn" in m.get("content", "")
                       for m in request["messages"])
            return _completion_response("profile pressure verified")

        def close(self):
            pass

    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1", "adapter_kind": "local_process",
        "model_condition": "fixture-model", "argv": ["python", "-c", "pass"],
        "probe_argv": ["python", "-c", "pass"], "env": {}, "inherit_env": []}))
    profile = tmp_path / "selection.json"
    profile.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
        "model_condition": "fixture-model", "adapter_config_path": str(adapter),
        "timeout_seconds": 30, "max_output_tokens": 128000, "max_response_bytes": 65536,
        "context_window_tokens": 272000}))
    selection = load_llm_execution_selection(profile)
    assert selection.as_registry_policy()["context_window_tokens"] == 272000
    port = Port()
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port",
                        lambda _selection, *, destination_run_root: port)
    run = tmp_path / "run"
    result = run_agent_task(AgentTaskSpec(
        run_dir=run, prompt="Exercise offline profile context pressure.",
        stages=(AgentStage("main", "Produce the requested result."),),
        execution_config_path=profile, max_attempts_per_stage=3))
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "profile pressure verified"
    assert len(port.requests) == 3
    registered_targets = []
    for resource in (run / ".registry_v1/objects/resource_version").iterdir():
        try:
            document = json.loads(resource.read_bytes())
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(document, dict) and document.get("schema_version") == "llm_input_target/v1":
            registered_targets.append(document)
    assert registered_targets
    assert all(d["context_window_tokens"] == 272000 and d["max_output_tokens"] == 128000
               for d in registered_targets)
