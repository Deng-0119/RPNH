"""Offline native contract fixtures; installed process acceptance is separate."""
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fake_provider import MODEL, TOOLS, plan, response, write_profile
from rpnh_erp_bench.native import (build_spec, freeze_profile, graph_for,
                                   profile_identity, run_owner, safe_owner_projection)


def test_timeout_acceptance_budget_includes_observed_owner_startup():
    helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "native_acceptance.py"))
    previous = [{"backend_entered_elapsed_seconds": 35.2}, {"backend_entered_elapsed_seconds": 38.1}]
    assert helper["timeout_fixture_budget"](previous) == 54
    # Fast machines also stop before the fake tool's own 60-second deadline.
    assert helper["timeout_fixture_budget"]([{"backend_entered_elapsed_seconds": 1.1}]) == 17


def test_timeout_before_admission_is_not_misreported_as_backend_cancellation_failure():
    helper = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "native_acceptance.py"))
    with pytest.raises(AssertionError, match="before backend admission"):
        helper["require_backend_exit"](entered=False, exited=False)
    with pytest.raises(AssertionError, match="admitted backend did not exit"):
        helper["require_backend_exit"](entered=True, exited=False)
    helper["require_backend_exit"](entered=True, exited=True)


def test_graph_is_single_actor_with_closed_workspace_inventory():
    graph = graph_for()
    assert len(graph.nodes) == 1
    assert graph.nodes[0].execution.role == "actor"
    assert set(graph.nodes[0].execution.tools) == TOOLS - {"validate_plan", "erp_python"}
    assert graph.nodes[0].execution.plugin is None


def test_spec_preserves_prompt_and_serializes_real_effect_domains(tmp_path, monkeypatch):
    # Declaration-only fixture injection is not installed-owner acceptance.
    from cpn.plugins.catalog import load_catalog as installed_load_catalog
    from rpnh_erp_bench.plugin import factory
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog",
                        lambda document: installed_load_catalog(document, factories={"erp_bench": factory}))
    prompt = "  Synthetic original instruction.\nPreserve whitespace.\n"
    spec = build_spec(tmp_path / "r", tmp_path / "profile.json", tmp_path / "bridge.sock", "synthetic-trial", prompt)
    assert spec.prompt == prompt
    assert spec.stages == () and spec.max_attempts_per_stage is None
    assert spec.max_parallel_nodes == 1
    document = spec.as_worker_document()
    assert document["max_attempts_per_stage"] is None
    assert spec.managed_tool_policy["max_in_flight"] == 1
    from cpn.plugins.catalog import load_catalog
    from cpn.plugins.managed_tools import ManagedPluginToolCatalog
    catalog = load_catalog(spec.plugin_configuration)
    assert catalog.digest == spec.plugin_catalog_digest
    managed = ManagedPluginToolCatalog(catalog, {"erp_python": "erp_bench/erp_python",
        "validate_plan": "erp_bench/validate_plan"}, admitted_effects=("pure", "external_write"))
    domains = {row["registration_key"]: row for row in spec.managed_tool_policy["conflict_domains"]}
    for name, effect, writes in (("erp_python", "external_write", ("erp-world:synthetic-trial",)),
                                 ("validate_plan", "pure", ())):
        declaration = managed.declaration(name)
        domain = domains[declaration.registration_key]
        assert declaration.effect == domain["effect"] == effect
        assert tuple(domain["writes"]) == writes and not domain["reads"] and not domain["unknown"]
        assert spec.managed_bindings["executor"]["tools"][name]["selector"] == "erp_bench/" + name


@pytest.mark.parametrize("prompt", [None, "", " \n"])
def test_empty_original_prompt_rejected(tmp_path, prompt):
    with pytest.raises(ValueError, match="instruction"):
        build_spec(tmp_path / "r", tmp_path / "p", tmp_path / "sock", "trial", prompt)


def test_fake_adapter_sequence_is_three_submissions_and_only_synthetic_data(tmp_path):
    selection = write_profile(tmp_path / "profile")
    adapter = json.loads((selection.parent / "adapter.json").read_text())
    argv = [sys.executable if item == "{python}" else item for item in adapter["argv"]]
    request = {"protocol": "llm_request_envelope/v1", "model_condition": MODEL,
               "tools": [{"function": {"name": name}} for name in TOOLS], "messages": []}
    names = []
    for _ in range(3):
        completed = subprocess.run(argv, input=json.dumps(request), capture_output=True,
                                   text=True, timeout=5)
        assert completed.returncode == 0, completed.stderr
        from cpn.rpnh.response_protocol import canonicalize_llm_response_payload
        assert canonicalize_llm_response_payload(completed.stdout.encode()) == completed.stdout.encode()
        result = json.loads(completed.stdout)
        names += [call["name"] for call in result["tool_calls"]]
        request["messages"].append({"role": "assistant", "tool_calls": result["tool_calls"]})
    assert names == ["validate_plan", "erp_python", "write_file", "complete_interaction"]
    report_arguments = json.loads(result["tool_calls"][0]["arguments"])
    assert json.loads(report_arguments["content"]).startswith("Synthetic local adapter")
    assert plan()["orders"][0]["quantity"] == 4 and plan()["orders"][0]["list_price"] == 10
    request["tools"].append({"function": {"name": "workspace"}})
    with pytest.raises(ValueError, match="capability"):
        response(request)
    assert len(list((selection.parent / "transcript").glob("request-*.json"))) == 3
    assert json.loads(selection.read_text())["adapter_kind"] == "local_process"


@pytest.mark.parametrize("timeout", [0, -1, 1.5, True])
def test_owner_timeout_requires_positive_integer(tmp_path, timeout):
    with pytest.raises(ValueError, match="positive integer"):
        run_owner(None, tmp_path, agent_timeout_seconds=timeout)


@pytest.mark.parametrize("registry_status,quiescent", [("stopped_by_owner", True), ("running", False)])
def test_ordinary_timeout_uses_supported_stop_and_real_status_for_quiescence(tmp_path, monkeypatch, registry_status, quiescent):
    # A contract fixture only: this deliberately makes no native process claim.
    stopped = []
    process = SimpleNamespace(poll=lambda: 0 if stopped else None)
    class Control:
        def __init__(self, root):
            assert root == tmp_path
        def start(self, spec):
            return SimpleNamespace(process=process, task_id="fixture-task")
        def stop(self, task_id, *, startup_safe):
            stopped.append((task_id, startup_safe))
        def status(self, task_id):
            return {"registry": {"execution_status": registry_status}}
        def result(self, task_id):
            raise RuntimeError("owner stopped without terminal")
        def result_evidence(self, task_id):
            return {"actual_model_call_counts": [0, 0]}
    ticks = iter((0, 2, 3))
    monkeypatch.setattr("cpn.rpnh.task_control.TaskControl", Control)
    monkeypatch.setattr("rpnh_erp_bench.native.time.monotonic", lambda: next(ticks))
    value = run_owner(SimpleNamespace(max_attempts_per_stage=None), tmp_path, agent_timeout_seconds=1)
    assert stopped == [("fixture-task", True)]
    assert value["stop_reason"] == "official_agent_timeout"
    assert value["owner_quiescent"] is quiescent and value["process_exit_confirmed"]
    assert value["process_exit_code"] == 0
    assert not value["forced_termination"] and value["terminal"] is None


def test_safe_projection_keeps_actual_refs_and_omits_transcripts():
    raw = {"task_id": "fixture", "owner_quiescent": True, "process_exit_confirmed": True,
           "process_exit_code": 7, "forced_termination": False, "terminal": {"terminal_evidence_ref": {"version_id": "actual"},
           "terminal_result_ref": {"version_id": "result"}, "actual_model_call_counts": [3, 0],
           "output": "private synthetic body"},
           "result_evidence": {"actions": [{"agent_action_ref": {"version_id": "action"},
           "requests": ["private request"], "registered_return": {"outcome": "returned",
           "terminal_receipt_ref": {"resource_version_id": "receipt"}}}]}}
    projected = safe_owner_projection(raw)
    assert projected["actual_model_call_counts"] == [3, 0]
    assert projected["terminal_evidence_ref"] == raw["terminal"]["terminal_evidence_ref"]
    assert "private" not in json.dumps(projected)
    assert projected["semantic_use"] == "unknown"
    assert projected["process_exit_code"] == 7


@pytest.mark.parametrize("exit_code", [0, 7, -15])
def test_owner_exit_code_is_observed_not_derived_from_registry(tmp_path, monkeypatch, exit_code):
    # poll-only fixture deliberately has no .returncode attribute.
    process = SimpleNamespace(poll=lambda: exit_code)
    class Control:
        def __init__(self, root):
            pass
        def start(self, spec):
            return SimpleNamespace(process=process, task_id="fixture-task")
        def status(self, task_id):
            return {"registry": {"execution_status": "stopped_by_owner"}}
        def result(self, task_id):
            raise RuntimeError("stopped without terminal")
        def result_evidence(self, task_id):
            return {"actual_model_call_counts": [0, 0]}
    monkeypatch.setattr("cpn.rpnh.task_control.TaskControl", Control)
    result = run_owner(SimpleNamespace(max_attempts_per_stage=None), tmp_path)
    assert result["owner_quiescent"] is True
    assert result["process_exit_code"] == exit_code
    assert safe_owner_projection(result)["process_exit_code"] == exit_code


@pytest.mark.parametrize("relative_adapter", [False, True])
def test_freeze_profile_keeps_adapter_bytes_policy_and_private_permissions(tmp_path, relative_adapter):
    from cpn.llm_adapters import load_llm_execution_selection
    from cpn.rpnh.runtime_policy import RuntimePolicy
    from dataclasses import replace
    import stat

    source = tmp_path / "original"
    (source / "selections").mkdir(parents=True)
    (source / "adapters").mkdir()
    auth = source / "auth.json"
    auth.write_text('{"token":"synthetic-file-secret"}')
    adapter_path = source / "adapters" / "fixture.json"
    adapter = {
        "schema_version": "local_process_adapter_config/v2", "adapter_kind": "local_process",
        "model_condition": "synthetic-exact-model", "reasoning_effort": "high",
        "argv": ["{python}", "relative_adapter.py", "--service-tier", "priority", "--auth-file", str(auth)],
        "probe_argv": ["{python}", "relative_adapter.py", "--probe"],
        "env": {"FIXTURE_SECRET": "synthetic-env-secret", "AUTH_FILE": str(auth)},
        "inherit_env": ["FIXTURE_INHERITED_AUTH"]}
    adapter_bytes = (json.dumps(adapter, indent=3) + "\n").encode()
    adapter_path.write_bytes(adapter_bytes)
    selection = source / "selections" / "fixture.json"
    document = {
        "schema_version": "llm_execution_selection/v2", "adapter_kind": "local_process",
        "model_condition": "synthetic-exact-model",
        "adapter_config_path": "../adapters/fixture.json" if relative_adapter else str(adapter_path),
        "timeout_seconds": 83, "max_output_tokens": 2345, "max_response_bytes": 345678,
        "physical_profile": "synthetic-profile", "logical_selection_id": "synthetic-selection",
        "reasoning_effort": "high", "supported_reasoning_efforts": ["low", "high"],
        "default_reasoning_effort": "low", "context_window_tokens": 10000,
        "context_compaction_retained_tokens": 1000,
        "runtime": RuntimePolicy(max_turns_per_node=None, max_parallel_nodes=2,
                                  context_tool_output_byte_limit=1234).as_document()}
    selection.write_text(json.dumps(document))
    original_selection_bytes = selection.read_bytes()
    original = load_llm_execution_selection(selection)
    destination = tmp_path / "frozen"
    frozen_path = freeze_profile(selection, destination)
    frozen = load_llm_execution_selection(frozen_path)
    assert frozen_path == destination / "selection.json"
    assert replace(frozen, adapter_config_path=original.adapter_config_path) == original
    assert frozen.adapter_config_path == destination / "adapter.json"
    assert frozen.adapter_config_path.read_bytes() == adapter_bytes
    assert selection.read_bytes() == original_selection_bytes
    assert adapter_path.read_bytes() == adapter_bytes
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert {p.name for p in destination.iterdir()} == {"selection.json", "adapter.json"}
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in destination.iterdir())
    identity = profile_identity(frozen_path)
    assert identity["reasoning_effort"] == "high"
    assert identity["runtime"] == document["runtime"]
    assert identity["context_window_tokens"] == 10000
    assert "secret" not in json.dumps(identity) and str(tmp_path) not in json.dumps(identity)
    # The worker resolves argv against its run root; moving JSON must not rebase it.
    assert json.loads(frozen.adapter_config_path.read_bytes())["argv"] == adapter["argv"]
    # A later source edit cannot change what the owner loads from the snapshot.
    selection.write_text('{}')
    adapter_path.write_text('{}')
    assert profile_identity(frozen_path) == identity
    assert auth.read_text() == '{"token":"synthetic-file-secret"}'
    with pytest.raises(FileExistsError):
        freeze_profile(frozen_path, destination)


def test_freeze_legacy_selection_preserves_defaults_and_rejects_destination_link(tmp_path):
    from cpn.llm_adapters import load_llm_execution_selection
    selection = write_profile(tmp_path / "source-profile")
    original = load_llm_execution_selection(selection)
    frozen = load_llm_execution_selection(freeze_profile(selection, tmp_path / "frozen"))
    assert frozen.input_target == original.input_target
    assert frozen.runtime_policy == original.runtime_policy
    outside = tmp_path / "other"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        freeze_profile(selection, link / "snapshot")
    assert list(outside.iterdir()) == []


def test_freeze_rejects_nonlocal_and_adapter_identity_mismatch(tmp_path):
    selection = write_profile(tmp_path / "profile")
    document = json.loads(selection.read_text())
    document["adapter_kind"] = "external_provider"
    selection.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="local_process"):
        freeze_profile(selection, tmp_path / "unsupported")
    assert not (tmp_path / "unsupported").exists()
    document["adapter_kind"] = "local_process"
    selection.write_text(json.dumps(document))
    adapter = selection.parent / "adapter.json"
    data = json.loads(adapter.read_text())
    data["model_condition"] = "different-model"
    adapter.write_text(json.dumps(data))
    from cpn.llm_adapters import LLMExecutionConfigError
    with pytest.raises(LLMExecutionConfigError, match="differs from selection"):
        freeze_profile(selection, tmp_path / "mismatch")


def _codex_profile(tmp_path):
    selection = write_profile(tmp_path / "profile")
    adapter = selection.parent / "adapter.json"
    document = json.loads(adapter.read_bytes())
    document["argv"] = ["{python}", "-B", "-m", "cpn.llm_adapters.codex_subscription_bridge",
                        "--codex", "{codex}", "--model", MODEL, "--service-tier", "priority"]
    document["env"] = {"FIXTURE_AUTH": "synthetic-private-token", "AUTH_FILE": "private/auth.json"}
    adapter.write_bytes((json.dumps(document, indent=3) + "\n").encode())
    shim = tmp_path / "codex-shim"
    shim.write_text("#!/bin/sh\nexit 93\n")  # Never launched by these tests.
    shim.chmod(0o700)
    return selection, adapter, shim


def test_freeze_codex_override_retains_original_and_changes_only_one_token(tmp_path):
    import stat
    from dataclasses import replace
    from cpn.llm_adapters import load_llm_execution_selection
    selection, adapter, shim = _codex_profile(tmp_path)
    original_bytes = adapter.read_bytes()
    original_selection = load_llm_execution_selection(selection)
    expected_identity = profile_identity(selection)
    frozen_path = freeze_profile(selection, tmp_path / "frozen", codex_executable=shim)
    frozen = load_llm_execution_selection(frozen_path)
    assert replace(frozen, adapter_config_path=original_selection.adapter_config_path) == original_selection
    assert profile_identity(frozen_path) == expected_identity
    assert adapter.read_bytes() == original_bytes
    retained = frozen_path.parent / "original-adapter.json"
    assert retained.read_bytes() == original_bytes
    assert frozen.adapter_config_path.read_bytes() != original_bytes
    expected = json.loads(original_bytes)
    expected["argv"][expected["argv"].index("{codex}")] = str(shim.resolve())
    assert json.loads(frozen.adapter_config_path.read_bytes()) == expected
    assert {p.name for p in frozen_path.parent.iterdir()} == {"selection.json", "adapter.json", "original-adapter.json"}
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in frozen_path.parent.iterdir())
    assert "synthetic-private-token" not in json.dumps(profile_identity(frozen_path))


def test_freeze_codex_default_still_copies_original_bytes_only(tmp_path):
    selection, adapter, _ = _codex_profile(tmp_path)
    original_bytes = adapter.read_bytes()
    frozen = freeze_profile(selection, tmp_path / "frozen")
    assert (frozen.parent / "adapter.json").read_bytes() == original_bytes
    assert not (frozen.parent / "original-adapter.json").exists()


def test_existing_script_profile_binds_supported_module_without_changing_model_policy(tmp_path):
    from rpnh_erp_bench.endpoint import is_codex_profile
    selection, adapter, shim = _codex_profile(tmp_path)
    document = json.loads(adapter.read_bytes())
    legacy = tmp_path / "codex_subscription_bridge_outer_sandbox.py"
    legacy.write_text("raise RuntimeError('legacy sandbox wrapper must never execute')\n")
    tail = document["argv"][4:]
    document["argv"] = ["{python}", str(legacy), *tail]
    adapter.write_text(json.dumps(document))
    original = adapter.read_bytes()
    identity = profile_identity(selection)
    assert is_codex_profile(selection)
    frozen = freeze_profile(selection, tmp_path / "frozen", codex_executable=shim)
    result = json.loads((frozen.parent / "adapter.json").read_bytes())
    expected_tail = [str(shim.resolve()) if a == "{codex}" else a for a in tail]
    assert result["argv"] == ["{python}", "-m", "cpn.llm_adapters.codex_subscription_bridge", *expected_tail]
    assert {k: v for k, v in result.items() if k != "argv"} == {k: v for k, v in document.items() if k != "argv"}
    assert profile_identity(frozen) == identity
    assert adapter.read_bytes() == original == (frozen.parent / "original-adapter.json").read_bytes()


@pytest.mark.parametrize("argv", [
    ["{python}", "other.py", "{codex}"],
    ["{python}", "-m", "other.module", "{codex}"],
    ["{python}", "-m", "cpn.llm_adapters.codex_subscription_bridge"],
    ["{python}", "-m", "cpn.llm_adapters.codex_subscription_bridge", "{codex}", "{codex}"],
    ["{python}", "-m", "cpn.llm_adapters.codex_subscription_bridge", "--codex={codex}"],
    ["{python}", "cpn.llm_adapters.codex_subscription_bridge", "{codex}", "-m"],
])
def test_freeze_codex_rejects_nonmatching_bridge_before_writing(tmp_path, argv):
    selection, adapter, shim = _codex_profile(tmp_path)
    document = json.loads(adapter.read_bytes())
    document["argv"] = argv
    adapter.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="subscription bridge"):
        freeze_profile(selection, tmp_path / "frozen", codex_executable=shim)
    assert not (tmp_path / "frozen").exists()


@pytest.mark.parametrize("kind", ["missing", "directory", "not-executable"])
def test_freeze_codex_requires_existing_executable_file(tmp_path, kind):
    selection, _, shim = _codex_profile(tmp_path)
    if kind == "missing":
        shim.unlink()
    elif kind == "directory":
        shim.unlink(); shim.mkdir()
    else:
        shim.chmod(0o600)
    with pytest.raises((ValueError, FileNotFoundError)):
        freeze_profile(selection, tmp_path / "frozen", codex_executable=shim)
    assert not (tmp_path / "frozen").exists()
