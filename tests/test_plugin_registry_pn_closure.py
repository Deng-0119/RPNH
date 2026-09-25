"""Actual plugin execution -> Registry evidence -> unchanged Petri topology.

Handlers/IPC/Registry are real. Only model responses in existing mixed-workflow
fixtures are offline doubles; this file's MCP peer is an explicit local subset.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cpn.cli import _filter_projection, _projection_text
from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation, PluginResource
from cpn.plugins.api import PluginError
from cpn.plugins.host import NativePluginExecutor
from cpn.plugins.runtime import run_plugin
from cpn.rpnh.agent_tasks import agent_task_catalog
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from test_native_plugins import catalog, prepared_host


def view(path):
    return project_registry_net(Path(path), catalog=agent_task_catalog())


def transition(projection):
    nodes = [n for n in projection["nodes"] if n["kind"] == "transition"]
    assert len(nodes) == 1
    return nodes[0]


def mcp_adapter(context, arguments):
    """One semantic tools/call in a real, bounded stdio exchange."""
    assert not hasattr(context, "core") and not hasattr(context, "gateway")
    server = subprocess.Popen(
        [sys.executable, "-I", "-u", "-c", context.resources["server"].text()],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    def send(value):
        server.stdin.write(json.dumps({"jsonrpc": "2.0", **value}) + "\n")
        server.stdin.flush()
    try:
        send({"id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "test-adapter", "version": "1"}}})
        initialized = json.loads(server.stdout.readline())
        assert initialized["id"] == 1 and initialized["result"]["capabilities"] == {"tools": {}}
        send({"method": "notifications/initialized"})
        send({"id": 2, "method": "tools/call", "params": {"name": "double", "arguments": arguments}})
        response = json.loads(server.stdout.readline())
        assert response["id"] == 2 and response["result"]["isError"] is False
        server.stdin.close()
        assert server.wait(timeout=3) == 0
        return {**response["result"]["structuredContent"], "worker_pid": os.getpid(),
                "firing_id": context.firing_id}
    finally:
        if server.poll() is None:
            server.kill()
        server.wait(timeout=3)
        server.stdout.close()
        server.stderr.close()


def mcp_catalog():
    resource = PluginResource("server", (Path(__file__).parent / "fixtures/native_mcp_server.py").read_bytes())
    return PluginCatalog((BoundPlugin(PluginDefinition("mcp_adapter", "1", (
        PluginOperation("double", "Offline stdio MCP call", {
            "type": "object", "properties": {"value": {"type": "integer"}},
            "required": ["value"], "additionalProperties": False}, {"type": "object"},
            mcp_adapter, resources=("server",), effect="external_read"),), resources=(resource,)), {}),))


@pytest.mark.parametrize("kind", ["tool", "skill", "mcp"])
def test_actual_dispatch_is_already_admitted_and_visible(tmp_path, monkeypatch, kind):
    import cpn.plugins.worker as worker
    selected, selector, arguments = (mcp_catalog(), "mcp_adapter/double", {"value": 4}) if kind == "mcp" else (
        catalog(), "test_plugin/skill", {}) if kind == "skill" else (catalog(), "test_plugin/add", {"x": 3})
    run_dir = tmp_path / "run"
    original = worker.execute_worker
    snapshots = []
    def audited(*args, **kwargs):
        # This runs after the owner gateway commits preparation, before the
        # real handler/subprocess is dispatched. No fabricated receipts.
        before = view(run_dir)
        t = transition(before)
        invocation = t["runtime"]["firings"][0]
        assert invocation["status"] == "dispatch_authorized"
        assert invocation["admission_ordinal"] < invocation["start_ordinal"]
        assert invocation["result_ref"] is None
        assert [r["phase"] for r in invocation["receipts"]] == ["started"]
        snapshots.append(before)
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, "execute_worker", audited)
    result = run_plugin(selected, selector, arguments, run_dir=run_dir)
    assert result["stop_reason"] == "terminal" and result["actual_model_call_counts"] == [0, 0]
    if kind == "mcp":
        assert result["output"]["value"] == 8
        assert result["output"]["server_pid"] != result["output"]["worker_pid"] != os.getpid()
    elif kind == "tool":
        assert result["output"] == 5
    else:
        assert result["output"] == "Use exact registered inputs."
    after = view(run_dir)
    assert len(snapshots) == 1
    assert [(n["id"], n["kind"]) for n in snapshots[0]["nodes"]] == [
        (n["id"], n["kind"]) for n in after["nodes"]]
    assert snapshots[0]["edges"] == after["edges"]  # no retrospective drawing
    t = transition(after)
    assert t["capability"]["selector"] == selector
    assert t["runtime"]["status"] == "settled" and t["runtime"]["firing_count"] == 1
    f = t["runtime"]["firings"][0]
    if kind == "mcp":
        assert result["output"]["firing_id"] == f["firing_ref"]["version_id"]
    assert [r["phase"] for r in f["receipts"]] == ["started", "returned"]
    assert f["settlement_ordinal"] > f["start_ordinal"]
    assert f["result_ref"] is not None and f["successor_checkpoint_ref"] is not None
    core = _RegistryCore(run_dir, create=False, read_only=True)
    net, structure, marking = hydrate_module_runtime(core)
    assert {n["id"] for n in after["nodes"]} == {
        p.name for p in structure.compiled.symbolic.places} | {
        t.name for t in structure.compiled.symbolic.transitions}
    result_metadata = core.get_version(f["result_ref"]["version_id"]).metadata
    assert result_metadata["transition_firing_ref"] == f["firing_ref"]
    assert result_metadata["output_resource_refs"] == f["output_resource_refs"]
    capability = next(n for n in after["nodes"] if n["id"] == t["capability"]["place"])
    assert capability["category"] == "resource" and capability["token_kind"] == "data"
    assert capability["tokens"][0]["active_in_checkpoint"] is True
    assert any(i["resource_ref"] == capability["tokens"][0]["resource_ref"] for i in f["inputs"])
    assert any(a["source"] == capability["id"] and a["target"] == t["id"]
               and a["mode"] == "read" for a in after["edges"])
    only_resources = _filter_projection(after, show_resources=True, resources_only=True, node_id=None)
    assert {n["id"] for n in only_resources["nodes"]} == {capability["id"], t["id"]}
    assert selector in _projection_text(after) and "observed=settled" in _projection_text(after)
    evidence_root = os.environ.get("RPNH_TEST_EVIDENCE_DIR")
    if evidence_root:
        target = Path(evidence_root); target.mkdir(parents=True, exist_ok=True)
        for label, value in (("before", snapshots[0]), ("after", after), ("result", result)):
            (target / f"{kind}-{label}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_returned_observation_does_not_imply_petri_settlement(tmp_path):
    from cpn.plugins.worker import execute_worker
    owner, execution, host, descriptor = prepared_host(tmp_path)
    assert transition(view(tmp_path / "run"))["runtime"]["status"] == "started"
    prepared = host.prepare(execution, descriptor)
    plugin, op = catalog().resolve("test_plugin/add")
    value = execute_worker(op.handler, prepared["packet"], environment_names=(),
                           timeout_seconds=op.timeout_seconds, cancelled=lambda: False)
    host.products(execution, prepared["attempt_ref"], value)
    observed = transition(view(tmp_path / "run"))["runtime"]
    assert observed["status"] == "returned_unsettled"
    assert observed["firings"][0]["result_ref"] is None
    assert not owner._core.event_store.object_rows_by_type("operation_result/v1")
    assert not owner._core.event_store.list_events_by_type(("transition_firing_settled/v1",))


def write_then_fail(context, arguments):
    Path(arguments["path"]).write_text("side effect occurred", encoding="utf-8")
    raise RuntimeError("not a rollback")


def test_external_effect_failure_is_visible_without_fabricating_success(tmp_path):
    selected = PluginCatalog((BoundPlugin(PluginDefinition("external_test", "1", (
        PluginOperation("write", "Test unresolved effect", {"type": "object"}, {"type": "object"},
                        write_then_fail, effect="external_write"),)), {}),))
    marker = tmp_path / "effect.txt"
    result = run_plugin(selected, "external_test/write", {"path": str(marker)}, run_dir=tmp_path / "run")
    assert marker.read_text() == "side effect occurred"
    assert result["terminal_evidence_ref"] is None
    record = transition(view(tmp_path / "run"))["runtime"]["firings"][0]
    assert record["status"] == "outcome_unknown" and record["publication_state"] == "PROVISIONAL"
    assert record["result_ref"] is None and record["settlement_event_id"] is None
    assert [r["phase"] for r in record["receipts"]] == ["started", "failed"]
    assert record["receipts"][-1]["code"] == "handler_failed"


def test_invalid_execution_identity_cannot_reach_handler(tmp_path, monkeypatch):
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    import cpn.plugins.worker as worker
    owner, execution, host, descriptor = prepared_host(tmp_path)
    calls = []
    monkeypatch.setattr(worker, "execute_worker", lambda *a, **k: calls.append(True))
    plugin, op = catalog().resolve("test_plugin/add")
    from cpn.rpnh.registry.operations import OperationAuthorityError
    # Invalid execution identities already fail at the public authority DTO.
    with pytest.raises(OperationAuthorityError, match="lease differs"):
        replace(execution, operation_execution_lease_ref=VersionRef(
            "operation_execution_lease/v1", new_id("operation_execution_lease"),
            new_id("operation_execution_lease_version")))
    # A well-typed execution paired with a different implementation contract
    # must additionally fail at the real HOST gate before any handler starts.
    different_op = replace(op, description="A different unadopted contract")
    gateway = SimpleNamespace(native_plugin_prepare=host.prepare)
    with pytest.raises(PluginError, match="worker implementation differs"):
        NativePluginExecutor(plugin, different_op)(
            execution=execution, gateway=gateway, resources=None, host_context=None)
    assert calls == []
    assert transition(view(tmp_path / "run"))["runtime"]["status"] == "started"
    assert not host._receipts(execution, "started")


def test_viewer_has_no_writer_or_plugin_loader_and_does_not_change_authority(tmp_path, monkeypatch):
    import cpn.plugins.catalog as loader
    run_dir = tmp_path / "run"
    run_plugin(catalog(), "test_plugin/add", {"x": 3}, run_dir=run_dir)
    core = _RegistryCore(run_dir, create=False, read_only=True)
    before = core.event_store.max_ordinal()
    original = _RegistryCore.__init__
    def read_only_init(self, *args, **kwargs):
        assert kwargs.get("read_only") is True, "a viewer must not acquire a writer"
        return original(self, *args, **kwargs)
    monkeypatch.setattr(_RegistryCore, "__init__", read_only_init)
    monkeypatch.setattr(loader, "load_catalog", lambda *a, **k: pytest.fail("viewer imported plugin"))
    first = view(run_dir)
    second = view(run_dir)
    assert first == second and core.event_store.max_ordinal() == before
    assert first["execution"]["head_ordinal"] == before
