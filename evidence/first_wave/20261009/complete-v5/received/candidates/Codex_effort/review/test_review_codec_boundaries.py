"""Independent review probes. No source changes or real client/provider use."""
import asyncio
import json
from pathlib import Path

import jsonschema
import pytest

from cpn.frontend.codex_app_server import CodexAppServer
from cpn.llm_adapters import load_llm_execution_selection
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh.task_control import TaskControl
from cpn.rpnh.user_config import discover_profiles, read_selected_path


class SocketDouble:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))


def profiles(root, kind):
    providers = []
    for configured in (False, True):
        name = "configured" if configured else "unset"
        if kind == "local_process":
            adapter = {
                "adapter_kind": kind,
                "argv": ["/usr/bin/true", "{model}"],
                "probe_argv": ["/usr/bin/true"], "env": {}, "inherit_env": [],
            }
            if configured:
                adapter["argv"] += ["{reasoning_effort}"]
        else:
            adapter = {
                "adapter_kind": kind, "route_id": "primary", "backend": "offline-fixture",
                "protocol": "openai_chat_completions/v1",
                "endpoint": "https://offline.example.invalid/v1/chat/completions",
                "credential": None, "headers": {},
                "recovery": {"strategy": "bounded_same_route_health_probe/v1",
                             "max_probe_attempts": 1, "probe_timeout_budget_seconds": 1,
                             "max_probe_success_formal_failure_cycles": 1},
            }
        model = {
            "profile": name, "model_condition": "same-exact-model@review",
            "adapter": adapter, "timeout_seconds": 5,
            "max_output_tokens": 32, "max_response_bytes": 8192,
        }
        if configured:
            model["reasoning_efforts"] = {"supported": ["none", "low"], "default": "low"}
        providers.append({"provider": "provider-" + name, "display_name": name, "models": [model]})
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": "rpnh/provider_model_catalog/v3", "providers": providers}))
    generated = root / "generated"
    build_provider_catalog(catalog, generated)
    result = {(p.selection_id, p.reasoning_effort): p for p in discover_profiles(generated / "execution")}
    return generated, result


@pytest.mark.parametrize("kind", ["local_process", "external_provider"])
def test_nondefault_real_none_and_unset_marker_stay_distinct(tmp_path, monkeypatch, kind):
    def forbidden(*args, **kwargs):
        raise AssertionError("No process, worker, or socket is allowed in review probes")
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr(TaskControl, "start", forbidden)
    generated, variants = profiles(tmp_path, kind)
    canonical = variants[("unset", None)]
    real_none = variants[("configured", "none")]
    real_low = variants[("configured", "low")]
    before_bytes = {str(p): p.read_bytes() for p in generated.rglob("*") if p.is_file()}
    config = tmp_path / "user-config.json"
    monkeypatch.setenv("RPNH_CONFIG", str(config))
    server = CodexAppServer(tmp_path / "session", canonical.path)
    socket = SocketDouble()
    server._initialized_connections.add(id(socket))
    # Use a pre-existing loop: patching socket after its creation avoids asyncio's
    # own local self-pipe while forbidding any application/provider socket.
    async def request(method, params=None):
        await server._handle_request(socket, method, method, params or {})
        return socket.sent[-1].get("result") if socket.sent[-1].get("id") == method else next(
            m["result"] for m in reversed(socket.sent) if m.get("id") == method)

    async def probe():
        listed = await request("model/list")
        wire_models = {m["model"]: m for m in listed["data"]}
        assert wire_models["unset"]["defaultReasoningEffort"] == "none"
        assert wire_models["unset"]["supportedReasoningEfforts"] == []
        assert wire_models["configured"]["defaultReasoningEffort"] == "low"
        for profile in (real_none, canonical, real_low, canonical):
            wire = profile.reasoning_effort or "none"
            await request("config/batchWrite", {"edits": [
                {"keyPath": "model_reasoning_effort", "value": wire, "mergeStrategy": "replace"},
                {"keyPath": "model", "value": profile.selection_id, "mergeStrategy": "replace"},
            ]})
            assert read_selected_path() == profile.path
            assert json.loads(config.read_text())["reasoning_effort"] == profile.reasoning_effort
            assert (await request("config/read"))["config"]["model_reasoning_effort"] == wire
        started = await request("thread/start", {})
        state = server._threads[started["thread"]["id"]]
        assert state.reasoning_effort is None
        for profile, params in [(real_none, {"model": "configured", "effort": "none"}),
                                (real_low, {"effort": "low"}),
                                (real_none, {"effort": "none"}),
                                (canonical, {"model": "unset", "effort": "none"})]:
            await request("thread/settings/update", {"threadId": state.thread_id, **params})
            assert state.session.execution_config_path == profile.path
            assert state.reasoning_effort == profile.reasoning_effort
            selection = load_llm_execution_selection(profile.path)
            assert selection.reasoning_effort == profile.reasoning_effort
            identity = server._execution_identity(profile.path)
            assert identity["provider"] == profile.provider
            assert identity["model_condition"] == "same-exact-model@review"
            assert identity["adapter_kind"] == kind
        resumed = await request("thread/resume", {"threadId": state.thread_id})
        assert resumed["reasoningEffort"] == "none"
        assert state.reasoning_effort is None

    # asyncio needs a self-pipe for constructing its loop; it was created before
    # the application socket guard below in the fixture's loop factory.
    try:
        asyncio.get_event_loop().run_until_complete(probe())
    finally:
        server.close()
    assert {str(p): p.read_bytes() for p in generated.rglob("*") if p.is_file()} == before_bytes
    reopened = CodexAppServer(tmp_path / "session", read_selected_path())
    try:
        state = next(iter(reopened._threads.values()))
        assert state.session.execution_config_path == canonical.path
        assert state.reasoning_effort is None
    finally:
        reopened.close()


@pytest.fixture(autouse=True)
def isolated_event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.mark.parametrize("version", ["0.155.0", "0.161.0"])
def test_schema_rejects_the_pre_fix_null_default(tmp_path, version):
    _, variants = profiles(tmp_path, "local_process")
    server = CodexAppServer(tmp_path / "session", variants[("unset", None)].path)
    try:
        response = {"data": [server._model(p) for p in server.model_profiles]}
        schema = json.loads((Path(__file__).parents[1] / "source/tests/fixtures/codex" / version / "ModelListResponse.json").read_text())
        validator = jsonschema.Draft7Validator(schema)
        validator.validate(response)
        unset = next(m for m in response["data"] if m["model"] == "unset")
        unset["defaultReasoningEffort"] = None
        errors = list(validator.iter_errors(response))
        assert any(list(e.path)[-1] == "defaultReasoningEffort" for e in errors)
    finally:
        server.close()
