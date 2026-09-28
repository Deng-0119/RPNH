from __future__ import annotations

import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import tarfile
import threading

import pytest

from cpn.components.agent_loop import optional_execution
from cpn.components.agent_loop.optional_host_bindings import (
    _declared_agent_prompt,
)
from cpn.components.agent_loop.optional_execution import (
    optional_agent_loop_schema_data,
)
from cpn.components.basic import CONFIG_SCHEMA_ID
from cpn.rpnh.agent_tasks import (
    AgentStage,
    AgentTaskSpec,
    agent_task_catalog,
    agent_task_registration,
    build_agent_task_module,
    build_agent_workflow_module,
    resume_agent_task,
    run_agent_task,
)
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.llm_contracts import LLMInputResponseBytes
from cpn.rpnh.agent_workflows import (
    AgentWorkflowArc,
    AgentWorkflowEndpoint,
    AgentWorkflowExecution,
    AgentWorkflowGraph,
    AgentWorkflowNode,
    AgentWorkflowPort,
)
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.llm_contracts import LLMInputTarget
from cpn.rpnh.main_session import MainSession, parse_main_decision
from cpn.rpnh.task_control import TaskControl
from cpn.rpnh.task_control import claim_task_worker_launch
from cpn.rpnh.unix_transport import unix_socket_address
from cpn.rpnh_cli import _task_command


class _Process:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.return_code = None
        self.signals: list[int] = []

    def poll(self):
        return self.return_code

    def send_signal(self, value):
        self.signals.append(value)


def _workflow_graph() -> AgentWorkflowGraph:
    port = AgentWorkflowPort
    endpoint = AgentWorkflowEndpoint
    return AgentWorkflowGraph(
        nodes=(
            AgentWorkflowNode(
                "plan", "Plan the work.",
                (port("request", "task"),),
                (port("plan", "plan"),)),
            AgentWorkflowNode(
                "deliver", "Deliver the result.",
                (port("plan", "plan"),),
                (port("result", "result"),)),
        ),
        arcs=(AgentWorkflowArc(
            "plan_to_deliver", endpoint("plan", "plan"),
            endpoint("deliver", "plan")),),
        ingress=endpoint("plan", "request"),
        egress=endpoint("deliver", "result"),
    )


def _fanout_workflow_graph() -> AgentWorkflowGraph:
    port = AgentWorkflowPort
    endpoint = AgentWorkflowEndpoint
    return AgentWorkflowGraph(
        nodes=(
            AgentWorkflowNode(
                "start", "Create the shared starting point.",
                (port("request", "task"),),
                (port("seed", "seed"),)),
            AgentWorkflowNode(
                "branch_a", "Produce branch A.",
                (port("seed", "seed"),),
                (port("a", "branch_a"),)),
            AgentWorkflowNode(
                "branch_b", "Produce branch B.",
                (port("seed", "seed"),),
                (port("b", "branch_b"),)),
            AgentWorkflowNode(
                "join", "Join both branches.",
                (port("a", "branch_a"), port("b", "branch_b")),
                (port("result", "result"),)),
        ),
        arcs=(
            AgentWorkflowArc(
                "start_to_a", endpoint("start", "seed"),
                endpoint("branch_a", "seed")),
            AgentWorkflowArc(
                "start_to_b", endpoint("start", "seed"),
                endpoint("branch_b", "seed")),
            AgentWorkflowArc(
                "a_to_join", endpoint("branch_a", "a"),
                endpoint("join", "a")),
            AgentWorkflowArc(
                "b_to_join", endpoint("branch_b", "b"),
                endpoint("join", "b")),
        ),
        ingress=endpoint("start", "request"),
        egress=endpoint("join", "result"),
    )


def _feedback_workflow_graph(max_rework_cycles: int) -> AgentWorkflowGraph:
    port = AgentWorkflowPort
    endpoint = AgentWorkflowEndpoint
    return AgentWorkflowGraph(
        nodes=(
            AgentWorkflowNode(
                "draft", "Draft or revise the result.",
                (port("request", "task"), port("changes", "changes")),
                (port("candidate", "draft"),)),
            AgentWorkflowNode(
                "review", "Review the draft.",
                (port("candidate", "draft"),),
                (port("changes", "changes"), port("result", "result"))),
        ),
        arcs=(
            AgentWorkflowArc(
                "draft_to_review", endpoint("draft", "candidate"),
                endpoint("review", "candidate")),
            AgentWorkflowArc(
                "review_to_draft", endpoint("review", "changes"),
                endpoint("draft", "changes"), "feedback"),
        ),
        ingress=endpoint("draft", "request"),
        egress=endpoint("review", "result"),
        max_rework_cycles=max_rework_cycles,
    )


def _profiled_workflow_graph(
        plan_profile: str, deliver_profile: str,
) -> AgentWorkflowGraph:
    port = AgentWorkflowPort
    endpoint = AgentWorkflowEndpoint
    return AgentWorkflowGraph(
        nodes=(
            AgentWorkflowNode(
                "plan", "Plan the work.",
                (port("request", "task"),),
                (port("plan", "plan"),),
                AgentWorkflowExecution(profile_id=plan_profile)),
            AgentWorkflowNode(
                "deliver", "Deliver the result.",
                (port("plan", "plan"),),
                (port("result", "result"),),
                AgentWorkflowExecution(profile_id=deliver_profile)),
        ),
        arcs=(AgentWorkflowArc(
            "plan_to_deliver", endpoint("plan", "plan"),
            endpoint("deliver", "plan")),),
        ingress=endpoint("plan", "request"),
        egress=endpoint("deliver", "result"),
    )


def test_single_agent_and_graph_workflow_compile_as_real_modules() -> None:
    single = build_agent_task_module((
        AgentStage("main", "Answer as the only main-session agent."),
    ))
    registration = agent_task_registration()
    schemas, _types = optional_agent_loop_schema_data()
    workflow = build_agent_workflow_module(
        _workflow_graph(),
        executor_key="rpnh/default-agent/v1",
        terminal_key="rpnh/agent-task-terminal/v1",
        tools=(),
        required_schemas=(CONFIG_SCHEMA_ID, *schemas),
    )
    single_compiled = compile_module(single, registration)
    workflow_compiled = compile_module(workflow, registration)

    assert [item.name for item in single_compiled.symbolic.transitions] == [
        "main.run"]
    assert {item.name for item in workflow_compiled.symbolic.transitions} == {
        "team.plan", "team.deliver"}
    assert workflow_compiled.symbolic.entry == {"request": "team.request"}
    assert workflow_compiled.symbolic.exit == {"result": "team.result"}
    plan = next(
        item for item in workflow_compiled.operations
        if item.declaration.name == "team.plan")
    prompt = _declared_agent_prompt(
        plan, LLMInputTarget("test-model", 1024, 8192))
    assert prompt["messages"][0]["content"] == "Plan the work."


@pytest.mark.parametrize(
    ("max_rework_cycles", "max_attempts_per_node"), ((1, 4), (5, 2)))
def test_rework_cycle_budget_does_not_cap_one_firing_turns(
        max_rework_cycles: int, max_attempts_per_node: int,
) -> None:
    schemas, _types = optional_agent_loop_schema_data()
    module = build_agent_workflow_module(
        _feedback_workflow_graph(max_rework_cycles),
        executor_key="rpnh/default-agent/v1",
        terminal_key="rpnh/agent-task-terminal/v1",
        tools=(),
        required_schemas=(CONFIG_SCHEMA_ID, *schemas),
        max_attempts_per_node=max_attempts_per_node,
    ).to_dict()
    rework = next(
        operation
        for operation in module["components"][0]["operations"]
        if operation["name"] == "draft__rework")

    assert rework["config"]["resource_bounds"] == {
        "max_llm_attempts": max_attempts_per_node,
        "max_tool_turns": max_attempts_per_node,
    }
    assert rework["config"]["turn_budget_extension"] == max_attempts_per_node
    rework_bucket = next(
        bucket for bucket in module["budget_buckets"]
        if bucket["bucket_id"] == "rpnh:workflow:rework")
    assert rework_bucket["max_attempts"] == (
        max_rework_cycles * max_attempts_per_node)


def test_main_decision_accepts_variable_workflow_topology() -> None:
    graph = _workflow_graph()
    decision = parse_main_decision({
        "reply": "启动独立工作流。",
        "task": {
            "kind": "workflow",
            "prompt": "完成任务",
            "graph": graph.to_dict(),
        },
    })
    assert decision.task is not None
    assert decision.task.stages == ()
    assert decision.task.workflow_graph == graph


def test_task_control_launches_an_installed_module_worker(
        tmp_path: Path, capsys,
) -> None:
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-task-control",
        "argv": [sys.executable, "-c", "pass"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-task-control",
        "adapter_config_path": str(adapter),
        "timeout_seconds": 30,
        "max_output_tokens": 64,
        "max_response_bytes": 65536,
    }), encoding="utf-8")
    control = TaskControl(
        tmp_path / "control",
        popen_factory=_Process,
        channel_ready=lambda _path: True,
    )
    session = MainSession(
        tmp_path / "session",
        execution,
        task_control=control,
    )
    assert _task_command(session, "/agent inspect this repository") is True
    task_id = capsys.readouterr().out.split()[1]
    handle = control.get(task_id)
    assert handle.kind == "single_agent"
    assert handle.process.kwargs["cwd"] == str(tmp_path / "control")
    assert handle.process.kwargs["env"]["PYTHONPATH"].split(
        os.pathsep)[0] == str(Path(__file__).resolve().parents[1])
    worker_argv = handle.process.args[0]
    assert worker_argv[1:3] == ["-m", "cpn.rpnh.task_worker"]
    assert worker_argv[3] == "--launch-lock-fd"
    assert worker_argv[-1] == str(
        next((tmp_path / "control" / "specs").glob("*.json")))

    spec_path = next((tmp_path / "control" / "specs").glob("*.json"))
    document = json.loads(spec_path.read_text(encoding="utf-8"))
    assert AgentTaskSpec.from_worker_document(
        document, document_root=spec_path.parent).stages == (
        AgentStage(
            "worker",
            "Complete the requested task and return its result.",
        ),
    )
    assert control.stop(task_id)["status"] == "STOP_REQUESTED"
    assert handle.process.signals == [signal.SIGINT]


def test_task_control_serializes_canonical_paths_before_worker_launch(
        tmp_path: Path, monkeypatch,
) -> None:
    invocation = tmp_path / "invocation"
    other_cwd = tmp_path / "other"
    invocation.mkdir()
    other_cwd.mkdir()
    monkeypatch.chdir(invocation)
    spec = AgentTaskSpec(
        run_dir=Path("runs/task"),
        prompt="Inspect canonical worker paths.",
        stages=(AgentStage("worker", "Inspect the task."),),
        execution_config_path=Path("profiles/default.json"),
        execution_profiles=(("critic", Path("profiles/critic.json")),),
    )
    expected_run = invocation / "runs" / "task"
    expected_default = invocation / "profiles" / "default.json"
    expected_critic = invocation / "profiles" / "critic.json"

    monkeypatch.chdir(other_cwd)
    control = TaskControl(
        tmp_path / "control",
        popen_factory=_Process,
        channel_ready=lambda _path: False,
    )
    handle = control.start(spec)
    document = json.loads(next(
        (tmp_path / "control" / "specs").glob("*.json")
    ).read_text(encoding="utf-8"))

    document_root = tmp_path / "control" / "specs"
    assert document["schema_version"] == "rpnh/agent_task_spec/v6"
    assert document["run_relative_path"] == Path(os.path.relpath(
        expected_run, document_root)).as_posix()
    assert document["execution_config_relative_path"] == Path(os.path.relpath(
        expected_default, document_root)).as_posix()
    assert document["execution_profiles"] == {
        "critic": Path(os.path.relpath(
            expected_critic, document_root)).as_posix()}
    assert handle.run_dir == expected_run
    assert handle.socket_path == expected_run / "owner.sock"
    assert document["owner_socket_relative_path"] == "owner.sock"
    restored = AgentTaskSpec.from_worker_document(
        document, document_root=document_root)
    assert restored.run_dir == expected_run
    assert restored.execution_config_path == expected_default
    assert restored.execution_profiles == (("critic", expected_critic),)
    assert restored.owner_socket_path == handle.socket_path


def test_task_control_keeps_owner_socket_in_registry_and_shortens_only_transport(
        tmp_path: Path,
) -> None:
    long_run = tmp_path / ("session-" + "x" * 80) / ("run-" + "y" * 80)
    control_root = tmp_path / "control"
    control = TaskControl(control_root, popen_factory=_Process)
    handle = control.start(AgentTaskSpec(
        run_dir=long_run,
        prompt="Keep the owner channel addressable.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    ))
    spec_path = control_root / "specs" / f"{handle.task_id}.json"
    manifest_path = control_root / "manifests" / f"{handle.task_id}.json"
    worker_spec = AgentTaskSpec.from_worker_document(
        json.loads(spec_path.read_text(encoding="utf-8")),
        document_root=spec_path.parent)

    assert len(os.fsencode(str(long_run / "owner.sock"))) >= 108
    assert handle.socket_path == long_run / "owner.sock"
    assert worker_spec.owner_socket_path == handle.socket_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "rpnh/task_handle/v2"
    assert manifest["socket_relative_path"] == "owner.sock"
    assert all(not Path(value).is_absolute() for key, value in manifest.items()
               if key.endswith("_relative_path"))
    long_run.mkdir(parents=True)
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with unix_socket_address(handle.socket_path) as address:
            assert len(os.fsencode(address)) < 108
            channel.bind(address)
    finally:
        channel.close()
        handle.socket_path.unlink()

    recovered = TaskControl(control_root, popen_factory=_Process)
    assert recovered.get(handle.task_id).socket_path == handle.socket_path


def test_owner_socket_remains_local_through_frontend_grouping_depth(
        tmp_path: Path,
) -> None:
    nested = tmp_path
    for number in range(6):
        nested /= f"frontend-layer-{number}-" + "x" * 24
    control = TaskControl(
        nested / "threads" / ("ses_" + "a" * 32) / "main-turn-control",
        popen_factory=_Process,
    )
    handle = control.start(AgentTaskSpec(
        run_dir=nested / "runs" / "one",
        prompt="Exercise a deeply grouped frontend owner.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    ))

    assert handle.socket_path == handle.run_dir / "owner.sock"
    handle.run_dir.mkdir(parents=True)
    with unix_socket_address(handle.socket_path) as address:
        assert len(os.fsencode(address)) < 108


def test_task_status_preserves_local_exit_code_without_forging_recovered_code(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CompletedProcess:
        pid = 4242

        @staticmethod
        def poll():
            return 0

        @staticmethod
        def send_signal(_value):
            raise AssertionError("completed process must not be signalled")

    root = tmp_path / "control"
    control = TaskControl(root, popen_factory=_Process)
    handle = control.start(AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="Return one result.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    ))
    completed = _CompletedProcess()
    handle.process = completed
    control._write_manifest(
        handle, launch_state="active", launch_mode="fresh",
        pid=completed.pid, process_start_ticks=12345)
    monkeypatch.setattr(
        "cpn.rpnh.task_control._process_start_ticks", lambda _pid: None)

    local = control.status(handle.task_id)

    assert handle.process is completed
    assert local["process_status"] == "EXITED"
    assert local["return_code"] == 0

    recovered = TaskControl(root, popen_factory=_Process)
    detached = recovered.status(handle.task_id)
    assert detached["process_status"] == "FAILED"
    assert detached["return_code"] is None


def test_task_control_upgrades_pending_v3_worker_specs_before_recovery(
        tmp_path: Path,
) -> None:
    root = tmp_path / "control"
    first = TaskControl(root, popen_factory=_Process)
    handle = first.start(AgentTaskSpec(
        run_dir=tmp_path / ("run-" + "x" * 100),
        prompt="Recover a legacy worker specification.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    ))
    spec_path = root / "specs" / f"{handle.task_id}.json"
    manifest_path = root / "manifests" / f"{handle.task_id}.json"
    legacy = handle.spec.as_worker_document()
    legacy["schema_version"] = "rpnh/agent_task_spec/v3"
    legacy.pop("owner_socket_path")
    spec_path.write_text(json.dumps(legacy), encoding="utf-8")
    manifest = {
        "schema_version": "rpnh/task_handle/v1",
        "task_id": handle.task_id,
        "kind": handle.kind,
        "run_dir": str(handle.run_dir),
        "socket_path": str(handle.run_dir / "owner.sock"),
        "log_path": str(handle.log_path),
        "spec_path": str(spec_path),
        "pid": None,
        "process_start_ticks": None,
        "launch_state": "pending",
        "launch_mode": "fresh",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    recovered = TaskControl(root, popen_factory=_Process)
    recovered_handle = recovered.get(handle.task_id)
    upgraded = json.loads(spec_path.read_text(encoding="utf-8"))

    assert upgraded["schema_version"] == "rpnh/agent_task_spec/v6"
    assert upgraded["owner_socket_relative_path"] == "owner.sock"
    assert recovered_handle.socket_path == recovered_handle.run_dir / "owner.sock"
    assert json.loads(manifest_path.read_text(
        encoding="utf-8"))["schema_version"] == "rpnh/task_handle/v2"


def test_legacy_pending_recovery_never_overwrites_an_inflight_worker_claim(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "control"
    first = TaskControl(root, popen_factory=_Process)
    handle = first.start(AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="Preserve the original launch claim.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    ))
    spec_path = root / "specs" / f"{handle.task_id}.json"
    manifest_path = root / "manifests" / f"{handle.task_id}.json"
    legacy_spec = handle.spec.as_worker_document()
    legacy_spec["schema_version"] = "rpnh/agent_task_spec/v3"
    legacy_spec.pop("owner_socket_path")
    spec_path.write_text(json.dumps(legacy_spec), encoding="utf-8")
    legacy_manifest = {
        "schema_version": "rpnh/task_handle/v1",
        "task_id": handle.task_id,
        "kind": handle.kind,
        "run_dir": str(handle.run_dir),
        "socket_path": str(handle.run_dir / "owner.sock"),
        "log_path": str(handle.log_path),
        "spec_path": str(spec_path),
        "pid": None,
        "process_start_ticks": None,
        "launch_state": "pending",
        "launch_mode": "fresh",
    }
    manifest_path.write_text(
        json.dumps(legacy_manifest), encoding="utf-8")
    spawned: list[_Process] = []
    monkeypatch.setattr(
        "cpn.rpnh.task_control._acquire_task_launch_lock",
        lambda _root, _task_id: None)

    recovered = TaskControl(
        root,
        popen_factory=lambda *args, **kwargs: (
            spawned.append(_Process(*args, **kwargs)) or spawned[-1]),
    )

    assert spawned == []
    assert recovered.get(handle.task_id).launch_state == "pending"
    assert json.loads(manifest_path.read_text(
        encoding="utf-8")) == legacy_manifest


def test_task_control_rehomes_a_persisted_global_tmp_owner_socket(
        tmp_path: Path,
) -> None:
    root = tmp_path / "control"
    control = TaskControl(root, popen_factory=_Process)
    handle = control.start(AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="Rehome the old owner socket.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    ))
    old_socket = Path("/tmp/rpnh-owner-1234") / ("a" * 64 + ".sock")
    handle.spec = AgentTaskSpec.from_worker_document({
        **handle.spec.as_worker_document(),
        "owner_socket_path": str(old_socket),
    })
    handle.socket_path = old_socket

    control._upgrade_legacy_owner_socket(handle)

    assert handle.socket_path == handle.run_dir / "owner.sock"
    assert handle.spec.owner_socket_path == handle.socket_path
    document = json.loads(
        (root / "specs" / f"{handle.task_id}.json").read_text(
            encoding="utf-8"))
    assert document["schema_version"] == "rpnh/agent_task_spec/v6"
    assert document["owner_socket_relative_path"] == "owner.sock"


@pytest.mark.parametrize("version", (1, 2, 3, 4))
def test_agent_task_spec_reads_legacy_worker_documents(version: int) -> None:
    spec = AgentTaskSpec(
        run_dir=Path("/tmp/legacy-run"),
        prompt="Read a persisted worker specification.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=Path("/tmp/execution.json"),
    )
    document = spec.as_worker_document()
    document["schema_version"] = f"rpnh/agent_task_spec/v{version}"
    document.pop("owner_socket_path")
    if version == 1:
        document.pop("workflow_graph")
        document.pop("max_parallel_nodes")
        document.pop("execution_profiles")
    elif version == 2:
        document.pop("max_parallel_nodes")
        document.pop("execution_profiles")
    elif version == 4:
        document["plugin_configuration"] = {}
        document["plugin_catalog_digest"] = "0" * 64

    restored = AgentTaskSpec.from_worker_document(document)

    assert restored.run_dir == spec.run_dir
    assert restored.owner_socket_path is None


def test_task_control_recovers_pre_spawn_intent_with_same_identity(
        tmp_path: Path,
) -> None:
    spawned: list[_Process] = []

    def spawn(*args, **kwargs):
        process = _Process(*args, **kwargs)
        spawned.append(process)
        return process

    root = tmp_path / "control"
    spec = AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="Recover one durable launch intent.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    )
    first = TaskControl(root, popen_factory=spawn)
    handle = first.start(spec)
    manifest_path = root / "manifests" / f"{handle.task_id}.json"
    intent = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert intent["launch_state"] == "pending"
    assert intent["pid"] is None

    recovered = TaskControl(root, popen_factory=spawn)
    recovered_handle = recovered.get(handle.task_id)

    assert len(spawned) == 2
    assert tuple(recovered._tasks) == (handle.task_id,)
    assert recovered_handle.task_id == handle.task_id
    assert recovered_handle.run_dir == spec.run_dir
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == intent


def test_worker_claim_activates_intent_and_excludes_duplicate(
        tmp_path: Path,
) -> None:
    root = tmp_path / "control"
    spec = AgentTaskSpec(
        run_dir=tmp_path / "run",
        prompt="Claim one worker identity.",
        stages=(AgentStage("worker", "Return one result."),),
        execution_config_path=tmp_path / "execution.json",
    )
    control = TaskControl(root, popen_factory=_Process)
    handle = control.start(spec)
    spec_path = root / "specs" / f"{handle.task_id}.json"

    claim = claim_task_worker_launch(spec_path, spec, resume=False)
    assert claim is not None
    try:
        manifest = json.loads((
            root / "manifests" / f"{handle.task_id}.json"
        ).read_text(encoding="utf-8"))
        assert manifest["launch_state"] == "active"
        assert manifest["pid"] == os.getpid()
        assert manifest["process_start_ticks"] is not None
        assert claim_task_worker_launch(
            spec_path, spec, resume=False) is None
    finally:
        claim.close()


def test_task_worker_inherits_launch_lock_when_parent_stdin_is_closed(
        tmp_path: Path,
) -> None:
    root = tmp_path / "control"
    control = TaskControl(root)
    try:
        saved_stdin = os.dup(0)
    except OSError:
        saved_stdin = None
    try:
        if saved_stdin is not None:
            os.close(0)
        handle = control.start(AgentTaskSpec(
            run_dir=tmp_path / "run",
            prompt="Exercise worker launch descriptor inheritance.",
            stages=(AgentStage("worker", "Return one result."),),
            execution_config_path=tmp_path / "missing-execution.json",
        ))
    finally:
        if saved_stdin is not None:
            os.dup2(saved_stdin, 0)
            os.close(saved_stdin)

    assert handle.process.wait(timeout=10) == 1
    manifest = json.loads((
        root / "manifests" / f"{handle.task_id}.json"
    ).read_text(encoding="utf-8"))
    assert manifest["launch_state"] == "active"
    assert "inherited launch lock differs from task identity" not in (
        handle.log_path.read_text(encoding="utf-8"))


def test_resume_uses_persisted_transition_profiles_when_graph_swaps_them(
        tmp_path: Path, monkeypatch,
) -> None:
    def write_selection(name: str) -> Path:
        adapter_path = tmp_path / f"{name}-adapter.json"
        adapter_path.write_text(json.dumps({
            "schema_version": "local_process_adapter_config/v1",
            "adapter_kind": "local_process",
            "model_condition": name,
            "argv": [sys.executable, "-c", "pass"],
            "probe_argv": [sys.executable, "-c", "pass"],
            "env": {},
            "inherit_env": [],
        }), encoding="utf-8")
        selection_path = tmp_path / f"{name}-selection.json"
        selection_path.write_text(json.dumps({
            "schema_version": "llm_execution_selection/v1",
            "adapter_kind": "local_process",
            "model_condition": name,
            "adapter_config_path": str(adapter_path),
            "timeout_seconds": 30,
            "max_output_tokens": 1024,
            "max_response_bytes": 65536,
        }), encoding="utf-8")
        return selection_path

    default_path = write_selection("default-model")
    planner_path = write_selection("planner-model")
    writer_path = write_selection("writer-model")
    resuming = False
    resumed_dispatches: list[tuple[str, str]] = []

    class _ProfilePort:
        def __init__(self, model: str) -> None:
            self.model = model

        def request_once(self, attempt):
            envelope = json.loads(attempt.canonical_request_bytes)
            combined = "\n".join(
                message["content"] for message in envelope["messages"]
                if isinstance(message.get("content"), str))
            if not resuming:
                assert "Plan the work." in combined
                assert self.model == "planner-model"
                calls = [{
                    "id": "interrupt-plan",
                    "name": "workspace",
                    "arguments": json.dumps({
                        "script": "interrupt-profiled-plan",
                        "timeout_seconds": 10,
                    }),
                }]
            elif "Plan the work." in combined:
                resumed_dispatches.append(("plan", self.model))
                calls = [{
                    "id": "resume-plan-output",
                    "name": "write_file",
                    "arguments": json.dumps({
                        "path": "outputs/plan.txt",
                        "description": "Plan produced after resume.",
                        "content": json.dumps("plan ready"),
                        "output_port_id": "team.output__plan__plan",
                        "outcome_id": "complete",
                    }),
                }, {
                    "id": "resume-plan-complete",
                    "name": "complete_interaction",
                    "arguments": "{}",
                }]
            elif "Deliver the result." in combined:
                resumed_dispatches.append(("deliver", self.model))
                calls = [{
                    "id": "resume-deliver-output",
                    "name": "write_file",
                    "arguments": json.dumps({
                        "path": "outputs/result.txt",
                        "description": "Result produced after resume.",
                        "content": json.dumps("persisted routes"),
                        "output_port_id": "team.result",
                        "outcome_id": "complete",
                    }),
                }, {
                    "id": "resume-deliver-complete",
                    "name": "complete_interaction",
                    "arguments": "{}",
                }]
            else:
                raise AssertionError("unknown profiled workflow transition")
            return LLMInputResponseBytes(json.dumps({
                "protocol": "llm_response_envelope/v1",
                "tool_calls": calls,
                "finish_reason": "tool_calls",
            }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                status_code=None, external_request_id=None)

        def close(self):
            pass

    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda selection, *, destination_run_root: _ProfilePort(
            selection.input_target.model_condition))

    def interrupt_workspace(*, script, **_kwargs):
        assert script == "interrupt-profiled-plan"
        os.kill(os.getpid(), signal.SIGINT)
        return {
            "status": "completed", "exit_code": 0,
            "stdout": "", "stderr": "", "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        interrupt_workspace)
    run_dir = tmp_path / "profiled-run"
    common = {
        "run_dir": run_dir,
        "prompt": "Use the registered profiles for each workflow node.",
        "stages": (),
        "execution_config_path": default_path,
        "max_attempts_per_stage": 3,
        "execution_profiles": (
            ("planner", planner_path), ("writer", writer_path)),
    }
    stopped = run_agent_task(AgentTaskSpec(
        **common,
        workflow_graph=_profiled_workflow_graph("planner", "writer"),
    ))
    assert stopped["stop_reason"] == "stopped_by_owner"

    resuming = True
    resumed = resume_agent_task(AgentTaskSpec(
        **common,
        workflow_graph=_profiled_workflow_graph("writer", "planner"),
    ))

    assert resumed["stop_reason"] == "terminal"
    assert resumed["output"] == "persisted routes"
    assert resumed_dispatches == [
        ("plan", "planner-model"),
        ("deliver", "writer-model"),
    ]


def test_workspace_materialization_rejects_symlink_parent(
        tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        optional_execution.OptionalAgentLoopRegistryService._write_workspace_bytes(
            root, "escape/result.txt", b"must remain confined")

    assert not (outside / "result.txt").exists()


class _WorkspaceWorkflowPort:
    def __init__(self) -> None:
        self.deliver_saw_registered_workspace_file = False

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        messages = envelope["messages"]
        combined = "\n".join(
            message["content"] for message in messages
            if isinstance(message.get("content"), str))
        tool_results = [
            message for message in messages if message["role"] == "tool"]
        planning = "Plan the work." in combined
        if planning and not tool_results:
            calls = [{
                "id": "plan-workspace",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": "create-shared-note",
                    "timeout_seconds": 10,
                }),
            }]
        elif planning:
            calls = [{
                "id": "plan-output",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/plan.txt",
                    "description": "Registered plan output.",
                    "content": json.dumps("plan ready"),
                    "output_port_id": "team.output__plan__plan",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "plan-complete",
                "name": "complete_interaction",
                "arguments": "{}",
            }]
        elif not tool_results:
            self.deliver_saw_registered_workspace_file = (
                '"source_relative_path":"shared/note.txt"' in combined)
            calls = [{
                "id": "deliver-workspace",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": "revise-shared-note",
                    "timeout_seconds": 10,
                }),
            }, {
                "id": "deliver-output",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": "Registered workflow result.",
                    "content": json.dumps("delivery ready"),
                    "output_port_id": "team.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "deliver-complete",
                "name": "complete_interaction",
                "arguments": "{}",
            }]
        else:
            raise AssertionError("deliver node requested an unexpected extra turn")
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": calls,
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            status_code=None, external_request_id=None)

    def close(self):
        pass


class _FanoutWorkspacePort:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.join_saw_both_registry_files = False

    def request_once(self, attempt):
        envelope = json.loads(attempt.canonical_request_bytes)
        combined = "\n".join(
            message["content"] for message in envelope["messages"]
            if isinstance(message.get("content"), str))
        if "Create the shared starting point." in combined:
            role = "start"
            script = "create-start"
            path = "outputs/seed.txt"
            output_port = "team.output__start__seed"
            content = "seed"
        elif "Produce branch A." in combined:
            role = "branch_a"
            script = "create-branch-a"
            path = "outputs/result.json"
            output_port = "team.output__branch_a__a"
            content = "a"
        elif "Produce branch B." in combined:
            role = "branch_b"
            script = "create-branch-b"
            path = "outputs/result.json"
            output_port = "team.output__branch_b__b"
            content = "b"
        elif "Join both branches." in combined:
            role = "join"
            script = "verify-joined-workspace"
            path = "outputs/result.txt"
            output_port = "team.result"
            content = "joined"
            self.join_saw_both_registry_files = all(
                f'"source_relative_path":"branches/{name}.txt"'
                in combined for name in ("a", "b"))
        else:
            raise AssertionError("unknown fan-out workflow role")
        with self._lock:
            if role in self.calls:
                raise AssertionError(f"duplicate fan-out role call: {role}")
            self.calls.append(role)
        calls = [{
            "id": f"{role}-workspace",
            "name": "workspace",
            "arguments": json.dumps({
                "script": script,
                "timeout_seconds": 10,
            }),
        }, {
            "id": f"{role}-output",
            "name": "write_file",
            "arguments": json.dumps({
                "path": path,
                "description": f"Semantic output from {role}.",
                "content": json.dumps(content),
                "output_port_id": output_port,
                "outcome_id": "complete",
            }),
        }, {
            "id": f"{role}-complete",
            "name": "complete_interaction",
            "arguments": "{}",
        }]
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": calls,
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            status_code=None, external_request_id=None)

    def close(self):
        pass


class _InterruptedWorkspacePort:
    def __init__(self) -> None:
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        if self.calls == 1:
            calls = [{
                "id": "interrupted-workspace",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": "write-then-interrupt",
                    "timeout_seconds": 10,
                }),
            }]
        elif self.calls == 2:
            calls = [{
                "id": "interrupted-output",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": "Output superseded by owner interruption.",
                    "content": json.dumps("discarded"),
                    "output_port_id": "main.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "interrupted-complete",
                "name": "complete_interaction",
                "arguments": "{}",
            }]
        else:
            raise AssertionError("interrupted firing requested an extra turn")
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": calls,
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            status_code=None, external_request_id=None)

    def close(self):
        pass


class _ResumedWorkspacePort:
    def __init__(self) -> None:
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("resumed firing requested an extra turn")
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": "resume-workspace",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": "verify-clean-resume",
                    "timeout_seconds": 10,
                }),
            }, {
                "id": "resume-output",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": "Result from the resumed clean firing.",
                    "content": json.dumps("resumed"),
                    "output_port_id": "main.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "resume-complete",
                "name": "complete_interaction",
                "arguments": "{}",
            }],
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            status_code=None, external_request_id=None)

    def close(self):
        pass


class _CheckpointBeforeInterruptedPort:
    def __init__(self) -> None:
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        scripts = {
            1: "write-settled-draft",
            2: "write-interrupted-draft",
        }
        if self.calls not in scripts:
            raise AssertionError("checkpoint test requested an extra turn")
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": f"checkpoint-workspace-{self.calls}",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": scripts[self.calls],
                    "timeout_seconds": 10,
                }),
            }],
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            status_code=None, external_request_id=None)

    def close(self):
        pass


class _ResumeCheckpointPort:
    def __init__(self) -> None:
        self.calls = 0

    def request_once(self, _attempt):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("checkpoint resume requested an extra turn")
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1",
            "tool_calls": [{
                "id": "resume-checkpoint-workspace",
                "name": "workspace",
                "arguments": json.dumps({
                    "script": "verify-settled-checkpoint",
                    "timeout_seconds": 10,
                }),
            }, {
                "id": "resume-checkpoint-output",
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "outputs/result.txt",
                    "description": "Result after the owner checkpoint resumed.",
                    "content": json.dumps("checkpoint resumed"),
                    "output_port_id": "main.result",
                    "outcome_id": "complete",
                }),
            }, {
                "id": "resume-checkpoint-complete",
                "name": "complete_interaction",
                "arguments": "{}",
            }],
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            status_code=None, external_request_id=None)

    def close(self):
        pass


def test_workflow_registers_and_projects_workspace_files(
        tmp_path: Path, monkeypatch,
) -> None:
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-workspace-test",
        "argv": [sys.executable, "-c", "pass"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-workspace-test",
        "adapter_config_path": str(adapter_path),
        "timeout_seconds": 30,
        "max_output_tokens": 1024,
        "max_response_bytes": 65536,
    }), encoding="utf-8")
    port = _WorkspaceWorkflowPort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)

    def fake_workspace(*, script, cwd, **_kwargs):
        root = Path(cwd)
        shared = root / "shared" / "note.txt"
        shared.parent.mkdir(parents=True, exist_ok=True)
        if script == "create-shared-note":
            shared.write_text("draft", encoding="utf-8")
        elif script == "revise-shared-note":
            assert shared.read_text(encoding="utf-8") == "draft"
            shared.write_text("revised", encoding="utf-8")
        else:
            raise AssertionError(f"unexpected workspace script: {script}")
        return {
            "status": "completed",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        fake_workspace)
    run_dir = tmp_path / "run"
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Create and revise one shared workspace document.",
        stages=(),
        execution_config_path=execution_path,
        workflow_graph=_workflow_graph(),
        max_attempts_per_stage=4,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "delivery ready"
    assert result["actual_model_call_counts"] == [3, 0]
    assert port.deliver_saw_registered_workspace_file is True

    workspace_roots = tuple(sorted(
        path for path in (run_dir / "workspace" / "views").iterdir()
        if path.is_dir()))
    assert len(workspace_roots) == 2
    assert {path.joinpath("shared/note.txt").read_bytes()
            for path in workspace_roots} == {b"draft", b"revised"}

    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT version_id,metadata_json FROM objects "
            "WHERE object_type='resource_version/v1' ORDER BY rowid",
        ).fetchall()
    shared_versions = []
    for row in rows:
        metadata = json.loads(row["metadata_json"])
        if (metadata.get("origin_kind") == "workspace_write"
                and metadata.get("descriptors", {}).get("workspace_path")
                == "shared/note.txt"):
            version = row["version_id"].split(":", 1)[1]
            shared_versions.append(
                (run_dir / ".registry_v1" / "objects"
                 / "resource_version" / version).read_bytes())
    assert shared_versions == [b"draft", b"revised"]
    readonly = _RegistryCore(
        run_dir, create=False, read_only=True,
        catalog=agent_task_catalog())
    canonical_workspace_versions = {
        str(row["version_id"])
        for row in readonly.event_store.canonical_object_rows(
            object_type="resource_version/v1")
        if (json.loads(row["metadata_json"]).get("origin_kind")
            == "workspace_write")
    }
    assert len(canonical_workspace_versions) == 4

    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        db.row_factory = sqlite3.Row
        revisions = db.execute(
            "SELECT version_id,metadata_json FROM objects "
            "WHERE object_type='workspace_revision/v1' ORDER BY rowid",
        ).fetchall()
        head = db.execute(
            "SELECT workspace_revision_version_id "
            "FROM workspace_lineage_heads",
        ).fetchone()
        firing_workspaces = db.execute(
            "SELECT workspace_revision_version_id FROM firing_publications "
            "ORDER BY rowid",
        ).fetchall()
    assert len(revisions) == 3
    assert [
        json.loads(row["metadata_json"])["disposition"]
        for row in revisions
    ] == ["genesis", "committed", "committed"]
    assert head["workspace_revision_version_id"] == revisions[-1]["version_id"]
    assert [row["workspace_revision_version_id"]
            for row in firing_workspaces] == [
                revisions[1]["version_id"], revisions[2]["version_id"]]
    final_metadata = json.loads(revisions[-1]["metadata_json"])
    assert final_metadata["changed_paths"] == [
        "outputs/result.txt", "shared/note.txt"]
    assert final_metadata["inventory_paths"] == [
        "outputs/ports/output__plan__plan/plan.txt",
        "outputs/result.txt", "shared/note.txt"]
    final_revision = revisions[-1]["version_id"].split(":", 1)[1]
    archive_payload = (
        run_dir / ".registry_v1" / "objects"
        / "workspace_revision" / final_revision).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:") as archive:
        assert {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers() if member.isfile()
        } == {
            "outputs/ports/output__plan__plan/plan.txt": b"plan ready",
            "outputs/result.txt": b"delivery ready",
            "shared/note.txt": b"revised",
        }


def test_fanout_workspaces_namespace_semantic_products_and_merge_branch_files(
        tmp_path: Path, monkeypatch,
) -> None:
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-workspace-fanout-test",
        "argv": [sys.executable, "-c", "pass"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-workspace-fanout-test",
        "adapter_config_path": str(adapter_path),
        "timeout_seconds": 30,
        "max_output_tokens": 1024,
        "max_response_bytes": 65536,
    }), encoding="utf-8")
    port = _FanoutWorkspacePort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)
    branch_roots: dict[str, Path] = {}
    branch_barrier = threading.Barrier(2)
    active_branches = 0
    peak_active_branches = 0
    activity_lock = threading.Lock()

    def fake_workspace(*, script, cwd, **_kwargs):
        nonlocal active_branches, peak_active_branches
        root = Path(cwd)
        if script == "create-start":
            target = root / "shared" / "start.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("start", encoding="utf-8")
        elif script in {"create-branch-a", "create-branch-b"}:
            branch = script.removeprefix("create-branch-")
            with activity_lock:
                active_branches += 1
                peak_active_branches = max(
                    peak_active_branches, active_branches)
            try:
                branch_barrier.wait(timeout=10)
            finally:
                with activity_lock:
                    active_branches -= 1
            assert (root / "shared" / "start.txt").read_text(
                encoding="utf-8") == "start"
            other = "b" if branch == "a" else "a"
            assert not (root / "branches" / f"{other}.txt").exists()
            target = root / "branches" / f"{branch}.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(branch, encoding="utf-8")
            branch_roots[branch] = root
        elif script == "verify-joined-workspace":
            assert (root / "branches" / "a.txt").read_text(
                encoding="utf-8") == "a"
            assert (root / "branches" / "b.txt").read_text(
                encoding="utf-8") == "b"
        else:
            raise AssertionError(f"unexpected fan-out script: {script}")
        return {
            "status": "completed",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        fake_workspace)
    run_dir = tmp_path / "run"
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Run a two-branch workspace workflow.",
        stages=(),
        execution_config_path=execution_path,
        workflow_graph=_fanout_workflow_graph(),
        max_attempts_per_stage=3,
    ))

    assert result["stop_reason"] == "terminal"
    assert result["output"] == "joined"
    assert set(port.calls) == {"start", "branch_a", "branch_b", "join"}
    assert port.join_saw_both_registry_files is True
    assert peak_active_branches == 2
    assert branch_roots["a"] != branch_roots["b"]
    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        db.row_factory = sqlite3.Row
        revisions = db.execute(
            "SELECT version_id, metadata_json FROM objects "
            "WHERE object_type='workspace_revision/v1' ORDER BY rowid",
        ).fetchall()
        head = db.execute(
            "SELECT workspace_revision_version_id "
            "FROM workspace_lineage_heads",
        ).fetchone()
    metadata = [json.loads(row["metadata_json"]) for row in revisions]
    assert [item["disposition"] for item in metadata].count("merged") == 1
    assert head["workspace_revision_version_id"] == revisions[-1]["version_id"]
    readonly = _RegistryCore(
        run_dir, create=False, read_only=True,
        catalog=agent_task_catalog())
    for revision in metadata[1:]:
        deltas = revision["path_deltas"]
        assert [item["path"] for item in deltas] == sorted(
            revision["changed_paths"] + revision["deleted_paths"])
        for delta in deltas:
            for side in ("before", "after"):
                ref = delta[f"{side}_resource_ref"]
                summary = delta[f"{side}_summary"]
                if ref is None:
                    assert summary is None
                    continue
                version_id = TypedId.parse(
                    ref["resource_version_id"], expected="resource_version")
                prepared = readonly.get_version(version_id)
                assert readonly.event_store.canonical_object_row(
                    version_id) is not None
                assert prepared.metadata["descriptors"]["workspace_path"] == (
                    delta["path"])
                assert prepared.metadata["summary"] == summary
    archive_payload = (
        run_dir / ".registry_v1" / "objects" / "workspace_revision"
        / revisions[-1]["version_id"].split(":", 1)[1]).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(archive_payload), mode="r:") as archive:
        files = {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers() if member.isfile()
        }
    assert files["shared/start.txt"] == b"start"
    assert files["branches/a.txt"] == b"a"
    assert files["branches/b.txt"] == b"b"
    assert files[
        "outputs/ports/output__branch_a__a/result.json"] == b"a"
    assert files[
        "outputs/ports/output__branch_b__b/result.json"] == b"b"


def test_interrupted_firing_does_not_publish_workspace_files(
        tmp_path: Path, monkeypatch,
) -> None:
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-workspace-interruption-test",
        "argv": [sys.executable, "-c", "pass"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-workspace-interruption-test",
        "adapter_config_path": str(adapter_path),
        "timeout_seconds": 30,
        "max_output_tokens": 1024,
        "max_response_bytes": 65536,
    }), encoding="utf-8")
    port = _InterruptedWorkspacePort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)

    def fake_workspace(*, script, cwd, **_kwargs):
        assert script == "write-then-interrupt"
        partial = Path(cwd) / "drafts" / "partial.txt"
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text("not permanent", encoding="utf-8")
        os.kill(os.getpid(), signal.SIGINT)
        return {
            "status": "completed",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        fake_workspace)
    run_dir = tmp_path / "run"
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Create a draft that will be interrupted.",
        stages=(AgentStage("main", "Draft the requested file."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=3,
    ))

    assert result["stop_reason"] == "stopped_by_owner"
    assert port.calls == 1
    readonly = _RegistryCore(
        run_dir, create=False, read_only=True,
        catalog=agent_task_catalog())
    interrupted_actions = [
        json.loads(row["metadata_json"])
        for row in readonly.event_store.canonical_object_rows(
            object_type="agent_action/v2")]
    assert len(interrupted_actions) == 1
    assert interrupted_actions[0]["state"] == "ACTION_REJECTED"
    error_ref = interrupted_actions[0]["tool_error_ref"]
    error = readonly.get_version(
        TypedId.parse(
            error_ref["version_id"], expected="agent_tool_error_version"))
    assert error.metadata["error_code"] == "owner_interrupted"
    assert error.metadata["disposition"] == "close_at_owner_checkpoint"
    canonical_workspace_rows = [
        row for row in readonly.event_store.canonical_object_rows(
            object_type="resource_version/v1")
        if (json.loads(row["metadata_json"]).get("origin_kind")
            == "workspace_write")
    ]
    assert canonical_workspace_rows == []
    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        db.row_factory = sqlite3.Row
        revisions = db.execute(
            "SELECT version_id FROM objects "
            "WHERE object_type='workspace_revision/v1' ORDER BY rowid",
        ).fetchall()
        head = db.execute(
            "SELECT workspace_revision_version_id "
            "FROM workspace_lineage_heads",
        ).fetchone()
    assert len(revisions) == 1
    assert head["workspace_revision_version_id"] == revisions[0]["version_id"]

    # A local profile edit must be rejected through a read-only preflight.
    # In particular, the rejected attempt must not acquire a new writer or
    # append the run-resume authority that would consume the clean stop point.
    execution_document = json.loads(execution_path.read_text(encoding="utf-8"))
    execution_document["timeout_seconds"] = 31
    execution_path.write_text(
        json.dumps(execution_document), encoding="utf-8")
    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        before_writer_epoch = db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'",
        ).fetchone()[0]
        before_authorities = db.execute(
            "SELECT COUNT(*) FROM objects "
            "WHERE object_type='run_execution_authority/v1'",
        ).fetchone()[0]
    with pytest.raises(
            ValueError, match="changed the exact provider route"):
        resume_agent_task(AgentTaskSpec(
            run_dir=run_dir,
            prompt="Create a draft that will be interrupted.",
            stages=(AgentStage("main", "Draft the requested file."),),
            execution_config_path=execution_path,
            max_attempts_per_stage=3,
        ))
    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        assert db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'",
        ).fetchone()[0] == before_writer_epoch
        assert db.execute(
            "SELECT COUNT(*) FROM objects "
            "WHERE object_type='run_execution_authority/v1'",
        ).fetchone()[0] == before_authorities
    execution_document["timeout_seconds"] = 30
    execution_path.write_text(
        json.dumps(execution_document), encoding="utf-8")

    resumed_port = _ResumedWorkspacePort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: resumed_port)

    def resumed_workspace(*, script, cwd, **_kwargs):
        assert script == "verify-clean-resume"
        root = Path(cwd)
        assert not (root / "drafts" / "partial.txt").exists()
        final = root / "drafts" / "final.txt"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text("permanent", encoding="utf-8")
        return {
            "status": "completed",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        resumed_workspace)
    resumed = resume_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Create a draft that will be interrupted.",
        stages=(AgentStage("main", "Draft the requested file."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=3,
    ))

    assert resumed["stop_reason"] == "terminal"
    assert resumed["output"] == "resumed"
    assert resumed_port.calls == 1
    readonly = _RegistryCore(
        run_dir, create=False, read_only=True,
        catalog=agent_task_catalog())
    canonical_workspace_paths = sorted(
        json.loads(row["metadata_json"])["descriptors"]["workspace_path"]
        for row in readonly.event_store.canonical_object_rows(
            object_type="resource_version/v1")
        if (json.loads(row["metadata_json"]).get("origin_kind")
            == "workspace_write"))
    assert canonical_workspace_paths == [
        "drafts/final.txt", "outputs/result.txt"]
    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        db.row_factory = sqlite3.Row
        revisions = db.execute(
            "SELECT version_id FROM objects "
            "WHERE object_type='workspace_revision/v1' ORDER BY rowid",
        ).fetchall()
        head = db.execute(
            "SELECT workspace_revision_version_id "
            "FROM workspace_lineage_heads",
        ).fetchone()
    assert len(revisions) == 2
    assert head["workspace_revision_version_id"] == revisions[-1]["version_id"]


def test_interruption_checkpoints_prior_workspace_action_and_discards_current(
        tmp_path: Path, monkeypatch,
) -> None:
    adapter_path = tmp_path / "adapter.json"
    adapter_path.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-owner-checkpoint-test",
        "argv": [sys.executable, "-c", "pass"],
        "probe_argv": [sys.executable, "-c", "pass"],
        "env": {},
        "inherit_env": [],
    }), encoding="utf-8")
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1",
        "adapter_kind": "local_process",
        "model_condition": "offline-owner-checkpoint-test",
        "adapter_config_path": str(adapter_path),
        "timeout_seconds": 30,
        "max_output_tokens": 1024,
        "max_response_bytes": 65536,
    }), encoding="utf-8")
    port = _CheckpointBeforeInterruptedPort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: port)

    def interrupted_workspace(*, script, cwd, **_kwargs):
        root = Path(cwd)
        if script == "write-settled-draft":
            target = root / "drafts" / "settled.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("settled before interruption", encoding="utf-8")
        elif script == "write-interrupted-draft":
            target = root / "drafts" / "discarded.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("must not survive", encoding="utf-8")
            os.kill(os.getpid(), signal.SIGINT)
        else:
            raise AssertionError("unknown checkpoint workspace script")
        return {
            "status": "completed", "exit_code": 0,
            "stdout": "", "stderr": "", "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        interrupted_workspace)
    run_dir = tmp_path / "run"
    result = run_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Preserve completed work across an owner interruption.",
        stages=(AgentStage("main", "Write and revise the draft."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=4,
    ))

    assert result["stop_reason"] == "stopped_by_owner"
    assert port.calls == 2
    readonly = _RegistryCore(
        run_dir, create=False, read_only=True,
        catalog=agent_task_catalog())
    paths = sorted(
        json.loads(row["metadata_json"])["descriptors"]["workspace_path"]
        for row in readonly.event_store.canonical_object_rows(
            object_type="resource_version/v1")
        if (json.loads(row["metadata_json"]).get("origin_kind")
            == "workspace_write"))
    assert paths == ["drafts/settled.txt"]
    with sqlite3.connect(run_dir / ".registry_v1" / "registry.sqlite3") as db:
        db.row_factory = sqlite3.Row
        revisions = db.execute(
            "SELECT version_id FROM objects "
            "WHERE object_type='workspace_revision/v1' ORDER BY rowid",
        ).fetchall()
        head = db.execute(
            "SELECT workspace_revision_version_id "
            "FROM workspace_lineage_heads",
        ).fetchone()
    assert len(revisions) == 2
    assert head["workspace_revision_version_id"] == revisions[-1]["version_id"]

    resumed_port = _ResumeCheckpointPort()
    monkeypatch.setattr(
        "cpn.rpnh.agent_tasks.build_llm_input_port",
        lambda _selection, *, destination_run_root: resumed_port)

    def resumed_workspace(*, script, cwd, **_kwargs):
        assert script == "verify-settled-checkpoint"
        root = Path(cwd)
        assert (root / "drafts" / "settled.txt").read_text(
            encoding="utf-8") == "settled before interruption"
        assert not (root / "drafts" / "discarded.txt").exists()
        return {
            "status": "completed", "exit_code": 0,
            "stdout": "", "stderr": "", "output_truncated": False,
            "command_started": True,
        }

    monkeypatch.setattr(
        optional_execution, "execute_bounded_workspace_tool",
        resumed_workspace)
    resumed = resume_agent_task(AgentTaskSpec(
        run_dir=run_dir,
        prompt="Preserve completed work across an owner interruption.",
        stages=(AgentStage("main", "Write and revise the draft."),),
        execution_config_path=execution_path,
        max_attempts_per_stage=4,
    ))
    assert resumed["stop_reason"] == "terminal"
    assert resumed["output"] == "checkpoint resumed"
    assert resumed_port.calls == 1
