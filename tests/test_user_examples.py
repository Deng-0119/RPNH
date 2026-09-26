"""Focused deterministic checks for the three public newcomer examples."""
from __future__ import annotations

import importlib.util
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cpn.plugins.api import PluginError
from cpn.plugins.api import canonical
from cpn.plugins.catalog import BoundPlugin, PluginCatalog
from cpn.plugins.runtime import run_plugin
from cpn.rpnh.agent_tasks import AgentStage, agent_task_catalog
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.main_session import MainSession
from cpn.rpnh.task_control import TaskControl
from cpn.rpnh_cli import _BasicFrontendState, _task_command


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def example_plugin():
    source = str(EXAMPLES / "native_plugin")
    if source not in sys.path:
        sys.path.insert(0, source)
    sys.modules.pop("rpnh_demo", None)
    return importlib.import_module("rpnh_demo")


@pytest.fixture
def example_catalog(example_plugin):
    return PluginCatalog((BoundPlugin(example_plugin.plugin(), {}),))


def test_native_tool_and_instruction_create_real_terminal_runs(
        tmp_path: Path, example_plugin, example_catalog: PluginCatalog,
) -> None:
    add_result = run_plugin(
        example_catalog, "demo/add", {"left": 2, "right": 3},
        run_dir=tmp_path / "add-run")
    assert add_result["output"] == {"value": 5}
    assert add_result["terminal_evidence_ref"] is not None
    assert add_result["actual_model_call_counts"][0] == 0

    instruction_result = run_plugin(
        example_catalog, "demo/instruction", {},
        run_dir=tmp_path / "instruction-run")
    output = instruction_result["output"]
    assert output["instruction"] == (
        "Use the supplied exact inputs. Report the computed result.")
    assert output["source_resource_id"].startswith("resource:")
    assert output["source_resource_version_id"].startswith(
        "resource_version:")
    assert instruction_result["terminal_evidence_ref"] is not None
    assert instruction_result["actual_model_call_counts"][0] == 0
    operations = example_plugin.plugin().operations
    assert {operation.max_result_bytes for operation in operations} == {1024}
    assert len(canonical(add_result["output"])) <= 1024
    assert len(canonical(instruction_result["output"])) <= 1024
    operation_by_name = {operation.name: operation for operation in operations}
    context = SimpleNamespace(check_cancelled=lambda: None)
    largest_add = operation_by_name["add"].handler(
        context, {"left": 1_000_000_000, "right": 1_000_000_000})
    largest_summary = operation_by_name["summarize"].handler(
        context, {"values": [1_000_000] * 1000})
    assert len(canonical(largest_add)) <= operation_by_name["add"].max_result_bytes
    assert len(canonical(largest_summary)) <= operation_by_name["summarize"].max_result_bytes


def test_native_example_rejects_missing_and_empty_inputs_before_a_run(
        tmp_path: Path, example_catalog: PluginCatalog,
) -> None:
    with pytest.raises(PluginError):
        run_plugin(
            example_catalog, "demo/add", {"left": 2},
            run_dir=tmp_path / "missing-run")
    with pytest.raises(PluginError):
        run_plugin(
            example_catalog, "demo/summarize", {"values": []},
            run_dir=tmp_path / "empty-run")
    with pytest.raises(PluginError):
        run_plugin(
            example_catalog, "demo/add", {"left": 1_000_000_001, "right": 0},
            run_dir=tmp_path / "unbounded-run")
    assert not (tmp_path / "missing-run").exists()
    assert not (tmp_path / "empty-run").exists()
    assert not (tmp_path / "unbounded-run").exists()


@pytest.mark.parametrize(("input_name", "expected", "rename_nodes"), (
    ("input.txt", "3 batches total 36; mean 12; minimum 9; maximum 15.", False),
    ("variant-input.txt", "3 batches total 39; mean 13; minimum 9; maximum 18.", True),
))
def test_hybrid_example_uses_real_plugin_output(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        example_catalog: PluginCatalog, input_name: str, expected: str,
        rename_nodes: bool,
) -> None:
    hybrid = _load_module(
        "rpnh_public_hybrid_example_" + input_name.replace(".", "_"),
        EXAMPLES / "hybrid_summary" / "run.py",
    )
    profile = _load_module(
        "rpnh_public_example_profile",
        EXAMPLES / "_support" / "profile.py",
    )
    import cpn.plugins.catalog as catalog_module
    monkeypatch.setattr(
        catalog_module, "load_catalog", lambda _document: example_catalog)
    monkeypatch.setattr(
        hybrid, "load_catalog", lambda _document: example_catalog)
    execution = profile.write_scripted_profile(tmp_path / "profile")
    graph_path = EXAMPLES / "hybrid_summary" / "graph.json"
    expected_nodes = {"team.normalize", "team.summarize", "team.explain"}
    if rename_nodes:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        renamed = {
            "normalize": "prepare",
            "summarize": "calculate",
            "explain": "report",
        }
        for node in graph["nodes"]:
            node["node_id"] = renamed[node["node_id"]]
        for arc in graph["arcs"]:
            arc["source"]["node_id"] = renamed[arc["source"]["node_id"]]
            arc["target"]["node_id"] = renamed[arc["target"]["node_id"]]
        graph["ingress"]["node_id"] = renamed[graph["ingress"]["node_id"]]
        graph["egress"]["node_id"] = renamed[graph["egress"]["node_id"]]
        graph_path = tmp_path / "renamed-graph.json"
        graph_path.write_text(json.dumps(graph), encoding="utf-8")
        expected_nodes = {"team.prepare", "team.calculate", "team.report"}
    run_dir = tmp_path / "run"
    result = hybrid.run_example(
        run_dir=run_dir,
        input_path=EXAMPLES / "hybrid_summary" / input_name,
        graph_path=graph_path,
        plugin_config_path=EXAMPLES / "native_plugin" / "plugins.json",
        execution_config_path=execution,
    )
    assert result["stop_reason"] == "terminal"
    assert result["terminal_evidence_ref"] is not None
    assert result["output"] == expected
    assert result["actual_model_call_counts"][0] == 4
    projection = project_registry_net(
        run_dir, catalog=agent_task_catalog(example_catalog))
    assert projection["summary"]["transition_count"] == 3
    assert {node["id"] for node in projection["nodes"]} >= expected_nodes


def test_workflow_pattern_catalog_matches_sanitized_validation() -> None:
    patterns = _load_module(
        "rpnh_public_workflow_pattern_catalog",
        EXAMPLES / "workflow_patterns" / "run.py",
    )
    validation = json.loads((
        EXAMPLES / "workflow_patterns" / "validation.json"
    ).read_text(encoding="utf-8"))
    expected_nodes = {
        "serial": 3,
        "parallel": 4,
        "document": 4,
        "long_process": 6,
    }
    for name, count in expected_nodes.items():
        scenario = patterns.load_scenario(name)
        assert len(scenario["graph"]["nodes"]) == count
        assert validation["scenarios"][name]["transition_count"] == count
        assert validation["scenarios"][name]["registry_terminal"] is True
    parallel = patterns.load_scenario("parallel")
    assert parallel["max_parallel_nodes"] == 2
    assert len(parallel["graph"]["arcs"]) == 4
    join = next(
        node for node in parallel["graph"]["nodes"]
        if node["node_id"] == "join")
    assert {item["port_id"] for item in join["input_ports"]} == {
        "facts", "risks"}


def test_parallel_workflow_pattern_reaches_registered_join(
        tmp_path: Path,
) -> None:
    patterns = _load_module(
        "rpnh_public_parallel_workflow_pattern",
        EXAMPLES / "workflow_patterns" / "run.py",
    )
    profile = _load_module(
        "rpnh_public_workflow_pattern_profile",
        EXAMPLES / "_support" / "profile.py",
    )
    result = patterns.run_example(
        scenario_name="parallel",
        run_dir=tmp_path / "parallel-run",
        execution_config_path=profile.write_scripted_profile(
            tmp_path / "profile"),
    )
    assert result["status"] == "PASS"
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "Parallel workflow complete."
    assert result["actual_model_call_counts"] == [4, 0]
    assert result["petri_net"] == {
        "node_count": 13,
        "edge_count": 26,
        "transition_count": 4,
        "place_count": 9,
        "resource_place_count": 0,
        "resource_edge_count": 0,
    }


def test_two_independent_scripted_tasks_keep_distinct_results_and_nets(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _load_module(
        "rpnh_public_task_example_profile",
        EXAMPLES / "_support" / "profile.py",
    )
    execution = profile.write_scripted_profile(tmp_path / "profile")
    monkeypatch.delenv("RPNH_PLUGIN_CONFIG", raising=False)
    control = TaskControl(tmp_path / "control")
    session = MainSession(
        tmp_path / "session", execution, task_control=control)
    first = session.launch(
        "List exactly three data quality checks for a small batch table.",
        (AgentStage("worker", "Complete the requested task and return its result."),),
    )
    second = session.launch(
        "Task values: 4, 8, 12. Summarize the count, total and mean.",
        (AgentStage("worker", "Complete the requested task and return its result."),),
    )
    assert first.process.wait(timeout=30) == 0
    assert second.process.wait(timeout=30) == 0
    assert first.task_id != second.task_id
    assert first.run_dir != second.run_dir

    state = _BasicFrontendState()
    assert _task_command(session, f"/switch {first.task_id}", state)
    assert state.selected_task_id == first.task_id
    first_result = control.result(first.task_id)
    assert "Check missing values" in first_result["output"]
    assert _task_command(session, f"/switch {second.task_id}", state)
    second_result = control.result(second.task_id)
    assert second_result["output"] == "3 values total 24; mean 8."
    assert control.status(first.task_id)["registry"]["execution_status"] == "terminal"
    assert control.status(second.task_id)["registry"]["execution_status"] == "terminal"
    assert control.net(first.task_id)["summary"]["transition_count"] == 1
    assert control.net(second.task_id)["summary"]["transition_count"] == 1
    assert _task_command(session, "/switch main", state)
    assert state.selected_task_id is None
