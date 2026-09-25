"""Offline contracts for the presentation-only client and RPNH-owned prompts."""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path
import textwrap
import tomllib

import pytest

from cpn.frontend import codex_app_server as frontend
from cpn.llm_adapters import codex_subscription_bridge as bridge
from cpn.rpnh.main_session import _main_prompt, MainTurnReconciliation
from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
from test_codex_compat import (
    _FakeWebSocket, _local_profile, _RecoverySession, _write_thread_projection,
)


def requests(*items):
    return _FakeWebSocket([
        {"id": "init", "method": "initialize", "params": {
            "clientInfo": {"name": "codex-tui", "version": frontend.CODEX_FRONTEND_VERSION}}},
        {"method": "initialized"},
        *({"id": i, "method": name, "params": params}
          for i, (name, params) in enumerate(items)),
    ])


@pytest.mark.parametrize("method", [
    "memory/status", "skills/config/write", "mcpServer/oauth/login", "process/spawn",
])
def test_unimplemented_frontend_services_have_no_execution_side_effects(tmp_path, method):
    server = frontend.CodexAppServer(tmp_path / "session", _local_profile(tmp_path / "profiles"))
    socket = requests((method, {}))
    asyncio.run(server.handle(socket))
    reply = next(item for item in socket.sent if item.get("id") == 0)
    assert reply["error"]["code"] == -32601
    assert not server._threads
    assert not (tmp_path / "session").exists()


def test_empty_discovery_does_not_advertise_foreign_capabilities(tmp_path):
    server = frontend.CodexAppServer(tmp_path / "session", _local_profile(tmp_path / "profiles"))
    names = ("skills/list", "hooks/list", "collaborationMode/list", "apps/list", "plugin/list")
    socket = requests(*((name, {}) for name in names))
    asyncio.run(server.handle(socket))
    for i, name in enumerate(names):
        result = next(item["result"] for item in socket.sent if item.get("id") == i)
        assert result["marketplaces" if name == "plugin/list" else "data"] == []
    assert not (tmp_path / "session").exists()


def test_legacy_attachment_metadata_is_preserved_but_never_delivered(tmp_path, monkeypatch):
    root = tmp_path / "session"
    execution = _local_profile(tmp_path / "profiles")
    thread_root = _write_thread_projection(root, execution=execution)
    path = thread_root / "thread_state.json"
    doc = json.loads(path.read_text())
    legacy = [{"id": "old", "attachmentType": "local-file", "identityKey": "x", "createdAt": 100,
               "payload": {"path": "/not-a-registered-input"}}]
    doc["attachments"] = legacy
    path.write_text(json.dumps(doc))
    session = _RecoverySession(thread_root, execution,
                               MainTurnReconciliation("idle", None))
    monkeypatch.setattr(frontend.MainSession, "resume", lambda _root: session)
    server = frontend.CodexAppServer(root, session.execution_config_path)
    assert server._threads["thread-1"].attachments == legacy
    assert json.loads(path.read_text())["attachments"] == legacy
    socket = requests(("thread/attachment/list", {"threadId": "thread-1"}))
    asyncio.run(server.handle(socket))
    assert next(item for item in socket.sent if item.get("id") == 0)["error"]["code"] == -32601
    assert session.history == []
    assert json.loads(path.read_text())["attachments"] == legacy


def test_manifest_matches_actual_method_dispatch_and_has_no_static_pass_claim():
    manifest = json.loads(Path(frontend.__file__).with_name("codex_compatibility.v1.json").read_text())
    tree = ast.parse(textwrap.dedent(inspect.getsource(frontend.CodexAppServer._handle_request)))
    actual = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "method":
            for comparator in node.comparators:
                actual.update(child.value for child in ast.walk(comparator)
                              if isinstance(child, ast.Constant) and isinstance(child.value, str))
    assert set(manifest["implemented_methods"]) == actual
    assert manifest["frontend"]["version"] == frontend.CODEX_FRONTEND_VERSION
    assert "PASS" not in json.dumps(manifest)
    assert "thread/attachment/*" in manifest["unsupported_methods"]


def test_rpnh_prompts_do_not_assign_client_identity_or_rewrite_user_content():
    instructions = bridge.RPNH_ENDPOINT_INSTRUCTIONS
    assert "RPNH alone owns tool execution" in instructions
    assert "request is not evidence of execution" in instructions
    for prompt in (instructions, _main_prompt((), "ordinary task"), CONTEXT_CHECKPOINT_PROMPT):
        assert "codex" not in prompt.lower()
    request = {"messages": [{"role": "user", "content": "Explain Codex and Claude; do not rewrite this."}],
               "tools": [], "model_condition": "owner/exact-model"}
    prompt = bridge._build_endpoint_prompt(request)
    body = prompt.split("REQUEST_JSON_BEGIN\n", 1)[1].rsplit("\nREQUEST_JSON_END", 1)[0]
    assert json.loads(body) == request
    assert "Codex" not in prompt.split("REQUEST_JSON_BEGIN", 1)[0]


def test_subscription_bridge_replaces_builtin_instructions_and_keeps_no_tool_boundary(tmp_path):
    path = tmp_path / 'instructions "quoted".txt'
    path.write_text(bridge.RPNH_ENDPOINT_INSTRUCTIONS)
    argv = bridge._codex_argv(codex_binary=tmp_path / "codex", model="owner/exact-model",
        response_schema=tmp_path / "schema.json", working_directory=tmp_path,
        reasoning_effort="medium", model_context_window=32768, verbosity="medium",
        instructions_file=path)
    config = [argv[i+1] for i, arg in enumerate(argv[:-1]) if arg == "-c"]
    config_doc = tomllib.loads("\n".join(config))
    assert config_doc["model_instructions_file"] == str(path)
    assert argv[argv.index("--model") + 1] == "owner/exact-model"
    assert "--ignore-user-config" in argv and "--ignore-rules" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    disabled = {argv[i+1] for i, arg in enumerate(argv[:-1]) if arg == "--disable"}
    assert {"shell_tool", "browser_use", "apps", "computer_use", "enable_mcp_apps"} <= disabled
    assert "model_max_output_tokens" not in config_doc


def test_transport_rejects_builtin_tool_events_even_with_successful_process():
    payload = json.dumps({"type": "item.completed", "item": {
        "type": "command_execution", "command": "ignored"}}).encode()
    with pytest.raises(bridge.CodexSubscriptionBridgeError, match="built-in tool"):
        bridge._canonical_response_from_codex_events(payload)


def test_frontend_argv_preserves_remote_and_disabled_features():
    argv = frontend.codex_frontend_argv("/bin/client", Path("/tmp/rpnh.sock"), "owner/exact-model")
    assert argv[argv.index("--remote") + 1] == "unix:///tmp/rpnh.sock"
    assert argv[argv.index("--model") + 1] == "owner/exact-model"
    disabled = {argv[i+1] for i, arg in enumerate(argv[:-1]) if arg == "--disable"}
    assert disabled == set(frontend.CODEX_DISABLED_FEATURES)
    assert "check_for_update_on_startup=false" in argv


def test_codex_frontend_uses_existing_short_socket_helper_for_long_root(
        tmp_path, monkeypatch,
):
    execution = _local_profile(tmp_path / "profiles")
    root = tmp_path / ("long-session-segment-" + "x" * 80)
    direct = root / "codex-app-server.sock"
    assert len(str(direct).encode()) >= 108
    monkeypatch.setattr(
        frontend, "resolve_codex_binary", lambda _binary: "/bin/true")

    result = asyncio.run(frontend._run_codex_frontend_async(
        root, execution, codex_binary="ignored"))

    assert result == 0
    assert not direct.exists()


def test_bridge_process_loads_rpnh_instructions_without_altering_registered_request(tmp_path):
    """Real local subprocesses; the endpoint is a fixture, not a live model."""
    import os
    import subprocess
    import sys
    fake = tmp_path / "fixture-endpoint"
    fake.write_text("#!" + sys.executable + "\n" + '''import json, sys, tomllib
from pathlib import Path
args = sys.argv[1:]
config = tomllib.loads("\\n".join(args[i+1] for i,a in enumerate(args[:-1]) if a == "-c"))
instructions = Path(config["model_instructions_file"]).read_text()
assert "RPNH alone owns tool execution" in instructions
assert "Codex" not in instructions
assert args[args.index("--sandbox") + 1] == "read-only"
prompt = sys.stdin.read()
request = json.loads(prompt.split("REQUEST_JSON_BEGIN\\n", 1)[1].rsplit("\\nREQUEST_JSON_END",1)[0])
assert request["messages"][0]["content"] == "Explain Codex; preserve my words."
answer = {"protocol":"llm_response_envelope/v1", "text":"fixture-ok", "tool_calls":[],
          "reasoning_content":None,"finish_reason":"stop","usage":None}
print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":json.dumps(answer)}}))
print(json.dumps({"type":"turn.completed", "usage":None}))
''')
    fake.chmod(0o700)
    schema = Path(bridge.__file__).with_name("codex_subscription_response_format.v1.schema.json")
    request = {"protocol": "llm_request_envelope/v1", "model_condition": "fixture-model",
               "max_output_tokens": 1024, "messages": [{"role": "user", "content": "Explain Codex; preserve my words."}],
               "tools": [], "tool_choice": "auto"}
    payload = json.dumps(request, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    repo = str(Path(__file__).resolve().parents[1])
    (tmp_path / "unused-home").mkdir()
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": repo, "HOME": str(tmp_path),
                   "CODEX_HOME": str(tmp_path / "unused-home")}
    completed = subprocess.run([
        sys.executable, "-m", "cpn.llm_adapters.codex_subscription_bridge", "--codex", str(fake),
        "--model", "fixture-model", "--model-context-window", "32768", "--model-max-output-tokens", "1024",
        "--output-token-policy", "auto", "--reasoning-effort", "medium", "--verbosity", "medium",
        "--response-schema", str(schema),
    ], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd=tmp_path, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode()
    assert json.loads(completed.stdout)["text"] == "fixture-ok"
    assert not list(tmp_path.glob("codex-subscription-endpoint-*"))
