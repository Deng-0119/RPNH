"""Focused checks for additive, non-authoritative dashboard reads."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.rpnh import inspection
from cpn.rpnh.agent_tasks import (
    AgentStage, agent_task_registration, build_agent_task_module,
)
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import Endpoint
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.errors import NotNativeRun
from cpn.rpnh.registry.main_thread import MainThreadRegistry
from cpn.rpnh.registry.schema_catalog import SchemaCatalog


@pytest.fixture(scope="module")
def compiled():
    return compile_module(
        build_agent_task_module((AgentStage("main", "Answer."),)),
        agent_task_registration(),
    )


def test_declared_boundaries_are_not_topological_or_terminal_evidence(compiled):
    source = replace(compiled.source, entry={
        "left": Endpoint("main", "request"),
        "right": Endpoint("main", "request"),
    }, exit={
        "result": Endpoint("main", "result"),
        "other": Endpoint("main", "result"),
    })
    symbolic = replace(compiled.symbolic, entry={
        "left": "main.request", "right": "main.request",
    }, exit={
        "result": "main.result", "other": "main.result",
    }, terminal_alternatives=(compiled.symbolic.terminal,))
    boundaries = inspection.project_compiled_boundaries(
        replace(compiled, source=source, symbolic=symbolic))
    assert [item["name"] for item in boundaries["entry"]] == ["left", "right"]
    assert [item["name"] for item in boundaries["exit"]] == ["other", "result"]
    assert len(boundaries["terminal_rules"]) == 2
    assert boundaries["terminal_rules"][0]["transition_ids"] == ["main.run"]
    assert boundaries["terminal_evidence"] == "not_provided"
    assert "config" not in boundaries["terminal_rules"][0]
    assert inspection.project_compiled_net(
        compiled, source={"mode": "initial_configured"})["schema_version"] == (
            "rpnh/net_view/v1")


def test_external_port_resolves_to_fused_compiled_place(compiled):
    symbolic = replace(compiled.symbolic, port_places={
        **compiled.symbolic.port_places,
        "main.request": "main.fused_ingress",
    })
    boundaries = inspection.project_compiled_boundaries(
        replace(compiled, symbolic=symbolic))
    assert boundaries["entry"] == [{
        "name": "request", "port": "main.request", "place": "main.fused_ingress",
    }]


def test_observation_never_creates_missing_run(tmp_path: Path):
    missing = tmp_path / "not-a-run"
    with pytest.raises(NotNativeRun):
        inspection.project_registry_observation(missing, catalog=SchemaCatalog())
    assert not missing.exists()


@pytest.mark.parametrize("heads,epochs,should_fail", [
    ((7, 7), (2, 2), False),
    ((7, 8), (2, 2), True),
    ((7, 7), (2, 3), True),
])
def test_observation_requires_stable_read_only_head(
        compiled, tmp_path, monkeypatch, heads, epochs, should_fail):
    class Store:
        def __init__(self):
            self.heads = iter(heads)
            self.epochs = iter(epochs)

        def max_ordinal(self):
            return next(self.heads)

        @property
        def writer_epoch(self):
            return next(self.epochs)

    calls = []
    store = Store()

    def open_core(path, *, create, read_only, catalog):
        calls.append((path, create, read_only, catalog))
        return SimpleNamespace(event_store=store, task_id="task:test")

    executable = SimpleNamespace(
        net_ref={"exact": "net"},
        verified_at_head=SimpleNamespace(ordinal=7, writer_fencing_epoch=2),
        transitions=(SimpleNamespace(
            transition_id="main.run", node_ref={"exact": "node"},
            operation_binding_ref={"exact": "operation"},
            binding_ref={"exact": "executable"}),),
    )
    monkeypatch.setattr(inspection, "_RegistryCore", open_core)
    monkeypatch.setattr(inspection, "hydrate_module_runtime", lambda core: (
        executable, SimpleNamespace(compiled=compiled), None))
    catalog = SchemaCatalog()
    if should_fail:
        with pytest.raises(RuntimeError, match="advanced during display observation"):
            inspection.project_registry_observation(tmp_path, catalog=catalog)
    else:
        result = inspection.project_registry_observation(tmp_path, catalog=catalog)
        assert result["schema_version"] == "rpnh/net_observation/v1"
        assert result["net"]["schema_version"] == "rpnh/net_view/v1"
        assert result["source"]["verified_head_ordinal"] == 7
        assert result["transition_bindings"][0]["node_ref"] == {"exact": "node"}
        assert result["transition_bindings"][0]["operation"] == "main.run"
        assert result["transition_bindings"][0]["input_ports"][0]["place"] == (
            "main.request")
        assert result["transition_bindings"][0]["output_ports"][0]["place"] == (
            "main.result")
        assert result["coverage"]["history"] == "unsupported"
        assert result["coverage"]["firings"] == "not_provided"
    assert calls == [(tmp_path.resolve(), False, True, catalog)]


def test_real_registry_writer_advance_rejects_mixed_observation(
        compiled, tmp_path, monkeypatch):
    """A real committed SQLite write cannot be joined to a prior snapshot."""
    run_dir = tmp_path / "real-registry"
    writer = _RegistryCore(run_dir, create=True)
    service = MainThreadRegistry(writer, session_root=tmp_path)
    initial = writer.event_store.max_ordinal()
    epoch = writer.event_store.writer_epoch

    def while_observing(reader):
        assert reader.read_only
        assert reader.event_store.max_ordinal() == initial
        service.create_thread(idempotency_key="real-concurrent-writer")
        return (
            SimpleNamespace(
                net_ref={"exact": "net"}, transitions=(),
                verified_at_head=SimpleNamespace(
                    ordinal=initial, writer_fencing_epoch=epoch)),
            SimpleNamespace(compiled=compiled), None,
        )

    monkeypatch.setattr(inspection, "hydrate_module_runtime", while_observing)
    with pytest.raises(RuntimeError, match="advanced during display observation"):
        inspection.project_registry_observation(run_dir, catalog=SchemaCatalog())
    assert writer.event_store.max_ordinal() > initial
    assert writer.event_store.writer_epoch == epoch
    assert MainThreadRegistry(
        _RegistryCore(run_dir, create=False, read_only=True),
        session_root=tmp_path,
    ).recover_thread()["state"] == "idle"
